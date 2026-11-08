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
- [ ] **Phase 5** — settings (model / context / task prompts / approval)
- [ ] **Phase 6** — polish, Chinese-input check, docs
