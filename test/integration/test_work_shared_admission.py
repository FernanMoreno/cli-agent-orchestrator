"""Shared ledger precedes real Work intent and registered managed delivery."""

import threading
from test.integration.test_work_dispatch import context  # noqa: F401
from test.postgres.test_admission_metadata_credits import grant
from test.postgres.test_admission_reservations import api, binding, domain
from test.postgres.test_shared_admission_mapping import add_domain
from test.services.test_delivery_admission import observe
from test.services.test_work_inbox import _managed_module, _request, paired_context

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
from cli_agent_orchestrator.services.inbox_service import InboxService
from cli_agent_orchestrator.services.shared_delivery_admission import authorize_shared_delivery
from cli_agent_orchestrator.services.work_admission import WorkAdmission
from cli_agent_orchestrator.services.work_blocking import run_owned_blocking

pytestmark = pytest.mark.postgres


def setup(context, allow):
    target = context.repo.target
    module = api()
    current = binding(target)
    repository = module.ResourceAdmissionRepository(target, binding_supplier=lambda: current)
    assert repository.audit_usage() == ()
    shared = target, repository, current, module
    storage = domain(shared, 8 if allow else 1)
    for kind in ("host", "node"):
        add_domain(shared, kind, "bytes", 4096 * 1024**2)
        add_domain(shared, kind, "millicores", 16000)
    grant(shared, storage)
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    owner.require_shared_admission()
    assert observe(owner, 16)
    retired = [False]
    with permit.operation(clock=lambda: 10):
        shared_coordinator = authorize_shared_delivery(
            principal=context.actor,
            repository=repository,
            owner=owner,
            control=permit,
            retirement=lambda: retired[0],
            clock=lambda: 10,
        )
        owner.bind_shared_admission(shared_coordinator)
    inbox = InboxService(admission=owner, clock=lambda: 10)
    context.service = WorkAdmission(
        context.repo,
        backends={"test": context.backend},
        delivery_adapters=_managed_module().managed_inbox_delivery_adapters(
            context.repo, delivery_service=inbox
        ),
        resource_admission=owner,
        resource_clock=lambda: 10,
    )
    coordinator = _managed_module().WorkInboxCoordinator(context.service)
    receipt = coordinator.admit(**_request(context, "shared-admission"))
    before = context.repo.get_work(receipt.work_item_id)
    return repository, storage, owner, shared_coordinator, retired, receipt, before


@pytest.fixture
def off_loop_sql(paired_context, monkeypatch):
    from sqlalchemy import event

    from cli_agent_orchestrator.clients import work_repository as work_module

    loop_thread = threading.get_ident()
    original = work_module.connect
    seen = []

    def owned_connect(*args, **kwargs):
        assert threading.get_ident() != loop_thread, "Work SQL opened on the event loop"
        seen.append(threading.get_ident())
        return original(*args, **kwargs)

    def owned_checkout(*args):
        assert threading.get_ident() != loop_thread, "Inbox SQL checked out on the event loop"
        seen.append(threading.get_ident())

    with monkeypatch.context() as patch:
        patch.setattr(work_module, "connect", owned_connect)
        event.listen(database.engine, "checkout", owned_checkout)
        try:
            yield seen
        finally:
            event.remove(database.engine, "checkout", owned_checkout)
    assert work_module.connect is original


@pytest.mark.asyncio
@pytest.mark.parametrize("allow", [False, True])
async def test_work_intent_and_registered_effect_obey_shared_admission(
    paired_context, allow, off_loop_sql
):
    context = paired_context
    repository, storage, owner, shared, retired, receipt, before = await run_owned_blocking(
        setup, context, allow
    )
    try:
        result = await context.service.dispatch_registered_next()
        if allow:
            assert result["id"] == receipt.work_item_id
            assert len(context.backend.effects) == 1
        else:
            assert result is None
            after = await run_owned_blocking(context.repo.get_work, receipt.work_item_id)
            assert after == before
            assert after["attempts"][-1]["state"] == "planned"
            assert context.backend.effects == []
        owner.stop_accepting()
        await run_owned_blocking(shared.drain, 20)
        assert shared.pending_count == 0 and not shared.worker_alive
        assert owner.snapshot(now=10).inflight == 0
        assert await run_owned_blocking(repository.used, storage.spec.domain_id) == 1
        assert await run_owned_blocking(repository.audit_usage) == ()
        assert off_loop_sql
        rows = await run_owned_blocking(database.get_inbox_messages, "receiver", status=None)
        assert len(rows) == 1 and rows[0].status.value == "reconcile"
    finally:
        owner.stop_accepting()
        await run_owned_blocking(shared.drain, 20)
        retired[0] = True
