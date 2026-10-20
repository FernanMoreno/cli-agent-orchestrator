"""Private, approved plan-v2 setup for script-runner E2E cases.

The scripts still run as real child processes. This helper only supplies the
same durable prepare/review/approval state required by the production prepared
runner path; it does not replace the approval gate or script subprocess.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from cli_agent_orchestrator.models.workflow_runtime import WorkflowRunResult


def _with_scope(source: str, *, agent_profile: str) -> str:
    tree = ast.parse(source)
    if any(
        isinstance(statement, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "SCOPE" for target in statement.targets
        )
        for statement in tree.body
    ):
        raise ValueError("E2E script already declares SCOPE")

    insertion_line = 0
    for statement in tree.body:
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            if isinstance(statement.value.value, str) and statement.lineno == 1:
                insertion_line = statement.end_lineno or statement.lineno
                continue
        if isinstance(statement, ast.ImportFrom) and statement.module == "__future__":
            insertion_line = statement.end_lineno or statement.lineno

    declaration = {
        "version": 1,
        "targets": {
            "repo": {"agents": [agent_profile], "memory": "exact-snapshot"},
        },
    }
    lines = source.splitlines(keepends=True)
    assignment = f"SCOPE = {declaration!r}\n"
    return "".join((*lines[:insertion_line], assignment, *lines[insertion_line:]))


def _install_raw_http_capability_adapter(source: str) -> str:
    """Add the test-only auth header bridge used by the raw urllib example.

    The example intentionally has no CAO shim import. The prepared runner still
    supplies its one-run capability over the inherited descriptor, so this tiny
    adapter adds that capability to the example's existing urllib requests.
    It never writes the capability to output or a file.
    """
    tree = ast.parse(source)
    insertion_line = 0
    for statement in tree.body:
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            if isinstance(statement.value.value, str) and statement.lineno == 1:
                insertion_line = statement.end_lineno or statement.lineno
                continue
        if isinstance(statement, ast.ImportFrom) and statement.module == "__future__":
            insertion_line = statement.end_lineno or statement.lineno

    adapter = """
import os as _cao_test_os
import urllib.request as _cao_test_urllib_request

_cao_test_run_capability = None
_cao_test_original_urlopen = _cao_test_urllib_request.urlopen

def _cao_test_authorized_urlopen(request, timeout=None):
    global _cao_test_run_capability
    if _cao_test_run_capability is None:
        descriptor = _cao_test_os.environ.pop("CAO_WORKFLOW_AUTH_FD", None)
        if descriptor is None or not descriptor.isdecimal():
            raise RuntimeError("prepared run capability descriptor is missing")
        descriptor_fd = int(descriptor)
        try:
            capability = _cao_test_os.read(descriptor_fd, 4096).decode("ascii")
        finally:
            _cao_test_os.close(descriptor_fd)
        if not 32 <= len(capability) <= 256 or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
            for character in capability
        ):
            raise RuntimeError("prepared run capability is invalid")
        _cao_test_run_capability = capability
    request.add_header("X-CAO-Workflow-Run-Credential", _cao_test_run_capability)
    return _cao_test_original_urlopen(request, timeout=timeout)

_cao_test_urllib_request.urlopen = _cao_test_authorized_urlopen
"""
    lines = source.splitlines(keepends=True)
    return "".join((*lines[:insertion_line], adapter, *lines[insertion_line:]))


def _initialize_git_target(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "prepared E2E baseline",
            "-q",
        ],
        check=True,
    )


def _provision_seed(context, *, workflow_name: str, source_hash: str, agent_profile: str):
    """Derive a source-bound seed from the existing Work fixture authorities."""
    from test.services.test_work_workflow import _provisioned_workflow

    from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope
    from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning

    origins, _authority, subject, _authorization, selector, _ref = _provisioned_workflow(context)
    existing = WorkProvisioning(context.repo).resolve_workflow_step(
        subject,
        workflow_id="wf",
        step_id="build",
        spec_hash=selector["spec_hash"],
    )
    if existing is None:
        raise AssertionError("base workflow fixture did not create its exact seed")

    delivery = WorkDeliveryEnvelope(
        operation_kind="agent_step",
        adapter_version=1,
        payload_json=json.dumps(
            {
                "terminal_id": "a1b2c3d4",
                "agent_profile": agent_profile,
                "message": "prepared E2E seed",
            },
            separators=(",", ":"),
        ),
    )
    WorkProvisioning(context.repo).provision_workflow_step(
        context.actor,
        subject=subject,
        workflow_id=workflow_name,
        step_id="seed",
        expected_revision=0,
        spec_hash=source_hash,
        job_id=existing.job_id,
        grant_id=existing.grant_id,
        grant_revision=existing.grant_revision,
        subject_ref=existing.workflow_subject_ref,
        authorization_ref=existing.workflow_authorization_ref,
        receiver_subject_ref=existing.receiver_subject_ref,
        receiver_authorization_ref=existing.receiver_authorization_ref,
        contract=existing.contract,
        delivery=delivery,
        lease_seconds=300,
    )
    return origins, subject


async def run_prepared_script(
    context,
    tmp_path: Path,
    monkeypatch,
    *,
    source: str,
    run_id: str,
    agent_profile: str = "developer",
    raw_http_capability_adapter: bool = False,
) -> WorkflowRunResult:
    """Drive a real child script through public prepare/review/approve/start APIs."""
    from cli_agent_orchestrator.api import main as api
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services import (
        script_runner,
        workflow_service,
        workflow_spec_service,
    )
    from cli_agent_orchestrator.services.work_workflow_plans import WorkWorkflowPlans

    if raw_http_capability_adapter:
        source = _install_raw_http_capability_adapter(source)
    source = _with_scope(source, agent_profile=agent_profile)
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    workflow_name = "wf"
    spec_directory = tmp_path / "workflow-specs"
    spec_directory.mkdir(parents=True)
    (spec_directory / f"{workflow_name}.py").write_text(source, encoding="utf-8")

    _initialize_git_target(context.root)
    origins, subject = _provision_seed(
        context,
        workflow_name=workflow_name,
        source_hash=source_hash,
        agent_profile=agent_profile,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.utils.agent_profiles._read_agent_profile_source",
        lambda name: f"---\nname: {name}\nallowedTools: []\n---\nPrepared E2E profile\n",
    )
    # The public API resolves a bare workflow name through this configured
    # directory. Keeping it private avoids reading or writing the operator's
    # workflow profile.
    monkeypatch.setattr(workflow_spec_service, "WORKFLOW_SPEC_DIR", spec_directory)
    monkeypatch.setattr(
        script_runner,
        "WORKFLOW_SCRIPT_SCRATCH_DIR",
        tmp_path / "private-script-scratch",
    )

    plans = WorkWorkflowPlans(context.repo)
    origins.plans = plans
    plans.origins = origins
    monkeypatch.setattr(api.app.state, "work_workflow_origins", origins, raising=False)
    monkeypatch.setitem(api.app.dependency_overrides, auth.get_current_principal, lambda: subject)
    monkeypatch.setitem(
        api.app.dependency_overrides,
        auth.get_current_scopes,
        lambda: [auth.SCOPE_WRITE, auth.SCOPE_ADMIN],
    )
    workflow_service.run_registry.pop(run_id, None)

    client = TestClient(api.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    prepared_response = client.post(
        "/workflows/plans:prepare",
        json={
            "name_or_path": workflow_name,
            "inputs": {},
            "target_mappings": {"repo": str(context.root)},
            "binding_selections": {"repo": {agent_profile: "seed"}},
        },
    )
    assert prepared_response.status_code == 201, prepared_response.text
    prepared = prepared_response.json()

    reviewed_response = client.get(f"/workflows/plans/{prepared['prepared_id']}")
    assert reviewed_response.status_code == 200, reviewed_response.text
    reviewed = reviewed_response.json()
    if reviewed["plan_id"] != prepared["plan_id"]:
        raise AssertionError("public review returned a different plan identity")

    approval_response = client.post(
        "/workflows/plans/approve", json={"plan_id": prepared["plan_id"]}
    )
    assert approval_response.status_code == 200, approval_response.text
    assert approval_response.json()["approved"] is True

    start_response = client.post(
        "/workflows/runs",
        json={
            "name_or_path": workflow_name,
            "inputs": {},
            "run_id": run_id,
            "prepared_id": prepared["prepared_id"],
            "expected_plan_id": prepared["plan_id"],
        },
    )
    assert start_response.status_code == 200, start_response.text
    return WorkflowRunResult.model_validate(start_response.json())
