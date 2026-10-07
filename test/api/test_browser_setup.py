"""First local account setup remains bound to the existing signed operator."""

import importlib.util
import json
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cli_agent_orchestrator.api.browser_auth_routes import configure_browser_auth, router
from cli_agent_orchestrator.security import auth


@pytest.fixture
def setup_app(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts/personal_deployment.py"
    spec = importlib.util.spec_from_file_location("setup_deployment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "personal"
    module.initialize(root, server_port=19889, jwks_port=19890, image_id="sha256:" + "a" * 64)
    for key, value in module.environment(root).items():
        monkeypatch.setenv(key, value)
    # Explicit startup path is available before any browser policy is configured.
    monkeypatch.setenv("CAO_BROWSER_LOGIN_ROOT", str(root))
    cfg = json.loads((root / "deployment.json").read_text())
    key = serialization.load_pem_private_key((root / "issuer.pem").read_bytes(), password=None)

    def verified(token):
        return jwt.decode(
            token,
            key.public_key(),
            algorithms=["RS256"],
            issuer=cfg["issuer"],
            audience=cfg["audience"],
            options={"require": ["iss", "sub", "aud", "exp"]},
        )

    monkeypatch.setattr(auth, "verify_token_claims", verified)
    app = FastAPI()
    configure_browser_auth(app)
    app.include_router(router)
    client = TestClient(
        app,
        base_url="http://127.0.0.1:19889",
        client=("127.0.0.1", 50000),
        headers={"Origin": "http://127.0.0.1:19889", "X-CAO-Browser": "1"},
    )
    return client, root, module, cfg, key


def create(client, token=None, **kwargs):
    headers = {"Authorization": "Bearer " + token} if token else {}
    headers.update(kwargs)
    return client.post(
        "/auth/setup",
        json={
            "username": "felni",
            "password": "TenChars1!",
            "remember": True,
        },
        headers=headers,
    )


def test_owner_can_setup_in_browser_and_immediately_login(setup_app):
    client, root, module, cfg, _ = setup_app
    assert client.get("/auth/config").json()["local_setup_available"] is True
    response = create(client, module.mint_token(root))
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert response.json()["username"] == "felni"
    assert client.get("/auth/config").json()["mode"] == "local_password"
    assert client.get("/auth/session").status_code == 200
    assert create(client, module.mint_token(root)).status_code == 409
    assert module.environment(root)["CAO_BROWSER_LOGIN_ROOT"] == str(root)


@pytest.mark.parametrize(
    "kind", ["missing", "invalid", "wrong_subject", "partial_scopes", "origin"]
)
def test_setup_rejects_without_matching_operator_before_creating_state(setup_app, kind):
    client, root, module, cfg, key = setup_app
    token = module.mint_token(root)
    headers = {}
    if kind == "missing":
        token = None
    if kind == "invalid":
        token = "invalid"
    if kind in ("wrong_subject", "partial_scopes"):
        claims = jwt.decode(token, options={"verify_signature": False})
        if kind == "wrong_subject":
            claims["sub"] = "another-operator"
        else:
            claims["scope"] = "cao:admin"
        token = jwt.encode(claims, key, algorithm="RS256")
    if kind == "origin":
        headers["Origin"] = "http://127.0.0.1:9999"
    response = create(client, token, **headers)
    assert response.status_code in (401, 403)
    assert response.headers["cache-control"] == "no-store"
    assert not (root / "browser-auth.sqlite3").exists()
    assert "browser_login" not in json.loads((root / "deployment.json").read_text())


def test_setup_retry_finishes_interrupted_publication_without_replacing_account(
    setup_app, monkeypatch
):
    from cli_agent_orchestrator.services import browser_setup

    client, root, module, _, _ = setup_app
    original = browser_setup._publish
    calls = 0

    def interrupted(path, deployment):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("interrupted publication")
        return original(path, deployment)

    monkeypatch.setattr(browser_setup, "_publish", interrupted)
    token = module.mint_token(root)
    assert create(client, token).status_code == 503
    assert client.get("/auth/config").json()["local_setup_available"] is True
    assert create(client, token).status_code == 200
    assert client.get("/auth/session").status_code == 200


def test_first_marker_directory_sync_failure_requires_explicit_setup_retry(setup_app, monkeypatch):
    import os
    import stat

    from cli_agent_orchestrator.services import browser_setup

    client, root, module, _, _ = setup_app
    original_fsync = os.fsync
    directory_syncs = 0

    def interrupted(directory):
        nonlocal directory_syncs
        if stat.S_ISDIR(os.fstat(directory).st_mode):
            directory_syncs += 1
            if directory_syncs == 1:
                policy = json.loads((root / "deployment.json").read_text())["browser_login"]
                assert policy["enabled"] is False
                assert policy["provisioning_pending"] is True
                raise OSError("pending marker durability unconfirmed")
        return original_fsync(directory)

    monkeypatch.setattr(browser_setup.os, "fsync", interrupted)
    token = module.mint_token(root)
    response = create(client, token)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "setup_publication_uncertain"
    assert "set-cookie" not in response.headers
    assert not (root / "browser-auth.sqlite3").exists()
    installation_id = json.loads((root / "deployment.json").read_text())["browser_login"][
        "installation_id"
    ]
    configuration = client.get("/auth/config")
    assert configuration.status_code == 200
    assert configuration.json() == {"mode": "bearer", "local_setup_available": True}
    assert configuration.headers["cache-control"] == "no-store"
    assert directory_syncs == 1
    assert not (root / "browser-auth.sqlite3").exists()
    # Read-only reconciliation never retries the creation. A new owner-authorized
    # submission can complete it using the same pending installation binding.
    retry = create(client, token)
    assert retry.status_code == 200
    assert "HttpOnly" in retry.headers["set-cookie"]
    assert directory_syncs == 3
    policy = json.loads((root / "deployment.json").read_text())["browser_login"]
    assert policy["installation_id"] == installation_id
    assert policy["enabled"] is True
    assert "provisioning_pending" not in policy
    assert client.get("/auth/session").status_code == 200


def test_concurrent_setup_has_only_one_success(setup_app):
    from concurrent.futures import ThreadPoolExecutor

    client, root, module, _, _ = setup_app
    token = module.mint_token(root)

    def operation(_):
        other = TestClient(
            client.app,
            base_url="http://127.0.0.1:19889",
            client=("127.0.0.1", 50000),
            headers={"Origin": "http://127.0.0.1:19889", "X-CAO-Browser": "1"},
        )
        return create(other, token).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(operation, range(2)))
    assert sorted(statuses) == [200, 409]


def test_existing_disabled_account_cannot_be_reenabled_from_browser(setup_app):
    client, root, module, _, _ = setup_app
    token = module.mint_token(root)
    assert create(client, token).status_code == 200
    module.account_disable(root)
    configure_browser_auth(client.app)
    assert client.get("/auth/config").json()["local_setup_available"] is False
    assert create(client, token).status_code == 409


def test_invalid_body_has_no_password_echo_or_persisted_policy(setup_app):
    client, root, module, _, _ = setup_app
    response = client.post(
        "/auth/setup",
        headers={"Authorization": "Bearer " + module.mint_token(root)},
        json={"username": "felni", "password": ["private-value"], "remember": True},
    )
    assert response.status_code == 422
    assert "private-value" not in response.text
    assert not (root / "browser-auth.sqlite3").exists()


@pytest.mark.parametrize(
    "username,password",
    [
        ("felni", "short"),
        ("felni", "123456789"),
        ("felni", "x" * 129),
        ("invalid username", "TenChars1!"),
    ],
)
def test_setup_validates_credentials_before_creating_files(setup_app, username, password):
    client, root, module, _, _ = setup_app
    response = client.post(
        "/auth/setup",
        headers={"Authorization": "Bearer " + module.mint_token(root)},
        json={"username": username, "password": password, "remember": True},
    )
    assert response.status_code == 422
    assert password not in response.text
    assert "browser_login" not in json.loads((root / "deployment.json").read_text())
    assert not (root / "browser-auth.sqlite3").exists()


def test_owner_binding_is_rechecked_after_account_lock(setup_app, monkeypatch):
    from cli_agent_orchestrator.services import browser_setup

    client, root, module, _, _ = setup_app
    original = browser_setup.create_first_account

    def changed_deployment(*args):
        path = root / "deployment.json"
        deployment = json.loads(path.read_text())
        deployment["subject"] = "new-operator"
        path.write_text(json.dumps(deployment))
        return original(*args)

    monkeypatch.setattr(browser_setup, "create_first_account", changed_deployment)
    response = create(client, module.mint_token(root))
    assert response.status_code == 503
    assert not (root / "browser-auth.sqlite3").exists()


@pytest.mark.parametrize("tamper", ["none", "binding", "json"])
def test_setup_reconciles_after_final_rename_directory_sync_failure(setup_app, monkeypatch, tamper):
    import os
    import sqlite3
    import stat

    from cli_agent_orchestrator.services import browser_setup

    client, root, module, before, _ = setup_app
    private_key = (root / "issuer.pem").read_bytes()
    original_fsync = os.fsync
    directory_syncs = 0

    def interrupted(directory):
        nonlocal directory_syncs
        if stat.S_ISDIR(os.fstat(directory).st_mode):
            directory_syncs += 1
            if directory_syncs == 2:
                published = json.loads((root / "deployment.json").read_text())
                assert published["browser_login"]["enabled"] is True
                if tamper == "binding":
                    published["browser_login"]["binding"]["subject"] = "different-operator"
                    (root / "deployment.json").write_text(json.dumps(published))
                elif tamper == "json":
                    (root / "deployment.json").write_text("{invalid")
                raise OSError("directory publication durability unconfirmed")
        return original_fsync(directory)

    monkeypatch.setattr(browser_setup.os, "fsync", interrupted)
    token = module.mint_token(root)
    response = create(client, token)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "setup_publication_uncertain"
    assert "set-cookie" not in response.headers
    assert directory_syncs == 2
    assert (root / "issuer.pem").read_bytes() == private_key
    if tamper != "json":
        after = json.loads((root / "deployment.json").read_text())
        assert after["issuer"] == before["issuer"]
        assert after["subject"] == before["subject"]
    with sqlite3.connect(root / "browser-auth.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM browser_accounts").fetchone()[0] == 1
    configuration = client.get("/auth/config")
    if tamper != "none":
        assert configuration.status_code == 503
        assert configuration.headers["cache-control"] == "no-store"
        assert (
            client.post(
                "/auth/login",
                json={"username": "felni", "password": "TenChars1!", "remember": True},
            ).status_code
            == 503
        )
        from fastapi import Request

        @client.app.get("/protected-cookie")
        def protected(request: Request):
            return {"identity": auth.browser_principal(request)}

        assert client.get("/protected-cookie").status_code == 401
    else:
        assert configuration.status_code == 200
        assert configuration.json()["mode"] == "local_password"
        assert "local_setup_available" not in configuration.json()
        assert create(client, token).status_code == 409
        assert (
            client.post(
                "/auth/login",
                json={"username": "felni", "password": "TenChars1!", "remember": True},
            ).status_code
            == 200
        )
        assert client.get("/auth/session").status_code == 200
