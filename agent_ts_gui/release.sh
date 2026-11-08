#!/usr/bin/env bash
# Build distributable packages for the Coder Agent GUI.
#
# Usage:
#   ./release.sh linux     # -> gui/release/Coder Agent-<ver>-x86_64.AppImage
#   ./release.sh windows   # -> gui/release/Coder Agent Setup <ver>.exe
#
# Requirements:
#   - Node 20+ (npm) and Python 3.11+ with a venv in backend/.venv
#   - PyInstaller in the backend venv:  backend/.venv/bin/pip install pyinstaller
#   - Linux:  AppImage tooling is downloaded by electron-builder automatically
#   - Windows: build on Windows (or in an environment where electron-builder
#     can produce NSIS installers); the PyInstaller step must run on Windows
#     too, because PyInstaller cannot cross-compile.
set -euo pipefail
cd "$(dirname "$0")"

target="${1:?usage: ./release.sh [linux|windows]}"

echo "==> building backend standalone executable"
if [ "$target" = linux ]; then
  backend/.venv/bin/python backend/build_standalone.py gui/build-resources/backend
else
  # on Windows run:  backend\.venv\Scripts\python backend\build_standalone.py gui\build-resources\backend
  python backend/build_standalone.py gui/build-resources/backend
fi
ls -l gui/build-resources/backend

echo "==> building GUI (electron-builder)"
cd gui
case "$target" in
  linux)   npm run dist:linux ;;
  windows) npm run dist:win ;;
esac

echo "==> done, artifacts in gui/release/"
ls -l release
