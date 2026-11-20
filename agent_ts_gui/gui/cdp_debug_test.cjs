// Debug tab smoke test: Settings > Debug shows the prompt_dump switch (default off),
// saving persists it, "View dumps" opens the dumps dialog (empty state), Clear works.
const { spawnApp, killApp, getWsUrl, connectCdp, wait } = require("./cdp_test_lib.cjs");
const fs = require("fs");
const os = require("os");
const path = require("path");

// isolate the agent's user config (~/.coder_agent) so the test cannot read
// (or pollute) the developer's real prompt_dump setting
const home = fs.mkdtempSync(path.join(os.tmpdir(), "agent-cdp-home-"));
process.env.CODER_AGENT_HOME = home;

const app = spawnApp({ root: process.env.TEST_ROOT || path.join(os.tmpdir(), "proj_a"), onLog: (d) => process.stderr.write(`[app] ${d}`) });

let failed = false;
function check(name, cond) {
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}`);
  if (!cond) failed = true;
}

(async () => {
  const url = await getWsUrl(app.port);
  if (!url) throw new Error("no devtools url");

  const cdp = connectCdp(url);
  await cdp.opened;
  const evalJs = cdp.evalJs;

  for (let i = 0; i < 150; i++) {
    const lbl = await evalJs("document.getElementById('titlebar-name') && document.getElementById('titlebar-name').textContent");
    if (lbl && lbl.includes("Coder Agent:")) break;
    await wait(200);
  }
  check("project opened", (await evalJs("document.getElementById('titlebar-name').textContent")).includes("Coder Agent:"));

  // 1) open settings, go to the debug tab
  await evalJs("document.getElementById('settings-btn').click()");
  await wait(500);
  check("settings modal shown", await evalJs("!!document.querySelector('.settings')"));
  check("debug tab exists", await evalJs("!!document.querySelector('.settings .tabs button[data-p=\"debug\"]')"));
  await evalJs("document.querySelector('.settings .tabs button[data-p=\"debug\"]').click()");
  await wait(300);
  check("debug page active", await evalJs("!!document.querySelector('.settings .page[data-p=\"debug\"].active')"));
  const cb = () => evalJs("(()=>{const c=document.querySelector('[data-s=\"debug\"][data-k=\"prompt_dump\"]'); return c ? c.checked : null;})()");
  check("prompt_dump checkbox exists", (await cb()) === false);
  check("prompt_dump default off", (await cb()) === false);
  check("dump dir shown", (await evalJs("document.querySelector('.dpath').textContent")).includes(".agent/debug/prompts"));
  check("view/clear buttons", await evalJs("!!document.querySelector('.d-view') && !!document.querySelector('.d-clear')"));

  // 2) enable + save, reopen: switch must be on
  await evalJs("(()=>{const c=document.querySelector('[data-s=\"debug\"][data-k=\"prompt_dump\"]'); c.checked=true;})()");
  await evalJs("document.querySelector('#modal .foot .yes').click()");
  await wait(600);
  await evalJs("document.getElementById('settings-btn').click()");
  await wait(500);
  await evalJs("document.querySelector('.settings .tabs button[data-p=\"debug\"]').click()");
  await wait(300);
  check("prompt_dump persisted on", (await cb()) === true);
  await evalJs("document.querySelector('.settings .close-x').click()");
  await wait(200);

  // 3) View dumps dialog: empty state
  await evalJs("document.getElementById('settings-btn').click()");
  await wait(500);
  await evalJs("document.querySelector('.settings .tabs button[data-p=\"debug\"]').click()");
  await wait(300);
  await evalJs("document.querySelector('.d-view').click()");
  await wait(500);
  check("dumps modal shown", await evalJs("!!document.querySelector('.modal.dumps')"));
  check("dumps empty state", (await evalJs("document.querySelector('.modal.dumps .dlist').textContent")).includes("no dumps yet"));
  await evalJs("document.querySelector('.modal.dumps .no').click()");
  await wait(200);
  check("dumps modal closed", !(await evalJs("!!document.querySelector('.modal.dumps')")));

  // 4) clear (nothing to delete)
  await evalJs("document.querySelector('.settings .d-clear').click()");
  await wait(300);
  // stacked confirm modal appears; accept it
  await evalJs("document.querySelector('.modal-backdrop.stacked .yes').click()");
  await wait(400);
  const clearMsg = await evalJs("document.querySelector('.page[data-p=\"debug\"] .msg').textContent");
  check("clear ok message", typeof clearMsg === "string" && clearMsg.includes("Deleted 0"));

  await evalJs("document.querySelector('.settings .close-x').click()");
  await wait(200);

  cdp.close();
  await killApp(app);
  fs.rmSync(home, { recursive: true, force: true });
  process.exit(failed ? 1 : 0);
})().catch(async (e) => { console.error(e); await killApp(app); fs.rmSync(home, { recursive: true, force: true }); process.exit(1); });
