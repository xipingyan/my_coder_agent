"""GUI entry point: ``coder-agent [--root PROJECT]``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Coder Agent GUI")
    ap.add_argument("--root", help="project root (default: last used / ask)")
    args, qt_args = ap.parse_known_args(argv)

    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    app = QApplication([sys.argv[0], *qt_args])
    win = MainWindow(Path(args.root) if args.root else None)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
