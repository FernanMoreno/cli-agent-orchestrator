"""Execution composition fences introduced by integration 008."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import inbox_service


@pytest.mark.parametrize("status", [TerminalStatus.COMPLETED, TerminalStatus.PROCESSING])
def test_native_swarm_defers_even_eager_inbox_delivery(monkeypatch, status):
    message = SimpleNamespace(id=1, sender_id="parent", message="next task")
    monkeypatch.setattr(inbox_service, "get_pending_messages", lambda *a, **k: [message])
    monkeypatch.setattr(inbox_service, "EAGER_INBOX_DELIVERY", True)
    monkeypatch.setattr(inbox_service.status_monitor, "get_status", lambda _: status)
    provider = SimpleNamespace(has_pending_native_swarm=True, accepts_input_while_processing=True)
    monkeypatch.setattr(inbox_service.provider_manager, "get_provider", lambda _: provider)
    update = MagicMock()
    send = MagicMock()
    monkeypatch.setattr(inbox_service, "update_message_status", update)
    monkeypatch.setattr(inbox_service.terminal_service, "send_input", send)
    inbox_service.InboxService().deliver_pending("swarm")
    update.assert_not_called()
    send.assert_not_called()


def test_closed_herdr_workspace_keeps_work_bound_routing(monkeypatch):
    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.services import terminal_service
    from cli_agent_orchestrator.services.herdr_inbox_service import HerdrInboxService

    service = HerdrInboxService(socket_path="/tmp/unused-cao008.sock")
    service._terminal_to_pane["owned"] = "pane-1"
    service._pane_to_terminal["pane-1"] = "owned"
    monkeypatch.setattr(
        database,
        "list_terminals_by_session",
        lambda _: [{"id": "owned", "tmux_window": "owned-window"}],
    )
    monkeypatch.setattr(service, "_live_tab_labels", lambda: set())

    def ownership_unavailable(_):
        raise RuntimeError("durable ownership cannot be proved")

    monkeypatch.setattr(
        terminal_service, "ensure_terminal_is_not_work_owned", ownership_unavailable
    )
    teardown = MagicMock()
    monkeypatch.setattr(terminal_service, "delete_terminal", teardown)
    assert service._cleanup_closed_session("cao-closed") is False
    assert service._terminal_to_pane["owned"] == "pane-1"
    assert service._pane_to_terminal["pane-1"] == "owned"
    teardown.assert_not_called()


def test_retention_sweep_keeps_work_owned_runtime(monkeypatch):
    from cli_agent_orchestrator.services import cleanup_service, terminal_service

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [SimpleNamespace(id="owned")]
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    monkeypatch.setattr(cleanup_service, "SessionLocal", factory)

    def refuse(_):
        raise RuntimeError("terminal belongs to Work")

    monkeypatch.setattr(terminal_service, "ensure_terminal_is_not_work_owned", refuse)
    monkeypatch.setattr(
        terminal_service, "should_retain_deferred_failure_tombstone", lambda _: False
    )
    stop = MagicMock()
    clear = MagicMock()
    cleanup = MagicMock()
    delete = MagicMock()
    monkeypatch.setattr(cleanup_service.fifo_manager, "stop_reader", stop)
    monkeypatch.setattr(cleanup_service.status_monitor, "clear_terminal", clear)
    monkeypatch.setattr(cleanup_service.provider_manager, "cleanup_provider", cleanup)
    monkeypatch.setattr(terminal_service, "delete_terminal_row", delete)
    monkeypatch.setattr(cleanup_service, "TERMINAL_LOG_DIR", SimpleNamespace(exists=lambda: False))
    monkeypatch.setattr(cleanup_service, "LOG_DIR", SimpleNamespace(exists=lambda: False))
    monkeypatch.setattr(cleanup_service, "delete_old_handoff_results", lambda _: 0)
    cleanup_service.cleanup_old_data()
    stop.assert_not_called()
    clear.assert_not_called()
    cleanup.assert_not_called()
    delete.assert_not_called()


@pytest.mark.asyncio
async def test_herdr_reconcile_keeps_routing_when_metadata_is_unavailable(monkeypatch):
    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.services.herdr_inbox_service import HerdrInboxService

    service = HerdrInboxService(socket_path="/tmp/unused-cao008.sock")
    service.register_terminal("uncertain", "pane-1")

    def snapshot():
        return {"panes": [], "tabs": [], "workspaces": []}

    monkeypatch.setattr(service, "_fetch_snapshot", snapshot)

    def unavailable(_):
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(database, "get_terminal_metadata", unavailable)
    await service._reconcile()
    assert service._terminal_to_pane["uncertain"] == "pane-1"
    assert service._pane_to_terminal["pane-1"] == "uncertain"
