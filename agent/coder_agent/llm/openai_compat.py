"""OpenAI-compatible provider (works for Ollama, vLLM, OVMS ... via /v1)."""

from __future__ import annotations

import copy
import threading
from typing import Iterator, Optional

import openai
from openai import DefaultHttpxClient, OpenAI

from ..config import SamplingConfig, ServerConfig, ThinkingConfig
from .base import LLMCancelled, LLMChunk, LLMError, LLMProvider, ToolCall, Usage
from .think_parser import ThinkParser


class OpenAICompatProvider(LLMProvider):
    def __init__(self, server: ServerConfig, thinking: ThinkingConfig, sampling: SamplingConfig) -> None:
        self.server = server
        self.thinking = thinking
        self.sampling = sampling
        http_client = DefaultHttpxClient(
            timeout=openai.Timeout(server.timeout, connect=server.connect_timeout),
            trust_env=not server.bypass_proxy,
        )
        self.client = OpenAI(
            base_url=server.base_url,
            api_key=server.api_key or "EMPTY",
            http_client=http_client,
            max_retries=1,
        )

    # ----------------------------------------------------------- request
    def build_request(
        self,
        messages: list[dict],
        tools: Optional[list[dict]],
        thinking: Optional[bool],
        max_tokens: Optional[int],
    ) -> dict:
        kw: dict = {
            "model": self.server.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kw["tools"] = tools
        s, t = self.sampling, self.thinking
        if s.temperature is not None:
            kw["temperature"] = s.temperature
        if s.top_p is not None:
            kw["top_p"] = s.top_p
        mt = max_tokens if max_tokens is not None else s.max_tokens
        if mt:
            kw["max_tokens"] = mt

        extra = copy.deepcopy(t.extra_body)
        if thinking is False:
            extra.setdefault("chat_template_kwargs", {})["enable_thinking"] = False
        else:
            if t.send_effort and t.effort != "default":
                kw["reasoning_effort"] = t.effort
            if t.enable_thinking is not None:
                extra.setdefault("chat_template_kwargs", {})["enable_thinking"] = t.enable_thinking
            if t.token_budget:
                extra["thinking_token_budget"] = t.token_budget
        if extra:
            kw["extra_body"] = extra
        return kw

    # ------------------------------------------------------------ stream
    def stream_chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        cancel: Optional[threading.Event] = None,
        thinking: Optional[bool] = None,
        max_tokens: Optional[int] = None,
    ) -> Iterator[LLMChunk]:
        kw = self.build_request(messages, tools, thinking, max_tokens)
        try:
            stream = self.client.chat.completions.create(**kw)
        except openai.OpenAIError as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e

        parser = ThinkParser()
        acc: dict[int, dict] = {}
        finish: Optional[str] = None
        try:
            for chunk in stream:
                if cancel is not None and cancel.is_set():
                    raise LLMCancelled()
                if getattr(chunk, "usage", None):
                    u = chunk.usage
                    yield LLMChunk("usage", usage=Usage(u.prompt_tokens or 0, u.completion_tokens or 0, u.total_tokens or 0))
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if choice.finish_reason:
                    finish = choice.finish_reason

                reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if reasoning:
                    yield LLMChunk("thinking", reasoning)
                if delta.content:
                    for kind, text in parser.feed(delta.content):
                        yield LLMChunk(kind, text)
                for tc in delta.tool_calls or []:
                    slot = acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            slot["name"] += tc.function.name
                        if tc.function.arguments:
                            slot["arguments"] += tc.function.arguments
            for kind, text in parser.flush():
                yield LLMChunk(kind, text)
        except openai.OpenAIError as e:
            if cancel is not None and cancel.is_set():
                raise LLMCancelled() from e
            raise LLMError(f"{type(e).__name__}: {e}") from e
        finally:
            close = getattr(stream, "close", None)
            if close:
                try:
                    close()
                except Exception:
                    pass

        if acc:
            calls = [
                ToolCall(v["id"] or f"call_{i}", v["name"], v["arguments"] or "{}")
                for i, v in sorted(acc.items())
            ]
            yield LLMChunk("tool_calls", tool_calls=calls)
        yield LLMChunk("done", finish_reason=finish)

    def list_models(self) -> list[str]:
        try:
            return [m.id for m in self.client.models.list().data]
        except openai.OpenAIError as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e
