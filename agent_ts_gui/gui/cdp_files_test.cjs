// Smoke test for the Files tree + Diff view (left "Files" tab, mid Chat/Diff tabs).
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");
const os = require("os");
const path = require("path");
const electron = require("electron");
const WebSocket = require("ws");

// a scratch project with one file, plus a pre-seeded session whose turn t1
// modified src/mod.txt (snapshot of the original content stored in .agent)
const testRoot = fs.mkdtempSync(path.join(os.tmpdir(), "agent-files-test-"));
fs.mkdirSync(path.join(testRoot, "src"), { recursive: true });
fs.writeFileSync(path.join(testRoot, "src", "hello.txt"), "line1\nline2\nline3\n");
fs.writeFileSync(path.join(testRoot, "src", "mod.txt"), "changed line\n");
fs.writeFileSync(path.join(testRoot, "plain.txt"), "untouched\n");
const sid = "20260101-000000-test01";
const cache = path.join(testRoot, ".agent");
fs.mkdirSync(path.join(cache, "sessions"), { recursive: true });
fs.mkdirSync(path.join(cache, "snapshots", sid, "t1", "blobs"), { recursive: true });
fs.writeFileSync(path.join(cache, "snapshots", sid, "t1", "blobs", "blob1"), "old line\n");
fs.writeFileSync(path.join(cache, "snapshots", sid, "t1", "manifest.json"),
  JSON.stringify({ files: { "src/mod.txt": { existed: true, blob: "blob1" } } }));
fs.writeFileSync(path.join(cache, "sessions", sid + ".json"), JSON.stringify({
  id: sid, head: "t1",
  turns: [{ id: "t1", parent_id: null, prompt: "seed turn", task_type: "general",
    messages: [], base_messages: null, archive: [], files_changed: ["src/mod.txt"],
    ran_commands: false, status: "done", final_text: "", subtasks: [], created: 1 }],
}));

const proc = spawn(electron, [".", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=9347", "--user-data-dir=" + fs.mkdtempSync(path.join(os.tmpdir(), "agent-files-ud-")),
  "--root", testRoot], {
  cwd: __dirname,
  env: { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: "1" },
  stdio: ["ignore", "pipe", "pipe"],
});
let backendLog = "";
proc.stdout.on("data", (d) => { backendLog += d; });
proc.stderr.on("data", (d) => { backendLog += d; });

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
let failed = false;
const check = (name, ok, extra = "") => {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${extra ? "  (" + extra + ")" : ""}`);
  if (!ok) failed = true;
};

function getWsUrl() {
  return new Promise((resolve) => {
    http.get("http://127.0.0.1:9347/json", (res) => {
      let body = "";
      res.on("data", (d) => { body += d; });
      res.on("end", () => {
        try {
          const list = JSON.parse(body);
          const page = list.find((t) => t.type === "page");
          resolve(page ? page.webSocketDebuggerUrl : null);
        } catch { resolve(null); }
      });
    }).on("error", () => resolve(null));
  });
}

(async () => {
  let url = null;
  for (let i = 0; i < 100; i++) { url = await getWsUrl(); if (url) break; await wait(200); }
  if (!url) throw new Error("no devtools url");
  const ws = new WebSocket(url, { perMessageDeflate: false });
  let id = 0;
  const pending = new Map();
  ws.on("message", (buf) => {
    const msg = JSON.parse(buf);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  });
  const send = (method, params = {}) => new Promise((resolve) => {
    id += 1;
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });
  await new Promise((r) => ws.on("open", r));
  // tap all renderer RPC replies (CDP hook) to debug lost responses
  await send("Runtime.evaluate", { expression: `
    window.__rpcLog = [];
    window.api.onReply((m) => window.__rpcLog.push(JSON.stringify(m).slice(0, 120)));
    true`, returnByValue: true });
  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
    return r.result && r.result.result ? r.result.result.value : undefined;
  };

  // wait for the project to open (tree loaded)
  let opened = false;
  for (let i = 0; i < 120; i++) {
    opened = !!(await evalJs(`document.querySelector('#file-tree .tree-row') ? true : false`));
    if (opened) break;
    await wait(500);
  }
  check("project opened, tree has rows", opened);

  // 1) Files tab exists in the left tabs
  check("left Files tab exists", !!(await evalJs(`!!document.querySelector('#left-tabs .tab[data-tab="files"]')`)));

  // 1b) open the seeded session (the boot session is fresh, the seeded one holds the turn)
  await evalJs(`document.querySelector('#left-tabs .tab[data-tab="sessions"]').click()`);
  let seeded = false;
  for (let i = 0; i < 40; i++) {
    seeded = !!(await evalJs(`(() => {
      const li = Array.from(document.querySelectorAll('#session-list li')).find(e => e.textContent.includes('seed turn'));
      if (!li) return false;
      li.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
      return true;
    })()`));
    if (seeded) break;
    await wait(500);
  }
  check("seeded session opened", seeded);
  await wait(800);

  // 2) switch to the Files tab and verify the tree shows src/
  await evalJs(`document.querySelector('#left-tabs .tab[data-tab="files"]').click()`);
  await wait(300);
  const treeNames = await evalJs(`Array.from(document.querySelectorAll('#file-tree .tree-row')).map(e => e.textContent)`);
  check("tree shows src dir", (treeNames ?? []).some((n) => n.includes("src")), JSON.stringify(treeNames));

  // 2b) src/ contains a changed file: it must be auto-expanded (arrow ▾, children visible)
  let autoOpen = false;
  for (let i = 0; i < 20; i++) {
    autoOpen = !!(await evalJs(`(() => {
      const row = Array.from(document.querySelectorAll('#file-tree .tree-row.dir')).find(e => e.textContent.includes('src'));
      if (!row) return false;
      const child = row.nextElementSibling;
      return row.querySelector('.tree-arrow').textContent === '▾'
        && child && !child.classList.contains('hidden')
        && child.textContent.includes('mod.txt');
    })()`));
    if (autoOpen) break;
    await wait(300);
  }
  check("dir with changes is auto-expanded", autoOpen);
  check("changed file has changed style + icon", !!(await evalJs(`(() => {
      const row = Array.from(document.querySelectorAll('#file-tree .tree-row')).find(e => e.textContent.includes('mod.txt'));
      return row && row.classList.contains('changed') && row.querySelector('.tree-ico').textContent === '✎';
    })()`)));
  check("unchanged file has plain icon", !!(await evalJs(`(() => {
      const row = Array.from(document.querySelectorAll('#file-tree .tree-row')).find(e => e.textContent.includes('hello.txt'));
      return row && !row.classList.contains('changed') && row.querySelector('.tree-ico').textContent === '📄';
    })()`)));

  // 3) expand a plain dir (hello.txt already visible via auto-expand; toggle it closed and open)
  await evalJs(`document.querySelector('#file-tree .tree-row.dir').click()`); // collapse
  await wait(200);
  await evalJs(`document.querySelector('#file-tree .tree-row.dir').click()`); // re-expand
  let fileRow = false;
  for (let i = 0; i < 20; i++) {
    fileRow = !!(await evalJs(`Array.from(document.querySelectorAll('#file-tree .tree-row')).find(e => e.textContent.includes('hello.txt'))`));
    if (fileRow) break;
    await wait(300);
  }
  check("collapsing/expanding src keeps working", fileRow);

  // 4) click the unchanged file -> mid switches to Diff tab, plain content shown
  await evalJs(`Array.from(document.querySelectorAll('#file-tree .tree-row')).find(e => e.textContent.includes('hello.txt')).click()`);
  let diffShown = false;
  for (let i = 0; i < 20; i++) {
    diffShown = !!(await evalJs(`!document.getElementById('diff-view').classList.contains('hidden')`));
    if (diffShown) break;
    await wait(300);
  }
  check("mid switched to Diff view", diffShown);
  let diffText = "";
  for (let i = 0; i < 20; i++) {
    diffText = await evalJs(`document.getElementById('diff-body').textContent`) ?? "";
    if (!diffText.includes("loading...") && diffText.includes("line2")) break;
    await wait(300);
  }
  check("diff shows file content", diffText.includes("line2"), diffText.slice(0, 80));
  check("diff turn select lists the seeded turn", !!(await evalJs(`document.getElementById('diff-turn-select').textContent.includes('seed turn')`)));

  // 4b) click the changed file -> real diff with deleted + added lines
  await evalJs(`Array.from(document.querySelectorAll('#file-tree .tree-row')).find(e => e.textContent.includes('mod.txt')).click()`);
  let diffOk = false;
  for (let i = 0; i < 20; i++) {
    diffOk = !!(await evalJs(`(() => {
      const body = document.getElementById('diff-body');
      return body.querySelector('.dline.del') && body.querySelector('.dline.add')
        && body.querySelector('.dline.del .dtext').textContent === 'old line'
        && body.querySelector('.dline.add .dtext').textContent === 'changed line';
    })()`));
    if (diffOk) break;
    await wait(300);
  }
  check("changed file shows del/add diff lines", diffOk);
  if (!diffOk) {
    console.log("RPC log:", (await evalJs(`window.__rpcLog.slice(-15)`)).join("\n  "));
    console.log("backend:", backendLog.slice(-1500));
  }

  // 5) Chat tab still works: switch back and forth
  await evalJs(`document.querySelector('#mid-tabs .tab[data-midtab="chat"]').click()`);
  await wait(200);
  check("back to chat works", !!(await evalJs(`!document.getElementById('chat').classList.contains('hidden') && document.getElementById('diff-view').classList.contains('hidden')`)));
  await evalJs(`document.querySelector('#mid-tabs .tab[data-midtab="diff"]').click()`);
  await wait(200);
  check("back to diff works", !!(await evalJs(`!document.getElementById('diff-view').classList.contains('hidden')`)));

  // 6) tree refresh button does not break
  await evalJs(`document.getElementById('tree-refresh-btn').click()`);
  await wait(500);
  check("tree refresh keeps rows", !!(await evalJs(`document.querySelectorAll('#file-tree .tree-row').length > 0`)));

  ws.close();
  proc.kill("SIGTERM");
  await wait(1500);
  if (proc.exitCode === null) proc.kill("SIGKILL");
  fs.rmSync(testRoot, { recursive: true, force: true });
  console.log(failed ? "\nRESULT: FAILED" : "\nRESULT: OK");
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); proc.kill("SIGKILL"); process.exit(1); });
