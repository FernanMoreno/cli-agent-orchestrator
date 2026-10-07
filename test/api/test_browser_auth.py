"""Browser auth verifies origin, credential isolation and sealed operator identity."""

import asyncio

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from cli_agent_orchestrator.security import auth


def test_public_browser_mode_is_available_without_exposing_account():
    from cli_agent_orchestrator.api.main import app

    response = TestClient(app, base_url="http://127.0.0.1:9889").get("/auth/config")
    assert response.status_code == 200
    assert response.json()["mode"] in ("disabled", "bearer", "local_password")
    assert "username" not in response.json()


@pytest.fixture
def browser_app(tmp_path):
    from cli_agent_orchestrator.api.browser_auth_routes import router
    from cli_agent_orchestrator.services.browser_auth import BrowserAuthService

    app = FastAPI()
    binding = auth._verified_principal("urn:cao:test", "operator", ["cao:read"], "jwt")
    service = BrowserAuthService(
        tmp_path / "auth.sqlite3",
        {
            "id": binding.id,
            "issuer": binding.issuer,
            "subject": binding.subject,
            "kind": binding.kind,
            "scopes": list(binding.scopes),
        },
        {"installation_id": "test"},
    )
    service.create_account("operator", "a long private passphrase")
    app.state.browser_auth = service
    app.state.browser_auth_config = {
        "enabled": True,
        "installation_id": "test",
        "canonical_origin": "http://127.0.0.1:9889",
        "transport_policy": "loopback_http",
    }
    app.include_router(router)

    @app.get("/protected")
    async def protected(principal=Depends(auth.get_current_principal)):
        return {"id": principal.id, "kind": principal.kind, "scopes": sorted(principal.scopes)}

    @app.post("/write")
    async def write(scopes=Depends(auth.require_any_scope("cao:write"))):
        return {"ok": True}

    return app, service, binding


def client_for(app):
    return TestClient(
        app,
        base_url="http://127.0.0.1:9889",
        client=("127.0.0.1", 50000),
        headers={"Origin": "http://127.0.0.1:9889", "X-CAO-Browser": "1"},
    )


def login(client, remember=True):
    return client.post(
        "/auth/login",
        json={
            "username": "operator",
            "password": "a long private passphrase",
            "remember": remember,
        },
    )


def test_cookie_login_preserves_identity_scope_and_never_returns_secret(browser_app):
    app, service, binding = browser_app
    with client_for(app) as client:
        response = login(client)
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert "Max-Age=" in cookie and "Domain=" not in cookie
        assert "password" not in response.text and "secret" not in response.text
        principal = client.get("/protected")
        assert principal.status_code == 200
        assert principal.json() == {"id": binding.id, "kind": "jwt", "scopes": ["cao:read"]}
        assert client.post("/write").status_code == 403
        assert "set-cookie" not in client.post("/auth/renew").headers
        assert client.post("/auth/logout").status_code == 204
        assert client.get("/protected").status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://127.0.0.1:9999", "X-CAO-Browser": "1"},
        {"Origin": "null", "X-CAO-Browser": "1"},
        {"X-CAO-Browser": "1"},
        {"Origin": "http://127.0.0.1:9889"},
    ],
)
def test_login_rejects_untrusted_or_missing_browser_origin(browser_app, headers):
    app, _, _ = browser_app
    client = TestClient(
        app, base_url="http://127.0.0.1:9889", client=("127.0.0.1", 50000), headers=headers
    )
    response = login(client)
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers


def test_cookie_cannot_rescue_explicit_invalid_bearer(browser_app, monkeypatch):
    monkeypatch.setenv("CAO_AUTH_JWKS_URI", "http://127.0.0.1/jwks")
    app, _, _ = browser_app
    client = client_for(app)
    assert login(client).status_code == 200
    assert client.get("/protected", headers={"Authorization": "Bearer invalid"}).status_code == 401


def test_temporary_cookie_and_password_validation_do_not_leak(browser_app):
    app, _, _ = browser_app
    client = client_for(app)
    response = login(client, False)
    assert response.status_code == 200
    assert "Max-Age" not in response.headers["set-cookie"]
    assert "expires=" not in response.headers["set-cookie"].lower()
    response = client.post(
        "/auth/login", json={"username": "operator", "password": ["sensitive"], "remember": True}
    )
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert "sensitive" not in response.text
    response = client.post(
        "/auth/login", content=b"x" * 4097, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


def test_logout_old_response_cannot_delete_new_cookie(browser_app):
    app, _, _ = browser_app
    client = client_for(app)
    assert login(client).status_code == 200
    response = client.post("/auth/logout")
    assert "set-cookie" not in response.headers
    assert login(client).status_code == 200
    assert client.get("/auth/session").status_code == 200


def test_production_startup_accepts_locally_created_default_operator(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path

    from cli_agent_orchestrator.api.browser_auth_routes import configure_browser_auth

    path = Path(__file__).resolve().parents[2] / "scripts/personal_deployment.py"
    spec = importlib.util.spec_from_file_location("browser_deployment_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "personal"
    module.initialize(root, server_port=19889, jwks_port=19890, image_id="sha256:" + "a" * 64)
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: "a long private passphrase")
    module.account_create(root, username="operator")
    for key, value in module.environment(root).items():
        monkeypatch.setenv(key, value)
    app = FastAPI()
    configure_browser_auth(app)
    assert app.state.browser_auth_config["enabled"] is True
    secret, dto = app.state.browser_auth.login(
        "operator", "a long private passphrase", True, "local"
    )
    assert dto["username"] == "operator"
    assert app.state.browser_auth.identity(secret)["kind"] == "jwt"
