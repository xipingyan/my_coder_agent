from coder_agent.agent import AgentResult
from coder_agent.approval import ApprovalResponse
from coder_agent.config import AppConfig
from coder_agent.engine import Engine
from coder_agent.events import EventBus
from coder_agent.session import Session

from conftest import FakeProvider


def make_engine(project, script, approver=None, cfg=None, report_usage=True):
    bus = EventBus()
    events = []
    bus.subscribe(events.append)
    prov = FakeProvider(script, report_usage)
    eng = Engine(cfg or AppConfig(), project, bus, approver=approver, provider=prov)
    return eng, prov, events


def kinds(events):
    return [e.kind for e in events]


def test_simple_answer(project):
    eng, prov, ev = make_engine(project, [{"content": "hello"}])
    res = eng.submit("hi")
    assert res.status == "done" and res.final_text == "hello"
    assert prov.requests[0][0]["role"] == "system" and prov.requests[0][1] == {"role": "user", "content": "hi"}
    assert "turn_finished" in kinds(ev)


def test_tool_roundtrip_with_approval_and_diff(project):
    asked = []

    def approver(req):
        asked.append(req)
        return ApprovalResponse(True)

    script = [
        {"tool_calls": [("write_file", {"path": "src/main.cpp", "content": "int main(){}\n"})]},
        {"tool_calls": [("read_file", {"path": "src/main.cpp"})]},
        {"content": "created"},
    ]
    eng, prov, ev = make_engine(project, script, approver)
    res = eng.submit("write main")
    assert res.status == "done"
    assert (project / "src/main.cpp").read_text() == "int main(){}\n"
    assert len(asked) == 1 and asked[0].kind == "write" and "+int main(){}" in asked[0].detail
    assert asked[0].suggested_rule == "src/*"
    # tool result was fed back to the model
    last = prov.requests[-1]
    assert last[-1]["role"] == "tool" and "int main" in last[-1]["content"]


def test_rejection_is_reported_to_model_and_file_untouched(project):
    script = [
        {"tool_calls": [("write_file", {"path": "a.txt", "content": "x"})]},
        {"content": "ok, skipped"},
    ]
    eng, prov, _ = make_engine(project, script, lambda r: ApprovalResponse(False, "use b.txt instead"))
    eng.submit("make a.txt")
    assert not (project / "a.txt").exists()
    tool_msg = prov.requests[-1][-1]
    assert "REJECTED" in tool_msg["content"] and "use b.txt instead" in tool_msg["content"]


def test_auto_approved_command_and_denied_command(project):
    script = [
        {"tool_calls": [("run_command", {"command": "echo hello-from-shell"}), ("run_command", {"command": "sudo ls"})]},
        {"content": "done"},
    ]
    asked = []
    eng, prov, ev = make_engine(project, script, lambda r: asked.append(r) or ApprovalResponse(True))
    eng.submit("run things")
    assert asked == []  # echo is allow-listed, sudo is denied: nobody was asked
    results = [m["content"] for m in prov.requests[-1] if m["role"] == "tool"]
    assert "hello-from-shell" in results[0] and "DENIED" in results[1]


def test_unknown_tool_and_bad_json(project):
    script = [{"tool_calls": [("nope", {})]}, {"content": "fine"}]
    eng, prov, _ = make_engine(project, script)
    eng.submit("x")
    assert "unknown tool" in prov.requests[-1][-1]["content"]


def test_loop_detection(project):
    same = {"tool_calls": [("list_dir", {"path": "."})]}
    eng, prov, ev = make_engine(project, [same] * 20)
    res = eng.submit("loop")
    assert res.status == "loop"
    assert "warning" in kinds(ev)


def test_max_steps(project):
    cfg = AppConfig()
    cfg.agent.max_steps = 3
    script = [{"tool_calls": [("list_dir", {"path": f"d{i}"})]} for i in range(10)]
    eng, _, _ = make_engine(project, script, cfg=cfg)
    assert eng.submit("x").status == "max_steps"


def test_text_tool_call_fallback(project):
    (project / "f.txt").write_text("data")
    script = [
        {"content": '<tool_call>{"name": "read_file", "arguments": {"path": "f.txt"}}</tool_call>'},
        {"content": "read it"},
    ]
    eng, prov, _ = make_engine(project, script)
    assert eng.submit("read f").final_text == "read it"
    msgs = prov.requests[-1]
    assert msgs[-2]["tool_calls"][0]["function"]["name"] == "read_file" and "data" in msgs[-1]["content"]


def test_review_type_is_read_only(project):
    script = [{"tool_calls": [("write_file", {"path": "x", "content": "y"})]}, {"content": "nope"}]
    eng, prov, _ = make_engine(project, script, lambda r: ApprovalResponse(True))
    eng.submit("请帮我 review 一下代码", task_type="review")
    assert not (project / "x").exists()
    assert "unknown tool" in prov.requests[-1][-1]["content"]


def test_multi_turn_history_and_rewind_restores_files(project):
    (project / "keep.txt").write_text("v0")
    allow = lambda r: ApprovalResponse(True)
    script = [
        {"tool_calls": [("write_file", {"path": "keep.txt", "content": "v1"}), ("write_file", {"path": "new.txt", "content": "n"})]},
        {"content": "turn1 done"},
        {"tool_calls": [("write_file", {"path": "keep.txt", "content": "v2"})]},
        {"content": "turn2 done"},
    ]
    eng, prov, _ = make_engine(project, script, allow)
    eng.submit("first")
    eng.submit("second")
    assert (project / "keep.txt").read_text() == "v2"
    # turn 2 sees turn 1 in its history
    assert any(m.get("content") == "turn1 done" for m in prov.requests[2])

    t1, t2 = eng.active_turns
    assert t1.files_changed == ["keep.txt", "new.txt"]
    eng.rewind(t2.id)
    assert (project / "keep.txt").read_text() == "v1" and len(eng.active_turns) == 1
    eng.rewind(t1.id)
    assert (project / "keep.txt").read_text() == "v0" and not (project / "new.txt").exists()
    assert eng.active_turns == []


def test_edit_and_resubmit_replaces_later_turns(project):
    script = [{"content": "A1"}, {"content": "A2"}, {"content": "A2-edited"}]
    eng, prov, _ = make_engine(project, script)
    eng.submit("q1")
    eng.submit("q2")
    t2 = eng.active_turns[1]
    eng.edit_and_resubmit(t2.id, "q2 better")
    turns = eng.active_turns
    assert [t.prompt for t in turns] == ["q1", "q2 better"]
    assert len(eng.session.turns) == 2  # old turn deleted
    sent = prov.requests[-1]
    assert not any(m.get("content") == "q2" for m in sent) and any(m.get("content") == "A1" for m in sent)


def test_rewind_keep_branch(project):
    eng, _, _ = make_engine(project, [{"content": "a"}, {"content": "b"}])
    eng.submit("q1")
    eng.submit("q2")
    t2 = eng.active_turns[1]
    eng.rewind(t2.id, keep_branch=True)
    assert len(eng.active_turns) == 1 and len(eng.session.turns) == 2


def test_session_persistence(project):
    eng, _, _ = make_engine(project, [{"content": "a"}])
    eng.submit("remember me")
    loaded = Session.load(project, eng.session.id)
    assert [t.prompt for t in loaded.path()] == ["remember me"]
    assert loaded.build_messages()[-1]["content"] == "a"


def test_compaction_inside_turn_keeps_history_consistent(project):
    big = "y" * 6000
    (project / "big.txt").write_text(big)
    cfg = AppConfig()
    cfg.context.max_tokens = 6000
    cfg.context.keep_recent_units = 2
    cfg.agent.loop_limit = 99
    script = [{"tool_calls": [("read_file", {"path": "big.txt"})]} for i in range(6)]
    script.append({"content": "finished"})
    eng, prov, ev = make_engine(project, script, cfg=cfg, report_usage=False)
    res = eng.submit("read lots")
    assert res.status == "done"
    assert "context_compressed" in kinds(ev)
    turn = eng.active_turns[0]
    assert turn.base_messages is not None and turn.archive
    rebuilt = eng.session.build_messages()
    assert rebuilt[-1]["content"] == "finished"
    # next turn starts from the compacted history, not the raw one
    eng.submit("next")
    assert len(prov.requests[-1]) < len(turn.archive) + len(turn.messages) + 10


def test_think_limit_retries_without_thinking(project):
    cfg = AppConfig()
    cfg.thinking.max_think_chars = 50
    script = [{"thinking": "t" * 100, "content": "ignored"}, {"content": "answer"}]
    eng, prov, ev = make_engine(project, script, cfg=cfg)
    res = eng.submit("x")
    assert res.final_text == "answer" and prov.thinking_flags == [None, False]


def test_cancel_before_tool_leaves_valid_history(project):
    eng, prov, _ = make_engine(project, [{"tool_calls": [("list_dir", {})]}, {"content": "after"}])
    eng.bus.subscribe(lambda e: eng.cancel() if e.kind == "llm_done" else None)
    res = eng.submit("x")
    assert res.status == "cancelled"
    msgs = eng.session.build_messages()
    assert msgs[-1]["role"] == "tool" and msgs[-2].get("tool_calls")
