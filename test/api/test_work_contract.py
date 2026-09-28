"""Read-only work routes expose one durable snapshot and enforce job ownership."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from cli_agent_orchestrator.api.main import app
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work import EventPage, WorkView
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence


@pytest.fixture
def durable_work(tmp_path, monkeypatch):
    path = tmp_path / "work.sqlite3"
    monkeypatch.setattr("cli_agent_orchestrator.constants.DATABASE_FILE", path)
    principal = auth._verified_principal("https://issuer.test/", "owner", [auth.SCOPE_READ], "jwt")
    app.dependency_overrides[auth.get_current_principal] = lambda: principal
    repository = WorkRepository(path)
    repository.initialize()
    job = repository.create_job(
        project_id="project",
        principal_id=principal.id,
        allowed_providers=["mock_cli"],
        grant_id="root",
    )
    work = repository.admit_work(
        job_id=job["id"],
        operation_kind="launch",
        idempotency_key="request",
        request_hash="a" * 64,
        contract_id="contract",
        snapshot_id=None,
        provider="mock_cli",
        actor_id=principal.id,
    )
    yield repository, principal, job, work
    app.dependency_overrides.pop(auth.get_current_principal, None)


def test_get_work_is_versioned_durable_and_never_infers_success(client, durable_work):
    repository, principal, job, work = durable_work
    attempt = work["attempts"][-1]
    repository.transition_attempt(
        attempt_id=attempt["id"],
        generation=1,
        expected_revision=1,
        expected_state="planned",
        target="sent",
        actor_id=principal.id,
        event_id="sent",
        evidence=TransitionEvidence(
            generation=1,
            expected_generation=1,
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        ),
    )
    response = client.get(f"/work-items/{work['id']}")
    assert response.status_code == 200
    view = WorkView.model_validate(response.json())
    assert view.schema_version == 1
    assert view.work_state == "running" and view.attempt_state == "sent"
    assert view.turn_state == "input_sent" and view.process_state == "unknown"
    assert view.result_ref is None
    assert "request_hash" not in response.json()


def test_event_page_preserves_retention_gaps_and_cursor(client, durable_work):
    repository, principal, job, work = durable_work
    repository.prune_events(job["id"], through_sequence=1)
    response = client.get(f"/jobs/{job['id']}/events", params={"after_sequence": 0, "limit": 1})
    assert response.status_code == 200
    page = EventPage.model_validate(response.json())
    assert page.gaps == [{"from_sequence": 1, "through_sequence": 1}]
    assert len(page.events) == 1 and page.next_cursor == page.events[-1].sequence
    assert page.high_water >= page.next_cursor


@pytest.mark.parametrize("path", ["work", "events"])
def test_other_principal_cannot_read_or_spoof_owner(client, durable_work, path):
    repository, principal, job, work = durable_work
    other = auth._verified_principal("https://issuer.test/", "other", [auth.SCOPE_ADMIN], "jwt")
    app.dependency_overrides[auth.get_current_principal] = lambda: other
    url = f"/work-items/{work['id']}" if path == "work" else f"/jobs/{job['id']}/events"
    assert client.get(url, params={"caller_id": principal.id}).status_code == 404


def test_owner_without_read_scope_is_forbidden(client, durable_work):
    repository, principal, job, work = durable_work
    actor = auth._verified_principal(principal.issuer, principal.subject, [], "jwt")
    app.dependency_overrides[auth.get_current_principal] = lambda: actor
    assert client.get(f"/work-items/{work['id']}").status_code == 403


def test_schema_damage_is_unavailable_not_empty_success(client, durable_work):
    repository, principal, job, work = durable_work
    with sqlite3.connect(repository.path) as connection:
        connection.execute("UPDATE work_migrations SET checksum='tampered'")
    response = client.get(f"/work-items/{work['id']}")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "work_store_unavailable"
    assert response.json()["detail"]["retryable"] is True
    assert response.json()["detail"]["required_action"] == "retry_query"
    assert response.json()["detail"]["message"]
    assert str(repository.path) not in response.text


def test_event_cursor_limits_are_explicit(client, durable_work):
    repository, principal, job, work = durable_work
    url = f"/jobs/{job['id']}/events"
    assert client.get(url, params={"limit": 1001}).status_code == 422
    assert client.get(url, params={"after_sequence": -1}).status_code == 422
    assert client.get(url, params={"after_sequence": 999}).status_code == 409


def test_unverified_remote_reader_is_rejected(durable_work):
    repository, principal, job, work = durable_work
    app.dependency_overrides.pop(auth.get_current_principal)
    remote_client = TestClient(app, base_url="http://127.0.0.1", client=("192.0.2.1", 50000))
    try:
        assert remote_client.get(f"/work-items/{work['id']}").status_code == 401
    finally:
        remote_client.close()


def test_uncertain_delivery_does_not_claim_provider_is_blocked(client, durable_work):
    from cli_agent_orchestrator.services.work_service import WorkService, DeliveryUncertain

    repository, principal, job, work = durable_work

    def uncertain_send():
        raise TimeoutError("worker may still be running")

    with pytest.raises(DeliveryUncertain):
        WorkService(repository).dispatch(
            work["id"],
            uncertain_send,
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
    response = client.get(f"/work-items/{work['id']}")
    assert response.status_code == 200
    assert response.json()["work_state"] == "reconcile"
    assert response.json()["turn_state"] is None
    assert response.json()["required_action"] == "reconcile_attempt"
