"""Approval policy: decide whether a command / file write runs automatically."""

from __future__ import annotations

import fnmatch
import os
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


def _read_word(cmd: str, j: int) -> tuple[str, int]:
    """Read one (possibly quoted) shell word starting at ``j``; returns ``(word, next_index)``."""
    out: list[str] = []
    quote = ""
    while j < len(cmd):
        c = cmd[j]
        if quote:
            if c == quote:
                quote = ""
            else:
                out.append(c)
        elif c in "'\"":
            quote = c
        elif c.isspace() or c in ";|&<>()":
            break
        else:
            out.append(c)
        j += 1
    return "".join(out), j


def split_command(cmd: str, redirects: Optional[list[str]] = None) -> tuple[list[str], str]:
    """Split a shell line into simple commands. Returns ``(segments, unsafe_reason)``.

    With ``redirects`` (a list), output redirections to a literal file name are not 'unsafe':
    their targets are appended to the list so the caller can check where they point."""
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
            if redirects is not None and (c == ">" or c == "&"):
                j = i + (2 if c == "&" else 1)
                if j < n and cmd[j] == ">":
                    j += 1  # >> (or &>>)
                if j < n and cmd[j] == "|":
                    j += 1  # >|
                while j < n and cmd[j] in " \t":
                    j += 1
                target, j = _read_word(cmd, j)
                if not target or re.search(r"[$`&]", target):
                    unsafe = unsafe or "output redirection"
                else:
                    redirects.append(target)
                if cur and cur[-1].isdigit() and (len(cur) == 1 or cur[-2] == " "):
                    cur.pop()  # the fd number of "2>file"
                i = j
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


# Used when the policy files do not define them (e.g. an approval.toml written by an older version).
DEFAULT_TRUSTED = ["/tmp"]
# Commands that never run automatically just because their paths are trusted (network, installs,
# processes, history rewriting). An explicit [commands].allow rule still wins.
DEFAULT_CONFIRM = [
    "curl", "wget", "ssh", "scp", "sftp", "rsync", "nc", "ncat", "telnet", "ftp",
    "pip", "pip3", "python -m pip", "python3 -m pip", "npm install", "npm i", "npm publish", "yarn", "pnpm",
    "apt", "apt-get", "dpkg", "dnf", "yum", "pacman", "brew", "snap", "docker", "podman",
    "kill", "pkill", "killall", "systemctl", "service", "mount", "umount", "chown", "chroot", "crontab",
    "eval", "exec", "source",
    "git push", "git reset", "git clean", "git checkout", "git restore", "git rebase", "git stash drop",
    "git branch -D", "git remote", "git clone", "git fetch", "git pull",
]
_DESTRUCTIVE = {"rm", "rmdir", "mv", "unlink", "shred", "truncate"}
_SAFE_DEVICES = {"/dev/null", "/dev/stdout", "/dev/stderr"}
_DYNAMIC = re.compile(r"\$[\w{(]")
_URL = re.compile(r"\w+://|\w+@[\w.-]+:")
_ABS_IN_TOKEN = re.compile(r"(?:^|[\s'\"=:(,])(~?/[^\s'\"`;|&<>(),:]*)")


class _Unresolvable(Exception):
    """A path argument whose real location cannot be known statically (variable, URL)."""


def _path_candidates(tok: str) -> list[str]:
    """Parts of one command-line token that name a file or directory."""
    if tok.startswith("-"):
        if "=" not in tok:
            return []
        tok = tok.split("=", 1)[1]  # --out=/some/path
    if not tok:
        return []
    if _DYNAMIC.search(tok):
        raise _Unresolvable(f"'{tok}' uses a variable")
    if _URL.search(tok):
        raise _Unresolvable(f"'{tok}' is a network address")
    out: list[str] = []
    if not any(ch.isspace() for ch in tok) and (tok.startswith(("/", "~", ".")) or "/" in tok):
        out.append(tok)
    out += _ABS_IN_TOKEN.findall(tok)  # absolute paths hidden inside e.g. `python -c "open('/etc/x')"`
    return out


def _resolve_path(cand: str, base: Path) -> Path:
    p = Path(cand).expanduser()
    if not p.is_absolute():
        p = base / p
    return p.resolve()


def _git_subcommand(toks: list[str]) -> list[str]:
    """``git -C dir push`` -> ``git push`` so confirm rules see the real subcommand."""
    i = 1
    while i < len(toks) and toks[i].startswith("-"):
        i += 2 if toks[i] in ("-C", "-c", "--git-dir", "--work-tree") else 1
    return [toks[0], *toks[i:]]


class ApprovalPolicy:
    """``root`` enables path-based auto approval: file writes and commands whose paths all lie inside the
    project root or a trusted directory (``[paths].trusted``, default ``/tmp``) run without asking."""

    def __init__(
        self,
        data: Optional[dict] = None,
        user_file: Optional[Path] = None,
        project_file: Optional[Path] = None,
        root: Optional[Path] = None,
    ):
        self.data = data or {}
        self.user_file = user_file
        self.project_file = project_file
        self.root = Path(root).resolve() if root else None
        # Resolved trusted dirs outside the root. Mutated in place: ToolContext shares this list so the
        # file tools may touch these paths (after the policy said yes).
        self.extra_roots: list[Path] = []
        self._refresh_roots()

    @classmethod
    def load(cls, root: Optional[Path] = None) -> "ApprovalPolicy":
        user_file = ensure_user_policy()
        project_file = project_cache_dir(root) / "approval.toml" if root else None
        data = _merge(_read(user_file), _read(project_file))
        return cls(data, user_file, project_file, root)

    def reload(self) -> None:
        self.data = _merge(_read(self.user_file), _read(self.project_file))
        self._refresh_roots()

    # -------------------------------------------------------------- paths
    def _paths_cfg(self) -> dict:
        return self.data.get("paths", {})

    def _refresh_roots(self) -> None:
        out: list[Path] = []
        for p in self._paths_cfg().get("trusted", DEFAULT_TRUSTED):
            try:
                r = Path(p).expanduser().resolve()
            except (RuntimeError, OSError):
                continue
            if r.parent != r and r != self.root and r not in out:  # never trust the whole filesystem
                out.append(r)
        self.extra_roots[:] = out

    def _scoped(self) -> bool:
        """Is path-based auto approval active?"""
        return self.root is not None and bool(self._paths_cfg().get("auto_approve", True))

    def trusted_roots(self) -> list[Path]:
        return ([self.root] if self.root else []) + self.extra_roots

    def _within(self, p: Path) -> bool:
        return any(p == r or r in p.parents for r in self.trusted_roots())

    def _protected(self, p: Path) -> bool:
        """The policy files themselves: editing them would let the agent grant itself rights."""
        return any(f and p == Path(f).resolve() for f in (self.user_file, self.project_file))

    def _path_reason(self, cand: str, cwd: Optional[Path], destructive: bool = False) -> str:
        """Why ``cand`` may not be touched automatically (empty string: it may)."""
        if cwd is None:
            return "working directory is unknown"
        if cand in _SAFE_DEVICES:
            return ""
        try:
            p = _resolve_path(cand, cwd)
        except (RuntimeError, OSError):
            return f"cannot resolve '{cand}'"
        if not self._within(p):
            return f"'{cand}' is outside the trusted paths"
        if self._protected(p):
            return "touches the approval policy file"
        if destructive and p in self.trusted_roots():
            return f"would remove the trusted directory '{cand}'"
        return ""

    def _scoped_reason(self, toks: list[str], cwd: Optional[Path]) -> str:
        """Why a simple command may not run automatically although it is not allow-listed."""
        if cwd is None or not self._within(cwd):
            return "working directory is outside the trusted paths"
        check = _git_subcommand(toks) if toks[0] == "git" else toks
        for rule in self._cmd().get("confirm", DEFAULT_CONFIRM):
            if _prefix_match(check, rule):
                return f"'{rule}' always needs confirmation"
        destructive = os.path.basename(toks[0]) in _DESTRUCTIVE
        for tok in toks:
            if destructive and tok in ("*", ".*", "./*"):
                return "bulk delete/move with a bare wildcard"
            try:
                cands = _path_candidates(tok)
            except _Unresolvable as e:
                return str(e)
            for c in cands:
                why = self._path_reason(c, cwd, destructive)
                if why:
                    return why
        return ""

    def _cd_target(self, toks: list[str], cwd: Optional[Path]) -> Optional[Path]:
        args = [t for t in toks[1:] if not t.startswith("-")]
        if cwd is None or (args and (args[0] == "-" or _DYNAMIC.search(args[0]))):
            return None
        try:
            return _resolve_path(args[0] if args else "~", cwd)
        except (RuntimeError, OSError):
            return None

    # ------------------------------------------------------------- checks
    def _cmd(self) -> dict:
        return self.data.get("commands", {})

    def _default(self, section: str) -> Decision:
        d = self.data.get(section, {}).get("default", "ask")
        return Decision("deny" if d == "deny" else "ask", f"no {section} rule matched")

    def check_command(self, command: str, cwd: Optional[Path] = None) -> Decision:
        cfg = self._cmd()
        scoped = self._scoped()
        redirects: list[str] = []
        segs, unsafe = split_command(command, redirects if scoped else None)
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

        cur: Optional[Path] = (Path(cwd).resolve() if cwd else self.root) if scoped else None
        cwds = [cur]  # every directory the line runs in (it may `cd`)
        by_scope = False
        for s, toks in parsed:
            if not toks:
                continue
            before = cur
            if scoped and toks[0] == "cd":
                cur = self._cd_target(toks, cur)
                cwds.append(cur)
            ok = any(_prefix_match(toks, r) for r in cfg.get("allow", [])) or any(
                re.fullmatch(r, s) for r in cfg.get("allow_regex", [])
            )
            if ok:
                continue
            if not scoped:
                return Decision(self._default("commands").action, f"'{s.split()[0] if s.split() else s}' is not in the allow list")
            why = self._scoped_reason(toks, before)
            if why:
                return Decision(self._default("commands").action, f"'{s.split()[0]}': {why}")
            by_scope = True
        for target in redirects:
            for c in cwds:
                why = self._path_reason(target, c)
                if why:
                    return Decision(self._default("commands").action, f"redirect: {why}")
        if redirects:
            by_scope = True
        return Decision("allow", "inside trusted paths" if by_scope else "all parts match allow rules")

    def check_write(self, rel_path: str) -> Decision:
        cfg = self.data.get("files", {})
        p = rel_path.replace("\\", "/")
        abs_p: Optional[Path] = None
        names = [p]  # what the globs are matched against: as given + relative to its trusted root
        if self.root is not None:
            try:
                abs_p = _resolve_path(p, self.root)
            except (RuntimeError, OSError):
                abs_p = None
            if abs_p is not None:
                for r in self.trusted_roots():
                    if abs_p == r or r in abs_p.parents:
                        names.append(abs_p.relative_to(r).as_posix())
                        break
        for g in cfg.get("deny", []):
            if any(fnmatch.fnmatch(n, g) for n in names):
                return Decision("deny", f"matches deny pattern '{g}'")
        if self.data.get("auto_approve_all"):
            return Decision("allow", "auto_approve_all")
        if abs_p is not None and self._protected(abs_p):
            return Decision("ask", "this is the approval policy file")
        for g in cfg.get("allow", []):
            if any(fnmatch.fnmatch(n, g) for n in names):
                return Decision("allow", f"matches allow pattern '{g}'")
        if self._scoped() and abs_p is not None and self._within(abs_p):
            return Decision("allow", "inside trusted paths")
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
