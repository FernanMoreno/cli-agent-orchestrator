"""Real local-resource closure, distinct from peer/kernel EOF."""

import errno
import fcntl
import os
import signal
import time
from pathlib import Path

import anyio
import pytest

from cli_agent_orchestrator.mcp_server.physical_eof import (
    GuardError,
    ObservedPipe,
    PhysicalEofGuard,
    guard_from_environment,
)

pytestmark = pytest.mark.offline_database


def test_real_owned_pipe_stop_closes_resource_without_fabricating_eof():
    read, write = os.pipe()
    owner = os.fdopen(read, "rb", buffering=0)
    guard = PhysicalEofGuard(allow_signal_close=True)
    pipe = ObservedPipe(owner.fileno(), inbound=True, state=guard.state, owner=owner)
    duplicate = pipe.fileno()
    try:
        guard.state.request_stop(signal.SIGINT)
        with pytest.raises(anyio.ClosedResourceError):
            pipe.read1(1)
        assert pipe.closed
        with pytest.raises(OSError) as caught:
            os.fstat(duplicate)
        assert caught.value.errno == errno.EBADF
        # Reserve the original descriptor atomically, without a reusable-FD gap
        # or a readable /dev/null that would pretend the peer sent EOF.
        assert fcntl.fcntl(owner.fileno(), fcntl.F_GETFL) & os.O_ACCMODE == os.O_WRONLY
        with pytest.raises(OSError) as caught:
            owner.read(1)
        assert caught.value.errno == errno.EBADF
        with pytest.raises(BrokenPipeError):
            os.write(write, b"late")
        assert guard.state.physical_eof is False
        assert guard.state.local_input_closed is True
        guard.state.verify_normal()
    finally:
        pipe.close()
        owner.close()
        os.close(write)


def test_local_mode_requires_its_own_explicit_selector_and_rejects_both():
    local = "CAO_INTERNAL_MCP_SIGNAL_STOP"
    physical = "CAO_INTERNAL_MCP_PHYSICAL_EOF"
    assert guard_from_environment({}) is None
    assert guard_from_environment({physical: "1"}).state.allow_signal_close is False
    assert guard_from_environment({local: "1"}).state.allow_signal_close is True
    for env in [{local: "0"}, {local: "yes"}, {local: "1", physical: "1"}]:
        with pytest.raises(GuardError):
            guard_from_environment(env)


LOCAL_SCRIPT = """import asyncio,atexit,fcntl,json,os,signal,sys,time
from pathlib import Path
from cli_agent_orchestrator.mcp_server.physical_eof import PhysicalEofGuard
before=sys.stdin,sys.stdout
@atexit.register
def normal_cleanup():
    time.sleep(1.0 if sys.argv[2]=='local_vendor_cutoff' else 0.15)
    Path(sys.argv[1]).with_name('atexit-completed').write_text('done')
if sys.argv[2]=='local_production':
    from cli_agent_orchestrator.mcp_server import server
    guard=server._physical_stdio_guard
    run=server.main
else:
    from fastmcp import FastMCP
    guard=PhysicalEofGuard(allow_signal_close=True)
    app=FastMCP('local-stop',lifespan=guard.lifespan)
    @app.tool
    async def send_message() -> dict:
        Path(sys.argv[3]).write_text('entered')
        await asyncio.sleep(30)
        return {'success':True}
    run=lambda:guard.run(lambda:app.run(show_banner=False))
from message_mcp_observer import install_message_observer
from sitecustomize import _finish_source_bound_run
root=Path(sys.argv[1]).parent
audit_path=root/'mcp-audit.jsonl'
os.environ['CAO_SPEC023_MCP_AUDIT_PATH']=str(audit_path)
observer=install_message_observer(server.mcp if sys.argv[2]=='local_production' else app,audit_path)
result=run()
_finish_source_bound_run(result,observer)
# Observe the original fd before opening the marker file.
stdin_write_only=fcntl.fcntl(before[0].fileno(),fcntl.F_GETFL)&os.O_ACCMODE==os.O_WRONLY
Path(sys.argv[1]).write_text(json.dumps({'state':guard.state.snapshot(),'stdin_write_only':stdin_write_only,'streams_restored':sys.stdin is before[0] and sys.stdout is before[1]}))
"""


@pytest.mark.parametrize(
    "mode",
    [
        "local_idle",
        "local_busy",
        "local_partial",
        "local_production",
        "local_vendor_cutoff",
        "local_unknown_notification",
    ],
)
def test_actual_sdk_owned_stop_preserves_origin_seal_and_process_completion(tmp_path, mode):
    from test.mcp_server.test_physical_eof_sdk import Peer

    repo = Path(__file__).resolve().parents[2]
    peer = Peer(
        tmp_path,
        mode,
        script_text=LOCAL_SCRIPT,
        env_overrides={
            "CAO_INTERNAL_MCP_PHYSICAL_EOF": None,
            "CAO_INTERNAL_MCP_SIGNAL_STOP": "1",
            "PYTHONPATH": str(repo / "test/live_provider")
            + ":"
            + str(repo / "src")
            + ":"
            + str(repo),
        },
    )
    try:
        peer.initialize()
        if mode == "local_busy":
            peer.send(
                {
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "send_message", "arguments": {}},
                }
            )
            deadline = time.monotonic() + 5
            while not peer.busy.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert peer.busy.exists()
        elif mode == "local_partial":
            peer.process.stdin.write(b'{"jsonrpc":"2.0","id":99')
            peer.process.stdin.flush()
        if mode == "local_unknown_notification":
            peer.send({"method": "notifications/unobserved", "params": {}})
        started = time.monotonic()
        peer.process.send_signal(signal.SIGINT)
        if mode == "local_vendor_cutoff":
            # Preserve/reproduce the failed vendor order: even a genuine SDK
            # seal is NOT complete process retirement. Do not accept this path.
            time.sleep(0.1)
            if peer.process.poll() is None:
                peer.process.send_signal(signal.SIGTERM)
            time.sleep(max(0, started + 0.5 - time.monotonic()))
            assert peer.process.poll() is None
            peer.process.kill()
            assert peer.process.wait(timeout=2) == -signal.SIGKILL
            assert peer.marker.exists()
            assert not (tmp_path / "atexit-completed").exists()
        elif mode in {"local_idle", "local_production"}:
            # Correct owner order: no parent/vendor escalation until the child
            # is fully retired. The ORIGINAL six-second allowance is unchanged.
            status = peer.process.wait(timeout=max(0.001, started + 6 - time.monotonic()))
            assert time.monotonic() - started <= 6
            assert status == 0
            result = __import__("json").loads(peer.marker.read_text())
            assert result["state"]["physical_eof"] is False
            assert result["state"]["local_input_closed"] is True
            assert result["state"]["unsafe"] is None
            assert result["stdin_write_only"] and result["streams_restored"]
            assert (tmp_path / "atexit-completed").read_text() == "done"
        else:
            status = peer.process.wait(timeout=7)
            assert time.monotonic() - started <= 6.5
            assert status != 0
            assert not peer.marker.exists()
        seal = tmp_path / "mcp-audit.jsonl.complete"
        if mode in {"local_idle", "local_production", "local_vendor_cutoff"}:
            assert seal.read_bytes() == b"SPEC023_AUDIT_COMPLETE\n"
            assert seal.stat().st_mode & 0o777 == 0o600
        else:
            assert not seal.exists()
    finally:
        peer.close()


def test_ready_input_and_pending_reply_refuse_local_close_until_settled():
    import json

    read, write = os.pipe()
    owner = os.fdopen(read, "rb", buffering=0)
    guard = PhysicalEofGuard(allow_signal_close=True)
    pipe = ObservedPipe(owner.fileno(), inbound=True, state=guard.state, owner=owner)
    request = b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
    try:
        os.write(write, request)
        guard.state.request_stop(signal.SIGINT)
        assert pipe._close_on_stop() is False
        assert pipe.read1(4096) == request
        assert pipe._close_on_stop() is False
        guard.state.feed_output(
            (json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n").encode()
        )
        assert pipe._close_on_stop() is False
        guard.state.output_flushed()
        with pytest.raises(anyio.ClosedResourceError):
            pipe.read1(1)
        assert guard.state.local_input_closed and not guard.state.physical_eof
    finally:
        pipe.close()
        owner.close()
        os.close(write)


def test_real_socket_read_resource_stop_does_not_turn_local_shutdown_into_peer_eof():
    import socket

    left, right = socket.socketpair()
    owner = os.fdopen(left.detach(), "rb", buffering=0)
    guard = PhysicalEofGuard(allow_signal_close=True)
    pipe = ObservedPipe(owner.fileno(), inbound=True, state=guard.state, owner=owner)
    try:
        guard.state.request_stop(signal.SIGTERM)
        with pytest.raises(anyio.ClosedResourceError):
            pipe.read1(1)
        assert guard.state.local_input_closed and not guard.state.physical_eof
        with pytest.raises(BrokenPipeError):
            right.send(b"late")
        guard.state.verify_normal()
    finally:
        pipe.close()
        owner.close()
        right.close()
