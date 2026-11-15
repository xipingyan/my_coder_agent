---
name: app-release
description: Use when building or modifying the release pipeline for the agent_ts_gui desktop app (release.sh / release.bat, PyInstaller backend, electron-builder AppImage/NSIS, versioned artifacts, plain-name launchers, remote-server artifact). Proven recipe and pitfalls from the agent_ts_gui project.
triggers: release|AppImage|NSIS|PyInstaller|electron-builder|artifact|standalone|launcher
---
# Releasing agent_ts_gui (Electron GUI + Python backend)

Proven recipe from `agent_ts_gui`. Everything here was actually hit.

## 1. Layout

```
release.sh            # Linux only: AppImage + server artifact (bash)
release.bat           # Windows only: NSIS + server artifact (run on Windows)
backend/build_standalone.py   # PyInstaller one-file build of the backend
backend/_standalone_entry.py  # dedicated entry (server.py uses relative imports,
                              #   so it CANNOT be the PyInstaller entry script)
gui/package.json "build"      # electron-builder config, output dir = ../release
guide_release.txt             # end-user guide, copied to release/guide.txt
release/                  # all artifacts (gitignored)
```

## 2. Pipeline (both scripts do the same 4 steps)

1. **PyInstaller** backend -> `gui/build-resources/backend/coder-agent-server[.exe]`
   (auto-install PyInstaller into `backend/.venv` if missing).
2. **electron-builder** (`npm run dist:linux` / `dist:win`) -> `release/`
   (targets `AppImage`+`dir` on Linux, `nsis`+`dir` on Windows; the `dir`
   target gives the unpacked runnable folder).
3. **Collect artifacts** into `release/` with the version from
   `node -p "require('./gui/package.json').version"`:
   - `coder-agent-<ver>.AppImage` (or NSIS installer)
   - `coder-agent-<ver>-linux` / `-win` (renamed unpacked dir)
   - `coder-agent-server-<ver>.linux` / `.win.exe` (standalone server binary)
   - `coder-agent-server-<ver>.linux.sh` / `.win.bat` (server launcher:
     `cd "$(dirname "$0")"`, `--host 0.0.0.0 --port $1 --token $2`)
   - `guide.txt`
4. **Plain-name launcher** for customers (no version in the name):
   - Linux: `ln -sf coder-agent-<ver>.AppImage release/coder-agent`
   - Windows: no symlinks -> generate `release\coder-agent.bat` containing
     `start "" "%~dp0coder-agent-<ver>-win\coder-agent.exe"`

## 3. Rules & pitfalls

- **No cross-compiling**: PyInstaller cannot build a Windows exe on Linux.
  `release.sh` = Linux artifacts only; Windows is always `release.bat` on a
  Windows machine. Never add a `windows` branch to release.sh.
- **Version comes from one place**: `gui/package.json` version. Bump it and
  every artifact name follows. `artifactName: "${name}-${version}.${ext}"`
  in the electron-builder config is what produces `coder-agent-<ver>.AppImage`.
- **sed placeholder for the launcher script**: when generating the server
  launcher with a heredoc, write `__SERVER_BIN__` and `sed -i` it to the
  versioned binary name. Do NOT use `<ver>` as the sed pattern - it also
  matches inside `<ver>` in other text and corrupts the file.
- **chmod +x** both the server binary and its launcher script.
- **Standalone backend contract**: it must print `WS_URL=ws://...` on stdout
  when ready (the GUI main process parses that line). Verify it standalone
  before packaging the GUI.
- **PyInstaller data**: `--add-data` for `prompts/defaults`, `defaults`,
  `skills` (separator is `:` on POSIX, `;` on Windows - use `os.pathsep`).
- **Plain-name launchers are relative**: the AppImage symlink is relative
  (`ln -sf coder-agent-<ver>.AppImage coder-agent`) so the `release/` folder
  can be moved/copied as a whole; the Windows .bat uses `%~dp0`.
- **electron-builder downloads AppImage tooling** automatically; no extra setup.

## 4. Verification checklist

1. Standalone backend runs and prints `WS_URL` (before GUI build).
2. `release/` contains all artifacts with the current version, all executable.
3. `release/coder-agent` (symlink) resolves and the AppImage boots.
4. Server launcher starts the server; the GUI can connect to it
   (Settings > Connection > remote mode - see `gui/cdp_remote_test.cjs`).
5. `guide.txt` present and up to date.
