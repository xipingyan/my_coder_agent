"""Provider-independent LLM types and interface."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator, Optional


class LLMError(Exception):
    """Transport / server error."""


class LLMCancelled(Exception):
    """Raised when the caller's cancel event fires."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text, as produced by the model


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMChunk:
    """kind: thinking | content | tool_calls | usage | done"""

    kind: str
    text: str = ""
    tool_calls: Optional[list[ToolCall]] = None
    usage: Optional[Usage] = None
    finish_reason: Optional[str] = None


class LLMProvider(ABC):
    @abstractmethod
    def stream_chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        cancel: Optional[threading.Event] = None,
        thinking: Optional[bool] = None,
        max_tokens: Optional[int] = None,
    ) -> Iterator[LLMChunk]:
        """Stream a chat completion.

        ``thinking=False`` forces thinking off for this call (used for
        summaries and for retrying after an over-long thinking phase).
        """

    def complete(
        self,
        messages: list[dict],
        cancel: Optional[threading.Event] = None,
        thinking: Optional[bool] = False,
        max_tokens: Optional[int] = None,
    ) -> str:
        parts: list[str] = []
        for ch in self.stream_chat(messages, None, cancel, thinking, max_tokens):
            if ch.kind == "content":
                parts.append(ch.text)
        return "".join(parts).strip()

    @abstractmethod
    def list_models(self) -> list[str]: ...
