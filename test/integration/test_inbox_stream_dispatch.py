"""A live discovery cursor and independent delivery SQL sessions compose safely."""

import asyncio
import json
import threading
from contextlib import closing
from types import SimpleNamespace

import pytest
from sqlalchemy import event, text

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.inbox import MessageStatus
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import inbox_service as module


@pytest.mark.asyncio
async def test_live_receiver_cursor_is_not_shared_with_delivery_workers(
    isolated_memory_db, monkeypatch
):
    assert db.engine.dialect.name == "postgresql"
    count = 160
    ids = [f"live-stream-{i:04d}" for i in range(count)]
    with db.SessionLocal() as seed:
        seed.bulk_insert_mappings(
            db.TerminalModel,
            [
                {
                    "id": tid,
                    "tmux_session": "live-stream",
                    "tmux_window": tid,
                    "provider": "opencode_cli",
                    "agent_profile": "reviewer",
                }
                for tid in ids
            ],
        )
        seed.bulk_insert_mappings(
            db.InboxModel,
            [
                {
                    "sender_id": "sender",
                    "receiver_id": tid,
                    "message": tid,
                    "status": MessageStatus.PENDING.value,
                }
                for tid in ids
            ],
        )
        seed.commit()
    service = module.InboxService()
    actual_discovery = module.iter_pending_receiver_ids_by_provider
    actual_pending = module.get_pending_messages
    loop = asyncio.get_running_loop()
    held_started = threading.Event()
    held_read = asyncio.Event()
    release_held = threading.Event()
    resume_scan = threading.Event()
    sent = asyncio.Queue()
    targets = {}
    worker_pids = {}
    cursors = []
    closed_at = []
    baseline = db.engine.pool.checkedout()

    def capture(_connection, cursor, _statement, _parameters, context, _many):
        if context.execution_options.get("yield_per"):
            cursors.append(cursor)

    def receivers(provider):
        try:
            with closing(actual_discovery(provider)) as source:
                targets["held"] = next(source)
                targets["ready"] = next(source)
                yield targets["held"]
                yield targets["ready"]
                assert resume_scan.wait(10), "parent discovery barrier was not released"
                yield from source
        finally:
            closed_at.append((service._active_operations, all(cursor.closed for cursor in cursors)))

    def pending(tid, *, limit):
        if tid == targets["ready"]:
            assert held_started.wait(5)
        rows = actual_pending(tid, limit=limit)
        if tid in targets.values():
            with db.SessionLocal() as session:
                worker_pids[tid] = session.execute(text("SELECT pg_backend_pid()")).scalar_one()
        if tid == targets["held"]:
            held_started.set()
            loop.call_soon_threadsafe(held_read.set)
            assert release_held.wait(10)
        return rows

    def send(tid, payload, **kwargs):
        assert payload == tid and kwargs == {"task_delivery": True}
        loop.call_soon_threadsafe(sent.put_nowait, tid)

    monkeypatch.setattr(module, "iter_pending_receiver_ids_by_provider", receivers)
    monkeypatch.setattr(module, "get_pending_messages", pending)
    monkeypatch.setattr(
        module.provider_manager,
        "get_provider",
        lambda _: SimpleNamespace(has_pending_native_swarm=False),
    )
    monkeypatch.setattr(module.status_monitor, "get_status", lambda _: TerminalStatus.IDLE)
    monkeypatch.setattr(module.terminal_service, "send_input", send)
    event.listen(db.engine, "before_cursor_execute", capture)
    task = asyncio.create_task(asyncio.to_thread(service.poll_opencode_pending_messages))
    observed = []
    try:
        await asyncio.wait_for(held_read.wait(), 5)
        observed.append(await asyncio.wait_for(sent.get(), 5))
        assert observed == [targets["ready"]]
        assert len(cursors) == 1 and cursors[0].name and not cursors[0].closed
        parent_pid = cursors[0].connection.info.backend_pid
        assert all(pid != parent_pid for pid in worker_pids.values())
        assert db.engine.pool.checkedout() == baseline + 1
        with db.SessionLocal() as observer:
            activity = (
                observer.execute(
                    text(
                        "SELECT state, backend_xmin IS NOT NULL AS snapshot_open, "
                        "EXTRACT(EPOCH FROM (clock_timestamp()-xact_start)) AS snapshot_age_seconds "
                        "FROM pg_stat_activity WHERE pid=:pid"
                    ),
                    {"pid": parent_pid},
                )
                .mappings()
                .one()
            )
        assert activity["state"] == "idle in transaction" and activity["snapshot_open"]
        print(
            json.dumps(
                {
                    "t222_live_cursor": {
                        "state": activity["state"],
                        "snapshot_open": activity["snapshot_open"],
                        "snapshot_age_seconds": float(activity["snapshot_age_seconds"]),
                    }
                }
            )
        )
        assert not release_held.is_set() and not resume_scan.is_set()
        assert db.get_inbox_messages(targets["held"])[0].status == MessageStatus.PENDING
        assert db.get_inbox_messages(targets["ready"])[0].status == MessageStatus.DELIVERED
        release_held.set()
        resume_scan.set()
        await asyncio.wait_for(task, 10)
        while not sent.empty():
            observed.append(sent.get_nowait())
    finally:
        release_held.set()
        resume_scan.set()
        await asyncio.gather(task, return_exceptions=True)
        service.stop_accepting()
        await service.drain(timeout_s=10)
        event.remove(db.engine, "before_cursor_execute", capture)
    assert len(observed) == count and set(observed) == set(ids)
    assert closed_at == [(1, True)]
    assert service._active_operations == 0 and service._delivery_locks == {}
    assert db.engine.pool.checkedout() == baseline
