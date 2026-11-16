"""Audio metadata and album cover art extraction for MP3 and FLAC files."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

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


def format_duration(seconds: float | int) -> str:
    """Format seconds into MM:SS or HH:MM:SS format."""
    if not seconds or seconds < 0:
        return "00:00"
    seconds = int(round(seconds))
    hrs = seconds // 3600
    mins = (seconds % 3600) // 60
    secs = seconds % 60
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def format_file_size(num_bytes: int) -> str:
    """Format byte size into human readable string."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    elif num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    elif num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"


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
    channels: str        # "Stereo" or "Mono"
    file_size_bytes: int
    file_format: str = "MP3"
    cover_data: Optional[bytes] = None
    cover_mime: Optional[str] = None
    cover_source: str = ""  # "embedded", "folder", "online", or ""

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
        """Clean 1-bit monochrome frame when track has no cover."""
        if height <= 2:
            return [" " * width] * height
        top = "┌" + ("─" * max(0, width - 2)) + "┐"
        bot = "└" + ("─" * max(0, width - 2)) + "┘"
        inner_h = max(0, height - 2)
        lines = [top]
        mid = inner_h // 2
        for r in range(inner_h):
            if r == mid and inner_h >= 2:
                txt = "NO COVER".center(width - 2)
            elif r == mid + 1 and inner_h >= 3:
                txt = "ART".center(width - 2)
            else:
                txt = " " * (width - 2)
            lines.append("│" + txt + "│")
        lines.append(bot)
        return lines[:height]

    def get_pixel_art(self, width: int = 14, height: int = 5) -> List[str]:
        """Generate a 1-bit monochrome pixel-art rendition of the actual album cover."""
        if not hasattr(self, "_pixel_art_cache"):
            self._pixel_art_cache = {}
        cache_key = (width, height)
        if cache_key in self._pixel_art_cache:
            return self._pixel_art_cache[cache_key]

        if not self.has_cover:
            res = TrackMetadata.empty_pixel_art(width, height)
            self._pixel_art_cache[cache_key] = res
            return res

        try:
            from PIL import Image, ImageOps

            im = Image.open(io.BytesIO(self.cover_data)).convert("L")
            im = ImageOps.autocontrast(im)
            resized = im.resize((width, height * 2), Image.Resampling.LANCZOS)
            dithered = resized.convert("1")

            lines = []
            for y in range(0, height * 2, 2):
                row = []
                for x in range(width):
                    top_px = (dithered.getpixel((x, y)) > 0)
                    bot_px = (dithered.getpixel((x, y + 1)) > 0)
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

    def save_cover_temp(self) -> Optional[Path]:
        """Save cover art to a temporary file for viewing in macOS Preview."""
        if not self.has_cover or not self.cover_data:
            return None
        try:
            ext = ".png" if (self.cover_mime and "png" in self.cover_mime.lower()) else ".jpg"
            temp_dir = Path("/tmp/pulse_covers")
            temp_dir.mkdir(parents=True, exist_ok=True)
            safe_name = "".join(c for c in self.title if c.isalnum() or c in (" ", "-", "_")).strip()
            safe_name = safe_name.replace(" ", "_")[:32] or "track"
            target = temp_dir / f"cover_{safe_name}{ext}"
            target.write_bytes(self.cover_data)
            return target
        except Exception:
            return None

    @classmethod
    def from_path(cls, path: Path | str) -> TrackMetadata:
        """Parse metadata and genuine cover art from an audio file."""
        path = Path(path).resolve()
        file_size = 0
        try:
            file_size = os.path.getsize(path)
        except OSError:
            pass

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
        cover_data: Optional[bytes] = None
        cover_mime: Optional[str] = None
        cover_source: str = ""

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
                    channels = "Mono" if raw_channels == 1 else "Stereo"
                    if duration > 0 and file_size > 0:
                        bitrate = int(round((file_size * 8) / (duration * 1000.0)))

                for k, v in audio.items():
                    kl = k.lower()
                    if kl == "title" and v:
                        title = str(v[0]).strip()
                    elif kl in ("artist", "albumartist") and v:
                        artist = str(v[0]).strip()
                    elif kl == "album" and v:
                        album = str(v[0]).strip()
                    elif kl in ("date", "year") and v:
                        year = str(v[0]).strip()[:4]
                    elif kl == "genre" and v:
                        genre = str(v[0]).strip()

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
                    channels = "Mono" if raw_channels == 1 else "Stereo"

                tags = audio.tags
                if tags:
                    for tag_key in ("TIT2", "title"):
                        val = tags.get(tag_key)
                        if val:
                            title = str(val[0]).strip()
                            break

                    for tag_key in ("TPE1", "artist", "albumartist"):
                        val = tags.get(tag_key)
                        if val:
                            artist = str(val[0]).strip()
                            break

                    for tag_key in ("TALB", "album"):
                        val = tags.get(tag_key)
                        if val:
                            album = str(val[0]).strip()
                            break

                    for tag_key in ("TDRC", "TYER", "date"):
                        val = tags.get(tag_key)
                        if val:
                            year = str(val[0]).strip()[:4]
                            break

                    for tag_key in ("TCON", "genre"):
                        val = tags.get(tag_key)
                        if val:
                            genre = str(val[0]).strip()
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
                        # Prefer type 3 (Front Cover)
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
        # 3. Track Cover Matcher (Online Fallback for Known Songs)
        # -------------------------------------------------------------
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
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    q_hash = hashlib.md5(query.lower().encode("utf-8")).hexdigest()
                    cached_img = cache_dir / f"{q_hash}.jpg"

                    if cached_img.is_file():
                        try:
                            raw = cached_img.read_bytes()
                            if len(raw) > 64:
                                cover_data = raw
                                cover_mime = "image/jpeg"
                                cover_source = "online"
                        except Exception:
                            pass
                    else:
                        # Quick lookup from official music database
                        encoded_q = urllib.parse.quote(query)
                        url = f"https://itunes.apple.com/search?term={encoded_q}&media=music&entity=song&limit=1"
                        req = urllib.request.Request(url, headers={"User-Agent": "PulseCLI/1.0"})
                        with urllib.request.urlopen(req, timeout=1.5) as resp:
                            payload = json.loads(resp.read().decode("utf-8"))
                            if payload.get("results"):
                                item = payload["results"][0]
                                art_url = item.get("artworkUrl100", "")
                                if art_url:
                                    hi_res_url = art_url.replace("100x100bb", "600x600bb")
                                    with urllib.request.urlopen(hi_res_url, timeout=2.0) as img_resp:
                                        img_bytes = img_resp.read()
                                        if len(img_bytes) > 256:
                                            cover_data = img_bytes
                                            cover_mime = "image/jpeg"
                                            cover_source = "online"
                                            try:
                                                cached_img.write_bytes(img_bytes)
                                            except Exception:
                                                pass
                except Exception:
                    pass

        return cls(
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
            cover_data=cover_data,
            cover_mime=cover_mime,
            cover_source=cover_source,
        )
