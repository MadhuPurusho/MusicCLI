# ⚡ Pulse CLI

> A sleek, keyboard-driven terminal MP3 player designed specifically for macOS, featuring real-time audio spectrum visualization and a modern Textual TUI.

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│  ⚡ PULSE CLI  ::  Terminal MP3 Player             [~/Music]                    18:48  [?] Help  │
├───────────────────────────────┬──────────────────────────────────────────────────────────────────┤
│  📁 FILE EXPLORER             │  🎵 NOW PLAYING                                                  │
│  [ Filter: synthwave_________ ]│  ┌────────────────────────────────────────────────────────────┐  │
│                               │  │  COSMIC VOYAGE                                             │  │
│  ▾ 📂 Music                   │  │  Artist: Stellaris   •   Album: Horizons (2026)             │  │
│    ▾ 📂 Chillwave             │  │  Format: MP3 320 kbps (Joint Stereo, 44.1 kHz)             │  │
│      ▸ Midnight_Drift.mp3     │  └────────────────────────────────────────────────────────────┘  │
│      ▸ Cosmic_Voyage.mp3      │                                                                  │
│      ▸ Solar_Echoes.mp3       │  LIVE AUDIO SPECTRUM                                             │
│    ▸ 📂 Electronic            │   ▄█ █▄ █  ▄▆ █▅ █  ▄█ █▄ █  ▄▆ █▅ █  ▄█ █▄ █  ▄▆ █▅ █           │
│    ▸ 📂 Lo-Fi                 │  █████████████████████████████████████████████████████████████   │
│                               │                                                                  │
│  [Enter] Play  [A] Add Queue  │  02:14 ━━━━━━━━━━━━━━━━━━━━━━●───────────────────── 04:55        │
├───────────────────────────────┼──────────────────────────────────────────────────────────────────┤
│  📑 QUEUE & PLAYLIST (3)      │  CONTROLS & STATUS                                               │
│  1. Cosmic Voyage     [04:55] │  Status:  [ ▶ PLAYING ]       Volume: [ 🔊 85%  ████████░░ ]     │
│  2. Midnight Drift    [03:40] │  Mode:    [ 🔁 Repeat: Track ] [ 🔀 Shuffle: Off ]               │
│  3. Solar Echoes      [05:12] │  Shortcuts: [Space] Pause  [←/→] Seek  [↑/↓] Vol  [N] Next       │
└───────────────────────────────┴──────────────────────────────────────────────────────────────────┘
```

---

## ✨ Features

- **Exclusive MP3 Focus**: Filters out all non-MP3 files, giving you a clutter-free music browsing experience.
- **Mac-Native CoreAudio Engine**: Direct hardware integration via `miniaudio` with instant sub-millisecond seeking and zero audio stutter.
- **Dynamic Spectrum Visualizer**: Animated multi-band audio equalizer rendered in neon gradients, synchronized directly with the output audio frames.
- **ID3 Tag & Metadata Extraction**: Displays track title, artist, album, release year, bitrate (e.g. 320 kbps), sample rate, audio channels, and file size.
- **Interactive File Explorer**: Navigate directories, jump to `~/Music` or `~/Downloads`, and use the live search filter (`/`) to find tracks instantly.
- **Playlist & Queue System**: Enqueue single tracks or entire folders, reorder, delete, and enable loop (single/queue) or shuffle modes.
- **Vim & Arrow Key Friendly**: Complete keyboard control with mouse click & drag support.

---

## 🚀 Installation

### Option 1: Quick Install Script (macOS)
```bash
./install.sh
```
This sets up an isolated environment and installs the executable `pulse` to `~/.local/bin/pulse`.

### Option 2: Using pip / virtualenv
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

---

## 🎮 Usage

Launch in your terminal:
```bash
# Launch player in ~/Music (or current folder)
pulse

# Open directly into a specific folder
pulse ~/Music/Electronic

# Play an MP3 file immediately
pulse /path/to/song.mp3

# Check version or help
pulse --help
pulse --version
```

---

## ⌨️ Keyboard Shortcuts

| Category | Shortcut | Description |
| :--- | :--- | :--- |
| **Playback** | `Space` | Play / Pause |
| | `←` / `→` | Seek Backward / Forward 5 seconds |
| | `Shift + ←` / `Shift + →` | Seek Backward / Forward 15 seconds |
| | `↑` / `↓` | Volume Up / Down 5% |
| | `M` | Mute / Unmute audio |
| | `N` | Skip to Next track in queue |
| | `P` | Jump to Previous track |
| | `R` | Cycle Repeat Mode (*Off* → *Track* → *Queue*) |
| | `S` | Toggle Shuffle Mode (*On* / *Off*) |
| **Library** | `Enter` | Play selected MP3 or open folder |
| | `A` | Add selected MP3 or entire folder to queue |
| | `/` | Focus search filter bar |
| | `Esc` | Clear search / close modal |
| | `Tab` | Switch focus between panels |
| **Queue** | `Enter` | Jump directly to track in queue |
| | `D` / `Delete` | Remove track from queue |
| | `C` | Clear entire queue |
| **General** | `?` | Toggle Help Overlay |
| | `Q` | Quit Pulse CLI |

---

## 🛠️ Architecture

- **UI Framework**: [Textual](https://github.com/Textualize/textual) & [Rich](https://github.com/Textualize/rich)
- **Audio Engine**: [Miniaudio](https://github.com/irmen/miniaudio) with native macOS CoreAudio backend
- **Metadata Parser**: [Mutagen](https://github.com/quodlibet/mutagen) for ID3v1 and ID3v2 tags

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
