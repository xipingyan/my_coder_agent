// Regression test: in the Projects dialog, selecting / double-clicking a
// different saved project must actually open it (root label changes).
const { spawn } = require("child_process");
const http = require("http");
const electron = require("electron");
const WebSocket = require("ws");

const ROOT_A = process.env.TEST_ROOT_A || "/tmp/proj_a";
const ROOT_B = process.env.TEST_ROOT_B || "/tmp/proj_b";

const proc = spawn(electron, [".", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=9343",
  "--root", ROOT_A], {
  cwd: __dirname,
  env: { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: "1" },
  stdio: ["ignore", "pipe", "pipe"],
});
proc.stdout.on("data", (d) => process.stdout.write(`[app] ${d}`));
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

  // wait for project A to open
  let opened = false;
  for (let i = 0; i < 120; i++) {
    const lbl = await evalJs("document.getElementById('root-label') && document.getElementById('root-label').textContent");
    if (lbl && (lbl.includes(ROOT_A) || lbl.includes("proj_a"))) { opened = true; break; }
    await wait(500);
  }
  check("project A opened on boot", opened);

  // open the projects dialog, add project B (directly via localStorage + dialog render is not
  // accessible; use the dialog's own add flow is interactive, so seed localStorage and
  // re-open the dialog)
  await evalJs(`localStorage.setItem("coder-agent.projects",
    JSON.stringify([
      { name: "proj_a", mode: "remote", path: ${JSON.stringify(ROOT_A)} },
      { name: "proj_b", mode: "remote", path: ${JSON.stringify(ROOT_B)} }
    ]))`);
  await evalJs("document.getElementById('projects-btn').click()");
  await wait(300);
  const rows = await evalJs("[...document.querySelectorAll('#modal .projrow .pname')].map(e => e.textContent)");
  check("dialog lists both projects", rows.some((n) => n.includes("proj_a")) && rows.some((n) => n.includes("proj_b")));

  // 1) single click selects proj_b
  await evalJs(`[...document.querySelectorAll('#modal .projrow')].find(r => r.textContent.includes('proj_b')).click()`);
  await wait(200);
  const sel = await evalJs("document.querySelector('#modal .projrow.sel .pname') && document.querySelector('#modal .projrow.sel .pname').textContent");
  check("single click selects proj_b", !!sel && sel.includes("proj_b"));

  // 2) the Open button opens proj_b
  await evalJs("document.querySelector('#modal .open').click()");
  let switched = false;
  for (let i = 0; i < 40; i++) {
    const lbl = await evalJs("document.getElementById('root-label').textContent");
    if (lbl && lbl.includes(ROOT_B)) { switched = true; break; }
    await wait(500);
  }
  check("Open button switches to project B", switched);
  check("dialog closed after Open", !(await evalJs("!!document.querySelector('#modal-backdrop.show')")));

  // 3) double-clicking a row (from a fresh dialog) also opens it
  await evalJs("document.getElementById('projects-btn').click()");
  await wait(300);
  await evalJs(`[...document.querySelectorAll('#modal .projrow')].find(r => r.textContent.includes('proj_a')).dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))`);
  let back = false;
  for (let i = 0; i < 40; i++) {
    const lbl = await evalJs("document.getElementById('root-label').textContent");
    if (lbl && lbl.includes(ROOT_A)) { back = true; break; }
    await wait(500);
  }
  check("double-click switches back to project A", back);

  ws.close();
  proc.kill("SIGTERM");
  await wait(1500);
  if (proc.exitCode === null) proc.kill("SIGKILL");
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); proc.kill("SIGKILL"); process.exit(1); });
