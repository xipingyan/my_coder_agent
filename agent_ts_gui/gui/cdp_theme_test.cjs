// Theme smoke test: default light, switch dark/solarized via Settings > Appearance, persistence across reload.
const { spawnApp, killApp, getWsUrl, connectCdp, wait } = require("./cdp_test_lib.cjs");

const app = spawnApp({ root: process.env.TEST_ROOT || "/home/xiping/mygitee/my_coder_agent/agent_ts_gui", onLog: (d) => process.stderr.write(`[app] ${d}`) });

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

  // wait for the project to open (S.config is set in openProject; Settings
  // needs it, so we must not open the dialog before this)
  for (let i = 0; i < 150; i++) {
    const lbl = await evalJs("document.getElementById('titlebar-name') && document.getElementById('titlebar-name').textContent");
    if (lbl && lbl.includes("Coder Agent:")) break;
    await wait(200);
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
  // NOTE: wrap in an IIFE - a top-level `const` in Runtime.evaluate leaks into
  // the global lexical scope and the NEXT eval re-declaring it throws
  await evalJs("(()=>{const s=document.querySelector('.theme-sel'); s.value='dark'; s.dispatchEvent(new Event('change'));})()");
  await wait(200);
  check("dark data-theme", (await dt()) === "dark");
  check("dark body bg rgb(30,30,30)", (await bg()) === "rgb(30, 30, 30)");
  check("dark panel rgb(37,37,38)",
    (await evalJs("getComputedStyle(document.getElementById('toolbar')).backgroundColor")) === "rgb(37, 37, 38)");
  check("dark persisted", (await evalJs("localStorage.getItem('agent_ts_gui.theme')")) === "dark");

  // 3) solarized
  await evalJs("(()=>{const s=document.querySelector('.theme-sel'); s.value='solarized'; s.dispatchEvent(new Event('change'));})()");
  await wait(200);
  check("solarized data-theme", (await dt()) === "solarized");
  check("solarized body bg rgb(238,232,213)", (await bg()) === "rgb(238, 232, 213)");

  // 4) back to light, close dialog
  await evalJs("(()=>{const s=document.querySelector('.theme-sel'); s.value='light'; s.dispatchEvent(new Event('change'));})()");
  await wait(200);
  check("light data-theme empty", (await dt()) === undefined);
  check("light body bg rgb(240,240,240)", (await bg()) === "rgb(240, 240, 240)");
  await evalJs("document.querySelector('.settings .close-x').click()");
  await wait(200);

  // 5) persistence across reload: set dark, reload, check
  await evalJs("localStorage.setItem('agent_ts_gui.theme','dark');");
  await cdp.send("Page.reload");
  for (let i = 0; i < 100; i++) {
    if (await evalJs("!!document.getElementById('toolbar')")) break;
    await wait(200);
  }
  check("after reload data-theme dark", (await dt()) === "dark");
  check("after reload body bg rgb(30,30,30)", (await bg()) === "rgb(30, 30, 30)");

  cdp.close();
  await killApp(app);
  process.exit(failed ? 1 : 0);
})().catch(async (e) => { console.error(e); await killApp(app); process.exit(1); });
