"""Installed SDK and real process signals; no model, auth, API or PostgreSQL."""

import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline_database

SCRIPT = """import asyncio,json,os,signal,sys,time
from contextlib import suppress
from pathlib import Path
from cli_agent_orchestrator.mcp_server.physical_eof import PhysicalEofGuard
before=(sys.stdin,sys.stdout)
handlers={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM,signal.SIGALRM)}
timer=signal.getitimer(signal.ITIMER_REAL)
blocking=(os.get_blocking(0),os.get_blocking(1))
if sys.argv[2]=='production':
    from cli_agent_orchestrator.mcp_server import server
    guard=server._physical_stdio_guard
    run=server.main
else:
    from fastmcp import FastMCP
    guard=PhysicalEofGuard()
    server=FastMCP('physical-eof-control',lifespan=guard.lifespan)
    @server.tool
    async def held() -> dict:
        Path(sys.argv[3]).write_text('entered')
        if sys.argv[2]=='thread_cancelled':
            task=asyncio.create_task(asyncio.to_thread(time.sleep,30))
            await asyncio.sleep(0.1)
            task.cancel()
            with suppress(asyncio.CancelledError):await task
        elif sys.argv[2] in {'thread_busy','thread_malformed'}:
            await asyncio.to_thread(time.sleep,30)
        else:
            await asyncio.sleep(30)
        return {'success':True}
    run=lambda:guard.run(lambda:server.run(show_banner=False))
run()
Path(sys.argv[1]).write_text(json.dumps({
    'state':guard.state.snapshot(),
    'streams_restored':sys.stdin is before[0] and sys.stdout is before[1],
    'signals_restored':all(signal.getsignal(s)==v for s,v in handlers.items()),
    'blocking_restored':blocking==(os.get_blocking(0),os.get_blocking(1)),
    'timer_restored':timer==signal.getitimer(signal.ITIMER_REAL),
}))
"""


class Peer:
    def __init__(self, root, mode):
        self.marker = root / "normal.json"
        self.busy = root / "busy"
        script = root / "server.py"
        script.write_text(SCRIPT)
        script.chmod(0o600)
        repo = Path(__file__).resolve().parents[2]
        env = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "HOME": str(root),
            "CAO_HOME_DIR": str(root / "cao"),
            "PYTHONPATH": str(repo / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "CAO_INTERNAL_MCP_PHYSICAL_EOF": "1",
            "CAO_INTERNAL_MCP_TOOL_PROFILE": "interaction",
            "FASTMCP_LOG_LEVEL": "ERROR",
        }
        self.log = (root / "stderr.log").open("wb")
        self.process = subprocess.Popen(
            [sys.executable, str(script), str(self.marker), mode, str(self.busy)],
            cwd=root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            start_new_session=True,
            bufsize=0,
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.buffer = b""

    def send(self, value):
        self.process.stdin.write((json.dumps({"jsonrpc": "2.0", **value}) + "\n").encode())
        self.process.stdin.flush()

    def response(self, identity):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                row = json.loads(line)
                if row.get("id") == identity:
                    assert "error" not in row
                    return row
            assert self.selector.select(max(0, deadline - time.monotonic()))
            data = os.read(self.process.stdout.fileno(), 65536)
            assert data, "owned MCP exited before response; inspect private stderr"
            self.buffer += data
        raise AssertionError("owned_mcp_response_deadline")

    def initialize(self):
        self.send(
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "physical-eof-test", "version": "1"},
                },
            }
        )
        self.response(1)
        self.send({"method": "notifications/initialized"})
        self.send({"id": 2, "method": "tools/list", "params": {}})
        assert self.response(2)["result"]["tools"]

    def close(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=5)
        self.selector.close()
        for stream in [self.process.stdin, self.process.stdout, self.log]:
            if stream and not stream.closed:
                stream.close()


@pytest.mark.parametrize(
    "mode",
    [
        "eof",
        "signal_eof",
        "signal_no_eof",
        "busy",
        "thread_busy",
        "thread_malformed",
        "thread_cancelled",
        "partial",
        "malformed",
        "notification",
        "production",
    ],
)
def test_installed_sdk_seals_only_real_settled_eof_and_restores_owner_state(tmp_path, mode):
    peer = Peer(tmp_path, mode)
    try:
        peer.initialize()
        if mode in {"signal_eof", "signal_no_eof"}:
            peer.process.send_signal(signal.SIGINT)
            time.sleep(0.1)
            if peer.process.poll() is None:
                peer.process.send_signal(signal.SIGTERM)
        if mode in {"busy", "thread_busy", "thread_malformed", "thread_cancelled"}:
            peer.send(
                {"id": 3, "method": "tools/call", "params": {"name": "held", "arguments": {}}}
            )
            deadline = time.monotonic() + 5
            while not peer.busy.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert peer.busy.exists()
            if mode == "thread_cancelled":
                peer.response(3)
            elif mode == "thread_malformed":
                peer.process.stdin.write(b"PRIVATE INVALID WITH ACTIVE THREAD\n")
                peer.process.stdin.flush()
        elif mode == "partial":
            peer.process.stdin.write(b'{"jsonrpc":"2.0","id":99')
            peer.process.stdin.flush()
        elif mode == "malformed":
            peer.process.stdin.write(b"PRIVATE INVALID\n")
            peer.process.stdin.flush()
        elif mode == "notification":
            peer.send({"method": "notifications/unobserved", "params": {}})
        close_started = time.monotonic()
        if mode != "signal_no_eof":
            peer.process.stdin.close()
        status = peer.process.wait(timeout=12)
        elapsed = time.monotonic() - close_started
        if mode in {"eof", "signal_eof", "production"}:
            assert elapsed <= 6.0
            assert status == 0, "SDK normal path failed; inspect private stderr"
            result = json.loads(peer.marker.read_text())
            assert result["state"]["physical_eof"] is True
            assert result["state"]["unsafe"] is None
            assert (
                result["streams_restored"]
                and result["signals_restored"]
                and result["blocking_restored"]
                and result["timer_restored"]
            )
            if mode == "signal_eof":
                assert result["state"]["signal_count"] >= 1
        else:
            assert elapsed <= 6.5  # Fixed6s watchdog plus process scheduling.
            assert status != 0
            assert not peer.marker.exists()
    finally:
        peer.close()
