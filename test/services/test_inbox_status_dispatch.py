"""Bound status-driven delivery without losing individual ready events."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from test.services.test_inbox_resource_admission import service as controlled_service
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cli_agent_orchestrator.services import inbox_service as module

pytestmark = [pytest.mark.asyncio, pytest.mark.offline_database]


class ObservedSink(module._StatusEventSink):
    def __init__(self, capacity, is_busy):
        super().__init__(capacity, is_busy)
        self.accepted = 0
        self.changed = asyncio.Event()

    def put_nowait(self, event):
        super().put_nowait(event)
        self.accepted += 1
        self.changed.set()

    async def put(self, event):
        self.put_nowait(event)

    async def wait_accepted(self, count):
        async def wait():
            while self.accepted < count:
                self.changed.clear()
                await self.changed.wait()

        await asyncio.wait_for(wait(), timeout=5)


def ready(terminal_id):
    return {"topic": f"terminal.{terminal_id}.status", "data": {"status": "idle"}}


@asynccontextmanager
async def consumer(monkeypatch, *, held=(), failures=(), capacity=32, resource_slots=4):
    loop = asyncio.get_running_loop()
    service = (
        module.InboxService() if resource_slots is None else controlled_service(resource_slots)[0]
    )
    queue = None
    release = threading.Event()
    started = asyncio.Queue()
    completed = asyncio.Queue()
    subscribed = asyncio.Event()
    calls = []
    active = set()
    guard = threading.Lock()
    maximum = 0
    registry = object()

    def subscribe(_pattern, sink):
        nonlocal queue
        queue = sink
        subscribed.set()

    unsubscribe = MagicMock()
    monkeypatch.setattr(
        module, "bus", SimpleNamespace(subscribe_sink=subscribe, unsubscribe=unsubscribe)
    )

    def deliver(terminal_id, _num_messages, actual_registry):
        nonlocal maximum
        assert actual_registry is registry
        with guard:
            assert terminal_id not in active
            active.add(terminal_id)
            maximum = max(maximum, len(active))
            calls.append(terminal_id)
        loop.call_soon_threadsafe(started.put_nowait, terminal_id)
        try:
            if terminal_id in held:
                assert release.wait(timeout=5), "delivery barrier was not released"
            if terminal_id in failures:
                raise RuntimeError("controlled delivery failure")
        finally:
            with guard:
                active.remove(terminal_id)
            loop.call_soon_threadsafe(completed.put_nowait, terminal_id)

    monkeypatch.setattr(service, "_deliver_pending_locked", deliver)
    monkeypatch.setattr(module, "_StatusEventSink", ObservedSink)
    monkeypatch.setattr(
        module, "get_server_settings", lambda: {"event_bus_max_queue_size": capacity}
    )
    task = asyncio.create_task(service.run(registry))
    await asyncio.wait_for(subscribed.wait(), timeout=5)
    state = SimpleNamespace(
        service=service,
        queue=queue,
        release=release,
        started=started,
        completed=completed,
        calls=calls,
        task=task,
        unsubscribe=unsubscribe,
        maximum=lambda: maximum,
    )
    try:
        yield state
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        service.stop_accepting()
        await service.drain(timeout_s=5)


async def take(queue):
    return await asyncio.wait_for(queue.get(), timeout=2)


async def test_held_terminal_duplicates_do_not_block_another_terminal_or_lose_events(monkeypatch):
    async with consumer(monkeypatch, held={"a"}) as c:
        await c.queue.put(ready("a"))
        assert await take(c.started) == "a"
        for _ in range(6):
            await c.queue.put(ready("a"))
        await c.queue.put(ready("b"))
        assert await take(c.started) == "b"
        assert not c.release.is_set()
        c.release.set()
        for _ in range(8):
            await take(c.completed)
        assert c.calls.count("a") == 7
        assert c.calls.count("b") == 1


async def test_busy_burst_bookkeeping_has_linear_key_operations(monkeypatch):
    class CountedKey(str):
        operations = 0

        def __hash__(self):
            type(self).operations += 1
            return super().__hash__()

        def __eq__(self, other):
            type(self).operations += 1
            return super().__eq__(other)

    parse = module.terminal_id_from_topic
    monkeypatch.setattr(module, "terminal_id_from_topic", lambda topic: CountedKey(parse(topic)))
    count = 512
    async with consumer(monkeypatch, held={"a"}, capacity=1024) as c:
        await c.queue.put(ready("a"))
        assert await take(c.started) == "a"
        CountedKey.operations = 0
        for _ in range(count):
            await c.queue.put(ready("a"))
        await c.queue.wait_accepted(count + 1)
        await c.queue.put(ready("b"))
        assert await take(c.started) == "b"
        # Count key work rather than platform-dependent elapsed time. A full
        # pending scan per duplicate performs >130000 probes for this burst.
        assert CountedKey.operations < count * 32


@pytest.mark.parametrize("resource_slots", [None, 2, 4, 7])
async def test_dispatch_uses_observed_slots_and_one_combined_capacity(monkeypatch, resource_slots):
    slots = 4 if resource_slots is None else resource_slots
    keys = {f"held-{i}" for i in range(slots)}
    async with consumer(monkeypatch, held=keys, capacity=3, resource_slots=resource_slots) as c:
        for key in sorted(keys):
            await c.queue.put(ready(key))
            assert await take(c.started) == key
        for i in range(6):
            c.queue.put_nowait(ready(f"pending-{i}"))
        with pytest.raises(asyncio.QueueFull):
            c.queue.put_nowait(ready("overflow"))
        assert c.queue.qsize() == 6
        assert c.started.empty()
        assert c.maximum() == slots
        c.release.set()
        for _ in range(slots + 6):
            await take(c.completed)
        assert len(c.calls) == slots + 6
        assert c.maximum() <= slots


async def test_delivery_failure_does_not_stop_other_terminals(monkeypatch, caplog):
    async with consumer(monkeypatch, failures={"bad"}) as c:
        await c.queue.put(ready("bad"))
        await c.queue.put(ready("good"))
        assert {await take(c.completed), await take(c.completed)} == {"bad", "good"}
        await c.queue.put(ready("later"))
        assert await take(c.completed) == "later"
        assert not c.task.done()


async def test_failed_key_republishes_its_remaining_events(monkeypatch):
    async with consumer(monkeypatch, failures={"bad"}) as c:
        for _ in range(3):
            await c.queue.put(ready("bad"))
        await c.queue.put(ready("good"))
        results = [await take(c.completed) for _ in range(4)]
        assert results.count("bad") == 3
        assert results.count("good") == 1
        assert not c.task.done()


async def test_cancel_joins_dispatch_awaiters_but_drain_still_owns_real_workers(monkeypatch):
    async with consumer(monkeypatch, held={"a", "b"}) as c:
        for key in ("a", "b"):
            await c.queue.put(ready(key))
            assert await take(c.started) == key
        c.service.stop_accepting()
        c.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await c.task
        c.unsubscribe.assert_called_once_with("terminal.*.status", c.queue)
        assert c.service._active_operations == 2
        with pytest.raises(RuntimeError, match="prior operations"):
            c.service.start_accepting()
        with pytest.raises(module.InboxServiceDrainTimeout):
            await c.service.drain(timeout_s=0.02)
        assert not [
            task
            for task in asyncio.all_tasks()
            if task.get_name() in {"inbox-status-reader", "inbox-status-delivery"}
        ]
    assert c.service._active_operations == 0
    assert c.service._delivery_locks == {}


async def test_closed_admission_does_not_submit_new_delivery(monkeypatch):
    async with consumer(monkeypatch) as c:
        c.service.stop_accepting()
        await c.queue.put(ready("stopped"))
        await asyncio.wait_for(c.task, timeout=2)
        assert c.calls == []
        assert c.service._active_operations == 0


async def test_admitted_other_key_bypasses_saturated_busy_prefix(monkeypatch):
    async with consumer(monkeypatch, held={"a"}, capacity=2) as c:
        await c.queue.put(ready("a"))
        assert await take(c.started) == "a"
        for _ in range(2):
            await c.queue.put(ready("a"))
        await c.queue.wait_accepted(3)
        await c.queue.put(ready("b"))
        assert await take(c.started) == "b"
        assert not c.release.is_set()
        c.release.set()
        for _ in range(4):
            await take(c.completed)
        assert c.calls.count("a") == 3
        assert c.calls.count("b") == 1


@pytest.mark.parametrize("busy_events", [3, 4])
async def test_busy_prefix_at_exact_combined_admission_edge(monkeypatch, busy_events):
    from cli_agent_orchestrator.services.event_bus import EventBus

    async with consumer(monkeypatch, held={"a"}, capacity=2) as c:
        bus = EventBus()
        bus.subscribe_sink("terminal.*.status", c.queue)
        dropped = MagicMock()
        monkeypatch.setattr(bus, "_record_drop", dropped)
        try:
            bus._dispatch("terminal.a.status", {"status": "idle"})
            assert await take(c.started) == "a"
            for _ in range(busy_events):
                bus._dispatch("terminal.a.status", {"status": "idle"})
            bus._dispatch("terminal.b.status", {"status": "completed"})
            if busy_events == 3:
                assert c.queue.qsize() == 4
                assert await take(c.started) == "b"
                assert not c.release.is_set()
                dropped.assert_not_called()
            else:
                assert c.queue.qsize() == 4
                assert c.started.empty()
                dropped.assert_called_once_with("terminal.b.status")
            c.release.set()
            expected = 1 + busy_events + (busy_events == 3)
            for _ in range(expected):
                await take(c.completed)
            assert c.calls.count("a") == busy_events + 1
            assert c.calls.count("b") == (busy_events == 3)
        finally:
            bus.unsubscribe("terminal.*.status", c.queue)


@pytest.mark.parametrize("capacity", [0, 2])
async def test_busy_prefix_cannot_hide_an_admitted_other_key(monkeypatch, capacity):
    pending_cap = max(1, capacity)
    async with consumer(monkeypatch, held={"a"}, capacity=capacity) as c:
        await c.queue.put(ready("a"))
        assert await take(c.started) == "a"
        for _ in range(pending_cap):
            await c.queue.put(ready("a"))
        await c.queue.put(ready("b"))
        assert await take(c.started) == "b"
        assert not c.release.is_set()
        c.release.set()
        for _ in range(pending_cap + 2):
            await take(c.completed)
        assert c.calls.count("a") == pending_cap + 1
        assert c.calls.count("b") == 1


async def test_malformed_event_does_not_stop_dispatch(monkeypatch):
    async with consumer(monkeypatch) as c:
        await c.queue.put({"data": {}})
        await c.queue.put(ready("good"))
        assert await take(c.completed) == "good"
        assert not c.task.done()


async def test_repeated_idle_wakeup_has_no_lost_admission(monkeypatch):
    async with consumer(monkeypatch) as c:
        for i in range(50):
            key = f"wake-{i}"
            await c.queue.put(ready(key))
            assert await take(c.completed) == key
        assert len(c.calls) == 50


async def test_fatal_worker_error_propagates_and_cleans_children(monkeypatch):
    class FatalDelivery(BaseException):
        pass

    failure = FatalDelivery("controlled fatal worker")
    async with consumer(monkeypatch) as c:

        def fail(*args):
            raise failure

        monkeypatch.setattr(c.service, "_deliver_pending_locked", fail)
        await c.queue.put(ready("fatal"))
        with pytest.raises(FatalDelivery) as raised:
            await asyncio.wait_for(c.task, timeout=5)
        assert raised.value is failure
        c.unsubscribe.assert_called_once_with("terminal.*.status", c.queue)
        assert c.service._active_operations == 0


@pytest.mark.parametrize("resource_slots", [None, 4])
async def test_late_worker_is_fenced_even_after_admission_reopens(monkeypatch, resource_slots):
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    finished = asyncio.Event()
    release = threading.Event()
    # Pause before operation tracking in both paths; an old handoff must not
    # join a new lifetime even after a healthy resource budget is restored.
    async with consumer(monkeypatch, resource_slots=resource_slots) as c:
        original = c.service._deliver_status_pending

        def pause_before_admission(*args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            try:
                assert release.wait(timeout=5)
                original(*args, **kwargs)
            finally:
                loop.call_soon_threadsafe(finished.set)

        monkeypatch.setattr(c.service, "_deliver_status_pending", pause_before_admission)
        try:
            await c.queue.put(ready("old"))
            await asyncio.wait_for(entered.wait(), timeout=5)
            c.service.stop_accepting()
            c.task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await c.task
            await c.service.drain(timeout_s=5)
            c.service.start_accepting()
            if resource_slots is not None:
                from cli_agent_orchestrator.services.admission_policy import CapacityObservation

                owner = c.service._admission
                memory = 80 * 1024**2 * resource_slots
                assert owner.observe(
                    CapacityObservation(10.0, memory, memory, 16000, 16000, 2 * resource_slots + 1),
                    now=10.0,
                    generation=owner.generation,
                )
                assert owner.snapshot(now=10.0).available_slots == resource_slots
        finally:
            release.set()
            await asyncio.wait_for(finished.wait(), timeout=5)
        assert c.calls == []
        assert c.service._active_operations == 0
        if resource_slots is not None:
            assert c.service._admission.snapshot(now=10.0).inflight == 0


async def test_cancelling_queued_executor_work_prevents_delivery_after_reopen(monkeypatch):
    loop = asyncio.get_running_loop()
    old_executor = loop._default_executor
    submitted = asyncio.Event()
    release = threading.Event()

    class ObservedExecutor(ThreadPoolExecutor):
        submissions = 0

        def submit(self, *args, **kwargs):
            result = super().submit(*args, **kwargs)
            self.submissions += 1
            if self.submissions == 2:
                loop.call_soon_threadsafe(submitted.set)
            return result

    executor = ObservedExecutor(max_workers=1)
    blocker = executor.submit(release.wait, 5)
    loop.set_default_executor(executor)
    try:
        # Bound delivery has no queued default-executor handoff to cancel.
        async with consumer(monkeypatch, resource_slots=None) as c:
            await c.queue.put(ready("queued"))
            await asyncio.wait_for(submitted.wait(), timeout=5)
            c.service.stop_accepting()
            c.task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await c.task
            await c.service.drain(timeout_s=5)
            c.service.start_accepting()
            release.set()
            # The sentinel runs after any previously queued work.
            await asyncio.wait_for(loop.run_in_executor(executor, lambda: None), timeout=5)
            assert c.calls == []
    finally:
        release.set()
        loop._default_executor = old_executor
        executor.shutdown(wait=True, cancel_futures=True)
    assert blocker.done()
