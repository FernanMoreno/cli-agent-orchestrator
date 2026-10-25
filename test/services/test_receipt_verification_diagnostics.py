"""Content-free stage observations for existing receipt-verification attempts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers import opencode_cli as opencode_module
from cli_agent_orchestrator.providers.base import OutputExtractionError, TurnResultUnavailableError
from cli_agent_orchestrator.providers.opencode_cli import OpenCodeCliProvider
from cli_agent_orchestrator.services import terminal_service as ts
from cli_agent_orchestrator.services.status_monitor import StatusMonitor
from cli_agent_orchestrator.utils import opencode_v2_result_binding, opencode_v2_store
from cli_agent_orchestrator.utils.receipt_verification_diagnostics import (
    emit_receipt_verification_event,
)

STAGES = {
    "admission",
    "attempt",
    "result_extraction",
    "receipt_witness",
    "canonicalizer",
    "active_receipt_identity",
    "native_binding",
    "native_parts",
    "native_receipt_identity",
    "native_render_guard",
    "durable_cas",
}
OUTCOMES = {
    "stale_capture",
    "current_capture",
    "progress_unconfirmed",
    "started",
    "verified",
    "unresolved",
    "superseded",
    "returned",
    "unavailable",
    "error",
    "passed",
    "rejected",
    "not_applicable",
    "skip",
    "settled",
    "conflict",
}
PRIVATE_CANARY = "private-terminal-generation-receipt-path-exception-canary"


@pytest.fixture
def diagnostic_events():
    events: list[tuple[int, str, str]] = []
    previous = ts.set_receipt_verification_diagnostic_sink(
        lambda attempt, stage, outcome: events.append((attempt, stage, outcome))
    )
    try:
        yield events
    finally:
        ts.set_receipt_verification_diagnostic_sink(previous)


def _provider() -> tuple[OpenCodeCliProvider, str]:
    provider = OpenCodeCliProvider(
        terminal_id="private-terminal-id",
        session_name="private-session-name",
        window_name="private-window-name",
        agent_profile="diagnostic-agent",
    )
    provider._cli_major = 2
    provider.prepare_input(PRIVATE_CANARY)
    receipt = provider._pending_turn_receipt
    assert type(receipt) is str
    return provider, receipt


def _binding() -> SimpleNamespace:
    return SimpleNamespace(
        mode="native_text",
        agent_id="diagnostic-agent",
        database_path=Path("/private/native.db"),
        database_identity=(11, 17),
    )


def _patch_native_reads(monkeypatch, *, native_parts):
    monkeypatch.setattr(
        opencode_v2_result_binding,
        "read_v2_result_binding",
        lambda *_args, **_kwargs: _binding(),
    )
    monkeypatch.setattr(
        opencode_v2_result_binding,
        "assert_v2_result_binding_unchanged",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        opencode_v2_result_binding,
        "assert_v2_database_unchanged",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(opencode_v2_store, "_native_parts", native_parts)
    monkeypatch.setattr(
        "cli_agent_orchestrator.utils.opencode_config.to_opencode_agent_id",
        lambda _profile: "diagnostic-agent",
    )


def _assert_private_projection(events) -> None:
    assert all(
        type(event) is tuple
        and len(event) == 3
        and type(event[0]) is int
        and event[0] in {0, 1, 2, 3}
        and event[1] in STAGES
        and event[2] in OUTCOMES
        for event in events
    )
    exported = json.dumps(events)
    assert PRIVATE_CANARY not in exported
    assert "private-terminal-id" not in exported
    assert "private-session-name" not in exported
    assert "private-window-name" not in exported
    assert "/private/native.db" not in exported
    assert "private exception" not in exported


def test_canonicalizer_observes_actual_native_boundaries_without_content_export(
    diagnostic_events, monkeypatch
) -> None:
    provider, receipt = _provider()
    _patch_native_reads(
        monkeypatch,
        native_parts=lambda *_args, **_kwargs: [f"Native answer café\n{receipt}"],
    )
    monkeypatch.setattr(provider, "_config_ownership", lambda: object())
    result = f"Native answer café\n{receipt}"

    canonical = provider.canonicalize_turn_receipt_result(result)

    assert canonical == result
    _assert_private_projection(diagnostic_events)
    assert [event[1:] for event in diagnostic_events] == [
        ("active_receipt_identity", "started"),
        ("active_receipt_identity", "passed"),
        ("native_binding", "started"),
        ("native_binding", "passed"),
        ("native_parts", "started"),
        ("native_parts", "passed"),
        ("native_receipt_identity", "started"),
        ("native_receipt_identity", "passed"),
        ("native_render_guard", "started"),
        ("native_render_guard", "passed"),
    ]


@pytest.mark.parametrize(
    ("failure_stage", "expected_events"),
    [
        (
            "native_binding",
            [
                ("active_receipt_identity", "started"),
                ("active_receipt_identity", "passed"),
                ("native_binding", "started"),
                ("native_binding", "error"),
                ("native_parts", "skip"),
                ("native_receipt_identity", "skip"),
                ("native_render_guard", "skip"),
            ],
        ),
        (
            "native_parts",
            [
                ("active_receipt_identity", "started"),
                ("active_receipt_identity", "passed"),
                ("native_binding", "started"),
                ("native_binding", "passed"),
                ("native_parts", "started"),
                ("native_parts", "error"),
                ("native_receipt_identity", "skip"),
                ("native_render_guard", "skip"),
            ],
        ),
    ],
)
def test_canonicalizer_failure_reports_only_fixed_boundary_and_outcome(
    diagnostic_events, monkeypatch, failure_stage: str, expected_events
) -> None:
    provider, receipt = _provider()
    if failure_stage == "native_binding":
        monkeypatch.setattr(
            opencode_v2_result_binding,
            "read_v2_result_binding",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(PRIVATE_CANARY)),
        )
        monkeypatch.setattr(opencode_v2_store, "_native_parts", MagicMock())
    else:
        _patch_native_reads(
            monkeypatch,
            native_parts=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError(PRIVATE_CANARY)
            ),
        )
    monkeypatch.setattr(provider, "_config_ownership", lambda: object())
    result = f"Rendered answer\n{receipt}"

    with pytest.raises(OutputExtractionError):
        provider.canonicalize_turn_receipt_result(result)

    _assert_private_projection(diagnostic_events)
    assert [event[1:] for event in diagnostic_events] == expected_events


def test_canonicalizer_rejects_invalid_active_receipt_before_any_native_read(
    diagnostic_events, monkeypatch
) -> None:
    provider, _receipt = _provider()
    monkeypatch.setattr(provider, "_config_ownership", lambda: object())

    with pytest.raises(OutputExtractionError):
        provider.canonicalize_turn_receipt_result("Rendered answer\nnot-an-active-receipt")

    assert [event[1:] for event in diagnostic_events] == [
        ("active_receipt_identity", "started"),
        ("active_receipt_identity", "rejected"),
        ("native_binding", "skip"),
        ("native_parts", "skip"),
        ("native_receipt_identity", "skip"),
        ("native_render_guard", "skip"),
    ]
    _assert_private_projection(diagnostic_events)


@pytest.mark.parametrize("sink_enabled", [False, True])
def test_initial_provider_state_exception_keeps_original_type_and_identity(
    monkeypatch, sink_enabled: bool
) -> None:
    provider, receipt = _provider()
    sentinel = ValueError(PRIVATE_CANARY)
    state_reads = []

    def fail_state_read():
        state_reads.append(None)
        raise sentinel

    monkeypatch.setattr(provider, "pending_turn_receipt_state", fail_state_read)
    events: list[tuple[int, str, str]] = []
    previous = ts.set_receipt_verification_diagnostic_sink(
        (lambda *event: events.append(event)) if sink_enabled else None
    )
    try:
        with pytest.raises(ValueError) as caught:
            provider.canonicalize_turn_receipt_result(f"Rendered answer\n{receipt}")
    finally:
        ts.set_receipt_verification_diagnostic_sink(previous)

    assert caught.value is sentinel
    assert len(state_reads) == 1
    if sink_enabled:
        assert [event[1:] for event in events] == [
            ("active_receipt_identity", "started"),
            ("active_receipt_identity", "error"),
            ("native_binding", "skip"),
            ("native_parts", "skip"),
            ("native_receipt_identity", "skip"),
            ("native_render_guard", "skip"),
        ]
    else:
        assert events == []
    assert PRIVATE_CANARY not in json.dumps(events)


@pytest.mark.parametrize(
    ("native_result", "expected_rejected_stage"),
    [
        (
            lambda receipt: [f"Native answer\nCAO-TURN-RECEIPT-{'c' * 32}"],
            "native_receipt_identity",
        ),
        (lambda receipt: [f"Different answer\n{receipt}"], "native_render_guard"),
    ],
)
def test_canonicalizer_distinguishes_receipt_identity_from_render_guard(
    diagnostic_events, monkeypatch, native_result, expected_rejected_stage: str
) -> None:
    provider, receipt = _provider()
    _patch_native_reads(
        monkeypatch,
        native_parts=lambda *_args, **_kwargs: native_result(receipt),
    )
    monkeypatch.setattr(provider, "_config_ownership", lambda: object())
    result = f"Rendered answer\n{receipt}"

    with pytest.raises(OutputExtractionError):
        provider.canonicalize_turn_receipt_result(result)

    _assert_private_projection(diagnostic_events)
    assert (0, expected_rejected_stage, "rejected") in diagnostic_events


def test_slow_native_binding_event_does_not_extend_existing_read_deadline(
    diagnostic_events, monkeypatch
) -> None:
    provider, receipt = _provider()
    _patch_native_reads(
        monkeypatch,
        native_parts=lambda *_args, **_kwargs: [f"Rendered answer\n{receipt}"],
    )
    monkeypatch.setattr(provider, "_config_ownership", lambda: object())
    now = {"value": 10.0}
    monkeypatch.setattr(opencode_module.time, "monotonic", lambda: now["value"])

    def delayed_sink(_ordinal: int, stage: str, outcome: str) -> None:
        diagnostic_events.append((_ordinal, stage, outcome))
        if stage == "native_binding" and outcome == "started":
            now["value"] = 20.0

    previous = ts.set_receipt_verification_diagnostic_sink(delayed_sink)
    try:
        with pytest.raises(OutputExtractionError):
            provider.canonicalize_turn_receipt_result(f"Rendered answer\n{receipt}")
    finally:
        ts.set_receipt_verification_diagnostic_sink(previous)

    assert (0, "native_binding", "passed") in diagnostic_events
    assert (0, "native_parts", "rejected") in diagnostic_events
    assert (0, "native_receipt_identity", "skip") in diagnostic_events
    _assert_private_projection(diagnostic_events)


def test_service_reports_witness_miss_without_entering_canonicalizer(diagnostic_events) -> None:
    provider, receipt = _provider()
    provider.receipt_result_terminal_status = lambda *_args: None
    result = f"Rendered answer\n{receipt}"

    with pytest.raises(TurnResultUnavailableError):
        ts._verify_receipt_bearing_result("private-terminal-id", provider, "frame", result)

    assert (0, "result_extraction", "returned") in diagnostic_events
    assert (0, "receipt_witness", "started") in diagnostic_events
    assert (0, "receipt_witness", "rejected") in diagnostic_events
    assert (0, "canonicalizer", "not_applicable") in diagnostic_events
    assert not any(stage in {"native_binding", "durable_cas"} for _, stage, _ in diagnostic_events)
    _assert_private_projection(diagnostic_events)


@pytest.mark.parametrize(
    ("cas_behavior", "expected_error", "cas_outcome"),
    [
        ("settled", None, "settled"),
        ("conflict", TurnResultUnavailableError, "conflict"),
        ("error", OutputExtractionError, "error"),
    ],
)
def test_service_observes_canonicalizer_and_durable_cas_without_changing_result(
    diagnostic_events, monkeypatch, cas_behavior, expected_error, cas_outcome
) -> None:
    provider, receipt = _provider()
    result = f"Rendered answer café\n{receipt}"
    provider.receipt_result_terminal_status = lambda *_args: TerminalStatus.COMPLETED
    _patch_native_reads(
        monkeypatch,
        native_parts=lambda *_args, **_kwargs: [f"Rendered answer café\n{receipt}"],
    )
    monkeypatch.setattr(provider, "_config_ownership", lambda: object())
    settle = MagicMock()
    if cas_behavior == "settled":
        settle.return_value = True
    elif cas_behavior == "conflict":
        settle.return_value = False
    else:
        settle.side_effect = RuntimeError(PRIVATE_CANARY)
    monkeypatch.setattr(ts, "settle_terminal_turn_receipt_result", settle)
    publish = MagicMock()
    monkeypatch.setattr(ts.status_monitor, "publish_observed_status", publish)

    if expected_error is None:
        value = ts._verify_receipt_bearing_result(
            "private-terminal-id", provider, "completed frame", result
        )
        assert value == "Rendered answer café"
        publish.assert_called_once_with("private-terminal-id", TerminalStatus.COMPLETED)
    else:
        with pytest.raises(expected_error):
            ts._verify_receipt_bearing_result("private-terminal-id", provider, "frame", result)
        publish.assert_not_called()
    settle.assert_called_once()
    assert (0, "canonicalizer", "passed") in diagnostic_events
    assert (0, "durable_cas", "started") in diagnostic_events
    assert (0, "durable_cas", cas_outcome) in diagnostic_events
    _assert_private_projection(diagnostic_events)


@pytest.mark.parametrize("prior_failures", [0, 1, 2])
def test_scheduled_verifier_uses_local_attempt_ordinal_and_no_exception_text(
    diagnostic_events, monkeypatch, prior_failures: int
) -> None:
    terminal_id = f"private-terminal-{prior_failures}"
    provider = MagicMock()
    provider.requires_turn_receipt = True
    provider.pending_turn_receipt_state.return_value = {
        "generation": "a" * 32,
        "receipt_sha256": "b" * 64,
    }
    durable_started = {"attempts": prior_failures, "state": "verifying"}
    monkeypatch.setattr(
        ts, "mark_terminal_turn_verification_started", lambda *_args: durable_started
    )
    monkeypatch.setattr(
        ts,
        "get_output",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OutputExtractionError(PRIVATE_CANARY)),
    )
    monkeypatch.setattr(ts, "_record_receipt_verification_failure", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ts, "_receipt_verification_state", {})
    monkeypatch.setattr(ts, "_receipt_verification_inflight", set())
    monkeypatch.setattr(ts, "_receipt_dispatch_inflight", set())

    class InlineThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(ts.threading, "Thread", InlineThread)
    monkeypatch.setattr(ts.threading, "Timer", lambda *_args, **_kwargs: MagicMock())

    assert ts.schedule_receipt_result_verification(terminal_id, provider) is True

    ordinal = prior_failures + 1
    assert [event[1:] for event in diagnostic_events] == [
        ("attempt", "started"),
        ("result_extraction", "started"),
        ("result_extraction", "unavailable"),
        ("attempt", "unresolved"),
    ]
    assert all(event[0] == ordinal for event in diagnostic_events)
    _assert_private_projection(diagnostic_events)


@pytest.mark.parametrize(
    ("later_state", "attempt_outcome"),
    [
        (None, "verified"),
        ({"generation": "c" * 32, "receipt_sha256": "d" * 64}, "superseded"),
    ],
)
def test_scheduled_verifier_reports_verified_or_superseded_without_extra_read(
    diagnostic_events, monkeypatch, later_state, attempt_outcome: str
) -> None:
    terminal_id = "private-terminal-id"
    provider = MagicMock()
    provider.requires_turn_receipt = True
    initial_state = {"generation": "a" * 32, "receipt_sha256": "b" * 64}
    provider.pending_turn_receipt_state.side_effect = [initial_state, later_state]
    monkeypatch.setattr(
        ts,
        "mark_terminal_turn_verification_started",
        lambda *_args: {"attempts": 0, "state": "verifying"},
    )
    capture = MagicMock(return_value="captured")
    monkeypatch.setattr(ts, "get_output", capture)
    state = {}
    monkeypatch.setattr(ts, "_receipt_verification_state", state)
    monkeypatch.setattr(ts, "_receipt_verification_inflight", set())
    monkeypatch.setattr(ts, "_receipt_dispatch_inflight", set())

    class InlineThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(ts.threading, "Thread", InlineThread)
    monkeypatch.setattr(ts.threading, "Timer", lambda *_args, **_kwargs: MagicMock())

    assert ts.schedule_receipt_result_verification(terminal_id, provider) is True

    capture.assert_called_once()
    assert [event[1:] for event in diagnostic_events] == [
        ("attempt", "started"),
        ("result_extraction", "started"),
        ("result_extraction", "returned"),
        ("attempt", attempt_outcome),
    ]
    assert all(event[0] == 1 for event in diagnostic_events)
    _assert_private_projection(diagnostic_events)


def test_sink_baseexception_cannot_change_canonical_result_or_cas(monkeypatch) -> None:
    provider, receipt = _provider()
    result = f"Rendered answer\n{receipt}"
    provider.receipt_result_terminal_status = lambda *_args: TerminalStatus.COMPLETED
    _patch_native_reads(
        monkeypatch,
        native_parts=lambda *_args, **_kwargs: [f"Rendered answer\n{receipt}"],
    )
    monkeypatch.setattr(provider, "_config_ownership", lambda: object())
    settle = MagicMock(return_value=True)
    monkeypatch.setattr(ts, "settle_terminal_turn_receipt_result", settle)
    monkeypatch.setattr(ts.status_monitor, "publish_observed_status", MagicMock())

    def broken_sink(*_args):
        raise KeyboardInterrupt(PRIVATE_CANARY)

    previous = ts.set_receipt_verification_diagnostic_sink(broken_sink)
    try:
        value = ts._verify_receipt_bearing_result(
            "private-terminal-id", provider, "completed frame", result
        )
    finally:
        ts.set_receipt_verification_diagnostic_sink(previous)

    assert value == "Rendered answer"
    settle.assert_called_once()


def test_producer_baseexception_and_sink_baseexception_preserve_original(monkeypatch) -> None:
    provider, receipt = _provider()
    _patch_native_reads(
        monkeypatch,
        native_parts=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            KeyboardInterrupt("producer-private-exception")
        ),
    )
    monkeypatch.setattr(provider, "_config_ownership", lambda: object())
    previous = ts.set_receipt_verification_diagnostic_sink(
        lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt(PRIVATE_CANARY))
    )
    try:
        with pytest.raises(KeyboardInterrupt, match="producer-private-exception"):
            provider.canonicalize_turn_receipt_result(f"Rendered answer\n{receipt}")
    finally:
        ts.set_receipt_verification_diagnostic_sink(previous)


def test_default_disabled_sink_does_not_change_existing_result(monkeypatch) -> None:
    provider, receipt = _provider()
    provider.receipt_result_terminal_status = lambda *_args: TerminalStatus.COMPLETED
    _patch_native_reads(
        monkeypatch,
        native_parts=lambda *_args, **_kwargs: [f"Rendered answer\n{receipt}"],
    )
    monkeypatch.setattr(provider, "_config_ownership", lambda: object())
    monkeypatch.setattr(ts, "settle_terminal_turn_receipt_result", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ts.status_monitor, "publish_observed_status", MagicMock())
    captured: list[tuple[int, str, str]] = []
    previous = ts.set_receipt_verification_diagnostic_sink(lambda *event: captured.append(event))
    ts.set_receipt_verification_diagnostic_sink(None)

    try:
        result = ts._verify_receipt_bearing_result(
            "private-terminal-id", provider, "completed frame", f"Rendered answer\n{receipt}"
        )
    finally:
        ts.set_receipt_verification_diagnostic_sink(previous)

    assert result == "Rendered answer"
    assert captured == []


def test_sink_receives_no_unlisted_stage_or_outcome(diagnostic_events) -> None:
    emit_receipt_verification_event(PRIVATE_CANARY, "started")
    emit_receipt_verification_event("attempt", PRIVATE_CANARY)

    assert diagnostic_events == []


@pytest.mark.parametrize(
    ("observed_turn", "expected_outcome"),
    [(1, "stale_capture"), (2, "current_capture"), (None, "progress_unconfirmed")],
)
def test_status_admission_is_observed_before_existing_completion_gate(
    diagnostic_events, monkeypatch, observed_turn, expected_outcome: str
) -> None:
    monitor = StatusMonitor()
    terminal_id = "private-terminal-id"
    monitor._turn[terminal_id] = 2
    monitor._turn_started[terminal_id] = True
    gate_events: list[list[tuple[int, str, str]]] = []

    def gate(_terminal_id, _provider, detected):
        gate_events.append(list(diagnostic_events))
        return detected

    monkeypatch.setattr(monitor, "_gate_terminal_completion", gate)
    with monitor._lock:
        monitor._apply_detection_locked(
            terminal_id,
            TerminalStatus.COMPLETED,
            observed_turn=observed_turn,
        )

    assert diagnostic_events == [(0, "admission", expected_outcome)]
    assert gate_events == [[(0, "admission", expected_outcome)]]
    _assert_private_projection(diagnostic_events)
