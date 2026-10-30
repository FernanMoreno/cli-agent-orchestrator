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
def test_real_pending_row_survives_denial_and_delivers_after_capacity(
    isolated_memory_db, monkeypatch, path
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
    observe(2)
    deliver()
    assert sent == [(("resource-receiver", payload), {"task_delivery": True})]
    assert [message.status for message in db.get_inbox_messages("resource-receiver")] == [
        MessageStatus.DELIVERED
    ]
    assert owner.snapshot(now=clock[0]).inflight == service._active_operations == 0
    assert service._delivery_locks == {}
