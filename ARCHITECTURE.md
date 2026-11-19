# Pulse CLI: Subsystem Architecture & Engineering Specification

This document provides a low-level architectural specification of the Pulse CLI music playback engine, covering memory topology, thread scheduling, synchronization primitives, signal paths, and rendering protocols.

---

## 1. System Process & Thread Topology

The Pulse CLI process comprises three distinct execution contexts running concurrently on Darwin / macOS:

```
+-------------------------------------------------------------------------------------------------+
|                                     POSIX Process: Pulse CLI                                    |
|                                                                                                 |
|   Thread 0: UI / Event Loop             Thread 1: Audio HAL Callback     Thread Pool: Workers   |
|   (CPython Main Thread)                 (CoreAudio Real-Time Driver)     (Daemon ThreadPool)    |
|                                                                                                 |
|   +--------------------------+          +--------------------------+     +-------------------+  |
|   | curses.getch() (25 Hz)   |          | miniaudio C Generator    |     | iTunes Catalog    |  |
|   | double-buffer rendering  | <======> | PCM ring-buffer slice    |     | HTTP Fetcher      |  |
|   | stdscr.timeout(40ms)     |  State   | volume attenuation       |     | Disk Cache I/O    |  |
|   +--------------------------+  Events  +--------------------------+     +-------------------+  |
|                 |                                     |                            |            |
|                 v                                     v                            v            |
|   +-----------------------------------------------------------------------------------------+   |
|   |                             Shared Heap Memory Space                                    |   |
|   |   - array.array('h') Linear PCM Buffer (Stereo 44.1 kHz signed 16-bit)                 |   |
|   |   - Permutation Vector $\vec{\pi} \in S_N$ (Queue Index Map)                            |   |
|   |   - Hierarchical Metadata & Pixel Art LRU Caches                                        |   |
|   +-----------------------------------------------------------------------------------------+   |
+-------------------------------------------------------------------------------------------------+
```

### 1.1 Thread 0: Main Curses Interface & Event Loop
* **Scheduling**: Managed by the OS process scheduler; executes at standard user priority.
* **Frame Clock**: Clocked via `stdscr.timeout(40)` enforcing a target frame rate of $25\text{ frames/sec}$ ($\Delta T = 40\text{ ms}$).
* **Responsibilities**:
  1. Polls terminal input events via non-blocking POSIX `wgetch()`.
  2. Queries audio telemetry (`get_progress()`, `get_visualizer_levels()`).
  3. Evaluates track completion edge conditions (`check_and_clear_finished()`).
  4. Redraws the terminal grid utilizing curses virtual screens and calls `stdscr.refresh()`.

### 1.2 Thread 1: Real-Time Audio HAL Driver
* **Scheduling**: High-priority real-time audio thread scheduled directly by Apple's `coreaudiod` daemon via the miniaudio C runtime.
* **Buffer Chunk Interval**:
  $$\Delta t_{\text{buffer}} = \frac{1024\text{ frames}}{44100\text{ frames/sec}} \approx 23.2199\text{ ms}$$
* **Responsibilities**:
  1. Requests $N_{\text{frames}} = 1024$ signed 16-bit PCM frames from the Python generator.
  2. Acquires `self._lock` for $\Delta t < 1.5\ \mu\text{s}$ to read and advance the frame cursor.
  3. Computes 64-band visualizer peaks before applying volume attenuation.
  4. Yields the interleaved byte stream to the physical audio output buffer.

### 1.3 Thread 2+: Asynchronous I/O Workers (`ThreadPoolExecutor`)
* **Scheduling**: Background daemon worker pool (`max_workers=2`).
* **Responsibilities**:
  1. Non-blocking retrieval of catalog artwork from remote music endpoints.
  2. Writing downloaded binary images to persistent local cache (`~/.pulse-cli/cover_cache/`).
  3. Invoking in-place updates of `TrackMetadata.cover_data` and invalidating the 1-bit terminal raster cache.

---

## 2. Memory Topology & Buffer Architecture

### 2.1 Audio Buffer Memory Layout
Audio is fully decoded into contiguous host memory upon loading to guarantee sub-millisecond seek latency and prevent disk I/O stalls during streaming.

```
Address:       0x00000000        0x00000004        0x00000008        ...
Frame Index:   Frame 0           Frame 1           Frame 2           ...
Channels:      [ L0 ][ R0 ]      [ L1 ][ R1 ]      [ L2 ][ R2 ]      ...
Bit Layout:    [16-bit][16-bit]  [16-bit][16-bit]  [16-bit][16-bit]  ...
Byte Size:     |--- 4 Bytes ---| |--- 4 Bytes ---| |--- 4 Bytes ---| ...
```

* **Data Structure**: `array.array('h')` (Signed 16-bit integer representation in native machine endianness).
* **Quantization Range**: $[-32768, 32767]$.
* **Stereo Interleaving**: Odd indices represent the Left channel; even indices represent the Right channel.
* **Memory Bounds**: A standard 3-minute stereo song at $44.1\text{ kHz}$ allocates:
  $$\text{Memory} = 3 \times 60 \times 44100 \times 4\text{ bytes} = 31,752,000\text{ bytes} \approx 30.28\text{ MB}$$

### 2.2 Sub-Millisecond Seek Mathematics
Arbitrary temporal seeking maps directly to memory slice boundaries in $O(1)$ time complexity without re-reading container structures:

$$\text{Target Frame } k = \text{clamp}\left(0, N_{\text{total}}, \left\lfloor t_{\text{seconds}} \times 44100 \right\rfloor\right)$$
$$\text{Array Start Index} = k \times 2$$
$$\text{Array End Index} = \min\left(N_{\text{total}} \times 2, (k + N_{\text{chunk}}) \times 2\right)$$

---

## 3. Concurrency Invariants & Lock Hierarchy

To prevent priority inversion, deadlocks, and audio crackle/underruns, Pulse CLI enforces a strict lock acquisition discipline.

### 3.1 Lock Invariant Table
| Lock / Primitive | Guarded Resource | Acquisition Thread | Maximum Hold Time | Suspension Allowed |
| :--- | :--- | :--- | :--- | :--- |
| `AudioEngine._lock` | `_current_frame`, `state`, `_samples`, `track_finished_flag` | Thread 0 (UI), Thread 1 (Audio) | $\le 1.5\ \mu\text{s}$ | **NO** (Never hold across `yield`) |
| `_METADATA_CACHE` | Parsed track metadata dictionary | Thread 0 (UI), Thread 2+ (I/O) | Bounded by dict lookup ($O(1)$) | **NO** |

### 3.2 Deadlock Prevention Theorem
**Condition**: A generator yielding control back to a C driver holding an OS mutex causes permanent deadlock or high-latency lock contention if an external consumer calls methods requiring that mutex.

**Mitigation Protocol**:
1. All mutations of `_current_frame` and `state` occur within `with self._lock:`.
2. A local reference to the sample slice `raw_chunk` is copied within the lock.
3. The mutex is released.
4. Volume scaling integer math and padding are executed on the local slice.
5. The generator invokes `yield data` while **zero locks are held**.

---

## 4. Signal Processing & Equalizer Mathematics

```
+-----------------------------------------------------------------------------------+
|                        Audio Telemetry Signal Flow                                |
|                                                                                   |
|  [PCM Buffer] ---> [Slicing: 2048 Samples] ---> [Sub-Band Partitioning: 64 Bands] |
|                                                               |                   |
|                                                               v                   |
|  [Visualizer Grid] <-- [Attack/Decay Smoothing] <-- [Power-Law Compression]       |
+-----------------------------------------------------------------------------------+
```

### 4.1 Sub-Band Decimation
The incoming $1024$-frame buffer ($2048$ interleaved values) is partitioned into $B = 64$ frequency bins, each containing $K = 32$ samples:

$$\mathcal{B}_b = \{ s_{b \cdot 32 + i} \mid i \in [0, 31] \}, \quad b \in [0, 63]$$

The peak absolute value is computed:

$$P_b = \max_{x \in \mathcal{B}_b} |x| = \max\left( \left|\min(\mathcal{B}_b)\right|, \left|\max(\mathcal{B}_b)\right| \right)$$

### 4.2 Non-Linear Compression & Normalization
To compensate for human auditory threshold sensitivity (Fletcher-Munson equal-loudness contours), raw linear peaks are mapped through an exponential compression function:

$$A_b = \frac{P_b}{32768.0}$$
$$Y_b = \min\left(1.0, 1.65 \times A_b^{0.38}\right)$$

### 4.3 Envelope Follower Filter
Temporal transitions between frames $t-1$ and $t$ are smoothed using an asymmetric recursive IIR envelope follower:

$$L_b^{(t)} = \begin{cases}
\alpha_{\text{attack}} \cdot Y_b + (1 - \alpha_{\text{attack}}) \cdot L_b^{(t-1)}, & \text{if } Y_b > L_b^{(t-1)} \\
\alpha_{\text{decay}} \cdot Y_b + (1 - \alpha_{\text{decay}}) \cdot L_b^{(t-1)}, & \text{if } Y_b \le L_b^{(t-1)}
\end{cases}$$

where $\alpha_{\text{attack}} = 0.85$ (instantaneous response) and $\alpha_{\text{decay}} = 0.30$ (smooth visual release).

---

## 5. Permutational Queue Subsystem

The queue engine implements an exact Fisher-Yates permutation vector $\vec{\pi} \in S_N$, ensuring that every track is played once before recycling, with complete bi-directional traversal.

```
Queue Vector:   [ Track A,  Track B,  Track C,  Track D,  Track E ]
Indices:             0          1          2          3          4
Permutation π:  [    3,         0,         4,         1,         2    ]
Shuffle Cursor:                           ^ (k = 2, playing Track E)
```

### 5.1 Permutation Lifecycle
1. **Instantiation**: Given a queue of length $N$, the index vector $\vec{\pi} = \langle 0, 1, \dots, N-1 \rangle$ is generated.
2. **Fisher-Yates Shuffle**:
   $$\text{For } i \text{ from } N-1 \text{ down to } 1: \quad j \leftarrow \text{uniform}(0, i), \quad \text{swap}(\vec{\pi}[i], \vec{\pi}[j])$$
3. **Cursor Tracking**: A pointer $k \in [0, N-1]$ tracks the current playback position in $\vec{\pi}$.
4. **Bi-directional Navigation**:
   * Advance (`next_track`): $k \leftarrow k + 1$. Current track is $\text{tracks}[\vec{\pi}[k]]$.
   * Retreat (`prev_track`): $k \leftarrow k - 1$. Current track is $\text{tracks}[\vec{\pi}[k]]$.
5. **Dynamic Insertion**: When a track is added at index $M = N$, an insertion index $r \in [k + 1, |\vec{\pi}|]$ is selected at random, inserting the track into the unplayed partition of the permutation.

---

## 6. Terminal Display & Curses Double-Buffering

### 6.1 Layout Decomposition
The display layout automatically adapts based on terminal dimensions $(H, W)$:

* **$W \ge 96$**: 3-Pane Side-by-Side (`1: FILES` [36%], `2: QUEUE` [34%], `3: ALBUM ART` [30%]).
* **$70 \le W < 96$**: 2-Pane Split (`1: FILES` [50%], Active Pane `[2]` or `[3]` [50%]).
* **$W < 70$**: 1-Pane Full Focus (Cycles between `[1]`, `[2]`, and `[3]` via `Tab` or `1`/`2`/`3`).

### 6.2 Curses Virtual Buffer Optimization
To eliminate flickering, Pulse CLI leverages curses virtual window buffering:
1. `stdscr.erase()` clears the in-memory back-buffer without issuing VT100 clear-screen escape codes (`\033[2J`).
2. Pane borders, track lists, equalizers, and pixel art are written to the back-buffer using `_safe_addstr()` with boundary clipping.
3. `stdscr.refresh()` computes the minimal character diff against the physical terminal screen and flushes only changed cells via POSIX write calls.

---

## 7. Digital Signal Processing (DSP) & Tri-Mode Visualizer Pipeline

The `pulse.dsp` module introduces zero-dependency, pure-Python digital signal processing primitives executing in sub-millisecond execution windows.

```
+-------------------------------------------------------------------------------------------------+
|                                    pulse.dsp Subsystem                                          |
|                                                                                                 |
|   +---------------------+     +----------------------+     +--------------------------------+   |
|   |   AudioDSP Engine   |     |   StereoVUMeter      |     |     WaveformOscilloscope       |   |
|   | - 512-pt Cooley-    |     | - True-Peak Norm     |     | - Discrete PCM Downsampling    |   |
|   |   Tukey Radix-2 FFT |     | - RMS Power Integral |     | - Terminal Coordinate Mapping  |   |
|   | - Hann Windowing    |     | - dBFS Math (-60..+3)|     | - Unicode Half-Block Rendering |   |
|   | - 64 Octave Bands   |     | - Clip Warning Logic |     +--------------------------------+   |
|   +---------------------+     +----------------------+                                          |
+-------------------------------------------------------------------------------------------------+
```

### 7.1 Visualizer Operational Modes
1. **`VisualizerMode.SPECTRUM`**:
   512-point Cooley-Tukey Radix-2 FFT with periodic Hann windowing, mapped into 64 logarithmic octave frequency bands with peak-hold decay dots.
2. **`VisualizerMode.WAVEFORM`**:
   Discrete-time real-time oscilloscopic PCM trace rendering continuous time-domain waveform deflection using Unicode block elements (`─`, `▀`, `▄`).
3. **`VisualizerMode.STEREO_VU`**:
   Dual Left/Right analog dB level meters with RMS power estimation, true-peak tracking, calibrated tick scales, and dynamic clip warnings (`[CLIP!]`).

---

## 8. Dynamic VT100 OSC Terminal Protocol

Pulse CLI leverages the standard VT100 Operating System Command (OSC) escape sequence to update the host terminal emulator's window title and tab title dynamically:

$$\text{OSC Sequence: } \texttt{\textbackslash 033]0;}\langle \text{Title String} \rangle\texttt{\textbackslash 007}$$

Title formatting updates on state transitions:
* **Playing**: `▶ Artist - Title // PULSE`
* **Paused**: `⏸ Artist - Title // PULSE`
* **Stopped/Idle**: `■ PULSE CLI // [IDLE]`

Written directly to `sys.stdout` outside curses window buffers, ensuring complete cross-terminal compatibility across macOS Terminal.app, iTerm2, Alacritty, Kitty, WezTerm, and tmux.
