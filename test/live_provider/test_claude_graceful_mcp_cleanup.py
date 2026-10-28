"""Offline composition proof for Claude MCP graceful close before DELETE."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import message_api_restart_live as live
import message_mcp_observer
import pytest
import sitecustomize

pytestmark = pytest.mark.offline_database


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def monotonic(self) -> float:
        return self.now


class _ExitedProcess:
    def poll(self) -> int:
        return 0


class _ActiveServer:
    url = "http://127.0.0.1:24571"

    def stop(self) -> None:
        return None


def _setup_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: str = "claude_code",
) -> tuple[live.RunState, _ActiveServer, list[tuple[str, str]], Any, _Clock]:
    tmp_path.chmod(0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    audit_path = tmp_path / "sender-mcp-audit.jsonl"
    monkeypatch.setenv("CAO_SPEC023_MCP_AUDIT_PATH", str(audit_path))

    class _FakeMCP:
        async def call_tool(self, _name: str, _arguments: dict[str, str]) -> object:
            return object()

    observer = message_mcp_observer.install_message_observer(_FakeMCP(), audit_path)
    fake_mcp = observer._server
    asyncio.run(fake_mcp.call_tool("send_message", {"message": "offline-one"}))
    asyncio.run(fake_mcp.call_tool("send_message", {"message": "offline-two"}))
    assert live.read_dispatch_audit(audit_path) == [
        {"tool": "send_message", "status": "completed", "outcome": "unknown"},
        {"tool": "send_message", "status": "completed", "outcome": "unknown"},
    ]
    assert not live.audit_completion_seal_valid(audit_path)

    state = live.RunState(
        provider=provider,
        run_root=tmp_path,
        workspace=workspace,
        home=tmp_path / "home",
        source_root=tmp_path / "source",
        audit_path=audit_path,
    )
    state.sender_id = "a1b2c3d4"
    state.receiver_id = "e5f60718"
    server = _ActiveServer()
    state.active_server = server
    state.servers.append(server)
    calls: list[tuple[str, str]] = []
    clock = _Clock()
    monkeypatch.setattr(live.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(live, "_api_process", lambda _server: _ExitedProcess())
    monkeypatch.setattr(live, "_stop_server", lambda _state, _server: True)
    return state, server, calls, observer, clock


def _run_cleanup_with_fake_api(
    state: live.RunState,
    server: _ActiveServer,
    calls: list[tuple[str, str]],
    observer: Any,
    clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
    *,
    exit_mode: str,
) -> tuple[bool, list[object]]:
    request_timeouts: list[object] = []
    wait_timeouts: list[float] = []

    def fake_api_request(
        method: str,
        url: str,
        *,
        timeout: object,
        failure_stage: str,
        **_kwargs: object,
    ) -> tuple[int, object]:
        terminal_id = (
            url.rstrip("/").rsplit("/", 2)[-2] if method == "POST" else url.rsplit("/", 1)[-1]
        )
        calls.append((method, terminal_id))
        if method == "POST":
            assert url.endswith(f"/terminals/{state.sender_id}/exit")
            request_timeouts.append(timeout)
            assert isinstance(timeout, live.requests.adapters.TimeoutSauce)
            timeout.start_connect()
            if exit_mode == "error":
                clock.now += 0.25
                raise live.AcceptanceFailure(failure_stage)
            if exit_mode == "timeout":
                clock.now += 6.25
                timeout_error = live.requests.Timeout("synthetic offline timeout")
                assert type(timeout_error) is live.requests.Timeout
                raise timeout_error
            if exit_mode in {"normal-return", "non-success-with-seal", "late-seal-observation"}:
                result = object()
                assert sitecustomize._finish_source_bound_run(result, observer) is result
            if exit_mode == "late-normal-return":
                result = object()
                assert sitecustomize._finish_source_bound_run(result, observer) is result
            clock.now += 2.0
            if exit_mode == "late-normal-return":
                clock.now += 5.0
            status = 503 if exit_mode in {"non-success", "non-success-with-seal"} else 200
            return status, {"success": True}
        if method == "DELETE" and terminal_id == state.sender_id:
            # This fake models terminal deletion ending the stdio child without
            # a normal FastMCP.run return. It closes the observer but never
            # calls the producer's completion-marker writer.
            observer.close()
        return 200, {"success": True}

    def fake_wait(current: live.RunState, *, timeout: float = 6.0) -> bool:
        wait_timeouts.append(timeout)
        if exit_mode == "late-seal-observation":
            clock.now += timeout + 0.1
        return live.audit_completion_seal_valid(current.audit_path)

    monkeypatch.setattr(live, "_api_request", fake_api_request)
    monkeypatch.setattr(live, "_wait_for_audit_completion", fake_wait)
    cleanup_ok = live._cleanup(state, server_module=object())
    return cleanup_ok, [request_timeouts, wait_timeouts]


def test_claude_exit_closes_real_observer_before_sender_delete_with_shared_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, server, calls, observer, clock = _setup_cleanup(tmp_path, monkeypatch)
    cleanup_ok, timings = _run_cleanup_with_fake_api(
        state,
        server,
        calls,
        observer,
        clock,
        monkeypatch,
        exit_mode="normal-return",
    )

    assert calls == [
        ("POST", state.sender_id),
        ("DELETE", state.sender_id),
        ("DELETE", state.receiver_id),
    ]
    request_timeouts, wait_timeouts = timings
    assert len(request_timeouts) == 1
    timeout = request_timeouts[0]
    assert isinstance(timeout, live.requests.adapters.TimeoutSauce)
    assert timeout.total == pytest.approx(6.0)
    assert timeout.connect_timeout == pytest.approx(6.0)
    assert timeout.read_timeout == pytest.approx(4.0)
    assert len(wait_timeouts) == 1
    assert wait_timeouts[0] == pytest.approx(4.0)
    assert clock.now == pytest.approx(102.0)
    assert (clock.now - 100.0) + wait_timeouts[0] <= 6.0
    assert live.audit_completion_seal_valid(state.audit_path)
    assert not state.audit_failed
    assert cleanup_ok
    assert state.cleanup == "pass"


def test_claude_exit_http_success_without_normal_seal_still_fails_and_deletes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, server, calls, observer, clock = _setup_cleanup(tmp_path, monkeypatch)
    cleanup_ok, _ = _run_cleanup_with_fake_api(
        state,
        server,
        calls,
        observer,
        clock,
        monkeypatch,
        exit_mode="no-return",
    )

    assert calls == [
        ("POST", state.sender_id),
        ("DELETE", state.sender_id),
        ("DELETE", state.receiver_id),
    ]
    assert live.read_dispatch_audit(state.audit_path) == [
        {"tool": "send_message", "status": "completed", "outcome": "unknown"},
        {"tool": "send_message", "status": "completed", "outcome": "unknown"},
    ]
    assert not live.audit_completion_seal_valid(state.audit_path)
    assert state.audit_failed
    assert cleanup_ok
    assert state.cleanup == "pass"


def test_claude_exit_rejects_valid_seal_after_shared_budget_expires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, server, calls, observer, clock = _setup_cleanup(tmp_path, monkeypatch)
    cleanup_ok, timings = _run_cleanup_with_fake_api(
        state,
        server,
        calls,
        observer,
        clock,
        monkeypatch,
        exit_mode="late-normal-return",
    )

    assert calls == [
        ("POST", state.sender_id),
        ("DELETE", state.sender_id),
        ("DELETE", state.receiver_id),
    ]
    assert clock.now == pytest.approx(107.0)
    assert live.audit_completion_seal_valid(state.audit_path)
    assert state.audit_failed
    assert timings[1] == []
    assert cleanup_ok
    assert state.cleanup == "pass"


def test_claude_exit_rejects_seal_observed_after_wait_consumes_remaining_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, server, calls, observer, clock = _setup_cleanup(tmp_path, monkeypatch)
    cleanup_ok, timings = _run_cleanup_with_fake_api(
        state,
        server,
        calls,
        observer,
        clock,
        monkeypatch,
        exit_mode="late-seal-observation",
    )

    assert calls == [
        ("POST", state.sender_id),
        ("DELETE", state.sender_id),
        ("DELETE", state.receiver_id),
    ]
    assert clock.now == pytest.approx(106.1)
    assert timings[1] == [pytest.approx(4.0)]
    assert live.audit_completion_seal_valid(state.audit_path)
    assert state.audit_failed
    assert cleanup_ok
    assert state.cleanup == "pass"


def test_claude_exit_timeout_exhausting_budget_skips_late_seal_poll_and_deletes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, server, calls, observer, clock = _setup_cleanup(tmp_path, monkeypatch)
    cleanup_ok, timings = _run_cleanup_with_fake_api(
        state,
        server,
        calls,
        observer,
        clock,
        monkeypatch,
        exit_mode="timeout",
    )

    assert calls == [
        ("POST", state.sender_id),
        ("DELETE", state.sender_id),
        ("DELETE", state.receiver_id),
    ]
    assert clock.now == pytest.approx(106.25)
    assert timings[1] == []
    assert state.audit_failed
    assert cleanup_ok
    assert state.cleanup == "pass"


def test_claude_cleanup_preserves_prior_audit_failure_even_with_valid_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, server, calls, observer, clock = _setup_cleanup(tmp_path, monkeypatch)
    result = object()
    assert sitecustomize._finish_source_bound_run(result, observer) is result
    state.audit_failed = True

    cleanup_ok, _ = _run_cleanup_with_fake_api(
        state,
        server,
        calls,
        observer,
        clock,
        monkeypatch,
        exit_mode="no-return",
    )

    assert calls == [
        ("POST", state.sender_id),
        ("DELETE", state.sender_id),
        ("DELETE", state.receiver_id),
    ]
    assert live.audit_completion_seal_valid(state.audit_path)
    assert state.audit_failed
    assert cleanup_ok
    assert state.cleanup == "pass"


@pytest.mark.parametrize("exit_mode", ["error", "non-success", "non-success-with-seal"])
def test_claude_exit_failure_still_deletes_owned_ids_and_keeps_audit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_mode: str,
) -> None:
    state, server, calls, observer, clock = _setup_cleanup(tmp_path, monkeypatch)
    cleanup_ok, _ = _run_cleanup_with_fake_api(
        state,
        server,
        calls,
        observer,
        clock,
        monkeypatch,
        exit_mode=exit_mode,
    )

    assert calls == [
        ("POST", state.sender_id),
        ("DELETE", state.sender_id),
        ("DELETE", state.receiver_id),
    ]
    if exit_mode == "non-success-with-seal":
        assert live.audit_completion_seal_valid(state.audit_path)
    assert state.audit_failed
    assert cleanup_ok
    assert state.cleanup == "pass"


@pytest.mark.parametrize("provider", ["opencode_cli"])
def test_non_claude_cleanup_keeps_delete_first_and_never_sends_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    state, server, calls, observer, clock = _setup_cleanup(tmp_path, monkeypatch, provider=provider)
    assert state.provider == provider
    cleanup_ok, timings = _run_cleanup_with_fake_api(
        state,
        server,
        calls,
        observer,
        clock,
        monkeypatch,
        exit_mode="normal-return",
    )

    assert calls == [
        ("DELETE", state.sender_id),
        ("DELETE", state.receiver_id),
    ]
    assert timings == [[], [6.0]]
    assert not live.audit_completion_seal_valid(state.audit_path)
    assert cleanup_ok


def test_codex_unknown_audit_refuses_eof_without_legacy_seal_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex's existing two-success gate rejects this fixture's unknown results."""
    state, server, calls, observer, clock = _setup_cleanup(tmp_path, monkeypatch, provider="codex")
    cleanup_ok, timings = _run_cleanup_with_fake_api(
        state, server, calls, observer, clock, monkeypatch, exit_mode="normal-return"
    )
    assert calls == [("DELETE", state.sender_id), ("DELETE", state.receiver_id)]
    assert timings == [[], []]
    assert state.audit_failed
    assert not live.audit_completion_seal_valid(state.audit_path)
    assert cleanup_ok
