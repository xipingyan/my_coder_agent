// Smoke test for markdown rendering in the chat pane (user prompts +
// assistant replies, live streaming and session replay).
//
// The seeded turn's prompt and assistant content contain markdown (heading,
// bold, fenced code, table) plus a <script> tag that must be escaped, not
// executed. The live path is exercised by calling window.__onEvent directly
// (no backend turn needed).
const { spawnApp, killApp, getWsUrl, connectCdp, wait } = require("./cdp_test_lib.cjs");
const fs = require("fs");
const os = require("os");
const path = require("path");

const testRoot = fs.mkdtempSync(path.join(os.tmpdir(), "agent-md-test-"));
fs.writeFileSync(path.join(testRoot, "hello.txt"), "hi\n");
const sid = "20260101-000000-mdtest";
const cache = path.join(testRoot, ".agent");
fs.mkdirSync(path.join(cache, "sessions"), { recursive: true });
const mdPrompt =
  "# Prompt Title\n\n**bold word** and a link\n\n```\nplain code\n```\n\n<script>window.__mdXSS=1</script>";
const mdContent =
  "## Reply\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```python\ndef f():\n    return 1\n```\n";
fs.writeFileSync(path.join(cache, "sessions", sid + ".json"), JSON.stringify({
  id: sid, head: "t1",
  turns: [{ id: "t1", parent_id: null, prompt: mdPrompt, task_type: "general",
    messages: [{ role: "assistant", content: mdContent }],
    base_messages: null, archive: [], files_changed: [],
    ran_commands: false, status: "done", final_text: "", subtasks: [], created: 1 }],
}));

const app = spawnApp({ root: testRoot });

let failed = false;
const check = (name, ok, extra = "") => {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${extra ? "  (" + extra + ")" : ""}`);
  if (!ok) failed = true;
};

(async () => {
  const url = await getWsUrl(app.port);
  if (!url) throw new Error("no devtools url");
  const cdp = connectCdp(url);
  await cdp.opened;
  const evalJs = cdp.evalJs;

  // wait for the project to open
  let opened = false;
  for (let i = 0; i < 120; i++) {
    opened = !!(await evalJs(`document.querySelector('#file-tree .tree-row') ? true : false`));
    if (opened) break;
    await wait(500);
  }
  check("project opened", opened);

  // open the seeded session (the boot session is fresh)
  await evalJs(`document.querySelector('#left-tabs .tab[data-tab="sessions"]').click()`);
  let seeded = false;
  for (let i = 0; i < 40; i++) {
    seeded = !!(await evalJs(`(() => {
      const li = Array.from(document.querySelectorAll('#session-list li')).find(e => e.textContent.includes('Prompt Title'));
      if (!li) return false;
      li.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
      return true;
    })()`));
    if (seeded) break;
    await wait(500);
  }
  check("seeded session opened", seeded);
  await wait(800);

  // 1) replay: user prompt rendered as markdown
  let replay = false;
  for (let i = 0; i < 20; i++) {
    replay = !!(await evalJs(`(() => {
      const chat = document.getElementById('chat');
      return chat.querySelector('.md h1') && chat.querySelector('.md h1').textContent === 'Prompt Title'
        && chat.querySelector('.md strong') && chat.querySelector('.md strong').textContent === 'bold word';
    })()`));
    if (replay) break;
    await wait(300);
  }
  check("replayed prompt: h1 + strong rendered", replay);

  // 2) replay: assistant content rendered (table + highlighted code)
  let assistant = false;
  for (let i = 0; i < 20; i++) {
    assistant = !!(await evalJs(`(() => {
      const chat = document.getElementById('chat');
      return chat.querySelector('.md table') && chat.querySelector('.md th')
        && chat.querySelector('.md pre code.lang-python')
        && chat.querySelector('.md pre .hljs-keyword');
    })()`));
    if (assistant) break;
    await wait(300);
  }
  check("replayed assistant: table + hljs code rendered", assistant);

  // 3) XSS: the <script> in the prompt must be escaped, not executed
  check("script tag not executed", (await evalJs(`window.__mdXSS === undefined`)));
  check("script tag shown as text", (await evalJs(`document.getElementById('chat').textContent.includes('<script>')`)));
  check("no script element in chat", !!(await evalJs(`!document.getElementById('chat').querySelector('script')`)));

  // 4) live streaming: feed events directly (no backend turn needed)
  await evalJs(`(() => {
    window.__mdOpenRequests = [];
    window.__onEvent({ event: 'turn_started', agent_id: 'main', ts: Date.now()/1000,
      data: { prompt: '# Live Prompt' } });
    window.__onEvent({ event: 'content_delta', agent_id: 'main', ts: Date.now()/1000,
      data: { text: '**par' } });
    window.__onEvent({ event: 'content_delta', agent_id: 'main', ts: Date.now()/1000,
      data: { text: 'tial bold** done [link](https://example.com/x) ' } });
    var fence = String.fromCharCode(96) + String.fromCharCode(96) + String.fromCharCode(96);
    window.__onEvent({ event: 'content_delta', agent_id: 'main', ts: Date.now()/1000,
      data: { text: '\\n\\n' + fence + 'python\\ns = "a & b"\\n' + fence + '\\n' } });
    return true;
  })()`);
  let live = false;
  for (let i = 0; i < 20; i++) {
    live = !!(await evalJs(`(() => {
      const chat = document.getElementById('chat');
      const h = Array.from(chat.querySelectorAll('.md h1')).find(e => e.textContent === 'Live Prompt');
      const s = Array.from(chat.querySelectorAll('.md strong')).find(e => e.textContent === 'partial bold');
      return h && s;
    })()`));
    if (live) break;
    await wait(300);
  }
  check("streaming: h1 + split-bold rendered", live);

  // 4b) link in markdown opens via external browser, not in the app window
  await evalJs(`(() => {
    const a = Array.from(document.querySelectorAll('#chat .md a')).find(e => e.textContent === 'link');
    if (a) a.click();
    return true;
  })()`);
  check("link click routed to openExternal", (await evalJs(`(window.__mdOpenRequests || []).includes('https://example.com/x')`)));

  // 4c) code fence: the raw & in source is highlighted, not shown as &amp;
  let codeOk = false;
  for (let i = 0; i < 10; i++) {
    codeOk = !!(await evalJs(`(() => {
      const pres = document.querySelectorAll('#chat .md pre code');
      const c = pres[pres.length - 1];
      return c && c.textContent.includes('a & b') && !c.textContent.includes('&amp;');
    })()`));
    if (codeOk) break;
    await wait(200);
  }
  check("code fence: & kept literal (not &amp;)", codeOk);

  // 5) tool lines stay plain mono text (not markdown)
  await evalJs(`(() => {
    window.__onEvent({ event: 'tool_call', agent_id: 'main', ts: Date.now()/1000,
      data: { name: 'read_file', args: { path: '/tmp/x.md' }, id: 't1' } });
    return true;
  })()`);
  await wait(300);
  check("tool line is plain span, not md", !!(await evalJs(`(() => {
    const spans = Array.from(document.getElementById('chat').children);
    const t = spans.find(e => e.tagName === 'SPAN' && e.textContent.includes('read_file'));
    return t && !t.querySelector('.md');
  })()`)));

  // 6) textContent still carries the content (other tests rely on it)
  check("textContent has prompt + reply", !!(await evalJs(`(() => {
    const t = document.getElementById('chat').textContent;
    return t.includes('Prompt Title') && t.includes('bold word') && t.includes('partial bold');
  })()`)));

  cdp.close();
  await killApp(app);
  fs.rmSync(testRoot, { recursive: true, force: true });
  console.log(failed ? "\nRESULT: FAILED" : "\nRESULT: OK");
  process.exit(failed ? 1 : 0);
})().catch(async (e) => { console.error(e); await killApp(app); process.exit(1); });
