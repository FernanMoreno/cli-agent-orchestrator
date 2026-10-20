"""The workflow effect fence remains usable from reads and serialized on writes."""

import threading
import time
from test.services.test_integration_008_coordinator import (
    coordinator_context,
    start_controller,
)
from test.services.test_integration_008_prepared_plans import context, plan_context  # noqa: F401
from test.services.test_workflow_step_projection_journal import (
    _project,
    projection_state,
)

import pytest

from cli_agent_orchestrator.services.workflow_continuation_driver import (
    DriverRefused,
    check_effect,
)


def _active_controller(f):
    start_controller(f)
    f.driver.claim(f.subject, "controller-run")
    with f.context.repo.read_snapshot() as connection:
        row = connection.execute(
            "SELECT run_generation FROM workflow_driver WHERE run_id=%s",
            ("controller-run",),
        ).fetchone()
    assert row is not None
    return row["run_generation"]


def _drop_continuation_trigger(f, postgres_targets):
    from cli_agent_orchestrator.clients.postgres import connect

    assert f.context.repo.target.identity == postgres_targets.main.identity
    with connect(postgres_targets.main.migration_target()) as owner:
        with owner.transaction():
            owner.execute(
                "DROP TRIGGER coordinator_events_no_update ON workflow_coordinator_events"
            )


def _controller_driver_state(f):
    with f.context.repo.read_snapshot() as connection:
        row = connection.execute(
            "SELECT state,epoch,revision,lease_expires_at FROM workflow_driver " "WHERE run_id=%s",
            ("controller-run",),
        ).fetchone()
    assert row is not None
    return tuple(row)


def test_check_effect_validates_current_and_stale_generation_in_read_snapshot(
    coordinator_context,
):
    f = coordinator_context
    generation = _active_controller(f)

    with f.context.repo.read_snapshot() as connection:
        assert connection.read_only is True
        check_effect(connection, "controller-run", generation)
        with pytest.raises(DriverRefused, match="driver_generation_changed"):
            check_effect(connection, "controller-run", "stale-generation")


def test_check_effect_keeps_driver_row_lock_in_writable_transaction(coordinator_context):
    f = coordinator_context
    generation = _active_controller(f)
    repository = f.context.repo
    started = threading.Event()
    backend_pid = []
    errors = []

    def check_from_second_connection():
        try:
            with repository.transaction() as connection:
                backend_pid.append(connection.execute("SELECT pg_backend_pid()").fetchone()[0])
                started.set()
                check_effect(connection, "controller-run", generation)
        except BaseException as error:  # propagate worker-thread failures to pytest
            errors.append(error)

    worker = threading.Thread(target=check_from_second_connection, daemon=True)
    try:
        with repository.connection() as holder:
            with holder.transaction():
                holder.execute(
                    "SELECT run_id FROM workflow_driver WHERE run_id=%s FOR UPDATE",
                    ("controller-run",),
                ).fetchone()
                worker.start()
                assert started.wait(5), "second PostgreSQL connection did not start"
                deadline = time.monotonic() + 5
                blocked = False
                while time.monotonic() < deadline:
                    row = holder.execute(
                        "SELECT pg_blocking_pids(%s)", (backend_pid[0],)
                    ).fetchone()
                    if row is not None and row[0]:
                        blocked = True
                        break
                    time.sleep(0.01)
                assert blocked, "writable check_effect did not wait for the held driver row lock"
    finally:
        if worker.ident is not None:
            worker.join(5)

    assert not worker.is_alive(), "writable check_effect did not finish after lock release"
    assert errors == []


def test_check_effect_reverifies_component_in_read_only_snapshot(
    coordinator_context, postgres_targets
):
    from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch

    f = coordinator_context
    generation = _active_controller(f)
    _drop_continuation_trigger(f, postgres_targets)

    with f.context.repo.read_snapshot() as connection:
        assert connection.read_only is True
        with pytest.raises(SchemaMismatch, match="component schema fingerprint mismatch"):
            check_effect(connection, "controller-run", generation)


def test_assert_current_reverifies_component_before_return(coordinator_context, postgres_targets):
    from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch

    f = coordinator_context
    _active_controller(f)
    before = _controller_driver_state(f)
    _drop_continuation_trigger(f, postgres_targets)

    with pytest.raises(SchemaMismatch, match="component schema fingerprint mismatch"):
        f.driver.assert_current("controller-run", before[1])
    assert _controller_driver_state(f) == before


def test_shutdown_reverifies_component_before_pausing_owned_driver(
    coordinator_context, postgres_targets
):
    import asyncio

    from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch

    f = coordinator_context
    _active_controller(f)
    before = _controller_driver_state(f)
    _drop_continuation_trigger(f, postgres_targets)

    with pytest.raises(SchemaMismatch, match="component schema fingerprint mismatch"):
        asyncio.run(f.driver.shutdown())
    assert _controller_driver_state(f) == before


def test_driver_constructor_verifies_component_without_recreating_it(
    coordinator_context, postgres_targets
):
    from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch
    from cli_agent_orchestrator.services.workflow_continuation_driver import (
        WorkflowContinuationDriver,
    )

    f = coordinator_context
    _drop_continuation_trigger(f, postgres_targets)

    with pytest.raises(SchemaMismatch, match="component schema fingerprint mismatch"):
        WorkflowContinuationDriver(f.driver.plans, f.projector)


def test_coordinator_start_reverifies_component_before_driver_enable(
    coordinator_context, postgres_targets
):
    from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch
    from cli_agent_orchestrator.services import approval_store

    f = coordinator_context
    prepared = f.service.prepare(
        f.subject,
        "wf",
        {"description": "Return answer 42"},
        [{"kind": "output_equals", "path": ["answer"], "value": 42}],
        {"repo": str(f.context.root)},
        {"repo": {"developer": "controller-seed"}},
        scan_dir=str(f.directory),
        max_iterations=3,
    )
    approval_store.grant(prepared["plan_id"], "fixture-admin")
    _drop_continuation_trigger(f, postgres_targets)

    with pytest.raises(SchemaMismatch, match="component schema fingerprint mismatch"):
        f.service.start(
            f.subject,
            prepared["prepared_id"],
            prepared["plan_id"],
            "drifted-enable",
            scan_dir=str(f.directory),
        )

    with f.context.repo.read_snapshot() as connection:
        for table in ("workflow_run", "workflow_driver", "workflow_coordinator"):
            assert (
                connection.execute(
                    "SELECT 1 FROM " + table + " WHERE run_id=%s", ("drifted-enable",)
                ).fetchone()
                is None
            )


@pytest.mark.parametrize(
    "operation",
    ("record_process_exit", "release", "request_stop", "pause", "write_without_run_fence"),
)
def test_driver_writer_boundaries_reverify_component_before_mutation(
    coordinator_context, postgres_targets, operation
):
    from types import SimpleNamespace

    from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch

    f = coordinator_context
    _active_controller(f)
    before = _controller_driver_state(f)
    _drop_continuation_trigger(f, postgres_targets)

    with pytest.raises(SchemaMismatch, match="component schema fingerprint mismatch"):
        if operation == "record_process_exit":
            f.driver.record_process_exit("controller-run", before[1], SimpleNamespace(returncode=0))
        elif operation == "release":
            f.driver.release("controller-run", before[1])
        elif operation == "request_stop":
            f.driver.request_stop(f.subject, "controller-run")
        elif operation == "pause":
            f.driver.pause("controller-run", "test", epoch=before[1])
        else:
            f.driver._write(
                "UPDATE workflow_driver SET revision=revision+1 WHERE run_id=%s",
                ("controller-run",),
            )

    assert _controller_driver_state(f) == before


@pytest.mark.parametrize("reader", ("owner", "read_rows"))
def test_driver_read_boundaries_reverify_component_after_startup(
    coordinator_context, postgres_targets, reader
):
    from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch

    f = coordinator_context
    _active_controller(f)
    _drop_continuation_trigger(f, postgres_targets)

    with pytest.raises(SchemaMismatch, match="component schema fingerprint mismatch"):
        if reader == "owner":
            f.driver.owner("controller-run")
        else:
            f.driver._read_rows(
                "SELECT run_id FROM workflow_driver WHERE run_id=%s", ("controller-run",)
            )


@pytest.mark.parametrize("prior_projection", (False, True), ids=("new", "replay"))
def test_projection_enqueue_reverifies_continuation_schema_before_commit(
    projection_state, postgres_targets, prior_projection
):
    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch

    repository, binding, accepted = projection_state
    assert repository.target.identity == postgres_targets.main.identity
    if prior_projection:
        assert _project(repository, binding, accepted) == "projected"

    with repository.read_snapshot() as connection:
        step_before = tuple(
            connection.execute(
                "SELECT state,attempts,output_json,result_json FROM workflow_run_step "
                "WHERE run_id=%s AND step_id=%s",
                (binding.run_id, binding.step_id),
            ).fetchone()
        )
        marker_before = connection.execute(
            "SELECT accepted_result_id,content_hash FROM work_workflow_step_projections "
            "WHERE binding_id=%s",
            (binding.binding_id,),
        ).fetchone()
        outbox_before = connection.execute(
            "SELECT count(*) FROM workflow_continuation_outbox WHERE binding_id=%s",
            (binding.binding_id,),
        ).fetchone()[0]

    with connect(postgres_targets.main.migration_target()) as owner:
        with owner.transaction():
            owner.execute(
                "DROP TRIGGER coordinator_events_no_update ON workflow_coordinator_events"
            )

    with pytest.raises(SchemaMismatch, match="component schema fingerprint mismatch"):
        _project(repository, binding, accepted)

    with repository.read_snapshot() as connection:
        step_after = tuple(
            connection.execute(
                "SELECT state,attempts,output_json,result_json FROM workflow_run_step "
                "WHERE run_id=%s AND step_id=%s",
                (binding.run_id, binding.step_id),
            ).fetchone()
        )
        marker_after = connection.execute(
            "SELECT accepted_result_id,content_hash FROM work_workflow_step_projections "
            "WHERE binding_id=%s",
            (binding.binding_id,),
        ).fetchone()
        outbox_after = connection.execute(
            "SELECT count(*) FROM workflow_continuation_outbox WHERE binding_id=%s",
            (binding.binding_id,),
        ).fetchone()[0]

    assert step_after == step_before
    assert marker_after == marker_before
    assert outbox_after == outbox_before
