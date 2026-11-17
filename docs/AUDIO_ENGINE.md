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
