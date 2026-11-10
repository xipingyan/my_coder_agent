"""Integration tests for the WebSocket server: RPC, event stream, async approvals.

A real server process is started with a scripted FakeProvider (same style as
tests/conftest.py in the old project); the test talks to it over a real
WebSocket connection, exactly like the Electron GUI will.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import websockets

HERE = Path(__file__).parent
BACKEND = HERE.parent


class FakeProvider:
    """Scripted LLM, runs inside the server subprocess (imported by name there)."""

    def __init__(self):
        self.script = [
            {"tool_calls": [("write_file", {"path": "hello.txt", "content": "hi"})]},
            {"content": "all done"},
        ]

    def stream_chat(self, messages, tools=None, cancel=None, thinking=None, max_tokens=None):
        from coder_agent.llm import LLMChunk, ToolCall, Usage
        reply = self.script.pop(0) if self.script else {"content": "done"}
        if reply.get("content"):
            yield LLMChunk("content", reply["content"])
        calls = [ToolCall(f"c{i}", n, json.dumps(a)) for i, (n, a) in enumerate(reply.get("tool_calls", []))]
        if calls:
            yield LLMChunk("tool_calls", tool_calls=calls)
        yield LLMChunk("usage", usage=Usage(100, 10, 110))
        yield LLMChunk("done", finish_reason="tool_calls" if calls else "stop")

    def list_models(self):
        return ["fake"]


# ---------------------------------------------------------------------------
@pytest.fixture
def server_process(tmp_path, monkeypatch):
    """Start the server with the fake provider and yield (url, proc)."""
    home = tmp_path / "home"
    project = tmp_path / "proj"
    project.mkdir()
    # strict approval policy: every write must be asked
    (project / ".agent").mkdir()
    (project / ".agent" / "approval.toml").write_text("[paths]\nauto_approve = false\n", encoding="utf-8")

    env = dict(os.environ, CODER_AGENT_HOME=str(home))
    code = (
        "import asyncio, json, sys\n"
        "from coder_agent.llm import LLMProvider\n"
        "from coder_agent.server import Server, handler, run\n"
        "import websockets\n"
        "class Fake(LLMProvider):\n"
        "    def __init__(self):\n"
        "        self.script = [\n"
        "            {'tool_calls': [('write_file', {'path': 'hello.txt', 'content': 'hi'})]},\n"
        "            {'content': 'all done'},\n"
        "        ]\n"
        "    def stream_chat(self, messages, tools=None, cancel=None, thinking=None, max_tokens=None):\n"
        "        from coder_agent.llm import LLMChunk, ToolCall, Usage\n"
        "        reply = self.script.pop(0) if self.script else {'content': 'done'}\n"
        "        if reply.get('content'):\n"
        "            yield LLMChunk('content', reply['content'])\n"
        "        calls = [ToolCall(f'c{i}', n, json.dumps(a)) for i, (n, a) in enumerate(reply.get('tool_calls', []))]\n"
        "        if calls:\n"
        "            yield LLMChunk('tool_calls', tool_calls=calls)\n"
        "        yield LLMChunk('usage', usage=Usage(100, 10, 110))\n"
        "        yield LLMChunk('done', finish_reason='tool_calls' if calls else 'stop')\n"
        "    def list_models(self):\n"
        "        return ['fake']\n"
        "async def main():\n"
        f"    server = Server(r'{project}', Fake)\n"
        "    srv = await websockets.serve(lambda ws, *a: handler(ws, server), '127.0.0.1', 0)\n"
        "    print('WS_URL=ws://127.0.0.1:' + str(srv.sockets[0].getsockname()[1]), flush=True)\n"
        "    await asyncio.Future()\n"
        "asyncio.run(main())\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=str(BACKEND),
    )
    line = proc.stdout.readline().strip()
    assert line.startswith("WS_URL="), f"server failed to start: {line}\n{proc.stderr.read()}"
    url = line.split("=", 1)[1]
    yield url, project, home
    proc.terminate()
    proc.wait(timeout=10)


# ---------------------------------------------------------------------------
class Client:
    """Tiny RPC client mirroring what the Electron renderer will do."""

    def __init__(self, ws):
        self.ws = ws
        self.next_id = 0
        self.events: list[dict] = []

    async def call(self, method: str, **params) -> dict:
        self.next_id += 1
        await self.ws.send(json.dumps({"id": self.next_id, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if "event" in msg:
                self.events.append(msg)
                continue
            assert msg.get("id") == self.next_id, msg
            if "error" in msg:
                raise AssertionError(f"{method} failed: {msg['error']}")
            return msg["result"]

    async def wait_event(self, kind: str, timeout: float = 15.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for i, ev in enumerate(self.events):
                if ev["event"] == kind:
                    return self.events.pop(i)
            # drain whatever is already queued
            try:
                while True:
                    msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=0.2))
                    if "event" in msg:
                        self.events.append(msg)
            except (asyncio.TimeoutError, TimeoutError):
                pass
        raise AssertionError(f"event {kind!r} not received; got {[e['event'] for e in self.events]}")


def test_full_turn_with_approval(server_process):
    url, project, home = server_process

    async def scenario():
        async with websockets.connect(url) as ws:
            c = Client(ws)
            r = await c.call("open_project", root=str(project))
            assert r["root"] == str(project)
            assert "general" in r["task_types"]

            await c.call("submit", prompt="write hello.txt", task_type="general")

            # the model wants to write a file -> approval is asked
            ev = await c.wait_event("approval_requested")
            assert ev["data"]["kind"] == "write"
            assert "hello.txt" in ev["data"]["title"] or "hello.txt" in ev["data"]["detail"]
            req_id = ev["data"]["req_id"]

            # answer from the "GUI"
            await c.call("answer", req_id=req_id, response={"allow": True})

            fin = await c.wait_event("turn_finished")
            assert fin["data"]["status"] == "done", fin
            assert (project / "hello.txt").read_text() == "hi"
            kinds = [e["event"] for e in c.events]
            assert "turn_started" in kinds and "tool_call" in kinds and "context_usage" in kinds

    asyncio.run(scenario())


def test_reject_approval_with_feedback(server_process):
    url, project, home = server_process

    async def scenario():
        async with websockets.connect(url) as ws:
            c = Client(ws)
            await c.call("open_project", root=str(project))
            await c.call("submit", prompt="write hello.txt")
            ev = await c.wait_event("approval_requested")
            await c.call("answer", req_id=ev["data"]["req_id"],
                         response={"allow": False, "feedback": "do not write files"})
            fin = await c.wait_event("turn_finished")
            assert fin["data"]["status"] == "done"  # the model gets the feedback and stops
            assert not (project / "hello.txt").exists()

    asyncio.run(scenario())


def test_cancel_while_waiting_approval(server_process):
    url, project, home = server_process

    async def scenario():
        async with websockets.connect(url) as ws:
            c = Client(ws)
            await c.call("open_project", root=str(project))
            await c.call("submit", prompt="write hello.txt")
            ev = await c.wait_event("approval_requested")
            await c.call("cancel")
            fin = await c.wait_event("turn_finished")
            assert fin["data"]["status"] in ("cancelled", "done")
            assert not (project / "hello.txt").exists()

    asyncio.run(scenario())


def test_busy_guard_and_sessions(server_process):
    url, project, home = server_process

    async def scenario():
        async with websockets.connect(url) as ws:
            c = Client(ws)
            await c.call("open_project", root=str(project))
            sid = (await c.call("open_project", root=str(project)))["session_id"]
            # unknown method
            c.next_id += 1
            await c.ws.send(json.dumps({"id": c.next_id, "method": "nope", "params": {}}))
            while True:
                msg = json.loads(await c.ws.recv())
                if msg.get("id") == c.next_id:
                    assert "error" in msg
                    break

            # rewind of a non-existent turn -> error, not crash
            with pytest.raises(AssertionError):
                await c.call("rewind", turn_id="nope")

            # context estimate works on an empty session
            r = await c.call("context_estimate")
            assert r["max_tokens"] > 0

            # system prompt is non-empty
            r = await c.call("system_prompt", task_type="general")
            assert len(r["text"]) > 100

    asyncio.run(scenario())


def test_delete_sessions(server_process):
    url, project, home = server_process

    async def scenario():
        async with websockets.connect(url) as ws:
            c = Client(ws)
            others = [(await c.call("open_project", root=str(project)))["session_id"] for _ in range(2)]
            current = (await c.call("open_project", root=str(project)))["session_id"]
            # delete two non-current sessions
            r = await c.call("delete_sessions", session_ids=others)
            assert r["ok"] and r["deleted"] == 2 and "new_session" not in r
            # (summaries only list sessions with turns, so just check the deleted ones are gone)
            left = [s["id"] for s in (await c.call("session_summaries"))["sessions"]]
            assert not (set(others) & set(left))
            # deleting the current session starts a fresh one
            r = await c.call("delete_sessions", session_ids=[current])
            assert r["ok"] and r["new_session"] != current

    asyncio.run(scenario())
