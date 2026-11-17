"""Playlist and queue management with repeat, shuffle, and reordering."""

from __future__ import annotations

import random
from enum import Enum
from typing import List, Optional

from pulse.metadata import TrackMetadata


class RepeatMode(Enum):
    OFF = "OFF"
    TRACK = "TRACK"
    QUEUE = "QUEUE"

    @property
    def label(self) -> str:
        if self == RepeatMode.OFF:
            return "Repeat: Off"
        elif self == RepeatMode.TRACK:
            return "Repeat: Track"
        return "Repeat: Queue"

    def next_mode(self) -> RepeatMode:
        if self == RepeatMode.OFF:
            return RepeatMode.TRACK
        elif self == RepeatMode.TRACK:
            return RepeatMode.QUEUE
        return RepeatMode.OFF


class QueueManager:
    """Manages playback queue, history, shuffle, and repeat modes."""

    def __init__(self):
        self.tracks: List[TrackMetadata] = []
        self.current_index: int = -1
        self.repeat_mode: RepeatMode = RepeatMode.OFF
        self.is_shuffled: bool = False
        self._shuffle_order: List[int] = []
        self._shuffle_pos: int = 0
        self.history: List[int] = []

    @property
    def current_track(self) -> Optional[TrackMetadata]:
        if 0 <= self.current_index < len(self.tracks):
            return self.tracks[self.current_index]
        return None

    @property
    def count(self) -> int:
        return len(self.tracks)

    def add_track(self, track: TrackMetadata) -> int:
        """Append track to queue and return its index."""
        self.tracks.append(track)
        new_idx = len(self.tracks) - 1
        if self.is_shuffled:
            insert_at = random.randint(self._shuffle_pos + 1, len(self._shuffle_order)) if self._shuffle_order else 0
            self._shuffle_order.insert(insert_at, new_idx)
        return new_idx

    def add_tracks(self, tracks: List[TrackMetadata]) -> None:
        """Append multiple tracks to queue."""
        if not tracks:
            return
        start_len = len(self.tracks)
        self.tracks.extend(tracks)
        if self.is_shuffled:
            new_indices = list(range(start_len, len(self.tracks)))
            random.shuffle(new_indices)
            for idx in new_indices:
                insert_at = random.randint(self._shuffle_pos + 1, len(self._shuffle_order)) if self._shuffle_order else 0
                self._shuffle_order.insert(insert_at, idx)

    def insert_next(self, track: TrackMetadata) -> None:
        """Insert track right after the currently playing track."""
        if self.current_index < 0 or not self.tracks:
            self.tracks.append(track)
            self.current_index = 0
            if self.is_shuffled:
                self._shuffle_order = [0]
                self._shuffle_pos = 0
        else:
            insert_idx = self.current_index + 1
            self.tracks.insert(insert_idx, track)
            if self.is_shuffled:
                self._shuffle_order = [i + 1 if i >= insert_idx else i for i in self._shuffle_order]
                self._shuffle_order.insert(self._shuffle_pos + 1, insert_idx)

    def remove_at(self, index: int) -> Optional[TrackMetadata]:
        """Remove a track by index."""
        if not (0 <= index < len(self.tracks)):
            return None

        removed = self.tracks.pop(index)

        # Adjust current index
        if index < self.current_index:
            self.current_index -= 1
        elif index == self.current_index:
            if self.current_index >= len(self.tracks):
                self.current_index = len(self.tracks) - 1

        # Adjust shuffle order
        if self.is_shuffled:
            if index in self._shuffle_order:
                rm_pos = self._shuffle_order.index(index)
                self._shuffle_order.pop(rm_pos)
                if rm_pos < self._shuffle_pos:
                    self._shuffle_pos = max(0, self._shuffle_pos - 1)
            self._shuffle_order = [i - 1 if i > index else i for i in self._shuffle_order]
            if self._shuffle_order:
                self._shuffle_pos = max(0, min(self._shuffle_pos, len(self._shuffle_order) - 1))
            else:
                self._shuffle_pos = 0

        # Prune history of the removed index
        self.history = [i - 1 if i > index else i for i in self.history if i != index]
        return removed

    def move_track(self, from_idx: int, to_idx: int) -> bool:
        """Reorder a track from from_idx to to_idx in the queue."""
        if not (0 <= from_idx < len(self.tracks)) or not (0 <= to_idx < len(self.tracks)) or from_idx == to_idx:
            return False

        item = self.tracks.pop(from_idx)
        self.tracks.insert(to_idx, item)

        if self.current_index == from_idx:
            self.current_index = to_idx
        elif from_idx < self.current_index <= to_idx:
            self.current_index -= 1
        elif to_idx <= self.current_index < from_idx:
            self.current_index += 1

        if self.is_shuffled:
            def remap(i: int) -> int:
                if i == from_idx:
                    return to_idx
                if from_idx < i <= to_idx:
                    return i - 1
                if to_idx <= i < from_idx:
                    return i + 1
                return i
            self._shuffle_order = [remap(i) for i in self._shuffle_order]

        def remap_hist(i: int) -> int:
            if i == from_idx:
                return to_idx
            if from_idx < i <= to_idx:
                return i - 1
            if to_idx <= i < from_idx:
                return i + 1
            return i
        self.history = [remap_hist(i) for i in self.history]
        return True

    def clear(self) -> None:
        """Clear all tracks from queue."""
        self.tracks.clear()
        self.current_index = -1
        self._shuffle_order.clear()
        self._shuffle_pos = 0
        self.history.clear()

    def jump_to(self, index: int) -> Optional[TrackMetadata]:
        """Jump to a specific track index."""
        if 0 <= index < len(self.tracks):
            if self.current_index >= 0 and self.current_index != index:
                self.history.append(self.current_index)
                if len(self.history) > 100:
                    self.history.pop(0)

            self.current_index = index
            if self.is_shuffled:
                if index in self._shuffle_order:
                    self._shuffle_pos = self._shuffle_order.index(index)
                else:
                    self._rebuild_shuffle(keep_current=True)
            return self.tracks[self.current_index]
        return None

    def next_track(self, auto_advance: bool = False) -> Optional[TrackMetadata]:
        """Advance to next track according to repeat and shuffle rules."""
        if not self.tracks:
            return None

        # Repeat current track only on automatic advance
        if auto_advance and self.repeat_mode == RepeatMode.TRACK and self.current_index >= 0:
            return self.tracks[self.current_index]

        if self.current_index >= 0:
            self.history.append(self.current_index)
            if len(self.history) > 100:
                self.history.pop(0)

        # Shuffle mode traversal
        if self.is_shuffled and self.tracks:
            if not self._shuffle_order:
                self._rebuild_shuffle(keep_current=True)

            self._shuffle_pos += 1
            if self._shuffle_pos < len(self._shuffle_order):
                self.current_index = self._shuffle_order[self._shuffle_pos]
                return self.tracks[self.current_index]
            else:
                # End of shuffled queue reached
                if self.repeat_mode == RepeatMode.QUEUE and self.tracks:
                    self._rebuild_shuffle(keep_current=False)
                    self._shuffle_pos = 0
                    self.current_index = self._shuffle_order[0]
                    return self.tracks[self.current_index]
                else:
                    self._shuffle_pos = len(self._shuffle_order)
                    return None

        # Normal sequential advancement
        next_idx = self.current_index + 1
        if next_idx < len(self.tracks):
            self.current_index = next_idx
            return self.tracks[self.current_index]

        # End of queue reached: check queue repeat
        if self.repeat_mode == RepeatMode.QUEUE and self.tracks:
            self.current_index = 0
            return self.tracks[0]

        return None

    def prev_track(self) -> Optional[TrackMetadata]:
        """Return to previous track or start of current."""
        if not self.tracks:
            return None

        # Shuffle mode previous
        if self.is_shuffled and self._shuffle_order:
            if self._shuffle_pos > 0:
                self._shuffle_pos -= 1
                self.current_index = self._shuffle_order[self._shuffle_pos]
                return self.tracks[self.current_index]
            elif self.repeat_mode == RepeatMode.QUEUE:
                self._shuffle_pos = len(self._shuffle_order) - 1
                self.current_index = self._shuffle_order[self._shuffle_pos]
                return self.tracks[self.current_index]
            else:
                return self.current_track

        # Sequential mode previous
        if self.current_index > 0:
            self.current_index -= 1
            return self.tracks[self.current_index]

        if self.repeat_mode == RepeatMode.QUEUE and self.tracks:
            self.current_index = len(self.tracks) - 1
            return self.tracks[self.current_index]

        if self.tracks:
            self.current_index = 0
            return self.tracks[0]

        return None

    def toggle_repeat(self) -> RepeatMode:
        """Cycle repeat mode."""
        self.repeat_mode = self.repeat_mode.next_mode()
        return self.repeat_mode

    def toggle_shuffle(self) -> bool:
        """Toggle shuffle mode."""
        self.is_shuffled = not self.is_shuffled
        if self.is_shuffled:
            self._rebuild_shuffle(keep_current=True)
        return self.is_shuffled

    def _rebuild_shuffle(self, keep_current: bool = True) -> None:
        """Rebuild a deterministic random permutation of the queue."""
        if not self.tracks:
            self._shuffle_order = []
            self._shuffle_pos = 0
            return

        order = list(range(len(self.tracks)))
        if keep_current and 0 <= self.current_index < len(self.tracks):
            order.remove(self.current_index)
            random.shuffle(order)
            self._shuffle_order = [self.current_index] + order
            self._shuffle_pos = 0
        else:
            random.shuffle(order)
            self._shuffle_order = order
            self._shuffle_pos = 0
