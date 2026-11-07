"""Doc analysis: read the module docs relevant to a subtask and condense them for the sub-agent."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Optional

from ..events import EventBus
from ..knowledge import IndexEntry, module_index
from ..llm import LLMProvider
from ..paths import project_cache_dir
from .models import SubTask

MAX_DOC_CHARS = 6000
MAX_DOCS = 3

ANALYZE_PROMPT = (
    "You read module documentation for a coding agent. Extract ONLY what is relevant to the given "
    "subtask: public interfaces (names/signatures), constraints, conventions, dependencies, and how to "
    "build/test. At most 150 words, bullet points, same language as the documents. No preamble."
)


class DocAnalyzer:
    def __init__(self, provider: LLMProvider, root: Path, bus: EventBus,
                 cancel: Optional[threading.Event] = None) -> None:
        self.provider = provider
        self.root = Path(root)
        self.bus = bus
        self.cancel = cancel
        self.cache_file = project_cache_dir(self.root) / "doc_cache.json"
        self._lock = threading.Lock()

    # ------------------------------------------------------------- selection
    def relevant_docs(self, st: SubTask) -> list[IndexEntry]:
        text = (st.title + " " + st.goal).lower()
        files = [f.removeprefix("./").strip("/").lower() for f in st.files]
        scored: list[tuple[int, IndexEntry]] = []
        for m in module_index(self.root):
            d = m.name.lower() if m.name != "." else ""
            score = 0
            if d and any(f == d or f.startswith(d + "/") or d.startswith(f + "/") for f in files if f):
                score += 10
            if d and Path(d).name in text:
                score += 4
            if not d:
                score += 1  # root doc: weak fallback
            if score:
                scored.append((score, m))
        scored.sort(key=lambda x: -x[0])
        return [m for _, m in scored[:MAX_DOCS]]

    # ----------------------------------------------------------------- cache
    def _cache_load(self) -> dict:
        try:
            return json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _cache_store(self, key: str, value: str) -> None:
        with self._lock:
            data = self._cache_load()
            data[key] = value
            if len(data) > 200:
                data = dict(list(data.items())[-200:])
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    # --------------------------------------------------------------- analysis
    def analyze(self, st: SubTask, agent_id: str) -> str:
        docs = self.relevant_docs(st)
        if not docs:
            return ""
        parts, names = [], []
        for d in docs:
            body = (self.root / d.path).read_text(encoding="utf-8", errors="replace")[:MAX_DOC_CHARS]
            parts.append(f"### {d.path}\n{body}")
            names.append(d.path)
        material = "\n\n".join(parts)
        key = hashlib.sha1((st.goal + "\0" + material).encode()).hexdigest()
        cached = self._cache_load().get(key)
        if cached:
            summary, source = cached, "cache"
        else:
            self.bus.emit("status", agent_id, state="waiting_model", detail=f"reading docs for {st.id}")
            try:
                summary = self.provider.complete(
                    [
                        {"role": "system", "content": ANALYZE_PROMPT},
                        {"role": "user", "content": f"Subtask: {st.title}\n{st.goal}\n\nDocuments:\n{material}"},
                    ],
                    self.cancel, thinking=False, max_tokens=500,
                )
                source = "model"
            except Exception:  # noqa: BLE001 - fall back to raw heads of the docs
                summary, source = "\n\n".join(p[:1200] for p in parts), "raw"
            if summary and source == "model":
                self._cache_store(key, summary)
        self.bus.emit("doc_analysis", agent_id, subtask=st.id, docs=names, summary=summary, source=source)
        return summary
