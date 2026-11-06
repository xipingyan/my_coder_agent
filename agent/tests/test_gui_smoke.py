import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from coder_agent.gui import dialogs, main_window
from coder_agent.gui.main_window import MainWindow

from conftest import FakeProvider


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _cleanup_qt(app):
    """Destroy Qt objects on the main thread; otherwise Python's GC may free them from a worker
    thread of a later (non-GUI) test and crash."""
    yield
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent

    for w in app.topLevelWidgets():
        w.close()
        w.deleteLater()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


def wait_idle(app, win, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if not win.busy and (win.worker is None or win.worker.isFinished()):
            app.processEvents()
            return
        time.sleep(0.01)
    raise AssertionError("worker did not finish")


def test_gui_turn_approval_rewind_and_edit(app, project, monkeypatch, tmp_path):
    QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, str(tmp_path / "qs"))
    seen = []

    def fake_exec(self):
        seen.append(self.windowTitle())
        self._done(dialogs.ApprovalResponse(True, remember_rule="src/*"))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialogs.ApprovalDialog, "exec", fake_exec)

    win = MainWindow(project)
    win.engine._provider_override = FakeProvider([
        {"thinking": "hmm", "tool_calls": [("write_file", {"path": "src/a.txt", "content": "A"})]},
        {"content": "all done"},
        {"content": "second"},
    ])
    win.input.setPlainText("create a file")
    win.send()
    wait_idle(app, win)

    assert (project / "src/a.txt").read_text() == "A"
    assert seen == ["Approval needed"]
    chat = win.chat.toPlainText()
    assert "create a file" in chat and "all done" in chat and "write_file" in chat
    assert "hmm" in win.thinking.toPlainText()
    assert win.history.count() == 1
    assert "tool_call" in win.activity.toPlainText()

    # rule was remembered in the project policy -> no second dialog
    win.engine._provider_override.script = [
        {"tool_calls": [("write_file", {"path": "src/b.txt", "content": "B"})]}, {"content": "ok"}]
    win.input.setPlainText("another")
    win.send()
    wait_idle(app, win)
    assert seen == ["Approval needed"] and (project / "src/b.txt").exists()
    assert win.history.count() == 2

    # rewind the second turn
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    t2 = win.engine.active_turns[1]
    win._rewind_turn(t2.id, keep_branch=False)
    assert not (project / "src/b.txt").exists() and (project / "src/a.txt").exists()
    assert win.history.count() == 1 and "another" not in win.chat.toPlainText()

    # edit first prompt and rerun
    def fake_edit_exec(self):
        self.edit.setPlainText("create a better file")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialogs.EditPromptDialog, "exec", fake_edit_exec)
    monkeypatch.setattr(main_window, "EditPromptDialog", dialogs.EditPromptDialog)
    win.engine._provider_override.script = [{"content": "redone"}]
    win._edit_turn(win.engine.active_turns[0].id)
    wait_idle(app, win)
    assert not (project / "src/a.txt").exists()
    assert [t.prompt for t in win.engine.active_turns] == ["create a better file"]
    assert "redone" in win.chat.toPlainText()
    win.close()


def test_settings_dialog_roundtrip(app):
    from coder_agent.config import AppConfig

    cfg = AppConfig()
    cfg.thinking.extra_body = {"top_k": 20}
    dlg = dialogs.SettingsDialog(cfg, ["general", "cpp_impl"])
    new = dlg._collect()
    assert new.model_dump() == cfg.model_dump()
    dlg.forms["context"]._getters["max_tokens"]  # field exists


def test_gui_plan_mode(app, project, monkeypatch, tmp_path):
    import json

    QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, str(tmp_path / "qs2"))
    monkeypatch.setattr(dialogs.ApprovalDialog, "exec", lambda self: (self._done(dialogs.ApprovalResponse(True)), 1)[1])
    stage_calls = []

    def plan_exec(self):
        # edit the goal of the first subtask like a user would, then accept
        self.table.item(0, 5).setText("write a.txt with content A")
        self.accept()
        return QDialog.DialogCode.Accepted

    def stage_exec(self):
        stage_calls.append(self.windowTitle())
        self._pick("continue")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialogs.PlanDialog, "exec", plan_exec)
    monkeypatch.setattr(dialogs.StageDialog, "exec", stage_exec)

    plan = {"subtasks": [
        {"id": "T1", "title": "write", "goal": "x", "depends_on": [], "task_type": "general", "files": []},
        {"id": "T2", "title": "check", "goal": "verify", "depends_on": ["T1"], "task_type": "general", "files": []},
    ]}
    win = MainWindow(project)
    prov = FakeProvider([
        {"content": json.dumps(plan)},
        {"tool_calls": [("write_file", {"path": "a.txt", "content": "A"})]},
        {"content": "T1 done"},
        {"content": "T2 done"},
    ])
    win.engine._provider_override = prov
    win.mode_combo.setCurrentIndex(1)
    win.input.setPlainText("make a.txt then verify")
    win.send()
    wait_idle(app, win)

    assert (project / "a.txt").read_text() == "A"
    assert prov.requests[2][1]["content"] == "write a.txt with content A"  # edited plan was executed
    assert stage_calls == ["Stage 1/2 finished"]
    plan_text = win.plan_view.toPlainText()
    assert "Stage 2" in plan_text and "T1 [done]" in plan_text and "T2 [done]" in plan_text
    assert "(sub:T1)" in win.chat.toPlainText()
    assert win.history.count() == 1 and "[done]" in win.history.item(0).text()
    win.close()


def test_new_session_clears_every_panel_and_history_reopens_old_session(app, project, monkeypatch, tmp_path):
    QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, str(tmp_path / "qs3"))
    monkeypatch.setattr(dialogs.ApprovalDialog, "exec", lambda self: (self._done(dialogs.ApprovalResponse(True)), 1)[1])
    win = MainWindow(project)
    win.engine._provider_override = FakeProvider([
        {"thinking": "pondering", "tool_calls": [("run_command", {"command": "echo hi"})]},
        {"content": "first answer"},
    ])
    win.input.setPlainText("first task")
    win.send()
    wait_idle(app, win)
    first_id = win.engine.session.id
    for view in (win.activity, win.thinking, win.commands, win.chat):
        assert view.toPlainText().strip(), view
    win.plan_view.write("stale plan")
    win.docs.write("stale docs")
    assert win.sessions.count() == 1 and "first task" in win.sessions.item(0).text()
    assert win.ctx_bar.value() >= 0

    # ---- new session: nothing from the old one may remain visible
    win.new_session()
    for name in ("activity", "thinking", "commands", "docs", "plan_view", "chat"):
        assert getattr(win, name).toPlainText() == "", name
    assert win.history.count() == 0 and win.llm_label.text() == "" and win.ctx_bar.value() == 0
    assert win.engine.session.id != first_id and win._plan_dict is None
    assert win.sessions.count() == 1  # the new empty session is not listed, the old one is

    # second session with its own work
    win.engine._provider_override = FakeProvider([{"content": "second answer"}])
    win.input.setPlainText("second task")
    win.send()
    wait_idle(app, win)
    assert win.sessions.count() == 2
    assert "second task" in win.sessions.item(0).text() and win.sessions.item(0).text().startswith("● ")  # newest, current

    # ---- go back to the first session from the history list
    old = [win.sessions.item(i) for i in range(2) if win.sessions.item(i).data(Qt.ItemDataRole.UserRole) == first_id][0]
    win.sessions.itemDoubleClicked.emit(old)
    assert win.engine.session.id == first_id
    chat = win.chat.toPlainText()
    assert "first task" in chat and "first answer" in chat and "second" not in chat
    assert win.history.count() == 1 and win.activity.toPlainText() == ""  # live logs are not persisted
    assert win.ctx_bar.value() >= 0 and "context" in win.ctx_bar.text()

    # the reopened session continues with its history
    prov = FakeProvider([{"content": "third answer"}])
    win.engine._provider_override = prov
    win.input.setPlainText("follow up")
    win.send()
    wait_idle(app, win)
    assert any(m.get("content") == "first answer" for m in prov.requests[0])
    assert win.history.count() == 2

    # ---- delete the other session, then the current one
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    other = [i for i in range(win.sessions.count()) if win.sessions.item(i).data(Qt.ItemDataRole.UserRole) != first_id][0]
    win.delete_session(win.sessions.item(other).data(Qt.ItemDataRole.UserRole))
    assert win.sessions.count() == 1
    win.delete_session(first_id)
    assert win.sessions.count() == 0 and win.engine.session.id != first_id and win.chat.toPlainText() == ""
    win.close()
