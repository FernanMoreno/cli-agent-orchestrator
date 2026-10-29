"""Real API fallback daemons cross PostgreSQL while an independent key is held."""

import asyncio
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from cli_agent_orchestrator.api import main as api
from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.constants import INBOX_RECONCILE_GRACE_SECONDS
from cli_agent_orchestrator.models.inbox import MessageStatus, OrchestrationType
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.plugins import PluginRegistry
from cli_agent_orchestrator.services import inbox_service as module


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("listing", "daemon", "interval"),
    [
        (
            "iter_pending_receiver_ids_by_provider",
            "opencode_inbox_delivery_daemon",
            "INBOX_POLLING_INTERVAL",
        ),
        (
            "iter_pending_receiver_ids_older_than",
            "inbox_reconciliation_daemon",
            "INBOX_RECONCILE_INTERVAL",
        ),
    ],
)
async def test_actual_daemon_and_postgres_deliver_b_while_a_read_is_held(
    isolated_memory_db, monkeypatch, listing, daemon, interval
):
    assert db.engine.dialect.name == "postgresql"
    payloads = {"held": ["primero café 中文", "segundo acción"], "ready": ["independiente Ω"]}
    ids = []
    for tid, messages in payloads.items():
        db.create_terminal(tid, "sweep-owned", tid, "opencode_cli", "reviewer")
        ids.extend(db.create_inbox_message("sender", tid, message).id for message in messages)
    with db.SessionLocal() as session:
        expected_database = session.execute(text("SELECT current_database()")).scalar_one()
        session.query(db.InboxModel).filter(db.InboxModel.id.in_(ids)).update(
            {"created_at": datetime.now() - timedelta(seconds=INBOX_RECONCILE_GRACE_SECONDS + 2)}
        )
        session.commit()

    service = module.InboxService()
    registry = PluginRegistry()
    loop = asyncio.get_running_loop()
    entered_a = asyncio.Event()
    release_a = threading.Event()
    sent = asyncio.Queue()
    lock = threading.Lock()
    first = True
    databases = []
    actual_listing = getattr(module, listing)
    actual_pending = module.get_pending_messages

    def receivers(*args):
        # Preserve the actual PostgreSQL discovery query; only make the held
        # key's discovery order deterministic for the barrier schedule.
        return sorted(actual_listing(*args), key=lambda tid: tid != "held")

    def pending(tid, *, limit):
        nonlocal first
        rows = actual_pending(tid, limit=limit)
        with db.SessionLocal() as session:
            observed_database = session.execute(text("SELECT current_database()")).scalar_one()
        with lock:
            databases.append(observed_database)
            hold = tid == "held" and first
            if hold:
                first = False
        if hold:
            loop.call_soon_threadsafe(entered_a.set)
            assert release_a.wait(10), "owned PostgreSQL read barrier was not released"
        return rows

    def send_input(tid, message, **kwargs):
        assert kwargs == {
            "registry": registry,
            "sender_id": "sender",
            "orchestration_type": OrchestrationType.SEND_MESSAGE,
            "task_delivery": True,
        }
        loop.call_soon_threadsafe(sent.put_nowait, (tid, message))

    monkeypatch.setattr(module, listing, receivers)
    monkeypatch.setattr(module, "get_pending_messages", pending)
    monkeypatch.setattr(
        module.provider_manager,
        "get_provider",
        lambda _: SimpleNamespace(has_pending_native_swarm=False),
    )
    monkeypatch.setattr(module.status_monitor, "get_status", lambda _: TerminalStatus.IDLE)
    monkeypatch.setattr(module.terminal_service, "send_input", send_input)
    monkeypatch.setattr(api, "inbox_service", service)
    monkeypatch.setattr(api, interval, 0.01)
    task = asyncio.create_task(getattr(api, daemon)(registry))
    try:
        await asyncio.wait_for(entered_a.wait(), 5)
        assert await asyncio.wait_for(sent.get(), 5) == ("ready", payloads["ready"][0])
        assert not release_a.is_set()
        assert [row.status for row in db.get_inbox_messages("ready")] == [MessageStatus.DELIVERED]
        assert all(row.status == MessageStatus.PENDING for row in db.get_inbox_messages("held"))
        release_a.set()
        for expected in payloads["held"]:
            assert await asyncio.wait_for(sent.get(), 5) == ("held", expected)
        for tid in payloads:
            assert all(row.status == MessageStatus.DELIVERED for row in db.get_inbox_messages(tid))
        assert not module.get_pending_messages("held", limit=10)
        assert not module.get_pending_messages("ready", limit=10)
    finally:
        release_a.set()
        service.stop_accepting()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.drain(timeout_s=10)
    assert databases and set(databases) == {expected_database}
    assert service._active_operations == 0 and service._delivery_locks == {}
    assert sent.empty(), "an already-delivered row was sent again"
