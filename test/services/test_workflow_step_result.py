"""Strict wire contract for results returned by managed workflow workers."""

import hashlib
import json
from test.integration.test_work_dispatch import context  # noqa: F401
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_origin import WorkflowStepResultV1
from cli_agent_orchestrator.services.work_origin import WorkOriginAuthority, WorkOrigins
from cli_agent_orchestrator.services.work_service import WorkConflict


def test_workflow_step_result_is_strict_and_canonical():
    result = WorkflowStepResultV1.from_payload(
        {
            "output": {"message": "niño", "score": 1},
            "status": "completed",
            "schema_version": 1,
        }
    )

    assert result.canonical_bytes() == (
        '{"output":{"message":"niño","score":1},"schema_version":1,' '"status":"completed"}'
    ).encode("utf-8")


def test_receiver_result_action_is_separate_and_explicit():
    assert WorkOriginAuthority._action("task_result") == "task_result"
    assert WorkOriginAuthority._actions({"task_received", "task_result"}) == (
        "task_received",
        "task_result",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1, "status": "completed", "output": {}, "identity": "worker"},
        {"schema_version": True, "status": "completed", "output": {}},
        {"schema_version": 2, "status": "completed", "output": {}},
        {"schema_version": 1, "status": "failed", "output": {}},
        {"schema_version": 1, "status": "completed", "output": []},
        {"schema_version": 1, "status": "completed", "output": None},
        {"schema_version": 1, "status": "completed", "output": "value"},
        {"schema_version": 1, "status": "completed", "output": 3},
        {"schema_version": 1, "status": "completed", "output": True},
        {"schema_version": 1, "status": "completed"},
    ],
)
def test_workflow_step_result_rejects_noncontract_envelopes(payload):
    with pytest.raises(ValueError):
        WorkflowStepResultV1.from_payload(payload)


def test_workflow_step_result_rejects_duplicate_json_keys():
    raw = b'{"schema_version":1,"status":"completed","output":{"x":1,"x":2}}'

    with pytest.raises(ValueError, match="duplicate"):
        WorkflowStepResultV1.from_json_bytes(raw)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":1,"status":"completed","output":{"x":NaN}}',
        b'{"schema_version":1,"status":"completed","output":{"x":Infinity}}',
        b'{"schema_version":1,"status":"completed","output":{"x":-Infinity}}',
    ],
)
def test_workflow_step_result_rejects_nonfinite_json_numbers(raw):
    with pytest.raises(ValueError):
        WorkflowStepResultV1.from_json_bytes(raw)


def test_workflow_step_result_rejects_nonfinite_python_numbers():
    with pytest.raises(ValueError):
        WorkflowStepResultV1.from_payload(
            {"schema_version": 1, "status": "completed", "output": {"x": float("nan")}}
        )


def test_workflow_step_result_rejects_envelope_over_one_mibibyte():
    with pytest.raises(ValueError, match="1 MiB"):
        WorkflowStepResultV1.from_payload(
            {
                "schema_version": 1,
                "status": "completed",
                "output": {"text": "x" * (1024 * 1024)},
            }
        )


def test_frozen_workflow_output_schema_is_validated_before_work_settlement():
    from cli_agent_orchestrator.services.work_service import validate_workflow_step_result

    schema_json = '{"additionalProperties":false,"properties":{"answer":{"type":"integer"}},"required":["answer"],"type":"object"}'
    binding = SimpleNamespace(
        binding_id="workflow-binding",
        provision_fingerprint="b" * 64,
        output_schema_json=schema_json,
        output_schema_hash=hashlib.sha256(schema_json.encode("utf-8")).hexdigest(),
    )
    result = WorkflowStepResultV1.from_payload(
        {"schema_version": 1, "status": "completed", "output": {"answer": 42}}
    )

    evidence = validate_workflow_step_result(binding, result.canonical_bytes())

    assert evidence["valid"] is True
    assert evidence["output_schema_hash"] == binding.output_schema_hash


def test_frozen_workflow_output_schema_rejects_invalid_output_and_schema_drift():
    from cli_agent_orchestrator.services.work_service import validate_workflow_step_result

    schema_json = '{"type":"object","required":["answer"]}'
    binding = SimpleNamespace(
        binding_id="workflow-binding",
        provision_fingerprint="b" * 64,
        output_schema_json=schema_json,
        output_schema_hash=hashlib.sha256(schema_json.encode("utf-8")).hexdigest(),
    )
    invalid = WorkflowStepResultV1.from_payload(
        {"schema_version": 1, "status": "completed", "output": {"other": 42}}
    )

    with pytest.raises((WorkConflict, ValueError)):
        validate_workflow_step_result(binding, invalid.canonical_bytes())

    tampered = SimpleNamespace(
        **{
            **binding.__dict__,
            "output_schema_hash": "0" * 64,
        }
    )
    with pytest.raises((WorkConflict, ValueError)):
        validate_workflow_step_result(tampered, invalid.canonical_bytes())


def test_private_mcp_submit_result_routes_only_a_strict_envelope_with_separate_credentials(
    tmp_path,
):
    from types import SimpleNamespace
    from unittest.mock import Mock

    repository = WorkRepository(tmp_path / "work.sqlite3")
    repository.initialize()
    origins = WorkOrigins(repository)
    submit = Mock(
        return_value={
            "id": "work-item",
            "state": "succeeded",
            "attempts": [{"id": "work-attempt", "generation": 4, "state": "finished"}],
        }
    )
    origins._result_service = SimpleNamespace(submit_workflow_step_result=submit)
    request = {
        "jsonrpc": "2.0",
        "id": "submit-1",
        "method": "tools/call",
        "params": {
            "name": "cao.work.submit_result",
            "arguments": {
                "schema_version": 1,
                "status": "completed",
                "output": {"answer": 42},
            },
        },
    }

    response = origins.handle_mcp_request(
        request, b"attempt credential", receiver_credential=b"receiver credential"
    )

    assert response["result"]["content"]
    body = json.loads(response["result"]["content"][0]["text"])
    assert body == {
        "attempt_id": "work-attempt",
        "generation": 4,
        "state": "finished",
        "work_item_id": "work-item",
    }
    submit.assert_called_once()
    arguments = submit.call_args.kwargs
    assert arguments["attempt_credential"] == b"attempt credential"
    assert arguments["receiver_credential"] == b"receiver credential"
    assert isinstance(arguments["result"], WorkflowStepResultV1)
    assert arguments["result"].output == {"answer": 42}


@pytest.mark.parametrize(
    "arguments,receiver_credential",
    [
        ({"schema_version": 1, "status": "completed", "output": {}, "extra": 1}, b"receiver"),
        ({"schema_version": 1, "status": "completed", "output": {}}, None),
    ],
)
def test_private_mcp_submit_result_rejects_invalid_payload_or_missing_receiver_credential(
    tmp_path, arguments, receiver_credential
):
    from types import SimpleNamespace
    from unittest.mock import Mock

    repository = WorkRepository(tmp_path / "work.sqlite3")
    repository.initialize()
    origins = WorkOrigins(repository)
    submit = Mock()
    origins._result_service = SimpleNamespace(submit_workflow_step_result=submit)
    response = origins.handle_mcp_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "cao.work.submit_result", "arguments": arguments},
        },
        b"attempt credential",
        receiver_credential=receiver_credential,
    )

    assert "error" in response
    submit.assert_not_called()


def _result_submission_harness(tmp_path, *, accepted_bytes, initially_accepted=False):
    from types import SimpleNamespace

    from cli_agent_orchestrator.services.work_service import WorkService

    repository = WorkRepository(tmp_path / "workflow-result-settlement.sqlite3")
    repository.initialize()
    origins = WorkOrigins(repository)
    artifacts = SimpleNamespace(publish=lambda *_args: None, read=lambda *_args: None)
    service = WorkService(repository, origins=origins, artifacts=artifacts)
    binding = SimpleNamespace(
        binding_id="workflow-binding",
        provision_fingerprint="b" * 64,
        tier="yaml",
        run_id="workflow-run",
        run_generation=1,
        step_id="build",
        workflow_step_attempt=1,
        job_id="job",
        work_item_id="work-item",
        work_attempt_id="work-attempt",
        work_generation=3,
        delivery_id="delivery",
        delivery_hash="c" * 64,
        output_schema_json=None,
        output_schema_hash=None,
    )
    attempt = SimpleNamespace(
        attempt_id=binding.work_attempt_id,
        generation=binding.work_generation,
        work_item_id=binding.work_item_id,
        job_id="job",
        grant_id="grant",
        grant_revision=1,
        contract_hash="d" * 64,
    )
    receiver = SimpleNamespace(
        attempt_id=binding.work_attempt_id,
        generation=binding.work_generation,
        work_item_id=binding.work_item_id,
        binding_id=binding.binding_id,
        receiver_subject_id="receiver",
        receiver_subject_revision=1,
        receiver_authorization_revision=1,
        receiver_grant_id="receiver-grant",
        receiver_grant_revision=1,
        delivery_id=binding.delivery_id,
        delivery_hash=binding.delivery_hash,
    )
    winning = WorkflowStepResultV1.from_payload(
        {"schema_version": 1, "status": "completed", "output": {"answer": 42}}
    )
    submitted = WorkflowStepResultV1.from_payload(
        {"schema_version": 1, "status": "completed", "output": {"answer": 7}}
    )
    accepted = SimpleNamespace(
        accepted_result_id="winner-result",
        canonical_bytes=accepted_bytes or winning.canonical_bytes(),
        content_hash=hashlib.sha256(accepted_bytes or winning.canonical_bytes()).hexdigest(),
    )
    current = {
        "id": binding.work_item_id,
        "accepted_result_id": "winner-result" if initially_accepted else None,
        "attempts": [
            {
                "id": binding.work_attempt_id,
                "generation": binding.work_generation,
                "state": "finished" if initially_accepted else "running",
            }
        ],
    }
    settled = {
        "id": binding.work_item_id,
        "accepted_result_id": "winner-result",
        "attempts": [
            {
                "id": binding.work_attempt_id,
                "generation": binding.work_generation,
                "state": "finished",
            }
        ],
    }
    repository._work = lambda _connection, _work_item_id: current
    repository._job = lambda _connection, _job_id: {"principal_id": "actor"}
    service._authenticate_workflow_result = lambda *_args, **_kwargs: (
        attempt,
        receiver,
        binding,
        object(),
    )
    service.read_accepted_workflow_result = lambda _binding: SimpleNamespace(
        accepted_result_id=accepted.accepted_result_id,
        canonical_bytes=accepted.canonical_bytes,
        content_hash=accepted.content_hash,
    )
    service.settle_attempt = lambda *_args, **_kwargs: settled
    return service, binding, submitted, winning, settled, accepted


def test_losing_concurrent_different_workflow_result_is_rejected_after_finish(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from cli_agent_orchestrator.services.work_service import WorkConflict

    service, binding, submitted, _winning, settled, accepted = _result_submission_harness(
        tmp_path, accepted_bytes=None
    )
    other = WorkflowStepResultV1.from_payload(
        {"schema_version": 1, "status": "completed", "output": {"answer": 99}}
    )
    barrier = threading.Barrier(2)
    winner = {}
    winner_lock = threading.Lock()

    def concurrent_settlement(_work_item_id, *, content, **_kwargs):
        barrier.wait(timeout=5)
        with winner_lock:
            if "content" not in winner:
                winner["content"] = content
                accepted.canonical_bytes = content
                accepted.content_hash = hashlib.sha256(content).hexdigest()
                accepted.accepted_result_id = hashlib.sha256(
                    f"{binding.work_attempt_id}:{accepted.content_hash}".encode()
                ).hexdigest()
            return {
                **settled,
                "accepted_result_id": accepted.accepted_result_id,
            }

    service.settle_attempt = concurrent_settlement

    def submit(result):
        try:
            return service.submit_workflow_step_result(
                attempt_credential=b"attempt",
                receiver_credential=b"receiver",
                result=result,
            )
        except WorkConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, (submitted, other)))

    assert sum(isinstance(outcome, WorkConflict) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1
    assert accepted.canonical_bytes in {submitted.canonical_bytes(), other.canonical_bytes()}


def test_identical_workflow_result_submission_replays_the_durable_bytes(tmp_path):
    service, _binding, submitted, _winning, settled, _accepted = _result_submission_harness(
        tmp_path,
        accepted_bytes=WorkflowStepResultV1.from_payload(
            {"schema_version": 1, "status": "completed", "output": {"answer": 7}}
        ).canonical_bytes(),
        initially_accepted=True,
    )
    service.settle_attempt = lambda *_args, **_kwargs: pytest.fail(
        "an exact result replay must not settle a second time"
    )

    replay = service.submit_workflow_step_result(
        attempt_credential=b"attempt",
        receiver_credential=b"receiver",
        result=submitted,
    )

    assert replay == settled


def test_invalid_frozen_schema_result_does_not_settle_or_emit_work_events(tmp_path):
    from cli_agent_orchestrator.services.work_service import WorkConflict

    service, binding, _submitted, _winning, _settled, _accepted = _result_submission_harness(
        tmp_path, accepted_bytes=None
    )
    schema_json = (
        '{"additionalProperties":false,"properties":{"answer":{"type":"integer"}},'
        '"required":["answer"],"type":"object"}'
    )
    binding.output_schema_json = schema_json
    binding.output_schema_hash = hashlib.sha256(schema_json.encode("utf-8")).hexdigest()
    invalid = WorkflowStepResultV1.from_payload(
        {"schema_version": 1, "status": "completed", "output": {"other": 7}}
    )
    service.settle_attempt = lambda *_args, **_kwargs: pytest.fail(
        "schema-invalid output must fail before settlement"
    )
    with service.repository.connection() as connection:
        before_events = connection.execute("SELECT count(*) FROM work_events").fetchone()[0]
        before_results = connection.execute("SELECT count(*) FROM work_results").fetchone()[0]

    with pytest.raises(WorkConflict, match="does not match its frozen schema"):
        service.submit_workflow_step_result(
            attempt_credential=b"attempt",
            receiver_credential=b"receiver",
            result=invalid,
        )

    with service.repository.connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_events").fetchone()[0] == before_events
        assert (
            connection.execute("SELECT count(*) FROM work_results").fetchone()[0] == before_results
        )


def _workflow_work_state_fixture(
    tmp_path, monkeypatch, *, work_state, attempt_state, add_current=False
):
    import time
    from types import SimpleNamespace

    from cli_agent_orchestrator.services.work_service import WorkService

    repository = WorkRepository(tmp_path / "workflow-work-state.sqlite3")
    repository.initialize()
    binding = SimpleNamespace(
        binding_id="workflow-binding",
        tier="yaml",
        run_id="workflow-run",
        run_generation=1,
        step_id="build",
        workflow_step_attempt=1,
        job_id="job",
        work_item_id="work-item",
        work_attempt_id="work-attempt",
        work_generation=3,
    )
    now = time.time()
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO work_jobs (id,project_id,principal_id,allowed_providers,grant_id,created_at) "
            "VALUES ('job','project','principal','[]','grant',?)",
            (now,),
        )
        connection.execute(
            "INSERT INTO work_items "
            "(id,job_id,operation_kind,idempotency_key,request_hash,contract_id,state,"
            "accepted_result_id,created_at) VALUES "
            "('work-item','job','agent_step','step-key',?,'contract',?,NULL,?)",
            ("a" * 64, work_state, now),
        )
        connection.execute(
            "INSERT INTO work_attempts "
            "(id,work_item_id,attempt_number,generation,provider,state,lease_expires_at,created_at) "
            "VALUES ('work-attempt','work-item',1,3,'mock_cli',?,?,?)",
            (attempt_state, now + 300, now),
        )
        if add_current:
            connection.execute(
                "INSERT INTO work_attempts "
                "(id,work_item_id,attempt_number,generation,provider,state,lease_expires_at,created_at) "
                "VALUES ('new-attempt','work-item',2,4,'mock_cli','planned',?,?)",
                (now + 300, now),
            )
            connection.execute("UPDATE work_items SET state='queued' WHERE id='work-item'")

    from cli_agent_orchestrator.services import work_workflow

    def read_binding(
        _origins, attempt_id, generation, work_item_id, *, connection=None, historical=False
    ):
        # This fixture stands in only for immutable binding lookup. State reads
        # must request historical proof, without reopening live grant authority,
        # and retain the reader's exact existing SQLite transaction.
        assert historical is True
        assert connection is not None and connection.in_transaction
        if (attempt_id, generation, work_item_id) == (
            binding.work_attempt_id,
            binding.work_generation,
            binding.work_item_id,
        ):
            return binding
        return None

    monkeypatch.setattr(work_workflow.WorkWorkflowOrigins, "read_binding_for_attempt", read_binding)
    service = WorkService(repository, origins=WorkOrigins(repository))
    return service, binding


@pytest.mark.parametrize(
    "work_state,attempt_state",
    [
        ("succeeded", "finished"),
        ("failed", "failed"),
        ("reconcile", "reconcile"),
        ("running", "running"),
    ],
)
def test_workflow_work_state_reader_returns_exact_durable_states(
    tmp_path, monkeypatch, work_state, attempt_state
):
    service, binding = _workflow_work_state_fixture(
        tmp_path, monkeypatch, work_state=work_state, attempt_state=attempt_state
    )
    state = service.read_workflow_step_state(binding)

    assert state.binding_id == binding.binding_id
    assert state.work_item_id == binding.work_item_id
    assert state.work_attempt_id == binding.work_attempt_id
    assert state.work_generation == binding.work_generation
    assert state.work_state == work_state
    assert state.attempt_state == attempt_state
    assert state.current_attempt_id == binding.work_attempt_id
    assert state.current_generation == binding.work_generation
    assert state.current_attempt_state == attempt_state


def test_workflow_work_state_reader_marks_exact_old_binding_as_superseded(tmp_path, monkeypatch):
    service, binding = _workflow_work_state_fixture(
        tmp_path, monkeypatch, work_state="running", attempt_state="reconcile", add_current=True
    )
    state = service.read_workflow_step_state(binding)

    assert state.attempt_state == "reconcile"
    assert state.current_attempt_id == "new-attempt"
    assert state.current_generation == binding.work_generation + 1
    assert state.current_attempt_state == "planned"
    assert state.accepted_result_id is None


def test_workflow_work_state_reader_can_share_caller_owned_transaction(tmp_path, monkeypatch):
    from cli_agent_orchestrator.services.work_service import WorkConflict

    service, binding = _workflow_work_state_fixture(
        tmp_path, monkeypatch, work_state="reconcile", attempt_state="reconcile"
    )
    with service.repository.connection() as connection:
        with pytest.raises(WorkConflict, match="stable SQLite snapshot"):
            service.read_workflow_step_state(binding, connection=connection)
    with service.repository.transaction() as connection:
        state = service.read_workflow_step_state(binding, connection=connection)
        assert state.work_state == "reconcile"
        assert state.attempt_state == "reconcile"


@pytest.mark.parametrize("revoke_after_acceptance", [False, True])
def test_workflow_result_requires_live_authority_at_acceptance_but_reads_after_revoke(
    context, tmp_path, revoke_after_acceptance
):
    import asyncio
    import os
    from test.services.test_work_workflow import _provisioned_workflow

    from cli_agent_orchestrator.models.work_origin import WorkflowStepResultV1
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.step_output_store import ImmutableResultStore
    from cli_agent_orchestrator.services.work_origin import WorkOrigins
    from cli_agent_orchestrator.services.work_service import WorkConflict, WorkService

    workflow_origins, authority, subject, _authorization, selector, _provision = (
        _provisioned_workflow(context)
    )
    assert context.service.workflow_origins is workflow_origins
    admitter = workflow_origins.resolve_step_admitter(
        subject, "wf", selector["spec_hash"], selector["step_id"]
    )
    selector_args = {key: value for key, value in selector.items() if key != "principal"}
    work = asyncio.run(admitter(**selector_args))
    binding = workflow_origins.read_step_binding("yaml", selector["run_id"], 1, "build", 1)
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
    artifacts = ImmutableResultStore(tmp_path / "result-content")
    service = WorkService(context.repo, origins=origins, artifacts=artifacts)
    received = origins.accept_task_received_with_credentials(
        attempt_credential=attempt_credential,
        receiver_credential=receiver_credential,
    )
    assert received["attempts"][-1]["state"] == "acknowledged"

    result = WorkflowStepResultV1.from_payload(
        {"schema_version": 1, "status": "completed", "output": {"answer": 42}}
    )
    result_response = lambda: origins.handle_mcp_request(
        {
            "jsonrpc": "2.0",
            "id": "result",
            "method": "tools/call",
            "params": {
                "name": "cao.work.submit_result",
                "arguments": result.model_dump(mode="json"),
            },
        },
        attempt_credential,
        receiver_credential=receiver_credential,
    )
    receiver = auth._verified_principal("issuer", "workflow-receiver", [auth.SCOPE_WRITE], "jwt")

    if revoke_after_acceptance:
        accepted_response = result_response()
        assert "result" in accepted_response
        accepted_before_revoke = service.read_accepted_workflow_result(binding)
        assert accepted_before_revoke is not None
        assert accepted_before_revoke.canonical_bytes == result.canonical_bytes()

    authority.revoke(
        owner=context.actor,
        subject=receiver,
        origin_kind="receiver",
        expected_revision=binding.receiver_authorization_ref.revision,
    )

    if not revoke_after_acceptance:
        rejected_response = result_response()
        assert "error" in rejected_response
        assert context.repo.get_work(binding.work_item_id)["accepted_result_id"] is None
        with pytest.raises(WorkConflict):
            service.submit_workflow_step_result(
                attempt_credential=attempt_credential,
                receiver_credential=receiver_credential,
                result=result,
            )
        return

    restarted_repository = WorkRepository(context.repo.path)
    restarted_origins = WorkOrigins(restarted_repository)
    restarted = WorkService(
        restarted_repository,
        origins=restarted_origins,
        artifacts=ImmutableResultStore(tmp_path / "result-content"),
    )
    accepted_after_revoke = restarted.read_accepted_workflow_result(binding)
    assert accepted_after_revoke == accepted_before_revoke
