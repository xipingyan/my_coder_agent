"""Dialogs: approval, edit-prompt and settings."""

from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..approval import ApprovalRequest, ApprovalResponse, ensure_user_policy
from ..config import AppConfig
from ..context import available_strategies
from ..llm import LLMError, create_provider
from ..orchestrator import CONTINUE, RETRY, STOP, Plan, PlanError, StageReport, SubTask
from ..paths import user_dir
from .widgets import DiffView, ModelForm, RichLog, mono_font


# ---------------------------------------------------------------- approval
class ApprovalDialog(QDialog):
    def __init__(self, req: ApprovalRequest, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Approval needed")
        self.resize(820, 560)
        self.response = ApprovalResponse(False, "dialog closed")

        lay = QVBoxLayout(self)
        what = "Run command" if req.kind == "command" else "Write file"
        head = QLabel(f"{what}: {req.title}")
        head.setTextFormat(Qt.TextFormat.PlainText)
        head.setWordWrap(True)
        f = head.font()
        f.setBold(True)
        head.setFont(f)
        lay.addWidget(head)

        view = DiffView()
        view.show_text(req.detail)
        lay.addWidget(view, 1)

        row = QHBoxLayout()
        row.addWidget(QLabel("Remember rule:"))
        self.rule = QLineEdit(req.suggested_rule)
        row.addWidget(self.rule, 1)
        self.scope = QComboBox()
        self.scope.addItems(["project", "user"])
        row.addWidget(self.scope)
        lay.addLayout(row)

        self.feedback = QLineEdit()
        self.feedback.setPlaceholderText("Optional feedback sent to the model when rejecting")
        lay.addWidget(self.feedback)

        btns = QHBoxLayout()
        allow = QPushButton("Allow once")
        always = QPushButton("Allow && remember rule")
        reject = QPushButton("Reject")
        allow.setDefault(True)
        for b in (allow, always, reject):
            btns.addWidget(b)
        lay.addLayout(btns)
        allow.clicked.connect(lambda: self._done(ApprovalResponse(True)))
        always.clicked.connect(
            lambda: self._done(ApprovalResponse(True, remember_rule=self.rule.text().strip(),
                                                remember_scope=self.scope.currentText()))
        )
        reject.clicked.connect(lambda: self._done(ApprovalResponse(False, self.feedback.text().strip())))

    def _done(self, resp: ApprovalResponse) -> None:
        self.response = resp
        self.accept()

    def reject(self) -> None:  # Esc / window close
        self.response = ApprovalResponse(False, self.feedback.text().strip())
        super().reject()


# -------------------------------------------------------------- edit prompt
class EditPromptDialog(QDialog):
    def __init__(self, prompt: str, info: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit prompt and run again")
        self.resize(640, 360)
        lay = QVBoxLayout(self)
        lbl = QLabel(info)
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        self.edit = QPlainTextEdit(prompt)
        lay.addWidget(self.edit, 1)
        self.keep_branch = QCheckBox("Keep the discarded turns as an inactive branch (instead of deleting them)")
        lay.addWidget(self.keep_branch)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def prompt(self) -> str:
        return self.edit.toPlainText().strip()


# --------------------------------------------------------------- plan review
class PlanDialog(QDialog):
    """Review / edit the planner's subtasks before anything runs."""

    COLS = ["id", "title", "depends on", "task type", "files", "goal"]

    def __init__(self, plan: Plan, type_names: list[str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review plan")
        self.resize(1100, 520)
        self.goal = plan.goal
        self.type_names = type_names
        self.result_plan: Optional[Plan] = None

        lay = QVBoxLayout(self)
        lbl = QLabel("The plan below was generated for your goal. Edit, add or remove subtasks, then run it. "
                     "Subtasks without dependencies between them may run in parallel (parallelism is configurable).")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setWordWrap(True)
        lay.addWidget(self.table, 1)
        for s in plan.subtasks:
            self._add_row(s)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(5, 480)
        self.table.resizeRowsToContents()

        row = QHBoxLayout()
        add = QPushButton("Add subtask")
        rem = QPushButton("Remove selected")
        add.clicked.connect(lambda: self._add_row(SubTask(f"T{self.table.rowCount() + 1}", "", "")))
        rem.clicked.connect(self._remove)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        lay.addLayout(row)
        bb = QDialogButtonBox()
        run = bb.addButton("Run plan", QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        run.setDefault(True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _add_row(self, s: SubTask) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, text in ((0, s.id), (1, s.title), (2, ", ".join(s.depends_on)), (4, ", ".join(s.files)), (5, s.goal)):
            self.table.setItem(r, c, QTableWidgetItem(text))
        combo = QComboBox()
        combo.addItems(self.type_names)
        combo.setCurrentText(s.task_type)
        self.table.setCellWidget(r, 3, combo)

    def _remove(self) -> None:
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def _cell(self, r: int, c: int) -> str:
        it = self.table.item(r, c)
        return it.text().strip() if it else ""

    def accept(self) -> None:
        try:
            subs = []
            for r in range(self.table.rowCount()):
                combo = self.table.cellWidget(r, 3)
                subs.append(SubTask.from_dict({
                    "id": self._cell(r, 0), "title": self._cell(r, 1), "depends_on": self._cell(r, 2),
                    "task_type": combo.currentText() if combo else "general",  # type: ignore[union-attr]
                    "files": self._cell(r, 4), "goal": self._cell(r, 5),
                }))
            plan = Plan(self.goal, subs)
            plan.validate()
        except PlanError as e:
            QMessageBox.warning(self, "Invalid plan", str(e))
            return
        self.result_plan = plan
        super().accept()


class StageDialog(QDialog):
    """Shown after each stage: results so far, then continue / retry failed / stop."""

    def __init__(self, report: StageReport, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Stage {report.index + 1}/{report.total} finished")
        self.resize(760, 520)
        self.decision = STOP
        lay = QVBoxLayout(self)
        view = RichLog()
        any_failed = False
        for s in report.subtasks:
            bad = s.status in ("failed", "skipped", "cancelled")
            any_failed = any_failed or bad
            view.line(f"{s.id}  [{s.status}]  {s.title}", color="error" if bad else "ok", bold=True)
            view.line(s.summary or "(no summary)")
            view.line()
        view.line("Files changed so far by this plan:", bold=True)
        view.line("\n".join(report.changed_files) or "(none)", mono=True)
        lay.addWidget(view, 1)
        nxt = "Finish" if report.is_last else f"Continue to stage {report.index + 2}"
        row = QHBoxLayout()
        cont = QPushButton(nxt)
        retry = QPushButton("Retry failed")
        stop = QPushButton("Stop here")
        retry.setEnabled(any_failed)
        for b in (cont, retry, stop):
            row.addWidget(b)
        lay.addLayout(row)
        cont.setDefault(True)
        cont.clicked.connect(lambda: self._pick(CONTINUE))
        retry.clicked.connect(lambda: self._pick(RETRY))
        stop.clicked.connect(lambda: self._pick(STOP))

    def _pick(self, d: str) -> None:
        self.decision = d
        self.accept()


# ----------------------------------------------------------------- settings
def _builtin_prompt(name: str) -> str:
    f = resources.files("coder_agent").joinpath(f"prompts/defaults/{name}.md")
    return f.read_text(encoding="utf-8") if f.is_file() else ""


_NEW_TYPE = "---\nname: {name}\ndescription: \nkeywords: \nglobs: \neffort: medium\n---\nTask type: {name}.\n"


class PromptsEditor(QWidget):
    """Edit task-type prefixes. Saved as ~/.coder_agent/prompts/<name>.md (overrides built-ins)."""

    def __init__(self, names: list[str]) -> None:
        super().__init__()
        self.dir = user_dir() / "prompts"
        self._current: Optional[str] = None
        self._dirty: dict[str, str] = {}
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.combo = QComboBox()
        self.combo.addItems(["base", *names])
        new = QPushButton("New type...")
        revert = QPushButton("Revert to default")
        top.addWidget(self.combo, 1)
        top.addWidget(new)
        top.addWidget(revert)
        lay.addLayout(top)
        self.edit = QPlainTextEdit()
        self.edit.setFont(mono_font())
        lay.addWidget(self.edit, 1)
        hint = QLabel("Front-matter keys: name, description, keywords, globs, effort, tools. "
                      "Saved to the user prompts folder; a project can override in <project>/.agent/prompts/.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.combo.currentTextChanged.connect(self._switch)
        new.clicked.connect(self._new)
        revert.clicked.connect(self._revert)
        self._switch(self.combo.currentText())

    def _source(self, name: str) -> str:
        p = self.dir / f"{name}.md"
        return p.read_text(encoding="utf-8") if p.is_file() else _builtin_prompt(name)

    def _stash(self) -> None:
        if self._current is not None:
            text = self.edit.toPlainText()
            if text != self._source(self._current):
                self._dirty[self._current] = text

    def _switch(self, name: str) -> None:
        self._stash()
        self._current = name
        self.edit.setPlainText(self._dirty.get(name, self._source(name)))

    def _new(self) -> None:
        name, ok = QInputDialog.getText(self, "New task type", "Name (letters, digits, _):")
        name = name.strip()
        if ok and name and name.replace("_", "").isalnum():
            self._stash()
            self._dirty[name] = _NEW_TYPE.format(name=name)
            if self.combo.findText(name) < 0:
                self.combo.addItem(name)
            self.combo.setCurrentText(name)
            self.edit.setPlainText(self._dirty[name])

    def _revert(self) -> None:
        name = self.combo.currentText()
        self._dirty.pop(name, None)
        (self.dir / f"{name}.md").unlink(missing_ok=True)
        self.edit.setPlainText(_builtin_prompt(name))

    def apply(self) -> None:
        self._stash()
        if self._dirty:
            self.dir.mkdir(parents=True, exist_ok=True)
        for name, text in self._dirty.items():
            (self.dir / f"{name}.md").write_text(text, encoding="utf-8")
        self._dirty.clear()


class ApprovalEditor(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.path = ensure_user_policy()
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"User-wide approval policy: {self.path}\n"
                             "(Projects can add rules in <project>/.agent/approval.toml)"))
        self.edit = QPlainTextEdit(self.path.read_text(encoding="utf-8"))
        self.edit.setFont(mono_font())
        lay.addWidget(self.edit, 1)

    def validate(self) -> None:
        tomllib.loads(self.edit.toPlainText())

    def apply(self) -> None:
        text = self.edit.toPlainText()
        if text != self.path.read_text(encoding="utf-8"):
            self.path.write_text(text, encoding="utf-8")


class SettingsDialog(QDialog):
    def __init__(self, cfg: AppConfig, task_names: list[str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(720, 640)
        self.cfg = cfg
        self.result_config: Optional[AppConfig] = None

        choices = {
            "server": {"provider": ["openai_compat", "ollama"]},
            "context": {"algorithm": available_strategies()},
        }
        tips = {
            "effort": "reasoning_effort sent to the server ('default' = send nothing)",
            "send_effort": "Send the reasoning_effort request field",
            "enable_thinking": "chat_template_kwargs.enable_thinking (unset = server default)",
            "token_budget": "thinking_token_budget (vLLM). 0/blank = unset",
            "max_think_chars": "If thinking exceeds this many characters, abort it and retry with thinking off. 0 = unlimited",
            "extra_body": "JSON merged into the request body, e.g. {\"top_k\": 20}",
            "max_tokens": "Server-side context length (cache size). Compression is based on this.",
            "compress_threshold": "Compress when usage reaches this fraction (0.95 = 95%)",
            "compress_target": "Compress down to this fraction",
            "parallelism": "Concurrent sub-agents. Keep 1 for a single-GPU Ollama.",
            "bypass_proxy": "Ignore HTTP(S)_PROXY environment variables (LAN server)",
            "planner_thinking": "Allow thinking while planning (slower)",
            "doc_analysis": "Summarise relevant MODULE.md files before each subtask",
            "confirm_each_stage": "Ask for confirmation after every stage",
        }
        self.forms: dict[str, ModelForm] = {}
        for section in ("server", "thinking", "sampling", "context", "agent", "orchestrator", "paths"):
            self.forms[section] = ModelForm(
                getattr(cfg, section),
                choices.get(section),
                skip={"project_root"} if section == "paths" else None,
                descriptions=tips,
            )

        def group(title: str, w: QWidget) -> QGroupBox:
            g = QGroupBox(title)
            QVBoxLayout(g).addWidget(w)
            return g

        general = QWidget()
        gl = QVBoxLayout(general)
        gl.addWidget(group("Server", self.forms["server"]))
        test = QPushButton("Test connection / list models")
        test.clicked.connect(self._test)
        gl.addWidget(test)
        gl.addWidget(group("Thinking", self.forms["thinking"]))
        gl.addWidget(group("Sampling", self.forms["sampling"]))
        gl.addStretch(1)

        ctx = QWidget()
        cl = QVBoxLayout(ctx)
        cl.addWidget(group("Context / compression", self.forms["context"]))
        cl.addWidget(group("Agent", self.forms["agent"]))
        cl.addWidget(group("Subtask planning", self.forms["orchestrator"]))
        cl.addWidget(group("Paths", self.forms["paths"]))
        cl.addStretch(1)

        self.prompts = PromptsEditor(task_names)
        self.approval = ApprovalEditor()
        tabs = QTabWidget()
        tabs.addTab(general, "Model")
        tabs.addTab(ctx, "Context && agent")
        tabs.addTab(self.prompts, "Task prompts")
        tabs.addTab(self.approval, "Approval")

        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _collect(self) -> AppConfig:
        data = {name: form.values() for name, form in self.forms.items()}
        data["paths"]["project_root"] = self.cfg.paths.project_root
        return AppConfig.model_validate(data)

    def _test(self) -> None:
        try:
            models = create_provider(self._collect()).list_models()
        except (LLMError, ValueError) as e:
            QMessageBox.warning(self, "Connection failed", str(e))
            return
        QMessageBox.information(self, "Server reachable", "Models:\n" + "\n".join(models))

    def accept(self) -> None:
        try:
            self.result_config = self._collect()
            self.approval.validate()
        except Exception as e:  # noqa: BLE001 - show any validation problem
            QMessageBox.warning(self, "Invalid settings", str(e))
            return
        self.prompts.apply()
        self.approval.apply()
        super().accept()
