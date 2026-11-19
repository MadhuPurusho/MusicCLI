# Metadata & Halftone Synthesizer Specification

This document details the binary container formats, Abstract Syntax Tree (AST) tag extractions, image processing algorithms, and Unicode halftone synthesis implemented in `pulse.metadata`.

---

## 1. Binary Container Structures

### 1.1 FLAC `METADATA_BLOCK_PICTURE` (Xiph.Org Spec)
FLAC stores embedded artwork in a designated metadata block (Block Type 6: `PICTURE`).

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                          Picture Type                         |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                      MIME Type Length                         |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                      MIME Type String...                      |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                     Description Length                        |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                     Description String...                     |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                         Pixel Width                           |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                         Pixel Height                          |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                         Color Depth                           |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                     Indexed Color Count                       |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                      Picture Data Length                      |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                       Raw Image Bytes...                      |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

* **Target Picture Type**: Type 3 (`Cover (front)`).
* **Extraction Strategy**: Iterates over `audio.pictures`, prioritizing `type == 3`. If missing, falls back to `audio.pictures[0]` or parses base64-encoded `METADATA_BLOCK_PICTURE` / `COVERART` tags in Vorbis comments.

### 1.2 ID3v2.3 / ID3v2.4 `APIC` (Attached Picture)
In MP3 bitstreams, ID3 tags encode artwork within the `APIC` frame.

```
+-------------------------------------------------------------------------------+
| Frame Header: ID = "APIC", Size = 32-bit syncsafe integer, Flags = 16-bit    |
+-------------------------------------------------------------------------------+
| Text Encoding | MIME Type (null-term) | Picture Type (1 byte) | Description... |
+-------------------------------------------------------------------------------+
| Binary Image Payload (JFIF / JPEG or PNG bitstream)                           |
+-------------------------------------------------------------------------------+
```

* **Prioritization**: Scans `APIC` frames; prioritizes `picture_type == 3` (Front Cover).

---

## 2. Image Preprocessing & Spatial Transformation

```
+-------------------------------------------------------------------------------+
|                      Monochrome Raster Processing Flow                        |
|                                                                               |
|  [Raw Image Bytes]                                                            |
|          |                                                                    |
|          v                                                                    |
|  [PIL.Image.open] -> Convert to 8-bit Greyscale ('L')                         |
|          |           $L = 0.299R + 0.587G + 0.114B$                           |
|          v                                                                    |
|  [ImageOps.autocontrast] -> Dynamic Range Expansion                           |
|          |                  $L'(x,y) = \text{clamp}(0, 255, (L - L_{\min}) /  |
|          |                             (L_{\max} - L_{\min}) \times 255)$     |
|          v                                                                    |
|  [Lanczos Resampling] -> Resize to Target Grid $(W, 2H)$                      |
|          |                                                                    |
|          v                                                                    |
|  [Floyd-Steinberg Dither] -> 1-Bit Error Diffusion Matrix                     |
|          |                                                                    |
|          v                                                                    |
|  [Unicode Half-Block Packing] -> Rendered Grid Strings                        |
+-------------------------------------------------------------------------------+
```

### 2.1 Lanczos-3 Spatial Resampling
Spatial resizing uses a 3-lobed Lanczos windowed sinc filter:

$$L(x) = \begin{cases}
\text{sinc}(x) \cdot \text{sinc}\left(\frac{x}{3}\right), & \text{if } |x| < 3 \\
0, & \text{if } |x| \ge 3
\end{cases}$$

where $\text{sinc}(x) = \frac{\sin(\pi x)}{\pi x}$.

---

## 3. Floyd-Steinberg Error Diffusion

### 3.1 Kernel Propagation
For each pixel in scanline order $(x, y)$, the 8-bit value $p = L'(x, y)$ is quantized to binary:

$$q = \begin{cases} 255, & \text{if } p \ge 128 \\ 0, & \text{if } p < 128 \end{cases}$$

The quantization residual error is computed:

$$e = p - q$$

The error $e$ is diffused into neighboring pixels according to the Floyd-Steinberg weights:

$$p(x+1, y) \leftarrow p(x+1, y) + \frac{7}{16} e$$
$$p(x-1, y+1) \leftarrow p(x-1, y+1) + \frac{3}{16} e$$
$$p(x, y+1) \leftarrow p(x, y+1) + \frac{5}{16} e$$
$$p(x+1, y+1) \leftarrow p(x+1, y+1) + \frac{1}{16} e$$

---

## 4. Multi-Algorithm 1-Bit Halftone Engine

`pulse.metadata` provides multiple interchangeable 1-bit dithering algorithms switchable in real-time via the `[T]` keybinding.

### 4.1 Bill Atkinson (1984 Macintosh) Dithering
Developed by Bill Atkinson for the original Apple Macintosh Lisa/Mac OS graphical subsystem. Unlike standard error-diffusion algorithms that propagate $100\%$ of error, Atkinson diffuses only $\frac{6}{8}$ ($75\%$) of quantization error across 6 neighboring pixels, discarding the remaining $25\%$:

$$\begin{aligned}
p(x+1, y)   &\leftarrow p(x+1, y) + \frac{1}{8} e \\
p(x+2, y)   &\leftarrow p(x+2, y) + \frac{1}{8} e \\
p(x-1, y+1) &\leftarrow p(x-1, y+1) + \frac{1}{8} e \\
p(x, y+1)   &\leftarrow p(x, y+1) + \frac{1}{8} e \\
p(x+1, y+1) &\leftarrow p(x+1, y+1) + \frac{1}{8} e \\
p(x, y+2)   &\leftarrow p(x, y+2) + \frac{1}{8} e
\end{aligned}$$

**Error Budget Conservation**:
$$\sum_{k=1}^6 w_k = 6 \times \frac{1}{8} = 0.75, \quad \text{Residual Discard} = 0.25$$

**Acoustic/Visual Properties**: Discarding $25\%$ of error prevents diffuse "shadow mud" streaks from propagating into neighboring white areas, producing crisp, high-contrast monochrome halftone artwork ideal for 1-bit terminal character grids.

### 4.2 Ordered Bayer 4x4 Spatial Threshold Matrix
Ordered dithering compares input luminance against an orthogonal spatial threshold matrix:

$$M_{4\times4} = \begin{bmatrix}
0 & 8 & 2 & 10 \\
12 & 4 & 14 & 6 \\
3 & 11 & 1 & 9 \\
15 & 7 & 13 & 5
\end{bmatrix}$$

The pixel threshold $T(x, y)$ is computed deterministically:

$$T(x, y) = \left(M_{4\times4}[y \bmod 4, x \bmod 4] + 0.5\right) \times \frac{255.0}{16.0}$$

$$B(x, y) = \begin{cases} 1, & \text{if } L'(x, y) > T(x, y) \\ 0, & \text{otherwise} \end{cases}$$

---

## 5. Vertical 2:1 Pixel Packing Matrix

Terminal character cells typically exhibit a 1:2 physical aspect ratio. To render square pixel art, pairs of vertically adjacent binary pixels $(x, 2y)$ and $(x, 2y + 1)$ are packed into Unicode block elements:

$$\text{Bit State } S(x, y) = \left( B(x, 2y) \ll 1 \right) \mid B(x, 2y + 1)$$

| State $S$ | Upper Bit | Lower Bit | Unicode Codepoint | Character |
| :---: | :---: | :---: | :---: | :---: |
| `00` | $0$ | $0$ | `U+0020` | ` ` *(SPACE)* |
| `10` | $1$ | $0$ | `U+2580` | `▀` *(UPPER HALF BLOCK)* |
| `01` | $0$ | $1$ | `U+2584` | `▄` *(LOWER HALF BLOCK)* |
| `11` | $1$ | $1$ | `U+2588` | `█` *(FULL BLOCK)* |

---

## 6. Metadata Cache Architecture

To achieve sub-millisecond lookup times, metadata is cached hierarchically:

```
Level 1: _METADATA_CACHE (In-Memory Map)
Key: (Canonical Path, File Size in Bytes, Modification Timestamp mtime)
Capacity: MAX_METADATA_CACHE_ENTRIES = 2048
Latency: < 0.03 ms
Validation: Direct stat() check; auto-invalidates if mtime changes.

Level 2: _pixel_art_cache (Instance Property)
Key: (Grid Width W, Grid Height H, DitherAlgorithm)
Latency: < 0.01 ms
Stores: Pre-computed Unicode UTF-8 strings.

Level 3: Disk Cache (~/.pulse-cli/cover_cache/)
Key: MD5(artist + title).jpg
Latency: ~0.1 ms (Local filesystem SSD read).
```

---

## 7. Synchronized Karaoke Lyrics Engine (LRC Grammar & AST Specification)

`pulse.metadata` provides high-precision parsing of standard `.lrc` lyrics files as well as embedded ID3v2 `SYLT`/`USLT` and Vorbis `LYRICS` tags.

### 7.1 LRC Temporal Grammar
LRC lines conform to the following Extended Backus-Naur Form (EBNF) grammar:

$$\begin{aligned}
\langle \text{LRC-Line} \rangle &::= \langle \text{Timestamp} \rangle^+ \langle \text{Text} \rangle \\
\langle \text{Timestamp} \rangle &::= \text{"["} \langle \text{Min} \rangle \text{":"} \langle \text{Sec} \rangle (\text{"."} \langle \text{Centisec} \rangle)? \text{"]"} \\
\langle \text{Min} \rangle &::= [0-9]{1,2} \\
\langle \text{Sec} \rangle &::= [0-9]{2} \\
\langle \text{Centisec} \rangle &::= [0-9]{1,3}
\end{aligned}$$

The absolute temporal offset $t \in \mathbb{R}^+$ in seconds is parsed via:

$$t = \text{Min} \times 60 + \text{Sec} + \frac{\text{Centisec}}{10^{\text{len}(\text{Centisec})}}$$

### 7.2 Binary Temporal Lookup & Window Scrolling
For active playback timestamp $t_{\text{current}}$, the active line index $i^*$ is determined via monotonic binary search:

$$i^* = \max \left\{ i \in [0, K-1] \mid t_i \le t_{\text{current}} \right\}$$

The curses rendering window extracts $M = \text{height}$ lines centered symmetrically around $i^*$:

$$\text{Window} = \left[ \max\left(0, i^* - \lfloor M/2 \rfloor\right), \min\left(K, i^* + \lceil M/2 \rceil\right) \right)$$

Active lines are emphasized with solid reverse-video inverted background (`curses.A_BOLD | curses.color_pair(2)`), and upcoming verses render with standard 1-bit text styling.
