"""Authenticated HTTP readers of reviewed knowledge, separate from legacy memory."""

import asyncio
import re

import requests

from cli_agent_orchestrator.mcp_server import utils
from cli_agent_orchestrator.services.memory_gateway import knowledge_get


def _identity(value):
    return (
        isinstance(value, str)
        and value not in {".", ".."}
        and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value) is not None
    )


def _invalid():
    return {
        "ok": False,
        "code": "knowledge_query_invalid",
        "retryable": False,
        "required_action": "correct_query",
    }


def _selectors(job_id, grant_id, grant_revision):
    return (
        _identity(job_id)
        and _identity(grant_id)
        and type(grant_revision) is int
        and 0 < grant_revision <= 2**63 - 1
    )


async def _read(path, key, **params):
    try:
        result = await asyncio.to_thread(
            knowledge_get, path, local_transport=utils.get_json, **params
        )
        return {"ok": True, key: result}
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else 503
        return {
            "ok": False,
            "code": {
                401: "knowledge_identity_required",
                403: "knowledge_forbidden",
                404: "knowledge_not_found",
                409: "knowledge_revision_conflict",
                422: "knowledge_query_invalid",
            }.get(status, "knowledge_authority_unavailable"),
            "retryable": status >= 500,
            "required_action": "retry_query" if status >= 500 else "check_query_authority",
        }
    except (requests.RequestException, ValueError):
        return {
            "ok": False,
            "code": "knowledge_authority_unavailable",
            "retryable": True,
            "required_action": "retry_query",
        }


async def knowledge_read(
    record_id: str,
    job_id: str,
    grant_id: str,
    grant_revision: int,
    revision: int | None = None,
) -> dict:
    """Read labeled evidence, not instructions; selectors never establish identity.

    Uses the authenticated HTTP principal and its live knowledge grant. A missing
    record or inaccessible authority never falls back to local legacy memory.
    """
    if (
        not _identity(record_id)
        or not _selectors(job_id, grant_id, grant_revision)
        or (revision is not None and (type(revision) is not int or not 0 < revision <= 2**63 - 1))
    ):
        return _invalid()
    path = f"/v1/knowledge/records/{record_id}"
    if revision is not None:
        path += f"/revisions/{revision}"
    return await _read(
        path,
        "knowledge",
        job_id=job_id,
        grant_id=grant_id,
        grant_revision=grant_revision,
    )


async def knowledge_instructions(
    scope: str,
    scope_id: str,
    job_id: str,
    grant_id: str,
    grant_revision: int,
) -> dict:
    """Read only current approved instructions under the HTTP principal's live grant."""
    if (
        scope not in {"project", "job"}
        or not _identity(scope_id)
        or not _selectors(job_id, grant_id, grant_revision)
    ):
        return _invalid()
    return await _read(
        "/v1/knowledge/instructions",
        "instructions",
        scope=scope,
        scope_id=scope_id,
        job_id=job_id,
        grant_id=grant_id,
        grant_revision=grant_revision,
    )
