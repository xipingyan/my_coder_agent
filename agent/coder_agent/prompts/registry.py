"""Task types: a named prompt prefix + hints (keywords/globs for auto-detection, effort, tools).

Prefixes are Markdown files with a small front-matter. Layers (later overrides earlier by name):
    built-in  <  ~/.coder_agent/prompts/  <  config paths.prompt_dirs  <  <project>/.agent/prompts/
Drop a file named like a built-in (e.g. ``cpp_impl.md``) to override it, or a new name to add a type.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Optional

from ..paths import project_cache_dir, user_dir
from ..util import parse_front_matter


@dataclass
class TaskType:
    name: str
    description: str
    prefix: str
    keywords: list[str] = field(default_factory=list)
    globs: list[str] = field(default_factory=list)
    effort: Optional[str] = None  # suggested thinking effort
    tools: Optional[list[str]] = None  # restrict tool set when set
    source: str = "builtin"


def _csv(v: str) -> list[str]:
    return [x.strip() for x in v.split(",") if x.strip()]


def _parse(text: str, fallback_name: str, source: str) -> TaskType:
    meta, body = parse_front_matter(text)
    return TaskType(
        name=meta.get("name", fallback_name),
        description=meta.get("description", ""),
        prefix=body.strip(),
        keywords=_csv(meta.get("keywords", "")),
        globs=_csv(meta.get("globs", "")),
        effort=meta.get("effort") or None,
        tools=_csv(meta["tools"]) if "tools" in meta else None,
        source=source,
    )


class TaskTypeRegistry:
    def __init__(self, types: dict[str, TaskType], base_prompt: str) -> None:
        self.types = types
        self.base_prompt = base_prompt

    @classmethod
    def load(cls, root: Optional[Path] = None, extra_dirs: Optional[list[str]] = None) -> "TaskTypeRegistry":
        pkg = resources.files("coder_agent").joinpath("prompts/defaults")
        types: dict[str, TaskType] = {}
        base = ""
        for entry in sorted(pkg.iterdir(), key=lambda e: e.name):
            if not entry.name.endswith(".md"):
                continue
            text = entry.read_text(encoding="utf-8")
            if entry.name == "base.md":
                base = text.strip()
            else:
                t = _parse(text, entry.name[:-3], "builtin")
                types[t.name] = t

        dirs = [user_dir() / "prompts", *(Path(d).expanduser() for d in (extra_dirs or []))]
        if root:
            dirs.append(project_cache_dir(root) / "prompts")
        for d in dirs:
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.md")):
                text = f.read_text(encoding="utf-8", errors="replace")
                if f.name == "base.md":
                    base = text.strip()
                else:
                    t = _parse(text, f.stem, str(f))
                    types[t.name] = t
        return cls(types, base)

    def names(self) -> list[str]:
        return sorted(self.types)

    def get(self, name: str) -> TaskType:
        return self.types.get(name) or self.types["general"]

    def detect(self, prompt: str, hint_paths: Optional[list[str]] = None) -> str:
        """Pick a type from keywords in the prompt and file names mentioned in it / given as hints."""
        low = prompt.lower()
        mentioned = re.findall(r"[\w./\\-]+\.[A-Za-z0-9]{1,5}\b", prompt) + list(hint_paths or [])
        best, best_score = "general", 0
        for t in self.types.values():
            if t.name == "general":
                continue
            score = sum(2 for k in t.keywords if k.lower() in low)
            score += sum(
                3 for g in t.globs for p in mentioned if fnmatch.fnmatch(p.replace("\\", "/").rsplit("/", 1)[-1], g)
            )
            if score > best_score:
                best, best_score = t.name, score
        return best
