"""Opt-in operator control of a separately funded Root deployment."""

import asyncio
from typing import Any, Callable, Coroutine

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response

from cli_agent_orchestrator.api.admission_routes import (
    FixedValidationRoute,
    failure,
    operator,
)
from cli_agent_orchestrator.models.root_deployment_control import (
    RootDeploymentActivation,
    RootDeploymentCommand,
    RootDeploymentStatus,
)
from cli_agent_orchestrator.security.auth import Principal
from cli_agent_orchestrator.services.root_deployment_runtime import (
    RootDeploymentOperatorMismatch,
    RootDeploymentRuntime,
    RootDeploymentRuntimeUnavailable,
)


class RootDeploymentValidationRoute(FixedValidationRoute):
    validation_error_code = "invalid_root_deployment_command"

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def protected(request: Request) -> Response:
            try:
                response = await handler(request)
            except HTTPException as error:
                error.headers = {**(error.headers or {}), "Cache-Control": "no-store"}
                raise
            response.headers["Cache-Control"] = "no-store"
            return response

        return protected


router = APIRouter(
    prefix="/admission/root",
    tags=["root deployment control"],
    route_class=RootDeploymentValidationRoute,
)


def configure_root_deployment(app: FastAPI, value: RootDeploymentRuntime) -> None:
    """Trusted assembly only; configuration creates no principal, key or budget.

    The operator runtime needs its own independently funded ledger and control
    lane. It must not borrow the API admission monitor's exclusive SQL permit.
    Install before serving; never call this from a request or settings payload.
    """
    if type(value) is not RootDeploymentRuntime:
        raise TypeError("root deployment requires a trusted runtime")
    if getattr(app.state, "root_deployment_runtime", None) is not None:
        raise RuntimeError("root deployment runtime is already configured")
    value.claim_installation()
    app.state.root_deployment_runtime = value


def runtime(request: Request) -> RootDeploymentRuntime:
    value = getattr(request.app.state, "root_deployment_runtime", None)
    if not isinstance(value, RootDeploymentRuntime):
        raise failure(503, "root_deployment_unavailable")
    return value


async def close_root_deployment(app: FastAPI) -> None:
    """Retain the configured owner if physical shutdown is unconfirmed."""
    value = getattr(app.state, "root_deployment_runtime", None)
    if value is None:
        return
    if not isinstance(value, RootDeploymentRuntime):
        raise RootDeploymentRuntimeUnavailable()
    operation = value.aclose()
    try:
        retirement = asyncio.create_task(operation)
    except BaseException:
        operation.close()
        raise
    interrupted = False
    while not retirement.done():
        try:
            await asyncio.shield(retirement)
        except asyncio.CancelledError:
            if retirement.cancelled():
                raise
            interrupted = True
    retirement.result()
    if getattr(app.state, "root_deployment_runtime", None) is value:
        delattr(app.state, "root_deployment_runtime")
    if interrupted:
        raise asyncio.CancelledError


@router.get("", response_model=RootDeploymentStatus)
async def status(
    request: Request,
    response: Response,
    principal: Principal = Depends(operator),
) -> RootDeploymentStatus:
    try:
        result = runtime(request).status(principal=principal)
    except RootDeploymentOperatorMismatch:
        raise failure(403, "root_deployment_operator_required") from None
    except RootDeploymentRuntimeUnavailable:
        raise failure(503, "root_deployment_unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/launch", response_model=RootDeploymentStatus)
async def launch(
    body: RootDeploymentCommand,
    request: Request,
    response: Response,
    principal: Principal = Depends(operator),
) -> RootDeploymentStatus:
    try:
        result = await runtime(request).launch(principal=principal)
    except RootDeploymentOperatorMismatch:
        raise failure(403, "root_deployment_operator_required") from None
    except RootDeploymentRuntimeUnavailable:
        raise failure(503, "root_deployment_unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/activate", response_model=RootDeploymentStatus)
async def activate(
    body: RootDeploymentActivation,
    request: Request,
    response: Response,
    principal: Principal = Depends(operator),
) -> RootDeploymentStatus:
    try:
        result = await runtime(request).activate(
            principal=principal,
            journal_id=body.journal_id,
            intent_id=body.intent_id,
            session_id=body.session_id,
        )
    except RootDeploymentOperatorMismatch:
        raise failure(403, "root_deployment_operator_required") from None
    except RootDeploymentRuntimeUnavailable:
        raise failure(503, "root_deployment_unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/refresh", response_model=RootDeploymentStatus)
async def refresh(
    body: RootDeploymentCommand,
    request: Request,
    response: Response,
    principal: Principal = Depends(operator),
) -> RootDeploymentStatus:
    try:
        result = await runtime(request).refresh(principal=principal)
    except RootDeploymentOperatorMismatch:
        raise failure(403, "root_deployment_operator_required") from None
    except RootDeploymentRuntimeUnavailable:
        raise failure(503, "root_deployment_unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/stop", response_model=RootDeploymentStatus)
async def stop(
    body: RootDeploymentCommand,
    request: Request,
    response: Response,
    principal: Principal = Depends(operator),
) -> RootDeploymentStatus:
    try:
        result = await runtime(request).stop(principal=principal)
    except RootDeploymentOperatorMismatch:
        raise failure(403, "root_deployment_operator_required") from None
    except RootDeploymentRuntimeUnavailable:
        raise failure(503, "root_deployment_unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    return result
