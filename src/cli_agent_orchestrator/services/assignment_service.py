"""Durable ownership of an ordinary whole assignment, including deferred delivery.

Managed Work uses approved plans and its authenticated delivery reducer; this
ordinary endpoint cannot supply that authority. An unknown outcome is retained,
never interpreted as permission to launch a second worker.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from sqlalchemy.exc import IntegrityError

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.models.inbox import OrchestrationType
from cli_agent_orchestrator.services import terminal_service
from cli_agent_orchestrator.utils.agent_profiles import load_agent_profile, resolve_provider


class AssignmentConflict(ValueError):
    pass


class AssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    agent_profile: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=32768)
    working_directory: str | None = None
    engine: str | None = None
    model: str | None = None
    use_worktree: bool = False
    generation: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class FreshAssignmentRequest(AssignmentRequest):
    """One whole ordinary cross-node assignment, with no invented local parent."""

    callback_terminal_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    callback_url: HttpUrl
    session_name: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"
    )


class LocalPeerAssignmentRequest(AssignmentRequest):
    """A local CAO peer task; unlike remote callbacks it may use a worktree."""

    worktree_subdirectory: str | None = Field(default=None, max_length=4096)


_jobs: set[asyncio.Task] = set()


def _finished(task):
    _jobs.discard(task)
    if not task.cancelled():
        task.exception()


def _identity(parent_id: str, owner: str, operation_key: str) -> str:
    material = json.dumps([owner, parent_id, operation_key], separators=(",", ":"))
    return "assignment_" + hashlib.sha256(material.encode()).hexdigest()


def local_peer_assignment_id(*, owner: str, operation_key: str) -> str:
    """Return the deterministic assignment id used by a local peer launch."""
    return _identity("local-peer", owner, operation_key)


def _dto(row) -> dict[str, Any]:
    if row.result_json is not None:
        return cast(dict[str, Any], json.loads(row.result_json))
    return {
        "assignment_id": row.assignment_id,
        "terminal_id": None,
        "success": False,
        "state": "reconcile",
        "error_kind": "assignment_reconcile_required",
        "message": "Assignment admission exists; inspect its retained launch before retrying.",
    }


def inspect_assignment(assignment_id: str, *, owner: str) -> dict[str, Any]:
    with database.SessionLocal() as db:
        row = (
            db.query(database.AssignmentIntentModel).filter_by(assignment_id=assignment_id).first()
        )
        if row is None or row.owner != owner:
            raise AssignmentConflict("assignment not found for caller")
        return _dto(row)


def _claim(parent_id, payload, owner, parent):
    material = payload.model_dump(mode="json")
    material["parent_incarnation_id"] = parent.get("session_incarnation_id")
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    identity = _identity(parent_id, owner, payload.operation_key)
    with database.SessionLocal() as db:
        row = db.query(database.AssignmentIntentModel).filter_by(assignment_id=identity).first()
        if row is not None:
            if row.owner != owner or row.request_hash != digest:
                raise AssignmentConflict("assignment key was used for different material")
            return False, _dto(row)
        row = database.AssignmentIntentModel(
            assignment_id=identity,
            owner=owner,
            parent_terminal_id=parent_id,
            parent_incarnation_id=parent.get("session_incarnation_id"),
            generation=payload.generation,
            operation_key=payload.operation_key,
            request_hash=digest,
            state="admitted",
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            row = db.query(database.AssignmentIntentModel).filter_by(assignment_id=identity).one()
            if row.owner != owner or row.request_hash != digest:
                raise AssignmentConflict("assignment key was used for different material")
            return False, _dto(row)
        return True, {"assignment_id": identity}


def _finish(identity, result):
    with database.SessionLocal() as db:
        row = db.query(database.AssignmentIntentModel).filter_by(assignment_id=identity).one()
        if row.result_json is None:
            row.result_json = json.dumps(result, sort_keys=True)
            # A known external refusal settles this intent without claiming delivery.
            # Preserve its typed public receipt while using the closed local ledger state.
            row.state = "admitted" if result["state"] == "refused" else result["state"]
            db.commit()
        return _dto(row)


async def assign(
    parent_id: str, payload: AssignmentRequest, *, owner: str, registry
) -> dict[str, Any]:
    job = asyncio.create_task(_assign_owned(parent_id, payload, owner=owner, registry=registry))
    _jobs.add(job)
    job.add_done_callback(_finished)
    return await asyncio.shield(job)


async def _assign_owned(parent_id, payload, *, owner, registry):
    await asyncio.to_thread(terminal_service.ensure_terminal_is_not_work_owned, parent_id)
    parent = await asyncio.to_thread(database.get_terminal_metadata, parent_id)
    if parent is None:
        raise ValueError("parent terminal not found")
    receipt = await asyncio.to_thread(database.get_terminal_turn_receipt, parent_id)
    if payload.generation is None and receipt is not None:
        payload = payload.model_copy(update={"generation": receipt["generation"]})
    if payload.generation is not None:
        if receipt is None or receipt["generation"] != payload.generation:
            raise AssignmentConflict("parent turn generation changed")
    # Provider/profile errors are definitive refusals before durable admission.
    await asyncio.to_thread(load_agent_profile, payload.agent_profile)
    provider = await asyncio.to_thread(
        resolve_provider, payload.agent_profile, fallback_provider=parent["provider"]
    )
    admitted, receipt = await asyncio.to_thread(_claim, parent_id, payload, owner, parent)
    if not admitted:
        return receipt
    identity = receipt["assignment_id"]
    try:
        terminal = await terminal_service.create_terminal(
            provider=provider,
            agent_profile=payload.agent_profile,
            session_name=parent["tmux_session"],
            new_session=False,
            working_directory=payload.working_directory or parent.get("working_directory"),
            allowed_tools=parent.get("allowed_tools"),
            registry=registry,
            caller_id=parent_id,
            defer_init=True,
            initial_message=payload.message,
            initial_message_orchestration_type=OrchestrationType.ASSIGN,
            engine=payload.engine,
            model=payload.model,
            use_worktree=payload.use_worktree,
            idempotency_key=identity,
            fence_parent=True,
            expected_parent_incarnation_id=parent.get("session_incarnation_id"),
            expected_parent_generation=payload.generation,
        )
        result = {
            "assignment_id": identity,
            "terminal_id": terminal.id,
            "success": True,
            "state": "submitted",
            "message": "Assignment admitted; worker initialization and delivery are pending.",
        }
    except Exception:
        result = {
            "assignment_id": identity,
            "terminal_id": None,
            "success": False,
            "state": "reconcile",
            "error_kind": "assignment_reconcile_required",
            "message": "Assignment outcome requires inspection; the task will not be repeated.",
        }
    return await asyncio.to_thread(_finish, identity, result)


async def assign_fresh(payload: FreshAssignmentRequest, *, owner: str, registry) -> dict[str, Any]:
    job = asyncio.create_task(_assign_fresh_owned(payload, owner=owner, registry=registry))
    _jobs.add(job)
    job.add_done_callback(_finished)
    return await asyncio.shield(job)


async def assign_local_peer_fresh(
    payload: LocalPeerAssignmentRequest, *, owner: str, registry
) -> dict[str, Any]:
    """Admit a local peer task with the ordinary durable assignment ledger.

    No callback URL or remote target is involved. The peer task service owns
    project authorization and write leases; this boundary owns idempotent
    terminal creation using the existing assignment identity and receipt.
    """
    job = asyncio.create_task(
        _assign_local_peer_fresh_owned(payload, owner=owner, registry=registry)
    )
    _jobs.add(job)
    job.add_done_callback(_finished)
    return await asyncio.shield(job)


async def _assign_local_peer_fresh_owned(payload, *, owner, registry):
    from cli_agent_orchestrator.services.work_launch_mode import managed_launch_required

    if managed_launch_required():
        raise PermissionError("Managed Work launch is required; local peer task is unavailable")
    await asyncio.to_thread(load_agent_profile, payload.agent_profile)
    provider = await asyncio.to_thread(
        resolve_provider, payload.agent_profile, fallback_provider="kiro_cli"
    )
    admitted, receipt = await asyncio.to_thread(_claim, "local-peer", payload, owner, {})
    if not admitted:
        return receipt
    identity = receipt["assignment_id"]
    try:
        terminal = await terminal_service.create_terminal(
            provider=provider,
            agent_profile=payload.agent_profile,
            session_name=None,
            new_session=True,
            working_directory=payload.working_directory,
            registry=registry,
            defer_init=True,
            initial_message=payload.message,
            initial_message_orchestration_type=OrchestrationType.ASSIGN,
            engine=payload.engine,
            model=payload.model,
            use_worktree=payload.use_worktree,
            worktree_subdirectory=payload.worktree_subdirectory,
            idempotency_key=identity,
        )
        result = {
            "assignment_id": identity,
            "terminal_id": terminal.id,
            "session_name": terminal.session_name,
            "success": True,
            "state": "submitted",
            "message": "Local peer assignment admitted; initialization and delivery are pending.",
        }
    except Exception:
        result = {
            "assignment_id": identity,
            "terminal_id": None,
            "success": False,
            "state": "reconcile",
            "error_kind": "assignment_reconcile_required",
            "message": "Local peer assignment outcome requires inspection; it will not be repeated.",
        }
    return await asyncio.to_thread(_finish, identity, result)


async def _assign_fresh_owned(payload, *, owner, registry):
    from cli_agent_orchestrator.constants import CALLBACK_TERMINAL_ID_ENV, CALLBACK_URL_ENV
    from cli_agent_orchestrator.services.work_launch_mode import managed_launch_required

    if managed_launch_required():
        raise PermissionError("Managed Work launch is required; use /work-launches.")
    if payload.use_worktree:
        raise ValueError("Cross-node ordinary assignments do not provision worktrees")
    await asyncio.to_thread(load_agent_profile, payload.agent_profile)
    provider = await asyncio.to_thread(
        resolve_provider, payload.agent_profile, fallback_provider="kiro_cli"
    )
    # Callback identity is external routing, never proof of local Work ownership.
    parent_id = "remote-assignment"
    admitted, receipt = await asyncio.to_thread(_claim, parent_id, payload, owner, {})
    if not admitted:
        return receipt
    identity = receipt["assignment_id"]
    try:
        terminal = await terminal_service.create_terminal(
            provider=provider,
            agent_profile=payload.agent_profile,
            session_name=payload.session_name,
            new_session=True,
            working_directory=payload.working_directory,
            registry=registry,
            env_vars={
                CALLBACK_URL_ENV: str(payload.callback_url).rstrip("/"),
                CALLBACK_TERMINAL_ID_ENV: payload.callback_terminal_id,
            },
            defer_init=True,
            initial_message=payload.message,
            initial_message_orchestration_type=OrchestrationType.ASSIGN,
            engine=payload.engine,
            model=payload.model,
            idempotency_key=identity,
        )
        result = {
            "assignment_id": identity,
            "terminal_id": terminal.id,
            "session_name": terminal.session_name,
            "success": True,
            "state": "submitted",
            "message": "Assignment admitted; initialization and delivery are pending.",
        }
    except Exception:
        result = {
            "assignment_id": identity,
            "terminal_id": None,
            "success": False,
            "state": "reconcile",
            "error_kind": "assignment_reconcile_required",
            "message": "Assignment outcome requires inspection; the task will not be repeated.",
        }
    return await asyncio.to_thread(_finish, identity, result)
