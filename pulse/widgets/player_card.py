"""Now Playing track information, progress scrubber, and audio state card."""

from __future__ import annotations

from typing import Optional

from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from pulse.audio import AudioState
from pulse.metadata import TrackMetadata, format_duration


class PlayerCardWidget(Static):
    """Card displaying current track details, progress bar, and status badges."""

    DEFAULT_CSS = """
    PlayerCardWidget {
        height: auto;
        width: 100%;
        padding: 0 1;
        margin-bottom: 1;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._current_track: Optional[TrackMetadata] = None
        self._state: AudioState = AudioState.STOPPED
        self._current_sec: float = 0.0
        self._total_sec: float = 0.0
        self._pct: float = 0.0
        self._volume: float = 0.85
        self._muted: bool = False
        self._repeat_label: str = "Repeat: Off"
        self._shuffle: bool = False

    def update_player(
        self,
        track: Optional[TrackMetadata],
        state: AudioState,
        current_sec: float,
        total_sec: float,
        pct: float,
        volume: float,
        muted: bool,
        repeat_label: str,
        shuffle: bool,
    ) -> None:
        """Update the player card with current state and trigger refresh."""
        self._current_track = track
        self._state = state
        self._current_sec = current_sec
        self._total_sec = total_sec
        self._pct = pct
        self._volume = volume
        self._muted = muted
        self._repeat_label = repeat_label
        self._shuffle = shuffle
        self.refresh()

    def render(self) -> Panel:
        """Render the complete Now Playing panel."""
        track = self._current_track

        # Title & Artist
        if track:
            title_text = Text(f"🎵  {track.title}", style="bold #00ffcc")
            artist_album = f"{track.artist}  •  {track.album}"
            if track.year:
                artist_album += f" ({track.year})"
            subtitle_text = Text(artist_album, style="#a0aec0")
        else:
            title_text = Text("🎵  No Track Selected", style="bold #718096")
            subtitle_text = Text("Browse files on the left and press [Enter] to play", style="#4a5568")

        # Badges row
        badges = Text()
        if track:
            if track.bitrate > 0:
                badges.append(f" {track.bitrate} kbps ", style="on #2d3748 #63b3ed bold")
                badges.append(" ")
            if track.sample_rate > 0:
                badges.append(f" {track.sample_rate / 1000:.1f} kHz ", style="on #2d3748 #81e6d9")
                badges.append(" ")
            badges.append(f" {track.channels} ", style="on #2d3748 #b794f4")
            badges.append(" ")
            if track.formatted_size:
                badges.append(f" {track.formatted_size} ", style="on #2d3748 #cbd5e0")
        else:
            badges.append(" Ready ", style="on #1a202c #718096")

        # Progress bar
        curr_str = format_duration(self._current_sec)
        tot_str = format_duration(self._total_sec)
        rem_sec = max(0.0, self._total_sec - self._current_sec)
        rem_str = f"-{format_duration(rem_sec)}"

        prog_text = Text()
        prog_text.append(f"{curr_str} ", style="bold #00f0ff")

        bar_width = 38
        if self._total_sec > 0:
            ratio = max(0.0, min(1.0, self._current_sec / self._total_sec))
            pos = int(ratio * (bar_width - 1))
            prog_text.append("━" * pos, style="#00f0ff")
            prog_text.append("●", style="bold #ff007f")
            prog_text.append("─" * (bar_width - 1 - pos), style="#4a5568")
        else:
            prog_text.append("─" * bar_width, style="#2d3748")

        prog_text.append(f" {tot_str} ", style="bold #e2e8f0")
        prog_text.append(f"({rem_str})  ", style="#718096")
        prog_text.append(f"{self._pct:4.1f}%", style="#81e6d9 bold")

        # Status & Controls row
        status_text = Text()

        # Playback status badge
        if self._state == AudioState.PLAYING:
            status_text.append(" [ ▶ PLAYING ] ", style="on #22543d #48bb78 bold")
        elif self._state == AudioState.PAUSED:
            status_text.append(" [ ⏸ PAUSED ] ", style="on #744210 #ecc94b bold")
        else:
            status_text.append(" [ ⏹ STOPPED ] ", style="on #1a202c #718096")

        status_text.append("   ")

        # Volume badge & bar
        if self._muted:
            status_text.append("🔇 MUTED ", style="bold #f56565")
        else:
            vol_pct = int(self._volume * 100)
            vol_blocks = int(self._volume * 8)
            vol_bar = "█" * vol_blocks + "░" * (8 - vol_blocks)
            status_text.append(f"🔊 {vol_pct:3d}% [{vol_bar}] ", style="bold #63b3ed")

        status_text.append("  ")
        # Loop badge
        status_text.append(f"[ 🔁 {self._repeat_label} ] ", style="#b794f4")
        # Shuffle badge
        shuf_style = "bold #68d391" if self._shuffle else "#718096"
        status_text.append(f"[ 🔀 Shuffle: {'On' if self._shuffle else 'Off'} ]", style=shuf_style)

        # Assemble into clean table layout
        table = Table.grid(padding=(0, 0))
        table.add_column()
        table.add_row(title_text)
        table.add_row(subtitle_text)
        table.add_row(Text(""))
        table.add_row(badges)
        table.add_row(Text(""))
        table.add_row(prog_text)
        table.add_row(Text(""))
        table.add_row(status_text)

        return Panel(
            table,
            title="[bold #00f0ff]NOW PLAYING[/bold #00f0ff]",
            border_style="#00f0ff",
            padding=(1, 2),
        )
