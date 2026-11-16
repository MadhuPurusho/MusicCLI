"""Audio playback engine using Miniaudio and macOS CoreAudio."""

from __future__ import annotations

import array
import math
import threading
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import miniaudio

from pulse.metadata import TrackMetadata


class AudioState(Enum):
    STOPPED = "STOPPED"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"


class AudioEngine:
    """CoreAudio-backed audio playback and analysis engine."""

    SAMPLE_RATE = 44100
    CHANNELS = 2
    NUM_VISUALIZER_BARS = 24

    def __init__(self):
        self._lock = threading.Lock()
        self.state: AudioState = AudioState.STOPPED
        self.current_track: Optional[TrackMetadata] = None

        self._samples: Optional[array.array] = None
        self._total_frames: int = 0
        self._current_frame: int = 0

        self.volume: float = 0.85
        self.is_muted: bool = False
        self._prev_volume: float = 0.85

        self.levels: List[float] = [0.0] * self.NUM_VISUALIZER_BARS
        self.track_finished_flag: bool = False
        self.on_track_end: Optional[Callable[[], None]] = None

        # Initialize CoreAudio playback device
        self._device = miniaudio.PlaybackDevice(
            nchannels=self.CHANNELS,
            sample_rate=self.SAMPLE_RATE,
        )
        self._gen = self._audio_stream_generator()
        next(self._gen)  # Prime generator
        self._device.start(self._gen)

    def _audio_stream_generator(self):
        """Audio callback generator that streams PCM frames to the CoreAudio device."""
        num_frames = yield b""

        while True:
            if num_frames is None or num_frames <= 0:
                num_frames = 1024

            eff_vol = 0.0 if self.is_muted else self.volume

            with self._lock:
                if (
                    self.state == AudioState.PLAYING
                    and self._samples is not None
                    and self._current_frame < self._total_frames
                ):
                    count = min(num_frames, self._total_frames - self._current_frame)
                    start_idx = self._current_frame * self.CHANNELS
                    end_idx = start_idx + (count * self.CHANNELS)
                    raw_chunk = self._samples[start_idx:end_idx]

                    # Update visualizer spectrum from raw samples before volume attenuation
                    self._update_levels(raw_chunk)

                    # Apply volume scaling
                    if eff_vol < 0.98:
                        scaled_samples = array.array("h", (int(s * eff_vol) for s in raw_chunk))
                        data = scaled_samples.tobytes()
                    else:
                        data = raw_chunk.tobytes()

                    # Handle end of song padding if chunk is smaller than buffer
                    if count < num_frames:
                        pad_frames = num_frames - count
                        data += b"\x00" * (pad_frames * self.CHANNELS * 2)
                        self.state = AudioState.STOPPED
                        self._current_frame = self._total_frames
                        self.track_finished_flag = True
                    else:
                        self._current_frame += count

                    num_frames = yield data
                else:
                    # Silence when paused or stopped
                    self._decay_levels()
                    silence = b"\x00" * (num_frames * self.CHANNELS * 2)
                    num_frames = yield silence

    def _update_levels(self, chunk: array.array) -> None:
        """Compute visualizer bar levels from active audio PCM chunk."""
        if not chunk:
            self._decay_levels()
            return

        chunk_len = len(chunk)
        num_bars = self.NUM_VISUALIZER_BARS
        step = max(1, chunk_len // num_bars)

        for b in range(num_bars):
            sub = chunk[b * step : (b + 1) * step]
            if not sub:
                self.levels[b] = max(0.0, self.levels[b] * 0.75)
                continue

            # Compute peak amplitude normalized to [0, 1]
            peak = max(abs(s) for s in sub) / 32768.0
            # Non-linear perceptual scaling
            target = min(1.0, math.pow(peak, 0.6) * 1.35)

            # Attack / decay smoothing
            if target > self.levels[b]:
                self.levels[b] = self.levels[b] * 0.2 + target * 0.8
            else:
                self.levels[b] = self.levels[b] * 0.75 + target * 0.25

    def _decay_levels(self) -> None:
        """Decay spectrum bars toward zero when audio is silent/idle."""
        for b in range(self.NUM_VISUALIZER_BARS):
            self.levels[b] = max(0.0, self.levels[b] * 0.72 - 0.02)

    def load_track(self, metadata: TrackMetadata) -> bool:
        """Decode and load an MP3 track into memory."""
        try:
            decoded = miniaudio.decode_file(
                str(metadata.path),
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=self.CHANNELS,
                sample_rate=self.SAMPLE_RATE,
            )
            with self._lock:
                self._samples = decoded.samples
                self._total_frames = decoded.num_frames
                self._current_frame = 0
                self.current_track = metadata
                self.state = AudioState.PLAYING
                self.track_finished_flag = False
            return True
        except Exception:
            return False

    def play(self) -> None:
        """Resume playback."""
        with self._lock:
            if self._samples is not None:
                self.state = AudioState.PLAYING

    def pause(self) -> None:
        """Pause playback."""
        with self._lock:
            if self.state == AudioState.PLAYING:
                self.state = AudioState.PAUSED

    def toggle_play_pause(self) -> AudioState:
        """Toggle between playing and paused."""
        with self._lock:
            if self.state == AudioState.PLAYING:
                self.state = AudioState.PAUSED
            elif self.state == AudioState.PAUSED:
                self.state = AudioState.PLAYING
            elif self.state == AudioState.STOPPED and self._samples is not None:
                self.state = AudioState.PLAYING
            return self.state

    def stop(self) -> None:
        """Stop playback and rewind to start."""
        with self._lock:
            self.state = AudioState.STOPPED
            self._current_frame = 0
            self._decay_levels()

    def seek(self, delta_seconds: float) -> float:
        """Seek relative to current position."""
        with self._lock:
            if self._samples is None or self._total_frames == 0:
                return 0.0
            delta_frames = int(delta_seconds * self.SAMPLE_RATE)
            self._current_frame = max(0, min(self._total_frames, self._current_frame + delta_frames))
            return self._current_frame / self.SAMPLE_RATE

    def seek_to(self, target_seconds: float) -> float:
        """Seek to absolute time in seconds."""
        with self._lock:
            if self._samples is None or self._total_frames == 0:
                return 0.0
            target_frame = int(target_seconds * self.SAMPLE_RATE)
            self._current_frame = max(0, min(self._total_frames, target_frame))
            return self._current_frame / self.SAMPLE_RATE

    def set_volume(self, volume: float) -> float:
        """Set volume level between 0.0 and 1.0."""
        with self._lock:
            self.volume = max(0.0, min(1.0, volume))
            if self.volume > 0:
                self.is_muted = False
            return self.volume

    def change_volume(self, delta: float) -> float:
        """Adjust volume by delta."""
        return self.set_volume(self.volume + delta)

    def toggle_mute(self) -> bool:
        """Toggle mute on/off."""
        with self._lock:
            if self.is_muted:
                self.is_muted = False
                self.volume = self._prev_volume or 0.8
            else:
                self._prev_volume = self.volume
                self.is_muted = True
            return self.is_muted

    def get_progress(self) -> Tuple[float, float, float]:
        """Return (current_seconds, total_seconds, progress_percentage)."""
        with self._lock:
            if self._total_frames == 0:
                return 0.0, 0.0, 0.0
            current_sec = self._current_frame / self.SAMPLE_RATE
            total_sec = self._total_frames / self.SAMPLE_RATE
            pct = min(100.0, (current_sec / total_sec) * 100.0) if total_sec > 0 else 0.0
            return current_sec, total_sec, pct

    def get_visualizer_levels(self) -> List[float]:
        """Return snapshot of current visualizer bar heights [0.0 - 1.0]."""
        with self._lock:
            return list(self.levels)

    def check_and_clear_finished(self) -> bool:
        """Atomically check if track reached end and clear the flag."""
        with self._lock:
            if self.track_finished_flag:
                self.track_finished_flag = False
                return True
            return False

    def close(self) -> None:
        """Safely close CoreAudio device on shutdown."""
        try:
            self._device.close()
        except Exception:
            pass
