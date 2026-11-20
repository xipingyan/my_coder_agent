// Open Settings during local backend startup: Windows CRLF readiness must work.
const assert = require("node:assert/strict");
const { spawnApp, killApp, getWsUrl, connectCdp, wait } = require("./cdp_test_lib.cjs");

const app = spawnApp();
let cdp;
(async () => {
  const url = await getWsUrl(app.port);
  assert.ok(url, `no devtools target\n${app.getLog()}`);
  cdp = connectCdp(url);
  await cdp.opened;
  const deadline = Date.now() + 20000;
  let clicked = false;
  let ready = false;
  while (Date.now() < deadline) {
    if (!clicked) clicked = await cdp.evalJs(`(() => {
      const button = document.getElementById('settings-btn');
      if (!button) return false;
      button.click(); return true;
    })()`);
    ready = await cdp.evalJs(`!!document.querySelector('#modal input[data-s="server"][data-k="base_url"]')`);
    if (ready) break;
    await wait(100);
  }
  assert.ok(clicked, "Settings button missing");
  assert.ok(ready, `Settings defaults did not load\n${app.getLog()}`);
  assert.ok(app.getLog().includes("[main] ws connected"), app.getLog());
  assert.ok(!app.getLog().includes("backend not connected"), app.getLog());
  assert.equal(await cdp.evalJs("!!document.getElementById('backend-banner')"), false);
  console.log("PASS: local backend connected and Settings defaults loaded during boot");
})().catch(e => {
  console.error(e);
  process.exitCode = 1;
}).finally(async () => {
  if (cdp) cdp.close();
  await killApp(app);
  process.exit(process.exitCode || 0);
});