"""Retiring a shared hold wakes the exact parked receiver without a new event."""

import asyncio
import threading
from test.services.test_delivery_admission import observe
from test.services.test_shared_admission_runtime import Monitor, principal
from test.services.test_shared_delivery_admission import FakeRepo, registry
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services import inbox_service as module
from cli_agent_orchestrator.services.inbox_admission_runtime import InboxAdmissionRuntime

pytestmark = pytest.mark.offline_database


@pytest.mark.asyncio
async def test_shared_retirement_resumes_parked_receiver_without_another_status(
    registry, monkeypatch
):
    loop = asyncio.get_running_loop()
    events, sent = [], asyncio.Queue()
    monitor = Monitor(events)
    service = module.InboxService(clock=lambda: 10)
    repo = FakeRepo()
    runtime = InboxAdmissionRuntime(
        service,
        monitor=monitor,
        bootstrap=object(),
        clock=lambda: 10,
        drain_timeout_s=10,
        shared_repository=repo,
    )
    subscribed = asyncio.Event()
    cleanup_entered, release_cleanup, release_first = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    sink = None
    original_read = repo.read_operation
    calls = []

    def subscribe(pattern, actual):
        nonlocal sink
        sink = actual
        subscribed.set()

    def read(operation_id):
        cleanup_entered.set()
        assert release_cleanup.wait(10), "cleanup barrier was not released"
        return original_read(operation_id)

    def deliver(receiver, *_args):
        calls.append(receiver)
        loop.call_soon_threadsafe(sent.put_nowait, receiver)
        if len(calls) == 1:
            assert release_first.wait(10), "first body barrier was not released"

    def ready():
        return {"topic": "terminal.receiver.status", "data": {"status": "idle"}}

    async def parked():
        while (
            sink._is_busy("receiver")
            or len(sink.pending) != 1
            or sink.pending.peek_event() is not None
        ):
            await asyncio.sleep(0.005)

    consumer = None
    with monkeypatch.context() as patch:
        patch.setattr(
            module, "bus", SimpleNamespace(subscribe_sink=subscribe, unsubscribe=lambda *_: None)
        )
        patch.setattr(module, "get_server_settings", lambda: {"event_bus_max_queue_size": 8})
        patch.setattr(service, "_deliver_pending_locked", deliver)
        patch.setattr(repo, "read_operation", read)
        try:
            await runtime.start()
            assert observe(monitor.owner, 8)
            await runtime.activate_shared(principal=principal())
            assert monitor.owner.snapshot(now=10).available_slots > 0
            consumer = asyncio.create_task(service.run())
            await asyncio.wait_for(subscribed.wait(), 2)
            sink.put_nowait(ready())
            assert await asyncio.wait_for(sent.get(), 2) == "receiver"
            sink.put_nowait(ready())
            release_first.set()
            assert await asyncio.to_thread(cleanup_entered.wait, 2)
            await asyncio.wait_for(parked(), 2)
            assert runtime._shared.pending_count == 1
            assert monitor.owner.snapshot(now=10).delivery_started == 1
            release_cleanup.set()
            # No status, manual notify or monitor tick may manufacture this wake.
            assert await asyncio.wait_for(sent.get(), 2) == "receiver"
            assert calls == ["receiver", "receiver"]
        finally:
            release_first.set()
            release_cleanup.set()
            if consumer is not None:
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
            await runtime.stop()
    assert runtime.retired and not runtime._shared.worker_alive
    assert service._deliver_pending_locked is not deliver
    assert repo.read_operation == original_read
