"""CLI entry point for Pulse."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pulse import __app_name__, __version__
from pulse.app import PulseApp


def main() -> None:
    """Parse CLI arguments and run the application."""
    parser = argparse.ArgumentParser(
        prog="pulse",
        description="Pulse CLI - A pure 1-bit monochrome terminal MP3 & FLAC music player for macOS with live audio spectrum and cover art.",
        epilog="Examples:\n"
               "  pulse                     # Launch in default directory (~/Music or cwd)\n"
               "  pulse ~/Music/Favorites   # Open directly to a folder\n"
               "  pulse song.flac           # Play FLAC with embedded cover art immediately\n"
               "  pulse song.mp3            # Play MP3 immediately\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Path to an MP3 or FLAC file to play or directory to browse",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"{__app_name__} v{__version__}",
    )

    args = parser.parse_args()

    initial_path = None
    auto_play_file = None

    if args.path:
        target = Path(args.path).resolve()
        if not target.exists():
            sys.stderr.write(f"Error: Path '{args.path}' does not exist.\n")
            sys.exit(1)

        if target.is_file():
            if target.suffix.lower() not in (".mp3", ".flac"):
                sys.stderr.write(f"Error: Pulse CLI supports MP3 and FLAC audio files. Given: {target.name}\n")
                sys.exit(1)
            auto_play_file = target
            initial_path = target.parent
        elif target.is_dir():
            initial_path = target

    app = PulseApp(initial_path=initial_path, auto_play_file=auto_play_file)
    app.run()


if __name__ == "__main__":
    main()
