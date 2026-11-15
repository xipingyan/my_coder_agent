#!/usr/bin/env bash
# Build the Linux AppImage for the Coder Agent GUI.
#
# Usage:
#   ./release.sh   # -> release/coder-agent-<ver>.AppImage
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
# PyInstaller is a build-time tool, not a runtime dependency, so it may be
# missing from a freshly created venv.
if ! backend/.venv/bin/python -m PyInstaller --version >/dev/null 2>&1; then
    echo "PyInstaller not found in backend/.venv, installing..."
    backend/.venv/bin/python -m pip install pyinstaller
fi
backend/.venv/bin/python backend/build_standalone.py gui/build-resources/backend
ls -l gui/build-resources/backend

echo "==> building GUI (electron-builder)"
cd gui
npm run dist:linux

echo "==> collecting server artifact"
cd ..
VER=$(node -p "require('./gui/package.json').version")
OUTDIR=release
mkdir -p "$OUTDIR"
# unpacked runnable binary (release/coder-agent-<ver>-linux)
cp -r "$OUTDIR/linux-unpacked" "$OUTDIR/coder-agent-${VER}-linux"
rm -rf "$OUTDIR/linux-unpacked"
cp gui/build-resources/backend/coder-agent-server "$OUTDIR/coder-agent-server-${VER}.linux"
# plain-name symlink so customers can just run "coder-agent"
ln -sf "coder-agent-${VER}.AppImage" "$OUTDIR/coder-agent"
# plain server launcher: run it on the server machine, point the GUI at host:port
cat > "$OUTDIR/coder-agent-server-${VER}.linux.sh" <<'EOF'
#!/usr/bin/env bash
# Coder Agent server launcher (Linux).
# Usage: <this script> [port] [token]
# then in the GUI: Settings > Connection > mode "remote", host, port, token.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${1:-8931}"
TOKEN="${2:-}"
ARGS=(--host 0.0.0.0 --port "$PORT")
[ -n "$TOKEN" ] && ARGS+=(--token "$TOKEN")
exec ./__SERVER_BIN__ "${ARGS[@]}"
EOF
sed -i "s/__SERVER_BIN__/coder-agent-server-${VER}.linux/" "$OUTDIR/coder-agent-server-${VER}.linux.sh"
chmod +x "$OUTDIR/coder-agent-server-${VER}.linux" "$OUTDIR/coder-agent-server-${VER}.linux.sh"
# usage guide for the end user
cp guide_release.txt "$OUTDIR/guide.txt"

echo "==> done, artifacts in release/"
ls -l "$OUTDIR"
