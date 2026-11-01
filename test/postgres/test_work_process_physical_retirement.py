"""Native shared charges outlive uncertain external cleanup; no model/process run."""

import threading
from test.postgres.test_admission_admin_abort import pair
from test.postgres.test_admission_admin_abort_runtime import data_owner
from test.postgres.test_admission_reservations import shared  # noqa: F401 - pair dependency
from test.services.test_work_process_resource_retirement import resource_port

import pytest

from cli_agent_orchestrator.backends.docker_backend import DockerWorkExecutionUncertain
from cli_agent_orchestrator.services.work_bubblewrap_composition import (
    WorkBubblewrapCleanupUncertain,
)
from cli_agent_orchestrator.services.work_process_effect import (
    begin_native_effect,
    retain_native_effect_owner,
)

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("backend", ["bubblewrap", "docker"])
def test_uncertain_native_owner_keeps_shared_charge_until_its_exact_witness(pair, backend):
    owner, coordinator, retired = data_owner(pair)
    native_closed = threading.Event()
    native_owner = object()
    error = (
        WorkBubblewrapCleanupUncertain("controlled unknown tree", cleanup_confirmed=False)
        if backend == "bubblewrap"
        else DockerWorkExecutionUncertain("controlled unknown container")
    )

    def execute(restriction, **options):
        options["before_effect"]()
        begin_native_effect()
        retain_native_effect_owner(native_owner, native_closed.is_set)
        raise error

    owner, lease, _, port = resource_port(execute, False, owner=owner)
    before = pair.custodian.used(pair.storage.spec.domain_id)
    assert before == 2
    try:
        try:
            with pytest.raises(type(error)):
                port.execute_process("/cao-agent-step-worker", b"input")
        finally:
            port._attempt_credential_fd = None
            port.close()
            port._finish_resource()
        hold = lease._retirement_hold
        assert hold is not None and not hold.physical_ready
        assert owner.snapshot(now=10.0).inflight == 1
        assert pair.custodian.used(pair.storage.spec.domain_id) == before + 2
        assert len(lease._physical_effects) == 1
        token = next(iter(lease._physical_effects))
        assert not token.finish()
        assert pair.custodian.used(pair.storage.spec.domain_id) == before + 2
        native_closed.set()
        assert token.finish()
        owner.stop_accepting()
        coordinator.drain(20)
        assert owner.snapshot(now=10.0).inflight == 0
        assert pair.custodian.used(pair.storage.spec.domain_id) == before
        assert pair.custodian.audit_usage() == ()
        assert not token.finish()
    finally:
        native_closed.set()
        for token in tuple(lease._physical_effects):
            token.finish()
        owner.stop_accepting()
        coordinator.drain(20)
        retired[0] = True
