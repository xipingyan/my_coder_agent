// CDP smoke test for resizable splitters.
const { spawn } = require("child_process");
const path = require("path");
const WebSocket = require("ws");

const http = require("http");
const electron = require("electron");
const proc = spawn(electron, [".", "--no-sandbox", "--remote-debugging-port=9341"], {
  cwd: __dirname,
  env: { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: "1" },
  stdio: ["ignore", "pipe", "pipe"],
});
proc.stdout.on("data", () => {});
proc.stderr.on("data", () => {});

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function getWsUrl() {
  return new Promise((resolve) => {
    http.get("http://127.0.0.1:9341/json/version", (res) => {
      let body = "";
      res.on("data", (d) => { body += d; });
      res.on("end", () => {
        try { resolve(JSON.parse(body).webSocketDebuggerUrl); } catch { resolve(null); }
      });
    }).on("error", () => resolve(null));
  });
}

(async () => {
  let url = null;
  for (let i = 0; i < 50; i++) { url = await getWsUrl(); if (url) break; await wait(200); }
  if (!url) throw new Error("no devtools url");

  const ws = new WebSocket(url, { perMessageDeflate: false });
  let id = 0;
  const pending = new Map();
  ws.on("message", (buf) => {
    const msg = JSON.parse(buf);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  });
  const send = (method, params = {}) => new Promise((res) => {
    const mid = ++id; pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  await new Promise((r) => ws.on("open", r));

  // find the main app page target
  let pageId = null;
  for (let i = 0; i < 50 && !pageId; i++) {
    const { targetInfos } = (await send("Target.getTargets")).result;
    const t = targetInfos.find((t) => t.type === "page" && /index\.html/.test(t.url));
    if (t) pageId = t.targetId;
    else await wait(200);
  }
  if (!pageId) throw new Error("no page target");
  const attach = await send("Target.attachToTarget", { targetId: pageId, flatten: true });
  if (!attach.result || !attach.result.sessionId) throw new Error("attach failed: " + JSON.stringify(attach).slice(0, 300));
  const sessionId = attach.result.sessionId;

  const evalIn = (expression) => new Promise((res) => {
    const mid = ++id;
    const handler = (buf) => {
      const msg = JSON.parse(buf);
      if (msg.id === mid) { ws.off("message", handler); res(msg); }
    };
    ws.on("message", handler);
    ws.send(JSON.stringify({ id: mid, method: "Runtime.evaluate", params: { expression, returnByValue: true }, sessionId }));
  });
  const get = async (expr) => {
    const msg = await evalIn(expr);
    const v = msg.result && msg.result.result && msg.result.result.value;
    if (msg.result && msg.result.exceptionDetails) throw new Error("page: " + JSON.stringify(msg.result.exceptionDetails).slice(0, 300));
    return v;
  };

  for (let i = 0; i < 50; i++) {
    if (await get("!!document.getElementById('split-left')")) break;
    await wait(200);
  }

  const before = await get("document.getElementById('left').getBoundingClientRect().width");
  console.log("left before:", before);

  // drag split-left +100px
  await evalIn(`(() => {
    const s = document.getElementById('split-left');
    const r = s.getBoundingClientRect();
    const x = r.left + r.width/2, y = r.top + 100;
    s.dispatchEvent(new MouseEvent('mousedown', { clientX: x, clientY: y, button: 0, bubbles: true }));
    for (let i = 1; i <= 10; i++)
      window.dispatchEvent(new MouseEvent('mousemove', { clientX: x + i*10, clientY: y, bubbles: true }));
    window.dispatchEvent(new MouseEvent('mouseup', { clientX: x + 100, clientY: y, bubbles: true }));
  })()`);
  await wait(100);
  const after = await get("document.getElementById('left').getBoundingClientRect().width");
  console.log("left after +100 drag:", after);

  // drag split-right +50px (right panel grows when moving left... right splitter: drag left to grow)
  const rightBefore = await get("document.getElementById('right').getBoundingClientRect().width");
  await evalIn(`(() => {
    const s = document.getElementById('split-right');
    const r = s.getBoundingClientRect();
    const x = r.left + r.width/2, y = r.top + 100;
    s.dispatchEvent(new MouseEvent('mousedown', { clientX: x, clientY: y, button: 0, bubbles: true }));
    for (let i = 1; i <= 5; i++)
      window.dispatchEvent(new MouseEvent('mousemove', { clientX: x - i*10, clientY: y, bubbles: true }));
    window.dispatchEvent(new MouseEvent('mouseup', { clientX: x - 50, clientY: y, bubbles: true }));
  })()`);
  await wait(100);
  const rightAfter = await get("document.getElementById('right').getBoundingClientRect().width");
  console.log("right before:", rightBefore, "after -50 drag:", rightAfter);

  const stored = await get("localStorage.getItem('coder-agent-panel-widths')");
  console.log("stored:", stored);

  // double-click reset
  await evalIn("document.getElementById('split-left').dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))");
  await wait(100);
  const reset = await get("document.getElementById('left').getBoundingClientRect().width");
  console.log("left after dblclick reset:", reset);

  const ok = Math.abs(after - (before + 100)) < 2
    && Math.abs(rightAfter - (rightBefore + 50)) < 2
    && reset === 260;
  console.log(ok ? "SPLIT TEST PASS" : "SPLIT TEST FAIL");
  proc.kill("SIGTERM");
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.error(e); proc.kill("SIGTERM"); process.exit(1); });
