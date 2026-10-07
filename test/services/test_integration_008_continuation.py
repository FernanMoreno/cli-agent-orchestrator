"""Continuation fences are composed with actual scoped Work result proofs."""

from test.services.test_integration_008_scoped_results import (
    context,
    plan_context,
    scoped_result_context,
)
from test.services.test_integration_008_scoped_results import (
    test_composed_v2_receipt_result_and_projection_survive_service_restart as _accept_and_project,
)

import pytest


def driver_for(fixture):
    from cli_agent_orchestrator.services.workflow_continuation_driver import (
        WorkflowContinuationDriver,
    )

    driver = WorkflowContinuationDriver(fixture.plans, fixture.projector, instance_id="test-owner")
    driver.enable(fixture.subject, "scoped-result")
    return driver


def test_actual_accepted_projection_enqueues_exactly_once(scoped_result_context, monkeypatch):
    f = scoped_result_context
    driver_for(f)
    _accept_and_project(f, monkeypatch, "unchanged")
    with f.context.repo.read_snapshot() as conn:
        rows = conn.execute("SELECT * FROM workflow_continuation_outbox").fetchall()
        assert len(rows) == 1
        assert rows[0]["binding_id"] == f.binding.binding_id
        assert rows[0]["state"] == "ready"
    f.projector.project_pending_for_run("scoped-result")
    with f.context.repo.read_snapshot() as conn:
        assert conn.execute("SELECT count(*) FROM workflow_continuation_outbox").fetchone()[0] == 1


def test_lease_race_and_stopping_fence(scoped_result_context):
    f = scoped_result_context
    first = driver_for(f)
    from cli_agent_orchestrator.services.workflow_continuation_driver import (
        DriverRefused,
        WorkflowContinuationDriver,
    )

    other = WorkflowContinuationDriver(f.plans, f.projector, instance_id="other")
    claim = first.claim(f.subject, "scoped-result")
    with pytest.raises(DriverRefused, match="driver_owned"):
        other.claim(f.subject, "scoped-result")
    first.request_stop(f.subject, "scoped-result")
    with pytest.raises(DriverRefused, match="driver_stopping"):
        first.assert_current("scoped-result", claim)
    with pytest.raises(ValueError, match="driver_stopping"):
        f.plans.authorize_step(
            f.subject,
            "scoped-result",
            "next",
            "repo",
            dict(provider="mock_cli", agent="developer", prompt="No next effects"),
        )


def test_generation_compare_and_set_cannot_overwrite_newer_owner(scoped_result_context):
    from cli_agent_orchestrator.services.workflow_journal import get_run
    from cli_agent_orchestrator.services.workflow_service import update_run_generation

    f = scoped_result_context
    update_run_generation("scoped-result", "2", expected_generation="1")
    with pytest.raises(ValueError, match="generation_changed"):
        update_run_generation("scoped-result", "3", expected_generation="1")
    assert get_run("scoped-result").generation == "2"


def test_completed_handle_cleanup_keeps_result_and_new_generation(scoped_result_context):
    import asyncio

    f = scoped_result_context
    driver = driver_for(f)

    async def scenario():
        old = asyncio.create_task(asyncio.sleep(0, result={"output": "durable"}))
        await old
        driver.tasks["scoped-result"] = old
        await driver.tick()
        assert "scoped-result" not in driver.tasks
        assert old.result() == {"output": "durable"}
        replacement = asyncio.create_task(asyncio.sleep(0, result="new generation"))
        driver.tasks["scoped-result"] = replacement
        driver._cleanup_task("scoped-result", old)
        assert driver.tasks["scoped-result"] is replacement
        await replacement

    asyncio.run(scenario())


@pytest.mark.parametrize("surface", ["step", "run_step"])
def test_shim_options_preserve_managed_identity_and_custom_environment(monkeypatch, surface):
    import json
    from types import SimpleNamespace

    import cao_workflow

    for name, value in {
        "CAO_WORKFLOW_RUN_ID": "managed-run",
        "CAO_WORKFLOW_GENERATION": "7",
        "CAO_API_BASE_URL": "http://127.0.0.1:8000",
    }.items():
        monkeypatch.setenv(name, value)
    bodies = []

    def post(url, body, **kwargs):
        bodies.append(body)
        return SimpleNamespace(
            status=200,
            body=json.dumps(
                {
                    "terminal_id": "t",
                    "last_message": "result",
                    "status": "completed",
                    "replayed": False,
                }
            ),
        )

    monkeypatch.setattr(cao_workflow, "_post", post)
    extra = {"recovery": "manual"} if surface == "step" else {}
    getattr(cao_workflow, surface)(
        "mock_cli",
        "developer",
        "go",
        step_id="managed-step",
        env_vars={
            "CAO_WORKFLOW_RUN_ID": "forged",
            "CAO_WORKFLOW_GENERATION": "99",
            "CAO_WORKFLOW_STEP_ID": "forged",
            "CUSTOM": "kept",
        },
        target_key="repo",
        **extra,
    )
    assert bodies[0]["env_vars"] == {
        "CAO_WORKFLOW_RUN_ID": "managed-run",
        "CAO_WORKFLOW_GENERATION": "7",
        "CAO_WORKFLOW_STEP_ID": "managed-step",
        "CUSTOM": "kept",
    }
    assert bodies[0]["target_key"] == "repo"


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
def test_tracked_tasks_release_handles_for_every_outcome(scoped_result_context, outcome):
    import asyncio

    driver = driver_for(scoped_result_context)

    async def scenario():
        async def work():
            if outcome == "failed":
                raise ValueError("observation failure")
            if outcome == "cancelled":
                raise asyncio.CancelledError
            return "result"

        task = asyncio.create_task(work())
        driver._track_task("scoped-result", task)
        await asyncio.gather(task, return_exceptions=True)
        assert "scoped-result" not in driver.tasks
        if outcome == "completed":
            assert task.result() == "result"

    asyncio.run(scenario())
