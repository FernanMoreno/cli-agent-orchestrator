"""Authenticated resource registration; shared control contract, not live-capacity claims."""

from dataclasses import asdict
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from cli_agent_orchestrator.clients.admission_bootstrap_repository import (
    EnvelopeNotFound,
    EnvelopeRevisionConflict,
)
from cli_agent_orchestrator.clients.admission_repository import (
    SharedAdmissionDenied,
    SharedCapacityUnavailable,
    SharedCommitUncertain,
    SharedRevisionConflict,
)
from cli_agent_orchestrator.clients.postgres.config import ConfigurationError
from cli_agent_orchestrator.clients.postgres.connection import DatabaseError
from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch
from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.models.admission_control import (
    AdmissionDiagnostics,
    RegisteredResourceEnvelope,
    ResourceAttestationRequest,
    ResourceClaims,
    ResourceRevisionRequest,
    ResourceUpdate,
    SharedAdmissionActivationRequest,
    SharedAdmissionActivationResult,
    SharedDebtControlRequest,
    SharedDebtControlResult,
    SharedPreparationAbortRequest,
    SharedPreparationAbortResult,
)
from cli_agent_orchestrator.security.auth import (
    SCOPE_ADMIN,
    Principal,
    get_current_principal,
    is_verified_principal,
)
from cli_agent_orchestrator.services.admission_monitor import AuthorityChangeUnavailable
from cli_agent_orchestrator.services.inbox_admission_runtime import (
    AdmissionRuntimeUnavailable,
    InboxAdmissionRuntime,
)
from cli_agent_orchestrator.services.shared_delivery_admission import SharedDeliveryUnavailable


def failure(status: int, code: str) -> HTTPException:
    return HTTPException(status, detail={"code": code}, headers={"Cache-Control": "no-store"})


class FixedValidationRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def validated(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise failure(422, "invalid_resource_envelope") from None

        return validated


router = APIRouter(prefix="/admission", tags=["resource control"], route_class=FixedValidationRoute)


async def operator(principal: Principal = Depends(get_current_principal)) -> Principal:
    if (
        not is_verified_principal(principal)
        or principal.kind != "jwt"
        or SCOPE_ADMIN not in principal.scopes
    ):
        raise failure(403, "resource_admin_required")
    return principal


def runtime(request: Request) -> InboxAdmissionRuntime:
    value = getattr(request.app.state, "inbox_admission_runtime", None)
    if not isinstance(value, InboxAdmissionRuntime) or not value.available:
        raise failure(503, "resource_control_unavailable")
    return value


def control_error(error: Exception) -> HTTPException:
    if isinstance(error, EnvelopeRevisionConflict):
        return failure(409, "resource_revision_conflict")
    if isinstance(error, EnvelopeNotFound):
        return failure(404, "resource_envelope_not_found")
    # Authentication and DTO validation already ran. Errors here describe
    # server binding/configuration/storage, never a malformed caller field.
    return failure(503, "resource_control_unavailable")


_EXPECTED = (
    SharedAdmissionDenied,
    SharedCapacityUnavailable,
    SharedCommitUncertain,
    SharedRevisionConflict,
    SharedDeliveryUnavailable,
    StorageAdmissionUnavailable,
    EnvelopeRevisionConflict,
    EnvelopeNotFound,
    PermissionError,
    ValueError,
    AdmissionRuntimeUnavailable,
    AuthorityChangeUnavailable,
    DatabaseError,
    ConfigurationError,
    SchemaMismatch,
    OSError,
)


@router.post("/shared/activate", response_model=SharedAdmissionActivationResult)
async def activate_shared(
    body: SharedAdmissionActivationRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(operator),
):
    value = runtime(request)
    try:
        await value.activate_shared(principal=principal)
    except _EXPECTED as error:
        raise control_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return SharedAdmissionActivationResult()


@router.post("/shared/debt", response_model=SharedDebtControlResult)
async def shared_debt(
    body: SharedDebtControlRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(operator),
):
    value = runtime(request)
    try:
        page = await value.outstanding_resources(
            principal=principal,
            limit=body.limit,
            after=body.after.to_cursor() if body.after is not None else None,
        )
    except _EXPECTED as error:
        raise control_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return SharedDebtControlResult(entries=page.entries, next_cursor=page.next_cursor)


@router.post("/shared/abort-preparing", response_model=SharedPreparationAbortResult)
async def abort_preparing(
    body: SharedPreparationAbortRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(operator),
):
    value = runtime(request)
    try:
        receipt = await value.abort_preparing(
            principal=principal,
            action_id=body.action_id,
            operation_id=body.operation_id,
            expected_revision=body.expected_revision,
        )
    except SharedRevisionConflict:
        raise failure(409, "resource_operation_conflict") from None
    except _EXPECTED as error:
        raise control_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return SharedPreparationAbortResult(**asdict(receipt))


@router.get("/resource-envelope", response_model=RegisteredResourceEnvelope)
async def registered(
    request: Request, response: Response, principal: Principal = Depends(operator)
):
    value = runtime(request)
    try:
        record = await value.registered(principal=principal)
    except _EXPECTED as error:
        raise control_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return RegisteredResourceEnvelope(
        revision=record.revision,
        active=record.active,
        claims=ResourceClaims(**asdict(record.claims)),
    )


@router.post("/resource-envelope", response_model=ResourceUpdate)
async def attest(
    body: ResourceAttestationRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(operator),
):
    value = runtime(request)
    try:
        update = await value.attest(
            principal=principal,
            claims=body.claims.to_claims(),
            expected_revision=body.expected_revision,
        )
    except _EXPECTED as error:
        raise control_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return ResourceUpdate(revision=update.revision, active=update.active)


@router.post("/resource-envelope/revoke", response_model=ResourceUpdate)
async def revoke(
    body: ResourceRevisionRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(operator),
):
    value = runtime(request)
    try:
        update = await value.revoke(principal=principal, expected_revision=body.expected_revision)
    except _EXPECTED as error:
        raise control_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return ResourceUpdate(revision=update.revision, active=update.active)


@router.get("/diagnostics", response_model=AdmissionDiagnostics)
async def diagnostics(
    request: Request, response: Response, principal: Principal = Depends(operator)
):
    value = runtime(request)
    try:
        result = value.diagnostics(principal=principal)
    except _EXPECTED as error:
        raise control_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return result
