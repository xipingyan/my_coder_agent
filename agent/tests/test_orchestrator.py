import json

import pytest

from coder_agent.approval import ApprovalResponse
from coder_agent.config import AppConfig
from coder_agent.engine import Engine
from coder_agent.events import EventBus
from coder_agent.orchestrator import CONTINUE, RETRY, STOP, Plan, PlanError, extract_json
from coder_agent.orchestrator.orchestrator import _waves
from coder_agent.orchestrator.models import SubTask

from conftest import FakeProvider


def plan_json(*subs):
    return {"content": json.dumps({"subtasks": list(subs)})}


def sub(id, goal="do it", deps=(), files=(), type="general"):
    return {"id": id, "title": id, "goal": goal, "depends_on": list(deps), "files": list(files), "task_type": type}


def make(project, script, cfg=None, approver=lambda r: ApprovalResponse(True)):
    bus = EventBus()
    events = []
    bus.subscribe(events.append)
    prov = FakeProvider(script)
    eng = Engine(cfg or AppConfig(), project, bus, approver=approver, provider=prov)
    return eng, prov, events


# ----------------------------------------------------------------- models
def test_stages_and_validation():
    p = Plan.from_dict({"subtasks": [sub("A"), sub("B", deps=["A"]), sub("C", deps=["A"]), sub("D", deps=["B", "C"])]})
    assert [[s.id for s in lv] for lv in p.stages()] == [["A"], ["B", "C"], ["D"]]
    with pytest.raises(PlanError, match="cycle"):
        Plan.from_dict({"subtasks": [sub("A", deps=["B"]), sub("B", deps=["A"])]})
    with pytest.raises(PlanError, match="unknown"):
        Plan.from_dict({"subtasks": [sub("A", deps=["Z"])]})
    with pytest.raises(PlanError, match="duplicate"):
        Plan.from_dict({"subtasks": [sub("A"), sub("A")]})


def test_extract_json_variants():
    assert extract_json('text ```json\n{"a": 1}\n``` more') == {"a": 1}
    assert extract_json('Sure! {"a": {"b": 2}} done') == {"a": {"b": 2}}
    with pytest.raises(PlanError):
        extract_json("no json here")


def test_waves_separate_same_file_hints():
    st = [SubTask("A", "a", "g", files=["x.cpp"]), SubTask("B", "b", "g", files=["x.cpp"]), SubTask("C", "c", "g", files=["y.cpp"])]
    assert [[s.id for s in w] for w in _waves(st)] == [["A", "C"], ["B"]]


# --------------------------------------------------------------- planning
def test_planner_retries_on_invalid_json(project):
    script = [{"content": "I think we should..."}, plan_json(sub("T1")), {"content": "done T1"}]
    eng, prov, ev = make(project, script)
    res = eng.submit_plan("goal", confirm_plan=lambda p: p)
    assert res.status == "done"
    assert "Invalid plan" in prov.requests[1][-1]["content"]


def test_planner_gives_up(project):
    eng, prov, ev = make(project, [{"content": "nope"}] * 5)
    assert eng.submit_plan("goal") is None
    assert eng.active_turns[0].status == "error" and "Planning failed" in eng.active_turns[0].final_text
    assert any(e.kind == "error" for e in ev)


def test_user_rejects_plan(project):
    eng, _, _ = make(project, [plan_json(sub("T1"))])
    assert eng.submit_plan("g", confirm_plan=lambda p: None) is None
    assert eng.active_turns[0].status == "cancelled"


# -------------------------------------------------------------- execution
def test_stage_by_stage_with_dependency_summary_and_confirmation(project):
    script = [
        plan_json(sub("T1", "write a.txt"), sub("T2", "read a.txt", deps=["T1"])),
        {"tool_calls": [("write_file", {"path": "a.txt", "content": "hello"})]},
        {"content": "T1 wrote a.txt"},
        {"content": "T2 verified"},
    ]
    eng, prov, ev = make(project, script)
    stages = []

    def confirm(report):
        stages.append((report.index, [s.status for s in report.subtasks], report.changed_files))
        return CONTINUE

    res = eng.submit_plan("make a.txt", confirm_plan=lambda p: p, confirm_stage=confirm)
    assert res.status == "done"
    assert stages == [(0, ["done"], ["a.txt"])]  # confirmed after stage 1 only (last stage needs no confirm)
    # T2's system prompt carries T1's summary
    t2_system = prov.requests[-1][0]["content"]
    assert "T1 wrote a.txt" in t2_system
    turn = eng.active_turns[0]
    assert turn.task_type == "plan" and [s["status"] for s in turn.subtasks] == ["done", "done"]
    assert "T2 verified" in turn.final_text
    assert {e.agent_id for e in ev if e.kind == "tool_call"} == {"sub:T1"}
    assert [e.kind for e in ev if e.kind.startswith(("stage_", "plan_"))][:3] == ["plan_created", "plan_started", "stage_started"]
    # whole plan is one turn: rewind restores the file
    eng.rewind(turn.id)
    assert not (project / "a.txt").exists()


def test_failed_dependency_skips_and_retry_reruns(project):
    cfg = AppConfig()
    cfg.orchestrator.subtask_max_steps = 1
    script = [
        plan_json(sub("T1"), sub("T2", deps=["T1"])),
        {"tool_calls": [("list_dir", {})]},  # T1 hits the 1-step limit -> failed
        {"content": "T1 fixed"},  # retry succeeds
        {"content": "T2 ok"},
    ]
    eng, prov, _ = make(project, script, cfg)
    decisions = iter([RETRY, CONTINUE])
    seen = []

    def confirm(r):
        seen.append([s.status for s in r.subtasks])
        return next(decisions)

    res = eng.submit_plan("g", confirm_plan=lambda p: p, confirm_stage=confirm)
    assert seen == [["failed"], ["done"]]
    assert res.status == "done"


def test_stop_decision_and_skipped_status(project):
    script = [plan_json(sub("T1"), sub("T2", deps=["T1"])), {"content": "T1 done"}]
    eng, _, _ = make(project, script)
    res = eng.submit_plan("g", confirm_plan=lambda p: p, confirm_stage=lambda r: STOP)
    assert res.status == "stopped"
    assert [s.status for s in res.plan.subtasks] == ["done", "pending"]


def test_no_confirmation_when_disabled(project):
    cfg = AppConfig()
    cfg.orchestrator.confirm_each_stage = False
    script = [plan_json(sub("T1"), sub("T2", deps=["T1"])), {"content": "a"}, {"content": "b"}]
    eng, _, _ = make(project, script, cfg)
    res = eng.submit_plan("g", confirm_plan=lambda p: p, confirm_stage=lambda r: pytest.fail("asked"))
    assert res.status == "done"


def test_edited_plan_is_used(project):
    script = [plan_json(sub("T1")), {"content": "only edited"}]
    eng, prov, _ = make(project, script)

    def edit(plan):
        plan.subtasks[0].goal = "EDITED GOAL"
        return plan

    eng.submit_plan("g", confirm_plan=edit)
    assert prov.requests[-1][1]["content"] == "EDITED GOAL"


# ---------------------------------------------------------- doc analysis
def test_doc_analysis_selects_module_doc_and_caches(project):
    (project / "core").mkdir()
    (project / "core" / "MODULE.md").write_text("# Core\nEngine core module.\n\nAPI: Foo::run() returns int\n")
    (project / "ui").mkdir()
    (project / "ui" / "MODULE.md").write_text("# UI\nWidgets.\n")
    script = [
        plan_json(sub("T1", "extend Foo", files=["core/foo.cpp"])),
        {"content": "- Foo::run() returns int"},  # doc summary
        {"content": "T1 done"},
    ]
    eng, prov, ev = make(project, script)
    eng.submit_plan("g", confirm_plan=lambda p: p)
    docs = [e for e in ev if e.kind == "doc_analysis"]
    assert len(docs) == 1 and docs[0].data["docs"] == ["core/MODULE.md"]
    assert "Foo::run() returns int" in docs[0].data["summary"]
    assert "Foo::run() returns int" in prov.requests[-1][0]["content"]  # injected into sub-agent prompt

    # second identical run: summary comes from the cache, no extra model call
    eng2, prov2, ev2 = make(project, [plan_json(sub("T1", "extend Foo", files=["core/foo.cpp"])), {"content": "T1 done again"}])
    eng2.submit_plan("g", confirm_plan=lambda p: p)
    assert [e.data["source"] for e in ev2 if e.kind == "doc_analysis"] == ["cache"]
    assert len(prov2.requests) == 2


def test_doc_analysis_can_be_disabled(project):
    (project / "MODULE.md").write_text("# Root\nx\n")
    cfg = AppConfig()
    cfg.orchestrator.doc_analysis = False
    eng, prov, ev = make(project, [plan_json(sub("T1")), {"content": "ok"}], cfg)
    eng.submit_plan("g", confirm_plan=lambda p: p)
    assert not [e for e in ev if e.kind == "doc_analysis"] and len(prov.requests) == 2
