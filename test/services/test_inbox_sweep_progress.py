"""Independent fallback deliveries must not queue behind one held receiver."""

import threading

import pytest

from cli_agent_orchestrator.services import inbox_service as module

pytestmark = pytest.mark.offline_database
SWEEPS = [
    ("poll_opencode_pending_messages", "list_pending_receiver_ids_by_provider"),
    ("reconcile_orphaned_messages", "list_pending_receiver_ids_older_than"),
]


@pytest.mark.parametrize(("method", "listing"), SWEEPS)
def test_ready_receiver_progresses_while_another_receiver_query_is_held(
    monkeypatch, method, listing
):
    service = module.InboxService()
    entered_a = threading.Event()
    release_a = threading.Event()
    queried_b = threading.Event()
    errors = []
    monkeypatch.setattr(module, listing, lambda *_: ["held-a", "ready-b"])

    def pending(terminal_id, *, limit):
        assert limit == 1
        if terminal_id == "held-a":
            entered_a.set()
            assert release_a.wait(10), "held query was not released"
        elif terminal_id == "ready-b":
            queried_b.set()
        else:
            raise AssertionError("unexpected receiver")
        return []

    monkeypatch.setattr(module, "get_pending_messages", pending)

    def run():
        try:
            getattr(service, method)()
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered_a.wait(5)
        assert queried_b.wait(5), "ready B was hidden behind held A in the fallback sweep"
        assert not release_a.is_set()
    finally:
        release_a.set()
        worker.join(5)
        assert not worker.is_alive()
    assert not errors
    assert service._active_operations == 0
    assert service._delivery_locks == {}


@pytest.mark.parametrize(("method", "listing"), SWEEPS)
def test_sweep_window_context_and_threads_are_bounded_and_joined(monkeypatch, method, listing):
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    service = module.InboxService()
    cap = module._SWEEP_DELIVERY_CONCURRENCY
    owner = contextvars.ContextVar("sweep_target", default=None)
    token = owner.set("owned-test-target")
    lock = threading.Lock()
    release = threading.Event()
    saturated = threading.Event()
    consumed = []
    seen = []
    errors = []
    pools = []
    active = 0
    peak = 0
    count = cap * 12

    class ObservedExecutor(ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            pools.append(self)

    def receivers(*_args):
        for i in range(count):
            consumed.append(str(i))
            yield str(i)

    def deliver(tid, number, registry):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            seen.append((tid, owner.get(), number, registry))
            if active == cap:
                saturated.set()
        try:
            if int(tid) < cap:
                assert release.wait(10)
        finally:
            with lock:
                active -= 1

    def run():
        try:
            getattr(service, method)()
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(module, "ThreadPoolExecutor", ObservedExecutor)
    monkeypatch.setattr(module, listing, receivers)
    monkeypatch.setattr(service, "_deliver_pending_locked", deliver)
    worker = threading.Thread(target=contextvars.copy_context().run, args=(run,))
    try:
        worker.start()
        assert saturated.wait(5)
        assert len(consumed) == cap, "the sweep eagerly submitted its entire receiver inventory"
        assert len(seen) == cap
    finally:
        release.set()
        worker.join(10)
        owner.reset(token)
    assert not worker.is_alive() and not errors
    assert peak == cap and len(seen) == count
    assert {row[0] for row in seen} == {str(i) for i in range(count)}
    assert all(row[1:] == ("owned-test-target", 1, None) for row in seen)
    assert len(pools) == 1 and all(not thread.is_alive() for thread in pools[0]._threads)
    assert service._active_operations == 0 and service._delivery_locks == {}


@pytest.mark.parametrize(("method", "listing"), SWEEPS)
def test_busy_receiver_locks_do_not_fill_the_fallback_worker_slots(monkeypatch, method, listing):
    from contextlib import ExitStack

    service = module.InboxService()
    busy = [f"busy-{i}" for i in range(module._SWEEP_DELIVERY_CONCURRENCY)]
    monkeypatch.setattr(module, listing, lambda *_: [*busy, "ready"])
    attempted = []
    monkeypatch.setattr(service, "_deliver_pending_locked", lambda tid, *_: attempted.append(tid))
    finished = threading.Event()

    def run():
        try:
            getattr(service, method)()
        finally:
            finished.set()

    with ExitStack() as stack:
        for tid in busy:
            assert stack.enter_context(service._delivery_lock(tid)) is True
        worker = threading.Thread(target=run)
        worker.start()
        try:
            assert finished.wait(5), "busy keys occupied all fallback slots"
            assert attempted == ["ready"], "a busy receiver was entered without its lock"
        finally:
            # Retire held locks before joining even on a failed assertion.
            stack.close()
            worker.join(5)
    assert not worker.is_alive()
    assert service._delivery_locks == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "listing"), SWEEPS)
async def test_queued_sweep_work_keeps_parent_admission_after_cancellation_and_stop(
    monkeypatch, method, listing
):
    import asyncio

    service = module.InboxService()
    cap = module._SWEEP_DELIVERY_CONCURRENCY
    monkeypatch.setattr(module, listing, lambda *_: [str(i) for i in range(cap + 1)])
    saturated = threading.Event()
    release_initial = threading.Event()
    late_started = threading.Event()
    release_late = threading.Event()
    finished = threading.Event()
    lock = threading.Lock()
    started = set()
    errors = []

    def deliver(tid, *_):
        with lock:
            started.add(tid)
            if len(started) == cap:
                saturated.set()
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
        assert await asyncio.to_thread(saturated.wait, 5)
        service.stop_accepting()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert not finished.is_set()
        draining = asyncio.create_task(service.drain(timeout_s=5))
        await asyncio.sleep(0)
        assert not draining.done()
        with pytest.raises(module.InboxServiceStoppingError):
            service.deliver_pending("new-top-level")
        release_initial.set()
        assert await asyncio.to_thread(
            late_started.wait, 5
        ), "admitted queued work was rejected after stop"
        assert not draining.done() and not finished.is_set()
        with pytest.raises(RuntimeError):
            service.start_accepting()
        release_late.set()
        await draining
        assert await asyncio.to_thread(finished.wait, 5)
    finally:
        release_initial.set()
        release_late.set()
        assert await asyncio.to_thread(finished.wait, 5)
        if draining is not None:
            await asyncio.gather(draining, return_exceptions=True)
    assert not errors and len(started) == cap + 1
    assert service._active_operations == 0 and service._delivery_locks == {}
    service.start_accepting()


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "listing"), SWEEPS)
async def test_fallback_children_progress_with_one_occupied_default_executor_thread(
    monkeypatch, method, listing
):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    loop = asyncio.get_running_loop()
    service = module.InboxService()
    started = asyncio.Event()
    ready = asyncio.Event()
    release = threading.Event()
    monkeypatch.setattr(module, listing, lambda *_: ["held", "ready"])

    def deliver(tid, *_):
        if tid == "held":
            loop.call_soon_threadsafe(started.set)
            assert release.wait(10)
        else:
            loop.call_soon_threadsafe(ready.set)

    monkeypatch.setattr(service, "_deliver_pending_locked", deliver)
    previous = loop._default_executor
    parent_executor = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(parent_executor)
    caller = asyncio.create_task(asyncio.to_thread(getattr(service, method)))
    try:
        await asyncio.wait_for(started.wait(), 5)
        await asyncio.wait_for(ready.wait(), 5)
        assert not release.is_set()
    finally:
        release.set()
        await asyncio.wait_for(caller, 5)
        loop._default_executor = previous
        parent_executor.shutdown(wait=True)
    assert service._active_operations == 0 and service._delivery_locks == {}


@pytest.mark.parametrize(("method", "listing"), SWEEPS)
def test_worker_base_exception_is_propagated_only_after_other_workers_retire(
    monkeypatch, method, listing
):
    service = module.InboxService()
    error = KeyboardInterrupt()
    held = threading.Event()
    interrupting = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    errors = []
    monkeypatch.setattr(module, listing, lambda *_: ["interrupt", "held"])

    def deliver(tid, *_):
        if tid == "held":
            held.set()
            assert release.wait(10)
        else:
            assert held.wait(5)
            interrupting.set()
            raise error

    def run():
        try:
            getattr(service, method)()
        except BaseException as raised:
            errors.append(raised)
        finally:
            finished.set()

    monkeypatch.setattr(service, "_deliver_pending_locked", deliver)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert interrupting.wait(5)
        assert not finished.is_set()
        assert service._active_operations == 1
        service.stop_accepting()
        with pytest.raises(RuntimeError):
            service.start_accepting()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and errors == [error]
    assert service._active_operations == 0 and service._delivery_locks == {}
    service.start_accepting()
