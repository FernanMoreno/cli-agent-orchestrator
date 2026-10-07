"""Own broker allocation and remote delivery as one durable ordinary intent."""

from __future__ import annotations

import asyncio
import math
import os

import requests
from pydantic import Field

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.services import assignment_service as intents
from cli_agent_orchestrator.services import terminal_service
from cli_agent_orchestrator.utils.agent_profiles import load_agent_profile
from cli_agent_orchestrator.utils.orchestration import (
    REMOTE_CONNECT_TIMEOUT,
    _assign_remote,
    _mcp_timeout,
)


class ElasticAssignmentRequest(intents.AssignmentRequest):
    provider: str | None = Field(default=None, max_length=128)


def readiness_wait_seconds():
    try:
        value = float(os.environ.get("CAO_ELASTIC_WORKER_READY_WAIT", "120"))
    except (TypeError, ValueError):
        value = 120.0
    if not math.isfinite(value):
        value = 120.0
    return min(300.0, max(0.0, value))


async def assign(parent_id, payload, *, owner):
    job = asyncio.create_task(_owned(parent_id, payload, owner=owner))
    intents._jobs.add(job)
    job.add_done_callback(intents._finished)
    return await asyncio.shield(job)


async def _owned(parent_id, payload, *, owner):
    from cli_agent_orchestrator.services.work_launch_mode import managed_launch_required

    if managed_launch_required():
        raise PermissionError("Elastic ordinary placement cannot enforce managed Work launch")
    await asyncio.to_thread(terminal_service.ensure_terminal_is_not_work_owned, parent_id)
    parent = await asyncio.to_thread(database.get_terminal_metadata, parent_id)
    if parent is None:
        raise ValueError("parent terminal not found")
    await asyncio.to_thread(load_agent_profile, payload.agent_profile)
    receipt = await asyncio.to_thread(database.get_terminal_turn_receipt, parent_id)
    if payload.generation is None and receipt is not None:
        payload = payload.model_copy(update={"generation": receipt["generation"]})
    if payload.generation is not None and (
        receipt is None or receipt["generation"] != payload.generation
    ):
        raise intents.AssignmentConflict("parent turn changed")
    broker_url = os.environ.get("CAO_ELASTIC_BROKER_URL", "").strip().rstrip("/")
    broker_token = os.environ.get("CAO_ELASTIC_BROKER_TOKEN", "").strip()
    callback_url = os.environ.get("CAO_ELASTIC_CALLBACK_URL", "").strip()
    if not broker_url or not broker_token or not callback_url:
        raise ValueError("elastic broker and authenticated callback gateway must be configured")
    # Pin routing in the hash as well as task material. Secrets remain transient.
    from hashlib import sha256

    parent_incarnation_id = parent.get("session_incarnation_id")
    parent = {
        **parent,
        "session_incarnation_id": sha256(
            str([parent.get("session_incarnation_id"), broker_url, callback_url]).encode()
        ).hexdigest(),
    }
    admitted, retained = await asyncio.to_thread(
        intents._claim, "elastic:" + parent_id, payload, owner, parent
    )
    if not admitted:
        return retained
    identity = retained["assignment_id"]
    worker_id = None
    try:
        body = {"agent_profile": payload.agent_profile, "callback_terminal_id": parent_id}
        if payload.provider:
            body["provider"] = payload.provider
        response = await asyncio.to_thread(
            _allocate,
            parent_id,
            parent_incarnation_id,
            payload.generation,
            broker_url,
            broker_token,
            body,
        )
        response.raise_for_status()
        lease = response.json()
        worker_id = str(lease["worker_id"])
        worker_message = (
            payload.message
            + "\n\n[Elastic lifecycle: finish all tools before final prose. When work is complete call complete_assignment with the final result; do not use send_message for the final result.]"
        )
        result = await asyncio.to_thread(
            _assign_remote,
            agent_profile=payload.agent_profile,
            worker_message=worker_message,
            current_terminal_id=parent_id,
            working_directory=str(lease["working_directory"]),
            use_worktree=False,
            engine=payload.engine,
            model=payload.model,
            target_host=str(lease["target_host"]),
            ready_wait_seconds=readiness_wait_seconds(),
            callback_url=callback_url,
            remote_session_name=str(lease["session_name"]),
            operation_key=identity,
        )
        result = {
            **result,
            "assignment_id": identity,
            "operation_key": payload.operation_key,
            "worker_id": worker_id,
            "elastic": True,
            "state": result.get("state", "submitted" if result.get("success") else "reconcile"),
        }
        # Unknown delivery can mean a live worker. Never release it as a safe refusal.
        if result["state"] == "refused":
            released = await asyncio.to_thread(
                requests.delete,
                broker_url + "/workers/" + worker_id,
                headers={"X-CAO-Broker-Token": broker_token},
                timeout=(REMOTE_CONNECT_TIMEOUT, _mcp_timeout()),
            )
            result["worker_released"] = released.status_code < 400 or released.status_code == 404
    except Exception:
        result = {
            "assignment_id": identity,
            "operation_key": payload.operation_key,
            "worker_id": worker_id,
            "terminal_id": None,
            "elastic": True,
            "success": False,
            "state": "reconcile",
            "error_kind": "assignment_reconcile_required",
            "message": "Elastic placement requires inspection; broker allocation and delivery will not be repeated.",
        }
    return await asyncio.to_thread(intents._finish, identity, result)


def _allocate(parent_id, expected_incarnation, expected_generation, broker_url, broker_token, body):
    from cli_agent_orchestrator import constants
    from cli_agent_orchestrator.services.work_terminal import terminal_dispatch_lock

    with terminal_dispatch_lock(constants.DATABASE_FILE, parent_id):
        terminal_service.ensure_terminal_is_not_work_owned(parent_id)
        parent = database.get_terminal_metadata(parent_id)
        receipt = database.get_terminal_turn_receipt(parent_id)
        generation = receipt["generation"] if receipt is not None else None
        if (
            parent is None
            or parent.get("session_incarnation_id") != expected_incarnation
            or generation != expected_generation
        ):
            raise intents.AssignmentConflict("parent incarnation or turn changed before allocation")
        return requests.post(
            broker_url + "/workers",
            json=body,
            headers={"X-CAO-Broker-Token": broker_token},
            timeout=(REMOTE_CONNECT_TIMEOUT, 360),
        )
