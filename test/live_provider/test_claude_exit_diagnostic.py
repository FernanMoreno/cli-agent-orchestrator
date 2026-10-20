"""Offline contract tests for the private Claude exit diagnostic sidecar."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest
import test_claude_graceful_mcp_cleanup as cleanup_case

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize(
    ("exit_mode", "request_outcome", "seal", "budget", "audit_failed"),
    [
        (
            "normal-return",
            "http_200",
            "seal_found_poll_return_before_deadline",
            "within_nominal_budget",
            False,
        ),
        (
            "no-return",
            "http_200",
            "no_seal_at_poll_return",
            "within_nominal_budget",
            True,
        ),
        (
            "error",
            "request_error",
            "no_seal_at_poll_return",
            "within_nominal_budget",
            True,
        ),
        ("timeout", "request_error", "not_checked", "exhausted_after_request", True),
        (
            "non-success-with-seal",
            "http_other_status",
            "seal_found_poll_return_before_deadline",
            "within_nominal_budget",
            True,
        ),
        ("late-normal-return", "http_200", "not_checked", "exhausted_after_request", True),
        (
            "late-seal-observation",
            "http_200",
            "seal_found_poll_return_after_deadline",
            "exhausted_after_poll",
            True,
        ),
    ],
)
def test_claude_exit_diagnostic_is_closed_and_does_not_change_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_mode: str,
    request_outcome: str,
    seal: str,
    budget: str,
    audit_failed: bool,
) -> None:
    state, server, calls, observer, clock = cleanup_case._setup_cleanup(tmp_path, monkeypatch)
    diagnostic_writes: list[tuple[tuple[str, str], ...]] = []
    original_writer = cleanup_case.live._write_private_json

    def record_diagnostic_write(path: Path, value: Any) -> None:
        if path.name == "claude-exit-diagnostic.json":
            diagnostic_writes.append(tuple(calls))
        original_writer(path, value)

    monkeypatch.setattr(cleanup_case.live, "_write_private_json", record_diagnostic_write)
    cleanup_ok, _ = cleanup_case._run_cleanup_with_fake_api(
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
    assert diagnostic_writes == [tuple(calls)]
    assert cleanup_ok
    assert state.cleanup == "pass"
    assert state.audit_failed is audit_failed

    diagnostic_path = state.run_root / "claude-exit-diagnostic.json"
    diagnostic_bytes = diagnostic_path.read_bytes()
    diagnostic = json.loads(diagnostic_bytes)
    assert stat.S_IMODE(diagnostic_path.stat().st_mode) == 0o600
    assert diagnostic == {
        "schema": "spec023.claude-exit-diagnostic.v2",
        "exit_request": request_outcome,
        "seal_poll_result": seal,
        "budget": budget,
    }
    for forbidden in (
        "offline-one",
        "offline-two",
        "synthetic offline timeout",
        "127.0.0.1",
        state.sender_id,
        state.receiver_id,
        str(state.run_root),
    ):
        assert forbidden.encode() not in diagnostic_bytes


def test_diagnostic_write_failure_does_not_change_success_or_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, server, calls, observer, clock = cleanup_case._setup_cleanup(tmp_path, monkeypatch)
    original_writer = cleanup_case.live._write_private_json

    def fail_diagnostic(path: Path, value: Any) -> None:
        if path.name == "claude-exit-diagnostic.json":
            raise cleanup_case.live.AcceptanceFailure("cleanup")
        original_writer(path, value)

    monkeypatch.setattr(cleanup_case.live, "_write_private_json", fail_diagnostic)
    cleanup_ok, _ = cleanup_case._run_cleanup_with_fake_api(
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
    assert cleanup_ok
    assert not state.audit_failed
    assert state.cleanup == "pass"
    assert not (state.run_root / "claude-exit-diagnostic.json").exists()


def test_non_claude_cleanup_emits_no_claude_exit_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, server, calls, observer, clock = cleanup_case._setup_cleanup(
        tmp_path, monkeypatch, provider="opencode_cli"
    )
    cleanup_ok, _ = cleanup_case._run_cleanup_with_fake_api(
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
    assert cleanup_ok
    assert not (state.run_root / "claude-exit-diagnostic.json").exists()


def test_expired_budget_before_request_records_not_attempted_without_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, server, calls, observer, _clock = cleanup_case._setup_cleanup(tmp_path, monkeypatch)
    moments = iter((100.0, 107.0, 107.0))
    monkeypatch.setattr(cleanup_case.live.time, "monotonic", lambda: next(moments))

    cleanup_ok, _ = cleanup_case._run_cleanup_with_fake_api(
        state,
        server,
        calls,
        observer,
        _clock,
        monkeypatch,
        exit_mode="normal-return",
    )

    assert calls == [
        ("DELETE", state.sender_id),
        ("DELETE", state.receiver_id),
    ]
    assert cleanup_ok
    assert state.audit_failed
    diagnostic = json.loads((state.run_root / "claude-exit-diagnostic.json").read_bytes())
    assert diagnostic == {
        "schema": "spec023.claude-exit-diagnostic.v2",
        "exit_request": "not_attempted",
        "seal_poll_result": "not_checked",
        "budget": "exhausted_before_request",
    }
