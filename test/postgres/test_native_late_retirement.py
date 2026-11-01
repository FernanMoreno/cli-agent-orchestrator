"""An existing coordinator reobserves late exact helpers and retires PG charges."""

import threading
from test.postgres.test_admission_admin_abort import pair
from test.postgres.test_admission_admin_abort_runtime import data_owner
from test.postgres.test_admission_reservations import shared  # noqa: F401 - pair dependency
from test.services.test_work_process_resource_retirement import resource_port

import pytest

from cli_agent_orchestrator.backends.docker_backend import (
    DockerWorkExecution,
    DockerWorkExecutionUncertain,
)
from cli_agent_orchestrator.services.work_process_effect import (
    begin_native_effect,
    confirm_native_effect_closed,
    observe_thread_close,
    retain_native_effect_owner,
)

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("business_uncertain", [False, True])
def test_late_helper_retires_local_and_pg_quantities_automatically_without_replay(
    pair, business_uncertain
):
    owner, coordinator, retired = data_owner(pair)
    entered, release = threading.Event(), threading.Event()
    effects = []

    def run_helper():
        entered.set()
        assert release.wait(30)

    helper = threading.Thread(target=run_helper)
    helper.start()
    assert entered.wait(3)

    def execute(restriction, **options):
        options["before_effect"]()
        begin_native_effect()
        effects.append("one native effect")
        retain_native_effect_owner(helper, observe_thread_close(helper))
        confirm_native_effect_closed()  # Controlled backend proves its full native cleanup.
        if business_uncertain:
            raise DockerWorkExecutionUncertain("controlled business recording uncertainty")
        return DockerWorkExecution(0, b"", b"", True, True, True)

    _, lease, _, port = resource_port(execute, False, owner=owner)
    try:
        try:
            if business_uncertain:
                with pytest.raises(DockerWorkExecutionUncertain):
                    port.execute_process("/cao-agent-step-worker", b"input")
            else:
                assert port.execute_process("/cao-agent-step-worker", b"input").process_stopped
        finally:
            port._attempt_credential_fd = None
            port.close()
            port._finish_resource()
        hold = lease._retirement_hold
        assert hold is not None and not hold.physical_ready
        assert len(owner._physical_closes) == 1
        assert pair.custodian.used(pair.storage.spec.domain_id) == 4
        with coordinator._guard:
            operation_id = next(iter(coordinator._attempts.values())).operation_id
        release.set()
        helper.join(3)
        assert not helper.is_alive()
        owner.stop_accepting()
        coordinator.drain(20)  # Production retry loop; no token.finish call in acceptance path.
        assert owner.snapshot(now=10).inflight == 0
        assert not owner._physical_closes
        assert pair.custodian.used(pair.storage.spec.domain_id) == 2
        row = pair.owner.read_operation(operation_id)
        assert row.state == "released" and row.revision == 4
        assert pair.custodian.audit_usage() == ()
        assert effects == ["one native effect"]
        assert pair.owner.metadata_credits_current() == (pair.foreign_credit,)
    finally:
        release.set()
        helper.join(3)
        owner.retry_physical_closes()
        owner.stop_accepting()
        coordinator.drain(20)
        retired[0] = True
