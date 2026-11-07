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
exec node_modules/.bin/electron --no-sandbox --disable-gpu . "$@"
