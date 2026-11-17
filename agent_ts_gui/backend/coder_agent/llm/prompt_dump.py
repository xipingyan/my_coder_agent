"""Dump every request sent to the LLM server (Settings > Debug > prompt dump).

One JSON file per request, in ``<project>/.agent/debug/prompts/`` (or
``~/.coder_agent/debug/prompts/`` when no project root is known). The agent id
(main / planner / sub:N) is a thread-local set by the Agent loop, so parallel
sub-agents get labelled correctly. Off by default; when off, ``dump_request``
is a no-op.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

from ..paths import user_dir

_tls = threading.local()
_lock = threading.Lock()


def set_agent_id(agent_id: Optional[str]) -> None:
    _tls.agent_id = agent_id


def agent_id() -> str:
    return getattr(_tls, "agent_id", "") or "unknown"


def dump_dir(root: Optional[Path]) -> Path:
    if root:
        return Path(root) / ".agent" / "debug" / "prompts"
    return user_dir() / "debug" / "prompts"


def dump_request(root: Optional[Path], payload: dict) -> Optional[Path]:
    """Write the request payload to a numbered JSON file."""
    d = dump_dir(root)
    try:
        d.mkdir(parents=True, exist_ok=True)
        aid = agent_id()
        ts = time.strftime("%Y%m%dT%H%M%S")
        with _lock:
            n = len(list(d.glob("*.json"))) + 1
        path = d / f"{n:04d}_{ts}_{aid}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path
    except OSError:
        return None  # debugging aid: never break the agent over a dump failure
