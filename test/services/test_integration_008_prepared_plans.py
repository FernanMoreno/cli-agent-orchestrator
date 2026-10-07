"""Prepared plans never mint Work authority or publish partial executable state."""

import hashlib
import importlib
import json
import shutil
import subprocess
from pathlib import Path
from test.integration.test_work_dispatch import context  # noqa:F401
from test.services.test_work_workflow import _provisioned_workflow
from uuid import uuid4

import pytest

from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning


@pytest.fixture
def plan_context(context, monkeypatch):
    module = importlib.import_module("cli_agent_orchestrator.services.work_workflow_plans")
    from cli_agent_orchestrator.services import approval_store

    monkeypatch.setattr(approval_store, "DATABASE_FILE", context.repo.path)
    origins, authority, subject, authorization, request, ref = _provisioned_workflow(context)
    source = "INPUTS = {}\nSCOPE = {'version': 1, 'targets': {'repo': {'agents': ['developer'], 'memory': 'off'}}}\n"
    digest = hashlib.sha256(source.encode()).hexdigest()
    seed = WorkProvisioning(context.repo).resolve_workflow_step(
        subject, workflow_id="wf", step_id="build", spec_hash=request["spec_hash"]
    )
    from cli_agent_orchestrator.models.work_contract import ContractSnapshot
    from cli_agent_orchestrator.services.delegation_snapshot import (
        DelegationSnapshots,
        ResolvedSnapshot,
    )
    from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy

    empty = DelegationSnapshots(
        context.repo,
        policy=KnowledgePolicy(context.repo, seed.job_id, seed.grant_id, seed.grant_revision),
    ).freeze(
        principal=subject,
        job_id=seed.job_id,
        contract_id="workflow-empty-contract",
        binding_key="workflow-empty-snapshot",
        request_hash=hashlib.sha256(b"explicit-empty-workflow-context").hexdigest(),
        scope="project",
        scope_id="project",
        resolver=lambda connection, principal: ResolvedSnapshot(""),
    )
    empty_contract = seed.contract.model_copy(
        update={
            "id": "workflow-empty-contract",
            "snapshot": ContractSnapshot(
                state="present", id=empty.id, delivered_hash=empty.delivered_hash
            ),
        }
    )
    WorkProvisioning(context.repo).provision_workflow_step(
        context.actor,
        subject=subject,
        workflow_id="wf",
        step_id="seed",
        expected_revision=0,
        spec_hash=digest,
        job_id=seed.job_id,
        grant_id=seed.grant_id,
        grant_revision=seed.grant_revision,
        subject_ref=seed.workflow_subject_ref,
        authorization_ref=seed.workflow_authorization_ref,
        receiver_subject_ref=seed.receiver_subject_ref,
        receiver_authorization_ref=seed.receiver_authorization_ref,
        contract=empty_contract,
        delivery=seed.delivery_template,
    )
    subprocess.run(["git", "init", "-q", str(context.root)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(context.root),
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "fixture",
            "-q",
        ],
        check=True,
    )
    directory = Path.home() / ".cao-test-workflows" / uuid4().hex
    directory.mkdir(parents=True)
    (directory / "wf.py").write_text(source)
    monkeypatch.setattr(
        "cli_agent_orchestrator.utils.agent_profiles._read_agent_profile_source",
        lambda name: "---\nname: developer\nallowedTools: []\n---\nFrozen developer\n",
    )
    plans = module.WorkWorkflowPlans(context.repo)
    origins.plans = plans
    plans.origins = origins
    try:
        yield plans, subject, directory, context
    finally:
        shutil.rmtree(directory)


def prepare(fixture):
    plans, subject, directory, context = fixture
    return plans.prepare(
        subject,
        "wf",
        {},
        {"repo": str(context.root)},
        {"repo": {"developer": "seed"}},
        scan_dir=str(directory),
    )


def test_prepare_is_private_and_exact_but_creates_no_run(plan_context):
    result = prepare(plan_context)
    plans, subject, directory, context = plan_context
    assert result["plan_id"].startswith("plan-v2:")
    assert "Frozen developer" not in json.dumps(result)
    assert str(context.root) not in json.dumps(result)
    with context.repo.read_snapshot() as connection:
        assert connection.execute("SELECT 1 FROM workflow_scoped_run").fetchone() is None
    assert plans.review(subject, result["prepared_id"])["plan_id"] == result["plan_id"]


def test_unapproved_start_leaves_no_run_or_capability(plan_context):
    result = prepare(plan_context)
    plans, subject, directory, context = plan_context
    with pytest.raises(ValueError, match="plan_approval_required"):
        plans.start(
            subject,
            result["prepared_id"],
            "not-started",
            result["plan_id"],
            scan_dir=str(directory),
        )
    with context.repo.read_snapshot() as connection:
        assert (
            connection.execute("SELECT 1 FROM workflow_run WHERE run_id='not-started'").fetchone()
            is None
        )
        assert (
            connection.execute(
                "SELECT 1 FROM work_workflow_run_capabilities WHERE run_id='not-started'"
            ).fetchone()
            is None
        )


def test_approved_plan_starts_two_runs_with_atomic_private_attachment(plan_context):
    from cli_agent_orchestrator.services import approval_store

    result = prepare(plan_context)
    plans, subject, directory, context = plan_context
    approval_store.grant(result["plan_id"], "fixture-admin")
    for run_id in ("first-scoped", "second-scoped"):
        start = plans.start(
            subject, result["prepared_id"], run_id, result["plan_id"], scan_dir=str(directory)
        )
        assert start.run_credential
        with context.repo.read_snapshot() as connection:
            assert (
                connection.execute(
                    "SELECT plan_id FROM workflow_run_plan_snapshot WHERE run_id=?", (run_id,)
                ).fetchone()[0]
                == result["plan_id"]
            )


def test_source_drift_refuses_approved_start_before_run(plan_context):
    from cli_agent_orchestrator.services import approval_store

    result = prepare(plan_context)
    plans, subject, directory, context = plan_context
    approval_store.grant(result["plan_id"], "fixture-admin")
    path = directory / "wf.py"
    path.write_text(path.read_text() + "# changed\n")
    with pytest.raises(ValueError, match="prepared_source_changed"):
        plans.start(
            subject,
            result["prepared_id"],
            "changed-scoped",
            result["plan_id"],
            scan_dir=str(directory),
        )


def started(fixture, run_id="scoped-dynamic"):
    from cli_agent_orchestrator.services import approval_store

    result = prepare(fixture)
    plans, subject, directory, context = fixture
    approval_store.grant(result["plan_id"], "fixture-admin")
    plans.start(subject, result["prepared_id"], run_id, result["plan_id"], scan_dir=str(directory))
    return result


def test_dynamic_step_uses_existing_work_authority_and_replays_exact_order(plan_context):
    import asyncio

    from cli_agent_orchestrator.services import workflow_journal

    result = started(plan_context)
    plans, subject, directory, context = plan_context
    request = dict(provider="mock_cli", agent="developer", prompt="execute this workflow step")
    callback = plans.authorize_step(subject, "scoped-dynamic", "runtime-step", "repo", request)
    now = "2026-10-02T00:00:00Z"
    workflow_journal.insert_steps("scoped-dynamic", [("runtime-step", "pending")], now)
    workflow_journal.mark_work_pending(
        run_id="scoped-dynamic",
        step_id="runtime-step",
        generation="1",
        step_attempt=1,
        tier="script",
        updated_at=now,
    )
    selectors = dict(
        tier="script",
        run_id="scoped-dynamic",
        run_generation=1,
        step_id="runtime-step",
        workflow_id="wf",
        spec_hash=result["source_hash"],
        step_attempt=1,
        prompt=request["prompt"],
    )
    first = asyncio.run(callback(**selectors))
    second = asyncio.run(callback(**selectors))
    assert first["id"] == second["id"]
    binding = plans.origins.read_step_binding("script", "scoped-dynamic", 1, "runtime-step", 1)
    with context.repo.read_snapshot() as conn:
        alias = conn.execute(
            "SELECT * FROM workflow_plan_step_alias WHERE run_id='scoped-dynamic'"
        ).fetchone()
        assert binding.provision_id == alias["provision_id"]
        plans.revalidate_attempt(conn, binding.work_attempt_id, binding.work_generation)


def test_unknown_scope_has_no_dynamic_provision_or_work(plan_context):
    started(plan_context)
    plans, subject, directory, context = plan_context
    with pytest.raises(ValueError, match="scope_binding_not_declared"):
        plans.authorize_step(
            subject,
            "scoped-dynamic",
            "runtime-step",
            "unknown",
            dict(provider="mock_cli", agent="developer", prompt="execute this workflow step"),
        )
    with context.repo.read_snapshot() as conn:
        assert conn.execute("SELECT 1 FROM workflow_plan_step_alias").fetchone() is None


def test_private_attachment_removed_refuses_resume_and_admission(plan_context):
    started(plan_context)
    plans, subject, directory, context = plan_context
    with context.repo.transaction() as conn:
        conn.execute("DELETE FROM workflow_run_plan_snapshot WHERE run_id='scoped-dynamic'")
    with pytest.raises(ValueError, match="run_plan_attachment_missing"):
        plans.validate_resume(subject, "scoped-dynamic")
    with pytest.raises(ValueError, match="run_plan_attachment_missing"):
        plans.authorize_step(
            subject,
            "scoped-dynamic",
            "runtime-step",
            "repo",
            dict(provider="mock_cli", agent="developer", prompt="execute this workflow step"),
        )


def test_failed_capability_issuance_rolls_back_all_executable_state(plan_context, monkeypatch):
    from cli_agent_orchestrator.services import approval_store

    result = prepare(plan_context)
    plans, subject, directory, context = plan_context
    approval_store.grant(result["plan_id"], "fixture-admin")

    def refused(*args, **kwargs):
        raise ValueError("capability refused")

    monkeypatch.setattr(plans.origins, "create_run_capability", refused)
    with pytest.raises(ValueError, match="capability refused"):
        plans.start(
            subject,
            result["prepared_id"],
            "rollback-scoped",
            result["plan_id"],
            scan_dir=str(directory),
        )
    with context.repo.read_snapshot() as conn:
        for table in (
            "workflow_run",
            "workflow_run_plan_snapshot",
            "workflow_scoped_run",
            "work_workflow_run_capabilities",
        ):
            assert (
                conn.execute(
                    "SELECT 1 FROM " + table + " WHERE run_id='rollback-scoped'"
                ).fetchone()
                is None
            )


def test_foreign_owner_cannot_review_or_start_even_identical_digest(plan_context):
    from cli_agent_orchestrator.security import auth

    result = prepare(plan_context)
    plans, subject, directory, context = plan_context
    foreign = auth._verified_principal("issuer", "foreign", [auth.SCOPE_WRITE], "jwt")
    with pytest.raises(ValueError, match="prepared_plan_owner_mismatch"):
        plans.review(foreign, result["prepared_id"])
    with pytest.raises(ValueError, match="prepared_plan_owner_mismatch"):
        plans.start(
            foreign, result["prepared_id"], "foreign", result["plan_id"], scan_dir=str(directory)
        )


def test_verified_admin_can_review_public_plan_but_cannot_start_as_owner(plan_context):
    result = prepare(plan_context)
    plans, subject, directory, context = plan_context
    assert plans.review(context.actor, result["prepared_id"])["plan_id"] == result["plan_id"]
    with pytest.raises(ValueError, match="prepared_plan_owner_mismatch"):
        plans.start(
            context.actor,
            result["prepared_id"],
            "admin-run",
            result["plan_id"],
            scan_dir=str(directory),
        )


def test_start_rejects_name_override_before_run_insert(plan_context):
    from cli_agent_orchestrator.services import approval_store

    result = prepare(plan_context)
    plans, subject, directory, context = plan_context
    approval_store.grant(result["plan_id"], "fixture-admin")
    with pytest.raises(ValueError, match="prepared_workflow_changed"):
        plans.start(
            subject,
            result["prepared_id"],
            "override",
            result["plan_id"],
            scan_dir=str(directory),
            requested_name="another",
        )
    with context.repo.read_snapshot() as conn:
        assert conn.execute("SELECT 1 FROM workflow_run WHERE run_id='override'").fetchone() is None


def test_credential_reference_profile_is_refused_before_private_publication(
    plan_context, monkeypatch
):
    monkeypatch.setattr(
        "cli_agent_orchestrator.utils.agent_profiles._read_agent_profile_source",
        lambda name: "---\nname: developer\nallowedTools: []\nmcpServers:\n  external:\n    env:\n      API_TOKEN: ${API_TOKEN}\n---\nprofile\n",
    )
    with pytest.raises(ValueError, match="mcp_credential_destination_unsupported"):
        prepare(plan_context)
    with plan_context[3].repo.read_snapshot() as conn:
        assert conn.execute("SELECT 1 FROM workflow_prepared_plan").fetchone() is None


def test_drive_refuses_private_owner_absence_before_subprocess(plan_context):
    from types import SimpleNamespace

    started(plan_context)
    from cli_agent_orchestrator.services.work_workflow_plans import WorkWorkflowPlans

    with pytest.raises(ValueError, match="scoped_runtime_unavailable"):
        WorkWorkflowPlans.guard_drive(SimpleNamespace(run_id="scoped-dynamic"))


def test_scoped_owner_reference_removed_never_falls_back_to_legacy(plan_context):
    started(plan_context)
    plans, subject, directory, context = plan_context
    with context.repo.transaction() as conn:
        conn.execute("DELETE FROM workflow_scoped_run WHERE run_id='scoped-dynamic'")
    with pytest.raises(ValueError, match="run_plan_attachment_missing"):
        plans.authorize_step(
            subject,
            "scoped-dynamic",
            "seed",
            "repo",
            dict(provider="mock_cli", agent="developer", prompt="execute this workflow step"),
        )


def test_discovery_accepts_verified_read_scope_without_elevating_it(plan_context):
    from cli_agent_orchestrator.security import auth

    plans, subject, directory, context = plan_context
    reader = auth._verified_principal(
        subject.issuer, subject.subject, [auth.SCOPE_READ], subject.kind
    )
    discovered = plans.available_seeds(reader, "wf", scan_dir=str(directory))
    assert [seed["step_id"] for seed in discovered["seeds"]] == ["seed"]
    assert reader.scopes == frozenset({auth.SCOPE_READ})
    with pytest.raises(PermissionError):
        plans.provisioning.resolve_workflow_step(
            reader, workflow_id="wf", step_id="seed", spec_hash=discovered["source_hash"]
        )
    assert str(context.root) not in json.dumps(discovered)
    assert "execute this workflow step" not in json.dumps(discovered)


def test_provision_facade_refuses_write_subject_before_schema_mutation(plan_context):
    plans, subject, directory, context = plan_context
    with pytest.raises(PermissionError):
        plans.provision_seed(subject, "wf", {}, scan_dir=str(directory))
    with context.repo.read_snapshot() as conn:
        assert conn.execute("SELECT count(*) FROM work_workflow_step_provisions").fetchone()[0] == 2


def test_provision_facade_authenticates_subject_and_reuses_existing_authority(
    plan_context, monkeypatch
):
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning

    plans, subject, directory, context = plan_context
    view = plans.available_seeds(subject, "wf", scan_dir=str(directory))
    seed = WorkProvisioning(context.repo).resolve_workflow_step(
        subject, workflow_id="wf", step_id="seed", spec_hash=view["source_hash"]
    )
    observed = []

    def verified_token(token):
        observed.append(token)
        return subject

    monkeypatch.setattr(auth, "principal_from_token", verified_token)
    values = dict(
        step_id="second-seed",
        expected_revision=0,
        expected_source_hash=view["source_hash"],
        job_id=seed.job_id,
        grant_id=seed.grant_id,
        grant_revision=seed.grant_revision,
        subject_ref=seed.workflow_subject_ref,
        authorization_ref=seed.workflow_authorization_ref,
        receiver_subject_ref=seed.receiver_subject_ref,
        receiver_authorization_ref=seed.receiver_authorization_ref,
        contract=seed.contract,
        delivery=seed.delivery_template,
    )
    result = plans.provision_seed(
        context.actor,
        "wf",
        values,
        subject_token="fixture-authenticated-subject",
        scan_dir=str(directory),
    )
    assert observed == ["fixture-authenticated-subject"]
    assert result["step_id"] == "second-seed"
    assert "fixture-authenticated-subject" not in json.dumps(result)
    assert len(plans.available_seeds(subject, "wf", scan_dir=str(directory))["seeds"]) == 2


def test_dynamic_prompt_is_frozen_once_and_conflicting_replay_is_refused(plan_context):
    started(plan_context)
    plans, subject, directory, context = plan_context
    request = dict(
        provider="mock_cli",
        agent="developer",
        prompt="Runtime issue 583: approved source computed this task",
    )
    plans.authorize_step(subject, "scoped-dynamic", "computed", None, request)
    plans.authorize_step(subject, "scoped-dynamic", "computed", None, request)
    with context.repo.read_snapshot() as conn:
        alias = conn.execute(
            "SELECT provision_id FROM workflow_plan_step_alias WHERE run_id='scoped-dynamic' AND step_id='computed'"
        ).fetchone()
        row = conn.execute(
            "SELECT delivery_json FROM work_workflow_step_provisions WHERE id=?", (alias[0],)
        ).fetchone()
        assert json.loads(json.loads(row[0])["payload_json"])["message"] == request["prompt"]
    with pytest.raises(ValueError, match="scoped step is bound to another seed"):
        plans.authorize_step(
            subject, "scoped-dynamic", "computed", None, {**request, "prompt": "changed task"}
        )


def test_dynamic_order_revalidates_revoked_authority_before_effect(plan_context):
    import asyncio

    from cli_agent_orchestrator.services import workflow_journal
    from cli_agent_orchestrator.services.work_contract import WorkContracts
    from cli_agent_orchestrator.services.work_origin import WorkOriginAuthority

    result = started(plan_context)
    plans, subject, directory, context = plan_context
    request = dict(provider="mock_cli", agent="developer", prompt="A runtime derived prompt")
    callback = plans.authorize_step(subject, "scoped-dynamic", "dynamic", None, request)
    now = "2026-10-02T00:00:00Z"
    workflow_journal.insert_steps("scoped-dynamic", [("dynamic", "pending")], now)
    workflow_journal.mark_work_pending(
        run_id="scoped-dynamic",
        step_id="dynamic",
        generation="1",
        step_attempt=1,
        tier="script",
        updated_at=now,
    )
    work = asyncio.run(
        callback(
            tier="script",
            run_id="scoped-dynamic",
            run_generation=1,
            step_id="dynamic",
            step_attempt=1,
            prompt=request["prompt"],
        )
    )
    attempt = work["attempts"][0]
    contracts = WorkContracts(context.repo)
    contracts.revalidate_order(attempt["id"], generation=attempt["generation"])
    authority = WorkOriginAuthority(context.repo)
    authority.revoke(
        owner=context.actor, subject=subject, origin_kind="workflow", expected_revision=1
    )
    with pytest.raises((PermissionError, ValueError, LookupError)):
        contracts.revalidate_order(attempt["id"], generation=attempt["generation"])
    with pytest.raises((PermissionError, ValueError, LookupError)):
        plans.authorize_step(subject, "scoped-dynamic", "other", None, request)


def test_delete_run_preserves_immutable_work_references_and_private_snapshot(plan_context):
    from cli_agent_orchestrator.services import workflow_journal

    result = started(plan_context)
    plans, subject, directory, context = plan_context
    workflow_journal.update_run_state("scoped-dynamic", "failed", "2026-10-02T00:00:00Z")
    with pytest.raises(workflow_journal.WorkflowRunAuthorityReferencedError):
        workflow_journal.delete_run("scoped-dynamic")
    assert plans.review(subject, result["prepared_id"])["plan_id"] == result["plan_id"]
    with context.repo.read_snapshot() as conn:
        assert conn.execute("SELECT 1 FROM workflow_scoped_run").fetchone()
    with pytest.raises(ValueError, match="prepared_plan_in_use"):
        plans.delete_prepared(subject, result["prepared_id"])


def test_unreferenced_prepared_plan_cleanup_collects_private_bytes(plan_context):
    result = prepare(plan_context)
    plans, subject, directory, context = plan_context
    plans.delete_prepared(subject, result["prepared_id"])
    with context.repo.read_snapshot() as conn:
        assert conn.execute("SELECT 1 FROM workflow_prepared_plan").fetchone() is None
        assert (
            conn.execute(
                "SELECT 1 FROM workflow_plan_snapshot_component WHERE plan_id=?",
                (result["plan_id"],),
            ).fetchone()
            is None
        )


def test_prepared_api_blocking_run_uses_private_snapshot_and_reaps_scratch(
    plan_context, monkeypatch
):
    import asyncio
    from types import SimpleNamespace

    from starlette.requests import Request

    from cli_agent_orchestrator.api import main
    from cli_agent_orchestrator.services import (
        approval_store,
        script_runner,
        workflow_service,
        workflow_spec_service,
    )

    plans, subject, directory, context = plan_context
    result = prepare(plan_context)
    approval_store.grant(result["plan_id"], "fixture-admin")
    original = workflow_spec_service.get_workflow_source
    monkeypatch.setattr(
        workflow_spec_service,
        "get_workflow_source",
        lambda name, scan_dir=None: original(name, str(directory)),
    )
    scratch = context.root / "private-scratch"
    monkeypatch.setattr(script_runner, "WORKFLOW_SCRIPT_SCRATCH_DIR", scratch)
    application = SimpleNamespace(state=SimpleNamespace(work_workflow_origins=plans.origins))
    request = Request({"type": "http", "app": application})
    body = main.WorkflowRunRequest(
        name_or_path="wf",
        prepared_id=result["prepared_id"],
        expected_plan_id=result["plan_id"],
        run_id="api-scoped",
    )
    try:
        response = asyncio.run(main._start_scoped_workflow(body, request, subject, submit=False))
        assert response["state"] == "completed"
        assert not list(scratch.iterdir())
        with context.repo.read_snapshot() as conn:
            assert (
                conn.execute("SELECT state FROM workflow_run WHERE run_id='api-scoped'").fetchone()[
                    0
                ]
                == "completed"
            )
            assert conn.execute(
                "SELECT 1 FROM workflow_run_plan_snapshot WHERE run_id='api-scoped'"
            ).fetchone()
    finally:
        workflow_service.run_registry.pop("api-scoped", None)


def test_memory_off_refuses_nonempty_authorized_seed_before_private_publication(plan_context):
    plans, subject, directory, context = plan_context
    source_hash = hashlib.sha256((directory / "wf.py").read_bytes()).hexdigest()
    old = WorkProvisioning(context.repo).resolve_workflow_step(
        subject, workflow_id="wf", step_id="build", spec_hash=hashlib.sha256(b"{}").hexdigest()
    )
    WorkProvisioning(context.repo).provision_workflow_step(
        context.actor,
        subject=subject,
        workflow_id="wf",
        step_id="nonempty",
        expected_revision=0,
        spec_hash=source_hash,
        job_id=old.job_id,
        grant_id=old.grant_id,
        grant_revision=old.grant_revision,
        subject_ref=old.workflow_subject_ref,
        authorization_ref=old.workflow_authorization_ref,
        receiver_subject_ref=old.receiver_subject_ref,
        receiver_authorization_ref=old.receiver_authorization_ref,
        contract=old.contract,
        delivery=old.delivery_template,
    )
    with pytest.raises(ValueError, match="memory_off_requires_empty_snapshot"):
        plans.prepare(
            subject,
            "wf",
            {},
            {"repo": str(context.root)},
            {"repo": {"developer": "nonempty"}},
            scan_dir=str(directory),
        )
    with context.repo.read_snapshot() as conn:
        assert conn.execute("SELECT 1 FROM workflow_prepared_plan").fetchone() is None
        assert conn.execute("SELECT 1 FROM workflow_scoped_run").fetchone() is None


def test_blocking_start_postcommit_revoke_settles_before_process_effect(plan_context, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException
    from starlette.requests import Request

    from cli_agent_orchestrator.api import main
    from cli_agent_orchestrator.services import (
        approval_store,
        script_runner,
        workflow_journal,
        workflow_service,
        workflow_spec_service,
    )
    from cli_agent_orchestrator.services.work_origin import WorkOriginAuthority

    plans, subject, directory, context = plan_context
    result = prepare(plan_context)
    approval_store.grant(result["plan_id"], "fixture-admin")
    original_source = workflow_spec_service.get_workflow_source
    monkeypatch.setattr(
        workflow_spec_service,
        "get_workflow_source",
        lambda name, scan_dir=None: original_source(name, str(directory)),
    )
    original_start = plans.start

    def revoke_after_start(*args, **kwargs):
        prepared = original_start(*args, **kwargs)
        WorkOriginAuthority(context.repo).revoke(
            owner=context.actor, subject=subject, origin_kind="workflow", expected_revision=1
        )
        return prepared

    monkeypatch.setattr(plans, "start", revoke_after_start)

    async def forbidden_spawn(*args, **kwargs):
        pytest.fail("refused private authority reached subprocess effect")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_spawn)
    scratch = context.root / "refused-scratch"
    monkeypatch.setattr(script_runner, "WORKFLOW_SCRIPT_SCRATCH_DIR", scratch)
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(state=SimpleNamespace(work_workflow_origins=plans.origins)),
        }
    )
    body = main.WorkflowRunRequest(
        name_or_path="wf",
        prepared_id=result["prepared_id"],
        expected_plan_id=result["plan_id"],
        run_id="refused-scoped",
    )
    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(main._start_scoped_workflow(body, request, subject, submit=False))
        assert error.value.status_code == 403
        row = workflow_journal.get_run("refused-scoped")
        assert row.state == "failed" and row.finished_at is not None
        assert workflow_service.run_registry["refused-scoped"].state.value == "failed"
        assert not list(scratch.iterdir())
    finally:
        workflow_service.run_registry.pop("refused-scoped", None)


@pytest.mark.parametrize("manifest", [None, '{"corrupt":true}', '{"plan_id":"historic-looking"}'])
def test_scoped_drive_corrupt_or_downgraded_manifest_never_falls_back(plan_context, manifest):
    from types import SimpleNamespace

    from cli_agent_orchestrator.services.work_workflow_plans import WorkWorkflowPlans

    started(plan_context)
    plans, subject, directory, context = plan_context
    with context.repo.transaction() as connection:
        connection.execute(
            "UPDATE workflow_run SET manifest_json=? WHERE run_id='scoped-dynamic'", (manifest,)
        )
    record = SimpleNamespace(
        run_id="scoped-dynamic",
        workflow_name="wf",
        generation="1",
        inputs={},
        scoped_plan_owner=plans,
        scoped_principal=subject,
    )
    with pytest.raises(ValueError, match="run_manifest_integrity"):
        WorkWorkflowPlans.guard_drive(record)


def test_corrupt_scoped_resume_refuses_before_generation_or_process(plan_context, monkeypatch):
    import asyncio

    from cli_agent_orchestrator.services import script_runner, workflow_journal

    started(plan_context)
    plans, subject, directory, context = plan_context
    workflow_journal.update_run_state("scoped-dynamic", "failed", "2026-10-02T00:00:00Z")
    with context.repo.transaction() as connection:
        connection.execute(
            "UPDATE workflow_run SET manifest_json=NULL WHERE run_id='scoped-dynamic'"
        )

    async def forbidden_spawn(*args, **kwargs):
        pytest.fail("corrupt private identity reached subprocess effect")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_spawn)
    with pytest.raises(ValueError, match="run_manifest_integrity"):
        asyncio.run(
            script_runner.resume_script_run(
                "scoped-dynamic", scoped_plan_owner=plans, scoped_principal=subject
            )
        )
    assert workflow_journal.get_run("scoped-dynamic").generation == "1"


def test_existing_provisioned_child_prepare_and_head_pin_require_reprepare(plan_context):
    from cli_agent_orchestrator.services import approval_store

    plans, subject, directory, context = plan_context
    child = context.root / "approved-child"
    (context.root / ".git" / "info" / "exclude").write_text("approved-child/\n")
    subprocess.run(
        ["git", "-C", str(context.root), "worktree", "add", "--detach", str(child), "HEAD"],
        check=True,
        capture_output=True,
    )
    source = "INPUTS = {}\nSCOPE = {'version':1,'targets':{'repo':{'agents':['developer'],'memory':'off'},'child':{'agents':['developer'],'memory':'off','worktree':'ephemeral-child-of:repo'}}}\n"
    source_hash = hashlib.sha256(source.encode()).hexdigest()
    (directory / "wf.py").write_text(source)
    with context.repo.read_snapshot() as connection:
        original_hash = connection.execute(
            "SELECT spec_hash FROM work_workflow_step_provisions WHERE step_id='seed'"
        ).fetchone()[0]
    seed = plans.provisioning.resolve_workflow_step(
        subject, workflow_id="wf", step_id="seed", spec_hash=original_hash
    )
    for step_id, root in (("parent-child-seed", context.root), ("actual-child-seed", child)):
        contract = seed.contract.model_copy(
            update={
                "resources": seed.contract.resources.model_copy(update={"checkout_root": str(root)})
            }
        )
        plans.provisioning.provision_workflow_step(
            context.actor,
            subject=subject,
            workflow_id="wf",
            step_id=step_id,
            expected_revision=0,
            spec_hash=source_hash,
            job_id=seed.job_id,
            grant_id=seed.grant_id,
            grant_revision=seed.grant_revision,
            subject_ref=seed.workflow_subject_ref,
            authorization_ref=seed.workflow_authorization_ref,
            receiver_subject_ref=seed.receiver_subject_ref,
            receiver_authorization_ref=seed.receiver_authorization_ref,
            contract=contract,
            delivery=seed.delivery_template,
        )
    prepared = plans.prepare(
        subject,
        "wf",
        {},
        {"repo": str(context.root), "child": {"parent": "repo", "baseline": "HEAD"}},
        {"repo": {"developer": "parent-child-seed"}, "child": {"developer": "actual-child-seed"}},
        scan_dir=str(directory),
    )
    approval_store.grant(prepared["plan_id"], "fixture-admin")
    plans.start(
        subject,
        prepared["prepared_id"],
        "child-scoped",
        prepared["plan_id"],
        scan_dir=str(directory),
    )
    with pytest.raises(ValueError, match="scope_binding_not_declared"):
        plans.authorize_step(
            subject,
            "child-scoped",
            "ambiguous",
            None,
            dict(provider="mock_cli", agent="developer", prompt="Task"),
        )
    plans.authorize_step(
        subject,
        "child-scoped",
        "first",
        "child",
        dict(provider="mock_cli", agent="developer", prompt="Task"),
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(child),
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "changed baseline",
            "-q",
        ],
        check=True,
    )
    with pytest.raises(ValueError, match="scope_child_provenance_mismatch"):
        plans.authorize_step(
            subject,
            "child-scoped",
            "after-commit",
            "child",
            dict(provider="mock_cli", agent="developer", prompt="Task"),
        )
