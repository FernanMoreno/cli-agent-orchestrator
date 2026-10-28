"""Offline controls for explicit diagnostic EOF, never native shutdown credit."""

import asyncio
import importlib
import io
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.offline_database


def shutdown_module():
    spec = importlib.util.find_spec("message_mcp_shutdown")
    assert spec is not None, "source-bound graceful EOF helper is missing"
    return importlib.import_module("message_mcp_shutdown")


@pytest.mark.parametrize("payload", ["café 日本語\r\nnext\rfinal", "🙂\n", ""])
def test_controlled_reader_preserves_sdk_text_decoding(payload):
    module = shutdown_module()
    read_fd, write_fd = os.pipe()
    try:
        raw = module.ControlledInput(read_fd, lambda: False)
        with io.TextIOWrapper(io.BufferedReader(raw), encoding="utf-8", errors="replace") as reader:
            encoded = payload.encode()
            for byte in encoded:
                os.write(write_fd, bytes([byte]))
            os.close(write_fd)
            write_fd = -1
            assert reader.read() == payload.replace("\r\n", "\n").replace("\r", "\n")
        os.fstat(read_fd)  # the reader owns a duplicate, not the caller's fd
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


def test_controlled_reader_returns_eof_while_original_pipe_stays_open():
    module = shutdown_module()
    read_fd, write_fd = os.pipe()
    try:
        with module.ControlledInput(read_fd, lambda: True) as reader:
            assert reader.readinto(bytearray(10)) == 0
        os.write(write_fd, b"original pipe still open")
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_eof_request_refuses_unread_call_bytes_in_pipe():
    module = shutdown_module()
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b'{"jsonrpc":"2.0","method":"tools/call"}\n')
        with module.ControlledInput(read_fd, lambda: True) as reader:
            with pytest.raises(module.ShutdownError, match="shutdown_late_input"):
                reader.readinto(bytearray(100))
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_request_publication_never_exposes_a_partial_marker(tmp_path, monkeypatch):
    module = shutdown_module()
    tmp_path.chmod(0o700)
    path = tmp_path / "audit.jsonl.eof-request"
    original_write = os.write

    def observed_write(descriptor, payload):
        assert not path.exists(), "reader can observe an incomplete shutdown request"
        return original_write(descriptor, payload)

    with monkeypatch.context() as patch:
        patch.setattr(module.os, "write", observed_write)
        module._publish(path, module.REQUEST_MARKER)
    assert path.read_bytes() == module.REQUEST_MARKER
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(RuntimeError):
        module._publish(path, b"replacement")
    assert path.read_bytes() == module.REQUEST_MARKER


def test_publication_preserves_an_incumbent_created_during_reservation(tmp_path, monkeypatch):
    module = shutdown_module()
    tmp_path.chmod(0o700)
    path = tmp_path / "request"
    original_open = module._open_private_audit_file

    def concurrent_publication(pending):
        descriptor = original_open(pending)
        path.write_bytes(b"incumbent")
        path.chmod(0o600)
        return descriptor

    monkeypatch.setattr(module, "_open_private_audit_file", concurrent_publication)
    with pytest.raises(module.ShutdownError, match="shutdown_artifact_exists"):
        module._publish(path, module.REQUEST_MARKER)
    assert path.read_bytes() == b"incumbent"
    assert not path.with_name("." + path.name + ".pending").exists()


def test_shutdown_refuses_active_dispatch_and_restores_handler(tmp_path):
    module = shutdown_module()
    audit = tmp_path / "audit.jsonl"
    tmp_path.chmod(0o700)
    audit.touch(mode=0o600)
    read_fd, write_fd = os.pipe()

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def dispatch(*args, **kwargs):
            entered.set()
            await release.wait()
            return "actual result"

        server = SimpleNamespace(call_tool=dispatch)
        with pytest.raises(module.ShutdownError):
            with module.controlled_stdio(server, audit, stdin_fd=read_fd) as control:
                task = asyncio.create_task(server.call_tool("send_message"))
                await entered.wait()
                control.request_path.write_bytes(module.REQUEST_MARKER)
                control.request_path.chmod(0o600)
                with pytest.raises(module.ShutdownError):
                    control.should_stop()
                release.set()
                assert await task == "actual result"
        assert server.call_tool is dispatch
        assert not Path(str(audit) + ".complete").exists()

    try:
        asyncio.run(run())
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.parametrize("unsafe", ["content", "mode", "symlink"])
def test_shutdown_rejects_unsafe_request(tmp_path, unsafe):
    module = shutdown_module()
    tmp_path.chmod(0o700)
    audit = tmp_path / "audit.jsonl"
    audit.touch(mode=0o600)
    read_fd, write_fd = os.pipe()
    server = SimpleNamespace(call_tool=lambda: None)
    try:
        with pytest.raises(module.ShutdownError):
            with module.controlled_stdio(server, audit, stdin_fd=read_fd) as control:
                if unsafe == "symlink":
                    control.request_path.symlink_to(audit)
                else:
                    control.request_path.write_bytes(
                        b"wrong" if unsafe == "content" else module.REQUEST_MARKER
                    )
                    control.request_path.chmod(0o644 if unsafe == "mode" else 0o600)
                control.should_stop()
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_dispatch_after_eof_request_prevents_successful_finalization(tmp_path):
    module = shutdown_module()
    tmp_path.chmod(0o700)
    audit = tmp_path / "audit.jsonl"
    audit.touch(mode=0o600)
    read_fd, write_fd = os.pipe()

    async def dispatch(*args, **kwargs):
        raise AssertionError("late dispatch must not reach the handler")

    server = SimpleNamespace(call_tool=dispatch)
    try:
        with pytest.raises(module.ShutdownError):
            with module.controlled_stdio(server, audit, stdin_fd=read_fd) as control:
                control.request_path.write_bytes(module.REQUEST_MARKER)
                control.request_path.chmod(0o600)
                assert control.should_stop()
                with pytest.raises(module.ShutdownError):
                    asyncio.run(server.call_tool("send_message"))
        assert server.call_tool is dispatch
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_codex_diagnostic_config_explicitly_selects_graceful_eof(tmp_path):
    import sys

    from message_api_restart import build_message_mcp_server_config, message_mcp_config_ready

    repo = Path(__file__).resolve().parents[2]
    tmp_path.chmod(0o700)
    for provider in ("codex", "claude_code", "opencode_cli"):
        config = build_message_mcp_server_config(
            provider=provider,
            python_executable=Path(sys.executable),
            source_root=repo,
            candidate_test_dir=Path(__file__).parent,
            audit_path=tmp_path / "audit.jsonl",
        )
        assert (config["env"].get("CAO_SPEC023_MCP_GRACEFUL_EOF") == "1") == (provider == "codex")
        assert message_mcp_config_ready(
            provider=provider,
            config=config,
            python_executable=Path(sys.executable),
            source_root=repo,
            candidate_test_dir=Path(__file__).parent,
            audit_path=tmp_path / "audit.jsonl",
        )


def test_codex_shutdown_requires_exact_two_successes_before_request(tmp_path, monkeypatch):
    import message_api_restart_live as live

    function = getattr(live, "_graceful_codex_mcp_shutdown", None)
    assert callable(function), "Codex cleanup does not request quiescent EOF"
    module = shutdown_module()
    audit = tmp_path / "audit.jsonl"
    tmp_path.chmod(0o700)
    audit.touch(mode=0o600)
    requested = []
    monkeypatch.setattr(module, "request_shutdown", lambda *a, **k: requested.append(True) or True)
    state = SimpleNamespace(audit_path=audit)
    assert not function(state)
    assert not requested
    row = b'{"tool":"send_message","status":"completed","outcome":"success"}\n'
    audit.write_bytes(row * 2)
    monkeypatch.setattr(live, "_audit_completion_present", lambda state: True)
    assert function(state)
    assert requested == [True]
    assert state.shutdown_mode == "harness_requested_eof"
    assert function(state)
    assert requested == [True], "cleanup must not request an exited child twice"


def test_receipt_never_credits_native_automatic_shutdown():
    from message_api_restart import project_safe_receipt

    projected = project_safe_receipt(
        {
            "provider": "codex",
            "result": "pass",
            "shutdown_mode": "harness_requested_eof",
            "native_automatic_shutdown_accepted": True,
        }
    )
    assert projected.get("shutdown_mode") == "harness_requested_eof"
    assert projected.get("native_automatic_shutdown_accepted") is False


@pytest.mark.parametrize("completed", [True, False])
def test_cleanup_finalizes_audit_before_deleting_native_actors(tmp_path, monkeypatch, completed):
    import message_api_restart_live as live

    state = live.RunState(
        provider="codex",
        run_root=tmp_path,
        workspace=tmp_path / "workspace",
        home=tmp_path / "home",
        source_root=Path(__file__).resolve().parents[2],
        audit_path=tmp_path / "audit.jsonl",
        sender_id="a1b2c3d4",
        receiver_id="e5f60718",
        active_server=SimpleNamespace(url="http://127.0.0.1:1"),
        servers=[],
        deleted_ids=set(),
    )
    trace = []
    monkeypatch.setattr(
        live, "_graceful_codex_mcp_shutdown", lambda state: trace.append("finalize") or completed
    )
    monkeypatch.setattr(live, "_delete_one", lambda url, actor: trace.append(actor) or True)
    monkeypatch.setattr(live, "_audit_completion_present", lambda state: completed)
    assert live._cleanup(state, server_module=None)
    assert trace == ["finalize", "a1b2c3d4", "e5f60718"]
    assert state.audit_failed is (not completed)


@pytest.mark.parametrize("corruption", ["duplicate", "birth", "inode", "boolean", "extra"])
def test_parent_refuses_stale_or_ambiguous_ready_before_request(tmp_path, corruption):
    module = shutdown_module()
    tmp_path.chmod(0o700)
    audit = tmp_path / "audit.jsonl"
    audit.touch(mode=0o600)
    metadata = audit.stat()
    ready = {
        "version": 1,
        "pid": os.getpid(),
        "uid": os.geteuid(),
        "birth": module._birth(os.getpid()),
        "audit_device": metadata.st_dev,
        "audit_inode": metadata.st_ino,
    }
    if corruption in {"birth", "inode"}:
        ready["birth" if corruption == "birth" else "audit_inode"] += 1
    if corruption == "boolean":
        ready["version"] = True
    if corruption == "extra":
        ready["other"] = 1
    payload = json.dumps(ready)
    if corruption == "duplicate":
        payload = '{"version":0,' + payload[1:]
    path = Path(str(audit) + ".eof-ready")
    path.write_text(payload)
    path.chmod(0o600)
    assert not module.request_shutdown(audit, deadline=time.monotonic() + 0.1)
    assert not Path(str(audit) + ".eof-request").exists()


def test_installed_sdk_frames_calls_and_seals_only_after_requested_child_exit(tmp_path):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    from message_api_restart import (
        audit_completion_seal_valid,
        read_dispatch_audit,
        validate_dispatch_history,
    )

    module = shutdown_module()
    tmp_path.chmod(0o700)
    audit = tmp_path / "audit.jsonl"
    helper_dir = Path(__file__).parent.resolve()
    script = tmp_path / "server.py"
    script.write_text("""from pathlib import Path
from fastmcp import FastMCP
from message_mcp_observer import install_message_observer
from message_mcp_shutdown import controlled_stdio
from sitecustomize import _finish_source_bound_run
import sys
mcp = FastMCP("framing-control")
@mcp.tool
def send_message(message: str) -> dict:
    return {"success": True, "echo": message}
audit = Path(sys.argv[1])
observer = install_message_observer(mcp, audit)
try:
    with controlled_stdio(mcp, audit):
        result = mcp.run(show_banner=False)
except BaseException:
    observer.close()
    raise
else:
    _finish_source_bound_run(result, observer)
""")
    script.chmod(0o600)
    env = dict(os.environ)
    for key in (
        "CAO_SPEC023_MCP_OBSERVER",
        "CAO_OPENCODE_MCP_OBSERVER",
        "CAO_SPEC023_MCP_GRACEFUL_EOF",
    ):
        env.pop(key, None)
    env["PYTHONPATH"] = str(helper_dir)
    env["CAO_SPEC023_MCP_AUDIT_PATH"] = str(audit)

    async def run():
        transport = StdioTransport(command=sys.executable, args=[str(script), str(audit)], env=env)
        async with Client(transport, timeout=30) as client:
            assert [tool.name for tool in await client.list_tools()] == ["send_message"]
            for value in ("café 日本語\r\n🙂", "second\nline"):
                result = await client.call_tool("send_message", {"message": value})
                assert result.structured_content == {"success": True, "echo": value}
            assert validate_dispatch_history(read_dispatch_audit(audit), expected_calls=2)
            assert not audit_completion_seal_valid(audit)
            assert await asyncio.to_thread(
                module.request_shutdown, audit, deadline=time.monotonic() + 6
            )
            assert audit_completion_seal_valid(audit)

    asyncio.run(run())
