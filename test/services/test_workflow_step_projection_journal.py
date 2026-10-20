from __future__ import annotations

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from test.integration.test_work_dispatch import context  # noqa: F401
from threading import Barrier
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.clients.postgres import DatabaseTarget, IntegrityError
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_origin import WorkflowStepResultV1
from cli_agent_orchestrator.models.workflow_managed import WorkflowStepRetryAuthorization
from cli_agent_orchestrator.services import workflow_journal


@pytest.fixture
def projection_state(context, tmp_path):
    """Use an admitted, received Work attempt and its durable result artifact."""
    from test.services.test_work_workflow import _provisioned_workflow

    from cli_agent_orchestrator.services.step_output_store import ImmutableResultStore
    from cli_agent_orchestrator.services.work_origin import WorkOrigins
    from cli_agent_orchestrator.services.work_service import WorkService

    workflow_origins, _authority, subject, _authorization, selector, _provision = (
        _provisioned_workflow(context)
    )
    admitter = workflow_origins.resolve_step_admitter(
        subject, selector["workflow_id"], selector["spec_hash"], selector["step_id"]
    )
    assert admitter is not None
    work = asyncio.run(
        admitter(**{key: value for key, value in selector.items() if key != "principal"})
    )
    binding = workflow_origins.read_step_binding(
        selector["tier"],
        selector["run_id"],
        selector["run_generation"],
        selector["step_id"],
        selector["step_attempt"],
    )
    assert binding is not None and binding.work_item_id == work["id"]

    prepared = context.service._prepare_dispatch(registered_only=False)
    assert prepared is not None
    _dispatch_binding, port, sent = prepared
    try:
        attempt_credential = os.pread(port._attempt_credential_fd, 32, 0)
        receiver_credential = os.pread(port._receiver_credential_fd, 32, 0)
    finally:
        port.close()
    assert sent["attempts"][-1]["id"] == binding.work_attempt_id

    origins = WorkOrigins(context.repo)
    received = origins.accept_task_received_with_credentials(
        attempt_credential=attempt_credential,
        receiver_credential=receiver_credential,
    )
    assert received["attempts"][-1]["state"] == "acknowledged"

    result = WorkflowStepResultV1.from_payload(
        {"schema_version": 1, "status": "completed", "output": {"answer": 42}}
    )
    service = WorkService(
        context.repo,
        origins=origins,
        artifacts=ImmutableResultStore(tmp_path / "result-content"),
    )
    service.submit_workflow_step_result(
        attempt_credential=attempt_credential,
        receiver_credential=receiver_credential,
        result=result,
    )
    accepted = service.read_accepted_workflow_result(binding)
    assert accepted is not None
    assert service.read_accepted_workflow_result(binding) == accepted
    return context.repo, binding, accepted


def _project(repository, binding, accepted):
    return workflow_journal.project_work_result(
        repository=repository,
        run_id=binding.run_id,
        run_generation=binding.run_generation,
        tier=binding.tier,
        step_id=binding.step_id,
        step_attempt=binding.workflow_step_attempt,
        binding=binding,
        accepted_result=accepted,
        updated_at="2026-09-29T10:05:00Z",
    )


def _stored(repository, sql, parameters=()):
    with repository.connection() as connection:
        return connection.execute(sql, parameters).fetchone()


def test_projection_completes_step_and_records_marker_atomically(projection_state):
    repository, binding, accepted = projection_state

    assert _project(repository, binding, accepted) == "projected"

    step = _stored(
        repository,
        "SELECT state,attempts,output_json,result_json,error,error_kind "
        "FROM workflow_run_step WHERE run_id=%s AND step_id=%s",
        (binding.run_id, binding.step_id),
    )
    marker = _stored(
        repository,
        "SELECT binding_id,schema_version,accepted_result_id,content_hash "
        "FROM work_workflow_step_projections WHERE binding_id=%s",
        (binding.binding_id,),
    )
    assert tuple(step) == (
        "completed",
        binding.workflow_step_attempt,
        '{"answer":42}',
        accepted.canonical_bytes.decode("utf-8"),
        None,
        None,
    )
    assert tuple(marker) == (
        binding.binding_id,
        1,
        accepted.accepted_result_id,
        accepted.content_hash,
    )


def test_projection_duplicate_is_idempotent_after_run_and_work_advance(projection_state):
    repository, binding, accepted = projection_state
    assert _project(repository, binding, accepted) == "projected"
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE workflow_run SET generation=%s,current_step_id='next' WHERE run_id=%s",
            (str(binding.run_generation + 1), binding.run_id),
        )
        connection.execute(
            "UPDATE work_items SET state='queued',revision=revision+1,accepted_result_id=NULL "
            "WHERE id=%s",
            (binding.work_item_id,),
        )
        connection.execute(
            "INSERT INTO work_attempts "
            "(id,work_item_id,attempt_number,generation,provider,state,revision,"
            "lease_expires_at,cleanup_state,created_at) "
            "VALUES (%s,%s,2,%s,'mock_cli','planned',1,%s,'not_requested',%s)",
            (
                f"{binding.work_attempt_id}-next",
                binding.work_item_id,
                binding.work_generation + 1,
                time.time() + 300,
                time.time(),
            ),
        )

    assert _project(repository, binding, accepted) == "already_projected"


def test_work_projection_lookup_returns_original_identity_after_run_advances(projection_state):
    repository, binding, accepted = projection_state
    assert _project(repository, binding, accepted) == "projected"
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE workflow_run SET generation=%s WHERE run_id=%s",
            (str(binding.run_generation + 1), binding.run_id),
        )

    projection = workflow_journal.get_work_step_projection(binding.run_id, binding.step_id)

    assert projection is not None
    assert projection.binding_id == binding.binding_id
    assert projection.tier == binding.tier
    assert projection.run_generation == binding.run_generation
    assert projection.step_attempt == binding.workflow_step_attempt
    assert projection.accepted_result_id == accepted.accepted_result_id
    assert projection.content_hash == accepted.content_hash
    assert projection.result_json == accepted.canonical_bytes.decode("utf-8")


def test_work_projection_lookup_rejects_envelope_hash_mismatch(projection_state, postgres_service):
    repository, binding, accepted = projection_state
    assert _project(repository, binding, accepted) == "projected"
    from psycopg import sql

    # Simulate database-owner tampering after bypassing ordinary triggers.
    # Runtime roles cannot mutate this append-only marker, but reads still
    # verify its content if a privileged operator changes it.
    with postgres_service.connect_admin(repository.target.database) as connection:
        with connection.transaction():
            connection.execute("SET LOCAL session_replication_role = 'replica'")
            connection.execute(
                sql.SQL("UPDATE {}.{} SET content_hash=%s WHERE binding_id=%s").format(
                    sql.Identifier(repository.target.schema),
                    sql.Identifier("work_workflow_step_projections"),
                ),
                ("0" * 64, binding.binding_id),
            )

    with pytest.raises(
        workflow_journal.WorkflowProjectionConflict,
        match="differs from its Work marker",
    ):
        workflow_journal.get_work_step_projection(binding.run_id, binding.step_id)


def test_projection_rejects_conflicting_result_without_overwriting_marker(projection_state):
    from dataclasses import replace

    repository, binding, accepted = projection_state
    assert _project(repository, binding, accepted) == "projected"
    conflicting = replace(accepted, accepted_result_id=f"{accepted.accepted_result_id}-conflict")

    with pytest.raises(workflow_journal.WorkflowProjectionConflict, match="different"):
        _project(repository, binding, conflicting)

    marker = _stored(
        repository,
        "SELECT accepted_result_id,content_hash FROM work_workflow_step_projections "
        "WHERE binding_id=%s",
        (binding.binding_id,),
    )
    assert tuple(marker) == (accepted.accepted_result_id, accepted.content_hash)


def test_projection_cas_loss_leaves_pending_state_and_no_marker(projection_state):
    repository, binding, accepted = projection_state
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE workflow_run_step SET state='running' WHERE run_id=%s AND step_id=%s",
            (binding.run_id, binding.step_id),
        )

    with pytest.raises(workflow_journal.WorkflowProjectionConflict, match="compare-and-set"):
        _project(repository, binding, accepted)

    assert (
        _stored(
            repository,
            "SELECT state FROM workflow_run_step WHERE run_id=%s AND step_id=%s",
            (binding.run_id, binding.step_id),
        )[0]
        == "running"
    )
    assert _stored(repository, "SELECT count(*) FROM work_workflow_step_projections")[0] == 0


def test_projection_rejects_work_that_is_no_longer_the_current_successful_attempt(
    projection_state,
):
    repository, binding, accepted = projection_state
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE work_items SET state='failed',revision=4,accepted_result_id=NULL "
            "WHERE id=%s",
            (binding.work_item_id,),
        )
        connection.execute(
            "UPDATE work_attempts SET state='failed',revision=5 WHERE id=%s",
            (binding.work_attempt_id,),
        )

    with pytest.raises(
        workflow_journal.WorkflowProjectionConflict,
        match="no longer the current accepted attempt",
    ):
        _project(repository, binding, accepted)

    assert (
        _stored(
            repository,
            "SELECT state FROM workflow_run_step WHERE run_id=%s AND step_id=%s",
            (binding.run_id, binding.step_id),
        )[0]
        == "work_pending"
    )
    assert _stored(repository, "SELECT count(*) FROM work_workflow_step_projections")[0] == 0


def test_projection_rejects_stale_run_generation(projection_state):
    repository, binding, accepted = projection_state
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE workflow_run SET generation=%s WHERE run_id=%s",
            (str(binding.run_generation + 1), binding.run_id),
        )

    with pytest.raises(workflow_journal.WorkflowProjectionConflict, match="compare-and-set"):
        _project(repository, binding, accepted)

    assert (
        _stored(
            repository,
            "SELECT state FROM workflow_run_step WHERE run_id=%s AND step_id=%s",
            (binding.run_id, binding.step_id),
        )[0]
        == "work_pending"
    )
    assert _stored(repository, "SELECT count(*) FROM work_workflow_step_projections")[0] == 0


def test_projection_marker_insert_failure_rolls_back_step_completion(projection_state, monkeypatch):
    repository, binding, accepted = projection_state
    original_transaction = repository.transaction
    injected = {"marker_insert": False}

    @contextmanager
    def fail_marker_insert():
        with original_transaction() as connection:
            original_execute = connection.execute

            def execute(statement, parameters=(), *, prepare: bool | None = None):
                if isinstance(statement, str) and statement.startswith(
                    "INSERT INTO work_workflow_step_projections"
                ):
                    injected["marker_insert"] = True
                    parameters = (*parameters[:2], "invalid-content-hash", *parameters[3:])
                if prepare is None:
                    return original_execute(statement, parameters)
                return original_execute(statement, parameters, prepare=prepare)

            connection.execute = execute
            yield connection

    # Inject a real PostgreSQL check-constraint failure without adding
    # unregistered schema objects or weakening repository verification.
    monkeypatch.setattr(repository, "transaction", fail_marker_insert)

    with pytest.raises(IntegrityError):
        _project(repository, binding, accepted)
    assert injected["marker_insert"] is True

    assert tuple(
        _stored(
            repository,
            "SELECT state,output_json,result_json FROM workflow_run_step "
            "WHERE run_id=%s AND step_id=%s",
            (binding.run_id, binding.step_id),
        )
    ) == ("work_pending", None, None)
    assert _stored(repository, "SELECT count(*) FROM work_workflow_step_projections")[0] == 0


def test_work_failure_projection_records_only_exact_current_durable_failure(projection_state):
    repository, binding, _accepted = projection_state
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE work_items SET state='failed',revision=4,accepted_result_id=NULL "
            "WHERE id=%s",
            (binding.work_item_id,),
        )
        connection.execute(
            "UPDATE work_attempts SET state='failed',revision=5 WHERE id=%s",
            (binding.work_attempt_id,),
        )
    work_state = SimpleNamespace(
        binding_id=binding.binding_id,
        work_item_id=binding.work_item_id,
        work_attempt_id=binding.work_attempt_id,
        work_generation=binding.work_generation,
        work_state="failed",
        work_revision=4,
        attempt_state="failed",
        attempt_revision=5,
        current_attempt_id=binding.work_attempt_id,
        current_generation=binding.work_generation,
        current_attempt_state="failed",
        accepted_result_id=None,
        cleanup_state="not_requested",
    )

    status = workflow_journal.project_work_failure(
        repository=repository,
        run_id=binding.run_id,
        run_generation=binding.run_generation,
        tier=binding.tier,
        step_id=binding.step_id,
        step_attempt=binding.workflow_step_attempt,
        binding=binding,
        work_state=work_state,
        updated_at="2026-09-29T10:05:00Z",
    )

    assert status == "failed"
    assert tuple(
        _stored(
            repository,
            "SELECT state,attempts,output_json,result_json,error,error_kind "
            "FROM workflow_run_step WHERE run_id=%s AND step_id=%s",
            (binding.run_id, binding.step_id),
        )
    ) == (
        "failed",
        binding.workflow_step_attempt,
        None,
        None,
        "managed Work attempt failed",
        "managed_work_failed",
    )
    assert _stored(repository, "SELECT count(*) FROM work_workflow_step_projections")[0] == 0

    assert (
        workflow_journal.project_work_failure(
            repository=repository,
            run_id=binding.run_id,
            run_generation=binding.run_generation,
            tier=binding.tier,
            step_id=binding.step_id,
            step_attempt=binding.workflow_step_attempt,
            binding=binding,
            work_state=work_state,
            updated_at="2026-09-29T10:06:00Z",
        )
        == "already_failed"
    )


def test_work_failure_projection_rejects_stale_work_attempt(projection_state):
    repository, binding, _accepted = projection_state
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE work_items SET state='failed',revision=4,accepted_result_id=NULL "
            "WHERE id=%s",
            (binding.work_item_id,),
        )
        connection.execute(
            "UPDATE work_attempts SET state='failed',revision=5 WHERE id=%s",
            (binding.work_attempt_id,),
        )
    stale_state = SimpleNamespace(
        binding_id=binding.binding_id,
        work_item_id=binding.work_item_id,
        work_attempt_id=binding.work_attempt_id,
        work_generation=binding.work_generation,
        work_state="failed",
        work_revision=4,
        attempt_state="failed",
        attempt_revision=5,
        current_attempt_id=f"{binding.work_attempt_id}-next",
        current_generation=binding.work_generation + 1,
        current_attempt_state="running",
        accepted_result_id=None,
        cleanup_state="not_requested",
    )

    with pytest.raises(
        workflow_journal.WorkflowProjectionConflict,
        match="exact current failed attempt",
    ):
        workflow_journal.project_work_failure(
            repository=repository,
            run_id=binding.run_id,
            run_generation=binding.run_generation,
            tier=binding.tier,
            step_id=binding.step_id,
            step_attempt=binding.workflow_step_attempt,
            binding=binding,
            work_state=stale_state,
            updated_at="2026-09-29T10:05:00Z",
        )

    assert (
        _stored(
            repository,
            "SELECT state FROM workflow_run_step WHERE run_id=%s AND step_id=%s",
            (binding.run_id, binding.step_id),
        )[0]
        == "work_pending"
    )


def test_work_failure_projection_rechecks_current_attempt_inside_cas(projection_state):
    repository, binding, _accepted = projection_state
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE work_items SET state='failed',revision=4,accepted_result_id=NULL "
            "WHERE id=%s",
            (binding.work_item_id,),
        )
        connection.execute(
            "UPDATE work_attempts SET state='failed',revision=5 WHERE id=%s",
            (binding.work_attempt_id,),
        )
    proof = SimpleNamespace(
        binding_id=binding.binding_id,
        work_item_id=binding.work_item_id,
        work_attempt_id=binding.work_attempt_id,
        work_generation=binding.work_generation,
        work_state="failed",
        work_revision=4,
        attempt_state="failed",
        attempt_revision=5,
        current_attempt_id=binding.work_attempt_id,
        current_generation=binding.work_generation,
        current_attempt_state="failed",
        accepted_result_id=None,
        cleanup_state="not_requested",
    )
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE work_items SET state='running',revision=6 WHERE id=%s",
            (binding.work_item_id,),
        )
        connection.execute(
            "INSERT INTO work_attempts "
            "(id,work_item_id,attempt_number,generation,provider,state,revision,"
            "lease_expires_at,cleanup_state,created_at) "
            "VALUES (%s,%s,2,%s,'mock_cli','running',1,%s,'not_requested',%s)",
            (
                f"{binding.work_attempt_id}-next",
                binding.work_item_id,
                binding.work_generation + 1,
                time.time() + 300,
                time.time(),
            ),
        )

    with pytest.raises(
        workflow_journal.WorkflowProjectionConflict,
        match="changed before journal CAS",
    ):
        workflow_journal.project_work_failure(
            repository=repository,
            run_id=binding.run_id,
            run_generation=binding.run_generation,
            tier=binding.tier,
            step_id=binding.step_id,
            step_attempt=binding.workflow_step_attempt,
            binding=binding,
            work_state=proof,
            updated_at="2026-09-29T10:05:00Z",
        )

    assert (
        _stored(
            repository,
            "SELECT state FROM workflow_run_step WHERE run_id=%s AND step_id=%s",
            (binding.run_id, binding.step_id),
        )[0]
        == "work_pending"
    )


def test_concurrent_work_failure_projection_has_one_failure_and_one_replay(projection_state):
    repository, binding, _accepted = projection_state
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE work_items SET state='failed',revision=4,accepted_result_id=NULL "
            "WHERE id=%s",
            (binding.work_item_id,),
        )
        connection.execute(
            "UPDATE work_attempts SET state='failed',revision=5 WHERE id=%s",
            (binding.work_attempt_id,),
        )
    proof = SimpleNamespace(
        binding_id=binding.binding_id,
        work_item_id=binding.work_item_id,
        work_attempt_id=binding.work_attempt_id,
        work_generation=binding.work_generation,
        work_state="failed",
        work_revision=4,
        attempt_state="failed",
        attempt_revision=5,
        current_attempt_id=binding.work_attempt_id,
        current_generation=binding.work_generation,
        current_attempt_state="failed",
        accepted_result_id=None,
        cleanup_state="not_requested",
    )

    def project():
        return workflow_journal.project_work_failure(
            repository=repository,
            run_id=binding.run_id,
            run_generation=binding.run_generation,
            tier=binding.tier,
            step_id=binding.step_id,
            step_attempt=binding.workflow_step_attempt,
            binding=binding,
            work_state=proof,
            updated_at="2026-09-29T10:05:00Z",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: project(), range(2)))

    assert sorted(outcomes) == ["already_failed", "failed"]
    assert (
        _stored(
            repository,
            "SELECT state FROM workflow_run_step WHERE run_id=%s AND step_id=%s",
            (binding.run_id, binding.step_id),
        )[0]
        == "failed"
    )


def test_concurrent_projection_has_one_commit_and_one_idempotent_replay(
    projection_state, monkeypatch
):
    repository, binding, accepted = projection_state
    original_transaction = repository.transaction
    initial_marker_reads = Barrier(2)

    class _InitialMarkerRead:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchone(self):
            row = self.cursor.fetchone()
            assert row is None
            initial_marker_reads.wait(timeout=10)
            return row

    @contextmanager
    def coordinate_initial_marker_read():
        with original_transaction() as connection:
            original_execute = connection.execute
            intercepted = False

            def execute(statement, parameters=(), *, prepare: bool | None = None):
                nonlocal intercepted
                if prepare is None:
                    result = original_execute(statement, parameters)
                else:
                    result = original_execute(statement, parameters, prepare=prepare)
                if (
                    not intercepted
                    and isinstance(statement, str)
                    and statement.startswith(
                        "SELECT accepted_result_id,content_hash "
                        "FROM work_workflow_step_projections"
                    )
                ):
                    intercepted = True
                    return _InitialMarkerRead(result)
                return result

            connection.execute = execute
            yield connection

    monkeypatch.setattr(repository, "transaction", coordinate_initial_marker_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _index: _project(repository, binding, accepted),
                range(2),
            )
        )

    assert sorted(results) == ["already_projected", "projected"]
    assert _stored(repository, "SELECT count(*) FROM work_workflow_step_projections")[0] == 1
    assert tuple(
        _stored(
            repository,
            "SELECT state,attempts,result_json FROM workflow_run_step "
            "WHERE run_id=%s AND step_id=%s",
            (binding.run_id, binding.step_id),
        )
    ) == (
        "completed",
        binding.workflow_step_attempt,
        accepted.canonical_bytes.decode("utf-8"),
    )


def test_projection_rejects_work_repository_outside_configured_database(projection_state):
    from dataclasses import replace
    from uuid import uuid4

    repository, binding, accepted = projection_state
    other = WorkRepository(replace(repository.target, store_id=str(uuid4())))

    with pytest.raises(workflow_journal.WorkflowProjectionConflict, match="configured database"):
        _project(other, binding, accepted)


def test_journal_lists_only_runs_with_work_pending_steps():
    workflow_journal.insert_run(
        "run-pending", "workflow", "{}", "{}", "running", "2026-09-29T10:00:00Z"
    )
    workflow_journal.insert_steps(
        "run-pending", [("build", "pending"), ("review", "completed")], "2026-09-29T10:00:00Z"
    )
    workflow_journal.mark_work_pending(
        run_id="run-pending",
        step_id="build",
        generation="1",
        step_attempt=1,
        tier="yaml",
        updated_at="2026-09-29T10:01:00Z",
    )
    workflow_journal.insert_run(
        "run-clear", "workflow", "{}", "{}", "running", "2026-09-29T10:00:00Z"
    )
    workflow_journal.insert_steps("run-clear", [("build", "completed")], "2026-09-29T10:00:00Z")

    assert workflow_journal.list_work_pending_run_ids() == ["run-pending"]


def test_mark_work_pending_rejects_replay_when_run_no_longer_points_to_step():
    workflow_journal.insert_run(
        "run-diverged", "workflow", "{}", "{}", "running", "2026-09-29T10:00:00Z"
    )
    workflow_journal.insert_steps("run-diverged", [("build", "pending")], "2026-09-29T10:00:00Z")
    workflow_journal.mark_work_pending(
        run_id="run-diverged",
        step_id="build",
        generation="1",
        step_attempt=1,
        tier="yaml",
        updated_at="2026-09-29T10:01:00Z",
    )
    with workflow_journal._connect() as connection:
        connection.execute(
            "UPDATE workflow_run SET current_step_id=NULL WHERE run_id='run-diverged'"
        )

    with pytest.raises(ValueError, match="not current"):
        workflow_journal.mark_work_pending(
            run_id="run-diverged",
            step_id="build",
            generation="1",
            step_attempt=1,
            tier="yaml",
            updated_at="2026-09-29T10:02:00Z",
        )

    step = workflow_journal.get_step("run-diverged", "build")
    assert step.state == "work_pending"
    assert step.attempts == 1


def test_mark_work_pending_persists_and_fences_yaml_call_fingerprint():
    workflow_journal.insert_run(
        "run-yaml-fingerprint",
        "workflow",
        "{}",
        "{}",
        "running",
        "2026-09-29T10:00:00Z",
        "yaml",
        "1",
    )
    workflow_journal.insert_steps(
        "run-yaml-fingerprint", [("build", "pending")], "2026-09-29T10:00:00Z"
    )
    fingerprint = "a" * 64

    workflow_journal.mark_work_pending(
        run_id="run-yaml-fingerprint",
        step_id="build",
        generation="1",
        step_attempt=1,
        tier="yaml",
        updated_at="2026-09-29T10:01:00Z",
        call_fingerprint=fingerprint,
    )

    step = workflow_journal.get_step("run-yaml-fingerprint", "build")
    assert step.call_fingerprint == fingerprint
    with pytest.raises(ValueError, match="fingerprint"):
        workflow_journal.mark_work_pending(
            run_id="run-yaml-fingerprint",
            step_id="build",
            generation="1",
            step_attempt=1,
            tier="yaml",
            updated_at="2026-09-29T10:02:00Z",
            call_fingerprint="b" * 64,
        )
    assert workflow_journal.get_step("run-yaml-fingerprint", "build").call_fingerprint == (
        fingerprint
    )


def _insert_script_run(run_id, *, generation="1", current_step=None):
    now = "2026-09-29T10:00:00Z"
    workflow_journal.insert_run(
        run_id,
        "script-workflow",
        "{}",
        "{}",
        "running",
        now,
        "script",
        generation,
    )
    if current_step is not None:
        workflow_journal.update_run_current_step(run_id, current_step)


def _insert_failed_managed_run(run_id, *, tier="script", generation="4", attempts=2):
    now = "2026-09-29T10:00:00Z"
    workflow_journal.insert_run(
        run_id,
        "managed-workflow",
        "{}",
        "{}",
        "running",
        now,
        tier,
        generation,
    )
    workflow_journal.insert_steps(run_id, [("build", "failed")], now)
    with workflow_journal._connect() as connection:
        connection.execute(
            "UPDATE workflow_run SET current_step_id='build' WHERE run_id=%s", (run_id,)
        )
        connection.execute(
            "UPDATE workflow_run_step SET attempts=%s,error='managed Work attempt failed',"
            "error_kind='managed_work_failed',call_fingerprint=%s WHERE run_id=%s AND step_id='build'",
            (attempts, "1" * 64, run_id),
        )


def _retry_authorization(run_id, *, tier="script", generation=4, prior_attempt=2):
    provisional = WorkflowStepRetryAuthorization(
        authorization_id=f"retry-{run_id}",
        authorization_fingerprint="0" * 64,
        binding_id=f"binding-{run_id}",
        binding_fingerprint="2" * 64,
        tier=tier,
        run_id=run_id,
        run_generation=generation,
        workflow_id="workflow-1",
        spec_hash="3" * 64,
        step_id="build",
        workflow_step_attempt=prior_attempt,
        principal_id="principal-1",
        provision_id="provision-1",
        provision_revision=1,
        provision_fingerprint="4" * 64,
        work_item_id=f"work-{run_id}",
        work_attempt_id=f"attempt-{run_id}",
        work_generation=5,
        authorized_at=1790676000.0,
    )
    return provisional.model_copy(
        update={"authorization_fingerprint": provisional.computed_fingerprint()}
    )


def _retry_dependencies(authorization, *, authorization_from_reader=None, state=None):
    repository = WorkRepository(DatabaseTarget.from_settings("main"))
    binding = SimpleNamespace(
        binding_id=authorization.binding_id,
        binding_fingerprint=authorization.binding_fingerprint,
        tier=authorization.tier,
        run_id=authorization.run_id,
        run_generation=authorization.run_generation,
        step_id=authorization.step_id,
        workflow_step_attempt=authorization.workflow_step_attempt,
        workflow_id=authorization.workflow_id,
        spec_hash=authorization.spec_hash,
        provision_id=authorization.provision_id,
        provision_revision=authorization.provision_revision,
        provision_fingerprint=authorization.provision_fingerprint,
        work_item_id=authorization.work_item_id,
        work_attempt_id=authorization.work_attempt_id,
        work_generation=authorization.work_generation,
    )
    expected_state = SimpleNamespace(
        binding_id=authorization.binding_id,
        work_item_id=authorization.work_item_id,
        work_attempt_id=authorization.work_attempt_id,
        work_generation=authorization.work_generation,
        work_state="failed",
        work_revision=7,
        attempt_state="failed",
        attempt_revision=8,
        current_attempt_id=authorization.work_attempt_id,
        current_generation=authorization.work_generation,
        current_attempt_state="failed",
        accepted_result_id=None,
        cleanup_state="not_requested",
    )

    class Origins:
        def __init__(self):
            self.repository = repository
            self.authorization_reads = []
            self.binding_reads = []

        def read_step_retry_authorization(self, principal, **identity):
            self.authorization_reads.append((principal, identity))
            assert identity["connection"].in_transaction
            return authorization_from_reader or authorization

        def read_step_binding(self, *identity, connection=None):
            self.binding_reads.append((identity, connection))
            assert connection.in_transaction
            return binding

    class Service:
        def __init__(self):
            self.repository = repository
            self.reads = []

        def read_workflow_step_state(self, exact_binding, *, connection=None):
            self.reads.append((exact_binding, connection))
            assert connection.in_transaction
            return state or expected_state

    return SimpleNamespace(id=authorization.principal_id), Origins(), Service(), binding


def _begin_authorized_retry(run_id, authorization, dependencies, fingerprint="a" * 64):
    principal, origins, service, _binding = dependencies
    return workflow_journal.begin_managed_work_step(
        run_id,
        "build",
        str(authorization.run_generation),
        fingerprint,
        "2026-09-29T10:01:00Z",
        retry_authorization=authorization,
        principal=principal,
        workflow_origins=origins,
        work_service=service,
    )


def test_begin_managed_work_step_is_idempotent_for_same_pending_fingerprint():
    _insert_script_run("managed-first")
    fingerprint = "a" * 64

    first = workflow_journal.begin_managed_work_step(
        "managed-first", "build", "1", fingerprint, "2026-09-29T10:01:00Z"
    )
    original = workflow_journal.get_step("managed-first", "build")
    replay = workflow_journal.begin_managed_work_step(
        "managed-first", "build", "1", fingerprint, "2026-09-29T10:02:00Z"
    )
    current = workflow_journal.get_step("managed-first", "build")

    assert first == replay == 1
    assert current.state == "work_pending"
    assert current.attempts == 1
    assert current.call_fingerprint == fingerprint
    assert current.updated_at == original.updated_at


def test_concurrent_managed_step_arrivals_share_one_workflow_attempt():
    _insert_script_run("managed-concurrent")
    fingerprint = "f" * 64

    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(
            pool.map(
                lambda _index: workflow_journal.begin_managed_work_step(
                    "managed-concurrent",
                    "build",
                    "1",
                    fingerprint,
                    "2026-09-29T10:01:00Z",
                ),
                range(2),
            )
        )

    step = workflow_journal.get_step("managed-concurrent", "build")
    assert attempts == [1, 1]
    assert step.state == "work_pending"
    assert step.attempts == 1


def test_begin_managed_work_step_preserves_attempt_on_abandoned_running_row():
    _insert_script_run("managed-crash")
    fingerprint = "b" * 64
    workflow_journal.begin_step("managed-crash", "build", "2026-09-29T10:00:30Z", fingerprint)
    workflow_journal.update_step(
        "managed-crash",
        "build",
        "running",
        2,
        "2026-09-29T10:00:45Z",
        None,
        None,
    )

    attempt = workflow_journal.begin_managed_work_step(
        "managed-crash", "build", "1", fingerprint, "2026-09-29T10:01:00Z"
    )
    step = workflow_journal.get_step("managed-crash", "build")

    assert attempt == 2
    assert step.state == "work_pending"
    assert step.attempts == 2


def test_begin_managed_work_step_initializes_zero_count_running_row_to_first_attempt():
    _insert_script_run("managed-zero-running")
    fingerprint = "8" * 64
    workflow_journal.begin_step(
        "managed-zero-running", "build", "2026-09-29T10:00:30Z", fingerprint
    )

    attempt = workflow_journal.begin_managed_work_step(
        "managed-zero-running", "build", "1", fingerprint, "2026-09-29T10:01:00Z"
    )

    assert attempt == 1
    assert workflow_journal.get_step("managed-zero-running", "build").attempts == 1


def test_begin_managed_work_step_preserves_authorized_pending_retry_attempt():
    _insert_script_run("managed-authorized-retry")
    fingerprint = "9" * 64
    workflow_journal.insert_steps(
        "managed-authorized-retry",
        [("build", "pending")],
        "2026-09-29T10:00:00Z",
    )
    with workflow_journal._connect() as connection:
        connection.execute(
            "UPDATE workflow_run_step SET attempts=2,call_fingerprint=%s "
            "WHERE run_id='managed-authorized-retry' AND step_id='build'",
            (fingerprint,),
        )

    attempt = workflow_journal.begin_managed_work_step(
        "managed-authorized-retry",
        "build",
        "1",
        fingerprint,
        "2026-09-29T10:01:00Z",
    )
    claimed = workflow_journal.get_step("managed-authorized-retry", "build")
    recovered_attempt = workflow_journal.begin_managed_work_step(
        "managed-authorized-retry",
        "build",
        "1",
        fingerprint,
        "2026-09-29T10:02:00Z",
    )
    recovered = workflow_journal.get_step("managed-authorized-retry", "build")

    assert attempt == recovered_attempt == 2
    assert claimed.state == recovered.state == "work_pending"
    assert claimed.attempts == recovered.attempts == 2
    assert claimed.call_fingerprint == recovered.call_fingerprint == fingerprint
    assert claimed.updated_at == recovered.updated_at


def test_begin_managed_work_step_rejects_negative_attempt_count_without_write():
    _insert_script_run("managed-negative-attempt")
    fingerprint = "7" * 64
    workflow_journal.begin_step(
        "managed-negative-attempt", "build", "2026-09-29T10:00:30Z", fingerprint
    )
    with workflow_journal._connect() as connection:
        connection.execute(
            "UPDATE workflow_run_step SET attempts=-1 "
            "WHERE run_id='managed-negative-attempt' AND step_id='build'"
        )

    with pytest.raises(ValueError, match="attempt count"):
        workflow_journal.begin_managed_work_step(
            "managed-negative-attempt",
            "build",
            "1",
            fingerprint,
            "2026-09-29T10:01:00Z",
        )

    step = workflow_journal.get_step("managed-negative-attempt", "build")
    run = workflow_journal.get_run("managed-negative-attempt")
    assert step.state == "running"
    assert step.attempts == -1
    assert step.call_fingerprint == fingerprint
    assert run.current_step_id is None


@pytest.mark.parametrize("tier", ["yaml", "script"])
def test_begin_managed_work_step_consumes_exact_retry_authorization_once(tier):
    run_id = f"managed-retry-{tier}"
    _insert_failed_managed_run(run_id, tier=tier)
    authorization = _retry_authorization(run_id, tier=tier)
    dependencies = _retry_dependencies(authorization)

    attempt = _begin_authorized_retry(run_id, authorization, dependencies)
    step = workflow_journal.get_step(run_id, "build")

    assert attempt == 3
    assert step.state == "work_pending"
    assert step.attempts == 3
    assert step.error == "awaiting_authenticated_work_result"
    assert step.error_kind is None
    assert step.call_fingerprint == workflow_journal._managed_retry_call_fingerprint(
        "a" * 64, authorization
    )
    _principal, origins, service, binding = dependencies
    assert len(origins.authorization_reads) == 1
    assert len(origins.binding_reads) == 1
    assert len(service.reads) == 1
    assert service.reads[0][0] is binding


def test_concurrent_authorized_retry_claims_share_one_next_attempt():
    run_id = "managed-retry-concurrent"
    _insert_failed_managed_run(run_id)
    authorization = _retry_authorization(run_id)
    dependencies = _retry_dependencies(authorization)

    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(
            pool.map(
                lambda _index: _begin_authorized_retry(run_id, authorization, dependencies),
                range(2),
            )
        )

    step = workflow_journal.get_step(run_id, "build")
    assert attempts == [3, 3]
    assert step.state == "work_pending"
    assert step.attempts == 3
    assert len(dependencies[1].authorization_reads) == 1
    assert len(dependencies[2].reads) == 1


def test_begin_managed_work_step_reuses_same_authorized_retry_after_restart():
    run_id = "managed-retry-restart"
    _insert_failed_managed_run(run_id)
    authorization = _retry_authorization(run_id)
    dependencies = _retry_dependencies(authorization)

    assert _begin_authorized_retry(run_id, authorization, dependencies) == 3
    _principal, origins, service, _binding = dependencies
    service.reads.clear()
    service.read_workflow_step_state = lambda *_args, **_kwargs: pytest.fail(
        "pending retry replay must not read or replace the current Work attempt"
    )
    original = workflow_journal.get_step(run_id, "build")

    recovered = _begin_authorized_retry(run_id, authorization, dependencies)
    replayed = workflow_journal.get_step(run_id, "build")

    assert recovered == 3
    assert replayed.attempts == 3
    assert replayed.state == "work_pending"
    assert replayed.call_fingerprint == original.call_fingerprint
    assert replayed.updated_at == original.updated_at
    assert len(origins.authorization_reads) == 1
    assert service.reads == []


def test_begin_managed_work_step_rejects_another_authorization_on_pending_replay():
    run_id = "managed-retry-other-auth"
    _insert_failed_managed_run(run_id)
    authorization = _retry_authorization(run_id)
    dependencies = _retry_dependencies(authorization)
    assert _begin_authorized_retry(run_id, authorization, dependencies) == 3
    other_authorization = _retry_authorization(run_id).model_copy(
        update={"authorization_id": "another-auth"}
    )
    other_authorization = other_authorization.model_copy(
        update={"authorization_fingerprint": other_authorization.computed_fingerprint()}
    )
    other_dependencies = _retry_dependencies(other_authorization)

    with pytest.raises(ValueError, match="fingerprint or identity changed"):
        _begin_authorized_retry(run_id, other_authorization, other_dependencies)

    step = workflow_journal.get_step(run_id, "build")
    assert step.state == "work_pending"
    assert step.attempts == 3


def test_begin_managed_work_step_compares_retry_authorization_to_durable_record(monkeypatch):
    run_id = "managed-retry-mismatched-record"
    _insert_failed_managed_run(run_id)
    authorization = _retry_authorization(run_id)
    other_authorization = authorization.model_copy(
        update={"authorization_id": "different-durable-auth"}
    )
    other_authorization = other_authorization.model_copy(
        update={"authorization_fingerprint": other_authorization.computed_fingerprint()}
    )
    dependencies = _retry_dependencies(authorization, authorization_from_reader=other_authorization)
    transaction_state_before_close = {}
    original_closing = workflow_journal.closing

    @contextmanager
    def observe_close(connection):
        original_close = connection.close

        def observe_native_close():
            transaction_state_before_close.setdefault(connection, connection.in_transaction)
            original_close()

        connection.close = observe_native_close
        with original_closing(connection):
            yield connection

    monkeypatch.setattr(workflow_journal, "closing", observe_close)

    with pytest.raises(ValueError, match="differs from its durable record"):
        _begin_authorized_retry(run_id, authorization, dependencies)

    step = workflow_journal.get_step(run_id, "build")
    assert step.state == "failed"
    assert step.attempts == 2
    connection = dependencies[1].authorization_reads[0][1]["connection"]
    assert transaction_state_before_close[connection] is False
    from psycopg import OperationalError

    with pytest.raises(OperationalError, match="closed"):
        connection.execute("SELECT 1")


def test_begin_managed_work_step_rejects_retry_when_current_work_attempt_is_not_failed():
    run_id = "managed-retry-work-stale"
    _insert_failed_managed_run(run_id)
    authorization = _retry_authorization(run_id)
    stale_state = SimpleNamespace(
        binding_id=authorization.binding_id,
        work_item_id=authorization.work_item_id,
        work_attempt_id=authorization.work_attempt_id,
        work_generation=authorization.work_generation,
        work_state="failed",
        work_revision=7,
        attempt_state="failed",
        attempt_revision=8,
        current_attempt_id="new-work-attempt",
        current_generation=6,
        current_attempt_state="running",
        accepted_result_id=None,
        cleanup_state="not_requested",
    )
    dependencies = _retry_dependencies(authorization, state=stale_state)

    with pytest.raises(ValueError, match="exact current failed Work attempt"):
        _begin_authorized_retry(run_id, authorization, dependencies)

    step = workflow_journal.get_step(run_id, "build")
    assert step.state == "failed"
    assert step.attempts == 2


def test_begin_managed_work_step_rejects_stale_retry_authorization_after_new_failure():
    run_id = "managed-retry-stale-auth"
    _insert_failed_managed_run(run_id, attempts=3)
    authorization = _retry_authorization(run_id, prior_attempt=2)
    dependencies = _retry_dependencies(authorization)

    with pytest.raises(ValueError, match="workflow_step_attempt"):
        _begin_authorized_retry(run_id, authorization, dependencies)

    step = workflow_journal.get_step(run_id, "build")
    assert step.state == "failed"
    assert step.attempts == 3
    assert dependencies[1].authorization_reads == []


def test_begin_managed_work_step_rejects_generic_rerun_without_fenced_authorization():
    run_id = "managed-rerun-unfenced"
    _insert_script_run(run_id)
    workflow_journal.insert_steps(
        run_id,
        [("build", "rerun_authorized")],
        "2026-09-29T10:00:00Z",
    )
    with workflow_journal._connect() as connection:
        connection.execute(
            "UPDATE workflow_run_step SET attempts=2 WHERE run_id=%s AND step_id='build'",
            (run_id,),
        )

    with pytest.raises(ValueError, match="current state"):
        workflow_journal.begin_managed_work_step(
            run_id, "build", "1", "c" * 64, "2026-09-29T10:01:00Z"
        )

    step = workflow_journal.get_step(run_id, "build")
    assert step.state == "rerun_authorized"
    assert step.attempts == 2


def test_begin_managed_work_step_rejects_failure_without_explicit_authorization():
    run_id = "managed-failure-without-retry-auth"
    _insert_failed_managed_run(run_id)

    with pytest.raises(ValueError, match="current state"):
        workflow_journal.begin_managed_work_step(
            run_id, "build", "4", "d" * 64, "2026-09-29T10:01:00Z"
        )

    step = workflow_journal.get_step(run_id, "build")
    assert step.state == "failed"
    assert step.attempts == 2


def test_begin_managed_work_step_rejects_stale_run_generation_without_write():
    _insert_script_run("managed-stale", generation="4")
    fingerprint = "c" * 64

    with pytest.raises(ValueError, match="generation"):
        workflow_journal.begin_managed_work_step(
            "managed-stale", "build", "5", fingerprint, "2026-09-29T10:01:00Z"
        )

    assert workflow_journal.get_step("managed-stale", "build") is None


def test_begin_managed_work_step_rejects_fingerprint_change_and_terminal_row():
    _insert_script_run("managed-conflict")
    workflow_journal.begin_step("managed-conflict", "build", "2026-09-29T10:00:30Z", "d" * 64)

    with pytest.raises(ValueError, match="fingerprint"):
        workflow_journal.begin_managed_work_step(
            "managed-conflict", "build", "1", "e" * 64, "2026-09-29T10:01:00Z"
        )

    with workflow_journal._connect() as connection:
        connection.execute(
            "UPDATE workflow_run_step SET state='completed' "
            "WHERE run_id='managed-conflict' AND step_id='build'"
        )
    with pytest.raises(ValueError, match="current state"):
        workflow_journal.begin_managed_work_step(
            "managed-conflict", "build", "1", "d" * 64, "2026-09-29T10:02:00Z"
        )
