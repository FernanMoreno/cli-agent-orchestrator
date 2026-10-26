"""Per-terminal sidecar leases must not retain history or block unrelated IDs."""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.services import terminal_service as module

pytestmark = pytest.mark.offline_database


@pytest.fixture(autouse=True)
def isolated_filesystem_leases(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "TERMINAL_LOG_DIR", tmp_path / "terminal-logs")


def test_reentrant_entries_retire_after_last_scope_and_exception():
    assert module._deferred_sidecar_locks == {}
    error = RuntimeError("controlled scope failure")
    with pytest.raises(RuntimeError) as raised:
        with module._deferred_init_sidecar_lock("A"):
            entry = module._deferred_sidecar_locks["A"]
            with module._deferred_init_sidecar_lock("A"):
                assert module._deferred_sidecar_locks["A"] is entry
                assert entry.refs == 2
            assert entry.refs == 1
            raise error
    assert raised.value is error
    assert module._deferred_sidecar_locks == {}


def test_registered_waiter_shares_owner_while_another_key_progresses(monkeypatch):
    guard = module._deferred_sidecar_file_locks_guard
    registered = threading.Event()

    class Guard:
        def __enter__(self):
            return guard.__enter__()

        def __exit__(self, *args):
            result = guard.__exit__(*args)
            if threading.current_thread().name == "sidecar-waiter":
                registered.set()
            return result

    monkeypatch.setattr(module, "_deferred_sidecar_file_locks_guard", Guard())

    def waiter():
        threading.current_thread().name = "sidecar-waiter"
        with module._deferred_init_sidecar_lock("A"):
            return next(iter(module._deferred_sidecar_file_locks.values()))

    def independent():
        with module._deferred_init_sidecar_lock("B"):
            return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        with module._deferred_init_sidecar_lock("A"):
            entry = module._deferred_sidecar_locks["A"]
            file_entry = next(iter(module._deferred_sidecar_file_locks.values()))
            future = pool.submit(waiter)
            assert registered.wait(timeout=5)
            assert file_entry.refs == 2
            assert entry.refs == 1
            assert not future.done()
            assert pool.submit(independent).result(timeout=5) is True
        assert future.result(timeout=5) is file_entry
    assert module._deferred_sidecar_locks == {}
    assert module._deferred_sidecar_file_locks == {}


def test_ten_thousand_historic_keys_leave_no_idle_entries():
    for index in range(10000):
        with module._deferred_init_sidecar_lock(f"terminal-{index}"):
            assert len(module._deferred_sidecar_locks) == 1
    assert module._deferred_sidecar_locks == {}
    assert module._deferred_sidecar_file_locks == {}


def test_failed_lock_acquisition_does_not_leak_a_registered_entry(monkeypatch):
    entry = module._DeferredSidecarLockEntry()

    class Broken:
        def __enter__(self):
            raise RuntimeError("acquisition interrupted")

        def __exit__(self, *args):
            pytest.fail("Unacquired lock cannot be released")

    entry.lock = Broken()
    monkeypatch.setattr(module, "_DeferredSidecarLockEntry", lambda: entry)
    with pytest.raises(RuntimeError, match="interrupted"):
        with module._deferred_init_sidecar_lock("A"):
            pytest.fail("Interrupted acquisition cannot enter the body")
    assert module._deferred_sidecar_locks == {}


@pytest.mark.parametrize("ids", [[], ["b", "a", "b"]])
def test_bulk_keeps_exact_callback_arguments_count_and_sorted_unique_leases(monkeypatch, ids):
    entered, exited = [], []
    original = module._deferred_init_sidecar_local_lock

    @contextmanager
    def observed(key):
        with original(key):
            entered.append(key)
            try:
                yield
            finally:
                exited.append(key)

    monkeypatch.setattr(module, "_deferred_init_sidecar_local_lock", observed)
    failures, completions = Mock(), Mock()
    monkeypatch.setattr(module, "_delete_deferred_failure_fallback", failures)
    monkeypatch.setattr(module, "_delete_deferred_init_complete_fallback", completions)
    operation = Mock(return_value=17)
    assert module._delete_terminal_rows_with_sidecars(ids, delete_rows=operation) == 17
    operation.assert_called_once_with(ids)
    assert entered == sorted(set(ids))
    assert exited == sorted(set(ids), reverse=True)
    assert [call.args[0] for call in failures.call_args_list] == sorted(set(ids))
    assert [call.args[0] for call in completions.call_args_list] == sorted(set(ids))
    assert module._deferred_sidecar_locks == {}


def test_bulk_callback_error_keeps_both_evidence_types_and_releases_all_leases(monkeypatch):
    error = RuntimeError("uncertain SQL outcome")
    failures, completions = Mock(), Mock()
    monkeypatch.setattr(module, "_delete_deferred_failure_fallback", failures)
    monkeypatch.setattr(module, "_delete_deferred_init_complete_fallback", completions)
    with pytest.raises(RuntimeError) as raised:
        module._delete_terminal_rows_with_sidecars(["b", "a"], delete_rows=Mock(side_effect=error))
    assert raised.value is error
    failures.assert_not_called()
    completions.assert_not_called()
    assert module._deferred_sidecar_locks == {}
