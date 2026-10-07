"""Local browser authentication, composed explicitly by the personal deployment."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from cli_agent_orchestrator.security.auth import (
    FULL_SCOPE_SET,
    _verified_principal,
    browser_session_context,
    is_auth_enabled,
)

router = APIRouter(prefix="/auth", tags=["browser authentication"])


def configure_browser_auth(app):
    """Fail closed on an explicitly configured private installation; preserve other modes."""
    root_value = os.getenv("CAO_BROWSER_LOGIN_ROOT", "").strip()
    if not root_value:
        return
    root = Path(root_value)
    from cli_agent_orchestrator.services.browser_setup import private_deployment

    deployment = private_deployment(root)
    if os.getenv("CAO_AUTH_ISSUER") != deployment["issuer"] or not is_auth_enabled():
        raise ValueError("browser login cannot replace existing JWT policy")
    app.state.browser_deployment_root = root
    config = deployment.get("browser_login", {})
    if not config.get("enabled"):
        app.state.browser_auth = None
        app.state.browser_auth_config = {}
        app.state.browser_auth_unavailable = False
        return
    if deployment.get("host") != "127.0.0.1":
        raise ValueError("browser login must remain local")
    expected_origin = f"http://127.0.0.1:{deployment['server_port']}"
    if (
        config.get("canonical_origin") != expected_origin
        or config.get("transport_policy") != "loopback_http"
    ):
        raise ValueError("invalid local browser origin configuration")
    if os.getenv("CAO_AUTH_ISSUER") != deployment["issuer"] or not is_auth_enabled():
        raise ValueError("browser login cannot replace existing JWT policy")
    from cli_agent_orchestrator.services.browser_auth import BrowserAuthService

    scopes = deployment.get("scopes", list(FULL_SCOPE_SET))
    if not isinstance(scopes, list) or any(scope not in FULL_SCOPE_SET for scope in scopes):
        raise ValueError("invalid configured browser scopes")
    principal = _verified_principal(deployment["issuer"], deployment["subject"], scopes, "jwt")
    binding = {key: getattr(principal, key) for key in ("id", "issuer", "subject", "kind")}
    binding["scopes"] = sorted(principal.scopes)
    stored_binding = config.get("binding")
    if isinstance(stored_binding, dict) and isinstance(stored_binding.get("scopes"), list):
        stored_binding = {**stored_binding, "scopes": sorted(set(stored_binding["scopes"]))}
    if stored_binding != binding:
        raise ValueError("browser binding differs from configured operator")
    service = BrowserAuthService(root / "browser-auth.sqlite3", binding, config)
    service.validate_binding()
    app.state.browser_auth = service
    app.state.browser_auth_config = config
    app.state.browser_auth_unavailable = False


def _service(request):
    if getattr(request.app.state, "browser_auth_unavailable", False):
        raise HTTPException(
            503,
            detail={"code": "auth_unavailable", "message": "Authentication unavailable."},
            headers={"Cache-Control": "no-store"},
        )
    service = getattr(request.app.state, "browser_auth", None)
    if service is None:
        raise HTTPException(
            404,
            detail={"code": "local_login_disabled", "message": "Local login disabled."},
            headers={"Cache-Control": "no-store"},
        )
    return service


def _context(request, *, secret_required=True):
    _service(request)
    return browser_session_context(request, secret_required=secret_required)


def _failure(exc):
    if isinstance(exc, HTTPException):
        return JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
            headers={"Cache-Control": "no-store", **(exc.headers or {})},
        )
    code = getattr(exc, "code", "auth_unavailable")
    status = getattr(exc, "status", 503)
    headers = {"Cache-Control": "no-store"}
    retry = getattr(exc, "retry_after", None)
    if retry is not None:
        headers["Retry-After"] = str(max(1, int(retry)))
    return JSONResponse(
        {
            "detail": {
                "code": code,
                "message": {
                    "credentials_rejected": "Username or password rejected.",
                    "session_expired": "Session expired. Please sign in.",
                    "session_revoked": "Session revoked. Please sign in.",
                    "access_renewal_required": "Access renewal required.",
                    "login_throttled": "Too many attempts. Please wait.",
                    "setup_publication_uncertain": (
                        "Account publication durability could not be confirmed. "
                        "Refresh to check login availability."
                    ),
                }.get(code, "Authentication unavailable."),
            }
        },
        status_code=status,
        headers=headers,
    )


async def _body(request, fields):
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > 4096:
            raise HTTPException(
                413,
                detail={"code": "auth_body_too_large", "message": "Request too large."},
                headers={"Cache-Control": "no-store"},
            )
    try:
        body = json.loads(chunks)
        if not isinstance(body, dict) or set(body) != set(fields):
            raise ValueError()
        for key, typ in fields.items():
            if type(body[key]) is not typ:
                raise ValueError()
        return body
    except (ValueError, TypeError):
        raise HTTPException(
            422,
            detail={"code": "invalid_auth_request", "message": "Invalid authentication request."},
            headers={"Cache-Control": "no-store"},
        ) from None


@router.get("/config")
async def config(request: Request):
    if getattr(request.app.state, "browser_auth_unavailable", False):
        return _failure(HTTPException(503, detail={"code": "auth_unavailable"}))
    service = getattr(request.app.state, "browser_auth", None)
    cfg = getattr(request.app.state, "browser_auth_config", {})
    cfg = {**cfg.get("limits", {}), **cfg}
    if service is not None and cfg.get("enabled"):
        body = {
            "mode": "local_password",
            "remembered_idle_seconds": cfg.get("remembered_idle_seconds", 604800),
            "remembered_absolute_seconds": cfg.get("remembered_absolute_seconds", 2592000),
            "temporal_idle_seconds": cfg.get("temporal_idle_seconds", 28800),
            "temporal_absolute_seconds": cfg.get("temporal_absolute_seconds", 86400),
        }
    else:
        body = {"mode": "bearer" if is_auth_enabled() else "disabled"}
        root = getattr(request.app.state, "browser_deployment_root", None)
        if root is not None:
            from cli_agent_orchestrator.services.browser_setup import setup_available

            body["local_setup_available"] = await asyncio.to_thread(setup_available, root)
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


@router.post("/setup")
async def setup(request: Request):
    from cli_agent_orchestrator.security.auth import principal_from_token
    from cli_agent_orchestrator.services.browser_setup import (
        PublicationUncertain,
        create_first_account,
        private_deployment,
    )

    try:
        root = getattr(request.app.state, "browser_deployment_root", None)
        if root is None:
            raise HTTPException(404, detail={"code": "local_setup_disabled"})
        deployment = await asyncio.to_thread(private_deployment, root)
        origin = f"http://127.0.0.1:{deployment['server_port']}"
        if (
            str(request.base_url).rstrip("/") != origin
            or request.headers.get("origin") != origin
            or request.headers.get("x-cao-browser") != "1"
            or request.client is None
            or request.scope["client"][0] != "127.0.0.1"
        ):
            raise HTTPException(403, detail={"code": "browser_origin_rejected"})
        authorization = request.headers.get("authorization", "")
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(401, detail={"code": "operator_token_required"})
        try:
            principal = await asyncio.to_thread(principal_from_token, parts[1])
        except Exception:
            raise HTTPException(401, detail={"code": "operator_token_rejected"}) from None
        scopes = deployment.get("scopes", ["cao:read", "cao:write", "cao:admin"])
        if (
            principal.issuer != deployment["issuer"]
            or principal.subject != deployment["subject"]
            or principal.kind != "jwt"
            or principal.scopes != frozenset(scopes)
            or "cao:admin" not in principal.scopes
        ):
            raise HTTPException(403, detail={"code": "operator_binding_rejected"})
        body = await _body(request, {"username": str, "password": str, "remember": bool})
        binding = {key: getattr(principal, key) for key in ("id", "issuer", "subject", "kind")}
        binding["scopes"] = scopes
        service, policy, secret, dto = await asyncio.to_thread(
            create_first_account,
            root,
            binding,
            body["username"],
            body["password"],
            body["remember"],
            request.scope["client"][0],
        )
        request.app.state.browser_auth = service
        request.app.state.browser_auth_config = policy
        return _login_response(
            request, "cao_browser_" + policy["installation_id"], secret, dto, body["remember"]
        )
    except PublicationUncertain as exc:
        # A committed rename followed by a failed directory flush is neither
        # an ordinary failed write nor a confirmed durable setup. Do not issue
        # a cookie or repeat creation; validate disk using the startup boundary.
        request.app.state.browser_auth = None
        request.app.state.browser_auth_config = {"enabled": True}
        request.app.state.browser_auth_unavailable = True
        try:
            await asyncio.to_thread(configure_browser_auth, request.app)
        except Exception:
            # Invalid publication stays unavailable; bearer verification remains
            # independently enforced, and cookies cannot acquire authority.
            pass
        return _failure(exc)
    except ValueError:
        return _failure(HTTPException(422, detail={"code": "invalid_auth_request"}))
    except Exception as exc:
        return _failure(exc)


@router.post("/login")
async def login(request: Request):
    service, name, _ = _context(request, secret_required=False)
    body = await _body(request, {"username": str, "password": str, "remember": bool})
    try:
        secret, dto = await asyncio.to_thread(
            service.login,
            body["username"],
            body["password"],
            body["remember"],
            request.scope["client"][0],
        )
    except Exception as exc:
        return _failure(exc)
    return _login_response(request, name, secret, dto, body["remember"])


def _login_response(request, name, secret, dto, remembered):
    response = JSONResponse(dto, headers={"Cache-Control": "no-store"})
    cfg = request.app.state.browser_auth_config
    max_age = max(1, int(dto["absolute_expires_at"] - dto["server_time"])) if remembered else None
    response.set_cookie(
        name,
        secret,
        httponly=True,
        samesite="strict",
        path="/",
        secure=cfg["canonical_origin"].startswith("https://"),
        max_age=max_age,
        expires=(
            datetime.fromtimestamp(dto["absolute_expires_at"], timezone.utc)
            if max_age is not None
            else None
        ),
    )
    return response


@router.get("/session")
async def session(request: Request):
    service, _, secret = _context(request)
    try:
        dto = await asyncio.to_thread(service.session, secret)
        return JSONResponse(dto, headers={"Cache-Control": "no-store"})
    except Exception as exc:
        return _failure(exc)


@router.post("/renew")
async def renew(request: Request):
    service, _, secret = _context(request)
    try:
        dto = await asyncio.to_thread(service.renew, secret)
        return JSONResponse(dto, headers={"Cache-Control": "no-store"})
    except Exception as exc:
        return _failure(exc)


@router.post("/logout")
async def logout(request: Request):
    service, _, secret = _context(request)
    try:
        await asyncio.to_thread(service.logout, secret)
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    except Exception as exc:
        return _failure(exc)


@router.post("/logout-all")
async def logout_all(request: Request):
    service, _, secret = _context(request)
    try:
        await asyncio.to_thread(service.logout_all, secret)
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    except Exception as exc:
        return _failure(exc)


@router.post("/password")
async def password(request: Request):
    service, _, secret = _context(request)
    body = await _body(request, {"current_password": str, "new_password": str})
    try:
        await asyncio.to_thread(
            service.change_password,
            secret,
            body["current_password"],
            body["new_password"],
            request.scope["client"][0],
        )
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    except Exception as exc:
        return _failure(exc)


class BrowserSessionLifetimeMiddleware:
    """Revalidate cookie streams and input even when the transport is silent."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        from starlette.requests import HTTPConnection

        connection = HTTPConnection(scope) if scope["type"] in ("http", "websocket") else None
        if connection is None:
            return await self.app(scope, receive, send)
        config = getattr(connection.app.state, "browser_auth_config", {})
        name = "cao_browser_" + str(config.get("installation_id", ""))
        explicit = "authorization" in connection.headers
        ticket_transport = scope["path"] in ("/events", "/agui/v1/stream") or (
            scope["type"] == "websocket"
            and scope["path"].startswith("/terminals/")
            and scope["path"].endswith("/ws")
        )
        explicit = explicit or (ticket_transport and "ticket" in connection.query_params)
        if scope["type"] == "websocket" and scope["path"].startswith("/terminals/"):
            explicit = explicit or "token" in connection.query_params
        if scope["path"] == "/agui/v1/stream":
            explicit = explicit or "access_token" in connection.query_params
        if not config.get("enabled") or explicit or not connection.cookies.get(name):
            return await self.app(scope, receive, send)
        # /auth manages session state itself, including an expired access lease.
        if scope["type"] == "http" and (
            scope["path"].startswith(("/auth/", "/assets/"))
            or scope["path"] in ("/", "/index.html", "/favicon.ico", "/health")
        ):
            return await self.app(scope, receive, send)
        websocket = scope["type"] == "websocket"
        # Request/WebSocket supply method and peer information to the origin guard.
        from starlette.requests import Request
        from starlette.websockets import WebSocket

        request = WebSocket(scope, receive, send) if websocket else Request(scope)
        try:
            service, _, secret = browser_session_context(request)
            passive = scope.get("method") in ("GET", "HEAD") and (
                scope["path"] in ("/health", "/sessions", "/workflows/runs")
                or scope["path"].startswith(("/terminals/", "/work-items/", "/workflows/runs/"))
            )
            await asyncio.to_thread(service.identity, secret, require_access=True)
        except Exception as exc:
            if websocket:
                await send(
                    {
                        "type": "websocket.close",
                        "code": (
                            1013
                            if getattr(exc, "status", getattr(exc, "status_code", 503)) == 503
                            else 4401
                        ),
                        "reason": "Browser session unavailable",
                    }
                )
            else:
                response = _failure(exc)
                await response(scope, receive, send)
            return
        retired = False
        stream = websocket
        started = False
        lock = asyncio.Lock()
        handler = None

        async def retire(exc):
            nonlocal retired
            async with lock:
                if retired:
                    return
                retired = True
                if websocket:
                    code = (
                        1013
                        if getattr(exc, "status", getattr(exc, "status_code", 503)) == 503
                        else 4401
                    )
                    await send(
                        {
                            "type": "websocket.close",
                            "code": code,
                            "reason": "Browser session unavailable",
                        }
                    )
                elif started:
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
            if handler is not None:
                handler.cancel()

        async def guard(touch=False):
            if retired:
                raise asyncio.CancelledError()
            try:
                await asyncio.to_thread(service.identity, secret, require_access=True, touch=touch)
            except Exception as exc:
                await retire(exc)
                raise asyncio.CancelledError() from None

        async def guarded_send(message):
            nonlocal stream, started
            kind = message["type"]
            if kind == "http.response.start":
                started = True
                if message.get("status", 500) < 400 and not passive and not websocket:
                    try:
                        await asyncio.to_thread(
                            service.identity, secret, require_access=True, touch=True
                        )
                    except Exception as exc:
                        if getattr(exc, "status", None) not in (401, 503):
                            raise
                        # A previously admitted operation can finish after revocation.
                        # Do not turn its known result into an ambiguous server error.
                stream = any(
                    k.lower() == b"content-type" and b"text/event-stream" in v
                    for k, v in message.get("headers", [])
                )
            if kind in ("websocket.accept", "websocket.send") or (
                kind == "http.response.body" and stream and message.get("body")
            ):
                await guard(touch=websocket and kind == "websocket.send")
            async with lock:
                if not retired:
                    await send(message)

        async def guarded_receive():
            message = await receive()
            if message["type"] == "websocket.receive":
                await guard(touch=True)
            return message

        async def monitor():
            while True:
                await asyncio.sleep(1)
                if stream:
                    await guard()

        handler = asyncio.create_task(self.app(scope, guarded_receive, guarded_send))
        watcher = asyncio.create_task(monitor())
        try:
            await handler
        except asyncio.CancelledError:
            if not retired:
                raise
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
