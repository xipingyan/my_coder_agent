---
name: electron-tsgui
description: Use when scaffolding or modifying a desktop GUI with TypeScript + Electron (main/preload/renderer split, esbuild, IPC, native backend subprocess, CDP smoke tests, electron-builder packaging). Proven layout and pitfalls from the agent_ts_gui project.
triggers: electron|esbuild|BrowserWindow|contextBridge|ipcMain|preload|electron-builder|AppImage|remote-debugging|CDP
---
# TypeScript + Electron desktop GUI

Working reference: `agent_ts_gui` (Electron 31 + esbuild + Python WS backend).
Follow this layout and these rules; every pitfall below was actually hit.

## 1. Project layout

```
gui/
  package.json        # main: dist/main/index.js; "build" field = electron-builder config
  tsconfig.json       # strict, noEmit, moduleResolution bundler, target ES2022
  build.mjs           # esbuild: main+preload (node/cjs) and renderer (browser/iife)
  src/
    types.ts          # shared types (AgentEvent, ReplyMsg, ...)
    main/index.ts     # window, backend subprocess, ws bridge, ipcMain handlers
    preload/index.ts  # contextBridge.exposeInMainWorld("api", {...}) - typed API
    renderer/
      index.html      # static, loaded via loadFile
      style.css
      app.ts          # all UI logic, one IIFE bundle
  cdp_*_test.cjs      # CDP smoke tests (plain .cjs, must live in gui/ to resolve "ws")
  build-resources/    # extraResources input (e.g. packaged backend binary)
  dist/ release/      # build outputs (gitignored)
```

Rules:
- No framework. Renderer = plain TS + DOM, one big `app.ts` is fine for a tool UI.
- `contextIsolation: true`, `nodeIntegration: false`. Renderer ONLY talks to main through the
  typed `window.api` exposed by preload. Keep the preload surface small and typed.
- Build with esbuild (not tsc): `npm run build` = `node build.mjs`; type-check separately with
  `npx tsc --noEmit`. Renderer is bundled as iife over copied static assets.
- Verify loop: `npm run build && npx tsc --noEmit`, then `npm run dev`
  (`electron --no-sandbox .` - `--no-sandbox` is required on this machine).

## 2. Main process: owning a native backend subprocess

Pattern (Python server speaking JSON over WS):
- `backendInfo()`: `app.isPackaged` ? packaged binary in `process.resourcesPath/backend`
  : repo `backend/` + venv python. Never hardcode one or the other.
- Backend prints `WS_URL=<url>` on its stdout when ready. Main parses that line to resolve a
  `startBackend(): Promise<string>`.
- **`settled` flag**: the promise can be resolved (WS_URL seen) AND rejected (exit/error) -
  guard with `let settled = false` or you get double-settle / double-handler bugs.
- **Restart**: kill old proc and `await` its real `exit` (with a 2s SIGKILL fallback) BEFORE
  starting the new one - otherwise the old stdout pipe can swallow the new `WS_URL` line.
- **WS connect**: `connectWs()` returns a promise that resolves on socket `open`; callers
  `await` it before sending RPCs, or the first RPC races the connection.
- **Quit**: in `before-quit`, `e.preventDefault()` while the backend is alive, SIGTERM it,
  wait for exit (SIGKILL fallback ~2.5s), then `app.quit()`. Without this, python processes
  are left behind.
- **No blank window**: `loadFile` immediately, then start the backend; the renderer shows a
  "Starting backend..." state and waits for the `backend-url` IPC event.

## 3. Renderer <-> main <-> backend

- IPC: `ipcMain.handle` for request/response (`ws-send`, `pick-directory`, `restart-backend`...),
  `webContents.send` for push events (`backend-url`, `backend-exit`, `agent-event`, `agent-reply`).
- Renderer keeps an RPC id counter: `send({id, method, params})`, answers matched by id in
  `onReply`; events arrive via `onEvent`. When the backend is disconnected, fail RPCs fast
  (reject pending ids, show a restart banner) instead of hanging.
- Chinese IME: in input handlers ignore Enter while composing
  (`e.isComposing || e.keyCode === 229`), or IME candidates submit the message early.
- Expose a `window.__onEvent` hook (or similar) so CDP tests can assert on rendered state.

## 4. CDP smoke tests (headless verification)

Use when you cannot click the UI yourself. Recipe (see `gui/cdp_boot_test.cjs`):
- Plain `.cjs` script in `gui/` (so `require("ws")`/`require("electron")` resolve).
- Spawn `electron . --no-sandbox --remote-debugging-port=9341`, poll
  `http://127.0.0.1:9341/json` (or `/json/version`) for the page's `webSocketDebuggerUrl`.
- Connect with `new WebSocket(url, { perMessageDeflate: false })`, wrap `Runtime.evaluate`
  in a promise with an id counter, drive the UI via `window.__*` hooks, assert on DOM.
- Always kill the electron proc at the end.
- Killing processes: never `pkill -f <pattern>` in a test shell - the pattern matches the
  test shell itself. Find the pid with `ps`/`awk` and kill by pid.

## 5. Packaging (electron-builder + PyInstaller)

- electron-builder config lives in `package.json` "build" field; `extraResources` copies
  `build-resources/backend` into `resources/backend` of the app.
- Python backend: PyInstaller **one-file**, but the entry must be a dedicated script
  (`_standalone_entry.py` that imports `coder_agent.server.run`) because a module with
  relative imports cannot be the entry script. `--add-data` for prompts/defaults/skills.
- **No cross-compiling**: PyInstaller cannot build a Windows exe on Linux.
  `release.sh` = Linux AppImage only; Windows builds run `release.bat` on a Windows machine.
- Verify the standalone backend alone first (it must print `WS_URL`), then
  `electron-builder --linux AppImage`, then boot the packaged app.

## 6. Checklist before calling a GUI change done

1. `npm run build && npx tsc --noEmit` clean.
2. `npm run dev` boots, page visible before backend ready, no blank window.
3. CDP test for the touched behavior passes.
4. Close the app: no orphan backend process (`ps` check).
5. If packaging: standalone backend runs, AppImage builds, packaged app boots.
