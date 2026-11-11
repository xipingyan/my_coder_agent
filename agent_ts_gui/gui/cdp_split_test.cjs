// CDP smoke test for resizable splitters.
const { spawnApp, killApp, getWsUrl, wait } = require("./cdp_test_lib.cjs");
const WebSocket = require("ws");

const app = spawnApp({});

(async () => {
  let url = null;
  for (let i = 0; i < 50; i++) { url = await getWsUrl(app.port, "/json/version"); if (url) break; await wait(200); }
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

  // the handle center must track the cursor exactly; the pane edge is
  // 2px (the pane border) behind the handle center.
  const cx = (id) => { const r = document.getElementById(id).getBoundingClientRect(); return r.left + r.width / 2; };
  const beforeCenter = await get("((s)=>{const r=s.getBoundingClientRect();return r.left+r.width/2})(document.getElementById('split-left'))");
  console.log("split-left center before:", beforeCenter);

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
  const afterCenter = await get("((s)=>{const r=s.getBoundingClientRect();return r.left+r.width/2})(document.getElementById('split-left'))");
  console.log("split-left center after +100 drag:", afterCenter);

  // drag split-right +50px (right splitter: drag left to grow)
  const rightBeforeCenter = await get("((s)=>{const r=s.getBoundingClientRect();return r.left+r.width/2})(document.getElementById('split-right'))");
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
  const rightAfterCenter = await get("((s)=>{const r=s.getBoundingClientRect();return r.left+r.width/2})(document.getElementById('split-right'))");
  console.log("split-right center before:", rightBeforeCenter, "after -50 drag:", rightAfterCenter);

  // drag the horizontal input splitter up by 40px (zone grows)
  const inputBefore = await get("document.getElementById('input-zone').getBoundingClientRect().height");
  await evalIn(`(() => {
    const s = document.getElementById('split-input');
    const r = s.getBoundingClientRect();
    const x = r.left + r.width/2, y = r.top + r.height/2;
    s.dispatchEvent(new MouseEvent('mousedown', { clientX: x, clientY: y, button: 0, bubbles: true }));
    for (let i = 1; i <= 4; i++)
      window.dispatchEvent(new MouseEvent('mousemove', { clientX: x, clientY: y - i*10, bubbles: true }));
    window.dispatchEvent(new MouseEvent('mouseup', { clientX: x, clientY: y - 40, bubbles: true }));
  })()`);
  await wait(100);
  const inputAfter = await get("document.getElementById('input-zone').getBoundingClientRect().height");
  console.log("input-zone before:", inputBefore, "after -40 drag:", inputAfter);

  const stored = await get("localStorage.getItem('coder-agent-panel-widths')");
  console.log("stored:", stored);

  // double-click reset
  await evalIn("document.getElementById('split-left').dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))");
  await wait(100);
  const reset = await get("document.getElementById('left').getBoundingClientRect().width");
  console.log("left after dblclick reset:", reset);

  const ok = Math.abs(afterCenter - (beforeCenter + 100)) < 1
    && Math.abs(rightAfterCenter - (rightBeforeCenter - 50)) < 1
    && Math.abs(inputAfter - (inputBefore + 40)) < 1
    && Math.abs(reset - 260) < 1;
  console.log(ok ? "SPLIT TEST PASS" : "SPLIT TEST FAIL");
  ws.close();
  await killApp(app);
  process.exit(ok ? 0 : 1);
})().catch(async (e) => { console.error(e); await killApp(app); process.exit(1); });
