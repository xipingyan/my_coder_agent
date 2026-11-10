// Verify the sessions list: multi-select (ctrl/shift click) + contextmenu shows the menu.
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
proc.stdout.on("data", (d) => process.stdout.write(`[app] ${d}`));
proc.stderr.on("data", (d) => process.stderr.write(`[err] ${d}`));

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
    if (r.exceptionDetails) throw new Error("JS exception: " + JSON.stringify(r.exceptionDetails));
    return r.result && r.result.result ? r.result.result.value : undefined;
  };

  // wait for project to open
  for (let i = 0; i < 120; i++) {
    const lbl = await evalJs("document.getElementById('root-label') && document.getElementById('root-label').textContent");
    if (lbl && lbl !== "(no project)") break;
    await wait(500);
  }

  // switch to sessions tab
  await evalJs("[...document.querySelectorAll('#left-tabs .tab')].find(b => b.dataset.tab === 'sessions').click()");
  const n = await evalJs("document.querySelectorAll('#session-list li').length");
  console.log("session items:", n);
  if (!n) throw new Error("no sessions in list (need history to test)");

  const click = (i, mods) => evalJs(`(() => {
    const li = document.querySelectorAll('#session-list li')[${i}];
    const r = li.getBoundingClientRect();
    li.dispatchEvent(new MouseEvent('click', {bubbles: true, clientX: r.x + 5, clientY: r.y + 5, ${mods}}));
    return li.classList.contains('selected');
  })()`);

  // 1) plain click selects only that one
  await click(0, "");
  console.log("after plain click, selected:", await evalJs("[...document.querySelectorAll('#session-list li.selected')].length"));

  // 2) ctrl+click adds
  await click(1, "ctrlKey: true");
  console.log("after ctrl+click, selected:", await evalJs("[...document.querySelectorAll('#session-list li.selected')].length"));

  // 3) shift+click range
  await click(3, "shiftKey: true");
  console.log("after shift+click, selected:", await evalJs("[...document.querySelectorAll('#session-list li.selected')].length"));

  // 4) contextmenu on a selected item -> menu visible with the right label
  const ctx = await evalJs(`(() => {
    const li = document.querySelectorAll('#session-list li')[1];
    const r = li.getBoundingClientRect();
    li.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, clientX: r.x + 5, clientY: r.y + 5}));
    const m = document.getElementById('ctxmenu');
    return { display: m.style.display, items: [...m.querySelectorAll('.item')].map(e => e.textContent) };
  })()`);
  console.log("contextmenu result:", JSON.stringify(ctx));
  if (ctx.display !== "block") throw new Error("context menu did NOT appear");
  if (!ctx.items.some((t) => t.startsWith("Delete"))) throw new Error("no Delete item in menu");

  // 5) cancel: click the menu item? No - just confirm the modal appears then Cancel it.
  await evalJs(`(() => {
    const m = document.getElementById('ctxmenu');
    const item = [...m.querySelectorAll('.item')].find(e => e.textContent.startsWith('Delete'));
    item.click();
  })()`);
  await wait(300);
  const modal = await evalJs(`(() => {
    const bd = document.querySelector('#modal-backdrop');
    return { show: bd.classList.contains('show'), text: bd.textContent.slice(0, 200) };
  })()`);
  console.log("confirm modal:", JSON.stringify(modal.show), modal.text);
  if (!modal.show) throw new Error("confirm modal did not appear");
  // cancel it
  await evalJs("document.querySelector('#modal .no') && document.querySelector('#modal .no').click()");
  await wait(200);
  const left = await evalJs("document.querySelectorAll('#session-list li').length");
  console.log("sessions after cancel (unchanged):", left);
  if (left !== n) throw new Error("sessions changed after cancel!");

  console.log("CTXMENU TEST OK");
  ws.close();
  proc.kill("SIGTERM");
  await wait(1500);
  if (proc.exitCode === null) proc.kill("SIGKILL");
  process.exit(0);
})().catch((e) => { console.error("FAIL:", e.message); proc.kill("SIGKILL"); process.exit(1); });
