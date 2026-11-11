// Regression: the window must close (and the process must exit) after a
// backend restart - including when the close races the in-flight restart.
// This used to hang: a backend spawned by the in-flight restart kept the
// app alive, and before-quit blocked on it forever.
const { spawnApp, killApp, getWsUrl, connectCdp, wait } = require("./cdp_test_lib.cjs");

let failed = false;
function check(label, ok) {
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}`);
  if (!ok) failed = true;
}

async function boot(app, evalJs) {
  for (let i = 0; i < 120; i++) {
    const lbl = await evalJs("document.getElementById('titlebar-name') && document.getElementById('titlebar-name').textContent");
    if (lbl && lbl.includes("proj_a")) return true;
    await wait(500);
  }
  return false;
}

async function expectExit(app, label, ms = 15000) {
  const t1 = Date.now();
  while (app.proc.exitCode === null && app.proc.signalCode === undefined && Date.now() - t1 < ms) {
    await wait(500);
  }
  if (app.proc.exitCode === null && app.proc.signalCode === undefined) {
    check(`${label}: process exited after win-close (still alive after ${ms}ms)`, false);
    return false;
  }
  check(`${label}: process exited ${Date.now() - t1}ms after win-close`, true);
  return true;
}

(async () => {
  // ---- A: close while the restart is in flight ----
  let app = spawnApp({ root: "/tmp/proj_a", onLog: (d) => process.stdout.write(`[app] ${d}`) });
  let url = await getWsUrl(app.port);
  if (!url) throw new Error("no devtools url");
  let cdp = connectCdp(url);
  await cdp.opened;
  const evalJs = cdp.evalJs;
  check("A: project opened on boot", await boot(app, evalJs));

  const p = evalJs("window.api.restartBackend(null)", 40000); // do NOT await
  await wait(100);
  await evalJs("document.getElementById('win-close').click()");
  try { await p; } catch { /* rejected is fine: the app is quitting */ }
  await expectExit(app, "A");
  cdp.close();
  await killApp(app);

  // ---- B: close after the restart finished ----
  app = spawnApp({ root: "/tmp/proj_a", onLog: (d) => process.stdout.write(`[app] ${d}`) });
  url = await getWsUrl(app.port);
  if (!url) throw new Error("no devtools url");
  cdp = connectCdp(url);
  await cdp.opened;
  const evalJs2 = cdp.evalJs;
  check("B: project opened on boot", await boot(app, evalJs2));
  await evalJs2("window.api.restartBackend(null)", 40000);
  await evalJs2("document.getElementById('win-close').click()");
  await expectExit(app, "B");
  cdp.close();
  await killApp(app);

  process.exit(failed ? 1 : 0);
})().catch(async (e) => { console.error(e); process.exit(1); });
