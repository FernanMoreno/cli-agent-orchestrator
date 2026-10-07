"""Authenticated durable-work queries and decision evidence mutations.

Decision records remain owned by :class:`WorkDecisions`.  This transport layer
accepts only coordinates and evidence; it never accepts an actor, grant, or
contract override from a client.
"""

import sqlite3
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from cli_agent_orchestrator.clients.work_repository import (
    SchemaMismatch,
    WorkConflict,
    WorkRepository,
)
from cli_agent_orchestrator.models.work import EventPage, WorkView
from cli_agent_orchestrator.models.work_operations import WorkOperations
from cli_agent_orchestrator.security.auth import (
    SCOPE_ADMIN,
    SCOPE_READ,
    SCOPE_WRITE,
    Principal,
    get_current_principal,
    require_any_scope,
)
from cli_agent_orchestrator.services.work_authority import AuthorityDenied
from cli_agent_orchestrator.services.work_contract import ContractConflict
from cli_agent_orchestrator.services.work_decisions import (
    DecisionConflict,
    DecisionRevoked,
    WorkDecisions,
)
from cli_agent_orchestrator.services.work_mcp_proxy import (
    WorkMcpProxy,
    WorkMcpProxyRejected,
    WorkMcpProxyUnavailable,
)
from cli_agent_orchestrator.services.work_projection import WorkQueries

router = APIRouter(tags=["durable-work"])


def query_error(status: int, code: str, message: str, required_action: str) -> HTTPException:
    return HTTPException(
        status,
        detail={
            "code": code,
            "message": message,
            "retryable": status >= 500,
            "required_action": required_action,
        },
    )


def queries() -> WorkQueries:
    from cli_agent_orchestrator.constants import DATABASE_FILE

    return WorkQueries(WorkRepository(DATABASE_FILE))


def repository() -> WorkRepository:
    """Construct the verified work store once per dependency resolution."""
    from cli_agent_orchestrator.constants import DATABASE_FILE

    return WorkRepository(DATABASE_FILE)


def decisions(store: Annotated[WorkRepository, Depends(repository)]) -> WorkDecisions:
    """Expose the one existing decision ledger; no route-local ledger exists."""
    return WorkDecisions(store)


def proxy_effects_service(
    store: Annotated[WorkRepository, Depends(repository)],
) -> WorkMcpProxy:
    """Expose the durable effect journal without creating route-local state."""
    return WorkMcpProxy(store)


class DecisionRequest(BaseModel):
    """Closed request shape; authority comes solely from the transport principal."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: Annotated[StrictStr, Field(min_length=1, max_length=512)]
    generation: Annotated[StrictInt, Field(gt=0, le=2**63 - 1)]
    idempotency_key: Annotated[StrictStr, Field(min_length=1, max_length=512)]
    evidence_refs: Annotated[list[StrictStr], Field(min_length=1, max_length=128)]
    action: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    reason: Annotated[StrictStr, Field(min_length=1, max_length=4096)]
    authorized_effects: Annotated[list[StrictStr], Field(min_length=1, max_length=128)]


class DecisionRevocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Annotated[StrictStr, Field(min_length=1, max_length=4096)]


class ProxyEffectReconciliationRequest(BaseModel):
    """A bounded operator note; the journal retains only its digest."""

    model_config = ConfigDict(extra="forbid")

    resolution: Annotated[StrictStr, Field(min_length=1, max_length=4096)]


def _decision_payload(decision) -> dict:
    """Serialize the retained ledger record without inventing a transport DTO."""
    return {
        "id": decision.id,
        "job_id": decision.job_id,
        "work_item_id": decision.work_item_id,
        "attempt_id": decision.attempt_id,
        "generation": decision.generation,
        "contract_hash": decision.contract_hash,
        "idempotency_key": decision.idempotency_key,
        "actor_id": decision.actor_id,
        "created_at": decision.created_at,
        "action": decision.action,
        "reason": decision.reason,
        "evidence_refs": list(decision.evidence_refs),
        "authorized_effects": list(decision.authorized_effects),
        "consumed_at": decision.consumed_at,
        "revoked_at": decision.revoked_at,
    }


def _revocation_payload(revocation) -> dict:
    return {
        "decision_id": revocation.decision_id,
        "actor_id": revocation.actor_id,
        "revoked_at": revocation.revoked_at,
        "reason": revocation.reason,
    }


def decision_error(status: int, code: str, message: str, required_action: str) -> HTTPException:
    return HTTPException(
        status,
        detail={
            "code": code,
            "message": message,
            "retryable": status >= 500,
            "required_action": required_action,
        },
    )


@contextmanager
def decision_errors():
    """Map write failures without leaking implementation or validation details."""
    try:
        yield
    except AuthorityDenied as error:
        raise decision_error(
            403,
            "work_decision_forbidden",
            "Current principal is not authorized for this durable decision.",
            "check_authority",
        ) from error
    except DecisionRevoked as error:
        raise decision_error(
            409,
            "work_decision_revoked",
            "Decision was revoked before its effect could be claimed.",
            "record_new_authorized_decision",
        ) from error
    except (DecisionConflict, ContractConflict, WorkConflict) as error:
        raise decision_error(
            409,
            "work_decision_conflict",
            "Decision evidence conflicts with durable state.",
            "read_current_decision",
        ) from error
    except KeyError as error:
        raise decision_error(
            404,
            "work_decision_not_found",
            "Durable work decision was not found.",
            "check_resource",
        ) from error
    except ValueError as error:
        raise decision_error(
            422,
            "work_decision_invalid",
            "Invalid durable decision request.",
            "correct_request",
        ) from error
    except (SchemaMismatch, sqlite3.Error) as error:
        raise decision_error(
            503,
            "work_store_unavailable",
            "Verified work store unavailable.",
            "retry_decision",
        ) from error


@contextmanager
def work_errors():
    try:
        yield
    except KeyError as error:
        raise query_error(
            404, "work_not_found", "Work resource not found.", "check_resource"
        ) from error
    except PermissionError as error:
        raise query_error(
            403, "work_read_forbidden", "Read scope required.", "authenticate"
        ) from error
    except WorkConflict as error:
        raise query_error(
            409, "work_revision_conflict", "Cursor exceeds job history.", "refresh_cursor"
        ) from error
    except (SchemaMismatch, sqlite3.Error) as error:
        raise query_error(
            503, "work_store_unavailable", "Verified work store unavailable.", "retry_query"
        ) from error


@contextmanager
def proxy_effect_errors():
    """Map journal failures without exposing request content or database details."""
    try:
        yield
    except WorkMcpProxyRejected as error:
        raise query_error(
            409,
            "proxy_effect_conflict",
            "Effect is not available for the requested reconciliation.",
            "inspect_current_effect_state",
        ) from error
    except WorkMcpProxyUnavailable as error:
        raise query_error(
            503,
            "proxy_effect_store_unavailable",
            "Durable effect journal unavailable.",
            "retry_query",
        ) from error
    except (SchemaMismatch, sqlite3.Error) as error:
        raise query_error(
            503,
            "work_store_unavailable",
            "Verified work store unavailable.",
            "retry_query",
        ) from error


@router.get("/work-items/{work_item_id}", response_model=WorkView)
def get_work(work_item_id: str, principal: Annotated[Principal, Depends(get_current_principal)]):
    with work_errors():
        return queries().work(principal, work_item_id)


@router.get("/work-proxy/effects")
def list_unresolved_proxy_effects(
    _scopes: Annotated[list[str], Depends(require_any_scope(SCOPE_ADMIN))],
    service: Annotated[WorkMcpProxy, Depends(proxy_effects_service)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
):
    """List unresolved effects with request and response contents redacted."""
    with proxy_effect_errors():
        return {"effects": service.unresolved_effects(limit=limit)}


@router.post("/work-proxy/effects/{effect_id}/reconcile")
def reconcile_proxy_effect(
    effect_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")],
    body: ProxyEffectReconciliationRequest,
    _scopes: Annotated[list[str], Depends(require_any_scope(SCOPE_ADMIN))],
    service: Annotated[WorkMcpProxy, Depends(proxy_effects_service)],
):
    """Close one uncertain effect only after an explicit administrator decision."""
    with proxy_effect_errors():
        service.reconcile_effect(effect_id, body.resolution)
    return {"effect_id": effect_id, "state": "reconciled"}


@router.get("/work-proxy/issues")
def list_unresolved_proxy_issues(
    _scopes: Annotated[list[str], Depends(require_any_scope(SCOPE_ADMIN))],
    service: Annotated[WorkMcpProxy, Depends(proxy_effects_service)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
):
    """List consumed endpoints whose process ended without a request journal."""
    with proxy_effect_errors():
        return {"issues": service.unresolved_issues(limit=limit)}


@router.post("/work-proxy/issues/{attempt_id}/{generation}/reconcile")
def reconcile_proxy_issue(
    attempt_id: Annotated[str, Path(min_length=1, max_length=256)],
    generation: Annotated[int, Path(gt=0, le=2**63 - 1)],
    body: ProxyEffectReconciliationRequest,
    _scopes: Annotated[list[str], Depends(require_any_scope(SCOPE_ADMIN))],
    service: Annotated[WorkMcpProxy, Depends(proxy_effects_service)],
):
    """Close one abandoned endpoint after an explicit administrator decision."""
    with proxy_effect_errors():
        service.reconcile_issue(attempt_id, generation, body.resolution)
    return {"attempt_id": attempt_id, "generation": generation, "state": "reconciled"}


@router.get("/jobs/{job_id}/events", response_model=EventPage)
def get_work_events(
    job_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
):
    with work_errors():
        return queries().events(principal, job_id, after_sequence=after_sequence, limit=limit)


@router.post("/work-items/{work_item_id}/decisions")
def record_decision(
    work_item_id: Annotated[str, Path(min_length=1, max_length=512)],
    body: DecisionRequest,
    _scopes: Annotated[list[str], Depends(require_any_scope(SCOPE_WRITE))],
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkDecisions, Depends(decisions)],
):
    """Record immutable authorized decision evidence; this never executes an effect."""
    with decision_errors():
        decision = service.decide_work(
            principal=principal,
            work_item_id=work_item_id,
            **body.model_dump(),
        )
        return _decision_payload(decision)


@router.post("/work-decisions/{decision_id}/revoke")
def revoke_decision(
    decision_id: Annotated[str, Path(min_length=1, max_length=512)],
    body: DecisionRevocationRequest,
    _scopes: Annotated[list[str], Depends(require_any_scope(SCOPE_WRITE))],
    principal: Annotated[Principal, Depends(get_current_principal)],
    service: Annotated[WorkDecisions, Depends(decisions)],
):
    """Record an original actor's revocation, even after its grant later expires."""
    with decision_errors():
        return _revocation_payload(
            service.revoke(principal=principal, decision_id=decision_id, reason=body.reason)
        )


@router.get("/work-items/{work_item_id}/operations", response_model=WorkOperations)
def get_work_operations(
    work_item_id: str,
    principal: Annotated[Principal, Depends(get_current_principal)],
    _scopes: Annotated[list[str], Depends(require_any_scope(SCOPE_READ, SCOPE_WRITE, SCOPE_ADMIN))],
):
    """Observe retained capacity; action links retain their existing live fences."""
    with work_errors():
        return queries().operational_status(principal, work_item_id)
