"""PyInstaller entry point (the package uses relative imports, so it cannot be the script itself)."""

from coder_agent.gui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
