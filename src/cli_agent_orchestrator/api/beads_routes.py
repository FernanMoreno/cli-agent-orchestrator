"""Optional external task metadata with explicit identity and uncertainty receipts."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from cli_agent_orchestrator.clients.beads import BeadsError
from cli_agent_orchestrator.security.auth import (
    SCOPE_ADMIN,
    SCOPE_READ,
    SCOPE_WRITE,
    Principal,
    get_current_principal,
    is_auth_enabled,
    require_any_scope,
)
from cli_agent_orchestrator.services import beads_service as service

router = APIRouter(prefix="/beads", tags=["beads"])
read = require_any_scope(SCOPE_READ, SCOPE_WRITE, SCOPE_ADMIN)
write = require_any_scope(SCOPE_WRITE, SCOPE_ADMIN)


async def optional_principal(
    request: Request, authorization: str | None = Header(default=None)
) -> Principal | None:
    return await get_current_principal(request, authorization) if is_auth_enabled() else None


def task_progress(request, principal, workspace_id, tasks):
    """Read optional owner-scoped execution evidence alongside external metadata."""
    if (
        principal is None
        and request.client
        and request.client.host in ("127.0.0.1", "::1", "localhost")
    ):
        from cli_agent_orchestrator.security.auth import local_operator_principal

        principal = local_operator_principal()
    if principal is None or getattr(request.app.state, "work_workflow_origins", None) is None:
        return tasks
    return assignments_for_request(request).describe_tasks(principal, workspace_id, tasks)


def _error(error):
    if isinstance(error, service.BeadsConflict):
        return HTTPException(409, detail={"kind": "beads_material_conflict", "retryable": False})
    if isinstance(error, BeadsError):
        return HTTPException(503, detail={"kind": error.kind, "retryable": False})
    return HTTPException(400, detail={"kind": "beads_invalid_input", "retryable": False})


class Mutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    action: Literal[
        "create",
        "update",
        "close",
        "delete",
        "comment",
        "notes",
        "label_add",
        "label_remove",
        "dep_add",
        "dep_remove",
    ]
    task_id: str | None = Field(default=None, max_length=128)
    expected_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    values: dict = Field(default_factory=dict)


class Decomposition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=32768)


@router.get("/capabilities")
async def capabilities(_scopes=Depends(read)):
    return await asyncio.to_thread(service.capabilities)


@router.get("/workspaces/{workspace_id}/tasks")
async def list_tasks(
    workspace_id: str,
    request: Request,
    status: str | None = None,
    priority: int | None = None,
    principal=Depends(optional_principal),
    _scopes=Depends(read),
):
    try:

        def load():
            workspace, adapter = service.client_for(workspace_id)
            return {
                "workspace_id": workspace.id,
                "revision": workspace.revision,
                "tasks": task_progress(
                    request,
                    principal,
                    workspace_id,
                    [service.task_dto(task) for task in adapter.list(status, priority)],
                ),
            }

        return await asyncio.to_thread(load)
    except (BeadsError, ValueError) as error:
        raise _error(error) from None


@router.get("/workspaces/{workspace_id}/tasks/{task_id}")
async def get_task(
    workspace_id: str,
    task_id: str,
    request: Request,
    principal=Depends(optional_principal),
    _scopes=Depends(read),
):
    try:

        def load():
            _, adapter = service.client_for(workspace_id)
            task = adapter.get(task_id)
            if task is None:
                raise HTTPException(404, detail="task not found")
            return task_progress(request, principal, workspace_id, [service.task_dto(task)])[0]

        return await asyncio.to_thread(load)
    except (BeadsError, ValueError) as error:
        raise _error(error) from None


@router.get("/workspaces/{workspace_id}/ready")
async def ready(
    workspace_id: str,
    request: Request,
    epic_id: str | None = None,
    principal=Depends(optional_principal),
    _scopes=Depends(read),
):
    try:

        def load():
            _, adapter = service.client_for(workspace_id)
            return {
                "tasks": task_progress(
                    request,
                    principal,
                    workspace_id,
                    [service.task_dto(task) for task in adapter.ready(epic_id)],
                )
            }

        return await asyncio.to_thread(load)
    except (BeadsError, ValueError) as error:
        raise _error(error) from None


@router.get("/workspaces/{workspace_id}/tasks/{task_id}/comments")
async def comments(workspace_id: str, task_id: str, _scopes=Depends(read)):
    try:

        def load():
            _, adapter = service.client_for(workspace_id)
            return {"comments": adapter.get_comments(task_id)}

        return await asyncio.to_thread(load)
    except (BeadsError, ValueError) as error:
        raise _error(error) from None


@router.post("/workspaces/{workspace_id}/mutations", status_code=202)
async def mutate(
    workspace_id: str, body: Mutation, principal=Depends(optional_principal), _scopes=Depends(write)
):
    try:
        return await asyncio.to_thread(
            service.metadata_operation,
            workspace_id,
            owner=principal.id if principal else "local",
            **body.model_dump(),
        )
    except (BeadsError, ValueError) as error:
        raise _error(error) from None


@router.get("/operations/{operation_id}")
async def operation(
    operation_id: str, principal=Depends(optional_principal), _scopes=Depends(read)
):
    try:
        return await asyncio.to_thread(
            service.inspect_operation, operation_id, owner=principal.id if principal else "local"
        )
    except service.BeadsConflict:
        raise HTTPException(404, detail="operation not found") from None


@router.post("/decompose-preview")
async def preview(body: Decomposition, _scopes=Depends(read)):
    try:
        return service.decompose_preview(body.text)
    except ValueError as error:
        raise _error(error) from None


class BulkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    tasks: list[dict] = Field(min_length=1, max_length=32)
    draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    epic: dict | None = None
    sequential: bool = False


@router.post("/workspaces/{workspace_id}/bulk-create", status_code=202)
async def bulk(
    workspace_id: str,
    body: BulkCreate,
    principal=Depends(optional_principal),
    _scopes=Depends(write),
):
    try:
        return await asyncio.to_thread(
            service.bulk_create,
            workspace_id,
            owner=principal.id if principal else "local",
            **body.model_dump(),
        )
    except (BeadsError, ValueError) as error:
        raise _error(error) from None


@router.get("/workspaces/{workspace_id}/epics/{epic_id}")
async def epic(
    workspace_id: str,
    epic_id: str,
    request: Request,
    principal=Depends(optional_principal),
    _scopes=Depends(read),
):
    try:

        def load():
            value = service.epic_status(workspace_id, epic_id)
            value["children"] = task_progress(request, principal, workspace_id, value["children"])
            value["work_verified_completed_count"] = sum(
                task["work_verified_completed"] for task in value["children"]
            )
            return value

        return await asyncio.to_thread(load)
    except (BeadsError, ValueError) as error:
        raise _error(error) from None


@router.get("/workspaces/{workspace_id}/tasks/{task_id}/context")
async def context(workspace_id: str, task_id: str, _scopes=Depends(read)):
    from cli_agent_orchestrator.clients.beads import resolve_context_files

    try:

        def load():
            _, adapter = service.client_for(workspace_id)
            task = adapter.get(task_id)
            if task is None:
                raise HTTPException(404, detail="task not found")
            files = resolve_context_files(task, adapter)
            return {"files": files, "material_hash": service.task_dto(task)["material_hash"]}

        return await asyncio.to_thread(load)
    except (BeadsError, ValueError) as error:
        raise _error(error) from None


@router.post("/operations/{operation_id}/reconcile")
async def reconcile(
    operation_id: str, principal=Depends(optional_principal), _scopes=Depends(read)
):
    try:
        return await asyncio.to_thread(
            service.reconcile_operation, operation_id, owner=principal.id if principal else "local"
        )
    except (BeadsError, ValueError) as error:
        raise _error(error) from None


def assignments_for_request(request: Request):
    from cli_agent_orchestrator.api.work_coordinator_routes import coordinator_for_request
    from cli_agent_orchestrator.services.beads_assignment_service import BeadsAssignments

    return BeadsAssignments(coordinator_for_request(request))


class WorkPrepare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    workflow_name: str
    criteria: list[dict] = Field(min_length=1, max_length=32)
    binding_selections: dict
    min_iterations: int = Field(default=1, strict=True, ge=1, le=64)
    max_iterations: int = Field(default=8, strict=True, ge=1, le=64)
    deadline_seconds: int = Field(default=3600, strict=True, ge=1, le=86400)
    stall_limit: int = Field(default=3, strict=True, ge=1, le=64)
    correction_budget: int = Field(default=4, strict=True, ge=0, le=64)


@router.post("/workspaces/{workspace_id}/tasks/{task_id}/plans:prepare")
async def prepare_work(
    workspace_id: str,
    task_id: str,
    body: WorkPrepare,
    request: Request,
    principal=Depends(get_current_principal),
    _scopes=Depends(write),
):
    try:
        return await asyncio.to_thread(
            assignments_for_request(request).prepare,
            principal,
            workspace_id,
            task_id,
            **body.model_dump(),
        )
    except (BeadsError, ValueError, PermissionError, LookupError) as error:
        from cli_agent_orchestrator.api.work_coordinator_routes import refused

        if isinstance(error, BeadsError):
            raise _error(error) from None
        refused(error)


@router.get("/assignments/{binding_id}")
async def work_status(
    binding_id: str,
    request: Request,
    principal=Depends(get_current_principal),
    _scopes=Depends(read),
):
    from cli_agent_orchestrator.api.work_coordinator_routes import refused

    try:
        return await asyncio.to_thread(
            assignments_for_request(request).status, principal, binding_id
        )
    except (ValueError, PermissionError, LookupError) as error:
        refused(error)


class WorkStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_plan_id: str
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")


@router.post("/assignments/{binding_id}/start", status_code=202)
async def start_work(
    binding_id: str,
    body: WorkStart,
    request: Request,
    principal=Depends(get_current_principal),
    _scopes=Depends(write),
):
    from cli_agent_orchestrator.api.work_coordinator_routes import refused

    service = assignments_for_request(request)
    try:
        value = await asyncio.to_thread(service.start, principal, binding_id, **body.model_dump())
        prepared = value.pop("_prepared", None)
        if prepared is not None:
            await service.coordinator.launch_started(
                principal,
                {
                    "prepared": prepared,
                    "run_id": value["run_id"],
                    "coordinator_id": value["coordinator_id"],
                    "state": value["state"],
                },
            )
        return value
    except (BeadsError, ValueError, PermissionError, LookupError) as error:
        if isinstance(error, BeadsError):
            raise _error(error) from None
        refused(error)


@router.post("/assignments/{binding_id}/unassign")
async def unassign_work(
    binding_id: str,
    request: Request,
    principal=Depends(get_current_principal),
    _scopes=Depends(write),
):
    from cli_agent_orchestrator.api.work_coordinator_routes import refused

    try:
        return await assignments_for_request(request).unassign(principal, binding_id)
    except (ValueError, PermissionError, LookupError) as error:
        refused(error)


class VerifiedClose(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.post("/assignments/{binding_id}/close-task", status_code=202)
async def close_verified_task(
    binding_id: str,
    body: VerifiedClose,
    request: Request,
    principal=Depends(get_current_principal),
    _scopes=Depends(write),
):
    from cli_agent_orchestrator.api.work_coordinator_routes import refused

    assignments = assignments_for_request(request)
    try:

        def close():
            status = assignments.status(principal, binding_id)
            if not status["work_verified_completed"]:
                raise ValueError("beads_verified_completion_required")
            assignments.coordinator.complete(principal, status["coordinator_id"])
            import json

            with assignments.repository.read_snapshot() as connection:
                binding = dict(assignments._row(connection, principal, binding_id))
            return service.metadata_operation(
                status["workspace_id"],
                owner=principal.id,
                operation_key=body.operation_key,
                action="close",
                task_id=status["task_id"],
                expected_hash=body.expected_hash,
                values={},
                _expected_workspace_identity=binding["workspace_identity"],
                _expected_task_content=json.loads(binding["material_json"])["task"],
            )

        return await asyncio.to_thread(close)
    except (BeadsError, ValueError, PermissionError, LookupError) as error:
        if isinstance(error, BeadsError):
            raise _error(error) from None
        refused(error)
