"""Contracts for provider-owned per-turn completion receipts.

The terminal service owns delivery ordering; providers own receipt syntax.
These tests keep that division explicit so adding a receipt-capable provider
cannot decorate interactive approval answers, control commands, or a retry
with a fresh nonce.
"""

import hashlib
from unittest.mock import ANY, MagicMock, call, patch

import pytest

from cli_agent_orchestrator.models.terminal import TerminalInputBlockedError, TerminalStatus
from cli_agent_orchestrator.providers.base import OutputExtractionError
from cli_agent_orchestrator.providers.gemini_cli import GeminiCliProvider
from cli_agent_orchestrator.services import terminal_service as ts


@pytest.fixture(autouse=True)
def _reset_receipt_verifier_state():
    """Keep bounded background-verifier evidence isolated per contract test."""
    with ts._receipt_verification_lock:
        ts._receipt_verification_inflight.clear()
        ts._receipt_verification_state.clear()
    yield
    with ts._receipt_verification_lock:
        ts._receipt_verification_inflight.clear()
        ts._receipt_verification_state.clear()


def _receipt_provider() -> MagicMock:
    provider = MagicMock()
    provider.requires_turn_receipt = True
    provider.blocks_new_task_input_for_reconciliation = False
    provider.blocks_orchestrated_input_while_waiting_user_answer = False
    provider.assume_processing_on_dispatch = False
    provider.paste_enter_count = 1
    provider.paste_submit_delay = 0.0
    provider.prepare_input.return_value = "materialized task\n\nCAO_GEMINI_TURN_nonce"
    provider.pending_turn_receipt_state.return_value = {
        "generation": "1" * 32,
        "receipt_sha256": "a" * 64,
    }
    # The shared service now asks each adapter to return the *actual* terminal
    # state that witnesses its receipt (rather than assuming COMPLETED itself).
    # This mock represents a normal provider with a conventional completed
    # renderer; the Codex post-turn dialog has a dedicated WAITING witness.
    provider.receipt_result_terminal_status.side_effect = lambda transcript, _result: (
        TerminalStatus.COMPLETED
        if provider.get_status(transcript) == TerminalStatus.COMPLETED
        else None
    )
    return provider


def _dispatch_with_status(status: TerminalStatus, *, attach_turn_receipt: bool):
    provider = _receipt_provider()
    backend = MagicMock()
    metadata = {"provider": "gemini_cli", "tmux_session": "session", "tmux_window": "window"}

    with (
        patch.object(ts, "get_terminal_metadata", return_value=metadata),
        patch.object(ts, "provider_manager") as manager,
        patch.object(ts.status_monitor, "get_status", return_value=status),
        patch.object(ts, "inject_memory_context", return_value="materialized task"),
        patch.object(ts.status_monitor, "notify_input_sent"),
        patch.object(ts.status_monitor, "clear_rolling_buffer"),
        patch.object(ts, "get_backend", return_value=backend),
        patch.object(ts, "begin_terminal_turn_receipt", return_value={}),
        patch.object(ts, "mark_terminal_turn_receipt_sent", return_value=True),
        patch.object(ts, "update_last_active"),
    ):
        manager.get_provider.return_value = provider
        assert (
            ts.send_input(
                "receipt-terminal", "caller task", attach_turn_receipt=attach_turn_receipt
            )
            is True
        )

    return provider, backend


def test_active_receipt_capable_task_is_prepared_after_memory_materialization():
    """Only a normal model task gets one provider-owned receipt instruction."""
    provider, backend = _dispatch_with_status(TerminalStatus.IDLE, attach_turn_receipt=True)

    provider.prepare_input.assert_called_once_with("materialized task")
    assert backend.send_keys.call_args.args[:3] == (
        "session",
        "window",
        "materialized task\n\nCAO_GEMINI_TURN_nonce",
    )
    provider.mark_input_received.assert_called_once_with()


@pytest.mark.parametrize(
    ("status", "attach_turn_receipt"),
    [
        (TerminalStatus.WAITING_USER_ANSWER, True),
        (TerminalStatus.IDLE, False),
    ],
    ids=["interactive-answer", "terminal-control"],
)
def test_receipt_is_not_attached_to_interactive_answers_or_control_input(
    status: TerminalStatus, attach_turn_receipt: bool
):
    """Trust/login answers and `/quit`-style control input must remain literal."""
    provider, backend = _dispatch_with_status(status, attach_turn_receipt=attach_turn_receipt)

    provider.prepare_input.assert_not_called()
    assert backend.send_keys.call_args.args[:3] == ("session", "window", "materialized task")


def test_restored_private_receipt_blocks_new_task_before_any_tui_or_provider_side_effect():
    """A restarted Gemini worker must reconcile its durable prior turn first."""
    provider = _receipt_provider()
    provider.blocks_new_task_input_for_reconciliation = True
    backend = MagicMock()
    metadata = {"provider": "gemini_cli", "tmux_session": "session", "tmux_window": "window"}

    with (
        patch.object(ts, "get_terminal_metadata", return_value=metadata),
        patch.object(ts, "provider_manager") as manager,
        patch.object(ts.status_monitor, "get_status", return_value=TerminalStatus.IDLE),
        patch.object(ts, "inject_memory_context") as inject,
        patch.object(ts, "begin_terminal_turn_receipt") as begin,
        patch.object(ts, "get_backend", return_value=backend),
        patch.object(ts.status_monitor, "notify_input_sent") as notify,
        patch.object(ts.status_monitor, "clear_rolling_buffer") as clear,
    ):
        manager.get_provider.return_value = provider
        with pytest.raises(TerminalInputBlockedError, match="active receipt-bearing task"):
            ts.send_input("receipt-terminal", "new task")

    provider.prepare_input.assert_not_called()
    inject.assert_not_called()
    begin.assert_not_called()
    notify.assert_not_called()
    clear.assert_not_called()
    backend.send_keys.assert_not_called()


def test_automated_task_delivery_does_not_type_into_a_waiting_provider_dialog():
    """run-step-like dispatch is not an interactive answer to a Gemini trust dialog."""
    provider = _receipt_provider()
    provider.blocks_orchestrated_input_while_waiting_user_answer = True
    backend = MagicMock()
    metadata = {"provider": "gemini_cli", "tmux_session": "session", "tmux_window": "window"}

    with (
        patch.object(ts, "get_terminal_metadata", return_value=metadata),
        patch.object(ts, "provider_manager") as manager,
        patch.object(
            ts.status_monitor,
            "get_status",
            return_value=TerminalStatus.WAITING_USER_ANSWER,
        ),
        patch.object(ts, "get_backend", return_value=backend),
    ):
        manager.get_provider.return_value = provider
        with pytest.raises(TerminalInputBlockedError, match="waiting for a user answer"):
            ts.send_input("receipt-terminal", "automated task", task_delivery=True)

    provider.prepare_input.assert_not_called()
    backend.send_keys.assert_not_called()


def test_receipt_claim_is_persisted_before_paste_and_marked_sent_afterward():
    """The DB transition brackets the only side effect that can execute a task."""
    provider = _receipt_provider()
    backend = MagicMock()
    metadata = {"provider": "gemini_cli", "tmux_session": "session", "tmux_window": "window"}
    events: list[str] = []

    def begin(*_args, **_kwargs):
        events.append("prepared")
        return {"phase": "prepared"}

    def paste(*_args, **_kwargs):
        events.append("pasted")

    def mark(*_args, **_kwargs):
        events.append("sent")
        return True

    backend.send_keys.side_effect = paste
    with (
        patch.object(ts, "get_terminal_metadata", return_value=metadata),
        patch.object(ts, "provider_manager") as manager,
        patch.object(ts.status_monitor, "get_status", return_value=TerminalStatus.IDLE),
        patch.object(ts, "inject_memory_context", return_value="materialized task"),
        patch.object(ts, "begin_terminal_turn_receipt", side_effect=begin) as claim,
        patch.object(ts, "mark_terminal_turn_receipt_sent", side_effect=mark) as sent,
        patch.object(ts.status_monitor, "notify_input_sent"),
        patch.object(ts.status_monitor, "clear_rolling_buffer"),
        patch.object(ts, "get_backend", return_value=backend),
        patch.object(ts, "update_last_active"),
    ):
        manager.get_provider.return_value = provider
        assert ts.send_input("receipt-terminal", "caller task") is True

    receipt_state = provider.pending_turn_receipt_state.return_value
    claim.assert_called_once_with(
        "receipt-terminal",
        "gemini_cli",
        receipt_state["generation"],
        receipt_state["receipt_sha256"],
    )
    sent.assert_called_once_with(
        "receipt-terminal", receipt_state["generation"], receipt_state["receipt_sha256"]
    )
    assert events == ["prepared", "pasted", "sent"]
    provider.mark_turn_receipt_sent.assert_called_once_with()


def test_receipt_task_send_error_after_paste_requires_reconciliation_not_retry():
    """A tmux error cannot prove that its bracketed paste was not accepted."""
    provider = _receipt_provider()
    backend = MagicMock()
    backend.send_keys.side_effect = OSError("tmux connection dropped after write")
    metadata = {"provider": "gemini_cli", "tmux_session": "session", "tmux_window": "window"}

    with (
        patch.object(ts, "get_terminal_metadata", return_value=metadata),
        patch.object(ts, "provider_manager") as manager,
        patch.object(ts.status_monitor, "get_status", return_value=TerminalStatus.IDLE),
        patch.object(ts, "inject_memory_context", return_value="materialized task"),
        patch.object(ts, "begin_terminal_turn_receipt", return_value={"phase": "prepared"}),
        patch.object(ts.status_monitor, "notify_input_sent"),
        patch.object(ts.status_monitor, "clear_rolling_buffer"),
        patch.object(ts, "get_backend", return_value=backend),
        patch.object(ts, "mark_terminal_turn_receipt_sent") as mark_sent,
    ):
        manager.get_provider.return_value = provider
        with pytest.raises(TerminalInputBlockedError, match="may have accepted input") as exc_info:
            ts.send_input("receipt-terminal", "caller task")

    assert exc_info.value.action == "reconcile"
    assert exc_info.value.delivery_may_have_occurred is True
    backend.send_keys.assert_called_once()
    mark_sent.assert_not_called()


def test_nonreceipt_task_send_error_after_paste_requires_reconciliation_not_retry():
    """Transport ambiguity protects every automated provider, not just Gemini."""
    provider = MagicMock()
    provider.requires_turn_receipt = False
    provider.blocks_orchestrated_input_while_waiting_user_answer = False
    provider.assume_processing_on_dispatch = False
    provider.paste_enter_count = 1
    provider.paste_submit_delay = 0.0
    backend = MagicMock()
    backend.send_keys.side_effect = OSError("tmux connection dropped after write")
    metadata = {"provider": "codex", "tmux_session": "session", "tmux_window": "window"}

    with (
        patch.object(ts, "get_terminal_metadata", return_value=metadata),
        patch.object(ts, "provider_manager") as manager,
        patch.object(ts.status_monitor, "get_status", return_value=TerminalStatus.IDLE),
        patch.object(ts, "inject_memory_context", return_value="materialized task"),
        patch.object(ts.status_monitor, "notify_input_sent"),
        patch.object(ts.status_monitor, "clear_rolling_buffer"),
        patch.object(ts, "get_backend", return_value=backend),
    ):
        manager.get_provider.return_value = provider
        with pytest.raises(TerminalInputBlockedError, match="may have accepted input") as exc_info:
            ts.send_input("ordinary-terminal", "caller task", task_delivery=True)

    assert exc_info.value.action == "reconcile"
    assert exc_info.value.delivery_may_have_occurred is True
    backend.send_keys.assert_called_once()


def test_verified_gemini_result_persists_only_a_digest_then_releases_the_task_slot():
    """Output text is never durable receipt state; its SHA-256 is the CAS witness."""
    provider = _receipt_provider()
    provider.get_status.return_value = TerminalStatus.COMPLETED
    receipt_state = provider.pending_turn_receipt_state.return_value
    result = "review complete: no regressions found"

    with (
        patch.object(ts, "settle_terminal_turn_receipt_result", return_value=True) as settle,
        patch.object(ts.status_monitor, "publish_observed_status") as publish,
    ):
        assert (
            ts._verify_receipt_bearing_result(
                "receipt-terminal", provider, "settled transcript", result
            )
            == result
        )

    settle.assert_called_once_with(
        "receipt-terminal",
        receipt_state["generation"],
        receipt_state["receipt_sha256"],
        hashlib.sha256(result.encode("utf-8")).hexdigest(),
    )
    provider.mark_turn_receipt_result_verified.assert_called_once_with()
    publish.assert_called_once_with("receipt-terminal", TerminalStatus.COMPLETED)


def test_background_receipt_verifier_relies_on_atomic_receipt_settlement():
    """Visual completion never performs a second child-success transition."""
    provider = _receipt_provider()
    pending = provider.pending_turn_receipt_state.return_value
    state = {"receipt": pending}
    provider.pending_turn_receipt_state.side_effect = lambda: state["receipt"]

    def settle_output(*_args, **_kwargs):
        state["receipt"] = None
        return "completed transcript"

    class _InlineThread:
        def __init__(self, *, target, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    with (
        patch.object(ts, "get_output", side_effect=settle_output) as output,
        patch.object(ts, "transition_native_child") as transition,
        patch.object(ts.threading, "Thread", _InlineThread),
    ):
        assert ts.schedule_receipt_result_verification("receipt-terminal", provider) is True

    output.assert_called_once_with("receipt-terminal", ts.OutputMode.FULL)
    transition.assert_not_called()


def test_background_receipt_verifier_backs_off_then_reconciles_an_unverifiable_result(monkeypatch):
    """Repeated visual markers cannot spawn an unbounded full-capture storm."""
    provider = _receipt_provider()
    now = {"value": 100.0}
    monkeypatch.setattr(ts.time, "monotonic", lambda: now["value"])

    class _InlineThread:
        def __init__(self, *, target, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    with (
        patch.object(
            ts,
            "get_output",
            side_effect=OutputExtractionError("answer not ready"),
        ) as output,
        patch.object(ts, "transition_native_child") as transition,
        patch.object(ts.threading, "Thread", _InlineThread),
    ):
        assert ts.schedule_receipt_result_verification("receipt-terminal", provider) is True
        # The second visual poll is within the exponential cooldown, so it
        # gates completion but does not invoke another full-history capture.
        assert ts.schedule_receipt_result_verification("receipt-terminal", provider) is True
        now["value"] += ts._RECEIPT_VERIFICATION_RETRY_BASE_SECONDS
        assert ts.schedule_receipt_result_verification("receipt-terminal", provider) is True
        now["value"] += ts._RECEIPT_VERIFICATION_RETRY_BASE_SECONDS * 2
        assert ts.schedule_receipt_result_verification("receipt-terminal", provider) is True
        # The bounded third failure persists reconcile. Later polls keep the
        # visual completion gated but cannot fork/capture again.
        assert ts.schedule_receipt_result_verification("receipt-terminal", provider) is True

    assert output.call_count == ts._RECEIPT_VERIFICATION_MAX_AUTOMATIC_ATTEMPTS
    transition.assert_called_once_with(
        "receipt-terminal",
        "reconcile",
        error_kind="receipt_result_unverifiable",
        error_summary=ANY,
    )


def test_full_output_settles_a_completed_receipt_before_returning_the_transcript():
    """The default output endpoint cannot strand a completed Gemini turn."""
    provider = _receipt_provider()
    provider.get_status.return_value = TerminalStatus.COMPLETED
    provider.extract_last_message_from_script.return_value = "review complete"
    receipt_state = provider.pending_turn_receipt_state.return_value
    metadata = {"provider": "gemini_cli", "tmux_session": "session", "tmux_window": "window"}

    with (
        patch.object(ts, "get_terminal_metadata", return_value=metadata),
        patch.object(ts.status_monitor, "get_buffer", return_value="settled transcript"),
        patch.object(ts, "provider_manager") as manager,
        patch.object(ts, "settle_terminal_turn_receipt_result", return_value=True) as settle,
    ):
        manager.get_provider.return_value = provider
        assert ts.get_output("receipt-terminal", ts.OutputMode.FULL) == "settled transcript"

    provider.extract_last_message_from_script.assert_called_once_with("settled transcript")
    settle.assert_called_once_with(
        "receipt-terminal",
        receipt_state["generation"],
        receipt_state["receipt_sha256"],
        hashlib.sha256(b"review complete").hexdigest(),
    )
    provider.mark_turn_receipt_result_verified.assert_called_once_with()


def test_full_output_recovers_a_truncated_query_from_history_before_settling_receipt():
    """A tail with the receipt but no user echo must not strand a completed task."""
    provider = _receipt_provider()
    provider.get_status.return_value = TerminalStatus.COMPLETED
    provider.extract_last_message_from_script.side_effect = [
        ValueError("No Gemini CLI user query found in terminal output"),
        "recovered review result",
    ]
    receipt_state = provider.pending_turn_receipt_state.return_value
    metadata = {"provider": "gemini_cli", "tmux_session": "session", "tmux_window": "window"}
    backend = MagicMock()
    backend.get_history.return_value = "> original query\n✦ recovered review result"

    with (
        patch.object(ts, "get_terminal_metadata", return_value=metadata),
        patch.object(ts.status_monitor, "get_buffer", return_value="receipt-only tail"),
        patch.object(ts, "provider_manager") as manager,
        patch.object(ts, "get_backend", return_value=backend),
        patch.object(ts, "settle_terminal_turn_receipt_result", return_value=True) as settle,
    ):
        manager.get_provider.return_value = provider
        assert ts.get_output("receipt-terminal", ts.OutputMode.FULL) == "receipt-only tail"

    provider.extract_last_message_from_script.assert_has_calls(
        [
            call("receipt-only tail"),
            call("> original query\n✦ recovered review result"),
        ]
    )
    backend.get_history.assert_called_once_with("session", "window", tail_lines=200)
    settle.assert_called_once_with(
        "receipt-terminal",
        receipt_state["generation"],
        receipt_state["receipt_sha256"],
        hashlib.sha256(b"recovered review result").hexdigest(),
    )
    provider.mark_turn_receipt_result_verified.assert_called_once_with()


def test_full_output_verifies_a_restored_gemini_receipt_from_complete_history():
    """A restart keeps no prompt plaintext, but can settle an evidenced result."""
    terminal_id = "receipt-terminal"
    generation = "1" * 32
    receipt = "CAO_GEMINI_TURN_" + "a" * 32
    receipt_sha256 = hashlib.sha256(receipt.encode("utf-8")).hexdigest()
    provider = GeminiCliProvider(terminal_id, "session", "window")
    provider.restore_turn_receipt_state(
        {"generation": generation, "receipt_sha256": receipt_sha256, "phase": "sent"}
    )
    transcript = (
        "> recovered task after CAO restart\n"
        "CAO completion receipt requirement:\n"
        f"{receipt}\n"
        "Responding with gemini-2.5-pro\n"
        "✦ recovered final result\n"
        f"✦ {receipt}\n"
        ">"
    )
    metadata = {"provider": "gemini_cli", "tmux_session": "session", "tmux_window": "window"}

    with (
        patch.object(ts, "get_terminal_metadata", return_value=metadata),
        patch.object(ts.status_monitor, "get_buffer", return_value=transcript),
        patch.object(ts, "provider_manager") as manager,
        patch.object(ts, "settle_terminal_turn_receipt_result", return_value=True) as settle,
    ):
        manager.get_provider.return_value = provider
        assert ts.get_output(terminal_id, ts.OutputMode.FULL) == transcript

    settle.assert_called_once_with(
        terminal_id,
        generation,
        receipt_sha256,
        hashlib.sha256(b"recovered final result").hexdigest(),
    )
    assert provider.pending_turn_receipt_state() is None


def test_unverified_gemini_result_keeps_the_task_slot_blocked_for_reconciliation():
    """A CAS miss may be another recovery actor, never permission to send again."""
    provider = _receipt_provider()
    provider.get_status.return_value = TerminalStatus.COMPLETED

    with patch.object(ts, "settle_terminal_turn_receipt_result", return_value=False):
        with pytest.raises(OutputExtractionError, match="receipt changed before verification"):
            ts._verify_receipt_bearing_result(
                "receipt-terminal", provider, "settled transcript", "candidate answer"
            )

    provider.mark_turn_receipt_result_verified.assert_not_called()


def test_receipt_governed_turn_never_full_re_pastes_when_its_composer_is_absent():
    """An absent Gemini composer is ambiguous, so recovery must reconcile instead.

    Reusing a nonce would prevent a second *receipt*, but it would still let a
    worker execute the same side-effecting prompt twice.  The only safe retry
    for a receipt-governed turn is a bare Enter while the exact prepared input
    remains visibly in the composer.
    """
    provider = _receipt_provider()
    prepared = "materialized task\n\nCAO_GEMINI_TURN_original"
    provider.prepared_input_for_redelivery.return_value = prepared

    with (
        patch.object(ts, "_message_visible_in_box", return_value=False) as box,
        patch.object(ts, "send_special_key") as send_key,
        patch.object(ts, "send_input") as send,
    ):
        assert ts.redeliver_dropped_message("receipt-terminal", "caller task", 1, provider) is False

    box.assert_called_once_with("receipt-terminal", prepared)
    send_key.assert_not_called()
    send.assert_not_called()


def test_receipt_governed_turn_can_submit_only_its_exact_visible_composer():
    """The one safe retry is Enter over the exact prepared prompt already typed."""
    provider = _receipt_provider()
    prepared = "materialized task\n\nCAO_GEMINI_TURN_original"
    provider.prepared_input_for_redelivery.return_value = prepared

    with (
        patch.object(ts, "_message_visible_in_box", return_value=True) as box,
        patch.object(ts, "send_special_key") as send_key,
        patch.object(ts, "send_input") as send,
    ):
        assert ts.redeliver_dropped_message("receipt-terminal", "caller task", 1, provider) is False

    box.assert_called_once_with("receipt-terminal", prepared)
    send_key.assert_called_once_with("receipt-terminal", "Enter")
    send.assert_not_called()


def test_visible_composer_enter_transport_error_requires_reconciliation():
    """A failed submit key can still have been accepted by every provider backend."""
    provider = _receipt_provider()
    prepared = "materialized task\n\nCAO_GEMINI_TURN_original"
    provider.prepared_input_for_redelivery.return_value = prepared

    with (
        patch.object(ts, "_message_visible_in_box", return_value=True),
        patch.object(ts, "send_special_key", side_effect=OSError("tmux closed after Enter")),
        patch.object(ts, "send_input") as send,
    ):
        with pytest.raises(TerminalInputBlockedError, match="may have accepted") as exc_info:
            ts.redeliver_dropped_message("receipt-terminal", "caller task", 1, provider)

    assert exc_info.value.action == "reconcile"
    assert exc_info.value.delivery_may_have_occurred is True
    send.assert_not_called()
