"""debug_backtrace: run a program under gdb in batch mode and return a trimmed backtrace.

Small local models are bad at composing gdb command lines (forgetting batch mode, missing -g,
hanging on an interactive prompt). This wrapper does it reliably and explains what it found."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import time

from .base import Tool, ToolContext, ToolError

NOISE = re.compile(
    r"Error disabling address space randomization|Thread debugging using libthread_db|Using host libthread_db|"
    r"debuginfod|auto-downloading|answered N|^\[New Thread|^\[Thread .* exited|^\[Detaching|"
    r"^warning: could not find|^Reading symbols|^Download failed",
    re.IGNORECASE,
)


def command_of(a: dict) -> str:
    """The command the approval policy should judge: running the program itself."""
    prog = a.get("program", "")
    if prog and not prog.startswith(("/", "./", "../")):
        prog = "./" + prog
    return f"{prog} {a.get('args', '')}".strip()


def has_debug_info(path) -> bool | None:
    """True/False from readelf, None when it cannot tell (no readelf / not an ELF file)."""
    readelf = shutil.which("readelf")
    if not readelf:
        return None
    r = subprocess.run([readelf, "-S", str(path)], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        return None
    return ".debug_info" in r.stdout


def _gdb_command(prog, args: list[str], breakpoint: str) -> list[str]:
    cmd = ["gdb", "-batch", "-nx", "-ex", "set pagination off", "-ex", "set confirm off",
           "-ex", "set debuginfod enabled off", "-ex", "set print frame-arguments scalars"]
    if breakpoint:
        cmd += ["-ex", f"break {breakpoint}", "-ex", "run", "-ex", "bt 15", "-ex", "info args", "-ex", "info locals"]
    else:
        cmd += ["-ex", "run", "-ex", "bt 25", "-ex", "info args", "-ex", "info locals",
                "-ex", "thread apply all bt 8"]
    return cmd + ["--args", str(prog), *args]


def _verdict(out: str, interrupted_after: float | None) -> str:
    if interrupted_after is not None:
        return (f"VERDICT: the program did not finish within {interrupted_after:.0f}s and was interrupted. "
                "The backtrace below shows where it was stuck (hang, infinite loop or waiting for input).")
    m = re.search(r"received signal (SIG\w+)", out)
    if m:
        return f"VERDICT: the program was stopped by {m.group(1)}. Look at the first frame that is in your own code."
    m = re.search(r"exited with code (\d+)", out)
    if m:
        return f"VERDICT: the program exited with code {int(m.group(1), 8)} - it did not crash. Use `breakpoint` to inspect state."
    if "exited normally" in out:
        return ("VERDICT: the program exited normally (code 0) - no crash under gdb. If the output is wrong, "
                "set `breakpoint` (file.cpp:LINE or function) to inspect values there.")
    if "Breakpoint 1," in out:
        return "VERDICT: stopped at your breakpoint; arguments and locals are shown below."
    return "VERDICT: see the output below."


def debug_backtrace(ctx: ToolContext, a: dict) -> str:
    if not shutil.which("gdb"):
        raise ToolError("gdb is not installed (sudo apt install gdb). Alternative: rebuild with "
                        "-fsanitize=address,undefined -g -O0 and run the program; the report names the line.")
    prog = ctx.resolve(a["program"])
    if not prog.is_file():
        raise ToolError(f"program not found: {a['program']} (build it first)")
    if not os.access(prog, os.X_OK):
        raise ToolError(f"{a['program']} is not executable")
    if has_debug_info(prog) is False:
        raise ToolError(
            f"{a['program']} has no debug info, a backtrace would be useless. Rebuild with debug symbols and "
            "no optimisation: `cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug && cmake --build build` "
            "(or compile with -g -O0), then call this tool again.")
    try:
        args = shlex.split(a.get("args", ""))
    except ValueError as e:
        raise ToolError(f"cannot parse args: {e}")
    cwd = ctx.resolve(a.get("cwd", "."))
    timeout = max(5, min(int(a.get("timeout", 60)), 600))
    cmd = _gdb_command(prog, args, a.get("breakpoint", "").strip())

    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors="replace", start_new_session=True,
                            env=dict(os.environ, TERM="dumb", LC_ALL="C"))
    start = time.monotonic()
    interrupted: float | None = None
    status = ""
    out = ""
    while True:
        try:
            out, _ = proc.communicate(timeout=0.3)
            break
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            if ctx.cancel.is_set():
                os.killpg(proc.pid, signal.SIGKILL)
                out, _ = proc.communicate()
                status = "cancelled by user"
                break
            if interrupted is None and elapsed > timeout:
                interrupted = elapsed
                os.killpg(proc.pid, signal.SIGINT)  # gdb stops the inferior and runs the queued bt commands
            elif interrupted is not None and elapsed > interrupted + 20:
                os.killpg(proc.pid, signal.SIGKILL)
                out, _ = proc.communicate()
                status = "gdb did not stop after the interrupt and was killed"
                break

    lines = [ln for ln in out.splitlines() if ln.strip() and not NOISE.search(ln)]
    head = f"debugger session: {command_of(a)}" + (f" (breakpoint {a['breakpoint']})" if a.get("breakpoint") else "")
    verdict = _verdict(out, interrupted)
    return "\n".join([head, verdict, *( [status] if status else [] ), "", *lines])


def debug_tools() -> list[Tool]:
    return [
        Tool(
            "debug_backtrace",
            "Run a program under gdb (batch mode, non-interactive) and get the backtrace, arguments and locals "
            "at the crash, at a breakpoint, or where it hangs. Use it when a program crashes/hangs/gives wrong "
            "results and two hypotheses did not explain it. The program must be built with -g -O0 "
            "(cmake -DCMAKE_BUILD_TYPE=Debug).",
            {
                "type": "object",
                "properties": {
                    "program": {"type": "string", "description": "path of the executable, e.g. build/app"},
                    "args": {"type": "string", "description": "command-line arguments for the program"},
                    "breakpoint": {"type": "string",
                                   "description": "optional: file.cpp:LINE or function name to stop at and inspect"},
                    "timeout": {"type": "integer", "description": "seconds before a hang is interrupted (default 60)"},
                    "cwd": {"type": "string", "description": "working directory relative to the project root"},
                },
                "required": ["program"],
            },
            debug_backtrace,
            kind="exec",
            preview=lambda ctx, a: "gdb -batch ... --args " + command_of(a) +
                                   (f"   [break {a['breakpoint']}]" if a.get("breakpoint") else ""),
            command_of=command_of,
        )
    ]
