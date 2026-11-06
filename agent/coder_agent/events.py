"""Event bus: decouples the core from any front-end (GUI / CLI / tests).

Event kinds emitted by the core
-------------------------------
status              state=waiting_model|running_tool|waiting_user|idle, detail=str
thinking_delta      text
content_delta       text
llm_done            usage={prompt,completion,total}, elapsed, ttft
tool_call           name, args (dict), id
tool_result         name, ok (bool), text (truncated)
command_output      text
approval_requested  request (ApprovalRequest)
approval_resolved   allow (bool), auto (bool), reason
context_usage       tokens, max_tokens, ratio
context_compressed  before, after, algorithm
turn_started        turn_id
turn_finished       turn_id, status
agent_finished      status, steps
warning / error     text
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass
class Event:
    kind: str
    agent_id: str = "main"
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


Subscriber = Callable[[Event], None]


class EventBus:
    """Thread-safe synchronous fan-out. Subscribers run on the emitting thread."""

    def __init__(self) -> None:
        self._subs: list[Subscriber] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subs.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._subs:
                    self._subs.remove(fn)

        return unsubscribe

    def emit(self, kind: str, agent_id: str = "main", **data: Any) -> None:
        ev = Event(kind, agent_id, data)
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(ev)
            except Exception:  # a broken listener must never break the agent
                log.exception("event subscriber failed for %s", kind)
