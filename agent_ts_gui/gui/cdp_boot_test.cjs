// Measure boot timing: page visible vs backend ready vs project opened.
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
const t0 = Date.now();
const ts = (label) => console.log(`t=${Date.now() - t0}ms  ${label}`);

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

(async () => {
  let url = null;
  for (let i = 0; i < 100; i++) { url = await getWsUrl(); if (url) break; await wait(200); }
  if (!url) throw new Error("no devtools url");
  ts("devtools page target appears");

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
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
    return r.result && r.result.result ? r.result.result.value : undefined;
  };

  // 1) page visible: toolbar exists in DOM; also record the first state label seen
  let seen = false;
  let firstState = null;
  for (let i = 0; i < 150; i++) {
    const r = await evalJs("JSON.stringify({t: !!document.getElementById('toolbar'), s: document.getElementById('state-label') && document.getElementById('state-label').textContent})");
    if (r) {
      const o = JSON.parse(r);
      if (!firstState && o.s) firstState = o.s;
      if (o.t) { seen = true; break; }
    }
    await wait(100);
  }
  ts(`page DOM ready (toolbar visible) = ${seen}`);
  ts(`first state label observed: ${firstState}`);

  // 3) if no project opens, how fast does the banner appear?
  let bannerAt = null;
  for (let i = 0; i < 100; i++) {
    if (await evalJs("!!document.getElementById('backend-banner')")) { bannerAt = Date.now() - t0; break; }
    await wait(200);
  }
  if (bannerAt !== null) {
    ts(`banner appeared at t=${bannerAt}ms`);
    // click Restart to verify the recovery path (flaky backend: first start fails, restart succeeds)
    await evalJs("document.querySelector('#backend-banner .restart') && document.querySelector('#backend-banner .restart').click()");
    ts("clicked Restart");
  }

  // 4) project opened: root-label no longer "(no project)"
  let opened = false;
  for (let i = 0; i < 120; i++) {
    const lbl = await evalJs("document.getElementById('root-label') && document.getElementById('root-label').textContent");
    if (lbl && lbl !== "(no project)") { opened = true; ts(`project opened: ${lbl.trim()}`); break; }
    await wait(500);
  }
  if (!opened) ts("project NOT opened within 60s");

  // 4) banner must NOT be present (no false alarm)
  const banner = await evalJs("!!document.getElementById('backend-banner')");
  ts(`backend banner present (should be false): ${banner}`);

  ws.close();
  proc.kill("SIGTERM");
  await wait(1500);
  if (proc.exitCode === null) proc.kill("SIGKILL");
  process.exit(0);
})().catch((e) => { console.error(e); proc.kill("SIGKILL"); process.exit(1); });
