"""Concurrency stress tests for parallel sub-agents (deterministic, fake model with latency).

The real-server version of this is ``coder-agent-stress`` (see README)."""

import json
import re
import threading
import time

import pytest

from coder_agent.approval import ApprovalPolicy, ApprovalResponse
from coder_agent.config import AppConfig
from coder_agent.llm import LLMChunk, LLMError, LLMProvider, ToolCall, Usage
from coder_agent.stress import (
    build_workload, check_expectations, execute, format_table, max_concurrency, summarize,
)


class ConcurrentFake(LLMProvider):
    """Thread-safe fake model. Sub-agent behaviour depends only on its own messages:
    first call -> write stress/mod_<i>.hpp, second call -> final answer."""

    def __init__(self, latency=0.05, fail_ids=(), run_command=False):
        self.latency = latency
        self.fail_ids = set(fail_ids)
        self.run_command = run_command
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.spans: dict[str, list[tuple[float, float]]] = {}

    def list_models(self):
        return ["fake"]

    def stream_chat(self, messages, tools=None, cancel=None, thinking=None, max_tokens=None):
        sys_text = messages[0]["content"]
        m = re.search(r"Subtask (T\d+):", sys_text)
        sid = m.group(1) if m else "planner"
        t0 = time.perf_counter()
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            deadline = time.monotonic() + self.latency
            while time.monotonic() < deadline:  # cancellable sleep
                if cancel is not None and cancel.is_set():
                    break
                time.sleep(0.005)
            if sid in self.fail_ids:
                raise LLMError("injected failure")
            i = int(sid[1:])
            if messages[-1]["role"] == "user":
                calls = [ToolCall(f"w{sid}", "write_file", json.dumps({
                    "path": f"stress/mod_{i}.hpp",
                    "content": f"#pragma once\ninline int value_{i}() {{ return {i}; }}\n"}))]
                if self.run_command:
                    calls.append(ToolCall(f"c{sid}", "run_command", json.dumps({"command": f"echo {sid}"})))
                yield LLMChunk("tool_calls", tool_calls=calls)
                yield LLMChunk("usage", usage=Usage(500, 40, 540))
                yield LLMChunk("done", finish_reason="tool_calls")
            else:
                yield LLMChunk("content", f"{sid} wrote stress/mod_{i}.hpp")
                yield LLMChunk("usage", usage=Usage(600, 20, 620))
                yield LLMChunk("done", finish_reason="stop")
        finally:
            with self.lock:
                self.active -= 1
                self.spans.setdefault(sid, []).append((t0, time.perf_counter()))


def cfg(max_steps=6):
    c = AppConfig()
    c.orchestrator.subtask_max_steps = max_steps
    return c


# --------------------------------------------------------------------------
def test_parallel_speedup_and_integrity(project):
    n = 12
    serial = execute(cfg(), n, 1, provider=ConcurrentFake(), root=project / "s")
    prov = ConcurrentFake()
    par = execute(cfg(), n, 4, provider=prov, root=project / "p")

    rs, rp = summarize(serial), summarize(par)
    assert rs["done"] == rp["done"] == n and rs["verified"] == rp["verified"] == n
    assert rs["max_concurrency"] == 1
    assert 2 <= rp["max_concurrency"] <= 4 and 2 <= prov.max_active <= 4
    assert par.wall < serial.wall * 0.7, (serial.wall, par.wall)
    assert check_expectations([rs, rp]) == []

    # every event delivered exactly once, nothing lost under concurrency
    finished = [e.data["id"] for e in par.events if e.kind == "subtask_finished"]
    assert sorted(finished) == sorted(f"T{i}" for i in range(1, n + 1))
    assert len([e for e in par.events if e.kind == "llm_done" and e.agent_id.startswith("sub:")]) == 2 * n

    # the snapshot manifest (read-modify-write per file) must contain every file
    turn = par.engine.active_turns[0]
    assert turn.files_changed == sorted(f"stress/mod_{i}.hpp" for i in range(1, n + 1))
    assert [s["status"] for s in turn.subtasks] == ["done"] * n

    # rewinding the whole parallel plan restores everything
    par.engine.rewind(turn.id)
    assert not any((par.root / "stress").glob("mod_*.hpp"))


def test_same_file_hint_subtasks_never_overlap(project):
    plan = build_workload(6, shared_every=1)  # every subtask also hints stress/shared.hpp
    prov = ConcurrentFake(latency=0.03)
    run = execute(cfg(), 6, 4, provider=prov, plan=plan, root=project)
    assert summarize(run)["done"] == 6
    assert max_concurrency(run.events) == 1 and prov.max_active == 1

    # mixed: only every 2nd shares the hint -> some parallelism remains, shared ones never overlap
    plan = build_workload(8, shared_every=2)
    prov = ConcurrentFake(latency=0.03)
    run = execute(cfg(), 8, 4, provider=prov, plan=plan, root=project / "mixed")
    assert summarize(run)["done"] == 8 and max_concurrency(run.events) >= 2
    spans = sorted(s for sid in ("T2", "T4", "T6", "T8") for s in prov.spans[sid])
    assert all(a[1] <= b[0] + 0.02 for a, b in zip(spans, spans[1:]))  # no overlap between the shared ones


def test_approvals_are_asked_one_at_a_time(project):
    """Writes need approval by default; with 4 parallel agents the user must still see one dialog at a time."""
    from coder_agent.engine import Engine
    from coder_agent.events import EventBus

    from conftest import strict_policy

    strict_policy(project)
    state = {"active": 0, "max": 0, "asked": 0}
    lock = threading.Lock()

    def approver(req):
        with lock:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
            state["asked"] += 1
        time.sleep(0.02)
        with lock:
            state["active"] -= 1
        return ApprovalResponse(True)

    n = 8
    eng = Engine(cfg(), project, EventBus(), approver=approver, provider=ConcurrentFake(latency=0.02))
    eng.config.agent.parallelism = 4
    eng.config.orchestrator.confirm_each_stage = False
    eng.config.orchestrator.doc_analysis = False
    res = eng.submit_plan("g", plan=build_workload(n))
    assert res.status == "done" and state["asked"] == n and state["max"] == 1
    assert len(list((project / "stress").glob("mod_*.hpp"))) == n


def test_failures_do_not_deadlock_or_poison_other_subtasks(project):
    n = 10
    run = execute(cfg(), n, 4, provider=ConcurrentFake(fail_ids={"T3", "T7"}), root=project)
    s = summarize(run)
    assert run.result.status == "partial"
    assert s["done"] == 8 and s["failed"] == 2 and s["verified"] == 8 and s["errors"] >= 2
    statuses = {x["id"]: x["status"] for x in run.engine.active_turns[0].subtasks}
    assert statuses["T3"] == statuses["T7"] == "failed" and statuses["T1"] == "done"
    assert check_expectations([s])  # the report flags the problem


def test_cancel_in_the_middle_of_a_parallel_run(project):
    prov = ConcurrentFake(latency=0.2)
    start = time.perf_counter()
    holder = {}

    def run():
        holder["run"] = execute(cfg(), 12, 4, provider=prov, root=project, timeout=0.3)

    th = threading.Thread(target=run)
    th.start()
    th.join(timeout=15)
    assert not th.is_alive(), "parallel run did not stop after cancel"
    r = holder["run"]
    assert r.cancelled_by_timeout and r.result.status == "cancelled"
    assert time.perf_counter() - start < 5
    assert summarize(r)["done"] < 12
    # whatever was written before the cancel is recorded and can be rolled back
    turn = r.engine.active_turns[0]
    r.engine.rewind(turn.id)
    assert not any((project / "stress").glob("mod_*.hpp"))


def test_many_subtasks_with_commands_stay_consistent(project):
    """Bigger fan-out mixing file writes and shell commands (separate processes + threads)."""
    n = 30
    run = execute(cfg(), n, 6, provider=ConcurrentFake(latency=0.01, run_command=True), root=project)
    s = summarize(run)
    assert s["done"] == n and s["verified"] == n and s["max_concurrency"] <= 6
    outs = [e.data["text"] for e in run.events if e.kind == "tool_result" and e.data["text"].startswith("exit code: 0")]
    assert len(outs) == n and all(f"T" in o for o in outs)
    assert run.engine.active_turns[0].ran_commands


def test_report_formatting():
    base = {"subtasks": 4, "wall_s": 8.0, "done": 4, "failed": 0, "verified": 4, "max_concurrency": 1,
            "avg_subtask_s": 2.0, "avg_ttft_s": 1.0, "max_ttft_s": 1.5, "agg_tok_s": 20.0, "errors": 0,
            "warnings": 0, "timed_out": False}
    rows = [{**base, "parallelism": 1}, {**base, "parallelism": 2, "wall_s": 4.5, "max_concurrency": 2}]
    table = format_table(rows)
    assert "1.78x" in table and "speedup" in table
    assert check_expectations(rows) == []
    slow = [rows[0], {**rows[1], "wall_s": 8.0}]
    assert any("no speed-up" in p for p in check_expectations(slow))
    assert any("exceeds" in p for p in check_expectations([{**base, "parallelism": 1, "max_concurrency": 3}]))
