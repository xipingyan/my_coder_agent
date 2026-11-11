// Shared helpers for the CDP smoke tests (cdp_*_test.cjs).
//
// Two rules these exist to enforce:
//
// 1. NEVER kill by process name (pkill/taskkill "electron"). The developer
//    may be running the same app right now (this repo is often edited through
//    its own GUI). Kill ONLY the pid this test spawned - and on Windows the
//    whole process tree, because proc.kill() there only kills the main
//    electron process and leaves zygote/gpu/renderer orphans behind.
//
// 2. Every test instance runs with its own --user-data-dir (a fresh temp
//    dir), so it cannot share localStorage / last-project / connection.json
//    with the developer's running instance.
//
// Usage:
//   const { spawnApp, killApp, getWsUrl, connectCdp } = require("./cdp_test_lib");
//   const app = spawnApp({ root: "/tmp/proj_a" });
//   ...
//   await killApp(app);

const { spawn, execSync } = require("child_process");
const http = require("http");
const fs = require("fs");
const os = require("os");
const path = require("path");
const electron = require("electron");
const WebSocket = require("ws");

const IS_WIN = process.platform === "win32";
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// --------------------------------------------------------------- spawn
// Spawns the app with an isolated user-data-dir. Returns { proc, pid,
// userDataDir, port }.
function spawnApp(opts = {}) {
  const port = opts.port || (9300 + Math.floor(Math.random() * 100));
  // Caller may pre-populate a specific dir (e.g. connection.json for remote
  // mode); otherwise a fresh temp dir isolates the instance.
  const userDataDir = opts.userDataDir || fs.mkdtempSync(path.join(os.tmpdir(), "agent-cdp-ud-"));
  fs.mkdirSync(userDataDir, { recursive: true });
  const args = [
    ".",
    "--no-sandbox",
    "--disable-gpu",
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${userDataDir}`,
  ];
  if (opts.root) args.push("--root", opts.root);
  const proc = spawn(electron, args, {
    cwd: __dirname,
    env: { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: "1" },
    stdio: ["ignore", "pipe", "pipe"],
    // POSIX: own process group so kill(-pid) reaches the whole tree.
    detached: !IS_WIN,
  });
  let log = "";
  proc.stdout.on("data", (d) => { log += d; if (opts.onLog) opts.onLog(d); });
  proc.stderr.on("data", (d) => { log += d; if (opts.onLog) opts.onLog(d); });
  return { proc, pid: proc.pid, userDataDir, port, getLog: () => log };
}

// ---------------------------------------------------------------- kill
// Kills ONLY the spawned pid (and its children on Windows). Never by name.
// SIGTERM first, SIGKILL after `graceMs`. Resolves when the proc is gone.
async function killApp(app, graceMs = 1500) {
  const { proc, pid } = app;
  if (proc.exitCode !== null || proc.signalCode) return;
  try {
    if (IS_WIN) {
      // /T = whole tree, /F = force. Only touches our pid.
      execSync(`taskkill /PID ${pid} /T /F`, { stdio: "ignore" });
    } else {
      try { process.kill(-pid, "SIGTERM"); } catch { try { proc.kill("SIGTERM"); } catch {} }
    }
  } catch { /* already dead */ }
  const deadline = Date.now() + graceMs;
  while (proc.exitCode === null && proc.signalCode === undefined && Date.now() < deadline) {
    await wait(100);
  }
  if (proc.exitCode === null && proc.signalCode === undefined) {
    try {
      if (IS_WIN) execSync(`taskkill /PID ${pid} /T /F`, { stdio: "ignore" });
      else { try { process.kill(-pid, "SIGKILL"); } catch { try { proc.kill("SIGKILL"); } catch {} } }
    } catch { /* ignore */ }
  }
  try { fs.rmSync(app.userDataDir, { recursive: true, force: true }); } catch { /* ignore */ }
}

// --------------------------------------------------------- devtools url
// Polls the CDP /json endpoint for the page's webSocketDebuggerUrl.
// path is "/json" (page targets) or "/json/version" (browser endpoint).
function getWsUrl(port, cdpPath = "/json", tries = 100, intervalMs = 200) {
  return (async () => {
    for (let i = 0; i < tries; i++) {
      const url = await new Promise((resolve) => {
        const req = http.get(`http://127.0.0.1:${port}${cdpPath}`, (res) => {
          let body = "";
          res.on("data", (d) => { body += d; });
          res.on("end", () => {
            try {
              const list = JSON.parse(body);
              if (Array.isArray(list)) {
                const page = list.find((t) => t.type === "page");
                resolve(page ? page.webSocketDebuggerUrl : null);
              } else {
                resolve(list.webSocketDebuggerUrl || null);
              }
            } catch { resolve(null); }
          });
        });
        req.on("error", () => resolve(null));
      });
      if (url) return url;
      await wait(intervalMs);
    }
    return null;
  })();
}

// ------------------------------------------------------- cdp connection
// Connects to the page and returns { ws, evalJs, send, close }.
// evalJs(expr, timeoutMs) - resolves the evaluated value; a blocked renderer
// (e.g. a native dialog) yields { __timeout: true } instead of hanging.
function connectCdp(wsUrl) {
  const ws = new WebSocket(wsUrl, { perMessageDeflate: false });
  let id = 0;
  const pending = new Map();
  ws.on("message", (buf) => {
    let msg;
    try { msg = JSON.parse(buf); } catch { return; }
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  });
  const send = (method, params = {}) => new Promise((resolve) => {
    id += 1;
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });
  const opened = new Promise((r) => ws.on("open", r));
  const evalJs = async (expr, timeoutMs = 8000) => {
    const p = send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    const t = new Promise((r) => setTimeout(() => r({ __timeout: true }), timeoutMs));
    const r = await Promise.race([p, t]);
    if (r && r.__timeout) return { __timeout: true };
    return r.result && r.result.result ? r.result.result.value : undefined;
  };
  return {
    ws,
    send,
    evalJs,
    opened,
    close: () => { try { ws.close(); } catch { /* ignore */ } },
  };
}

module.exports = { spawnApp, killApp, getWsUrl, connectCdp, wait, IS_WIN };
