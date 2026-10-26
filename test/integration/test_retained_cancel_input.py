"""A retained cancelled shell is not a ready message receiver."""

import asyncio
import threading
from test.api.conftest import client
from test.integration.test_cancelled_worktree_creation import repo
from test.services.test_terminal_service_inbox_registration import SESSION_NAME, create_mocks
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.backends.base import TerminalCleanupOutcome, TerminalCleanupResult
from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.terminal import TerminalInputBlockedError, TerminalStatus
from cli_agent_orchestrator.services import provider_lifecycle
from cli_agent_orchestrator.services import terminal_service as terminal

KEY = "a1b2c3d4"


async def retain_shell(
    create_mocks,
    repo,
    monkeypatch,
    caller,
    *,
    defer_init=False,
    cleanup_outcome=TerminalCleanupOutcome.UNKNOWN,
):
    assert db.engine.dialect.name == "postgresql"
    entered, release = threading.Event(), threading.Event()
    backend = create_mocks.backend
    create_mocks.generate_terminal_id.return_value = KEY
    backend.supports_event_inbox.return_value = False
    backend.kill_window.return_value = False
    backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(cleanup_outcome)
    window = backend.create_window.return_value
    before = set(terminal._create_compensations)
    monkeypatch.setattr(terminal, "_initial_task_pending_ids", set())
    monitor, fifo = Mock(), Mock()
    monitor.get_status.return_value = TerminalStatus.UNKNOWN
    monitor.turn_state.return_value = (0, False)
    monitor.notify_input_sent.return_value = 1
    monkeypatch.setattr(terminal, "status_monitor", monitor)
    monkeypatch.setattr(terminal, "fifo_manager", fifo)
    monkeypatch.setattr(terminal, "inject_memory_context", lambda message, *_a, **_k: message)

    def create_window(*_a, **_k):
        entered.set()
        assert release.wait(10)
        return window

    backend.create_window.side_effect = create_window
    task = asyncio.create_task(
        terminal.create_terminal(
            "claude_code",
            "developer",
            session_name=SESSION_NAME,
            use_worktree=True,
            working_directory=str(repo),
            caller_id="parent-fixture" if caller else None,
            defer_init=defer_init,
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel("cancelled before provider initialization")
        release.set()
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await task
        assert cancelled.value.args == ("cancelled before provider initialization",)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*(terminal._create_compensations - before), return_exceptions=True)
    assert (db.get_terminal_metadata(KEY) is None) == (
        cleanup_outcome in (TerminalCleanupOutcome.ABSENT, TerminalCleanupOutcome.DELETED)
    )
    create_mocks.provider_manager.create_provider.assert_not_called()
    fifo.start_reader.assert_not_called()
    provider = Mock()
    provider.requires_turn_receipt = False
    provider.assume_processing_on_dispatch = False
    provider.paste_enter_count = 1
    provider.paste_submit_delay = 0
    create_mocks.provider_manager.get_provider.return_value = provider
    return backend, monitor, provider


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", [False, True])
async def test_retained_cancel_is_public_initialization_failure(
    create_mocks, repo, monkeypatch, client, caller
):
    backend, monitor, provider = await retain_shell(create_mocks, repo, monkeypatch, caller)
    response = client.get(f"/terminals/{KEY}")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "error"
    assert response.json()["deferred_init_failure"]["kind"] == "creation_cancelled"
    provider.initialize.assert_not_called()
    backend.send_keys.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", [False, True])
async def test_public_input_does_not_reach_retained_cancelled_shell(
    create_mocks, repo, monkeypatch, client, caller
):
    backend, monitor, provider = await retain_shell(create_mocks, repo, monkeypatch, caller)
    response = client.post(f"/terminals/{KEY}/input", params={"message": "controlled task"})
    assert (
        backend.send_keys.call_count == 0
    ), f"transport reached cancelled shell; HTTP {response.status_code}"
    assert response.status_code == 409
    assert response.json()["detail"]["action"] == "reconcile"
    assert response.json()["detail"]["delivery_may_have_occurred"] is False
    provider.initialize.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [TerminalStatus.UNKNOWN, TerminalStatus.IDLE])
@pytest.mark.parametrize("receipt", [False, True])
async def test_durable_failure_blocks_before_adapter_and_receipt(
    create_mocks, repo, monkeypatch, status, receipt
):
    backend, monitor, provider = await retain_shell(create_mocks, repo, monkeypatch, False)
    monitor.get_status.return_value = status
    provider.requires_turn_receipt = receipt
    assert KEY not in provider_lifecycle._startups
    assert KEY not in terminal._initial_task_pending_ids
    create_mocks.provider_manager.get_provider.reset_mock()
    with pytest.raises(TerminalInputBlockedError) as raised:
        terminal.send_input(KEY, "controlled task", attach_turn_receipt=receipt)
    assert raised.value.action == "reconcile"
    assert raised.value.delivery_may_have_occurred is False
    create_mocks.provider_manager.get_provider.assert_not_called()
    provider.prepare_turn_receipt.assert_not_called()
    backend.send_keys.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_failure_uses_durable_sidecar_on_database_write_outage(
    create_mocks, repo, monkeypatch, client
):
    write = Mock(side_effect=RuntimeError("controlled failure write outage"))
    monkeypatch.setattr(terminal, "update_terminal_deferred_init_failure", write)
    backend, monitor, provider = await retain_shell(create_mocks, repo, monkeypatch, False)
    assert write.call_count == terminal._DEFERRED_INIT_FAILURE_PERSIST_ATTEMPTS
    assert db.get_terminal_metadata(KEY)["deferred_init_failure"] is None
    assert terminal._deferred_failure_fallback_path(KEY).is_file()
    assert KEY not in provider_lifecycle._startups
    # A fresh observer can use durable evidence without a monitor failure latch.
    monitor.get_status.return_value = TerminalStatus.IDLE
    response = client.get(f"/terminals/{KEY}")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "error"
    assert client.post(f"/terminals/{KEY}/input", params={"message": "task"}).status_code == 409
    backend.send_keys.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
@pytest.mark.parametrize("defer_init", [False, True])
@pytest.mark.parametrize("caller", [False, True])
async def test_all_failure_stores_unavailable_retains_local_admission(
    create_mocks, repo, monkeypatch, raises, defer_init, caller
):
    monkeypatch.setattr(
        terminal,
        "update_terminal_deferred_init_failure",
        Mock(side_effect=RuntimeError("write outage")),
    )
    fallback = Mock(side_effect=OSError("sidecar outage")) if raises else Mock(return_value=False)
    monkeypatch.setattr(terminal, "_publish_deferred_failure_fallback", fallback)
    backend, monitor, provider = await retain_shell(
        create_mocks, repo, monkeypatch, caller, defer_init=defer_init
    )
    assert provider_lifecycle._startups[KEY].uncertain is True
    assert KEY in terminal._initial_task_pending_ids
    with pytest.raises(TerminalInputBlockedError):
        terminal.send_input(KEY, "controlled task")
    backend.send_keys.assert_not_called()
    backend.kill_window.assert_not_called()
    # No claim of durability across process restart when both stores are down.
    assert db.get_terminal_metadata(KEY)["deferred_init_failure"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [TerminalStatus.UNKNOWN, TerminalStatus.WAITING_USER_ANSWER])
async def test_no_failure_marker_preserves_ordinary_input(create_mocks, repo, monkeypatch, status):
    backend, monitor, provider = await retain_shell(create_mocks, repo, monkeypatch, False)
    assert db.update_terminal_deferred_init_failure(KEY, None)
    monitor.get_status.return_value = status
    terminal.send_input(KEY, "controlled answer", attach_turn_receipt=False)
    backend.send_keys.assert_called_once()


@pytest.mark.asyncio
async def test_failure_published_during_preparation_is_rechecked_before_transport(
    create_mocks, repo, monkeypatch
):
    backend, monitor, provider = await retain_shell(create_mocks, repo, monkeypatch, False)
    assert db.update_terminal_deferred_init_failure(KEY, None)

    def publish(*_a, **_k):
        assert db.update_terminal_deferred_init_failure(
            KEY, {"phase": "deferred_init", "kind": "creation_cancelled", "message": "controlled"}
        )

    provider.mark_input_received.side_effect = publish
    with pytest.raises(TerminalInputBlockedError) as raised:
        terminal.send_input(KEY, "controlled task")
    assert raised.value.delivery_may_have_occurred is False
    backend.send_keys.assert_not_called()
    monitor.abort_turn.assert_called_once_with(KEY, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("sidecar", [False, True])
@pytest.mark.parametrize("outcome", [TerminalCleanupOutcome.ABSENT, TerminalCleanupOutcome.DELETED])
async def test_exact_absence_removes_row_and_cancel_failure_evidence(
    create_mocks, repo, monkeypatch, sidecar, outcome
):
    if sidecar:
        monkeypatch.setattr(
            terminal,
            "update_terminal_deferred_init_failure",
            Mock(side_effect=RuntimeError("failure-write outage")),
        )
    backend, monitor, provider = await retain_shell(
        create_mocks, repo, monkeypatch, False, cleanup_outcome=outcome
    )
    assert db.get_terminal_metadata(KEY) is None
    assert not terminal._deferred_failure_fallback_path(KEY).exists()
    assert KEY not in provider_lifecycle._startups
    assert KEY not in terminal._initial_task_pending_ids
    backend.cleanup_terminal_exact.assert_called_once_with(
        KEY, SESSION_NAME, backend.create_window.return_value, close=False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("sidecar", [False, True])
async def test_fresh_observer_without_process_marks_still_blocks_failed_initialization(
    create_mocks, repo, monkeypatch, client, sidecar
):
    if sidecar:
        monkeypatch.setattr(
            terminal,
            "update_terminal_deferred_init_failure",
            Mock(side_effect=RuntimeError("failure-write outage")),
        )
    backend, monitor, provider = await retain_shell(
        create_mocks, repo, monkeypatch, True, defer_init=True
    )
    assert (
        terminal.should_retain_deferred_failure_tombstone(KEY, db.get_terminal_metadata(KEY))
        is True
    )
    monkeypatch.setattr(terminal, "_initial_task_pending_ids", set())
    monkeypatch.setattr(terminal, "_active_deferred_init_external_owner_ids", set())
    monkeypatch.setattr(provider_lifecycle, "_startups", {})
    fresh = Mock()
    fresh.get_status.return_value = TerminalStatus.UNKNOWN
    fresh.turn_state.return_value = (0, False)
    monkeypatch.setattr(terminal, "status_monitor", fresh)
    create_mocks.provider_manager.get_provider.reset_mock()
    response = client.get(f"/terminals/{KEY}")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "error"
    with pytest.raises(TerminalInputBlockedError) as raised:
        terminal.send_input(KEY, "queued task", task_delivery=True)
    assert raised.value.action == "reconcile"
    create_mocks.provider_manager.get_provider.assert_not_called()
    fresh.get_status.assert_not_called()
    backend.send_keys.assert_not_called()
