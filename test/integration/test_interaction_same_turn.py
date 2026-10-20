"""Exercise the special-key turn fence against the durable interaction ledger."""

from __future__ import annotations

from test.integration.test_interaction_explicit_flow import interaction_http_context  # noqa: F401
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def _install_terminal_fakes(monkeypatch, context):
    from cli_agent_orchestrator.models.terminal import TerminalStatus
    from cli_agent_orchestrator.services import remote_terminal_service, terminal_service

    backend = MagicMock()
    monkeypatch.setattr(terminal_service, "_interaction_service", lambda: context.service)
    monkeypatch.setattr(terminal_service, "get_backend", lambda: backend)
    monkeypatch.setattr(terminal_service, "ensure_terminal_is_not_work_owned", lambda _id: None)
    monkeypatch.setattr(
        terminal_service.status_monitor, "get_status", lambda _id: TerminalStatus.IDLE
    )
    monkeypatch.setattr(terminal_service.status_monitor, "notify_input_sent", lambda _id: None)
    monkeypatch.setattr(terminal_service.status_monitor, "notify_input_delivered", lambda _id: None)
    monkeypatch.setattr(terminal_service, "update_last_active", lambda _id: None)
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _id: None)
    return backend, terminal_service


def test_same_turn_key_keeps_registered_source_generation_and_capability(
    interaction_http_context, monkeypatch
):
    context = interaction_http_context
    backend, terminal_service = _install_terminal_fakes(monkeypatch, context)
    before = context.service.source_status(context.terminal_id, context.incarnation_id)
    assert before.state == "current"
    assert before.current_generation is not None
    assert before.capability_available is True

    assert terminal_service.send_special_key(context.terminal_id, "Enter", same_turn=True) is True

    backend.send_special_key.assert_called_once_with(context.session_name, "worker", "Enter")
    after = context.service.source_status(context.terminal_id, context.incarnation_id)
    assert after.state == "current"
    assert after.current_generation == before.current_generation
    assert after.capability_available is True
    agent = context.service.authenticate_agent(context.source_token)
    assert agent.terminal_id == context.terminal_id
    assert agent.turn_generation == before.current_generation


def test_regular_special_key_advances_registered_source_generation(
    interaction_http_context, monkeypatch
):
    context = interaction_http_context
    backend, terminal_service = _install_terminal_fakes(monkeypatch, context)
    before = context.service.source_status(context.terminal_id, context.incarnation_id)
    assert before.current_generation is not None

    assert terminal_service.send_special_key(context.terminal_id, "Enter", same_turn=False) is True

    backend.send_special_key.assert_called_once_with(context.session_name, "worker", "Enter")
    after = context.service.source_status(context.terminal_id, context.incarnation_id)
    assert after.current_generation == before.current_generation + 1
    agent = context.service.authenticate_agent(context.source_token)
    assert agent.turn_generation == after.current_generation


def test_same_turn_key_cannot_bypass_pending_required_interaction(
    interaction_http_context, monkeypatch
):
    from cli_agent_orchestrator.models.terminal import TerminalInputBlockedError
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        QuestionInput,
    )

    context = interaction_http_context
    backend, terminal_service = _install_terminal_fakes(monkeypatch, context)
    context.service.create_group(
        context.source_token,
        CreateGroupRequest(
            questions=(QuestionInput(kind="text", prompt="Confirm the deployment target."),),
            wait_for_required=False,
        ),
        "same-turn-required-question-0001",
    )
    before = context.service.source_status(context.terminal_id, context.incarnation_id)
    assert context.service.has_required_wait(context.terminal_id, context.incarnation_id)

    with pytest.raises(TerminalInputBlockedError, match="required interaction"):
        terminal_service.send_special_key(context.terminal_id, "Enter", same_turn=True)

    backend.send_special_key.assert_not_called()
    after = context.service.source_status(context.terminal_id, context.incarnation_id)
    assert after.current_generation == before.current_generation
    assert context.service.has_required_wait(context.terminal_id, context.incarnation_id)


@pytest.mark.parametrize("dispatch_kind", ["input", "key"])
def test_native_manual_notice_blocks_automated_dispatch_during_transient_idle(
    interaction_http_context, monkeypatch, dispatch_kind
):
    from cli_agent_orchestrator.models.terminal import TerminalInputBlockedError
    from cli_agent_orchestrator.providers.mcp_interactions import NativeInteractionObservation

    context = interaction_http_context
    backend, terminal_service = _install_terminal_fakes(monkeypatch, context)
    before = context.service.source_status(context.terminal_id, context.incarnation_id)
    observation = NativeInteractionObservation.manual_signal(
        screen_lines=["Synthetic provider dialog requiring manual action."],
        terminal_id=context.terminal_id,
        session_name=context.session_name,
        window_name="worker",
    )
    assert observation is not None
    notice = context.service.observe_native(
        context.terminal_id,
        context.incarnation_id,
        before.current_generation,
        observation,
    )
    assert notice.lifecycle == "manual_only"

    if dispatch_kind == "input":
        provider = MagicMock(
            requires_turn_receipt=False,
            assume_processing_on_dispatch=False,
            paste_enter_count=1,
            paste_submit_delay=0.0,
        )
        monkeypatch.setattr(terminal_service.provider_manager, "get_provider", lambda _id: provider)
        monkeypatch.setattr(
            terminal_service, "inject_memory_context", lambda message, *_args: message
        )
        monkeypatch.setattr(
            terminal_service.status_monitor, "notify_input_sent", lambda *_a, **_k: 1
        )
        monkeypatch.setattr(
            terminal_service.status_monitor, "clear_rolling_buffer", lambda *_a, **_k: None
        )
        monkeypatch.setattr(terminal_service.status_monitor, "abort_turn", lambda *_a, **_k: None)

    with pytest.raises(TerminalInputBlockedError, match="provider-owned interaction"):
        if dispatch_kind == "input":
            terminal_service.send_input(
                context.terminal_id,
                "automated task must not enter the native dialog",
                task_delivery=True,
            )
        else:
            terminal_service.send_special_key(context.terminal_id, "Enter", same_turn=False)

    backend.send_keys.assert_not_called()
    backend.send_special_key.assert_not_called()
    after = context.service.source_status(context.terminal_id, context.incarnation_id)
    assert after.current_generation == before.current_generation
    with context.service.repository.read_connection() as connection:
        row = connection.execute(
            "SELECT lifecycle FROM interaction_questions WHERE question_id=%s",
            (notice.question_id,),
        ).fetchone()
    assert row["lifecycle"] == "manual_only"
    assert context.service.has_unresolved_native_notice(context.terminal_id, context.incarnation_id)


def test_authorized_websocket_input_advances_but_keeps_native_notice_gated(
    interaction_http_context, monkeypatch
):
    from cli_agent_orchestrator.providers.mcp_interactions import NativeInteractionObservation

    context = interaction_http_context
    _, terminal_service = _install_terminal_fakes(monkeypatch, context)
    before = context.service.source_status(context.terminal_id, context.incarnation_id)
    observation = NativeInteractionObservation.manual_signal(
        screen_lines=["Synthetic provider dialog requiring manual action."],
        terminal_id=context.terminal_id,
        session_name=context.session_name,
        window_name="worker",
    )
    assert observation is not None
    context.service.observe_native(
        context.terminal_id,
        context.incarnation_id,
        before.current_generation,
        observation,
    )

    generation = terminal_service.record_direct_interaction_input(
        context.terminal_id,
        expected_incarnation_id=context.incarnation_id,
        reason="websocket_input",
        idempotency_key="same-turn-native-ws-input-0001",
    )

    assert generation == before.current_generation + 1
    assert context.service.has_unresolved_native_notice(context.terminal_id, context.incarnation_id)
    assert not context.service.has_required_wait(context.terminal_id, context.incarnation_id)


def test_same_turn_key_rechecks_native_gate_at_transport_boundary(
    interaction_http_context, monkeypatch
):
    from cli_agent_orchestrator.models.terminal import TerminalInputBlockedError
    from cli_agent_orchestrator.providers.mcp_interactions import NativeInteractionObservation

    context = interaction_http_context
    backend, terminal_service = _install_terminal_fakes(monkeypatch, context)
    before = context.service.source_status(context.terminal_id, context.incarnation_id)

    def register_notice_during_dispatch(_terminal_id, **_kwargs):
        observation = NativeInteractionObservation.manual_signal(
            screen_lines=["Synthetic provider dialog requiring manual action."],
            terminal_id=context.terminal_id,
            session_name=context.session_name,
            window_name="worker",
        )
        assert observation is not None
        context.service.observe_native(
            context.terminal_id,
            context.incarnation_id,
            before.current_generation,
            observation,
        )
        return 1

    monkeypatch.setattr(
        terminal_service.status_monitor,
        "notify_input_sent",
        register_notice_during_dispatch,
    )

    with pytest.raises(TerminalInputBlockedError, match="provider-owned interaction"):
        terminal_service.send_special_key(context.terminal_id, "Enter", same_turn=True)

    backend.send_special_key.assert_not_called()
    after = context.service.source_status(context.terminal_id, context.incarnation_id)
    assert after.current_generation == before.current_generation
    assert context.service.has_unresolved_native_notice(context.terminal_id, context.incarnation_id)


def test_send_input_rechecks_native_gate_after_task_turn_advance(
    interaction_http_context, monkeypatch
):
    from cli_agent_orchestrator.models.terminal import TerminalInputBlockedError
    from cli_agent_orchestrator.providers.mcp_interactions import NativeInteractionObservation

    context = interaction_http_context
    backend, terminal_service = _install_terminal_fakes(monkeypatch, context)
    before = context.service.source_status(context.terminal_id, context.incarnation_id)
    provider = MagicMock(
        requires_turn_receipt=False,
        assume_processing_on_dispatch=False,
        paste_enter_count=1,
        paste_submit_delay=0.0,
    )
    monkeypatch.setattr(terminal_service.provider_manager, "get_provider", lambda _id: provider)
    monkeypatch.setattr(terminal_service, "inject_memory_context", lambda message, *_args: message)
    monkeypatch.setattr(
        terminal_service.status_monitor,
        "clear_rolling_buffer",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(terminal_service.status_monitor, "abort_turn", lambda *_args: None)

    def register_notice_after_advance(_terminal_id, **_kwargs):
        current = context.service.source_status(context.terminal_id, context.incarnation_id)
        observation = NativeInteractionObservation.manual_signal(
            screen_lines=["Synthetic provider dialog requiring manual action."],
            terminal_id=context.terminal_id,
            session_name=context.session_name,
            window_name="worker",
        )
        assert observation is not None
        context.service.observe_native(
            context.terminal_id,
            context.incarnation_id,
            current.current_generation,
            observation,
        )
        return 1

    monkeypatch.setattr(
        terminal_service.status_monitor,
        "notify_input_sent",
        register_notice_after_advance,
    )

    with pytest.raises(TerminalInputBlockedError, match="provider-owned interaction"):
        terminal_service.send_input(
            context.terminal_id,
            "task must not overwrite a provider dialog",
            task_delivery=True,
        )

    backend.send_keys.assert_not_called()
    after = context.service.source_status(context.terminal_id, context.incarnation_id)
    assert after.current_generation == before.current_generation + 1
    assert context.service.has_unresolved_native_notice(context.terminal_id, context.incarnation_id)
