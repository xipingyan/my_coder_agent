"""Incremental splitter for ``<think>...</think>`` embedded in content streams."""

from __future__ import annotations

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"


def _partial_tag_len(text: str, tag: str) -> int:
    """Longest suffix of ``text`` that is a proper prefix of ``tag``."""
    for n in range(min(len(tag) - 1, len(text)), 0, -1):
        if tag.startswith(text[-n:]):
            return n
    return 0


class ThinkParser:
    """Feed content deltas, get ``(kind, text)`` pairs back (kind: thinking|content).

    Tags may be split across chunks; a possible partial tag is held back until
    the next ``feed`` / ``flush``. ``start_in_think`` is for templates that put
    ``<think>`` into the prompt so only ``</think>`` appears in the output.
    """

    def __init__(self, start_in_think: bool = False) -> None:
        self.in_think = start_in_think
        self._buf = ""

    def feed(self, text: str, final: bool = False) -> list[tuple[str, str]]:
        self._buf += text
        out: list[tuple[str, str]] = []

        def emit(kind: str, s: str) -> None:
            if s:
                if out and out[-1][0] == kind:
                    out[-1] = (kind, out[-1][1] + s)
                else:
                    out.append((kind, s))

        while self._buf:
            tag = THINK_CLOSE if self.in_think else THINK_OPEN
            kind = "thinking" if self.in_think else "content"
            idx = self._buf.find(tag)
            if idx != -1:
                emit(kind, self._buf[:idx])
                self._buf = self._buf[idx + len(tag):]
                self.in_think = not self.in_think
                continue
            keep = 0 if final else _partial_tag_len(self._buf, tag)
            emit(kind, self._buf[: len(self._buf) - keep])
            self._buf = self._buf[len(self._buf) - keep:]
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        return self.feed("", final=True)
