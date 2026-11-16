"""Real-time audio spectrum and waveform visualizer widget."""

from __future__ import annotations

import math
import time
from typing import List

from rich.text import Text
from textual.widgets import Static


class VisualizerWidget(Static):
    """Dynamic audio equalizer and visualizer widget."""

    BLOCKS = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

    DEFAULT_CSS = """
    VisualizerWidget {
        height: 6;
        width: 100%;
        content-align: center middle;
        padding: 0 1;
        background: $surface-darken-1;
        border: round $primary-darken-2;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.height_lines: int = 4
        self.idle_t: float = 0.0

    def render_bars(self, levels: List[float], is_playing: bool) -> Text:
        """Render multi-band spectrum bars using Unicode blocks."""
        if not is_playing or all(lvl < 0.01 for lvl in levels):
            # Render a subtle idle pulsating waveform
            self.idle_t += 0.12
            ambient_levels = []
            num = len(levels) if levels else 20
            for i in range(num):
                w = math.sin(self.idle_t + (i * 0.35)) * 0.18 + 0.22
                ambient_levels.append(max(0.05, min(0.6, w)))
            levels = ambient_levels

        h = self.height_lines
        num_bars = len(levels)
        result = Text()

        for row in range(h - 1, -1, -1):
            row_text = Text()
            for b in range(num_bars):
                val = levels[b]
                bar_h = val * h
                row_fill = bar_h - row

                if row_fill >= 1.0:
                    char = self.BLOCKS[8]
                elif row_fill > 0.0:
                    idx = min(8, max(0, int(row_fill * 8)))
                    char = self.BLOCKS[idx]
                else:
                    char = " "

                # Color gradient mapping: cyan -> violet -> magenta
                if row >= h - 1:
                    style = "bold #ff007f"  # Neon pink/magenta
                elif row >= h - 2:
                    style = "bold #bf00ff"  # Electric purple
                elif row >= 1:
                    style = "#00f0ff"       # Cyan
                else:
                    style = "#00b4d8"       # Deep cyan

                row_text.append(char * 2 + " ", style=style)

            result.append(row_text)
            if row > 0:
                result.append("\n")

        return result

    def update_visualizer(self, levels: List[float], is_playing: bool) -> None:
        """Update widget with latest audio frame spectrum."""
        rendered = self.render_bars(levels, is_playing)
        self.update(rendered)
