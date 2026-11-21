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

# --- Linux dock / pin: dev mode intentionally does NOT manage a .desktop ----
# The GNOME dock matches windows to ~/.local/share/applications/*.desktop via
# WM_CLASS. The PACKAGED app (release/coder-agent) owns "coder-agent.desktop"
# (StartupWMClass=CoderAgent) so it can be pinned and re-launched from the dock.
# Dev mode must stay out of that file: a dev placeholder (Exec=/bin/true)
# sharing the filename/WM_CLASS with the AppImage entry clobbered the user's
# pinned dock entry (click = no-op) and broke the dock icon. Cost: the dev
# window runs with Electron's default WM_CLASS ("electron") and shows the
# generic icon in the dock - acceptable for a dev-only build.

exec node_modules/.bin/electron --no-sandbox --disable-gpu . "$@"
