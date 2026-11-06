from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Iterator, Optional

import pytest

from coder_agent.llm import LLMChunk, LLMProvider, ToolCall, Usage


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODER_AGENT_HOME", str(tmp_path / "home"))


@pytest.fixture
def project(tmp_path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    return p


class FakeProvider(LLMProvider):
    """Plays back scripted replies. Each reply: dict(content=str, tool_calls=[(name, args)], thinking=str)."""

    def __init__(self, script: list[dict], report_usage: bool = True):
        self.report_usage = report_usage
        self.script = list(script)
        self.requests: list[list[dict]] = []
        self.thinking_flags: list[Optional[bool]] = []

    def stream_chat(self, messages, tools=None, cancel=None, thinking=None, max_tokens=None) -> Iterator[LLMChunk]:
        self.requests.append([dict(m) for m in messages])
        self.thinking_flags.append(thinking)
        reply = self.script.pop(0) if self.script else {"content": "done"}
        if reply.get("thinking"):
            yield LLMChunk("thinking", reply["thinking"])
        if reply.get("content"):
            yield LLMChunk("content", reply["content"])
        calls = [
            ToolCall(f"c{len(self.requests)}_{i}", n, json.dumps(a))
            for i, (n, a) in enumerate(reply.get("tool_calls", []))
        ]
        if calls:
            yield LLMChunk("tool_calls", tool_calls=calls)
        if self.report_usage:
            yield LLMChunk("usage", usage=Usage(100, 10, 110))
        yield LLMChunk("done", finish_reason=reply.get("finish", "tool_calls" if calls else "stop"))

    def list_models(self):
        return ["fake"]
