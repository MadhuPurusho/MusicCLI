"""Audio metadata and album cover art extraction for MP3 and FLAC files."""

from __future__ import annotations

import atexit
import base64
import collections
import concurrent.futures
from enum import Enum
import hashlib
import io
import json
import math
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pulse.dsp import ReplayGain

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None
    ImageOps = None

try:
    from mutagen import File as MutagenFile
    from mutagen.flac import FLAC, Picture
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3NoHeaderError
except ImportError:
    MutagenFile = None
    MP3 = None
    FLAC = None
    Picture = None
    ID3NoHeaderError = Exception


SUPPORTED_EXTENSIONS = {".mp3", ".flac"}
MAX_METADATA_CACHE_ENTRIES = 2048

# Background executor for non-blocking asynchronous cover fetches
_COVER_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="pulse_cover")


def shutdown_cover_executor(wait: bool = False, cancel_futures: bool = True) -> None:
    """Terminate the background cover artwork thread pool without blocking process exit."""
    try:
        _COVER_EXECUTOR.shutdown(wait=wait, cancel_futures=cancel_futures)
    except Exception:
        pass


atexit.register(shutdown_cover_executor)

# Global thread-safe bounded LRU metadata cache keyed by (path_str, file_size, mtime)
_METADATA_CACHE: collections.OrderedDict[Tuple[str, int, float], TrackMetadata] = collections.OrderedDict()


def format_duration(seconds: float | int) -> str:
    """Format seconds into MM:SS or HH:MM:SS format safely."""
    if seconds is None:
        return "00:00"
    try:
        val = float(seconds)
        if math.isnan(val) or math.isinf(val) or val <= 0:
            return "00:00"
        total_sec = int(round(val))
        hrs = total_sec // 3600
        mins = (total_sec % 3600) // 60
        secs = total_sec % 60
        if hrs > 0:
            return f"{hrs:02d}:{mins:02d}:{secs:02d}"
        return f"{mins:02d}:{secs:02d}"
    except (TypeError, ValueError, OverflowError):
        return "00:00"


def format_file_size(num_bytes: int) -> str:
    """Format byte size into human readable string."""
    if num_bytes <= 0:
        return "0 B"
    elif num_bytes < 1024:
        return f"{num_bytes} B"
    elif num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    elif num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"


def _safe_tag_str(val) -> str:
    """Safely extract clean string from mutagen tag objects or sequences."""
    if val is None:
        return ""
    if hasattr(val, "text") and val.text:
        return str(val.text[0]).strip()
    if isinstance(val, (list, tuple)) and len(val) > 0:
        return str(val[0]).strip()
    return str(val).strip()


class DitherAlgorithm(Enum):
    """Supported 1-bit monochrome halftone dithering algorithms."""
    ATKINSON = "ATKINSON"
    FLOYD_STEINBERG = "FLOYD_STEINBERG"
    BAYER = "BAYER"

    def next_algorithm(self) -> DitherAlgorithm:
        algos = list(DitherAlgorithm)
        idx = (algos.index(self) + 1) % len(algos)
        return algos[idx]


def _dither_atkinson(im: Image.Image, width: int, height: int) -> List[List[bool]]:
    """Apply Bill Atkinson's 1984 Macintosh error-diffusion dithering.

    Distributes 1/8 of quantization error to 6 neighboring pixels:
    (x+1, y), (x+2, y), (x-1, y+1), (x, y+1), (x+1, y+1), (x, y+2).
    Discards 2/8 (25%) of error to eliminate diffuse shadow mud and maintain
    brilliant whites and punchy blacks on 1-bit displays.
    """
    raw = list(im.getdata())
    buf = [[float(raw[y * width + x]) for x in range(width)] for y in range(height)]
    result = [[False for _ in range(width)] for _ in range(height)]

    for y in range(height):
        for x in range(width):
            old_val = buf[y][x]
            new_val = 255.0 if old_val >= 128.0 else 0.0
            result[y][x] = (new_val > 0.0)
            err = (old_val - new_val) / 8.0

            # Distribute 1/8 to neighbors
            if x + 1 < width:
                buf[y][x + 1] += err
            if x + 2 < width:
                buf[y][x + 2] += err

            if y + 1 < height:
                if x - 1 >= 0:
                    buf[y + 1][x - 1] += err
                buf[y + 1][x] += err
                if x + 1 < width:
                    buf[y + 1][x + 1] += err

            if y + 2 < height:
                buf[y + 2][x] += err

    return result


_BAYER_4X4 = [
    [0, 8, 2, 10],
    [12, 4, 14, 6],
    [3, 11, 1, 9],
    [15, 7, 13, 5],
]


def _dither_bayer(im: Image.Image, width: int, height: int) -> List[List[bool]]:
    """Apply ordered Bayer 4x4 spatial threshold dithering."""
    raw = list(im.getdata())
    result = [[False for _ in range(width)] for _ in range(height)]
    scale = 255.0 / 16.0

    for y in range(height):
        row_offset = y * width
        for x in range(width):
            thresh = (_BAYER_4X4[y % 4][x % 4] + 0.5) * scale
            result[y][x] = (raw[row_offset + x] > thresh)

    return result


def _dither_floyd_steinberg(im: Image.Image, width: int, height: int) -> List[List[bool]]:
    """Apply classic Floyd-Steinberg error diffusion (7/16, 3/16, 5/16, 1/16)."""
    raw = list(im.getdata())
    buf = [[float(raw[y * width + x]) for x in range(width)] for y in range(height)]
    result = [[False for _ in range(width)] for _ in range(height)]

    for y in range(height):
        for x in range(width):
            old_val = buf[y][x]
            new_val = 255.0 if old_val >= 128.0 else 0.0
            result[y][x] = (new_val > 0.0)
            err = old_val - new_val

            if x + 1 < width:
                buf[y][x + 1] += err * (7.0 / 16.0)
            if y + 1 < height:
                if x - 1 >= 0:
                    buf[y + 1][x - 1] += err * (3.0 / 16.0)
                buf[y + 1][x] += err * (5.0 / 16.0)
                if x + 1 < width:
                    buf[y + 1][x + 1] += err * (1.0 / 16.0)

    return result


@dataclass
class LyricsLine:
    """Individual line of synchronized or unsynchronized lyrics."""
    timestamp: float  # seconds
    text: str

    @property
    def formatted_time(self) -> str:
        return format_duration(self.timestamp)


@dataclass
class TrackLyrics:
    """Collection of lyrics with synchronized temporal lookup."""
    lines: List[LyricsLine]
    is_synced: bool = True

    def get_current_index(self, current_time: float) -> int:
        """Find the index of the currently active lyric line."""
        if not self.lines:
            return -1
        if not self.is_synced:
            return 0
        idx = 0
        for i, line in enumerate(self.lines):
            if line.timestamp <= current_time:
                idx = i
            else:
                break
        return idx

    def get_window(self, current_time: float, max_lines: int = 7) -> List[Tuple[int, bool, LyricsLine]]:
        """Return a window of (index, is_active, LyricsLine) centered around current time."""
        if not self.lines:
            return []
        curr_idx = self.get_current_index(current_time) if self.is_synced else 0
        half = max_lines // 2
        start_idx = max(0, curr_idx - half)
        end_idx = min(len(self.lines), start_idx + max_lines)
        if end_idx - start_idx < max_lines and start_idx > 0:
            start_idx = max(0, end_idx - max_lines)

        window = []
        for i in range(start_idx, end_idx):
            is_active = (i == curr_idx and self.is_synced)
            window.append((i, is_active, self.lines[i]))
        return window


_LRC_TIMESTAMP_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]")


def parse_lrc(text: str) -> Optional[TrackLyrics]:
    """Parse standard .lrc or embedded timestamped lyrics string into TrackLyrics."""
    if not text:
        return None
    lines: List[LyricsLine] = []
    has_timestamps = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matches = list(_LRC_TIMESTAMP_RE.finditer(line))
        if matches:
            has_timestamps = True
            lyric_text = _LRC_TIMESTAMP_RE.sub("", line).strip()
            for m in matches:
                mins = int(m.group(1))
                secs = int(m.group(2))
                msec_str = m.group(3) or "0"
                if len(msec_str) == 2:
                    msecs = int(msec_str) * 10
                elif len(msec_str) == 1:
                    msecs = int(msec_str) * 100
                else:
                    msecs = int(msec_str[:3])
                ts = mins * 60.0 + secs + (msecs / 1000.0)
                lines.append(LyricsLine(timestamp=ts, text=lyric_text))
        else:
            # Skip ID tags like [ar: ...], [ti: ...]
            if not (line.startswith("[") and ":" in line and line.endswith("]")):
                lines.append(LyricsLine(timestamp=0.0, text=line))

    if not lines:
        return None

    if has_timestamps:
        lines.sort(key=lambda x: x.timestamp)
        return TrackLyrics(lines=lines, is_synced=True)
    else:
        return TrackLyrics(lines=lines, is_synced=False)


@dataclass
class TrackMetadata:
    """Metadata and cover art details for an audio track."""

    path: Path
    title: str
    artist: str
    album: str
    year: str
    genre: str
    duration: float      # in seconds
    bitrate: int         # in kbps
    sample_rate: int     # in Hz
    channels: str        # "Stereo", "Mono", or "Xch"
    file_size_bytes: int
    file_format: str = "MP3"
    track_number: Optional[int] = None
    bits_per_sample: int = 16
    cover_data: Optional[bytes] = None
    cover_mime: Optional[str] = None
    cover_source: str = ""  # "embedded", "folder", "online", or ""
    lyrics: Optional[TrackLyrics] = None
    replay_gain_db: float = 0.0

    @property
    def formatted_duration(self) -> str:
        return format_duration(self.duration)

    @property
    def formatted_size(self) -> str:
        return format_file_size(self.file_size_bytes)

    @property
    def display_name(self) -> str:
        if self.artist and self.artist != "Unknown Artist":
            return f"{self.artist} - {self.title}"
        return self.title

    @property
    def has_cover(self) -> bool:
        return bool(self.cover_data and len(self.cover_data) > 32)

    @staticmethod
    def empty_pixel_art(width: int = 14, height: int = 5) -> List[str]:
        """Clean 1-bit monochrome frame when track has no cover with strict bounds."""
        if width <= 0 or height <= 0:
            return []
        if width <= 3 or height <= 2:
            return [" " * width] * height

        inner_w = max(0, width - 2)
        top = "┌" + ("─" * inner_w) + "┐"
        bot = "└" + ("─" * inner_w) + "┘"
        inner_h = max(0, height - 2)
        lines = [top]
        mid = inner_h // 2
        for r in range(inner_h):
            if r == mid and inner_h >= 2:
                txt = "NO COVER"[:inner_w].center(inner_w)
            elif r == mid + 1 and inner_h >= 3:
                txt = "ART"[:inner_w].center(inner_w)
            else:
                txt = " " * inner_w
            lines.append("│" + txt + "│")
        lines.append(bot)
        return lines[:height]

    def get_pixel_art(
        self,
        width: int = 14,
        height: int = 5,
        algorithm: DitherAlgorithm = DitherAlgorithm.ATKINSON,
    ) -> List[str]:
        """Generate a 1-bit monochrome pixel-art rendition of the actual album cover."""
        if width <= 0 or height <= 0:
            return []

        if not hasattr(self, "_pixel_art_cache"):
            self._pixel_art_cache = {}
        cache_key = (width, height, algorithm)
        if cache_key in self._pixel_art_cache:
            return self._pixel_art_cache[cache_key]

        if not self.has_cover or Image is None:
            res = TrackMetadata.empty_pixel_art(width, height)
            self._pixel_art_cache[cache_key] = res
            return res

        try:
            im = Image.open(io.BytesIO(self.cover_data)).convert("L")
            if ImageOps is not None:
                im = ImageOps.autocontrast(im)
            resized = im.resize((width, height * 2), Image.Resampling.LANCZOS)

            if algorithm == DitherAlgorithm.ATKINSON:
                bit_grid = _dither_atkinson(resized, width, height * 2)
            elif algorithm == DitherAlgorithm.BAYER:
                bit_grid = _dither_bayer(resized, width, height * 2)
            else:
                bit_grid = _dither_floyd_steinberg(resized, width, height * 2)

            lines = []
            for y in range(0, height * 2, 2):
                row = []
                y_bot = y + 1
                for x in range(width):
                    top_px = bit_grid[y][x]
                    bot_px = bit_grid[y_bot][x] if y_bot < height * 2 else False
                    if top_px and bot_px:
                        row.append("█")
                    elif top_px and not bot_px:
                        row.append("▀")
                    elif not top_px and bot_px:
                        row.append("▄")
                    else:
                        row.append(" ")
                lines.append("".join(row))
            self._pixel_art_cache[cache_key] = lines
            return lines
        except Exception:
            res = TrackMetadata.empty_pixel_art(width, height)
            self._pixel_art_cache[cache_key] = res
            return res

    def load_lyrics(self) -> Optional[TrackLyrics]:
        """Load lyrics from adjacent .lrc file or embedded ID3/Vorbis tags."""
        if self.lyrics is not None:
            return self.lyrics

        # 1. Adjacent .lrc file in track folder
        if self.path.exists():
            lrc_cand = self.path.with_suffix(".lrc")
            if lrc_cand.is_file():
                try:
                    content = lrc_cand.read_text(encoding="utf-8", errors="replace")
                    self.lyrics = parse_lrc(content)
                    if self.lyrics:
                        return self.lyrics
                except Exception:
                    pass

        # 2. Embedded lyrics in FLAC Vorbis comments
        if self.file_format == "FLAC" and FLAC is not None and self.path.exists():
            try:
                audio = FLAC(self.path)
                if audio.tags:
                    for k in ("lyrics", "LYRICS", "unsyncedlyrics", "UNSYNCEDLYRICS"):
                        if k in audio.tags and audio.tags[k]:
                            raw_text = str(audio.tags[k][0])
                            self.lyrics = parse_lrc(raw_text)
                            if self.lyrics:
                                return self.lyrics
            except Exception:
                pass

        # 3. Embedded lyrics in MP3 ID3 tags
        elif self.file_format == "MP3" and MP3 is not None and self.path.exists():
            try:
                audio = MP3(self.path)
                if audio.tags:
                    # Check USLT frames
                    try:
                        uslt_frames = audio.tags.getall("USLT")
                        for frame in uslt_frames:
                            if hasattr(frame, "text") and frame.text:
                                self.lyrics = parse_lrc(str(frame.text))
                                if self.lyrics:
                                    return self.lyrics
                    except Exception:
                        pass
                    # Check SYLT frames
                    try:
                        sylt_frames = audio.tags.getall("SYLT")
                        for frame in sylt_frames:
                            if hasattr(frame, "text") and frame.text:
                                sylt_lines = [
                                    LyricsLine(timestamp=ts / 1000.0, text=txt)
                                    for txt, ts in frame.text
                                    if txt.strip()
                                ]
                                if sylt_lines:
                                    sylt_lines.sort(key=lambda x: x.timestamp)
                                    self.lyrics = TrackLyrics(lines=sylt_lines, is_synced=True)
                                    return self.lyrics
                    except Exception:
                        pass
            except Exception:
                pass

        return None

    def save_cover_temp(self) -> Optional[Path]:
        """Save cover art to a temporary file for viewing."""
        if not self.has_cover or not self.cover_data:
            return None
        try:
            ext = ".png" if (self.cover_mime and "png" in self.cover_mime.lower()) else ".jpg"
            temp_dir = Path("/tmp/pulse_covers")
            temp_dir.mkdir(parents=True, exist_ok=True)
            safe_name = "".join(c for c in self.title if c.isalnum() or c in (" ", "-", "_")).strip()
            safe_name = safe_name.replace(" ", "_")[:32] or "track"
            h_suffix = hashlib.md5(str(self.path).encode("utf-8")).hexdigest()[:8]
            target = temp_dir / f"cover_{safe_name}_{h_suffix}{ext}"
            target.write_bytes(self.cover_data)
            return target
        except Exception:
            return None

    def _fetch_online_cover_async(self, query: str) -> None:
        """Fetch album art from online catalog in background thread without blocking UI."""
        def _worker():
            try:
                cache_dir = Path.home() / ".pulse-cli" / "cover_cache"
                cache_dir.mkdir(parents=True, exist_ok=True)
                q_hash = hashlib.md5(query.lower().encode("utf-8")).hexdigest()
                cached_img = cache_dir / f"{q_hash}.jpg"

                if cached_img.is_file():
                    raw = cached_img.read_bytes()
                    if len(raw) > 64:
                        self.cover_data = raw
                        self.cover_mime = "image/jpeg"
                        self.cover_source = "online"
                        if hasattr(self, "_pixel_art_cache"):
                            self._pixel_art_cache.clear()
                        return

                encoded_q = urllib.parse.quote(query)
                url = f"https://itunes.apple.com/search?term={encoded_q}&media=music&entity=song&limit=1"
                req = urllib.request.Request(url, headers={"User-Agent": "PulseCLI/1.0"})
                with urllib.request.urlopen(req, timeout=2.5) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    if payload.get("results"):
                        item = payload["results"][0]
                        art_url = item.get("artworkUrl100", "")
                        if art_url:
                            hi_res_url = art_url.replace("100x100bb", "600x600bb")
                            with urllib.request.urlopen(hi_res_url, timeout=3.0) as img_resp:
                                img_bytes = img_resp.read()
                                if len(img_bytes) > 256:
                                    self.cover_data = img_bytes
                                    self.cover_mime = "image/jpeg"
                                    self.cover_source = "online"
                                    if hasattr(self, "_pixel_art_cache"):
                                        self._pixel_art_cache.clear()
                                    try:
                                        cached_img.write_bytes(img_bytes)
                                    except Exception:
                                        pass
            except Exception:
                pass

        try:
            _COVER_EXECUTOR.submit(_worker)
        except Exception:
            pass

    @classmethod
    def clear_cache(cls) -> None:
        """Clear cached metadata instances."""
        _METADATA_CACHE.clear()

    @classmethod
    def from_path(cls, path: Path | str, fetch_online: bool = True) -> TrackMetadata:
        """Parse metadata and genuine cover art from an audio file with instant caching."""
        path = Path(path).resolve()
        file_size = 0
        mtime = 0.0
        try:
            st = path.stat()
            file_size = st.st_size
            mtime = st.st_mtime
        except OSError:
            pass

        cache_key = (str(path), file_size, mtime)
        if cache_key in _METADATA_CACHE:
            instance = _METADATA_CACHE[cache_key]
            _METADATA_CACHE.move_to_end(cache_key)
            return instance

        ext = path.suffix.lower()
        file_format = "FLAC" if ext == ".flac" else "MP3"
        title = path.stem.replace("_", " ").strip()
        artist = "Unknown Artist"
        album = "Unknown Album"
        year = ""
        genre = ""
        duration = 0.0
        bitrate = 0
        sample_rate = 44100
        channels = "Stereo"
        bits_per_sample = 16
        track_number: Optional[int] = None
        cover_data: Optional[bytes] = None
        cover_mime: Optional[str] = None
        cover_source: str = ""
        replay_gain_db: float = 0.0

        # -------------------------------------------------------------
        # 1. Parse Metadata & Embedded Artwork
        # -------------------------------------------------------------
        # Parse FLAC files
        if ext == ".flac" and FLAC is not None and path.exists():
            try:
                audio = FLAC(path)
                if audio.info:
                    duration = float(getattr(audio.info, "length", 0.0))
                    sample_rate = int(getattr(audio.info, "sample_rate", 44100))
                    raw_channels = getattr(audio.info, "channels", 2)
                    channels = "Mono" if raw_channels == 1 else ("Stereo" if raw_channels == 2 else f"{raw_channels}ch")
                    bits_per_sample = int(getattr(audio.info, "bits_per_sample", 16))
                    if duration > 0 and file_size > 0:
                        bitrate = int(round((file_size * 8) / (duration * 1000.0)))

                for k, v in audio.items():
                    kl = k.lower()
                    if kl == "title" and v:
                        title = _safe_tag_str(v)
                    elif kl == "artist" and v:
                        artist = _safe_tag_str(v)
                    elif kl == "albumartist" and v and artist == "Unknown Artist":
                        artist = _safe_tag_str(v)
                    elif kl == "album" and v:
                        album = _safe_tag_str(v)
                    elif kl in ("date", "year") and v:
                        year = _safe_tag_str(v)[:4]
                    elif kl == "genre" and v:
                        genre = _safe_tag_str(v)
                    elif kl in ("tracknumber", "track") and v:
                        try:
                            track_number = int(_safe_tag_str(v).split("/")[0])
                        except Exception:
                            pass
                    elif kl in ("replaygain_track_gain", "replaygain_album_gain") and v:
                        replay_gain_db = ReplayGain.parse_gain_db(_safe_tag_str(v))

                # Extract embedded pictures from FLAC blocks
                if getattr(audio, "pictures", None):
                    # Prefer front cover (type 3)
                    front = next((p for p in audio.pictures if getattr(p, "type", 0) == 3), audio.pictures[0])
                    if getattr(front, "data", None):
                        cover_data = front.data
                        cover_mime = getattr(front, "mime", "image/jpeg")
                        cover_source = "embedded"

                # Extract pictures from Vorbis comments (METADATA_BLOCK_PICTURE)
                if not cover_data and hasattr(audio, "tags") and audio.tags and Picture is not None:
                    for key in ("metadata_block_picture", "METADATA_BLOCK_PICTURE"):
                        if key in audio.tags:
                            for b64_str in audio.tags[key]:
                                try:
                                    raw = base64.b64decode(b64_str)
                                    pic = Picture(raw)
                                    if getattr(pic, "data", None):
                                        cover_data = pic.data
                                        cover_mime = getattr(pic, "mime", "image/jpeg")
                                        cover_source = "embedded"
                                        break
                                except Exception:
                                    pass
                            if cover_data:
                                break

                # Extract pictures from Vorbis comments (COVERART)
                if not cover_data and hasattr(audio, "tags") and audio.tags:
                    for key in ("coverart", "COVERART"):
                        if key in audio.tags:
                            for b64_str in audio.tags[key]:
                                try:
                                    raw = base64.b64decode(b64_str)
                                    if len(raw) > 64:
                                        cover_data = raw
                                        cover_mime = "image/jpeg"
                                        cover_source = "embedded"
                                        break
                                except Exception:
                                    pass
                            if cover_data:
                                break
            except Exception:
                pass

        # Parse MP3 files
        elif ext == ".mp3" and MP3 is not None and path.exists():
            try:
                audio = MP3(path)
                if audio.info:
                    duration = float(getattr(audio.info, "length", 0.0))
                    raw_bitrate = getattr(audio.info, "bitrate", 0)
                    bitrate = int(round(raw_bitrate / 1000.0)) if raw_bitrate else 0
                    sample_rate = int(getattr(audio.info, "sample_rate", 44100))
                    raw_channels = getattr(audio.info, "channels", 2)
                    channels = "Mono" if raw_channels == 1 else ("Stereo" if raw_channels == 2 else f"{raw_channels}ch")

                tags = audio.tags
                if tags:
                    for tag_key in ("TIT2", "title"):
                        val = tags.get(tag_key)
                        if val:
                            title = _safe_tag_str(val)
                            break

                    for tag_key in ("TPE1", "artist"):
                        val = tags.get(tag_key)
                        if val:
                            artist = _safe_tag_str(val)
                            break
                    if artist == "Unknown Artist":
                        for tag_key in ("TPE2", "albumartist"):
                            val = tags.get(tag_key)
                            if val:
                                artist = _safe_tag_str(val)
                                break

                    for tag_key in ("TALB", "album"):
                        val = tags.get(tag_key)
                        if val:
                            album = _safe_tag_str(val)
                            break

                    for tag_key in ("TDRC", "TYER", "date"):
                        val = tags.get(tag_key)
                        if val:
                            year = _safe_tag_str(val)[:4]
                            break

                    for tag_key in ("TCON", "genre"):
                        val = tags.get(tag_key)
                        if val:
                            genre = _safe_tag_str(val)
                            break

                    trck = tags.get("TRCK")
                    if trck:
                        try:
                            track_number = int(_safe_tag_str(trck).split("/")[0])
                        except Exception:
                            pass

                    # Parse ReplayGain tags
                    for rg_key in ("TXXX:replaygain_track_gain", "replaygain_track_gain", "TXXX:REPLAYGAIN_TRACK_GAIN"):
                        rg_tag = tags.get(rg_key)
                        if rg_tag:
                            replay_gain_db = ReplayGain.parse_gain_db(_safe_tag_str(rg_tag))
                            break

                    # Extract APIC / PIC cover art from ID3 tags
                    apic_frames = []
                    try:
                        apic_frames = tags.getall("APIC")
                    except Exception:
                        pass
                    if not apic_frames:
                        try:
                            apic_frames = tags.getall("PIC")
                        except Exception:
                            pass
                    if not apic_frames:
                        for tag in tags.values():
                            frame_id = getattr(tag, "FrameID", "")
                            if frame_id in ("APIC", "PIC"):
                                apic_frames.append(tag)

                    if apic_frames:
                        front = next((p for p in apic_frames if getattr(p, "type", 0) == 3), apic_frames[0])
                        if getattr(front, "data", None):
                            cover_data = front.data
                            cover_mime = getattr(front, "mime", "image/jpeg")
                            cover_source = "embedded"
            except Exception:
                pass

        # Generic Mutagen container fallback (e.g. M4A/AAC 'covr')
        if not cover_data and MutagenFile is not None and path.exists():
            try:
                mf = MutagenFile(path)
                if mf and getattr(mf, "tags", None):
                    covr = mf.tags.get("covr")
                    if covr and len(covr) > 0:
                        raw_covr = covr[0]
                        cover_data = bytes(raw_covr)
                        cover_mime = "image/jpeg" if getattr(raw_covr, "imageformat", None) == 13 else "image/png"
                        cover_source = "embedded"
            except Exception:
                pass

        # -------------------------------------------------------------
        # 2. Local Folder Artwork (Album Directory)
        # -------------------------------------------------------------
        if not cover_data and path.parent.exists():
            candidate_names = (
                "cover.jpg", "cover.jpeg", "cover.png",
                "folder.jpg", "folder.jpeg", "folder.png",
                "front.jpg", "front.jpeg", "front.png",
                "album.jpg", "album.jpeg", "album.png",
                "artwork.jpg", "artwork.jpeg", "artwork.png",
                f"{path.stem}.jpg", f"{path.stem}.jpeg", f"{path.stem}.png",
            )
            for name in candidate_names:
                cand = path.parent / name
                if cand.is_file():
                    try:
                        raw = cand.read_bytes()
                        if len(raw) > 64:
                            cover_data = raw
                            cover_mime = "image/png" if cand.suffix.lower() == ".png" else "image/jpeg"
                            cover_source = "folder"
                            break
                    except Exception:
                        pass

        # -------------------------------------------------------------
        # 3. Track Cover Matcher (Instant Local Cache & Non-blocking Online Fallback)
        # -------------------------------------------------------------
        query_for_online = ""
        if not cover_data:
            query = ""
            if artist and artist != "Unknown Artist" and title:
                query = f"{artist} {title}"
            elif title and title != path.stem:
                query = title
            else:
                clean_name = re.sub(r"\(\d+\)|\.mp3|\.flac", "", path.stem, flags=re.I)
                clean_name = clean_name.replace("_", " ").replace("-", " ").strip()
                if len(clean_name) >= 3 and not clean_name.isdigit():
                    query = clean_name

            if query:
                cache_dir = Path.home() / ".pulse-cli" / "cover_cache"
                try:
                    q_hash = hashlib.md5(query.lower().encode("utf-8")).hexdigest()
                    cached_img = cache_dir / f"{q_hash}.jpg"
                    if cached_img.is_file():
                        raw = cached_img.read_bytes()
                        if len(raw) > 64:
                            cover_data = raw
                            cover_mime = "image/jpeg"
                            cover_source = "online"
                    else:
                        query_for_online = query
                except Exception:
                    pass

        instance = cls(
            path=path,
            title=title,
            artist=artist,
            album=album,
            year=year,
            genre=genre,
            duration=duration,
            bitrate=bitrate,
            sample_rate=sample_rate,
            channels=channels,
            file_size_bytes=file_size,
            file_format=file_format,
            track_number=track_number,
            bits_per_sample=bits_per_sample,
            cover_data=cover_data,
            cover_mime=cover_mime,
            cover_source=cover_source,
            replay_gain_db=replay_gain_db,
        )

        # Trigger background online fetch without blocking from_path if needed
        if fetch_online and query_for_online and not instance.has_cover:
            instance._fetch_online_cover_async(query_for_online)

        if len(_METADATA_CACHE) >= MAX_METADATA_CACHE_ENTRIES:
            _METADATA_CACHE.popitem(last=False)
        _METADATA_CACHE[cache_key] = instance
        return instance
