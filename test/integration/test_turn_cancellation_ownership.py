"""Recovery composition with real PostgreSQL; CLI/reset readiness are controlled."""

import asyncio
import os
import queue
import threading
from pathlib import Path
from test.postgres.orm import configured_orm_engine
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import turn_recovery_service as recovery
from cli_agent_orchestrator.services import work_terminal
from cli_agent_orchestrator.services.work_terminal import terminal_dispatch_lock

GEN = "a" * 32
TOKEN_HASH = "b" * 64
NEXT_GEN = "c" * 32


@pytest_asyncio.fixture
async def harness(monkeypatch):
    engine = configured_orm_engine()
    assert engine.dialect.name == "postgresql"
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine))
    loop = asyncio.get_running_loop()
    locks = {}
    before = set(recovery._cancel_jobs)
    h = SimpleNamespace(
        events=[],
        reset_entered=threading.Event(),
        reset_release=threading.Event(),
        ready_entered=asyncio.Event(),
        ready_release=asyncio.Event(),
        second_lock_blocked=asyncio.Event(),
        locks=[],
        providers={},
        failure=None,
        held=True,
        blocked="1234abcd",
    )
    h.reset_release.set()
    h.ready_release.set()

    def create(tid):
        child = db.plan_native_child(
            parent_terminal_id="parent",
            terminal_id=tid,
            provider="codex",
            agent_profile="developer",
            lease_seconds=30,
        )
        db.create_terminal(
            tid,
            "session",
            tid,
            "codex",
            "developer",
            caller_id="parent",
            expected_native_child_id=child["id"],
        )
        assert db.begin_terminal_turn_receipt(tid, "codex", GEN, TOKEN_HASH)
        assert db.mark_terminal_turn_receipt_sent(tid, GEN, TOKEN_HASH)
        db.transition_native_child(tid, "sent")
        provider = Mock()

        async def initialize():
            h.events.append((tid, "initializing"))
            assert db.get_terminal_turn_recovery(tid)["state"] == "cancelling"
            assert db.get_native_child_for_terminal(tid)["state"] == "cancelled"
            if h.backend.supports_event_inbox():
                h.monitor.reset_buffer.assert_any_call(tid, provider=provider)
            else:
                resets = [
                    call for call in h.monitor.reset_buffer.call_args_list if call.args == (tid,)
                ]
                assert len(resets) == 1
                assert resets[0].kwargs["provider"] is provider
                assert isinstance(resets[0].kwargs["capture_identity"], str)
                assert resets[0].kwargs["capture_identity"]
            if tid == h.blocked:
                h.ready_entered.set()
                await h.ready_release.wait()
                if h.failure == "initialize_raises":
                    raise RuntimeError("readiness uncertain")
                if h.failure == "initialize_false":
                    return False
            return True

        def release_receipt():
            assert db.get_terminal_turn_recovery(tid)["state"] == "cancelled"
            h.events.append((tid, "released"))
            if tid == h.blocked:
                h.held = False

        provider.initialize = AsyncMock(side_effect=initialize)
        provider.mark_turn_receipt_cancelled.side_effect = release_receipt
        h.providers[tid] = provider
        return tid

    h.create = create
    h.tid = create(h.blocked)

    def reset(_session, window, *, terminal_id):
        assert window == terminal_id
        assert db.get_terminal_turn_recovery(terminal_id)["state"] == "cancelling"
        h.events.append((terminal_id, "reset"))
        if terminal_id == h.blocked:
            h.reset_entered.set()
            assert h.reset_release.wait(10), "test reset barrier not released"
            if h.failure == "reset":
                raise RuntimeError("reset uncertain")

    backend = Mock()
    backend.reset_window.side_effect = reset
    backend.supports_event_inbox.return_value = True
    h.backend = backend
    monkeypatch.setattr(recovery, "get_backend", lambda: backend)
    monkeypatch.setattr(recovery.provider_manager, "get_provider", h.providers.get)
    monitor = Mock()
    monitor.get_status.return_value = TerminalStatus.IDLE
    h.monitor = monitor
    monkeypatch.setattr(recovery, "status_monitor", monitor)

    def publish(topic, payload):
        tid = topic.split(".")[1]
        # Another queued caller can already own the lock. Check THIS job's
        # actual PostgreSQL connection was released, rather than reacquiring
        # synchronously and blocking the event loop behind that caller.
        owned = locks[asyncio.current_task()].actual
        assert owned._connection is None and owned._lock_key is None
        assert db.get_terminal_turn_recovery(tid)["state"] == "cancelled"
        assert h.providers[tid].mark_turn_receipt_cancelled.call_count == 1
        assert payload == {"status": "idle"}
        h.events.append((tid, "published"))

    h.publish = Mock(side_effect=publish)
    monkeypatch.setattr(recovery.bus, "publish", h.publish)
    attempts = 0
    counter_lock = threading.Lock()
    current_lock = threading.local()
    original_connect = work_terminal.connect

    class ObservedCursor:
        def __init__(self, cursor, owner):
            self.cursor, self.owner = cursor, owner

        def fetchone(self):
            row = self.cursor.fetchone()
            if self.owner.second and row is not None and not row[0]:
                # Observe an actual negative PostgreSQL result, not merely
                # arrival at __enter__ or the absence of a second reset yet.
                loop.call_soon_threadsafe(h.second_lock_blocked.set)
            return row

    class ObservedConnection:
        def __init__(self, target):
            self.actual = original_connect(target)
            self.owner = current_lock.owner

        def execute(self, query, *args):
            result = self.actual.execute(query, *args)
            if query.startswith("SELECT pg_try_advisory_lock("):
                return ObservedCursor(result, self.owner)
            return result

        def close(self):
            self.actual.close()

    monkeypatch.setattr(work_terminal, "connect", ObservedConnection)

    class ObservedLock:
        def __init__(self, target, tid):
            self.actual = terminal_dispatch_lock(target, tid)
            self.tid = tid
            self.owner = asyncio.current_task()
            self.second = False
            self.acquired = threading.Event()
            locks[asyncio.current_task()] = self
            h.locks.append(self)

        def __enter__(self):
            nonlocal attempts
            if self.tid == h.blocked:
                with counter_lock:
                    attempts += 1
                    if attempts == 2:
                        self.second = True
            current_lock.owner = self
            try:
                result = self.actual.__enter__()
                self.acquired.set()
                return result
            finally:
                del current_lock.owner

        def __exit__(self, *args):
            return self.actual.__exit__(*args)

        def release(self):
            self.actual.release()

    monkeypatch.setattr(recovery, "terminal_dispatch_lock", ObservedLock)
    try:
        yield h
    finally:
        h.reset_release.set()
        h.ready_release.set()
        jobs = recovery._cancel_jobs - before
        if jobs:
            await asyncio.wait_for(asyncio.gather(*jobs, return_exceptions=True), 15)
        engine.dispose()


def assert_fenced(h):
    assert h.held
    assert db.get_terminal_turn_recovery(h.tid)["state"] == "cancelling"
    assert not db.settle_terminal_turn_receipt_result(h.tid, GEN, TOKEN_HASH, "d" * 64)
    assert db.begin_terminal_turn_receipt(h.tid, "codex", NEXT_GEN, "e" * 64) is None
    h.providers[h.tid].prepare_input.assert_not_called()
    h.backend.send_keys.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect_at", [None, "reset", "readiness"])
async def test_disconnected_caller_keeps_generation_fenced_until_replacement_ready(
    harness, disconnect_at
):
    h = harness
    h.reset_release.clear()
    h.ready_release.clear()
    caller = asyncio.create_task(recovery.cancel_turn(h.tid, GEN))
    assert await asyncio.to_thread(h.reset_entered.wait, 10)
    assert_fenced(h)
    assert db.get_native_child_for_terminal(h.tid)["state"] == "sent"
    if disconnect_at == "reset":
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
    h.reset_release.set()
    await asyncio.wait_for(h.ready_entered.wait(), 10)
    assert_fenced(h)
    assert db.get_native_child_for_terminal(h.tid)["state"] == "cancelled"
    if disconnect_at == "readiness":
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
    jobs = tuple(recovery._cancel_jobs)
    h.ready_release.set()
    if disconnect_at is None:
        assert (await caller)["state"] == "cancelled"
    else:
        assert len(jobs) == 1
        assert (await jobs[0])["state"] == "cancelled"
    assert not h.held
    assert h.events == [
        (h.tid, phase) for phase in ["reset", "initializing", "released", "published"]
    ]
    assert db.begin_terminal_turn_receipt(h.tid, "codex", NEXT_GEN, "e" * 64)
    with pytest.raises(recovery.TurnRecoveryConflict):
        await recovery.cancel_turn(h.tid, GEN)
    assert h.backend.reset_window.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect_waiter", [False, True])
async def test_duplicate_cancellation_waits_for_owner_then_refuses_without_second_reset(
    harness, disconnect_waiter
):
    h = harness
    h.reset_release.clear()
    first = asyncio.create_task(recovery.cancel_turn(h.tid, GEN))
    assert await asyncio.to_thread(h.reset_entered.wait, 10)
    second = asyncio.create_task(recovery.cancel_turn(h.tid, GEN))
    await asyncio.wait_for(h.second_lock_blocked.wait(), 10)
    waiter = h.locks[1]
    assert not waiter.acquired.is_set()
    assert not second.done() and h.backend.reset_window.call_count == 1
    assert_fenced(h)
    if disconnect_waiter:
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
    h.reset_release.set()
    assert (await first)["state"] == "cancelled"
    with pytest.raises(recovery.TurnRecoveryConflict):
        await waiter.owner
    if not disconnect_waiter:
        with pytest.raises(recovery.TurnRecoveryConflict):
            await second
    assert waiter.acquired.is_set()
    assert waiter.actual._connection is None and waiter.actual._lock_key is None
    assert h.backend.reset_window.call_count == 1
    assert h.publish.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["reset", "initialize_false", "initialize_raises", "finish"])
async def test_incomplete_cancellation_retains_fence_and_refuses_new_generation(
    harness, monkeypatch, failure
):
    h = harness
    h.failure = failure
    if failure == "finish":
        monkeypatch.setattr(recovery, "finish_terminal_turn_cancellation", lambda *_: False)
    expected = recovery.TurnRecoveryConflict if failure == "finish" else RuntimeError
    with pytest.raises(expected):
        await recovery.cancel_turn(h.tid, GEN)
    assert_fenced(h)
    assert db.get_native_child_for_terminal(h.tid)["state"] == (
        "sent" if failure == "reset" else "cancelled"
    )
    h.providers[h.tid].mark_turn_receipt_cancelled.assert_not_called()
    h.monitor.publish_receipt_reconciliation.assert_called_once_with(h.tid)
    h.publish.assert_not_called()


@pytest.mark.asyncio
async def test_stale_generation_is_refused_before_any_reset(harness):
    h = harness
    with pytest.raises(recovery.TurnRecoveryConflict):
        await recovery.cancel_turn(h.tid, NEXT_GEN)
    assert db.get_terminal_turn_recovery(h.tid)["state"] == "pending"
    h.backend.reset_window.assert_not_called()
    h.providers[h.tid].initialize.assert_not_awaited()
    h.providers[h.tid].mark_turn_receipt_cancelled.assert_not_called()


@pytest.mark.asyncio
async def test_blocked_reset_does_not_serialize_an_unrelated_terminal(harness):
    h = harness
    other = h.create("5678abcd")
    h.reset_release.clear()
    first = asyncio.create_task(recovery.cancel_turn(h.tid, GEN))
    assert await asyncio.to_thread(h.reset_entered.wait, 10)
    assert (await asyncio.wait_for(recovery.cancel_turn(other, GEN), 10))["state"] == "cancelled"
    assert not first.done()
    assert_fenced(h)
    h.reset_release.set()
    assert (await first)["state"] == "cancelled"
    assert h.publish.call_count == 2


@pytest_asyncio.fixture
async def fifo_harness(harness, monkeypatch, tmp_path):
    """Real per-terminal FIFOs; reset/readiness remain controlled, never a native CLI."""
    from cli_agent_orchestrator.services import fifo_reader as fifo

    h = harness
    h.other = h.create("5678abcd")
    h.backend.supports_event_inbox.return_value = False
    monkeypatch.setattr(fifo, "FIFO_DIR", tmp_path)
    monkeypatch.setattr(recovery, "FIFO_DIR", tmp_path)
    manager = fifo.FifoManager()
    monkeypatch.setattr(fifo, "fifo_manager", manager)
    monkeypatch.setattr(recovery, "fifo_manager", manager, raising=False)
    h.fifo = manager
    h.pipe_entered = threading.Event()
    h.pipe_release = threading.Event()
    h.pipe_release.set()
    h.pipe_stage = None
    h.pipe_failure = None
    received = {tid: queue.Queue() for tid in (h.tid, h.other)}
    ready = {tid: threading.Event() for tid in received}
    original_open = fifo._open_fifo
    original_stop = manager.stop_reader
    original_create = manager.create_reader

    def create_reader(tid, *args, **kwargs):
        ready[tid].clear()
        return original_create(tid, *args, **kwargs)

    monkeypatch.setattr(manager, "create_reader", create_reader)

    def opened(path, flags):
        fd = original_open(path, flags)
        if flags & os.O_WRONLY:
            ready[Path(path).stem].set()
        return fd

    def output(topic, payload):
        tid = topic.split(".")[1]
        received[tid].put(dict(payload))

    monkeypatch.setattr(fifo, "_open_fifo", opened)
    monkeypatch.setattr(fifo, "bus", SimpleNamespace(publish=output))
    for tid in received:
        manager.create_reader(tid)
        assert await asyncio.to_thread(ready[tid].wait, 10)

    def pipe_boundary(stage, tid):
        assert db.get_terminal_turn_recovery(tid)["state"] == "cancelling"
        h.events.append((tid, stage))
        if tid == h.tid and stage == h.pipe_stage:
            h.pipe_entered.set()
            assert h.pipe_release.wait(10), "FIFO boundary not released"
        if tid == h.tid and stage == h.pipe_failure:
            raise RuntimeError("owned FIFO attachment uncertain")

    def detach(session, tid):
        assert session == "session"
        pipe_boundary("detach", tid)

    def attach(session, tid, path):
        assert session == "session" and path == str(tmp_path / f"{tid}.fifo")
        pipe_boundary("attach", tid)
        assert ready[tid].wait(10)
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, (tid + ":replacement\n").encode())
        finally:
            os.close(fd)

    h.backend.stop_pipe_pane.side_effect = detach
    h.backend.pipe_pane.side_effect = attach

    async def received_replacement(tid):
        payload = await asyncio.to_thread(received[tid].get, True, 10)
        assert payload["data"] == tid + ":replacement\n"
        resets = [call for call in h.monitor.reset_buffer.call_args_list if call.args == (tid,)]
        assert len(resets) == 1
        assert payload["capture_identity"] == resets[0].kwargs["capture_identity"]
        assert received[tid].empty()

    h.received_replacement = received_replacement
    try:
        yield h
    finally:
        h.pipe_release.set()
        jobs = list(recovery._cancel_jobs)
        if jobs:
            await asyncio.wait_for(asyncio.gather(*jobs, return_exceptions=True), 15)
        for tid in received:
            assert await asyncio.to_thread(original_stop, tid)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["detach", "attach"])
async def test_fifo_disconnected_caller_keeps_ownership_while_other_terminal_progresses(
    fifo_harness, stage
):
    h = fifo_harness
    h.pipe_stage = stage
    h.pipe_release.clear()
    caller = asyncio.create_task(recovery.cancel_turn(h.tid, GEN))
    assert await asyncio.to_thread(h.pipe_entered.wait, 10)
    owner = next(lock.owner for lock in h.locks if lock.tid == h.tid)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert not owner.done()
    assert_fenced(h)
    h.providers[h.tid].initialize.assert_not_awaited()
    assert (await asyncio.wait_for(recovery.cancel_turn(h.other, GEN), 10))["state"] == "cancelled"
    await h.received_replacement(h.other)
    other_receipt = dict(db.get_native_child_for_terminal(h.other))
    assert not owner.done() and h.held
    h.pipe_release.set()
    assert (await asyncio.wait_for(owner, 10))["state"] == "cancelled"
    await h.received_replacement(h.tid)
    assert db.get_native_child_for_terminal(h.other) == other_receipt
    assert h.publish.call_count == 2
    assert not h.held


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["detach", "attach"])
async def test_fifo_attachment_failure_retains_fence_and_does_not_stop_sibling(fifo_harness, stage):
    h = fifo_harness
    h.pipe_failure = stage
    with pytest.raises(RuntimeError, match="FIFO attachment uncertain"):
        await recovery.cancel_turn(h.tid, GEN)
    assert_fenced(h)
    h.providers[h.tid].initialize.assert_not_awaited()
    h.providers[h.tid].mark_turn_receipt_cancelled.assert_not_called()
    assert (await asyncio.wait_for(recovery.cancel_turn(h.other, GEN), 10))["state"] == "cancelled"
    await h.received_replacement(h.other)
    h.monitor.publish_receipt_reconciliation.assert_called_once_with(h.tid)


@pytest.mark.asyncio
async def test_fifo_reader_retirement_uncertainty_prevents_receipt_release(
    fifo_harness, monkeypatch
):
    h = fifo_harness
    original_stop = h.fifo.stop_reader
    monkeypatch.setattr(
        h.fifo,
        "stop_reader",
        lambda tid, **kwargs: False if tid == h.tid else original_stop(tid, **kwargs),
    )
    with pytest.raises(RuntimeError):
        await recovery.cancel_turn(h.tid, GEN)
    assert_fenced(h)
    h.providers[h.tid].initialize.assert_not_awaited()
    assert not any(tid == h.tid and stage == "attach" for tid, stage in h.events)
    assert (await asyncio.wait_for(recovery.cancel_turn(h.other, GEN), 10))["state"] == "cancelled"
    await h.received_replacement(h.other)


@pytest.mark.asyncio
async def test_fifo_foreign_generation_cannot_mutate_pipe_or_reader(fifo_harness):
    h = fifo_harness
    with pytest.raises(recovery.TurnRecoveryConflict):
        await recovery.cancel_turn(h.tid, NEXT_GEN)
    h.backend.reset_window.assert_not_called()
    h.backend.stop_pipe_pane.assert_not_called()
    h.backend.pipe_pane.assert_not_called()
    assert db.get_terminal_turn_recovery(h.tid)["state"] == "pending"
    assert h.fifo.has_reader(h.tid) and h.fifo.has_reader(h.other)


@pytest.mark.asyncio
async def test_fifo_missing_reader_does_not_turn_unknown_capture_into_ready(fifo_harness):
    h = fifo_harness
    assert await asyncio.to_thread(h.fifo.stop_reader, h.tid)
    with pytest.raises(RuntimeError, match="owned FIFO reader is unavailable"):
        await recovery.cancel_turn(h.tid, GEN)
    assert_fenced(h)
    h.providers[h.tid].initialize.assert_not_awaited()
    h.providers[h.tid].mark_turn_receipt_cancelled.assert_not_called()
    assert (await asyncio.wait_for(recovery.cancel_turn(h.other, GEN), 10))["state"] == "cancelled"
    await h.received_replacement(h.other)
