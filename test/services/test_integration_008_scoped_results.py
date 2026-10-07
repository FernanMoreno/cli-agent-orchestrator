"""Actual composed v2 Work results retain their scoped binding owner."""

import asyncio
import os
from test.integration.test_work_dispatch import context  # noqa:F401
from test.services.test_integration_008_prepared_plans import plan_context, started
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.models.work_origin import WorkflowStepResultV1
from cli_agent_orchestrator.services import workflow_journal
from cli_agent_orchestrator.services.work_origin import WorkOrigins


@pytest.fixture
def scoped_result_context(plan_context):
    from cli_agent_orchestrator.api.main import _compose_managed_workflow_runtime

    _old_plans, subject, directory, context = plan_context
    context.service.workflow_origins = None  # model a fresh server composition
    runtime = SimpleNamespace(
        _admission=context.service, repository=context.repo, origins=WorkOrigins(context.repo)
    )
    gateway = SimpleNamespace(_launch_runtime_provider=SimpleNamespace(_runtime=runtime))
    application = SimpleNamespace(state=SimpleNamespace())
    projector = _compose_managed_workflow_runtime(application, context.repo, gateway)
    plans = application.state.work_workflow_origins.plans
    result = started((plans, subject, directory, context), run_id="scoped-result")
    request = dict(
        provider="mock_cli", agent="developer", prompt="Return exact approved task result"
    )
    callback = plans.authorize_step(subject, "scoped-result", "dynamic", "repo", request)
    now = "2026-10-02T00:00:00Z"
    workflow_journal.insert_steps("scoped-result", [("dynamic", "pending")], now)
    workflow_journal.mark_work_pending(
        run_id="scoped-result",
        step_id="dynamic",
        generation="1",
        step_attempt=1,
        tier="script",
        updated_at=now,
    )
    work = asyncio.run(
        callback(
            tier="script",
            run_id="scoped-result",
            run_generation=1,
            step_id="dynamic",
            step_attempt=1,
            prompt=request["prompt"],
        )
    )
    binding = plans.origins.read_step_binding("script", "scoped-result", 1, "dynamic", 1)
    return SimpleNamespace(
        plans=plans,
        subject=subject,
        context=context,
        service=application.state.work_workflow_result_service,
        origins=runtime.origins,
        binding=binding,
        work=work,
        projector=projector,
    )


def test_composed_v2_result_service_reads_actual_durable_work_state(scoped_result_context):
    fixture = scoped_result_context
    state = fixture.service.read_workflow_step_state(fixture.binding)
    assert state.work_item_id == fixture.work["id"]
    assert state.accepted_result_id is None
    assert state.work_state == "queued"


@pytest.mark.parametrize(
    "after_acceptance", ["unchanged", "origin_revoked", "grant_revoked", "profile_context_drift"]
)
def test_composed_v2_receipt_result_and_projection_survive_service_restart(
    scoped_result_context, monkeypatch, after_acceptance
):
    fixture = scoped_result_context
    prepared = fixture.context.service._prepare_dispatch(registered_only=False)
    assert prepared is not None
    _dispatch_binding, port, _sent = prepared
    try:
        attempt_credential = os.pread(port._attempt_credential_fd, 32, 0)
        receiver_credential = os.pread(port._receiver_credential_fd, 32, 0)
    finally:
        port.close()
    received = fixture.origins.accept_task_received_with_credentials(
        attempt_credential=attempt_credential, receiver_credential=receiver_credential
    )
    assert received["attempts"][-1]["state"] == "acknowledged"
    envelope = WorkflowStepResultV1.from_payload(
        {"schema_version": 1, "status": "completed", "output": {"answer": 42}}
    )
    settled = fixture.service.submit_workflow_step_result(
        attempt_credential=attempt_credential,
        receiver_credential=receiver_credential,
        result=envelope,
    )
    assert settled["state"] == "succeeded"
    accepted = fixture.service.read_accepted_workflow_result(fixture.binding)
    assert accepted.canonical_bytes == envelope.canonical_bytes()
    if after_acceptance == "origin_revoked":
        from cli_agent_orchestrator.services.work_origin import WorkOriginAuthority

        WorkOriginAuthority(fixture.context.repo).revoke(
            owner=fixture.context.actor,
            subject=fixture.subject,
            origin_kind="workflow",
            expected_revision=1,
        )
    elif after_acceptance == "grant_revoked":
        from cli_agent_orchestrator.services.work_authority import WorkAuthority

        WorkAuthority(fixture.context.repo).revoke(
            fixture.context.actor,
            grant_id=fixture.binding.grant_id,
            expected_grant_revision=fixture.binding.grant_revision,
            reason="independent accepted history proof",
        )
    elif after_acceptance == "profile_context_drift":
        monkeypatch.setattr(
            "cli_agent_orchestrator.utils.agent_profiles._read_agent_profile_source",
            lambda name: "---\nname: developer\nallowedTools: []\n---\nChanged live instructions after accepted result\n",
        )
    assert fixture.service.read_accepted_workflow_result(fixture.binding) == accepted
    assert fixture.projector.project_pending_for_run("scoped-result")[0].status == "projected"
    if after_acceptance != "unchanged":
        with pytest.raises((ValueError, PermissionError, LookupError)):
            fixture.plans.authorize_step(
                fixture.subject,
                "scoped-result",
                "after-revoke",
                "repo",
                dict(provider="mock_cli", agent="developer", prompt="Unpermitted next effect"),
            )
    assert (
        workflow_journal.get_work_step_projection("scoped-result", "dynamic").accepted_result_id
        == accepted.accepted_result_id
    )
    # Recompose against the same durable content namespace, with no old reader cache.
    from cli_agent_orchestrator.api.main import _compose_managed_workflow_runtime
    from cli_agent_orchestrator.services.work_admission import WorkAdmission

    fresh_origins = WorkOrigins(fixture.context.repo)
    fresh_admission = WorkAdmission(
        fixture.context.repo,
        backends=dict(fixture.context.service.backends),
        delivery_adapters=dict(fixture.context.service.deliveries.adapters),
        origins=fresh_origins,
    )
    fresh_runtime = SimpleNamespace(
        _admission=fresh_admission, repository=fixture.context.repo, origins=fresh_origins
    )
    restarted_app = SimpleNamespace(state=SimpleNamespace())
    restarted_projector = _compose_managed_workflow_runtime(
        restarted_app,
        fixture.context.repo,
        SimpleNamespace(_launch_runtime_provider=SimpleNamespace(_runtime=fresh_runtime)),
    )
    assert (
        restarted_app.state.work_workflow_result_service.read_accepted_workflow_result(
            fixture.binding
        )
        == accepted
    )
    assert restarted_projector.project_pending_for_run("scoped-result") == ()
