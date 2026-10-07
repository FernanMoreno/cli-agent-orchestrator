"""Loopback-only HTTP contract for independently running local CAO peers."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from cli_agent_orchestrator.security.auth import (
    SCOPE_ADMIN,
    SCOPE_READ,
    SCOPE_WRITE,
    Principal,
    get_current_principal,
    is_verified_principal,
    require_any_scope,
)
from cli_agent_orchestrator.services import local_peer_auth, local_peer_service
from cli_agent_orchestrator.services.local_peer_registry import (
    ProjectWriteLeaseBusy,
    get_instance,
)

router = APIRouter(prefix="/local-coordination", tags=["local CAO coordination"])
logger = logging.getLogger(__name__)


class PairingInvitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge_id: str = Field(min_length=36, max_length=36)
    code: str = Field(min_length=32, max_length=256)
    expires_at: float
    initiator_instance_id: str = Field(min_length=36, max_length=36)
    initiator_process_generation: str = Field(min_length=36, max_length=36)
    initiator_display_name: str = Field(min_length=1, max_length=80)
    initiator_public_key: str = Field(min_length=40, max_length=64)
    initiator_loopback_port: int = Field(ge=1, le=65535)
    candidate_instance_id: str = Field(min_length=36, max_length=36)
    candidate_process_generation: str = Field(min_length=36, max_length=36)
    candidate_display_name: str = Field(min_length=1, max_length=80)
    candidate_public_key: str = Field(min_length=40, max_length=64)
    project_id: str = Field(min_length=64, max_length=64)
    canonical_root: str = Field(min_length=1, max_length=4096)
    git_common_dir: str | None = Field(default=None, max_length=4096)
    requested_scopes: list[str] = Field(min_length=1, max_length=8)


class PairingAcceptance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=32, max_length=256)
    candidate_instance_id: str = Field(min_length=36, max_length=36)
    candidate_process_generation: str = Field(min_length=36, max_length=36)
    candidate_display_name: str = Field(min_length=1, max_length=80)
    candidate_public_key: str = Field(min_length=40, max_length=64)
    project_id: str = Field(min_length=64, max_length=64)


class LocalTaskSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=36, max_length=36)
    source_instance_id: str = Field(min_length=36, max_length=36)
    source_process_generation: str = Field(min_length=36, max_length=36)
    requester_terminal_id: str = Field(pattern=r"^[a-f0-9]{8}$")
    project_id: str = Field(min_length=64, max_length=64)
    operation_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    request_hash: str = Field(min_length=64, max_length=64)
    agent_profile: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=32768)
    relative_working_directory: str = Field(default=".", min_length=1, max_length=4096)
    use_worktree: bool


def _is_loopback(value: str | None) -> bool:
    if not value:
        return False
    if value.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(address.is_loopback)


def _require_loopback(request: Request) -> None:
    client_host = request.client.host if request.client is not None else None
    host_header = request.headers.get("host", "")
    parsed_host = urlsplit(f"//{host_header}").hostname
    if not _is_loopback(client_host) or not _is_loopback(parsed_host):
        raise HTTPException(status_code=403, detail={"kind": "not_local"})


async def _active_peer_headers(
    request: Request,
    *,
    project_id: str,
    required_scope: str,
    task_id: str | None = None,
    peer_path_id: str | None = None,
) -> str:
    body = await request.body()
    _require_loopback(request)
    peer_instance_id = request.headers.get("X-CAO-Peer-Instance", "")
    process_generation = request.headers.get("X-CAO-Peer-Generation", "")
    signature = request.headers.get("X-CAO-Peer-Signature", "")
    if peer_path_id is not None and peer_path_id != peer_instance_id:
        raise HTTPException(status_code=403, detail={"kind": "scope_denied"})
    peer = get_instance(peer_instance_id)
    if peer is None or peer.process_generation != process_generation:
        raise HTTPException(status_code=403, detail={"kind": "not_local"})
    try:
        local_peer_auth.authenticate_peer(
            peer_instance_id=peer_instance_id,
            peer_process_generation=process_generation,
            project_id=project_id,
            signature=signature,
            method=request.method,
            path=request.url.path,
            query=request.url.query,
            timestamp=request.headers.get("X-CAO-Peer-Timestamp", ""),
            nonce=request.headers.get("X-CAO-Peer-Nonce", ""),
            body_sha256=request.headers.get("X-CAO-Peer-Body-SHA256", ""),
            body=body,
            required_scope=required_scope,
            task_id=task_id,
            requester_terminal_id=request.query_params.get("requester_terminal_id"),
        )
    except local_peer_auth.LocalPeerAuthError as error:
        raise HTTPException(status_code=403, detail={"kind": "scope_denied"}) from error
    return peer_instance_id


@router.get("/identity")
async def local_peer_identity(request: Request) -> dict[str, object]:
    _require_loopback(request)
    try:
        return local_peer_service.local_identity()
    except local_peer_service.LocalPeerUnavailable as error:
        raise HTTPException(status_code=503, detail={"kind": "not_local"}) from error


@router.get("/instances")
async def list_local_peer_instances(request: Request) -> list[dict[str, object]]:
    _require_loopback(request)
    return local_peer_service.verified_peer_candidates()


@router.post("/pairings", status_code=status.HTTP_201_CREATED)
async def receive_pairing_invitation(
    request: Request, body: PairingInvitation
) -> dict[str, object]:
    _require_loopback(request)
    try:
        return local_peer_service.receive_pairing_invitation(body.model_dump())
    except local_peer_auth.PairingExpiredError as error:
        raise HTTPException(status_code=410, detail={"kind": "pairing_expired"}) from error
    except (local_peer_auth.PairingMismatchError, local_peer_service.LocalPeerConflict) as error:
        logger.info("Local CAO pairing invitation rejected: %s", error)
        raise HTTPException(status_code=409, detail={"kind": "pairing_mismatch"}) from error
    except local_peer_service.LocalPeerUnavailable as error:
        logger.info("Local CAO pairing invitation unavailable: %s", error)
        raise HTTPException(status_code=409, detail={"kind": "not_local"}) from error
    except local_peer_service.LocalPeerError as error:
        logger.info("Local CAO pairing project mismatch: %s", error)
        raise HTTPException(status_code=400, detail={"kind": "project_mismatch"}) from error


@router.post("/pairings/{challenge_id}/accept")
async def accept_pairing_invitation(
    challenge_id: str, request: Request, body: PairingAcceptance
) -> dict[str, object]:
    _require_loopback(request)
    candidate = get_instance(body.candidate_instance_id)
    if candidate is None or candidate.process_generation != body.candidate_process_generation:
        raise HTTPException(status_code=409, detail={"kind": "pairing_mismatch"})
    try:
        live_candidate = local_peer_service.verify_live_process(
            body.candidate_instance_id, body.candidate_process_generation
        )
        if (
            live_candidate.get("display_name") != body.candidate_display_name
            or live_candidate.get("public_key") != body.candidate_public_key
        ):
            raise local_peer_auth.PairingMismatchError("candidate display identity changed")
        local_peer_auth.accept_at_initiator(
            challenge_id=challenge_id,
            code=body.code,
            candidate_instance_id=body.candidate_instance_id,
            candidate_process_generation=body.candidate_process_generation,
            candidate_display_name=body.candidate_display_name,
            candidate_public_key=body.candidate_public_key,
            project_id=body.project_id,
        )
    except local_peer_auth.PairingExpiredError as error:
        raise HTTPException(status_code=410, detail={"kind": "pairing_expired"}) from error
    except local_peer_auth.LocalPeerAuthError as error:
        raise HTTPException(status_code=409, detail={"kind": "pairing_mismatch"}) from error
    except local_peer_service.LocalPeerUnavailable as error:
        raise HTTPException(status_code=409, detail={"kind": "not_local"}) from error
    return {"accepted": True, "project_id": body.project_id}


@router.get("/projects/verify")
async def verify_local_project(
    request: Request,
    project_path: str = Query(min_length=1, max_length=4096),
    expected_project_id: str = Query(min_length=64, max_length=64),
) -> dict[str, object]:
    _require_loopback(request)
    try:
        binding = local_peer_service.project_binding(project_path)
    except (OSError, local_peer_service.LocalPeerError) as error:
        raise HTTPException(status_code=400, detail={"kind": "project_mismatch"}) from error
    return {
        **binding,
        "matches": binding["project_id"] == expected_project_id,
    }


@router.post("/tasks", status_code=status.HTTP_202_ACCEPTED)
async def submit_local_peer_task(request: Request, body: LocalTaskSubmission) -> dict[str, Any]:
    peer_instance_id = await _active_peer_headers(
        request, project_id=body.project_id, required_scope="task:submit"
    )
    if peer_instance_id != body.source_instance_id:
        raise HTTPException(status_code=403, detail={"kind": "scope_denied"})
    payload = body.model_dump()
    try:
        return await local_peer_service.accept_incoming_task(
            payload, registry=request.app.state.plugin_registry
        )
    except ProjectWriteLeaseBusy as error:
        raise HTTPException(
            status_code=409, detail={"kind": "project_busy", "retryable": True}
        ) from error
    except local_peer_service.LocalPeerConflict as error:
        raise HTTPException(status_code=409, detail={"kind": "assignment_conflict"}) from error
    except local_peer_auth.LocalPeerAuthError as error:
        raise HTTPException(status_code=403, detail={"kind": "scope_denied"}) from error
    except (local_peer_service.LocalPeerError, ValueError, FileNotFoundError) as error:
        raise HTTPException(status_code=400, detail={"kind": "project_mismatch"}) from error
    except PermissionError as error:
        raise HTTPException(status_code=409, detail={"kind": "managed_work_required"}) from error


@router.get("/tasks/{task_id}")
async def inspect_local_peer_task(
    task_id: str,
    request: Request,
    project_id: str = Query(min_length=64, max_length=64),
    requester_terminal_id: str = Query(pattern=r"^[a-f0-9]{8}$"),
) -> dict[str, object]:
    peer_instance_id = await _active_peer_headers(
        request, project_id=project_id, required_scope="task:status", task_id=task_id
    )
    try:
        return await asyncio.to_thread(
            local_peer_service.task_for_peer,
            task_id,
            peer_instance_id=peer_instance_id,
            project_id=project_id,
            requester_terminal_id=requester_terminal_id,
        )
    except local_peer_service.LocalPeerError as error:
        raise HTTPException(status_code=404, detail={"kind": "task_not_found"}) from error


@router.post("/tasks/{task_id}/cancel")
async def cancel_local_peer_task(
    task_id: str,
    request: Request,
    project_id: str = Query(min_length=64, max_length=64),
    requester_terminal_id: str = Query(pattern=r"^[a-f0-9]{8}$"),
) -> dict[str, object]:
    peer_instance_id = await _active_peer_headers(
        request, project_id=project_id, required_scope="task:cancel", task_id=task_id
    )
    try:
        return await asyncio.to_thread(
            local_peer_service.cancel_incoming_task,
            task_id,
            peer_instance_id=peer_instance_id,
            project_id=project_id,
            requester_terminal_id=requester_terminal_id,
            registry=request.app.state.plugin_registry,
        )
    except local_peer_service.LocalPeerError as error:
        raise HTTPException(status_code=404, detail={"kind": "task_not_found"}) from error


@router.delete("/peers/{peer_id}")
async def revoke_local_peer(
    peer_id: str,
    request: Request,
    project_id: str = Query(min_length=64, max_length=64),
) -> dict[str, object]:
    await _active_peer_headers(
        request,
        project_id=project_id,
        required_scope="peer:revoke",
        peer_path_id=peer_id,
    )
    revoked = local_peer_auth.revoke_peer_grant(peer_instance_id=peer_id, project_id=project_id)
    return {"revoked": revoked}


@router.get("/sessions")
async def list_local_project_sessions(
    request: Request,
    project_id: str = Query(min_length=64, max_length=64),
) -> dict[str, object]:
    await _active_peer_headers(request, project_id=project_id, required_scope="session:read")
    try:
        return await asyncio.to_thread(local_peer_service.project_session_snapshot, project_id)
    except local_peer_service.LocalPeerError as error:
        raise HTTPException(status_code=400, detail={"kind": "project_mismatch"}) from error


class AgentTaskSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requester_terminal_id: str = Field(pattern=r"^[a-f0-9]{8}$")
    peer_instance_id: str = Field(min_length=36, max_length=36)
    project_path: str = Field(min_length=1, max_length=4096)
    operation_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    agent_profile: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=32768)
    relative_working_directory: str | None = Field(default=None, max_length=4096)


class AgentTaskCancellation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requester_terminal_id: str = Field(pattern=r"^[a-f0-9]{8}$")


def _require_agent_terminal(terminal_id: str) -> dict[str, Any]:
    from cli_agent_orchestrator.clients import database

    if len(terminal_id) != 8 or any(char not in "0123456789abcdef" for char in terminal_id):
        raise HTTPException(status_code=400, detail={"kind": "requester_identity_invalid"})
    metadata = database.get_terminal_metadata(terminal_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail={"kind": "requester_terminal_not_found"})
    return metadata


def _require_task_principal(task_id: str, principal: Principal) -> None:
    from cli_agent_orchestrator.clients import database

    if not is_verified_principal(principal):
        raise HTTPException(status_code=403, detail={"kind": "scope_denied"})
    with database.SessionLocal() as session:
        row = session.get(database.LocalPeerTaskModel, task_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"kind": "task_not_found"})
        if row.requester_principal_id == principal.id:
            return
        if row.requester_principal_id is None and (
            principal.kind == "local_operator" or SCOPE_ADMIN in principal.scopes
        ):
            return
    raise HTTPException(status_code=403, detail={"kind": "scope_denied"})


def _require_requester_project(requester: dict[str, Any], binding: Mapping[str, object]) -> None:
    cwd = requester.get("working_directory")
    if not cwd or local_peer_service.project_binding(cwd)["project_id"] != binding["project_id"]:
        raise HTTPException(status_code=403, detail={"kind": "project_mismatch"})


@router.get(
    "/agent/peers",
    dependencies=[Depends(require_any_scope(SCOPE_READ, SCOPE_WRITE, SCOPE_ADMIN))],
)
async def list_agent_local_peers(
    request: Request,
    requester_terminal_id: str = Query(pattern=r"^[a-f0-9]{8}$"),
    project_path: str = Query(min_length=1, max_length=4096),
) -> list[dict[str, object]]:
    _require_loopback(request)
    requester = _require_agent_terminal(requester_terminal_id)
    try:
        binding = await asyncio.to_thread(local_peer_service.project_binding, project_path)
        await asyncio.to_thread(_require_requester_project, requester, binding)
        peers = await asyncio.to_thread(
            local_peer_service.list_local_peers,
            project_id=str(binding["project_id"]),
        )
    except (OSError, local_peer_service.LocalPeerError) as error:
        raise HTTPException(status_code=400, detail={"kind": "project_mismatch"}) from error
    return [peer for peer in peers if peer.get("revoked_at") is None]


@router.get(
    "/agent/peers/{peer_id}/sessions",
    dependencies=[Depends(require_any_scope(SCOPE_READ, SCOPE_WRITE, SCOPE_ADMIN))],
)
async def inspect_agent_peer_sessions(
    peer_id: str,
    request: Request,
    requester_terminal_id: str = Query(pattern=r"^[a-f0-9]{8}$"),
    project_path: str = Query(min_length=1, max_length=4096),
) -> dict[str, object]:
    _require_loopback(request)
    requester = _require_agent_terminal(requester_terminal_id)
    try:
        binding = await asyncio.to_thread(local_peer_service.project_binding, project_path)
        await asyncio.to_thread(_require_requester_project, requester, binding)
        return await asyncio.to_thread(
            local_peer_service.sessions_from_peer, peer_id, str(binding["project_id"])
        )
    except local_peer_auth.LocalPeerAuthError as error:
        raise HTTPException(status_code=403, detail={"kind": "scope_denied"}) from error
    except (OSError, local_peer_service.LocalPeerError) as error:
        raise HTTPException(status_code=400, detail={"kind": "project_mismatch"}) from error


@router.post(
    "/agent/tasks",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_any_scope(SCOPE_WRITE, SCOPE_ADMIN))],
)
async def submit_agent_local_peer_task(
    request: Request,
    body: AgentTaskSubmission,
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    _require_loopback(request)
    requester = _require_agent_terminal(body.requester_terminal_id)
    try:
        binding = await asyncio.to_thread(local_peer_service.project_binding, body.project_path)
        await asyncio.to_thread(_require_requester_project, requester, binding)
        relative_path = body.relative_working_directory
        if relative_path is None:
            caller_cwd = requester.get("working_directory")
            if not caller_cwd:
                raise local_peer_service.LocalPeerError(
                    "calling terminal has no working directory within a linked project"
                )
            relative_path = (
                Path(caller_cwd)
                .resolve(strict=True)
                .relative_to(Path(str(binding["canonical_root"])).resolve(strict=True))
                .as_posix()
                or "."
            )
        return await asyncio.to_thread(
            local_peer_service.submit_task_to_peer,
            peer_instance_id=body.peer_instance_id,
            project_id=str(binding["project_id"]),
            operation_key=body.operation_key,
            agent_profile=body.agent_profile,
            message=body.message,
            relative_working_directory=relative_path,
            requester_terminal_id=body.requester_terminal_id,
            requester_principal_id=principal.id,
        )
    except ProjectWriteLeaseBusy as error:
        raise HTTPException(status_code=409, detail={"kind": "project_busy"}) from error
    except local_peer_auth.PeerScopeDeniedError as error:
        raise HTTPException(status_code=403, detail={"kind": "scope_denied"}) from error
    except local_peer_service.LocalPeerConflict as error:
        raise HTTPException(status_code=409, detail={"kind": "assignment_conflict"}) from error
    except local_peer_service.LocalPeerUnavailable as error:
        raise HTTPException(status_code=503, detail={"kind": "peer_unavailable"}) from error
    except (OSError, ValueError, local_peer_service.LocalPeerError) as error:
        raise HTTPException(status_code=400, detail={"kind": "project_mismatch"}) from error


@router.get(
    "/agent/tasks/{task_id}",
    dependencies=[Depends(require_any_scope(SCOPE_READ, SCOPE_WRITE, SCOPE_ADMIN))],
)
async def inspect_agent_local_peer_task(
    task_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    requester_terminal_id: str = Query(pattern=r"^[a-f0-9]{8}$"),
) -> dict[str, object]:
    _require_loopback(request)
    _require_agent_terminal(requester_terminal_id)
    await asyncio.to_thread(_require_task_principal, task_id, principal)
    try:
        return await asyncio.to_thread(
            local_peer_service.task_from_peer,
            task_id,
            requester_terminal_id=requester_terminal_id,
        )
    except local_peer_service.LocalPeerUnavailable as error:
        raise HTTPException(status_code=503, detail={"kind": "peer_unavailable"}) from error
    except local_peer_auth.LocalPeerAuthError as error:
        raise HTTPException(status_code=403, detail={"kind": "scope_denied"}) from error
    except local_peer_service.LocalPeerError as error:
        raise HTTPException(status_code=404, detail={"kind": "task_not_found"}) from error


@router.post(
    "/agent/tasks/{task_id}/cancel",
    dependencies=[Depends(require_any_scope(SCOPE_WRITE, SCOPE_ADMIN))],
)
async def cancel_agent_local_peer_task(
    task_id: str,
    request: Request,
    body: AgentTaskCancellation,
    principal: Principal = Depends(get_current_principal),
) -> dict[str, object]:
    _require_loopback(request)
    _require_agent_terminal(body.requester_terminal_id)
    await asyncio.to_thread(_require_task_principal, task_id, principal)
    try:
        task = await asyncio.to_thread(
            local_peer_service.task_from_peer,
            task_id,
            requester_terminal_id=body.requester_terminal_id,
        )
        if task.get("source_instance_id") != local_peer_service.local_identity()["instance_id"]:
            raise local_peer_service.LocalPeerError("only the requesting CAO can cancel this task")
        return await asyncio.to_thread(local_peer_service.cancel_task_at_peer, task_id)
    except local_peer_service.LocalPeerUnavailable as error:
        raise HTTPException(status_code=503, detail={"kind": "peer_unavailable"}) from error
    except local_peer_auth.LocalPeerAuthError as error:
        raise HTTPException(status_code=403, detail={"kind": "scope_denied"}) from error
    except local_peer_service.LocalPeerError as error:
        raise HTTPException(status_code=404, detail={"kind": "task_not_found"}) from error
