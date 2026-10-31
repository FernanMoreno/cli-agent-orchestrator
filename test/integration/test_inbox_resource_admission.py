"""Bound Inbox admission protects actual PostgreSQL rows before any effect."""

from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.inbox import MessageStatus
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import inbox_service as module
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy, CapacityObservation
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission


@pytest.mark.parametrize("path", ["direct", "poll"])
@pytest.mark.parametrize("recovered_slots", [1, 2, 7])
def test_real_pending_row_survives_denial_and_delivers_after_capacity(
    isolated_memory_db, monkeypatch, path, recovered_slots
):
    assert db.engine.dialect.name == "postgresql"
    db.create_terminal(
        "resource-receiver", "resource-session", "worker", "opencode_cli", "reviewer"
    )
    payload = "pendiente café 中文 Ω"
    row = db.create_inbox_message("sender", "resource-receiver", payload)
    owner = DeliveryAdmission(AdmissionPolicy())
    clock = [10.0]

    def observe(slots):
        owner.observe(
            CapacityObservation(clock[0], 2**32, 2**32, 16000, 16000, slots),
            now=clock[0],
            generation=owner.generation,
        )

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
    observe(recovered_slots)
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
    owner.observe(
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
    owner.observe(
        CapacityObservation(10.0, 2**32, 2**32, 16000, 16000, 1),
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
        assert snapshot.storage_completion_credits == 1
        sent.append(args)

    def before_query(*args):
        snapshot = owner.snapshot(now=10.0)
        assert snapshot.started == snapshot.storage_connection_loans == 1
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
