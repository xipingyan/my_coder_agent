"""Knowledge sources injected into the system prompt.

Where things live
-----------------
* repo-specific  -> inside the target repo: ``.github/`` (instructions, skills) and
                    ``.agent/memory.md`` (agent notes about *this* repo)
* shared         -> agent's own area ``~/.coder_agent/`` (skills/, memory/global.md)

Large documents are not pasted into the prompt; only a short index is, and the
model reads the files on demand (keeps the context small for local models).
"""

from __future__ import annotations

import fnmatch
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .paths import github_dir, project_cache_dir, user_dir
from .tools.base import Tool, ToolContext, ToolError
from .util import parse_front_matter

IGNORED = {".git", "node_modules", ".venv", "venv", "__pycache__", ".agent", "build", "out", "third_party"}
MEMORY_PROMPT_CHARS = 3000
MEMORY_MAX_CHARS = 20000


@dataclass
class IndexEntry:
    name: str
    description: str
    path: str  # as shown to the model (relative to root when possible)


# ------------------------------------------------------------- instructions
def load_general_instructions(root: Path) -> str:
    """``.github/copilot-instructions.md`` + instruction files whose applyTo is global."""
    parts: list[str] = []
    gh = github_dir(root)
    main = gh / "copilot-instructions.md"
    if main.is_file():
        parts.append(main.read_text(encoding="utf-8", errors="replace").strip())
    for f in sorted((gh / "instructions").glob("*.instructions.md")):
        meta, body = parse_front_matter(f.read_text(encoding="utf-8", errors="replace"))
        if meta.get("applyTo", "**").strip() in ("**", "*", "**/*"):
            parts.append(body.strip())
    return "\n\n".join(p for p in parts if p)


def scoped_instruction_index(root: Path) -> list[IndexEntry]:
    out: list[IndexEntry] = []
    for f in sorted((github_dir(root) / "instructions").glob("*.instructions.md")):
        meta, _ = parse_front_matter(f.read_text(encoding="utf-8", errors="replace"))
        scope = meta.get("applyTo", "**").strip()
        if scope not in ("**", "*", "**/*"):
            out.append(IndexEntry(f.name, f"applies to {scope}. {meta.get('description', '')}".strip(), f.relative_to(root).as_posix()))
    return out


def instructions_for_paths(root: Path, rel_paths: list[str]) -> str:
    """Bodies of scoped instruction files matching any of ``rel_paths``."""
    parts: list[str] = []
    for f in sorted((github_dir(root) / "instructions").glob("*.instructions.md")):
        meta, body = parse_front_matter(f.read_text(encoding="utf-8", errors="replace"))
        globs = [g.strip() for g in meta.get("applyTo", "").split(",") if g.strip()]
        if globs and any(fnmatch.fnmatch(p, g) or fnmatch.fnmatch(os.path.basename(p), g) for p in rel_paths for g in globs):
            parts.append(body.strip())
    return "\n\n".join(parts)


# ------------------------------------------------------------------- skills
def skill_index(root: Path) -> list[IndexEntry]:
    entries: list[IndexEntry] = []
    for base, label_root in ((github_dir(root) / "skills", root), (user_dir() / "skills", None)):
        for f in sorted(base.glob("*/SKILL.md")):
            meta, _ = parse_front_matter(f.read_text(encoding="utf-8", errors="replace"))
            shown = f.relative_to(root).as_posix() if label_root else str(f)
            entries.append(IndexEntry(meta.get("name", f.parent.name), meta.get("description", ""), shown))
    return entries


# ---------------------------------------------------------- module docs
def module_index(root: Path, max_depth: int = 5, limit: int = 60) -> list[IndexEntry]:
    """Every directory holding a MODULE.md, with its one-line description."""
    entries: list[IndexEntry] = []
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED and depth < max_depth)
        if "MODULE.md" in filenames:
            f = Path(dirpath) / "MODULE.md"
            _, body = parse_front_matter(f.read_text(encoding="utf-8", errors="replace"))
            heading = desc = ""
            for line in body.splitlines():
                s = line.strip()
                if not s:
                    continue
                if s.startswith("#"):
                    heading = heading or s.lstrip("#").strip()
                    continue
                desc = s
                break
            desc = desc or heading
            rel = f.relative_to(root).as_posix()
            entries.append(IndexEntry(f.parent.relative_to(root).as_posix() or ".", desc[:160], rel))
            if len(entries) >= limit:
                break
    return entries


# ------------------------------------------------------------------- memory
def _memory_paths(root: Path) -> dict[str, Path]:
    return {
        "project": project_cache_dir(root) / "memory.md",
        "global": user_dir() / "memory" / "global.md",
    }


def load_memory(root: Path) -> str:
    parts: list[str] = []
    for scope, p in _memory_paths(root).items():
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                parts.append(f"[{scope}]\n{text[-MEMORY_PROMPT_CHARS:]}")
    return "\n\n".join(parts)


def append_memory(root: Path, note: str, scope: str = "project") -> Path:
    paths = _memory_paths(root)
    if scope not in paths:
        raise ToolError("scope must be 'project' or 'global'")
    note = " ".join(note.split())
    if not note:
        raise ToolError("empty note")
    p = paths[scope]
    p.parent.mkdir(parents=True, exist_ok=True)
    old = p.read_text(encoding="utf-8") if p.is_file() else ""
    if note in old:
        return p
    new = old + f"- {time.strftime('%Y-%m-%d')}: {note}\n"
    p.write_text(new[-MEMORY_MAX_CHARS:], encoding="utf-8")
    return p


def memory_tools() -> list[Tool]:
    def remember(ctx: ToolContext, a: dict) -> str:
        p = append_memory(ctx.root, a["note"], a.get("scope", "project"))
        return f"Saved to {p}"

    return [
        Tool(
            "remember",
            "Save a short, durable fact for future sessions (build commands, conventions, pitfalls). "
            "scope 'project' = this repo only, 'global' = all projects. One sentence per note.",
            {
                "type": "object",
                "properties": {
                    "note": {"type": "string"},
                    "scope": {"type": "string", "enum": ["project", "global"]},
                },
                "required": ["note"],
            },
            remember,
        )
    ]
