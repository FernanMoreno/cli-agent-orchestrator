"""Authenticated resource registration; shared control contract, not live-capacity claims."""

from dataclasses import asdict
from typing import Any, Callable, ClassVar, Coroutine

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from cli_agent_orchestrator.clients.admission_bootstrap_repository import (
    EnvelopeNotFound,
    EnvelopeRevisionConflict,
)
from cli_agent_orchestrator.clients.admission_policy_repository import (
    PolicyNotFound,
    PolicyRevisionConflict,
    PolicyWriteNotStarted,
)
from cli_agent_orchestrator.clients.admission_repository import (
    SharedAdmissionDenied,
    SharedCapacityUnavailable,
    SharedClosureUnproven,
    SharedCommitUncertain,
    SharedRevisionConflict,
)
from cli_agent_orchestrator.clients.postgres.config import ConfigurationError
from cli_agent_orchestrator.clients.postgres.connection import DatabaseError
from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch
from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.models.admission_control import (
    AdmissionDiagnostics,
    AdmissionDiagnosticsQueryError,
    AdmissionDiagnosticsQueryErrorResponse,
    AdmissionDiagnosticsV2,
    AdmissionDiagnosticsVersionRequired,
    AdmissionDiagnosticsVersionRequiredResponse,
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
    SharedWitnessReleaseRequest,
    SharedWitnessReleaseResult,
)
from cli_agent_orchestrator.models.admission_policy_control import (
    AdmissionPolicyHistory,
    AdmissionPolicyRecord,
    AdmissionPolicyRevision,
    AdmissionPolicyState,
    AdmissionPolicyUpdate,
)
from cli_agent_orchestrator.security.auth import (
    SCOPE_ADMIN,
    Principal,
    get_current_principal,
    is_verified_principal,
)
from cli_agent_orchestrator.services.admission_diagnostics import DiagnosticsVersionRequired
from cli_agent_orchestrator.services.admission_monitor import AuthorityChangeUnavailable
from cli_agent_orchestrator.services.inbox_admission_runtime import (
    AdmissionRuntimeUnavailable,
    InboxAdmissionRuntime,
)
from cli_agent_orchestrator.services.shared_delivery_admission import SharedDeliveryUnavailable


def failure(status: int, code: str) -> HTTPException:
    return HTTPException(status, detail={"code": code}, headers={"Cache-Control": "no-store"})


class FixedValidationRoute(APIRoute):
    validation_error_code: ClassVar[str] = "invalid_resource_envelope"

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def validated(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                raise failure(422, self.validation_error_code) from None

        return validated


class DiagnosticsValidationRoute(FixedValidationRoute):
    validation_error_code: ClassVar[str] = AdmissionDiagnosticsQueryError().code


router = APIRouter(prefix="/admission", tags=["resource control"], route_class=FixedValidationRoute)
_diagnostics_router = APIRouter(route_class=DiagnosticsValidationRoute)


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
    SharedClosureUnproven,
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
) -> SharedAdmissionActivationResult:
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
) -> SharedDebtControlResult:
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
) -> SharedPreparationAbortResult:
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


@router.post("/shared/release-witnessed", response_model=SharedWitnessReleaseResult)
async def release_witnessed(
    body: SharedWitnessReleaseRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(operator),
) -> SharedWitnessReleaseResult:
    value = runtime(request)
    try:
        receipt = await value.release_witnessed(
            principal=principal,
            action_id=body.action_id,
            operation_id=body.operation_id,
            expected_revision=body.expected_revision,
        )
    except SharedClosureUnproven:
        raise failure(409, "resource_closure_unproven") from None
    except SharedRevisionConflict:
        raise failure(409, "resource_operation_conflict") from None
    except _EXPECTED as error:
        raise control_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return SharedWitnessReleaseResult(**asdict(receipt))


@router.get("/resource-envelope", response_model=RegisteredResourceEnvelope)
async def registered(
    request: Request, response: Response, principal: Principal = Depends(operator)
) -> RegisteredResourceEnvelope:
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
) -> ResourceUpdate:
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
) -> ResourceUpdate:
    value = runtime(request)
    try:
        update = await value.revoke(principal=principal, expected_revision=body.expected_revision)
    except _EXPECTED as error:
        raise control_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return ResourceUpdate(revision=update.revision, active=update.active)


@_diagnostics_router.get(
    "/diagnostics",
    response_model=AdmissionDiagnostics | AdmissionDiagnosticsV2,
    responses={
        409: {"model": AdmissionDiagnosticsVersionRequiredResponse},
        422: {"model": AdmissionDiagnosticsQueryErrorResponse},
    },
)
async def diagnostics(
    request: Request,
    response: Response,
    version: int = Query(default=1, ge=1, le=2),
    principal: Principal = Depends(operator),
) -> AdmissionDiagnostics | AdmissionDiagnosticsV2:
    value = runtime(request)
    result: AdmissionDiagnostics | AdmissionDiagnosticsV2
    try:
        if version == 1:
            result = value.diagnostics(principal=principal)
        else:
            result = value.diagnostics(principal=principal, version=version)
    except DiagnosticsVersionRequired:
        detail = AdmissionDiagnosticsVersionRequired().model_dump(mode="json")
        raise HTTPException(
            status_code=409,
            detail=detail,
            headers={"Cache-Control": "no-store"},
        ) from None
    except _EXPECTED as error:
        raise control_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return result


router.include_router(_diagnostics_router)

_POLICY_EXPECTED = (*_EXPECTED, PolicyRevisionConflict, PolicyNotFound, PolicyWriteNotStarted)


class PolicyValidationRoute(FixedValidationRoute):
    validation_error_code: ClassVar[str] = "invalid_admission_policy"


_policy_router = APIRouter(route_class=PolicyValidationRoute)


def policy_error(error: Exception) -> HTTPException:
    if isinstance(error, PolicyRevisionConflict):
        return failure(409, "policy_revision_conflict")
    if isinstance(error, PolicyNotFound):
        return failure(404, "policy_not_found")
    return failure(503, "policy_control_unavailable")


@_policy_router.get("/policy", response_model=AdmissionPolicyState)
async def get_policy(
    request: Request,
    response: Response,
    principal: Principal = Depends(operator),
) -> AdmissionPolicyState:
    try:
        result = await runtime(request).policy_status(principal=principal)
    except _POLICY_EXPECTED as error:
        raise policy_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return result


@_policy_router.put("/policy", response_model=AdmissionPolicyRecord)
async def update_policy(
    body: AdmissionPolicyUpdate,
    request: Request,
    response: Response,
    principal: Principal = Depends(operator),
) -> AdmissionPolicyRecord:
    try:
        result = await runtime(request).policy_update(principal=principal, body=body)
    except _POLICY_EXPECTED as error:
        raise policy_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return result


@_policy_router.post("/policy/revoke", response_model=AdmissionPolicyRecord)
async def revoke_policy(
    body: AdmissionPolicyRevision,
    request: Request,
    response: Response,
    principal: Principal = Depends(operator),
) -> AdmissionPolicyRecord:
    try:
        result = await runtime(request).policy_revoke(principal=principal, body=body)
    except _POLICY_EXPECTED as error:
        raise policy_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return result


@_policy_router.post("/policy/seed", response_model=AdmissionPolicyRecord)
async def seed_policy(
    body: AdmissionPolicyRevision,
    request: Request,
    response: Response,
    principal: Principal = Depends(operator),
) -> AdmissionPolicyRecord:
    try:
        result = await runtime(request).policy_seed(principal=principal, body=body)
    except _POLICY_EXPECTED as error:
        raise policy_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return result


@_policy_router.get("/policy/history", response_model=AdmissionPolicyHistory)
async def policy_history(
    request: Request,
    response: Response,
    limit: int = Query(default=50, ge=1, le=100),
    before_revision: int | None = Query(default=None, ge=1, le=2**63 - 1),
    principal: Principal = Depends(operator),
) -> AdmissionPolicyHistory:
    try:
        result = await runtime(request).policy_history(
            principal=principal, limit=limit, before_revision=before_revision
        )
    except _POLICY_EXPECTED as error:
        raise policy_error(error) from None
    response.headers["Cache-Control"] = "no-store"
    return result


router.include_router(_policy_router)
