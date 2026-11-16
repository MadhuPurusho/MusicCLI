# Pulse CLI: Architecture Specification and User Manual

**Technical Report:** TR-2026-PULSE  
**System:** High-Performance Terminal Audio Reproduction and Spectral Analysis Engine  
**Platform:** macOS (Darwin / CoreAudio)  

---

## Abstract

Pulse CLI is a dedicated, keyboard-driven terminal audio player optimized for the macOS platform. The system couples an asynchronous Terminal User Interface (TUI) event loop with a direct hardware CoreAudio playback thread. Audio frames encoded in MPEG-1/2 Audio Layer III (MP3) are decoded to uncompressed 16-bit signed Linear Pulse Code Modulation (LPCM) in system memory, yielding sub-millisecond seek latency, exact frame accounting, and real-time spectral amplitude analysis. This document specifies the operational design, signal pipeline, operational controls, and contributor workflows.

---

## 1. System Architecture

```
+-------------------------------------------------------------------------------+
|                             Terminal User Interface                           |
|                       (Textual Reactive Event Loop: 25 Hz)                    |
|                                                                               |
|  +-----------------------+  +----------------------------------------------+  |
|  |   File Explorer       |  |  Player Card: ID3 Tags & Scrubber Engine     |  |
|  |   - Inode Traversal   |  +----------------------------------------------+  |
|  |   - MP3 Filter (.mp3) |  +----------------------------------------------+  |
|  |   - Substring Query   |  |  Spectral Equalizer (Unicode Block Arrays)   |  |
|  +-----------------------+  +----------------------------------------------+  |
|  +-----------------------+  +----------------------------------------------+  |
|  |   Queue Controller    |  |  Playback State & Volume Attenuation Deck    |  |
|  +-----------------------+  +----------------------------------------------+  |
+-------------------------------------------------------------------------------+
                                    |        ^
                       State Events |        | Amplitude Metrics
                                    v        |
+-------------------------------------------------------------------------------+
|                             Core Audio Engine                                 |
|                     (miniaudio / Darwin CoreAudio Host)                       |
|                                                                               |
|   +-------------------+    +--------------------+    +--------------------+   |
|   | MP3 Bitstream     | -> | 44.1 kHz Stereo    | -> | CoreAudio Output   |   |
|   | Decoder           |    | PCM Circular Ring  |    | Buffer (1024 smp)  |   |
|   +-------------------+    +--------------------+    +--------------------+   |
|                                      |                                        |
|                                      v                                        |
|                            Spectral Energy Engine                             |
|                        (RMS / Peak Amplitude Extractor)                       |
+-------------------------------------------------------------------------------+
```

### 1.1 Audio Processing Pipeline
1. **Decode Phase**: Incoming MP3 bitstreams are parsed and decoded into continuous arrays of signed 16-bit interleaved integers normalized to a constant sampling frequency of 44,100 Hz across two channels (Stereo).
2. **Buffering & Delivery**: An audio generation coroutine supplies contiguous 1024-frame chunks to the native macOS CoreAudio output bus via `miniaudio`.
3. **Seeking**: Temporal offsets are mapped directly to memory indices via the formula:
   $$\text{Frame Offset} = \text{Target Time (seconds)} \times 44100$$
   This eliminates decoder re-initialization and enables instantaneous repositioning.
4. **Spectral Decomposition**: Prior to volume attenuation, each buffer chunk is partitioned into discrete bins. Peak and root-mean-square (RMS) energies are extracted and conditioned via non-linear perceptual scaling before transmission to the interface layer.

---

## 2. Installation and Deployment

### 2.1 System Prerequisites
- macOS 11.0 (Big Sur) or newer
- Python 3.9 or higher
- Homebrew package manager (recommended for dependency isolation)

### 2.2 Automated Local Installation
Execute the automated deployment script to generate an isolated execution environment and link the command directly to the user path:

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

Pulse CLI accepts an optional file or directory path as an execution argument:

```bash
# Initialize player within current directory or default library path (~/Music)
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

The application operates entirely within the terminal viewport without external dependencies. Control mappings conform to standard POSIX terminal navigation patterns:

| Subsystem | Input Key | Function |
| :--- | :--- | :--- |
| **Playback** | `Space` | Toggle Play / Pause state |
| | `Left` / `Right` | Seek backward / forward 5 seconds |
| | `Shift + Left` / `Shift + Right` | Seek backward / forward 15 seconds |
| | `Up` / `Down` | Increment / decrement gain attenuation by 5% |
| | `M` | Toggle audio mute |
| | `N` | Advance to subsequent track in queue sequence |
| | `P` | Revert to preceding track in queue sequence |
| | `R` | Cycle repetition state (Disabled -> Single Track -> Full Queue) |
| | `S` | Toggle randomized playback sequence |
| **File Browser** | `Enter` | Trigger playback of highlighted file or expand directory |
| | `A` | Enqueue selected file or recursively scan directory for MP3 files |
| | `/` | Activate live substring search filter |
| | `Esc` | Clear filter query or dismiss active overlays |
| | `Tab` | Shift focus across active layout panes |
| **Queue Matrix** | `Enter` | Force jump to selected row |
| | `D` / `Delete` | Remove selected record from queue |
| | `C` | Flush entire queue structure |
| **Global** | `?` | Display internal operator reference manual |
| | `Q` | Terminate application process cleanly |

---

## 5. Technical Specifications

- **UI Framework**: `textual` (Reactive terminal application framework)
- **Audio Output Driver**: Native CoreAudio via `miniaudio` (macOS Darwin API)
- **Metadata Inspection**: `mutagen` (ID3v1.1, ID3v2.3, ID3v2.4 parser)
- **Sampling Parameters**: 44,100 Hz, 16-bit Signed Integer, 2 Channels (Stereo)
- **Spectral Update Frequency**: 25 Hz (40 ms discrete clock)
- **Target Architecture**: Apple Silicon (arm64) and Intel (x86_64)

---

## 6. Project Structure

```
MusicCLI/
|-- pulse/
|   |-- __init__.py           # Package version definition
|   |-- __main__.py          # CLI entry point and argument parsing
|   |-- app.py               # Main application orchestration loop
|   |-- audio.py             # CoreAudio driver and PCM streaming
|   |-- metadata.py          # ID3 extraction and duration calculation
|   |-- queue_manager.py     # Deterministic and randomized queue models
|   |-- styles.tcss          # Terminal CSS component definitions
|   `-- widgets/
|       |-- __init__.py
|       |-- file_browser.py  # MP3-filtered directory tree widget
|       |-- player_card.py   # Telemetry panel and progress display
|       |-- queue_view.py    # Queue table viewer and manipulator
|       `-- visualizer.py    # Discrete Unicode spectral visualizer
|-- pyproject.toml           # PEP 621 package metadata
|-- requirements.txt         # Pinned runtime dependencies
|-- install.sh               # Local deployment script
|-- LICENSE                  # MIT License
`-- README.md                # System documentation
```

---

## 7. Contributing and Development Guidelines

Contributions that optimize performance, improve signal processing fidelity, or expand audio metadata support are welcomed.

### 7.1 Development Setup
1. Fork the upstream repository to your personal GitHub account.
2. Clone your fork locally:
   ```bash
   git clone https://github.com/<your-username>/MusicCLI.git
   cd MusicCLI
   ```
3. Establish a dedicated virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -e .
   ```

### 7.2 Verification Testing
Pulse CLI includes headless integration tests using Textual's asynchronous pilot harness. Execute test verification prior to submitting code:

```bash
python3 -c "
import asyncio
from pulse.app import PulseApp

async def verify():
    app = PulseApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one('#file-browser') is not None
        assert app.query_one('#player-card') is not None
        assert app.query_one('#visualizer') is not None
        assert app.query_one('#queue-view') is not None
        print('Integration test verified successfully.')

asyncio.run(verify())
"
```

### 7.3 Pull Request Protocol
- Ensure adherence to PEP 8 style conventions and static type annotations.
- Provide descriptive, imperative commit messages (`feat: ...`, `fix: ...`, `perf: ...`).
- Verify that terminal cleanup hooks execute without leaving audio threads active upon program termination.

---

## 8. License

This project is released under the terms of the MIT License. See [LICENSE](LICENSE) for the full text.
