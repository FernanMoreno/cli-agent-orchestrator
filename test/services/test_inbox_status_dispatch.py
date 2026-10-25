"""Bound status-driven delivery without losing individual ready events."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cli_agent_orchestrator.services import inbox_service as module

pytestmark = [pytest.mark.asyncio, pytest.mark.offline_database]


class ObservedQueue(asyncio.Queue):
    def __init__(self, capacity):
        super().__init__(maxsize=capacity)
        self.read_attempts = 0
        self.reads = 0
        self.changed = asyncio.Event()

    async def get(self):
        self.read_attempts += 1
        value = await super().get()
        self.reads += 1
        self.changed.set()
        return value

    async def wait_reads(self, count):
        async def wait():
            while self.reads < count:
                self.changed.clear()
                await self.changed.wait()

        await asyncio.wait_for(wait(), timeout=5)


def ready(terminal_id):
    return {"topic": f"terminal.{terminal_id}.status", "data": {"status": "idle"}}


@asynccontextmanager
async def consumer(monkeypatch, *, held=(), failures=(), capacity=32):
    loop = asyncio.get_running_loop()
    service = module.InboxService()
    queue = ObservedQueue(capacity)
    release = threading.Event()
    started = asyncio.Queue()
    completed = asyncio.Queue()
    subscribed = asyncio.Event()
    calls = []
    active = set()
    guard = threading.Lock()
    maximum = 0
    registry = object()

    def subscribe(_pattern):
        subscribed.set()
        return queue

    unsubscribe = MagicMock()
    monkeypatch.setattr(
        module, "bus", SimpleNamespace(subscribe=subscribe, unsubscribe=unsubscribe)
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


async def test_dispatch_has_four_slots_and_stops_reading_at_pending_capacity(monkeypatch):
    keys = {f"held-{i}" for i in range(4)}
    async with consumer(monkeypatch, held=keys, capacity=3) as c:
        for key in sorted(keys):
            await c.queue.put(ready(key))
            assert await take(c.started) == key
        for i in range(3):
            await c.queue.put(ready(f"pending-{i}"))
        await c.queue.wait_reads(7)
        # Resume after the dispatcher handled the seventh read.
        checkpoint = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_soon(checkpoint.set_result, None)
        await checkpoint
        for i in range(3):
            c.queue.put_nowait(ready(f"queued-{i}"))
        with pytest.raises(asyncio.QueueFull):
            c.queue.put_nowait(ready("overflow"))
        assert c.queue.read_attempts == 7
        assert c.started.empty()
        assert c.maximum() == 4
        c.release.set()
        for _ in range(10):
            await take(c.completed)
        assert len(c.calls) == 10
        assert c.maximum() <= 4


async def test_delivery_failure_does_not_stop_other_terminals(monkeypatch, caplog):
    async with consumer(monkeypatch, failures={"bad"}) as c:
        await c.queue.put(ready("bad"))
        await c.queue.put(ready("good"))
        assert {await take(c.completed), await take(c.completed)} == {"bad", "good"}
        await c.queue.put(ready("later"))
        assert await take(c.completed) == "later"
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


@pytest.mark.parametrize("capacity", [0, 2])
async def test_saturated_pending_buffer_has_finite_fairness_limit(monkeypatch, capacity):
    pending_cap = max(1, capacity)
    async with consumer(monkeypatch, held={"a"}, capacity=capacity) as c:
        await c.queue.put(ready("a"))
        assert await take(c.started) == "a"
        for _ in range(pending_cap):
            await c.queue.put(ready("a"))
        await c.queue.wait_reads(pending_cap + 1)
        await c.queue.put(ready("b"))
        assert c.started.empty()
        assert c.queue.qsize() == 1
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


async def test_late_worker_is_fenced_even_after_admission_reopens(monkeypatch):
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    finished = asyncio.Event()
    release = threading.Event()
    async with consumer(monkeypatch) as c:
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
        finally:
            release.set()
            await asyncio.wait_for(finished.wait(), timeout=5)
        assert c.calls == []
        assert c.service._active_operations == 0


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
        async with consumer(monkeypatch) as c:
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
