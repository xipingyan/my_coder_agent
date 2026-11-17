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

# clean old artifacts BEFORE building (the AppImage is produced into release/
# by electron-builder; cleaning afterwards would delete it and leave the
# "coder-agent" symlink dangling)
rm -rf release/coder-agent-* release/linux-unpacked release/win-unpacked \
       release/builder-debug.yml release/builder-effective-config.yaml

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
# electron-builder's debug dump, not a release artifact
rm -f "$OUTDIR/builder-debug.yml" "$OUTDIR/builder-effective-config.yaml"
# unpacked runnable binary (release/coder-agent-<ver>-linux)
cp -r "$OUTDIR/linux-unpacked" "$OUTDIR/coder-agent-${VER}-linux"
rm -rf "$OUTDIR/linux-unpacked"
cp gui/build-resources/backend/coder-agent-server "$OUTDIR/coder-agent-server-${VER}.linux"
# plain-name launcher so customers can just run "coder-agent"; it auto-adds
# --no-sandbox (chrome-sandbox cannot be SUID-configured in a user-built
# AppImage, see guide.txt). rm -f first: an old "coder-agent" may be a
# symlink pointing AT the AppImage, and "cat >" would follow it.
rm -f "$OUTDIR/coder-agent"
cat > "$OUTDIR/coder-agent" <<EOF
#!/usr/bin/env bash
exec "\$(cd "\$(dirname "\$0")" && pwd)/coder-agent-${VER}.AppImage" --no-sandbox "\$@"
EOF
chmod +x "$OUTDIR/coder-agent"
# server launcher (no version in name): starts the server in the background,
# idempotent (safe to call from .bashrc), supports "stop"
cat > "$OUTDIR/coder-agent-server.sh" <<'EOF'
#!/usr/bin/env bash
# Coder Agent server launcher (Linux, background).
# Usage:
#   coder-agent-server.sh [start] [port] [token]   start in background (default)
#   coder-agent-server.sh stop                     stop the running server
# then in the GUI: Settings > Connection > mode "remote", host, port, token.
# Only one server per machine: "start" fails if one is already running.
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
        echo "coder-agent-server already running (pid $(cat "$PIDFILE")); one server per machine, use 'stop' first" >&2
        exit 1
    fi
    PORT="${2:-8931}"
    TOKEN="${3:-}"
    TOKENFILE="$(pwd)/coder-agent-server.token"
    if [ -z "$TOKEN" ] && [ -f "$TOKENFILE" ]; then
        TOKEN="$(cat "$TOKENFILE")"
    fi
    if [ -z "$TOKEN" ]; then
        TOKEN="$(head -c 16 /dev/urandom 2>/dev/null | od -An -tx1 | tr -d ' \n')"
        [ -n "$TOKEN" ] || TOKEN="token-$(date +%s)-$$"
        printf '%s' "$TOKEN" > "$TOKENFILE"
        chmod 600 "$TOKENFILE"
        echo "generated a new token (saved to $TOKENFILE)"
    fi
    ARGS=(--host 0.0.0.0 --port "$PORT" --token "$TOKEN")
    nohup "./$BIN" "${ARGS[@]}" >>"$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    echo "coder-agent-server started (pid $!, log $LOG)"
    echo "token: $TOKEN   <- put this into the GUI: Settings > Connection"
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
