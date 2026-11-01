"""One process credit survives lifespan reconstruction on the real ledger."""

from test.postgres.test_admission_metadata_credits import grant
from test.postgres.test_admission_reservations import domain, shared
from test.postgres.test_shared_admission_mapping import add_domain
from test.services.test_delivery_admission import observe
from test.services.test_shared_admission_runtime import Monitor, principal
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.inbox_admission_runtime import InboxAdmissionRuntime
from cli_agent_orchestrator.services.inbox_service import InboxService
from cli_agent_orchestrator.services.shared_delivery_admission import SharedDeliveryUnavailable
from cli_agent_orchestrator.services.work_blocking import run_owned_blocking

pytestmark = pytest.mark.postgres


def enroll(shared):
    storage = domain(shared, 3)
    for kind in ("host", "node"):
        add_domain(shared, kind, "bytes", 4096 * 1024**2)
        add_domain(shared, kind, "millicores", 16000)
    credit = grant(shared, storage)
    return storage, credit


def runtime(shared):
    target, _repository, bound, _module = shared
    bootstrap = ResourceBootstrap(
        repository=SimpleNamespace(resolve_target=lambda: target),
        binding_supplier=lambda selected: bound,
        instance_supplier=lambda: bound.instance_id,
    )
    repository = bootstrap.shared_repository()
    monitor = Monitor([])  # No telemetry connection: this test exercises lifecycle/ledger.
    value = InboxAdmissionRuntime(
        InboxService(),
        monitor=monitor,
        bootstrap=bootstrap,
        clock=lambda: 10,
        drain_timeout_s=10,
        shared_repository=repository,
    )
    return value, monitor, repository


@pytest.mark.asyncio
async def test_real_metadata_credit_survives_repeated_runtime_lifespans(shared):
    storage, credit = await run_owned_blocking(enroll, shared)
    original_repository = shared[1]
    for _ in range(2):
        value, monitor, repository = runtime(shared)
        await value.start()
        assert observe(monitor.owner, 8)
        try:
            assert not monitor.owner.shared_ready
            await value.activate_shared(principal=principal())
            assert monitor.owner.shared_ready
            assert value._shared.coverage.credits == (credit,)
            await value.activate_shared(principal=principal())  # Same owner; no new grant.
        finally:
            await value.stop()
        assert value.retired and not value._shared.worker_alive
        assert monitor.owner.snapshot(now=10).inflight == 0
        assert await run_owned_blocking(repository.used, storage.spec.domain_id) == 1
    assert await run_owned_blocking(original_repository.audit_usage) == ()


@pytest.mark.asyncio
async def test_live_runtime_rejects_second_owner_before_metadata_sql(shared, monkeypatch):
    storage, _credit = await run_owned_blocking(enroll, shared)
    first, first_monitor, _first_repository = runtime(shared)
    second, second_monitor, second_repository = runtime(shared)
    await first.start()
    await second.start()
    assert observe(first_monitor.owner, 8)
    assert observe(second_monitor.owner, 8)
    await first.activate_shared(principal=principal())
    original = second_repository.metadata_coverage_current

    def forbidden():
        pytest.fail("a live rival consumed another metadata session on the same credit")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(second_repository, "metadata_coverage_current", forbidden)
            with pytest.raises(SharedDeliveryUnavailable):
                await second.activate_shared(principal=principal())
        assert second_repository.metadata_coverage_current == original
        assert not second_monitor.owner.shared_ready
        assert second_monitor.owner.snapshot(now=10).available_slots == 0
        assert second._shared is None
    finally:
        await second.stop()
        await first.stop()
    assert first.retired and second.retired
    assert await run_owned_blocking(shared[1].used, storage.spec.domain_id) == 1
    assert await run_owned_blocking(shared[1].audit_usage) == ()
