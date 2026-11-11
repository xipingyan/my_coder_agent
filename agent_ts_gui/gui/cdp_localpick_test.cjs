// Probe: click "+ Local project..." and see whether the native folder dialog
// opens (app blocks) or the click silently does nothing.
const { spawnApp, killApp, getWsUrl, connectCdp, wait } = require("./cdp_test_lib.cjs");
const fs = require("fs");

fs.mkdirSync("/tmp/proj_a", { recursive: true });
const app = spawnApp({ root: "/tmp/proj_a", onLog: (d) => process.stdout.write(`[app] ${d}`) });

(async () => {
  const url = await getWsUrl(app.port);
  if (!url) throw new Error("no devtools url");
  const cdp = connectCdp(url);
  await cdp.opened;
  const evalJs = cdp.evalJs;

  // wait for boot
  for (let i = 0; i < 120; i++) {
    const lbl = await evalJs("document.getElementById('root-label') && document.getElementById('root-label').textContent");
    if (lbl && lbl.includes("proj_a")) break;
    await wait(500);
  }

  // open projects dialog, click add-local, then check if the renderer is
  // blocked (native dialog open) by probing with a short-timeout eval
  await evalJs(`localStorage.setItem("coder-agent.projects", JSON.stringify([]))`);
  await evalJs("document.getElementById('root-label').click()");
  await wait(300);

  const click = await evalJs(`document.querySelector('#modal .add-local').click(); "clicked"`, 8000);
  console.log("click result:", click);
  await wait(1500);
  const probe = await evalJs("1+1", 3000);
  console.log("renderer probe after click:", probe, "(object with __timeout => renderer blocked => native dialog is open)");

  cdp.close();
  await killApp(app);
  process.exit(0);
})().catch(async (e) => { console.error(e); await killApp(app); process.exit(1); });
