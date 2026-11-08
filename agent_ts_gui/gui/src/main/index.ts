// Electron main process: owns the window, the Python backend subprocess and the
// WebSocket bridge to the backend.
import { app, BrowserWindow, ipcMain, dialog } from "electron";
import { spawn, ChildProcess } from "node:child_process";
import * as path from "node:path";
import * as fs from "node:fs";
import WebSocket from "ws";

// Backend location: in dev the repo's backend/ dir + its venv python; in a
// packaged app a standalone PyInstaller bundle shipped in resources/backend.
function backendInfo(): { cmd: string; args: string[]; cwd: string } {
  if (app.isPackaged) {
    const dir = path.join(process.resourcesPath, "backend");
    const exe = process.platform === "win32" ? "coder-agent-server.exe" : "coder-agent-server";
    return { cmd: path.join(dir, exe), args: [], cwd: dir };
  }
  const dir = path.resolve(__dirname, "../../../backend");
  const venv = process.platform === "win32"
    ? path.join(dir, ".venv", "Scripts", "python.exe")
    : path.join(dir, ".venv", "bin", "python");
  const py = fs.existsSync(venv) ? venv : (process.platform === "win32" ? "python" : "python3");
  return { cmd: py, args: ["-m", "coder_agent.server"], cwd: dir };
}

let backend: ChildProcess | null = null;
let backendUrl: string | null = null;
let backendRoot: string | null = null;
let window: BrowserWindow | null = null;
let ws: WebSocket | null = null;

// ------------------------------------------------------------- backend proc
function startBackend(root: string | null): Promise<string> {
  const bi = backendInfo();
  const args = [...bi.args];
  if (root) args.push("--root", root);
  console.log("[main] starting backend:", bi.cmd, "root =", root);
  return new Promise((resolve, reject) => {
    const proc = spawn(bi.cmd, args, { cwd: bi.cwd, stdio: ["pipe", "pipe", "pipe"] });
    backend = proc;
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) { settled = true; reject(new Error("backend did not start within 30s")); }
    }, 30000);
    const onLine = (line: string) => {
      const m = line.match(/^WS_URL=(\S+)$/);
      if (m) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(m[1]);
      } else process.stdout.write(`[backend] ${line}\n`);
    };
    let buf = "";
    proc.stdout.on("data", (d: Buffer) => {
      buf += d.toString();
      let i;
      while ((i = buf.indexOf("\n")) >= 0) { onLine(buf.slice(0, i)); buf = buf.slice(i + 1); }
    });
    proc.stderr.on("data", (d: Buffer) => process.stderr.write(`[backend] ${d.toString()}`));
    proc.on("exit", (code) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(new Error(`backend exited before it was ready (code=${code})`));
      }
      if (backend !== proc) return; // superseded by a restart
      backend = null;
      closeWs();
      if (window && !window.isDestroyed())
        window.webContents.send("backend-exit", code);
    });
    proc.on("error", (e) => {
      if (!settled) { settled = true; clearTimeout(timer); reject(e); }
    });
  });
}

// ------------------------------------------------------------- ws bridge
function closeWs() {
  if (ws) {
    try { ws.close(); } catch { /* ignore */ }
    ws = null;
  }
}

function connectWs(url: string): Promise<void> {
  closeWs();
  const socket = new WebSocket(url);
  ws = socket;
  // resolves once the socket is open, so callers can send RPCs immediately
  const opened = new Promise<void>((resolve, reject) => {
    socket.once("open", () => { console.log("[main] ws connected"); resolve(); });
    socket.once("error", (e) => reject(e));
  });
  opened.catch(() => { /* close follows */ });
  socket.on("message", (data) => {
    let msg: any;
    try { msg = JSON.parse(String(data)); } catch { return; }
    if (window && !window.isDestroyed()) {
      if (msg.event) window.webContents.send("agent-event", msg);
      else window.webContents.send("agent-reply", msg);
    }
  });
  socket.on("close", () => {
    if (ws === socket) ws = null;
  });
  socket.on("error", () => { /* close follows */ });
  return opened;
}

// ------------------------------------------------------------- last project
function lastProjectFile(): string {
  return path.join(app.getPath("userData"), "last-project.txt");
}

async function createWindowAsync(root: string | null) {
  window = new BrowserWindow({
    width: 1500,
    height: 900,
    title: "Coder Agent",
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  let startRoot = root;
  if (!startRoot) {
    const p = lastProjectFile();
    if (fs.existsSync(p)) {
      const last = fs.readFileSync(p, "utf8").trim();
      if (last && fs.existsSync(last)) startRoot = last;
    }
  }
  let url: string;
  try {
    url = await startBackend(startRoot);
  } catch (e) {
    window.webContents.send("backend-exit", null);
    window.loadFile(path.join(__dirname, "../renderer/index.html"));
    return;
  }
  backendUrl = url;
  backendRoot = startRoot;
  console.log("[main] backend ready:", url);
  window.loadFile(path.join(__dirname, "../renderer/index.html"));
  await connectWs(url).catch((e) => console.error("[main] ws connect failed:", e));
  window.webContents.send("backend-url", url);
}

app.whenReady().then(() => {
  const args = process.argv.slice(app.isPackaged ? 1 : 2);
  let root: string | null = null;
  const i = args.indexOf("--root");
  if (i >= 0 && args[i + 1]) root = args[i + 1];
  createWindowAsync(root);

  ipcMain.handle("backend-url", () => backendUrl);
  ipcMain.handle("backend-root", () => backendRoot);
  ipcMain.handle("ws-send", (_e, payload: object) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) throw new Error("backend not connected");
    ws.send(JSON.stringify(payload));
  });
  ipcMain.handle("pick-directory", async () => {
    if (!window) return null;
    const r = await dialog.showOpenDialog(window, { properties: ["openDirectory"] });
    return r.canceled ? null : r.filePaths[0];
  });
  ipcMain.handle("last-project", () => {
    const p = lastProjectFile();
    if (fs.existsSync(p)) {
      const last = fs.readFileSync(p, "utf8").trim();
      if (last && fs.existsSync(last)) return last;
    }
    return null;
  });
  ipcMain.handle("set-last-project", (_e, p: string) => {
    try { fs.writeFileSync(lastProjectFile(), p); } catch { /* ignore */ }
  });
  ipcMain.handle("restart-backend", async (_e, root: string | null) => {
    // restart the backend in place (same window): kill the old proc, start a new one
    const r = root ?? backendRoot;
    if (backend) {
      const old = backend;
      backend = null; // so the old proc's exit handler is ignored
      old.kill("SIGTERM");
      old.stdout?.removeAllListeners();
      // make sure the old proc is really gone before starting the new one,
      // otherwise its stdout pipe can swallow the new WS_URL line
      await new Promise<void>((res) => {
        const t = setTimeout(() => { try { old.kill("SIGKILL"); } catch { /* ignore */ } res(); }, 2000);
        old.once("exit", () => { clearTimeout(t); res(); });
      });
    }
    closeWs();
    try {
      const url = await startBackend(r);
      backendUrl = url;
      backendRoot = r;
      await connectWs(url);
      if (window && !window.isDestroyed()) window.webContents.send("backend-url", url);
    } catch (e) {
      console.error("[main] backend restart failed:", e);
      if (window && !window.isDestroyed()) window.webContents.send("backend-exit", null);
    }
  });
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindowAsync(root);
  });
});

let quitting = false;
function killBackend() {
  if (quitting) return;
  quitting = true;
  // best-effort: ask the backend to stop the running turn, then kill it
  if (ws && ws.readyState === WebSocket.OPEN) {
    try { ws.send(JSON.stringify({ id: 0, method: "cancel", params: {} })); } catch { /* ignore */ }
  }
  const proc = backend;
  if (!proc || proc.exitCode !== null || proc.signalCode) { app.quit(); return; }
  const finish = () => {
    backend = null;
    app.quit();
  };
  const t = setTimeout(() => { try { proc.kill("SIGKILL"); } catch { /* ignore */ } }, 2500);
  proc.once("exit", () => { clearTimeout(t); finish(); });
  try { proc.kill("SIGTERM"); } catch { /* already dead */ }
}

app.on("window-all-closed", () => {
  killBackend();
  if (process.platform !== "darwin") app.quit(); // blocked by before-quit while the backend is alive
});

app.on("before-quit", (e) => {
  // never let the app exit while the backend process is still running
  if (backend) {
    e.preventDefault();
    killBackend();
  }
});
