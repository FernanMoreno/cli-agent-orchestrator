"""Status events cross real PostgreSQL while an unrelated receiver is held."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.inbox import MessageStatus
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import event_bus
from cli_agent_orchestrator.services import inbox_service as module


@pytest.mark.asyncio
async def test_postgres_status_delivery_bypasses_held_receiver_and_preserves_order(
    isolated_memory_db,
    monkeypatch,
):
    assert db.engine.dialect.name == "postgresql"
    payloads = {"held": [f"task {i}: café, 中文." for i in range(3)], "free": ["otro: acción."]}
    for terminal_id, messages in payloads.items():
        db.create_terminal(terminal_id, "status-session", "worker", "opencode_cli", "reviewer")
        for body in messages:
            db.create_inbox_message("sender", terminal_id, body)

    monkeypatch.setattr(
        module.provider_manager,
        "get_provider",
        lambda _id: SimpleNamespace(has_pending_native_swarm=False),
    )
    monkeypatch.setattr(module.status_monitor, "get_status", lambda _id: TerminalStatus.IDLE)
    monkeypatch.setattr(event_bus, "get_server_settings", lambda: {"event_bus_max_queue_size": 16})
    bus = event_bus.EventBus()
    service = module.InboxService()
    monkeypatch.setattr(module, "bus", bus)
    loop = asyncio.get_running_loop()
    subscribed = asyncio.Event()
    held_read = asyncio.Event()
    release = threading.Event()
    sent = asyncio.Queue()
    real_subscribe = bus.subscribe
    real_get_pending = module.get_pending_messages
    first = True

    def subscribe(pattern):
        queue = real_subscribe(pattern)
        subscribed.set()
        return queue

    def get_pending(receiver_id, *, limit):
        nonlocal first
        rows = real_get_pending(receiver_id, limit=limit)
        if receiver_id == "held" and first:
            first = False
            loop.call_soon_threadsafe(held_read.set)
            assert release.wait(timeout=10), "PostgreSQL read barrier was not released"
        return rows

    def send_input(receiver_id, text, **kwargs):
        assert kwargs == {"task_delivery": True}
        loop.call_soon_threadsafe(sent.put_nowait, (receiver_id, text))

    monkeypatch.setattr(bus, "subscribe", subscribe)
    monkeypatch.setattr(module, "get_pending_messages", get_pending)
    monkeypatch.setattr(module.terminal_service, "send_input", send_input)
    task = asyncio.create_task(service.run())
    try:
        await asyncio.wait_for(subscribed.wait(), timeout=5)
        bus._dispatch("terminal.held.status", {"status": "idle"})
        await asyncio.wait_for(held_read.wait(), timeout=5)
        for _ in range(2):
            bus._dispatch("terminal.held.status", {"status": "idle"})
        bus._dispatch("terminal.free.status", {"status": "completed"})
        assert await asyncio.wait_for(sent.get(), timeout=5) == ("free", payloads["free"][0])
        assert not release.is_set()
        release.set()
        for expected in payloads["held"]:
            assert await asyncio.wait_for(sent.get(), timeout=5) == ("held", expected)
    finally:
        release.set()
        service.stop_accepting()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.drain(timeout_s=10)

    for terminal_id in payloads:
        assert real_get_pending(terminal_id, limit=10) == []
        assert all(
            row.status == MessageStatus.DELIVERED for row in db.get_inbox_messages(terminal_id)
        )
    assert sent.empty()
    assert bus._wildcard == {}
    assert service._active_operations == 0
    assert service._delivery_locks == {}
