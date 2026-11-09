// Smoke test: the Projects dialog opens, lists saved projects, and the
// remote dir browser works against the local (local-server) backend.
const { spawn } = require("child_process");
const http = require("http");
const electron = require("electron");
const WebSocket = require("ws");

const proc = spawn(electron, [".", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=9342",
  "--root", process.env.TEST_ROOT || "/home/xiping/mygitee/my_coder_agent/agent_ts_gui"], {
  cwd: __dirname,
  env: { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: "1" },
  stdio: ["ignore", "pipe", "pipe"],
});
proc.stdout.on("data", (d) => process.stdout.write(`[app] ${d}`));
proc.stderr.on("data", () => {});

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function getWsUrl() {
  return new Promise((resolve) => {
    http.get("http://127.0.0.1:9342/json", (res) => {
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

(async () => {
  let url = null;
  for (let i = 0; i < 100; i++) { url = await getWsUrl(); if (url) break; await wait(200); }
  if (!url) throw new Error("no devtools url");

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

  // wait for the project to open
  let opened = false;
  for (let i = 0; i < 120; i++) {
    const lbl = await evalJs("document.getElementById('root-label') && document.getElementById('root-label').textContent");
    if (lbl && lbl !== "(no project)") { opened = true; break; }
    await wait(500);
  }
  check("project opened on boot", opened);

  // 1) Projects dialog opens with the expected buttons
  await evalJs("document.getElementById('projects-btn').click()");
  await wait(300);
  const dlg = await evalJs(`JSON.stringify({
    show: !!document.querySelector('#modal-backdrop.show'),
    head: document.querySelector('#modal .head') && document.querySelector('#modal .head').textContent,
    btns: [...document.querySelectorAll('#modal .btnrow button')].map(b => b.textContent),
  })`);
  const o = JSON.parse(dlg);
  check("projects dialog visible", o.show);
  check("dialog title", o.head === "Projects");
  check("dialog buttons", o.btns.join(",") === "+ Local project...,+ Remote project...,Rename,Delete");

  // 2) the auto-opened root is remembered as a saved project (via openProject)
  const rows = await evalJs("[...document.querySelectorAll('#modal .projrow .pname')].map(e => e.textContent)");
  check("auto-opened project is in the list", rows.some((n) => n.includes("agent_ts_gui")));

  // 3) remote dir browser: list the backend machine's home dir
  await evalJs("document.querySelector('#modal .yes').click()"); // close projects dialog
  await wait(200);
  const browse = await evalJs(`(async () => {
    // reuse the internal dialog via the button path: open projects, click + Remote project...
    document.getElementById('projects-btn').click();
    await new Promise(r => setTimeout(r, 200));
    document.querySelector('#modal .add-remote').click();
    await new Promise(r => setTimeout(r, 1500));
    const head = document.querySelector('#modal .head') && document.querySelector('#modal .head').textContent;
    const entries = [...document.querySelectorAll('#modal .dirent')].slice(0, 3).map(e => e.textContent.trim());
    document.querySelector('#modal .no').click(); // cancel
    return JSON.stringify({ head, entries });
  })()`);
  const b = JSON.parse(browse);
  check("remote browser title", b.head.includes("backend machine"));
  check("remote browser lists entries", b.entries.length > 0);

  ws.close();
  proc.kill("SIGTERM");
  await wait(1500);
  if (proc.exitCode === null) proc.kill("SIGKILL");
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); proc.kill("SIGKILL"); process.exit(1); });
