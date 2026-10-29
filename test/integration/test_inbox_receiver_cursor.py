"""Actual PostgreSQL discovery uses a finite snapshot and bounded client buffer."""

import gc
import json
import tracemalloc
from contextlib import closing
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.inbox import MessageStatus


@pytest.mark.parametrize("mode", ["provider", "age"])
def test_receiver_cursor_filters_snapshot_and_explicit_early_close(
    isolated_memory_db, monkeypatch, mode
):
    assert db.engine.dialect.name == "postgresql"
    old = datetime.now() - timedelta(minutes=2)
    recent = datetime.now() + timedelta(minutes=5)
    receivers = [f"stream-{i:04d}" for i in range(160)]
    actual_factory = db.SessionLocal

    def terminal(session, tid, provider):
        session.add(
            db.TerminalModel(
                id=tid,
                tmux_session="stream-test",
                tmux_window=tid,
                provider=provider,
                agent_profile="reviewer",
            )
        )

    def message(session, tid, status, created):
        session.add(
            db.InboxModel(
                sender_id="sender",
                receiver_id=tid,
                message="payload",
                status=status,
                created_at=created,
            )
        )

    with actual_factory() as seed:
        for i, tid in enumerate(receivers):
            terminal(seed, tid, "opencode_cli")
            message(seed, tid, MessageStatus.PENDING.value, old)
            if i % 17 == 0:
                message(seed, tid, MessageStatus.PENDING.value, old)
        for tid, provider, status, created in [
            ("other-provider", "codex", MessageStatus.PENDING.value, old),
            ("delivered", "opencode_cli", MessageStatus.DELIVERED.value, old),
            ("recent-other", "codex", MessageStatus.PENDING.value, recent),
        ]:
            terminal(seed, tid, provider)
            message(seed, tid, status, created)
        message(seed, "ghost", MessageStatus.PENDING.value, old)
        seed.commit()

    sessions, results, cursors = [], [], []

    class TrackingSession(Session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            sessions.append(self)

        def execute(self, *args, **kwargs):
            result = super().execute(*args, **kwargs)
            results.append(result)
            return result

    monkeypatch.setattr(
        db,
        "SessionLocal",
        sessionmaker(bind=db.engine, class_=TrackingSession, autocommit=False, autoflush=False),
    )

    def cursor_observed(_connection, cursor, _statement, _parameters, context, _many):
        options = dict(context.execution_options)
        if options.get("yield_per"):
            cursors.append((getattr(cursor, "name", None), options))

    event.listen(db.engine, "before_cursor_execute", cursor_observed)
    baseline = db.engine.pool.checkedout()

    def discover():
        if mode == "provider":
            return db.iter_pending_receiver_ids_by_provider("opencode_cli")
        return db.iter_pending_receiver_ids_older_than(30)

    try:
        with closing(discover()) as probe:
            assert next(probe)
            result, session = results[-1], sessions[-1]
            assert not result.closed and session.get_transaction() is not None
            assert db.engine.pool.checkedout() == baseline + 1
        assert result.closed and session.get_transaction() is None
        assert db.engine.pool.checkedout() == baseline

        with closing(discover()) as current:
            first = next(current)
            changed = receivers[0]
            with actual_factory() as update:
                update.query(db.InboxModel).filter(db.InboxModel.receiver_id == changed).update(
                    {"status": MessageStatus.DELIVERED.value}, synchronize_session=False
                )
                if mode == "provider":
                    terminal(update, "late-new", "opencode_cli")
                    message(update, "late-new", MessageStatus.PENDING.value, old)
                else:
                    update.query(db.InboxModel).filter(
                        db.InboxModel.receiver_id == "recent-other"
                    ).update({"created_at": old}, synchronize_session=False)
                update.commit()
            snapshot = {first, *current}
        initial = set(receivers) | ({"other-provider"} if mode == "age" else set())
        assert snapshot == initial
        with closing(discover()) as later:
            refreshed = set(later)
        assert refreshed == (initial - {changed}) | (
            {"late-new"} if mode == "provider" else {"recent-other"}
        )
        assert len(cursors) == 3
        assert all(name for name, _ in cursors), "expected actual psycopg named cursors"
        for _name, options in cursors:
            assert options["stream_results"] is True
            assert options["yield_per"] == options["max_row_buffer"] == 128
        assert all(result.closed for result in results)
        assert all(session.get_transaction() is None for session in sessions)
        assert db.engine.pool.checkedout() == baseline
    finally:
        event.remove(db.engine, "before_cursor_execute", cursor_observed)


def test_discovery_python_memory_does_not_follow_receiver_count(isolated_memory_db):
    assert db.engine.dialect.name == "postgresql"
    sizes = {"opencode_cli": 256, "codex": 8192}
    created = datetime.now()
    with db.SessionLocal() as seed:
        for provider, count in sizes.items():
            seed.bulk_insert_mappings(
                db.TerminalModel,
                [
                    {
                        "id": f"{provider}-{i:05d}",
                        "tmux_session": "memory-test",
                        "tmux_window": f"{provider}-{i:05d}",
                        "provider": provider,
                        "agent_profile": "reviewer",
                    }
                    for i in range(count)
                ],
            )
            seed.bulk_insert_mappings(
                db.InboxModel,
                [
                    {
                        "sender_id": "sender",
                        "receiver_id": f"{provider}-{i:05d}",
                        "message": "payload",
                        "status": MessageStatus.PENDING.value,
                        "created_at": created,
                    }
                    for i in range(count)
                ],
            )
        seed.commit()

    def consume(provider):
        with closing(db.iter_pending_receiver_ids_by_provider(provider)) as iterator:
            return sum(1 for _ in iterator)

    for provider, count in sizes.items():
        assert consume(provider) == count

    def peak(provider):
        gc.collect()
        tracemalloc.start()
        try:
            count = consume(provider)
            _, maximum = tracemalloc.get_traced_memory()
            return count, maximum
        finally:
            tracemalloc.stop()

    small_count, small_peak = peak("opencode_cli")
    large_count, large_peak = peak("codex")
    print(
        json.dumps(
            {
                "t222_memory": {
                    "small_receivers": small_count,
                    "large_receivers": large_count,
                    "small_peak_bytes": small_peak,
                    "large_peak_bytes": large_peak,
                }
            }
        )
    )
    assert small_count == 256 and large_count == 8192
    assert large_peak <= small_peak + 512 * 1024
