// Electron main process: owns the window, the Python backend subprocess and the
// WebSocket bridge to the backend.
import { app, BrowserWindow, ipcMain, dialog, Menu } from "electron";
import { spawn, ChildProcess } from "node:child_process";
import * as path from "node:path";
import * as fs from "node:fs";
import WebSocket from "ws";
import { backendUrlFromLine } from "./backend-output";

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

let quitting = false; // set by killBackend(); blocks new backend spawns during shutdown
let backend: ChildProcess | null = null;
let backendUrl: string | null = null;
let backendRoot: string | null = null;
let window: BrowserWindow | null = null;
let ws: WebSocket | null = null;
let wsReady: Promise<void> = Promise.resolve(); // resolves when the current socket is open
let backendUp = false; // true once a connection is established; false while (re)connecting / down
let connecting = false; // true while a connectWs() is in flight (RPCs should queue)
let reconnecting = false; // suppress "backend-exit" while switching connections
let remoteReconnect = false; // auto-reconnect is active for the current remote connection
let reconnectTimer: NodeJS.Timeout | null = null;
let reconnectDelay = 1000; // ms, grows x2 up to 15s

// ------------------------------------------------------------- connection
// mode "local"  = spawn the bundled backend on this machine (default, as before).
// mode "remote" = connect to a coder-agent-server running on another machine
// (or on this machine, started separately). One server serves one GUI.
interface Connection { mode: "local" | "remote"; host: string; port: number; token: string; }
const DEFAULT_CONN: Connection = { mode: "local", host: "127.0.0.1", port: 8931, token: "" };
let conn: Connection = { ...DEFAULT_CONN };

function connFile(): string {
  return path.join(app.getPath("userData"), "connection.json");
}
function loadConn(): Connection {
  try {
    if (fs.existsSync(connFile())) {
      const c = JSON.parse(fs.readFileSync(connFile(), "utf8"));
      return { mode: c.mode === "remote" ? "remote" : "local",
        host: String(c.host || "127.0.0.1"), port: Number(c.port) || 8931, token: String(c.token || "") };
    }
  } catch { /* ignore */ }
  return { ...DEFAULT_CONN };
}
function saveConn(c: Connection) {
  conn = c;
  try { fs.writeFileSync(connFile(), JSON.stringify(c, null, 2)); } catch { /* ignore */ }
}

// ------------------------------------------------------------- backend proc
function startBackend(root: string | null): Promise<string> {
  const bi = backendInfo();
  const args = [...bi.args];
  if (root) args.push("--root", root);
  console.log("[main] starting backend:", bi.cmd, "root =", root);
  return new Promise((resolve, reject) => {
    const proc = spawn(bi.cmd, args, { cwd: bi.cwd, stdio: ["pipe", "pipe", "pipe"] });
    backend = proc;
    if (quitting) {
      // the app is shutting down (window closed while a restart was in flight):
      // never let this process outlive the app, or before-quit blocks forever
      try { proc.kill("SIGKILL"); } catch { /* ignore */ }
      proc.once("exit", () => reject(new Error("app is quitting")));
      return;
    }
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) { settled = true; reject(new Error("backend did not start within 30s")); }
    }, 30000);
    const onLine = (line: string) => {
      const url = backendUrlFromLine(line);
      if (url) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(url);
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
      if (backend !== proc) return; // superseded by a restart / connection switch
      backend = null;
      closeWs();
      // while switching connections the old proc dying is expected: no banner
      if (reconnecting) { reconnecting = false; return; }
      if (window && !window.isDestroyed())
        window.webContents.send("backend-exit", code);
    });
    proc.on("error", (e) => {
      if (!settled) { settled = true; clearTimeout(timer); reject(e); }
    });
  });
}

// ------------------------------------------------------------- ws bridge
function stopReconnect() {
  remoteReconnect = false;
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  reconnectDelay = 1000;
}

function closeWs() {
  if (ws) {
    try { ws.close(); } catch { /* ignore */ }
    ws = null;
  }
}

function connectWs(url: string, token = ""): Promise<void> {
  closeWs();
  backendUp = false; // a new connection is in flight
  connecting = true;
  const socket = new WebSocket(url);
  ws = socket;
  // resolves once the socket is open, so callers can send RPCs immediately.
  // wsReady is what the ws-send IPC handler awaits: RPCs arriving while the
  // (re)connect is in flight must queue here instead of failing with
  // "backend not connected". It ALWAYS resolves (the handler then checks
  // backendUp), so a failed connect can never hang a queued RPC.
  let resolveReady: () => void;
  wsReady = new Promise<void>((res) => { resolveReady = res; });
  const opened = new Promise<void>((resolve, reject) => {
    socket.once("open", () => {
      console.log("[main] ws connected");
      // token handshake: must be the very first frame when the server requires one
      if (token) { try { socket.send(JSON.stringify({ token })); } catch { /* ignore */ } }
      backendUp = true;
      connecting = false;
      resolveReady();
      resolve();
    });
    socket.once("error", (e) => { connecting = false; resolveReady(); reject(e); });
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
    // Only the socket that is still "current" triggers a reconnect. When we
    // deliberately closeWs() (switching connections / restarting), ws is already
    // null, so a replaced socket's close event must not start reconnecting.
    const isCurrent = ws === socket;
    if (isCurrent) { ws = null; backendUp = false; }
    if (isCurrent && remoteReconnect && !quitting) {
      if (window && !window.isDestroyed()) window.webContents.send("backend-exit", null);
      console.log(`[main] remote ws closed, retry in ${reconnectDelay}ms`);
      scheduleReconnect(url, token);
    }
  });
  socket.on("error", () => { /* close follows */ });
  return opened;
}

function scheduleReconnect(url: string, token: string) {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(async () => {
    reconnectTimer = null;
    if (!remoteReconnect || quitting) return;
    try {
      await connectWs(url, token);
      reconnectDelay = 1000;
      if (window && !window.isDestroyed()) window.webContents.send("backend-url", url);
    } catch (e) {
      console.log("[main] remote reconnect failed:", e);
      if (window && !window.isDestroyed()) window.webContents.send("backend-exit", null);
      reconnectDelay = Math.min(reconnectDelay * 2, 15000);
      if (!quitting) scheduleReconnect(url, token);
    }
  }, reconnectDelay);
}

// ------------------------------------------------------------- last project
function lastProjectFile(): string {
  return path.join(app.getPath("userData"), "last-project.txt");
}

function lastProject(): string | null {
  if (conn.mode === "remote") return null; // the path lives on the server machine
  const p = lastProjectFile();
  if (fs.existsSync(p)) {
    const last = fs.readFileSync(p, "utf8").trim();
    if (last && fs.existsSync(last)) return last;
  }
  return null;
}

// App icon: in dev the repo's build-resources/icon-128.png; in a packaged app
// the same file shipped as resources/icon.png (see electron-builder extraResources).
function appIcon(): string {
  return app.isPackaged
    ? path.join(process.resourcesPath, "icon.png")
    : path.resolve(__dirname, "../../build-resources/icon-128.png");
}

async function createWindowAsync(root: string | null) {
  window = new BrowserWindow({
    width: 1500,
    height: 900,
    title: "Coder Agent",
    icon: appIcon(),
    // frameless: the page draws its own title bar so it follows the app theme
    // (the native title bar follows the OS theme and cannot be themed on Linux)
    frame: false,
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  // load the page immediately: the window must not sit blank while the
  // Python backend starts (it takes a few seconds). The renderer shows a
  // "Starting backend..." state and waits for the "backend-url" event.
  window.loadFile(path.join(__dirname, "../renderer/index.html"));
  const startRoot = root ?? lastProject();
  let url: string;
  if (conn.mode === "remote") {
    url = `ws://${conn.host}:${conn.port}`;
    console.log("[main] remote mode, connecting to", url);
    remoteReconnect = true; // keep trying if the link drops later
    reconnectDelay = 1000;
    try {
      await connectWs(url, conn.token);
    } catch (e) {
      // first attempt failed: the banner is up, the auto-reconnect loop (armed
      // by the close event) will keep trying in the background
      console.error("[main] remote connect failed:", e);
      window.webContents.send("backend-exit", null);
      return;
    }
    backendUrl = url;
    backendRoot = startRoot;
    window.webContents.send("backend-url", url);
    return;
  }
  try {
    url = await startBackend(startRoot);
  } catch (e) {
    console.error("[main] backend startup failed:", e);
    window.webContents.send("backend-exit", null);
    return;
  }
  backendUrl = url;
  backendRoot = startRoot;
  console.log("[main] backend ready:", url);
  await connectWs(url).catch((e) => console.error("[main] ws connect failed:", e));
  window.webContents.send("backend-url", url);
}

app.whenReady().then(() => {
  // no native menu bar (File/Edit/View/...): the app is fully driven by the UI
  Menu.setApplicationMenu(null);
  conn = loadConn();
  const args = process.argv.slice(app.isPackaged ? 1 : 2);
  let root: string | null = null;
  const i = args.indexOf("--root");
  if (i >= 0 && args[i + 1]) root = args[i + 1];
  createWindowAsync(root);

  ipcMain.handle("win-minimize", () => window?.minimize());
  ipcMain.handle("win-toggle-maximize", () => {
    if (!window) return false;
    if (window.isMaximized()) window.unmaximize();
    else window.maximize();
    return window.isMaximized();
  });
  ipcMain.handle("win-is-maximized", () => window?.isMaximized() ?? false);
  ipcMain.handle("win-close", () => window?.close());
  ipcMain.handle("backend-url", () => backendUrl);
  ipcMain.handle("backend-root", () => backendRoot);
  ipcMain.handle("app-version", () => app.getVersion());
  ipcMain.handle("ws-send", async (_e, payload: object) => {
    // queue RPCs that arrive while the (re)connect is in flight (boot,
    // restart, local<->remote switch) instead of failing immediately.
    // If the backend is down (and no connect/reconnect is in flight) fail fast.
    if (!connecting && !backendUp && !remoteReconnect) throw new Error("backend not connected");
    await wsReady;
    if (!ws || ws.readyState !== WebSocket.OPEN) throw new Error("backend not connected");
    ws.send(JSON.stringify(payload));
  });
  ipcMain.handle("pick-directory", async () => {
    if (!window) return null;
    const r = await dialog.showOpenDialog(window, { properties: ["openDirectory"] });
    return r.canceled ? null : r.filePaths[0];
  });
  ipcMain.handle("pick-files", async () => {
    if (!window) return [];
    const r = await dialog.showOpenDialog(window, { properties: ["openFile", "multiSelections"] });
    return r.canceled ? [] : r.filePaths;
  });
  ipcMain.handle("read-file", (_e, p: string): { content?: string; error?: string } => {
    try {
      const buf = fs.readFileSync(p);
      if (buf.includes(0)) return { error: "binary file" };
      return { content: buf.toString("utf8") };
    } catch (e: any) {
      return { error: String(e?.message ?? e) };
    }
  });
  ipcMain.handle("last-project", () => lastProject());
  ipcMain.handle("set-last-project", (_e, p: string) => {
    try { fs.writeFileSync(lastProjectFile(), p); } catch { /* ignore */ }
  });
  ipcMain.handle("get-connection", () => conn);
  ipcMain.handle("conn-mode", () => conn.mode);
  ipcMain.handle("set-connection", async (_e, c: object) => {
    reconnecting = true; // cleared when the old proc's exit handler fires (or on timeout)
    setTimeout(() => { reconnecting = false; }, 15000);
    return doSetConnection(c);
  });
  async function doSetConnection(c: any): Promise<object> {
    // switch local <-> remote. c: {mode, host, port, token}
    const next: Connection = {
      mode: c.mode === "remote" ? "remote" : "local",
      host: String(c.host || "127.0.0.1"),
      port: Number(c.port) || 8931,
      token: String(c.token || ""),
    };
    if (next.mode === conn.mode && next.host === conn.host && next.port === conn.port && next.token === conn.token)
      return { ok: true, reconnected: false };
    saveConn(next);
    // reconnect in place: drop the old connection, then start/connect the new one
    stopReconnect(); // the old socket's close event must not start a remote reconnect
    if (backend) {
      const old = backend;
      backend = null;
      old.kill("SIGTERM");
      old.stdout?.removeAllListeners();
      await new Promise<void>((res) => {
        const t = setTimeout(() => { try { old.kill("SIGKILL"); } catch { /* ignore */ } res(); }, 2000);
        old.once("exit", () => { clearTimeout(t); res(); });
      });
    }
    closeWs();
    backendUrl = null;
    try {
      let url: string;
      if (next.mode === "remote") {
        url = `ws://${next.host}:${next.port}`;
        remoteReconnect = true;
        reconnectDelay = 1000;
        await connectWs(url, next.token);
      } else {
        // switch to local: start the bundled backend with the project the GUI
        // has open (c.root, sent by the renderer) so it is ready to re-open it
        const root = (typeof c.root === "string" && c.root) || backendRoot || lastProject();
        url = await startBackend(root);
        backendRoot = root;
        // startBackend only spawns the process and reads its WS_URL; the socket
        // must be opened here too, or every RPC fails with "backend not connected"
        await connectWs(url);
      }
      backendUrl = url;
      if (window && !window.isDestroyed()) window.webContents.send("backend-url", url);
      return { ok: true, reconnected: true, url };
    } catch (e: any) {
      if (window && !window.isDestroyed()) window.webContents.send("backend-exit", null);
      return { ok: false, error: String(e?.message ?? e) };
    }
  }
  ipcMain.handle("restart-backend", async (_e, root: string | null) => {
    // restart the backend in place (same window): kill the old proc, start a new one.
    // If no project was ever opened (e.g. the first start failed), fall back to
    // the last project so the new backend still starts with a root.
    const r = root ?? backendRoot ?? lastProject();
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
    stopReconnect();
    closeWs();
    try {
      let url: string;
      if (conn.mode === "remote") {
        // remote mode: "restart" = reconnect to the remote server
        url = `ws://${conn.host}:${conn.port}`;
        remoteReconnect = true;
        reconnectDelay = 1000;
        await connectWs(url, conn.token);
      } else {
        url = await startBackend(r);
        backendRoot = r;
        // same as doSetConnection: startBackend does not open the socket
        await connectWs(url);
      }
      backendUrl = url;
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

function killBackend() {
  if (quitting) return;
  quitting = true;
  // local mode: best-effort ask the backend to stop the running turn, then kill
  // the process. Remote mode: the server keeps running on its own machine -
  // closing the GUI must NOT cancel the user's turn, so just drop the socket.
  if (backend) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      try { ws.send(JSON.stringify({ id: 0, method: "cancel", params: {} })); } catch { /* ignore */ }
    }
  } else {
    stopReconnect();
    closeWs();
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
