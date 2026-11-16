"""MP3-filtered file explorer and directory navigation tree."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, DirectoryTree, Input, Static
from textual.widgets._tree import TreeNode
from textual.widgets._directory_tree import DirEntry


class MP3DirectoryTree(DirectoryTree):
    """Custom DirectoryTree that filters exclusively for MP3 files and directories."""

    ICON_MP3 = "🎵 "
    ICON_FOLDER = "📁 "
    ICON_FOLDER_OPEN = "📂 "

    def __init__(self, path: Path | str, **kwargs):
        super().__init__(path, **kwargs)
        self.search_filter: str = ""

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        """Filter paths to exclusively include MP3 files and relevant directories."""
        filtered = []
        for path in paths:
            # Skip hidden files and common development/system folders
            name = path.name
            if name.startswith(".") or name in ("node_modules", "__pycache__", "venv", ".venv"):
                continue

            try:
                if path.is_dir():
                    filtered.append(path)
                elif path.is_file() and path.suffix.lower() == ".mp3":
                    if not self.search_filter or self.search_filter.lower() in name.lower():
                        filtered.append(path)
            except (PermissionError, OSError):
                continue

        # Sort: directories first, then alphabetical MP3s
        return sorted(filtered, key=lambda p: (not p.is_dir(), p.name.lower()))

    def render_label(self, node: TreeNode[DirEntry], base_style: Style, style: Style) -> Text:
        """Render custom icons and styles for MP3s and directories."""
        node_label = node._label.copy()
        node_label.stylize(style)

        if not self.is_mounted:
            return node_label

        entry = node.data
        if entry is None or not hasattr(entry, "path"):
            return node_label

        path = entry.path
        if node._allow_expand:
            icon = self.ICON_FOLDER_OPEN if node.is_expanded else self.ICON_FOLDER
            icon_style = "bold #ecc94b"
            node_label.stylize("bold #e2e8f0")
        else:
            icon = self.ICON_MP3
            icon_style = "bold #00f0ff"
            node_label.stylize("#81e6d9")

        return Text.assemble((icon, icon_style), node_label)

    def set_filter(self, query: str) -> None:
        """Set substring filter and reload tree."""
        self.search_filter = query.strip()
        self.reload()


class FileBrowserWidget(Vertical):
    """File explorer container with search filter, quick bookmarks, and MP3 tree."""

    DEFAULT_CSS = """
    FileBrowserWidget {
        width: 100%;
        height: 100%;
        border: round $primary;
        padding: 0 1;
        background: $surface;
    }

    #browser-header {
        height: auto;
        dock: top;
        margin-bottom: 1;
    }

    #nav-bar {
        height: 3;
        align: left middle;
        margin-bottom: 1;
    }

    #nav-bar Button {
        min-width: 6;
        height: 1;
        margin-right: 1;
        padding: 0 1;
        border: none;
        background: $primary-darken-2;
        color: $text;
    }

    #nav-bar Button:hover {
        background: $primary;
        color: white;
    }

    #search-box {
        height: 1;
        border: none;
        background: $surface-darken-2;
        color: #00f0ff;
        padding: 0 1;
        margin-bottom: 1;
    }

    #tree-container {
        height: 1fr;
    }

    #browser-footer {
        height: 1;
        dock: bottom;
        color: $text-muted;
        background: $surface-darken-1;
        padding: 0 1;
    }
    """

    class TrackPlayRequested(Message):
        """User requested immediate playback of file."""
        def __init__(self, path: Path):
            super().__init__()
            self.path = path

    class TrackQueueRequested(Message):
        """User requested adding file or folder to queue."""
        def __init__(self, path: Path):
            super().__init__()
            self.path = path

    def __init__(self, initial_path: Optional[Path | str] = None, **kwargs):
        super().__init__(**kwargs)
        if initial_path:
            p = Path(initial_path).resolve()
            if p.is_file():
                self.current_path = p.parent
            else:
                self.current_path = p
        else:
            music_dir = Path.home() / "Music"
            self.current_path = music_dir if music_dir.exists() else Path.cwd()

    def compose(self) -> ComposeResult:
        with Vertical(id="browser-header"):
            yield Static("📁 [bold #00f0ff]LIBRARY & FILE EXPLORER[/bold #00f0ff]")
            with Horizontal(id="nav-bar"):
                yield Button("⬆ Up", id="btn-up", tooltip="Go up to parent directory")
                yield Button("🎵 Music", id="btn-music", tooltip="Open ~/Music")
                yield Button("⬇ Dwnld", id="btn-dl", tooltip="Open ~/Downloads")
                yield Button("🏠 Home", id="btn-home", tooltip="Open home directory")
                yield Button("📁 Here", id="btn-cwd", tooltip="Open current directory")

            yield Input(
                placeholder="🔍 Type to filter MP3s... (Press / to search)",
                id="search-box",
            )

        with Vertical(id="tree-container"):
            yield MP3DirectoryTree(self.current_path, id="mp3-tree")

        yield Static("", id="browser-footer")

    def on_mount(self) -> None:
        self.update_footer()

    def update_footer(self) -> None:
        footer = self.query_one("#browser-footer", Static)
        footer.update(f"📂 {self.current_path}  |  [Enter] Play  [A] Queue")

    def navigate_to(self, new_path: Path) -> None:
        """Change directory tree root path."""
        p = Path(new_path).resolve()
        if p.exists() and p.is_dir():
            self.current_path = p
            tree = self.query_one("#mp3-tree", MP3DirectoryTree)
            tree.path = p
            self.update_footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle quick navigation buttons."""
        button_id = event.button.id
        if button_id == "btn-up":
            if self.current_path.parent != self.current_path:
                self.navigate_to(self.current_path.parent)
        elif button_id == "btn-music":
            music_p = Path.home() / "Music"
            if music_p.exists():
                self.navigate_to(music_p)
        elif button_id == "btn-dl":
            dl_p = Path.home() / "Downloads"
            if dl_p.exists():
                self.navigate_to(dl_p)
        elif button_id == "btn-home":
            self.navigate_to(Path.home())
        elif button_id == "btn-cwd":
            self.navigate_to(Path.cwd())

    def on_input_changed(self, event: Input.Changed) -> None:
        """Live filter directory tree on search input."""
        tree = self.query_one("#mp3-tree", MP3DirectoryTree)
        tree.set_filter(event.value)

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        """Handle enter or click on an MP3 file."""
        if event.path.is_file() and event.path.suffix.lower() == ".mp3":
            self.post_message(self.TrackPlayRequested(event.path))

    def queue_current_selection(self) -> None:
        """Queue currently highlighted file or directory."""
        tree = self.query_one("#mp3-tree", MP3DirectoryTree)
        if tree.cursor_node and tree.cursor_node.data:
            path = tree.cursor_node.data.path
            self.post_message(self.TrackQueueRequested(path))

    def focus_search(self) -> None:
        """Focus the search filter input."""
        inp = self.query_one("#search-box", Input)
        inp.focus()
