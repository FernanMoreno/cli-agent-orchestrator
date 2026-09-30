"""Durable Work origins for explicitly managed YAML/script workflow steps."""

import asyncio
import hashlib
import json
import time
from test.integration.test_work_dispatch import AdmissionOnlyBackend, context  # noqa: F401

import pytest

from cli_agent_orchestrator.models.work_contract import (
    ContractPermissions,
    ContractResources,
    ContractSnapshot,
    EffectiveWorkContract,
)
from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    ResolvedSnapshot,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.work_authority import Permissions
from cli_agent_orchestrator.services.work_origin import OriginDenied, WorkOriginAuthority
from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning
from cli_agent_orchestrator.services.work_scheduler import WorkScheduler
from cli_agent_orchestrator.services.work_workflow import WorkWorkflowOrigins


def _managed_workflow(context, *, provision=True):
    job, root = context.jobs[0]
    subject = auth._verified_principal("issuer", "workflow-subject", [auth.SCOPE_WRITE], "jwt")
    grant = context.control.delegate(
        context.actor,
        parent_grant_id=root.id,
        expected_parent_revision=root.revision,
        child_principal=subject,
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read"}, paths={str(context.root)}),
        expires_at=time.time() + 300,
    )
    authority = WorkOriginAuthority(context.repo)
    subject_ref = authority.register_subject(
        context.actor,
        verified_subject=subject,
        kind="workflow",
        issuer_id=context.actor.id,
        expected_revision=0,
    )
    authorization = authority.authorize(
        context.actor,
        subject=subject,
        origin_kind="workflow",
        grant_id=grant.id,
        grant_revision=grant.revision,
        actions={"admit_step", "execute"},
        expires_at=time.time() + 240,
        expected_revision=0,
    )
    origins = WorkWorkflowOrigins(context.repo)
    context.service = __import__(
        "cli_agent_orchestrator.services.work_admission", fromlist=["WorkAdmission"]
    ).WorkAdmission(
        context.repo,
        backends={"test": AdmissionOnlyBackend()},
        delivery_adapters={
            ("agent_step", 1): __import__(
                "cli_agent_orchestrator.services.work_agent_step", fromlist=["agent_step_adapter"]
            ).agent_step_adapter()
        },
        workflow_origins=origins,
    )
    snapshot = DelegationSnapshots(
        context.repo,
        policy=KnowledgePolicy(context.repo, job["id"], grant.id, grant.revision),
    ).freeze(
        principal=subject,
        job_id=job["id"],
        contract_id="workflow-agent-contract",
        binding_key="workflow-agent-snapshot",
        request_hash=hashlib.sha256(b"workflow-snapshot").hexdigest(),
        scope="project",
        scope_id="project",
        resolver=lambda _connection, _principal: ResolvedSnapshot("frozen workflow context"),
    )
    contract = EffectiveWorkContract(
        id="workflow-agent-contract",
        operation_kind="agent_step",
        provider="mock_cli",
        backend="test",
        permissions=ContractPermissions(paths=(str(context.root),)),
        resources=ContractResources(
            checkout_root=str(context.root),
            write_paths=(str(context.root / "workflow-step"),),
            units=1,
        ),
        snapshot=ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
    )
    delivery = WorkDeliveryEnvelope(
        operation_kind="agent_step",
        adapter_version=1,
        payload_json=json.dumps(
            {
                "terminal_id": "a1b2c3d4",
                "agent_profile": "developer",
                "message": "execute this workflow step",
            },
            separators=(",", ":"),
        ),
    )
    spec_hash = hashlib.sha256(b"{}").hexdigest()
    from cli_agent_orchestrator.services import workflow_journal

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    workflow_journal.insert_run("workflow-run", "wf", "{}", "{}", "running", now, "yaml", "1")
    workflow_journal.insert_steps("workflow-run", [("build", "pending")], now)
    workflow_journal.update_run_current_step("workflow-run", "build")
    workflow_journal.mark_work_pending(
        run_id="workflow-run",
        step_id="build",
        generation="1",
        step_attempt=1,
        tier="yaml",
        updated_at=now,
    )
    if provision:
        receiver = auth._verified_principal(
            "issuer", "workflow-receiver", [auth.SCOPE_WRITE], "jwt"
        )
        receiver_grant = context.control.delegate(
            context.actor,
            parent_grant_id=root.id,
            expected_parent_revision=root.revision,
            child_principal=receiver,
            providers={"mock_cli"},
            permissions=Permissions(tools={"knowledge.read"}, paths={str(context.root)}),
            expires_at=time.time() + 200,
        )
        receiver_authority = WorkOriginAuthority(context.repo)
        receiver_ref = receiver_authority.register_subject(
            context.actor,
            verified_subject=receiver,
            kind="receiver",
            issuer_id=context.actor.id,
            expected_revision=0,
        )
        receiver_authorization = receiver_authority.authorize(
            context.actor,
            subject=receiver,
            origin_kind="receiver",
            grant_id=receiver_grant.id,
            grant_revision=receiver_grant.revision,
            actions={"task_received", "task_result"},
            expires_at=time.time() + 150,
            expected_revision=0,
        )
        WorkProvisioning(context.repo).provision_workflow_step(
            context.actor,
            subject=subject,
            workflow_id="wf",
            step_id="build",
            expected_revision=0,
            spec_hash=spec_hash,
            job_id=job["id"],
            grant_id=grant.id,
            grant_revision=grant.revision,
            subject_ref=subject_ref,
            authorization_ref=authorization.ref,
            receiver_subject_ref=receiver_ref,
            receiver_authorization_ref=receiver_authorization.ref,
            contract=contract,
            delivery=delivery,
            lease_seconds=300,
        )
    request = dict(
        principal=subject,
        workflow_id="wf",
        spec_hash=spec_hash,
        tier="yaml",
        subject_ref=subject_ref,
        authorization_ref=authorization.ref,
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=grant.revision,
        run_id="workflow-run",
        run_generation=1,
        step_id="build",
        step_attempt=1,
        contract=contract,
        delivery=delivery,
        lease_seconds=300,
    )
    return origins, authority, subject, authorization, request


def _provisioned_workflow(context):
    origins, authority, subject, authorization, request = _managed_workflow(
        context, provision=False
    )
    receiver = auth._verified_principal("issuer", "workflow-receiver", [auth.SCOPE_WRITE], "jwt")
    _job, root_grant = context.jobs[0]
    receiver_grant = context.control.delegate(
        context.actor,
        parent_grant_id=root_grant.id,
        expected_parent_revision=root_grant.revision,
        child_principal=receiver,
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read"}, paths={str(context.root)}),
        expires_at=time.time() + 200,
    )
    receiver_authority = WorkOriginAuthority(context.repo)
    receiver_ref = receiver_authority.register_subject(
        context.actor,
        verified_subject=receiver,
        kind="receiver",
        issuer_id=context.actor.id,
        expected_revision=0,
    )
    receiver_authorization = receiver_authority.authorize(
        context.actor,
        subject=receiver,
        origin_kind="receiver",
        grant_id=receiver_grant.id,
        grant_revision=receiver_grant.revision,
        actions={"task_received", "task_result"},
        expires_at=time.time() + 150,
        expected_revision=0,
    )
    spec_hash = request["spec_hash"]
    provision_ref = WorkProvisioning(context.repo).provision_workflow_step(
        context.actor,
        subject=subject,
        workflow_id="wf",
        step_id="build",
        expected_revision=0,
        spec_hash=spec_hash,
        job_id=request["job_id"],
        grant_id=request["grant_id"],
        grant_revision=request["grant_revision"],
        subject_ref=request["subject_ref"],
        authorization_ref=request["authorization_ref"],
        receiver_subject_ref=receiver_ref,
        receiver_authorization_ref=receiver_authorization.ref,
        contract=request["contract"],
        delivery=request["delivery"],
        lease_seconds=request["lease_seconds"],
    )
    selector = {
        "principal": subject,
        "workflow_id": "wf",
        "spec_hash": spec_hash,
        "tier": "yaml",
        "run_id": request["run_id"],
        "run_generation": request["run_generation"],
        "step_id": request["step_id"],
        "step_attempt": request["step_attempt"],
        "prompt": "execute this workflow step",
    }
    return origins, authority, subject, authorization, selector, provision_ref


def test_provisioned_admitter_binds_exact_run_step_and_recovers_same_work(context):
    origins, _authority, subject, _authorization, selector, provision_ref = _provisioned_workflow(
        context
    )

    admitter = origins.resolve_step_admitter(subject, "wf", selector["spec_hash"], "build")
    assert admitter is not None
    assert asyncio.iscoroutinefunction(admitter)
    first = asyncio.run(
        admitter(**{key: value for key, value in selector.items() if key != "principal"})
    )
    binding = origins.read_step_binding("yaml", "workflow-run", 1, "build", 1)

    assert binding is not None
    assert binding.provision_id == provision_ref.id
    assert binding.provision_revision == provision_ref.revision
    assert binding.spec_hash == selector["spec_hash"]
    assert binding.work_item_id == first["id"]
    assert binding.work_attempt_id == first["attempts"][0]["id"]
    assert binding.work_generation == first["attempts"][0]["generation"]

    replay = asyncio.run(
        admitter(**{key: value for key, value in selector.items() if key != "principal"})
    )
    recovered = origins.recover_step(
        subject,
        "wf",
        selector["spec_hash"],
        "yaml",
        "workflow-run",
        1,
        "build",
        1,
    )
    assert replay["id"] == recovered["id"] == first["id"]


def test_workflow_binding_write_failure_rolls_back_admission(context):
    from cli_agent_orchestrator.clients.work_repository import WorkConflict

    origins, _authority, _subject, _authorization, request = _managed_workflow(context)

    def fail_binding(_connection, _handoff, *, work, contract_binding):
        raise WorkConflict("injected workflow binding failure")

    origins._bind_admitted = fail_binding
    with pytest.raises(WorkConflict, match="injected workflow binding failure"):
        origins.admit_step(**request)

    with context.repo.read_snapshot() as connection:
        for table in (
            "work_items",
            "work_attempts",
            "work_dispatch_bindings",
            "work_delivery_orders",
            "work_scheduler_requests",
            "work_workflow_step_bindings",
        ):
            assert connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] == 0


def test_workflow_retry_authorization_requires_a_registered_binding_owner(context):
    origins, _authority, _subject, _authorization, request = _managed_workflow(context)
    first = origins.admit_step(**request)
    unregistered = auth._verified_principal(
        "issuer", "unregistered-retry-operator", [auth.SCOPE_WRITE], "jwt"
    )

    with pytest.raises(OriginDenied, match="no longer registered"):
        origins.authorize_step_retry(
            unregistered,
            workflow_id=request["workflow_id"],
            spec_hash=request["spec_hash"],
            tier=request["tier"],
            run_id=request["run_id"],
            run_generation=request["run_generation"],
            step_id=request["step_id"],
            workflow_step_attempt=request["step_attempt"],
            work_attempt_id=first["attempts"][0]["id"],
            work_generation=first["attempts"][0]["generation"],
        )


def test_workflow_retry_authorization_persists_and_replays_one_exact_failure(context):
    from cli_agent_orchestrator.services import workflow_journal
    from cli_agent_orchestrator.services.work_service import WorkService

    origins, _authority, subject, _authorization, request = _managed_workflow(context)
    work = origins.admit_step(**request)
    binding = origins.read_step_binding(
        request["tier"],
        request["run_id"],
        request["run_generation"],
        request["step_id"],
        request["step_attempt"],
    )
    with context.repo.transaction() as connection:
        connection.execute(
            "UPDATE work_items SET state='failed',revision=revision+1 WHERE id=?",
            (work["id"],),
        )
        connection.execute(
            "UPDATE work_attempts SET state='failed',revision=revision+1 WHERE id=?",
            (binding.work_attempt_id,),
        )

    work_service = WorkService(context.repo)
    work_state = work_service.read_workflow_step_state(binding)
    assert (
        workflow_journal.project_work_failure(
            repository=context.repo,
            run_id=request["run_id"],
            run_generation=request["run_generation"],
            tier=request["tier"],
            step_id=request["step_id"],
            step_attempt=request["step_attempt"],
            binding=binding,
            work_state=work_state,
            updated_at="2026-09-30T10:00:00Z",
        )
        == "failed"
    )

    authorization = origins.authorize_step_retry(
        subject,
        workflow_id=request["workflow_id"],
        spec_hash=request["spec_hash"],
        tier=request["tier"],
        run_id=request["run_id"],
        run_generation=request["run_generation"],
        step_id=request["step_id"],
        workflow_step_attempt=request["step_attempt"],
        work_attempt_id=binding.work_attempt_id,
        work_generation=binding.work_generation,
    )
    assert (
        origins.authorize_step_retry(
            subject,
            workflow_id=request["workflow_id"],
            spec_hash=request["spec_hash"],
            tier=request["tier"],
            run_id=request["run_id"],
            run_generation=request["run_generation"],
            step_id=request["step_id"],
            workflow_step_attempt=request["step_attempt"],
            work_attempt_id=binding.work_attempt_id,
            work_generation=binding.work_generation,
        )
        == authorization
    )

    next_attempt = workflow_journal.begin_managed_work_step(
        request["run_id"],
        request["step_id"],
        str(request["run_generation"]),
        "a" * 64,
        "2026-09-30T10:01:00Z",
        retry_authorization=authorization,
        principal=subject,
        workflow_origins=origins,
        work_service=work_service,
    )
    assert next_attempt == request["step_attempt"] + 1
    assert (
        workflow_journal.begin_managed_work_step(
            request["run_id"],
            request["step_id"],
            str(request["run_generation"]),
            "a" * 64,
            "2026-09-30T10:01:00Z",
            retry_authorization=authorization,
            principal=subject,
            workflow_origins=origins,
            work_service=work_service,
        )
        == next_attempt
    )
    assert (
        origins.authorize_step_retry(
            subject,
            workflow_id=request["workflow_id"],
            spec_hash=request["spec_hash"],
            tier=request["tier"],
            run_id=request["run_id"],
            run_generation=request["run_generation"],
            step_id=request["step_id"],
            workflow_step_attempt=request["step_attempt"],
            work_attempt_id=binding.work_attempt_id,
            work_generation=binding.work_generation,
        )
        == authorization
    )
    assert (
        origins.resolve_step_retry_authorization(
            subject,
            request["workflow_id"],
            request["spec_hash"],
            request["tier"],
            request["run_id"],
            request["run_generation"],
            request["step_id"],
            request["step_attempt"],
            binding.work_attempt_id,
            binding.work_generation,
        )
        == authorization
    )
    with context.repo.read_snapshot() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_workflow_step_retry_authorizations WHERE binding_id=?",
                (binding.binding_id,),
            ).fetchone()[0]
            == 1
        )


def test_run_capability_is_hashed_run_scoped_and_generation_fenced(context):
    origins, _authority, subject, _authorization, selector, _provision_ref = _provisioned_workflow(
        context
    )

    token = origins.create_run_capability(
        subject,
        run_id="workflow-run",
        workflow_id="wf",
        tier="yaml",
        run_generation=1,
        spec_hash=selector["spec_hash"],
    )
    assert origins.authenticate_run_capability("workflow-run", 1, token).id == subject.id
    with context.repo.read_snapshot() as connection:
        row = connection.execute(
            "SELECT credential_sha256,state FROM work_workflow_run_capabilities "
            "WHERE run_id=? ORDER BY revision DESC LIMIT 1",
            ("workflow-run",),
        ).fetchone()
    assert row["credential_sha256"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in row["credential_sha256"]

    with context.repo.transaction() as connection:
        connection.execute("UPDATE workflow_run SET generation='2' WHERE run_id='workflow-run'")
    with pytest.raises(OriginDenied):
        origins.authenticate_run_capability("workflow-run", 1, token)


def test_unprovisioned_workflow_does_not_require_work_principal_registration(context):
    principal = auth._verified_principal("issuer", "legacy-only", [auth.SCOPE_WRITE], "jwt")
    origins = WorkWorkflowOrigins(context.repo)
    assert origins.resolve_step_admitter(principal, "legacy-workflow", "a" * 64, "s1") is None
    assert origins.requires_run_capability(principal, "legacy-workflow", "a" * 64) is False


def test_script_capability_selection_revalidates_source_and_revocation(context):
    origins, authority, subject, authorization, selector, _ref = _provisioned_workflow(context)
    assert origins.requires_run_capability(subject, "wf", selector["spec_hash"]) is True
    with pytest.raises((PermissionError, LookupError)):
        origins.requires_run_capability(subject, "wf", "f" * 64)
    authority.revoke(
        owner=context.actor,
        subject=subject,
        origin_kind="workflow",
        expected_revision=authorization.ref.revision,
    )
    with pytest.raises((PermissionError, LookupError)):
        origins.requires_run_capability(subject, "wf", selector["spec_hash"])


def test_managed_workflow_attempt_binds_exact_contract_snapshot_and_replays_after_restart(
    context,
):
    origins, _authority, _subject, _authorization, request = _managed_workflow(context)

    first = origins.admit_step(**request)
    assert first["state"] == "queued"
    first_attempt = first["attempts"][0]
    assert first_attempt["generation"] == 1

    restarted_origins = WorkWorkflowOrigins(context.repo)
    from cli_agent_orchestrator.services.work_admission import WorkAdmission

    restarted_admission = WorkAdmission(
        context.repo,
        backends={"test": AdmissionOnlyBackend()},
        delivery_adapters=context.service.deliveries.adapters,
        workflow_origins=restarted_origins,
    )
    replay = restarted_origins.admit_step(**request)
    recovered = restarted_origins.recover_step(**request)

    assert replay["id"] == first["id"]
    assert replay["attempts"][0]["id"] == first_attempt["id"]
    assert recovered["id"] == first["id"]
    assert recovered["attempts"][0]["id"] == first_attempt["id"]
    with context.repo.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM work_items").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM work_attempts").fetchone()[0] == 1
        binding = connection.execute(
            "SELECT * FROM work_dispatch_bindings WHERE attempt_id=? AND generation=?",
            (first_attempt["id"], first_attempt["generation"]),
        ).fetchone()
        item = connection.execute(
            "SELECT idempotency_key,request_hash FROM work_items WHERE id=?", (first["id"],)
        ).fetchone()
        assert binding["contract_hash"] == request["contract"].canonical_hash()
        assert binding["snapshot_id"] == request["contract"].snapshot.id
        assert item["idempotency_key"].startswith("workflow-step-v1:")
        assert item["request_hash"] != "0" * 64
    assert restarted_admission.workflow_origins is restarted_origins


def test_managed_workflow_replay_is_frozen_and_pending_attempt_cannot_retry(context):
    origins, _authority, _subject, _authorization, request = _managed_workflow(context)
    first = origins.admit_step(**request)

    changed_delivery = request["delivery"].model_copy(
        update={"payload_json": request["delivery"].payload_json.replace("execute", "change")}
    )
    with pytest.raises(OriginDenied, match="exact server provision"):
        origins.admit_step(**{**request, "delivery": changed_delivery})

    with pytest.raises(OriginDenied, match="durable pre-admission"):
        origins.admit_step(**{**request, "step_attempt": 2})
    with context.repo.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM work_items").fetchone()[0] == 1


def test_workflow_revocation_blocks_replay_and_missing_source_never_resolves(context):
    origins, authority, subject, authorization, request = _managed_workflow(context)
    first = origins.admit_step(**request)
    with pytest.raises(OriginDenied, match="durable source and generation"):
        origins.recover_step(**{**request, "run_id": "legacy_run"})
    authority.revoke(
        owner=context.actor,
        subject=subject,
        origin_kind="workflow",
        expected_revision=authorization.ref.revision,
    )

    with pytest.raises(OriginDenied):
        origins.admit_step(**request)
    with pytest.raises(OriginDenied):
        origins.recover_step(**request)

    unrelated = {**request, "run_id": "legacy-run"}
    with pytest.raises(OriginDenied):
        origins.recover_step(**unrelated)
    assert first["state"] == "queued"


def test_ordinary_work_admission_cannot_claim_reserved_workflow_step_identity(context):
    from cli_agent_orchestrator.clients.work_repository import WorkConflict

    _origins, _authority, _subject, _authorization, request = _managed_workflow(context)
    with pytest.raises(WorkConflict, match="managed workflow origin required"):
        context.service.admit(
            principal=request["principal"],
            job_id=request["job_id"],
            idempotency_key="workflow-step-v1:" + "a" * 64,
            request_hash="b" * 64,
            grant_id=request["grant_id"],
            expected_grant_revision=request["grant_revision"],
            contract=request["contract"],
            delivery=request["delivery"],
        )
    with context.repo.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM work_items").fetchone()[0] == 0


def test_managed_workflow_cannot_be_created_without_registered_source(context):
    origins, _authority, _subject, _authorization, request = _managed_workflow(
        context, provision=False
    )
    # The verified identity selects the source; body supplied origin refs do not.
    with pytest.raises(OriginDenied, match="no exact-source provision"):
        origins.admit_step(
            principal=request["principal"],
            workflow_id=request["workflow_id"],
            spec_hash=request["spec_hash"],
            tier=request["tier"],
            run_id=request["run_id"],
            run_generation=request["run_generation"],
            step_id=request["step_id"],
            workflow_step_attempt=request["step_attempt"],
        )


def test_workflow_origin_requires_durable_step_marker_before_work_admission(context):
    origins, _authority, _subject, _authorization, request = _managed_workflow(context)
    with context.repo.transaction() as connection:
        connection.execute(
            "UPDATE workflow_run_step SET state='running' WHERE run_id=? AND step_id=?",
            (request["run_id"], request["step_id"]),
        )

    with pytest.raises(OriginDenied, match="durable pre-admission"):
        origins.admit_step(**request)
    with context.repo.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM work_items").fetchone()[0] == 0


def test_workflow_binding_lookups_return_none_for_unmanaged_work(context):
    origins = WorkWorkflowOrigins(context.repo)
    assert origins.read_binding_for_attempt("missing-attempt", 1, "missing-item") is None
    assert origins.read_step_binding("yaml", "missing-run", 1, "missing-step", 1) is None


def test_durable_workflow_marker_cannot_skip_step_attempts(context):
    from cli_agent_orchestrator.services import workflow_journal

    _origins, _authority, _subject, _authorization, request = _managed_workflow(context)
    with context.repo.transaction() as connection:
        connection.execute(
            "UPDATE workflow_run_step SET state='running', attempts=0 WHERE run_id=? AND step_id=?",
            (request["run_id"], request["step_id"]),
        )

    with pytest.raises(ValueError, match="next workflow step attempt"):
        workflow_journal.mark_work_pending(
            run_id=request["run_id"],
            step_id=request["step_id"],
            generation=str(request["run_generation"]),
            step_attempt=2,
            tier="yaml",
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
