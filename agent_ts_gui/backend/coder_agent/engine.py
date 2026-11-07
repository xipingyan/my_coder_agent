"""Engine: the facade used by GUI / CLI. Owns session, policy, provider creation and one agent run per turn."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

from .agent import Agent, AgentResult, Approver
from .approval import ApprovalPolicy
from .config import AppConfig
from .context import ContextManager
from .events import EventBus
from .llm import LLMError, LLMProvider, create_provider
from .orchestrator import Orchestrator, Plan, PlanError, PlanResult, Planner, StageConfirm
from .prompts import PromptBuilder, PromptSections, TaskTypeRegistry
from .session import Session, Turn
from .tools import ToolContext, ToolRegistry, default_registry


def _serialised(approver: Optional[Approver]) -> Optional[Approver]:
    """Parallel sub-agents may need approval at the same time; ask the user one question at a time."""
    if approver is None:
        return None
    lock = threading.Lock()

    def ask(req):
        with lock:
            return approver(req)

    return ask


class Engine:
    def __init__(
        self,
        config: AppConfig,
        project_root: Path,
        bus: Optional[EventBus] = None,
        approver: Optional[Approver] = None,
        session: Optional[Session] = None,
        provider: Optional[LLMProvider] = None,  # injectable for tests
    ) -> None:
        self.config = config
        self.root = Path(project_root).resolve()
        self.bus = bus or EventBus()
        self.approver = _serialised(approver)
        self.session = session or Session(self.root)
        self._provider_override = provider
        self.policy = ApprovalPolicy.load(self.root)
        self.task_types = TaskTypeRegistry.load(self.root, config.paths.prompt_dirs)
        self.sections = PromptSections()
        self.ctx_mgr = ContextManager(config.context)
        self.registry: ToolRegistry = default_registry()
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------- settings
    def apply_config(self, config: AppConfig) -> None:
        """Call between runs after the user edited settings."""
        self.config = config
        self.ctx_mgr = ContextManager(config.context, self.ctx_mgr.counter)
        self.task_types = TaskTypeRegistry.load(self.root, config.paths.prompt_dirs)

    def reload_policy(self) -> None:
        self.policy.reload()

    def cancel(self) -> None:
        self.cancel_event.set()

    # ----------------------------------------------------------------- run
    def submit(self, prompt: str, task_type: Optional[str] = None) -> AgentResult:
        """Run one user turn to completion (blocking - call from a worker thread)."""
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("a turn is already running")
        try:
            self.cancel_event.clear()
            tt = self.task_types.get(task_type or self.task_types.detect(prompt))
            turn = self.session.new_turn(prompt, tt.name)
            self.session.save()
            self.bus.emit("turn_started", turn_id=turn.id, task_type=tt.name, prompt=prompt)

            provider = self._provider_override or create_provider(self.config)
            registry = self.registry.subset(tt.tools) if tt.tools else self.registry
            tool_ctx = ToolContext(
                root=self.root,
                cancel=self.cancel_event,
                emit=lambda kind, data: self.bus.emit(kind, "main", **data),
                snapshots=self.session.snapshots,
                max_output_chars=self.config.context.tool_output_max_chars,
                extra_roots=self.policy.extra_roots,
            )
            agent = Agent(
                "main", provider, registry, tool_ctx, self.policy, self.approver,
                self.ctx_mgr, self.bus, self.config.agent, self.config.thinking, self.cancel_event,
            )
            system = PromptBuilder(self.root, self.task_types, self.sections).build(tt)
            messages = [{"role": "system", "content": system}, *self.session.build_messages()]

            def on_append(msg: dict) -> None:
                turn.messages.append(msg)

            def on_compact(new_full: list[dict]) -> None:
                turn.archive.extend(turn.messages)
                turn.messages = []
                turn.base_messages = [m for m in new_full if m.get("role") != "system"]

            # The user message is already in turn.messages and in `messages`.
            result = agent.run(messages, on_append=on_append, on_compact=on_compact)
            turn.ran_commands = agent.ran_commands
            self.session.finish_turn(turn, result.status, result.final_text)
            self.bus.emit("turn_finished", turn_id=turn.id, status=result.status, error=result.error)
            self.bus.emit("status", state="idle", detail="")
            return result
        finally:
            self._lock.release()

    # ------------------------------------------------------ plan (subtasks)
    def submit_plan(
        self,
        goal: str,
        confirm_plan: Optional[Callable[[Plan], Optional[Plan]]] = None,
        confirm_stage: Optional[StageConfirm] = None,
        plan: Optional[Plan] = None,
    ) -> PlanResult | None:
        """Split ``goal`` into subtasks, let the user review the plan, run it stage by stage.

        Pass ``plan`` to skip the planner (e.g. a saved or hand-written plan).
        Blocking. Returns ``None`` if planning failed or the user rejected the plan."""
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("a turn is already running")
        try:
            self.cancel_event.clear()
            turn = self.session.new_turn(goal, "plan")
            self.session.save()
            self.bus.emit("turn_started", turn_id=turn.id, task_type="plan", prompt=goal)
            provider = self._provider_override or create_provider(self.config)
            status, text, result = "error", "", None
            try:
                self.bus.emit("status", "planner", state="waiting_model", detail="planning")
                if plan is None:
                    plan = Planner(
                        provider, self.root, self.task_types, self.config.orchestrator.max_subtasks,
                        self.config.orchestrator.planner_thinking, self.cancel_event,
                    ).make_plan(goal)
                else:
                    plan.validate()
                self.bus.emit("plan_created", "planner", plan=plan.to_dict())
                if confirm_plan:
                    self.bus.emit("status", "planner", state="waiting_user", detail="review the plan")
                    plan = confirm_plan(plan)
                if plan is None:
                    status, text = "cancelled", "Plan rejected by the user."
                else:
                    result = Orchestrator(self, provider, turn).run(plan, confirm_stage)
                    status, text = result.status, result.summary
                    turn.ran_commands = result.ran_commands
                    turn.subtasks = [
                        {"id": s.id, "title": s.title, "status": s.status, "summary": s.summary}
                        for s in plan.subtasks
                    ]
            except (PlanError, LLMError) as e:
                text = f"Planning failed: {e}"
                self.bus.emit("error", "planner", text=text)
            turn.messages.append({"role": "assistant", "content": text})
            self.session.finish_turn(turn, status, text)
            self.bus.emit("turn_finished", turn_id=turn.id, status=status, error="")
            self.bus.emit("status", state="idle", detail="")
            return result
        finally:
            self._lock.release()

    # ------------------------------------------------------ rewind / edit
    def rewind(self, turn_id: str, keep_branch: bool = False) -> tuple[list[str], bool]:
        """Undo ``turn_id`` and later turns. Returns (restored_files, has_unrevertable_commands)."""
        if self._lock.locked():
            raise RuntimeError("cannot rewind while a turn is running")
        side = self.session.side_effects_after(turn_id)
        restored = self.session.rewind(turn_id, keep_branch)
        self.bus.emit("session_rewound", turn_id=turn_id, restored=restored)
        return restored, side

    def edit_and_resubmit(self, turn_id: str, new_prompt: str, task_type: Optional[str] = None,
                          keep_branch: bool = False) -> AgentResult:
        self.rewind(turn_id, keep_branch)
        return self.submit(new_prompt, task_type)

    @property
    def active_turns(self) -> list[Turn]:
        return self.session.path()
