"""Production agent-step adapter retains shared credits across owned startup SQL."""

from test.integration.test_work_dispatch import admit, context  # noqa: F401
from test.integration.test_work_shared_admission import off_loop_sql
from test.postgres.test_admission_metadata_credits import grant
from test.postgres.test_admission_reservations import api, binding, domain
from test.postgres.test_shared_admission_mapping import add_domain
from test.services.test_delivery_admission import observe
from test.services.test_work_agent_step import _adapter, agent_step_context, agent_step_envelope
from test.services.test_work_inbox import paired_context

import pytest

from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
from cli_agent_orchestrator.services.shared_delivery_admission import authorize_shared_delivery
from cli_agent_orchestrator.services.work_admission import WorkAdmission
from cli_agent_orchestrator.services.work_blocking import run_owned_blocking

pytestmark = pytest.mark.postgres


def setup(context):
    target = context.repo.target
    module = api()
    current = binding(target)
    repository = module.ResourceAdmissionRepository(target, binding_supplier=lambda: current)
    assert repository.audit_usage() == ()
    shared = target, repository, current, module
    storage = domain(shared, 8)
    for kind in ("host", "node"):
        add_domain(shared, kind, "bytes", 4096 * 1024**2)
        add_domain(shared, kind, "millicores", 16000)
    grant(shared, storage)
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    owner.require_shared_admission()
    assert observe(owner, 16)
    retired = [False]
    with permit.operation(clock=lambda: 10):
        coordinator = authorize_shared_delivery(
            principal=context.actor,
            repository=repository,
            owner=owner,
            control=permit,
            retirement=lambda: retired[0],
            clock=lambda: 10,
        )
        owner.bind_shared_admission(coordinator)
    context.service = WorkAdmission(
        context.repo,
        backends={"test": context.backend},
        delivery_adapters={("agent_step", 1): _adapter()},
        resource_admission=owner,
        resource_clock=lambda: 10,
    )
    work = admit(
        context, "shared-agent-step", operation="agent_step", delivery=agent_step_envelope()
    )
    return repository, storage, owner, coordinator, retired, work


@pytest.mark.asyncio
async def test_shared_agent_step_startup_and_task_preserve_owned_sql(
    agent_step_context, off_loop_sql
):
    context = agent_step_context
    repository, storage, owner, coordinator, retired, work = await run_owned_blocking(
        setup, context
    )
    try:
        result = await context.service.dispatch_registered_next()
        assert result["id"] == work["id"]
        assert result["attempts"][-1]["state"] == "sent"
        assert len(context.backend.effects) == 2
        assert context.backend.effects[0][2] == "mock_cli startup"
        assert "implement the frozen task" in context.backend.effects[1][2]
        assert off_loop_sql
    finally:
        owner.stop_accepting()
        await run_owned_blocking(coordinator.drain, 20)
        retired[0] = True
    assert coordinator.pending_count == 0 and not coordinator.worker_alive
    assert owner.snapshot(now=10).inflight == 0
    assert await run_owned_blocking(repository.used, storage.spec.domain_id) == 1
    assert await run_owned_blocking(repository.audit_usage) == ()
