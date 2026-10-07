"""Authenticated Ralph metadata; actual authority remains the scoped Work runtime."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from cli_agent_orchestrator.security.auth import (
    SCOPE_READ,
    SCOPE_WRITE,
    get_current_principal,
    require_any_scope,
)

router = APIRouter(prefix="/ralph", tags=["ralph"])


def coordinator_for_request(request):
    owner = getattr(request.app.state, "work_workflow_origins", None)
    projector = getattr(request.app.state, "workflow_step_projector", None)
    if owner is None or projector is None:
        raise HTTPException(503, detail={"kind": "coordinator_runtime_unavailable"})
    service = getattr(request.app.state, "workflow_coordinator", None)
    if service is None:
        from cli_agent_orchestrator.services.work_coordinator import WorkCoordinator
        from cli_agent_orchestrator.services.workflow_continuation_driver import (
            WorkflowContinuationDriver,
        )

        driver = WorkflowContinuationDriver(owner.plans, projector)
        service = WorkCoordinator(owner.plans, driver)
        request.app.state.workflow_coordinator = service
        request.app.state.workflow_continuation_driver = driver
    return service


def refused(error):
    if isinstance(error, HTTPException):
        raise error
    code = (
        403
        if isinstance(error, PermissionError)
        else 404 if isinstance(error, LookupError) else 409
    )
    raise HTTPException(
        code,
        detail={
            "kind": (
                str(error)
                if isinstance(error, (ValueError, PermissionError, LookupError))
                else "coordinator_unavailable"
            ),
            "retryable": False,
        },
    ) from None


class TemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    agent: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    memory: str = "exact-snapshot"
    name: str | None = None


@router.post("/templates", dependencies=[Depends(require_any_scope(SCOPE_WRITE))])
async def template(
    body: TemplateRequest, request: Request, principal=Depends(get_current_principal)
):
    try:
        return await asyncio.to_thread(
            coordinator_for_request(request).publish_template, principal, **body.model_dump()
        )
    except (ValueError, PermissionError, LookupError) as error:
        refused(error)


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_name: str
    task: dict
    criteria: list[dict]
    target_mappings: dict
    binding_selections: dict
    min_iterations: int = Field(default=1, strict=True, ge=1, le=64)
    max_iterations: int = Field(default=8, strict=True, ge=1, le=64)
    deadline_seconds: int = Field(default=3600, strict=True, ge=1, le=86400)
    stall_limit: int = Field(default=3, strict=True, ge=1, le=64)
    correction_budget: int = Field(default=4, strict=True, ge=0, le=64)


@router.post("/plans:prepare", dependencies=[Depends(require_any_scope(SCOPE_WRITE))])
async def prepare(body: PrepareRequest, request: Request, principal=Depends(get_current_principal)):
    try:
        return await asyncio.to_thread(
            coordinator_for_request(request).prepare, principal, **body.model_dump()
        )
    except (ValueError, PermissionError, LookupError) as error:
        refused(error)


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prepared_id: str
    expected_plan_id: str
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")


@router.post("/runs", status_code=202, dependencies=[Depends(require_any_scope(SCOPE_WRITE))])
async def start(body: StartRequest, request: Request, principal=Depends(get_current_principal)):
    service = coordinator_for_request(request)
    try:
        value = await asyncio.to_thread(service.start, principal, **body.model_dump())
        return await service.launch_started(principal, value)
    except (ValueError, PermissionError, LookupError) as error:
        refused(error)


@router.get("/runs/{identity}", dependencies=[Depends(require_any_scope(SCOPE_READ))])
async def status(identity: str, request: Request, principal=Depends(get_current_principal)):
    try:
        return await asyncio.to_thread(coordinator_for_request(request).status, principal, identity)
    except (ValueError, PermissionError, LookupError) as error:
        refused(error)


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4096)


@router.post("/runs/{identity}/feedback", dependencies=[Depends(require_any_scope(SCOPE_WRITE))])
async def feedback(
    identity: str, body: FeedbackRequest, request: Request, principal=Depends(get_current_principal)
):
    try:
        return await asyncio.to_thread(
            coordinator_for_request(request).feedback,
            principal,
            identity,
            body.request_id,
            body.text,
        )
    except (ValueError, PermissionError, LookupError) as error:
        refused(error)


@router.post("/runs/{identity}/stop", dependencies=[Depends(require_any_scope(SCOPE_WRITE))])
async def stop(identity: str, request: Request, principal=Depends(get_current_principal)):
    try:
        return await coordinator_for_request(request).stop(principal, identity)
    except (ValueError, PermissionError, LookupError) as error:
        refused(error)


@router.post("/runs/{identity}/complete", dependencies=[Depends(require_any_scope(SCOPE_WRITE))])
async def complete(identity: str, request: Request, principal=Depends(get_current_principal)):
    try:
        return await asyncio.to_thread(
            coordinator_for_request(request).complete, principal, identity
        )
    except (ValueError, PermissionError, LookupError) as error:
        refused(error)


@router.post(
    "/runs/{identity}/resume",
    status_code=202,
    dependencies=[Depends(require_any_scope(SCOPE_WRITE))],
)
async def resume(identity: str, request: Request, principal=Depends(get_current_principal)):
    service = coordinator_for_request(request)
    try:
        return await service.resume(principal, identity)
    except (ValueError, PermissionError, LookupError) as error:
        refused(error)


class ContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    iteration: int = Field(strict=True, ge=1, le=64)
    generation: int = Field(strict=True, ge=1)


@router.post("/runs/{run_id}/context")
async def context(run_id: str, body: ContextRequest, request: Request):
    # A sealed current run capability is required, never a body principal/token.
    token = request.headers.get("X-CAO-Workflow-Run-Credential")
    service = coordinator_for_request(request)
    try:
        principal = await asyncio.to_thread(
            service.plans.origins.authenticate_run_capability, run_id, body.generation, token
        )
        return await asyncio.to_thread(service.context, principal, run_id, body.iteration)
    except (ValueError, PermissionError, LookupError) as error:
        refused(error)
