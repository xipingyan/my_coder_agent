// Coder Agent GUI - main window: layout, RPC client, live event rendering.
import type { AgentEvent, ReplyMsg } from "../types";

// ------------------------------------------------------------------ helpers
const $ = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;

function short(text: string, n: number): string {
  const t = String(text).replace(/\s+/g, " ");
  return t.length <= n ? t : t.slice(0, n - 1) + "…";
}

function argsSummary(name: string, args: Record<string, any>): string {
  if (name === "run_command") return String(args.command ?? "");
  if (name === "read_file" || name === "list_dir" || name === "write_file" || name === "apply_patch") {
    let extra = "";
    if (name === "read_file" && args.start_line) extra = ` [${args.start_line}-${args.end_line ?? ""}]`;
    return String(args.path ?? ".") + extra;
  }
  return short(JSON.stringify(args), 140);
}

function fmtTok(n: number): string {
  return n >= 1024 ? `${(n / 1024).toFixed(1)}k` : String(Math.round(n));
}

// log panes: append-only, stick to the bottom while the user is at the bottom
class Pane {
  el: HTMLElement;
  constructor(el: HTMLElement) { this.el = el; }
  clear() { this.el.textContent = ""; }
  write(text: string, cls = "", mono = false) {
    const atBottom = this.el.scrollHeight - this.el.scrollTop - this.el.clientHeight < 8;
    const span = document.createElement("span");
    if (cls) span.className = cls;
    if (mono) span.classList.add("m");
    span.textContent = text;
    this.el.appendChild(span);
    if (atBottom) this.el.scrollTop = this.el.scrollHeight;
  }
  line(text = "", cls = "", mono = false) {
    this.write(text + "\n", cls, mono);
  }
}

const chat = new Pane($("chat"));
const activity = new Pane($("tab-activity"));
const thinking = new Pane($("tab-thinking"));
const commands = new Pane($("tab-commands"));
const docs = new Pane($("tab-docs"));
const planView = $("tab-plan");

// ------------------------------------------------------------------- state
const S = {
  root: null as string | null,
  taskTypes: [] as string[],
  effort: "medium",
  mode: 0, // 0 = single agent, 1 = plan & subtasks
  busy: false,
  ctxMax: 131072,
  compressThreshold: 0.95,
  compressTarget: 0.5,
  config: null as Record<string, any> | null,
  state: "idle",
  stateDetail: "",
  stateSince: 0,
  thinkChars: 0,
  block: null as string | null,
  pendingDocs: new Map<string, string>(),
  planDict: null as Record<string, any> | null,
};

// --------------------------------------------------------------- rpc client
let nextId = 0;
const pending = new Map<number, (r: any) => void>();
window.api.onReply((msg: ReplyMsg) => {
  const cb = pending.get(msg.id);
  if (!cb) return;
  pending.delete(msg.id);
  if (msg.error) cb({ __error: msg.error });
  else cb(msg.result);
});

function call(method: string, params: Record<string, any> = {}): Promise<any> {
  return new Promise((resolve) => {
    nextId += 1;
    pending.set(nextId, resolve);
    const p = window.api.send({ id: nextId, method, params });
    if (p && typeof p.catch === "function") {
      p.catch((e: any) => {
        if (pending.has(nextId)) { pending.delete(nextId); resolve({ __error: `backend not connected (${e?.message ?? e})` }); }
      });
    }
    setTimeout(() => {
      if (pending.has(nextId)) { pending.delete(nextId); resolve({ __error: "request timed out" }); }
    }, 120000);
  });
}

// ------------------------------------------------------------------- status
const STATE_TEXT: Record<string, string> = {
  waiting_model: "Waiting for model",
  running_tool: "Running tool",
  waiting_user: "Waiting for your approval",
  idle: "Idle",
};

function setState(state: string, detail: string) {
  S.state = state;
  S.stateDetail = detail;
  S.stateSince = Date.now();
  tick();
}

function tick() {
  let text = STATE_TEXT[S.state] ?? S.state;
  if (S.state !== "idle") {
    if (S.stateDetail) text += `: ${S.stateDetail}`;
    text += `  (${Math.floor((Date.now() - S.stateSince) / 1000)}s)`;
    if (S.state === "waiting_model" && S.thinkChars) text += `  thinking ${S.thinkChars} chars`;
  }
  $("state-label").textContent = text;
}
setInterval(tick, 1000);

function setCtxBar(tokens: number, max: number, exact: boolean) {
  const pct = Math.min(100, Math.round((tokens / Math.max(1, max)) * 100));
  $("ctx-fill").style.width = pct + "%";
  const mark = exact ? "" : "~";
  $("ctx-text").textContent = tokens
    ? `context ${mark}${fmtTok(tokens)} / ${fmtTok(max)} (${pct}%)`
    : `context 0 / ${fmtTok(max)}`;
  $("ctx-bar").title =
    `Current cache length: ${tokens.toLocaleString()} tokens (${exact ? "reported by the server" : "local estimate"})\n` +
    `Configured maximum (context.max_tokens): ${max.toLocaleString()}\n` +
    `Compression starts at ${Math.round(S.compressThreshold * 100)}% = ${Math.round(max * S.compressThreshold).toLocaleString()} tokens` +
    ` and shrinks to ${Math.round(S.compressTarget * 100)}%.`;
}

function refreshContextEstimate() {
  call("context_estimate").then((r) => {
    if (r && !r.__error) setCtxBar(r.tokens, r.max_tokens, false);
  });
}

async function refreshServerInfo() {
  const btn = $("server-ctx-btn");
  btn.textContent = "server cache: checking...";
  btn.className = "flat";
  btn.title = "";
  const r = await call("context_info");
  if (r && r.__error) { btn.textContent = "server cache: ?"; return; }
  const info = r.context;
  const mx = r.configured_max;
  if (!info.loaded && !info.model_max) {
    btn.textContent = "server cache: unknown";
    btn.title = `${r.context_text}\nClick to retry.`;
    return;
  }
  const parts: string[] = [];
  if (info.loaded) parts.push(`server cache ${fmtTok(info.loaded)}` + (info.loaded_is_configured ? " (configured)" : ""));
  if (info.model_max) parts.push(`model max ${fmtTok(info.model_max)}`);
  btn.textContent = parts.join(" | ");
  const tip = [r.context_text, `Configured context.max_tokens: ${mx.toLocaleString()}`];
  if (info.loaded && mx > info.loaded) {
    btn.className = "flat bad";
    tip.push("WARNING: the configured maximum is larger than the server's cache, so compression would start " +
      "too late and requests may be truncated. Settings > Context & agent > Detect.");
  } else if (info.loaded && mx !== info.loaded) {
    btn.className = "flat warn";
    tip.push("The configured maximum is smaller than the server's cache (safe, but part of it is unused). " +
      "Settings > Context & agent > Detect.");
  }
  tip.push("Click to refresh.");
  btn.title = tip.join("\n");
}

// ------------------------------------------------------------------ toolbar
function setBusy(busy: boolean) {
  S.busy = busy;
  ($("send-btn") as HTMLButtonElement).disabled = busy;
  ($("stop-btn") as HTMLButtonElement).disabled = !busy;
  ($("input") as HTMLTextAreaElement).readOnly = busy;
}

function reloadTaskTypes() {
  const sel = $("task-combo") as HTMLSelectElement;
  const cur = sel.value;
  sel.innerHTML = "<option>(auto)</option>" + S.taskTypes.map((n) => `<option>${n}</option>`).join("");
  if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
}

async function openProject(root: string, sessionId: string | null = null) {
  const r = await call("open_project", { root, session_id: sessionId });
  if (r && r.__error) { chat.line(`\n[error] open project: ${r.__error}`, "c-error"); return; }
  if (backendDown) {
    backendDown = false;
    const banner = document.getElementById("backend-banner");
    if (banner) banner.remove();
  }
  S.root = r.root;
  S.taskTypes = r.task_types;
  S.effort = r.effort;
  S.ctxMax = r.config?.context?.max_tokens ?? S.ctxMax;
  S.compressThreshold = r.config?.context?.compress_threshold ?? 0.95;
  S.compressTarget = r.config?.context?.compress_target ?? 0.5;
  S.config = r.config;
  $("root-label").textContent = ` Project: ${r.root} `;
  $("root-label").title = r.root;
  document.title = `Coder Agent - ${r.root.split(/[\\/]/).pop()}`;
  ($("effort-combo") as HTMLSelectElement).value = S.effort;
  window.api.setLastProject(r.root);
  reloadTaskTypes();
  resetViews();
  await replaySession();
  await refreshTurnList();
  await refreshSessionList();
  refreshContextEstimate();
  refreshServerInfo();
}

function resetViews() {
  chat.clear(); activity.clear(); thinking.clear(); docs.clear();
  planView.innerHTML = "";
  S.planDict = null;
  S.pendingDocs.clear();
  S.block = null;
  S.thinkChars = 0;
  $("llm-label").textContent = "";
  setState("idle", "");
}

// ------------------------------------------------------- chat rendering
function chatTool(name: string, args: Record<string, any>, agent = "main") {
  const who = agent === "main" ? "" : `[${agent}] `;
  chat.line(`\n> ${who}${name}  ${short(argsSummary(name, args), 160)}`, "c-tool", true);
  S.block = null;
}

function chatResult(ok: boolean, text: string) {
  chat.line("  " + short(text, 300), ok ? "c-muted" : "c-error", true);
}

function renderTurn(t: { prompt: string; task_type: string; messages: any[]; status: string }) {
  chat.line(`\nYou  [${t.task_type}]`, "c-user");
  chat.line(t.prompt);
  for (const m of t.messages) {
    if (m.role === "assistant") {
      if (m.content) {
        chat.line("\nAssistant", "c-ok b");
        chat.line(m.content);
      }
      for (const tc of m.tool_calls ?? []) {
        let args: any = {};
        try { args = JSON.parse(tc.function.arguments || "{}"); } catch { args = { raw: tc.function.arguments }; }
        chatTool(tc.function.name, typeof args === "object" && args ? args : {});
      }
    } else if (m.role === "tool") {
      const c = m.content ?? "";
      chatResult(!c.startsWith("ERROR") && !c.startsWith("DENIED") && !c.startsWith("REJECTED"), c);
    }
  }
  chat.line(`-- ${t.status} --`, "c-muted");
}

async function replaySession() {
  const r = await call("session_detail");
  if (!r || r.__error) return;
  for (const t of r.turns) renderTurn(t);
  chat.el.scrollTop = chat.el.scrollHeight;
}

// ------------------------------------------------- left panels (turns / sessions)
async function refreshTurnList() {
  const r = await call("session_turns");
  if (!r || r.__error) return;
  const ul = $("turn-list");
  (ul as any).__turns = r.turns;
  ul.innerHTML = "";
  for (const t of r.turns) {
    const li = document.createElement("li");
    li.dataset.id = t.id;
    li.title = t.prompt;
    li.textContent = t.active
      ? `${t.index}. [${t.status}] ${short(t.prompt, 60)}`
      : `(branch) [${t.status}] ${short(t.prompt, 54)}`;
    if (!t.active) li.classList.add("branch");
    ul.appendChild(li);
  }
}

async function refreshSessionList() {
  const r = await call("session_summaries");
  if (!r || r.__error) return;
  const ul = $("session-list");
  ul.innerHTML = "";
  for (const s of r.sessions) {
    const when = new Date(s.updated * 1000);
    const stamp = `${String(when.getMonth() + 1).padStart(2, "0")}-${String(when.getDate()).padStart(2, "0")} ` +
      `${String(when.getHours()).padStart(2, "0")}:${String(when.getMinutes()).padStart(2, "0")}`;
    const li = document.createElement("li");
    li.textContent = `${s.id === r.current ? "● " : ""}${stamp}  ${s.title || "(empty)"}`;
    li.title = `${s.title}\n${s.turns} turn(s), last status: ${s.status}\n${s.id}`;
    if (s.id === r.current) li.classList.add("current");
    li.addEventListener("dblclick", () => {
      if (S.busy || s.id === r.current) return;
      openProject(S.root!, s.id).then(() => switchLeftTab("turns"));
    });
    li.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      showMenu(e.clientX, e.clientY, [
        { label: "Open", enabled: !S.busy && s.id !== r.current,
          fn: () => openProject(S.root!, s.id).then(() => switchLeftTab("turns")) },
        { label: "Delete session...", enabled: !S.busy, fn: () => deleteSession(s.id) },
      ]);
    });
    ul.appendChild(li);
  }
}

async function deleteSession(sid: string) {
  const ok = await confirmModal("Delete session",
    "Delete this session and its file snapshots?\nFiles in the project are not touched, but this " +
    "session can no longer be rewound.");
  if (!ok) return;
  const r = await call("delete_session", { session_id: sid });
  if (r && r.__error) { chat.line(`\n[error] ${r.__error}`, "c-error"); return; }
  if (r.new_session) {
    // the current session was deleted: the backend started a fresh one
    resetViews();
    await refreshTurnList();
    await refreshSessionList();
    refreshContextEstimate();
    chat.line("\n[new session started]", "c-muted");
  } else {
    await refreshSessionList();
  }
}

// ------------------------------------------------- context menu + modal
function showMenu(x: number, y: number, items: { label: string; enabled?: boolean; fn?: () => void }[]) {
  const menu = $("ctxmenu");
  menu.innerHTML = "";
  for (const it of items) {
    const el = document.createElement("div");
    el.className = "item" + (it.enabled === false ? " disabled" : "");
    el.textContent = it.label;
    if (it.enabled !== false && it.fn) el.addEventListener("click", () => { hideMenu(); it.fn!(); });
    menu.appendChild(el);
  }
  menu.style.display = "block";
  const rect = menu.getBoundingClientRect();
  menu.style.left = Math.min(x, window.innerWidth - rect.width - 4) + "px";
  menu.style.top = Math.min(y, window.innerHeight - rect.height - 4) + "px";
}
function hideMenu() { $("ctxmenu").style.display = "none"; }
document.addEventListener("click", hideMenu);
document.addEventListener("contextmenu", (e) => {
  // close the menu unless the event is on the menu itself
  if (!(e.target as HTMLElement).closest("#ctxmenu")) hideMenu();
});
window.addEventListener("blur", hideMenu);

function confirmModal(title: string, text: string): Promise<boolean> {
  return new Promise((resolve) => {
    const modal = $("modal");
    modal.innerHTML = `<div class="head"></div><div class="body"></div>` +
      `<div class="foot"><button class="no">Cancel</button><button class="primary yes">OK</button></div>`;
    modal.querySelector(".head")!.textContent = title;
    modal.querySelector(".body")!.textContent = text;
    $("modal-backdrop").classList.add("show");
    const done = (v: boolean) => { $("modal-backdrop").classList.remove("show"); resolve(v); };
    modal.querySelector(".yes")!.addEventListener("click", () => done(true));
    modal.querySelector(".no")!.addEventListener("click", () => done(false));
  });
}

function editPromptModal(title: string, info: string, prompt: string): Promise<{ prompt: string; keepBranch: boolean } | null> {
  return new Promise((resolve) => {
    const modal = $("modal");
    modal.innerHTML =
      `<div class="head"></div>` +
      `<div class="body"><div class="info"></div><textarea></textarea>` +
      `<label class="check"><input type="checkbox" class="keep"/> Keep the discarded turns as an inactive branch (instead of deleting them)</label></div>` +
      `<div class="foot"><button class="no">Cancel</button><button class="primary yes">Run again</button></div>`;
    modal.querySelector(".head")!.textContent = title;
    modal.querySelector(".info")!.textContent = info;
    const ta = modal.querySelector("textarea")!;
    ta.value = prompt;
    $("modal-backdrop").classList.add("show");
    const done = (v: { prompt: string; keepBranch: boolean } | null) => {
      $("modal-backdrop").classList.remove("show");
      resolve(v);
    };
    modal.querySelector(".yes")!.addEventListener("click", () => {
      const p = ta.value.trim();
      if (!p) return;
      done({ prompt: p, keepBranch: (modal.querySelector(".keep") as HTMLInputElement).checked });
    });
    modal.querySelector(".no")!.addEventListener("click", () => done(null));
  });
}

// ------------------------------------------------- approval / plan / stage
function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function diffHtml(text: string): string {
  return text.split("\n").map((ln) => {
    if (ln.startsWith("+") && !ln.startsWith("+++")) return `<span class="add">${esc(ln)}</span>`;
    if (ln.startsWith("-") && !ln.startsWith("---")) return `<span class="del">${esc(ln)}</span>`;
    if (ln.startsWith("@@")) return `<span class="hunk">${esc(ln)}</span>`;
    return esc(ln);
  }).join("\n");
}

function approvalDialog(req: Record<string, any>): Promise<Record<string, any>> {
  return new Promise((resolve) => {
    const modal = $("modal");
    modal.className = "modal";
    const what = req.kind === "command" ? "Run command" : "Write file";
    modal.innerHTML =
      `<div class="head"></div>` +
      `<div class="body"><div class="diff"></div>` +
      `<div class="row"><label>Remember rule:</label><input type="text" class="rule"/><select class="scope"><option>project</option><option>user</option></select></div>` +
      `<input type="text" class="full feedback" placeholder="Optional feedback sent to the model when rejecting"/>` +
      `<div class="foot"><button class="reject">Reject</button><button class="allow">Allow once</button><button class="primary remember">Allow && remember rule</button></div>`;
    modal.querySelector(".head")!.textContent = `${what}: ${req.title ?? ""}`;
    modal.querySelector(".diff")!.innerHTML = diffHtml(req.detail ?? "");
    (modal.querySelector(".rule") as HTMLInputElement).value = req.suggested_rule ?? "";
    (modal.querySelector(".feedback") as HTMLInputElement).value = "";
    $("modal-backdrop").classList.add("show");
    const done = (resp: Record<string, any>) => { $("modal-backdrop").classList.remove("show"); resolve(resp); };
    modal.querySelector(".allow")!.addEventListener("click", () => done({ allow: true }));
    modal.querySelector(".remember")!.addEventListener("click", () => done({
      allow: true,
      remember_rule: (modal.querySelector(".rule") as HTMLInputElement).value.trim(),
      remember_scope: (modal.querySelector(".scope") as HTMLSelectElement).value,
    }));
    modal.querySelector(".reject")!.addEventListener("click", () => done({
      allow: false,
      feedback: (modal.querySelector(".feedback") as HTMLInputElement).value.trim(),
    }));
  });
}

function stageDialog(report: Record<string, any>): Promise<string> {
  return new Promise((resolve) => {
    const modal = $("modal");
    modal.className = "modal";
    const subs: any[] = report.subtasks ?? [];
    let anyFailed = false;
    const rows = subs.map((s) => {
      const bad = ["failed", "skipped", "cancelled"].includes(s.status);
      anyFailed = anyFailed || bad;
      const cls = bad ? "c-error" : "c-ok";
      return `<div class="${cls} b">${esc(s.id)}  [${esc(s.status ?? "")}]  ${esc(s.title ?? "")}</div>` +
        `<div>${esc(s.summary || "(no summary)")}</div><div>&nbsp;</div>`;
    }).join("");
    modal.innerHTML =
      `<div class="head"></div>` +
      `<div class="body">${rows}` +
      `<div class="b">Files changed so far by this plan:</div><div class="diff"></div></div>` +
      `<div class="foot"><button class="stop">Stop here</button><button class="retry">Retry failed</button><button class="primary cont">Continue</button></div>`;
    modal.querySelector(".head")!.textContent = `Stage ${(report.index ?? 0) + 1}/${report.total ?? 0} finished`;
    modal.querySelector(".diff")!.textContent = (report.changed_files ?? []).join("\n") || "(none)";
    const cont = modal.querySelector(".cont") as HTMLButtonElement;
    cont.textContent = report.is_last ? "Finish" : `Continue to stage ${(report.index ?? 0) + 2}`;
    (modal.querySelector(".retry") as HTMLButtonElement).disabled = !anyFailed;
    $("modal-backdrop").classList.add("show");
    const done = (v: string) => { $("modal-backdrop").classList.remove("show"); resolve(v); };
    modal.querySelector(".cont")!.addEventListener("click", () => done("continue"));
    modal.querySelector(".retry")!.addEventListener("click", () => done("retry"));
    modal.querySelector(".stop")!.addEventListener("click", () => done("stop"));
  });
}

function planDialog(plan: Record<string, any>, typeNames: string[]): Promise<Record<string, any> | null> {
  return new Promise((resolve) => {
    const modal = $("modal");
    modal.className = "modal wide";
    const subs: any[] = ((plan.subtasks ?? []) as any[]).map((s: any) => ({
      id: s.id ?? "", title: s.title ?? "", goal: s.goal ?? "",
      depends_on: (s.depends_on ?? []).join(", "), task_type: s.task_type ?? "general",
      files: (s.files ?? []).join(", "),
    }));
    const opts = typeNames.map((n) => `<option>${esc(n)}</option>`).join("");
    modal.innerHTML =
      `<div class="head"></div>` +
      `<div class="body"><div class="info">The plan below was generated for your goal. Edit, add or remove subtasks, then run it. Subtasks without dependencies between them may run in parallel.</div>` +
      `<table class="plan-table"><thead><tr><th>id</th><th>title</th><th>depends on</th><th>task type</th><th>files</th><th class="goal-cell">goal</th></tr></thead><tbody></tbody></table>` +
      `<div class="table-tools"><button class="add">Add subtask</button><button class="rem">Remove selected</button></div></div>` +
      `<div class="foot"><button class="no">Cancel</button><button class="primary yes">Run plan</button></div>`;
    modal.querySelector(".head")!.textContent = "Review plan";
    const tbody = modal.querySelector("tbody")!;
    subs.forEach((s) => tbody.insertAdjacentHTML("beforeend", planRow(s, opts)));

    function planRow(s: any, o: string): string {
      const sel = o.replace(new RegExp(`<option>${esc(s.task_type)}</option>`), `<option selected>${esc(s.task_type)}</option>`);
      return `<tr><td><input class="c-id" value="${esc(s.id)}"/></td>` +
        `<td><input class="c-title" value="${esc(s.title)}"/></td>` +
        `<td><input class="c-deps" value="${esc(s.depends_on)}"/></td>` +
        `<td><select class="c-type">${sel}</select></td>` +
        `<td><input class="c-files" value="${esc(s.files)}"/></td>` +
        `<td class="goal-cell"><input class="c-goal" value="${esc(s.goal)}"/></td></tr>`;
    }

    modal.querySelector(".add")!.addEventListener("click", () => {
      const n = tbody.rows.length + 1;
      tbody.insertAdjacentHTML("beforeend", planRow({ id: `T${n}`, title: "", goal: "", depends_on: "", task_type: typeNames[0] ?? "general", files: "" }, opts));
    });
    modal.querySelector(".rem")!.addEventListener("click", () => {
      const sel = [...tbody.querySelectorAll("tr.selected")];
      if (sel.length) sel.forEach((tr) => tr.remove());
    });
    tbody.addEventListener("click", (e) => {
      const tr = (e.target as HTMLElement).closest("tr");
      if (!tr) return;
      const was = tr.classList.contains("selected");
      tbody.querySelectorAll("tr.selected").forEach((x) => x.classList.remove("selected"));
      if (!was) tr.classList.add("selected");
    });

    $("modal-backdrop").classList.add("show");
    const done = (v: Record<string, any> | null) => { $("modal-backdrop").classList.remove("show"); resolve(v); };
    modal.querySelector(".no")!.addEventListener("click", () => done(null));
    modal.querySelector(".yes")!.addEventListener("click", () => {
      const out = [...tbody.rows].map((tr) => {
        const g = (c: string) => (tr.querySelector(c) as HTMLInputElement | HTMLSelectElement).value.trim();
        return {
          id: g(".c-id"), title: g(".c-title"),
          depends_on: g(".c-deps") ? g(".c-deps").split(",").map((x) => x.trim()).filter(Boolean) : [],
          task_type: g(".c-type"),
          files: g(".c-files") ? g(".c-files").split(",").map((x) => x.trim()).filter(Boolean) : [],
          goal: g(".c-goal"),
        };
      }).filter((s) => s.id || s.goal);
      done({ goal: plan.goal ?? "", subtasks: out });
    });
  });
}

// ------------------------------------------------------------- settings
const SETTINGS_TIPS: Record<string, string> = {
  effort: "reasoning_effort sent to the server ('default' = send nothing)",
  send_effort: "Send the reasoning_effort request field",
  enable_thinking: "chat_template_kwargs.enable_thinking (blank = server default)",
  token_budget: "thinking_token_budget (vLLM). 0/blank = unset",
  max_think_chars: "Abort thinking and retry without it if it exceeds this many chars. 0 = unlimited",
  extra_body: "JSON merged into the request body, e.g. {\"top_k\": 20}",
  max_tokens: "Server-side context length (cache size). Compression is based on this.",
  compress_threshold: "Compress when usage reaches this fraction (0.95 = 95%)",
  compress_target: "Compress down to this fraction",
  parallelism: "Concurrent sub-agents. Keep 1 for a single-GPU Ollama.",
  bypass_proxy: "Ignore HTTP(S)_PROXY environment variables (LAN server)",
  planner_thinking: "Allow thinking while planning (slower)",
  doc_analysis: "Summarise relevant MODULE.md files before each subtask",
  confirm_each_stage: "Ask for confirmation after every stage",
};

async function settingsDialog(): Promise<boolean> {
  const strategiesR = await call("context_strategies");
  const strategies: string[] = (strategiesR && !strategiesR.__error) ? strategiesR.strategies : ["summarize", "truncate"];
  return new Promise((resolve) => {
    const modal = $("modal");
    modal.className = "modal wide";
    modal.innerHTML =
      `<div class="head">Settings</div>` +
      `<div class="settings">` +
      `<div class="tabs"><button data-p="model" class="active">Model</button><button data-p="context">Context &amp; agent</button><button data-p="prompts">Task prompts</button><button data-p="approval">Approval</button><button class="close-x" title="Close">&#10005;</button></div>` +
      `<div class="pages"><div class="page active" data-p="model"></div><div class="page" data-p="context"></div><div class="page" data-p="prompts"></div><div class="page" data-p="approval"></div></div>` +
      `</div>` +
      `<div class="foot"><button class="no">Cancel</button><button class="primary yes">Save</button></div>`;

    const cfg = JSON.parse(JSON.stringify(S.config ?? {}));
    const choices: Record<string, string[]> = {
      provider: ["openai_compat", "ollama"],
      algorithm: strategies,
    };
    const skip = new Set(["project_root"]);
    const dirtyPrompts = new Map<string, string>();

    function fieldHtml(sec: string, key: string, val: any): string {
      if (skip.has(key)) return "";
      const tip = SETTINGS_TIPS[key] ? `<span class="tip">${SETTINGS_TIPS[key]}</span>` : "";
      const label = `<span class="lbl">${key}${tip}</span>`;
      const opts = choices[key];
      if (opts) {
        return `<div class="field">${label}<select data-s="${sec}" data-k="${key}">` +
          opts.map((o) => `<option${o === val ? " selected" : ""}>${o}</option>`).join("") + `</select></div>`;
      }
      if (typeof val === "boolean") {
        return `<div class="field">${label}<input type="checkbox" data-s="${sec}" data-k="${key}"${val ? " checked" : ""}/></div>`;
      }
      if (val === null || val === undefined) {
        return `<div class="field">${label}<input type="text" data-s="${sec}" data-k="${key}" data-null="1" value=""/></div>`;
      }
      if (typeof val === "number") {
        return `<div class="field">${label}<input type="number" step="any" data-s="${sec}" data-k="${key}" value="${val}"/></div>`;
      }
      if (typeof val === "object") {
        // extra_body (dict) / prompt_dirs (list)
        const text = Array.isArray(val) ? val.join(", ") : JSON.stringify(val);
        return `<div class="field">${label}<input type="text" data-s="${sec}" data-k="${key}" data-obj="1" value="${esc(text)}"/></div>`;
      }
      return `<div class="field">${label}<input type="text" data-s="${sec}" data-k="${key}" value="${esc(String(val))}"/></div>`;
    }

    function group(sec: string, title: string): string {
      const body = Object.entries(cfg[sec] ?? {}).map(([k, v]) => fieldHtml(sec, k, v)).join("");
      return `<fieldset class="grp"><legend>${title}</legend>${body}</fieldset>`;
    }

    const modelPage = modal.querySelector<HTMLElement>('.page[data-p="model"]')!;
    modelPage.innerHTML =
      group("server", "Server") +
      `<div class="btnrow"><button class="test">Test connection / list models</button></div>` +
      `<div class="msg"></div>` +
      group("thinking", "Thinking") +
      group("sampling", "Sampling");

    const ctxPage = modal.querySelector<HTMLElement>('.page[data-p="context"]')!;
    ctxPage.innerHTML =
      group("context", "Context / compression") +
      `<div class="btnrow"><button class="detect">Detect server context length and use it for max_tokens</button></div>` +
      `<div class="msg"></div>` +
      group("agent", "Agent") +
      group("orchestrator", "Subtask planning") +
      group("paths", "Paths");

    // ---- prompts tab
    const pPage = modal.querySelector<HTMLElement>('.page[data-p="prompts"]')!;
    pPage.innerHTML =
      `<div class="toprow"><select class="ptype"></select><button class="pnew">New type...</button><button class="prevert">Revert to default</button></div>` +
      `<textarea class="code"></textarea>` +
      `<div class="tip" style="font-size:11px;color:#888;margin-top:6px">Front-matter keys: name, description, keywords, globs, effort, tools. Saved to the user prompts folder; a project can override in &lt;project&gt;/.agent/prompts/.</div>`;
    const pType = pPage.querySelector(".ptype") as HTMLSelectElement;
    const pEdit = pPage.querySelector("textarea") as HTMLTextAreaElement;
    let pCurrent: string | null = null;

    async function pSwitch(name: string) {
      if (pCurrent && pEdit.value !== (dirtyPrompts.get(pCurrent) ?? (await pText(pCurrent)))) dirtyPrompts.set(pCurrent, pEdit.value);
      pCurrent = name;
      pEdit.value = dirtyPrompts.get(name) ?? (await pText(name));
    }
    async function pText(name: string): Promise<string> {
      const r = await call("prompt_source", { name });
      return r && r.__error ? "" : r.text;
    }
    async function pLoad() {
      const names = ["base", ...S.taskTypes];
      pType.innerHTML = names.map((n) => `<option>${esc(n)}</option>`).join("");
      await pSwitch(names[0]);
    }
    pType.addEventListener("change", () => void pSwitch(pType.value));
    pPage.querySelector(".pnew")!.addEventListener("click", async () => {
      const name = window.prompt("New task type name (letters, digits, _):");
      if (!name || !/^[A-Za-z0-9_]+$/.test(name)) return;
      if (!dirtyPrompts.has(name)) {
        dirtyPrompts.set(name, `---\nname: ${name}\ndescription: \nkeywords: \nglobs: \neffort: medium\n---\nTask type: ${name}.\n`);
      }
      if (![...pType.options].some((o) => o.value === name)) pType.add(new Option(name, name));
      pType.value = name;
      await pSwitch(name);
    });
    pPage.querySelector(".prevert")!.addEventListener("click", async () => {
      if (!pCurrent) return;
      dirtyPrompts.delete(pCurrent);
      await call("revert_prompt", { name: pCurrent });
      pEdit.value = await pText(pCurrent);
    });
    void pLoad();

    // ---- approval tab
    const aPage = modal.querySelector<HTMLElement>('.page[data-p="approval"]')!;
    aPage.innerHTML = `<div class="apath" style="font-size:12px;color:#555;margin-bottom:6px"></div><textarea class="code"></textarea>`;
    const aEdit = aPage.querySelector("textarea") as HTMLTextAreaElement;
    let approvalText = "";
    void (async () => {
      const r = await call("approval_policy");
      if (r && r.__error) return;
      approvalText = r.text;
      aEdit.value = r.text;
      aPage.querySelector(".apath")!.textContent = `User-wide approval policy: ${r.path} (projects can add rules in <project>/.agent/approval.toml)`;
    })();

    // ---- tabs
    modal.querySelectorAll(".settings .tabs button[data-p]").forEach((b) => {
      b.addEventListener("click", () => {
        modal.querySelectorAll(".settings .tabs button[data-p]").forEach((x) => x.classList.remove("active"));
        (b as HTMLElement).classList.add("active");
        const name = (b as HTMLElement).dataset.p!;
        modal.querySelectorAll(".settings .page").forEach((pg) => pg.classList.toggle("active", (pg as HTMLElement).dataset.p === name));
      });
    });

    // ---- collect / save
    function collect(): Record<string, any> {
      const out: Record<string, any> = JSON.parse(JSON.stringify(cfg));
      modal.querySelectorAll<HTMLElement>("[data-s]").forEach((el) => {
        const anyEl = el as HTMLInputElement & HTMLSelectElement;
        const sec = anyEl.dataset.s!, k = anyEl.dataset.k!;
        let v: any;
        if ((anyEl as HTMLInputElement).type === "checkbox") v = (anyEl as HTMLInputElement).checked;
        else if (anyEl.dataset.obj === "1") {
          const t = (anyEl as HTMLInputElement).value.trim();
          const base = cfg[sec]?.[k];
          if (!t) v = Array.isArray(base) ? [] : {};
          else if (Array.isArray(base)) v = t.split(",").map((x) => x.trim()).filter(Boolean);
          else v = JSON.parse(t);
        } else if (anyEl.dataset.null === "1") {
          const t = (anyEl as HTMLInputElement).value.trim();
          if (!t) v = null;
          else if (!isNaN(Number(t))) v = Number(t);
          else v = t;
        } else if (typeof cfg[sec]?.[k] === "number") {
          const t = (anyEl as HTMLInputElement).value.trim();
          v = t === "" ? cfg[sec][k] : Number(t);
        } else v = (anyEl as HTMLInputElement).value;
        out[sec] = out[sec] ?? {};
        out[sec][k] = v;
      });
      return out;
    }

    function showMsg(page: HTMLElement, ok: boolean, text: string) {
      const m = page.querySelector(".msg") as HTMLElement;
      m.className = "msg " + (ok ? "ok" : "err");
      m.textContent = text;
    }

    modelPage.querySelector(".test")!.addEventListener("click", async () => {
      const m = modelPage.querySelector(".msg") as HTMLElement;
      m.className = "msg"; m.textContent = "Testing...";
      const r = await call("test_connection", { config: collect() });
      if (r && r.__error) { showMsg(modelPage, false, String(r.__error)); return; }
      showMsg(modelPage, true, `Models:\n${(r.models ?? []).join("\n")}\n\n${r.context_text ?? ""}`);
    });
    ctxPage.querySelector(".detect")!.addEventListener("click", async () => {
      const m = ctxPage.querySelector(".msg") as HTMLElement;
      m.className = "msg"; m.textContent = "Detecting...";
      const r = await call("test_connection", { config: collect() });
      if (r && r.__error) { showMsg(ctxPage, false, String(r.__error)); return; }
      const info = r.context ?? {};
      if (info.loaded) {
        cfg.context.max_tokens = info.loaded;
        const inp = ctxPage.querySelector('[data-s="context"][data-k="max_tokens"]') as HTMLInputElement;
        inp.value = String(info.loaded);
        showMsg(ctxPage, true, `${r.context_text ?? ""}\n\ncontext.max_tokens set to ${info.loaded.toLocaleString()}.`);
      } else {
        showMsg(ctxPage, false, r.context_text ?? "could not detect server context length");
      }
    });

    $("modal-backdrop").classList.add("show");
    const done = (saved: boolean) => { $("modal-backdrop").classList.remove("show"); resolve(saved); };
    modal.querySelector(".no")!.addEventListener("click", () => done(false));
    modal.querySelector(".close-x")!.addEventListener("click", () => done(false));
    modal.querySelector(".yes")!.addEventListener("click", async () => {
      // stash the prompt editor
      if (pCurrent) dirtyPrompts.set(pCurrent, pEdit.value);
      const btn = modal.querySelector(".yes") as HTMLButtonElement;
      btn.disabled = true;
      try {
        const r = await call("save_config", { config: collect() });
        if (r && r.__error) throw new Error(String(r.__error));
        for (const [name, text] of dirtyPrompts) await call("save_prompt", { name, text });
        if (aEdit.value !== approvalText) {
          const ar = await call("save_approval_policy", { text: aEdit.value });
          if (ar && ar.__error) throw new Error("approval policy: " + ar.__error);
        }
        S.config = collect();
        S.taskTypes = r.task_types ?? S.taskTypes;
        S.effort = S.config.thinking?.effort ?? S.effort;
        S.ctxMax = S.config.context?.max_tokens ?? S.ctxMax;
        S.compressThreshold = S.config.context?.compress_threshold ?? 0.95;
        S.compressTarget = S.config.context?.compress_target ?? 0.5;
        ($("effort-combo") as HTMLSelectElement).value = S.effort;
        reloadTaskTypes();
        setCtxBar(0, S.ctxMax, false);
        refreshContextEstimate();
        refreshServerInfo();
        done(true);
      } catch (e: any) {
        showMsg(modelPage, false, String(e?.message ?? e));
        btn.disabled = false;
      }
    });
  });
}

// ------------------------------------------------- turn actions (rewind / edit)
async function impactText(turnId: string): Promise<string> {
  const r = await call("rewind_impact", { turn_id: turnId });
  if (r && r.__error) return r.__error;
  let txt = `${r.turns} turn(s) will be discarded.\n`;
  txt += "Files restored to their earlier state:\n  " + (r.files.length ? r.files.join("\n  ") : "(none)");
  if (r.ran_commands) {
    txt += "\n\nNote: shell commands ran in these turns; their side effects (build output, installs...) are NOT reverted.";
  }
  return txt;
}

async function rewindTurn(turnId: string, keepBranch: boolean) {
  const info = await impactText(turnId);
  if (info.startsWith("RuntimeError") || info.startsWith("turn not on")) { chat.line(`\n[error] ${info}`, "c-error"); return; }
  const ok = await confirmModal("Rewind", info + "\n\nContinue?");
  if (!ok) return;
  const r = await call("rewind", { turn_id: turnId, keep_branch: keepBranch });
  if (r && r.__error) { chat.line(`\n[error] ${r.__error}`, "c-error"); return; }
  chat.line(`\n[rewound; restored ${(r.restored ?? []).length} file(s)]`, "c-warn");
  await replaySession();
  await refreshTurnList();
  await refreshSessionList();
  refreshContextEstimate();
}

async function editTurn(turnId: string) {
  const turns = await call("session_turns");
  if (turns && turns.__error) return;
  const t = (turns.turns ?? []).find((x: any) => x.id === turnId);
  if (!t) return;
  const info = await impactText(turnId);
  const res = await editPromptModal("Edit prompt and run again", info, t.prompt);
  if (!res) return;
  const wasPlan = t.task_type === "plan";
  const task = ($("task-combo") as HTMLSelectElement).value;
  const taskType = task === "(auto)" ? null : task;
  const r = await call("edit_and_resubmit", {
    turn_id: turnId, prompt: res.prompt, task_type: taskType,
    keep_branch: res.keepBranch, plan_mode: wasPlan,
  });
  if (r && r.__error) { chat.line(`\n[error] ${r.__error}`, "c-error"); return; }
  setBusy(true);
  S.thinkChars = 0;
}

function wireTurnList() {
  const ul = $("turn-list");
  ul.addEventListener("contextmenu", (e) => {
    const li = (e.target as HTMLElement).closest("li");
    if (!li || S.busy) return;
    e.preventDefault();
    const turns = (ul as any).__turns as any[] | undefined;
    const t = turns?.find((x) => x.id === li.dataset.id);
    if (!t || !t.active) return;
    showMenu(e.clientX, e.clientY, [
      { label: "Edit this prompt and run again...", fn: () => editTurn(t.id) },
      { label: "Rewind to before this turn (delete it and later turns)", fn: () => rewindTurn(t.id, false) },
      { label: "Rewind to before this turn (keep later turns as branch)", fn: () => rewindTurn(t.id, true) },
    ]);
  });
}
wireTurnList();

// ------------------------------------------------------------------ sending
function send() {
  const input = $("input") as HTMLTextAreaElement;
  const prompt = input.value.trim();
  if (!prompt || S.busy || !S.root) return;
  input.value = "";
  const task = ($("task-combo") as HTMLSelectElement).value;
  const taskType = task === "(auto)" ? null : task;
  if (S.mode === 1) {
    call("submit_plan", { goal: prompt }).then((r) => afterSubmit(r));
  } else {
    call("submit", { prompt, task_type: taskType }).then((r) => afterSubmit(r));
  }
}

function afterSubmit(r: any) {
  if (r && r.__error) {
    chat.line(`\n[error] ${r.__error}`, "c-error");
    setBusy(false);
    return;
  }
  setBusy(true);
  S.thinkChars = 0;
}

// ------------------------------------------------------------------- events
window.api.onEvent(onEvent);
// exposed for automated UI tests (CDP)
(window as any).__onEvent = onEvent;

function onEvent(ev: AgentEvent) {
  const k = ev.event;
  const d = ev.data;
  if (k === "turn_started") {
    S.block = null;
    thinking.clear();
    S.thinkChars = 0;
    chat.line(`\nYou  [${d.task_type}]`, "c-user");
    chat.line(d.prompt);
    log(ev, `task type ${d.task_type}`);
    refreshTurnList();
    refreshSessionList();
  } else if (k === "thinking_delta") {
    S.thinkChars += (d.text ?? "").length;
    thinking.write(d.text, "c-muted");
  } else if (k === "content_delta") {
    if (S.block !== ev.agent_id) {
      const who = ev.agent_id === "main" ? "" : ` (${ev.agent_id})`;
      chat.line(`\nAssistant${who}`, "c-ok b");
      S.block = ev.agent_id;
    }
    chat.write(d.text);
  } else if (k === "tool_call") {
    chatTool(d.name, d.args ?? {}, ev.agent_id);
    log(ev, `${d.name} ${argsSummary(d.name, d.args ?? {})}`, "c-tool");
    const p = String((d.args ?? {}).path ?? "");
    if (d.name === "read_file" && p.toLowerCase().endsWith(".md")) S.pendingDocs.set(d.id ?? "", p);
  } else if (k === "tool_result") {
    chatResult(!!d.ok, d.text ?? "");
    log(ev, (d.ok ? "ok " : "FAILED ") + (d.text ?? ""), d.ok ? "" : "c-error");
    const doc = S.pendingDocs.get(d.id ?? "");
    if (doc) {
      S.pendingDocs.delete(d.id ?? "");
      if (d.ok) {
        docs.line(`=== ${doc} ===`, "", true);
        docs.line(String(d.text ?? "").slice(0, 4000), "", true);
        docs.line();
      }
    }
  } else if (k === "command_output") {
    commands.write(d.text ?? "", "", true);
  } else if (k === "status") {
    setState(d.state, d.detail ?? "");
    if (d.state === "running_tool") commands.line(`\n$ (${d.detail ?? ""})`, "c-tool", true);
    log(ev, `${d.state} ${d.detail ?? ""}`, "c-muted");
  } else if (k === "approval_resolved") {
    const who = d.auto ? "auto" : "user";
    log(ev, `${d.allow ? "allowed" : "denied"} by ${who}: ${d.reason ?? ""}`, d.allow ? "c-ok" : "c-warn");
  } else if (k === "llm_done") {
    const u = d.usage ?? {};
    const el = d.elapsed ?? 0;
    const ttft = d.ttft;
    const tps = el && u ? (u.completion ?? 0) / el : 0;
    $("llm-label").textContent = ttft != null
      ? `ttft ${ttft.toFixed(1)}s | ${tps.toFixed(1)} tok/s | ${u.prompt ?? "?"} prompt tok`
      : "";
    log(ev, `${el.toFixed(1)}s ttft=${ttft == null ? "?" : ttft.toFixed(1)} usage=${JSON.stringify(u)} finish=${d.finish}`);
    S.thinkChars = 0;
  } else if (k === "context_usage") {
    setCtxBar(d.tokens, d.max_tokens, !!d.exact);
  } else if (k === "context_compressed") {
    chat.line(`\n[context compressed: ${d.before} -> ${d.after} tokens (${d.algorithm})]`, "c-warn");
    log(ev, `${d.before} -> ${d.after} tokens via ${d.algorithm}`, "c-warn");
  } else if (k === "warning" || k === "error") {
    chat.line(`\n[${k}] ${d.text ?? ""}`, k === "error" ? "c-error" : "c-warn");
    log(ev, d.text ?? "", k === "error" ? "c-error" : "c-warn");
  } else if (k === "turn_finished") {
    chat.line(`\n-- ${d.status} --`, "c-muted");
    log(ev, d.status);
    setBusy(false);
    setState("idle", "");
    refreshTurnList();
    refreshSessionList();
    refreshServerInfo();
  } else if (k === "agent_finished") {
    log(ev, `${d.status} after ${d.steps} step(s)`);
  } else if (k === "plan_created" || k === "plan_started" || k === "subtask_started" || k === "subtask_finished" || k === "plan_finished") {
    S.planDict = d.plan;
    renderPlan();
    const extra = d.id ?? d.status ?? "";
    log(ev, `${extra} (${(d.plan?.subtasks ?? []).length} subtasks)`);
    if (k === "plan_created") chat.line(`\n[plan: ${(d.plan?.subtasks ?? []).length} subtasks]`, "c-tool");
  } else if (k === "stage_started" || k === "stage_finished") {
    log(ev, `stage ${d.index + 1}` + (k === "stage_started" ? `/${d.total} ids=${(d.ids ?? []).join(", ")}` : ""));
    if (k === "stage_started") {
      chat.line(`\n== stage ${d.index + 1}/${d.total}: ${(d.ids ?? []).join(", ") || "nothing to run"} ==`, "c-tool b");
      S.block = null;
    }
  } else if (k === "doc_analysis") {
    docs.line(`=== ${d.subtask}: ${(d.docs ?? []).join(", ")}  (${d.source}) ===`, "b", true);
    docs.line(d.summary || "(empty)", "", true);
    docs.line();
    log(ev, `${d.subtask} read ${(d.docs ?? []).join(", ")} (${d.source})`);
  } else if (k === "session_rewound") {
    void (async () => {
      await replaySession();
      await refreshTurnList();
      await refreshSessionList();
      refreshContextEstimate();
    })();
  } else if (k === "approval_requested") {
    log(ev, `approval requested: ${d.title ?? d.kind}`, "c-warn");
    void (async () => {
      const resp = await approvalDialog(d);
      await call("answer", { req_id: d.req_id, response: resp });
    })();
  } else if (k === "plan_review_requested") {
    log(ev, "plan review requested", "c-warn");
    void (async () => {
      const plan = await planDialog(d, S.taskTypes);
      // null = cancelled (plan rejected); otherwise the (possibly edited) plan dict
      await call("answer", { req_id: d.req_id, response: plan });
    })();
  } else if (k === "stage_confirm_requested") {
    log(ev, "stage confirm requested", "c-warn");
    void (async () => {
      const decision = await stageDialog(d);
      await call("answer", { req_id: d.req_id, response: decision });
    })();
  }
}

function log(ev: AgentEvent, text: string, cls = "") {
  const ts = new Date(ev.ts * 1000);
  const stamp = `${String(ts.getHours()).padStart(2, "0")}:${String(ts.getMinutes()).padStart(2, "0")}:${String(ts.getSeconds()).padStart(2, "0")}`;
  activity.line(`${stamp} [${ev.agent_id}] ${ev.event}: ${short(text, 220)}`, cls, true);
}

// -------------------------------------------------------------------- plan
const PLAN_COLORS: Record<string, string> = {
  done: "c-ok", running: "c-tool", failed: "c-error", skipped: "c-warn", cancelled: "c-warn",
};

function renderPlan() {
  planView.innerHTML = "";
  if (!S.planDict) return;
  const plan = S.planDict;
  const subs: any[] = plan.subtasks ?? [];
  const line = (text: string, cls = "", bold = false) => {
    const div = document.createElement("div");
    if (cls) div.className = cls;
    if (bold) div.classList.add("b");
    div.textContent = text;
    planView.appendChild(div);
  };
  line(`Goal: ${plan.goal ?? ""}`, "", true);
  // group into stages by dependency level (same as the backend Plan.stages())
  const done = new Set<string>();
  const remaining = new Map(subs.map((s) => [s.id, s]));
  let stageNo = 0;
  while (remaining.size) {
    const level = [...remaining.values()].filter((s) => (s.depends_on ?? []).every((x: string) => done.has(x)));
    if (!level.length) { // broken deps: show the rest as one stage
      line(`\nStage ${++stageNo}`, "", true);
      for (const s of remaining.values()) renderSubtask(s);
      break;
    }
    line(`\nStage ${++stageNo}`, "", true);
    for (const s of level) { done.add(s.id); remaining.delete(s.id); renderSubtask(s); }
  }
  function renderSubtask(s: any) {
    line(`  ${s.id} [${s.status ?? "pending"}] (${s.task_type}) ${s.title ?? ""}`, PLAN_COLORS[s.status] ?? "c-muted");
    if ((s.depends_on ?? []).length) line(`      after: ${(s.depends_on as string[]).join(", ")}`, "c-muted");
    if (s.summary) line(`      ${short(s.summary, 200)}`, "c-muted");
  }
}

// -------------------------------------------------------------------- tabs
function bindTabs(barId: string, pagePrefix: string) {
  const bar = $(barId);
  bar.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      bar.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const name = (btn as HTMLElement).dataset.tab!;
      const parent = bar.parentElement!;
      parent.querySelectorAll(".tabpage").forEach((p) => p.classList.add("hidden"));
      $(pagePrefix + name).classList.remove("hidden");
    });
  });
}
bindTabs("left-tabs", "tab-");
bindTabs("right-tabs", "tab-");

function switchLeftTab(name: string) {
  const btn = document.querySelector(`#left-tabs .tab[data-tab="${name}"]`) as HTMLElement;
  btn?.click();
}

// ------------------------------------------------------------------ wiring
$("send-btn").addEventListener("click", send);
$("input").addEventListener("keydown", (e) => {
  // ignore Enter while an IME (e.g. Chinese pinyin) composition is in progress
  if (e.isComposing || e.keyCode === 229) return;
  if (e.key === "Enter" && e.ctrlKey) { e.preventDefault(); send(); }
});
$("stop-btn").addEventListener("click", () => {
  call("cancel");
  chat.line("\n[stopping...]", "c-warn");
});
$("mode-combo").addEventListener("change", (e) => {
  S.mode = (e.target as HTMLSelectElement).selectedIndex;
});
$("effort-combo").addEventListener("change", (e) => {
  S.effort = (e.target as HTMLSelectElement).value;
});
$("server-ctx-btn").addEventListener("click", refreshServerInfo);
$("new-session-btn").addEventListener("click", async () => {
  if (S.busy || !S.root) return;
  const r = await call("new_session");
  if (r && r.__error) { chat.line(`\n[error] ${r.__error}`, "c-error"); return; }
  resetViews();
  await refreshTurnList();
  await refreshSessionList();
  refreshContextEstimate();
  switchLeftTab("turns");
});
$("settings-btn").addEventListener("click", () => {
  if (S.busy) { chat.line("\n[settings disabled while a turn is running]", "c-muted"); return; }
  void settingsDialog().then((saved) => { if (saved) chat.line("\n[settings saved]", "c-ok"); });
});
$("sp-btn").addEventListener("click", async () => {
  const task = ($("task-combo") as HTMLSelectElement).value;
  const taskType = task === "(auto)" ? "general" : task;
  const r = await call("system_prompt", { task_type: taskType });
  if (r && r.__error) { chat.line(`\n[error] ${r.__error}`, "c-error"); return; }
  // read-only modal
  const modal = $("modal");
  modal.className = "modal wide";
  modal.innerHTML =
    `<div class="head">System prompt (${esc(taskType)})</div>` +
    `<div class="body"><pre class="diff" style="max-height:60vh"></pre></div>` +
    `<div class="foot"><button class="primary yes">Close</button></div>`;
  modal.querySelector("pre")!.textContent = r.text;
  $("modal-backdrop").classList.add("show");
  modal.querySelector(".yes")!.addEventListener("click", () => $("modal-backdrop").classList.remove("show"));
});

async function chooseProject() {
  if (S.busy) return;
  const dir = await window.api.pickDirectory();
  if (dir) openProject(dir);
}

$("root-label").style.cursor = "pointer";
$("root-label").title = "Click to open a different project";
$("root-label").addEventListener("click", chooseProject);

// ------------------------------------------------------- resizable splitters
const SPLIT_DEFAULTS = { left: 260, right: 520, input: 130 };
const SPLIT_LIMITS = {
  left: [180, 600] as const,
  right: [260, 1000] as const,
  input: [80, 400] as const,
};
const SPLIT_STORE = "coder-agent-panel-widths";

function loadSplitSizes(): { left?: number; right?: number; input?: number } {
  try {
    const raw = localStorage.getItem(SPLIT_STORE);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveSplitSizes() {
  const sizes = {
    left: Math.round($("left").getBoundingClientRect().width),
    right: Math.round($("right").getBoundingClientRect().width),
    input: Math.round($("input-zone").getBoundingClientRect().height),
  };
  try { localStorage.setItem(SPLIT_STORE, JSON.stringify(sizes)); } catch { /* ignore */ }
}

function setSplitLeft(w: number) { ($("left") as HTMLElement).style.width = `${w}px`; }
function setSplitRight(w: number) { ($("right") as HTMLElement).style.width = `${w}px`; }
function setSplitInput(h: number) { ($("input-zone") as HTMLElement).style.height = `${h}px`; }

function initSplitter(splitId: "split-left" | "split-right") {
  const splitter = $(splitId);
  const isLeft = splitId === "split-left";
  const limits = SPLIT_LIMITS[isLeft ? "left" : "right"];
  const body = $("body");

  splitter.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    splitter.classList.add("dragging");
    document.body.classList.add("resizing");
    // where inside the handle the mouse grabbed it, so the pane does not
    // jump on the first move
    const grab = e.clientX - (splitter.getBoundingClientRect().left + 2);

    const onMove = (ev: MouseEvent) => {
      // position the pane absolutely from the cursor: the handle's center
      // stays at (cursor - grab) at all times.
      //   4px body padding + 2px splitter margin + 2px half splitter width = 8px
      // (w is the border-box width, so the pane border is already inside it)
      const bodyRect = body.getBoundingClientRect();
      let w = isLeft
        ? ev.clientX - grab - bodyRect.left - 8
        : bodyRect.right - (ev.clientX - grab) - 8;
      // clamp: within limits and leave at least 120px for the middle pane.
      // non-mid space = 2x padding(4) + 4x margins(2) + 2x splitters(4) = 24
      const room = bodyRect.width - 24 - 120;
      const otherW = isLeft
        ? ($("right") as HTMLElement).offsetWidth
        : ($("left") as HTMLElement).offsetWidth;
      const maxByMid = Math.max(limits[0], room - otherW);
      w = Math.min(Math.max(w, limits[0]), maxByMid, limits[1]);
      if (isLeft) setSplitLeft(w); else setSplitRight(w);
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      splitter.classList.remove("dragging");
      document.body.classList.remove("resizing");
      saveSplitSizes();
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  });

  splitter.addEventListener("dblclick", () => {
    const w = SPLIT_DEFAULTS[isLeft ? "left" : "right"];
    if (isLeft) setSplitLeft(w); else setSplitRight(w);
    saveSplitSizes();
  });
}

function initInputSplitter() {
  const splitter = $("split-input");
  const mid = $("mid");
  const zone = $("input-zone");
  const limits = SPLIT_LIMITS.input;

  splitter.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    splitter.classList.add("dragging");
    document.body.classList.add("resizing-h");
    const grab = e.clientY - (splitter.getBoundingClientRect().top + 2);

    const onMove = (ev: MouseEvent) => {
      // zone height from the cursor: the handle center is 2px (zone margin)
      // + 2px (half splitter) above the zone top
      const midRect = mid.getBoundingClientRect();
      let h = midRect.bottom - (ev.clientY - grab) - 4;
      h = Math.min(Math.max(h, limits[0]), limits[1]);
      setSplitInput(h);
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      splitter.classList.remove("dragging");
      document.body.classList.remove("resizing-h");
      saveSplitSizes();
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  });

  splitter.addEventListener("dblclick", () => {
    setSplitInput(SPLIT_DEFAULTS.input);
    saveSplitSizes();
  });
}

const savedSplits = loadSplitSizes();
if (savedSplits.left) setSplitLeft(savedSplits.left);
if (savedSplits.right) setSplitRight(savedSplits.right);
if (savedSplits.input) setSplitInput(savedSplits.input);
initSplitter("split-left");
initSplitter("split-right");
initInputSplitter();

// ------------------------------------------------------------------- boot
let backendDown = false;

function showBackendBanner(code: number | null) {
  backendDown = true;
  setBusy(false);
  const bar = document.createElement("div");
  bar.id = "backend-banner";
  bar.innerHTML = `Backend process exited (code=${code}). <button class="primary restart">Restart backend</button>`;
  bar.querySelector(".restart")!.addEventListener("click", async () => {
    bar.textContent = "Restarting backend...";
    bar.style.opacity = "0.6";
    try {
      await window.api.restartBackend(S.root);
    } catch (e) {
      showBackendBanner(null);
      return;
    }
    if (S.root) await openProject(S.root);
  });
  document.body.appendChild(bar);
}

window.api.onBackendExit((code) => {
  if (backendDown) return;
  chat.line(`\n[backend exited (code=${code})]`, "c-error");
  showBackendBanner(code);
});

(async function boot() {
  // if the backend failed to start at boot, show the restart banner immediately
  if (!(await window.api.getBackendUrl())) {
    showBackendBanner(null);
    return;
  }
  const root = (await window.api.getBackendRoot()) || (await window.api.getLastProject());
  if (root) await openProject(root);
  else {
    chat.line("No project open yet. Click the project label above to choose one.", "c-muted");
    $("state-label").textContent = "No project";
  }
})();
