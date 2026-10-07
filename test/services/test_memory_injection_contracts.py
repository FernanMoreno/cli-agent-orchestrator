"""Real requester state gates curator reuse; transport failure falls back safely."""

import asyncio
import json
import threading
from unittest.mock import MagicMock

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import memory_service, settings_service, terminal_service
from cli_agent_orchestrator.services.memory_service import MemoryService
from cli_agent_orchestrator.services.vault.config import VaultConfig


@pytest.fixture
def injection(tmp_path, monkeypatch, isolated_memory_db):
    monkeypatch.setattr(settings_service, "get_vault_config", lambda: VaultConfig(enabled=False))
    monkeypatch.setattr(terminal_service, "get_working_directory", lambda _id: str(tmp_path))
    for identity, profile in (("worker", "developer"), ("curator", "memory_manager")):
        database.create_terminal(
            identity, "same-session", identity, "mock_cli", agent_profile=profile
        )
    service = MemoryService(base_dir=tmp_path / "memory")
    asyncio.run(service.store("Use the local fallback", scope="global", key="fallback"))
    provider = MagicMock()
    monitor = MagicMock()
    monitor.get_status.return_value = TerminalStatus.IDLE
    from cli_agent_orchestrator.providers.manager import provider_manager
    from cli_agent_orchestrator.services.status_monitor import status_monitor

    monkeypatch.setattr(provider_manager, "get_provider", provider)
    monkeypatch.setattr(status_monitor, "get_status", monitor.get_status)
    send = MagicMock()
    output = MagicMock(side_effect=["", "<cao-memory>current response</cao-memory>"])
    monkeypatch.setattr(terminal_service, "send_input", send)
    monkeypatch.setattr(terminal_service, "get_output", output)
    monkeypatch.setattr(memory_service, "_curator_locks", {})
    return service, send, output, provider, monitor


def _metadata(value):
    with database.SessionLocal() as session:
        terminal = session.get(database.TerminalModel, "curator")
        terminal.metadata_json = json.dumps(value)
        session.commit()


def test_live_database_context_and_persistent_policy_stamp_allow_dispatch(injection):
    service, send, output, _provider, monitor = injection
    assert (
        service.get_curated_memory_context("worker", "Task")
        == "<cao-memory>current response</cao-memory>"
    )
    send.assert_called_once_with("curator", "Task")
    assert (
        database.get_terminal_metadata("curator")["metadata"][
            memory_service._CURATOR_POLICY_METADATA_KEY
        ]
        == []
    )
    assert monitor.get_status.call_count == 2
    assert output.call_count == 2


@pytest.mark.parametrize(
    "metadata", ["malformed", {"vault_injection_policy": [["old", "global", None, True]]}]
)
def test_unavailable_or_changed_durable_policy_refuses_curator_reuse(injection, metadata):
    service, send, output, _provider, _monitor = injection
    _metadata(metadata)
    result = service.get_curated_memory_context("worker", "Task")
    assert "Use the local fallback" in result
    send.assert_not_called()
    output.assert_not_called()
    assert database.get_terminal_metadata("curator")["metadata"] == metadata


def test_curator_from_another_session_is_never_dispatched(injection):
    service, send, output, _provider, _monitor = injection
    with database.SessionLocal() as session:
        session.get(database.TerminalModel, "curator").tmux_session = "foreign-session"
        session.commit()
    assert "Use the local fallback" in service.get_curated_memory_context("worker", "Task")
    send.assert_not_called()
    output.assert_not_called()


def test_missing_provider_falls_back_before_transport(injection):
    service, send, output, provider, _monitor = injection
    provider.return_value = None
    assert "Use the local fallback" in service.get_curated_memory_context("worker", "Task")
    provider.assert_called_once_with("curator")
    send.assert_not_called()
    output.assert_not_called()


def test_concurrent_dispatch_lock_falls_back_without_waiting_or_sending(injection):
    service, send, output, _provider, monitor = injection
    lock = threading.Lock()
    lock.acquire()
    memory_service._curator_locks["curator"] = lock
    try:
        assert "Use the local fallback" in service.get_curated_memory_context("worker", "Task")
        assert lock.locked()
        send.assert_not_called()
        output.assert_not_called()
        monitor.get_status.assert_not_called()
    finally:
        lock.release()


@pytest.mark.parametrize(
    "response",
    [
        "old output changed entirely",
        {"output": "old output"},
        {"output": 123},
        "old output<cao-memory>unterminated",
    ],
)
def test_replaced_invalid_or_unclosed_buffer_never_injects_unverified_response(injection, response):
    service, send, output, _provider, _monitor = injection
    output.side_effect = [{"output": "old output"}, response]
    result = service.get_curated_memory_context("worker", "Task")
    assert "Use the local fallback" in result
    assert "unterminated" not in result
    send.assert_called_once()


def test_nonstr_initial_buffer_and_provider_processing_are_bounded(injection):
    service, send, output, _provider, monitor = injection
    monitor.get_status.side_effect = [
        TerminalStatus.IDLE,
        TerminalStatus.PROCESSING,
        TerminalStatus.COMPLETED,
    ]
    output.side_effect = [None, {"output": "<cao-memory>new response</cao-memory>"}]
    assert (
        service.get_curated_memory_context("worker", "Task")
        == "<cao-memory>new response</cao-memory>"
    )
    send.assert_called_once()
    assert monitor.get_status.call_count == 3


def test_transport_exception_releases_curator_lock_and_preserves_local_fallback(injection):
    service, send, output, _provider, _monitor = injection
    send.side_effect = RuntimeError("transport unavailable")
    assert "Use the local fallback" in service.get_curated_memory_context("worker", "Task")
    assert not memory_service._curator_locks["curator"].locked()
    assert output.call_count == 1


def test_failed_settings_read_keeps_available_memory_enabled(injection, monkeypatch):
    service, _send, _output, _provider, _monitor = injection

    def unavailable():
        raise OSError("settings unavailable")

    monkeypatch.setattr(settings_service, "is_memory_enabled", unavailable)
    assert "Use the local fallback" in service.get_memory_context_for_terminal("worker")


def test_tmux_directory_failure_preserves_database_requester_context(injection, monkeypatch):
    service, send, _output, _provider, _monitor = injection

    def unavailable(_id):
        raise RuntimeError("pane no longer exists")

    monkeypatch.setattr(terminal_service, "get_working_directory", unavailable)
    # Session identity is available from durable metadata even if tmux cannot resolve cwd.
    context = service._get_terminal_context("worker")
    assert context["terminal_id"] == "worker"
    assert context["session_name"] == "same-session"
    assert context["cwd"] is None
    send.assert_not_called()
