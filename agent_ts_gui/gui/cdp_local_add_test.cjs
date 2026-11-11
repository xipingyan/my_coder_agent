// Regression test: "+ Local project..." in the Projects dialog must add the
// picked directory to the project list (the name input modal works in Electron,
// where window.prompt() silently returns null).
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

  for (let i = 0; i < 120; i++) {
    const lbl = await evalJs("document.getElementById('titlebar-name') && document.getElementById('titlebar-name').textContent");
    if (lbl && (lbl.includes(ROOT_A) || lbl.includes("proj_a"))) break;
    await wait(500);
  }

  // mock the native folder picker, open the dialog, click "+ Local project..."
  await evalJs(`localStorage.setItem("coder-agent.projects", JSON.stringify([]))`);
  // window.api is a contextBridge proxy: assigning to it silently fails, so the
  // app exposes a __pickDirectory test hook (see window.__onEvent).
  await evalJs(`window.__pickDirectory = () => Promise.resolve(${JSON.stringify(ROOT_B)})`);
  await evalJs("document.getElementById('root-label').click()");
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
    const lbl = await evalJs("document.getElementById('titlebar-name').textContent");
    if (lbl && lbl.includes(ROOT_B)) { switched = true; break; }
    await wait(500);
  }
  check("Open switches to the new local project", switched);

  cdp.close();
  await killApp(app);
  process.exit(failed ? 1 : 0);
})().catch(async (e) => { console.error(e); await killApp(app); process.exit(1); });
