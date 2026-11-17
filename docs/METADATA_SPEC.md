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

## 4. Vertical 2:1 Pixel Packing Matrix

Terminal character cells typically exhibit a 1:2 physical aspect ratio. To render square pixel art, pairs of vertically adjacent binary pixels $(x, 2y)$ and $(x, 2y + 1)$ are packed into Unicode block elements:

$$\text{Bit State } S(x, y) = \left( B(x, 2y) \ll 1 \right) \mid B(x, 2y + 1)$$

| State $S$ | Upper Bit | Lower Bit | Unicode Codepoint | Character |
| :---: | :---: | :---: | :---: | :---: |
| `00` | $0$ | $0$ | `U+0020` | ` ` *(SPACE)* |
| `10` | $1$ | $0$ | `U+2580` | `▀` *(UPPER HALF BLOCK)* |
| `01` | $0$ | $1$ | `U+2584` | `▄` *(LOWER HALF BLOCK)* |
| `11` | $1$ | $1$ | `U+2588` | `█` *(FULL BLOCK)* |

---

## 5. Metadata Cache Architecture

To achieve sub-millisecond lookup times, metadata is cached hierarchically:

```
Level 1: _METADATA_CACHE (In-Memory Map)
Key: (Canonical Path, File Size in Bytes, Modification Timestamp mtime)
Latency: < 0.03 ms
Validation: Direct stat() check; auto-invalidates if mtime changes.

Level 2: _pixel_art_cache (Instance Property)
Key: (Grid Width W, Grid Height H)
Latency: < 0.01 ms
Stores: Pre-computed Unicode UTF-8 strings.

Level 3: Disk Cache (~/.pulse-cli/cover_cache/)
Key: MD5(artist + title).jpg
Latency: ~0.1 ms (Local filesystem SSD read).
```
