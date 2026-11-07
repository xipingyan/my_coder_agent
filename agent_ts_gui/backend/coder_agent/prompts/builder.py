"""Assemble the system prompt from independent, individually switchable sections."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import knowledge
from .registry import TaskType, TaskTypeRegistry


@dataclass
class PromptSections:
    base: bool = True
    task_prefix: bool = True
    instructions: bool = True
    indexes: bool = True  # skills / module docs / scoped instructions
    memory: bool = True


class PromptBuilder:
    def __init__(self, root: Path, registry: TaskTypeRegistry, sections: Optional[PromptSections] = None) -> None:
        self.root = Path(root)
        self.registry = registry
        self.sections = sections or PromptSections()

    def build(self, task_type: TaskType, extra: str = "") -> str:
        s, parts = self.sections, []
        if s.base and self.registry.base_prompt:
            parts.append(self.registry.base_prompt.replace("{project_root}", str(self.root)))
        if s.task_prefix and task_type.prefix:
            parts.append("# Task type: " + task_type.name + "\n" + task_type.prefix)
        if s.instructions:
            ins = knowledge.load_general_instructions(self.root)
            if ins:
                parts.append("# Project instructions\n" + ins)
        if s.indexes:
            idx: list[str] = []
            mods = knowledge.module_index(self.root)
            if mods:
                idx.append("Module docs (read the relevant MODULE.md before working in a module):")
                idx += [f"- {e.name}: {e.description} [{e.path}]" for e in mods]
            scoped = knowledge.scoped_instruction_index(self.root)
            if scoped:
                idx.append("Extra instructions (read when relevant):")
                idx += [f"- {e.name}: {e.description} [{e.path}]" for e in scoped]
            skills = knowledge.skill_index(self.root)
            if skills:
                idx.append("Skills (read the SKILL.md when relevant):")
                idx += [f"- {e.name}: {e.description} [{e.path}]" for e in skills]
            if idx:
                parts.append("# Available knowledge\n" + "\n".join(idx))
        if s.memory:
            mem = knowledge.load_memory(self.root)
            if mem:
                parts.append("# Notes from earlier sessions\n" + mem)
        if extra:
            parts.append(extra)
        return "\n\n".join(parts)
