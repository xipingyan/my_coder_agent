"""Command-line front-end (also handy for smoke-testing the server without the GUI)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .approval import ApprovalRequest, ApprovalResponse
from .config import load_config
from .engine import Engine
from .events import Event, EventBus
from .llm import LLMError, create_provider


def _printer(show_thinking: bool):
    state = {"mode": ""}

    def switch(mode: str, header: str) -> None:
        if state["mode"] != mode:
            print(f"\n{header}", flush=True)
            state["mode"] = mode

    def on_event(ev: Event) -> None:
        d = ev.data
        if ev.kind == "thinking_delta" and show_thinking:
            switch("think", "[thinking]")
            print(d["text"], end="", flush=True)
        elif ev.kind == "content_delta":
            switch("content", "[assistant]")
            print(d["text"], end="", flush=True)
        elif ev.kind == "tool_call":
            state["mode"] = ""
            print(f"\n[tool] {d['name']} {str(d['args'])[:200]}", flush=True)
        elif ev.kind == "tool_result":
            print(f"[result] {'ok' if d['ok'] else 'FAILED'}: {d['text'][:300]!r}", flush=True)
        elif ev.kind == "command_output":
            print("   | " + d["text"].rstrip(), flush=True)
        elif ev.kind in ("warning", "error"):
            print(f"\n[{ev.kind}] {d['text']}", flush=True)
        elif ev.kind == "context_compressed":
            print(f"\n[context] compressed {d['before']} -> {d['after']} tokens ({d['algorithm']})", flush=True)
        elif ev.kind == "llm_done":
            u = d.get("usage") or {}
            ttft = d.get("ttft")
            print(f"\n[llm] {d['elapsed']:.1f}s ttft={ttft:.1f}s tokens={u}" if ttft is not None else "", flush=True)
            state["mode"] = ""
        elif ev.kind == "approval_resolved" and d.get("auto"):
            print(f"[approval] auto {'allow' if d['allow'] else 'deny'}: {d['reason']}", flush=True)
        elif ev.kind == "doc_analysis":
            print(f"\n[docs] {d['subtask']} read {', '.join(d['docs'])} ({d['source']}):\n{d['summary']}", flush=True)
        elif ev.kind == "stage_started":
            print(f"\n=== stage {d['index'] + 1}/{d['total']}: {', '.join(d['ids'])} ===", flush=True)
        elif ev.kind == "subtask_finished":
            print(f"\n[subtask {d['id']}] {d['status']}", flush=True)

    return on_event


def _ask(req: ApprovalRequest) -> ApprovalResponse:
    print(f"\n=== Approval needed: {req.title} ===\n{req.detail}\n")
    ans = input("[y]es / [n]o / [a]lways (remember rule '%s') / or type feedback: " % req.suggested_rule).strip()
    low = ans.lower()
    if low in ("y", "yes", ""):
        return ApprovalResponse(True)
    if low in ("a", "always"):
        return ApprovalResponse(True, remember_rule=req.suggested_rule)
    if low in ("n", "no"):
        return ApprovalResponse(False)
    return ApprovalResponse(False, feedback=ans)


def _confirm_plan(plan):
    print("\n=== Plan ===")
    for i, stage in enumerate(plan.stages(), 1):
        print(f"Stage {i}:")
        for s in stage:
            dep = f" (after {', '.join(s.depends_on)})" if s.depends_on else ""
            print(f"  {s.id} [{s.task_type}] {s.title}{dep}\n      {s.goal}")
    return plan if input("Run this plan? [Y/n] ").strip().lower() in ("", "y", "yes") else None


def _confirm_stage(report):
    print(f"\n=== Stage {report.index + 1}/{report.total} finished ===")
    for s in report.subtasks:
        print(f"  {s.id} [{s.status}] {s.summary[:200]!r}")
    print("  files changed so far:", ", ".join(report.changed_files) or "none")
    ans = input("[c]ontinue / [r]etry failed / [s]top: ").strip().lower()
    return {"r": "retry", "s": "stop"}.get(ans[:1], "continue")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="coder-agent CLI")
    ap.add_argument("--root", default=".", help="project root")
    ap.add_argument("--prompt", help="run a single prompt and exit")
    ap.add_argument("--task-type", help="force a task type")
    ap.add_argument("--plan", action="store_true", help="split the prompt into subtasks (plan mode)")
    ap.add_argument("--base-url")
    ap.add_argument("--model")
    ap.add_argument("--effort", choices=["default", "none", "low", "medium", "high"])
    ap.add_argument("--show-thinking", action="store_true")
    ap.add_argument("--check", action="store_true", help="test the server connection and exit")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    cfg = load_config(root)
    if args.base_url:
        cfg.server.base_url = args.base_url
    if args.model:
        cfg.server.model = args.model
    if args.effort:
        cfg.thinking.effort = args.effort

    if args.check:
        try:
            models = create_provider(cfg).list_models()
        except LLMError as e:
            print("connection failed:", e)
            return 1
        print("server ok, models:", ", ".join(models))
        return 0

    bus = EventBus()
    bus.subscribe(_printer(args.show_thinking))
    engine = Engine(cfg, root, bus, approver=_ask)

    def run(prompt: str, plan: bool = False) -> None:
        try:
            if plan:
                res = engine.submit_plan(prompt, _confirm_plan, _confirm_stage)
                print(f"\n\n== plan finished: {res.status if res else 'no plan executed'} ==")
                if res:
                    print(res.summary)
                return
            res = engine.submit(prompt, args.task_type)
            print(f"\n\n== finished: {res.status} after {res.steps} step(s) ==")
        except KeyboardInterrupt:
            engine.cancel()

    if args.prompt:
        run(args.prompt, args.plan)
        return 0

    print(f"project: {root}\ncommands: /plan <goal>  /rewind <n>  /turns  /exit")
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if not line:
            continue
        if line in ("/exit", "/quit"):
            return 0
        if line == "/turns":
            for i, t in enumerate(engine.active_turns, 1):
                print(f"{i}. [{t.status}] ({t.task_type}) {t.prompt[:80]}")
            continue
        if line.startswith("/plan"):
            goal = line[5:].strip()
            if goal:
                run(goal, plan=True)
            else:
                print("usage: /plan <goal>")
            continue
        if line.startswith("/rewind"):
            turns = engine.active_turns
            try:
                idx = int(line.split()[1])
                restored, side = engine.rewind(turns[idx - 1].id)
            except (IndexError, ValueError):
                print("usage: /rewind <turn number>")
                continue
            print(f"rewound; restored files: {restored or 'none'}" + ("  (note: shell commands ran, their effects are not reverted)" if side else ""))
            continue
        run(line)


if __name__ == "__main__":
    sys.exit(main())
