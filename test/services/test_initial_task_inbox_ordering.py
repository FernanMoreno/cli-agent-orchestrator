"""Inbox input cannot steal the first prompt from a deferred initializer."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.inbox import OrchestrationType
from cli_agent_orchestrator.models.terminal import TerminalInputBlockedError, TerminalStatus
from cli_agent_orchestrator.providers.catalog import registered_provider_descriptors
from cli_agent_orchestrator.services import terminal_service as ts


@pytest.mark.asyncio
@pytest.mark.parametrize("descriptor", registered_provider_descriptors(), ids=lambda d: d.name)
async def test_ready_frame_does_not_authorize_inbox_during_initialization(
    isolated_memory_db, monkeypatch, descriptor
):
    terminal_id = "initial-order"
    db.create_terminal(terminal_id, "order", "worker", descriptor.name, "reviewer")
    provider = MagicMock()
    provider.initialize = AsyncMock(side_effect=lambda: asyncio.sleep(300))
    provider.shell_baseline = None
    provider.blocks_new_task_input_for_reconciliation = False
    provider.requires_turn_receipt = False
    provider.requires_direct_execution_evidence = False
    provider.paste_enter_count = 1
    provider.paste_submit_delay = 0
    backend = MagicMock()
    backend.send_keys.side_effect = AssertionError("early inbox reached keyboard")
    monkeypatch.setattr(ts, "inject_memory_context", lambda text, *args: text)
    monkeypatch.setattr(ts, "get_backend", lambda: backend)
    monkeypatch.setattr(ts.provider_manager, "get_provider", lambda _: provider)
    monkeypatch.setattr(ts.status_monitor, "get_status", lambda _: TerminalStatus.IDLE)
    task = ts._schedule_deferred_init(
        provider,
        terminal_id,
        "original assignment",
        OrchestrationType.ASSIGN,
        None,
        prompt_redelivery=False,
        delete_on_failure=True,
    )
    try:
        with pytest.raises(TerminalInputBlockedError, match="initial task") as blocked:
            ts.send_input(terminal_id, "peer ID arrived early", task_delivery=True)
        assert not blocked.value.delivery_may_have_occurred
        backend.send_keys.assert_not_called()
        backend.send_special_key.assert_not_called()
        assert db.get_terminal_turn_receipt(terminal_id) is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_pending_peer_message_follows_initial_task_once(isolated_memory_db, monkeypatch):
    from cli_agent_orchestrator.models.inbox import MessageStatus
    from cli_agent_orchestrator.services.inbox_service import InboxService

    terminal_id = "queue-order"
    db.create_terminal(terminal_id, "order", "worker", "codex", "reviewer")
    db.create_inbox_message("supervisor", terminal_id, "peer ID arrived early")
    ready = asyncio.Event()

    async def initialize():
        await ready.wait()
        return True

    provider = MagicMock()
    provider.initialize = initialize
    provider.shell_baseline = None
    provider.blocks_new_task_input_for_reconciliation = False
    provider.requires_turn_receipt = False
    provider.has_pending_native_swarm = False
    provider.paste_enter_count = 1
    provider.paste_submit_delay = 0
    backend = MagicMock()
    sent = []
    backend.send_keys.side_effect = lambda session, window, text, **kwargs: sent.append(text)
    monkeypatch.setattr(ts, "get_backend", lambda: backend)
    monkeypatch.setattr(ts, "inject_memory_context", lambda text, *args: text)
    monkeypatch.setattr(ts.provider_manager, "get_provider", lambda _: provider)
    monkeypatch.setattr(ts.status_monitor, "get_status", lambda _: TerminalStatus.IDLE)
    monkeypatch.setattr(ts.status_monitor, "notify_input_sent", lambda *a, **k: 1)
    monkeypatch.setattr(ts.status_monitor, "clear_rolling_buffer", lambda *a, **k: None)
    monkeypatch.setattr(ts.status_monitor, "notify_input_delivered", lambda *a, **k: None)
    task = ts._schedule_deferred_init(
        provider,
        terminal_id,
        "original assignment",
        OrchestrationType.ASSIGN,
        None,
        prompt_redelivery=False,
        delete_on_failure=True,
    )
    service = InboxService()
    try:
        await asyncio.sleep(0)
        service.deliver_pending(terminal_id)
        assert sent == []
        assert db.get_inbox_messages(terminal_id)[0].status == MessageStatus.PENDING
        ready.set()
        await asyncio.wait_for(task, timeout=5)
        assert sent == ["original assignment"]
        service.deliver_pending(terminal_id)
        service.deliver_pending(terminal_id)
        assert sent == ["original assignment", "peer ID arrived early"]
        assert db.get_inbox_messages(terminal_id)[0].status == MessageStatus.DELIVERED
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
