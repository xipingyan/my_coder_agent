"""Thread bridges between the core (worker thread) and Qt (main thread)."""

from __future__ import annotations

import threading
from typing import Callable, Optional

from PySide6.QtCore import QObject, QThread, Signal

from ..approval import ApprovalRequest, ApprovalResponse
from ..events import EventBus


class EventBridge(QObject):
    """Re-emits every bus event as a Qt signal (queued onto the GUI thread)."""

    event = Signal(object)

    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        bus.subscribe(self.event.emit)


class _Pending:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.response = ApprovalResponse(False, "no answer")


class ApprovalBridge(QObject):
    """``ask`` is called on the worker thread; it blocks until the GUI answered."""

    requested = Signal(object, object)  # ApprovalRequest, _Pending

    def __init__(self) -> None:
        super().__init__()
        self.cancelled: Callable[[], bool] = lambda: False

    def ask(self, req: ApprovalRequest) -> ApprovalResponse:
        pending = _Pending()
        self.requested.emit(req, pending)
        while not pending.done.wait(0.2):
            if self.cancelled():
                return ApprovalResponse(False, "cancelled by user")
        return pending.response

    @staticmethod
    def answer(pending: _Pending, response: ApprovalResponse) -> None:
        pending.response = response
        pending.done.set()


class PlanBridge(QObject):
    """Blocking GUI questions for the orchestrator (plan review, stage confirmation)."""

    requested = Signal(str, object, object)  # kind ("plan" | "stage"), payload, _Pending

    def __init__(self) -> None:
        super().__init__()
        self.cancelled: Callable[[], bool] = lambda: False

    def _ask(self, kind: str, payload, default):
        pending = _Pending()
        pending.response = default
        self.requested.emit(kind, payload, pending)
        while not pending.done.wait(0.2):
            if self.cancelled():
                return default
        return pending.response

    def confirm_plan(self, plan):
        return self._ask("plan", plan, None)

    def confirm_stage(self, report):
        return self._ask("stage", report, "stop")

    @staticmethod
    def answer(pending: _Pending, value) -> None:
        pending.response = value
        pending.done.set()


class TurnWorker(QThread):
    """Runs one blocking engine call off the GUI thread."""

    def __init__(self, fn: Callable[[], object]) -> None:
        super().__init__()
        self._fn = fn
        self.result: object = None
        self.error: Optional[str] = None

    def run(self) -> None:  # noqa: D401 - Qt entry point
        try:
            self.result = self._fn()
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
