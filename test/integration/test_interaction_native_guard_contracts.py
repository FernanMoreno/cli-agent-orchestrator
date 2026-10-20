"""Native provider dialogs remain a physical input fence until exact closure.

These tests use the real PostgreSQL interaction ledger and terminal input service.
Provider and backend effects are fakes; the provider reports only bounded
``manual_signal`` fingerprints and never supplies answer content.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from test.integration.test_interaction_explicit_flow import interaction_http_context  # noqa: F401
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def _install_native_runtime(monkeypatch, context):
    from cli_agent_orchestrator.models.terminal import TerminalStatus
    from cli_agent_orchestrator.providers.mcp_interactions import NativeInteractionObservation
    from cli_agent_orchestrator.services import remote_terminal_service, terminal_service
    from cli_agent_orchestrator.services.status_monitor import StatusMonitor

    metadata = {
        "session_incarnation_id": context.incarnation_id,
        "tmux_session": context.session_name,
        "tmux_window": "worker",
        "provider": "codex",
    }
    backend = MagicMock()
    provider = MagicMock()
    provider.requires_turn_receipt = False
    provider.assume_processing_on_dispatch = False
    provider.paste_enter_count = 1
    provider.paste_submit_delay = 0.0
    provider.blocks_new_task_input_for_reconciliation = False

    def manual_signal(_status, screen_lines):
        return NativeInteractionObservation.manual_signal(
            screen_lines=screen_lines,
            terminal_id=context.terminal_id,
            session_name=context.session_name,
            window_name="worker",
        )

    provider.observe_native_interaction.side_effect = manual_signal
    monitor = StatusMonitor()
    monkeypatch.setattr(terminal_service, "_interaction_service", lambda: context.service)
    monkeypatch.setattr(terminal_service, "get_terminal_metadata", lambda _id: metadata)
    monkeypatch.setattr(terminal_service, "get_backend", lambda: backend)
    monkeypatch.setattr(terminal_service, "ensure_terminal_is_not_work_owned", lambda _id: None)
    monkeypatch.setattr(terminal_service, "update_last_active", lambda _id: None)
    monkeypatch.setattr(terminal_service, "inject_memory_context", lambda message, *_args: message)
    monkeypatch.setattr(terminal_service.provider_manager, "get_provider", lambda _id: provider)
    monkeypatch.setattr(terminal_service, "status_monitor", monitor)
    monkeypatch.setattr(monitor, "get_status", lambda _id: TerminalStatus.IDLE)
    monkeypatch.setattr(monitor, "notify_input_sent", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(monitor, "notify_input_delivered", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(monitor, "clear_rolling_buffer", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(monitor, "abort_turn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _id: None)
    return terminal_service, monitor, provider, backend, metadata


def _observe_native(context, monitor, provider, screen_lines):
    from cli_agent_orchestrator.models.terminal import TerminalStatus

    monitor._observe_native_interaction(
        context.terminal_id,
        provider,
        TerminalStatus.WAITING_USER_ANSWER,
        screen_lines,
    )
    with context.service.repository.read_connection() as connection:
        row = connection.execute(
            "SELECT question_id,observation_id,dialog_fingerprint,provider_evidence_version,"
            "turn_generation,lifecycle FROM interaction_questions WHERE terminal_id=%s "
            "AND incarnation_id=%s AND source_kind='native' ORDER BY question_sequence DESC LIMIT 1",
            (context.terminal_id, context.incarnation_id),
        ).fetchone()
    assert row is not None
    return row


def _question_lifecycle(context, question_id):
    with context.service.repository.read_connection() as connection:
        row = connection.execute(
            "SELECT lifecycle FROM interaction_questions WHERE question_id=%s", (question_id,)
        ).fetchone()
    assert row is not None
    return row["lifecycle"]


def _answer_count(context):
    with context.service.repository.read_connection() as connection:
        row = connection.execute("SELECT count(*) FROM interaction_answers").fetchone()
    return int(row[0])


def _close_on_idle(context, monitor, provider, screen_lines):
    from cli_agent_orchestrator.models.terminal import TerminalStatus

    monitor._observe_native_interaction(
        context.terminal_id, provider, TerminalStatus.IDLE, screen_lines
    )
    monitor._observe_native_interaction(
        context.terminal_id, provider, TerminalStatus.IDLE, screen_lines
    )


def _advance_native_dialog_over_two_websocket_inputs(
    context, monitor, provider, original_screen, *, key_prefix
):
    from cli_agent_orchestrator.models.terminal import TerminalStatus
    from cli_agent_orchestrator.services import terminal_service as terminal_module

    generation = None
    for index in (1, 2):
        generation = terminal_module.record_direct_interaction_input(
            context.terminal_id,
            expected_incarnation_id=context.incarnation_id,
            idempotency_key=f"{key_prefix}-websocket-input-000{index}",
        )
        monitor._observe_native_interaction(
            context.terminal_id,
            provider,
            TerminalStatus.WAITING_USER_ANSWER,
            original_screen,
        )
    assert generation is not None
    return generation


def _assert_task_and_key_blocked(terminal_service, backend, context):
    from cli_agent_orchestrator.models.terminal import TerminalInputBlockedError

    with pytest.raises(TerminalInputBlockedError):
        terminal_service.send_input(
            context.terminal_id,
            "SYNTHETIC_TASK_MUST_NOT_REACH_NATIVE_DIALOG",
            task_delivery=True,
        )
    with pytest.raises(TerminalInputBlockedError):
        terminal_service.send_special_key(context.terminal_id, "Enter")
    backend.send_keys.assert_not_called()
    backend.send_special_key.assert_not_called()


@pytest.mark.parametrize("dispatch_kind", ["task", "key"])
def test_unresolved_native_notice_blocks_task_and_key_before_bytes_or_generation(
    interaction_http_context, monkeypatch, dispatch_kind
):
    from cli_agent_orchestrator.models.terminal import TerminalInputBlockedError

    context = interaction_http_context
    terminal_service, monitor, provider, backend, _metadata = _install_native_runtime(
        monkeypatch, context
    )
    notice = _observe_native(
        context, monitor, provider, ["Synthetic provider dialog still needs manual action."]
    )
    before = context.service.source_status(context.terminal_id, context.incarnation_id)
    assert before.current_generation == notice["turn_generation"]
    assert context.service.has_unresolved_native_notice(context.terminal_id, context.incarnation_id)

    with pytest.raises(TerminalInputBlockedError):
        if dispatch_kind == "task":
            terminal_service.send_input(
                context.terminal_id,
                "SYNTHETIC_TASK_MUST_NOT_REACH_NATIVE_DIALOG",
                task_delivery=True,
            )
        else:
            terminal_service.send_special_key(context.terminal_id, "Enter")

    after = context.service.source_status(context.terminal_id, context.incarnation_id)
    assert after.current_generation == before.current_generation
    assert _question_lifecycle(context, notice["question_id"]) == "manual_only"
    assert _answer_count(context) == 0
    backend.send_keys.assert_not_called()
    backend.send_special_key.assert_not_called()


@pytest.mark.parametrize("terminal_state", ["expired", "cancelled"])
def test_ttl_or_cancel_is_not_native_closure_and_exact_idle_close_is_event_only(
    interaction_http_context, monkeypatch, terminal_state
):
    from cli_agent_orchestrator.models.terminal import TerminalInputBlockedError
    from cli_agent_orchestrator.services.interactions import service as service_module

    context = interaction_http_context
    terminal_service, monitor, provider, backend, _metadata = _install_native_runtime(
        monkeypatch, context
    )
    notice = _observe_native(
        context, monitor, provider, ["Synthetic dialog requiring manual confirmation."]
    )
    generation = int(notice["turn_generation"])
    if terminal_state == "expired":
        now = service_module._now()
        monkeypatch.setattr(service_module, "_now", lambda: now + 86_400)
    else:
        context.service.cancel(
            context.owner,
            notice["question_id"],
            1,
            "The operator cancelled this synthetic prompt.",
        )

    waited = asyncio.run(
        context.service.wait_for_native_manual(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=0,
            expected_generation=generation,
        )
    )
    assert waited.state == terminal_state
    assert _question_lifecycle(context, notice["question_id"]) == terminal_state
    assert context.service.has_unresolved_native_notice(context.terminal_id, context.incarnation_id)
    before = context.service.source_status(context.terminal_id, context.incarnation_id)
    with pytest.raises(TerminalInputBlockedError):
        terminal_service.send_special_key(context.terminal_id, "Enter")
    assert (
        context.service.source_status(
            context.terminal_id, context.incarnation_id
        ).current_generation
        == before.current_generation
    )
    backend.send_special_key.assert_not_called()

    _close_on_idle(context, monitor, provider, ["Stable synthetic ready viewport."])
    assert _question_lifecycle(context, notice["question_id"]) == terminal_state
    assert not context.service.has_unresolved_native_notice(
        context.terminal_id, context.incarnation_id
    )
    assert (
        asyncio.run(
            context.service.wait_for_native_manual(
                context.terminal_id,
                context.incarnation_id,
                timeout_seconds=0,
                expected_generation=generation,
            )
        ).state
        == terminal_state
    )
    assert _answer_count(context) == 0
    with context.service.repository.read_connection() as connection:
        closure = connection.execute(
            "SELECT event_type FROM interaction_events WHERE question_id=%s "
            "AND event_type='interaction.native_closed'",
            (notice["question_id"],),
        ).fetchone()
    assert closure is not None


def test_reconcile_after_ttl_reloads_expired_notice_without_revival(
    interaction_http_context, monkeypatch
):
    """Expiry during reconciliation stays terminal and creates no answer."""
    from cli_agent_orchestrator.services.interactions import service as service_module

    context = interaction_http_context
    _terminal_service, monitor, provider, _backend, _metadata = _install_native_runtime(
        monkeypatch, context
    )
    notice = _observe_native(
        context, monitor, provider, ["Synthetic prompt expires before reconciliation."]
    )
    with context.service.repository.read_connection() as connection:
        expires_at = connection.execute(
            "SELECT expires_at FROM interaction_questions WHERE question_id=%s",
            (notice["question_id"],),
        ).fetchone()["expires_at"]
    monkeypatch.setattr(service_module, "_now", lambda: float(expires_at) + 1)

    # Do not call source_status, has_unresolved_native_notice, or another
    # expiry path between advancing time and reconciling the captured evidence.
    receipt = context.service.reconcile_native_observation(
        context.terminal_id,
        context.incarnation_id,
        int(notice["turn_generation"]),
        observation_id=notice["observation_id"],
        dialog_fingerprint=notice["dialog_fingerprint"],
        provider_evidence_version=notice["provider_evidence_version"],
    )
    assert receipt.lifecycle == "expired"
    assert _question_lifecycle(context, notice["question_id"]) == "expired"
    assert (
        asyncio.run(
            context.service.wait_for_native_manual(
                context.terminal_id,
                context.incarnation_id,
                timeout_seconds=0,
                expected_generation=int(notice["turn_generation"]),
            )
        ).state
        == "expired"
    )
    assert _answer_count(context) == 0
    with context.service.repository.read_connection() as connection:
        closure = connection.execute(
            "SELECT event_type FROM interaction_events WHERE question_id=%s "
            "AND event_type='interaction.native_closed'",
            (notice["question_id"],),
        ).fetchone()
    assert closure is not None


@pytest.mark.asyncio
async def test_two_websocket_advances_keep_same_viewport_waiting_until_exact_closure(
    interaction_http_context, monkeypatch
):
    from cli_agent_orchestrator.models.terminal import TerminalStatus
    from cli_agent_orchestrator.services import terminal_service as terminal_module

    context = interaction_http_context
    terminal_service, monitor, provider, _backend, _metadata = _install_native_runtime(
        monkeypatch, context
    )
    original_screen = ["Synthetic native dialog remains visible across manual key frames."]
    notice = _observe_native(context, monitor, provider, original_screen)
    initial_generation = int(notice["turn_generation"])

    waiter = asyncio.create_task(
        context.service.wait_for_native_manual(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=5,
            expected_generation=initial_generation,
        )
    )
    await asyncio.sleep(0.05)
    first_generation = terminal_module.record_direct_interaction_input(
        context.terminal_id,
        expected_incarnation_id=context.incarnation_id,
        idempotency_key="native-ws-direct-input-advance-0001",
    )
    assert first_generation == initial_generation + 1
    monitor._observe_native_interaction(
        context.terminal_id,
        provider,
        TerminalStatus.WAITING_USER_ANSWER,
        original_screen,
    )
    second_generation = terminal_module.record_direct_interaction_input(
        context.terminal_id,
        expected_incarnation_id=context.incarnation_id,
        idempotency_key="native-ws-direct-input-advance-0002",
    )
    assert second_generation == initial_generation + 2
    monitor._observe_native_interaction(
        context.terminal_id,
        provider,
        TerminalStatus.WAITING_USER_ANSWER,
        original_screen,
    )
    await asyncio.sleep(0.05)

    assert not waiter.done()
    assert context.service.has_unresolved_native_notice(context.terminal_id, context.incarnation_id)
    assert _question_lifecycle(context, notice["question_id"]) == "stale"
    with context.service.repository.read_connection() as connection:
        native_count = connection.execute(
            "SELECT count(*) FROM interaction_questions WHERE terminal_id=%s AND incarnation_id=%s "
            "AND source_kind='native'",
            (context.terminal_id, context.incarnation_id),
        ).fetchone()[0]
    assert native_count == 1

    closed_viewport = ["Synthetic stable viewport after exact dialog closure."]
    monitor._observe_native_interaction(
        context.terminal_id, provider, TerminalStatus.IDLE, closed_viewport
    )
    assert not waiter.done()
    assert _question_lifecycle(context, notice["question_id"]) == "stale"
    monitor._observe_native_interaction(
        context.terminal_id, provider, TerminalStatus.IDLE, closed_viewport
    )
    released = await asyncio.wait_for(waiter, timeout=1)

    assert released.state == "released"
    assert released.lifecycle == "reconciled_external"
    assert released.question_id == notice["question_id"]
    assert released.observation_id == notice["observation_id"]
    assert released.current_generation == second_generation
    assert _question_lifecycle(context, notice["question_id"]) == "reconciled_external"
    assert not context.service.has_unresolved_native_notice(
        context.terminal_id, context.incarnation_id
    )
    assert _answer_count(context) == 0


@pytest.mark.parametrize("reopen_same_dialog", [False, True], ids=["new-dialog", "reopened-dialog"])
def test_new_or_reopened_dialog_makes_old_native_wait_stale(
    interaction_http_context, monkeypatch, reopen_same_dialog
):
    from cli_agent_orchestrator.services import terminal_service as terminal_module

    context = interaction_http_context
    _terminal_service, monitor, provider, _backend, _metadata = _install_native_runtime(
        monkeypatch, context
    )
    original_screen = ["Synthetic original approval dialog."]
    old_notice = _observe_native(context, monitor, provider, original_screen)
    old_generation = int(old_notice["turn_generation"])
    new_generation = terminal_module.record_direct_interaction_input(
        context.terminal_id,
        expected_incarnation_id=context.incarnation_id,
        idempotency_key="native-reopen-direct-input-0001",
    )
    assert new_generation == old_generation + 1

    _close_on_idle(context, monitor, provider, ["Synthetic provider composer after closure."])
    assert _question_lifecycle(context, old_notice["question_id"]) == "reconciled_external"

    current_screen = (
        original_screen
        if reopen_same_dialog
        else ["Synthetic different dialog with a new exact fingerprint."]
    )
    new_notice = _observe_native(context, monitor, provider, current_screen)
    assert new_notice["question_id"] != old_notice["question_id"]
    assert new_notice["turn_generation"] == new_generation

    old_wait = asyncio.run(
        context.service.wait_for_native_manual(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=0,
            expected_generation=old_generation,
        )
    )
    assert old_wait.state == "stale"
    assert old_wait.question_id == old_notice["question_id"]
    assert _question_lifecycle(context, new_notice["question_id"]) == "manual_only"
    assert context.service.has_unresolved_native_notice(context.terminal_id, context.incarnation_id)
    assert _answer_count(context) == 0


def test_non_websocket_advance_chain_closes_physically_but_never_releases_old_wait(
    interaction_http_context, monkeypatch
):
    from cli_agent_orchestrator.services import terminal_service as terminal_module

    context = interaction_http_context
    _terminal_service, monitor, provider, _backend, _metadata = _install_native_runtime(
        monkeypatch, context
    )
    notice = _observe_native(
        context, monitor, provider, ["Synthetic dialog before a non-WS source advance."]
    )
    generation = int(notice["turn_generation"])
    next_generation = terminal_module.record_direct_interaction_input(
        context.terminal_id,
        expected_incarnation_id=context.incarnation_id,
        idempotency_key="native-nonws-first-ws-advance-0001",
    )
    assert next_generation == generation + 1

    # Simulate a durable source turn written by an out-of-band task producer.
    # Service admission would reject this advance while the physical notice is
    # unresolved; the seeded row proves the waiter still rejects such history.
    from cli_agent_orchestrator.services.interactions import service as service_module

    with context.service.repository.transaction() as connection:
        connection.execute(
            "INSERT INTO interaction_turn_advances VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (
                context.terminal_id,
                context.incarnation_id,
                "native-nonws-task-advance-0001",
                "task_delivery",
                next_generation,
                next_generation + 1,
                service_module._now(),
            ),
        )
        connection.execute(
            "UPDATE interaction_sources SET turn_generation=%s WHERE terminal_id=%s AND incarnation_id=%s",
            (next_generation + 1, context.terminal_id, context.incarnation_id),
        )

    assert context.service.has_unresolved_native_notice(context.terminal_id, context.incarnation_id)
    old_wait = asyncio.run(
        context.service.wait_for_native_manual(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=0,
            expected_generation=generation,
        )
    )
    assert old_wait.state == "stale"

    receipt = context.service.reconcile_native_observation(
        context.terminal_id,
        context.incarnation_id,
        generation,
        observation_id=notice["observation_id"],
        dialog_fingerprint=notice["dialog_fingerprint"],
        provider_evidence_version=notice["provider_evidence_version"],
    )
    assert receipt.lifecycle == "stale"
    assert _question_lifecycle(context, notice["question_id"]) == "stale"
    assert not context.service.has_unresolved_native_notice(
        context.terminal_id, context.incarnation_id
    )
    still_stale = asyncio.run(
        context.service.wait_for_native_manual(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=0,
            expected_generation=generation,
        )
    )
    assert still_stale.state == "stale"
    assert _answer_count(context) == 0


@pytest.mark.asyncio
async def test_new_monitor_recovers_exact_closed_pin_without_releasing_stale_waiter(
    interaction_http_context, monkeypatch
):
    """A restarted monitor can clear the physical gate without resuming a task."""
    from cli_agent_orchestrator.models.terminal import TerminalStatus
    from cli_agent_orchestrator.services import terminal_service as terminal_module
    from cli_agent_orchestrator.services.status_monitor import StatusMonitor

    context = interaction_http_context
    terminal_service, first_monitor, provider, _backend, _metadata = _install_native_runtime(
        monkeypatch, context
    )
    original_screen = ["Synthetic native dialog remains visible across monitor restart."]
    notice = _observe_native(context, first_monitor, provider, original_screen)
    original_generation = int(notice["turn_generation"])

    waiter = asyncio.create_task(
        context.service.wait_for_native_manual(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=5,
            expected_generation=original_generation,
        )
    )
    await asyncio.sleep(0.05)
    current_generation = _advance_native_dialog_over_two_websocket_inputs(
        context,
        first_monitor,
        provider,
        original_screen,
        key_prefix="native-monitor-restart",
    )
    assert current_generation == original_generation + 2
    assert _question_lifecycle(context, notice["question_id"]) == "stale"
    assert not waiter.done()
    assert context.service.has_unresolved_native_notice(context.terminal_id, context.incarnation_id)

    pin = context.service.latest_unresolved_native_observation(
        context.terminal_id, context.incarnation_id
    )
    assert pin is not None
    assert pin.question_id == notice["question_id"]
    assert pin.observation_id == notice["observation_id"]
    assert pin.turn_generation == original_generation
    assert pin.current_generation == current_generation
    assert pin.exact_target == (
        context.terminal_id,
        context.session_name,
        "worker",
    )

    # A newly constructed monitor has no process-local observation cache. The
    # provider dialog is already gone, so two stable, changed IDLE viewports
    # must use only the durable exact target pin to record physical closure.
    recovered_monitor = StatusMonitor()
    monkeypatch.setattr(terminal_module, "status_monitor", recovered_monitor)
    closed_screen = ["Synthetic stable ready viewport after monitor restart."]
    recovered_monitor._observe_native_interaction(
        context.terminal_id, provider, TerminalStatus.IDLE, closed_screen
    )
    assert context.service.has_unresolved_native_notice(context.terminal_id, context.incarnation_id)
    assert not waiter.done()
    assert _question_lifecycle(context, notice["question_id"]) == "stale"

    recovered_monitor._observe_native_interaction(
        context.terminal_id, provider, TerminalStatus.IDLE, closed_screen
    )
    assert not context.service.has_unresolved_native_notice(
        context.terminal_id, context.incarnation_id
    )
    waited = await asyncio.wait_for(waiter, timeout=1)
    assert waited.state == "stale"
    assert waited.lifecycle == "stale"
    assert waited.question_id == notice["question_id"]
    assert _question_lifecycle(context, notice["question_id"]) == "stale"
    assert _answer_count(context) == 0
    with context.service.repository.read_connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM interaction_answers WHERE question_id=%s",
                (notice["question_id"],),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM interaction_deliveries d "
                "JOIN interaction_answers a ON a.answer_id=d.answer_id WHERE a.question_id=%s",
                (notice["question_id"],),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM interaction_events WHERE question_id=%s "
                "AND event_type IN ('interaction.answer_returned','interaction.answer_acknowledged')",
                (notice["question_id"],),
            ).fetchone()[0]
            == 0
        )
        closure = connection.execute(
            "SELECT event_type FROM interaction_events WHERE question_id=%s "
            "AND event_type='interaction.native_closed'",
            (notice["question_id"],),
        ).fetchone()
    assert closure is not None


@pytest.mark.parametrize(
    "missing_target_evidence", [False, True], ids=["mismatched-target", "legacy-no-target"]
)
@pytest.mark.asyncio
async def test_monitor_restart_rejects_native_pin_without_matching_historical_target(
    interaction_http_context, monkeypatch, missing_target_evidence
):
    """A restart cannot infer an old tmux target from present-day metadata."""
    from cli_agent_orchestrator.models.terminal import TerminalStatus
    from cli_agent_orchestrator.services.status_monitor import StatusMonitor

    context = interaction_http_context
    _terminal_service, first_monitor, provider, _backend, metadata = _install_native_runtime(
        monkeypatch, context
    )
    if missing_target_evidence:
        append_event = context.service.repository.append_event

        def omit_target_binding_event(connection, **kwargs):
            if kwargs.get("event_type") == "interaction.native_observed":
                return 0
            return append_event(connection, **kwargs)

        monkeypatch.setattr(context.service.repository, "append_event", omit_target_binding_event)

    original_screen = ["Synthetic dialog with exact historical tmux target."]
    notice = _observe_native(context, first_monitor, provider, original_screen)
    original_generation = int(notice["turn_generation"])
    current_generation = _advance_native_dialog_over_two_websocket_inputs(
        context,
        first_monitor,
        provider,
        original_screen,
        key_prefix=(
            "native-monitor-legacy-target"
            if missing_target_evidence
            else "native-monitor-mismatched-target"
        ),
    )
    assert current_generation == original_generation + 2

    if missing_target_evidence:
        assert (
            context.service.latest_unresolved_native_observation(
                context.terminal_id, context.incarnation_id
            )
            is None
        )
    else:
        # The current terminal metadata must still match the durable target
        # digest. A renamed window cannot be treated as the original target.
        with context.service.repository.transaction() as connection:
            connection.execute(
                "UPDATE terminals SET tmux_window=%s WHERE id=%s",
                ("different-window", context.terminal_id),
            )
        assert (
            context.service.latest_unresolved_native_observation(
                context.terminal_id, context.incarnation_id
            )
            is None
        )

    assert context.service.has_unresolved_native_notice(context.terminal_id, context.incarnation_id)
    waiter = asyncio.create_task(
        context.service.wait_for_native_manual(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=5,
            expected_generation=original_generation,
        )
    )
    await asyncio.sleep(0.05)
    recovered_monitor = StatusMonitor()
    closed_screen = ["Synthetic changed stable viewport after dialog disappeared."]
    recovered_monitor._observe_native_interaction(
        context.terminal_id, provider, TerminalStatus.IDLE, closed_screen
    )
    recovered_monitor._observe_native_interaction(
        context.terminal_id, provider, TerminalStatus.IDLE, closed_screen
    )

    assert context.service.has_unresolved_native_notice(context.terminal_id, context.incarnation_id)
    assert _question_lifecycle(context, notice["question_id"]) == "stale"
    assert _answer_count(context) == 0
    assert not waiter.done()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
