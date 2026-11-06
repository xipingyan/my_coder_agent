"""Plan / sub-task data model."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class PlanError(ValueError):
    pass


@dataclass
class SubTask:
    id: str
    title: str
    goal: str
    depends_on: list[str] = field(default_factory=list)
    task_type: str = "general"
    files: list[str] = field(default_factory=list)  # path / module hints (used for docs + conflict avoidance)
    status: str = "pending"  # pending | running | done | failed | skipped | cancelled
    summary: str = ""
    steps: int = 0

    @classmethod
    def from_dict(cls, d: dict[str, Any], default_type: str = "general") -> "SubTask":
        if not isinstance(d, dict) or not d.get("goal"):
            raise PlanError("every subtask needs a 'goal'")
        sid = str(d.get("id") or "").strip()
        if not sid:
            raise PlanError("every subtask needs an 'id'")
        deps = d.get("depends_on") or []
        files = d.get("files") or []
        if isinstance(deps, str):
            deps = [x.strip() for x in deps.split(",") if x.strip()]
        if isinstance(files, str):
            files = [x.strip() for x in files.split(",") if x.strip()]
        return cls(
            id=sid,
            title=str(d.get("title") or d["goal"])[:80],
            goal=str(d["goal"]),
            depends_on=[str(x) for x in deps],
            task_type=str(d.get("task_type") or default_type),
            files=[str(x) for x in files],
        )


@dataclass
class Plan:
    goal: str
    subtasks: list[SubTask] = field(default_factory=list)

    def get(self, sid: str) -> SubTask:
        for s in self.subtasks:
            if s.id == sid:
                return s
        raise KeyError(sid)

    def validate(self) -> None:
        if not self.subtasks:
            raise PlanError("plan has no subtasks")
        ids = [s.id for s in self.subtasks]
        if len(set(ids)) != len(ids):
            raise PlanError("duplicate subtask ids")
        for s in self.subtasks:
            for d in s.depends_on:
                if d not in ids:
                    raise PlanError(f"{s.id} depends on unknown subtask '{d}'")
                if d == s.id:
                    raise PlanError(f"{s.id} depends on itself")
        self.stages()  # raises on cycles

    def stages(self) -> list[list[SubTask]]:
        """Topological levels: every subtask in a level only depends on earlier levels."""
        remaining = {s.id: s for s in self.subtasks}
        done: set[str] = set()
        levels: list[list[SubTask]] = []
        while remaining:
            level = [s for s in remaining.values() if all(d in done for d in s.depends_on)]
            if not level:
                raise PlanError("dependency cycle between: " + ", ".join(remaining))
            levels.append(level)
            for s in level:
                done.add(s.id)
                del remaining[s.id]
        return levels

    def to_dict(self) -> dict:
        return {"goal": self.goal, "subtasks": [asdict(s) for s in self.subtasks]}

    @classmethod
    def from_dict(cls, d: dict[str, Any], goal: str = "") -> "Plan":
        items = d.get("subtasks") if isinstance(d, dict) else None
        if not isinstance(items, list):
            raise PlanError("JSON must contain a 'subtasks' list")
        plan = cls(d.get("goal") or goal, [SubTask.from_dict(x) for x in items])
        plan.validate()
        return plan
