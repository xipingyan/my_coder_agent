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
    window.api.send({ id: nextId, method, params });
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
  S.root = r.root;
  S.taskTypes = r.task_types;
  S.effort = r.effort;
  S.ctxMax = r.config?.context?.max_tokens ?? S.ctxMax;
  S.compressThreshold = r.config?.context?.compress_threshold ?? 0.95;
  S.compressTarget = r.config?.context?.compress_target ?? 0.5;
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
  ul.innerHTML = "";
  for (const t of r.turns) {
    const li = document.createElement("li");
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
    ul.appendChild(li);
  }
}

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
  } else if (k === "approval_requested") {
    // dialog comes in Phase 4; for now just log so the user sees what is pending
    log(ev, `approval requested: ${d.title ?? d.kind}`, "c-warn");
    chat.line(`\n[approval requested: ${d.title ?? d.kind} — dialog coming in Phase 4]`, "c-warn");
  } else if (k === "plan_review_requested" || k === "stage_confirm_requested") {
    log(ev, `${k.replace("_requested", "")} requested (dialog coming in Phase 4)`, "c-warn");
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
  chat.line("\n[settings panel coming in Phase 5]", "c-muted");
});

async function chooseProject() {
  if (S.busy) return;
  const dir = await window.api.pickDirectory();
  if (dir) openProject(dir);
}

$("root-label").style.cursor = "pointer";
$("root-label").title = "Click to open a different project";
$("root-label").addEventListener("click", chooseProject);

// ------------------------------------------------------------------- boot
window.api.onBackendExit((code) => {
  chat.line(`\n[backend exited (code=${code})]`, "c-error");
  setBusy(false);
});

(async function boot() {
  const last = await window.api.getLastProject();
  if (last) await openProject(last);
  else {
    chat.line("No project open yet. Click the project label above to choose one.", "c-muted");
    $("state-label").textContent = "No project";
  }
})();
