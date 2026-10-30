"""Every bound legacy Inbox entry shares capacity before SQL or handoff."""

import asyncio
import contextvars
import threading
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services import inbox_service as module
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy, CapacityObservation
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

pytestmark = pytest.mark.offline_database


def service(slots=8):
    owner = DeliveryAdmission(AdmissionPolicy())
    owner.observe(
        CapacityObservation(10.0, 2**32, 2**32, 16000, 16000, slots),
        now=10.0,
        generation=owner.generation,
    )
    return module.InboxService(admission=owner, clock=lambda: 10.0), owner


def test_denied_direct_delivery_does_not_read_or_mark_rows(monkeypatch):
    inbox, owner = service(0)
    calls = []
    monkeypatch.setattr(module, "get_pending_messages", lambda *_a, **_k: calls.append("read"))
    monkeypatch.setattr(module, "update_message_status", lambda *_: calls.append("mark"))
    inbox.deliver_pending("pending")
    assert calls == []
    assert inbox._delivery_locks == {}
    assert owner.snapshot(now=10.0).inflight == inbox._active_operations == 0


def test_failed_owner_restart_leaves_service_admission_closed():
    inbox, owner = service(1)
    lease = owner.try_reserve("prior-physical-worker", now=10.0).reservation
    assert lease is not None and lease.start()
    inbox.stop_accepting()
    try:
        with pytest.raises(RuntimeError, match="active"):
            inbox.start_accepting()
        with pytest.raises(module.InboxServiceStoppingError):
            inbox.deliver_pending("new")
    finally:
        lease.finish()


def test_thread_creation_failure_before_start_cancels_unused_reservation(monkeypatch):
    inbox, owner = service(1)
    seen = []

    class NeverStarted:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise RuntimeError("controlled-never-started")

    monkeypatch.setattr(module.threading, "Thread", NeverStarted)
    monkeypatch.setattr(module, "iter_pending_receiver_ids_by_provider", lambda *_: ["unused"])
    monkeypatch.setattr(inbox, "_deliver_pending_locked", lambda *_: seen.append("effect"))
    with pytest.raises(RuntimeError, match="controlled-never-started"):
        inbox.poll_opencode_pending_messages()
    assert seen == []
    assert owner.snapshot(now=10.0).inflight == inbox._active_operations == 0


def test_serial_batch_keeps_one_invocation_reservation_until_every_send_and_write(monkeypatch):
    from datetime import datetime

    from cli_agent_orchestrator.models.inbox import InboxMessage, MessageStatus
    from cli_agent_orchestrator.models.terminal import TerminalStatus

    inbox, owner = service(1)
    messages = [
        InboxMessage(
            id=i,
            sender_id=sender,
            receiver_id="batch",
            message=str(i),
            status=MessageStatus.PENDING,
            created_at=datetime.now(),
        )
        for i, sender in ((1, "sender-a"), (2, "sender-b"))
    ]
    reads, writes, sent = [], [], []

    def read(tid, *, limit):
        reads.append((tid, limit))
        return messages

    def send(tid, text, **kwargs):
        assert owner.snapshot(now=10.0).started == 1
        assert owner.try_reserve("other", now=10.0).reservation is None
        sent.append(text)

    monkeypatch.setattr(module, "get_pending_messages", read)
    monkeypatch.setattr(module, "update_message_status", lambda *a: writes.append(a))
    monkeypatch.setattr(
        module.provider_manager,
        "get_provider",
        lambda _: SimpleNamespace(has_pending_native_swarm=False),
    )
    monkeypatch.setattr(module.status_monitor, "get_status", lambda _: TerminalStatus.IDLE)
    monkeypatch.setattr(module.terminal_service, "send_input", send)
    inbox.deliver_pending("batch", num_messages=0)
    assert reads == [("batch", 100)]
    assert writes == [(1, MessageStatus.DELIVERED), (2, MessageStatus.DELIVERED)]
    assert sent == ["1", "2"]
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.asyncio
async def test_consumer_cancellation_does_not_release_a_physically_started_delivery(monkeypatch):
    inbox, owner = service(1)
    subscribed, entered, release = asyncio.Event(), threading.Event(), threading.Event()

    def subscribe(*_):
        subscribed.set()

    def deliver(*_):
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(
        module, "bus", SimpleNamespace(subscribe_sink=subscribe, unsubscribe=lambda *_: None)
    )
    monkeypatch.setattr(inbox, "_deliver_pending_locked", deliver)
    runner = asyncio.create_task(inbox.run())
    try:
        await asyncio.wait_for(subscribed.wait(), 5)
        inbox.queue_delivery("held", registry=None)
        assert await asyncio.to_thread(entered.wait, 5)
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        assert owner.snapshot(now=10.0).started == 1
        assert inbox._active_operations == 1
        assert owner.try_reserve("other", now=10.0).reservation is None
    finally:
        release.set()
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        inbox.stop_accepting()
        await inbox.drain(timeout_s=10)
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.asyncio
async def test_sweep_drain_owns_worker_when_thread_start_reports_error_after_physical_start(
    monkeypatch,
):
    inbox, owner = service(1)
    entered, release, parent_done = threading.Event(), threading.Event(), threading.Event()
    errors = []
    original_thread = threading.Thread

    class StartedButUnpublished(original_thread):
        def start(self):
            super().start()
            if self.name == "cao-inbox-admitted":
                assert entered.wait(5)
                raise RuntimeError("controlled-start-publication-failure")

    def deliver(*_):
        entered.set()
        assert release.wait(10)

    def sweep():
        try:
            inbox.poll_opencode_pending_messages()
        except BaseException as error:
            errors.append(error)
        finally:
            parent_done.set()

    monkeypatch.setattr(module.threading, "Thread", StartedButUnpublished)
    monkeypatch.setattr(module, "iter_pending_receiver_ids_by_provider", lambda *_: ["held"])
    monkeypatch.setattr(inbox, "_deliver_pending_locked", deliver)
    parent = original_thread(target=sweep)
    parent.start()
    try:
        assert await asyncio.to_thread(parent_done.wait, 5)
        assert len(errors) == 1 and str(errors[0]) == "controlled-start-publication-failure"
        assert owner.snapshot(now=10.0).started == 1
        assert inbox._active_operations == 1, "live child lost its drain ownership with its parent"
    finally:
        inbox.stop_accepting()
        release.set()
        parent.join(5)
        await inbox.drain(timeout_s=10)
    assert not parent.is_alive()
    assert owner.snapshot(now=10.0).inflight == inbox._active_operations == 0


@pytest.mark.asyncio
async def test_consumer_can_start_more_than_four_under_one_observed_budget(monkeypatch):
    inbox, owner = service(8)
    subscribed = asyncio.Event()
    started = asyncio.Queue()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    sinks = []

    def subscribe(_topic, sink):
        sinks.append(sink)
        subscribed.set()

    def deliver(tid, *_):
        loop.call_soon_threadsafe(started.put_nowait, tid)
        assert release.wait(10)

    monkeypatch.setattr(
        module, "bus", SimpleNamespace(subscribe_sink=subscribe, unsubscribe=lambda *_: None)
    )
    monkeypatch.setattr(inbox, "_deliver_pending_locked", deliver)
    runner = asyncio.create_task(inbox.run())
    try:
        await asyncio.wait_for(subscribed.wait(), 5)
        for i in range(9):
            inbox.queue_delivery(str(i), registry=None)
        observed = {await asyncio.wait_for(started.get(), 5) for _ in range(8)}
        assert observed == {str(i) for i in range(8)}
        assert owner.snapshot(now=10.0).started == 8
        assert sinks[0].qsize() == 1
    finally:
        release.set()
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        inbox.stop_accepting()
        await inbox.drain(timeout_s=10)
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.asyncio
async def test_denied_wake_retains_ordinal_context_registry_until_capacity_signal(monkeypatch):
    inbox, owner = service(0)
    marker = contextvars.ContextVar("request_trace", default="lifespan")
    loop = asyncio.get_running_loop()
    subscribed, delivered = asyncio.Event(), asyncio.Queue()
    sinks = []
    registry = object()

    def subscribe(_topic, sink):
        sinks.append(sink)
        subscribed.set()

    monkeypatch.setattr(
        module, "bus", SimpleNamespace(subscribe_sink=subscribe, unsubscribe=lambda *_: None)
    )
    monkeypatch.setattr(
        inbox,
        "_deliver_pending_locked",
        lambda tid, limit, reg: loop.call_soon_threadsafe(
            delivered.put_nowait, (tid, marker.get(), reg)
        ),
    )
    runner = asyncio.create_task(inbox.run())
    try:
        await asyncio.wait_for(subscribed.wait(), 5)
        token = marker.set("original-request")
        inbox.queue_delivery("pending", registry=registry)
        marker.reset(token)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert sinks[0].qsize() == 1 and len(sinks[0]._requests) == 1
        assert not delivered.qsize()
        owner.observe(
            CapacityObservation(11.0, 2**32, 2**32, 16000, 16000, 2),
            now=11.0,
            generation=owner.generation,
        )
        inbox._admission_clock = lambda: 11.0
        inbox.notify_admission_changed()
        assert await asyncio.wait_for(delivered.get(), 5) == (
            "pending",
            "original-request",
            registry,
        )
        assert sinks[0].qsize() == len(sinks[0]._requests) == 0
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        inbox.stop_accepting()
        await inbox.drain(timeout_s=10)


@pytest.mark.parametrize(
    ("method", "listing"),
    [
        ("poll_opencode_pending_messages", "iter_pending_receiver_ids_by_provider"),
        ("reconcile_orphaned_messages", "iter_pending_receiver_ids_older_than"),
    ],
)
def test_sweep_closes_cursor_before_waiting_and_shares_direct_budget(monkeypatch, method, listing):
    inbox, owner = service(1)
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    seen, errors = [], []

    def receivers(*_):
        try:
            yield "sweep"
            yield "deferred"
        finally:
            closed.set()

    def deliver(tid, *_):
        seen.append(tid)
        entered.set()
        assert release.wait(10)

    def sweep():
        try:
            getattr(inbox, method)()
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(module, listing, receivers)
    monkeypatch.setattr(inbox, "_deliver_pending_locked", deliver)
    thread = threading.Thread(target=sweep)
    thread.start()
    try:
        assert entered.wait(5)
        assert closed.wait(5), "SQL cursor remained open during a capacity wait"
        inbox.deliver_pending("direct-deferred")
        assert seen == ["sweep"]
        assert owner.snapshot(now=10.0).started == 1
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and not errors
    assert owner.snapshot(now=10.0).inflight == inbox._active_operations == 0


def test_join_reports_original_failure_only_after_all_owned_children_retire():
    from concurrent.futures import Future

    failure = RuntimeError("controlled-worker-failure")
    failed, held = Future(), Future()
    failed.set_exception(failure)
    entered, returned = threading.Event(), threading.Event()
    errors = []

    def join():
        entered.set()
        try:
            module.InboxService._join_deliveries([failed, held])
        except BaseException as error:
            errors.append(error)
        finally:
            returned.set()

    thread = threading.Thread(target=join)
    thread.start()
    assert entered.wait(5)
    # The first result is an error, but this physically active child still owns
    # parent admission. A barrier proves the error cannot finish the parent.
    premature = returned.wait(0.1)
    held.set_result(None)
    thread.join(5)
    assert not thread.is_alive()
    assert not premature
    assert errors == [failure]


@pytest.mark.asyncio
async def test_physical_finish_cannot_republish_key_before_consumer_retires_its_future(monkeypatch):
    from itertools import count

    inbox, owner = service(2)
    subscribed = asyncio.Event()
    finished, publish = threading.Event(), threading.Event()
    sinks = []
    calls = []
    loop = asyncio.get_running_loop()
    second = asyncio.Event()

    def subscribe(_topic, sink):
        sinks.append(sink)
        subscribed.set()

    original = inbox._deliver_status_pending
    publications = count()

    def delayed_publication(*args):
        first = next(publications) == 0
        original(*args)
        if first:
            finished.set()
            assert publish.wait(10)

    def deliver(tid, *_):
        calls.append(tid)
        if len(calls) == 2:
            loop.call_soon_threadsafe(second.set)

    monkeypatch.setattr(
        module, "bus", SimpleNamespace(subscribe_sink=subscribe, unsubscribe=lambda *_: None)
    )
    monkeypatch.setattr(inbox, "_deliver_pending_locked", deliver)
    monkeypatch.setattr(inbox, "_deliver_status_pending", delayed_publication)
    runner = asyncio.create_task(inbox.run())
    try:
        await asyncio.wait_for(subscribed.wait(), 5)
        inbox.queue_delivery("same", registry=None)
        inbox.queue_delivery("same", registry=None)
        assert await asyncio.to_thread(finished.wait, 5)
        # Let the physical-finally notification run while publication is held.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert calls == ["same"]
        assert sinks[0].qsize() == 1
        publish.set()
        await asyncio.wait_for(second.wait(), 5)
    finally:
        publish.set()
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        inbox.stop_accepting()
        await inbox.drain(timeout_s=10)
