"""WebSocket server: the bridge between the Electron GUI and the agent core.

Phase 0: start the server, print ``WS_URL=<url>`` on stdout, keep a live
connection. The full RPC/event protocol lands in phase 1.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import websockets


async def handler(ws) -> None:
    async for raw in ws:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            await ws.send(json.dumps({"error": "invalid json"}))
            continue
        if msg.get("method") == "ping":
            await ws.send(json.dumps({"id": msg.get("id"), "result": "pong"}))
        else:
            await ws.send(json.dumps({"id": msg.get("id"), "error": f"unknown method: {msg.get('method')}"}))


def run() -> int:
    import websockets

    async def _run() -> None:
        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        print(f"WS_URL=ws://127.0.0.1:{port}", flush=True)
        await asyncio.Future()  # run forever

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(run())
