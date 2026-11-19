# Audio Engine: Mathematical Foundations & CoreAudio Integration

This document specifies the internal mathematical models, signal routing, and Darwin CoreAudio Hardware Abstraction Layer (HAL) interfacing implemented in `pulse.audio`.

---

## 1. Digital Signal Representation

### 1.1 Quantization & Sampling Model
Audio signals are represented as discrete-time, quantized linear pulse-code modulation (LPCM) sequences.

* **Sampling Frequency ($f_s$)**: $44,100\text{ Hz}$.
* **Nyquist Limit ($f_N$)**:
  $$f_N = \frac{f_s}{2} = 22,050\text{ Hz}$$
  In accordance with the Nyquist-Shannon sampling theorem, frequencies exceeding $f_N$ are band-limited prior to reconstruction by the digital-to-analog converter (DAC).
* **Sample Quantization**: Signed 16-bit two's complement integers:
  $$s \in \mathbb{Z}, \quad -2^{15} \le s \le 2^{15} - 1 \implies -32768 \le s \le 32767$$
* **Theoretical Dynamic Range**:
  $$\text{DR} = 20 \log_{10}(2^{16}) \approx 96.3296\text{ dB}$$
* **Signal-to-Quantization-Noise Ratio (SQNR)**:
  $$\text{SQNR} \approx 6.02 \cdot B + 1.76\text{ dB} = 6.02 \times 16 + 1.76 \approx 98.08\text{ dB}$$

---

## 2. Darwin CoreAudio Real-Time Execution Model

### 2.1 Hardware Abstraction Layer (HAL) AudioUnit Architecture
On macOS Darwin, audio output is mediated by `AudioOutputUnit` instances managed by the CoreAudio HAL daemon (`coreaudiod`).

```
+-------------------------------------------------------------------------------+
|                       Darwin Kernel Space (Mach Core)                         |
|                                                                               |
|   +-----------------------------------------------------------------------+   |
|   | Physical Audio Hardware / DMA Engine (I2S / USB / Thunderbolt)        |   |
|   +-----------------------------------------------------------------------+   |
+--------------------------------------|----------------------------------------+
                                       | Mach Message IPC
+--------------------------------------v----------------------------------------+
|                      User Space: coreaudiod HAL Daemon                        |
|                                                                               |
|   +-----------------------------------------------------------------------+   |
|   | Real-Time Audio Thread (POSIX Thread with THREAD_TIME_CONSTRAINT_POLICY)|
|   +-----------------------------------------------------------------------+   |
+--------------------------------------|----------------------------------------+
                                       | C Callback
+--------------------------------------v----------------------------------------+
|                      Miniaudio C Runtime (CFFI Layer)                         |
|                                                                               |
|   +-----------------------------------------------------------------------+   |
|   | Pull-Model Ring Buffer Engine (ma_device_callback)                    |   |
|   +-----------------------------------------------------------------------+   |
+--------------------------------------|----------------------------------------+
                                       | Python Generator: next()
+--------------------------------------v----------------------------------------+
|                      Pulse AudioEngine (`pulse.audio`)                        |
|                                                                               |
|   +-----------------------------------------------------------------------+   |
|   | Linear PCM Memory Buffer -> Volume Scale -> Endian-Safe Byte Stream   |   |
|   +-----------------------------------------------------------------------+   |
+-------------------------------------------------------------------------------+
```

### 2.2 Callback Timing & Jitter Budget
Miniaudio configures an output buffer of $N_{\text{chunk}} = 1024$ frames. The nominal callback delivery interval is:

$$T_{\text{period}} = \frac{N_{\text{chunk}}}{f_s} = \frac{1024}{44100} \approx 23.21995\text{ ms}$$

The deadline for Thread 1 to compute, attenuate, and deliver the 4096-byte chunk is:

$$t_{\text{execution}} < T_{\text{period}}$$

To prevent buffer underrun (audio dropout/glitch), `AudioEngine` enforces:
1. Complete memory pre-buffering of the decoded track.
2. In-place slicing of pre-allocated contiguous arrays (`array.array('h')`).
3. Zero lock retention across the generator `yield`.

---

## 3. Volume Attenuation & Signal Scaling

### 3.1 Digital Gain Formula
Volume scaling applies digital attenuation $G \in [0.0, 1.0]$ directly to the discrete sample points:

$$s_{\text{out}}[n] = \text{clamp}\left(-32768, 32767, \left\lfloor s_{\text{in}}[n] \times G \right\rfloor\right)$$

### 3.2 Optimization Paths
1. **Unity Gain Pass-Through ($G \ge 0.98$)**:
   Direct memory view serialization via `.tobytes()`, bypassing $2048$ floating-point multiplications per callback chunk.
2. **Zero-Volume Mute Optimization ($G \le 0.001$)**:
   Instantaneous allocation of a zeroed byte sequence:
   $$\text{data} = b\text{"\textbackslash x00"} \times (N_{\text{samples}} \times 2)$$
   Execution time is $O(1)$ relative to sample processing, yielding $>1000\times$ speedup.

---

## 4. Visualizer Equalizer Processing

### 4.1 Sub-Band Amplitude Computation
For each buffer delivery, the $2048$ interleaved samples are partitioned into $B = 64$ sub-bands:

$$\text{Bin Size } K = \left\lfloor \frac{2048}{64} \right\rfloor = 32\text{ samples}$$

The peak amplitude $\hat{P}_b$ for bin $b \in [0, 63]$ is determined by extracting the maximum absolute integer value:

$$\hat{P}_b = \max_{i=0}^{31} |s_{b \cdot 32 + i}|$$

### 4.2 Non-Linear Equalizer Perceptual Curve
Human acoustic sensitivity follows a non-linear logarithmic curve. Linear peaks $A_b = \frac{\hat{P}_b}{32768.0}$ are transformed using an exponential power-law curve:

$$Y_b = \min\left(1.0, 1.65 \times A_b^{0.38}\right)$$

### 4.3 Temporal Low-Pass Smoothing
To avoid erratic jitter in the visual display, levels undergo asymmetric temporal smoothing:

$$L_b^{(t)} = \begin{cases}
0.15 \cdot L_b^{(t-1)} + 0.85 \cdot Y_b, & \text{if } Y_b > L_b^{(t-1)} \\
0.70 \cdot L_b^{(t-1)} + 0.30 \cdot Y_b, & \text{if } Y_b \le L_b^{(t-1)}
\end{cases}$$

When audio is paused or stopped, idle decay is modeled linearly:

$$L_b^{(t)} = \max\left(0.0, 0.72 \cdot L_b^{(t-1)} - 0.02\right)$$

---

## 5. Discrete-Time Fourier Transform & FFT Subsystem

### 5.1 Discrete Fourier Transform Formulation
The discrete Fourier transform (DFT) converts a finite discrete-time sequence $x[n]$ of length $N = 512$ into complex frequency components $X[k]$:

$$X[k] = \sum_{n=0}^{N-1} x[n] \cdot W_N^{kn}, \quad W_N = e^{-j \frac{2\pi}{N}}, \quad k \in [0, N-1]$$

### 5.2 Cooley-Tukey Radix-2 Butterfly Decomposition
To avoid $O(N^2)$ direct computation time ($262,144$ operations), `AudioDSP` implements the decimation-in-time (DIT) Cooley-Tukey algorithm, reducing complexity to $O(N \log_2 N) = 512 \times 9 = 4,608$ butterfly operations:

$$X[k] = E[k] + W_N^k O[k]$$
$$X[k + N/2] = E[k] - W_N^k O[k]$$

where $E[k]$ and $O[k]$ represent the $N/2$-point DFTs of the even and odd indexed input samples, respectively.

#### 5.2.1 Twiddle Factor Precomputation
Complex twiddle factors $W_N^k$ are computed once during engine instantiation and memoized in contiguous memory:

$$W_N^k = \cos\left(\frac{2\pi k}{N}\right) - j \sin\left(\frac{2\pi k}{N}\right), \quad k \in [0, 255]$$

#### 5.2.2 Bit-Reversal Permutation
Samples are reordered into bit-reversed sequence prior to butterfly evaluation using a precalculated permutation array $\sigma: [0, 511] \to [0, 511]$:

$$\sigma(n) = \text{reverse\_bits}_{9}(n)$$

### 5.3 Hann Windowing Function
To mitigate spectral leakage caused by finite-length time-domain truncation, samples are weighted by a periodic Hann window prior to FFT transformation:

$$w[n] = 0.5 \left(1 - \cos\left(\frac{2\pi n}{N - 1}\right)\right), \quad n \in [0, 511]$$

### 5.4 Hybrid Linear-Logarithmic Frequency Bin Decimation
The 256 positive frequency bins ($0 \text{ Hz}$ to $22,050 \text{ Hz}$, resolution $\Delta f = \frac{44100}{512} \approx 86.13\text{ Hz}$) are partitioned into $B = 64$ musical sub-bands:
* **Bands $0 \dots 15$ (Fundamentals)**: Linear 1-bin mapping covering $86.13\text{ Hz}$ through $1378.1\text{ Hz}$.
* **Bands $16 \dots 63$ (Harmonics & Treble)**: Logarithmic octave mapping covering $1.4\text{ kHz}$ to $20\text{ kHz}$.

### 5.5 Psychoacoustic Equal-Loudness Weighting
In accordance with ISO 226 equal-loudness contours, high-frequency bins undergo linear psychoacoustic tilt compensation:

$$T_b = 1.0 + \frac{b}{63} \times 0.95, \quad b \in [0, 63]$$

---

## 6. Stereo Root-Mean-Square (RMS) & True-Peak VU Metering

### 6.1 True-Peak Normalization
For discrete channels $c \in \{L, R\}$:

$$\hat{P}_c = \frac{\max_{n} |s_c[n]|}{32768.0}, \quad \hat{P}_c \in [0.0, 1.0]$$

### 6.2 Root-Mean-Square (RMS) Power & Decibel Full Scale (dBFS)
RMS power models perceived acoustic energy across the chunk duration:

$$\text{RMS}_c = \frac{1}{32768.0} \sqrt{\frac{1}{M} \sum_{m=0}^{M-1} s_c[m]^2}$$

The decibel full scale level $L_{\text{dBFS}}$ is calculated as:

$$L_{\text{dBFS}} = 20 \log_{10}\left(\text{RMS}_c + 10^{-6}\right)$$

Clamped between $-60.0\text{ dBFS}$ (noise floor) and $+3.0\text{ dBFS}$ (headroom). Clip warnings assert whenever $\hat{P}_c \ge 0.98$ or $L_{\text{dBFS}} \ge -0.1\text{ dBFS}$.

---

## 7. Clickless 128-Sample Seek Gain Ramping

Direct time seeks in linear PCM buffers introduce step discontinuities $\Delta s = s[k_{\text{new}}] - s[k_{\text{old}}]$ that manifest as audible acoustic transients (clicks/pops).

To eliminate seek transients without altering the immutable audio buffer, `AudioEngine` activates a non-destructive 128-sample linear gain envelope immediately following any seek instruction:

$$s_{\text{ramped}}[n] = \left\lfloor s[n] \times \frac{n}{128} \right\rfloor, \quad n \in [0, 127]$$

At $f_s = 44,100\text{ Hz}$, the ramp interval is $\tau = \frac{128}{44100} \approx 2.902\text{ ms}$, entirely below human pitch perception thresholds while preventing high-frequency Dirac click impulses.

---

## 8. ReplayGain & EBU R128 Loudness Normalization

ReplayGain tags (`REPLAYGAIN_TRACK_GAIN`) parsed from FLAC Vorbis comments and MP3 ID3 frames specify track gain deviations in decibels. The linear scaling multiplier $S$ is computed via:

$$S = 10^{\frac{G_{\text{track}} + G_{\text{preamp}}}{20}}$$

Multipliers are clamped between $-18.0\text{ dB}$ ($S = 0.126$) and $+12.0\text{ dB}$ ($S = 3.981$) to prevent uncontrolled digital overflow.
