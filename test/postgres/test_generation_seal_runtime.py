"""Terminal seal uses the existing funded control lane before full shutdown."""

from test.postgres.test_admission_reservations import shared
from test.postgres.test_shared_admission_runtime import enroll
from test.services.test_delivery_admission import observe
from test.services.test_shared_admission_runtime import Monitor, principal
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.clients.storage_admission import _current_storage_owner
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.inbox_admission_runtime import InboxAdmissionRuntime
from cli_agent_orchestrator.services.inbox_service import InboxService
from cli_agent_orchestrator.services.work_blocking import run_owned_blocking

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_terminal_generation_seal_is_funded_and_never_releases_credit(
    shared, monkeypatch, fail
):
    method = getattr(InboxAdmissionRuntime, "seal_terminal_generation", None)
    assert callable(method), "missing explicit terminal runtime producer"
    storage, credit = await run_owned_blocking(enroll, shared)
    target, _, bound, _ = shared
    events = []

    class StrictStopMonitor(Monitor):
        def stop_accepting(self):
            self.owner.stop_accepting()  # Same ledger gate as the actual monitor.
            super().stop_accepting()

    monitor = StrictStopMonitor(events)
    bootstrap = ResourceBootstrap(
        repository=SimpleNamespace(resolve_target=lambda: target),
        binding_supplier=lambda selected: bound,
        instance_supplier=lambda: bound.instance_id,
        terminal_generation=True,
    )
    repository = bootstrap.shared_repository()
    value = InboxAdmissionRuntime(
        InboxService(),
        monitor=monitor,
        bootstrap=bootstrap,
        clock=lambda: 10,
        drain_timeout_s=10,
        shared_repository=repository,
    )
    query = repository._query
    original = repository.seal_current_generation

    def observe_query(connection, statement, parameters=()):
        if "{generation_seals}" in statement:
            assert monitor.owner.snapshot(now=10).control_connection_loans == 1
            events.append("funded SQL")
        return query(connection, statement, parameters)

    def seal(authority):
        assert _current_storage_owner() is monitor.owner._control_lease
        assert monitor.owner._accepting and not monitor.stopped
        assert not value.available and not value._shared.accepting
        assert authority.binding == value._shared.coverage.binding
        events.append("seal")
        if fail:
            raise OSError("controlled terminal seal failure")
        return original(authority)

    monkeypatch.setattr(repository, "_query", observe_query)
    monkeypatch.setattr(repository, "seal_current_generation", seal)
    await value.start()
    assert observe(monitor.owner, 8)
    try:
        await value.activate_shared(principal=principal())
        if fail:
            with pytest.raises(OSError, match="controlled terminal seal failure"):
                await value.seal_terminal_generation()
        else:
            receipt = await value.seal_terminal_generation()
            assert receipt.instance_id == bound.instance_id
            assert events.count("funded SQL") >= 2
        assert monitor.owner.snapshot(now=10).control_inflight == 0
    finally:
        await value.stop()
    assert value.retired and not value._shared.worker_alive
    assert monitor.owner.snapshot(now=10).inflight == 0
    assert events.count("seal") == 1
    assert await run_owned_blocking(repository.metadata_credits_current) == (credit,)
    assert await run_owned_blocking(repository.used, storage.spec.domain_id) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["unpublished", "uncertain_close"])
async def test_unproven_terminal_seal_never_opens_sql_or_retires_credit(
    shared, monkeypatch, condition
):
    from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
    from cli_agent_orchestrator.services import delivery_admission

    storage, credit = await run_owned_blocking(enroll, shared)
    target, _, bound, _ = shared
    monitor = Monitor([])
    bootstrap = ResourceBootstrap(
        repository=SimpleNamespace(resolve_target=lambda: target),
        binding_supplier=lambda selected: bound,
        instance_supplier=lambda: bound.instance_id,
        terminal_generation=True,
    )
    repository = bootstrap.shared_repository()
    value = InboxAdmissionRuntime(
        InboxService(),
        monitor=monitor,
        bootstrap=bootstrap,
        clock=lambda: 10,
        drain_timeout_s=10,
        shared_repository=repository,
    )

    def forbidden(*args):
        pytest.fail("unproven or unfunded terminal path reached seal SQL")

    monkeypatch.setattr(repository, "seal_current_generation", forbidden)
    await value.start()
    assert observe(monitor.owner, 8)
    try:
        if condition == "unpublished":
            assert await value.seal_terminal_generation() is None
            assert value._shared is None
        else:
            await value.activate_shared(principal=principal())
            with monkeypatch.context() as patch:
                patch.setattr(delivery_admission, "storage_close_uncertainty", lambda: True)
                with pytest.raises(StorageAdmissionUnavailable):
                    await value.seal_terminal_generation()
        assert monitor.owner.snapshot(now=10).control_inflight == 0
    finally:
        await value.stop()
    assert value.retired and monitor.owner.snapshot(now=10).inflight == 0
    assert await run_owned_blocking(repository.metadata_credits_current) == (credit,)
    assert await run_owned_blocking(repository.used, storage.spec.domain_id) == 1
