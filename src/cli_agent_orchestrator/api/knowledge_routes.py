"""Additive v1 knowledge authority; no legacy memory or recovery-cursor fallback."""

from contextlib import contextmanager
import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from cli_agent_orchestrator.clients.work_repository import SchemaMismatch, WorkRepository
from cli_agent_orchestrator.models.memory import KnowledgeRevision, KnowledgeScope
from cli_agent_orchestrator.security.auth import Principal, get_current_principal
from cli_agent_orchestrator.services.knowledge_policy import (
    KnowledgePolicy,
    LegacyMemoryAuditError,
    audit_legacy_memory_denied,
    require_legacy_operator,
)
from cli_agent_orchestrator.services.memory_service import MemoryService, MemoryDisabledError
from cli_agent_orchestrator.services.knowledge_revisions import (
    KnowledgeConflict,
    KnowledgeCursorExpired,
)


def _error(code: str, message: str, *, retryable=False, required_action="check_request"):
    return {
        "code": code,
        "message": message,
        "retryable": retryable,
        "required_action": required_action,
    }


class KnowledgeRoute(APIRoute):
    """Keep secret-bearing validation inputs out of HTTP diagnostics, router-locally."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def bounded_handler(request: Request):
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 1048576 + 16384:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": _error(
                                "knowledge_input_too_large", "Knowledge request exceeds byte limit."
                            )
                        },
                    )
                chunks.append(chunk)
            request._body = b"".join(chunks)
            try:
                return await original(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": _error("knowledge_request_invalid", "Invalid knowledge request.")
                    },
                )

        return bounded_handler


router = APIRouter(prefix="/v1/knowledge", tags=["knowledge-authority"], route_class=KnowledgeRoute)


class VersionedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Annotated[StrictInt, Field(ge=1, le=1)]
    expected_version: Annotated[StrictInt, Field(ge=0, le=2**63 - 1)]


class ProposalRequest(VersionedRequest):
    scope: KnowledgeScope
    scope_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    content: Annotated[StrictStr, Field(max_length=1048576)]
    work_item_id: StrictStr | None = None
    attempt_id: StrictStr | None = None
    source_artifact_id: StrictStr | None = None
    evidence_refs: Annotated[list[StrictStr], Field(max_length=64)] = []
    confidence: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
    fresh_until: Annotated[float, Field(strict=True, gt=0, lt=1e15, allow_inf_nan=False)]


class ReviewRequest(VersionedRequest):
    decision: Literal["verified", "approved", "rejected"]
    examined_refs: Annotated[list[StrictStr], Field(max_length=64)] = []


class RecoveryPage(BaseModel):
    """A restart-required recovery page, not a live feed or multinode snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    revisions: tuple[KnowledgeRevision, ...]
    checkpoint: Annotated[StrictStr, Field(min_length=64, max_length=64)]
    next_cursor: StrictStr | None
    expires_at: float
    retention_policy: Literal["immutable_history_tombstones_retain_metadata_cursor_ttl_only"]


def repository() -> WorkRepository:
    from cli_agent_orchestrator.constants import DATABASE_FILE

    return WorkRepository(DATABASE_FILE)


def authority(
    job_id: Annotated[str, Query(min_length=1, max_length=128)],
    grant_id: Annotated[str, Query(min_length=1, max_length=128)],
    grant_revision: Annotated[int, Query(ge=1, le=2**63 - 1)],
    store: Annotated[WorkRepository, Depends(repository)],
) -> MemoryService:
    # Selectors are not authority: the policy checks their live durable chain in
    # the service's own SQLite transaction for every read and mutation.
    return MemoryService(knowledge_policy=KnowledgePolicy(store, job_id, grant_id, grant_revision))


VerifiedPrincipal = Annotated[Principal, Depends(get_current_principal)]
KnowledgeAuthority = Annotated[MemoryService, Depends(authority)]
RevisionNumber = Annotated[int, Path(ge=1, le=2**63 - 1)]


def _legacy_request_target(request: Request):
    """Hash route identifiers only; never inspect body, query text or credentials."""
    route = getattr(request.scope.get("route"), "path", request.url.path)
    if "/relationships" in route:
        action = "relationships"
    elif route.endswith("/export"):
        action = "export"
    elif route.startswith("/graph/"):
        action = "graph"
    elif route.endswith("/context") or route.endswith("/memory-context"):
        action = "context"
    elif route.endswith("/store"):
        action = "store"
    elif route.endswith("/forget") or request.method == "DELETE":
        action = "forget"
    else:
        action = "recall"
    return action, {"route": route, "identifiers": dict(request.path_params)}


def legacy_memory_operator(
    request: Request,
    principal: VerifiedPrincipal,
    store: Annotated[WorkRepository, Depends(repository)],
):
    try:
        return require_legacy_operator(principal)
    except PermissionError as error:
        action, target = _legacy_request_target(request)
        try:
            audit_legacy_memory_denied(store, principal, action, target)
        except LegacyMemoryAuditError:
            raise HTTPException(
                503,
                detail=_error(
                    "legacy_memory_audit_unavailable",
                    "Verified memory audit unavailable.",
                    retryable=True,
                    required_action="retry_authority",
                ),
            ) from None
        raise HTTPException(
            403,
            detail=_error(
                "legacy_memory_local_only",
                "Shared memory requires the v1 knowledge authority.",
                required_action="use_knowledge_authority",
            ),
        ) from error


async def legacy_graph_memory_operator(
    request: Request, store: Annotated[WorkRepository, Depends(repository)]
):
    """Guard memory projections before cache lookup; leave other providers unchanged."""
    if request.path_params.get("provider") == "memory":
        principal = await get_current_principal(
            request, authorization=request.headers.get("Authorization")
        )
        legacy_memory_operator(request, principal, store)


@contextmanager
def knowledge_errors():
    try:
        yield
    except MemoryDisabledError as error:
        raise HTTPException(
            404, detail=_error("memory_disabled", "Memory system is disabled.")
        ) from error
    except PermissionError as error:
        raise HTTPException(
            403,
            detail=_error(
                "knowledge_forbidden",
                "Knowledge authority required.",
                required_action="check_authority",
            ),
        ) from error
    except KnowledgeConflict as error:
        raise HTTPException(
            409,
            detail=_error(
                "knowledge_revision_conflict",
                "Knowledge revision changed.",
                required_action="read_current_revision",
            ),
        ) from error
    except KnowledgeCursorExpired as error:
        raise HTTPException(
            410,
            detail=_error(
                "knowledge_cursor_expired",
                "Knowledge recovery cursor expired.",
                required_action="restart_authorized_snapshot",
            ),
        ) from error
    except KeyError as error:
        raise HTTPException(
            404, detail=_error("knowledge_not_found", "Knowledge record or revision not found.")
        ) from error
    except ValueError as error:
        raise HTTPException(
            422, detail=_error("knowledge_request_invalid", "Invalid knowledge request.")
        ) from error
    except (SchemaMismatch, sqlite3.Error) as error:
        raise HTTPException(
            503,
            detail=_error(
                "knowledge_store_unavailable",
                "Verified knowledge authority unavailable.",
                retryable=True,
                required_action="retry_authority",
            ),
        ) from error


@router.post("/records/{record_id}/revisions", response_model=KnowledgeRevision, status_code=201)
def propose(
    record_id: str, body: ProposalRequest, principal: VerifiedPrincipal, service: KnowledgeAuthority
):
    with knowledge_errors():
        return service.propose_revision(
            principal, record_id=record_id, **body.model_dump(exclude={"schema_version"})
        )


@router.get("/records/{record_id}", response_model=KnowledgeRevision)
def read_current(record_id: str, principal: VerifiedPrincipal, service: KnowledgeAuthority):
    with knowledge_errors():
        return service.read_revision(principal, record_id)


@router.get("/records/{record_id}/revisions/{revision}", response_model=KnowledgeRevision)
def read_history(
    record_id: str,
    revision: RevisionNumber,
    principal: VerifiedPrincipal,
    service: KnowledgeAuthority,
):
    """Read one explicit historical revision, not a checkpoint or cursor page."""
    with knowledge_errors():
        return service.read_revision(principal, record_id, revision)


@router.post("/records/{record_id}/revisions/{revision}/review", response_model=KnowledgeRevision)
def review(
    record_id: str,
    revision: RevisionNumber,
    body: ReviewRequest,
    principal: VerifiedPrincipal,
    service: KnowledgeAuthority,
):
    with knowledge_errors():
        return service.review_revision(
            principal, record_id, revision, **body.model_dump(exclude={"schema_version"})
        )


@router.post(
    "/records/{record_id}/revisions/{revision}/tombstone", response_model=KnowledgeRevision
)
def tombstone(
    record_id: str,
    revision: RevisionNumber,
    body: VersionedRequest,
    principal: VerifiedPrincipal,
    service: KnowledgeAuthority,
):
    with knowledge_errors():
        return service.tombstone_revision(
            principal, record_id, revision, expected_version=body.expected_version
        )


@router.get("/instructions", response_model=list[KnowledgeRevision])
def instructions(
    scope: KnowledgeScope, scope_id: str, principal: VerifiedPrincipal, service: KnowledgeAuthority
):
    with knowledge_errors():
        return service.instructions(principal, scope=scope, scope_id=scope_id)


@router.get("/recovery", response_model=RecoveryPage)
def recovery(
    schema_version: Annotated[int, Query(ge=1, le=1)],
    scope: KnowledgeScope,
    scope_id: Annotated[str, Query(min_length=1, max_length=128)],
    limit: Annotated[int, Query(ge=1, le=100)],
    principal: VerifiedPrincipal,
    service: KnowledgeAuthority,
    cursor: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
):
    """Recover immutable revisions with a persistent, scope-bound cursor."""
    with knowledge_errors():
        return service.recovery_page(
            principal,
            scope=scope,
            scope_id=scope_id,
            limit=limit,
            cursor=cursor,
        )
