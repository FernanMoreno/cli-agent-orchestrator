"""Opt-in API-to-Docker acceptance for managed YAML and script agent steps.

The runner in this directory supplies a pinned Linux/amd64 Docker image and a
static worker binary.  These tests exercise the same versioned process adapter,
private MCP proxy, Work result authority and workflow recovery routes used by
the local server composition.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.api import main
from cli_agent_orchestrator.backends import work_registry
from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
from cli_agent_orchestrator.clients.database import (
    _migrate_workflow_run,
    _migrate_workflow_run_step,
)
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_contract import (
    ContractPermissions,
    ContractResources,
    ContractSnapshot,
    EffectiveWorkContractV2,
)
from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope
from cli_agent_orchestrator.models.workflow import WorkflowSpec, WorkflowStep
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services import workflow_journal, workflow_service
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    ResolvedSnapshot,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_elf_identity import identify_static_executable
from cli_agent_orchestrator.services.work_executable_content import WorkExecutableContent
from cli_agent_orchestrator.services.work_origin import WorkOriginAuthority, WorkOrigins
from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning
from cli_agent_orchestrator.services.work_scheduler import WorkScheduler

_WORKER_COMMAND = "/cao-agent-step-worker"
_WORKFLOW_TOOLS = ("cao.work.submit_result", "cao.work.task_received")
_CONTEXT_TEXT = "T020 frozen delegated context: exact bytes survive restart."
_MISSING = object()


def _require_opt_in():
    if os.environ.get("CAO_T020_ACCEPTANCE") != "1":
        pytest.skip("run test/integration/t020/run-docker-acceptance.sh for Docker acceptance")


def _capture_lifespan_backend(monkeypatch):
    image_id = os.environ.get("CAO_T020_DOCKER_IMAGE_ID")
    if not image_id:
        pytest.skip("T020 runner did not provide the pinned local Docker image ID")
    if os.environ.get("CAO_WORK_DOCKER_LOCAL") != "1":
        pytest.skip("T020 runner did not opt in to the local Docker backend")

    original_factory = work_registry.local_work_backends_for
    docker_cli = os.environ.get(
        "CAO_T020_DOCKER_CLI", os.environ.get("CAO_T019_DOCKER_CLI", "docker")
    )
    process_inputs = []

    def capture_local_backends(repository, *, environ=None):
        backends = original_factory(repository, environ=environ)
        assert set(backends) == {"docker-local"}
        backend = backends["docker-local"]
        backend.docker_command = docker_cli
        execute = backend.execute_bound_process

        def capture_process_input(restriction, **kwargs):
            process_inputs.append(kwargs["worker_input"])
            return execute(restriction, **kwargs)

        backend.execute_bound_process = capture_process_input
        return backends

    monkeypatch.setattr(work_registry, "local_work_backends_for", capture_local_backends)
    assert work_registry.WORK_BACKENDS == {}
    return process_inputs


def _install_lifespan_sandbox(monkeypatch):
    async def never_returns(*_args):
        await asyncio.Future()

    async def no_op(*_args):
        return None

    monkeypatch.setattr(main, "setup_logging", lambda: None)
    monkeypatch.setattr(main, "install_access_log_redaction", lambda: None)
    monkeypatch.setattr(main, "init_telemetry", lambda *_args: None)
    monkeypatch.setattr(main, "shutdown_telemetry", lambda: None)
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "_seed_default_skills_at_startup", lambda: None)
    monkeypatch.setattr(main, "_reconcile_memory_at_startup", lambda: None)
    monkeypatch.setattr(main, "cleanup_old_data", lambda: None)
    monkeypatch.setattr(main, "cleanup_expired_memories", no_op)
    monkeypatch.setattr(main, "_sweep_workflow_runs_at_startup", lambda: None)
    monkeypatch.setattr(main, "flow_daemon", never_returns)
    monkeypatch.setattr(main, "opencode_inbox_delivery_daemon", never_returns)
    monkeypatch.setattr(main, "inbox_reconciliation_daemon", never_returns)
    monkeypatch.setattr(main.status_monitor, "run", never_returns)
    monkeypatch.setattr(main.log_writer, "run", never_returns)
    monkeypatch.setattr(main.inbox_service, "run", never_returns)
    monkeypatch.setattr(main.PluginRegistry, "load", no_op)
    monkeypatch.setattr(main.PluginRegistry, "teardown", no_op)
    monkeypatch.setattr(main.fifo_manager, "stop_watchdog", lambda: None)
    monkeypatch.setattr(main.bus, "set_loop", lambda _loop: None)
    monkeypatch.setattr(main, "get_backend", lambda: TmuxBackend())
    monkeypatch.setattr(main, "_run_registered_work_dispatcher", never_returns)


def _make_authority_and_provision(
    repository: WorkRepository,
    tmp_path: Path,
    *,
    tier: str,
    run_id: str,
    workflow_id: str,
    step_id: str,
    spec_hash: str,
    worker_bytes: bytes,
    worker_sha256: str,
    delivery_message: str,
):
    owner = auth._verified_principal("https://issuer.test", "t020-owner", [auth.SCOPE_ADMIN], "jwt")
    workflow_subject = auth._verified_principal(
        "https://issuer.test", f"t020-{tier}-workflow", [auth.SCOPE_WRITE], "jwt"
    )
    receiver = auth._verified_principal(
        "https://issuer.test", f"t020-{tier}-receiver", [auth.SCOPE_WRITE], "jwt"
    )
    provider = "scratch_worker"
    job = repository.create_job(
        project_id=f"t020-{tier}-project",
        principal_id=owner.id,
        allowed_providers=[provider],
        grant_id=f"t020-{tier}-root",
        budget={"scheduler_units": 20},
    )
    authority = WorkAuthority(repository)
    root_tools = {"knowledge.read", "tool.read", *_WORKFLOW_TOOLS}
    root = authority.issue_root(
        owner,
        job_id=job["id"],
        providers={provider},
        permissions=Permissions(
            tools=root_tools,
            commands={_WORKER_COMMAND},
        ),
        expires_at=time.time() + 900,
    )
    delegated_permissions = Permissions(
        tools=root_tools,
        commands={_WORKER_COMMAND},
    )
    workflow_grant = authority.delegate(
        owner,
        parent_grant_id=root.id,
        expected_parent_revision=root.revision,
        child_principal=workflow_subject,
        providers={provider},
        permissions=delegated_permissions,
        expires_at=time.time() + 800,
    )
    receiver_grant = authority.delegate(
        owner,
        parent_grant_id=root.id,
        expected_parent_revision=root.revision,
        child_principal=receiver,
        providers={provider},
        permissions=delegated_permissions,
        expires_at=time.time() + 800,
    )

    origin_authority = WorkOriginAuthority(repository)
    workflow_subject_ref = origin_authority.register_subject(
        owner,
        verified_subject=workflow_subject,
        kind="workflow",
        issuer_id=owner.id,
        expected_revision=0,
    )
    workflow_authorization = origin_authority.authorize(
        owner,
        subject=workflow_subject,
        origin_kind="workflow",
        grant_id=workflow_grant.id,
        grant_revision=workflow_grant.revision,
        actions={"admit_step", "execute"},
        expires_at=time.time() + 700,
        expected_revision=0,
    )
    receiver_subject_ref = origin_authority.register_subject(
        owner,
        verified_subject=receiver,
        kind="receiver",
        issuer_id=owner.id,
        expected_revision=0,
    )
    receiver_authorization = origin_authority.authorize(
        owner,
        subject=receiver,
        origin_kind="receiver",
        grant_id=receiver_grant.id,
        grant_revision=receiver_grant.revision,
        actions={"task_received", "task_result"},
        expires_at=time.time() + 700,
        expected_revision=0,
    )

    contract_id = f"t020-{tier}-agent-step"
    context = DelegationSnapshots(
        repository,
        policy=KnowledgePolicy(repository, job["id"], workflow_grant.id, workflow_grant.revision),
    ).freeze(
        principal=workflow_subject,
        job_id=job["id"],
        contract_id=contract_id,
        binding_key=f"{workflow_id}-{step_id}-context",
        request_hash=hashlib.sha256(f"t020:{tier}:{run_id}".encode()).hexdigest(),
        scope="project",
        scope_id=job["project_id"],
        resolver=lambda _connection, _principal: ResolvedSnapshot(_CONTEXT_TEXT),
    )
    executable = identify_static_executable(_WORKER_COMMAND, worker_bytes)
    assert executable.sha256_digest == worker_sha256
    WorkExecutableContent(repository).publish(executable, worker_bytes)
    contract = EffectiveWorkContractV2(
        id=contract_id,
        operation_kind="agent_step",
        provider=provider,
        backend="docker-local",
        permissions=ContractPermissions(
            tools=_WORKFLOW_TOOLS,
            commands=(_WORKER_COMMAND,),
        ),
        resources=ContractResources(checkout_root=str(tmp_path), units=1),
        snapshot=ContractSnapshot(
            state="present", id=context.id, delivered_hash=context.delivered_hash
        ),
        executable_identities=(executable,),
    )
    delivery = WorkDeliveryEnvelope(
        operation_kind="agent_step",
        adapter_version=2,
        payload_json=json.dumps(
            {"agent_profile": "developer", "message": delivery_message},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    provision_ref = WorkProvisioning(repository).provision_workflow_step(
        owner,
        subject=workflow_subject,
        workflow_id=workflow_id,
        step_id=step_id,
        expected_revision=0,
        spec_hash=spec_hash,
        job_id=job["id"],
        grant_id=workflow_grant.id,
        grant_revision=workflow_grant.revision,
        subject_ref=workflow_subject_ref,
        authorization_ref=workflow_authorization.ref,
        receiver_subject_ref=receiver_subject_ref,
        receiver_authorization_ref=receiver_authorization.ref,
        contract=contract,
        delivery=delivery,
        adapter_version=2,
        lease_seconds=300,
    )
    return {
        "owner": owner,
        "principal": workflow_subject,
        "workflow_id": workflow_id,
        "step_id": step_id,
        "spec_hash": spec_hash,
        "snapshot": context,
        "provision_ref": provision_ref,
    }


def _install_api_state(monkeypatch, *, principal):
    from cli_agent_orchestrator.api import main as api_main

    saved = {
        name: getattr(api_main.app.state, name, _MISSING)
        for name in (
            "durable_launch_gateway",
            "work_workflow_origins",
            "work_workflow_result_service",
            "workflow_step_projector",
        )
    }
    previous_override = api_main.app.dependency_overrides.get(
        api_main.get_current_principal, _MISSING
    )
    api_main.app.dependency_overrides[api_main.get_current_principal] = lambda: principal
    _install_lifespan_sandbox(monkeypatch)
    client = TestClient(
        api_main.app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )
    client.__enter__()
    client._t020_active = True
    gateway = api_main.app.state.durable_launch_gateway
    runtime = gateway._launch_runtime_provider._runtime
    origins = api_main.app.state.work_workflow_origins
    projector = api_main.app.state.workflow_step_projector
    assert projector.workflow_origins is origins
    assert runtime._admission.workflow_origins is origins
    assert runtime._admission.deliveries.adapters[("agent_step", 1)].payload_model.__name__ == (
        "AgentStepPayload"
    )
    assert runtime._admission.deliveries.adapters[("agent_step", 2)].payload_model.__name__ == (
        "ProcessAgentStepPayloadV2"
    )
    assert work_registry.WORK_BACKENDS == {}
    monkeypatch.setenv("CAO_T020_ACCEPTANCE", "1")
    return client, saved, previous_override, origins, runtime


def _restore_api_state(client, saved, previous_override):
    if getattr(client, "_t020_active", False):
        client.__exit__(None, None, None)
        client._t020_active = False
    for name, value in saved.items():
        if value is _MISSING:
            if hasattr(main.app.state, name):
                delattr(main.app.state, name)
        else:
            setattr(main.app.state, name, value)
    if previous_override is _MISSING:
        main.app.dependency_overrides.pop(main.get_current_principal, None)
    else:
        main.app.dependency_overrides[main.get_current_principal] = previous_override


def _binding_proof(binding):
    return tuple(
        getattr(binding, field)
        for field in (
            "binding_id",
            "binding_fingerprint",
            "tier",
            "run_id",
            "run_generation",
            "workflow_id",
            "spec_hash",
            "step_id",
            "workflow_step_attempt",
            "provision_id",
            "provision_revision",
            "provision_fingerprint",
            "contract_hash",
            "snapshot_id",
            "snapshot_hash",
            "delivery_id",
            "delivery_hash",
            "work_item_id",
            "work_attempt_id",
            "work_generation",
        )
    )


def _snapshot_proof(repository, snapshot_id):
    with repository.read_snapshot() as connection:
        row = connection.execute(
            "SELECT id,source_hash,delivered_hash,content FROM work_delegation_snapshots WHERE id=?",
            (snapshot_id,),
        ).fetchone()
    assert row is not None
    return row["id"], row["source_hash"], row["delivered_hash"], bytes(row["content"])


def _receiver_receipt_proof(repository, attempt_id, generation):
    with repository.read_snapshot() as connection:
        row = connection.execute(
            "SELECT delivery_id,delivery_hash FROM work_workflow_step_task_received_receipts "
            "WHERE attempt_id=? AND generation=?",
            (attempt_id, generation),
        ).fetchone()
    if row is None:
        return None
    return row["delivery_id"], row["delivery_hash"]


def _step_call_body(run_id, step_id, prompt):
    return {
        "provider": "scratch_worker",
        "agent": "developer",
        "prompt": prompt,
        "timeout": 60,
        "env_vars": {
            "CAO_WORKFLOW_RUN_ID": run_id,
            "CAO_WORKFLOW_GENERATION": "1",
            "CAO_WORKFLOW_STEP_ID": step_id,
        },
    }


def _insert_script_run(run_id, workflow_id, source):
    snapshot = json.dumps(
        {
            "source": source,
            "path": f"{workflow_id}.py",
            "content_hash": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        }
    )
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    workflow_journal.insert_run(
        run_id,
        workflow_id,
        snapshot,
        "{}",
        "running",
        now,
        "script",
        "1",
    )
    return snapshot


@pytest.fixture
def docker_workflow_harness(tmp_path, monkeypatch):
    _require_opt_in()
    worker_path = os.environ.get("CAO_T020_WORKER_BINARY")
    worker_sha256 = os.environ.get("CAO_T020_WORKER_SHA256")
    if not worker_path or not worker_sha256:
        pytest.skip("T020 runner did not provide its compiled, digest-pinned worker")
    worker_bytes = Path(worker_path).read_bytes()
    assert hashlib.sha256(worker_bytes).hexdigest() == worker_sha256

    database = tmp_path / "t020-workflow.sqlite3"
    monkeypatch.setattr(constants, "DATABASE_FILE", database)
    _migrate_workflow_run()
    _migrate_workflow_run_step()
    repository = WorkRepository(database)
    repository.initialize()
    WorkScheduler(repository).configure(
        capacity=2, max_queue=20, aging_seconds=5, expected_policy_revision=0
    )
    process_inputs = _capture_lifespan_backend(monkeypatch)
    monkeypatch.setenv("CAO_ENABLE_PUBLIC_WORK_INGRESS", "true")
    return repository, worker_bytes, worker_sha256, process_inputs


@pytest.mark.integration
@pytest.mark.asyncio
async def test_yaml_docker_work_result_projects_after_restart_without_changing_frozen_inputs(
    docker_workflow_harness, tmp_path, monkeypatch
):
    repository, worker_bytes, worker_sha256, process_inputs = docker_workflow_harness
    workflow_id = "t020_yaml_workflow"
    run_id = "t020-yaml-success"
    step_id = "worker"
    prompt = "YAML step input retained in the run snapshot."
    spec = WorkflowSpec(
        name=workflow_id,
        steps=[
            WorkflowStep(id=step_id, provider="scratch_worker", agent="developer", prompt=prompt)
        ],
    )
    spec_snapshot = spec.model_dump_json()
    spec_hash = hashlib.sha256(spec_snapshot.encode("utf-8")).hexdigest()
    case = _make_authority_and_provision(
        repository,
        tmp_path,
        tier="yaml",
        run_id=run_id,
        workflow_id=workflow_id,
        step_id=step_id,
        spec_hash=spec_hash,
        worker_bytes=worker_bytes,
        worker_sha256=worker_sha256,
        delivery_message=prompt,
    )
    from cli_agent_orchestrator.services import workflow_spec_service

    monkeypatch.setattr(workflow_spec_service, "get_workflow", lambda _name: spec)
    client, saved, previous_override, origins, runtime = _install_api_state(
        monkeypatch, principal=case["principal"]
    )
    try:
        started = client.post(
            "/workflows/runs",
            headers={"Host": "localhost"},
            json={"name_or_path": workflow_id, "run_id": run_id, "inputs": {}},
        )
        assert started.status_code == 200, started.text
        assert started.json()["state"] == "running"
        journal_run = workflow_journal.get_run(run_id)
        journal_step = workflow_journal.get_step(run_id, step_id)
        assert journal_run is not None
        assert journal_run.spec_snapshot.encode("utf-8") == spec_snapshot.encode("utf-8")
        assert hashlib.sha256(journal_run.spec_snapshot.encode("utf-8")).hexdigest() == spec_hash
        assert journal_run.generation == "1" and journal_run.finished_at is None
        assert journal_step is not None and journal_step.state == "work_pending"
        assert journal_step.attempts == 1

        binding_before = origins.read_step_binding("yaml", run_id, 1, step_id, 1)
        assert binding_before is not None
        assert binding_before.spec_hash == spec_hash
        assert binding_before.snapshot_id == case["snapshot"].id
        assert binding_before.snapshot_hash == case["snapshot"].delivered_hash
        proof_before = _binding_proof(binding_before)
        snapshot_before = _snapshot_proof(repository, binding_before.snapshot_id)
        assert snapshot_before[2] == hashlib.sha256(snapshot_before[3]).hexdigest()
        assert snapshot_before[3] == _CONTEXT_TEXT.encode("utf-8")

        executed = await runtime._admission.dispatch_registered_next()
        assert executed is not None
        assert process_inputs == [_CONTEXT_TEXT.encode("utf-8") + b"\n\n" + prompt.encode("utf-8")]
        work = repository.get_work(binding_before.work_item_id)
        assert work["state"] == "succeeded"
        assert work["accepted_result_id"]
        receiver_receipt_before = _receiver_receipt_proof(
            repository, binding_before.work_attempt_id, binding_before.work_generation
        )
        assert receiver_receipt_before is not None

        # Drop process-local workflow state and rebuild a fresh registry/runtime
        # over the same durable stores before projection and YAML recovery.
        _restore_api_state(client, saved, previous_override)
        workflow_service.run_registry.pop(run_id, None)
        restarted_repository = WorkRepository(repository.path)
        restarted_repository.initialize()
        client2, saved2, override2, restarted_origins, restarted_runtime = _install_api_state(
            monkeypatch, principal=case["principal"]
        )
        try:
            resumed = client2.post(
                f"/workflows/runs/{run_id}/resume", headers={"Host": "localhost"}, json={}
            )
            assert resumed.status_code == 200, resumed.text
            assert resumed.json()["state"] == "completed"
            recovered = restarted_origins.read_step_binding("yaml", run_id, 1, step_id, 1)
            assert recovered is not None
            assert _binding_proof(recovered) == proof_before
            assert _snapshot_proof(restarted_repository, recovered.snapshot_id) == snapshot_before
            assert (
                _receiver_receipt_proof(
                    restarted_repository, recovered.work_attempt_id, recovered.work_generation
                )
                == receiver_receipt_before
            )
            current = workflow_journal.get_run(run_id)
            assert current is not None and current.spec_snapshot.encode("utf-8") == (
                spec_snapshot.encode("utf-8")
            )
            assert current.generation == "1"
            assert workflow_journal.get_step(run_id, step_id).attempts == 1
            assert process_inputs == [
                _CONTEXT_TEXT.encode("utf-8") + b"\n\n" + prompt.encode("utf-8")
            ]
            assert await restarted_runtime._admission.dispatch_registered_next() is None
        finally:
            _restore_api_state(client2, saved2, override2)
    finally:
        _restore_api_state(client, saved, previous_override)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_script_run_step_returns_only_verified_work_result_after_runtime_restart(
    docker_workflow_harness, tmp_path, monkeypatch
):
    repository, worker_bytes, worker_sha256, process_inputs = docker_workflow_harness
    workflow_id = "t020_script_workflow"
    run_id = "t020-script-success"
    step_id = "worker"
    prompt = "Script step input retained in the run snapshot."
    source = (
        "from cao_workflow import run_step\n"
        f"run_step('scratch_worker', 'developer', {prompt!r}, step_id={step_id!r})\n"
    )
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    spec_snapshot = _insert_script_run(run_id, workflow_id, source)
    case = _make_authority_and_provision(
        repository,
        tmp_path,
        tier="script",
        run_id=run_id,
        workflow_id=workflow_id,
        step_id=step_id,
        spec_hash=source_hash,
        worker_bytes=worker_bytes,
        worker_sha256=worker_sha256,
        delivery_message=prompt,
    )
    client, saved, previous_override, origins, runtime = _install_api_state(
        monkeypatch, principal=case["principal"]
    )
    try:
        capability = origins.create_run_capability(
            case["principal"],
            run_id=run_id,
            workflow_id=workflow_id,
            tier="script",
            run_generation=1,
            spec_hash=source_hash,
        )
        body = _step_call_body(run_id, step_id, prompt)
        headers = {
            "Host": "localhost",
            "X-CAO-Workflow-Run-Credential": capability,
        }
        pending = client.post("/terminals/run-step", headers=headers, json=body)
        assert pending.status_code == 409, pending.text
        assert pending.json()["detail"]["kind"] == "work_pending"
        binding_before = origins.read_step_binding("script", run_id, 1, step_id, 1)
        assert binding_before is not None
        assert binding_before.spec_hash == source_hash
        proof_before = _binding_proof(binding_before)
        snapshot_before = _snapshot_proof(repository, binding_before.snapshot_id)
        run_before = workflow_journal.get_run(run_id)
        assert run_before is not None and run_before.spec_snapshot == spec_snapshot
        frozen_script = json.loads(run_before.spec_snapshot)["source"].encode("utf-8")
        assert frozen_script == source.encode("utf-8")
        assert hashlib.sha256(frozen_script).hexdigest() == binding_before.spec_hash
        assert snapshot_before[2] == hashlib.sha256(snapshot_before[3]).hexdigest()
        assert snapshot_before[3] == _CONTEXT_TEXT.encode("utf-8")

        await runtime._admission.dispatch_registered_next()
        assert process_inputs == [_CONTEXT_TEXT.encode("utf-8") + b"\n\n" + prompt.encode("utf-8")]
        work = repository.get_work(binding_before.work_item_id)
        assert work["state"] == "succeeded" and work["accepted_result_id"]
        receiver_receipt_before = _receiver_receipt_proof(
            repository, binding_before.work_attempt_id, binding_before.work_generation
        )
        assert receiver_receipt_before is not None

        # A restarted API authenticates the same durable run capability and
        # returns a typed Work result, with no terminal delivery or new attempt.
        _restore_api_state(client, saved, previous_override)
        workflow_service.run_registry.pop(run_id, None)
        restarted_repository = WorkRepository(repository.path)
        restarted_repository.initialize()
        client2, saved2, override2, origins2, runtime2 = _install_api_state(
            monkeypatch, principal=case["principal"]
        )
        try:
            accepted = client2.post("/terminals/run-step", headers=headers, json=body)
            assert accepted.status_code == 200, accepted.text
            result = accepted.json()
            assert result["status"] == "completed"
            assert result["work_result"] == {
                "schema_version": 1,
                "status": "completed",
                "output": {"value": "t122-deterministic"},
            }
            recovered = origins2.read_step_binding("script", run_id, 1, step_id, 1)
            assert recovered is not None and _binding_proof(recovered) == proof_before
            assert _snapshot_proof(restarted_repository, recovered.snapshot_id) == snapshot_before
            assert (
                _receiver_receipt_proof(
                    restarted_repository, recovered.work_attempt_id, recovered.work_generation
                )
                == receiver_receipt_before
            )
            assert workflow_journal.get_run(run_id).spec_snapshot == spec_snapshot
            assert workflow_journal.get_run(run_id).generation == "1"
            assert workflow_journal.get_step(run_id, step_id).attempts == 1
            assert process_inputs == [
                _CONTEXT_TEXT.encode("utf-8") + b"\n\n" + prompt.encode("utf-8")
            ]
            assert await runtime2._admission.dispatch_registered_next() is None
        finally:
            _restore_api_state(client2, saved2, override2)
    finally:
        _restore_api_state(client, saved, previous_override)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["yaml", "script"])
async def test_no_result_stays_pending_after_restart_without_step_or_work_retry(
    tier, docker_workflow_harness, tmp_path, monkeypatch
):
    repository, worker_bytes, worker_sha256, process_inputs = docker_workflow_harness
    workflow_id = f"t020_{tier}_pending"
    run_id = f"t020-{tier}-pending"
    step_id = "worker"
    prompt = "input that is deliberately not followed by a result"
    if tier == "yaml":
        spec = WorkflowSpec(
            name=workflow_id,
            steps=[
                WorkflowStep(
                    id=step_id, provider="scratch_worker", agent="developer", prompt=prompt
                )
            ],
        )
        spec_snapshot = spec.model_dump_json()
        spec_hash = hashlib.sha256(spec_snapshot.encode("utf-8")).hexdigest()
        from cli_agent_orchestrator.services import workflow_spec_service

        monkeypatch.setattr(workflow_spec_service, "get_workflow", lambda _name: spec)
    else:
        source = (
            "from cao_workflow import run_step\n"
            f"run_step('scratch_worker', 'developer', {prompt!r}, step_id={step_id!r})\n"
        )
        spec_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        spec_snapshot = _insert_script_run(run_id, workflow_id, source)

    case = _make_authority_and_provision(
        repository,
        tmp_path,
        tier=tier,
        run_id=run_id,
        workflow_id=workflow_id,
        step_id=step_id,
        spec_hash=spec_hash,
        worker_bytes=worker_bytes,
        worker_sha256=worker_sha256,
        delivery_message="T122_OMIT_RESULT",
    )
    client, saved, previous_override, origins, runtime = _install_api_state(
        monkeypatch, principal=case["principal"]
    )
    try:
        if tier == "yaml":
            started = client.post(
                "/workflows/runs",
                headers={"Host": "localhost"},
                json={"name_or_path": workflow_id, "run_id": run_id, "inputs": {}},
            )
            assert started.status_code == 200, started.text
            assert started.json()["state"] == "running"
        else:
            capability = origins.create_run_capability(
                case["principal"],
                run_id=run_id,
                workflow_id=workflow_id,
                tier="script",
                run_generation=1,
                spec_hash=spec_hash,
            )
            headers = {
                "Host": "localhost",
                "X-CAO-Workflow-Run-Credential": capability,
            }
            body = _step_call_body(run_id, step_id, prompt)
            pending = client.post("/terminals/run-step", headers=headers, json=body)
            assert pending.status_code == 409, pending.text
            assert pending.json()["detail"]["kind"] == "work_pending"

        binding_before = origins.read_step_binding(tier, run_id, 1, step_id, 1)
        assert binding_before is not None
        assert binding_before.spec_hash == spec_hash
        proof_before = _binding_proof(binding_before)
        snapshot_before = _snapshot_proof(repository, binding_before.snapshot_id)
        assert snapshot_before[2] == hashlib.sha256(snapshot_before[3]).hexdigest()
        journal_run = workflow_journal.get_run(run_id)
        assert journal_run is not None and journal_run.spec_snapshot == spec_snapshot
        assert journal_run.generation == "1" and journal_run.finished_at is None
        assert workflow_journal.get_step(run_id, step_id).state == "work_pending"
        assert workflow_journal.get_step(run_id, step_id).attempts == 1

        await runtime._admission.dispatch_registered_next()
        assert process_inputs == [_CONTEXT_TEXT.encode("utf-8") + b"\n\nT122_OMIT_RESULT"]
        work_before_restart = repository.get_work(binding_before.work_item_id)
        assert work_before_restart["accepted_result_id"] is None
        receiver_receipt_before = _receiver_receipt_proof(
            repository, binding_before.work_attempt_id, binding_before.work_generation
        )
        assert receiver_receipt_before is not None
        assert work_before_restart["attempts"][0]["id"] == binding_before.work_attempt_id
        assert work_before_restart["attempts"][0]["generation"] == binding_before.work_generation

        _restore_api_state(client, saved, previous_override)
        workflow_service.run_registry.pop(run_id, None)
        restarted_repository = WorkRepository(repository.path)
        restarted_repository.initialize()
        client2, saved2, override2, origins2, runtime2 = _install_api_state(
            monkeypatch, principal=case["principal"]
        )
        try:
            if tier == "yaml":
                recovered_response = client2.post(
                    f"/workflows/runs/{run_id}/resume",
                    headers={"Host": "localhost"},
                    json={},
                )
            else:
                recovered_response = client2.post("/terminals/run-step", headers=headers, json=body)
            if tier == "yaml":
                assert recovered_response.status_code == 200, recovered_response.text
                assert recovered_response.json()["state"] == "running"
                assert recovered_response.json()["steps"][0]["state"] == "work_pending"
                assert recovered_response.json()["steps"][0]["attempts"] == 1
            else:
                assert recovered_response.status_code == 409, recovered_response.text
                assert recovered_response.json()["detail"]["kind"] == "work_pending"

            recovered_binding = origins2.read_step_binding(tier, run_id, 1, step_id, 1)
            assert recovered_binding is not None
            assert _binding_proof(recovered_binding) == proof_before
            assert _snapshot_proof(restarted_repository, recovered_binding.snapshot_id) == (
                snapshot_before
            )
            recovered_run = workflow_journal.get_run(run_id)
            recovered_step = workflow_journal.get_step(run_id, step_id)
            assert recovered_run is not None and recovered_run.spec_snapshot == spec_snapshot
            if tier == "yaml":
                recovered_source = recovered_run.spec_snapshot.encode("utf-8")
            else:
                recovered_source = json.loads(recovered_run.spec_snapshot)["source"].encode("utf-8")
            assert hashlib.sha256(recovered_source).hexdigest() == spec_hash
            assert recovered_run.generation == "1" and recovered_run.finished_at is None
            assert recovered_run.state == "running"
            assert recovered_step.state == "work_pending" and recovered_step.attempts == 1
            recovered_work = restarted_repository.get_work(binding_before.work_item_id)
            assert len(recovered_work["attempts"]) == 1
            assert recovered_work["attempts"][0]["id"] == binding_before.work_attempt_id
            assert recovered_work["attempts"][0]["generation"] == binding_before.work_generation
            assert recovered_work["accepted_result_id"] is None
            assert (
                _receiver_receipt_proof(
                    restarted_repository,
                    binding_before.work_attempt_id,
                    binding_before.work_generation,
                )
                == receiver_receipt_before
            )
            assert process_inputs == [_CONTEXT_TEXT.encode("utf-8") + b"\n\nT122_OMIT_RESULT"]
            assert await runtime2._admission.dispatch_registered_next() is None
        finally:
            _restore_api_state(client2, saved2, override2)
    finally:
        _restore_api_state(client, saved, previous_override)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["yaml", "script"])
async def test_docker_process_failure_requires_explicit_fenced_workflow_retry(
    tier, docker_workflow_harness, tmp_path, monkeypatch
):
    """A real nonzero Docker exit projects failure and cannot retry on restart."""
    repository, worker_bytes, worker_sha256, process_inputs = docker_workflow_harness
    workflow_id = f"t020_{tier}_failure_retry"
    run_id = f"t020-{tier}-failure-retry"
    step_id = "worker"
    prompt = "exercise the exact failed Docker process"
    if tier == "yaml":
        spec = WorkflowSpec(
            name=workflow_id,
            steps=[
                WorkflowStep(
                    id=step_id,
                    provider="scratch_worker",
                    agent="developer",
                    prompt=prompt,
                    retries=1,
                )
            ],
        )
        spec_snapshot = spec.model_dump_json()
        spec_hash = hashlib.sha256(spec_snapshot.encode("utf-8")).hexdigest()
        from cli_agent_orchestrator.services import workflow_spec_service

        monkeypatch.setattr(workflow_spec_service, "get_workflow", lambda _name: spec)
    else:
        source = (
            "from cao_workflow import run_step\n"
            f"run_step('scratch_worker', 'developer', {prompt!r}, step_id={step_id!r})\n"
        )
        spec_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        spec_snapshot = _insert_script_run(run_id, workflow_id, source)

    case = _make_authority_and_provision(
        repository,
        tmp_path,
        tier=tier,
        run_id=run_id,
        workflow_id=workflow_id,
        step_id=step_id,
        spec_hash=spec_hash,
        worker_bytes=worker_bytes,
        worker_sha256=worker_sha256,
        delivery_message="T122_FAIL",
    )
    primary = _install_api_state(monkeypatch, principal=case["principal"])
    try:
        client, saved, override, origins, runtime = primary
        if tier == "yaml":
            started = client.post(
                "/workflows/runs",
                headers={"Host": "localhost"},
                json={"name_or_path": workflow_id, "run_id": run_id, "inputs": {}},
            )
            assert started.status_code == 200, started.text
            assert started.json()["state"] == "running"
        else:
            capability = origins.create_run_capability(
                case["principal"],
                run_id=run_id,
                workflow_id=workflow_id,
                tier="script",
                run_generation=1,
                spec_hash=spec_hash,
            )
            headers = {
                "Host": "localhost",
                "X-CAO-Workflow-Run-Credential": capability,
            }
            body = _step_call_body(run_id, step_id, prompt)
            admitted = client.post("/terminals/run-step", headers=headers, json=body)
            assert admitted.status_code == 409, admitted.text
            assert admitted.json()["detail"]["kind"] == "work_pending"

        binding = origins.read_step_binding(tier, run_id, 1, step_id, 1)
        assert binding is not None
        assert workflow_journal.get_step(run_id, step_id).state == "work_pending"
        failed = await runtime._admission.dispatch_registered_next()
        assert failed["id"] == binding.work_item_id
        assert failed["state"] == "failed"
        assert failed["attempts"][-1]["id"] == binding.work_attempt_id
        assert failed["attempts"][-1]["generation"] == binding.work_generation
        assert failed["attempts"][-1]["cleanup_state"] == "complete"
        assert failed["accepted_result_id"] is None
        assert process_inputs == [_CONTEXT_TEXT.encode("utf-8") + b"\n\nT122_FAIL"]
        failure_event = next(
            event
            for event in repository.read_events(failed["job_id"])["events"]
            if event["event_type"] == "attempt.failed"
        )
        assert failure_event["metadata"] == {
            "process_exit_code": 23,
            "process_stopped": True,
            "container_removed": True,
            "image_removed": True,
        }
        projection = main.app.state.workflow_step_projector.project_pending_for_run(run_id)
        assert [(item.step_id, item.status) for item in projection] == [(step_id, "failed")]
        failed_step = workflow_journal.get_step(run_id, step_id)
        assert failed_step.state == "failed"
        assert failed_step.attempts == 1
        assert failed_step.error_kind == "managed_work_failed"

        _restore_api_state(client, saved, override)
        primary = None
        workflow_service.run_registry.pop(run_id, None)
        restarted_repository = WorkRepository(repository.path)
        restarted_repository.initialize()
        client2, saved2, override2, origins2, runtime2 = _install_api_state(
            monkeypatch, principal=case["principal"]
        )
        primary = (client2, saved2, override2, origins2, runtime2)
        recovered_binding = origins2.read_step_binding(tier, run_id, 1, step_id, 1)
        assert recovered_binding is not None
        assert _binding_proof(recovered_binding) == _binding_proof(binding)
        assert origins2.read_step_binding(tier, run_id, 1, step_id, 2) is None
        recovered_step = workflow_journal.get_step(run_id, step_id)
        assert recovered_step.state == "failed" and recovered_step.attempts == 1
        assert await runtime2._admission.dispatch_registered_next() is None
        assert process_inputs == [_CONTEXT_TEXT.encode("utf-8") + b"\n\nT122_FAIL"]
        assert restarted_repository.get_work(binding.work_item_id)["attempts"][-1]["state"] == (
            "failed"
        )

        retry_principal = auth._verified_principal(
            "https://issuer.test",
            f"t020-{tier}-workflow",
            [auth.SCOPE_WRITE, auth.SCOPE_ADMIN],
            "jwt",
        )
        assert retry_principal.id == case["principal"].id
        main.app.dependency_overrides[main.get_current_principal] = lambda: retry_principal
        retry_body = {
            "run_generation": 1,
            "workflow_step_attempt": 1,
            "work_attempt_id": binding.work_attempt_id,
            "work_generation": binding.work_generation,
        }
        response = client2.post(
            f"/workflows/runs/{run_id}/steps/{step_id}/retry",
            headers={"Host": "localhost"},
            json=retry_body,
        )
        assert response.status_code == 202, response.text
        admitted_retry = response.json()
        assert admitted_retry["state"] == "retry_admitted"
        assert admitted_retry["workflow_step_attempt"] == 2
        assert admitted_retry["work_attempt_id"] != binding.work_attempt_id
        replayed_retry = client2.post(
            f"/workflows/runs/{run_id}/steps/{step_id}/retry",
            headers={"Host": "localhost"},
            json=retry_body,
        )
        assert replayed_retry.status_code == 202, replayed_retry.text
        assert replayed_retry.json() == admitted_retry

        retry_binding = origins2.read_step_binding(tier, run_id, 1, step_id, 2)
        assert retry_binding is not None
        assert retry_binding.binding_id != binding.binding_id
        assert retry_binding.work_item_id != binding.work_item_id
        assert retry_binding.work_attempt_id == admitted_retry["work_attempt_id"]
        assert workflow_journal.get_step(run_id, step_id).attempts == 2
        failed_retry = await runtime2._admission.dispatch_registered_next()
        assert failed_retry["id"] == retry_binding.work_item_id
        assert failed_retry["state"] == "failed"
        assert failed_retry["attempts"][-1]["cleanup_state"] == "complete"
        assert len(process_inputs) == 2
        assert process_inputs[-1] == _CONTEXT_TEXT.encode("utf-8") + b"\n\nT122_FAIL"
        retry_projection = main.app.state.workflow_step_projector.project_pending_for_run(run_id)
        assert [(item.step_id, item.status) for item in retry_projection] == [(step_id, "failed")]
        final_step = workflow_journal.get_step(run_id, step_id)
        assert final_step.state == "failed" and final_step.attempts == 2
        assert final_step.error_kind == "managed_work_failed"
    finally:
        if primary is not None:
            _restore_api_state(primary[0], primary[1], primary[2])
