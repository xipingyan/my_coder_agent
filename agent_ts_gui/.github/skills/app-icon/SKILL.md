---
name: app-icon
description: Use when adding or replacing an application icon for an Electron (or similar) desktop app - SVG design, PNG/ICO generation, BrowserWindow + electron-builder wiring, and the Linux/GNOME dock WM_CLASS pitfall. Proven recipe from the agent_ts_gui project.
triggers: icon|ico|appicon|favicon|logo|dock|taskbar|WM_CLASS|StartupWMClass|electron-builder icon
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
- GNOME caches icons: the dock may keep the old icon until logout/login (or `killall
  gnome-shell` on Xorg). Not a bug in your setup.
- Packaged AppImage needs none of this: electron-builder generates the `.desktop` with
  the icon from `linux.icon`.

## 5. Checklist

1. `icon.svg` renders correctly (ASCII preview + pixel probes).
2. PNG/ICO generated by a committed script (`make_icons.py`), so the icon is reproducible.
3. All icon files visible in `git status` (gitignore un-ignored).
4. `BrowserWindow` icon path exists in dev; `extraResources` + `linux/win.icon` set for packaging.
5. Linux dev launch: `.desktop` entry installed AND `--class=` matches `StartupWMClass`.
6. Boot the app: dock/taskbar shows the new icon (after session refresh if cached).
