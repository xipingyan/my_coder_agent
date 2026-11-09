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

## Run

```bash
./start_gui.sh                       # last-used project / ask
./start_gui.sh --root /path/to/proj  # explicit project
```

First time only, set up the two environments:

```bash
# backend (Python venv)
cd backend && python3 -m venv .venv && .venv/bin/pip install -e .

# gui (Node 20+, via nvm)
cd gui && npm install
```

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
# one-time: PyInstaller in the backend venv
cd backend && python3 -m venv .venv && .venv/bin/pip install -e . pyinstaller

# Linux (build on Linux x86_64)
./release.sh           # -> gui/release/coder-agent-<ver>.AppImage

# Windows (build on Windows; PyInstaller and NSIS cannot cross-compile)
release.bat            # -> gui/release/coder-agent-<ver>.exe
```

`release.sh` takes no arguments — it always builds the Linux AppImage.
The Windows installer is only built by `release.bat`, which must be run on
Windows (PyInstaller cannot cross-compile the backend).

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
