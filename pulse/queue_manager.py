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
        if self.is_shuffled:
            self._shuffle_order.append(len(self.tracks) - 1)
        return len(self.tracks) - 1

    def add_tracks(self, tracks: List[TrackMetadata]) -> None:
        """Append multiple tracks to queue."""
        for t in tracks:
            self.add_track(t)

    def insert_next(self, track: TrackMetadata) -> None:
        """Insert track right after the currently playing track."""
        if self.current_index < 0:
            self.tracks.append(track)
            self.current_index = 0
        else:
            self.tracks.insert(self.current_index + 1, track)

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

        self._rebuild_shuffle()
        return removed

    def clear(self) -> None:
        """Clear all tracks from queue."""
        self.tracks.clear()
        self.current_index = -1
        self._shuffle_order.clear()

    def jump_to(self, index: int) -> Optional[TrackMetadata]:
        """Jump to a specific track index."""
        if 0 <= index < len(self.tracks):
            self.current_index = index
            return self.tracks[self.current_index]
        return None

    def next_track(self) -> Optional[TrackMetadata]:
        """Advance to next track according to repeat and shuffle rules."""
        if not self.tracks:
            return None

        # Repeat current track
        if self.repeat_mode == RepeatMode.TRACK and self.current_index >= 0:
            return self.tracks[self.current_index]

        # Shuffle mode
        if self.is_shuffled and len(self.tracks) > 1:
            candidates = [i for i in range(len(self.tracks)) if i != self.current_index]
            if candidates:
                self.current_index = random.choice(candidates)
                return self.tracks[self.current_index]

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

        if self.current_index > 0:
            self.current_index -= 1
            return self.tracks[self.current_index]

        if self.repeat_mode == RepeatMode.QUEUE and self.tracks:
            self.current_index = len(self.tracks) - 1
            return self.tracks[self.current_index]

        return self.tracks[0] if self.tracks else None

    def toggle_repeat(self) -> RepeatMode:
        """Cycle repeat mode."""
        self.repeat_mode = self.repeat_mode.next_mode()
        return self.repeat_mode

    def toggle_shuffle(self) -> bool:
        """Toggle shuffle mode."""
        self.is_shuffled = not self.is_shuffled
        if self.is_shuffled:
            self._rebuild_shuffle()
        return self.is_shuffled

    def _rebuild_shuffle(self) -> None:
        """Rebuild shuffle sequence."""
        self._shuffle_order = list(range(len(self.tracks)))
        random.shuffle(self._shuffle_order)
