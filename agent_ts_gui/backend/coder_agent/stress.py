"""Stress / benchmark for parallel sub-agents against a real server.

    coder-agent-stress --base-url http://host:11434/v1 --model qwen3.8-27b:latest \
        --subtasks 8 --parallelism 1,2,4

For every parallelism level a fresh temporary project is created and the same workload
(N independent "write one small header" subtasks) is executed. The report shows wall time,
speed-up, aggregate throughput, observed concurrency, per-call latency and failures, and
verifies that every file really was written with the right content.

Server side requirements for a *real* speed-up:
* Ollama: set ``OLLAMA_NUM_PARALLEL`` (and note that ``num_ctx`` is split between the slots).
* vLLM / OVMS: continuous batching handles it without extra settings.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .approval import ApprovalPolicy
from .config import AppConfig, load_config
from .engine import Engine
from .events import Event, EventBus
from .llm import LLMProvider
from .orchestrator import Plan, PlanResult, SubTask


# ----------------------------------------------------------------- workload
def build_workload(n: int, shared_every: int = 0) -> Plan:
    """N independent subtasks, each creating ``stress/mod_<i>.hpp``.

    ``shared_every=k`` makes every k-th subtask also declare ``stress/shared.hpp`` as a file hint, so
    those subtasks must be serialised by the scheduler."""
    subs = []
    for i in range(1, n + 1):
        files = [f"stress/mod_{i}.hpp"]
        if shared_every and i % shared_every == 0:
            files.append("stress/shared.hpp")
        subs.append(SubTask(
            id=f"T{i}",
            title=f"header mod_{i}",
            goal=(
                f"Create the file stress/mod_{i}.hpp containing a header-guarded C++ header that defines "
                f"`inline int value_{i}() {{ return {i}; }}`. Use write_file. Then reply with a one-line summary."
            ),
            task_type="cpp_impl",
            files=files,
        ))
    return Plan(f"stress workload ({n} independent subtasks)", subs)


# ------------------------------------------------------------------- running
@dataclass
class Run:
    engine: Engine
    root: Path
    result: Optional[PlanResult]
    events: list[Event]
    wall: float
    parallelism: int
    n: int
    cancelled_by_timeout: bool = False
    extra: dict = field(default_factory=dict)


def execute(
    base_cfg: AppConfig,
    n: int,
    parallelism: int,
    provider: Optional[LLMProvider] = None,
    plan: Optional[Plan] = None,
    root: Optional[Path] = None,
    max_steps: int = 12,
    timeout: float = 0,
    on_event=None,
) -> Run:
    cfg = base_cfg.model_copy(deep=True)
    cfg.agent.parallelism = parallelism
    cfg.orchestrator.doc_analysis = False
    cfg.orchestrator.confirm_each_stage = False
    cfg.orchestrator.subtask_max_steps = max_steps
    root = Path(root) if root else Path(tempfile.mkdtemp(prefix="coder_stress_"))

    bus = EventBus()
    events: list[Event] = []
    bus.subscribe(events.append)
    if on_event:
        bus.subscribe(on_event)
    engine = Engine(cfg, root, bus, approver=None, provider=provider)
    engine.policy = ApprovalPolicy({"auto_approve_all": True})

    timer = None
    flag = {"timeout": False}
    if timeout:
        def fire() -> None:
            flag["timeout"] = True
            engine.cancel()
        timer = threading.Timer(timeout, fire)
        timer.daemon = True
        timer.start()
    t0 = time.perf_counter()
    try:
        result = engine.submit_plan("stress", plan=plan or build_workload(n))
    finally:
        if timer:
            timer.cancel()
    return Run(engine, root, result, events, time.perf_counter() - t0, parallelism, n, flag["timeout"])


def max_concurrency(events: list[Event]) -> int:
    marks = sorted(
        (e.ts, 1 if e.kind == "subtask_started" else -1)
        for e in events if e.kind in ("subtask_started", "subtask_finished")
    )
    cur = best = 0
    for _, d in marks:
        cur += d
        best = max(best, cur)
    return best


def verify_files(root: Path, n: int) -> int:
    ok = 0
    for i in range(1, n + 1):
        p = root / "stress" / f"mod_{i}.hpp"
        if p.is_file():
            text = p.read_text(errors="replace")
            if f"value_{i}" in text and f"return {i}" in text:
                ok += 1
    return ok


def summarize(run: Run) -> dict:
    ev = run.events
    start: dict[str, float] = {}
    dur: list[float] = []
    status: dict[str, str] = {}
    for e in ev:
        if e.kind == "subtask_started":
            start[e.data["id"]] = e.ts
        elif e.kind == "subtask_finished":
            sid = e.data["id"]
            status[sid] = e.data["status"]
            if sid in start:
                dur.append(e.ts - start[sid])
    calls = [e for e in ev if e.kind == "llm_done" and e.agent_id.startswith("sub:")]
    ttfts = [e.data["ttft"] for e in calls if e.data.get("ttft") is not None]
    completion = sum((e.data.get("usage") or {}).get("completion", 0) for e in calls)
    prompt = sum((e.data.get("usage") or {}).get("prompt", 0) for e in calls)
    return {
        "parallelism": run.parallelism,
        "subtasks": run.n,
        "wall_s": round(run.wall, 2),
        "done": sum(1 for s in status.values() if s == "done"),
        "failed": sum(1 for s in status.values() if s not in ("done",)),
        "verified": verify_files(run.root, run.n),
        "plan_status": run.result.status if run.result else "none",
        "timed_out": run.cancelled_by_timeout,
        "max_concurrency": max_concurrency(ev),
        "avg_subtask_s": round(sum(dur) / len(dur), 2) if dur else 0,
        "max_subtask_s": round(max(dur), 2) if dur else 0,
        "llm_calls": len(calls),
        "avg_ttft_s": round(sum(ttfts) / len(ttfts), 2) if ttfts else 0,
        "max_ttft_s": round(max(ttfts), 2) if ttfts else 0,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "agg_tok_s": round(completion / run.wall, 1) if run.wall else 0,
        "errors": sum(1 for e in ev if e.kind == "error"),
        "warnings": sum(1 for e in ev if e.kind == "warning"),
    }


def format_table(rows: list[dict]) -> str:
    base = rows[0]["wall_s"] if rows else 0
    cols = [
        ("par", "parallelism"), ("wall s", "wall_s"), ("speedup", None), ("done", "done"),
        ("failed", "failed"), ("verified", "verified"), ("max conc", "max_concurrency"),
        ("avg sub s", "avg_subtask_s"), ("avg ttft s", "avg_ttft_s"), ("max ttft s", "max_ttft_s"),
        ("tok/s agg", "agg_tok_s"), ("errors", "errors"), ("warn", "warnings"),
    ]
    table = [[c[0] for c in cols]]
    for r in rows:
        line = []
        for _, key in cols:
            if key is None:
                line.append(f"{base / r['wall_s']:.2f}x" if r["wall_s"] else "-")
            else:
                line.append(str(r[key]))
        table.append(line)
    widths = [max(len(row[i]) for row in table) for i in range(len(cols))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(row, widths)) for row in table)


def check_expectations(rows: list[dict]) -> list[str]:
    """Human-readable problems found in a stress run (empty list = healthy)."""
    problems = []
    for r in rows:
        p, n = r["parallelism"], r["subtasks"]
        if r["done"] != n or r["verified"] != n:
            problems.append(f"parallelism {p}: only {r['done']}/{n} subtasks done and {r['verified']}/{n} files verified")
        if r["max_concurrency"] > p:
            problems.append(f"parallelism {p}: observed concurrency {r['max_concurrency']} exceeds the limit")
        if r["timed_out"]:
            problems.append(f"parallelism {p}: hit the timeout")
    if len(rows) > 1 and rows[-1]["parallelism"] > 1 and rows[-1]["wall_s"] >= rows[0]["wall_s"] * 0.95:
        problems.append("no speed-up from parallelism: the server probably serialises requests "
                        "(Ollama: set OLLAMA_NUM_PARALLEL; keep agent.parallelism = 1 otherwise)")
    return problems


# ----------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stress test parallel sub-agents against a real server")
    ap.add_argument("--base-url")
    ap.add_argument("--model")
    ap.add_argument("--effort", choices=["default", "none", "low", "medium", "high"])
    ap.add_argument("--subtasks", type=int, default=8, help="independent subtasks per run (default 8)")
    ap.add_argument("--parallelism", default="1,2,4", help="comma separated levels (default 1,2,4)")
    ap.add_argument("--max-steps", type=int, default=12, help="agent steps allowed per subtask")
    ap.add_argument("--ctx", type=int, help="context.max_tokens to use (per slot: num_ctx / OLLAMA_NUM_PARALLEL)")
    ap.add_argument("--timeout", type=float, default=900, help="per run, seconds (0 = none)")
    ap.add_argument("--report", help="write the full results as JSON to this file")
    ap.add_argument("--keep", action="store_true", help="keep the temporary projects")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every subtask start/finish")
    args = ap.parse_args(argv)

    cfg = load_config()
    if args.base_url:
        cfg.server.base_url = args.base_url
    if args.model:
        cfg.server.model = args.model
    if args.effort:
        cfg.thinking.effort = args.effort
    if args.ctx:
        cfg.context.max_tokens = args.ctx
    levels = [int(x) for x in args.parallelism.split(",") if x.strip()]
    print(f"server: {cfg.server.base_url}  model: {cfg.server.model}  effort: {cfg.thinking.effort}")
    print(f"workload: {args.subtasks} independent subtasks, levels {levels}\n")

    t_start = time.time()

    def progress(ev: Event) -> None:
        if args.verbose and ev.kind in ("subtask_started", "subtask_finished"):
            tag = ev.data.get("status", "start")
            print(f"  [{ev.ts - t_start:7.1f}s] {ev.data['id']} {tag}", flush=True)

    rows, roots = [], []
    for p in levels:
        print(f"--- parallelism {p} ---", flush=True)
        t_start = time.time()
        run = execute(cfg, args.subtasks, p, max_steps=args.max_steps, timeout=args.timeout, on_event=progress)
        row = summarize(run)
        rows.append(row)
        roots.append(run.root)
        print(f"    wall {row['wall_s']}s, done {row['done']}/{args.subtasks}, verified {row['verified']}", flush=True)
    print("\n" + format_table(rows))

    problems = check_expectations(rows)
    print("\n" + ("RESULT: healthy" if not problems else "RESULT: problems found"))
    for pr in problems:
        print(" -", pr)
    if args.report:
        Path(args.report).write_text(json.dumps({"rows": rows, "problems": problems}, indent=1), encoding="utf-8")
        print("report written to", args.report)
    if not args.keep:
        for r in roots:
            shutil.rmtree(r, ignore_errors=True)
    else:
        print("projects kept:", ", ".join(map(str, roots)))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
