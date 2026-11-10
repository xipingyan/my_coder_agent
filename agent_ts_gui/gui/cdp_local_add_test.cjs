// Regression test: "+ Local project..." in the Projects dialog must add the
// picked directory to the project list (the name input modal works in Electron,
// where window.prompt() silently returns null).
const { spawn } = require("child_process");
const http = require("http");
const electron = require("electron");
const WebSocket = require("ws");

const ROOT_A = process.env.TEST_ROOT_A || "/tmp/proj_a";
const ROOT_B = process.env.TEST_ROOT_B || "/tmp/proj_b";

const proc = spawn(electron, [".", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=9346",
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
    http.get("http://127.0.0.1:9346/json", (res) => {
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

  for (let i = 0; i < 120; i++) {
    const lbl = await evalJs("document.getElementById('root-label') && document.getElementById('root-label').textContent");
    if (lbl && (lbl.includes(ROOT_A) || lbl.includes("proj_a"))) break;
    await wait(500);
  }

  // mock the native folder picker, open the dialog, click "+ Local project..."
  await evalJs(`localStorage.setItem("coder-agent.projects", JSON.stringify([]))`);
  await evalJs(`window.api.pickDirectory = () => Promise.resolve(${JSON.stringify(ROOT_B)})`);
  await evalJs("document.getElementById('projects-btn').click()");
  await wait(300);
  await evalJs("document.querySelector('#modal .add-local').click()");
  await wait(300);

  // the name input modal must be up (previously window.prompt returned null and
  // the project was silently dropped)
  const promptUp = await evalJs("!!document.querySelector('.modal-backdrop.stacked .pinput')");
  check("name input modal appears after picking a folder", promptUp);

  // accept the pre-filled name (Enter)
  await evalJs(`document.querySelector('.modal-backdrop.stacked .pinput').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))`);
  await wait(300);
  const rows = await evalJs("[...document.querySelectorAll('#modal .projrow .pname')].map(e => e.textContent)");
  check("picked project appears in the list", rows.some((n) => n.includes("proj_b")));
  const mode = await evalJs("[...document.querySelectorAll('#modal .projrow .pmode')].map(e => e.textContent)");
  check("project is saved as local", mode.some((m) => m === "local"));

  // Open it: root label switches to B
  await evalJs(`[...document.querySelectorAll('#modal .projrow')].find(r => r.textContent.includes('proj_b')).click()`);
  await wait(200);
  await evalJs("document.querySelector('#modal .open').click()");
  let switched = false;
  for (let i = 0; i < 40; i++) {
    const lbl = await evalJs("document.getElementById('root-label').textContent");
    if (lbl && lbl.includes(ROOT_B)) { switched = true; break; }
    await wait(500);
  }
  check("Open switches to the new local project", switched);

  ws.close();
  proc.kill("SIGTERM");
  await wait(1500);
  if (proc.exitCode === null) proc.kill("SIGKILL");
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); proc.kill("SIGKILL"); process.exit(1); });
