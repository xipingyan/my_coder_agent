// Theme smoke test: default light, switch dark/solarized via Settings > Appearance, persistence across reload.
const { spawn } = require("child_process");
const http = require("http");
const electron = require("electron");
const WebSocket = require("ws");

const proc = spawn(electron, [".", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=9343",
  "--root", process.env.TEST_ROOT || "/home/xiping/mygitee/my_coder_agent/agent_ts_gui"], {
  cwd: __dirname,
  env: { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: "1" },
  stdio: ["ignore", "pipe", "pipe"],
});
proc.stdout.on("data", () => {});
proc.stderr.on("data", (d) => process.stderr.write(`[app] ${d}`));

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
let failed = false;
function check(name, cond) {
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}`);
  if (!cond) failed = true;
}

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
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
    return r.result && r.result.result ? r.result.result.value : undefined;
  };

  // wait for toolbar
  for (let i = 0; i < 150; i++) {
    if (await evalJs("!!document.getElementById('toolbar')")) break;
    await wait(100);
  }

  const bg = () => evalJs("getComputedStyle(document.body).backgroundColor");
  const dt = () => evalJs("document.documentElement.dataset.theme");

  // 1) default = light
  check("default data-theme empty", (await dt()) === undefined);
  check("default body bg rgb(240,240,240)", (await bg()) === "rgb(240, 240, 240)");

  // 2) open settings, go to appearance, pick dark
  await evalJs("document.getElementById('settings-btn').click()");
  await wait(400);
  check("settings modal shown", await evalJs("!!document.querySelector('.settings')"));
  check("appearance tab exists", await evalJs("!!document.querySelector('.settings .tabs button[data-p=\"appearance\"]')"));
  await evalJs("document.querySelector('.settings .tabs button[data-p=\"appearance\"]').click()");
  await wait(100);
  await evalJs("const s=document.querySelector('.theme-sel'); s.value='dark'; s.dispatchEvent(new Event('change'));");
  await wait(200);
  check("dark data-theme", (await dt()) === "dark");
  check("dark body bg rgb(30,30,30)", (await bg()) === "rgb(30, 30, 30)");
  check("dark panel rgb(37,37,38)",
    (await evalJs("getComputedStyle(document.getElementById('toolbar')).backgroundColor")) === "rgb(37, 37, 38)");
  check("dark persisted", (await evalJs("localStorage.getItem('agent_ts_gui.theme')")) === "dark");

  // 3) solarized
  await evalJs("const s=document.querySelector('.theme-sel'); s.value='solarized'; s.dispatchEvent(new Event('change'));");
  await wait(200);
  check("solarized data-theme", (await dt()) === "solarized");
  check("solarized body bg rgb(238,232,213)", (await bg()) === "rgb(238, 232, 213)");

  // 4) back to light, close dialog
  await evalJs("const s=document.querySelector('.theme-sel'); s.value='light'; s.dispatchEvent(new Event('change'));");
  await wait(200);
  check("light data-theme empty", (await dt()) === undefined);
  check("light body bg rgb(240,240,240)", (await bg()) === "rgb(240, 240, 240)");
  await evalJs("document.querySelector('.settings .close-x').click()");
  await wait(200);

  // 5) persistence across reload: set dark, reload, check
  await evalJs("localStorage.setItem('agent_ts_gui.theme','dark');");
  await send("Page.reload");
  for (let i = 0; i < 100; i++) {
    if (await evalJs("!!document.getElementById('toolbar')")) break;
    await wait(200);
  }
  check("after reload data-theme dark", (await dt()) === "dark");
  check("after reload body bg rgb(30,30,30)", (await bg()) === "rgb(30, 30, 30)");

  ws.close();
  proc.kill("SIGTERM");
  await wait(1500);
  if (proc.exitCode === null) proc.kill("SIGKILL");
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); proc.kill("SIGKILL"); process.exit(1); });
