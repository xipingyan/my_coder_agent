"""Context-window bookkeeping and compression trigger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..config import ContextConfig
from .compression import CompressionContext, Summarizer, get_strategy
from .tokens import TokenCounter


@dataclass
class CompressionInfo:
    before: int
    after: int
    algorithm: str


class ContextManager:
    def __init__(self, cfg: ContextConfig, counter: Optional[TokenCounter] = None) -> None:
        self.cfg = cfg
        self.counter = counter or TokenCounter()

    def tokens(self, messages: list[dict], tools: Optional[list[dict]] = None) -> int:
        return self.counter.count(messages, tools)

    def ratio(self, messages: list[dict], tools: Optional[list[dict]] = None) -> float:
        return self.tokens(messages, tools) / max(1, self.cfg.max_tokens)

    def needs_compression(self, messages: list[dict], tools: Optional[list[dict]] = None) -> bool:
        return self.ratio(messages, tools) >= self.cfg.compress_threshold

    def calibrate(self, messages: list[dict], tools: Optional[list[dict]], actual_prompt_tokens: int) -> None:
        self.counter.calibrate(self.counter.count_raw(messages, tools), actual_prompt_tokens)

    def compress(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        summarizer: Optional[Summarizer] = None,
    ) -> tuple[list[dict], CompressionInfo]:
        before = self.tokens(messages, tools)
        strategy = get_strategy(self.cfg.algorithm)
        ctx = CompressionContext(
            counter=self.counter,
            summarizer=summarizer,
            keep_recent_units=self.cfg.keep_recent_units,
            tools=tools,
            max_tokens=self.cfg.max_tokens,
        )
        target = int(self.cfg.max_tokens * self.cfg.compress_target)
        new = strategy.compress(messages, target, ctx)
        return new, CompressionInfo(before, self.tokens(new, tools), strategy.name)
