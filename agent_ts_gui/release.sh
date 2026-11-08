#!/usr/bin/env bash
# Build the Linux AppImage for the Coder Agent GUI.
#
# Usage:
#   ./release.sh   # -> gui/release/Coder Agent-<ver>-x86_64.AppImage
#
# Windows: use release.bat (on Windows; PyInstaller cannot cross-compile).
#
# Requirements:
#   - Node 20+ (npm) and Python 3.11+ with a venv in backend/.venv
#   - PyInstaller in the backend venv:  backend/.venv/bin/pip install pyinstaller
#   - AppImage tooling is downloaded by electron-builder automatically
set -euo pipefail
cd "$(dirname "$0")"

echo "==> building backend standalone executable"
backend/.venv/bin/python backend/build_standalone.py gui/build-resources/backend
ls -l gui/build-resources/backend

echo "==> building GUI (electron-builder)"
cd gui
npm run dist:linux

echo "==> done, artifacts in gui/release/"
ls -l release
