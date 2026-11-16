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
# clean old artifacts so release/ only holds the current version
rm -rf "$OUTDIR"/coder-agent-* "$OUTDIR"/builder-debug.yml \
       "$OUTDIR"/builder-effective-config.yaml
# unpacked runnable binary (release/coder-agent-<ver>-linux)
cp -r "$OUTDIR/linux-unpacked" "$OUTDIR/coder-agent-${VER}-linux"
rm -rf "$OUTDIR/linux-unpacked"
cp gui/build-resources/backend/coder-agent-server "$OUTDIR/coder-agent-server-${VER}.linux"
# plain-name symlink so customers can just run "coder-agent"
ln -sf "coder-agent-${VER}.AppImage" "$OUTDIR/coder-agent"
# server launcher (no version in name): starts the server in the background,
# idempotent (safe to call from .bashrc), supports "stop"
cat > "$OUTDIR/coder-agent-server.sh" <<'EOF'
#!/usr/bin/env bash
# Coder Agent server launcher (Linux, background).
# Usage:
#   coder-agent-server.sh [start] [port] [token]   start in background (default)
#   coder-agent-server.sh stop                     stop the running server
# then in the GUI: Settings > Connection > mode "remote", host, port, token.
# Idempotent: if a server is already running, "start" exits 0 without dup.
set -euo pipefail
cd "$(dirname "$0")"
BIN="__SERVER_BIN__"
PIDFILE="$(pwd)/coder-agent-server.pid"
LOG="$(pwd)/coder-agent-server.log"

is_running() {
    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

case "${1:-start}" in
stop)
    if is_running; then
        kill "$(cat "$PIDFILE")" && rm -f "$PIDFILE"
        echo "coder-agent-server stopped"
    else
        rm -f "$PIDFILE"
        echo "coder-agent-server not running"
    fi
    ;;
start)
    if is_running; then
        echo "coder-agent-server already running (pid $(cat "$PIDFILE"))"
        exit 0
    fi
    PORT="${2:-8931}"
    TOKEN="${3:-}"
    ARGS=(--host 0.0.0.0 --port "$PORT")
    [ -n "$TOKEN" ] && ARGS+=(--token "$TOKEN")
    nohup "./$BIN" "${ARGS[@]}" >>"$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    echo "coder-agent-server started (pid $!, log $LOG)"
    ;;
*)
    echo "usage: $0 [start [port] [token] | stop]" >&2
    exit 1
    ;;
esac
EOF
sed -i "s/__SERVER_BIN__/coder-agent-server-${VER}.linux/" "$OUTDIR/coder-agent-server.sh"
chmod +x "$OUTDIR/coder-agent-server-${VER}.linux" "$OUTDIR/coder-agent-server.sh"
# systemd unit for running the server as a service on the Linux target machine
cp coder-agent-server.service "$OUTDIR/coder-agent-server.service"
# usage guide for the end user
cp guide_release.txt "$OUTDIR/guide.txt"

echo "==> done, artifacts in release/"
ls -l "$OUTDIR"
