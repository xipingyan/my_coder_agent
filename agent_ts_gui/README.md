# Coder Agent — TS GUI

New front-end for the local coding agent. The heavy lifting (agent loop, LLM,
tools, sessions, approvals, context compression, planning) stays in **Python**
(`backend/`); the interface is **Electron + TypeScript** (`gui/`).

Why TS/Electron: Chromium's IME handles ibus/fcitx Chinese input natively, so
the PySide6/Qt input-method pain goes away.

## Layout

```
agent_ts_gui/
  backend/            # Python package `coder_agent` (core copied from the old project)
    coder_agent/      #   agent/engine/session/approval/llm/context/tools/orchestrator/prompts
    coder_agent/server.py   # WebSocket server (the new bridge to the GUI)
    pyproject.toml
  gui/                # Electron + TypeScript
    src/main/         #   main process: window + Python backend subprocess + IPC
    src/preload/      #   type-safe bridge to the renderer
    src/renderer/     #   the UI (HTML/CSS/JS, styled to match the old PySide6 look)
  start_gui.sh
  release.sh            # Linux AppImage build (PyInstaller backend + electron-builder)
  release.bat           # Windows NSIS build (run on Windows)
```

## Development setup (from scratch)

Prerequisites: **Python 3.11+** and **Node 20+**. The GUI owns the Python
backend: in development it spawns `backend/.venv`'s python (see
`gui/src/main/index.ts:backendInfo`), so both environments must exist.

### Linux

```bash
# 1. backend (Python venv)
cd backend && python3 -m venv .venv && .venv/bin/pip install -e .
# 2. gui (Node 20+, e.g. via nvm)
cd ../gui && npm install

# run
cd .. && ./start_gui.sh                       # last-used project / ask
./start_gui.sh --root /path/to/proj           # explicit project
```

### China / restricted network

`npm install` often dies in `electron`'s `postinstall`, not on the npm packages
themselves. The script downloads `electron.exe` from GitHub
(`github.com/electron/electron/releases`). On this machine that connection
timed out (`ETIMEDOUT`). `https://npmmirror.com/mirrors/electron/` also timed
out. The URL that returned HTTP 200 was:

```text
https://cdn.npmmirror.com/binaries/electron/v<version>/electron-v<version>-win32-x64.zip
```

Two different mirrors are involved. Do not put `electron_mirror` in `.npmrc`.
npm 11 warns `Unknown project config "electron_mirror"` and that value
overrides the working mirror, so the install still hits the dead host.

1. Package registry — local only. `gui/.npmrc` is gitignored, so create it
   yourself:

```text
registry=https://registry.npmmirror.com
```

2. Electron binary — already in `gui/package.json`. `@electron/get` reads
   `npm_package_config_electron_*` from this block:

```json
"config": {
  "electron_mirror": "https://cdn.npmmirror.com/binaries/electron/",
  "electron_custom_dir": "{{ version }}"
}
```

Then, from `gui/`:

```bat
npm install
node -e "console.log(require('electron'))"
```

The second command should print
`...\gui\node_modules\electron\dist\electron.exe`.

If `postinstall` still resets mid-download (`ECONNRESET`), skip the script,
download the zip, and drop it where Electron expects it. Replace `31.7.7`
with `node -p "require('./node_modules/electron/package.json').version"`.

```bat
cd gui
set ELECTRON_SKIP_BINARY_DOWNLOAD=1
npm install
curl -L --retry 5 -o %TEMP%\electron.zip https://cdn.npmmirror.com/binaries/electron/v31.7.7/electron-v31.7.7-win32-x64.zip
mkdir node_modules\electron\dist
tar -xf %TEMP%\electron.zip -C node_modules\electron\dist
if exist node_modules\electron\dist\electron.d.ts move /Y node_modules\electron\dist\electron.d.ts node_modules\electron\electron.d.ts
<nul set /p=electron.exe> node_modules\electron\path.txt
node -e "console.log(require('electron'))"
```

`path.txt` must be exactly `electron.exe` with no trailing newline. Linux uses
`electron-v<version>-linux-x64.zip` and writes `electron`. macOS writes
`Electron.app/Contents/MacOS/Electron`.

### Windows

```bat
:: 1. backend (Python venv; python must be 3.11+)
cd backend
python -m venv .venv
.venv\Scripts\pip install -e .
:: 2. gui (Node 20+ on PATH)
cd ..\gui
npm install

:: run (from the repo root)
start_gui.bat
start_gui.bat --root C:\path\to\project
```

Notes:

- The GUI locates the backend at `backend\.venv\Scripts\python.exe`
  automatically; if it is missing it falls back to `python` on PATH (the
  `coder_agent` package must then be importable).
- `--no-sandbox` is not needed on Windows (it is a Linux/root concern), so
  `start_gui.bat` does not pass it.
- The CDP smoke tests (`cdp_*_test.cjs`) are cross-platform: on Windows they
  use `netstat`/`taskkill` instead of `ss`/process groups.
- The `debug_backtrace` agent tool needs `gdb`, which is Linux-only; on
  Windows the tool reports a friendly "gdb is not installed" error and the
  agent falls back to `run_command`.

### Tests

```bash
# Linux: everything (backend pytest + all GUI CDP smoke tests)
./run_tests.sh

# Windows: same, in a cmd prompt
run_tests.bat
```

Individual targets: `backend`, `gui`, `remote` (needs a standalone server on
`127.0.0.1:8931`), or a single GUI test name (`boot`, `theme`, `split`, ...).
`cd gui && node run_all_tests.cjs [names...]` runs the CDP suite directly
(starts the remote server itself for the `remote` test).

## Status (phased build)

- [x] **Phase 0** — scaffold: Electron+TS app, Python backend subprocess, WS handshake
- [x] **Phase 1** — backend WebSocket RPC + event protocol
- [x] **Phase 2** — main window layout + live event rendering
- [x] **Phase 3** — sessions, turns, rewind / edit-and-rerun
- [x] **Phase 4** — dialogs (approval, plan review, stage confirm, edit prompt)
- [x] **Phase 5** — settings (model / context / task prompts / approval)
- [x] **Phase 6** — polish: backend crash banner + in-place restart, clean shutdown
      (backend is SIGTERM'd and the app waits for it before exiting), IME-safe
      Enter handling, fail-fast RPC when the backend is down, English UI

## Release (installing on a third-party machine)

The app is distributed as a single self-contained binary per platform:

- **Linux**: `coder-agent-<version>.AppImage` (x86_64)
- **Windows**: `coder-agent-<version>.exe` (NSIS installer)

The target machine needs **nothing pre-installed** — no Python, no Node.
The Python backend is bundled as a PyInstaller one-file executable and
shipped inside the app (`resources/backend/`); the Electron main process
locates and spawns it automatically (see `gui/src/main/index.ts:backendInfo`).

### Building the packages

```bash
# one-time: backend venv (PyInstaller is auto-installed by the release
# scripts if missing, since it is a build-time tool, not a runtime dep)
cd backend && python3 -m venv .venv && .venv/bin/pip install -e .

# Linux (build on Linux x86_64)
./release.sh           # -> release/coder-agent-<ver>.AppImage

# Windows (build on Windows; PyInstaller and NSIS cannot cross-compile)
release.bat            # -> release/coder-agent-<ver>.exe
```

`release.sh` takes no arguments — it always builds the Linux AppImage.
The Windows installer is only built by `release.bat`, which must be run on
Windows (PyInstaller cannot cross-compile the backend).

**Windows: run `release.bat` from an ELEVATED (administrator) prompt.**
electron-builder unpacks its winCodeSign tooling with 7-Zip, which creates
symbolic links (e.g. `darwin/10.12/lib/libcrypto.dylib`); a normal user
lacks that privilege and the build dies with
`Cannot create symbolic link : A required privilege is not held by the
client`. Fix: right-click cmd → "Run as administrator", then `release.bat`
(alternatively enable Windows Developer Mode, which grants normal users
symlink creation).

What the script does:

1. `backend/build_standalone.py` — PyInstaller one-file build of
   `coder_agent.server` (package data `prompts/defaults`, `defaults`,
   `skills` are embedded) into `gui/build-resources/backend/`.
2. `electron-builder` (config in `gui/package.json` under `"build"`) —
   bundles `gui/dist` + the backend into the AppImage / NSIS installer.

### Installing on the target machine

**Linux**

```bash
chmod +x coder-agent-0.1.0.AppImage
./coder-agent-0.1.0.AppImage
```

(Optionally move it to `/opt` or `~/Applications` and create a `.desktop`
entry.) First run: click the project label in the toolbar to pick the
project to work on, then open **Settings** to configure the LLM provider
and API key.

**Windows**

Run `coder-agent-0.1.0.exe` and follow the installer (you can choose
the install directory). Launch "Coder Agent" from the Start menu. First run
is the same: pick a project, then configure the LLM in **Settings**.

### Where the app keeps its data

| What | Linux | Windows |
|---|---|---|
| Config, prompts, skills, memory (`~/.coder_agent`) | `~/.coder_agent` | `%USERPROFILE%\.coder_agent` |
| GUI state (last project, userData) | `~/.config/coder-agent-gui` | `%APPDATA%/coder-agent-gui` |
| Sessions / snapshots | `<project>/.agent` | `<project>\.agent` |

The backend is a child of the GUI process: it starts with the app, can be
restarted from the toolbar crash banner, and is killed on quit.
If it cannot start, the window shows a "Backend process exited" banner with
a Restart button.

## Notes

- The GUI owns the Python backend: it starts it on launch, restarts it on
  demand (toolbar crash banner), and stops it on quit. The backend prints
  `WS_URL=ws://127.0.0.1:<port>` on stdout once it is ready.
- Automated checks: `cd backend && .venv/bin/python -m pytest tests` (WS RPC
  integration tests), `cd gui && npx tsc --noEmit && npm run build`.
- The renderer exposes `window.__onEvent` for CDP-based UI tests; the app can
  be launched with `--remote-debugging-port=<port>` for that.

### China / restricted network

`npm install` often dies in `electron`'s `postinstall`, not on the npm packages
themselves. The script downloads `electron.exe` from GitHub
(`github.com/electron/electron/releases`). On this machine that connection
timed out (`ETIMEDOUT`). `https://npmmirror.com/mirrors/electron/` also timed
out. The URL that returned HTTP 200 was:

```text
https://cdn.npmmirror.com/binaries/electron/v<version>/electron-v<version>-win32-x64.zip
```

Two different mirrors are involved. Do not put `electron_mirror` in `.npmrc`.
npm 11 warns `Unknown project config "electron_mirror"` and that value
overrides the working mirror, so the install still hits the dead host.

1. Package registry — local only. `gui/.npmrc` is gitignored, so create it
   yourself:

```text
registry=https://registry.npmmirror.com
```

2. Electron binary — already in `gui/package.json`. `@electron/get` reads
   `npm_package_config_electron_*` from this block:

```json
"config": {
  "electron_mirror": "https://cdn.npmmirror.com/binaries/electron/",
  "electron_custom_dir": "{{ version }}"
}
```

Then, from `gui/`:

```bat
npm install
node -e "console.log(require('electron'))"
```

The second command should print
`...\gui\node_modules\electron\dist\electron.exe`.

If `postinstall` still resets mid-download (`ECONNRESET`), skip the script,
download the zip, and drop it where Electron expects it. Replace `31.7.7`
with `node -p "require('./node_modules/electron/package.json').version"`.

```bat
cd gui
set ELECTRON_SKIP_BINARY_DOWNLOAD=1
npm install
curl -L --retry 5 -o %TEMP%\electron.zip https://cdn.npmmirror.com/binaries/electron/v31.7.7/electron-v31.7.7-win32-x64.zip
mkdir node_modules\electron\dist
tar -xf %TEMP%\electron.zip -C node_modules\electron\dist
if exist node_modules\electron\dist\electron.d.ts move /Y node_modules\electron\dist\electron.d.ts node_modules\electron\electron.d.ts
<nul set /p=electron.exe> node_modules\electron\path.txt
node -e "console.log(require('electron'))"
```

`path.txt` must be exactly `electron.exe` with no trailing newline. Linux uses
`electron-v<version>-linux-x64.zip` and writes `electron`. macOS writes
`Electron.app/Contents/MacOS/Electron`.

# run
