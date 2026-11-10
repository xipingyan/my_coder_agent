// Probe: click "+ Local project..." and see whether the native folder dialog
// opens (app blocks) or the click silently does nothing.
const { spawn } = require("child_process");
const http = require("http");
const electron = require("electron");
const WebSocket = require("ws");

const proc = spawn(electron, [".", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=9344",
  "--root", "/tmp/proj_a"], {
  cwd: __dirname,
  env: { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: "1" },
  stdio: ["ignore", "pipe", "pipe"],
});
proc.stdout.on("data", (d) => process.stdout.write(`[app] ${d}`));
proc.stderr.on("data", (d) => process.stderr.write(`[err] ${d}`));

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function getWsUrl() {
  return new Promise((resolve) => {
    http.get("http://127.0.0.1:9344/json", (res) => {
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
  const evalJs = async (expr, timeoutMs = 5000) => {
    const p = send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    const t = new Promise((r) => setTimeout(() => r({ timeout: true }), timeoutMs));
    const r = await Promise.race([p, t]);
    if (r && r.timeout) return { __timeout: true };
    return r.result && r.result.result ? r.result.result.value : undefined;
  };

  // wait for boot
  for (let i = 0; i < 120; i++) {
    const lbl = await evalJs("document.getElementById('root-label') && document.getElementById('root-label').textContent");
    if (lbl && lbl.includes("proj_a")) break;
    await wait(500);
  }

  // open projects dialog, click add-local, then check if the renderer is
  // blocked (native dialog open) by probing with a short-timeout eval
  await evalJs(`localStorage.setItem("coder-agent.projects", JSON.stringify([]))`);
  await evalJs("document.getElementById('projects-btn').click()");
  await wait(300);

  const click = await evalJs(`document.querySelector('#modal .add-local').click(); "clicked"`, 8000);
  console.log("click result:", click);
  await wait(1500);
  const probe = await evalJs("1+1", 3000);
  console.log("renderer probe after click:", probe, "(object with __timeout => renderer blocked => native dialog is open)");

  ws.close();
  proc.kill("SIGKILL");
  process.exit(0);
})().catch((e) => { console.error(e); proc.kill("SIGKILL"); process.exit(1); });
