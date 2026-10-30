"""Durable inbox receipts use the existing bounded, context-preserving consumer."""

import asyncio
import contextvars
import threading
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services import inbox_service as module

pytestmark = [pytest.mark.asyncio, pytest.mark.offline_database]


async def test_queued_requests_preserve_independent_context_and_registry(monkeypatch):
    service = module.InboxService()
    loop = asyncio.get_running_loop()
    subscribed = asyncio.Event()
    observed = asyncio.Queue()
    sink_holder = []
    marker = contextvars.ContextVar("delivery_trace_tag", default="lifespan")
    server_registry, request_registry = object(), object()

    def subscribe(_pattern, sink):
        sink_holder.append(sink)
        subscribed.set()

    monkeypatch.setattr(
        module, "bus", SimpleNamespace(subscribe_sink=subscribe, unsubscribe=lambda *_: None)
    )
    monkeypatch.setattr(module, "get_server_settings", lambda: {"event_bus_max_queue_size": 8})

    def deliver(terminal_id, _limit, registry):
        loop.call_soon_threadsafe(observed.put_nowait, (terminal_id, marker.get(), registry))

    monkeypatch.setattr(service, "_deliver_pending_locked", deliver)
    task = asyncio.create_task(service.run(server_registry))
    try:
        await asyncio.wait_for(subscribed.wait(), timeout=5)
        token = marker.set("request-a")
        assert service.queue_delivery("a", registry=request_registry)
        marker.set("request-b")
        assert service.queue_delivery("b", registry=None)
        marker.reset(token)
        sink_holder[0].put_nowait({"topic": "terminal.c.status", "data": {"status": "idle"}})
        rows = [await asyncio.wait_for(observed.get(), timeout=5) for _ in range(3)]
        assert set(rows) == {
            ("a", "request-a", request_registry),
            ("b", "request-b", None),
            ("c", "lifespan", server_registry),
        }
        assert marker.get() == "lifespan"
        service.stop_accepting()
        with pytest.raises(module.InboxServiceStoppingError):
            service.queue_delivery("late", registry=request_registry)
    finally:
        service.stop_accepting()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.drain(timeout_s=5)
    assert service._delivery_sink is None
    assert not sink_holder[0]._requests


async def test_full_wake_sink_retains_old_context_and_closed_sink_releases_it():
    sink = module._StatusEventSink(1, lambda _key: False)
    context = contextvars.copy_context()
    registry = object()
    assert sink.offer_pending("a", (context, registry))
    with pytest.raises(asyncio.QueueFull):
        sink.offer_pending("b", (contextvars.copy_context(), None))
    assert sink.qsize() == 1
    assert sink.pop_delivery() == ("a", (context, registry))
    assert not sink._requests
    sink.pending.completed("a")
    assert sink.offer_pending("b", (contextvars.copy_context(), None))
    sink.close()
    assert not sink._requests and sink.empty()
    assert sink.offer_pending("late", (contextvars.copy_context(), registry)) is False


async def test_no_consumer_cannot_accept_a_transient_wake():
    service = module.InboxService()
    assert service.queue_delivery("pending", registry=None) is False


async def test_request_burst_uses_four_workers_and_shutdown_drops_only_transient_wakes(monkeypatch):
    service = module.InboxService()
    loop = asyncio.get_running_loop()
    subscribed = asyncio.Event()
    started = asyncio.Queue()
    release = threading.Event()
    sinks = []

    def subscribe(_pattern, sink):
        sinks.append(sink)
        subscribed.set()

    monkeypatch.setattr(
        module, "bus", SimpleNamespace(subscribe_sink=subscribe, unsubscribe=lambda *_: None)
    )
    monkeypatch.setattr(module, "get_server_settings", lambda: {"event_bus_max_queue_size": 8})

    def deliver(terminal_id, _limit, _registry):
        loop.call_soon_threadsafe(started.put_nowait, terminal_id)
        assert release.wait(5), "request consumer controller did not release workers"

    monkeypatch.setattr(service, "_deliver_pending_locked", deliver)
    task = asyncio.create_task(service.run())
    try:
        await asyncio.wait_for(subscribed.wait(), timeout=5)
        for index in range(12):
            assert service.queue_delivery(f"request-{index}", registry=None)
        active = {await asyncio.wait_for(started.get(), timeout=5) for _ in range(4)}
        assert len(active) == 4
        assert sinks[0].qsize() == 8 and len(sinks[0]._requests) == 8
        assert service._active_operations == 4
        with pytest.raises(RuntimeError):
            await asyncio.to_thread(service.queue_delivery, "wrong-loop", registry=None)
        assert sinks[0].qsize() == 8
        service.stop_accepting()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert sinks[0].empty() and not sinks[0]._requests
        assert service._delivery_sink is None
        assert service._active_operations == 4
    finally:
        service.stop_accepting()
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.drain(timeout_s=5)
    assert service._active_operations == 0
