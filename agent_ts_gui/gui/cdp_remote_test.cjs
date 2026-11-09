// Smoke test: remote-mode connection. A standalone coder-agent-server is
// already listening on 127.0.0.1:8931 (started separately, like on a real
// remote machine). The GUI is pointed at it via connection.json (mode
// "remote") and must NOT spawn its own backend.
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");
const os = require("os");
const path = require("path");
const electron = require("electron");
const WebSocket = require("ws");

const ROOT = process.env.TEST_ROOT || "/home/xiping/mygitee/my_coder_agent/agent_ts_gui";
const PORT = 8931;

// point the app at the remote server (userData dir of the unpacked dev app)
const userData = path.join(os.homedir(), ".config", "Coder Agent");
const connFile = path.join(userData, "connection.json");
const backup = connFile + ".bak";
if (fs.existsSync(connFile)) fs.copyFileSync(connFile, backup);
fs.writeFileSync(connFile, JSON.stringify({ mode: "remote", host: "127.0.0.1", port: PORT, token: "" }));

const proc = spawn(electron, [".", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=9343",
  "--root", ROOT], {
  cwd: __dirname,
  env: { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: "1" },
  stdio: ["ignore", "pipe", "pipe"],
});
let appLog = "";
proc.stdout.on("data", (d) => { appLog += d; process.stdout.write(`[app] ${d}`); });
proc.stderr.on("data", () => {});

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function getWsUrl() {
  return new Promise((resolve) => {
    http.get("http://127.0.0.1:9343/json", (res) => {
      let body = "";
      res.on("data", (d) => { body += d; });
      res.on("end", () => {
        try {
          const list = JSON.parse(body);
          const page = list.find((t) => t.type === "page");
          resolve(page ? page.webSocketDebuggerUrl : null);
        } catch { resolve(null); }
      });
    }).on("error", () => resolve(null));
  });
}

let failed = false;
function check(label, ok) {
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}`);
  if (!ok) failed = true;
}

function cleanup() {
  try { fs.unlinkSync(connFile); if (fs.existsSync(backup)) fs.renameSync(backup, connFile); } catch { /* ignore */ }
}

(async () => {
  let url = null;
  for (let i = 0; i < 100; i++) { url = await getWsUrl(); if (url) break; await wait(200); }
  if (!url) { cleanup(); throw new Error("no devtools url"); }

  const ws = new WebSocket(url, { perMessageDeflate: false });
  let id = 0;
  const pending = new Map();
  ws.on("message", (buf) => {
    const msg = JSON.parse(buf);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  });
  const send = (method, params = {}) => new Promise((resolve) => {
    id += 1;
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });
  await new Promise((r) => ws.on("open", r));
  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    return r.result && r.result.result ? r.result.result.value : undefined;
  };

  try {
    // 1) the main process must connect to the remote server, not spawn one
    let connected = false;
    for (let i = 0; i < 60; i++) {
      if (appLog.includes(`ws://127.0.0.1:${PORT}`)) { connected = true; break; }
      await wait(500);
    }
    check("main connected to remote server", connected);
    check("no local backend spawned", !appLog.includes("starting backend"));

    // 2) project opens through the remote connection
    let opened = false;
    for (let i = 0; i < 120; i++) {
      const lbl = await evalJs("document.getElementById('root-label') && document.getElementById('root-label').textContent");
      if (lbl && lbl !== "(no project)") { opened = true; break; }
      await wait(500);
    }
    check("project opened via remote server", opened);

    // 3) connection tab shows remote mode
    await evalJs("document.getElementById('settings-btn').click()");
    await wait(300);
    await evalJs("[...document.querySelectorAll('#modal .tabs button')].find(b => b.dataset.p === 'connection').click()");
    await wait(500);
    const conn = await evalJs(`JSON.stringify({
      mode: document.querySelector('#modal .c-mode') && document.querySelector('#modal .c-mode').value,
      host: document.querySelector('#modal .c-host') && document.querySelector('#modal .c-host').value,
      port: document.querySelector('#modal .c-port') && document.querySelector('#modal .c-port').value,
    })`);
    const c = JSON.parse(conn);
    check("connection tab shows remote", c.mode === "remote");
    check("connection tab host/port", c.host === "127.0.0.1" && String(c.port) === String(PORT));

    // 4) list_dir goes over the remote connection
    const ls = await evalJs(`(async () => {
      // quick direct RPC via the app's internal call() is not exposed; use the projects dialog browser instead
      document.getElementById('projects-btn').click();
      await new Promise(r => setTimeout(r, 200));
      document.querySelector('#modal .add-remote').click();
      await new Promise(r => setTimeout(r, 1500));
      const n = document.querySelectorAll('#modal .dirent').length;
      document.querySelector('#modal .no').click();
      await new Promise(r => setTimeout(r, 200));
      document.querySelector('#modal .yes').click();
      return n;
    })()`);
    check("remote dir browser works over remote connection", ls > 0);
  } finally {
    ws.close();
    proc.kill("SIGTERM");
    await wait(1500);
    if (proc.exitCode === null) proc.kill("SIGKILL");
    cleanup();
    process.exit(failed ? 1 : 0);
  }
})().catch((e) => { console.error(e); proc.kill("SIGKILL"); cleanup(); process.exit(1); });
