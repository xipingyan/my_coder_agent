"""WebSocket server: the bridge between the Electron GUI and the agent core.

Protocol (JSON over WebSocket, one GUI connection at a time)
------------------------------------------------------------
GUI -> server:  {"id": <n>, "method": <str>, "params": <obj>}
server -> GUI:  {"id": <n>, "result": <obj>}          (or {"id": <n>, "error": <str>})
server -> GUI:  {"event": <kind>, "agent_id": <str>, "data": <obj>, "ts": <float>}
                (every EventBus event, same kinds as coder_agent/events.py)

Long-running work (submit / submit_plan) is fire-and-forget: the RPC returns
immediately with the turn id and progress arrives as events. Approval, plan
review and stage confirmation are async questions: the server pushes an
``approval_requested`` / ``plan_review_requested`` / ``stage_confirm_requested``
event and waits for the matching ``answer`` RPC (this replaces the blocking
Qt bridges of the old GUI; cancelling the turn answers with "cancelled").
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import websockets

from .approval import ApprovalRequest, ApprovalResponse
from .config import AppConfig, load_config, save_config
from .engine import Engine
from .events import EventBus
from .llm import LLMError, LLMProvider, ContextInfo, create_provider, fetch_context_info
from .orchestrator import Plan, StageReport
from .prompts import PromptBuilder, PromptSections
from .session import Session

ProviderFactory = Optional[Callable[[], LLMProvider]]  # injectable for tests


# --------------------------------------------------------------- async asks
class _Pending:
    """A question asked from a worker thread, answered by the GUI via RPC."""

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.response: Any = None
        self.cancelled: Callable[[], bool] = lambda: False
        self.on_timeout: Any = None  # value returned when the user cancels


def _jsonable(value: Any) -> Any:
    """Make dataclass-ish payloads JSON-safe (Plan, StageReport, SubTask...)."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


class ApprovalAsk:
    """``ask`` runs on the engine worker thread; it parks until the GUI answers."""

    def __init__(self, server: "Server") -> None:
        self.server = server
        self._next = 0

    def ask(self, req: ApprovalRequest) -> ApprovalResponse:
        self._next += 1
        req_id = f"approval-{self._next}"
        p = _Pending()
        p.cancelled = self.server.cancelled
        p.on_timeout = ApprovalResponse(False, "cancelled by user")
        self.server.pending[req_id] = p
        self.server.push_event("approval_requested", "main", _jsonable(req))
        return self._wait(p)

    def _wait(self, p: _Pending) -> Any:
        loop = self.server.loop
        assert loop is not None
        while not p.event.is_set():
            try:
                loop.run_coroutine_threadsafe(p.event.wait(), loop).result(timeout=0.2)
            except (asyncio.TimeoutError, TimeoutError):
                if p.cancelled():
                    return p.on_timeout
        return p.response


class PlanAsk:
    """Plan review and stage confirmation, same pattern as ApprovalAsk."""

    def __init__(self, server: "Server") -> None:
        self.server = server
        self._n_plan = 0
        self._n_stage = 0

    def confirm_plan(self, plan: Plan) -> Optional[Plan]:
        self._n_plan += 1
        req_id = f"plan-{self._n_plan}"
        p = _Pending()
        p.cancelled = self.server.cancelled
        p.on_timeout = None  # cancellation == plan rejected
        self.server.pending[req_id] = p
        self.server.push_event("plan_review_requested", "planner", _jsonable(plan))
        return self._wait(p)

    def confirm_stage(self, report: StageReport) -> str:
        self._n_stage += 1
        req_id = f"stage-{self._n_stage}"
        p = _Pending()
        p.cancelled = self.server.cancelled
        p.on_timeout = "stop"
        self.server.pending[req_id] = p
        self.server.push_event("stage_confirm_requested", "planner", _jsonable(report))
        return self._wait(p)

    def _wait(self, p: _Pending) -> Any:
        loop = self.server.loop
        assert loop is not None
        while not p.event.is_set():
            try:
                loop.run_coroutine_threadsafe(p.event.wait(), loop).result(timeout=0.2)
            except (asyncio.TimeoutError, TimeoutError):
                if p.cancelled():
                    return p.on_timeout
        return p.response


# ------------------------------------------------------------------- server
class Server:
    def __init__(self, root: Optional[Path], provider_factory: ProviderFactory = None) -> None:
        self.root: Optional[Path] = Path(root).resolve() if root else None
        self.provider_factory = provider_factory
        self.engine: Optional[Engine] = None
        self.bus: Optional[EventBus] = None
        self.approvals: Optional[ApprovalAsk] = None
        self.plans: Optional[PlanAsk] = None
        self.ws: Any = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.pending: dict[str, _Pending] = {}
        self._send_lock = asyncio.Lock()
        self._busy = False
        self._methods: dict[str, Callable[..., Any]] = self._register()

    # ------------------------------------------------------------- plumbing
    def _register(self) -> dict[str, Callable[..., Any]]:
        m = {
            "ping": lambda p: "pong",
            "open_project": self.open_project,
            "submit": self.submit,
            "submit_plan": self.submit_plan,
            "cancel": self.cancel,
            "rewind": self.rewind,
            "edit_and_resubmit": self.edit_and_resubmit,
            "new_session": self.new_session,
            "open_session": self.open_session,
            "delete_session": self.delete_session,
            "save_config": self.save_config,
            "test_connection": self.test_connection,
            "context_info": self.context_info,
            "context_estimate": self.context_estimate,
            "task_types": self.task_types,
            "system_prompt": self.system_prompt,
            "answer": self.answer,
        }
        return m

    @property
    def busy(self) -> bool:
        return self._busy

    def cancelled(self) -> bool:
        return bool(self.engine and self.engine.cancel_event.is_set())

    async def send(self, payload: dict) -> None:
        if self.ws is None:
            return
        async with self._send_lock:
            try:
                await self.ws.send(json.dumps(payload, ensure_ascii=False))
            except websockets.ConnectionClosed:
                pass

    def push_event(self, kind: str, agent_id: str, data: Any) -> None:
        """Thread-safe EventBus subscriber -> queued WebSocket send."""
        if self.ws is None or self.loop is None:
            return
        payload = {"event": kind, "agent_id": agent_id, "data": _jsonable(data), "ts": time.time()}
        asyncio.run_coroutine_threadsafe(self.send(payload), self.loop)

    def _require_engine(self) -> Engine:
        if self.engine is None:
            raise RuntimeError("no project open - call open_project first")
        return self.engine

    # -------------------------------------------------------------- methods
    def open_project(self, p: dict) -> dict:
        root = Path(p.get("root") or (self.root or Path.cwd())).resolve()
        session_id = p.get("session_id")
        cfg = load_config(root)
        session = Session.load(root, session_id) if session_id else None
        self.engine = Engine(cfg, root, EventBus(), approver=None, session=session)
        self.bus = self.engine.bus
        self.approvals = ApprovalAsk(self)
        self.plans = PlanAsk(self)
        self.engine.approver = self.engine.__class__.__mro__[0] and None  # placeholder, replaced below
        # Engine wraps the approver with a serialising lock at construction;
        # rebuild it here so parallel sub-agents are asked one at a time.
        from .engine import _serialised
        self.engine.approver = _serialised(self.approvals.ask)
        self.approvals.server = self
        self.bus.subscribe(self.push_event_sub)
        self.root = root
        return {
            "root": str(root),
            "session_id": self.engine.session.id,
            "task_types": self.engine.task_types.names(),
            "effort": cfg.thinking.effort,
            "config": _jsonable(cfg),
        }

    def push_event_sub(self, ev) -> None:
        self.push_event(ev.kind, ev.agent_id, ev.data)

    def submit(self, p: dict) -> dict:
        e = self._require_engine()
        if self._busy:
            raise RuntimeError("a turn is already running")
        self._busy = True
        prompt, task_type = p.get("prompt", ""), p.get("task_type")

        def work() -> None:
            try:
                e.submit(prompt, task_type)
            finally:
                self._busy = False

        threading.Thread(target=work, daemon=True).start()
        return {"turn_id": e.session.head}

    def submit_plan(self, p: dict) -> dict:
        e = self._require_engine()
        if self._busy:
            raise RuntimeError("a turn is already running")
        self._busy = True
        goal = p.get("goal", "")

        def work() -> None:
            try:
                e.submit_plan(goal, self.plans.confirm_plan, self.plans.confirm_stage)
            finally:
                self._busy = False

        threading.Thread(target=work, daemon=True).start()
        return {"turn_id": e.session.head}

    def cancel(self, p: dict) -> dict:
        e = self._require_engine()
        e.cancel()
        return {"ok": True}

    def rewind(self, p: dict) -> dict:
        e = self._require_engine()
        if self._busy:
            raise RuntimeError("cannot rewind while a turn is running")
        restored, side = e.rewind(p["turn_id"], bool(p.get("keep_branch")))
        return {"restored": restored, "side_effects": side}

    def edit_and_resubmit(self, p: dict) -> dict:
        e = self._require_engine()
        if self._busy:
            raise RuntimeError("a turn is already running")
        e.rewind(p["turn_id"], bool(p.get("keep_branch")))
        self._busy = True

        def work() -> None:
            try:
                e.submit(p["prompt"], p.get("task_type"))
            finally:
                self._busy = False

        threading.Thread(target=work, daemon=True).start()
        return {"turn_id": e.session.head}

    def new_session(self, p: dict) -> dict:
        e = self._require_engine()
        if self._busy:
            raise RuntimeError("a turn is running")
        root = e.root
        self.open_project({"root": str(root)})
        return {"session_id": e.session.id}

    def open_session(self, p: dict) -> dict:
        e = self._require_engine()
        if self._busy:
            raise RuntimeError("a turn is running")
        self.open_project({"root": str(e.root), "session_id": p["session_id"]})
        return {"session_id": e.session.id}

    def delete_session(self, p: dict) -> dict:
        e = self._require_engine()
        if self._busy:
            raise RuntimeError("a turn is running")
        Session.delete(e.root, p["session_id"])
        return {"ok": True}

    def save_config(self, p: dict) -> dict:
        e = self._require_engine()
        if self._busy:
            raise RuntimeError("a turn is running")
        data = p.get("config") or {}
        # the project root is managed by the server, not by the GUI form
        data.setdefault("paths", {})["project_root"] = str(e.root)
        cfg = AppConfig.model_validate(data)
        persisted = cfg.model_copy(deep=True)
        persisted.paths.project_root = ""
        save_config(persisted)
        e.apply_config(cfg)
        e.reload_policy()
        return {"ok": True, "task_types": e.task_types.names()}

    def test_connection(self, p: dict) -> dict:
        cfg = AppConfig.model_validate(p.get("config") or {})
        models = create_provider(cfg).list_models()
        info = fetch_context_info(cfg.server)
        return {"models": models, "context": _jsonable(info), "context_text": info.describe()}

    def context_info(self, p: dict) -> dict:
        e = self._require_engine()
        info = fetch_context_info(e.config.server)
        return {"context": _jsonable(info), "context_text": info.describe(),
                "configured_max": e.config.context.max_tokens}

    def context_estimate(self, p: dict) -> dict:
        e = self._require_engine()
        msgs = e.session.build_messages()
        return {"tokens": e.ctx_mgr.tokens(msgs), "max_tokens": e.config.context.max_tokens}

    def task_types(self, p: dict) -> dict:
        e = self._require_engine()
        return {"task_types": e.task_types.names()}

    def system_prompt(self, p: dict) -> dict:
        e = self._require_engine()
        name = p.get("task_type") or "general"
        tt = e.task_types.get(name)
        return {"text": PromptBuilder(e.root, e.task_types, PromptSections()).build(tt)}

    def answer(self, p: dict) -> dict:
        req_id = p.get("req_id", "")
        pend = self.pending.get(req_id)
        if pend is None:
            raise RuntimeError(f"no pending request {req_id!r}")
        pend.response = p.get("response")
        if pend.event.get_loop() is not self.loop:
            pend.event = asyncio.Event()  # pragma: no cover - defensive
        pend.event.set()
        self.pending.pop(req_id, None)
        return {"ok": True}

    # ------------------------------------------------------------- lifecycle
    async def attach(self, ws) -> None:
        self.ws = ws
        self.loop = asyncio.get_running_loop()

    async def detach(self) -> None:
        self.ws = None
        # answer every pending question with its cancel value so workers unblock
        for req_id, pend in list(self.pending.items()):
            pend.response = pend.on_timeout
            pend.event.set()
            self.pending.pop(req_id, None)
        if self.engine and self.bus:
            self.engine.cancel()


# --------------------------------------------------------------------- main
async def handler(ws, server: Server) -> None:
    await server.attach(ws)
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                await server.send({"error": "invalid json"})
                continue
            mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
            fn = server._methods.get(method)
            if fn is None:
                await server.send({"id": mid, "error": f"unknown method: {method}"})
                continue
            try:
                result = await asyncio.to_thread(fn, params)
                await server.send({"id": mid, "result": _jsonable(result)})
            except Exception as e:  # noqa: BLE001 - report any failure to the GUI
                await server.send({"id": mid, "error": f"{type(e).__name__}: {e}"})
    finally:
        await server.detach()


def run(argv: list[str], provider_factory: ProviderFactory = None) -> int:
    ap = argparse.ArgumentParser(description="Coder Agent backend server")
    ap.add_argument("--root", help="project root (optional)")
    args = ap.parse_args(argv)

    async def _run() -> None:
        server = Server(Path(args.root) if args.root else None, provider_factory)
        # the Engine created in open_project must use the injected provider
        if provider_factory is not None:
            orig_open = server.open_project

            def open_with_provider(p: dict) -> dict:
                r = orig_open(p)
                server.engine._provider_override = provider_factory()
                return r

            server._methods["open_project"] = open_with_provider
        async with websockets.serve(lambda ws: handler(ws, server), "127.0.0.1", 0):
            port = server.loop.sockets[0].getsockname()[1]
            print(f"WS_URL=ws://127.0.0.1:{port}", flush=True)
            await asyncio.Future()  # run forever

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
