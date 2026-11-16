"""Pure Unix terminal interface for Pulse CLI using standard curses."""

from __future__ import annotations

import curses
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

from pulse.audio import AudioEngine, AudioState
from pulse.metadata import TrackMetadata, format_duration
from pulse.queue_manager import QueueManager, RepeatMode


class PulseApp:
    """Pure terminal curses music player and library explorer."""

    BLOCKS = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

    def __init__(self, initial_path: Optional[Path | str] = None, auto_play_file: Optional[Path | str] = None):
        self.initial_path = Path(initial_path).resolve() if initial_path else None
        self.auto_play_file = Path(auto_play_file).resolve() if auto_play_file else None

        # Resolve starting directory
        if self.initial_path and self.initial_path.is_dir():
            self.current_dir = self.initial_path
        elif self.auto_play_file:
            self.current_dir = self.auto_play_file.parent
        else:
            music_dir = Path.home() / "Music"
            self.current_dir = music_dir if music_dir.exists() else Path.cwd()

        self.audio = AudioEngine()
        self.queue = QueueManager()

        # Browser state
        self.browser_items: List[Tuple[str, Path, bool]] = []  # (display_name, path, is_dir)
        self.browser_cursor: int = 0
        self.browser_scroll: int = 0
        self.filter_query: str = ""
        self.is_filtering: bool = False

        # Queue view state
        self.queue_cursor: int = 0
        self.queue_scroll: int = 0

        # Active pane: 0 = Browser, 1 = Queue
        self.active_pane: int = 0
        self.show_help: bool = False
        self.status_message: str = ""
        self.status_time: float = 0.0

        # Initial populate
        self._load_directory(self.current_dir)

        if self.auto_play_file and self.auto_play_file.exists():
            meta = TrackMetadata.from_path(self.auto_play_file)
            self.queue.add_track(meta)
            self.queue.current_index = 0
            self.audio.load_track(meta)

    def set_status(self, msg: str) -> None:
        self.status_message = msg
        self.status_time = time.time()

    def _load_directory(self, path: Path) -> None:
        """Scan directory for subdirectories and MP3 files."""
        self.current_dir = path.resolve()
        items: List[Tuple[str, Path, bool]] = []

        # Add parent directory entry if not at root
        if self.current_dir.parent != self.current_dir:
            items.append((".. (parent directory)", self.current_dir.parent, True))

        dirs: List[Tuple[str, Path, bool]] = []
        files: List[Tuple[str, Path, bool]] = []

        try:
            for entry in self.current_dir.iterdir():
                name = entry.name
                if name.startswith(".") or name in ("node_modules", "__pycache__", "venv", ".venv"):
                    continue

                if self.filter_query and self.filter_query.lower() not in name.lower():
                    continue

                if entry.is_dir():
                    dirs.append((f"{name}/", entry, True))
                elif entry.is_file() and entry.suffix.lower() == ".mp3":
                    files.append((name, entry, False))
        except (PermissionError, OSError):
            self.set_status(f"Permission denied: {self.current_dir}")

        dirs.sort(key=lambda x: x[0].lower())
        files.sort(key=lambda x: x[0].lower())

        self.browser_items = items + dirs + files
        self.browser_cursor = min(self.browser_cursor, max(0, len(self.browser_items) - 1))
        self.browser_scroll = 0

    def run(self) -> None:
        """Launch curses wrapper."""
        try:
            curses.wrapper(self._main_loop)
        finally:
            self.audio.close()

    def _init_colors(self) -> None:
        """Configure curses terminal color palette."""
        curses.start_color()
        try:
            curses.use_default_colors()
            bg = -1
        except Exception:
            bg = curses.COLOR_BLACK

        curses.init_pair(1, curses.COLOR_CYAN, bg)      # Accent / Primary
        curses.init_pair(2, curses.COLOR_GREEN, bg)     # Playing / Success
        curses.init_pair(3, curses.COLOR_YELLOW, bg)    # Directories / Warnings
        curses.init_pair(4, curses.COLOR_MAGENTA, bg)   # Metadata
        curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLUE)   # Active cursor
        curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_CYAN)   # Top/Bottom status bar
        curses.init_pair(7, curses.COLOR_WHITE, curses.COLOR_BLACK)  # Inactive cursor

    def _main_loop(self, stdscr) -> None:
        """Main curses event and redraw loop."""
        self.stdscr = stdscr
        curses.curs_set(0)
        stdscr.timeout(40)  # 25 FPS non-blocking polling
        self._init_colors()

        while True:
            # Check for track auto-advancement
            if self.audio.check_and_clear_finished():
                next_t = self.queue.next_track()
                if next_t:
                    self.audio.load_track(next_t)
                else:
                    self.audio.stop()

            # Render screen frame
            self._render()

            # Process input
            try:
                ch = stdscr.getch()
            except Exception:
                ch = -1

            if ch != -1:
                if not self._handle_input(ch):
                    break

    def _handle_input(self, ch: int) -> bool:
        """Handle key input. Return False to exit."""
        if self.show_help:
            self.show_help = False
            return True

        if self.is_filtering:
            return self._handle_filter_input(ch)

        # Global navigation & shortcuts
        if ch in (ord('q'), ord('Q')):
            return False

        elif ch == ord('?'):
            self.show_help = True

        elif ch == ord(' '):
            if self.audio.state == AudioState.STOPPED:
                track = self.queue.current_track
                if track:
                    self.audio.load_track(track)
                elif self.queue.count > 0:
                    self.queue.current_index = 0
                    self.audio.load_track(self.queue.tracks[0])
            else:
                self.audio.toggle_play_pause()

        elif ch == ord('\t'):
            # Toggle between Browser (0) and Queue (1)
            self.active_pane = 1 - self.active_pane

        elif ch in (ord('/'), ):
            self.is_filtering = True
            self.filter_query = ""

        # Volume controls (+ / - / [ / ])
        elif ch in (ord('+'), ord('='), ord(']')):
            vol = self.audio.change_volume(0.05)
            self.set_status(f"Volume: {int(vol * 100)}%")

        elif ch in (ord('-'), ord('_'), ord('[')):
            vol = self.audio.change_volume(-0.05)
            self.set_status(f"Volume: {int(vol * 100)}%")

        elif ch in (ord('m'), ord('M')):
            muted = self.audio.toggle_mute()
            self.set_status("Muted" if muted else f"Unmuted ({int(self.audio.volume * 100)}%)")

        # Seeking (Left / Right or , / .)
        elif ch in (curses.KEY_LEFT, ord(',')):
            self.audio.seek(-5.0)

        elif ch in (curses.KEY_RIGHT, ord('.')):
            self.audio.seek(5.0)

        # Track skipping
        elif ch in (ord('n'), ord('N')):
            next_t = self.queue.next_track()
            if next_t:
                self.audio.load_track(next_t)
            else:
                self.set_status("End of queue reached")

        elif ch in (ord('p'), ord('P')):
            prev_t = self.queue.prev_track()
            if prev_t:
                self.audio.load_track(prev_t)

        elif ch in (ord('r'), ord('R')):
            mode = self.queue.toggle_repeat()
            self.set_status(f"{mode.label}")

        elif ch in (ord('s'), ord('S')):
            shuf = self.queue.toggle_shuffle()
            self.set_status(f"Shuffle: {'On' if shuf else 'Off'}")

        # Pane-specific inputs
        if self.active_pane == 0:
            self._handle_browser_input(ch)
        else:
            self._handle_queue_input(ch)

        return True

    def _handle_filter_input(self, ch: int) -> bool:
        """Handle input while live search filter prompt is active."""
        if ch in (10, 13, curses.KEY_ENTER):
            self.is_filtering = False
            self._load_directory(self.current_dir)
        elif ch in (27, ):  # Escape
            self.is_filtering = False
            self.filter_query = ""
            self._load_directory(self.current_dir)
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            self.filter_query = self.filter_query[:-1]
            self._load_directory(self.current_dir)
        elif 32 <= ch <= 126:
            self.filter_query += chr(ch)
            self._load_directory(self.current_dir)
        return True

    def _handle_browser_input(self, ch: int) -> None:
        """Handle cursor and actions inside File Browser pane."""
        count = len(self.browser_items)
        if count == 0:
            if ch in (curses.KEY_BACKSPACE, 127, 8, ord('h')):
                if self.current_dir.parent != self.current_dir:
                    self._load_directory(self.current_dir.parent)
            return

        if ch in (curses.KEY_UP, ord('k')):
            self.browser_cursor = max(0, self.browser_cursor - 1)

        elif ch in (curses.KEY_DOWN, ord('j')):
            self.browser_cursor = min(count - 1, self.browser_cursor + 1)

        elif ch == curses.KEY_PPAGE:  # Page Up
            self.browser_cursor = max(0, self.browser_cursor - 10)

        elif ch == curses.KEY_NPAGE:  # Page Down
            self.browser_cursor = min(count - 1, self.browser_cursor + 10)

        elif ch in (curses.KEY_BACKSPACE, 127, 8, ord('h')):
            # Ascend to parent directory
            if self.current_dir.parent != self.current_dir:
                self._load_directory(self.current_dir.parent)

        elif ch in (10, 13, curses.KEY_ENTER, ord('l')):
            # Open directory or play file
            name, path, is_dir = self.browser_items[self.browser_cursor]
            if is_dir:
                self._load_directory(path)
            else:
                meta = TrackMetadata.from_path(path)
                idx = self.queue.add_track(meta)
                self.queue.current_index = idx
                self.audio.load_track(meta)

        elif ch in (ord('a'), ord('A')):
            # Add file or directory to queue
            name, path, is_dir = self.browser_items[self.browser_cursor]
            if is_dir:
                added = 0
                for f in sorted(list(path.glob("*.mp3")) + list(path.glob("*.MP3"))):
                    self.queue.add_track(TrackMetadata.from_path(f))
                    added += 1
                self.set_status(f"Added {added} tracks from {path.name}/ to queue")
            else:
                meta = TrackMetadata.from_path(path)
                self.queue.add_track(meta)
                self.set_status(f"Added: {meta.title}")

            # If stopped and empty, play immediately
            if self.audio.state == AudioState.STOPPED and self.queue.current_index < 0:
                self.queue.current_index = 0
                t = self.queue.current_track
                if t:
                    self.audio.load_track(t)

    def _handle_queue_input(self, ch: int) -> None:
        """Handle cursor and actions inside Queue pane."""
        count = self.queue.count
        if count == 0:
            return

        if ch in (curses.KEY_UP, ord('k')):
            self.queue_cursor = max(0, self.queue_cursor - 1)

        elif ch in (curses.KEY_DOWN, ord('j')):
            self.queue_cursor = min(count - 1, self.queue_cursor + 1)

        elif ch in (10, 13, curses.KEY_ENTER):
            track = self.queue.jump_to(self.queue_cursor)
            if track:
                self.audio.load_track(track)

        elif ch in (ord('d'), ord('x'), curses.KEY_DC):
            removed = self.queue.remove_at(self.queue_cursor)
            if removed:
                self.set_status(f"Removed: {removed.title}")
            self.queue_cursor = min(self.queue_cursor, max(0, self.queue.count - 1))

        elif ch in (ord('c'), ord('C')):
            self.queue.clear()
            self.audio.stop()
            self.queue_cursor = 0
            self.set_status("Queue cleared")

    def _render(self) -> None:
        """Render complete terminal screen layout."""
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()

        if h < 14 or w < 40:
            self._safe_addstr(0, 0, "Terminal window too small. Resize to at least 80x24.")
            self.stdscr.refresh()
            return

        if self.show_help:
            self._render_help_screen(h, w)
            self.stdscr.refresh()
            return

        # 1. Header Bar (Row 0)
        self._render_header(w)

        # 2. Split Main View (Rows 1 to H - 7)
        main_h = h - 7
        if w >= 80:
            left_w = w // 2
            right_w = w - left_w
            self._render_browser_pane(1, 0, main_h, left_w)
            self._render_queue_pane(1, left_w, main_h, right_w)
        else:
            if self.active_pane == 0:
                self._render_browser_pane(1, 0, main_h, w)
            else:
                self._render_queue_pane(1, 0, main_h, w)

        # 3. Separator (Row H - 7)
        self._safe_addstr(h - 7, 0, "─" * w, curses.color_pair(1))

        # 4. Live Spectrum Visualizer (Rows H - 6 to H - 4)
        self._render_visualizer(h - 6, 0, 3, w)

        # 5. Playback & Progress Status (Row H - 3)
        self._render_playback_line(h - 3, w)

        # 6. Status / Messages (Row H - 2)
        self._render_status_line(h - 2, w)

        # 7. Hotkey Bar (Row H - 1)
        self._render_footer(h - 1, w)

        self.stdscr.refresh()

    def _safe_addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        """Safely write string inside terminal boundaries."""
        h, w = self.stdscr.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        max_len = w - x
        if max_len <= 0:
            return
        try:
            self.stdscr.addstr(y, x, text[:max_len], attr)
        except curses.error:
            pass

    def _render_header(self, w: int) -> None:
        """Render top system header bar."""
        path_str = str(self.current_dir)
        header_text = f" PULSE CLI ── {path_str} "
        self._safe_addstr(0, 0, " " * w, curses.color_pair(6))
        self._safe_addstr(0, 0, header_text, curses.color_pair(6) | curses.A_BOLD)
        help_hint = "[?] Help  [q] Quit "
        if w > len(header_text) + len(help_hint):
            self._safe_addstr(0, w - len(help_hint), help_hint, curses.color_pair(6))

    def _render_browser_pane(self, top: int, left: int, height: int, width: int) -> None:
        """Render file browser directory tree."""
        is_active = (self.active_pane == 0)
        border_attr = curses.color_pair(1) | curses.A_BOLD if is_active else curses.A_DIM

        # Header title
        filter_str = f" [Filter: {self.filter_query}]" if self.filter_query else ""
        title = f" [1: FILES ({len(self.browser_items)}){filter_str}] "
        self._safe_addstr(top, left, title, border_attr)

        content_h = height - 1
        count = len(self.browser_items)

        # Adjust scroll offset to keep cursor in view
        if self.browser_cursor < self.browser_scroll:
            self.browser_scroll = self.browser_cursor
        elif self.browser_cursor >= self.browser_scroll + content_h:
            self.browser_scroll = self.browser_cursor - content_h + 1

        for idx in range(content_h):
            row_y = top + 1 + idx
            item_idx = self.browser_scroll + idx

            if item_idx < count:
                name, path, is_dir = self.browser_items[item_idx]
                is_selected = (item_idx == self.browser_cursor)

                if is_selected and is_active:
                    attr = curses.color_pair(5) | curses.A_BOLD
                elif is_selected:
                    attr = curses.color_pair(7) | curses.A_BOLD
                elif is_dir:
                    attr = curses.color_pair(3) | curses.A_BOLD  # Yellow for folders
                else:
                    attr = curses.color_pair(1)                  # Cyan for MP3s

                prefix = "> " if is_selected else "  "
                display = f"{prefix}{name}"
                # Pad to fill line
                line_str = f"{display:<{width - 2}}"
                self._safe_addstr(row_y, left, line_str, attr)
            else:
                self._safe_addstr(row_y, left, " " * (width - 1))

        # Vertical divider line on right if side-by-side
        if left + width < self.stdscr.getmaxyx()[1]:
            for r in range(height):
                self._safe_addstr(top + r, left + width - 1, "│", curses.A_DIM)

    def _render_queue_pane(self, top: int, left: int, height: int, width: int) -> None:
        """Render interactive queue / playlist table."""
        is_active = (self.active_pane == 1)
        border_attr = curses.color_pair(1) | curses.A_BOLD if is_active else curses.A_DIM

        # Header title
        title = f" [2: QUEUE ({self.queue.count} tracks)] "
        self._safe_addstr(top, left, title, border_attr)

        content_h = height - 1
        count = self.queue.count

        if self.queue_cursor < self.queue_scroll:
            self.queue_scroll = self.queue_cursor
        elif self.queue_cursor >= self.queue_scroll + content_h:
            self.queue_scroll = self.queue_cursor - content_h + 1

        for idx in range(content_h):
            row_y = top + 1 + idx
            item_idx = self.queue_scroll + idx

            if item_idx < count:
                track = self.queue.tracks[item_idx]
                is_selected = (item_idx == self.queue_cursor)
                is_playing = (item_idx == self.queue.current_index)

                if is_selected and is_active:
                    attr = curses.color_pair(5) | curses.A_BOLD
                elif is_selected:
                    attr = curses.color_pair(7) | curses.A_BOLD
                elif is_playing:
                    attr = curses.color_pair(2) | curses.A_BOLD
                else:
                    attr = curses.A_NORMAL

                state_icon = "▶" if is_playing else " "
                num_str = f"{item_idx + 1:2d}."
                dur_str = track.formatted_duration
                max_title_len = max(10, width - 18)
                title_str = track.title[:max_title_len]

                line = f" {state_icon} {num_str} {title_str:<{max_title_len}} {dur_str:>6s} "
                self._safe_addstr(row_y, left, f"{line:<{width}}", attr)
            else:
                self._safe_addstr(row_y, left, " " * width)

    def _render_visualizer(self, top: int, left: int, height: int, width: int) -> None:
        """Render multi-band dynamic spectrum equalizer."""
        levels = self.audio.get_visualizer_levels()
        is_playing = (self.audio.state == AudioState.PLAYING)

        num_bars = min(len(levels), width // 2)
        if num_bars <= 0:
            return

        for row in range(height):
            y = top + row
            line_row = height - 1 - row  # from bottom to top
            row_chars = []

            for b in range(num_bars):
                val = levels[b] if is_playing else 0.05
                bar_h = val * height
                fill = bar_h - line_row

                if fill >= 1.0:
                    char = self.BLOCKS[8]
                elif fill > 0.0:
                    char = self.BLOCKS[min(8, max(0, int(fill * 8)))]
                else:
                    char = " "
                row_chars.append(f"{char} ")

            spectrum_line = "".join(row_chars)
            # Center visualizer
            pad = max(0, (width - len(spectrum_line)) // 2)
            color = curses.color_pair(2) if line_row == 0 else curses.color_pair(1)
            self._safe_addstr(y, pad, spectrum_line, color | curses.A_BOLD)

    def _render_playback_line(self, y: int, width: int) -> None:
        """Render Now Playing track details, time scrubber, and bitrate."""
        track = self.queue.current_track
        curr_sec, tot_sec, pct = self.audio.get_progress()

        if track:
            state_str = "PLAYING" if self.audio.state == AudioState.PLAYING else "PAUSED"
            if self.audio.state == AudioState.STOPPED:
                state_str = "STOPPED"

            state_color = curses.color_pair(2) if state_str == "PLAYING" else curses.color_pair(3)
            title = f"{track.artist} - {track.title}" if track.artist != "Unknown Artist" else track.title
            curr_str = format_duration(curr_sec)
            tot_str = format_duration(tot_sec)

            # High resolution scrubber bar
            bar_w = max(10, min(30, width - 60))
            if tot_sec > 0:
                pos = int((curr_sec / tot_sec) * (bar_w - 1))
                bar_str = "=" * pos + ">" + "-" * (bar_w - 1 - pos)
            else:
                bar_str = "-" * bar_w

            line = f" [{state_str}] {title}   {curr_str} / {tot_str} [{bar_str}] ({pct:4.1f}%)"
            if track.bitrate > 0:
                line += f"  {track.bitrate}k"
            self._safe_addstr(y, 0, f"{line:<{width}}", curses.A_BOLD)
        else:
            self._safe_addstr(y, 0, f" [STOPPED] No track loaded. Select an MP3 to play.{' ':<{width}}", curses.A_DIM)

    def _render_status_line(self, y: int, width: int) -> None:
        """Render volume, loop/shuffle mode, and flash messages."""
        if self.is_filtering:
            prompt = f" Search Filter: {self.filter_query}_  (Press Enter to confirm, Esc to clear)"
            self._safe_addstr(y, 0, f"{prompt:<{width}}", curses.color_pair(3) | curses.A_BOLD)
            return

        now = time.time()
        if self.status_message and (now - self.status_time < 3.0):
            self._safe_addstr(y, 0, f" >>> {self.status_message}{' ':<{width}}", curses.color_pair(2) | curses.A_BOLD)
            return

        vol_str = "MUTED" if self.audio.is_muted else f"{int(self.audio.volume * 100)}%"
        rep_str = self.queue.repeat_mode.label
        shuf_str = "On" if self.queue.is_shuffled else "Off"
        status = f" Volume: {vol_str}  │  {rep_str}  │  Shuffle: {shuf_str}"
        self._safe_addstr(y, 0, f"{status:<{width}}", curses.A_NORMAL)

    def _render_footer(self, y: int, width: int) -> None:
        """Render bottom command shortcuts reference line."""
        shortcuts = " [Space] Pause  [Enter] Play  [a] Add  [Tab] Pane  [h/l] Nav  [<-/->] Seek  [+/-] Vol  [?] Help  [q] Quit "
        self._safe_addstr(y, 0, " " * width, curses.color_pair(6))
        self._safe_addstr(y, 0, shortcuts[:width], curses.color_pair(6) | curses.A_BOLD)

    def _render_help_screen(self, h: int, w: int) -> None:
        """Render internal keyboard reference overlay."""
        box_w = min(74, w - 4)
        box_h = min(22, h - 4)
        top = (h - box_h) // 2
        left = (w - box_w) // 2

        for r in range(box_h):
            self._safe_addstr(top + r, left, " " * box_w, curses.color_pair(5))

        lines = [
            "PULSE CLI: KEYBOARD CONTROLS REFERENCE",
            "─────────────────────────────────────────────────────────────",
            "PLAYBACK:",
            "  Space          Toggle Play / Pause",
            "  Left / Right   Seek backward / forward 5 seconds",
            "  + / -          Increase / decrease volume by 5%",
            "  m              Mute / unmute audio",
            "  n / p          Next / Previous track in queue",
            "  r              Cycle Repeat mode (Off -> Track -> Queue)",
            "  s              Toggle Shuffle mode",
            "",
            "NAVIGATION & LIBRARY:",
            "  j / k (or Up/Down)  Move cursor selection",
            "  Enter               Open directory or play file immediately",
            "  Backspace / h       Go up to parent directory",
            "  a                   Add file or entire folder to queue",
            "  Tab                 Switch focus between Files and Queue",
            "  /                   Activate search filter",
            "  d / x               Remove track from queue",
            "  c                   Clear entire queue",
            "  q                   Quit Pulse CLI",
            "─────────────────────────────────────────────────────────────",
            "Press any key to close this help screen...",
        ]

        for idx, line in enumerate(lines[:box_h - 2]):
            self._safe_addstr(top + 1 + idx, left + 2, line, curses.color_pair(5) | curses.A_BOLD)
