"""WebSocket server: the bridge between the Electron GUI and the agent core.

Protocol (JSON over WebSocket, one GUI connection at a time)
------------------------------------------------------------
GUI -> server:  {"id": <n>, "method": <str>, "params": <obj>}
server -> GUI:  {"id": <n>, "result": <obj>}          (or {"id": <n>, "error": <str>})
server -> GUI:  {"event": <kind>, "agent_id": <str>, "data": <obj>, "ts": <float>}
                (every EventBus event, same kinds as coder_agent/events.py)

Long-running work (submit / submit_plan / edit_and_resubmit) is fire-and-forget:
the RPC returns immediately with the turn id, progress arrives as events.
Approval, plan review and stage confirmation are async questions: the server
pushes an ``approval_requested`` / ``plan_review_requested`` /
``stage_confirm_requested`` event (data carries ``req_id``) and waits for the
matching ``answer`` RPC. This replaces the blocking Qt bridges of the old GUI;
cancelling the turn answers the pending question with its "cancelled" value.
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
from .engine import Engine, _serialised
from .events import EventBus
from .llm import LLMProvider, create_provider, fetch_context_info
from .orchestrator import Plan, StageReport
from .prompts import PromptBuilder, PromptSections
from .session import Session

ProviderFactory = Optional[Callable[[], LLMProvider]]  # injectable for tests


# --------------------------------------------------------------- async asks
class _Pending:
    """A question asked from a worker thread, answered by the GUI via ``answer``."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.done = asyncio.Event()  # created on the loop thread (see Server.question)
        self.response: Any = None
        self.on_timeout: Any = None  # value used when the user cancels


def _jsonable(value: Any) -> Any:
    """Make dataclass / pydantic payloads JSON-safe (Plan, StageReport, AppConfig...)."""
    from pydantic import BaseModel
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="json"))
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
        req_id, p = self.server.question("approval", ApprovalResponse(False, "cancelled by user"))
        self.server.push_event("approval_requested", "main", dict(_jsonable(req), req_id=req_id))
        resp = self.server.wait(p)
        if not isinstance(resp, ApprovalResponse):  # the GUI answers with a JSON dict
            resp = ApprovalResponse(
                bool(resp.get("allow")),
                str(resp.get("feedback", "")),
                str(resp.get("remember_rule", "")),
                str(resp.get("remember_scope", "project")),
            )
        return resp


class PlanAsk:
    """Plan review and stage confirmation, same pattern as ApprovalAsk."""

    def __init__(self, server: "Server") -> None:
        self.server = server
        self._n_plan = 0
        self._n_stage = 0

    def confirm_plan(self, plan: Plan) -> Optional[Plan]:
        req_id, p = self.server.question("plan", None)  # cancellation == plan rejected
        self.server.push_event("plan_review_requested", "planner", dict(_jsonable(plan), req_id=req_id))
        resp = self.server.wait(p)
        if isinstance(resp, dict):  # the GUI answers with the (possibly edited) plan dict
            return Plan.from_dict(resp)
        return resp

    def confirm_stage(self, report: StageReport) -> str:
        req_id, p = self.server.question("stage", "stop")
        self.server.push_event("stage_confirm_requested", "planner", dict(_jsonable(report), req_id=req_id))
        return self.server.wait(p)


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
        self._counter = 0
        self._methods: dict[str, Callable[..., Any]] = self._register()

    # ------------------------------------------------------------- plumbing
    def _register(self) -> dict[str, Callable[..., Any]]:
        return {
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

    def _on_bus_event(self, ev) -> None:
        """EventBus subscriber (fn(ev)) -> push_event."""
        self.push_event(ev.kind, ev.agent_id, ev.data)

    def question(self, prefix: str, on_timeout: Any) -> tuple[str, _Pending]:
        """Create a pending question from a worker thread (Event on the loop thread)."""
        assert self.loop is not None
        self._counter += 1
        req_id = f"{prefix}-{self._counter}"
        p = _Pending(self.loop)
        p.on_timeout = on_timeout
        self.pending[req_id] = p
        return req_id, p

    def wait(self, p: _Pending) -> Any:
        """Block the calling worker thread until the GUI answers or the turn is cancelled."""
        while not p.done.is_set():
            try:
                asyncio.run_coroutine_threadsafe(p.done.wait(), p.loop).result(timeout=0.2)
            except (asyncio.TimeoutError, TimeoutError):
                if self.cancelled():
                    return p.on_timeout
        return p.response

    def _require_engine(self) -> Engine:
        if self.engine is None:
            raise RuntimeError("no project open - call open_project first")
        return self.engine

    def _start_turn(self, fn: Callable[[], None]) -> dict:
        if self._busy:
            raise RuntimeError("a turn is already running")
        self._busy = True

        def work() -> None:
            try:
                fn()
            finally:
                self._busy = False

        threading.Thread(target=work, daemon=True).start()
        return {"turn_id": self._require_engine().session.head}

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
        self.engine.approver = _serialised(self.approvals.ask)
        if self.provider_factory is not None:
            self.engine._provider_override = self.provider_factory()
        self.bus.subscribe(self._on_bus_event)
        self.root = root
        return {
            "root": str(root),
            "session_id": self.engine.session.id,
            "task_types": self.engine.task_types.names(),
            "effort": cfg.thinking.effort,
            "config": _jsonable(cfg),
        }

    def submit(self, p: dict) -> dict:
        e = self._require_engine()
        prompt, task_type = p.get("prompt", ""), p.get("task_type")
        return self._start_turn(lambda: e.submit(prompt, task_type))

    def submit_plan(self, p: dict) -> dict:
        e = self._require_engine()
        goal = p.get("goal", "")
        return self._start_turn(lambda: e.submit_plan(goal, self.plans.confirm_plan, self.plans.confirm_stage))

    def cancel(self, p: dict) -> dict:
        self._require_engine().cancel()
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
        return self._start_turn(lambda: e.submit(p["prompt"], p.get("task_type")))

    def new_session(self, p: dict) -> dict:
        e = self._require_engine()
        if self._busy:
            raise RuntimeError("a turn is running")
        self.open_project({"root": str(e.root)})
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
        tt = e.task_types.get(p.get("task_type") or "general")
        return {"text": PromptBuilder(e.root, e.task_types, PromptSections()).build(tt)}

    def answer(self, p: dict) -> dict:
        req_id = p.get("req_id", "")
        pend = self.pending.get(req_id)
        if pend is None:
            raise RuntimeError(f"no pending request {req_id!r}")
        pend.response = p.get("response")
        pend.loop.call_soon_threadsafe(pend.done.set)
        self.pending.pop(req_id, None)
        return {"ok": True}

    # ------------------------------------------------------------- lifecycle
    async def attach(self, ws) -> None:
        self.loop = asyncio.get_running_loop()
        self.ws = ws

    async def detach(self) -> None:
        self.ws = None
        # answer every pending question with its cancel value so workers unblock
        for pend in list(self.pending.values()):
            pend.response = pend.on_timeout
            pend.done.set()
        self.pending.clear()
        if self.engine is not None:
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
        server = await websockets.serve(lambda ws, *args: handler(ws, server), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        print(f"WS_URL=ws://127.0.0.1:{port}", flush=True)
        await asyncio.Future()  # run forever

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
