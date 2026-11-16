"""Pure Black and White 1-Bit Pixel-Art Curses Interface for Pulse CLI."""

from __future__ import annotations

import curses
import math
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

from pulse.audio import AudioEngine, AudioState
from pulse.metadata import TrackMetadata, format_duration
from pulse.queue_manager import QueueManager, RepeatMode


class PulseApp:
    """1-Bit Monochrome Pixel-Art Terminal Audio Station."""

    # 1-Bit dithering & block characters
    DITHER = [" ", "░", "▒", "▓", "█"]
    BLOCKS = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

    def __init__(self, initial_path: Optional[Path | str] = None, auto_play_file: Optional[Path | str] = None):
        self.initial_path = Path(initial_path).resolve() if initial_path else None
        self.auto_play_file = Path(auto_play_file).resolve() if auto_play_file else None

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
        self.browser_items: List[Tuple[str, Path, bool]] = []
        self.browser_cursor: int = 0
        self.browser_scroll: int = 0
        self.filter_query: str = ""
        self.is_filtering: bool = False

        # Queue state
        self.queue_cursor: int = 0
        self.queue_scroll: int = 0

        # Pane focus: 0 = Browser, 1 = Queue
        self.active_pane: int = 0
        self.show_help: bool = False
        self.status_message: str = ""
        self.status_time: float = 0.0

        # Spectrum peak-hold state for retro 1-bit hardware meters
        self.idle_t: float = 0.0
        self.peak_levels: List[float] = [0.0] * 128
        self.peak_hold_frames: List[int] = [0] * 128

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

        if self.current_dir.parent != self.current_dir:
            items.append(("..", self.current_dir.parent, True))

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
            self.set_status(f"ERR: ACCESS DENIED: {self.current_dir}")

        dirs.sort(key=lambda x: x[0].lower())
        files.sort(key=lambda x: x[0].lower())

        self.browser_items = items + dirs + files
        self.browser_cursor = min(self.browser_cursor, max(0, len(self.browser_items) - 1))
        self.browser_scroll = 0

    def run(self) -> None:
        try:
            curses.wrapper(self._main_loop)
        finally:
            self.audio.close()

    def _init_colors(self) -> None:
        """Enforce strict high-contrast monochrome (Black & White)."""
        curses.start_color()
        # Pair 1: Normal White on Black
        curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLACK)
        # Pair 2: Reverse Inverted Black on White (1-Bit Solid Highlight)
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)

    def _main_loop(self, stdscr) -> None:
        self.stdscr = stdscr
        curses.curs_set(0)
        stdscr.timeout(40)  # 25 FPS update rate
        self._init_colors()

        while True:
            if self.audio.check_and_clear_finished():
                next_t = self.queue.next_track()
                if next_t:
                    self.audio.load_track(next_t)
                else:
                    self.audio.stop()

            self._render()

            try:
                ch = stdscr.getch()
            except Exception:
                ch = -1

            if ch != -1:
                if not self._handle_input(ch):
                    break

    def _handle_input(self, ch: int) -> bool:
        if self.show_help:
            self.show_help = False
            return True

        if self.is_filtering:
            return self._handle_filter_input(ch)

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
            self.active_pane = 1 - self.active_pane

        elif ch == ord('/'):
            self.is_filtering = True
            self.filter_query = ""

        elif ch in (ord('+'), ord('='), ord(']')):
            vol = self.audio.change_volume(0.05)
            self.set_status(f"VOL: {int(vol * 100)}%")

        elif ch in (ord('-'), ord('_'), ord('[')):
            vol = self.audio.change_volume(-0.05)
            self.set_status(f"VOL: {int(vol * 100)}%")

        elif ch in (ord('m'), ord('M')):
            muted = self.audio.toggle_mute()
            self.set_status("AUDIO: MUTED" if muted else f"AUDIO: {int(self.audio.volume * 100)}%")

        elif ch in (curses.KEY_LEFT, ord(',')):
            self.audio.seek(-5.0)

        elif ch in (curses.KEY_RIGHT, ord('.')):
            self.audio.seek(5.0)

        elif ch in (ord('n'), ord('N')):
            next_t = self.queue.next_track()
            if next_t:
                self.audio.load_track(next_t)
            else:
                self.set_status("QUEUE: END REACHED")

        elif ch in (ord('p'), ord('P')):
            prev_t = self.queue.prev_track()
            if prev_t:
                self.audio.load_track(prev_t)

        elif ch in (ord('r'), ord('R')):
            mode = self.queue.toggle_repeat()
            self.set_status(f"LOOP: {mode.name}")

        elif ch in (ord('s'), ord('S')):
            shuf = self.queue.toggle_shuffle()
            self.set_status(f"SHUFFLE: {'ON' if shuf else 'OFF'}")

        if self.active_pane == 0:
            self._handle_browser_input(ch)
        else:
            self._handle_queue_input(ch)

        return True

    def _handle_filter_input(self, ch: int) -> bool:
        if ch in (10, 13, curses.KEY_ENTER):
            self.is_filtering = False
            self._load_directory(self.current_dir)
        elif ch == 27:  # Escape
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
        elif ch == curses.KEY_PPAGE:
            self.browser_cursor = max(0, self.browser_cursor - 10)
        elif ch == curses.KEY_NPAGE:
            self.browser_cursor = min(count - 1, self.browser_cursor + 10)
        elif ch in (curses.KEY_BACKSPACE, 127, 8, ord('h')):
            if self.current_dir.parent != self.current_dir:
                self._load_directory(self.current_dir.parent)
        elif ch in (10, 13, curses.KEY_ENTER, ord('l')):
            name, path, is_dir = self.browser_items[self.browser_cursor]
            if is_dir:
                self._load_directory(path)
            else:
                meta = TrackMetadata.from_path(path)
                idx = self.queue.add_track(meta)
                self.queue.current_index = idx
                self.audio.load_track(meta)
        elif ch in (ord('a'), ord('A')):
            name, path, is_dir = self.browser_items[self.browser_cursor]
            if is_dir:
                added = 0
                for f in sorted(list(path.glob("*.mp3")) + list(path.glob("*.MP3"))):
                    self.queue.add_track(TrackMetadata.from_path(f))
                    added += 1
                self.set_status(f"QUEUED: {added} TRACKS ({path.name}/)")
            else:
                meta = TrackMetadata.from_path(path)
                self.queue.add_track(meta)
                self.set_status(f"QUEUED: {meta.title}")

            if self.audio.state == AudioState.STOPPED and self.queue.current_index < 0:
                self.queue.current_index = 0
                t = self.queue.current_track
                if t:
                    self.audio.load_track(t)

    def _handle_queue_input(self, ch: int) -> None:
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
                self.set_status(f"REMOVED: {removed.title}")
            self.queue_cursor = min(self.queue_cursor, max(0, self.queue.count - 1))
        elif ch in (ord('c'), ord('C')):
            self.queue.clear()
            self.audio.stop()
            self.queue_cursor = 0
            self.set_status("QUEUE: CLEARED")

    def _render(self) -> None:
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()

        if h < 16 or w < 60:
            self._safe_addstr(0, 0, "TERMINAL SIZE TOO SMALL. MIN: 80x24", curses.A_BOLD)
            self.stdscr.refresh()
            return

        if self.show_help:
            self._render_help_screen(h, w)
            self.stdscr.refresh()
            return

        # 1. Top Header Box
        self._render_header(w)

        # 2. Main File & Queue Split View
        if h >= 28:
            vis_h = 7
        elif h >= 24:
            vis_h = 6
        else:
            vis_h = 5

        tele_h = 4
        main_h = max(6, h - vis_h - tele_h - 3)

        if w >= 80:
            left_w = w // 2
            right_w = w - left_w
            self._render_browser_pane(2, 0, main_h, left_w)
            self._render_queue_pane(2, left_w, main_h, right_w)
        else:
            if self.active_pane == 0:
                self._render_browser_pane(2, 0, main_h, w)
            else:
                self._render_queue_pane(2, 0, main_h, w)

        # 3. Retro 1-Bit Spectrum Analyzer
        vis_y = 2 + main_h
        self._render_spectrum_box(vis_y, 0, vis_h, w)

        # 4. Playback Scrubber & Telemetry Box
        tele_y = vis_y + vis_h
        self._render_telemetry_box(tele_y, 0, tele_h, w)

        # 5. Bottom Solid Command Strip
        self._render_footer(h - 1, w)

        self.stdscr.refresh()

    def _safe_addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
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
        """Sharp pixel-art title bar."""
        bar = " " * w
        self._safe_addstr(0, 0, bar, curses.color_pair(2))
        title = " ■ PULSE // AUDIO ENGINE 1.0 "
        self._safe_addstr(0, 0, title, curses.color_pair(2) | curses.A_BOLD)

        path_display = f" {self.current_dir} "
        if w > len(title) + len(path_display) + 12:
            self._safe_addstr(0, len(title) + 2, path_display, curses.color_pair(2))

        right_hint = "[?] HELP  [Q] QUIT "
        if w > len(title) + len(right_hint):
            self._safe_addstr(0, w - len(right_hint), right_hint, curses.color_pair(2) | curses.A_BOLD)

        # Divider line
        self._safe_addstr(1, 0, "─" * w, curses.A_DIM)

    def _render_browser_pane(self, top: int, left: int, height: int, width: int) -> None:
        """Render 1-bit pixel box for file browser."""
        is_active = (self.active_pane == 0)
        box_attr = curses.A_BOLD if is_active else curses.A_DIM

        # Frame Header
        filt = f" [QUERY: {self.filter_query}]" if self.filter_query else ""
        title = f"┌─[ 1: FILES {filt}]"
        rem_w = max(0, width - len(title) - 1)
        self._safe_addstr(top, left, f"{title}{'─' * rem_w}┐", box_attr)

        content_h = height - 2
        count = len(self.browser_items)

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
                    attr = curses.color_pair(2) | curses.A_BOLD  # Solid white highlight
                    marker = "▶ "
                elif is_selected:
                    attr = curses.A_BOLD
                    marker = "▷ "
                else:
                    attr = curses.A_NORMAL
                    marker = "  "

                tag = "[DIR] " if is_dir else "[MP3] "
                entry_str = f"{marker}{tag}{name}"
                fill_len = width - 4
                self._safe_addstr(row_y, left, "│ ", box_attr)
                self._safe_addstr(row_y, left + 2, f"{entry_str:<{fill_len}}", attr)
                self._safe_addstr(row_y, left + width - 1, "│", box_attr)
            else:
                self._safe_addstr(row_y, left, "│" + (" " * (width - 2)) + "│", box_attr)

        self._safe_addstr(top + height - 1, left, "└" + ("─" * (width - 2)) + "┘", box_attr)

    def _render_queue_pane(self, top: int, left: int, height: int, width: int) -> None:
        """Render 1-bit pixel box for queue."""
        is_active = (self.active_pane == 1)
        box_attr = curses.A_BOLD if is_active else curses.A_DIM

        title = f"┌─[ 2: QUEUE ({self.queue.count}) ]"
        rem_w = max(0, width - len(title) - 1)
        self._safe_addstr(top, left, f"{title}{'─' * rem_w}┐", box_attr)

        content_h = height - 2
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
                    attr = curses.color_pair(2) | curses.A_BOLD
                    marker = "▶ "
                elif is_selected:
                    attr = curses.A_BOLD
                    marker = "▷ "
                elif is_playing:
                    attr = curses.A_BOLD
                    marker = "■ "
                else:
                    attr = curses.A_NORMAL
                    marker = "  "

                dur = track.formatted_duration
                num_str = f"{item_idx + 1:02d}. "
                avail_title = max(8, width - 18)
                title_str = track.title[:avail_title]
                entry_str = f"{marker}{num_str}{title_str:<{avail_title}} {dur:>6s}"

                self._safe_addstr(row_y, left, "│ ", box_attr)
                self._safe_addstr(row_y, left + 2, f"{entry_str:<{width - 4}}", attr)
                self._safe_addstr(row_y, left + width - 1, "│", box_attr)
            else:
                self._safe_addstr(row_y, left, "│" + (" " * (width - 2)) + "│", box_attr)

        self._safe_addstr(top + height - 1, left, "└" + ("─" * (width - 2)) + "┘", box_attr)

    def _render_spectrum_box(self, top: int, left: int, height: int, width: int) -> None:
        """Render retro 1-bit hardware spectrum analyzer with bold bars and peak dots."""
        title = "┌─[ SPECTRUM ANALYZER ]"
        rem_w = max(0, width - len(title) - 1)
        self._safe_addstr(top, left, f"{title}{'─' * rem_w}┐", curses.A_NORMAL)

        raw_levels = self.audio.get_visualizer_levels()
        is_playing = (self.audio.state == AudioState.PLAYING)
        plot_h = max(2, height - 2)  # internal rows

        inner_w = width - 4
        bar_w = 2  # Each bar is 2 characters wide
        bar_stride = bar_w + 1  # 2 columns bar + 1 column gap
        num_bars = min(64, inner_w // bar_stride)
        if num_bars <= 0:
            return

        # Resample / interpolate raw_levels across num_bars
        levels = []
        if is_playing and raw_levels:
            raw_len = len(raw_levels)
            for b in range(num_bars):
                src_idx = int(b * raw_len / num_bars)
                levels.append(raw_levels[min(raw_len - 1, src_idx)])
        else:
            # Animated 1-bit hardware oscilloscope wave when idle / stopped
            self.idle_t += 0.08
            for b in range(num_bars):
                w1 = math.sin(self.idle_t + (b * 0.25)) * 0.28
                w2 = math.cos((self.idle_t * 0.7) + (b * 0.5)) * 0.15
                val = max(0.12, min(0.95, 0.45 + w1 + w2))
                levels.append(val)

        # Update peak hold decay
        while len(self.peak_levels) < num_bars:
            self.peak_levels.append(0.0)
            self.peak_hold_frames.append(0)

        for b in range(num_bars):
            lvl = levels[b]
            if lvl >= self.peak_levels[b]:
                self.peak_levels[b] = lvl
                self.peak_hold_frames[b] = 8
            else:
                if self.peak_hold_frames[b] > 0:
                    self.peak_hold_frames[b] -= 1
                else:
                    self.peak_levels[b] = max(0.0, self.peak_levels[b] - 0.08)

        for row in range(plot_h):
            row_y = top + 1 + row
            line_row = plot_h - 1 - row  # 0 is bottom row, plot_h-1 is top row
            row_cells = []

            for b in range(num_bars):
                val = levels[b]
                bar_h = val * plot_h
                fill = bar_h - line_row

                # Peak hold dot
                peak_row = int(self.peak_levels[b] * plot_h)
                is_peak = (peak_row == line_row and peak_row > 0)

                if fill >= 1.0:
                    char = "█" * bar_w
                elif fill > 0.0:
                    idx = min(8, max(0, int(fill * 8)))
                    char = self.BLOCKS[idx] * bar_w
                elif is_peak:
                    char = "▀" * bar_w
                else:
                    char = " " * bar_w
                row_cells.append(char + " ")

            spectrum_line = "".join(row_cells)
            pad_left = max(0, (inner_w - len(spectrum_line)) // 2)
            pad_right = max(0, inner_w - len(spectrum_line) - pad_left)
            full_line = "│ " + (" " * pad_left) + spectrum_line + (" " * pad_right) + " │"
            self._safe_addstr(row_y, left, full_line, curses.A_BOLD if line_row <= 1 else curses.A_NORMAL)

        self._safe_addstr(top + height - 1, left, "└" + ("─" * (width - 2)) + "┘", curses.A_NORMAL)

    def _render_telemetry_box(self, top: int, left: int, height: int, width: int) -> None:
        """Render 1-bit pixel playback telemetry, dithered progress, and mode meters."""
        self._safe_addstr(top, left, "┌─[ TELEMETRY ]" + ("─" * (width - 16)) + "┐", curses.A_NORMAL)

        track = self.queue.current_track
        curr_sec, tot_sec, pct = self.audio.get_progress()

        # Row 1: Track title and format info
        if track:
            state_tag = "[PLAY]" if self.audio.state == AudioState.PLAYING else "[PAUSE]"
            if self.audio.state == AudioState.STOPPED:
                state_tag = "[STOP]"

            artist_title = f"{track.artist} - {track.title}" if track.artist != "Unknown Artist" else track.title
            specs = f"[{track.bitrate}k / {track.channels.upper()}]"
            avail_w = width - len(state_tag) - len(specs) - 6
            t_str = artist_title[:avail_w]
            line1 = f"│ {state_tag} {t_str:<{avail_w}} {specs} │"
        else:
            line1 = f"│ [IDLE] NO TRACK LOADED.{' ' * (width - 27)}│"

        self._safe_addstr(top + 1, left, line1, curses.A_BOLD)

        # Row 2: 1-Bit Dithered Scrubber Bar
        curr_str = format_duration(curr_sec)
        tot_str = format_duration(tot_sec)
        rem_sec = max(0.0, tot_sec - curr_sec)
        rem_str = f"-{format_duration(rem_sec)}"

        time_pre = f"{curr_str} ["
        time_post = f"] {tot_str} ({rem_str}) {pct:5.1f}%"
        fixed_len = len(time_pre) + len(time_post) + 4
        bar_w = max(10, width - fixed_len)

        if tot_sec > 0:
            filled_len = int((curr_sec / tot_sec) * bar_w)
            # 1-bit dithered fill: solid pixel body with dithered lead
            dither_len = min(2, filled_len)
            solid_len = max(0, filled_len - dither_len)
            empty_len = max(0, bar_w - filled_len)
            bar_chars = ("█" * solid_len) + ("▓" * dither_len) + ("░" * empty_len)
        else:
            bar_chars = "░" * bar_w

        line2 = f"│ {time_pre}{bar_chars}{time_post}{' ' * max(0, width - fixed_len - bar_w)} │"
        self._safe_addstr(top + 2, left, line2, curses.A_NORMAL)

        # Row 3: Status / Mode meters & message
        now = time.time()
        if self.is_filtering:
            status_text = f"QUERY: {self.filter_query}_ [ENTER: CONFIRM  ESC: CANCEL]"
        elif self.status_message and (now - self.status_time < 3.0):
            status_text = f"STATUS >> {self.status_message}"
        else:
            vol_lvl = int(self.audio.volume * 10)
            vol_bar = ("█" * vol_lvl) + ("░" * (10 - vol_lvl))
            vol_str = f"VOL:[{vol_bar}]"
            rep_str = f"LOOP:{self.queue.repeat_mode.name}"
            shuf_str = f"SHUF:{'ON' if self.queue.is_shuffled else 'OFF'}"
            track_num = f"TRK:{self.queue.current_index + 1:02d}/{self.queue.count:02d}" if self.queue.count else "TRK:--/--"
            status_text = f"{vol_str}  │  {rep_str}  │  {shuf_str}  │  {track_num}"

        self._safe_addstr(top + 3, left, "└" + ("─" * (width - 2)) + "┘", curses.A_NORMAL)
        # Overlay status inside frame
        self._safe_addstr(top + 3, left + 3, f" {status_text} ", curses.A_BOLD)

    def _render_footer(self, y: int, width: int) -> None:
        """High-contrast solid pixel command bar."""
        cmd_text = " [SPACE] PLAY/PAUSE  [ENTER] OPEN/PLAY  [A] ADD  [TAB] PANE  [←/→] SEEK  [+/-] VOL  [Q] QUIT "
        self._safe_addstr(y, 0, " " * width, curses.color_pair(2))
        self._safe_addstr(y, 0, cmd_text[:width], curses.color_pair(2) | curses.A_BOLD)

    def _render_help_screen(self, h: int, w: int) -> None:
        box_w = min(74, w - 4)
        box_h = min(22, h - 4)
        top = (h - box_h) // 2
        left = (w - box_w) // 2

        for r in range(box_h):
            self._safe_addstr(top + r, left, " " * box_w, curses.color_pair(2))

        lines = [
            "■ PULSE // KEYBOARD MATRIX",
            "─────────────────────────────────────────────────────────────",
            "PLAYBACK:",
            "  SPACE          TOGGLE PLAY / PAUSE",
            "  LEFT / RIGHT   SEEK -5S / +5S",
            "  + / -          VOLUME UP / DOWN (5%)",
            "  M              MUTE / UNMUTE",
            "  N / P          NEXT / PREV TRACK",
            "  R              CYCLE REPEAT (OFF -> TRACK -> QUEUE)",
            "  S              TOGGLE SHUFFLE",
            "",
            "NAVIGATION & LIBRARY:",
            "  J / K (UP/DN)  MOVE SELECTION",
            "  ENTER          OPEN DIR / PLAY FILE",
            "  BACKSPACE / H  ASCEND (PARENT DIRECTORY)",
            "  A              QUEUE FILE OR ENTIRE DIR",
            "  TAB            SWITCH PANE (FILES <-> QUEUE)",
            "  /              ACTIVATE SEARCH FILTER",
            "  D / X          REMOVE TRACK FROM QUEUE",
            "  C              CLEAR ALL QUEUE ITEMS",
            "  Q              QUIT",
            "─────────────────────────────────────────────────────────────",
            "PRESS ANY KEY TO CLOSE...",
        ]

        for idx, line in enumerate(lines[:box_h - 2]):
            self._safe_addstr(top + 1 + idx, left + 2, line, curses.color_pair(2) | curses.A_BOLD)
