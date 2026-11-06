"""The agent loop: model call -> tool calls (with approval) -> repeat."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .approval import ApprovalPolicy, ApprovalRequest, ApprovalResponse
from .config import AgentConfig, ThinkingConfig
from .context import ContextManager
from .events import EventBus
from .llm import LLMCancelled, LLMError, LLMProvider, ToolCall, extract_text_tool_calls
from .tools.base import ToolContext, ToolRegistry

Approver = Callable[[ApprovalRequest], ApprovalResponse]


@dataclass
class AgentResult:
    status: str  # done | cancelled | error | max_steps | loop
    final_text: str = ""
    steps: int = 0
    error: str = ""


class _ThinkLimit(Exception):
    pass


class Agent:
    def __init__(
        self,
        agent_id: str,
        provider: LLMProvider,
        registry: ToolRegistry,
        tool_ctx: ToolContext,
        policy: ApprovalPolicy,
        approver: Optional[Approver],
        ctx_mgr: ContextManager,
        bus: EventBus,
        cfg: AgentConfig,
        thinking_cfg: ThinkingConfig,
        cancel: threading.Event,
    ) -> None:
        self.id = agent_id
        self.provider = provider
        self.registry = registry
        self.tool_ctx = tool_ctx
        self.policy = policy
        self.approver = approver
        self.ctx_mgr = ctx_mgr
        self.bus = bus
        self.cfg = cfg
        self.thinking_cfg = thinking_cfg
        self.cancel = cancel
        self.ran_commands = False
        self._tools = registry.schemas()

    # -------------------------------------------------------------- events
    def _emit(self, kind: str, **data) -> None:
        self.bus.emit(kind, self.id, **data)

    # ----------------------------------------------------------- compaction
    def _summarize(self, msgs: list[dict]) -> str:
        self._emit("status", state="waiting_model", detail="summarising old context")
        return self.provider.complete(msgs, self.cancel, thinking=False, max_tokens=1500)

    def _maybe_compress(self, messages: list[dict], on_compact) -> None:
        tokens = self.ctx_mgr.tokens(messages, self._tools)
        self._emit("context_usage", tokens=tokens, max_tokens=self.ctx_mgr.cfg.max_tokens,
                   ratio=tokens / max(1, self.ctx_mgr.cfg.max_tokens))
        if not self.ctx_mgr.needs_compression(messages, self._tools):
            return
        self._emit("status", state="waiting_model", detail="compressing context")
        new, info = self.ctx_mgr.compress(messages, self._tools, self._summarize)
        messages[:] = new
        if on_compact:
            on_compact(list(new))
        self._emit("context_compressed", before=info.before, after=info.after, algorithm=info.algorithm)
        self._emit("context_usage", tokens=info.after, max_tokens=self.ctx_mgr.cfg.max_tokens,
                   ratio=info.after / max(1, self.ctx_mgr.cfg.max_tokens))

    # ------------------------------------------------------------ model call
    def _call_llm(self, messages: list[dict], thinking: Optional[bool]):
        content: list[str] = []
        think_chars = 0
        calls: list[ToolCall] = []
        finish = None
        usage = None
        start = time.time()
        ttft = None
        limit = self.thinking_cfg.max_think_chars if thinking is not False else 0
        for ch in self.provider.stream_chat(messages, self._tools, self.cancel, thinking):
            if ttft is None and ch.kind in ("thinking", "content", "tool_calls"):
                ttft = time.time() - start
            if ch.kind == "thinking":
                think_chars += len(ch.text)
                self._emit("thinking_delta", text=ch.text)
                if limit and think_chars > limit and not content:
                    raise _ThinkLimit()
            elif ch.kind == "content":
                content.append(ch.text)
                self._emit("content_delta", text=ch.text)
            elif ch.kind == "tool_calls":
                calls = ch.tool_calls or []
            elif ch.kind == "usage":
                usage = ch.usage
            elif ch.kind == "done":
                finish = ch.finish_reason
        return "".join(content), calls, finish, usage, time.time() - start, ttft

    # ------------------------------------------------------------- tool exec
    def _run_tool(self, tc: ToolCall) -> str:
        try:
            args = json.loads(tc.arguments) if tc.arguments.strip() else {}
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            self._emit("tool_call", name=tc.name, args={"raw": tc.arguments}, id=tc.id)
            self._emit("tool_result", name=tc.name, ok=False, text=f"bad arguments: {e}", id=tc.id)
            return f"ERROR: arguments are not valid JSON ({e}). Send a JSON object matching the tool schema."

        self._emit("tool_call", name=tc.name, args=args, id=tc.id)
        tool = self.registry.get(tc.name)
        if tool is None:
            text = f"ERROR: unknown tool '{tc.name}'. Available: {', '.join(self.registry.names())}"
            self._emit("tool_result", name=tc.name, ok=False, text=text, id=tc.id)
            return text

        denied = self._authorise(tool, args)
        if denied is not None:
            self._emit("tool_result", name=tc.name, ok=False, text=denied, id=tc.id)
            return denied

        if tool.kind == "exec":
            self.ran_commands = True
        self._emit("status", state="running_tool", detail=tc.name)
        ok, text = self.registry.execute(tc.name, args, self.tool_ctx)
        self._emit("tool_result", name=tc.name, ok=ok, text=text, id=tc.id)
        return text

    def _authorise(self, tool, args: dict) -> Optional[str]:
        """Return an error text if the call must not run, else None."""
        if tool.kind == "read":
            return None
        ctx = self.tool_ctx
        if tool.kind == "exec":
            kind, subject = "command", args.get("command", "")
            decision = self.policy.check_command(subject)
        else:
            kind = "write"
            try:
                subject = ctx.rel(ctx.resolve(args.get("path", "")))
            except Exception as e:  # noqa: BLE001
                return f"ERROR: {e}"
            decision = self.policy.check_write(subject)

        if decision.action == "deny":
            self._emit("approval_resolved", allow=False, auto=True, reason=decision.reason)
            return f"DENIED by policy ({decision.reason}). Do not retry this; choose a different approach."
        if decision.action == "allow":
            self._emit("approval_resolved", allow=True, auto=True, reason=decision.reason)
            return None

        try:
            detail = tool.preview(ctx, args) if tool.preview else json.dumps(args, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001 - e.g. patch does not apply: tell the model directly
            return f"ERROR: {e}"
        req = ApprovalRequest(
            kind=kind,
            tool=tool.name,
            title=subject if kind == "write" else f"Run command ({decision.reason})",
            detail=detail,
            args=args,
            suggested_rule=self.policy.suggest_rule(kind, {**args, "path": subject} if kind == "write" else args),
        )
        self._emit("status", state="waiting_user", detail=req.title)
        self._emit("approval_requested", request=req)
        resp = self.approver(req) if self.approver else ApprovalResponse(False, "no approver available")
        self._emit("approval_resolved", allow=resp.allow, auto=False, reason=resp.feedback)
        if resp.allow and resp.remember_rule:
            self.policy.remember(kind, resp.remember_rule, resp.remember_scope)
        if not resp.allow:
            fb = f" User feedback: {resp.feedback}" if resp.feedback else ""
            return f"REJECTED by the user.{fb}"
        return None

    # ------------------------------------------------------------------ run
    def run(self, messages: list[dict], on_append=None, on_compact=None) -> AgentResult:
        """Run until the model stops calling tools. ``messages`` (incl. system) is mutated in place."""

        def add(msg: dict) -> None:
            messages.append(msg)
            if on_append:
                on_append(msg)

        seen: dict[str, int] = {}
        strikes = 0
        cutoffs = 0
        known = set(self.registry.names())
        final_text = ""
        try:
            for step in range(1, self.cfg.max_steps + 1):
                if self.cancel.is_set():
                    raise LLMCancelled()
                self._maybe_compress(messages, on_compact)
                self._emit("status", state="waiting_model", detail=f"step {step}")

                est_raw = self.ctx_mgr.counter.count_raw(messages, self._tools)
                try:
                    try:
                        text, calls, finish, usage, elapsed, ttft = self._call_llm(messages, None)
                    except _ThinkLimit:
                        self._emit("warning", text="thinking too long - retrying with thinking disabled")
                        text, calls, finish, usage, elapsed, ttft = self._call_llm(messages, False)
                except LLMError as e:
                    self._emit("error", text=str(e))
                    return AgentResult("error", final_text, step, str(e))

                if usage:
                    self.ctx_mgr.counter.calibrate(est_raw, usage.prompt_tokens)
                self._emit(
                    "llm_done",
                    usage={"prompt": usage.prompt_tokens, "completion": usage.completion_tokens} if usage else {},
                    elapsed=elapsed,
                    ttft=ttft,
                    finish=finish,
                )

                if not calls and text:
                    text, calls = extract_text_tool_calls(text, known)

                msg: dict = {"role": "assistant", "content": text}
                if calls:
                    msg["tool_calls"] = [
                        {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                        for c in calls
                    ]
                add(msg)

                if not calls:
                    if finish == "length" and cutoffs < 2:
                        cutoffs += 1
                        self._emit("warning", text="response was cut off (length); asking the model to continue")
                        add({"role": "user", "content": "Your previous reply was cut off. Continue, be brief, and call a tool or give the final answer."})
                        continue
                    final_text = text
                    self._emit("agent_finished", status="done", steps=step)
                    return AgentResult("done", text, step)

                for tc in calls:
                    if self.cancel.is_set():
                        add({"role": "tool", "tool_call_id": tc.id, "content": "Cancelled by the user before running."})
                        continue
                    key = tc.name + "|" + tc.arguments
                    seen[key] = seen.get(key, 0) + 1
                    if seen[key] > self.cfg.loop_limit:
                        strikes += 1
                        result = (
                            "ERROR: you already made this exact call several times with the same result. "
                            "Stop repeating it: try a different approach or finish with what you have."
                        )
                        self._emit("warning", text=f"repeated call to {tc.name}")
                    else:
                        result = self._run_tool(tc)
                    add({"role": "tool", "tool_call_id": tc.id, "content": result})
                if strikes >= 3:
                    self._emit("agent_finished", status="loop", steps=step)
                    return AgentResult("loop", final_text, step, "agent kept repeating the same calls")

            self._emit("agent_finished", status="max_steps", steps=self.cfg.max_steps)
            return AgentResult("max_steps", final_text, self.cfg.max_steps)
        except LLMCancelled:
            self._emit("agent_finished", status="cancelled", steps=0)
            return AgentResult("cancelled", final_text, 0)
