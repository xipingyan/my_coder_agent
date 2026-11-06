"""Approval policy: decide whether a command / file write runs automatically."""

from __future__ import annotations

import fnmatch
import re
import shlex
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Optional

from .paths import project_cache_dir, user_dir
from .util import dump_toml


@dataclass
class Decision:
    action: str  # allow | ask | deny
    reason: str = ""


@dataclass
class ApprovalRequest:
    kind: str  # command | write
    tool: str
    title: str
    detail: str  # command line, or unified diff
    args: dict = field(default_factory=dict)
    suggested_rule: str = ""


@dataclass
class ApprovalResponse:
    allow: bool
    feedback: str = ""  # sent back to the model when rejected
    remember_rule: str = ""  # add to policy when non-empty
    remember_scope: str = "project"  # project | user


# --------------------------------------------------------------- shell split
_SAFE_REDIRECT = re.compile(r"^(?:&>\s*/dev/null|\d*>{1,2}\s*/dev/null|\d*>{1,2}&\d+)")


def split_command(cmd: str) -> tuple[list[str], str]:
    """Split a shell line into simple commands. Returns ``(segments, unsafe_reason)``."""
    segs: list[str] = []
    cur: list[str] = []
    unsafe = ""
    quote = ""
    i, n = 0, len(cmd)
    while i < n:
        c = cmd[i]
        if quote:
            if c == quote:
                quote = ""
            elif quote == '"' and c == "\\" and i + 1 < n:
                cur.append(c)
                i += 1
                c = cmd[i]
            elif quote == '"' and (cmd.startswith("$(", i) or c == "`"):
                unsafe = unsafe or "command substitution"
            cur.append(c)
            i += 1
            continue
        if c in "'\"":
            quote = c
            cur.append(c)
        elif c == "\\" and i + 1 < n:
            cur.append(c)
            cur.append(cmd[i + 1])
            i += 2
            continue
        elif cmd.startswith("$(", i) or c == "`" or cmd.startswith("<(", i) or cmd.startswith(">(", i):
            unsafe = unsafe or "command substitution"
            cur.append(c)
        elif cmd.startswith("<<", i):
            unsafe = unsafe or "heredoc"
            cur.append(c)
        elif c in "<>" or (c == "&" and cmd.startswith("&>", i)):
            m = _SAFE_REDIRECT.match(cmd[i:])
            if m:
                if cur and cur[-1].isdigit() and (len(cur) == 1 or cur[-2] == " "):
                    cur.pop()  # the fd number of "2>&1"
                i += m.end()
                continue
            # "2>file": the leading digit already sits in cur; either way it is a redirect
            if c == ">" or c == "&":
                unsafe = unsafe or "output redirection"
            cur.append(c)
        elif c in ";\n" or (c in "|&"):
            if c in "|&" and i + 1 < n and cmd[i + 1] == c:
                i += 1  # && or ||
            segs.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    segs.append("".join(cur))
    if quote:
        unsafe = unsafe or "unbalanced quote"
    return [s.strip() for s in segs if s.strip()], unsafe


def _tokens(seg: str) -> Optional[list[str]]:
    try:
        toks = shlex.split(seg)
    except ValueError:
        return None
    while toks and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", toks[0]):
        toks.pop(0)  # leading VAR=value
    return toks


def _prefix_match(toks: list[str], rule: str) -> bool:
    rt = shlex.split(rule)
    return bool(rt) and toks[: len(rt)] == rt


# ------------------------------------------------------------------- policy
def _read(path: Optional[Path]) -> dict:
    if path and path.is_file():
        with open(path, "rb") as f:
            return tomllib.load(f)
    return {}


def _merge(a: dict, b: dict) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in a.items()}
    for k, v in b.items():
        if isinstance(v, dict):
            cur = out.setdefault(k, {})
            for kk, vv in v.items():
                if isinstance(vv, list) and isinstance(cur.get(kk), list):
                    cur[kk] = cur[kk] + [x for x in vv if x not in cur[kk]]
                else:
                    cur[kk] = vv
        else:
            out[k] = v
    return out


def ensure_user_policy() -> Path:
    """Create ``~/.coder_agent/approval.toml`` from the packaged template if missing."""
    p = user_dir() / "approval.toml"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            resources.files("coder_agent").joinpath("defaults/approval.toml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return p


class ApprovalPolicy:
    def __init__(self, data: Optional[dict] = None, user_file: Optional[Path] = None, project_file: Optional[Path] = None):
        self.data = data or {}
        self.user_file = user_file
        self.project_file = project_file

    @classmethod
    def load(cls, root: Optional[Path] = None) -> "ApprovalPolicy":
        user_file = ensure_user_policy()
        project_file = project_cache_dir(root) / "approval.toml" if root else None
        data = _merge(_read(user_file), _read(project_file))
        return cls(data, user_file, project_file)

    def reload(self) -> None:
        self.data = _merge(_read(self.user_file), _read(self.project_file))

    # ------------------------------------------------------------- checks
    def _cmd(self) -> dict:
        return self.data.get("commands", {})

    def _default(self, section: str) -> Decision:
        d = self.data.get(section, {}).get("default", "ask")
        return Decision("deny" if d == "deny" else "ask", f"no {section} rule matched")

    def check_command(self, command: str) -> Decision:
        cfg = self._cmd()
        segs, unsafe = split_command(command)
        if not segs:
            return Decision("deny", "empty command")
        parsed = [(s, _tokens(s)) for s in segs]

        for s, toks in parsed:
            if toks is None:
                continue
            for rule in cfg.get("deny", []):
                if toks and _prefix_match(toks, rule):
                    return Decision("deny", f"matches deny rule '{rule}'")
        if unsafe:
            return Decision("ask", f"contains {unsafe}")
        if self.data.get("auto_approve_all"):
            return Decision("allow", "auto_approve_all")

        for s, toks in parsed:
            if not toks:
                continue
            ok = any(_prefix_match(toks, r) for r in cfg.get("allow", [])) or any(
                re.fullmatch(r, s) for r in cfg.get("allow_regex", [])
            )
            if not ok:
                return Decision(self._default("commands").action, f"'{s.split()[0] if s.split() else s}' is not in the allow list")
        return Decision("allow", "all parts match allow rules")

    def check_write(self, rel_path: str) -> Decision:
        cfg = self.data.get("files", {})
        p = rel_path.replace("\\", "/")
        for g in cfg.get("deny", []):
            if fnmatch.fnmatch(p, g):
                return Decision("deny", f"matches deny pattern '{g}'")
        if self.data.get("auto_approve_all"):
            return Decision("allow", "auto_approve_all")
        for g in cfg.get("allow", []):
            if fnmatch.fnmatch(p, g):
                return Decision("allow", f"matches allow pattern '{g}'")
        return self._default("files")

    # --------------------------------------------------------- persistence
    @staticmethod
    def suggest_rule(kind: str, args: dict) -> str:
        if kind == "command":
            segs, _ = split_command(args.get("command", ""))
            toks = (_tokens(segs[-1]) if segs else None) or []
            if len(toks) >= 2 and not toks[1].startswith("-"):
                return f"{toks[0]} {toks[1]}"
            return toks[0] if toks else ""
        path = args.get("path", "")
        return (path.rsplit("/", 1)[0] + "/*") if "/" in path else path

    def remember(self, kind: str, rule: str, scope: str = "project") -> Optional[Path]:
        rule = rule.strip()
        file = self.project_file if scope == "project" and self.project_file else self.user_file
        if not rule or file is None:
            return None
        raw = _read(file)
        section = raw.setdefault("commands" if kind == "command" else "files", {})
        lst = section.setdefault("allow", [])
        if rule not in lst:
            lst.append(rule)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(dump_toml(raw), encoding="utf-8")
        self.reload()
        return file
