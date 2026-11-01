"""A joined Python callback is not proof that its Work process tree closed."""

from test.services.test_delivery_admission import create, observe
from test.services.test_work_agent_step_process_adapter import _process_contract
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.backends.docker_backend import (
    DockerWorkExecution,
    DockerWorkExecutionUncertain,
)
from cli_agent_orchestrator.services.work_admission import _WorkEffectPort
from cli_agent_orchestrator.services.work_bubblewrap_composition import (
    WorkBubblewrapCleanupUncertain,
    WorkBubblewrapSetupFailed,
)
from cli_agent_orchestrator.services.work_process_effect import confirm_native_effect_closed

pytestmark = pytest.mark.offline_database


def resource_port(execute, held, *, owner=None):
    if owner is None:
        owner = create()
        assert observe(owner, 2)
    lease = owner.try_reserve("process-receiver", now=10.0, storage_slots=2).reservation
    assert lease is not None and lease.start()
    hold = lease.hold_retirement() if held else None
    port = _WorkEffectPort(
        SimpleNamespace(execute_bound_process=execute),
        object(),
        lambda *args: None,
        lambda: None,
        None,
        binding=SimpleNamespace(contract=_process_contract()),
        guard_connection=lambda *args: None,
        attempt_revision=1,
        attempt_credential_fd=37,  # Structural seam only; no descriptor is read here.
        resource_reservation=lease,
        resource_clock=lambda: 10.0,
    )
    return owner, lease, hold, port


@pytest.mark.parametrize("held", [False, True])
@pytest.mark.parametrize("backend", ["bubblewrap", "docker"])
def test_uncertain_native_cleanup_cannot_retire_its_local_or_shared_footprint(held, backend):
    entered = []
    error = (
        WorkBubblewrapCleanupUncertain("controlled unknown tree", cleanup_confirmed=False)
        if backend == "bubblewrap"
        else DockerWorkExecutionUncertain("controlled unknown container")
    )

    def execute(restriction, **options):
        options["before_effect"]()
        entered.append(True)
        raise error

    owner, lease, hold, port = resource_port(execute, held)
    try:
        with pytest.raises(type(error)):
            port.execute_process("/cao-agent-step-worker", b"input")
    finally:
        # No real descriptor is owned by this composition seam.
        port._attempt_credential_fd = None
        port.close()
        port._finish_resource()
    assert entered == [True]
    assert owner.snapshot(now=10.0).inflight == 1
    assert owner.try_reserve("process-receiver", now=10.0).reservation is None
    if hold is not None:
        assert not hold.physical_ready
        assert not hold.finish()


@pytest.mark.parametrize("held", [False, True])
@pytest.mark.parametrize("outcome", ["exit", "confirmed_setup_failure"])
def test_positive_native_closure_or_proven_setup_cleanup_can_retire(held, outcome):
    def execute(restriction, **options):
        options["before_effect"]()
        # Native backend seam emits its positive closure after verified cleanup.
        confirm_native_effect_closed()
        if outcome == "confirmed_setup_failure":
            raise WorkBubblewrapSetupFailed("controlled safe failure", cleanup_confirmed=True)
        return DockerWorkExecution(0, b"", b"", True, True, True)

    owner, lease, hold, port = resource_port(execute, held)
    try:
        if outcome == "confirmed_setup_failure":
            with pytest.raises(WorkBubblewrapSetupFailed):
                port.execute_process("/cao-agent-step-worker", b"input")
        else:
            assert port.execute_process("/cao-agent-step-worker", b"input").process_stopped
    finally:
        port._attempt_credential_fd = None
        port.close()
        port._finish_resource()
    if hold is not None:
        assert hold.physical_ready and hold.finish()
    assert owner.snapshot(now=10.0).inflight == 0
