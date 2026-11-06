"""Per-turn file snapshots (own copies, no git needed) used to roll files back."""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
from pathlib import Path
from typing import Optional


class SnapshotStore:
    """Before a turn modifies a file, its original content is copied here.

    Layout: ``<dir>/<turn_id>/manifest.json`` + ``<dir>/<turn_id>/blobs/<sha1(relpath)>``.
    Only the first modification of a file inside one turn is recorded.
    """

    def __init__(self, root: Path, directory: Path) -> None:
        self.root = Path(root).resolve()
        self.dir = Path(directory)
        self.current: Optional[str] = None
        self._lock = threading.Lock()

    # --------------------------------------------------------------- record
    def begin(self, turn_id: str) -> None:
        self.current = turn_id

    def _turn_dir(self, turn_id: str) -> Path:
        return self.dir / turn_id

    def _manifest(self, turn_id: str) -> dict:
        p = self._turn_dir(turn_id) / "manifest.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"files": {}}

    def _rel(self, path: Path) -> str:
        return Path(path).resolve().relative_to(self.root).as_posix()

    def record(self, path: Path) -> None:
        with self._lock:
            self._record(path)

    def _record(self, path: Path) -> None:
        if self.current is None:
            return
        rel = self._rel(path)
        man = self._manifest(self.current)
        if rel in man["files"]:
            return
        tdir = self._turn_dir(self.current)
        (tdir / "blobs").mkdir(parents=True, exist_ok=True)
        entry = {"existed": Path(path).is_file(), "blob": ""}
        if entry["existed"]:
            blob = hashlib.sha1(rel.encode()).hexdigest()
            shutil.copy2(path, tdir / "blobs" / blob)
            entry["blob"] = blob
        man["files"][rel] = entry
        (tdir / "manifest.json").write_text(json.dumps(man, indent=1), encoding="utf-8")

    # -------------------------------------------------------------- queries
    def changed_files(self, turn_id: str) -> list[str]:
        return sorted(self._manifest(turn_id)["files"])

    # -------------------------------------------------------------- restore
    def restore(self, turn_id: str) -> list[str]:
        """Put every file changed by ``turn_id`` back to its state before that turn."""
        man = self._manifest(turn_id)
        restored: list[str] = []
        for rel, entry in man["files"].items():
            target = self.root / rel
            if entry["existed"]:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self._turn_dir(turn_id) / "blobs" / entry["blob"], target)
            elif target.exists():
                target.unlink()
            restored.append(rel)
        return restored

    def discard(self, turn_id: str) -> None:
        shutil.rmtree(self._turn_dir(turn_id), ignore_errors=True)
