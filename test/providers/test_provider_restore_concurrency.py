"""Independent terminals restore independently, with same-key fenced publication."""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.providers import manager as module
from cli_agent_orchestrator.services import remote_terminal_service

pytestmark = pytest.mark.offline_database


@pytest.fixture(autouse=True)
def local_placement(monkeypatch):
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _key: None)


def metadata(key):
    return dict(provider="mock_cli", tmux_session="session", tmux_window=key, agent_profile=None)


@pytest.mark.parametrize("cached", [False, True])
def test_slow_restore_does_not_block_another_terminal(monkeypatch, cached):
    manager = module.ProviderManager()
    entered = threading.Event()
    release = threading.Event()
    b_done = threading.Event()
    a = SimpleNamespace(requires_turn_receipt=False, terminal_id="a")
    b = SimpleNamespace(requires_turn_receipt=False, terminal_id="b")
    if cached:
        manager._providers["b"] = b

    def read(key):
        if key == "a":
            entered.set()
            assert release.wait(timeout=5)
        return metadata(key)

    def build(_kind, key, *_args, **kwargs):
        assert kwargs["publish"] is False
        return a if key == "a" else b

    def get_b():
        result = manager.get_provider("b")
        b_done.set()
        return result

    monkeypatch.setattr(module, "get_terminal_metadata", read)
    monkeypatch.setattr(manager, "create_provider", build)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(manager.get_provider, "a")
        try:
            assert entered.wait(timeout=5)
            second = workers.submit(get_b)
            assert b_done.wait(timeout=1), "B waited behind A's independent restoration"
            assert second.result(timeout=5) is b
            assert not release.is_set()
        finally:
            release.set()
        assert first.result(timeout=5) is a


def test_same_key_waiters_share_only_a_fully_restored_receipt(monkeypatch):
    manager = module.ProviderManager()
    entered = threading.Event()
    release = threading.Event()
    start = threading.Barrier(9)
    receipt = dict(phase="sent", generation="a" * 32, receipt_sha256="b" * 64)
    provider = SimpleNamespace(requires_turn_receipt=True)

    def restore(actual):
        assert actual == receipt
        entered.set()
        assert release.wait(timeout=5)
        provider.restored_receipt = actual

    provider.restore_turn_receipt_state = restore
    constructor = Mock(return_value=provider)
    monkeypatch.setattr(manager, "create_provider", constructor)
    monkeypatch.setattr(module, "get_terminal_metadata", metadata)
    monkeypatch.setattr(module, "get_terminal_turn_receipt", lambda _key: receipt)
    monkeypatch.setattr(module, "get_terminal_turn_recovery", lambda _key: None)

    def get():
        start.wait(timeout=5)
        result = manager.get_provider("same")
        assert result.restored_receipt == receipt
        return result

    with ThreadPoolExecutor(max_workers=8) as workers:
        futures = [workers.submit(get) for _ in range(8)]
        try:
            start.wait(timeout=5)
            assert entered.wait(timeout=5)
            assert "same" not in manager._providers
            assert not any(future.done() for future in futures)
        finally:
            release.set()
        assert all(future.result(timeout=5) is provider for future in futures)
    constructor.assert_called_once()
    assert manager._providers["same"] is provider


@pytest.mark.parametrize("failure", [ValueError("restore failed"), KeyboardInterrupt()])
def test_restore_failure_preserves_identity_and_allows_fresh_retry(monkeypatch, failure):
    manager = module.ProviderManager()
    provider = SimpleNamespace(requires_turn_receipt=False)
    read = Mock(side_effect=[failure, metadata("retry")])
    monkeypatch.setattr(module, "get_terminal_metadata", read)
    monkeypatch.setattr(manager, "create_provider", lambda *_args, **_kwargs: provider)
    with pytest.raises(type(failure)) as raised:
        manager.get_provider("retry")
    assert raised.value is failure
    assert "retry" not in manager._providers
    assert manager.get_provider("retry") is provider
    assert manager._restore_locks == {}


def test_remote_lookup_does_not_read_or_publish_local_provider(monkeypatch):
    manager = module.ProviderManager()
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _key: {"remote": True})
    read = Mock(side_effect=AssertionError("local restoration must not run"))
    monkeypatch.setattr(module, "get_terminal_metadata", read)
    assert manager.get_provider("remote") is None
    read.assert_not_called()
    assert manager._providers == {}
    assert manager._restore_locks == {}


def test_missing_terminal_churn_does_not_retain_restoration_locks(monkeypatch):
    manager = module.ProviderManager()
    monkeypatch.setattr(module, "get_terminal_metadata", lambda _key: None)
    for i in range(10000):
        with pytest.raises(ValueError, match="not found"):
            manager.get_provider(f"missing-{i}")
    assert manager._providers == {}
    assert manager._restore_locks == {}


def test_restore_lease_is_reentrant_and_retires_after_registered_waiters(monkeypatch):
    manager = module.ProviderManager()
    registered = threading.Event()
    proceed = threading.Event()
    acquired = threading.Event()
    original_guard = manager._restore_locks_guard

    class Guard:
        def __enter__(self):
            return original_guard.__enter__()

        def __exit__(self, *args):
            result = original_guard.__exit__(*args)
            if threading.current_thread().name == "registered-waiter" and not registered.is_set():
                registered.set()
                assert proceed.wait(timeout=5)
            return result

    monkeypatch.setattr(manager, "_restore_locks_guard", Guard())

    def waiter():
        threading.current_thread().name = "registered-waiter"
        with manager._restoration_lock("key"):
            acquired.set()

    with ThreadPoolExecutor(max_workers=1) as workers:
        try:
            with manager._restoration_lock("key"):
                entry = manager._restore_locks["key"]
                with manager._restoration_lock("key"):
                    assert entry.refs == 2
                future = workers.submit(waiter)
                assert registered.wait(timeout=5)
                assert not acquired.is_set()
            assert manager._restore_locks["key"] is entry
            # A lease for a different terminal must not wait for this handoff.
            with manager._restoration_lock("other"):
                assert not acquired.is_set()
            with manager._restoration_lock("key"):
                assert manager._restore_locks["key"] is entry
            proceed.set()
            future.result(timeout=5)
        finally:
            proceed.set()
    assert acquired.is_set()
    assert manager._restore_locks == {}


def test_interrupted_restore_lock_acquisition_retires_registered_reference(monkeypatch):
    manager = module.ProviderManager()
    failure = KeyboardInterrupt()

    class InterruptedLock:
        def __enter__(self):
            raise failure

        def __exit__(self, *args):
            pytest.fail("an unacquired lock must not be released")

    monkeypatch.setattr(module, "threading", SimpleNamespace(RLock=InterruptedLock))
    with pytest.raises(KeyboardInterrupt) as raised:
        with manager._restoration_lock("interrupted"):
            pytest.fail("lock acquisition must fail")
    assert raised.value is failure
    assert manager._restore_locks == {}
