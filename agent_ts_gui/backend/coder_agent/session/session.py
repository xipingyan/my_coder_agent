"""Session = tree of turns. Rewinding / editing an earlier prompt drops (or branches off) later turns."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from ..paths import project_cache_dir
from .snapshots import SnapshotStore


def _id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class SessionInfo:
    """Cheap summary of a saved session, for history lists."""

    id: str
    title: str
    updated: float
    turns: int
    status: str  # status of the last turn on the active path


@dataclass
class Turn:
    id: str
    parent_id: Optional[str]
    prompt: str
    task_type: str = "general"
    messages: list[dict] = field(default_factory=list)  # model view since the last compaction
    base_messages: Optional[list[dict]] = None  # full compacted history, set when compaction ran in this turn
    archive: list[dict] = field(default_factory=list)  # this turn's messages removed by compaction (display only)
    files_changed: list[str] = field(default_factory=list)
    ran_commands: bool = False  # shell side-effects cannot be rolled back
    status: str = "running"  # running | done | cancelled | error | max_steps | loop
    final_text: str = ""
    subtasks: list[dict] = field(default_factory=list)  # plan turns: id/title/status/summary
    created: float = field(default_factory=time.time)

    def display_messages(self) -> list[dict]:
        return self.archive + self.messages


class Session:
    def __init__(self, root: Path, session_id: Optional[str] = None) -> None:
        self.root = Path(root).resolve()
        self.id = session_id or time.strftime("%Y%m%d-%H%M%S") + "-" + _id()
        self.turns: dict[str, Turn] = {}
        self.head: Optional[str] = None
        base = project_cache_dir(self.root)
        self.file = base / "sessions" / f"{self.id}.json"
        self.snapshots = SnapshotStore(self.root, base / "snapshots" / self.id)

    # ------------------------------------------------------------- structure
    def path(self) -> list[Turn]:
        out: list[Turn] = []
        cur = self.turns.get(self.head) if self.head else None
        while cur:
            out.append(cur)
            cur = self.turns.get(cur.parent_id) if cur.parent_id else None
        return list(reversed(out))

    def children(self, turn_id: Optional[str]) -> list[Turn]:
        return [t for t in self.turns.values() if t.parent_id == turn_id]

    def _descendants(self, turn_id: str) -> list[str]:
        out, stack = [], [turn_id]
        while stack:
            cur = stack.pop()
            out.append(cur)
            stack.extend(t.id for t in self.children(cur))
        return out

    # --------------------------------------------------------------- turns
    def new_turn(self, prompt: str, task_type: str = "general") -> Turn:
        t = Turn(_id(), self.head, prompt, task_type, [{"role": "user", "content": prompt}])
        self.turns[t.id] = t
        self.head = t.id
        self.snapshots.begin(t.id)
        return t

    def build_messages(self) -> list[dict]:
        """Messages (without system prompt) for the next model call along the active path."""
        path = self.path()
        start = 0
        for i, t in enumerate(path):
            if t.base_messages is not None:
                start = i
        out: list[dict] = []
        for t in path[start:]:
            if t.base_messages is not None:
                out = list(t.base_messages)
            out.extend(t.messages)
        return out

    def finish_turn(self, turn: Turn, status: str, final_text: str = "") -> None:
        turn.status = status
        turn.final_text = final_text
        turn.files_changed = self.snapshots.changed_files(turn.id)
        self.save()

    # ---------------------------------------------------------------- rewind
    def rewind(self, turn_id: str, keep_branch: bool = False) -> list[str]:
        """Undo ``turn_id`` and every later turn on the active path: files are restored to the
        state before ``turn_id`` and the head moves to its parent. Discarded turns are deleted
        unless ``keep_branch`` (then they stay in the tree as an inactive branch)."""
        if turn_id not in self.turns:
            raise KeyError(turn_id)
        path = self.path()
        ids = [t.id for t in path]
        if turn_id not in ids:
            raise ValueError("turn is not on the active path")
        restored: list[str] = []
        for t in reversed(path[ids.index(turn_id):]):  # newest first
            for rel in self.snapshots.restore(t.id):
                if rel not in restored:
                    restored.append(rel)
        self.head = self.turns[turn_id].parent_id
        if not keep_branch:
            for tid in self._descendants(turn_id):
                self.turns.pop(tid, None)
                self.snapshots.discard(tid)
        self.save()
        return restored

    def side_effects_after(self, turn_id: str) -> bool:
        """True if commands ran in/after ``turn_id`` (their effects can't be reverted)."""
        path = self.path()
        ids = [t.id for t in path]
        return any(t.ran_commands for t in path[ids.index(turn_id):]) if turn_id in ids else False

    # ------------------------------------------------------------ persistence
    def info(self) -> SessionInfo:
        path = self.path()
        ordered = sorted(self.turns.values(), key=lambda t: t.created)
        first = path[0] if path else (ordered[0] if ordered else None)
        last = path[-1] if path else (ordered[-1] if ordered else None)
        lines = first.prompt.strip().splitlines() if first else []
        return SessionInfo(self.id, (lines[0] if lines else "")[:100], time.time(), len(path), last.status if last else "")

    def save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        data = {"id": self.id, "head": self.head, "turns": [asdict(t) for t in self.turns.values()]}
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.file)
        self.file.with_suffix(".meta").write_text(json.dumps(asdict(self.info()), ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, root: Path, session_id: str) -> "Session":
        s = cls(root, session_id)
        data = json.loads(s.file.read_text(encoding="utf-8"))
        s.head = data.get("head")
        for td in data.get("turns", []):
            t = Turn(**td)
            if t.status == "running":  # the app died / was killed in the middle of this turn
                t.status = "interrupted"
            s.turns[t.id] = t
        return s

    @classmethod
    def list_sessions(cls, root: Path) -> list[str]:
        d = project_cache_dir(Path(root).resolve()) / "sessions"
        return sorted((p.stem for p in d.glob("*.json")), reverse=True) if d.is_dir() else []

    @classmethod
    def summaries(cls, root: Path) -> list[SessionInfo]:
        """Saved sessions (newest first) that contain at least one turn. Uses the small ``.meta``
        files; sessions saved by older versions are loaded once and get a meta file."""
        root = Path(root).resolve()
        out: list[SessionInfo] = []
        for sid in cls.list_sessions(root):
            meta = project_cache_dir(root) / "sessions" / f"{sid}.meta"
            info: Optional[SessionInfo] = None
            try:
                info = SessionInfo(**json.loads(meta.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                try:
                    s = cls.load(root, sid)
                    info = s.info()
                    info.updated = s.file.stat().st_mtime
                    meta.write_text(json.dumps(asdict(info), ensure_ascii=False), encoding="utf-8")
                except (OSError, ValueError, TypeError, KeyError):
                    continue  # unreadable file: skip it
            if info.turns > 0:
                out.append(info)
        return sorted(out, key=lambda i: i.updated, reverse=True)

    @classmethod
    def delete(cls, root: Path, session_id: str) -> None:
        root = Path(root).resolve()
        d = project_cache_dir(root)
        for ext in (".json", ".meta", ".tmp"):
            (d / "sessions" / f"{session_id}{ext}").unlink(missing_ok=True)
        shutil.rmtree(d / "snapshots" / session_id, ignore_errors=True)
