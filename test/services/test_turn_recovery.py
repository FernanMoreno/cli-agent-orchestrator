"""Recovery must observe or fence a turn, never deliver its task again."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cli_agent_orchestrator.services import terminal_service as ts


def test_ordinary_supervisor_verification_failure_has_durable_diagnostic():
    fingerprint = ("1" * 32, "a" * 64)
    with ts._receipt_verification_lock:
        ts._receipt_verification_state["abcd1234"] = ts._ReceiptVerificationState(fingerprint)
    try:
        with (
            patch.object(
                ts,
                "record_terminal_turn_verification_failure",
                return_value={"state": "reconcile", "attempts": 3},
            ) as durable,
            patch.object(ts, "transition_native_child", return_value=None),
        ):
            ts._record_receipt_verification_failure(
                "abcd1234", fingerprint, reason="no final receipt"
            )
        durable.assert_called_once_with(
            "abcd1234", *fingerprint, reason="receipt_result_unverifiable"
        )
        assert ts._receipt_verification_state["abcd1234"].reconciled
    finally:
        ts._receipt_verification_state.pop("abcd1234", None)


def test_full_transcript_survives_missing_receipt_result():
    provider = MagicMock(requires_turn_receipt=True)
    provider.pending_turn_receipt_state.return_value = {
        "generation": "1" * 32,
        "receipt_sha256": "a" * 64,
    }
    provider.get_status.return_value = ts.TerminalStatus.COMPLETED
    provider.extract_last_message_from_script.side_effect = ValueError("no answer")
    with (
        patch.object(
            ts, "get_terminal_metadata", return_value={"tmux_session": "s", "tmux_window": "w"}
        ),
        patch.object(
            ts.status_monitor, "get_buffer", return_value="final transcript without receipt"
        ),
        patch.object(ts.provider_manager, "get_provider", return_value=provider),
        patch.object(ts, "get_backend") as backend,
    ):
        backend.return_value.get_history.return_value = "final transcript without receipt"
        assert ts.get_output("abcd1234", ts.OutputMode.FULL) == "final transcript without receipt"


@pytest.mark.asyncio
async def test_cancel_stops_old_pane_before_releasing_receipt_and_never_sends_task():
    from cli_agent_orchestrator.services import turn_recovery_service as recovery

    provider = MagicMock()
    provider.initialize = AsyncMock(return_value=True)
    metadata = {
        "provider": "claude_code",
        "tmux_session": "s",
        "tmux_window": "w",
        "agent_profile": "test",
    }
    events = []
    backend = MagicMock()
    backend.reset_window.side_effect = lambda *args, **kwargs: events.append("stopped")
    with (
        patch.object(recovery, "get_terminal_metadata", return_value=metadata),
        patch.object(recovery, "ensure_terminal_is_not_work_owned"),
        patch.object(
            recovery,
            "begin_terminal_turn_cancellation",
            side_effect=lambda *args: events.append("fenced") or True,
        ),
        patch.object(
            recovery,
            "finish_terminal_turn_cancellation",
            side_effect=lambda *args: events.append("cancelled") or True,
        ),
        patch.object(recovery, "get_backend", return_value=backend),
        patch.object(recovery.provider_manager, "get_provider", return_value=provider),
        patch.object(
            recovery, "get_turn", return_value={"state": "cancelled", "generation": "1" * 32}
        ),
        patch.object(recovery, "status_monitor"),
    ):
        result = await recovery.cancel_turn("abcd1234", "1" * 32)
    assert events == ["fenced", "stopped", "cancelled"]
    assert result["state"] == "cancelled"
    backend.reset_window.assert_called_once_with("s", "w", terminal_id="abcd1234")
    backend.send_keys.assert_not_called()
    provider.prepare_input.assert_not_called()
    provider.initialize.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_transport_failure_does_not_release_receipt():
    from cli_agent_orchestrator.services import turn_recovery_service as recovery

    with (
        patch.object(
            recovery,
            "get_terminal_metadata",
            return_value={"tmux_session": "s", "tmux_window": "w"},
        ),
        patch.object(recovery, "ensure_terminal_is_not_work_owned"),
        patch.object(recovery, "begin_terminal_turn_cancellation", return_value=True),
        patch.object(recovery, "finish_terminal_turn_cancellation") as finish,
        patch.object(recovery, "get_backend") as backend,
        patch.object(recovery.provider_manager, "get_provider") as providers,
    ):
        backend.return_value.reset_window.side_effect = RuntimeError("transport uncertain")
        with pytest.raises(RuntimeError, match="transport uncertain"):
            await recovery.cancel_turn("abcd1234", "1" * 32)
    finish.assert_not_called()
    providers.return_value.mark_turn_receipt_result_verified.assert_not_called()


def test_result_persistence_failure_is_not_expected_pending():
    from cli_agent_orchestrator.providers.base import (
        OutputExtractionError,
        TurnResultUnavailableError,
    )

    provider = MagicMock()
    provider.requires_turn_receipt = True
    provider.pending_turn_receipt_state.return_value = {
        "generation": "1" * 32,
        "receipt_sha256": "a" * 64,
    }
    provider.extraction_tail_lines = 20
    provider.extraction_retries = 0
    provider.extract_last_message_from_script.return_value = "answer"
    provider.receipt_result_terminal_status.return_value = ts.TerminalStatus.COMPLETED
    with (
        patch.object(
            ts, "get_terminal_metadata", return_value={"tmux_session": "s", "tmux_window": "w"}
        ),
        patch.object(ts, "get_terminal_turn_recovery", return_value=None),
        patch.object(ts.provider_manager, "get_provider", return_value=provider),
        patch.object(ts, "get_backend"),
        patch.object(
            ts, "settle_terminal_turn_receipt_result", side_effect=RuntimeError("database down")
        ),
    ):
        with pytest.raises(OutputExtractionError) as error:
            ts.get_output("abcd1234", ts.OutputMode.LAST)
    assert not isinstance(error.value, TurnResultUnavailableError)


def test_verification_for_old_generation_never_settles_new_turn():
    from cli_agent_orchestrator.providers.base import TurnResultUnavailableError

    provider = MagicMock()
    provider.requires_turn_receipt = True
    provider.pending_turn_receipt_state.return_value = {
        "generation": "2" * 32,
        "receipt_sha256": "b" * 64,
    }
    with patch.object(ts, "settle_terminal_turn_receipt_result") as settle:
        with pytest.raises(TurnResultUnavailableError, match="generation changed"):
            ts._verify_receipt_bearing_result(
                "abcd1234", provider, "answer", "answer", expected_generation="1" * 32
            )
    settle.assert_not_called()


def test_slow_final_capture_expires_without_starting_any_task():
    fingerprint = ("1" * 32, "a" * 64)
    with ts._receipt_verification_lock:
        ts._receipt_verification_state["abcd1234"] = ts._ReceiptVerificationState(fingerprint)
    try:
        with (
            patch.object(ts, "record_terminal_turn_verification_failure", return_value=None),
            patch.object(ts, "transition_native_child"),
            patch.object(ts.status_monitor, "publish_receipt_reconciliation") as publish,
            patch.object(ts, "send_input") as send,
        ):
            ts._expire_receipt_verification("abcd1234", fingerprint)
        assert ts.receipt_requires_reconciliation("abcd1234")
        publish.assert_called_once_with("abcd1234")
        send.assert_not_called()
    finally:
        ts._receipt_verification_state.pop("abcd1234", None)


@pytest.mark.parametrize(
    "waiting", [ts.TerminalStatus.WAITING_USER_ANSWER, ts.TerminalStatus.WAITING_QUOTA]
)
def test_durable_reconciliation_preserves_live_waiting_dialog(waiting):
    from cli_agent_orchestrator.services.status_monitor import StatusMonitor

    monitor = StatusMonitor()
    with monitor._lock:
        monitor._last_status["abcd1234"] = waiting
    monitor.publish_receipt_reconciliation("abcd1234")
    assert monitor._last_status["abcd1234"] == waiting


def test_tmux_backend_forwards_authoritative_reset_identity():
    from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend

    client = MagicMock()
    backend = TmuxBackend(client=client)
    backend.reset_window("session", "worker", terminal_id="abcd1234")
    client.reset_window.assert_called_once_with("session", "worker", terminal_id="abcd1234")
