"""Read iterators and delivery workers stay owned until both actually finish."""

import asyncio
import threading

import pytest

from cli_agent_orchestrator.services import inbox_service as module

pytestmark = pytest.mark.offline_database
SWEEPS = [
    ("poll_opencode_pending_messages", "iter_pending_receiver_ids_by_provider"),
    ("reconcile_orphaned_messages", "iter_pending_receiver_ids_older_than"),
]


def start(service, method):
    errors = []
    finished = threading.Event()

    def run():
        try:
            getattr(service, method)()
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    worker = threading.Thread(target=run)
    worker.start()
    return worker, errors, finished


@pytest.mark.parametrize(("method", "listing"), SWEEPS)
def test_discovery_failure_still_joins_its_started_delivery(monkeypatch, method, listing):
    service = module.InboxService()
    held = threading.Event()
    release = threading.Event()
    failed_read = threading.Event()
    closed_at = []
    error = ValueError("discovery failed")

    def receivers():
        try:
            yield "held"
            assert held.wait(5)
            raise error
        finally:
            closed_at.append(service._active_operations)
            failed_read.set()

    source = receivers()  # Retain the reference: cleanup must not rely on GC.
    monkeypatch.setattr(module, listing, lambda *_: source)

    def deliver(*_):
        held.set()
        assert release.wait(10)

    monkeypatch.setattr(service, "_deliver_pending_locked", deliver)
    worker, errors, finished = start(service, method)
    try:
        assert failed_read.wait(5)
        assert not finished.is_set() and service._active_operations == 1
        service.stop_accepting()
        with pytest.raises(RuntimeError):
            service.start_accepting()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and errors == [error]
    assert closed_at == [1]
    assert service._active_operations == 0 and service._delivery_locks == {}


@pytest.mark.parametrize(("method", "listing"), SWEEPS)
def test_worker_interruption_joins_then_closes_unfinished_discovery(monkeypatch, method, listing):
    service = module.InboxService()
    cap = module._SWEEP_DELIVERY_CONCURRENCY
    all_started = threading.Event()
    interrupting = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    started = set()
    ended = set()
    closed_at = []
    error = KeyboardInterrupt()

    def receivers():
        try:
            yield from (str(i) for i in range(cap + 1))
        finally:
            closed_at.append((service._active_operations, len(ended)))

    source = receivers()
    monkeypatch.setattr(module, listing, lambda *_: source)

    def deliver(tid, *_):
        with lock:
            started.add(tid)
            if len(started) == cap:
                all_started.set()
        if tid == "0":
            assert all_started.wait(5)
            interrupting.set()
            raise error
        assert release.wait(10)
        with lock:
            ended.add(tid)

    monkeypatch.setattr(service, "_deliver_pending_locked", deliver)
    worker, errors, finished = start(service, method)
    try:
        assert interrupting.wait(5)
        assert not closed_at and not finished.is_set()
        assert service._active_operations == 1
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and errors == [error]
    assert closed_at == [
        (1, cap - 1)
    ], "cursor closed before workers joined or after admission retired"
    assert len(started) == cap  # Unread keys were not dispatched after interruption.
    assert service._active_operations == 0 and service._delivery_locks == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "listing"), SWEEPS)
async def test_cancelled_sweep_keeps_discovery_and_children_owned_through_drain(
    monkeypatch, method, listing
):
    service = module.InboxService()
    cap = module._SWEEP_DELIVERY_CONCURRENCY
    all_started = threading.Event()
    late_started = threading.Event()
    release_initial = threading.Event()
    release_late = threading.Event()
    finished = threading.Event()
    lock = threading.Lock()
    started = set()
    closed_at = []
    errors = []

    def receivers():
        try:
            yield from (str(i) for i in range(cap + 1))
        finally:
            closed_at.append(service._active_operations)

    source = receivers()
    monkeypatch.setattr(module, listing, lambda *_: source)

    def deliver(tid, *_):
        with lock:
            started.add(tid)
            if len(started) == cap:
                all_started.set()
        if int(tid) < cap:
            assert release_initial.wait(10)
        else:
            late_started.set()
            assert release_late.wait(10)

    def run():
        try:
            getattr(service, method)()
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    monkeypatch.setattr(service, "_deliver_pending_locked", deliver)
    caller = asyncio.create_task(asyncio.to_thread(run))
    draining = None
    try:
        assert await asyncio.to_thread(all_started.wait, 5)
        assert not closed_at
        service.stop_accepting()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        draining = asyncio.create_task(service.drain(timeout_s=5))
        await asyncio.sleep(0)
        assert not draining.done() and not finished.is_set()
        release_initial.set()
        assert await asyncio.to_thread(late_started.wait, 5)
        assert not draining.done() and not finished.is_set()
        release_late.set()
        await draining
        assert await asyncio.to_thread(finished.wait, 5)
    finally:
        release_initial.set()
        release_late.set()
        assert await asyncio.to_thread(finished.wait, 5)
        if draining is not None:
            await asyncio.gather(draining, return_exceptions=True)
    assert not errors and closed_at == [1]
    assert service._active_operations == 0 and service._delivery_locks == {}
    service.start_accepting()
