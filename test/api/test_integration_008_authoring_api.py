"""Authoring endpoints preserve exact source and never execute validation input."""

from unittest.mock import patch


def test_create_uses_server_owned_directory_and_exact_revision(client):
    dto = {"name": "new", "content": "INPUTS = {}\n", "source_hash": "a" * 64}
    with patch(
        "cli_agent_orchestrator.services.workflow_spec_service.create_workflow", return_value=dto
    ) as create:
        response = client.post("/workflows", json={"name": "new", "content": dto["content"]})
    assert response.status_code == 201
    assert response.json() == dto
    create.assert_called_once_with("new", dto["content"])


def test_source_only_validation_never_executes_python(client):
    source = "INPUTS = {}\nraise RuntimeError('must not execute')\n"
    response = client.post("/workflows/validate", json={"name": "new", "content": source})
    assert response.status_code == 200
    assert response.json()["status"] == "pass"


def test_validate_rejects_mixed_sources_without_reading_file(client):
    response = client.post(
        "/workflows/validate",
        json={"path": "/etc/passwd", "name": "new", "content": "INPUTS = {}\n"},
    )
    assert response.status_code == 400
