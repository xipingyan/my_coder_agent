// Smoke test: remote-mode connection. A standalone coder-agent-server is
// already listening on 127.0.0.1:8931 (started separately, like on a real
// remote machine). The GUI is pointed at it via connection.json (mode
// "remote") and must NOT spawn its own backend.
const { spawnApp, killApp, getWsUrl, connectCdp, wait, venvPython } = require("./cdp_test_lib.cjs");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ROOT = process.env.TEST_ROOT || path.resolve(__dirname, "..");
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

    // 4) add-project in remote mode: a text input validated via the list_dir
    // RPC (over the remote connection). A bad path must show an error; a
    // valid one (with an ssh-style user@host: prefix) must proceed to the
    // name prompt.
    const ls = await evalJs(`(async () => {
      const sleep = (ms) => new Promise(r => setTimeout(r, ms));
      document.getElementById('root-label').click();
      await sleep(300);
      document.querySelector('#modal .add-project').click();
      await sleep(400);
      const input = document.querySelector('.modal-backdrop.stacked .rpath');
      const inputUp = !!input && input.placeholder === '~/mygitee/tmp';
      // bad path: list_dir over the remote connection must reject it
      input.value = '/no/such/dir/xyz';
      document.querySelector('.modal-backdrop.stacked .yes').click();
      await sleep(1500);
      const errShown = (document.querySelector('.modal-backdrop.stacked .msg') || {}).textContent || '';
      // good path with an ssh-style prefix: host part is stripped
      input.value = 'xiping@xiping-home-nv:~';
      document.querySelector('.modal-backdrop.stacked .yes').click();
      await sleep(1500);
      // the name prompt (stacked on top of the add-project modal) must be up
      const stacked = () => [...document.querySelectorAll('.modal-backdrop.stacked')];
      const promptUp = !!stacked().some(b => b.querySelector('.pinput'));
      const promptVal = stacked().map(b => b.querySelector('.pinput')?.value).find(v => v !== undefined);
      // cancel the name prompt, then the add-project modal, then the projects dialog
      [...stacked().reverse()].forEach(b => b.querySelector('.no')?.click());
      await sleep(200);
      // close the projects dialog
      document.querySelector('#modal .yes').click();
      return JSON.stringify({ inputUp, errShown, promptUp, promptVal });
    })()`);
    const r4 = JSON.parse(ls);
    check("remote add-project shows a path text input with example placeholder", r4.inputUp);
    check("bad path rejected via list_dir over the remote connection", /not a directory/i.test(r4.errShown));
    check("valid ssh-style path (user@host:~) proceeds to the name prompt", r4.promptUp && r4.promptVal === "xiping");

    // 5) auto-reconnect: kill the remote server, the GUI must show the banner
    // and keep trying; restart the server and the GUI must reconnect on its own
    const { spawn, execSync } = require("child_process");
    const BACKEND = path.join(__dirname, "..", "backend");
    const srvLog = path.join(os.tmpdir(), "cdp-remote-restart.log");
    const startSrv = () => {
      const out = fs.openSync(srvLog, "a");
      const p = spawn(venvPython(), ["-m", "coder_agent.server", "--host", "127.0.0.1", "--port", String(PORT)],
        { cwd: BACKEND, detached: true, stdio: ["ignore", out, out] });
      p.unref();
    };
    const stopSrv = () => {
      try {
        if (process.platform === "win32") {
          const out = execSync(`netstat -ano -p tcp | findstr "LISTENING" | findstr /R ":${PORT} "`, { stdio: "pipe" }).toString();
          for (const line of out.split(/\r?\n/)) {
            const pid = line.trim().split(/\s+/).pop();
            if (pid) execSync(`taskkill /PID ${pid} /F`, { stdio: "ignore" });
          }
        } else {
          execSync(`for p in $(ps -eo pid,cmd | awk '/coder_agent.server --host 127.0.0.1 --port ${PORT}/ && !/awk/ {print $1}'); do kill $p 2>/dev/null; done`,
            { shell: "/bin/bash", stdio: "ignore" });
        }
      } catch { /* ignore */ }
    };
    // the runner owns the server on :8931 when it started it; if not, we own it
    const srvOwnedHere = process.env.CDP_REMOTE_OWN_SERVER === "1";
    if (srvOwnedHere) {
      stopSrv();
      await wait(1500);
      let banner = false;
      for (let i = 0; i < 30; i++) {
        banner = await evalJs("!!document.getElementById('backend-banner')");
        if (banner) break;
        await wait(500);
      }
      check("banner shown when the remote server dies", banner);
      check("auto-reconnect is retrying", appLog().includes("remote ws closed, retry"));
      // restart the server: the GUI must reconnect without any user action
      startSrv();
      let restored = false;
      for (let i = 0; i < 60; i++) {
        const b = await evalJs("!!document.getElementById('backend-banner')");
        if (!b) { restored = true; break; }
        await wait(500);
      }
      check("GUI auto-reconnected after the server came back", restored);
      stopSrv(); // we started it, we clean up
    } else {
      console.log("SKIP  auto-reconnect (server on :8931 is owned by the runner)");
    }
  } finally {
    cdp.close();
    await killApp(app);
    process.exit(failed ? 1 : 0);
  }
})().catch(async (e) => { console.error(e); await killApp(app); process.exit(1); });
