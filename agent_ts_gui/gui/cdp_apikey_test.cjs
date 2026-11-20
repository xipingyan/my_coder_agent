// Verify the api_key field in Settings > Model has a datalist quick-pick (same style as base_url).
const { spawnApp, killApp, getWsUrl, connectCdp, wait } = require("./cdp_test_lib.cjs");
const path = require("path");

const app = spawnApp({ root: process.env.TEST_ROOT || path.resolve(__dirname, "..") });

(async () => {
  const url = await getWsUrl(app.port);
  if (!url) throw new Error("no devtools url");
  const cdp = connectCdp(url);
  await cdp.opened;
  const evalJs = cdp.evalJs;

  // wait for project to open
  let opened = false;
  for (let i = 0; i < 120; i++) {
    const lbl = await evalJs("document.getElementById('titlebar-name') && document.getElementById('titlebar-name').textContent");
    if (lbl && lbl.includes("Coder Agent:")) { opened = true; break; }
    await wait(500);
  }
  if (!opened) throw new Error("project not opened");

  // open settings dialog
  await evalJs("document.querySelector('#settings-btn') && document.querySelector('#settings-btn').click()");
  for (let i = 0; i < 50; i++) {
    if (await evalJs("!!document.querySelector('#modal .settings')")) break;
    await wait(100);
  }

  const r = await evalJs(`JSON.stringify({
    hasInput: !!document.querySelector('[data-s="server"][data-k="api_key"]'),
    listAttr: document.querySelector('[data-s="server"][data-k="api_key"]') && document.querySelector('[data-s="server"][data-k="api_key"]').getAttribute("list"),
    hasDl: !!document.getElementById("apikey-dl"),
    dlOptions: document.getElementById("apikey-dl") ? document.getElementById("apikey-dl").options.length : -1,
    baseList: document.querySelector('[data-s="server"][data-k="base_url"]') && document.querySelector('[data-s="server"][data-k="base_url"]').getAttribute("list"),
  })`);
  console.log("api_key field:", r);
  const o = JSON.parse(r);
  if (!o.hasInput) throw new Error("api_key input missing");
  if (o.listAttr !== "apikey-dl") throw new Error("api_key input not linked to datalist, got: " + o.listAttr);
  if (!o.hasDl) throw new Error("apikey-dl datalist missing");
  console.log("OK: api_key has datalist quick-pick (options:", o.dlOptions, "), base_url list:", o.baseList);

  // close dialog
  await evalJs("document.querySelector('#modal .no') && document.querySelector('#modal .no').click()");

  cdp.close();
  await killApp(app);
  process.exit(0);
})().catch(async (e) => { console.error(e); await killApp(app); process.exit(1); });
