# Contributing to Pulse CLI: Systems Engineering & Development Guidelines

This document outlines the low-level architectural invariants, concurrency requirements, memory budgets, and verification protocols that govern contributions to the Pulse CLI codebase.

---

## 1. Core Architectural Invariants

Every pull request or modification must preserve the following architectural guarantees:

### 1.1 Real-Time Audio Callback Constraints (Thread Safety & Latency)
The CoreAudio callback generator in `pulse/audio.py` runs within a high-priority thread scheduled by the Darwin kernel HAL (`coreaudiod`).

* **Zero Unbounded Allocations**: Do not instantiate large heap objects (e.g. re-allocating full audio arrays or deep copies) inside the active audio streaming loop.
* **Bounded Mutex Hold Time ($\Delta t_{\text{lock}} \le 1.5\ \mu\text{s}$)**:
  `self._lock` may only be held to calculate slice indices and advance frame counters. 
  **Violation Rule**: Never invoke `yield` or make blocking I/O calls while holding `self._lock`.
* **Zero Network / Disk I/O on the Audio Thread**: All disk reading and network lookups must execute on worker threads or prior to playback dispatch.

### 1.2 Memory Footprint Invariants
* **In-Memory PCM Buffer Layout**: The decoded audio stream is stored as a contiguous C-level array of signed 16-bit integers (`array.array('h')`).
  $$\text{Memory Size} = N_{\text{frames}} \times 2 \times 2\text{ bytes} = 4 \times N_{\text{frames}}\text{ bytes}$$
  For a typical 4-minute track at $44.1\text{ kHz}$:
  $$M = 240 \times 44100 \times 4 \approx 42.33\text{ MB}$$
* **Deallocation on Unload**: When `AudioEngine.unload()` is invoked, the reference to `_samples` must be explicitly cleared to enable immediate garbage collection by CPython's cyclic collector.

### 1.3 1-Bit Monochromatic Display Invariants
* **No ANSI Color Escapes**: All terminal output must strictly adhere to 1-bit monochrome high-contrast styling using standard curses attributes (`A_BOLD`, `A_DIM`, `A_NORMAL`, `A_REVERSE`).
* **Aspect-Ratio-Preserving Half-Block Packing**: Terminal glyphs represent two vertical pixels via `U+2580` (`▀`), `U+2584` (`▄`), and `U+2588` (`█`). Resampling must always project image dimensions onto $(W, 2H)$.

---

## 2. Concurrency & State Machine Model

Pulse CLI enforces a deterministic 3-state transport finite-state machine (FSM):

$$\Sigma = \{ \text{STOPPED}, \text{PLAYING}, \text{PAUSED} \}$$

```
                +------------+
                |  STOPPED   | <-------------------------+
                +------------+                           |
                   |      ^                              |
        load_track |      | stop() / End-of-Track        |
                   v      |                              |
                +------------+      pause()        +------------+
                |  PLAYING   | ------------------> |   PAUSED   |
                +------------+ <------------------ +------------+
                                    play()
```

### State Transition Invariants
1. `AudioState.STOPPED` $\rightarrow$ `AudioState.PLAYING`: Triggered by `load_track()`, `play()`, or `toggle_play_pause()` when `_samples` is resident in memory.
2. `AudioState.PLAYING` $\rightarrow$ `AudioState.PAUSED`: Triggered by `pause()` or `toggle_play_pause()`. `_current_frame` remains frozen at the exact temporal offset.
3. `AudioState.PLAYING` $\rightarrow$ `AudioState.STOPPED`: Triggered by `stop()`, user seek past `_total_frames`, or EOF frame starvation. `self.track_finished_flag` is set atomically to signal the UI thread for playlist advancement.

---

## 3. Subsystem Contribution Rules

### 3.1 `pulse/audio.py` (Audio Driver)
* Modifications to `_update_levels()` must remain mathematically deterministic. All calculations on PCM chunks must operate across $B = 64$ sub-bands.
* Peak detection must avoid generator expressions inside the sub-band loop. Utilize direct iteration or optimized C-primitives to prevent Python heap allocations.
* CoreAudio device initialization parameters (`nchannels=2`, `sample_rate=44100`) must remain constant across all decoded codecs. Miniaudio handles input sample-rate conversion (SRC) transparently during decode.

### 3.2 `pulse/metadata.py` (AST & Tag Processing)
* **Tag Priority**: Track artist (`artist`) must take strict precedence over album artist (`albumartist`). Never allow `albumartist` to overwrite a valid `artist` string.
* **Corrupt Container Resistance**: All tag extractions must use the `_safe_tag_str()` helper. Do not assume tag frames return lists or strings.
* **Non-Blocking Online Fallback**: Never perform synchronous `urllib` network calls in `from_path()`. Use `_COVER_EXECUTOR.submit()` to dispatch lookups to the background thread pool.
* **Cache Integrity**: When mutating cover art, always clear `_pixel_art_cache` to invalidate stale terminal halftone representations.

### 3.3 `pulse/queue_manager.py` (Permutation Queue)
* **Permutational Shuffling**: The shuffle subsystem must never rely on stateless random choice (`random.choice`). A shuffle cycle must always be a random permutation $\vec{\pi} \in S_N$ executed via Fisher-Yates, with a cursor $k \in [0, N-1]$ tracking playback state.
* **Bidirectional Traversal**: `prev_track()` in shuffle mode must navigate backward along $\vec{\pi}$ ($k \leftarrow k - 1$).
* **Dynamic Splicing**: Adding tracks during active shuffle must splice the new track indices into random positions *after* the current shuffle cursor ($j > k$), preserving unplayed guarantees.

---

## 4. Coding Standards & Tooling

### 4.1 Type Annotations & Static Analysis
* Strict compliance with **PEP 484** type hints.
* All module boundaries must define explicit signatures (`from __future__ import annotations`).
* Avoid using `Any`; prefer explicit `Union`, `Optional`, and protocol interfaces.

### 4.2 Error Handling Protocols
* Terminal crashes must never expose uncaught Python tracebacks to the shell.
* Curses cleanup must be guaranteed on exit via a `try ... finally` block restoring `curses.nocbreak()`, `curses.echo()`, and `curses.endwin()`.

---

## 5. Development Workflow & Testing

### 5.1 Setting Up the Development Environment
```bash
git clone https://github.com/pookie421/MusicCLI.git
cd MusicCLI
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 5.2 Test & Preflight Requirements
To make contributions frictionless and eliminate subjective review delays, all submissions must pass the automated preflight verification suite:

```bash
./scripts/preflight.sh
```

This script automatically verifies:
1. Complete CPython bytecode compilation across all source trees.
2. Full execution of the unit test suite (`tests/test_backend.py`).
3. Mathematical and concurrency verification via the **Unified Contributor Invariant Verification Harness (UCIVH)**:
   * Real-time audio callback static AST inspection.
   * Signed 16-bit PCM quantization bounds and zero-volume bypass.
   * Fisher-Yates permutational bijection completeness.
   * Unicode halftone charset orthogonality.
   * Bounded-memory LRU metadata cache guarantees.

Upon successful completion, the harness emits a cryptographic verification receipt:
```
ALL ARCHITECTURAL INVARIANTS SATISFIED in 92.81 ms.
Cryptographic Verification Receipt: [UCIVH-XXXXXXXXXXXXXXXX]
```

Contributors must include this receipt in their pull request description. Detailed specifications for each invariant are documented in [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md).

---

## 6. Conventional Commit Format

Commit messages must follow the Conventional Commits specification:

```
<type>(<scope>): <short summary>

[optional body providing low-level rationale and mathematical/architectural context]

[optional footer referencing issues]
```

### Recognized Types:
* `feat`: New functional capability (e.g. adding 24-bit audio depth handling).
* `fix`: Bug fix (e.g. resolving lock contention or seek boundary condition).
* `perf`: Execution speedup or memory optimization.
* `refactor`: Structural changes that do not alter public runtime behavior.
* `test`: Adding or expanding test suites.
* `docs`: Documentation updates.
