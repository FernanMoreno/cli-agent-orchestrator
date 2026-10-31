"""Bound Inbox admission protects actual PostgreSQL rows before any effect."""

from threading import Event, Lock, Thread
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.inbox import MessageStatus
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import inbox_service as module
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy, CapacityObservation
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission


@pytest.mark.parametrize(
    "method", ["poll_opencode_pending_messages", "reconcile_orphaned_messages"]
)
def test_one_storage_credit_cannot_open_discovery_sql_without_delivery_capacity(
    isolated_memory_db, monkeypatch, method
):
    from sqlalchemy import event

    db.create_terminal("no-scan", "no-scan-session", "worker", "opencode_cli", "r")
    row = db.create_inbox_message("sender", "no-scan", "retained café")
    owner = DeliveryAdmission(AdmissionPolicy())
    memory = 80 * 1024**2
    assert owner.observe(
        CapacityObservation(10.0, memory, memory, 16000, 16000, 1),
        now=10.0,
        generation=owner.generation,
    )
    service = module.InboxService(admission=owner, clock=lambda: 10.0)
    sessions, checkouts = [], []
    original_factory = db.SessionLocal

    def factory(*args, **kwargs):
        sessions.append(True)
        return original_factory(*args, **kwargs)

    def checkout(*args):
        checkouts.append(True)

    with monkeypatch.context() as patch:
        patch.setattr(db, "SessionLocal", factory)
        event.listen(db.engine, "checkout", checkout)
        try:
            getattr(service, method)()
            assert sessions == checkouts == []
        finally:
            event.remove(db.engine, "checkout", checkout)
    assert owner.snapshot(now=10.0).inflight == 0
    assert owner.snapshot(now=10.0).storage_only_inflight == 0
    assert [(message.id, message.status) for message in db.get_inbox_messages("no-scan")] == [
        (row.id, MessageStatus.PENDING)
    ]


def test_real_discovery_cursor_and_deliveries_share_exact_physical_quantities(
    isolated_memory_db, monkeypatch
):
    from sqlalchemy import text

    from cli_agent_orchestrator.services.work_terminal import terminal_dispatch_lock

    assert db.engine.dialect.name == "postgresql"
    receivers = ["scanner-worker-a", "scanner-worker-b"]
    for receiver in receivers:
        db.create_terminal(receiver, "scan-session-" + receiver, "worker", "opencode_cli", "r")
        db.create_inbox_message("sender", receiver, "scanner café 中文")
    owner = DeliveryAdmission(AdmissionPolicy())
    memory = 80 * 1024**2 * len(receivers)
    assert owner.observe(
        CapacityObservation(10.0, memory, memory, 16000, 16000, 1 + 2 * len(receivers)),
        now=10.0,
        generation=owner.generation,
    )
    service = module.InboxService(admission=owner, clock=lambda: 10.0)
    all_entered, release_workers, allow_scan_close = Event(), Event(), Event()
    closed, joining = Event(), Event()
    guard = Lock()
    entered, errors = [], []
    original_list = module.iter_pending_receiver_ids_by_provider
    original_join = service._join_deliveries

    def listing(provider):
        source = original_list(provider)
        try:
            for receiver in source:
                yield receiver
            # PostgreSQL's cursor remains owned until source.close(). Hold its
            # last yielded row instead of exhausting it before the workers run.
        finally:
            source.close()
            closed.set()

    def held_listing(provider):
        source = listing(provider)
        try:
            for number, receiver in enumerate(source, 1):
                yield receiver
                if number == len(receivers):
                    assert all_entered.wait(10)
                    assert allow_scan_close.wait(15)
        finally:
            source.close()

    def send(terminal_id, *_args, **_kwargs):
        with terminal_dispatch_lock(db.main_target(), terminal_id):
            with db.SessionLocal() as session:
                assert session.execute(text("SELECT 42")).scalar() == 42
                with guard:
                    entered.append(terminal_id)
                    if len(entered) == len(receivers):
                        all_entered.set()
                assert release_workers.wait(15)

    def join(pending):
        joining.set()
        return original_join(pending)

    def run():
        try:
            service.poll_opencode_pending_messages()
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(module, "iter_pending_receiver_ids_by_provider", held_listing)
    monkeypatch.setattr(service, "_join_deliveries", join)
    monkeypatch.setattr(
        module.provider_manager,
        "get_provider",
        lambda _: SimpleNamespace(has_pending_native_swarm=False),
    )
    monkeypatch.setattr(module.status_monitor, "get_status", lambda _: TerminalStatus.IDLE)
    monkeypatch.setattr(module.terminal_service, "send_input", send)
    thread = Thread(target=run)
    thread.start()
    try:
        assert all_entered.wait(10), "both real delivery transactions did not enter"
        assert not closed.is_set()
        snapshot = owner.snapshot(now=10.0)
        assert snapshot.delivery_started == len(receivers)
        assert snapshot.storage_only_started == snapshot.storage_only_inflight == 1
        assert snapshot.inflight == 1 + len(receivers)
        assert snapshot.storage_connection_loans == snapshot.committed.storage_slots == 5
        assert snapshot.storage_completion_credits == 5
        allow_scan_close.set()
        assert joining.wait(10) and closed.is_set(), "scanner was not closed before join"
        snapshot = owner.snapshot(now=10.0)
        assert snapshot.storage_only_inflight == snapshot.storage_only_started == 0
        assert snapshot.delivery_started == len(receivers)
        assert snapshot.storage_connection_loans == snapshot.committed.storage_slots == 4
    finally:
        allow_scan_close.set()
        release_workers.set()
        thread.join(10)
    assert not thread.is_alive() and not errors
    assert sorted(entered) == receivers
    assert owner.snapshot(now=10.0).inflight == 0
    assert all(
        db.get_inbox_messages(receiver)[0].status == MessageStatus.DELIVERED
        for receiver in receivers
    )


@pytest.mark.parametrize("path", ["direct", "poll"])
@pytest.mark.parametrize("recovered_deliveries", [1, 2, 7])
def test_real_pending_row_survives_denial_and_delivers_after_capacity(
    isolated_memory_db, monkeypatch, path, recovered_deliveries
):
    assert db.engine.dialect.name == "postgresql"
    db.create_terminal(
        "resource-receiver", "resource-session", "worker", "opencode_cli", "reviewer"
    )
    payload = "pendiente café 中文 Ω"
    row = db.create_inbox_message("sender", "resource-receiver", payload)
    owner = DeliveryAdmission(AdmissionPolicy())
    clock = [10.0]

    def observe(deliveries):
        memory = 80 * 1024**2 * deliveries
        slots = 2 * deliveries + int(path == "poll" and deliveries > 0)
        assert owner.observe(
            CapacityObservation(clock[0], memory, max(memory, 1024**2), 16000, 16000, slots),
            now=clock[0],
            generation=owner.generation,
        )
        assert owner.snapshot(now=clock[0]).limit == deliveries

    observe(0)
    service = module.InboxService(admission=owner, clock=lambda: clock[0])
    sent = []
    monkeypatch.setattr(
        module.provider_manager,
        "get_provider",
        lambda _: SimpleNamespace(has_pending_native_swarm=False),
    )
    monkeypatch.setattr(module.status_monitor, "get_status", lambda _: TerminalStatus.IDLE)
    monkeypatch.setattr(module.terminal_service, "send_input", lambda *a, **k: sent.append((a, k)))

    def deliver():
        if path == "direct":
            service.deliver_pending("resource-receiver")
        else:
            service.poll_opencode_pending_messages()

    deliver()
    actual = db.get_inbox_messages("resource-receiver")
    assert [(message.id, message.status) for message in actual] == [(row.id, MessageStatus.PENDING)]
    assert sent == []
    clock[0] = 11.0
    observe(recovered_deliveries)
    deliver()
    assert sent == [(("resource-receiver", payload), {"task_delivery": True})]
    assert [message.status for message in db.get_inbox_messages("resource-receiver")] == [
        MessageStatus.DELIVERED
    ]
    assert owner.snapshot(now=clock[0]).inflight == service._active_operations == 0
    assert service._delivery_locks == {}


def test_storage_stages_between_real_transactions_keep_delivery_ownership(isolated_memory_db):
    """Component composition only: Inbox does not yet invoke these stage tokens."""
    assert db.engine.dialect.name == "postgresql"
    db.create_terminal("staged-receiver", "staged-session", "worker", "opencode_cli", "reviewer")
    owner = DeliveryAdmission(AdmissionPolicy())
    assert owner.observe(
        CapacityObservation(10.0, 2**32, 2**32, 16000, 16000, 1),
        now=10.0,
        generation=owner.generation,
    )
    first = owner.try_reserve("staged-receiver", now=10.0).reservation
    assert first is not None and first.start()
    stage = first.take_storage_stage(now=10.0)
    assert stage is not None
    try:
        row = db.create_inbox_message("sender", "staged-receiver", "etapas café 中文")
    finally:
        assert stage.finish()
    assert owner.snapshot(now=10.0).committed.storage_slots == 0
    assert owner.try_reserve("staged-receiver", now=10.0).reason == "receiver_busy"
    other = owner.try_reserve("other-receiver", now=10.0).reservation
    assert other is not None and other.start()
    assert first.take_storage_stage(now=10.0) is None
    assert other.finish()
    stage = first.take_storage_stage(now=10.0)
    assert stage is not None
    try:
        actual = db.get_inbox_messages("staged-receiver")
        assert [(message.id, message.status) for message in actual] == [
            (row.id, MessageStatus.PENDING)
        ]
    finally:
        assert stage.finish()
    snapshot = owner.snapshot(now=10.0)
    assert snapshot.started == 1 and snapshot.committed.storage_slots == 0
    assert snapshot.committed.memory_bytes == first.footprint.memory_bytes
    assert snapshot.committed.cpu_millicores == first.footprint.cpu_millicores
    assert first.finish()
    assert owner.snapshot(now=10.0).inflight == 0


def test_inbox_real_queries_and_receipt_share_physical_storage_owner(
    isolated_memory_db, monkeypatch
):
    from sqlalchemy import event

    db.create_terminal(
        "physical-receiver", "physical-session", "worker", "opencode_cli", "reviewer"
    )
    row = db.create_inbox_message("sender", "physical-receiver", "physical owner café")
    owner = DeliveryAdmission(AdmissionPolicy())
    assert owner.observe(
        CapacityObservation(10.0, 2**32, 2**32, 16000, 16000, 2),
        now=10.0,
        generation=owner.generation,
    )
    service = module.InboxService(admission=owner, clock=lambda: 10.0)
    monkeypatch.setattr(
        module.provider_manager,
        "get_provider",
        lambda _: SimpleNamespace(has_pending_native_swarm=False),
    )
    monkeypatch.setattr(module.status_monitor, "get_status", lambda _: TerminalStatus.IDLE)
    sent, statements = [], []

    def send(*args, **kwargs):
        snapshot = owner.snapshot(now=10.0)
        assert snapshot.started == 1
        assert snapshot.storage_connection_loans == 0
        assert snapshot.storage_completion_credits == 2
        sent.append(args)

    def before_query(*args):
        snapshot = owner.snapshot(now=10.0)
        assert snapshot.started == snapshot.storage_connection_loans == 1
        assert snapshot.committed.storage_slots == 2
        statements.append(args[2])

    monkeypatch.setattr(module.terminal_service, "send_input", send)
    event.listen(db.engine, "before_cursor_execute", before_query)
    try:
        service.deliver_pending("physical-receiver")
    finally:
        event.remove(db.engine, "before_cursor_execute", before_query)
    assert statements and len(sent) == 1
    actual = db.get_inbox_messages("physical-receiver")
    assert [(message.id, message.status) for message in actual] == [
        (row.id, MessageStatus.DELIVERED)
    ]
    assert owner.snapshot(now=10.0).inflight == 0
