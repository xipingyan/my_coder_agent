---
name: app-icon
description: Use when adding or replacing an application icon for an Electron (or similar) desktop app - SVG design, PNG/ICO generation, BrowserWindow + electron-builder wiring, the Linux/GNOME dock WM_CLASS pitfall, and the dock-pin (.desktop clobber) pitfall. Proven recipe from the agent_ts_gui project.
triggers: icon|ico|appicon|favicon|logo|dock|taskbar|WM_CLASS|StartupWMClass|electron-builder icon|pin|pinned
---
# Application icon: design, generate, wire up

Working reference: `agent_ts_gui` (`gui/build-resources/icon.svg`, `make_icons.py`,
`gui/src/main/index.ts` `appIcon()`, `gui/package.json` build field, `start_gui.sh`).
Every pitfall below was actually hit.

## 1. Design the icon as SVG first

- One source of truth: `build-resources/icon.svg`, 512x512 viewBox.
- **Draw glyphs as paths/arcs, not `<text>`**: fonts differ per machine, so text-based
  icons render differently (or missing) on other systems. Arcs/circles/rects are
  deterministic everywhere.
- Keep it legible at 16px: at most 1-2 shapes, thick strokes (>= 40/512), high contrast.
  Detail that only reads at 512px is wasted.
- Reuse the app's own theme colors (e.g. accent + dark bg) so the icon matches the UI.
- Classic layout: rounded square background (`rx ~= 22%` of size) + one centered glyph.
- Arc geometry: for a "C" (circle radius r, gap of 2*theta degrees open right, center (cx,cy)):
  endpoint = (cx + r*cos(theta), cy -/+ r*sin(theta)), path = `M x1 y1 A r r 0 1 0 x2 y2`
  with `stroke-width` for thickness. Verify endpoints land ON the circle (r*cos/sin of the
  actual gap half-angle) - wrong angles make the arc bulge or close.

## 2. Generate PNG/ICO (no system image tools needed)

Use Python; install into the project venv: `pip install cairosvg pillow`.
Reference script: `gui/build-resources/make_icons.py` - copy it, adjust paths.

```python
import cairosvg
from PIL import Image
cairosvg.svg2png(url="icon.svg", write_to="icon-512.png", output_width=512, output_height=512)
cairosvg.svg2png(url="icon.svg", write_to="icon-128.png", output_width=128, output_height=128)
Image.open("icon-512.png").save("icon.ico",
    sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
```

- `icon-512.png` -> electron-builder packaging (linux `icon`, desktop entries).
- `icon-128.png` -> runtime `BrowserWindow({icon})` (128 is enough for taskbar/Alt-Tab).
- `icon.ico` (multi-size) -> Windows exe / NSIS installer.

### Verify the render before wiring it up

You cannot eyeball a PNG in a shell - verify programmatically:

1. **ASCII preview**: resize to 48x48 with PIL, print `#` for glyph pixels, `.` for bg,
   space for transparent. Catches geometry errors (overlapping shapes, off-center,
   wrong gap) instantly.
2. **Pixel probes**: sample known points (corner must be transparent/bg, glyph center,
   the gap between two glyphs must be background, not glyph). Scan a horizontal line and
   print the colored-pixel ranges - they must match the intended layout.

## 3. Wire it up (Electron)

Three places - missing any one leaves a platform without the icon:

1. **Runtime window icon** (taskbar/Alt-Tab): `BrowserWindow({ icon: ... })` in main.
   Dev vs packaged differ:
   ```ts
   function appIcon(): string {
     return app.isPackaged
       ? path.join(process.resourcesPath, "icon.png")   // via extraResources
       : path.resolve(__dirname, "../../build-resources/icon-128.png");
   }
   ```
   Path depth depends on your dist layout - resolve it and check `fs.existsSync` once.
2. **electron-builder** (`package.json` "build" field):
   ```json
   "extraResources": [{ "from": "build-resources/icon-512.png", "to": "icon.png" }],
   "linux": { "icon": "build-resources/icon-512.png" },
   "win":   { "icon": "build-resources/icon.ico" }
   ```
   `linux.icon` makes the AppImage/desktop entry carry the icon (packaged apps need no
   WM_CLASS workaround).
3. **`.gitignore`**: if `build-resources/` is ignored for build artifacts (e.g. a packaged
   backend binary), do NOT ignore the whole dir - use:
   ```
   build-resources/*
   !build-resources/icon.svg
   !build-resources/icon-128.png
   !build-resources/icon-512.png
   !build-resources/icon.ico
   !build-resources/make_icons.py
   ```
   Otherwise other clones silently fall back to the default icon (Electron does not error
   on a missing icon file). Verify with `git add --dry-run build-resources/`.

## 4. THE Linux/GNOME dock pitfall (biggest one)

**The GNOME dock does not use the window icon.** It matches the window's **WM_CLASS**
against `.desktop` entries in `~/.local/share/applications/` (and system dirs).
`BrowserWindow({icon})` only affects Alt-Tab and some compositors.

In dev mode Electron's WM_CLASS is `electron` -> no matching .desktop -> the dock shows
the generic system icon (on Ubuntu it looks like the settings/gear icon). This is the
most common "I set the icon but the taskbar didn't change" bug.

Fix in the launch script (e.g. `start_gui.sh`), Linux only:

1. Install a `.desktop` entry whose `Icon=` points at the 512px PNG and whose
   `StartupWMClass=` is a custom class:
   ```
   [Desktop Entry]
   Type=Application
   Name=My App
   Exec=/bin/true
   Icon=$PWD/build-resources/icon-512.png
   StartupWMClass=MyApp
   ```
   (run `update-desktop-database ~/.local/share/applications` afterwards, ignore failure)
2. Launch with `electron --class=MyApp .` so the WM_CLASS matches `StartupWMClass`.

Notes:
- GNOME caches icons in TWO layers, and both must be refreshed after regenerating
  the PNG:
  1. **Disk caches** (`~/.cache/gtk-3.0/icon-theme.cache`,
     `~/.cache/gtk-4.0/icon-theme.cache`, `~/.cache/gnome-shell/icon-cache`).
     Cheap to clear - do it automatically in the launch script after writing the
     `.desktop` entry:
     ```sh
     rm -f "$HOME/.cache/gtk-3.0/icon-theme.cache" \
           "$HOME/.cache/gtk-4.0/icon-theme.cache" \
           "$HOME/.cache/gnome-shell/icon-cache" 2>/dev/null || true
     ```
  2. **gnome-shell's in-memory cache**: it loads the `.desktop` icon at session
     start and never re-reads the file. Clearing the disk caches and restarting the
     app are NOT enough - the dock keeps the old icon until the shell itself is
     restarted. This is the layer people usually miss.
- So after changing the icon: clear disk caches (automated) AND restart the shell:
  - **Xorg** (`XDG_SESSION_TYPE=x11`): `killall gnome-shell` - the screen flashes
    back to the login screen for a moment, then the session returns. Works, verified.
  - **Wayland**: `killall gnome-shell` does not work (no X); logout/login instead.
- Packaged AppImage needs none of this: electron-builder generates the `.desktop` with
  the icon from `linux.icon`.

## 5. Pinning the AppImage to the dock (and the .desktop clobber pitfall)

GNOME dock icons are not direct AppImage launches: clicking a dock icon executes the
`Exec=` of the `.desktop` entry whose `StartupWMClass` matches the window's WM_CLASS
(or the one the user pinned). Two consequences:

- **Never let the dev launch script unconditionally overwrite
  `~/.local/share/applications/<name>.desktop`.** The placeholder entry
  (`Exec=/bin/true`, used only for the icon) and the user's pinned entry share the
  same filename. Overwriting it after the user pinned the AppImage turns the dock
  icon into a no-op - clicking it "does nothing" (it just runs `/bin/true`).
  Guard the write: only create/rewrite when the file is missing OR its `Exec=` is
  still the placeholder:
  ```sh
  if [ ! -f "$DESKTOP_DIR/<name>.desktop" ] || grep -q "^Exec=/bin/true" "$DESKTOP_DIR/<name>.desktop"; then
    cat > "$DESKTOP_DIR/<name>.desktop" <<EOF
  ...
  EOF
  fi
  ```
- **Diagnosing "pinned dock icon won't launch"**: read the `.desktop` file first -
  if `Exec=/bin/true` (or a stale path), that's the cause. Fix by restoring
  `Exec=<abs path to AppImage> --no-sandbox`, then `update-desktop-database` and
  clear the icon caches.
- **Versioned AppImage names** (`app-<ver>.AppImage`) make pinned entries go stale on
  upgrade - the user must re-pin. A symlink with a stable name (e.g. `app`) next to
  the AppImage avoids this.

## 6. Checklist

1. `icon.svg` renders correctly (ASCII preview + pixel probes).
2. PNG/ICO generated by a committed script (`make_icons.py`), so the icon is reproducible.
3. All icon files visible in `git status` (gitignore un-ignored).
4. `BrowserWindow` icon path exists in dev; `extraResources` + `linux/win.icon` set for packaging.
5. Linux dev launch: `.desktop` entry installed AND `--class=` matches `StartupWMClass`.
6. Boot the app: dock/taskbar shows the new icon. If it doesn't, the disk caches
   were cleared but gnome-shell still holds the old icon in memory - `killall
   gnome-shell` (Xorg) or logout/login (Wayland).
7. If the user pins the AppImage to the dock: the dev launch script must not
   clobber the resulting `.desktop` entry (see section 5).
