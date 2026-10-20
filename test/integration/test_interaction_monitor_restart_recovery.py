"""Restart recovery for exact provider-native manual observation pins."""

from __future__ import annotations

import asyncio
from test.integration.test_interaction_explicit_flow import interaction_http_context  # noqa: F401
from test.integration.test_interaction_native_guard_contracts import (
    _answer_count,
    _install_native_runtime,
    _observe_native,
    _question_lifecycle,
)

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services.interactions import service as service_module
from cli_agent_orchestrator.services.status_monitor import StatusMonitor

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def _observe_closed_viewport(context, monitor, provider, screen_lines):
    for _ in range(2):
        monitor._observe_native_interaction(
            context.terminal_id,
            provider,
            TerminalStatus.IDLE,
            screen_lines,
        )


@pytest.mark.asyncio
async def test_restarted_monitor_recovers_exact_stale_notice_event_only(
    interaction_http_context, monkeypatch
):
    context = interaction_http_context
    terminal_service, original_monitor, provider, backend, _metadata = _install_native_runtime(
        monkeypatch, context
    )
    original_screen = ["Synthetic native prompt remains active until manual input."]
    notice = _observe_native(context, original_monitor, provider, original_screen)
    old_generation = int(notice["turn_generation"])

    waiter = asyncio.create_task(
        context.service.wait_for_native_manual(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=5,
            expected_generation=old_generation,
        )
    )
    await asyncio.sleep(0.05)
    generation = terminal_service.record_direct_interaction_input(
        context.terminal_id,
        expected_incarnation_id=context.incarnation_id,
        reason="websocket_input",
        idempotency_key="monitor-restart-native-websocket-input-0001",
    )
    assert generation == old_generation + 1
    assert context.service.has_unresolved_native_notice(context.terminal_id, context.incarnation_id)
    assert (
        await context.service.wait_for_native_manual(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=0,
            expected_generation=old_generation,
        )
    ).state == "waiting"

    # A new monitor has no process-local pin. It restores only the latest exact
    # durable question and requires two stable changed-screen captures.
    restarted_monitor = StatusMonitor()
    closed_screen = ["Synthetic terminal returned to the prompt after closure."]
    restarted_monitor._observe_native_interaction(
        context.terminal_id, provider, TerminalStatus.IDLE, closed_screen
    )
    recovered_pin = restarted_monitor._native_observation_pins[context.terminal_id]
    assert recovered_pin["recovery_only"] is True
    assert recovered_pin["observation_id"] == notice["observation_id"]
    assert context.service.has_unresolved_native_notice(context.terminal_id, context.incarnation_id)

    restarted_monitor._observe_native_interaction(
        context.terminal_id, provider, TerminalStatus.IDLE, closed_screen
    )

    assert _question_lifecycle(context, notice["question_id"]) == "stale"
    assert not context.service.has_unresolved_native_notice(
        context.terminal_id, context.incarnation_id
    )
    assert _answer_count(context) == 0
    assert (await waiter).state == "stale"
    assert backend.send_keys.call_count == 0
    with context.service.repository.read_connection() as connection:
        closure = connection.execute(
            "SELECT metadata_json FROM interaction_events WHERE question_id=%s "
            "AND event_type='interaction.native_closed' ORDER BY sequence DESC LIMIT 1",
            (notice["question_id"],),
        ).fetchone()
        event = connection.execute(
            "SELECT metadata_json FROM interaction_events WHERE question_id=%s "
            "AND event_type='interaction.native_observed' ORDER BY sequence DESC LIMIT 1",
            (notice["question_id"],),
        ).fetchone()
    assert closure is not None
    assert '"outcome":"closed_after_monitor_recovery"' in closure["metadata_json"]
    assert event is not None
    assert context.session_name not in event["metadata_json"]
    assert "worker" not in event["metadata_json"]


@pytest.mark.parametrize("terminal_state", ["expired", "cancelled"])
def test_restarted_monitor_closes_expired_or_cancelled_notice_without_revival(
    interaction_http_context, monkeypatch, terminal_state
):
    context = interaction_http_context
    _terminal_service, original_monitor, provider, _backend, _metadata = _install_native_runtime(
        monkeypatch, context
    )
    notice = _observe_native(
        context, original_monitor, provider, ["Synthetic prompt requires manual action."]
    )
    old_generation = int(notice["turn_generation"])
    if terminal_state == "expired":
        now = service_module._now()
        monkeypatch.setattr(service_module, "_now", lambda: now + 86_400)
    else:
        context.service.cancel(
            context.owner,
            notice["question_id"],
            1,
            "Cancel the synthetic native prompt.",
        )

    restarted_monitor = StatusMonitor()
    _observe_closed_viewport(
        context,
        restarted_monitor,
        provider,
        ["Synthetic terminal returned to a stable idle prompt."],
    )

    assert _question_lifecycle(context, notice["question_id"]) == terminal_state
    assert not context.service.has_unresolved_native_notice(
        context.terminal_id, context.incarnation_id
    )
    assert _answer_count(context) == 0
    assert (
        asyncio.run(
            context.service.wait_for_native_manual(
                context.terminal_id,
                context.incarnation_id,
                timeout_seconds=0,
                expected_generation=old_generation,
            )
        ).state
        == terminal_state
    )


@pytest.mark.parametrize("identity_change", ["window", "incarnation"])
def test_restarted_monitor_does_not_restore_pin_for_changed_identity(
    interaction_http_context, monkeypatch, identity_change
):
    context = interaction_http_context
    _terminal_service, original_monitor, provider, _backend, metadata = _install_native_runtime(
        monkeypatch, context
    )
    notice = _observe_native(
        context, original_monitor, provider, ["Synthetic provider prompt is active."]
    )
    if identity_change == "window":
        metadata["tmux_window"] = "different-window"
        with context.service.repository.transaction() as connection:
            connection.execute(
                "UPDATE terminals SET tmux_window=%s WHERE id=%s",
                ("different-window", context.terminal_id),
            )
    else:
        metadata["session_incarnation_id"] = "replacement-incarnation"

    restarted_monitor = StatusMonitor()
    _observe_closed_viewport(
        context,
        restarted_monitor,
        provider,
        ["Changed viewport must not be attached to another target."],
    )

    assert context.terminal_id not in restarted_monitor._native_observation_pins
    assert _question_lifecycle(context, notice["question_id"]) == "manual_only"
    assert context.service.has_unresolved_native_notice(context.terminal_id, context.incarnation_id)
    assert _answer_count(context) == 0


def test_restarted_monitor_fails_closed_for_legacy_notice_without_target_binding(
    interaction_http_context, monkeypatch
):
    context = interaction_http_context
    _terminal_service, original_monitor, provider, _backend, _metadata = _install_native_runtime(
        monkeypatch, context
    )
    original_append_event = context.service.repository.append_event

    def append_without_target_event(connection, *, event_type, **kwargs):
        if event_type == "interaction.native_observed":
            return None
        return original_append_event(connection, event_type=event_type, **kwargs)

    monkeypatch.setattr(context.service.repository, "append_event", append_without_target_event)
    notice = _observe_native(
        context, original_monitor, provider, ["Synthetic legacy provider prompt."]
    )
    monkeypatch.setattr(context.service.repository, "append_event", original_append_event)

    restarted_monitor = StatusMonitor()
    _observe_closed_viewport(
        context,
        restarted_monitor,
        provider,
        ["Synthetic screen after prompt closure."],
    )

    assert context.terminal_id not in restarted_monitor._native_observation_pins
    assert context.service.has_unresolved_native_notice(context.terminal_id, context.incarnation_id)
    assert _question_lifecycle(context, notice["question_id"]) == "manual_only"


@pytest.mark.parametrize("terminal_state", ["expired", "cancelled"])
def test_reappearing_terminal_dialog_after_newer_dialog_gets_fresh_notice_and_closes(
    interaction_http_context, monkeypatch, terminal_state
):
    context = interaction_http_context
    _terminal_service, monitor, provider, _backend, _metadata = _install_native_runtime(
        monkeypatch, context
    )
    original_screen = ["Synthetic native dialog F1 is visible."]
    first = _observe_native(context, monitor, provider, original_screen)
    old_generation = int(first["turn_generation"])

    if terminal_state == "expired":
        from cli_agent_orchestrator.services.interactions import service as service_module

        real_now = service_module._now
        now = real_now()
        monkeypatch.setattr(service_module, "_now", lambda: now + 86_400)
        assert context.service.has_unresolved_native_notice(
            context.terminal_id, context.incarnation_id
        )
        monkeypatch.setattr(service_module, "_now", real_now)
    else:
        context.service.cancel(
            context.owner,
            first["question_id"],
            1,
            "Cancel the first synthetic native dialog.",
        )

    old_wait_state = asyncio.run(
        context.service.wait_for_native_manual(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=0,
            expected_generation=old_generation,
        )
    )
    assert old_wait_state.state == terminal_state

    second = _observe_native(
        context,
        monitor,
        provider,
        ["Synthetic native dialog F2 replaces the first dialog."],
    )
    assert second["question_id"] != first["question_id"]
    assert _question_lifecycle(context, second["question_id"]) == "manual_only"

    reopened = _observe_native(context, monitor, provider, original_screen)
    assert reopened["question_id"] != first["question_id"]
    assert reopened["observation_id"] != first["observation_id"]
    assert reopened["question_id"] != second["question_id"]
    assert _question_lifecycle(context, first["question_id"]) == terminal_state
    assert _question_lifecycle(context, second["question_id"]) == "stale"
    assert context.service.has_unresolved_native_notice(context.terminal_id, context.incarnation_id)

    _observe_closed_viewport(
        context,
        monitor,
        provider,
        ["Synthetic stable idle screen after F1 closes."],
    )

    assert not context.service.has_unresolved_native_notice(
        context.terminal_id, context.incarnation_id
    )
    assert _question_lifecycle(context, first["question_id"]) == terminal_state
    assert _question_lifecycle(context, reopened["question_id"]) == "reconciled_external"
    assert (
        asyncio.run(
            context.service.wait_for_native_manual(
                context.terminal_id,
                context.incarnation_id,
                timeout_seconds=0,
                expected_generation=old_generation,
                expected_question_id=first["question_id"],
                expected_observation_id=first["observation_id"],
            )
        ).state
        != "released"
    )
    assert _answer_count(context) == 0


def test_native_notice_expired_after_websocket_advance_cannot_release_old_waiter(
    interaction_http_context, monkeypatch
):
    from cli_agent_orchestrator.services.interactions import service as service_module

    context = interaction_http_context
    terminal_service, monitor, provider, _backend, _metadata = _install_native_runtime(
        monkeypatch, context
    )
    notice = _observe_native(
        context, monitor, provider, ["Synthetic native prompt remains open past TTL."]
    )
    generation = int(notice["turn_generation"])
    initial_wait = asyncio.run(
        context.service.wait_for_native_manual(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=0,
            expected_generation=generation,
        )
    )
    assert initial_wait.state == "waiting"

    advanced = terminal_service.record_direct_interaction_input(
        context.terminal_id,
        expected_incarnation_id=context.incarnation_id,
        reason="websocket_input",
        idempotency_key="monitor-restart-native-ttl-websocket-0001",
    )
    assert advanced == generation + 1

    real_now = service_module._now
    now = real_now()
    monkeypatch.setattr(service_module, "_now", lambda: now + 86_400)
    _observe_closed_viewport(
        context,
        monitor,
        provider,
        ["Synthetic stable idle viewport after expired native prompt."],
    )

    assert _question_lifecycle(context, notice["question_id"]) == "expired"
    assert not context.service.has_unresolved_native_notice(
        context.terminal_id, context.incarnation_id
    )
    assert _answer_count(context) == 0
    assert (
        asyncio.run(
            context.service.wait_for_native_manual(
                context.terminal_id,
                context.incarnation_id,
                timeout_seconds=0,
                expected_generation=generation,
                expected_question_id=initial_wait.question_id,
                expected_observation_id=initial_wait.observation_id,
            )
        ).state
        == "expired"
    )
