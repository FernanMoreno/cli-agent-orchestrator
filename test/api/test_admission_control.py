"""One strict control contract, authenticated independently of legacy scopes."""

import importlib
from contextlib import asynccontextmanager
from dataclasses import asdict
from test.conftest import mint_test_token
from test.services.test_inbox_admission_runtime import operational_fixture
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.inbox_service import InboxService

pytestmark = pytest.mark.offline_database


@pytest.fixture
def control(auth_enabled_env, rsa_keys, monkeypatch):
    import jwt

    public_key = jwt.PyJWK.from_dict(rsa_keys[1].as_dict()).key
    jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=public_key))
    monkeypatch.setattr(auth.get_jwks_cache(), "get_client", lambda _: jwks)
    routes = importlib.import_module("cli_agent_orchestrator.api.admission_routes")
    runtime = importlib.import_module("cli_agent_orchestrator.services.inbox_admission_runtime")
    f = operational_fixture()
    value = runtime.InboxAdmissionRuntime(
        InboxService(),
        monitor=f.monitor,
        bootstrap=f.bootstrap,
        clock=lambda: f.clock[0],
        drain_timeout_s=10,
    )

    @asynccontextmanager
    async def lifespan(app):
        await value.start()
        yield
        await value.stop()

    app = FastAPI(lifespan=lifespan)
    app.include_router(routes.router)
    app.state.inbox_admission_runtime = value
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50101)) as client:
        yield client, f, rsa_keys[0]


def headers(key, scopes="cao:admin"):
    return {"Authorization": "Bearer " + mint_test_token(key, scopes=scopes, subject="operator")}


def test_authenticated_admin_can_read_attest_and_revoke_with_cas(control):
    client, f, key = control
    response = client.get("/admission/resource-envelope", headers=headers(key))
    assert response.status_code == 200
    assert response.json()["revision"] == 1
    claims = asdict(f.record.claims)
    response = client.post(
        "/admission/resource-envelope",
        headers=headers(key),
        json={"expected_revision": 1, "claims": claims},
    )
    assert response.status_code == 200 and response.json() == {"revision": 2, "active": True}
    assert response.headers["cache-control"] == "no-store"
    conflict = client.post(
        "/admission/resource-envelope",
        headers=headers(key),
        json={"expected_revision": 1, "claims": claims},
    )
    assert conflict.status_code == 409 and f.repository.latest.revision == 2
    revoked = client.post(
        "/admission/resource-envelope/revoke", headers=headers(key), json={"expected_revision": 2}
    )
    assert revoked.status_code == 200 and revoked.json() == {"revision": 3, "active": False}


@pytest.mark.parametrize("scopes", ["cao:read", "cao:write"])
def test_nonadmin_cannot_modify_authority(control, scopes):
    client, f, key = control
    response = client.post(
        "/admission/resource-envelope/revoke",
        headers=headers(key, scopes),
        json={"expected_revision": 1},
    )
    assert response.status_code == 403
    assert f.repository.latest.revision == 1


def test_implicit_auth_off_local_admin_does_not_authorize_claims(control, monkeypatch):
    client, f, _ = control
    for name in (
        "AUTH0_DOMAIN",
        "AUTH0_AUDIENCE",
        "CAO_AUTH_AUDIENCE",
        "CAO_AUTH_ISSUER",
        "CAO_AUTH_JWKS_URI",
    ):
        monkeypatch.delenv(name, raising=False)
    response = client.post("/admission/resource-envelope/revoke", json={"expected_revision": 1})
    assert response.status_code in (401, 403)
    assert f.repository.latest.revision == 1


@pytest.mark.parametrize(
    "extra", [{"binding": "private-input-canary"}, {"expected_revision": True}]
)
def test_invalid_control_dto_does_not_echo_untrusted_input(control, extra):
    client, f, key = control
    response = client.post(
        "/admission/resource-envelope/revoke",
        headers=headers(key),
        json={"expected_revision": 1, **extra},
    )
    assert response.status_code == 422
    assert "private-input-canary" not in response.text
    assert f.repository.latest.revision == 1


def test_storage_failure_is_fixed_and_blocks_automatic_authority_retry(control):
    client, f, key = control

    def fail(*_a, **_k):
        raise OSError("private-connection-canary")

    f.repository.append = fail
    response = client.post(
        "/admission/resource-envelope",
        headers=headers(key),
        json={"expected_revision": 1, "claims": asdict(f.record.claims)},
    )
    assert response.status_code == 503 and "private-connection-canary" not in response.text
    again = client.post(
        "/admission/resource-envelope/revoke", headers=headers(key), json={"expected_revision": 1}
    )
    assert again.status_code == 503


@pytest.mark.parametrize("error_type", [ValueError, PermissionError])
def test_server_binding_failures_are_not_reported_as_client_errors(control, error_type):
    client, f, key = control

    def fail(_):
        raise error_type("private-binding-canary")

    f.bootstrap._capture = fail
    response = client.post(
        "/admission/resource-envelope",
        headers=headers(key),
        json={"expected_revision": 1, "claims": asdict(f.record.claims)},
    )
    assert response.status_code == 503
    assert "private-binding-canary" not in response.text
