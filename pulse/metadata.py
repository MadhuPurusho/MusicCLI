"""Audio metadata extraction for MP3 files using Mutagen."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3NoHeaderError
except ImportError:
    MP3 = None
    ID3NoHeaderError = Exception


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
    """Metadata details for an MP3 track."""

    path: Path
    title: str
    artist: str
    album: str
    year: str
    genre: str
    duration: float  # in seconds
    bitrate: int     # in kbps
    sample_rate: int # in Hz
    channels: str    # "Stereo" or "Mono"
    file_size_bytes: int

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

    @classmethod
    def from_path(cls, path: Path | str) -> TrackMetadata:
        """Parse metadata from an MP3 file with safe fallbacks."""
        path = Path(path).resolve()
        file_size = 0
        try:
            file_size = os.path.getsize(path)
        except OSError:
            pass

        title = path.stem.replace("_", " ").strip()
        artist = "Unknown Artist"
        album = "Unknown Album"
        year = ""
        genre = ""
        duration = 0.0
        bitrate = 0
        sample_rate = 44100
        channels = "Stereo"

        if MP3 is not None and path.exists():
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
                    # Title
                    for tag_key in ("TIT2", "title"):
                        val = tags.get(tag_key)
                        if val:
                            title = str(val[0]).strip()
                            break

                    # Artist
                    for tag_key in ("TPE1", "artist", "albumartist"):
                        val = tags.get(tag_key)
                        if val:
                            artist = str(val[0]).strip()
                            break

                    # Album
                    for tag_key in ("TALB", "album"):
                        val = tags.get(tag_key)
                        if val:
                            album = str(val[0]).strip()
                            break

                    # Year
                    for tag_key in ("TDRC", "TYER", "date"):
                        val = tags.get(tag_key)
                        if val:
                            year = str(val[0]).strip()
                            if len(year) > 4:
                                year = year[:4]
                            break

                    # Genre
                    for tag_key in ("TCON", "genre"):
                        val = tags.get(tag_key)
                        if val:
                            genre = str(val[0]).strip()
                            break
            except Exception:
                # Fallback to filename-based inference
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
        )
