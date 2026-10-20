"""Real PostgreSQL contracts across authenticated managed workflow API boundaries."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.postgres

from test.services.test_integration_008_prepared_plans import context, plan_context  # noqa: F401

from cli_agent_orchestrator.api import main
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services import script_runner, workflow_journal, workflow_service


@pytest.fixture
def authenticated_workflow_api(client, plan_context, postgres_targets, tmp_path, monkeypatch):
    """Compose real Work plan/origin owners and a BrowserAuth cookie on isolated PostgreSQL."""
    from cli_agent_orchestrator.clients.browser_auth_repository import BROWSER_AUTH_MIGRATIONS
    from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
    from cli_agent_orchestrator.services import workflow_spec_service
    from cli_agent_orchestrator.services.browser_auth import BrowserAuthService

    # The runner captures this constant when its module is imported, before the
    # per-test PostgreSQL fixture redirects CAO_HOME_DIR. Keep private snapshot
    # materialization inside pytest's POSIX temp root instead of the operator's
    # configured home (which may be on a mount that cannot enforce 0700).
    private_scratch = tmp_path / "workflow-script-scratch"
    private_scratch.mkdir(mode=0o700)
    monkeypatch.setattr(script_runner, "WORKFLOW_SCRIPT_SCRATCH_DIR", private_scratch)

    plans, subject, directory, context = plan_context
    monkeypatch.setattr(workflow_spec_service, "WORKFLOW_SPEC_DIR", directory)

    # Keep the provisioned subject identity while granting the local operator's
    # approval scope through its sealed BrowserAuth binding.
    scopes = sorted(set(subject.scopes) | {auth.SCOPE_ADMIN})
    binding = {
        "id": subject.id,
        "issuer": subject.issuer,
        "subject": subject.subject,
        "kind": subject.kind,
        "scopes": scopes,
    }
    migrate_component(postgres_targets.auth, BROWSER_AUTH_MIGRATIONS)
    browser_auth = BrowserAuthService(
        postgres_targets.auth,
        binding,
        {"installation_id": "postgres-migration-workflow-contracts"},
    )
    browser_auth.initialize_store()
    browser_auth.create_account("workflow-operator", "a long private passphrase")

    browser_config = {
        "enabled": True,
        "installation_id": "postgres-migration-workflow-contracts",
        "canonical_origin": "http://localhost",
        "transport_policy": "loopback_http",
    }
    monkeypatch.setattr(main.app.state, "browser_auth", browser_auth, raising=False)
    monkeypatch.setattr(main.app.state, "browser_auth_config", browser_config, raising=False)
    monkeypatch.setattr(main.app.state, "browser_auth_unavailable", False, raising=False)
    monkeypatch.setattr(main.app.state, "work_workflow_origins", plans.origins, raising=False)

    client.headers.update({"Origin": "http://localhost", "X-CAO-Browser": "1"})
    login = client.post(
        "/auth/login",
        json={
            "username": "workflow-operator",
            "password": "a long private passphrase",
            "remember": True,
        },
    )
    assert login.status_code == 200, login.text
    assert "Set-Cookie" in login.headers
    assert "secret" not in login.text and "password" not in login.text

    return SimpleNamespace(
        client=client,
        plans=plans,
        origins=plans.origins,
        subject=subject,
        directory=directory,
        context=context,
    )


def _prepare_via_api(api):
    response = api.client.post(
        "/workflows/plans:prepare",
        json={
            "name_or_path": "wf",
            "inputs": {},
            "target_mappings": {"repo": str(api.context.root)},
            "binding_selections": {"repo": {"developer": "seed"}},
        },
    )
    assert response.status_code == 201, response.text
    prepared = response.json()
    review = api.client.get(f"/workflows/plans/{prepared['prepared_id']}")
    assert review.status_code == 200, review.text
    assert review.json()["plan_id"] == prepared["plan_id"]
    return prepared


def _approve_via_api(api, plan_id):
    response = api.client.post("/workflows/plans/approve", json={"plan_id": plan_id})
    assert response.status_code == 200, response.text
    assert response.json()["approved"] is True


def _assert_no_started_run(api, run_id):
    with api.context.repo.read_snapshot() as connection:
        for table in (
            "workflow_run",
            "workflow_scoped_run",
            "workflow_run_plan_snapshot",
            "work_workflow_run_capabilities",
        ):
            assert (
                connection.execute(f"SELECT 1 FROM {table} WHERE run_id=%s", (run_id,)).fetchone()
                is None
            ), f"{table} contains an unauthorized start for {run_id}"


def test_unapproved_prepared_start_has_no_run_or_work_capability(authenticated_workflow_api):
    api = authenticated_workflow_api
    prepared = _prepare_via_api(api)
    run_id = "api-unapproved-start"

    response = api.client.post(
        "/workflows/runs",
        json={
            "name_or_path": "wf",
            "inputs": {},
            "run_id": run_id,
            "prepared_id": prepared["prepared_id"],
            "expected_plan_id": prepared["plan_id"],
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["kind"] == "plan_approval_required"
    _assert_no_started_run(api, run_id)


@pytest.mark.parametrize("tamper", ["expected_plan", "inputs", "source"])
def test_stale_or_tampered_prepared_start_has_no_durable_authority(
    authenticated_workflow_api, tamper
):
    api = authenticated_workflow_api
    prepared = _prepare_via_api(api)
    _approve_via_api(api, prepared["plan_id"])
    run_id = f"api-rejected-{tamper}"

    expected_plan_id = prepared["plan_id"]
    inputs = {}
    if tamper == "expected_plan":
        expected_plan_id = "plan-v2:" + "0" * 64
    elif tamper == "inputs":
        inputs = {"forged": "caller value"}
    else:
        source_path = api.directory / "wf.py"
        source_path.write_text(source_path.read_text(encoding="utf-8") + "# changed after review\n")

    response = api.client.post(
        "/workflows/runs",
        json={
            "name_or_path": "wf",
            "inputs": inputs,
            "run_id": run_id,
            "prepared_id": prepared["prepared_id"],
            "expected_plan_id": expected_plan_id,
        },
    )

    expected_kind = {
        "expected_plan": "prepared_plan_changed",
        "inputs": "prepared_inputs_changed",
        "source": "prepared_source_changed",
    }[tamper]
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["kind"] == expected_kind
    _assert_no_started_run(api, run_id)


def test_work_issued_capability_is_private_and_superseded_capability_never_falls_back(
    authenticated_workflow_api, monkeypatch, request
):
    api = authenticated_workflow_api
    prepared = _prepare_via_api(api)
    _approve_via_api(api, prepared["plan_id"])
    run_id = "api-managed-capability"
    observed = {}

    async def fake_run_prepared(record, source_path, env, *, run_credential=None):
        observed.update(
            record=record,
            source_path=source_path,
            env=env,
            credential=run_credential,
        )
        return SimpleNamespace(
            model_dump=lambda: {"run_id": run_id, "workflow_name": "wf", "state": "completed"}
        )

    monkeypatch.setattr(script_runner, "run_script_workflow_prepared", fake_run_prepared)
    prior_registry_record = workflow_service.run_registry.get(run_id)

    def restore_registry_entry():
        if prior_registry_record is None:
            workflow_service.run_registry.pop(run_id, None)
        else:
            workflow_service.run_registry[run_id] = prior_registry_record

    request.addfinalizer(restore_registry_entry)

    response = api.client.post(
        "/workflows/runs",
        json={
            "name_or_path": "wf",
            "inputs": {},
            "run_id": run_id,
            "prepared_id": prepared["prepared_id"],
            "expected_plan_id": prepared["plan_id"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["run_id"] == run_id
    assert "credential" not in response.json()

    credential = observed["credential"]
    assert isinstance(credential, str) and credential
    assert observed["record"].run_capability_required is True
    issued = api.origins.authenticate_run_capability(run_id, 1, credential)
    assert issued.id == api.subject.id

    with api.context.repo.read_snapshot() as connection:
        row = connection.execute(
            "SELECT revision,credential_sha256,state FROM work_workflow_run_capabilities "
            "WHERE run_id=%s",
            (run_id,),
        ).fetchone()
    assert row[0] == 1 and row[2] == "active"
    assert row[1] == hashlib.sha256(credential.encode("ascii")).hexdigest()

    replacement = api.origins.create_run_capability(
        api.subject,
        run_id=run_id,
        workflow_id="wf",
        tier="script",
        run_generation=1,
        spec_hash=prepared["source_hash"],
        ttl_seconds=3600,
    )
    assert api.origins.authenticate_run_capability(run_id, 1, replacement).id == api.subject.id

    terminal = AsyncMock()
    monkeypatch.setattr(main, "run_agent_step", terminal)
    rejected = api.client.post(
        "/terminals/run-step",
        headers={"X-CAO-Workflow-Run-Credential": credential},
        json={
            "provider": "mock_cli",
            "agent": "developer",
            "prompt": "must not run through the browser cookie",
            "env_vars": {
                "CAO_WORKFLOW_RUN_ID": run_id,
                "CAO_WORKFLOW_GENERATION": "1",
                "CAO_WORKFLOW_STEP_ID": "runtime-step",
            },
        },
    )

    assert rejected.status_code == 401, rejected.text
    assert "invalid" in rejected.json()["detail"]
    terminal.assert_not_awaited()
    assert workflow_journal.get_run(run_id) is not None
    with api.context.repo.read_snapshot() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM work_items WHERE id=%s", (run_id,)).fetchone()[
                0
            ]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM workflow_plan_step_alias WHERE run_id=%s", (run_id,)
            ).fetchone()[0]
            == 0
        )
