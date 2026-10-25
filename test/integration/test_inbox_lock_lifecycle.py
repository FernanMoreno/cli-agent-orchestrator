"""Real PostgreSQL inbox ordering and exactly-once delivery under lock contention."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.inbox import MessageStatus
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import inbox_service as module


def test_contended_postgres_delivery_keeps_order_and_retires_lock(isolated_memory_db, monkeypatch):
    assert db.engine.dialect.name == "postgresql"
    terminal_id = "inbox-lock-race"
    db.create_terminal(terminal_id, "delivery-session", "worker", "opencode_cli", "reviewer")
    payloads = [f"task {index}: café, 中文." for index in range(3)]
    messages = [db.create_inbox_message("sender", terminal_id, body) for body in payloads]
    with db.SessionLocal() as session:
        session.query(db.InboxModel).filter(db.InboxModel.receiver_id == terminal_id).update(
            {db.InboxModel.created_at: datetime(2026, 10, 25, 12, 0, 0)}
        )
        session.commit()

    service = module.InboxService()
    monkeypatch.setattr(
        module.provider_manager,
        "get_provider",
        lambda _terminal_id: SimpleNamespace(has_pending_native_swarm=False),
    )
    monkeypatch.setattr(module.status_monitor, "get_status", lambda _id: TerminalStatus.IDLE)
    real_get_pending = module.get_pending_messages
    first_read = threading.Event()
    release_read = threading.Event()
    sent = []

    def held_read(receiver_id, *, limit):
        rows = real_get_pending(receiver_id, limit=limit)
        if rows and not first_read.is_set():
            first_read.set()
            assert release_read.wait(10)
        return rows

    def send_input(receiver_id, text, **kwargs):
        assert receiver_id == terminal_id
        assert kwargs == {"task_delivery": True}
        sent.append(text)

    monkeypatch.setattr(module, "get_pending_messages", held_read)
    monkeypatch.setattr(module.terminal_service, "send_input", send_input)
    start = threading.Barrier(9)

    def deliver():
        start.wait(timeout=10)
        service.deliver_pending(terminal_id)

    with ThreadPoolExecutor(max_workers=8) as workers:
        futures = [workers.submit(deliver) for _ in range(8)]
        try:
            start.wait(timeout=10)
            assert first_read.wait(10)
            release_read.set()
            for future in futures:
                future.result(timeout=10)
        finally:
            release_read.set()

    assert sent == payloads
    assert real_get_pending(terminal_id, limit=10) == []
    rows = db.get_inbox_messages(terminal_id)
    assert {row.id for row in rows} == {message.id for message in messages}
    assert all(row.status == MessageStatus.DELIVERED for row in rows)
    assert service._active_operations == 0
    assert service._delivery_locks == {}
