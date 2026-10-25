"""Teardown must exclude cold restoration until durable row deletion ends."""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.providers import manager as module
from cli_agent_orchestrator.services import (
    cleanup_service,
    flow_service,
    remote_terminal_service,
    session_service,
    terminal_service,
)


@pytest.mark.parametrize(
    "teardown_path", ["terminal", "flow_missing", "flow_live", "session", "session_sweep", "stale"]
)
@pytest.mark.parametrize("restore_in_cleanup_gap", [False, True])
def test_terminal_delete_keeps_cold_getter_out_until_row_is_gone(
    isolated_memory_db, monkeypatch, tmp_path, restore_in_cleanup_gap, teardown_path
):
    assert db.engine.dialect.name == "postgresql"
    key = "row-retirement"
    db.create_terminal(key, "cao-retirement-session", "worker", "codex", "reviewer")
    manager = module.ProviderManager()
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _key: None)
    old = manager.get_provider(key)
    old.cleanup = Mock(return_value=True)
    monkeypatch.setattr(terminal_service, "provider_manager", manager)
    monkeypatch.setattr(terminal_service, "terminal_dispatch_lock", lambda *_a: nullcontext())
    monkeypatch.setattr(terminal_service, "ensure_terminal_is_not_work_owned", lambda _k: None)
    monkeypatch.setattr(terminal_service, "capture_terminal_snapshot", db.get_terminal_metadata)
    gap_providers = []

    def dismantle(k, _m, **_kwargs):
        assert manager.cleanup_provider(k) is True
        if restore_in_cleanup_gap:
            restored = manager.get_provider(k)
            restored.cleanup = Mock(return_value=True)
            gap_providers.append(restored)
        return True

    monkeypatch.setattr(terminal_service, "dismantle_terminal_runtime", dismantle)
    registered, completed = threading.Event(), threading.Event()
    guard = manager._restore_locks_guard

    class Guard:
        def __enter__(self):
            return guard.__enter__()

        def __exit__(self, *args):
            result = guard.__exit__(*args)
            if threading.current_thread().name == "deletion-getter":
                registered.set()
            return result

    monkeypatch.setattr(manager, "_restore_locks_guard", Guard())

    def get():
        threading.current_thread().name = "deletion-getter"
        try:
            return manager.get_provider(key)
        finally:
            completed.set()

    target, delete_name, real_delete = terminal_service, "db_delete_terminal", db.delete_terminal
    invoke = lambda: terminal_service.delete_terminal(key)

    if teardown_path.startswith("flow"):
        target, delete_name, real_delete = (
            flow_service,
            "delete_terminals_by_ids",
            db.delete_terminals_by_ids,
        )
        monkeypatch.setattr(flow_service, "provider_manager", manager)
        monkeypatch.setattr(flow_service, "_verified_flow_terminal_rows", lambda *_a: [])
        monkeypatch.setattr(
            flow_service,
            "list_current_session_terminals",
            lambda *_a, **_k: [db.get_terminal_metadata(key)],
        )
        monkeypatch.setattr(
            flow_service, "should_retain_deferred_failure_tombstone", lambda *_a: False
        )
        monkeypatch.setattr(flow_service, "_is_terminal_busy", lambda _k: False)
        backend = MagicMock()
        backend.session_exists.return_value = teardown_path == "flow_live"
        monkeypatch.setattr(flow_service, "get_backend", lambda: backend)
        monkeypatch.setattr(flow_service, "fifo_manager", MagicMock())
        monkeypatch.setattr(flow_service, "status_monitor", MagicMock())
        # Exercise the gap after the Flow's first cleanup as well.
        initial_cleanup = manager.cleanup_provider

        def flow_cleanup(k):
            result = initial_cleanup(k)
            if restore_in_cleanup_gap:
                restored = manager.get_provider(k)
                restored.cleanup = Mock(return_value=True)
                gap_providers.append(restored)
            return result

        monkeypatch.setattr(manager, "cleanup_provider", flow_cleanup)
        invoke = lambda: flow_service._recycle_flow_session(
            "retirement-flow", "cao-retirement-session"
        )
    elif teardown_path.startswith("session"):
        monkeypatch.setattr(remote_terminal_service, "session_terminals", lambda _s: None)
        monkeypatch.setattr(session_service, "terminal_dispatch_lock", lambda *_a: nullcontext())
        backend = MagicMock()
        backend.session_exists_strict.return_value = False
        monkeypatch.setattr(session_service, "get_backend", lambda: backend)
        if teardown_path == "session_sweep":
            target, delete_name, real_delete = (
                session_service,
                "delete_terminals_by_ids",
                db.delete_terminals_by_ids,
            )
            monkeypatch.setattr(
                terminal_service,
                "delete_terminal_row",
                Mock(side_effect=RuntimeError("first delete failed")),
            )

        def invoke():
            result = session_service.delete_session("cao-retirement-session")
            assert result == {"deleted": ["cao-retirement-session"], "errors": []}
            return True

    elif teardown_path == "stale":
        fake_db = MagicMock()
        fake_db.query.return_value.filter.return_value.all.return_value = [SimpleNamespace(id=key)]
        fake_db.query.return_value.filter.return_value.delete.return_value = 0
        session_factory = MagicMock()
        session_factory.return_value.__enter__.return_value = fake_db
        monkeypatch.setattr(cleanup_service, "SessionLocal", session_factory)
        monkeypatch.setattr(cleanup_service, "provider_manager", manager)
        monkeypatch.setattr(cleanup_service, "fifo_manager", MagicMock())
        monkeypatch.setattr(cleanup_service, "status_monitor", MagicMock())
        monkeypatch.setattr(cleanup_service, "delete_old_handoff_results", lambda **_k: 0)
        for name in ("LOG_DIR", "TERMINAL_LOG_DIR", "MEMORY_BASE_DIR"):
            monkeypatch.setattr(cleanup_service, name, tmp_path / name)
        monkeypatch.setattr(
            terminal_service, "should_retain_deferred_failure_tombstone", lambda *_a: False
        )
        initial_cleanup = manager.cleanup_provider

        def stale_cleanup(k):
            result = initial_cleanup(k)
            if restore_in_cleanup_gap:
                restored = manager.get_provider(k)
                restored.cleanup = Mock(return_value=True)
                gap_providers.append(restored)
            return result

        monkeypatch.setattr(manager, "cleanup_provider", stale_cleanup)

        def invoke():
            cleanup_service.cleanup_old_data()
            return db.get_terminal_metadata(key) is None

    futures = []
    with ThreadPoolExecutor(max_workers=1) as workers:

        def delete(k):
            futures.append(workers.submit(get))
            assert registered.wait(timeout=5)
            completed.wait(timeout=0.2)
            return real_delete(k)

        monkeypatch.setattr(target, delete_name, delete)
        assert invoke() is True
        assert len(futures) == 1
        with pytest.raises(ValueError, match="not found in database"):
            futures[0].result(timeout=5)
    assert key not in manager._providers
    assert db.get_terminal_metadata(key) is None
    old.cleanup.assert_called_once_with()
    for restored in gap_providers:
        restored.cleanup.assert_called_once_with()
    assert manager._restore_locks == {}


@pytest.mark.offline_database
def test_observer_teardown_can_complete_a_provider_lookup(monkeypatch):
    """A monitor holding its own lock must not wait behind a retirement lease."""
    manager = module.ProviderManager()
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _key: None)
    monkeypatch.setattr(
        terminal_service, "DatabaseTarget", SimpleNamespace(from_settings=lambda **_k: None)
    )
    provider = manager.create_provider("mock_cli", "monitor-order", "s", "w")
    provider.cleanup = Mock(return_value=True)
    monkeypatch.setattr(terminal_service, "provider_manager", manager)
    monkeypatch.setattr(terminal_service, "terminal_dispatch_lock", lambda *_a: nullcontext())
    monkeypatch.setattr(terminal_service, "ensure_terminal_is_not_work_owned", lambda _k: None)
    monkeypatch.setattr(terminal_service, "capture_terminal_snapshot", lambda _k: None)
    monkeypatch.setattr(terminal_service, "db_delete_terminal", lambda _k: True)
    with ThreadPoolExecutor(max_workers=1) as workers:

        def dismantle(k, _metadata):
            # This is the monitor-lock -> manager-lease direction, observed
            # while the deleting thread still has observer teardown to finish.
            assert workers.submit(manager.get_provider, k).result(timeout=2) is provider
            return manager.cleanup_provider(k)

        monkeypatch.setattr(terminal_service, "dismantle_terminal_runtime", dismantle)
        assert terminal_service.delete_terminal("monitor-order") is True
    assert manager._restore_locks == {}


@pytest.mark.offline_database
@pytest.mark.parametrize("outcome", [False, RuntimeError("private cleanup failed")])
def test_final_cached_cleanup_failure_keeps_row_and_sidecars(monkeypatch, outcome):
    manager = module.ProviderManager()
    provider = manager.create_provider("mock_cli", "retained-final", "s", "w")
    provider.cleanup = (
        Mock(side_effect=outcome) if isinstance(outcome, Exception) else Mock(return_value=outcome)
    )
    monkeypatch.setattr(terminal_service, "provider_manager", manager)
    delete = Mock()
    sidecar = Mock()
    monkeypatch.setattr(terminal_service, "db_delete_terminal", delete)
    monkeypatch.setattr(terminal_service, "_delete_deferred_failure_fallback", sidecar)
    monkeypatch.setattr(terminal_service, "_delete_deferred_init_complete_fallback", sidecar)
    assert terminal_service.delete_terminal_row("retained-final", None) is False
    delete.assert_not_called()
    sidecar.assert_not_called()
    assert manager._providers["retained-final"] is provider
    assert manager._restore_locks == {}


@pytest.mark.offline_database
def test_retirement_does_not_repeat_cold_cleanup_and_releases_after_db_error(monkeypatch):
    manager = module.ProviderManager()
    monkeypatch.setattr(terminal_service, "provider_manager", manager)
    cold_cleanup = Mock(side_effect=AssertionError("cold cleanup repeated"))
    monkeypatch.setattr(manager, "_cleanup_provider_locked", cold_cleanup)
    error = RuntimeError("row delete failed")
    monkeypatch.setattr(terminal_service, "db_delete_terminal", Mock(side_effect=error))
    with pytest.raises(RuntimeError) as caught:
        terminal_service.delete_terminal_row("cold-final", None)
    assert caught.value is error
    cold_cleanup.assert_not_called()
    assert manager._restore_locks == {}


@pytest.mark.parametrize("path", ["session", "session_sweep", "flow_live", "flow_missing"])
def test_final_cleanup_deferral_is_not_swept_or_reported_deleted(
    isolated_memory_db, monkeypatch, path
):
    key, session = "final-deferred", "cao-final-deferred"
    db.create_terminal(key, session, "worker", "codex", "reviewer")
    manager = module.ProviderManager()
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _k: None)
    monkeypatch.setattr(terminal_service, "provider_manager", manager)
    old = manager.get_provider(key)
    old.cleanup = Mock(return_value=True)
    initial_cleanup = manager.cleanup_provider
    replacements = []

    def cleanup_and_restore(k):
        result = initial_cleanup(k)
        replacement = manager.get_provider(k)
        replacement.cleanup = Mock(return_value=False)
        replacements.append(replacement)
        return result

    events = Mock()
    monkeypatch.setattr(terminal_service, "dispatch_plugin_event", events)
    if path.startswith("session"):
        monkeypatch.setattr(remote_terminal_service, "session_terminals", lambda _s: None)
        monkeypatch.setattr(session_service, "dispatch_plugin_event", events)
        monkeypatch.setattr(session_service, "terminal_dispatch_lock", lambda *_a: nullcontext())
        monkeypatch.setattr(terminal_service, "ensure_terminal_is_not_work_owned", lambda _k: None)
        monkeypatch.setattr(terminal_service, "capture_terminal_snapshot", db.get_terminal_metadata)
        monkeypatch.setattr(
            terminal_service,
            "dismantle_terminal_runtime",
            lambda k, _m, **_kw: cleanup_and_restore(k),
        )
        backend = MagicMock()
        backend.session_exists_strict.return_value = False
        monkeypatch.setattr(session_service, "get_backend", lambda: backend)
        if path == "session_sweep":
            monkeypatch.setattr(
                terminal_service,
                "delete_terminal_row",
                Mock(side_effect=RuntimeError("first delete failed")),
            )
        result = session_service.delete_session(session)
        assert result["deleted"] == []
        assert any(item.get("terminal_id") == key for item in result["errors"])
    else:
        monkeypatch.setattr(flow_service, "provider_manager", manager)
        monkeypatch.setattr(manager, "cleanup_provider", cleanup_and_restore)
        monkeypatch.setattr(flow_service, "_verified_flow_terminal_rows", lambda *_a: [])
        monkeypatch.setattr(
            flow_service,
            "list_current_session_terminals",
            lambda *_a, **_kw: [db.get_terminal_metadata(key)],
        )
        monkeypatch.setattr(
            flow_service, "should_retain_deferred_failure_tombstone", lambda *_a: False
        )
        monkeypatch.setattr(flow_service, "_is_terminal_busy", lambda _k: False)
        backend = MagicMock()
        backend.session_exists.return_value = path == "flow_live"
        monkeypatch.setattr(flow_service, "get_backend", lambda: backend)
        monkeypatch.setattr(flow_service, "fifo_manager", MagicMock())
        monkeypatch.setattr(flow_service, "status_monitor", MagicMock())
        assert flow_service._recycle_flow_session("deferred-flow", session) is False
    assert db.get_terminal_metadata(key) is not None
    assert manager._providers[key] is replacements[0]
    replacements[0].cleanup.assert_called_once_with()
    assert not any(call.args[1] == "post_kill_terminal" for call in events.call_args_list)
    assert manager._restore_locks == {}
