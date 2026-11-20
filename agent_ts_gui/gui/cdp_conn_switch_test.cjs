// Smoke test: Settings > Remote server tab + local<->remote switching.
// 1) boot in remote mode against a standalone server (a saved project is
//    seeded in localStorage so boot auto-opens it),
// 2) settings > model tab: server fields editable, Save works,
// 3) switch mode to local and Apply: the GUI must spawn a local backend,
//    connect it (ws open), reopen the project, no "backend not connected",
// 4) the remote label hides in local mode,
// 5) switching back to remote reconnects to the standalone server.
const { spawnApp, killApp, getWsUrl, connectCdp, wait, venvPython } = require("./cdp_test_lib.cjs");
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ROOT = process.env.TEST_ROOT || path.resolve(__dirname, "..");
const PORT = 8932; // isolated port so we own the server
const B = path.join(__dirname, "..", "backend");
const srvLog = path.join(os.tmpdir(), "cdp-conn-switch-srv.log");
let srv = null;
function startSrv() {
  const out = fs.openSync(srvLog, "a");
  srv = spawn(venvPython(), ["-m", "coder_agent.server", "--host", "127.0.0.1", "--port", String(PORT)],
    { cwd: B, detached: true, stdio: ["ignore", out, out] });
  srv.unref();
}
function stopSrv() {
  if (srv) { try { srv.kill("SIGTERM"); } catch { /* ignore */ } srv = null; }
}

const userData = fs.mkdtempSync(path.join(os.tmpdir(), "agent-cdp-conn-"));
fs.mkdirSync(userData, { recursive: true });
fs.writeFileSync(path.join(userData, "connection.json"),
  JSON.stringify({ mode: "remote", host: "127.0.0.1", port: PORT, token: "" }));
// isolate the user-wide config so the test's Save cannot clobber the
// developer's ~/.coder_agent/config.toml
process.env.CODER_AGENT_HOME = userData;

const app = spawnApp({ root: ROOT, userDataDir: userData, onLog: (d) => process.stdout.write(`[app] ${d}`) });
const appLog = () => app.getLog();

let failed = false;
function check(label, ok) {
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}`);
  if (!ok) failed = true;
}

(async () => {
  startSrv();
  await wait(2500); // let the server bind before the GUI connects
  const url = await getWsUrl(app.port);
  if (!url) throw new Error("no devtools url");
  const cdp = connectCdp(url);
  await cdp.opened;
  const evalJs = cdp.evalJs;

  try {
    // boot: connected to the remote server, project auto-opened
    let connected = false;
    for (let i = 0; i < 60; i++) {
      if (appLog().includes("[main] ws connected")) { connected = true; break; }
      await wait(500);
    }
    check("booted in remote mode", connected);

    // the server's first bind can be slow, so the GUI's first connect may fail
    // and show the banner. Recover deterministically: if the banner is up,
    // click Restart, then wait until a real RPC (ping) succeeds.
    async function ensureConnected() {
      for (let i = 0; i < 40; i++) {
        const banner = await evalJs("!!document.getElementById('backend-banner')");
        if (banner) {
          await evalJs("const b=document.querySelector('#backend-banner .restart'); b && b.click()");
          await wait(1500);
        }
        const pong = await evalJs(`(async () => {
          const id = 55500;
          return new Promise((res) => {
            window.api.onReply((m) => { if (m.id === id) res(m.result === "pong"); });
            window.api.send({ id, method: "ping", params: {} });
            setTimeout(() => res(false), 4000);
          });
        })()`);
        if (pong) return true;
        await wait(500);
      }
      return false;
    }
    check("backend connected (ping ok)", await ensureConnected());
    // the boot's auto-open can race the first remote connect (the project gets
    // queued and then lost); make sure the link is solid before opening by hand
    await ensureConnected();

    // seed a saved project in localStorage and open it through the app's own
    // openProject (via the projects dialog) so S.root is set and the local
    // switch has a project to restore.
    await evalJs(`localStorage.setItem('coder-agent.projects', ${JSON.stringify(JSON.stringify([{ name: "proj", mode: "remote", path: ROOT }]))});
      localStorage.setItem('coder-agent.last-project', 'proj')`);
    let opened = false;
    for (let i = 0; i < 20; i++) {
      const t = String(await evalJs("document.getElementById('titlebar-name') && document.getElementById('titlebar-name').textContent"));
      if (t.includes("Coder Agent:")) { opened = true; break; }
      // open it through the projects dialog (the app's openProjectNamed path)
      await evalJs(`(async () => {
        document.getElementById('root-label').click();
        await new Promise(r => setTimeout(r, 400));
        const row = document.querySelector('#modal .projrow');
        if (row) row.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
      })()`);
      await wait(1500);
    }
    check("project open over remote (for the switch test)", opened);

    // remote label visible in the toolbar (refreshRemoteLabel is async: poll)
    let label = "";
    for (let i = 0; i < 20; i++) {
      label = String(await evalJs("document.getElementById('remote-label') && document.getElementById('remote-label').textContent"));
      if (label.includes(`remote: 127.0.0.1:${PORT}`)) break;
      await wait(250);
    }
    check("remote label visible", label.includes(`remote: 127.0.0.1:${PORT}`));

    // settings > model tab: server fields editable, Save works and persists
    await evalJs("document.getElementById('settings-btn').click()");
    await wait(800);
    const modelInput = await evalJs(`(() => {
      const i = document.querySelector('#modal [data-s="server"][data-k="model"]');
      return i ? { tagName: i.tagName, disabled: i.disabled, value: i.value } : null;
    })()`);
    check("model field present & editable", !!modelInput && modelInput.tagName === "INPUT" && !modelInput.disabled && modelInput.value.length > 0);
    await evalJs(`document.querySelector('#modal [data-s="server"][data-k="model"]').value = "test-model-xyz"`);
    await evalJs("document.querySelector('#modal .yes').click()");
    await wait(800);
    const savedMsg = String(await evalJs("document.getElementById('chat').textContent"));
    check("settings saved", savedMsg.includes("settings saved"));
    const savedVal = await evalJs(`(() => {
      document.getElementById('settings-btn').click();
      return new Promise(r => setTimeout(() => {
        const i = document.querySelector('#modal [data-s="server"][data-k="model"]');
        r(i ? i.value : null);
      }, 800));
    })()`);
    check("saved model value persists", savedVal === "test-model-xyz");
    await evalJs("document.querySelector('#modal .no').click()");
    await wait(300);

    // switch to local and Apply: local backend spawned + ws connected,
    // project reopened, no RPC errors, dialog closes
    await evalJs("document.getElementById('settings-btn').click()");
    await wait(500);
    await evalJs("[...document.querySelectorAll('#modal .tabs button')].find(b => b.dataset.p === 'connection').click()");
    await wait(500);
    await evalJs("document.querySelector('#modal .c-mode').value = 'local'");
    await evalJs("document.querySelector('#modal .c-apply').click()");
    await wait(500);
    const msg = String(await evalJs("document.querySelector('#modal .page[data-p=connection] .msg') && document.querySelector('#modal .page[data-p=connection] .msg').textContent"));
    check("apply local: no error message", !msg.includes("Error") && !msg.includes("not connected"));
    let closed = false;
    for (let i = 0; i < 20; i++) {
      closed = await evalJs("!document.getElementById('modal-backdrop').classList.contains('show')");
      if (closed) break;
      await wait(250);
    }
    check("apply local: dialog closed", closed);
    // the local backend is spawned AFTER the boot's remote connect, so count
    // "starting backend" occurrences and require a "ws connected" after it
    let localUp = false;
    for (let i = 0; i < 80; i++) {
      const log = appLog();
      const idx = log.lastIndexOf("starting backend");
      if (idx >= 0 && log.indexOf("[main] ws connected", idx) > idx) { localUp = true; break; }
      await wait(500);
    }
    check("local backend spawned and ws connected", localUp);
    let reopened = false;
    for (let i = 0; i < 60; i++) {
      const t = String(await evalJs("document.getElementById('titlebar-name') && document.getElementById('titlebar-name').textContent"));
      if (t.includes("Coder Agent:")) { reopened = true; break; }
      await wait(500);
    }
    check("project reopened after switch to local", reopened);
    const chatText = String(await evalJs("document.getElementById('chat').textContent"));
    check("no 'backend not connected' in chat", !chatText.includes("backend not connected"));
    const hidden = await evalJs("document.getElementById('remote-label').classList.contains('hidden')");
    check("remote label hidden in local mode", hidden);

    // switch back to remote: reconnects to the standalone server
    await evalJs("document.getElementById('settings-btn').click()");
    await wait(500);
    await evalJs("[...document.querySelectorAll('#modal .tabs button')].find(b => b.dataset.p === 'connection').click()");
    await wait(500);
    await evalJs("document.querySelector('#modal .c-mode').value = 'remote'");
    await evalJs("document.querySelector('#modal .c-apply').click()");
    let back = false;
    for (let i = 0; i < 40; i++) {
      const l = String(await evalJs("document.getElementById('remote-label') && document.getElementById('remote-label').textContent"));
      if (l.includes(`remote: 127.0.0.1:${PORT}`)) { back = true; break; }
      await wait(500);
    }
    check("switch back to remote: reconnected + label shown", back);
  } finally {
    cdp.close();
    await killApp(app);
    stopSrv();
    process.exit(failed ? 1 : 0);
  }
})().catch(async (e) => { console.error(e); await killApp(app); stopSrv(); process.exit(1); });
