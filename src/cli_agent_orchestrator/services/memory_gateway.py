"""Route MCP memory through its authenticated local or remote authority."""

from __future__ import annotations

import asyncio
import math
import os
import re
from typing import Any, Optional
from urllib.parse import urlsplit

import requests

from cli_agent_orchestrator.constants import API_BASE_URL, MCP_REQUEST_TIMEOUT
from cli_agent_orchestrator.security.auth import get_local_bearer
from cli_agent_orchestrator.services.elastic_worker_gateway import (
    elastic_worker_gateway_headers,
)
from cli_agent_orchestrator.services.memory_service import MemoryPartialWriteError, MemoryService
from cli_agent_orchestrator.services.secret_gate import scan_for_secrets


_IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_RECOVERY_CURSOR = re.compile(r"kcr1_[A-Za-z0-9_-]{43}")
_MAX_VERSION = 2**63 - 1
_MAX_CONTENT_BYTES = 1048576
_RETENTION_POLICY = "immutable_history_tombstones_retain_metadata_cursor_ttl_only"
_REVISION_FIELDS = frozenset(
    {
        "schema_version",
        "record_id",
        "revision",
        "record_version",
        "scope",
        "scope_id",
        "producer_principal_id",
        "work_item_id",
        "attempt_id",
        "source_artifact_id",
        "source_hash",
        "delivered_hash",
        "evidence_refs",
        "confidence",
        "fresh_until",
        "decision",
        "supersedes",
        "tombstone",
        "legacy",
        "content",
        "redacted",
        "truncated",
        "created_at",
    }
)
_RECOVERY_FIELDS = frozenset(
    {
        "schema_version",
        "revisions",
        "checkpoint",
        "next_cursor",
        "expires_at",
        "retention_policy",
    }
)


def remote_memory_url() -> Optional[str]:
    value = os.environ.get("CAO_MEMORY_API_URL", "").strip()
    return value.rstrip("/") if value else None


def _headers() -> dict[str, str]:
    headers = elastic_worker_gateway_headers()
    token = get_local_bearer()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _timeout() -> float:
    return float(MCP_REQUEST_TIMEOUT)


def _knowledge_authority_url() -> str:
    """Return one safe configured authority URL, never a fallback destination."""
    configured_authority = os.environ.get("CAO_MEMORY_API_URL")
    if configured_authority is not None and any(
        ord(character) < 32 or ord(character) == 127 for character in configured_authority
    ):
        raise ValueError("invalid knowledge authority URL")
    authority = remote_memory_url() or API_BASE_URL
    if (
        not isinstance(authority, str)
        or authority != authority.strip()
        or "\\" in authority
        or "?" in authority
        or "#" in authority
    ):
        raise ValueError("invalid knowledge authority URL")
    try:
        parsed = urlsplit(authority)
        hostname = parsed.hostname
        _ = parsed.port
        requests.Request("GET", f"{authority.rstrip('/')}/v1/knowledge/recovery").prepare()
    except (requests.RequestException, ValueError):
        raise ValueError("invalid knowledge authority URL") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(ord(character) < 33 or ord(character) == 127 for character in authority)
    ):
        raise ValueError("invalid knowledge authority URL")
    return authority.rstrip("/")


def _is_identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value)) and not scan_for_secrets(value)


def _require_identifier(value: Any, field: str) -> str:
    if not _is_identifier(value):
        raise ValueError(f"invalid {field}")
    return value


def _is_number(value: Any, *, minimum: float, maximum: float) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum


def _is_version(value: Any, *, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= _MAX_VERSION


def _require_proposal_inputs(
    record_id: Any,
    *,
    job_id: Any,
    grant_id: Any,
    grant_revision: Any,
    expected_version: Any,
    scope: Any,
    scope_id: Any,
    content: Any,
    evidence_refs: Any,
    confidence: Any,
    fresh_until: Any,
) -> None:
    _require_identifier(record_id, "record_id")
    if record_id in {".", ".."}:
        raise ValueError("invalid record_id")
    _require_identifier(job_id, "job_id")
    _require_identifier(grant_id, "grant_id")
    if not _is_version(grant_revision, minimum=1):
        raise ValueError("invalid grant_revision")
    if not _is_version(expected_version):
        raise ValueError("invalid expected_version")
    if not isinstance(scope, str) or scope not in {"project", "job"}:
        raise ValueError("invalid scope")
    _require_identifier(scope_id, "scope_id")
    if not isinstance(content, str):
        raise ValueError("invalid content")
    try:
        content_size = len(content.encode("utf-8"))
    except UnicodeError:
        raise ValueError("invalid content") from None
    if content_size > _MAX_CONTENT_BYTES:
        raise ValueError("invalid content")
    if not isinstance(evidence_refs, list) or len(evidence_refs) > 64:
        raise ValueError("invalid evidence_refs")
    references = tuple(_require_identifier(reference, "evidence_refs") for reference in evidence_refs)
    if len(set(references)) != len(references):
        raise ValueError("invalid evidence_refs")
    if not _is_number(confidence, minimum=0, maximum=1):
        raise ValueError("invalid confidence")
    if not _is_number(fresh_until, minimum=0, maximum=1e15) or not 0 < fresh_until < 1e15:
        raise ValueError("invalid fresh_until")


def _require_recovery_inputs(
    *,
    job_id: Any,
    grant_id: Any,
    grant_revision: Any,
    scope: Any,
    scope_id: Any,
    limit: Any,
    cursor: Any,
) -> None:
    _require_identifier(job_id, "job_id")
    _require_identifier(grant_id, "grant_id")
    if not _is_version(grant_revision, minimum=1):
        raise ValueError("invalid grant_revision")
    if not isinstance(scope, str) or scope not in {"project", "job"}:
        raise ValueError("invalid scope")
    _require_identifier(scope_id, "scope_id")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("invalid limit")
    if cursor is not None and (
        not isinstance(cursor, str) or not _RECOVERY_CURSOR.fullmatch(cursor)
    ):
        raise ValueError("invalid cursor")


def _response_json(response: requests.Response, message: str) -> Any:
    try:
        return response.json()
    except (TypeError, ValueError):
        raise ValueError(message) from None


def _is_revision_payload(payload: Any) -> bool:
    if not isinstance(payload, dict) or set(payload) != _REVISION_FIELDS:
        return False
    optional_strings = ("work_item_id", "attempt_id", "source_artifact_id", "content")
    return (
        type(payload["schema_version"]) is int
        and payload["schema_version"] == 1
        and _is_identifier(payload["record_id"])
        and _is_version(payload["revision"], minimum=1)
        and _is_version(payload["record_version"], minimum=1)
        and isinstance(payload["scope"], str)
        and payload["scope"] in {"project", "job"}
        and _is_identifier(payload["scope_id"])
        and isinstance(payload["producer_principal_id"], str)
        and all(
            value is None or isinstance(value, str)
            for value in (payload[name] for name in optional_strings)
        )
        and isinstance(payload["source_hash"], str)
        and bool(re.fullmatch(r"[a-f0-9]{64}", payload["source_hash"]))
        and isinstance(payload["delivered_hash"], str)
        and bool(re.fullmatch(r"[a-f0-9]{64}", payload["delivered_hash"]))
        and isinstance(payload["evidence_refs"], list)
        and len(payload["evidence_refs"]) <= 64
        and all(_is_identifier(reference) for reference in payload["evidence_refs"])
        and len(set(payload["evidence_refs"])) == len(payload["evidence_refs"])
        and _is_number(payload["confidence"], minimum=0, maximum=1)
        and _is_number(payload["fresh_until"], minimum=0, maximum=1e15)
        and 0 < payload["fresh_until"] < 1e15
        and isinstance(payload["decision"], str)
        and payload["decision"] in {"proposed", "verified", "approved", "rejected", "superseded"}
        and (payload["supersedes"] is None or _is_version(payload["supersedes"], minimum=1))
        and type(payload["tombstone"]) is bool
        and type(payload["legacy"]) is bool
        and type(payload["redacted"]) is bool
        and type(payload["truncated"]) is bool
        and _is_number(payload["created_at"], minimum=0, maximum=1e15)
        and payload["created_at"] > 0
    )


def _is_recovery_page(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and set(payload) == _RECOVERY_FIELDS
        and type(payload["schema_version"]) is int
        and payload["schema_version"] == 1
        and isinstance(payload["revisions"], list)
        and all(_is_revision_payload(revision) for revision in payload["revisions"])
        and isinstance(payload["checkpoint"], str)
        and bool(re.fullmatch(r"[a-f0-9]{64}", payload["checkpoint"]))
        and (
            payload["next_cursor"] is None
            or (
                isinstance(payload["next_cursor"], str)
                and bool(_RECOVERY_CURSOR.fullmatch(payload["next_cursor"]))
            )
        )
        and _is_number(payload["expires_at"], minimum=0, maximum=1e15)
        and payload["expires_at"] > 0
        and payload["retention_policy"] == _RETENTION_POLICY
    )


def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    base_url = remote_memory_url() or API_BASE_URL
    response = requests.post(
        f"{base_url}{path}",
        json=body,
        headers=_headers() or None,
        timeout=_timeout(),
    )
    if response.status_code >= 400:
        try:
            error = response.json()
        except (TypeError, ValueError):
            error = {}
        if error.get("error_kind") == MemoryPartialWriteError.error_kind:
            partial = error.get("partial_write")
            if isinstance(partial, dict):
                required = ("key", "scope", "file_path")
                if all(isinstance(partial.get(field), str) for field in required):
                    scope_id = partial.get("scope_id")
                    if scope_id is None or isinstance(scope_id, str):
                        raise MemoryPartialWriteError(
                            key=partial["key"],
                            scope=partial["scope"],
                            scope_id=scope_id,
                            file_path=partial["file_path"],
                        )
    response.raise_for_status()
    result: dict[str, Any] = response.json()
    return result


def _require_enabled():
    from cli_agent_orchestrator.services.memory_service import (
        _is_memory_enabled,
        MemoryDisabledError,
        MEMORY_DISABLED_MESSAGE,
    )

    if not _is_memory_enabled():
        raise MemoryDisabledError(MEMORY_DISABLED_MESSAGE)


async def store_memory(
    *,
    content: str,
    scope: str,
    memory_type: str,
    key: Optional[str],
    tags: str,
    terminal_context: Optional[dict[str, Any]],
):
    _require_enabled()
    payload = await asyncio.to_thread(
        _post,
        "/internal/memory/store",
        {
            "content": content,
            "scope": scope,
            "memory_type": memory_type,
            "key": key,
            "tags": tags,
            "terminal_context": terminal_context,
        },
    )
    from cli_agent_orchestrator.models.memory import Memory

    memory = Memory.model_validate(payload["memory"])
    memory.action = payload.get("action")
    return memory


async def recall_memory(
    *,
    query: Optional[str],
    scope: Optional[str],
    memory_type: Optional[str],
    limit: int,
    terminal_context: Optional[dict[str, Any]],
    search_mode: str,
    sort_by: str,
    include_related: bool,
):
    payload = await asyncio.to_thread(
        _post,
        "/internal/memory/recall",
        {
            "query": query,
            "scope": scope,
            "memory_type": memory_type,
            "limit": limit,
            "terminal_context": terminal_context,
            "search_mode": search_mode,
            "sort_by": sort_by,
            "include_related": include_related,
        },
    )
    from cli_agent_orchestrator.models.memory import Memory

    return [Memory.model_validate(item) for item in payload["memories"]]


async def forget_memory(
    *,
    key: str,
    scope: str,
    terminal_context: Optional[dict[str, Any]],
) -> bool:
    _require_enabled()
    payload = await asyncio.to_thread(
        _post,
        "/internal/memory/forget",
        {"key": key, "scope": scope, "terminal_context": terminal_context},
    )
    return bool(payload["deleted"])


def memory_context_for_terminal(terminal_id: str, task_description: str = "") -> str:
    service = MemoryService()
    if not remote_memory_url():
        return service.get_curated_memory_context(
            terminal_id,
            task_description=task_description,
        )
    terminal_context = service._get_terminal_context(terminal_id)
    if not terminal_context:
        return ""
    payload = _post(
        "/internal/memory/context",
        {"terminal_context": terminal_context, "budget_chars": 3000},
    )
    return str(payload.get("context", ""))


def knowledge_get(path, *, local_transport=None, **params):
    """One authenticated authority; selecting a remote never enables local fallback."""
    if not isinstance(path, str) or not path.startswith("/v1/knowledge/"):
        raise ValueError("invalid knowledge endpoint")
    remote = remote_memory_url()
    if remote is None and local_transport is not None:
        return local_transport(path, **params)
    response = requests.get(
        f"{remote or API_BASE_URL}{path}",
        params={key: value for key, value in params.items() if value is not None} or None,
        headers=_headers() or None,
        timeout=_timeout(),
        allow_redirects=False,
    )
    if 300 <= response.status_code < 400:
        raise ValueError("knowledge authority redirects are not accepted")
    response.raise_for_status()
    return response.json()


class KnowledgeRevisionConflict(Exception):
    """The authority rejected a stale known knowledge revision."""

    def __init__(self) -> None:
        self.code = "knowledge_revision_conflict"
        self.required_action = "read_current_revision"
        super().__init__("knowledge revision conflict; read the current revision")


class KnowledgeRecoveryCursorExpired(Exception):
    """The authority requires an explicit new authorized recovery request."""

    def __init__(self) -> None:
        self.code = "knowledge_cursor_expired"
        self.required_action = "restart_authorized_snapshot"
        super().__init__("knowledge recovery cursor expired; restart the authorized snapshot")


def _is_exact_error(
    response: requests.Response,
    *,
    status_code: int,
    code: str,
    message: str,
    required_action: str,
) -> bool:
    if response.status_code != status_code:
        return False
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict) or set(payload) != {"detail"}:
        return False
    detail = payload["detail"]
    return (
        isinstance(detail, dict)
        and set(detail) == {"code", "message", "retryable", "required_action"}
        and detail["code"] == code
        and type(detail["message"]) is str
        and detail["message"] == message
        and type(detail["retryable"]) is bool
        and detail["retryable"] is False
        and detail["required_action"] == required_action
    )


def _is_known_revision_conflict(response: requests.Response) -> bool:
    return _is_exact_error(
        response,
        status_code=409,
        code="knowledge_revision_conflict",
        message="Knowledge revision changed.",
        required_action="read_current_revision",
    )


def _is_known_cursor_expired(response: requests.Response) -> bool:
    return _is_exact_error(
        response,
        status_code=410,
        code="knowledge_cursor_expired",
        message="Knowledge recovery cursor expired.",
        required_action="restart_authorized_snapshot",
    )


def knowledge_propose_revision(
    record_id: str,
    *,
    job_id: str,
    grant_id: str,
    grant_revision: int,
    expected_version: int,
    scope: str,
    scope_id: str,
    content: str,
    evidence_refs: list[str],
    confidence: float,
    fresh_until: float,
) -> dict[str, Any]:
    """Propose one revision through the configured knowledge authority."""
    _require_proposal_inputs(
        record_id,
        job_id=job_id,
        grant_id=grant_id,
        grant_revision=grant_revision,
        expected_version=expected_version,
        scope=scope,
        scope_id=scope_id,
        content=content,
        evidence_refs=evidence_refs,
        confidence=confidence,
        fresh_until=fresh_until,
    )
    authority = _knowledge_authority_url()
    response = requests.post(
        f"{authority}/v1/knowledge/records/{record_id}/revisions",
        params={
            "job_id": job_id,
            "grant_id": grant_id,
            "grant_revision": grant_revision,
        },
        json={
            "schema_version": 1,
            "expected_version": expected_version,
            "scope": scope,
            "scope_id": scope_id,
            "content": content,
            "evidence_refs": evidence_refs,
            "confidence": confidence,
            "fresh_until": fresh_until,
        },
        headers=_headers() or None,
        timeout=_timeout(),
        allow_redirects=False,
    )
    if 300 <= response.status_code < 400:
        raise ValueError("knowledge authority redirects are not accepted")
    if _is_known_revision_conflict(response):
        raise KnowledgeRevisionConflict()
    if response.status_code != 201:
        response.raise_for_status()
        raise ValueError("unexpected knowledge revision response status")
    payload = _response_json(response, "malformed knowledge revision response")
    if not _is_revision_payload(payload):
        raise ValueError("malformed knowledge revision response")
    return payload


def knowledge_recovery_page(
    *,
    job_id: str,
    grant_id: str,
    grant_revision: int,
    scope: str,
    scope_id: str,
    limit: int,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Read one validated immutable-history page without retrying or restarting it."""
    _require_recovery_inputs(
        job_id=job_id,
        grant_id=grant_id,
        grant_revision=grant_revision,
        scope=scope,
        scope_id=scope_id,
        limit=limit,
        cursor=cursor,
    )
    authority = _knowledge_authority_url()
    response = requests.get(
        f"{authority}/v1/knowledge/recovery",
        params={
            "schema_version": 1,
            "job_id": job_id,
            "grant_id": grant_id,
            "grant_revision": grant_revision,
            "scope": scope,
            "scope_id": scope_id,
            "limit": limit,
            "cursor": cursor,
        },
        headers=_headers() or None,
        timeout=_timeout(),
        allow_redirects=False,
    )
    if 300 <= response.status_code < 400:
        raise ValueError("knowledge authority redirects are not accepted")
    if _is_known_cursor_expired(response):
        raise KnowledgeRecoveryCursorExpired()
    if response.status_code != 200:
        response.raise_for_status()
        raise ValueError("unexpected knowledge recovery response status")
    payload = _response_json(response, "malformed knowledge recovery response")
    if not _is_recovery_page(payload):
        raise ValueError("malformed knowledge recovery response")
    return payload
