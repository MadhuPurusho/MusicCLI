"""Main Textual Application for Pulse CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich.markdown import Markdown
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Static

from pulse.audio import AudioEngine, AudioState
from pulse.metadata import TrackMetadata
from pulse.queue_manager import QueueManager
from pulse.widgets.file_browser import FileBrowserWidget
from pulse.widgets.player_card import PlayerCardWidget
from pulse.widgets.queue_view import QueueViewWidget
from pulse.widgets.visualizer import VisualizerWidget


HELP_MARKDOWN = """
### ⚡ Playback Controls
- **`Space`**: Play / Pause audio
- **`←` / `→`**: Seek Backward / Forward **5s**
- **`Shift + ←` / `Shift + →`**: Seek Backward / Forward **15s**
- **`↑` / `↓`**: Volume Up / Down **5%**
- **`M`**: Mute / Unmute
- **`N`**: Skip to Next track
- **`P`**: Jump to Previous track
- **`R`**: Cycle Repeat mode (*Off* → *Track* → *Queue*)
- **`S`**: Toggle Shuffle mode (*On* / *Off*)

---

### 📁 Library & Navigation
- **`Enter`**: Play selected file or open folder
- **`A`**: Add selected MP3 or entire folder to queue
- **`/`**: Focus search filter input
- **`Esc`**: Clear search filter or close dialogs
- **`Tab`**: Switch focus between panels

---

### 📑 Queue Management
- **`Enter`** *(in Queue)*: Jump directly to selected track
- **`D`** / **`Delete`**: Remove track from queue
- **`C`**: Clear entire queue
- **`?`**: Toggle this help screen
- **`Q`**: Quit Pulse CLI
"""


class HelpModal(ModalScreen):
    """Overlay modal showing shortcuts and controls."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close", show=False),
        Binding("q", "dismiss", "Close", show=False),
        Binding("enter", "dismiss", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-container"):
            yield Static("⚡ [bold #00f0ff]PULSE CLI SHORTCUTS & HELP[/bold #00f0ff] ⚡", id="dialog-title")
            yield Static(Markdown(HELP_MARKDOWN), id="dialog-body")
            yield Button("Got it (Esc)", id="dialog-close-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()


class PulseApp(App):
    """Pulse CLI - The Modern macOS Terminal MP3 Player."""

    TITLE = "Pulse CLI"
    SUB_TITLE = "Terminal MP3 Player"
    CSS_PATH = "styles.tcss"

    BINDINGS = [
        Binding("space", "toggle_play", "Play/Pause", priority=True),
        Binding("left", "seek_backward", "Seek -5s"),
        Binding("right", "seek_forward", "Seek +5s"),
        Binding("shift+left", "seek_back_fast", "Seek -15s", show=False),
        Binding("shift+right", "seek_forward_fast", "Seek +15s", show=False),
        Binding("up", "volume_up", "Vol +5%"),
        Binding("down", "volume_down", "Vol -5%"),
        Binding("m", "toggle_mute", "Mute"),
        Binding("n", "next_track", "Next"),
        Binding("p", "prev_track", "Prev"),
        Binding("r", "cycle_repeat", "Repeat"),
        Binding("s", "toggle_shuffle", "Shuffle"),
        Binding("slash", "focus_search", "Search", priority=True),
        Binding("a", "queue_selected", "Add Queue"),
        Binding("d", "remove_from_queue", "Remove Track"),
        Binding("delete", "remove_from_queue", "Remove Track", show=False),
        Binding("c", "clear_queue", "Clear Queue"),
        Binding("question_mark", "show_help", "Help"),
        Binding("q", "quit_app", "Quit"),
    ]

    def __init__(self, initial_path: Optional[Path | str] = None, auto_play_file: Optional[Path | str] = None):
        super().__init__()
        self.initial_path = Path(initial_path).resolve() if initial_path else None
        self.auto_play_file = Path(auto_play_file).resolve() if auto_play_file else None

        self.audio = AudioEngine()
        self.queue = QueueManager()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main-container"):
            with Vertical(id="left-panel"):
                yield FileBrowserWidget(initial_path=self.initial_path, id="file-browser")
            with Vertical(id="right-panel"):
                yield PlayerCardWidget(id="player-card")
                yield VisualizerWidget(id="visualizer")
                yield QueueViewWidget(id="queue-view")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize UI timer and auto-play requested file if provided."""
        # 25 FPS update timer for fluid visualizer and progress bar
        self.set_interval(0.04, self.on_tick)

        if self.auto_play_file and self.auto_play_file.exists():
            meta = TrackMetadata.from_path(self.auto_play_file)
            self.queue.add_track(meta)
            self.queue.current_index = 0
            self._start_track(meta)
            self._sync_queue_view()

    def on_tick(self) -> None:
        """Periodic loop to update visualizer, scrubber, and check track transitions."""
        # Check if current track ended
        if self.audio.check_and_clear_finished():
            self._handle_track_finished()

        # Audio progress and spectrum metrics
        curr_sec, tot_sec, pct = self.audio.get_progress()
        levels = self.audio.get_visualizer_levels()
        is_playing = (self.audio.state == AudioState.PLAYING)

        # Update visualizer
        vis = self.query_one("#visualizer", VisualizerWidget)
        vis.update_visualizer(levels, is_playing)

        # Update player card
        card = self.query_one("#player-card", PlayerCardWidget)
        card.update_player(
            track=self.queue.current_track,
            state=self.audio.state,
            current_sec=curr_sec,
            total_sec=tot_sec,
            pct=pct,
            volume=self.audio.volume,
            muted=self.audio.is_muted,
            repeat_label=self.queue.repeat_mode.label,
            shuffle=self.queue.is_shuffled,
        )

    def _start_track(self, track: TrackMetadata) -> None:
        """Load and start playback of a track."""
        success = self.audio.load_track(track)
        if success:
            self.notify(f"Playing: {track.title}", title="Pulse CLI", severity="information")
        else:
            self.notify(f"Could not decode: {track.path.name}", title="Playback Error", severity="error")
        self._sync_queue_view()

    def _handle_track_finished(self) -> None:
        """Automatically advance to next track when finished."""
        next_t = self.queue.next_track()
        if next_t:
            self._start_track(next_t)
        else:
            self.audio.stop()
            self._sync_queue_view()

    def _sync_queue_view(self) -> None:
        """Update queue widget display."""
        queue_view = self.query_one("#queue-view", QueueViewWidget)
        queue_view.update_queue(self.queue.tracks, self.queue.current_index)

    # --- Event Handlers from Child Widgets ---

    def on_file_browser_widget_track_play_requested(self, event: FileBrowserWidget.TrackPlayRequested) -> None:
        """Handle immediate play request from file browser."""
        meta = TrackMetadata.from_path(event.path)
        idx = self.queue.add_track(meta)
        self.queue.current_index = idx
        self._start_track(meta)

    def on_file_browser_widget_track_queue_requested(self, event: FileBrowserWidget.TrackQueueRequested) -> None:
        """Handle queue request for a file or entire directory."""
        path = event.path
        added_count = 0

        if path.is_file() and path.suffix.lower() == ".mp3":
            meta = TrackMetadata.from_path(path)
            self.queue.add_track(meta)
            added_count = 1
        elif path.is_dir():
            # Scan directory for all MP3s
            mp3_files = sorted(
                list(path.glob("*.mp3")) + list(path.glob("*.MP3"))
            )
            for f in mp3_files:
                meta = TrackMetadata.from_path(f)
                self.queue.add_track(meta)
                added_count += 1

        if added_count > 0:
            self.notify(f"Added {added_count} track(s) to queue", title="Queue")
            # If nothing currently playing, start the first queued item
            if self.audio.state == AudioState.STOPPED and self.queue.current_index < 0:
                self.queue.current_index = 0
                first_track = self.queue.current_track
                if first_track:
                    self._start_track(first_track)
            self._sync_queue_view()

    def on_queue_view_widget_track_selected(self, event: QueueViewWidget.TrackSelected) -> None:
        """Handle direct jump to track from queue table."""
        track = self.queue.jump_to(event.index)
        if track:
            self._start_track(track)

    def on_queue_view_widget_track_remove_requested(self, event: QueueViewWidget.TrackRemoveRequested) -> None:
        """Handle track removal from queue table."""
        removed = self.queue.remove_at(event.index)
        if removed:
            self.notify(f"Removed: {removed.title}", title="Queue")
            self._sync_queue_view()

    def on_queue_view_widget_clear_queue_requested(self, event: QueueViewWidget.ClearQueueRequested) -> None:
        """Handle queue clearing."""
        self.queue.clear()
        self.audio.stop()
        self.notify("Queue cleared", title="Queue")
        self._sync_queue_view()

    # --- App Actions & Keybindings ---

    def action_toggle_play(self) -> None:
        """Toggle playback."""
        if self.audio.state == AudioState.STOPPED:
            track = self.queue.current_track
            if track:
                self._start_track(track)
            elif self.queue.count > 0:
                self.queue.current_index = 0
                self._start_track(self.queue.tracks[0])
            else:
                self.notify("No song loaded. Select an MP3 from explorer.", title="Pulse")
        else:
            self.audio.toggle_play_pause()

    def action_seek_forward(self) -> None:
        new_pos = self.audio.seek(5.0)

    def action_seek_backward(self) -> None:
        new_pos = self.audio.seek(-5.0)

    def action_seek_forward_fast(self) -> None:
        new_pos = self.audio.seek(15.0)

    def action_seek_back_fast(self) -> None:
        new_pos = self.audio.seek(-15.0)

    def action_volume_up(self) -> None:
        vol = self.audio.change_volume(0.05)
        self.notify(f"Volume: {int(vol * 100)}%", title="Audio", timeout=1.0)

    def action_volume_down(self) -> None:
        vol = self.audio.change_volume(-0.05)
        self.notify(f"Volume: {int(vol * 100)}%", title="Audio", timeout=1.0)

    def action_toggle_mute(self) -> None:
        muted = self.audio.toggle_mute()
        msg = "Audio Muted" if muted else f"Audio Unmuted ({int(self.audio.volume * 100)}%)"
        self.notify(msg, title="Audio", timeout=1.0)

    def action_next_track(self) -> None:
        track = self.queue.next_track()
        if track:
            self._start_track(track)
        else:
            self.notify("Reached end of queue", title="Queue")

    def action_prev_track(self) -> None:
        track = self.queue.prev_track()
        if track:
            self._start_track(track)

    def action_cycle_repeat(self) -> None:
        mode = self.queue.toggle_repeat()
        self.notify(f"{mode.label}", title="Playback Mode", timeout=1.5)

    def action_toggle_shuffle(self) -> None:
        shuf = self.queue.toggle_shuffle()
        msg = "Shuffle: On" if shuf else "Shuffle: Off"
        self.notify(msg, title="Playback Mode", timeout=1.5)

    def action_focus_search(self) -> None:
        browser = self.query_one("#file-browser", FileBrowserWidget)
        browser.focus_search()

    def action_queue_selected(self) -> None:
        browser = self.query_one("#file-browser", FileBrowserWidget)
        browser.queue_current_selection()

    def action_remove_from_queue(self) -> None:
        queue_widget = self.query_one("#queue-view", QueueViewWidget)
        queue_widget.remove_selected()

    def action_clear_queue(self) -> None:
        queue_widget = self.query_one("#queue-view", QueueViewWidget)
        queue_widget.clear_queue()

    def action_show_help(self) -> None:
        self.push_screen(HelpModal())

    def action_quit_app(self) -> None:
        self.audio.close()
        self.exit()

    def on_unmount(self) -> None:
        self.audio.close()
