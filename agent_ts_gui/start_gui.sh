#!/usr/bin/env bash
# Launch the Coder Agent GUI (Electron front-end + Python backend).
# Usage: ./start_gui.sh [--root /path/to/project]
set -euo pipefail
cd "$(dirname "$0")"

# Node via nvm (installed at ~/.nvm)
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

# Electron binary download needs a proxy in this environment (optional)
# export http_proxy=http://proxy-dmz.intel.com:912
# export https_proxy=http://proxy-dmz.intel.com:912

cd gui
npm run build

# --- app icon on Linux (GNOME dock / taskbar) --------------------------------
# The dock does NOT use the window icon; it matches the window's WM_CLASS
# against a .desktop entry. Electron's default WM_CLASS is "electron", so
# install a "CoderAgent" .desktop entry (with our icon) and start with
# --class=CoderAgent so the dock shows the CC icon instead of the system default.
if [ "$(uname)" = "Linux" ]; then
  DESKTOP_DIR="$HOME/.local/share/applications"
  mkdir -p "$DESKTOP_DIR"
  cat > "$DESKTOP_DIR/coder-agent.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Coder Agent
Comment=AI coding agent (Electron GUI + Python backend)
Exec=/bin/true
Icon=$PWD/build-resources/icon-512.png
StartupWMClass=CoderAgent
EOF
  update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

exec node_modules/.bin/electron --no-sandbox --disable-gpu --class=CoderAgent . "$@"
