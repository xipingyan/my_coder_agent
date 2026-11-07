"""Token estimation (no tokenizer dependency) with calibration from server usage."""

from __future__ import annotations

import json
import re

_CJK = re.compile(r"[\u2e80-\u9fff\uac00-\ud7af\uff00-\uffef]")


def estimate_text(text: str) -> int:
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    return cjk + int((len(text) - cjk) / 3.0) + 1  # code is token-dense: ~3 chars/token


def estimate_message(msg: dict) -> int:
    n = 4
    content = msg.get("content")
    if isinstance(content, str):
        n += estimate_text(content)
    elif isinstance(content, list):
        n += sum(estimate_text(p.get("text", "")) for p in content if isinstance(p, dict))
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        n += estimate_text(fn.get("name", "")) + estimate_text(fn.get("arguments", "")) + 4
    return n


class TokenCounter:
    """Estimator scaled by a factor learned from the server's real ``prompt_tokens``."""

    def __init__(self) -> None:
        self.factor = 1.0
        self._calibrated = False

    def count_raw(self, messages: list[dict], tools: list[dict] | None = None) -> int:
        raw = sum(estimate_message(m) for m in messages)
        if tools:
            raw += estimate_text(json.dumps(tools, ensure_ascii=False))
        return raw

    def count(self, messages: list[dict], tools: list[dict] | None = None) -> int:
        return int(self.count_raw(messages, tools) * self.factor)

    def calibrate(self, raw_estimate: int, actual: int) -> None:
        """Learn from one request: ``raw_estimate`` is the uncalibrated count, ``actual`` the server's."""
        if raw_estimate <= 0 or actual <= 0:
            return
        f = max(0.5, min(3.0, actual / raw_estimate))
        self.factor = f if not self._calibrated else 0.5 * self.factor + 0.5 * f
        self._calibrated = True
