"""Digital Signal Processing (DSP) and spectral analysis engine.

Provides pure-Python, zero-dependency implementations of:
- 512-point Cooley-Tukey Radix-2 Fast Fourier Transform (FFT) with Hann windowing.
- Logarithmic musical octave sub-band decimation (64 bands, 20 Hz - 20,000 Hz).
- Stereo Left/Right Root-Mean-Square (RMS) and peak decibel VU metering.
- Real-time oscilloscopic PCM waveform rendering.
- ReplayGain / EBU R128 loudness scaling.
"""

from __future__ import annotations

import array
import cmath
import math
from enum import Enum
from typing import List, Sequence, Tuple


class VisualizerMode(Enum):
    SPECTRUM = "SPECTRUM"
    WAVEFORM = "WAVEFORM"
    STEREO_VU = "STEREO_VU"

    def next_mode(self) -> VisualizerMode:
        modes = list(VisualizerMode)
        idx = (modes.index(self) + 1) % len(modes)
        return modes[idx]


class AudioDSP:
    """Discrete-time Fourier analysis and psychoacoustic spectral weighting."""

    FFT_SIZE = 512
    HALF_FFT = FFT_SIZE // 2
    NUM_BANDS = 64

    def __init__(self):
        # Precomputed periodic Hann window function
        self._hann = [
            0.5 * (1.0 - math.cos(2.0 * math.pi * i / (self.FFT_SIZE - 1)))
            for i in range(self.FFT_SIZE)
        ]

        # Precomputed complex twiddle factors W_N^k = exp(-2pi*j*k / N)
        self._twiddles = [
            cmath.exp(-2j * math.pi * k / self.FFT_SIZE)
            for k in range(self.HALF_FFT)
        ]

        # Bit-reversal permutation lookup table for O(N) reordering
        bits = int(math.log2(self.FFT_SIZE))
        self._rev_map = [int(f"{i:0{bits}b}"[::-1], 2) for i in range(self.FFT_SIZE)]

        # Hybrid linear-logarithmic frequency bin allocations
        # Bands 0-15: linear 1-bin (fundamentals ~86 Hz to 1378 Hz)
        # Bands 16-63: logarithmic octaves (harmonics ~1.4 kHz to 20 kHz)
        self._bands: List[Tuple[int, int]] = []
        for i in range(16):
            self._bands.append((i + 1, i + 2))

        min_log = math.log10(17)
        max_log = math.log10(self.HALF_FFT - 1)
        for b in range(48):
            start = int(round(10 ** (min_log + b * (max_log - min_log) / 48)))
            end = int(round(10 ** (min_log + (b + 1) * (max_log - min_log) / 48)))
            start = max(17, min(self.HALF_FFT - 2, start))
            end = max(start + 1, min(self.HALF_FFT - 1, end))
            self._bands.append((start, end))

        # Psychoacoustic equal-loudness tilt (ISO 226 curve approximation)
        self._tilt = [1.0 + (b / 63.0) * 0.95 for b in range(self.NUM_BANDS)]

    def compute_spectrum(self, mono_chunk: Sequence[float | int]) -> List[float]:
        """Compute 64-band logarithmic FFT spectrum from 512 mono audio samples."""
        if len(mono_chunk) < self.FFT_SIZE:
            return [0.0] * self.NUM_BANDS

        # 1. Apply Hann window and bit-reversal reordering
        a: List[complex] = [
            complex(
                float(mono_chunk[self._rev_map[i]])
                * self._hann[self._rev_map[i]]
                / 32768.0,
                0.0,
            )
            for i in range(self.FFT_SIZE)
        ]

        # 2. In-place Cooley-Tukey Radix-2 butterfly network
        length = 2
        while length <= self.FFT_SIZE:
            half = length // 2
            step = self.FFT_SIZE // length
            for i in range(0, self.FFT_SIZE, length):
                for k in range(half):
                    w = self._twiddles[k * step]
                    u = a[i + k]
                    v = a[i + k + half] * w
                    a[i + k] = u + v
                    a[i + k + half] = u - v
            length *= 2

        # 3. Magnitude spectrum normalization
        magnitudes = [abs(a[i]) / self.HALF_FFT for i in range(self.HALF_FFT)]

        # 4. Decimate into 64 logarithmic octave bands with equal-loudness weighting
        output_levels = []
        for b, (s, e) in enumerate(self._bands):
            sub = magnitudes[s:e]
            peak = max(sub) if sub else magnitudes[s]
            weighted = peak * self._tilt[b]
            # Non-linear power-law dynamic range compression
            scaled = min(1.0, math.pow(weighted * 3.5, 0.40)) if weighted > 0 else 0.0
            output_levels.append(scaled)

        return output_levels


class StereoVUMeter:
    """Stereo Root-Mean-Square (RMS) and true-peak decibel level meter."""

    @staticmethod
    def compute(chunk: array.array, channels: int = 2) -> Tuple[float, float, float, float]:
        """Return (peak_left, peak_right, db_left, db_right)."""
        if not chunk or len(chunk) < channels:
            return 0.0, 0.0, -60.0, -60.0

        left = chunk[0::channels]
        right = chunk[1::channels] if channels > 1 else left

        # True Peak normalization
        max_l = max(abs(min(left)), abs(max(left)))
        max_r = max(abs(min(right)), abs(max(right)))
        peak_l = min(1.0, max_l / 32768.0)
        peak_r = min(1.0, max_r / 32768.0)

        # RMS Power calculation
        sum_sq_l = sum(s * s for s in left)
        sum_sq_r = sum(s * s for s in right)
        rms_l = math.sqrt(sum_sq_l / len(left)) / 32768.0
        rms_r = math.sqrt(sum_sq_r / len(right)) / 32768.0

        db_l = max(-60.0, min(3.0, 20.0 * math.log10(rms_l + 1e-6)))
        db_r = max(-60.0, min(3.0, 20.0 * math.log10(rms_r + 1e-6)))

        return peak_l, peak_r, db_l, db_r


class WaveformOscilloscope:
    """Downsamples active audio PCM stream to generate terminal oscilloscope trace."""

    @staticmethod
    def render(
        chunk: array.array,
        width: int,
        height: int,
        channels: int = 2,
    ) -> List[str]:
        """Render discrete oscilloscope waveform into a grid of text strings."""
        if width <= 2 or height <= 1 or not chunk:
            return [" " * width] * height

        inner_w = width - 2
        inner_h = height - 2
        mono = chunk[0::channels]
        step = max(1, len(mono) // inner_w)
        mid_y = inner_h // 2

        # Grid allocation: matrix of character coordinates
        grid = [[" " for _ in range(inner_w)] for _ in range(inner_h)]

        for x in range(inner_w):
            sub = mono[x * step : (x + 1) * step]
            if not sub:
                val = 0.0
            else:
                # Average deflection for smoothed visual wave
                val = sum(sub) / (len(sub) * 32768.0)

            # Map [-1.0, 1.0] to row index [inner_h - 1, 0]
            row_idx = int(round(mid_y - (val * (inner_h / 2.1))))
            row_idx = max(0, min(inner_h - 1, row_idx))

            if row_idx == mid_y:
                char = "─"
            elif row_idx < mid_y:
                char = "▀"
            else:
                char = "▄"

            grid[row_idx][x] = char

        lines = []
        for r in range(inner_h):
            lines.append("".join(grid[r]))

        return lines


class ReplayGain:
    """Parses standard ReplayGain and EBU R128 tags to compute linear amplitude multipliers."""

    @staticmethod
    def parse_gain_db(gain_str: str) -> float:
        """Parse gain string (e.g. '-6.5 dB', '+1.20 LUFS') into numeric decibels."""
        if not gain_str:
            return 0.0
        try:
            cleaned = gain_str.lower().replace("db", "").replace("lufs", "").strip()
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def to_linear_scale(gain_db: float, pre_amp_db: float = 0.0) -> float:
        """Convert decibel gain to linear multiplication factor: 10^((gain + pre) / 20)."""
        tot_db = gain_db + pre_amp_db
        # Clamp multiplier between -18 dB and +12 dB to prevent clipping
        tot_db = max(-18.0, min(12.0, tot_db))
        return math.pow(10.0, tot_db / 20.0)
