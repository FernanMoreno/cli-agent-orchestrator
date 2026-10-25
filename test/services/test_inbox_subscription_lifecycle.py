"""A stopped consumer releases its queue, independently of delivery workers."""

import asyncio
import threading

import pytest

from cli_agent_orchestrator.services import event_bus
from cli_agent_orchestrator.services import inbox_service as module

pytestmark = [pytest.mark.asyncio, pytest.mark.offline_database]


class TrackingBus(event_bus.EventBus):
    def __init__(self):
        super().__init__()
        self.subscribed = asyncio.Event()
        self.queues = []

    def subscribe_sink(self, pattern, queue):
        super().subscribe_sink(pattern, queue)
        self.queues.append(queue)
        self.subscribed.set()
        return queue


@pytest.fixture
def bus(monkeypatch):
    monkeypatch.setattr(event_bus, "get_server_settings", lambda: {"event_bus_max_queue_size": 32})
    instance = TrackingBus()
    monkeypatch.setattr(module, "bus", instance)
    return instance


async def start_consumer(service, bus):
    bus.subscribed.clear()
    task = asyncio.create_task(service.run())
    await asyncio.wait_for(bus.subscribed.wait(), timeout=5)
    return task, bus.queues[-1]


async def cancel_consumer(task):
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_completed_consumer_churn_does_not_retain_or_feed_queues(bus):
    for _ in range(20):
        task, _queue = await start_consumer(module.InboxService(), bus)
        await cancel_consumer(task)
        assert all(
            _queue is not registered
            for _pattern, queues in bus._wildcard.values()
            for registered in queues
        )

    bus._dispatch("terminal.after.status", {"status": "idle"})
    assert all(queue.empty() for queue in bus.queues)
    assert bus._wildcard == {}


async def test_cancel_removes_only_the_exiting_consumers_queue(bus, monkeypatch):
    observer = bus.subscribe("terminal.*.status")
    other_pattern = bus.subscribe("terminal.*.output")
    service = module.InboxService()
    monkeypatch.setattr(service, "_deliver_pending_locked", lambda *_args: None)
    task, stopped_queue = await start_consumer(service, bus)
    other_task, live_queue = await start_consumer(service, bus)
    try:
        await cancel_consumer(task)
        assert all(
            stopped_queue is not registered
            for _pattern, queues in bus._wildcard.values()
            for registered in queues
        )
        # Ready status reaches the live sink; its delivery port is controlled.
        bus._dispatch("terminal.live.status", {"status": "idle"})
        bus._dispatch("terminal.live.output", {"text": "ready"})
        assert stopped_queue.empty()
        assert observer.qsize() == 1
        assert live_queue.qsize() == 1
        assert other_pattern.qsize() == 1
    finally:
        await cancel_consumer(other_task)
    assert sum(len(queues) for _, queues in bus._wildcard.values()) == 2


@pytest.mark.parametrize("ordinary", [False, True])
async def test_waiter_failure_releases_subscription_and_preserves_error(bus, monkeypatch, ordinary):
    class ConsumerFailure(BaseException):
        pass

    failure = RuntimeError("waiter failed") if ordinary else ConsumerFailure("waiter failed")
    sink = module._StatusEventSink(4, lambda _key: False)

    async def fail_get():
        raise failure

    monkeypatch.setattr(sink.ready, "wait", fail_get)
    monkeypatch.setattr(module, "_StatusEventSink", lambda *_args: sink)
    with pytest.raises(type(failure)) as raised:
        await module.InboxService().run()
    assert raised.value is failure
    assert bus._wildcard == {}


async def test_cancel_during_delivery_unsubscribes_but_keeps_worker_owned(bus, monkeypatch):
    service = module.InboxService()
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()

    def held_delivery(*_args):
        loop.call_soon_threadsafe(entered.set)
        try:
            assert release.wait(timeout=5), "delivery barrier was not released"
        finally:
            finished.set()

    monkeypatch.setattr(service, "_deliver_pending_locked", held_delivery)
    task, queue = await start_consumer(service, bus)
    try:
        bus._dispatch("terminal.held.status", {"status": "idle"})
        await asyncio.wait_for(entered.wait(), timeout=5)
        await cancel_consumer(task)
        service.stop_accepting()
        assert not finished.is_set()
        assert service._active_operations == 1
        with pytest.raises(module.InboxServiceDrainTimeout):
            await service.drain(timeout_s=0.02)
        assert bus._wildcard == {}
        bus._dispatch("terminal.held.status", {"status": "completed"})
        assert queue.empty()
    finally:
        release.set()
        if not task.done():
            await cancel_consumer(task)
        service.stop_accepting()
        await service.drain(timeout_s=5)
    assert finished.is_set()
    assert service._active_operations == 0
    assert service._delivery_locks == {}
