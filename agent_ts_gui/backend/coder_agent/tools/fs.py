"""File-system tools: read / list / find / grep / write / patch."""

from __future__ import annotations

import difflib
import fnmatch
import os
import re
from pathlib import Path

from .base import Tool, ToolContext, ToolError

IGNORED_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".agent", ".idea", ".vscode", ".cache"}
MAX_READ_LINES = 500
MAX_FILE_BYTES = 2_000_000


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
        for f in sorted(filenames):
            yield Path(dirpath) / f


def _read_text(path: Path) -> str:
    if not path.is_file():
        raise ToolError(f"file not found: {path.name}")
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        raise ToolError("binary file")
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- read_file
def read_file(ctx: ToolContext, a: dict) -> str:
    path = ctx.resolve_readable(a["path"])
    lines = _read_text(path).splitlines()
    total = len(lines)
    start = max(1, a.get("start_line", 1))
    end = a.get("end_line") or min(total, start + MAX_READ_LINES - 1)
    end = min(end, total, start + MAX_READ_LINES - 1)
    if start > total and total:
        raise ToolError(f"start_line {start} is past the end of file ({total} lines)")
    body = "\n".join(f"{i:>5} | {lines[i - 1]}" for i in range(start, end + 1))
    head = f"{ctx.rel(path)} (lines {start}-{end} of {total})"
    if end < total:
        head += f" - more available, call again with start_line={end + 1}"
    return head + "\n" + body


# ----------------------------------------------------------------- list_dir
def list_dir(ctx: ToolContext, a: dict) -> str:
    base = ctx.resolve_readable(a.get("path", "."))
    if not base.is_dir():
        raise ToolError(f"not a directory: {a.get('path', '.')}")
    depth = max(1, min(a.get("depth", 1), 4))
    out: list[str] = []

    def rec(d: Path, level: int) -> None:
        for e in sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
            if e.name in IGNORED_DIRS:
                continue
            out.append("  " * level + e.name + ("/" if e.is_dir() else ""))
            if len(out) >= 300:
                return
            if e.is_dir() and level + 1 < depth:
                rec(e, level + 1)

    rec(base, 0)
    text = "\n".join(out) or "(empty)"
    return text + ("\n...(truncated at 300 entries)" if len(out) >= 300 else "")


# ---------------------------------------------------------------- find_files
def find_files(ctx: ToolContext, a: dict) -> str:
    pat = a["pattern"]
    base = ctx.resolve(a.get("path", "."))
    hits = []
    for f in _walk(base):
        rel = ctx.rel(f)
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(f.name, pat):
            hits.append(rel)
            if len(hits) >= 200:
                break
    return "\n".join(hits) if hits else "(no matches)"


# --------------------------------------------------------------- grep_search
def grep_search(ctx: ToolContext, a: dict) -> str:
    pattern = a["pattern"]
    base = ctx.resolve(a.get("path", "."))
    flags = 0 if a.get("case_sensitive", False) else re.IGNORECASE
    try:
        rx = re.compile(pattern if a.get("is_regex", False) else re.escape(pattern), flags)
    except re.error as e:
        raise ToolError(f"invalid regex: {e}")
    glob = a.get("glob")
    files = [base] if base.is_file() else _walk(base)
    hits: list[str] = []
    for f in files:
        if glob and not (fnmatch.fnmatch(f.name, glob) or fnmatch.fnmatch(ctx.rel(f), glob)):
            continue
        try:
            if f.stat().st_size > MAX_FILE_BYTES:
                continue
            data = f.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:4096]:
            continue
        for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if rx.search(line):
                hits.append(f"{ctx.rel(f)}:{n}: {line.strip()[:200]}")
                if len(hits) >= 100:
                    return "\n".join(hits) + "\n...(truncated at 100 matches)"
    return "\n".join(hits) if hits else "(no matches)"


# --------------------------------------------------------------- write_file
def _unified(old: str, new: str, name: str) -> str:
    diff = difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True), f"a/{name}", f"b/{name}"
    )
    text = "".join(d if d.endswith("\n") else d + "\n" for d in diff)
    return text or "(no changes)"


def _current(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace") if path.is_file() else ""


def write_file(ctx: ToolContext, a: dict) -> str:
    path = ctx.resolve(a["path"])
    if path.is_dir():
        raise ToolError("path is a directory")
    if ctx.snapshots:
        ctx.snapshots.record(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = a["content"]
    path.write_text(content, encoding="utf-8", newline="")
    return f"Wrote {len(content.splitlines())} lines to {ctx.rel(path)}"


def write_file_preview(ctx: ToolContext, a: dict) -> str:
    path = ctx.resolve(a["path"])
    return _unified(_current(path), a["content"], ctx.rel(path))


# -------------------------------------------------------------- apply_patch
def _norm(s: str) -> str:
    return s.replace("\r\n", "\n")


def _replace(text: str, search: str, replace: str, replace_all: bool) -> tuple[str, int]:
    if not search:
        raise ToolError("'search' must not be empty (use write_file to create or overwrite files)")
    n = text.count(search)
    if n == 1 or (n > 1 and replace_all):
        return text.replace(search, replace), n
    if n > 1:
        raise ToolError(f"'search' matches {n} places; add more surrounding context or set replace_all=true")
    # Fallback: ignore trailing whitespace differences line by line.
    lines = text.split("\n")
    s_lines = [l.rstrip() for l in search.split("\n")]
    hits = [
        i
        for i in range(len(lines) - len(s_lines) + 1)
        if all(lines[i + k].rstrip() == s_lines[k] for k in range(len(s_lines)))
    ]
    if len(hits) == 1:
        i = hits[0]
        return "\n".join(lines[:i] + replace.split("\n") + lines[i + len(s_lines):]), 1
    if len(hits) > 1 and replace_all:
        for i in reversed(hits):
            lines[i:i + len(s_lines)] = replace.split("\n")
        return "\n".join(lines), len(hits)
    if len(hits) > 1:
        raise ToolError(f"'search' matches {len(hits)} places; add more surrounding context")
    raise ToolError(
        "'search' text not found. It must match the file exactly (do not include the line-number "
        "prefix shown by read_file). Re-read the file and try again."
    )


def _patched(ctx: ToolContext, a: dict) -> tuple[Path, str, str, bool]:
    path = ctx.resolve(a["path"])
    raw = _read_text(path)
    crlf = "\r\n" in raw
    old = _norm(raw)
    new, _ = _replace(old, _norm(a["search"]), _norm(a["replace"]), bool(a.get("replace_all", False)))
    return path, old, new, crlf


def apply_patch(ctx: ToolContext, a: dict) -> str:
    path, old, new, crlf = _patched(ctx, a)
    if ctx.snapshots:
        ctx.snapshots.record(path)
    path.write_text(new.replace("\n", "\r\n") if crlf else new, encoding="utf-8", newline="")
    return f"Patched {ctx.rel(path)}\n" + _unified(old, new, ctx.rel(path))


def apply_patch_preview(ctx: ToolContext, a: dict) -> str:
    path, old, new, _ = _patched(ctx, a)
    return _unified(old, new, ctx.rel(path))


def fs_tools() -> list[Tool]:
    P = "path relative to the project root (absolute paths are accepted only inside trusted dirs such as /tmp)"
    return [
        Tool(
            "read_file",
            "Read a text file with line numbers (the 'N |' prefix is NOT part of the file). "
            f"Returns at most {MAX_READ_LINES} lines per call.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": P},
                    "start_line": {"type": "integer", "description": "1-based first line"},
                    "end_line": {"type": "integer", "description": "1-based last line (inclusive)"},
                },
                "required": ["path"],
            },
            read_file,
        ),
        Tool(
            "list_dir",
            "List a directory (dirs end with '/').",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": P},
                    "depth": {"type": "integer", "description": "levels to descend, 1-4"},
                },
            },
            list_dir,
        ),
        Tool(
            "find_files",
            "Find files by glob pattern, e.g. '*.cpp' or 'src/**/CMakeLists.txt'.",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "directory to search in"},
                },
                "required": ["pattern"],
            },
            find_files,
        ),
        Tool(
            "grep_search",
            "Search file contents. Case-insensitive plain text by default.",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "file or directory"},
                    "glob": {"type": "string", "description": "only files matching, e.g. '*.hpp'"},
                    "is_regex": {"type": "boolean"},
                    "case_sensitive": {"type": "boolean"},
                },
                "required": ["pattern"],
            },
            grep_search,
        ),
        Tool(
            "write_file",
            "Create a new file or fully overwrite an existing one. For small changes to existing "
            "files use apply_patch instead.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": P},
                    "content": {"type": "string", "description": "complete file content"},
                },
                "required": ["path", "content"],
            },
            write_file,
            kind="write",
            preview=write_file_preview,
        ),
        Tool(
            "apply_patch",
            "Edit an existing file by replacing exact text. 'search' must match the file content "
            "exactly and be unique (include a few surrounding lines).",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": P},
                    "search": {"type": "string", "description": "exact text to find"},
                    "replace": {"type": "string", "description": "replacement text"},
                    "replace_all": {"type": "boolean", "description": "replace every occurrence"},
                },
                "required": ["path", "search", "replace"],
            },
            apply_patch,
            kind="write",
            preview=apply_patch_preview,
        ),
    ]
