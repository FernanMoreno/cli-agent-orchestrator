import pytest


def test_ticket_expires_is_single_use_and_scope_bound():
    from cli_agent_orchestrator.services.event_stream_ticket import TicketStore

    now = [10.0]
    store = TicketStore(clock=lambda: now[0])
    ticket = store.issue("alice", "/events", lambda: None)
    with pytest.raises(ValueError):
        store.consume(ticket, "/terminals/one/ws")
    ticket = store.issue("alice", "/events", lambda: None)
    assert store.consume(ticket, "/events").subject == "alice"
    with pytest.raises(ValueError):
        store.consume(ticket, "/events")
    ticket = store.issue("alice", "/events", lambda: None)
    now[0] += 30
    with pytest.raises(ValueError):
        store.consume(ticket, "/events")


@pytest.mark.asyncio
async def test_ticket_checks_revocation_before_attach(monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request

    from cli_agent_orchestrator.api import main
    from cli_agent_orchestrator.services.event_stream_ticket import store

    active = [True]

    async def validate():
        if not active[0]:
            raise HTTPException(401, "revoked")
        return ["cao:read"]

    ticket = store.issue("alice", "/events", validate)
    active[0] = False
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/events",
            "headers": [],
            "query_string": f"ticket={ticket}".encode(),
        }
    )
    with pytest.raises(HTTPException) as error:
        await main._stream_authorization(request, "/events", ("cao:read",))
    assert error.value.status_code == 401
    with pytest.raises(ValueError):
        store.consume(ticket, "/events")


@pytest.mark.asyncio
async def test_events_register_before_replay_and_no_duplicate_handoff(monkeypatch):
    from starlette.requests import Request

    from cli_agent_orchestrator.api import main
    from cli_agent_orchestrator.services import event_log_service, sse_bus

    log = event_log_service.EventLog()
    prior = log.append("launch", "one", "session", {})
    bus = sse_bus.SseBus()
    monkeypatch.setenv("CAO_MCP_APPS_ENABLED", "true")
    monkeypatch.setattr(event_log_service, "get_event_log", lambda: log)
    monkeypatch.setattr(sse_bus, "get_bus", lambda: bus)

    async def validate():
        return ["cao:read"]

    async def authorize(*args):
        return validate

    monkeypatch.setattr(main, "_stream_authorization", authorize)
    original = log.history
    published = [False]

    def racing_history(*args, **kwargs):
        if bus.subscriber_count == 1 and not published[0]:
            published[0] = True
            event = log.append("delegate", "two", "session", {})
            bus.publish(event)
        return original(*args, **kwargs)

    monkeypatch.setattr(log, "history", racing_history)
    request = Request(
        {"type": "http", "method": "GET", "path": "/events", "headers": [], "query_string": b""}
    )
    response = await main.events_stream(request, cursor=prior["id"])
    frame = await response.body_iterator.__anext__()
    assert "delegate" in frame
    # Next frame is heartbeat after replay/live overlap is skipped.
    next_frame = await response.body_iterator.__anext__()
    assert next_frame == ": keep-alive\n\n"
    await response.body_iterator.aclose()
    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_idle_stream_closes_on_revocation():
    from fastapi import HTTPException

    from cli_agent_orchestrator.api import main
    from cli_agent_orchestrator.services.sse_bus import SseBus

    bus = SseBus()
    sub = bus.register()

    async def revoked():
        raise HTTPException(401, "revoked")

    assert [event async for event in main._authorized_live_events(bus, sub, revoked)] == []
    bus.unregister(sub)


@pytest.mark.asyncio
async def test_ticket_rechecks_actual_bearer_and_identity(monkeypatch, jwt_factory):
    from types import SimpleNamespace

    from cryptography.hazmat.primitives import serialization
    from fastapi import HTTPException
    from starlette.requests import Request

    from cli_agent_orchestrator.api import main
    from cli_agent_orchestrator.security import auth

    monkeypatch.setenv("AUTH0_DOMAIN", "test.local")
    monkeypatch.setenv("AUTH0_AUDIENCE", "cao://test")
    key = serialization.load_pem_private_key(jwt_factory.private_pem, password=None).public_key()

    class JWKS:
        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=key)

    monkeypatch.setattr(auth.get_jwks_cache(), "get_client", lambda uri: JWKS())
    token = jwt_factory.mint(scopes="cao:read", extra_claims={"sub": "alice"})
    app = SimpleNamespace(state=SimpleNamespace(browser_auth_config={}))
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/events/ticket",
            "server": ("localhost", 80),
            "headers": [(b"host", b"localhost"), (b"authorization", f"Bearer {token}".encode())],
            "query_string": b"",
            "app": app,
        }
    )
    subject, validate = main._transport_validator(request, ("cao:read",), require_identity=True)
    assert subject.startswith("principal-")
    assert "cao:read" in await validate()
    monkeypatch.setattr(main, "CORS_ORIGINS", ["http://localhost:8000", "*"])
    request.scope["headers"].append((b"origin", b"http://localhost:8000"))
    request = Request(request.scope)
    issued = await main._issue_transport_ticket(request, "/events", ("cao:read",))
    assert issued.status_code == 200
    untrusted_scope = {
        **request.scope,
        "headers": [
            (name, b"https://untrusted.example" if name == b"origin" else value)
            for name, value in request.scope["headers"]
        ],
    }
    with pytest.raises(HTTPException) as untrusted:
        await main._issue_transport_ticket(Request(untrusted_scope), "/events", ("cao:read",))
    assert untrusted.value.status_code == 403
    with pytest.raises(HTTPException) as denied:
        await main._issue_transport_ticket(request, "/terminals/one/ws", ("cao:write",))
    assert denied.value.status_code == 403

    def expired(token):
        import jwt

        raise jwt.ExpiredSignatureError("expired after issuance")

    monkeypatch.setattr(auth, "verify_token_claims", expired)
    with pytest.raises(HTTPException) as error:
        await validate()
    assert error.value.status_code == 401


def test_mcp_unknown_cursor_requests_resync_without_issuing_ticket(monkeypatch):
    from unittest.mock import Mock

    from cli_agent_orchestrator.mcp_server import app_tools

    monkeypatch.setattr(app_tools, "_get_json", lambda *args, **kwargs: {"events": []})
    post = Mock()
    monkeypatch.setattr(app_tools.requests, "post", post)
    result = app_tools._subscribe_events_impl("expired-cursor")
    assert result["resync_required"] is True
    post.assert_not_called()


@pytest.mark.asyncio
async def test_ticket_boundary_ignores_unrelated_stale_cookie_and_rejects_invalid_ticket():
    from types import SimpleNamespace

    from fastapi import HTTPException
    from starlette.requests import Request

    from cli_agent_orchestrator.api import main
    from cli_agent_orchestrator.api.browser_auth_routes import BrowserSessionLifetimeMiddleware
    from cli_agent_orchestrator.services.event_stream_ticket import store

    validated = []

    async def validate():
        validated.append("ticket-owner")
        return ["cao:read"]

    ticket = store.issue("ticket-owner", "/events", validate)
    app = SimpleNamespace(
        state=SimpleNamespace(browser_auth_config={"enabled": True, "installation_id": "test"})
    )
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/events",
        "server": ("localhost", 80),
        "headers": [(b"host", b"localhost"), (b"cookie", b"cao_browser_test=stale-unrelated")],
        "query_string": f"ticket={ticket}".encode(),
        "app": app,
    }

    async def endpoint(scope, receive, send):
        await main._stream_authorization(Request(scope), "/events", ("cao:read",))

    async def unused(*args):
        raise AssertionError("unexpected socket IO")

    middleware = BrowserSessionLifetimeMiddleware(endpoint)
    await middleware(scope, unused, unused)
    assert validated == ["ticket-owner"]
    scope["query_string"] = b"ticket=invalid"
    with pytest.raises(HTTPException) as error:
        await middleware(scope, unused, unused)
    assert error.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["events", "agui"])
async def test_cursor_evicted_before_first_stream_iteration_signals_resync(monkeypatch, surface):
    from starlette.requests import Request

    from cli_agent_orchestrator.api import main
    from cli_agent_orchestrator.services import event_log_service, sse_bus

    log = event_log_service.EventLog()
    prior = log.append("launch", "one", "session", {})
    bus = sse_bus.SseBus()
    monkeypatch.setenv("CAO_MCP_APPS_ENABLED", "true")
    monkeypatch.setattr(event_log_service, "get_event_log", lambda: log)
    monkeypatch.setattr(sse_bus, "get_bus", lambda: bus)

    async def validate():
        return ["cao:read"]

    async def authorize(*args):
        return validate

    monkeypatch.setattr(main, "_stream_authorization", authorize)
    request = Request(
        {"type": "http", "method": "GET", "path": "/events", "headers": [], "query_string": b""}
    )
    if surface == "events":
        response = await main.events_stream(request, cursor=prior["id"])
    else:
        response = await main.agui_stream(
            request, cursor=prior["id"], since=None, last_event_id=None
        )
    # Retention expires after preflight, before the generator registers/replays.
    for _ in range(event_log_service.RING_CAPACITY):
        log.append("delegate", "two", "session", {})
    frame = await response.body_iterator.__anext__()
    assert "event: cursor_expired" in frame
    assert "event_cursor_expired" in frame
    with pytest.raises(StopAsyncIteration):
        await response.body_iterator.__anext__()
    assert bus.subscriber_count == 0
