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

## Notes

- The GUI owns the Python backend: it starts it on launch, restarts it on
  demand (toolbar crash banner), and stops it on quit. The backend prints
  `WS_URL=ws://127.0.0.1:<port>` on stdout once it is ready.
- Automated checks: `cd backend && .venv/bin/python -m pytest tests` (WS RPC
  integration tests), `cd gui && npx tsc --noEmit && npm run build`.
- The renderer exposes `window.__onEvent` for CDP-based UI tests; the app can
  be launched with `--remote-debugging-port=<port>` for that.
