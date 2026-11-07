"""Runs a plan: stage by stage, each subtask in its own agent, with user confirmation between stages."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

from ..agent import Agent
from ..context import ContextManager
from ..prompts import PromptBuilder
from ..tools import ToolContext
from .docs import DocAnalyzer
from .models import Plan, SubTask

if TYPE_CHECKING:
    from ..engine import Engine
    from ..llm import LLMProvider
    from ..session import Turn

CONTINUE, RETRY, STOP = "continue", "retry", "stop"


@dataclass
class StageReport:
    index: int
    total: int
    subtasks: list[SubTask]
    changed_files: list[str] = field(default_factory=list)
    is_last: bool = False


StageConfirm = Callable[[StageReport], str]  # -> CONTINUE | RETRY | STOP


@dataclass
class PlanResult:
    plan: Plan
    status: str  # done | partial | stopped | cancelled
    summary: str
    ran_commands: bool = False


def _waves(stage: list[SubTask]) -> list[list[SubTask]]:
    """Split a stage so that no two subtasks in one wave declare the same file hint."""
    waves: list[tuple[list[SubTask], set[str]]] = []
    for st in stage:
        hints = {f.strip("/").lower() for f in st.files if f}
        for members, used in waves:
            if not (hints & used):
                members.append(st)
                used |= hints
                break
        else:
            waves.append(([st], set(hints)))
    return [m for m, _ in waves]


class Orchestrator:
    def __init__(self, engine: "Engine", provider: "LLMProvider", turn: "Turn") -> None:
        self.engine = engine
        self.provider = provider
        self.turn = turn
        self.cfg = engine.config
        self.bus = engine.bus
        self.cancel: threading.Event = engine.cancel_event
        self.docs = DocAnalyzer(provider, engine.root, engine.bus, self.cancel)
        self._ran_commands = False
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- helpers
    def _emit_plan(self, kind: str, plan: Plan, **extra) -> None:
        self.bus.emit(kind, "planner", plan=plan.to_dict(), **extra)

    def _brief(self, plan: Plan, st: SubTask, docs_summary: str) -> str:
        lines = ["# Your subtask", f"Overall goal: {plan.goal}", f"Subtask {st.id}: {st.title}", st.goal]
        if st.files:
            lines.append("Relevant paths: " + ", ".join(st.files))
        prev = [plan.get(d) for d in st.depends_on]
        if prev:
            lines.append("\nResults of the subtasks you depend on:")
            lines += [f"- {p.id} ({p.title}): {p.summary[:600]}" for p in prev]
        if docs_summary:
            lines.append("\nNotes from the module documentation (already read for you):\n" + docs_summary)
        lines.append(
            "\nDo ONLY this subtask; other subtasks are handled separately. When finished, reply with a brief "
            "summary: what you changed (files), how you verified it, and anything the next subtask must know. "
            "If you cannot complete it, say exactly why."
        )
        return "\n".join(lines)

    # ---------------------------------------------------------------- subtask
    def _run_subtask(self, plan: Plan, st: SubTask) -> None:
        e = self.engine
        aid = f"sub:{st.id}"
        if self.cancel.is_set():
            st.status = "cancelled"
            return
        st.status = "running"
        self._emit_plan("subtask_started", plan, id=st.id)
        try:
            docs_summary = self.docs.analyze(st, aid) if self.cfg.orchestrator.doc_analysis else ""
            tt = e.task_types.get(st.task_type)
            registry = e.registry.subset(tt.tools) if tt.tools else e.registry
            tool_ctx = ToolContext(
                root=e.root,
                cancel=self.cancel,
                emit=lambda kind, data, _aid=aid: self.bus.emit(kind, _aid, **data),
                snapshots=e.session.snapshots,
                agent_id=aid,
                max_output_chars=self.cfg.context.tool_output_max_chars,
                extra_roots=e.policy.extra_roots,
            )
            agent = Agent(
                aid, self.provider, registry, tool_ctx, e.policy, e.approver,
                ContextManager(self.cfg.context, e.ctx_mgr.counter), self.bus,
                self.cfg.agent.model_copy(update={"max_steps": self.cfg.orchestrator.subtask_max_steps}),
                self.cfg.thinking, self.cancel,
            )
            system = PromptBuilder(e.root, e.task_types, e.sections).build(tt, extra=self._brief(plan, st, docs_summary))
            res = agent.run([
                {"role": "system", "content": system},
                {"role": "user", "content": st.goal},
            ])
            st.steps = res.steps
            st.summary = (res.final_text or res.error or f"({res.status})").strip()
            st.status = {"done": "done", "cancelled": "cancelled"}.get(res.status, "failed")
            if res.status == "max_steps":
                st.summary = f"[stopped: step limit reached] {st.summary}"
            if agent.ran_commands:
                with self._lock:
                    self._ran_commands = True
        except Exception as ex:  # noqa: BLE001 - a crashing subtask must not kill the plan
            st.status, st.summary = "failed", f"{type(ex).__name__}: {ex}"
        self._emit_plan("subtask_finished", plan, id=st.id, status=st.status)

    # ------------------------------------------------------------------- run
    def run(self, plan: Plan, confirm_stage: Optional[StageConfirm] = None) -> PlanResult:
        stages = plan.stages()
        self._emit_plan("plan_started", plan)
        i, outcome = 0, "done"
        while i < len(stages):
            if self.cancel.is_set():
                outcome = "cancelled"
                break
            stage = [s for s in stages[i] if s.status in ("pending", "failed", "skipped", "cancelled")]
            runnable = []
            for s in stage:
                if any(plan.get(d).status != "done" for d in s.depends_on):
                    s.status, s.summary = "skipped", "a dependency did not finish"
                else:
                    runnable.append(s)
            self.bus.emit("stage_started", "planner", index=i, total=len(stages), ids=[s.id for s in runnable])
            for wave in _waves(runnable):
                with ThreadPoolExecutor(max_workers=max(1, self.cfg.agent.parallelism)) as pool:
                    list(pool.map(lambda s: self._run_subtask(plan, s), wave))
                if self.cancel.is_set():
                    break
            self._emit_plan("stage_finished", plan, index=i)
            if self.cancel.is_set():
                outcome = "cancelled"
                break
            failed = [s for s in stages[i] if s.status in ("failed", "skipped")]
            is_last = i == len(stages) - 1
            if confirm_stage and self.cfg.orchestrator.confirm_each_stage and (not is_last or failed):
                report = StageReport(i, len(stages), list(stages[i]),
                                     self.engine.session.snapshots.changed_files(self.turn.id), is_last)
                decision = confirm_stage(report)
                if decision == STOP:
                    outcome = "stopped"
                    break
                if decision == RETRY and failed:
                    for s in failed:
                        s.status = "pending"
                    continue
            i += 1

        statuses = [s.status for s in plan.subtasks]
        if outcome == "done" and any(s != "done" for s in statuses):
            outcome = "partial"
        lines = [f"Plan for: {plan.goal}", ""]
        for s in plan.subtasks:
            lines.append(f"- {s.id} [{s.status}] {s.title}: {_oneline(s.summary, 400)}")
        self._emit_plan("plan_finished", plan, status=outcome)
        return PlanResult(plan, outcome, "\n".join(lines), self._ran_commands)


def _oneline(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"
