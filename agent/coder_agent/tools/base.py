"""Tool abstraction, sandboxed context and registry."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from ..util import truncate_middle


class ToolError(Exception):
    """Expected failure; the message is returned to the model."""


class SnapshotRecorder(Protocol):
    def record(self, path: Path) -> None: ...


@dataclass
class ToolContext:
    root: Path
    cancel: threading.Event = field(default_factory=threading.Event)
    emit: Callable[[str, dict], None] = lambda kind, data: None
    snapshots: Optional[SnapshotRecorder] = None
    agent_id: str = "main"
    max_output_chars: int = 12000

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()

    def resolve(self, p: str) -> Path:
        """Resolve ``p`` (relative to root or absolute) and refuse to leave the root."""
        path = Path(p).expanduser()
        if not path.is_absolute():
            path = self.root / path
        path = path.resolve()
        if path != self.root and self.root not in path.parents:
            raise ToolError(f"path '{p}' is outside the project root {self.root}")
        return path

    def rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(path)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON schema
    func: Callable[[ToolContext, dict], str]
    kind: str = "read"  # read | write | exec
    preview: Optional[Callable[[ToolContext, dict], str]] = None  # diff / detail for approval

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


def _coerce(value: Any, spec: dict) -> Any:
    t = spec.get("type")
    try:
        if t == "integer" and not isinstance(value, bool):
            return int(value)
        if t == "number":
            return float(value)
        if t == "boolean" and isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes")
    except (TypeError, ValueError):
        raise ToolError(f"parameter must be of type {t}, got {value!r}")
    return value


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self, names: Optional[list[str]] = None) -> list[dict]:
        return [t.schema() for n, t in self._tools.items() if names is None or n in names]

    def subset(self, names: list[str]) -> "ToolRegistry":
        sub = ToolRegistry()
        for n in names:
            if n in self._tools:
                sub.register(self._tools[n])
        return sub

    def normalize_args(self, tool: Tool, args: dict) -> dict:
        props = tool.parameters.get("properties", {})
        missing = [r for r in tool.parameters.get("required", []) if r not in args]
        if missing:
            raise ToolError(
                f"missing required parameter(s): {', '.join(missing)}. "
                f"Expected parameters: {json.dumps(props, ensure_ascii=False)}"
            )
        return {k: _coerce(v, props.get(k, {})) for k, v in args.items() if k in props}

    def execute(self, name: str, args: dict, ctx: ToolContext) -> tuple[bool, str]:
        """Run a tool. Returns ``(ok, text)``; never raises for tool-level errors."""
        tool = self._tools.get(name)
        if tool is None:
            return False, f"ERROR: unknown tool '{name}'. Available: {', '.join(self._tools)}"
        try:
            clean = self.normalize_args(tool, args)
            out = tool.func(ctx, clean)
            return True, truncate_middle(out, ctx.max_output_chars)
        except ToolError as e:
            return False, f"ERROR: {e}"
        except Exception as e:  # noqa: BLE001 - report anything to the model
            return False, f"ERROR: {type(e).__name__}: {e}"
