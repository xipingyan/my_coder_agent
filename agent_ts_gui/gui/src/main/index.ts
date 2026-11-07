// Electron main process: owns the window, the Python backend subprocess and the
// WebSocket bridge to the backend.
import { app, BrowserWindow, ipcMain, dialog } from "electron";
import { spawn, ChildProcess } from "node:child_process";
import * as path from "node:path";
import * as fs from "node:fs";
import WebSocket from "ws";

const BACKEND_DIR = path.resolve(__dirname, "../../../backend");
let backend: ChildProcess | null = null;
let backendUrl: string | null = null;
let backendRoot: string | null = null;
let window: BrowserWindow | null = null;
let ws: WebSocket | null = null;

function findPython(): string {
  const venv = path.join(BACKEND_DIR, ".venv", "bin", "python");
  return fs.existsSync(venv) ? venv : "python3";
}

// ------------------------------------------------------------- backend proc
function startBackend(root: string | null): Promise<string> {
  console.log("[main] starting backend, python =", findPython(), "root =", root);
  return new Promise((resolve, reject) => {
    const args = ["-m", "coder_agent.server"];
    if (root) args.push("--root", root);
    const proc = spawn(findPython(), args, { cwd: BACKEND_DIR, stdio: ["pipe", "pipe", "pipe"] });
    backend = proc;
    let buf = "";
    const onLine = (line: string) => {
      const m = line.match(/^WS_URL=(\S+)$/);
      if (m) resolve(m[1]);
      else process.stdout.write(`[backend] ${line}\n`);
    };
    proc.stdout.on("data", (d: Buffer) => {
      buf += d.toString();
      let i;
      while ((i = buf.indexOf("\n")) >= 0) { onLine(buf.slice(0, i)); buf = buf.slice(i + 1); }
    });
    proc.stderr.on("data", (d: Buffer) => process.stderr.write(`[backend] ${d.toString()}`));
    proc.on("exit", (code) => {
      backend = null;
      closeWs();
      if (window && !window.isDestroyed())
        window.webContents.send("backend-exit", code);
    });
    proc.on("error", reject);
    setTimeout(() => reject(new Error("backend did not start within 30s")), 30000);
  });
}

// ------------------------------------------------------------- ws bridge
function closeWs() {
  if (ws) {
    try { ws.close(); } catch { /* ignore */ }
    ws = null;
  }
}

function connectWs(url: string) {
  closeWs();
  const socket = new WebSocket(url);
  ws = socket;
  socket.on("open", () => console.log("[main] ws connected"));
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
  window.webContents.send("backend-url", url);
  connectWs(url);
  window.loadFile(path.join(__dirname, "../renderer/index.html"));
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
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
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
    backend?.kill("SIGTERM");
    await createWindowAsync(root);
  });
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindowAsync(root);
  });
});

app.on("window-all-closed", () => {
  backend?.kill("SIGTERM");
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  backend?.kill("SIGTERM");
});
