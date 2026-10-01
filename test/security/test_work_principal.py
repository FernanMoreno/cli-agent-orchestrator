"""Durable work trusts verified identity and socket locality, never request bodies."""

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from starlette.requests import Request

from cli_agent_orchestrator.security import auth


@pytest.fixture(autouse=True)
def no_identity_provider(monkeypatch):
    for name in ("AUTH0_DOMAIN", "CAO_AUTH_JWKS_URI", "CAO_AUTH_ISSUER", "CAO_AUTH_AUDIENCE"):
        monkeypatch.delenv(name, raising=False)


def request(client="127.0.0.1", server="127.0.0.1", headers=()):
    return Request(
        {
            "type": "http",
            "app": SimpleNamespace(state=SimpleNamespace()),
            "client": (client, 1234),
            "server": (server, 9889),
            "headers": list(headers),
            "method": "POST",
            "path": "/work",
        }
    )


def signed_token(monkeypatch, **overrides):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setenv("AUTH0_DOMAIN", "issuer.example")
    monkeypatch.setenv("CAO_AUTH_AUDIENCE", "cao")
    signing_client = SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private.public_key())
    )
    monkeypatch.setattr(auth.get_jwks_cache(), "get_client", lambda uri: signing_client)
    claims = {
        "iss": "https://issuer.example/",
        "sub": "operator",
        "aud": "cao",
        "exp": time.time() + 600,
        "scope": "cao:read cao:admin",
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="RS256"), claims


@pytest.mark.asyncio
async def test_principal_comes_from_verified_issuer_subject_not_headers(monkeypatch):
    token, claims = signed_token(monkeypatch)
    principal = await auth.get_current_principal(
        request(headers=[(b"caller_id", b"admin")]), authorization="Bearer " + token
    )
    assert principal.issuer == claims["iss"]
    assert principal.subject == claims["sub"]
    assert principal.scopes == frozenset({"cao:read", "cao:admin"})
    assert principal.id == auth.principal_from_token(token).id
    assert principal.id != "admin"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [{"sub": ""}, {"sub": None}, {"iss": "https://evil.example/"}, {"aud": "other"}, {"exp": 1}],
)
async def test_invalid_identity_claims_cannot_become_principal(monkeypatch, claims):
    token, _ = signed_token(monkeypatch, **claims)
    with pytest.raises(HTTPException) as rejected:
        await auth.get_current_principal(request(), authorization="Bearer " + token)
    assert rejected.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client,server",
    [("192.0.2.1", "127.0.0.1"), ("127.0.0.1", "0.0.0.0"), ("localhost", "127.0.0.1")],
)
async def test_default_off_work_rejects_remote_even_with_forged_local_headers(client, server):
    forged = [
        (b"host", b"localhost"),
        (b"x-forwarded-for", b"127.0.0.1"),
        (b"caller_id", b"operator"),
    ]
    with pytest.raises(HTTPException) as rejected:
        await auth.get_current_principal(request(client, server, forged), authorization=None)
    assert rejected.value.status_code == 401


@pytest.mark.asyncio
async def test_explicit_local_factory_matches_loopback_dependency():
    principal = await auth.get_current_principal(request(), authorization=None)
    assert principal == auth.local_operator_principal()
    assert principal.kind == "local_operator"
    assert auth.extract_scopes_from_token("legacy untouched") == auth.FULL_SCOPE_SET


def test_local_factory_cannot_bypass_configured_identity_provider(monkeypatch):
    monkeypatch.setenv("AUTH0_DOMAIN", "issuer.example")
    with pytest.raises(PermissionError):
        auth.local_operator_principal()
