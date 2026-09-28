"""T019 integration boundary for legacy child and Work authority."""

from types import SimpleNamespace

import httpx
import pytest

from cli_agent_orchestrator.api import main as api
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_service import WorkService


@pytest.mark.asyncio
async def test_run_step_passes_verified_principal_and_ignores_forged_caller_id(
    tmp_path, monkeypatch
):
    """Breaks if the legacy terminal ID is all the authority Work receives."""
    monkeypatch.setenv("AUTH0_DOMAIN", "issuer.test")

    principal = auth._verified_principal(
        "https://issuer.test", "delegation-requester", [auth.SCOPE_WRITE], "jwt"
    )
    repository = WorkRepository(tmp_path / "delegation.sqlite3")
    repository.initialize()
    calls = []

    async def run_step_double(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(terminal_id="f00d1234", last_message="done", status="COMPLETED")

    monkeypatch.setattr(api, "run_agent_step", run_step_double)
    monkeypatch.setattr(api, "get_plugin_registry", lambda _request: None)

    async def current_principal(_request, authorization=None):
        return principal

    monkeypatch.setattr(api, "get_current_principal", current_principal)
    overrides_before = api.app.dependency_overrides.copy()
    api.app.dependency_overrides[auth.get_current_principal] = lambda: principal
    api.app.dependency_overrides[auth.get_current_scopes] = lambda: [auth.SCOPE_WRITE]
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://127.0.0.1"
        ) as client:
            response = await client.post(
                "/terminals/run-step",
                json={
                    "provider": "mock_cli",
                    "agent": "worker",
                    "prompt": "perform delegated work",
                    "caller_id": "forged-parent-terminal",
                    "teardown": False,
                },
            )
    finally:
        api.app.dependency_overrides.clear()
        api.app.dependency_overrides.update(overrides_before)

    assert response.status_code == 200, response.text
    assert len(calls) == 1
    with repository.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM work_items").fetchone()[0] == 0
        assert (
            connection.execute("SELECT count(*) FROM work_child_origin_bindings").fetchone()[0] == 0
        )
        assert (
            connection.execute("SELECT count(*) FROM work_task_received_receipts").fetchone()[0]
            == 0
        )
    received_principal = calls[0].get("principal")
    assert auth.is_verified_principal(received_principal), (
        "run-step must pass the server-verified Principal into delegation; "
        "caller_id is terminal metadata, not Work authority"
    )
    assert received_principal == principal


@pytest.mark.asyncio
async def test_run_step_keeps_legacy_remote_access_without_auth_and_passes_no_principal(
    monkeypatch,
):
    """Breaks if a new Work identity dependency rejects legacy remote callers."""
    for name in ("AUTH0_DOMAIN", "CAO_AUTH_JWKS_URI", "CAO_AUTH_ISSUER", "CAO_AUTH_AUDIENCE"):
        monkeypatch.delenv(name, raising=False)

    calls = []

    async def run_step_double(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(terminal_id="f00d1234", last_message="done", status="COMPLETED")

    monkeypatch.setattr(api, "run_agent_step", run_step_double)
    monkeypatch.setattr(api, "get_plugin_registry", lambda _request: None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app, client=("198.51.100.7", 50000)),
        base_url="http://198.51.100.7",
    ) as client:
        response = await client.post(
            "/terminals/run-step",
            headers={"Host": "localhost"},
            json={
                "provider": "mock_cli",
                "agent": "worker",
                "prompt": "perform legacy work",
                "teardown": False,
            },
        )

    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert calls[0]["principal"] is None


def test_legacy_native_child_acknowledgement_is_not_a_work_task_receipt(tmp_path):
    """A legacy acknowledged row cannot pass the Work receipt type boundary."""
    repository = WorkRepository(tmp_path / "legacy-child-ack.sqlite3")
    repository.initialize()
    service = WorkService(repository)
    legacy_child = {
        "id": "native-child-1",
        "terminal_id": "f00d1234",
        "state": "acknowledged",
    }

    with pytest.raises(TypeError, match="authenticated task receipt required"):
        service.record_task_received(legacy_child)

    with repository.read_snapshot() as connection:
        assert (
            connection.execute("SELECT count(*) FROM work_task_received_receipts").fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM work_events WHERE event_type='attempt.acknowledged'"
            ).fetchone()[0]
            == 0
        )
