"""Pluggable context-compression strategies.

Add an algorithm:

    from coder_agent.context.compression import CompressionStrategy, register_strategy

    class MyStrategy(CompressionStrategy):
        name = "mine"
        def compress(self, messages, target_tokens, ctx): ...

    register_strategy(MyStrategy())

then set ``context.algorithm = "mine"`` in the config.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional

from ..util import truncate_middle
from .tokens import TokenCounter

Summarizer = Callable[[list[dict]], str]  # messages -> summary text (may raise)


@dataclass
class CompressionContext:
    counter: TokenCounter
    summarizer: Optional[Summarizer] = None
    keep_recent_units: int = 10
    tools: Optional[list[dict]] = None
    max_tokens: int = 131072  # for sizing the summarizer input

    def count(self, messages: list[dict]) -> int:
        return self.counter.count(messages, self.tools)


class CompressionStrategy(ABC):
    name = "base"

    @abstractmethod
    def compress(self, messages: list[dict], target_tokens: int, ctx: CompressionContext) -> list[dict]:
        """Return a shorter, still valid message list (system messages first, tool results
        always following the assistant message that requested them)."""


_REGISTRY: dict[str, CompressionStrategy] = {}


def register_strategy(strategy: CompressionStrategy) -> None:
    _REGISTRY[strategy.name] = strategy


def get_strategy(name: str) -> CompressionStrategy:
    if name not in _REGISTRY:
        raise KeyError(f"unknown compression algorithm '{name}' (available: {', '.join(sorted(_REGISTRY))})")
    return _REGISTRY[name]


def available_strategies() -> list[str]:
    return sorted(_REGISTRY)


# ------------------------------------------------------------------ helpers
def split_units(messages: list[dict]) -> list[list[dict]]:
    """Group messages into units: a user message, or an assistant message plus the tool
    results answering it. Units are the smallest pieces that may be dropped safely."""
    units: list[list[dict]] = []
    for m in messages:
        if m.get("role") == "tool" and units:
            units[-1].append(m)
        else:
            units.append([m])
    return units


def _flatten(units: list[list[dict]]) -> list[dict]:
    return [m for u in units for m in u]


def trim_tool_outputs(messages: list[dict], max_chars: int) -> list[dict]:
    out = []
    for m in messages:
        c = m.get("content")
        if m.get("role") == "tool" and isinstance(c, str) and len(c) > max_chars:
            m = {**m, "content": truncate_middle(c, max_chars, "\n...[old tool output trimmed: {n} chars]...\n")}
        out.append(m)
    return out


def render_transcript(messages: list[dict], per_message_chars: int = 1500) -> str:
    lines = []
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", {})
            content += f"\n[call {fn.get('name')}({truncate_middle(fn.get('arguments', ''), 400)})]"
        lines.append(f"## {role}\n{truncate_middle(content, per_message_chars)}")
    return "\n\n".join(lines)


SUMMARY_PROMPT = (
    "You are compacting the history of a coding-agent session so work can continue with a smaller "
    "context. Write a dense summary (max ~600 words) in the same language the user used. Keep: "
    "1) the user's goal and constraints; 2) files created/modified and why; 3) important facts "
    "learned (build commands, errors, APIs, paths); 4) decisions made; 5) what is done and what "
    "remains. Do not invent anything. No preamble."
)
SUMMARY_HEADER = "[Summary of earlier conversation - older messages were compacted]\n"


# --------------------------------------------------------- default algorithm
class SummarizeStrategy(CompressionStrategy):
    """1. keep system messages and the newest ``keep_recent_units`` units untouched;
    2. shrink old tool outputs; 3. replace everything older by an LLM summary;
    4. if still too large (or no summarizer / summarizer fails) drop oldest units."""

    name = "summarize"

    def compress(self, messages: list[dict], target_tokens: int, ctx: CompressionContext) -> list[dict]:
        system = [m for m in messages if m.get("role") == "system"]
        rest = [m for m in messages if m.get("role") != "system"]
        units = split_units(rest)
        keep_n = max(1, min(ctx.keep_recent_units, len(units)))
        old_units, recent_units = units[:-keep_n], units[-keep_n:]

        # Step 1: cheap - trim old tool outputs.
        old = trim_tool_outputs(_flatten(old_units), 600)
        candidate = system + old + _flatten(recent_units)
        if ctx.count(candidate) <= target_tokens:
            return candidate

        # Step 2: summarise old part.
        summary_msg: Optional[dict] = None
        if old and ctx.summarizer:
            try:
                budget_chars = int(ctx.max_tokens * 0.6 * 3)
                transcript = render_transcript(old)
                transcript = truncate_middle(transcript, budget_chars)
                text = ctx.summarizer(
                    [
                        {"role": "system", "content": SUMMARY_PROMPT},
                        {"role": "user", "content": transcript},
                    ]
                )
                if text.strip():
                    summary_msg = {"role": "user", "content": SUMMARY_HEADER + text.strip()}
            except Exception:  # noqa: BLE001 - fall back to dropping
                summary_msg = None
        head = system + ([summary_msg] if summary_msg else [])
        candidate = head + _flatten(recent_units)
        if ctx.count(candidate) <= target_tokens:
            return candidate

        # Step 3: shrink recent tool outputs, then drop oldest recent units.
        for limit in (3000, 1200, 400):
            recent = trim_tool_outputs(_flatten(recent_units), limit)
            candidate = head + recent
            if ctx.count(candidate) <= target_tokens:
                return candidate
        units_left = list(recent_units)
        note = {"role": "user", "content": "[Some earlier steps were dropped to fit the context window.]"}
        while len(units_left) > 1:
            units_left.pop(0)
            candidate = head + [note] + trim_tool_outputs(_flatten(units_left), 400)
            if ctx.count(candidate) <= target_tokens:
                break
        return candidate


class TruncateStrategy(CompressionStrategy):
    """No LLM call: trim tool outputs, then drop the oldest units. Fast fallback."""

    name = "truncate"

    def compress(self, messages: list[dict], target_tokens: int, ctx: CompressionContext) -> list[dict]:
        return SummarizeStrategy().compress(messages, target_tokens, CompressionContext(
            ctx.counter, None, ctx.keep_recent_units, ctx.tools, ctx.max_tokens))


register_strategy(SummarizeStrategy())
register_strategy(TruncateStrategy())
