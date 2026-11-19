"""Audio playback engine using Miniaudio and macOS CoreAudio."""

from __future__ import annotations

import array
import math
import threading
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import miniaudio

from pulse.dsp import AudioDSP, ReplayGain, StereoVUMeter, VisualizerMode, WaveformOscilloscope
from pulse.metadata import TrackMetadata


class AudioState(Enum):
    STOPPED = "STOPPED"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"


class AudioEngine:
    """CoreAudio-backed audio playback and analysis engine."""

    SAMPLE_RATE = 44100
    CHANNELS = 2
    NUM_VISUALIZER_BARS = 64

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

        self.dsp = AudioDSP()
        self.visualizer_mode: VisualizerMode = VisualizerMode.SPECTRUM
        self.levels: List[float] = [0.0] * self.NUM_VISUALIZER_BARS
        self.stereo_levels: Tuple[float, float, float, float] = (0.0, 0.0, -60.0, -60.0)
        self._waveform_buffer: array.array = array.array("h")
        self.replay_gain_scale: float = 1.0
        self.replay_gain_enabled: bool = True
        self._seek_ramp_frames: int = 0

        self.track_finished_flag: bool = False
        self.on_track_end: Optional[Callable[[], None]] = None

        # Initialize CoreAudio playback device with resilience
        self._device: Optional[miniaudio.PlaybackDevice] = None
        self._gen = self._audio_stream_generator()
        next(self._gen)  # Prime generator
        try:
            self._device = miniaudio.PlaybackDevice(
                nchannels=self.CHANNELS,
                sample_rate=self.SAMPLE_RATE,
            )
            self._device.start(self._gen)
        except Exception:
            self._device = None

    def _audio_stream_generator(self):
        """Audio callback generator that streams PCM frames to the CoreAudio device."""
        num_frames = yield b""

        while True:
            if num_frames is None or num_frames <= 0:
                num_frames = 1024

            raw_chunk = None
            eff_vol = 0.0
            finished_callback = None
            ramp_needed = 0

            with self._lock:
                base_vol = 0.0 if self.is_muted else self.volume
                eff_vol = base_vol * (self.replay_gain_scale if self.replay_gain_enabled else 1.0)
                eff_vol = max(0.0, min(1.0, eff_vol))
                ramp_needed = self._seek_ramp_frames
                self._seek_ramp_frames = 0

                if self.state == AudioState.PLAYING and self._samples is not None:
                    if self._current_frame >= self._total_frames:
                        self.state = AudioState.STOPPED
                        self.track_finished_flag = True
                        self._decay_levels()
                        finished_callback = self.on_track_end
                    else:
                        count = min(num_frames, self._total_frames - self._current_frame)
                        start_idx = self._current_frame * self.CHANNELS
                        end_idx = start_idx + (count * self.CHANNELS)
                        raw_chunk = self._samples[start_idx:end_idx]

                        # Update visualizer spectrum from raw samples before volume attenuation
                        self._update_levels(raw_chunk)

                        if count < num_frames:
                            self.state = AudioState.STOPPED
                            self._current_frame = self._total_frames
                            self.track_finished_flag = True
                            finished_callback = self.on_track_end
                        else:
                            self._current_frame += count
                else:
                    self._decay_levels()

            # Trigger optional callback outside lock
            if finished_callback:
                try:
                    finished_callback()
                except Exception:
                    pass

            # Apply volume attenuation and padding outside lock to prevent contention
            if raw_chunk is not None:
                if eff_vol <= 0.001:
                    data = b"\x00" * (len(raw_chunk) * 2)
                elif eff_vol < 0.98 or ramp_needed > 0:
                    v_int = int(eff_vol * 32768)
                    scaled_samples = array.array("h", ((s * v_int) >> 15 for s in raw_chunk))
                    if ramp_needed > 0:
                        ramp_len = min(ramp_needed, len(scaled_samples) // self.CHANNELS)
                        for f in range(ramp_len):
                            factor = f / float(ramp_needed)
                            idx_l = f * self.CHANNELS
                            scaled_samples[idx_l] = int(scaled_samples[idx_l] * factor)
                            if self.CHANNELS > 1:
                                scaled_samples[idx_l + 1] = int(scaled_samples[idx_l + 1] * factor)
                    data = scaled_samples.tobytes()
                else:
                    data = raw_chunk.tobytes()

                pad_frames = num_frames - (len(raw_chunk) // self.CHANNELS)
                if pad_frames > 0:
                    data += b"\x00" * (pad_frames * self.CHANNELS * 2)
            else:
                data = b"\x00" * (num_frames * self.CHANNELS * 2)

            num_frames = yield data

    def _update_levels(self, chunk: array.array) -> None:
        """Compute real-time 64-band FFT spectrum and stereo VU levels."""
        if not chunk:
            self._decay_levels()
            return

        # 1. Update Stereo VU meters (peak + dBFS RMS)
        self.stereo_levels = StereoVUMeter.compute(chunk, self.CHANNELS)

        # 2. Store sample buffer for oscilloscope waveform
        self._waveform_buffer = chunk[:min(len(chunk), 2048)]

        # 3. Downmix chunk to 512 mono samples for Radix-2 Cooley-Tukey FFT
        chunk_len = len(chunk)
        if self.CHANNELS == 2:
            step = max(1, (chunk_len // 2) // AudioDSP.FFT_SIZE)
            mono = [
                (chunk[i * 2] + chunk[i * 2 + 1]) >> 1
                for i in range(0, min(chunk_len // 2, AudioDSP.FFT_SIZE * step), step)
            ][:AudioDSP.FFT_SIZE]
        else:
            step = max(1, chunk_len // AudioDSP.FFT_SIZE)
            mono = [chunk[i] for i in range(0, min(chunk_len, AudioDSP.FFT_SIZE * step), step)][:AudioDSP.FFT_SIZE]

        if len(mono) >= AudioDSP.FFT_SIZE:
            fft_levels = self.dsp.compute_spectrum(mono)
            for b in range(self.NUM_VISUALIZER_BARS):
                target = fft_levels[b]
                if target > self.levels[b]:
                    self.levels[b] = self.levels[b] * 0.15 + target * 0.85
                else:
                    self.levels[b] = self.levels[b] * 0.65 + target * 0.35
        else:
            # Fallback if chunk underflows FFT size
            num_bars = self.NUM_VISUALIZER_BARS
            step_bar = max(1, chunk_len // num_bars)
            for b in range(num_bars):
                sub = chunk[b * step_bar : (b + 1) * step_bar]
                if sub:
                    peak = max(abs(min(sub)), abs(max(sub))) / 32768.0
                    target = min(1.0, math.pow(peak, 0.38) * 1.65)
                    self.levels[b] = self.levels[b] * 0.20 + target * 0.80

    def _decay_levels(self) -> None:
        """Decay spectrum bars and VU meters toward zero when audio is silent/idle."""
        for b in range(self.NUM_VISUALIZER_BARS):
            self.levels[b] = max(0.0, self.levels[b] * 0.72 - 0.02)
        p_l, p_r, db_l, db_r = self.stereo_levels
        self.stereo_levels = (
            max(0.0, p_l * 0.75 - 0.02),
            max(0.0, p_r * 0.75 - 0.02),
            max(-60.0, db_l - 3.0),
            max(-60.0, db_r - 3.0),
        )

    def load_track(self, metadata: TrackMetadata) -> bool:
        """Decode and load an MP3 or FLAC track into memory."""
        try:
            if not metadata or not metadata.path.is_file():
                return False
            decoded = miniaudio.decode_file(
                str(metadata.path),
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=self.CHANNELS,
                sample_rate=self.SAMPLE_RATE,
            )
            rg_db = getattr(metadata, "replay_gain_db", 0.0)
            with self._lock:
                self._samples = decoded.samples
                self._total_frames = decoded.num_frames
                self._current_frame = 0
                self.current_track = metadata
                self.replay_gain_scale = ReplayGain.to_linear_scale(rg_db) if rg_db != 0.0 else 1.0
                self.state = AudioState.PLAYING
                self.track_finished_flag = False
                self._seek_ramp_frames = 128
            if decoded.num_frames > 0:
                metadata.duration = decoded.num_frames / self.SAMPLE_RATE
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
            self.track_finished_flag = False
            self._decay_levels()

    def unload(self) -> None:
        """Stop playback and release loaded audio buffer from memory."""
        with self._lock:
            self.state = AudioState.STOPPED
            self._samples = None
            self._total_frames = 0
            self._current_frame = 0
            self.current_track = None
            self.track_finished_flag = False
            self._decay_levels()

    def seek(self, delta_seconds: float) -> float:
        """Seek relative to current position."""
        with self._lock:
            if self._samples is None or self._total_frames == 0:
                return 0.0
            delta_frames = int(delta_seconds * self.SAMPLE_RATE)
            self._current_frame = max(0, min(self._total_frames, self._current_frame + delta_frames))
            self._seek_ramp_frames = 128
            if self._current_frame >= self._total_frames and self.state == AudioState.PLAYING:
                self.state = AudioState.STOPPED
                self.track_finished_flag = True
            elif self._current_frame < self._total_frames:
                self.track_finished_flag = False
            return self._current_frame / self.SAMPLE_RATE

    def seek_to(self, target_seconds: float) -> float:
        """Seek to absolute time in seconds."""
        with self._lock:
            if self._samples is None or self._total_frames == 0:
                return 0.0
            target_frame = int(target_seconds * self.SAMPLE_RATE)
            self._current_frame = max(0, min(self._total_frames, target_frame))
            self._seek_ramp_frames = 128
            if self._current_frame >= self._total_frames and self.state == AudioState.PLAYING:
                self.state = AudioState.STOPPED
                self.track_finished_flag = True
            elif self._current_frame < self._total_frames:
                self.track_finished_flag = False
            return self._current_frame / self.SAMPLE_RATE

    def cycle_visualizer_mode(self) -> VisualizerMode:
        """Cycle visualizer display mode: SPECTRUM -> WAVEFORM -> STEREO_VU."""
        with self._lock:
            self.visualizer_mode = self.visualizer_mode.next_mode()
            return self.visualizer_mode

    def get_visualizer_mode(self) -> VisualizerMode:
        """Return currently active visualizer mode."""
        with self._lock:
            return self.visualizer_mode

    def get_stereo_levels(self) -> Tuple[float, float, float, float]:
        """Return (peak_left, peak_right, db_left, db_right)."""
        with self._lock:
            return self.stereo_levels

    def get_waveform_lines(self, width: int, height: int) -> List[str]:
        """Generate discrete oscilloscope waveform string lines."""
        with self._lock:
            buf = array.array("h", self._waveform_buffer)
        return WaveformOscilloscope.render(buf, width, height, self.CHANNELS)

    def toggle_replay_gain(self) -> bool:
        """Toggle ReplayGain loudness normalization."""
        with self._lock:
            self.replay_gain_enabled = not self.replay_gain_enabled
            return self.replay_gain_enabled

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
            self.stop()
            if self._device is not None:
                self._device.close()
                self._device = None
        except Exception:
            pass
