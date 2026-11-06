"""Main window."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..approval import ApprovalRequest
from ..config import AppConfig, load_config, save_config
from ..engine import Engine
from ..events import Event, EventBus
from ..orchestrator import Plan, SubTask
from ..prompts import PromptBuilder
from ..session import Session
from .bridge import ApprovalBridge, EventBridge, PlanBridge, TurnWorker
from .dialogs import ApprovalDialog, EditPromptDialog, PlanDialog, SettingsDialog, StageDialog
from .widgets import RichLog

AUTO = "(auto)"
MODES = ["Single agent", "Plan & subtasks"]
EFFORTS = ["default", "none", "low", "medium", "high"]
STATE_TEXT = {
    "waiting_model": "Waiting for model",
    "running_tool": "Running tool",
    "waiting_user": "Waiting for your approval",
    "idle": "Idle",
}


class PromptEdit(QPlainTextEdit):
    submit = Signal()

    def keyPressEvent(self, e) -> None:  # noqa: N802 - Qt API
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.submit.emit()
            return
        super().keyPressEvent(e)


def _short(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _args_summary(name: str, args: dict) -> str:
    if name == "run_command":
        return args.get("command", "")
    if name in ("read_file", "list_dir", "write_file", "apply_patch"):
        extra = ""
        if name == "read_file" and args.get("start_line"):
            extra = f" [{args.get('start_line')}-{args.get('end_line', '')}]"
        return str(args.get("path", ".")) + extra
    return _short(json.dumps(args, ensure_ascii=False), 140)


class MainWindow(QMainWindow):
    def __init__(self, root: Optional[Path] = None) -> None:
        super().__init__()
        self.setWindowTitle("Coder Agent")
        self.resize(1500, 900)
        self.cfg: AppConfig = AppConfig()
        self.engine: Optional[Engine] = None
        self.worker: Optional[TurnWorker] = None
        self.busy = False

        self.bus = EventBus()
        self.events = EventBridge(self.bus)
        self.events.event.connect(self.on_event)
        self.approvals = ApprovalBridge()
        self.approvals.requested.connect(self.on_approval)
        self.plans = PlanBridge()
        self.plans.requested.connect(self.on_plan_request)
        self._plan_dict: Optional[dict] = None

        self._block: Optional[str] = None
        self._pending_docs: dict[str, str] = {}
        self._think_chars = 0
        self._state = ("idle", "")
        self._state_since = time.time()

        self._build_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)

        start = root or self._last_project()
        if start:
            self.open_project(start)
        else:
            QTimer.singleShot(0, self.choose_project)

    # -------------------------------------------------------------- UI build
    def _build_ui(self) -> None:
        # menu
        fm = self.menuBar().addMenu("&File")
        for text, fn, key in (
            ("Open project...", self.choose_project, QKeySequence.StandardKey.Open),
            ("New session", self.new_session, QKeySequence.StandardKey.New),
            ("Session history", self.open_session, None),
            ("Settings...", self.open_settings, QKeySequence("Ctrl+,")),
            ("Quit", self.close, QKeySequence.StandardKey.Quit),
        ):
            act = QAction(text, self)
            if key:
                act.setShortcut(key)
            act.triggered.connect(fn)
            fm.addAction(act)
        vm = self.menuBar().addMenu("&View")
        act = QAction("Show system prompt", self)
        act.triggered.connect(self.show_system_prompt)
        vm.addAction(act)

        # toolbar
        tb = QToolBar("main")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.root_label = QLabel("(no project)")
        tb.addWidget(self.root_label)
        tb.addSeparator()
        tb.addWidget(QLabel(" Task type: "))
        self.task_combo = QComboBox()
        self.task_combo.addItem(AUTO)
        tb.addWidget(self.task_combo)
        tb.addWidget(QLabel("  Thinking effort: "))
        self.effort_combo = QComboBox()
        self.effort_combo.addItems(EFFORTS)
        self.effort_combo.currentTextChanged.connect(self._effort_changed)
        tb.addWidget(self.effort_combo)
        tb.addWidget(QLabel("  Mode: "))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(MODES)
        self.mode_combo.setToolTip("Plan & subtasks: split the goal into small subtasks, review the plan, "
                                   "confirm after every stage")
        tb.addWidget(self.mode_combo)
        tb.addSeparator()
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.setEnabled(False)
        tb.addWidget(self.stop_btn)
        st = QPushButton("Settings")
        st.clicked.connect(self.open_settings)
        tb.addWidget(st)

        # left: turns of this session / session history
        self.history = QListWidget()
        self.history.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history.customContextMenuRequested.connect(self._history_menu)
        self.history.setToolTip("Right-click a turn: edit the prompt and run again, or rewind")

        sess = QWidget()
        sl = QVBoxLayout(sess)
        sl.setContentsMargins(0, 4, 0, 0)
        new_btn = QPushButton("New session")
        new_btn.clicked.connect(self.new_session)
        sl.addWidget(new_btn)
        self.sessions = QListWidget()
        self.sessions.setWordWrap(True)
        self.sessions.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.sessions.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sessions.customContextMenuRequested.connect(self._sessions_menu)
        self.sessions.itemDoubleClicked.connect(lambda it: self.open_session_id(it.data(Qt.ItemDataRole.UserRole)))
        self.sessions.setToolTip("Double-click to open, right-click for more")
        sl.addWidget(self.sessions)

        self.left_tabs = QTabWidget()
        self.left_tabs.addTab(self.history, "Turns")
        self.left_tabs.addTab(sess, "Sessions")
        left = self.left_tabs

        # middle: chat + input
        mid = QWidget()
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(4, 4, 4, 4)
        self.chat = RichLog()
        ml.addWidget(self.chat, 1)
        self.input = PromptEdit()
        self.input.setPlaceholderText("Describe the task...  (Ctrl+Enter to send)")
        self.input.setFixedHeight(110)
        self.input.submit.connect(self.send)
        ml.addWidget(self.input)
        row = QHBoxLayout()
        row.addStretch(1)
        self.send_btn = QPushButton("Send (Ctrl+Enter)")
        self.send_btn.clicked.connect(self.send)
        row.addWidget(self.send_btn)
        ml.addLayout(row)

        # right: tabs
        self.activity = RichLog(mono=True)
        self.thinking = RichLog()
        self.commands = RichLog(mono=True)
        self.docs = RichLog(mono=True)
        self.plan_view = RichLog()
        tabs = QTabWidget()
        tabs.addTab(self.activity, "Activity")
        tabs.addTab(self.plan_view, "Plan")
        tabs.addTab(self.thinking, "Thinking")
        tabs.addTab(self.commands, "Commands")
        tabs.addTab(self.docs, "Docs read")

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(mid)
        split.addWidget(tabs)
        split.setSizes([260, 720, 520])
        self.setCentralWidget(split)

        # status bar
        sb = self.statusBar()
        self.state_label = QLabel("Idle")
        sb.addWidget(self.state_label, 1)
        self.llm_label = QLabel("")
        sb.addPermanentWidget(self.llm_label)
        self.ctx_bar = QProgressBar()
        self.ctx_bar.setFixedWidth(260)
        self.ctx_bar.setRange(0, 100)
        self.ctx_bar.setFormat("context 0%")
        sb.addPermanentWidget(self.ctx_bar)

    # ------------------------------------------------------------ projects
    def _last_project(self) -> Optional[Path]:
        p = QSettings("coder_agent", "gui").value("last_project", "")
        return Path(p) if p and Path(p).is_dir() else None

    def choose_project(self) -> None:
        if self.busy:
            return
        d = QFileDialog.getExistingDirectory(self, "Project root", str(self.engine.root if self.engine else Path.home()))
        if d:
            self.open_project(Path(d))

    def open_project(self, root: Path, session_id: Optional[str] = None) -> None:
        root = Path(root).resolve()
        QSettings("coder_agent", "gui").setValue("last_project", str(root))
        self.cfg = load_config(root)
        session = Session.load(root, session_id) if session_id else None
        self.engine = Engine(self.cfg, root, self.bus, approver=self.approvals.ask, session=session)
        self.approvals.cancelled = self.engine.cancel_event.is_set
        self.plans.cancelled = self.engine.cancel_event.is_set
        self.root_label.setText(f" Project: {root} ")
        self.setWindowTitle(f"Coder Agent - {root.name}")
        self._reload_task_types()
        self.effort_combo.blockSignals(True)
        self.effort_combo.setCurrentText(self.cfg.thinking.effort)
        self.effort_combo.blockSignals(False)
        self._reset_views()
        self.render_session()
        self.refresh_history()
        self.refresh_sessions()
        self._update_context_bar()

    def _reset_views(self) -> None:
        """Forget everything that belongs to the previous session (live logs are not persisted)."""
        for view in (self.activity, self.thinking, self.commands, self.docs, self.plan_view, self.chat):
            view.clear()
        self._plan_dict = None
        self._pending_docs.clear()
        self._block = None
        self._think_chars = 0
        self.llm_label.setText("")
        self._set_state("idle", "")

    def _update_context_bar(self) -> None:
        """Estimated context usage of the history that the next prompt will carry."""
        if not self.engine:
            return
        msgs = self.engine.session.build_messages()
        tokens, mx = self.engine.ctx_mgr.tokens(msgs), self.cfg.context.max_tokens
        pct = int(tokens / max(1, mx) * 100)
        self.ctx_bar.setValue(min(100, pct))
        self.ctx_bar.setFormat(f"context {tokens / 1000:.1f}k / {mx / 1000:.0f}k ({pct}%)" if tokens else "context 0%")

    def new_session(self) -> None:
        if self.busy or not self.engine:
            return
        if not self.engine.session.turns:  # already empty: nothing to switch away from
            self._reset_views()
            self.left_tabs.setCurrentIndex(0)
            return
        self.open_project(self.engine.root)
        self.left_tabs.setCurrentIndex(0)

    def open_session(self) -> None:
        """Menu entry: show the session history."""
        self.left_tabs.setCurrentIndex(1)
        self.refresh_sessions()

    def open_session_id(self, session_id: str) -> None:
        if self.busy or not self.engine or session_id == self.engine.session.id:
            return
        try:
            self.open_project(self.engine.root, session_id)
        except (OSError, ValueError, KeyError, TypeError) as e:
            QMessageBox.warning(self, "Cannot open session", f"{session_id}: {e}")
            return
        self.left_tabs.setCurrentIndex(0)

    def refresh_sessions(self) -> None:
        self.sessions.clear()
        if not self.engine:
            return
        current = self.engine.session.id
        for info in Session.summaries(self.engine.root):
            when = time.strftime("%m-%d %H:%M", time.localtime(info.updated))
            mark = "● " if info.id == current else ""
            it = QListWidgetItem(f"{mark}{when}  {info.title or '(empty)'}")
            it.setData(Qt.ItemDataRole.UserRole, info.id)
            it.setToolTip(f"{info.title}\n{info.turns} turn(s), last status: {info.status}\n{info.id}")
            if info.id == current:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            self.sessions.addItem(it)

    def _sessions_menu(self, pos) -> None:
        it = self.sessions.itemAt(pos)
        if not it or not self.engine:
            return
        sid = it.data(Qt.ItemDataRole.UserRole)
        m = QMenu(self)
        a_open = m.addAction("Open")
        a_del = m.addAction("Delete session...")
        a_open.setEnabled(not self.busy and sid != self.engine.session.id)
        a_del.setEnabled(not self.busy)
        chosen = m.exec(self.sessions.mapToGlobal(pos))
        if chosen == a_open:
            self.open_session_id(sid)
        elif chosen == a_del:
            self.delete_session(sid)

    def delete_session(self, session_id: str) -> None:
        if not self.engine or self.busy:
            return
        r = QMessageBox.question(
            self, "Delete session",
            "Delete this session and its file snapshots?\nFiles in the project are not touched, but this "
            "session can no longer be rewound.")
        if r != QMessageBox.StandardButton.Yes:
            return
        was_current = session_id == self.engine.session.id
        Session.delete(self.engine.root, session_id)
        if was_current:
            self.open_project(self.engine.root)  # start a fresh session
        else:
            self.refresh_sessions()

    def _reload_task_types(self) -> None:
        cur = self.task_combo.currentText()
        self.task_combo.clear()
        self.task_combo.addItem(AUTO)
        if self.engine:
            for n in self.engine.task_types.names():
                self.task_combo.addItem(n)
        i = self.task_combo.findText(cur)
        self.task_combo.setCurrentIndex(max(0, i))

    # ------------------------------------------------------------ settings
    def _effort_changed(self, text: str) -> None:
        self.cfg.thinking.effort = text  # type: ignore[assignment]

    def open_settings(self) -> None:
        if self.busy:
            return
        names = self.engine.task_types.names() if self.engine else []
        dlg = SettingsDialog(self.cfg, names, self)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.result_config is None:
            return
        new = dlg.result_config
        persisted = new.model_copy(deep=True)
        persisted.paths.project_root = ""
        save_config(persisted)
        self.cfg = new
        if self.engine:
            self.engine.apply_config(new)
            self.engine.reload_policy()
            self._reload_task_types()
        self.effort_combo.blockSignals(True)
        self.effort_combo.setCurrentText(new.thinking.effort)
        self.effort_combo.blockSignals(False)

    def show_system_prompt(self) -> None:
        if not self.engine:
            return
        e = self.engine
        name = self.task_combo.currentText()
        tt = e.task_types.get("general" if name == AUTO else name)
        text = PromptBuilder(e.root, e.task_types, e.sections).build(tt)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"System prompt ({tt.name})")
        dlg.resize(800, 600)
        lay = QVBoxLayout(dlg)
        view = RichLog(mono=True)
        view.write(text)
        lay.addWidget(view)
        dlg.exec()

    # ------------------------------------------------------------- sending
    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.send_btn.setEnabled(not busy)
        self.input.setReadOnly(busy)
        self.stop_btn.setEnabled(busy)

    def send(self) -> None:
        prompt = self.input.toPlainText().strip()
        if not prompt or self.busy or not self.engine:
            return
        self.input.clear()
        task = self.task_combo.currentText()
        task_type = None if task == AUTO else task
        self._submit(prompt, self.mode_combo.currentIndex() == 1, task_type)

    def _submit(self, prompt: str, plan_mode: bool, task_type: Optional[str] = None) -> None:
        assert self.engine
        e = self.engine
        if plan_mode:
            self._run_async(lambda: e.submit_plan(prompt, self.plans.confirm_plan, self.plans.confirm_stage))
        else:
            self._run_async(lambda: e.submit(prompt, task_type))

    def _run_async(self, fn) -> None:
        self.set_busy(True)
        self._think_chars = 0
        self.worker = TurnWorker(fn)
        self.worker.finished.connect(self._worker_done)
        self.worker.start()

    def _worker_done(self) -> None:
        w = self.worker
        if w and w.error:
            self.chat.line(f"\n[error] {w.error}", color="error")
        self.set_busy(False)
        self._set_state("idle", "")
        self.refresh_history()

    def stop(self) -> None:
        if self.engine and self.busy:
            self.engine.cancel()
            self.chat.line("\n[stopping...]", color="warn")

    # ---------------------------------------------------- history / rewind
    def refresh_history(self) -> None:
        self.history.clear()
        if not self.engine:
            return
        s = self.engine.session
        active = [t.id for t in s.path()]
        for t in sorted(s.turns.values(), key=lambda t: t.created):
            if t.id in active:
                n = active.index(t.id) + 1
                text = f"{n}. [{t.status}] {_short(t.prompt, 60)}"
            else:
                text = f"(branch) [{t.status}] {_short(t.prompt, 54)}"
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, t.id)
            it.setToolTip(t.prompt)
            if t.id not in active:
                it.setForeground(Qt.GlobalColor.gray)
            self.history.addItem(it)

    def _history_menu(self, pos) -> None:
        it = self.history.itemAt(pos)
        if not it or self.busy or not self.engine:
            return
        tid = it.data(Qt.ItemDataRole.UserRole)
        if tid not in [t.id for t in self.engine.session.path()]:
            return
        m = QMenu(self)
        a1 = m.addAction("Edit this prompt and run again...")
        a2 = m.addAction("Rewind to before this turn (delete it and later turns)")
        a3 = m.addAction("Rewind to before this turn (keep later turns as branch)")
        chosen = m.exec(self.history.mapToGlobal(pos))
        if chosen == a1:
            self._edit_turn(tid)
        elif chosen in (a2, a3):
            self._rewind_turn(tid, keep_branch=chosen == a3)

    def _impact(self, turn_id: str) -> tuple[list[str], int, bool]:
        assert self.engine
        s = self.engine.session
        path = s.path()
        ids = [t.id for t in path]
        tail = path[ids.index(turn_id):]
        files: list[str] = []
        for t in tail:
            for f in s.snapshots.changed_files(t.id):
                if f not in files:
                    files.append(f)
        return files, len(tail), any(t.ran_commands for t in tail)

    def _impact_text(self, turn_id: str) -> str:
        files, n, cmds = self._impact(turn_id)
        txt = f"{n} turn(s) will be discarded.\n"
        txt += "Files restored to their earlier state:\n  " + ("\n  ".join(files) if files else "(none)")
        if cmds:
            txt += "\n\nNote: shell commands ran in these turns; their side effects (build output, installs...) are NOT reverted."
        return txt

    def _edit_turn(self, turn_id: str) -> None:
        assert self.engine
        turn = self.engine.session.turns[turn_id]
        was_plan = turn.task_type == "plan"
        dlg = EditPromptDialog(turn.prompt, self._impact_text(turn_id), self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.prompt():
            return
        self.engine.rewind(turn_id, dlg.keep_branch.isChecked())
        self.render_session()
        self.refresh_history()
        new_prompt = dlg.prompt()
        task = self.task_combo.currentText()
        task_type = None if task == AUTO else task
        self._submit(new_prompt, was_plan, task_type)

    def _rewind_turn(self, turn_id: str, keep_branch: bool) -> None:
        assert self.engine
        r = QMessageBox.question(self, "Rewind", self._impact_text(turn_id) + "\n\nContinue?")
        if r != QMessageBox.StandardButton.Yes:
            return
        restored, _ = self.engine.rewind(turn_id, keep_branch)
        self.render_session()
        self.refresh_history()
        self.refresh_sessions()
        self._update_context_bar()
        self.chat.line(f"\n[rewound; restored {len(restored)} file(s)]", color="warn")

    # ------------------------------------------------------------ rendering
    def _chat_tool(self, name: str, args: dict, agent: str = "main") -> None:
        who = "" if agent == "main" else f"[{agent}] "
        self.chat.line(f"\n> {who}{name}  {_short(_args_summary(name, args), 160)}", color="tool", mono=True)
        self._block = None

    def _chat_result(self, ok: bool, text: str) -> None:
        self.chat.line("  " + _short(text, 300), color="muted" if ok else "error", mono=True)

    def render_session(self) -> None:
        self.chat.clear()
        self._block = None
        if not self.engine:
            return
        for t in self.engine.session.path():
            self.chat.line(f"\nYou  [{t.task_type}]", color="user", bold=True)
            self.chat.line(t.prompt)
            for m in t.display_messages():
                role = m.get("role")
                if role == "assistant":
                    if m.get("content"):
                        self.chat.line("\nAssistant", color="ok", bold=True)
                        self.chat.line(m["content"])
                    for tc in m.get("tool_calls") or []:
                        fn = tc["function"]
                        try:
                            args = json.loads(fn["arguments"] or "{}")
                        except json.JSONDecodeError:
                            args = {"raw": fn["arguments"]}
                        self._chat_tool(fn["name"], args if isinstance(args, dict) else {})
                elif role == "tool":
                    c = m.get("content", "")
                    self._chat_result(not c.startswith(("ERROR", "DENIED", "REJECTED")), c)
            self.chat.line(f"-- {t.status} --", color="muted")

    # --------------------------------------------------------------- events
    def _set_state(self, state: str, detail: str) -> None:
        self._state = (state, detail)
        self._state_since = time.time()
        self._tick()

    def _tick(self) -> None:
        state, detail = self._state
        text = STATE_TEXT.get(state, state)
        if state != "idle":
            text += f": {detail}" if detail else ""
            text += f"  ({int(time.time() - self._state_since)}s)"
            if state == "waiting_model" and self._think_chars:
                text += f"  thinking {self._think_chars} chars"
        self.state_label.setText(text)

    def _log(self, ev: Event, text: str, color: Optional[str] = None) -> None:
        ts = time.strftime("%H:%M:%S", time.localtime(ev.ts))
        self.activity.line(f"{ts} [{ev.agent_id}] {ev.kind}: {_short(text, 220)}", color=color)

    def on_event(self, ev: Event) -> None:
        k, d = ev.kind, ev.data
        if k == "turn_started":
            self._block = None
            self.thinking.clear()
            self._think_chars = 0
            self.chat.line(f"\nYou  [{d['task_type']}]", color="user", bold=True)
            self.chat.line(d["prompt"])
            self._log(ev, f"task type {d['task_type']}")
            self.refresh_history()
            self.refresh_sessions()
        elif k == "thinking_delta":
            self._think_chars += len(d["text"])
            self.thinking.write(d["text"], color="muted")
        elif k == "content_delta":
            if self._block != ev.agent_id:
                who = "" if ev.agent_id == "main" else f" ({ev.agent_id})"
                self.chat.line(f"\nAssistant{who}", color="ok", bold=True)
                self._block = ev.agent_id
            self.chat.write(d["text"])
        elif k == "tool_call":
            self._chat_tool(d["name"], d["args"], ev.agent_id)
            self._log(ev, f"{d['name']} {_args_summary(d['name'], d['args'])}", "tool")
            path = str(d["args"].get("path", ""))
            if d["name"] == "read_file" and path.lower().endswith(".md"):
                self._pending_docs[d["id"]] = path
        elif k == "tool_result":
            self._chat_result(d["ok"], d["text"])
            self._log(ev, ("ok " if d["ok"] else "FAILED ") + d["text"], None if d["ok"] else "error")
            doc = self._pending_docs.pop(d.get("id", ""), None)
            if doc and d["ok"]:
                self.docs.line(f"=== {doc} ===", bold=True, mono=True)
                self.docs.line(d["text"][:4000], mono=True)
        elif k == "command_output":
            self.commands.write(d["text"], mono=True)
        elif k == "status":
            self._set_state(d["state"], d.get("detail", ""))
            if d["state"] == "running_tool":
                self.commands.line(f"\n$ ({d.get('detail', '')})", color="tool", mono=True)
            self._log(ev, f"{d['state']} {d.get('detail', '')}", "muted")
        elif k == "approval_resolved":
            who = "auto" if d.get("auto") else "user"
            self._log(ev, f"{'allowed' if d['allow'] else 'denied'} by {who}: {d.get('reason', '')}",
                      "ok" if d["allow"] else "warn")
        elif k == "llm_done":
            u = d.get("usage") or {}
            el, ttft = d.get("elapsed", 0), d.get("ttft")
            tps = (u.get("completion", 0) / el) if el and u else 0
            self.llm_label.setText(f"ttft {ttft:.1f}s | {tps:.1f} tok/s | {u.get('prompt', '?')} prompt tok  "
                                   if ttft is not None else "")
            self._log(ev, f"{el:.1f}s ttft={ttft if ttft is None else round(ttft, 1)} usage={u} finish={d.get('finish')}")
            self._think_chars = 0
        elif k == "context_usage":
            pct = int(d["ratio"] * 100)
            self.ctx_bar.setValue(min(100, pct))
            self.ctx_bar.setFormat(f"context {d['tokens'] / 1000:.1f}k / {d['max_tokens'] / 1000:.0f}k ({pct}%)")
        elif k == "context_compressed":
            self.chat.line(f"\n[context compressed: {d['before']} -> {d['after']} tokens ({d['algorithm']})]", color="warn")
            self._log(ev, f"{d['before']} -> {d['after']} tokens via {d['algorithm']}", "warn")
        elif k in ("warning", "error"):
            self.chat.line(f"\n[{k}] {d['text']}", color="error" if k == "error" else "warn")
            self._log(ev, d["text"], "error" if k == "error" else "warn")
        elif k == "turn_finished":
            self.chat.line(f"\n-- {d['status']} --", color="muted")
            self._log(ev, d["status"])
            self.refresh_history()
            self.refresh_sessions()
        elif k == "agent_finished":
            self._log(ev, f"{d['status']} after {d['steps']} step(s)")
        elif k in ("plan_created", "plan_started", "subtask_started", "subtask_finished", "plan_finished"):
            self._plan_dict = d["plan"]
            self._render_plan()
            extra = d.get("id") or d.get("status") or ""
            self._log(ev, f"{extra} ({len(d['plan']['subtasks'])} subtasks)")
            if k == "plan_created":
                self.chat.line(f"\n[plan: {len(d['plan']['subtasks'])} subtasks]", color="tool")
        elif k in ("stage_started", "stage_finished"):
            self._log(ev, f"stage {d['index'] + 1}" + (f"/{d['total']} ids={d['ids']}" if k == "stage_started" else ""))
            if k == "stage_started":
                self.chat.line(f"\n== stage {d['index'] + 1}/{d['total']}: {', '.join(d['ids']) or 'nothing to run'} ==",
                               color="tool", bold=True)
                self._block = None
        elif k == "doc_analysis":
            self.docs.line(f"=== {d['subtask']}: {', '.join(d['docs'])}  ({d['source']}) ===", bold=True, mono=True)
            self.docs.line(d["summary"] or "(empty)", mono=True)
            self.docs.line()
            self._log(ev, f"{d['subtask']} read {', '.join(d['docs'])} ({d['source']})")

    # ------------------------------------------------------------ approvals
    def _render_plan(self) -> None:
        self.plan_view.clear()
        if not self._plan_dict:
            return
        plan = Plan(self._plan_dict.get("goal", ""), [SubTask(**s) for s in self._plan_dict["subtasks"]])
        colors = {"done": "ok", "running": "tool", "failed": "error", "skipped": "warn", "cancelled": "warn"}
        self.plan_view.line(f"Goal: {plan.goal}", bold=True)
        try:
            stages = plan.stages()
        except Exception:  # noqa: BLE001 - invalid plan while editing
            stages = [plan.subtasks]
        for i, stage in enumerate(stages, 1):
            self.plan_view.line(f"\nStage {i}", bold=True)
            for s in stage:
                self.plan_view.line(f"  {s.id} [{s.status}] ({s.task_type}) {s.title}", color=colors.get(s.status, "muted"))
                if s.depends_on:
                    self.plan_view.line(f"      after: {', '.join(s.depends_on)}", color="muted")
                if s.summary:
                    self.plan_view.line(f"      {_short(s.summary, 200)}", color="muted")

    def on_plan_request(self, kind: str, payload, pending) -> None:
        if kind == "plan":
            dlg = PlanDialog(payload, self.engine.task_types.names() if self.engine else ["general"], self)
            ok = dlg.exec() == QDialog.DialogCode.Accepted
            PlanBridge.answer(pending, dlg.result_plan if ok else None)
        else:
            dlg2 = StageDialog(payload, self)
            dlg2.exec()
            PlanBridge.answer(pending, dlg2.decision)

    # ------------------------------------------------------------ approvals
    def on_approval(self, req: ApprovalRequest, pending) -> None:
        self.activity.line(f"           approval requested: {req.title}", color="warn", mono=True)
        dlg = ApprovalDialog(req, self)
        dlg.exec()
        ApprovalBridge.answer(pending, dlg.response)

    # ---------------------------------------------------------------- close
    def closeEvent(self, e) -> None:  # noqa: N802 - Qt API
        if self.busy and self.engine:
            self.engine.cancel()
            if self.worker:
                self.worker.wait(5000)
        super().closeEvent(e)
