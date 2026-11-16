"""Audio metadata and embedded cover art extraction for MP3 and FLAC files."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

try:
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC
    from mutagen.id3 import ID3NoHeaderError
except ImportError:
    MP3 = None
    FLAC = None
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
        """Default retro 1-bit vinyl disc placeholder."""
        cassette = [
            "┌──────────────┐",
            "│  ▄████████▄  │",
            "│ ███▀██▀▀███  │",
            "│ ███▄██▄▄███  │",
            "│  ▀████████▀  │",
            "└──────────────┘",
        ]
        res = []
        for l in cassette[:height]:
            res.append(l.center(width))
        while len(res) < height:
            res.append(" " * width)
        return res

    def get_pixel_art(self, width: int = 14, height: int = 5) -> List[str]:
        """Generate a 1-bit monochrome pixel-art rendition of embedded album cover."""
        if not self.has_cover:
            return TrackMetadata.empty_pixel_art(width, height)

        try:
            from PIL import Image

            im = Image.open(io.BytesIO(self.cover_data)).convert("L")
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
            return lines
        except Exception:
            return ["  [NO ART]  ".center(width)] * height

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
        """Parse metadata and cover art from an MP3 or FLAC file."""
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
        cover_data = None
        cover_mime = None

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

                if audio.pictures:
                    # Prefer front cover (type 3)
                    front = next((p for p in audio.pictures if p.type == 3), audio.pictures[0])
                    cover_data = front.data
                    cover_mime = getattr(front, "mime", "image/jpeg")
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

                    # Extract APIC cover art
                    for tag in tags.values():
                        if getattr(tag, "FrameID", None) == "APIC":
                            cover_data = getattr(tag, "data", None)
                            cover_mime = getattr(tag, "mime", "image/jpeg")
                            break
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
        )
