// Smoke test: the Projects dialog opens, lists saved projects, and the
// remote dir browser works against the local (local-server) backend.
const { spawnApp, killApp, getWsUrl, connectCdp, wait } = require("./cdp_test_lib.cjs");
const path = require("path");

const app = spawnApp({ root: process.env.TEST_ROOT || path.resolve(__dirname, ".."), onLog: (d) => process.stdout.write(`[app] ${d}`) });

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

  // wait for the project to open
  let opened = false;
  for (let i = 0; i < 120; i++) {
    const lbl = await evalJs("document.getElementById('titlebar-name') && document.getElementById('titlebar-name').textContent");
    if (lbl && lbl.includes("Coder Agent:")) { opened = true; break; }
    await wait(500);
  }
  check("project opened on boot", opened);

  // 1) Projects dialog opens with the expected buttons
  await evalJs("document.getElementById('root-label').click()");
  await wait(300);
  const dlg = await evalJs(`JSON.stringify({
    show: !!document.querySelector('#modal-backdrop.show'),
    head: document.querySelector('#modal .head') && document.querySelector('#modal .head').textContent,
    btns: [...document.querySelectorAll('#modal .btnrow button')].map(b => b.textContent),
  })`);
  const o = JSON.parse(dlg);
  check("projects dialog visible", o.show);
  check("dialog title", o.head === "Projects");
  check("dialog buttons", o.btns.join(",") === "+ Add project...,Rename,Delete");

  // 2) the auto-opened root is remembered as a saved project (via openProject)
  const rows = await evalJs("[...document.querySelectorAll('#modal .projrow .pname')].map(e => e.textContent)");
  check("auto-opened project is in the list", rows.some((n) => n.includes("agent_ts_gui")));

  // 3) local mode: "+ Add project..." uses the native folder picker (mocked via
  // the __pickDirectory hook); the remote dir browser is covered by cdp_remote_test
  await evalJs("document.querySelector('#modal .yes').click()"); // close projects dialog
  await wait(200);
  const add = await evalJs(`(async () => {
    window.__pickDirectory = () => Promise.resolve("/tmp");
    document.getElementById('root-label').click();
    await new Promise(r => setTimeout(r, 200));
    document.querySelector('#modal .add-project').click();
    await new Promise(r => setTimeout(r, 500));
    // the name input modal must be up (native picker was mocked, no browser rows)
    const promptUp = !!document.querySelector('.modal-backdrop.stacked .pinput');
    const hasBrowser = !!document.querySelector('#modal .pathbar');
    document.querySelector('.modal-backdrop.stacked .pno')?.click()
      ?? document.querySelector('.modal-backdrop.stacked .no')?.click();
    await new Promise(r => setTimeout(r, 200));
    document.querySelector('#modal .yes').click();
    return JSON.stringify({ promptUp, hasBrowser });
  })()`);
  const a = JSON.parse(add);
  check("local mode: add uses native picker (name prompt, no dir browser)", a.promptUp && !a.hasBrowser);

  cdp.close();
  await killApp(app);
  process.exit(failed ? 1 : 0);
})().catch(async (e) => { console.error(e); await killApp(app); process.exit(1); });
