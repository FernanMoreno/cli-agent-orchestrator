"""Non-ASCII bearer values must fail closed without escaping as TypeError."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from cli_agent_orchestrator.security import auth

pytestmark = pytest.mark.offline_database

ASCII_TOKEN = "internal-service-token"
UNICODE_TOKEN = "café"


def _request(presented_token: str | None) -> Request:
    headers = []
    if presented_token is not None:
        # ASGI exposes header octets as Latin-1. This lets the unit boundary
        # exercise a non-ASCII value without involving an HTTP server/parser.
        headers.append((b"authorization", f"Bearer {presented_token}".encode("latin-1")))
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/interaction-capabilities",
            "raw_path": b"/interaction-capabilities",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "server": ("127.0.0.1", 8000),
            "app": SimpleNamespace(state=SimpleNamespace()),
        }
    )


def _verified_principal():
    """Return a sealed identity for testing only the post-auth comparison boundary.

    JWT verification remains a separate dependency concern and is covered by
    the authentication tests; these tests do not replace or bypass that policy.
    """
    return auth._verified_principal("https://issuer.example/", "operator", ["cao:read"], "jwt")


def _principal_resolver(principal):
    async def resolve(_request, _authorization=None):
        return principal

    return resolve


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["human", "optional_creator", "capabilities"])
@pytest.mark.parametrize(
    ("presented_token", "configured_token"),
    [
        (UNICODE_TOKEN, ASCII_TOKEN),
        (ASCII_TOKEN, UNICODE_TOKEN),
    ],
    ids=["unicode-presented", "unicode-configured"],
)
async def test_api_token_comparison_handles_non_ascii_at_all_three_boundaries(
    boundary, presented_token, configured_token, monkeypatch
):
    """Local-token discrimination must not turn invalid Unicode into HTTP 500."""
    from cli_agent_orchestrator.api import interactions

    principal = _verified_principal()
    request = _request(presented_token)
    monkeypatch.setattr(interactions, "get_local_bearer", lambda: configured_token)

    if boundary == "human":
        result = await interactions._human_principal(request, principal)
        assert result is principal
    elif boundary == "optional_creator":
        monkeypatch.setattr(interactions, "get_current_principal", _principal_resolver(principal))
        result = await interactions.optional_creation_principal(request)
        assert result is principal
    else:

        class EmptyCapabilityService:
            async def capabilities(self, **_kwargs):
                return []

        monkeypatch.setattr(interactions, "get_current_principal", _principal_resolver(principal))
        monkeypatch.setattr(
            interactions, "get_interaction_service", lambda _request: EmptyCapabilityService()
        )
        result = await interactions.interaction_capabilities(request)
        assert result == {"items": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["human", "capabilities"])
async def test_ascii_service_bearer_is_still_rejected_as_human(boundary, monkeypatch):
    """The Unicode guard must preserve the existing exact-ASCII 403 policy."""
    from cli_agent_orchestrator.api import interactions

    principal = _verified_principal()
    request = _request(ASCII_TOKEN)
    monkeypatch.setattr(interactions, "get_local_bearer", lambda: ASCII_TOKEN)

    if boundary == "human":
        with pytest.raises(HTTPException) as rejected:
            await interactions._human_principal(request, principal)
    else:

        class EmptyCapabilityService:
            async def capabilities(self, **_kwargs):
                return []

        monkeypatch.setattr(interactions, "get_current_principal", _principal_resolver(principal))
        monkeypatch.setattr(
            interactions, "get_interaction_service", lambda _request: EmptyCapabilityService()
        )
        with pytest.raises(HTTPException) as rejected:
            await interactions.interaction_capabilities(request)

    assert rejected.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("presented_token", "configured_token"),
    [
        (UNICODE_TOKEN, ASCII_TOKEN),
        (ASCII_TOKEN, UNICODE_TOKEN),
        ("日本語", ASCII_TOKEN),
        (ASCII_TOKEN, "日本語"),
        (UNICODE_TOKEN, UNICODE_TOKEN),
        ("日本語", "日本語"),
        ("\ud800", ASCII_TOKEN),
        (ASCII_TOKEN, "\udcff"),
    ],
    ids=[
        "unicode-presented-latin",
        "unicode-configured-latin",
        "unicode-presented-cjk",
        "unicode-configured-cjk",
        "matching-invalid-latin",
        "matching-invalid-cjk",
        "invalid-surrogate",
        "configured-surrogateescape",
    ],
)
async def test_runtime_websocket_rejects_non_ascii_tokens_before_accept(
    presented_token, configured_token, monkeypatch
):
    """Invalid runtime-token encodings close the handshake rather than erroring."""
    from cli_agent_orchestrator.runtime_channel import server

    class FakeWebSocket:
        def __init__(self):
            self.headers = {server.TOKEN_HEADER: presented_token}
            self.close_codes = []
            self.accepted = False

        async def close(self, *, code):
            self.close_codes.append(code)

        async def accept(self):
            self.accepted = True

    websocket = FakeWebSocket()
    monkeypatch.setattr(server, "runtime_token", lambda: configured_token)

    await server.runtime_channel(websocket)

    assert websocket.close_codes == [1008]
    assert websocket.accepted is False
