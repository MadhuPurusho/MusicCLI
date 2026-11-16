# Pulse CLI: Architecture Specification and User Manual

---

## Abstract

Pulse CLI is a pure Unix terminal audio player optimized for the macOS platform. Operating in the tradition of classic console utilities such as `cmus` and `ncmpcpp`, the system utilizes a high-performance `curses` double-buffered cell grid with zero graphical toolkit or web styling overhead. Playback is driven directly via a native Darwin CoreAudio thread. Audio frames encoded in MPEG-1/2 Audio Layer III (MP3) are decoded to uncompressed 16-bit signed Linear Pulse Code Modulation (LPCM) in memory, yielding instantaneous seeking, exact frame accounting, and real-time discrete spectral analysis.

---

## 1. System Architecture

```
+-------------------------------------------------------------------------------+
|                      POSIX Terminal Display Engine                            |
|                  (Curses Double-Buffered Grid: 25 Hz)                         |
|                                                                               |
|  +-------------------------------------+  +--------------------------------+  |
|  |  [1: FILES]                         |  |  [2: QUEUE]                    |  |
|  |  - Direct Inode Navigation (../)    |  |  - Sequential / Shuffle Model  |  |
|  |  - Strict MP3 Filter (.mp3)         |  |  - Direct Index Traversal      |  |
|  |  - Instant Substring Search (/)     |  |  - Track Scrubber Accounting   |  |
|  +-------------------------------------+  +--------------------------------+  |
|                                                                               |
|  +-------------------------------------------------------------------------+  |
|  |  Dynamic Spectral Equalizer (Real-Time Unicode Discrete Block Array)    |  |
|  +-------------------------------------------------------------------------+  |
|  |  Now Playing Telemetry, Progress Scrubber, and Status Metrics           |  |
+-------------------------------------------------------------------------------+
                                    |        ^
                       State Events |        | PCM Energy Levels
                                    v        |
+-------------------------------------------------------------------------------+
|                             Core Audio Engine                                 |
|                     (miniaudio / Darwin CoreAudio Host)                       |
|                                                                               |
|   +-------------------+    +--------------------+    +--------------------+   |
|   | MP3 Bitstream     | -> | 44.1 kHz Stereo    | -> | CoreAudio Output   |   |
|   | Decoder           |    | PCM Circular Ring  |    | Buffer (1024 smp)  |   |
|   +-------------------+    +--------------------+    +--------------------+   |
+-------------------------------------------------------------------------------+
```

### 1.1 Audio Processing Pipeline
1. **Decode Phase**: Incoming MP3 bitstreams are parsed and decoded into continuous arrays of signed 16-bit interleaved integers normalized to a constant sampling frequency of 44,100 Hz across two channels (Stereo).
2. **Buffering & Delivery**: An audio generation coroutine supplies contiguous 1024-frame chunks to the native macOS CoreAudio output bus via `miniaudio`.
3. **Seeking**: Temporal offsets are mapped directly to memory indices via:
   $$\text{Frame Offset} = \text{Target Time (seconds)} \times 44100$$
4. **Spectral Decomposition**: Prior to volume attenuation, each buffer chunk is partitioned into discrete bins. Peak and root-mean-square (RMS) energies are extracted and conditioned via non-linear perceptual scaling before transmission to the interface layer.

---

## 2. Installation and Deployment

### 2.1 System Prerequisites
- macOS 11.0 (Big Sur) or newer
- Python 3.9 or higher

### 2.2 Automated Local Installation
```bash
git clone https://github.com/pookie421/MusicCLI.git
cd MusicCLI
./install.sh
```

Ensure `~/.local/bin` is exported in your shell environment configuration (`~/.zshrc`):
```bash
export PATH="$HOME/.local/bin:$PATH"
```

### 2.3 Manual Setup via Virtual Environment
```bash
git clone https://github.com/pookie421/MusicCLI.git
cd MusicCLI
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

---

## 3. Command-Line Interface and Execution

```bash
# Initialize player within default directory (~/Music or current path)
pulse

# Initialize file explorer anchored to a specific directory path
pulse /Volumes/Storage/Audio/Classics

# Initiate immediate playback of a specific audio file
pulse /Volumes/Storage/Audio/track_01.mp3

# Display version specification
pulse --version

# Display argument parser help
pulse --help
```

---

## 4. Control Interface Matrix

The application operates entirely within the terminal viewport without external dependencies or pseudo-GUI widgets. Navigation conforms to standard UNIX / Vim patterns:

| Subsystem | Input Key | Function |
| :--- | :--- | :--- |
| **Playback** | `Space` | Toggle Play / Pause state |
| | `Left` / `Right` (or `,` / `.`) | Seek backward / forward 5 seconds |
| | `+` / `-` (or `]` / `[`) | Increment / decrement gain attenuation by 5% |
| | `m` | Toggle audio mute |
| | `n` / `p` | Advance / revert track in queue sequence |
| | `r` | Cycle repetition state (Off -> Track -> Queue) |
| | `s` | Toggle randomized playback sequence (Shuffle) |
| **Browser** | `j` / `k` (or `Up` / `Down`) | Move cursor selection |
| | `Enter` (or `l`) | Open directory or trigger playback of selected file |
| | `Backspace` (or `h`) | Ascend to parent directory (`../`) |
| | `a` | Enqueue selected file or recursively scan directory for MP3 files |
| | `/` | Activate live substring search filter |
| | `Esc` | Clear search filter |
| **Panes & Queue**| `Tab` | Switch active focus between Files (1) and Queue (2) |
| | `Enter` *(in Queue)* | Force jump to selected record |
| | `d` / `x` | Remove selected record from queue |
| | `c` | Clear entire queue |
| **Global** | `?` | Display internal operator reference manual |
| | `q` | Terminate application process cleanly |

---

## 5. Technical Specifications

- **Terminal Driver**: Native POSIX `curses` standard library
- **Audio Output Driver**: Native CoreAudio via `miniaudio` (macOS Darwin API)
- **Metadata Engine**: `mutagen` (ID3v1.1, ID3v2.3, ID3v2.4 parser)
- **Sampling Parameters**: 44,100 Hz, 16-bit Signed Integer, 2 Channels (Stereo)
- **Refresh Frequency**: 25 Hz (40 ms non-blocking event loop)
- **Memory Footprint**: < 20 MB resident memory

---

## 6. Project Structure

```
MusicCLI/
|-- pulse/
|   |-- __init__.py           # Package version definition
|   |-- __main__.py          # CLI entry point and argument parsing
|   |-- app.py               # Pure curses interface loop and rendering
|   |-- audio.py             # CoreAudio driver and PCM streaming
|   |-- metadata.py          # ID3 extraction and duration calculation
|   `-- queue_manager.py     # Deterministic and randomized queue models
|-- pyproject.toml           # PEP 621 package metadata
|-- requirements.txt         # Minimal runtime dependencies (miniaudio, mutagen)
|-- install.sh               # Local deployment script
|-- LICENSE                  # MIT License
`-- README.md                # System documentation
```

---

## 7. Contributing and Development Guidelines

### 7.1 Development Setup
```bash
git clone https://github.com/<your-username>/MusicCLI.git
cd MusicCLI
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 7.2 Pull Request Protocol
- Maintain pure curses architecture; do not introduce web/CSS/widget framework dependencies.
- Ensure strict type annotations and PEP 8 formatting.
- Confirm terminal state restoration upon process exit.

---

## 8. License

This project is released under the terms of the MIT License. See [LICENSE](LICENSE) for the full text.
