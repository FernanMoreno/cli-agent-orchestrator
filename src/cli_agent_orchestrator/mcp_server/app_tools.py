"""MCP App tools for the CAO base app (SEP-1865).

This module implements the tools the sandboxed iframe calls through
``callServerTool`` and registers them on the FastMCP server via
``register_app_tools(mcp)`` (wired with a single line from ``server.py``, gated on
``CAO_MCP_APPS_ENABLED``).

Tools implemented here:

* ``render_dashboard``  — fleet snapshot         (visibility ``["model","app"]``)
* ``render_agent_view`` — per-agent detail        (visibility ``["model","app"]``)
* ``cao_fetch_history`` — replay the ring buffer   (visibility ``["app"]``)
* ``subscribe_events``  — SSE endpoint descriptor   (visibility ``["app"]``)
* ``render_graph_view`` — GraphView wire shape       (visibility ``["model","app"]``)
* ``submit_command``    — single mutation choke point **(Phase II skeleton)**

**HTTP-only boundary.** Every read of Backplane state goes through
the FastAPI surface over ``API_BASE_URL`` (``requests``) or through process-local
read-only presentation services (``ui_state_service``). Fleet history comes
from the API producer, never a separate MCP-process ring. This module — and
all of ``mcp_server/`` — must never import ``clients.tmux`` or ``clients.database``;
the guard test enforces it.

**Scopes seam (default-off).** The scope helper
(:func:`get_scopes_for_local_token`) and ``SCOPE_*`` constants are imported from
``security/auth.py``. With ``AUTH0_DOMAIN`` unset the helper returns
the full taxonomy, so every command passes the pre-check and behavior is
byte-for-byte identical to a build with no auth layer. The tool pre-check is a
UX gate; the FastAPI ``Depends(get_current_scopes)`` boundary is the real
enforcement point.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, TypeGuard
from urllib.parse import urlencode

import requests

from cli_agent_orchestrator.constants import API_BASE_URL
from cli_agent_orchestrator.ext_apps import (
    AGENT_RESOURCE_URI,
    DASHBOARD_RESOURCE_URI,
    EVENT_STREAM_RESOURCE_URI,
    GRAPH_RESOURCE_URI,
    register_apps,
    ui_meta,
)
from cli_agent_orchestrator.security.auth import (
    SCOPE_ADMIN,
    SCOPE_READ,
    SCOPE_WRITE,
    get_local_bearer,
    get_scopes_for_local_token,
    local_auth_misconfig_error,
)
from cli_agent_orchestrator.services.config_service import ConfigService
from cli_agent_orchestrator.services.event_primitives import normalize_kind
from cli_agent_orchestrator.services.ui_state_service import (
    build_agent_detail_snapshot,
    build_dashboard_snapshot,
)
from cli_agent_orchestrator.utils.orchestration import _mcp_timeout

logger = logging.getLogger(__name__)

# Re-exported for backwards compatibility with callers/tests that referenced the
# scope constants on ``app_tools`` during Phase II. The single source of truth is
# now ``security/auth.py``; ``get_scopes_for_local_token`` preserves
# the same default-off semantics (full scope set when ``AUTH0_DOMAIN`` is unset).
__all__ = [
    "SCOPE_READ",
    "SCOPE_WRITE",
    "SCOPE_ADMIN",
    "get_scopes_for_local_token",
    "register_app_tools",
]


# --- command-kind taxonomy for the choke point (mirrors SubmitCommandKind in types.ts) ---
STANDARD_KINDS = frozenset({"send_message", "assign", "create_session"})
LIFECYCLE_KINDS = frozenset({"interrupt", "pause", "resume", "verify_turn", "cancel_turn"})
DESTRUCTIVE_KINDS = frozenset({"shutdown_session"})

# Server-side backstop for oversized task payloads. The
# View_Layer rejects messages over 4000 chars before they ever reach the tool
# channel; this is a defense-in-depth cap at the choke point so an oversized
# payload bypassing the UI (or coming from another caller) is rejected with a
# structured warning rather than forwarded to the Backplane. The cap is on the
# serialized payload so any single oversized field (message, allowed_tools, ...)
# trips it.
MAX_PAYLOAD_CHARS = 16000


def _is_enabled() -> bool:
    """Return whether the MCP App surface is enabled via ``apps.enabled``
    (``CAO_MCP_APPS_ENABLED`` env var or ``settings.json``)."""

    return bool(ConfigService.get("apps.enabled", default=False))


# ---------------------------------------------------------------------------
# HTTP helpers (HTTP-only boundary)


def _auth_headers() -> Dict[str, str]:
    """Return the ``Authorization`` header for the internal MCP->API hop, if any.

    When the auth layer is enabled, the mutation endpoints enforce scope, so the
    loopback call must carry the operator-provisioned ``CAO_AUTH_LOCAL_TOKEN``
    (see :func:`security.auth.get_local_bearer`). Default-off this returns an
    empty mapping, so no header is attached and the no-auth posture is
    byte-for-byte unchanged. Applied to reads too (not just mutations) so the
    whole MCP->API hop is consistent.
    """

    token = get_local_bearer()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _get_json(path: str, **params: Any) -> Any:
    """GET ``{API_BASE_URL}{path}`` and return parsed JSON (raises on HTTP error)."""

    response = requests.get(
        f"{API_BASE_URL}{path}",
        params=params or None,
        headers=_auth_headers() or None,
        timeout=_mcp_timeout(),
    )
    response.raise_for_status()
    return response.json()


def _extract_error_detail(response: "requests.Response", fallback: str) -> str:
    """Pull a human-readable ``detail`` out of a FastAPI error response."""

    try:
        payload = response.json()
    except ValueError:
        return fallback
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        detail = detail.get("message")
    return detail if isinstance(detail, str) and detail else fallback


def _post_json(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """POST ``{API_BASE_URL}{path}`` with query params and return parsed JSON.

    The Backplane mutation endpoints (``/sessions``, ``/terminals/*/input``,
    ``/terminals/*/key``, ``/terminals/*/inbox/messages``) take their arguments as
    query parameters, mirroring how ``mcp_server/server.py`` already calls them.
    """

    response = requests.post(
        f"{API_BASE_URL}{path}",
        params={k: v for k, v in (params or {}).items() if v is not None} or None,
        headers=_auth_headers() or None,
        timeout=_mcp_timeout(),
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:  # pragma: no cover - some mutations return empty bodies
        return {}


def _delete_json(path: str) -> Any:
    """DELETE ``{API_BASE_URL}{path}`` and return parsed JSON (raises on HTTP error)."""

    response = requests.delete(
        f"{API_BASE_URL}{path}",
        headers=_auth_headers() or None,
        timeout=_mcp_timeout(),
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:  # pragma: no cover
        return {}


def _fetch_all_terminals(sessions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Aggregate terminals across every session via the per-session listing.

    There is no bare ``GET /terminals`` route on the Backplane, so the dashboard
    fans out over ``GET /sessions/{name}/terminals`` (HTTP-only) and flattens the
    result. A per-session failure is logged and skipped rather than failing the
    whole snapshot.
    """

    terminals: List[Dict[str, Any]] = []
    for session in sessions:
        name = session.get("id") or session.get("name")
        if not name:
            continue
        try:
            terminals.extend(_get_json(f"/sessions/{name}/terminals"))
        except Exception:  # pragma: no cover - defensive per-session isolation
            logger.warning("Failed to list terminals for session %s", name, exc_info=True)
    return terminals


# ---------------------------------------------------------------------------
# read tool implementations


def _render_dashboard_impl() -> Dict[str, Any]:
    """Build the dashboard snapshot from the live Backplane surface (HTTP-only)."""

    sessions = _get_json("/sessions")
    terminals = _fetch_all_terminals(sessions)
    return build_dashboard_snapshot(
        sessions=sessions,
        terminals=terminals,
        scopes=get_scopes_for_local_token(),
    )


def _render_agent_view_impl(terminal_id: str) -> Dict[str, Any]:
    """Build the per-agent detail snapshot for ``ui://cao/agent`` (HTTP-only)."""

    terminal = _get_json(f"/terminals/{terminal_id}")
    output_tail = ""
    output_error = None
    try:
        output = _get_json(f"/terminals/{terminal_id}/output", mode="last")
        if isinstance(output, dict):
            output_tail = output.get("output", "") or ""
    except Exception as exc:
        logger.debug("Could not fetch output tail for terminal %s", terminal_id, exc_info=True)
        response = getattr(exc, "response", None)
        code = getattr(response, "status_code", None)
        output_error = {
            "kind": (
                "internal_error" if isinstance(code, int) and code >= 500 else "output_unavailable"
            ),
            "message": "Output could not be captured; inspect the current turn",
            "http_status": code if isinstance(code, int) else None,
        }
    snapshot = build_agent_detail_snapshot(
        terminal=terminal,
        scopes=get_scopes_for_local_token(),
        output_tail=output_tail,
    )
    if output_error is not None:
        snapshot["output_error"] = output_error
    return snapshot


def _cao_fetch_history_impl(limit: int = 500, kinds: Optional[List[str]] = None) -> Dict[str, Any]:
    """Replay the ring buffer, normalized to the six primitives, newest-last."""

    snapshot = _get_json(
        "/events/history", limit=limit, **({"kinds": ",".join(kinds)} if kinds else {})
    )
    raw = snapshot["events"]
    events: List[Dict[str, Any]] = []
    for event in raw:
        detail = event.get("detail", {}) or {}
        # Events are already normalized at append time; re-normalize defensively
        # when the original lifecycle event_type is preserved in detail.
        if "event_type" in detail:
            kind = normalize_kind(str(detail.get("event_type", event.get("kind"))), detail)
            events.append({**event, "kind": kind})
        else:
            events.append(event)
    return {"events": events, "cursor": snapshot.get("cursor")}


def _subscribe_events_impl(last_event_id: Optional[str] = None) -> Dict[str, Any]:
    """Return the SSE endpoint descriptor the iframe connects to for live events.

    The live stream is delivered over the FastAPI ``/events`` SSE route (not the
    tool channel); the iframe backfills gaps via ``cao_fetch_history``.
    """

    if last_event_id:
        snapshot = _get_json("/events/history", limit=500)
        if not any(event.get("id") == last_event_id for event in snapshot["events"]):
            return {
                "resync_required": True,
                "error": "event_cursor_expired",
                "cursor": last_event_id,
            }
    response = requests.post(
        f"{API_BASE_URL.rstrip('/')}/events/ticket",
        headers=_auth_headers() or None,
        timeout=_mcp_timeout(),
        allow_redirects=False,
    )
    response.raise_for_status()
    params = {"ticket": response.json()["ticket"]}
    if last_event_id:
        params["cursor"] = last_event_id
    url = f"{API_BASE_URL.rstrip('/')}/events?{urlencode(params)}"
    return {
        "url": url,
        "sse_url": url,
        "cursor": last_event_id,
        "history_tool": "cao_fetch_history",
        "ring_capacity": 500,
    }


def _render_graph_view_impl(
    provider: str, scope: Optional[str] = None, scope_id: Optional[str] = None
) -> Dict[str, Any]:
    """Wrap ``GET /graph/{provider}`` for ``ui://cao/graph`` (HTTP-only).

    ``GET /graph/{provider}`` is scope-gated and refuses private tiers
    (``session`` / ``agent`` → 400) as of #424. This tool adds no gate of its
    own; it forwards the caller's ``scope`` / ``scope_id`` and relies on the
    route to enforce auth (it does not bypass it). Lets ``requests.HTTPError``
    propagate (mirrors ``_render_agent_view_impl``).
    """

    result: Dict[str, Any] = _get_json(f"/graph/{provider}", scope=scope, scope_id=scope_id)
    return result


# ---------------------------------------------------------------------------
# the single mutation choke point (Phase II skeleton)


def _classify_kind(kind: str) -> Optional[str]:
    """Return the required scope for ``kind``, or ``None`` if the kind is unknown."""

    if kind in DESTRUCTIVE_KINDS:
        return SCOPE_ADMIN
    if kind in STANDARD_KINDS or kind in LIFECYCLE_KINDS:
        return SCOPE_WRITE
    return None


def _payload_too_large(payload: Dict[str, Any]) -> Optional[str]:
    """Return an error string if ``payload`` exceeds the size cap, else ``None``.

    The cap is measured on the JSON-serialized payload so an oversized value in
    any field (message body, ``allowed_tools`` list, ...) is caught. Returns a
    structured, human-readable warning the iframe surfaces to the operator.
    """

    try:
        size = len(json.dumps(payload, default=str))
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None
    if size > MAX_PAYLOAD_CHARS:
        return f"payload too large: {size} characters exceeds the {MAX_PAYLOAD_CHARS} limit"
    return None


def _submit_command_impl(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Classify -> scope pre-check -> route to the matching Backplane endpoint.

    The single mutation choke point: every state-changing UI
    gesture flows through here. The flow is

    1. **classify** the ``kind`` as standard / lifecycle / destructive (unknown
       kinds are rejected with ``{"success": False, ...}``);
    2. **scope pre-check** — destructive kinds require ``cao:admin``, standard and
       lifecycle kinds require ``cao:write``. A non-empty granted set missing the
       required scope blocks (auth on); the full set (auth off, the default) passes;
    3. **size guard** — an oversized payload is rejected before routing
       — a structured warning rather than a forwarded request;
    4. **route** the authorized command over HTTP to the real FastAPI mutation
       endpoint (HTTP-only boundary).
    """

    required = _classify_kind(kind)
    if required is None:
        return {"success": False, "error": f"unknown command kind: {kind}"}

    scopes = get_scopes_for_local_token()
    # A non-empty granted set missing the required scope blocks (auth on); the
    # full set (auth off, the default) passes every kind.
    if scopes and required not in scopes:
        return {"success": False, "error": f"scope {required} required"}

    # H3: when auth is enabled the internal MCP->API hop must carry a bearer or
    # the FastAPI boundary rejects it with a raw 401. Surface the
    # misconfiguration as a structured, actionable result instead of forwarding
    # a request we know will fail. Default-off this is always None (no-op).
    misconfig = local_auth_misconfig_error()
    if misconfig is not None:
        return {"success": False, "kind": kind, "error": misconfig}

    oversized = _payload_too_large(payload)
    if oversized is not None:
        return {"success": False, "kind": kind, "error": oversized}

    return _route_command(kind, payload)


# ---------------------------------------------------------------------------
# command routing (HTTP-only). Each authorized kind maps to a *real* Backplane
# route discovered by inspecting ``api/main.py``. Where a
# design-named route does not exist, the mapping adapts to the closest existing
# endpoint (documented inline) rather than inventing one.


def _require(payload: Dict[str, Any], *keys: str) -> Optional[str]:
    """Return the first missing required key in ``payload``, or ``None`` if all present."""

    for key in keys:
        value = payload.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            return key
    return None


def _is_turn_projection(value: Any) -> TypeGuard[dict[str, Any]]:
    return (
        isinstance(value, dict)
        and value.get("state")
        in {"none", "pending", "verifying", "reconcile", "verified", "cancelling", "cancelled"}
        and isinstance(value.get("terminal_id"), str)
        and (
            value.get("generation") is None
            or isinstance(value.get("generation"), str)
            and re.fullmatch(r"[0-9a-f]{32}", value["generation"]) is not None
        )
        and isinstance(value.get("allowed_actions"), list)
        and all(action in {"verify", "cancel"} for action in value["allowed_actions"])
        and type(value.get("attempts")) is int
        and value["attempts"] >= 0
    )


def _route_command(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Route an authorized command to its real Backplane mutation endpoint.

    Route map (verified against ``api/main.py``):

    * ``create_session``   -> ``POST /sessions`` (query params; returns a Terminal).
    * ``send_message``     -> ``POST /terminals/{id}/inbox/messages`` — matches the
      existing ``send_message`` MCP tool semantics (``_send_to_inbox``: queued inbox
      delivery), not ``/input`` (which is the direct-type path used by handoff/assign).
    * ``assign``           -> ``POST /terminals/{id}/input`` with
      ``orchestration_type=assign`` — there is no single "create terminal + send"
      assign endpoint, so a reassignment-to-an-existing-agent gesture maps to the
      closest existing route (the direct-input path ``_assign_impl`` uses).
    * ``interrupt``        -> ``POST /terminals/{id}/key`` with ``key="C-c"`` (SIGINT;
      ``C-c`` is accepted by the endpoint's ``TMUX_KEY_PATTERN``).
    * ``pause`` / ``resume`` -> no corresponding terminal routes exist,
      so these return a structured ``{"success": False, "error": "unsupported"}``
      rather than inventing endpoints.
    * ``shutdown_session`` -> ``DELETE /sessions/{name}``.
    """

    try:
        if kind in {"verify_turn", "cancel_turn"}:
            terminal_id, generation = payload.get("terminal_id"), payload.get("generation")
            if (
                not isinstance(terminal_id, str)
                or not re.fullmatch(r"[0-9a-f]{8}", terminal_id)
                or not isinstance(generation, str)
                or not re.fullmatch(r"[0-9a-f]{32}", generation)
            ):
                return {
                    "success": False,
                    "kind": kind,
                    "error": "valid terminal_id and generation required",
                }
            action = "verify" if kind == "verify_turn" else "cancel"
            result = _post_json(
                f"/terminals/{terminal_id}/turn/{action}", {"generation": generation}
            )
            if not _is_turn_projection(result) or result["terminal_id"] != terminal_id:
                return {
                    "success": False,
                    "kind": kind,
                    "error": "Invalid turn recovery response; inspect the current turn",
                }
            accepted = result.get("generation") == generation and result.get("state") == (
                "verified" if action == "verify" else "cancelled"
            )
            return {"success": accepted, "kind": kind, "turn": result}
        if kind == "create_session":
            missing = _require(payload, "agent_profile")
            if missing:
                return {"success": False, "error": f"missing required field: {missing}"}
            params = {
                "agent_profile": payload.get("agent_profile"),
                "provider": payload.get("provider"),
                "session_name": payload.get("session_name"),
                "working_directory": payload.get("working_directory"),
                "allowed_tools": payload.get("allowed_tools"),
            }
            result = _post_json("/sessions", params)
            return {
                "success": True,
                "kind": kind,
                "terminal_id": result.get("id") if isinstance(result, dict) else None,
                "session_name": (result.get("session_name") if isinstance(result, dict) else None),
                "result": result,
            }

        if kind == "send_message":
            receiver = payload.get("terminal_id") or payload.get("receiver_id")
            message = payload.get("message")
            if not receiver:
                return {"success": False, "error": "missing required field: terminal_id"}
            if message is None or (isinstance(message, str) and not message.strip()):
                return {"success": False, "error": "missing required field: message"}
            result = _post_json(
                f"/terminals/{receiver}/inbox/messages",
                {
                    # sender_id is a display label for plugin event emission; the
                    # operator surface has no terminal context, so default to
                    # "operator" unless the gesture supplies one.
                    "sender_id": payload.get("sender_id") or "operator",
                    "message": message,
                },
            )
            return {"success": True, "kind": kind, "result": result}

        if kind == "assign":
            terminal_id = payload.get("terminal_id")
            message = payload.get("message")
            if not terminal_id:
                return {"success": False, "error": "missing required field: terminal_id"}
            if message is None or (isinstance(message, str) and not message.strip()):
                return {"success": False, "error": "missing required field: message"}
            result = _post_json(
                f"/terminals/{terminal_id}/input",
                {
                    "message": message,
                    "sender_id": payload.get("sender_id") or "operator",
                    "orchestration_type": "assign",
                },
            )
            return {"success": True, "kind": kind, "result": result}

        if kind == "interrupt":
            terminal_id = payload.get("terminal_id")
            if not terminal_id:
                return {"success": False, "error": "missing required field: terminal_id"}
            # SIGINT to the foreground process == Ctrl-C; the /key endpoint accepts
            # "C-c" via its TMUX_KEY_PATTERN.
            result = _post_json(f"/terminals/{terminal_id}/key", {"key": "C-c"})
            return {"success": True, "kind": kind, "result": result}

        if kind in ("pause", "resume"):
            # No pause/resume terminal routes exist. Return a
            # structured unsupported result rather than inventing endpoints.
            return {
                "success": False,
                "kind": kind,
                "error": "unsupported",
                "detail": f"{kind} has no corresponding Backplane route",
            }

        if kind == "shutdown_session":
            session_name = payload.get("session_name")
            if not session_name:
                return {"success": False, "error": "missing required field: session_name"}
            result = _delete_json(f"/sessions/{session_name}")
            return {"success": True, "kind": kind, "result": result}

        # Defensive: classification already rejected unknown kinds.
        return {"success": False, "error": f"unroutable command kind: {kind}"}

    except requests.HTTPError as exc:  # pragma: no cover - exercised via mocked errors
        detail = str(exc)
        if exc.response is not None:
            try:
                body = exc.response.json()
                turn = body.get("detail") if isinstance(body, dict) else None
            except ValueError:
                turn = None
            if (
                kind in {"verify_turn", "cancel_turn"}
                and _is_turn_projection(turn)
                and turn["terminal_id"] == payload.get("terminal_id")
            ):
                return {
                    "success": False,
                    "kind": kind,
                    "turn": turn,
                    "http_status": exc.response.status_code,
                    "error": "Turn recovery was not accepted; inspect its current generation",
                }
            detail = _extract_error_detail(exc.response, detail)
        logger.warning("submit_command routing failed for kind=%s: %s", kind, detail)
        return {"success": False, "kind": kind, "error": detail}
    except requests.RequestException as exc:  # pragma: no cover - network failure path
        logger.warning("submit_command transport error for kind=%s: %s", kind, exc)
        return {"success": False, "kind": kind, "error": str(exc)}


# ---------------------------------------------------------------------------
# registration


def register_app_tools(mcp: Any) -> bool:
    """Register the five MCP App tools on the FastMCP server.

    Best-effort and side-effect free when disabled:

    * returns ``False`` (logging at info level) when ``CAO_MCP_APPS_ENABLED`` is
      unset or when registration cannot proceed for any reason;
    * otherwise registers the five tools (with ``visibility`` + ``_meta.ui``
      annotations), also mounts the ``ui://cao/*`` resources via
      :func:`ext_apps.register_apps`, and returns ``True``.
    """

    if not _is_enabled():
        logger.info(
            "MCP Apps disabled (CAO_MCP_APPS_ENABLED unset); skipping app tool registration"
        )
        return False

    tool_decorator = getattr(mcp, "tool", None)
    if not callable(tool_decorator):
        logger.info("FastMCP build has no @mcp.tool decorator; skipping app tool registration")
        return False

    def _register(
        fn: Any,
        name: str,
        visibility: List[str],
        resource_uri: Optional[str],
        required_scopes: Optional[List[str]],
    ) -> bool:
        # SEP-1865 shape: visibility + resourceUri live *under* _meta.ui (per the
        # authoritative SEP-1865 spec; see docs/mcp-apps.md).
        meta = ui_meta(
            required_scopes=required_scopes,
            visibility=visibility,
            resource_uri=resource_uri,
        )
        if visibility == ["app"]:
            # Native FastMCP app lookup preserves app-only visibility and auth.
            meta["fastmcp"] = {"app": "cao"}
        try:
            tool_decorator(name=name, meta=meta)(fn)
            return True
        except TypeError:
            # Older FastMCP without a ``meta`` kwarg: register without annotations
            # rather than failing the whole registration.
            try:
                tool_decorator(name=name)(fn)
                logger.info("Registered %s without _meta (FastMCP lacks meta kwarg)", name)
                return True
            except Exception:  # pragma: no cover - defensive
                logger.exception("Failed to register app tool %s", name)
                return False
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to register app tool %s", name)
            return False

    async def render_dashboard() -> Dict[str, Any]:
        """Return a fleet Dashboard_Snapshot built from the Backplane (HTTP-only)."""
        return _render_dashboard_impl()

    async def render_agent_view(terminal_id: str) -> Dict[str, Any]:
        """Return a per-agent detail snapshot for the given terminal."""
        return _render_agent_view_impl(terminal_id)

    async def cao_fetch_history(
        limit: int = 500, kinds: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Replay recent fleet events (six-primitive vocabulary, newest-last)."""
        return _cao_fetch_history_impl(limit=limit, kinds=kinds)

    async def subscribe_events(last_event_id: Optional[str] = None) -> Dict[str, Any]:
        """Return the SSE endpoint descriptor for live events."""
        return _subscribe_events_impl(last_event_id=last_event_id)

    async def render_graph_view(
        provider: str, scope: Optional[str] = None, scope_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return a GraphView wire shape for the given provider via ``GET /graph/{provider}``."""
        return _render_graph_view_impl(provider, scope=scope, scope_id=scope_id)

    async def submit_command(kind: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Single mutation choke point: classify, scope-check, then route (Phase III)."""
        return _submit_command_impl(kind, payload or {})

    ok = True
    ok &= _register(
        render_dashboard, "render_dashboard", ["model", "app"], DASHBOARD_RESOURCE_URI, None
    )
    ok &= _register(
        render_agent_view, "render_agent_view", ["model", "app"], AGENT_RESOURCE_URI, None
    )
    ok &= _register(
        cao_fetch_history, "cao_fetch_history", ["app"], EVENT_STREAM_RESOURCE_URI, None
    )
    ok &= _register(subscribe_events, "subscribe_events", ["app"], EVENT_STREAM_RESOURCE_URI, None)
    ok &= _register(
        render_graph_view, "render_graph_view", ["model", "app"], GRAPH_RESOURCE_URI, None
    )
    ok &= _register(submit_command, "submit_command", ["app"], None, [SCOPE_WRITE, SCOPE_ADMIN])

    # Mount the ui://cao/* resources too (best-effort; independent of tool result).
    try:
        register_apps(mcp)
    except Exception:  # pragma: no cover - defensive
        logger.exception("register_apps failed during app tool registration")

    logger.info("MCP App tools registered (success=%s)", ok)
    return ok
