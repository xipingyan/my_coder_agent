// Electron main process: owns the window and the Python backend subprocess.
import { app, BrowserWindow, ipcMain } from "electron";
import { spawn, ChildProcess } from "node:child_process";
import * as path from "node:path";
import * as fs from "node:fs";

const BACKEND_DIR = path.resolve(__dirname, "../../../backend");
let backend: ChildProcess | null = null;
let backendUrl: string | null = null;
let window: BrowserWindow | null = null;

function findPython(): string {
  const venv = path.join(BACKEND_DIR, ".venv", "bin", "python");
  return fs.existsSync(venv) ? venv : "python3";
}

function startBackend(root: string | null): Promise<string> {
  console.log("[main] starting backend, python =", findPython(), "root =", root);
  return new Promise((resolve, reject) => {
    const args = [path.join(BACKEND_DIR, "coder_agent", "server.py")];
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
      if (window && !window.isDestroyed())
        window.webContents.send("backend-exit", code);
    });
    proc.on("error", reject);
    setTimeout(() => reject(new Error("backend did not start within 30s")), 30000);
  });
}

async function createWindow(root: string | null) {
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
  let url: string;
  try {
    url = await startBackend(root);
  } catch (e) {
    window.webContents.send("backend-exit", null);
    window.loadFile(path.join(__dirname, "../renderer/index.html"));
    return;
  }
  backendUrl = url;
  console.log("[main] backend ready:", url);
  window.webContents.send("backend-url", url);
  await window.loadFile(path.join(__dirname, "../renderer/index.html"));
}

app.whenReady().then(() => {
  const args = process.argv.slice(app.isPackaged ? 1 : 2);
  let root: string | null = null;
  const i = args.indexOf("--root");
  if (i >= 0 && args[i + 1]) root = args[i + 1];
  createWindow(root);
  ipcMain.handle("backend-url", () => backendUrl);
  ipcMain.handle("restart-backend", (_e, root: string | null) => {
    backend?.kill("SIGTERM");
    return createWindow(root);
  });
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow(root);
  });
});

app.on("window-all-closed", () => {
  backend?.kill("SIGTERM");
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  backend?.kill("SIGTERM");
});
