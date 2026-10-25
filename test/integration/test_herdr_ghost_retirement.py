"""Herdr ghost discovery cannot bypass ordinary provider/resource retirement."""

import asyncio
import logging
import threading
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.backends import registry
from cli_agent_orchestrator.backends.base import TerminalCleanupOutcome, TerminalCleanupResult
from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.providers.manager import ProviderManager
from cli_agent_orchestrator.services import herdr_inbox_registry
from cli_agent_orchestrator.services import herdr_inbox_service as herdr
from cli_agent_orchestrator.services import remote_terminal_service, terminal_service

KEY = "herdr-ghost-owner"
SESSION = "cao-herdr-ghost-session"
WINDOW = "ghost-window"


@pytest.fixture
def ghost(monkeypatch, tmp_path):
    db.create_terminal(KEY, SESSION, WINDOW, "mock_cli", "developer")
    manager = ProviderManager()
    adapter = manager.create_provider("mock_cli", KEY, SESSION, WINDOW)
    adapter.cleanup = Mock(return_value=True)
    monkeypatch.setattr(terminal_service, "provider_manager", manager)
    monkeypatch.setattr(terminal_service, "TERMINAL_LOG_DIR", tmp_path)
    monkeypatch.setattr(terminal_service, "capture_terminal_snapshot", db.get_terminal_metadata)
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _key: None)
    fifo, monitor = Mock(), Mock()
    fifo.stop_reader.return_value = True
    monkeypatch.setattr(terminal_service, "fifo_manager", fifo)
    monkeypatch.setattr(terminal_service, "status_monitor", monitor)
    monkeypatch.setattr(terminal_service, "_remove_interaction_capability_files", Mock())
    backend = Mock()
    backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
        TerminalCleanupOutcome.ABSENT
    )
    monkeypatch.setattr(registry, "_backend", backend)
    service = herdr.HerdrInboxService(socket_path="/tmp/caos-test-unused.sock")
    monkeypatch.setattr(herdr_inbox_registry, "_herdr_inbox_service", service)
    snapshot = {
        "workspaces": [{"workspace_id": "workspace", "label": SESSION}],
        "tabs": [],
        "panes": [],
    }
    monkeypatch.setattr(service, "_fetch_snapshot", Mock(return_value=snapshot))
    monkeypatch.setattr(service, "_live_tab_labels", lambda: set())
    return service, manager, adapter, backend, fifo, monitor


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["startup", "crosscheck", "mapped"])
@pytest.mark.parametrize("exact,cleanup", [("absent", True), ("absent", False), ("unknown", True)])
async def test_ghost_retirement_cleans_ready_cache_or_preserves_unproven_owner(
    ghost, monkeypatch, path, exact, cleanup, caplog
):
    assert db.engine.dialect.name == "postgresql"
    caplog.set_level(logging.INFO, logger=herdr.__name__)
    service, manager, adapter, backend, fifo, monitor = ghost
    adapter.cleanup.return_value = cleanup
    backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
        TerminalCleanupOutcome.ABSENT if exact == "absent" else TerminalCleanupOutcome.UNKNOWN
    )
    terminal_service._write_deferred_init_complete_fallback(KEY)
    sidecar = terminal_service._deferred_init_complete_fallback_path(KEY)
    if path == "mapped":
        service.register_terminal(KEY, "stale-pane")
        service._fetch_snapshot.return_value = {"workspaces": [], "tabs": [], "panes": []}
    if path == "startup":
        await service._startup_db_cleanup()
    else:
        await service._reconcile()
    if exact == "absent" and cleanup:
        assert db.get_terminal_metadata(KEY) is None
        assert KEY not in manager._providers
        assert not sidecar.exists()
        adapter.cleanup.assert_called_once_with()
        fifo.stop_reader.assert_called_once_with(KEY)
        monitor.clear_terminal.assert_called_once_with(KEY)
        if path == "mapped":
            assert "stale-pane" not in service._pane_to_terminal
            assert any("1 deleted" in record.getMessage() for record in caplog.records)
    else:
        assert db.get_terminal_metadata(KEY) is not None
        assert manager._providers[KEY] is adapter
        assert sidecar.exists()
        if exact == "unknown":
            adapter.cleanup.assert_not_called()
            fifo.stop_reader.assert_not_called()
            monitor.clear_terminal.assert_not_called()
        if path == "mapped":
            assert service._pane_to_terminal["stale-pane"] == KEY
            assert service._terminal_to_pane[KEY] == "stale-pane"
    backend.kill_window.assert_not_called()
    assert manager._restore_locks == {}


@pytest.mark.asyncio
async def test_live_tab_with_unresolved_pane_preserves_row_cache_and_routing(ghost, monkeypatch):
    service, manager, adapter, backend, fifo, monitor = ghost
    service.register_terminal(KEY, "stale-pane")
    service._fetch_snapshot.return_value = {"workspaces": [], "tabs": [], "panes": []}
    monkeypatch.setattr(service, "_live_tab_labels", lambda: {WINDOW})
    backend.get_pane_id.side_effect = RuntimeError("controlled pane resolution failure")
    row = db.get_terminal_metadata(KEY)
    await service._reconcile()
    assert db.get_terminal_metadata(KEY) == row
    assert manager._providers[KEY] is adapter
    assert service._pane_to_terminal["stale-pane"] == KEY
    assert service._terminal_to_pane[KEY] == "stale-pane"
    adapter.cleanup.assert_not_called()
    fifo.stop_reader.assert_not_called()
    monitor.clear_terminal.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["startup", "crosscheck"])
async def test_existing_fresh_snapshot_guard_ignores_stale_discovery(ghost, path):
    service, manager, adapter, backend, fifo, monitor = ghost
    discovery = service._fetch_snapshot.return_value
    fresh = {**discovery, "tabs": [{"workspace_id": "workspace", "label": WINDOW}]}
    service._fetch_snapshot.side_effect = [discovery, fresh]
    row = db.get_terminal_metadata(KEY)
    if path == "startup":
        await service._startup_db_cleanup()
    else:
        await service._reconcile()
    assert db.get_terminal_metadata(KEY) == row
    assert manager._providers[KEY] is adapter
    adapter.cleanup.assert_not_called()
    backend.cleanup_terminal_exact.assert_not_called()
    fifo.stop_reader.assert_not_called()
    monitor.clear_terminal.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["startup", "mapped"])
async def test_cancelled_retirement_drains_worker_and_loop_can_reap_completed_routing(ghost, path):
    service, manager, adapter, backend, fifo, monitor = ghost
    service.register_terminal(KEY, "stale-pane")
    if path == "mapped":
        service._fetch_snapshot.return_value = {"workspaces": [], "tabs": [], "panes": []}
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()

    def cleanup():
        entered.set()
        try:
            assert release.wait(timeout=8)
            return True
        finally:
            stopped.set()

    adapter.cleanup.side_effect = cleanup
    task = asyncio.create_task(
        service._startup_db_cleanup() if path == "startup" else service._reconcile()
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        tick = asyncio.Event()
        asyncio.get_running_loop().call_soon(tick.set)
        await asyncio.wait_for(tick.wait(), timeout=1)
        assert service._pane_to_terminal["stale-pane"] == KEY
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled() and stopped.is_set()
    assert db.get_terminal_metadata(KEY) is None
    assert KEY not in manager._providers
    # Cancellation may preempt applying the worker result. Retry may forget
    # routing only after exact absence and a constant-time empty-cache check.
    await service._reconcile()
    assert "stale-pane" not in service._pane_to_terminal
    assert KEY not in service._terminal_to_pane
    adapter.cleanup.assert_called_once_with()


@pytest.mark.asyncio
async def test_missing_row_with_cached_owner_preserves_private_resources_and_routing(ghost):
    service, manager, adapter, backend, fifo, monitor = ghost
    service.register_terminal(KEY, "stale-pane")
    service._fetch_snapshot.return_value = {"workspaces": [], "tabs": [], "panes": []}
    db.delete_terminal(KEY)
    await service._reconcile()
    assert manager._providers[KEY] is adapter
    assert service._pane_to_terminal["stale-pane"] == KEY
    adapter.cleanup.assert_not_called()
    fifo.stop_reader.assert_not_called()
    monitor.clear_terminal.assert_not_called()


@pytest.mark.asyncio
async def test_remote_placement_is_not_authority_for_local_ghost_cleanup(ghost, monkeypatch):
    service, manager, adapter, backend, fifo, monitor = ghost
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _key: {"remote": True})
    await service._startup_db_cleanup()
    assert db.get_terminal_metadata(KEY) is not None
    assert manager._providers[KEY] is adapter
    adapter.cleanup.assert_not_called()
    backend.cleanup_terminal_exact.assert_not_called()


@pytest.mark.asyncio
async def test_changed_registration_since_discovery_is_retained_without_native_cleanup(ghost):
    service, manager, adapter, backend, fifo, monitor = ghost
    original = service._fetch_snapshot.return_value
    calls = 0

    def snapshot():
        nonlocal calls
        calls += 1
        if calls == 2:
            with db.SessionLocal() as session:
                row = session.get(db.TerminalModel, KEY)
                row.tmux_window = "replacement-window"
                session.commit()
        return original

    service._fetch_snapshot.side_effect = snapshot
    await service._startup_db_cleanup()
    assert db.get_terminal_metadata(KEY)["tmux_window"] == "replacement-window"
    assert manager._providers[KEY] is adapter
    adapter.cleanup.assert_not_called()
    backend.cleanup_terminal_exact.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("same_compact_pane", [False, True])
async def test_worker_result_does_not_unregister_a_new_routing_owner(
    ghost, monkeypatch, same_compact_pane
):
    service, manager, adapter, backend, fifo, monitor = ghost
    service.register_terminal(KEY, "old-pane")
    owned = terminal_service._await_provider_operation
    replacement = None
    pane = "old-pane" if same_compact_pane else "new-pane"

    async def publish_replacement_after_worker(operation, *args, **kwargs):
        nonlocal replacement
        result = await owned(operation, *args, **kwargs)
        if getattr(operation, "__name__", "") == "_cleanup_ghost_session":
            assert db.get_terminal_metadata(KEY) is None
            db.create_terminal(
                KEY, "replacement-session", "replacement-window", "mock_cli", "developer"
            )
            replacement = manager.create_provider(
                "mock_cli", KEY, "replacement-session", "replacement-window"
            )
            replacement.cleanup = Mock(return_value=True)
            service.register_terminal(KEY, pane)
        return result

    monkeypatch.setattr(
        terminal_service, "_await_provider_operation", publish_replacement_after_worker
    )
    await service._startup_db_cleanup()
    assert db.get_terminal_metadata(KEY)["tmux_session"] == "replacement-session"
    assert manager._providers[KEY] is replacement
    assert service._terminal_to_pane[KEY] == pane
    assert service._pane_to_terminal[pane] == KEY
    replacement.cleanup.assert_not_called()


@pytest.mark.asyncio
async def test_observed_absence_cannot_retire_a_row_that_appeared_while_queued(ghost):
    service, manager, adapter, backend, fifo, monitor = ghost
    row = db.get_terminal_metadata(KEY)
    assert (
        await terminal_service._await_provider_operation(
            service._delete_ghost_terminal, KEY, expected_metadata=None
        )
        is False
    )
    assert db.get_terminal_metadata(KEY) == row
    assert manager._providers[KEY] is adapter
    adapter.cleanup.assert_not_called()
    backend.cleanup_terminal_exact.assert_not_called()
    fifo.stop_reader.assert_not_called()
    monitor.clear_terminal.assert_not_called()
