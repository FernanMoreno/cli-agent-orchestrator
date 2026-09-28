"""Pure transition rules: observations cannot fabricate execution evidence."""

import importlib
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def domain():
    name = "cli_agent_orchestrator.services.work_reducer"
    assert importlib.util.find_spec(name) is not None, "durable work reducer is missing"
    return importlib.import_module(name)


def evidence(domain, **overrides):
    values = dict(generation=1, expected_generation=1)
    values.update(overrides)
    return domain.TransitionEvidence(**values)


def test_admission_requires_all_confirmations(domain):
    for absent in (
        "contract_confirmed",
        "grant_confirmed",
        "capacity_confirmed",
        "reservations_confirmed",
    ):
        values = dict.fromkeys(
            (
                "contract_confirmed",
                "grant_confirmed",
                "capacity_confirmed",
                "reservations_confirmed",
            ),
            True,
        )
        values[absent] = False
        with pytest.raises(domain.TransitionConflict):
            domain.transition("queued", "running", evidence=evidence(domain, **values))
    assert (
        domain.transition(
            "queued",
            "running",
            evidence=evidence(
                domain,
                contract_confirmed=True,
                grant_confirmed=True,
                capacity_confirmed=True,
                reservations_confirmed=True,
            ),
        )
        == "running"
    )


@pytest.mark.parametrize("state", ["succeeded", "failed", "cancelled"])
@pytest.mark.parametrize("target", ["queued", "running", "reconcile", "succeeded"])
def test_settled_work_cannot_be_revived(domain, state, target):
    with pytest.raises(domain.TransitionConflict):
        domain.transition(
            state,
            target,
            evidence=evidence(
                domain, result_durable=True, result_validated=True, children_settled=True
            ),
        )


def test_success_needs_retrievable_validated_result_and_join(domain):
    for flags in (
        {},
        {"result_durable": True},
        {"result_validated": True},
        {"result_durable": True, "result_validated": True},
    ):
        with pytest.raises(domain.TransitionConflict):
            domain.transition("running", "succeeded", evidence=evidence(domain, **flags))
    assert (
        domain.transition(
            "waiting_children",
            "succeeded",
            evidence=evidence(
                domain, result_durable=True, result_validated=True, children_settled=True
            ),
        )
        == "succeeded"
    )


def test_generation_fences_even_cancellation(domain):
    with pytest.raises(domain.TransitionConflict):
        domain.transition("running", "cancelled", evidence=evidence(domain, expected_generation=2))
    assert domain.transition("running", "cancelled", evidence=evidence(domain)) == "cancelled"


def test_known_pre_dispatch_failure_does_not_fabricate_running(domain):
    assert domain.transition("queued", "failed", evidence=evidence(domain)) == "failed"
    with pytest.raises(domain.TransitionConflict):
        domain.transition("queued", "failed", evidence=evidence(domain, expected_generation=2))


def test_uncertain_delivery_cannot_return_to_queue(domain):
    assert domain.transition("running", "reconcile", evidence=evidence(domain)) == "reconcile"
    with pytest.raises(domain.TransitionConflict):
        domain.transition("reconcile", "running", evidence=evidence(domain))
    assert (
        domain.transition(
            "reconcile",
            "queued",
            evidence=evidence(domain, prior_stopped=True, reconciliation_authorized=True),
        )
        == "queued"
    )


def test_legacy_idle_is_not_work_success(domain):
    with pytest.raises(ValueError):
        domain.transition("idle", "succeeded", evidence=evidence(domain))


def test_delivery_ack_requires_task_receipt_not_terminal_allocation(domain):
    with pytest.raises(domain.TransitionConflict):
        domain.delivery_transition("sent", "acknowledged", evidence=evidence(domain))
    assert (
        domain.delivery_transition(
            "sent", "acknowledged", evidence=evidence(domain, task_received=True)
        )
        == "acknowledged"
    )
    with pytest.raises(domain.TransitionConflict):
        domain.delivery_transition("sent", "running", evidence=evidence(domain, task_received=True))
    assert (
        domain.delivery_transition(
            "sent", "running", evidence=evidence(domain, task_received=True, execution_started=True)
        )
        == "running"
    )


def test_delivery_cannot_resend_or_finish_without_result(domain):
    for source, target in (
        ("sent", "planned"),
        ("reconcile", "sent"),
        ("finished", "running"),
        ("running", "finished"),
    ):
        with pytest.raises(domain.TransitionConflict):
            domain.delivery_transition(source, target, evidence=evidence(domain))


@pytest.mark.parametrize(
    "flags", [{}, {"prior_stopped": True}, {"reconciliation_authorized": True}]
)
def test_reconcile_needs_both_authorization_and_stopped_prior(domain, flags):
    with pytest.raises(domain.TransitionConflict):
        domain.transition("reconcile", "queued", evidence=evidence(domain, **flags))


@pytest.mark.parametrize("generation", [0, -1, True, "1"])
def test_fences_reject_invalid_generations(domain, generation):
    with pytest.raises(ValueError):
        evidence(domain, generation=generation)


def test_delivery_finish_needs_validated_durable_result(domain):
    assert (
        domain.delivery_transition(
            "running",
            "finished",
            evidence=evidence(domain, result_durable=True, result_validated=True),
        )
        == "finished"
    )
    with pytest.raises(domain.TransitionConflict):
        domain.delivery_transition(
            "running",
            "finished",
            evidence=evidence(
                domain, result_durable=True, result_validated=True, expected_generation=2
            ),
        )


def test_public_dtos_read_shared_fixture_and_keep_state_levels_separate(domain):
    models = importlib.import_module("cli_agent_orchestrator.models.work")
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/work_contract_v1.json").read_text())
    view = models.WorkView.model_validate(fixture["views"][0])
    assert view.work_state == "running"
    assert view.turn_state == "ready"
    assert models.EventPage.model_validate(fixture["event_page"]).next_cursor == 1
    with pytest.raises(ValueError):
        models.WorkView.model_validate({**fixture["views"][0], "work_state": "idle"})
    for overrides in ({"schema_version": 2}, {"job_id": ""}, {"revision": 0}):
        with pytest.raises(ValueError):
            models.WorkView.model_validate({**fixture["views"][0], **overrides})


def test_entity_identity_and_generation_validation(domain):
    models = importlib.import_module("cli_agent_orchestrator.models.work")
    job = dict(
        id="job-1",
        project_id="project-1",
        principal_id="principal-1",
        allowed_providers=["codex"],
        grant_id="grant-1",
        budget={},
        priority=0,
    )
    assert models.Job(**job).state == "planning"
    work = dict(
        id="work-1",
        job_id="job-1",
        operation_kind="step",
        idempotency_key="key-1",
        request_hash="a" * 64,
        contract_id="contract-1",
        snapshot_id="snapshot-1",
    )
    assert models.WorkItem(**work).state == "queued"
    attempt = dict(
        id="attempt-1",
        work_item_id="work-1",
        attempt_number=1,
        generation=1,
        provider="codex",
        lease_expires_at="2026-09-22T01:00:00Z",
    )
    assert models.WorkAttempt(**attempt).state == "planned"
    for overrides in (
        {"generation": 0},
        {"attempt_number": -1},
        {"id": " "},
        {"schema_version": 2},
    ):
        with pytest.raises(ValueError):
            models.WorkAttempt(**{**attempt, **overrides})


@pytest.mark.parametrize("state", ["planned", "sent", "acknowledged", "running", "reconcile"])
def test_attempt_cancellation_is_generation_fenced(domain, state):
    assert domain.delivery_transition(state, "cancelled", evidence=evidence(domain)) == "cancelled"
    with pytest.raises(domain.TransitionConflict):
        domain.delivery_transition(
            state, "cancelled", evidence=evidence(domain, expected_generation=2)
        )


@pytest.mark.parametrize(
    "target",
    ["planned", "sent", "acknowledged", "running", "finished", "failed", "reconcile", "cancelled"],
)
def test_cancelled_attempt_cannot_change_winner(domain, target):
    with pytest.raises(domain.TransitionConflict):
        domain.delivery_transition(
            "cancelled",
            target,
            evidence=evidence(
                domain,
                result_durable=True,
                result_validated=True,
                task_received=True,
                execution_started=True,
            ),
        )


@pytest.mark.parametrize("lease", [None, "2026-09-22T01:00:00"])
def test_attempt_requires_an_aware_lease(domain, lease):
    models = importlib.import_module("cli_agent_orchestrator.models.work")
    with pytest.raises(ValueError):
        models.WorkAttempt(
            id="attempt-1",
            work_item_id="work-1",
            attempt_number=1,
            generation=1,
            provider="codex",
            lease_expires_at=lease,
        )


def test_attempt_requires_lease_and_validates_revision_and_cleanup(domain):
    models = importlib.import_module("cli_agent_orchestrator.models.work")
    base = dict(
        id="attempt-1", work_item_id="work-1", attempt_number=1, generation=1, provider="codex"
    )
    with pytest.raises(ValueError):
        models.WorkAttempt(**base)
    attempt = models.WorkAttempt(**base, lease_expires_at="2026-09-22T01:00:00Z")
    assert attempt.revision == 1
    assert attempt.cleanup_state == "not_requested"
    with pytest.raises(ValueError):
        models.WorkAttempt(**base, lease_expires_at="2026-09-22T01:00:00Z", revision=0)


@pytest.mark.parametrize("request_hash", ["hash-1", "A" * 64, "a" * 63, "a" * 65, "g" * 64])
def test_work_request_hash_must_be_lowercase_sha256(domain, request_hash):
    models = importlib.import_module("cli_agent_orchestrator.models.work")
    with pytest.raises(ValueError):
        models.WorkItem(
            id="work-1",
            job_id="job-1",
            operation_kind="step",
            idempotency_key="key-1",
            request_hash=request_hash,
            contract_id="contract-1",
            snapshot_id="snapshot-1",
        )


def test_absent_snapshot_is_explicitly_representable(domain):
    models = importlib.import_module("cli_agent_orchestrator.models.work")
    item = models.WorkItem(
        id="work-1",
        job_id="job-1",
        operation_kind="step",
        idempotency_key="key-1",
        request_hash="a" * 64,
        contract_id="contract-1",
        snapshot_id=None,
    )
    assert item.snapshot_id is None


def test_attempt_delivery_phase_is_derived_from_authoritative_state(domain):
    models = importlib.import_module("cli_agent_orchestrator.models.work")
    attempt = models.WorkAttempt(
        id="attempt-1",
        work_item_id="work-1",
        attempt_number=1,
        generation=1,
        provider="codex",
        lease_expires_at="2026-09-22T01:00:00Z",
        state="sent",
    )
    assert attempt.delivery_phase == "sent"
    assert attempt.model_dump(mode="json")["delivery_phase"] == "sent"
    with pytest.raises(ValueError):
        models.WorkAttempt(
            id="attempt-1",
            work_item_id="work-1",
            attempt_number=1,
            generation=1,
            provider="codex",
            lease_expires_at="2026-09-22T01:00:00Z",
            state="sent",
            delivery_phase="planned",
        )


def test_attempt_serialized_delivery_phase_round_trips_without_authority(domain):
    models = importlib.import_module("cli_agent_orchestrator.models.work")
    attempt = models.WorkAttempt(
        id="attempt-1",
        work_item_id="work-1",
        attempt_number=1,
        generation=1,
        provider="codex",
        lease_expires_at="2026-09-22T01:00:00Z",
        state="sent",
    )
    serialized = attempt.model_dump(mode="json")

    round_tripped = models.WorkAttempt.model_validate(serialized)

    assert round_tripped.state == "sent"
    assert round_tripped.delivery_phase == "sent"
    with pytest.raises(ValueError):
        models.WorkAttempt.model_validate({**serialized, "delivery_phase": "planned"})
