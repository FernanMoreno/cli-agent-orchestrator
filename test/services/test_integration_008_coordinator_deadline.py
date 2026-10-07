import time
from test.services.test_integration_008_coordinator import (
    context,
    coordinator_context,
    plan_context,
    start_controller,
)


def test_actual_slow_atomic_attachment_has_valid_immutable_coordinator_deadline(
    coordinator_context, monkeypatch
):
    f = coordinator_context
    original = f.driver.enable

    def delayed(*args, **kwargs):
        original(*args, **kwargs)
        time.sleep(1.2)

    monkeypatch.setattr(f.driver, "enable", delayed)
    started = start_controller(f)
    value = f.service.status(f.subject, started["coordinator_id"])
    assert value["state"] == "ready"

    from datetime import datetime

    import pytest

    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.workflow_continuation_driver import (
        DriverRefused,
        WorkflowContinuationDriver,
    )

    with f.context.repo.read_snapshot() as connection:
        journal_time = connection.execute(
            "SELECT started_at FROM workflow_run WHERE run_id='controller-run'"
        ).fetchone()[0]
        deadline = connection.execute("SELECT deadline FROM workflow_coordinator").fetchone()[0]
    assert (
        deadline == datetime.fromisoformat(journal_time.replace("Z", "+00:00")).timestamp() + 3600
    )
    foreign = auth._verified_principal(
        f.subject.issuer, "different-owner", [auth.SCOPE_WRITE], f.subject.kind
    )
    with pytest.raises(PermissionError, match="owner_mismatch"):
        f.service.status(foreign, started["coordinator_id"])
    f.driver.claim(f.subject, "controller-run")
    fresh = WorkflowContinuationDriver(
        f.service.plans, f.projector, instance_id="new-competing-reader"
    )
    with pytest.raises(DriverRefused, match="driver_owned"):
        fresh.claim(f.subject, "controller-run")
    with f.context.repo.transaction() as connection:
        connection.execute("UPDATE workflow_coordinator SET deadline=deadline+2")
    with pytest.raises(ValueError, match="coordinator_private_deadline_integrity"):
        f.service.status(f.subject, started["coordinator_id"])
