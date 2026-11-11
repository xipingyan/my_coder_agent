// Regression test: in the Projects dialog, selecting / double-clicking a
// different saved project must actually open it (root label changes).
const { spawnApp, killApp, getWsUrl, connectCdp, wait } = require("./cdp_test_lib.cjs");

const ROOT_A = process.env.TEST_ROOT_A || "/tmp/proj_a";
const ROOT_B = process.env.TEST_ROOT_B || "/tmp/proj_b";
const fs = require("fs");
fs.mkdirSync(ROOT_A, { recursive: true });
fs.mkdirSync(ROOT_B, { recursive: true });

const app = spawnApp({ root: ROOT_A, onLog: (d) => process.stdout.write(`[app] ${d}`) });

let failed = false;
function check(label, ok) {
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}`);
  if (!ok) failed = true;
}

(async () => {
  const url = await getWsUrl(app.port);
  if (!url) throw new Error("no devtools url");

  const cdp = connectCdp(url);
  await cdp.opened;
  const evalJs = cdp.evalJs;

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
  await evalJs("document.getElementById('root-label').click()");
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
  await evalJs("document.getElementById('root-label').click()");
  await wait(300);
  await evalJs(`[...document.querySelectorAll('#modal .projrow')].find(r => r.textContent.includes('proj_a')).dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))`);
  let back = false;
  for (let i = 0; i < 40; i++) {
    const lbl = await evalJs("document.getElementById('root-label').textContent");
    if (lbl && lbl.includes(ROOT_A)) { back = true; break; }
    await wait(500);
  }
  check("double-click switches back to project A", back);

  cdp.close();
  await killApp(app);
  process.exit(failed ? 1 : 0);
})().catch(async (e) => { console.error(e); await killApp(app); process.exit(1); });
