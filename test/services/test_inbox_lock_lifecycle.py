"""Per-terminal inbox locks live only while a holder or waiter owns a lease."""

import threading
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services import inbox_service as module

pytestmark = pytest.mark.offline_database


def test_completed_empty_delivery_churn_releases_terminal_locks(monkeypatch):
    service = module.InboxService()
    monkeypatch.setattr(module, "get_pending_messages", lambda *_args, **_kwargs: [])

    for index in range(10000):
        service.deliver_pending(f"ephemeral-{index}")

    assert service._active_operations == 0
    assert service._delivery_locks == {}


@pytest.mark.parametrize("error", [ValueError("delivery failed"), KeyboardInterrupt()])
def test_delivery_failure_releases_lock_and_preserves_exception(monkeypatch, error):
    service = module.InboxService()

    def fail(*_args):
        raise error

    monkeypatch.setattr(service, "_deliver_pending_locked", fail)
    with pytest.raises(type(error)) as raised:
        service.deliver_pending("failed")

    assert raised.value is error
    assert service._active_operations == 0
    assert service._delivery_locks == {}
    monkeypatch.setattr(service, "_deliver_pending_locked", lambda *_args: None)
    service.deliver_pending("failed")
    assert service._delivery_locks == {}


def test_interrupted_lock_acquisition_releases_its_lease(monkeypatch):
    service = module.InboxService()
    error = KeyboardInterrupt()

    class InterruptedLock:
        # Exercise the real acquire/release protocol, including the explicit
        # acquisition now required by nonblocking fallback deliveries.
        def acquire(self, *, blocking=True):
            raise error

        def release(self):
            pytest.fail("an unacquired lock must not be released")

        def __enter__(self):
            raise error

        def __exit__(self, *_args):
            pytest.fail("an unacquired lock must not be released")

    # Replace only this module's lock constructor, not threading globally.
    monkeypatch.setattr(module, "threading", SimpleNamespace(Lock=InterruptedLock))
    with pytest.raises(KeyboardInterrupt) as raised:
        service.deliver_pending("interrupted")

    assert raised.value is error
    assert service._active_operations == 0
    assert service._delivery_locks == {}


def test_registered_waiter_retains_shared_lock_without_blocking_other_terminals(monkeypatch):
    service = module.InboxService()
    first_entered = threading.Event()
    release_first = threading.Event()
    waiter_registered = threading.Event()
    allow_waiter_acquisition = threading.Event()
    waiter_entered = threading.Event()
    release_waiter = threading.Event()
    other_entered = threading.Event()
    errors = []
    wait_bound = 5
    guard = service._delivery_locks_guard
    waiter = None

    class ObservedGuard:
        reported = False

        def __enter__(self):
            return guard.__enter__()

        def __exit__(self, *args):
            result = guard.__exit__(*args)
            if threading.current_thread() is waiter and not self.reported:
                self.reported = True
                # Registry changes are complete, but this waiter has not yet
                # acquired the per-terminal lock. Let the old holder retire.
                waiter_registered.set()
                assert allow_waiter_acquisition.wait(wait_bound)
            return result

    service._delivery_locks_guard = ObservedGuard()

    def deliver(terminal_id, *_args):
        if terminal_id == "other":
            other_entered.set()
        elif threading.current_thread() is waiter:
            waiter_entered.set()
            assert release_waiter.wait(wait_bound)
        else:
            first_entered.set()
            assert release_first.wait(wait_bound)

    monkeypatch.setattr(service, "_deliver_pending_locked", deliver)

    def run(terminal_id):
        try:
            service.deliver_pending(terminal_id)
        except BaseException as error:
            errors.append(error)

    first = threading.Thread(target=run, args=("shared",))
    waiter = threading.Thread(target=run, args=("shared",))
    other = threading.Thread(target=run, args=("other",))
    started = []
    try:
        first.start()
        started.append(first)
        assert first_entered.wait(wait_bound)
        entry = service._delivery_locks["shared"]
        waiter.start()
        started.append(waiter)
        assert waiter_registered.wait(wait_bound)

        release_first.set()
        first.join(wait_bound)
        assert not first.is_alive()
        assert not errors
        assert service._delivery_locks.get("shared") is entry

        other.start()
        started.append(other)
        assert other_entered.wait(wait_bound)
        other.join(wait_bound)
        assert not other.is_alive()

        allow_waiter_acquisition.set()
        assert waiter_entered.wait(wait_bound)
        assert service._delivery_locks.get("shared") is entry
        release_waiter.set()
        waiter.join(wait_bound)
        assert not waiter.is_alive()
        assert not errors
        assert service._active_operations == 0
        assert service._delivery_locks == {}
    finally:
        release_first.set()
        allow_waiter_acquisition.set()
        release_waiter.set()
        for thread in started:
            thread.join(wait_bound)
        assert all(not thread.is_alive() for thread in started)
