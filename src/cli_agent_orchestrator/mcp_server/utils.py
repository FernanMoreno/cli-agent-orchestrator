"""MCP server utilities.

HTTP-only: like the rest of ``mcp_server``, this module reaches Backplane state
exclusively through the FastAPI surface over HTTP (never through
``clients.database`` / ``clients.tmux``), preserving the auditable MCP boundary
enforced by ``test/test_http_only_boundary.py``.
"""

import ipaddress
import logging
import os
import stat
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import requests

from cli_agent_orchestrator.constants import API_BASE_URL
from cli_agent_orchestrator.security.auth import get_local_bearer
from cli_agent_orchestrator.utils.orchestration import _mcp_timeout

logger = logging.getLogger(__name__)

INTERACTION_CAPABILITY_FILE_ENV = "CAO_INTERACTION_CAPABILITY_FILE"
INTERACTION_CAPABILITY_HEADER = "X-CAO-Interaction-Capability"


class InteractionCapabilityUnavailable(RuntimeError):
    """The bundled MCP process lacks a readable per-terminal capability file."""


def _auth_headers() -> Dict[str, str]:
    """Return the ``Authorization`` header for the internal MCP->API hop, if any.

    Mirrors ``app_tools._auth_headers``: attaches the operator-provisioned
    ``CAO_AUTH_LOCAL_TOKEN`` when the auth layer is enabled, and returns an empty
    mapping default-off so the no-auth posture is byte-for-byte unchanged. Reads
    are not scope-gated today, but the header is attached for consistency so the
    whole MCP->API hop behaves the same with auth on.
    """

    token = get_local_bearer()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _read_interaction_capability() -> str:
    """Read the private per-incarnation credential without logging its value.

    Only the path is placed in the bundled MCP environment. The token is not
    accepted from a second environment variable, request body, or CLI argument.
    ``O_NOFOLLOW`` prevents a symlink swap where supported, and the file must
    be private to the current OS user on POSIX systems.
    """
    path = os.environ.get(INTERACTION_CAPABILITY_FILE_ENV, "").strip()
    if not path:
        raise InteractionCapabilityUnavailable("agent interaction capability is unavailable")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        # Reject FIFOs without blocking the bundled MCP process on open.
        flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode) or details.st_size > 512:
                raise InteractionCapabilityUnavailable(
                    "agent interaction capability file is invalid"
                )
            if os.name == "posix" and details.st_uid != os.geteuid():
                raise InteractionCapabilityUnavailable(
                    "agent interaction capability file owner is invalid"
                )
            if os.name == "posix" and stat.S_IMODE(details.st_mode) & 0o077:
                raise InteractionCapabilityUnavailable(
                    "agent interaction capability file permissions are too broad"
                )
            raw = os.read(descriptor, 513)
        finally:
            os.close(descriptor)
    except InteractionCapabilityUnavailable:
        raise
    except OSError as exc:
        # Do not include the path or exception text; the path may be user data.
        raise InteractionCapabilityUnavailable(
            "agent interaction capability file cannot be read"
        ) from None
    try:
        capability = raw.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError:
        raise InteractionCapabilityUnavailable(
            "agent interaction capability file is invalid"
        ) from None
    if not capability or len(capability) > 512 or any(ch.isspace() for ch in capability):
        raise InteractionCapabilityUnavailable("agent interaction capability is invalid")
    return capability


def _interaction_headers(
    *,
    idempotency_key: Optional[str] = None,
    if_match: Optional[str] = None,
    capability: Optional[str] = None,
) -> Dict[str, str]:
    """Return service-hop plus per-source identity headers for agent routes."""
    headers = _auth_headers()
    headers[INTERACTION_CAPABILITY_HEADER] = (
        _read_interaction_capability() if capability is None else capability
    )
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    if if_match is not None:
        headers["If-Match"] = if_match
    return headers


def interaction_get_json(
    path: str,
    *,
    timeout: Optional[float] = None,
    **params: Any,
) -> Any:
    """GET a per-source interaction over direct, local-only HTTP."""
    with requests.Session() as client:
        client.trust_env = False
        response = client.request(
            "GET",
            _local_api_url(path),
            params={key: value for key, value in params.items() if value is not None} or None,
            headers=_interaction_headers(),
            timeout=_mcp_timeout() if timeout is None else timeout,
            allow_redirects=False,
        )
    response.raise_for_status()
    return response.json()


def interaction_post_json(
    path: str,
    body: Dict[str, Any],
    *,
    idempotency_key: Optional[str] = None,
    if_match: Optional[str] = None,
    timeout: Optional[float] = None,
    capability: Optional[str] = None,
) -> Any:
    """POST typed content directly to the local API without proxy forwarding."""
    with requests.Session() as client:
        client.trust_env = False
        response = client.request(
            "POST",
            _local_api_url(path),
            json=body,
            headers=_interaction_headers(
                idempotency_key=idempotency_key, if_match=if_match, capability=capability
            ),
            timeout=_mcp_timeout() if timeout is None else timeout,
            allow_redirects=False,
        )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:  # pragma: no cover - API mutations normally return JSON
        return {}


def get_json(path: str, *, timeout: Optional[float] = None, **params: Any) -> Any:
    """GET ``path`` and return parsed JSON. ``None`` params are dropped."""

    response = requests.get(
        f"{API_BASE_URL}{path}",
        params={k: v for k, v in params.items() if v is not None} or None,
        headers=_auth_headers() or None,
        timeout=_mcp_timeout() if timeout is None else timeout,
    )
    response.raise_for_status()
    return response.json()


def post_body_json(path: str, body: Dict[str, Any], *, timeout: Optional[float] = None) -> Any:
    """POST ``path`` with a JSON BODY.

    A body rather than query parameters because the payloads these carry include
    free text (``friction_notes``) that would be mangled in a URL.
    """

    response = requests.post(
        f"{API_BASE_URL}{path}",
        json=body,
        headers=_auth_headers() or None,
        timeout=_mcp_timeout() if timeout is None else timeout,
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:  # pragma: no cover - some mutations return empty bodies
        return {}


def _local_api_url(path: str) -> str:
    """Build a direct IPv4 loopback URL for local CAO-only operations."""
    base = urlsplit(API_BASE_URL)
    hostname = base.hostname
    try:
        address = ipaddress.ip_address(hostname) if hostname else None
    except ValueError:
        address = None
    local_hostname = hostname is not None and hostname.lower() in {"localhost", "0.0.0.0"}
    local_address = hostname == "127.0.0.1"
    try:
        port = base.port
    except ValueError:
        port = None
    if base.scheme != "http" or not (local_hostname or local_address) or port is None:
        raise ValueError("local CAO peer tools require a loopback API endpoint")
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("local CAO API paths must be absolute paths")
    return f"http://127.0.0.1:{port}{path}"


def local_get_json(path: str, *, timeout: Optional[float] = None, **params: Any) -> Any:
    """GET a local-only CAO endpoint without environment proxy routing."""
    with requests.Session() as client:
        client.trust_env = False
        response = client.request(
            "GET",
            _local_api_url(path),
            params={k: v for k, v in params.items() if v is not None} or None,
            headers=_auth_headers() or None,
            timeout=_mcp_timeout() if timeout is None else timeout,
        )
    response.raise_for_status()
    return response.json()


def local_post_body_json(
    path: str, body: Dict[str, Any], *, timeout: Optional[float] = None
) -> Any:
    """POST to a local-only CAO endpoint without environment proxy routing."""
    with requests.Session() as client:
        client.trust_env = False
        response = client.request(
            "POST",
            _local_api_url(path),
            json=body,
            headers=_auth_headers() or None,
            timeout=_mcp_timeout() if timeout is None else timeout,
        )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:  # pragma: no cover - some mutations return empty bodies
        return {}


def get_terminal_record(terminal_id: str) -> Optional[Dict[str, Any]]:
    """Return the terminal record for ``terminal_id`` from the Backplane.

    Fetches the record over HTTP via ``GET /terminals/{id}`` rather than
    touching the database directly, keeping the MCP server inside its
    HTTP-only boundary.

    Args:
        terminal_id: The terminal identifier to look up.

    Returns:
        The terminal record as a dict, or ``None`` if the terminal does not
        exist (HTTP 404) or the Backplane is unreachable.
    """

    try:
        response = requests.get(
            f"{API_BASE_URL}/terminals/{terminal_id}",
            headers=_auth_headers() or None,
            timeout=_mcp_timeout(),
        )
    except requests.RequestException as exc:
        logger.warning("Failed to fetch terminal record for %s: %s", terminal_id, exc)
        return None

    if response.status_code == 404:
        return None
    response.raise_for_status()
    record: Dict[str, Any] = response.json()
    return record
