"""Interactive playback queue and playlist data table."""

from __future__ import annotations

from typing import List, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import DataTable, Static

from pulse.metadata import TrackMetadata


class QueueViewWidget(Vertical):
    """Interactive playlist and upcoming queue table."""

    DEFAULT_CSS = """
    QueueViewWidget {
        height: 1fr;
        width: 100%;
        border: round $primary-darken-1;
        padding: 0 1;
        background: $surface;
    }

    #queue-header {
        height: 1;
        dock: top;
        color: #00f0ff;
        text-style: bold;
        margin-bottom: 1;
    }

    #queue-table {
        height: 1fr;
        background: $surface;
    }

    #queue-footer {
        height: 1;
        dock: bottom;
        color: $text-muted;
        background: $surface-darken-1;
        padding: 0 1;
    }
    """

    class TrackSelected(Message):
        """User pressed enter to jump to track in queue."""
        def __init__(self, index: int):
            super().__init__()
            self.index = index

    class TrackRemoveRequested(Message):
        """User requested removing track from queue."""
        def __init__(self, index: int):
            super().__init__()
            self.index = index

    class ClearQueueRequested(Message):
        """User requested clearing queue."""
        pass

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._tracks: List[TrackMetadata] = []
        self._current_index: int = -1

    def compose(self) -> ComposeResult:
        yield Static("📑 PLAYBACK QUEUE (0 tracks)", id="queue-header")
        yield DataTable(id="queue-table", cursor_type="row", zebra_stripes=True)
        yield Static("[Enter] Play  [D] Remove  [C] Clear", id="queue-footer")

    def on_mount(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        table.add_columns("#", " ", "Title", "Artist", "Length")

    def update_queue(self, tracks: List[TrackMetadata], current_index: int) -> None:
        """Repopulate table with current queue items."""
        self._tracks = tracks
        self._current_index = current_index

        header = self.query_one("#queue-header", Static)
        header.update(f"📑 [bold #00f0ff]PLAYBACK QUEUE[/bold #00f0ff] ({len(tracks)} tracks)")

        table = self.query_one("#queue-table", DataTable)
        saved_row = table.cursor_row
        table.clear()

        for idx, track in enumerate(tracks):
            is_active = (idx == current_index)
            num_str = f"{idx + 1}"
            status_icon = Text("▶", style="bold #48bb78") if is_active else Text(" ", style="#718096")
            title_text = Text(track.title, style="bold #00ffcc" if is_active else "#e2e8f0")
            artist_text = Text(track.artist, style="#63b3ed" if is_active else "#a0aec0")
            duration_text = Text(track.formatted_duration, style="#81e6d9" if is_active else "#718096")

            table.add_row(num_str, status_icon, title_text, artist_text, duration_text, key=str(idx))

        if saved_row < len(tracks):
            table.cursor_coordinate = (saved_row, 0)
        elif tracks:
            table.cursor_coordinate = (0, 0)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle selection on row to jump directly to track."""
        if event.cursor_row is not None and 0 <= event.cursor_row < len(self._tracks):
            self.post_message(self.TrackSelected(event.cursor_row))

    def remove_selected(self) -> None:
        """Remove currently selected row in queue."""
        table = self.query_one("#queue-table", DataTable)
        if table.cursor_row is not None and 0 <= table.cursor_row < len(self._tracks):
            self.post_message(self.TrackRemoveRequested(table.cursor_row))

    def clear_queue(self) -> None:
        """Post request to clear all queue items."""
        self.post_message(self.ClearQueueRequested())
