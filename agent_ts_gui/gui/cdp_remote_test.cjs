// Smoke test: remote-mode connection. A standalone coder-agent-server is
// already listening on 127.0.0.1:8931 (started separately, like on a real
// remote machine). The GUI is pointed at it via connection.json (mode
// "remote") and must NOT spawn its own backend.
const { spawnApp, killApp, getWsUrl, connectCdp, wait } = require("./cdp_test_lib.cjs");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ROOT = process.env.TEST_ROOT || "/home/xiping/mygitee/my_coder_agent/agent_ts_gui";
const PORT = 8931;

// point the app at the remote server, in an isolated user-data-dir (so the
// developer's running instance is untouched)
const userData = fs.mkdtempSync(path.join(os.tmpdir(), "agent-cdp-remote-"));
fs.mkdirSync(userData, { recursive: true });
fs.writeFileSync(path.join(userData, "connection.json"),
  JSON.stringify({ mode: "remote", host: "127.0.0.1", port: PORT, token: "" }));

const app = spawnApp({ root: ROOT, userDataDir: userData, onLog: (d) => process.stdout.write(`[app] ${d}`) });
const appLog = () => app.getLog();

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

  try {
    // 1) the main process must connect to the remote server, not spawn one
    let connected = false;
    for (let i = 0; i < 60; i++) {
      if (appLog().includes(`ws://127.0.0.1:${PORT}`)) { connected = true; break; }
      await wait(500);
    }
    check("main connected to remote server", connected);
    check("no local backend spawned", !appLog().includes("starting backend"));

    // 2) project opens through the remote connection
    let opened = false;
    for (let i = 0; i < 120; i++) {
      const lbl = await evalJs("document.getElementById('titlebar-name') && document.getElementById('titlebar-name').textContent");
      if (lbl && lbl.includes("Coder Agent:")) { opened = true; break; }
      await wait(500);
    }
    check("project opened via remote server", opened);

    // 3) connection tab shows remote mode
    await evalJs("document.getElementById('settings-btn').click()");
    await wait(300);
    await evalJs("[...document.querySelectorAll('#modal .tabs button')].find(b => b.dataset.p === 'connection').click()");
    await wait(500);
    const conn = await evalJs(`JSON.stringify({
      mode: document.querySelector('#modal .c-mode') && document.querySelector('#modal .c-mode').value,
      host: document.querySelector('#modal .c-host') && document.querySelector('#modal .c-host').value,
      port: document.querySelector('#modal .c-port') && document.querySelector('#modal .c-port').value,
    })`);
    const c = JSON.parse(conn);
    check("connection tab shows remote", c.mode === "remote");
    check("connection tab host/port", c.host === "127.0.0.1" && String(c.port) === String(PORT));

    // 4) list_dir goes over the remote connection
    const ls = await evalJs(`(async () => {
      // quick direct RPC via the app's internal call() is not exposed; use the projects dialog browser instead
      document.getElementById('root-label').click();
      await new Promise(r => setTimeout(r, 200));
      document.querySelector('#modal .add-remote').click();
      await new Promise(r => setTimeout(r, 1500));
      const n = document.querySelectorAll('#modal .dirent').length;
      document.querySelector('#modal .no').click();
      await new Promise(r => setTimeout(r, 200));
      document.querySelector('#modal .yes').click();
      return n;
    })()`);
    check("remote dir browser works over remote connection", ls > 0);
  } finally {
    cdp.close();
    await killApp(app);
    process.exit(failed ? 1 : 0);
  }
})().catch(async (e) => { console.error(e); await killApp(app); process.exit(1); });
