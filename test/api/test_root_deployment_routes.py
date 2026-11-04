"""Root operator HTTP contract; fixtures do not install deployment authority."""

import importlib
from test.conftest import mint_test_token
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cli_agent_orchestrator.security import auth

pytestmark = pytest.mark.offline_database


@pytest.fixture
def root_control(auth_enabled_env, rsa_keys, monkeypatch):
    public = jwt.PyJWK.from_dict(rsa_keys[1].as_dict()).key
    jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=public))
    monkeypatch.setattr(auth.get_jwks_cache(), "get_client", lambda _: jwks)
    routes = importlib.import_module("cli_agent_orchestrator.api.root_deployment_routes")
    models = importlib.import_module("cli_agent_orchestrator.models.root_deployment_control")
    runtime_module = importlib.import_module(
        "cli_agent_orchestrator.services.root_deployment_runtime"
    )
    value = Mock(spec=runtime_module.RootDeploymentRuntime)
    status = models.RootDeploymentStatus(phase="awaiting_authority")
    value.status.return_value = status
    for action in ("launch", "activate", "refresh", "stop"):
        setattr(value, action, AsyncMock(return_value=status))
    app = FastAPI()
    app.include_router(routes.router)
    app.state.root_deployment_runtime = value
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50202)) as client:
        yield SimpleNamespace(
            client=client,
            app=app,
            runtime=value,
            routes=routes,
            models=models,
            runtime_module=runtime_module,
            key=rsa_keys[0],
        )


def headers(key, scopes="cao:admin"):
    return {"Authorization": "Bearer " + mint_test_token(key, scopes=scopes, subject="operator")}


def test_status_requires_real_authenticated_admin(root_control):
    f = root_control
    response = f.client.get("/admission/root", headers=headers(f.key))
    assert response.status_code == 200
    assert response.json()["phase"] == "awaiting_authority"
    assert response.headers["cache-control"] == "no-store"
    principal = f.runtime.status.call_args.kwargs["principal"]
    assert auth.is_verified_principal(principal) and principal.kind == "jwt"


@pytest.mark.parametrize("action", ["launch", "activate", "refresh", "stop"])
def test_mutation_contract_passes_only_validated_command_and_principal(root_control, action):
    f = root_control
    body = {"version": 1}
    if action == "activate":
        body.update({key: str(uuid4()) for key in ("journal_id", "intent_id", "session_id")})
    response = f.client.post("/admission/root/" + action, headers=headers(f.key), json=body)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    kwargs = getattr(f.runtime, action).call_args.kwargs
    assert auth.is_verified_principal(kwargs.pop("principal"))
    assert kwargs == {key: value for key, value in body.items() if key != "version"}


@pytest.mark.parametrize("scopes", ["cao:read", "cao:write", ""])
def test_nonadmin_never_enters_runtime(root_control, scopes):
    f = root_control
    response = f.client.post(
        "/admission/root/launch", headers=headers(f.key, scopes), json={"version": 1}
    )
    assert response.status_code == 403
    f.runtime.launch.assert_not_called()


def test_auth_off_local_operator_does_not_acquire_root_authority(root_control, monkeypatch):
    f = root_control
    for key in ("AUTH0_DOMAIN", "CAO_AUTH_JWKS_URI"):
        monkeypatch.delenv(key, raising=False)
    response = f.client.post("/admission/root/launch", json={"version": 1})
    assert response.status_code == 403
    f.runtime.launch.assert_not_called()


def test_absent_runtime_fails_closed(root_control):
    f = root_control
    del f.app.state.root_deployment_runtime
    response = f.client.post("/admission/root/launch", headers=headers(f.key), json={"version": 1})
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "root_deployment_unavailable"}}


@pytest.mark.parametrize(
    "body", [{"version": True}, {"version": 2}, {}, {"version": 1, "argv": "sensitive-test-value"}]
)
def test_invalid_command_is_fixed_and_never_echoes_request(root_control, body):
    f = root_control
    response = f.client.post("/admission/root/launch", headers=headers(f.key), json=body)
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_root_deployment_command"}}
    assert response.headers["cache-control"] == "no-store"
    f.runtime.launch.assert_not_called()


def test_activation_rejects_noncanonical_identifiers_before_runtime(root_control):
    f = root_control
    response = f.client.post(
        "/admission/root/activate",
        headers=headers(f.key),
        json={
            "version": 1,
            "journal_id": "INVALID",
            "intent_id": str(uuid4()),
            "session_id": str(uuid4()),
        },
    )
    assert response.status_code == 422
    f.runtime.activate.assert_not_called()


def test_runtime_failure_has_fixed_external_error(root_control):
    f = root_control
    f.runtime.launch.side_effect = f.runtime_module.RootDeploymentRuntimeUnavailable()
    response = f.client.post("/admission/root/launch", headers=headers(f.key), json={"version": 1})
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "root_deployment_unavailable"}}


def test_operator_mismatch_is_distinct_from_runtime_failure(root_control):
    f = root_control
    f.runtime.launch.side_effect = f.runtime_module.RootDeploymentOperatorMismatch()
    response = f.client.post("/admission/root/launch", headers=headers(f.key), json={"version": 1})
    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "root_deployment_operator_required"}}
    assert response.headers["cache-control"] == "no-store"


def test_expired_or_invalid_token_never_enters_runtime(root_control):
    f = root_control
    response = f.client.post(
        "/admission/root/launch", headers={"Authorization": "Bearer invalid"}, json={"version": 1}
    )
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["www-authenticate"] == "Bearer"
    f.runtime.launch.assert_not_called()


@pytest.mark.asyncio
async def test_shutdown_preserves_owner_when_close_is_unconfirmed(root_control):
    f = root_control
    f.runtime.aclose = AsyncMock(side_effect=f.runtime_module.RootDeploymentRuntimeUnavailable())
    with pytest.raises(f.runtime_module.RootDeploymentRuntimeUnavailable):
        await f.routes.close_root_deployment(f.app)
    assert f.app.state.root_deployment_runtime is f.runtime
    f.runtime.aclose = AsyncMock()
    await f.routes.close_root_deployment(f.app)
    assert getattr(f.app.state, "root_deployment_runtime", None) is None


def test_configuration_rejects_runtime_lookalikes(root_control):
    f = root_control
    app = FastAPI()
    with pytest.raises(TypeError):
        f.routes.configure_root_deployment(app, f.runtime)
    assert getattr(app.state, "root_deployment_runtime", None) is None


@pytest.mark.asyncio
async def test_runtime_installation_cannot_be_shared_between_apps(root_control):
    from test.services.test_root_deployment_runtime import _fixture

    f = root_control
    config, _, _, _ = _fixture()
    value = f.runtime_module.RootDeploymentRuntime(config)
    first, second = FastAPI(), FastAPI()
    f.routes.configure_root_deployment(first, value)
    assert first.state.root_deployment_runtime is value
    assert not value.installable
    with pytest.raises(f.runtime_module.RootDeploymentRuntimeUnavailable):
        f.routes.configure_root_deployment(second, value)
    assert getattr(second.state, "root_deployment_runtime", None) is None
    replacement_config, _, _, _ = _fixture()
    replacement = f.runtime_module.RootDeploymentRuntime(replacement_config)
    with pytest.raises(RuntimeError):
        f.routes.configure_root_deployment(first, replacement)
    assert replacement.installable  # Rejection must not consume this unused owner.
    await f.routes.close_root_deployment(first)
