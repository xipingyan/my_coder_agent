"""Fallback extraction of tool calls written as plain text.

Quantised local models often emit tool calls inside the content instead of the
structured ``tool_calls`` field. Supported formats:

1. Hermes / Qwen:   ``<tool_call>{"name": ..., "arguments": {...}}</tool_call>``
2. Qwen3-Coder XML: ``<tool_call><function=NAME><parameter=KEY>VALUE</parameter>...</function></tool_call>``
3. A fenced ```json block holding ``{"name": ..., "arguments": {...}}``
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Iterable

from .base import ToolCall

_TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
_FUNC_RE = re.compile(r"<function=([\w\-.]+)>(.*?)</function>", re.DOTALL)
_PARAM_RE = re.compile(r"<parameter=([\w\-.]+)>\n?(.*?)\n?</parameter>", re.DOTALL)
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _new_id() -> str:
    return "call_" + uuid.uuid4().hex[:12]


def _from_json_obj(obj: object, known: set[str]) -> ToolCall | None:
    if not isinstance(obj, dict):
        return None
    fn = obj.get("function") if isinstance(obj.get("function"), dict) else None
    name = obj.get("name") or (fn or {}).get("name")
    if not isinstance(name, str) or not name or (known and name not in known):
        return None
    args = obj.get("arguments", obj.get("parameters", {}))
    if fn and "arguments" in fn:
        args = fn["arguments"]
    if not isinstance(args, str):
        args = json.dumps(args, ensure_ascii=False)
    return ToolCall(_new_id(), name, args)


def extract_text_tool_calls(text: str, known: Iterable[str] = ()) -> tuple[str, list[ToolCall]]:
    """Return ``(text_without_tool_calls, calls)``. ``known`` filters false positives."""
    known_set = set(known)
    calls: list[ToolCall] = []
    cleaned = text

    for m in _TOOL_CALL_RE.finditer(text):
        body = m.group(1).strip()
        call: ToolCall | None = None
        fm = _FUNC_RE.search(body)
        if fm:
            name = fm.group(1)
            if not known_set or name in known_set:
                params = {k: v for k, v in _PARAM_RE.findall(fm.group(2))}
                call = ToolCall(_new_id(), name, json.dumps(params, ensure_ascii=False))
        else:
            try:
                call = _from_json_obj(json.loads(body), known_set)
            except json.JSONDecodeError:
                call = None
        if call:
            calls.append(call)
            cleaned = cleaned.replace(m.group(0), "")

    if not calls:
        for m in _FENCE_RE.finditer(text):
            try:
                call = _from_json_obj(json.loads(m.group(1)), known_set)
            except json.JSONDecodeError:
                continue
            if call:
                calls.append(call)
                cleaned = cleaned.replace(m.group(0), "")

    return cleaned.strip(), calls
