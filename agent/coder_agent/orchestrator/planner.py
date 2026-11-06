"""Planner: ask the model to split a goal into small, ordered subtasks (JSON)."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Optional

from ..knowledge import module_index
from ..llm import LLMProvider
from ..prompts import TaskTypeRegistry
from .models import Plan, PlanError

PLANNER_SYSTEM = """You are the planner of a coding agent that uses a small local model, so every subtask must be SMALL and concrete.
Split the user's goal into at most {max_subtasks} subtasks. Rules:
- One subtask = one focused change (about 1-3 files, at most ~10 tool calls): e.g. "add class X header+source", "register X in CMakeLists.txt", "build and fix errors", "write unit test for X".
- Put verification (build / run tests) in its own final subtask.
- Use "depends_on" for ordering. Subtasks with no dependency between them may run in parallel, so subtasks that edit the SAME file must depend on each other.
- "files" lists the paths or module directories the subtask touches or needs to read (used to load the right module docs).
- "task_type" is one of: {types}.
- A simple goal may need only 1-2 subtasks. Do not invent extra work.
Answer with ONLY a JSON object, no prose:
{{"subtasks": [{{"id": "T1", "title": "short title", "goal": "precise instruction for the subtask", "depends_on": [], "task_type": "cpp_impl", "files": ["src/foo.cpp"]}}]}}"""


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (handles ``` fences and prose around it)."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = [m.group(1)] if m else []
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    raise PlanError("reply did not contain a valid JSON object")


class Planner:
    def __init__(self, provider: LLMProvider, root: Path, types: TaskTypeRegistry,
                 max_subtasks: int = 8, thinking: bool = False,
                 cancel: Optional[threading.Event] = None, attempts: int = 3) -> None:
        self.provider = provider
        self.root = Path(root)
        self.types = types
        self.max_subtasks = max_subtasks
        self.thinking = thinking
        self.cancel = cancel
        self.attempts = attempts

    def _context(self) -> str:
        entries = sorted(p.name + ("/" if p.is_dir() else "") for p in self.root.iterdir()
                         if not p.name.startswith(".") and p.name not in ("build", "node_modules"))
        lines = ["Project root entries: " + ", ".join(entries[:50])]
        mods = module_index(self.root)
        if mods:
            lines.append("Modules (each has a MODULE.md):")
            lines += [f"- {m.name}: {m.description}" for m in mods]
        return "\n".join(lines)

    def make_plan(self, goal: str) -> Plan:
        types = ", ".join(self.types.names())
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM.format(max_subtasks=self.max_subtasks, types=types)},
            {"role": "user", "content": f"{self._context()}\n\nGoal:\n{goal}"},
        ]
        last_err = ""
        for _ in range(self.attempts):
            reply = self.provider.complete(messages, self.cancel, thinking=None if self.thinking else False,
                                           max_tokens=3000)
            try:
                plan = Plan.from_dict(extract_json(reply), goal)
                if len(plan.subtasks) > self.max_subtasks * 2:
                    raise PlanError(f"too many subtasks ({len(plan.subtasks)}); at most {self.max_subtasks}")
                for s in plan.subtasks:
                    if s.task_type not in self.types.types:
                        s.task_type = "general"
                return plan
            except PlanError as e:
                last_err = str(e)
                messages += [
                    {"role": "assistant", "content": reply},
                    {"role": "user", "content": f"Invalid plan: {e}. Reply again with ONLY the corrected JSON object."},
                ]
        raise PlanError(f"planner failed after {self.attempts} attempts: {last_err}")
