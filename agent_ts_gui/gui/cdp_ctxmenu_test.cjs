// Verify the sessions list: multi-select (ctrl/shift click) + contextmenu shows the menu.
const { spawnApp, killApp, getWsUrl, connectCdp, wait } = require("./cdp_test_lib.cjs");

const app = spawnApp({ root: process.env.TEST_ROOT || "/home/xiping/mygitee/my_coder_agent/agent_ts_gui" });

(async () => {
  const url = await getWsUrl(app.port);
  if (!url) throw new Error("no devtools url");

  const cdp = connectCdp(url);
  await cdp.opened;
  const evalJs = cdp.evalJs;

  // wait for project to open
  for (let i = 0; i < 120; i++) {
    const lbl = await evalJs("document.getElementById('titlebar-name') && document.getElementById('titlebar-name').textContent");
    if (lbl && lbl.includes("Coder Agent:")) break;
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
  cdp.close();
  await killApp(app);
  process.exit(0);
})().catch(async (e) => { console.error("FAIL:", e.message); await killApp(app); process.exit(1); });
