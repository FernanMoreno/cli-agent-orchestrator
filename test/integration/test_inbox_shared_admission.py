"""Inbox effects use the real shared PostgreSQL ledger and exact local owner."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from test.postgres.test_admission_metadata_credits import grant
from test.postgres.test_admission_reservations import api, binding, domain
from test.postgres.test_shared_admission_mapping import add_domain
from test.services.test_delivery_admission import observe
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.inbox import MessageStatus
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.security.auth import _verified_principal
from cli_agent_orchestrator.services import inbox_service as inbox_module
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
from cli_agent_orchestrator.services.shared_delivery_admission import authorize_shared_delivery


def settle(coordinator):
    deadline = time.monotonic() + 10
    while coordinator.pending_count and time.monotonic() < deadline:
        threading.Event().wait(0.01)
    assert coordinator.pending_count == 0, coordinator.error


@pytest.mark.parametrize("deliveries,allow", [(1, False), (1, True), (6, True)])
def test_inbox_shared_capacity_precedes_real_receipt_and_send(
    isolated_memory_db, monkeypatch, deliveries, allow
):
    target = isolated_memory_db
    module = api()
    current = binding(target)
    repository = module.ResourceAdmissionRepository(target, binding_supplier=lambda: current)
    assert repository.audit_usage() == ()
    shared = target, repository, current, module
    storage = domain(shared, 1 + (2 * deliveries if allow else 0))
    for kind in ("host", "node"):
        add_domain(shared, kind, "bytes", 4096 * 1024**2)
        add_domain(shared, kind, "millicores", 16000)
    grant(shared, storage)
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    owner.require_shared_admission()
    assert observe(owner, 32)
    retired = [False]
    principal = _verified_principal("urn:caos:shared-inbox", "operator", ["cao:admin"], "jwt")
    with permit.operation(clock=lambda: 10):
        coordinator = authorize_shared_delivery(
            principal=principal,
            repository=repository,
            owner=owner,
            control=permit,
            retirement=lambda: retired[0],
            clock=lambda: 10,
        )
        owner.bind_shared_admission(coordinator)
    receivers = [f"shared-inbox-{i}" for i in range(deliveries)]
    for receiver in receivers:
        db.create_terminal(receiver, f"session-{receiver}", "worker", "opencode_cli", "reviewer")
        db.create_inbox_message("sender", receiver, "Shared delivery café 中文")
    service = inbox_module.InboxService(admission=owner, clock=lambda: 10)
    entered, release = threading.Event(), threading.Event()
    guard = threading.Lock()
    sent = []

    def send(receiver, *_args, **_kwargs):
        with guard:
            sent.append(receiver)
            if len(sent) == deliveries:
                entered.set()
        assert release.wait(15)

    with monkeypatch.context() as patch:
        patch.setattr(
            inbox_module.provider_manager,
            "get_provider",
            lambda _: SimpleNamespace(has_pending_native_swarm=False),
        )
        patch.setattr(inbox_module.status_monitor, "get_status", lambda _: TerminalStatus.IDLE)
        patch.setattr(inbox_module.terminal_service, "send_input", send)
        try:
            with ThreadPoolExecutor(max_workers=deliveries) as workers:
                futures = [
                    workers.submit(service.deliver_pending, receiver) for receiver in receivers
                ]
                try:
                    if allow:
                        assert entered.wait(15), "all admitted effects did not enter"
                        assert repository.used(storage.spec.domain_id) == 1 + 2 * deliveries
                        snapshot = owner.snapshot(now=10)
                        assert snapshot.delivery_started == deliveries
                        assert snapshot.committed.storage_slots == 2 * deliveries
                    else:
                        for future in futures:
                            future.result(timeout=15)
                        assert sent == []
                        assert repository.used(storage.spec.domain_id) == 1
                finally:
                    release.set()
                for future in futures:
                    future.result(timeout=15)
            settle(coordinator)
            expected = MessageStatus.DELIVERED if allow else MessageStatus.PENDING
            assert all(
                db.get_inbox_messages(receiver)[0].status == expected for receiver in receivers
            )
            assert repository.used(storage.spec.domain_id) == 1
            assert repository.audit_usage() == ()
            assert owner.snapshot(now=10).inflight == 0
        finally:
            release.set()
            owner.stop_accepting()
            coordinator.drain(20)
            assert not coordinator.worker_alive
            retired[0] = True
