"""Small shared helpers (TOML writer, front-matter parser, text truncation)."""

from __future__ import annotations

import json
from typing import Any


# ---------------------------------------------------------------- TOML writer
def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"unsupported TOML value: {type(v)}")


def dump_toml(data: dict, _prefix: str = "") -> str:
    """Minimal TOML writer: nested tables, scalars and lists of scalars.

    ``None`` values are skipped (TOML has no null).
    """
    lines: list[str] = []
    tables: list[tuple[str, dict]] = []
    for k, v in data.items():
        if v is None:
            continue
        if isinstance(v, dict):
            tables.append((k, v))
        else:
            lines.append(f"{k} = {_toml_value(v)}")
    out = "\n".join(lines)
    for k, v in tables:
        name = f"{_prefix}.{k}" if _prefix else k
        body = dump_toml(v, name)
        out += f"\n\n[{name}]\n{body}" if out else f"[{name}]\n{body}"
    return out.rstrip("\n") + "\n" if not _prefix else out


# ------------------------------------------------------------ front matter
def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Parse a leading ``---`` block of ``key: value`` lines."""
    text = text.lstrip("\ufeff")
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip("\"'")
    return meta, "\n".join(lines[end + 1:]).lstrip("\n")


# ------------------------------------------------------------- truncation
def truncate_middle(text: str, max_chars: int, marker: str = "\n...[{n} chars omitted]...\n") -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    head = int(max_chars * 0.6)
    tail = max_chars - head
    omitted = len(text) - head - tail
    return text[:head] + marker.format(n=omitted) + text[len(text) - tail:]
