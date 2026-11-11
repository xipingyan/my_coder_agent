// Measure boot timing: page visible vs backend ready vs project opened.
const { spawnApp, killApp, getWsUrl, connectCdp, wait } = require("./cdp_test_lib.cjs");

const app = spawnApp({ root: process.env.TEST_ROOT || "/home/xiping/mygitee/my_coder_agent/agent_ts_gui" });

const t0 = Date.now();
const ts = (label) => console.log(`t=${Date.now() - t0}ms  ${label}`);

(async () => {
  const url = await getWsUrl(app.port);
  if (!url) throw new Error("no devtools url");
  ts("devtools page target appears");

  const cdp = connectCdp(url);
  await cdp.opened;
  const evalJs = cdp.evalJs;

  // 1) page visible: toolbar exists in DOM; also record the first state label seen
  let seen = false;
  let firstState = null;
  for (let i = 0; i < 150; i++) {
    const r = await evalJs("JSON.stringify({t: !!document.getElementById('toolbar'), s: document.getElementById('state-label') && document.getElementById('state-label').textContent})");
    if (r) {
      const o = JSON.parse(r);
      if (!firstState && o.s) firstState = o.s;
      if (o.t) { seen = true; break; }
    }
    await wait(100);
  }
  ts(`page DOM ready (toolbar visible) = ${seen}`);
  ts(`first state label observed: ${firstState}`);

  // 3) if no project opens, how fast does the banner appear?
  let bannerAt = null;
  for (let i = 0; i < 100; i++) {
    if (await evalJs("!!document.getElementById('backend-banner')")) { bannerAt = Date.now() - t0; break; }
    await wait(200);
  }
  if (bannerAt !== null) {
    ts(`banner appeared at t=${bannerAt}ms`);
    // click Restart to verify the recovery path (flaky backend: first start fails, restart succeeds)
    await evalJs("document.querySelector('#backend-banner .restart') && document.querySelector('#backend-banner .restart').click()");
    ts("clicked Restart");
  }

  // 4) project opened: root-label no longer "(no project)"
  let opened = false;
  for (let i = 0; i < 120; i++) {
    const lbl = await evalJs("document.getElementById('root-label') && document.getElementById('root-label').textContent");
    if (lbl && lbl !== "(no project)") { opened = true; ts(`project opened: ${lbl.trim()}`); break; }
    await wait(500);
  }
  if (!opened) ts("project NOT opened within 60s");

  // 4) banner must NOT be present (no false alarm)
  const banner = await evalJs("!!document.getElementById('backend-banner')");
  ts(`backend banner present (should be false): ${banner}`);

  cdp.close();
  await killApp(app);
  process.exit(0);
})().catch(async (e) => { console.error(e); await killApp(app); process.exit(1); });
