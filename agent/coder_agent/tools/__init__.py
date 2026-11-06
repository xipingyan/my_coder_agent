"""Default tool set."""

from __future__ import annotations

from .base import SnapshotRecorder, Tool, ToolContext, ToolError, ToolRegistry
from .fs import fs_tools
from .shell import shell_tools


def default_registry() -> ToolRegistry:
    from ..knowledge import memory_tools  # local import: knowledge depends on tools.base

    reg = ToolRegistry()
    for t in [*fs_tools(), *shell_tools(), *memory_tools()]:
        reg.register(t)
    return reg


__all__ = ["SnapshotRecorder", "Tool", "ToolContext", "ToolError", "ToolRegistry", "default_registry"]
