"""v1 durable-work projection contract at the authenticated HTTP boundary."""

import json
from pathlib import Path

import pytest

from cli_agent_orchestrator.api.main import app
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work import EventPage, WorkView
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence, transition
from cli_agent_orchestrator.services.work_service import DeliveryObservation, WorkService


@pytest.fixture
def work_contract_v1():
    path = Path(__file__).parents[1] / "fixtures" / "work_contract_v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def projected_work(tmp_path, monkeypatch):
    path = tmp_path / "work.sqlite3"
    monkeypatch.setattr("cli_agent_orchestrator.constants.DATABASE_FILE", path)
    principal = auth._verified_principal(
        "https://issuer.test/", "projection-owner", [auth.SCOPE_READ], "jwt"
    )
    app.dependency_overrides[auth.get_current_principal] = lambda: principal
    repository = WorkRepository(path)
    repository.initialize()
    job = repository.create_job(
        project_id="projection-project",
        principal_id=principal.id,
        allowed_providers=["mock_cli"],
        grant_id="projection-grant",
    )
    work = repository.admit_work(
        job_id=job["id"],
        operation_kind="launch",
        idempotency_key="projection-request",
        request_hash="a" * 64,
        contract_id="projection-contract",
        snapshot_id=None,
        provider="mock_cli",
        actor_id=principal.id,
    )
    WorkService(repository).dispatch(
        work["id"],
        lambda: DeliveryObservation(),
        actor_id=principal.id,
        admission=TransitionEvidence(
            generation=1,
            expected_generation=1,
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        ),
    )
    try:
        yield repository, job, work
    finally:
        app.dependency_overrides.pop(auth.get_current_principal, None)


def test_v1_fixture_accepts_nullable_legacy_projection_fields(work_contract_v1):
    """Breaks if v1 clients can no longer parse the fixture's nullable fields or states."""
    view_payload = work_contract_v1["views"][0]

    assert WorkView.model_validate(view_payload).model_dump(mode="json") == {
        "schema_version": work_contract_v1["schema_version"],
        **view_payload,
    }
    assert EventPage.model_validate(work_contract_v1["event_page"]).model_dump(mode="json") == (
        work_contract_v1["event_page"]
    )


def test_get_work_projects_sent_delivery_without_a_receipt_or_terminal_success(
    client, projected_work, work_contract_v1
):
    """Legacy adapter observations cannot manufacture an authenticated receipt."""
    _, job, work = projected_work
    response = client.get(f"/work-items/{work['id']}")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": work_contract_v1["schema_version"],
        "job_id": job["id"],
        "work_item_id": work["id"],
        "attempt_id": work["attempts"][-1]["id"],
        "job_state": "running",
        "work_state": "running",
        "attempt_state": "sent",
        "turn_state": "input_sent",
        "process_state": "unknown",
        "revision": 2,
        "result_ref": None,
        "cleanup_state": "not_requested",
        "required_action": None,
    }


def test_api_projects_each_shared_work_transition_target(client, projected_work, work_contract_v1):
    repository, _, work = projected_work
    fixture = work_contract_v1["presentation_expectations"]
    edges = fixture.get("work_transitions", [])
    assert len(edges) == 15

    for edge in edges:
        evidence = TransitionEvidence(
            generation=1,
            expected_generation=1,
            **edge["evidence"],
        )
        target = transition(edge["from"], edge["to"], evidence=evidence).value

        # Seed this edge's source, then persist the reducer's accepted target so
        # the authenticated HTTP read exercises the public state projection.
        with repository.transaction() as connection:
            connection.execute(
                "UPDATE work_items SET state=?, revision=revision+1 WHERE id=?",
                (edge["from"], work["id"]),
            )
        with repository.transaction() as connection:
            cursor = connection.execute(
                "UPDATE work_items SET state=?, revision=revision+1 WHERE id=? AND state=?",
                (target, work["id"], edge["from"]),
            )
            assert cursor.rowcount == 1

        response = client.get(f"/work-items/{work['id']}")
        assert response.status_code == 200, response.text
        projected = response.json()
        assert projected["work_state"] == edge["to"]
        assert projected["attempt_state"] == "sent"
        assert projected["turn_state"] == "input_sent"
        assert projected["required_action"] == (
            "reconcile_attempt" if edge["to"] == "reconcile" else None
        )
