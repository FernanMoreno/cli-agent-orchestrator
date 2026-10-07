"""Scoped central remote routing with durable, exact runtime placement."""

import asyncio
import hmac
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.runtime_channel_schema import RemotePlacementModel
from cli_agent_orchestrator.models.terminal import Terminal
from cli_agent_orchestrator.models.work_operations import RuntimeOperations
from cli_agent_orchestrator.runtime_channel.durable_connection import DurableRuntimeConnection
from cli_agent_orchestrator.runtime_channel.protocol import (
    PROTOCOL_VERSION,
    CommandType,
    Hello,
    Result,
    Status,
    decode,
    encode,
)
from cli_agent_orchestrator.runtime_channel.registry import (
    LAUNCH_TIMEOUT,
    RemoteRuntimeError,
    RuntimeUnavailableError,
    runtime_registry,
)
from cli_agent_orchestrator.runtime_channel.store import (
    RemoteIdentityConflict,
    RemoteOperationStore,
)
from cli_agent_orchestrator.runtime_channel.token import TOKEN_HEADER, runtime_token
from cli_agent_orchestrator.security.auth import (
    SCOPE_ADMIN,
    SCOPE_READ,
    SCOPE_WRITE,
    Principal,
    get_current_principal,
    require_any_scope,
)
from cli_agent_orchestrator.services import remote_terminal_service as remote
from cli_agent_orchestrator.services.event_bus import bus

router = APIRouter()
_compensations = {}


async def compensate_launch(connection, operation):
    """One stable delete operation owns compensation; uncertainty retains rows."""
    terminal = operation["result"]["payload"]["terminal"]

    def teardown():
        from cli_agent_orchestrator import constants
        from cli_agent_orchestrator.services.terminal_service import (
            ensure_terminal_is_not_work_owned,
        )
        from cli_agent_orchestrator.services.work_terminal import terminal_dispatch_lock

        with terminal_dispatch_lock(constants.DATABASE_FILE, terminal["id"]):
            ensure_terminal_is_not_work_owned(terminal["id"])
            result = runtime_registry.call_blocking(
                connection.runtime_id,
                CommandType.DELETE,
                {
                    "_launch_op_id": operation["op_id"],
                    "_session_incarnation_id": terminal["session_incarnation_id"],
                },
                terminal_id=terminal["id"],
                incarnation_id=connection.incarnation_id,
                op_id="cleanup-" + operation["op_id"],
            )
            if result.get("deleted") is True:
                remote.remove_confirmed(
                    terminal["id"],
                    runtime_id=connection.runtime_id,
                    incarnation_id=connection.incarnation_id,
                    launch_op_id=operation["op_id"],
                    session_incarnation_id=terminal["session_incarnation_id"],
                )

    await asyncio.to_thread(teardown)


def schedule_compensation(connection, operation):
    if operation["op_id"] in _compensations:
        return
    task = asyncio.create_task(compensate_launch(connection, operation))
    _compensations[operation["op_id"]] = task

    def finished(done):
        _compensations.pop(operation["op_id"], None)
        if not done.cancelled():
            done.exception()  # Uncertain cleanup retains durable evidence for inspection.

    task.add_done_callback(finished)


async def shutdown():
    if _compensations:
        await asyncio.gather(*list(_compensations.values()), return_exceptions=True)
    for connection in list(runtime_registry._runtimes.values()):
        if connection.close_socket:
            await connection.close_socket()
        runtime_registry.unregister(connection.runtime_id, connection)


class LaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_profile: str = Field(min_length=1, max_length=256)
    provider: str | None = Field(None, max_length=128)
    working_directory: str | None = Field(None, max_length=4096)
    allowed_tools: list[str] | None = Field(None, max_length=1024)
    engine: str | None = Field(None, max_length=64)
    model: str | None = Field(None, max_length=256)
    managed_work: bool = False
    op_id: str = Field(default_factory=lambda: uuid.uuid4().hex, pattern=r"^[A-Za-z0-9._-]{1,128}$")


async def accept_result(connection, result):
    if result.runtime_incarnation_id != connection.incarnation_id:
        return False
    await connection.resolve_durable(result)
    row = await asyncio.to_thread(connection.store.get, result.op_id)
    if not row or row["state"] != "settled":
        return False
    canonical = Result.model_validate(row["result"])
    if row["command_type"] == "launch" and canonical.ok:
        cleanup = await asyncio.to_thread(connection.store.get, "cleanup-" + row["op_id"])
        if (
            cleanup
            and cleanup["state"] == "settled"
            and cleanup["result"]["ok"]
            and cleanup["result"]["payload"].get("deleted") is True
        ):
            return True
        # The immutable first result is authoritative even if the HTTP waiter left.
        terminal = Terminal.model_validate(canonical.payload["terminal"]).model_dump(mode="json")
        if await asyncio.to_thread(
            remote.launch_was_deleted,
            connection.runtime_id,
            connection.incarnation_id,
            result.op_id,
            terminal,
        ):
            return True
        await asyncio.to_thread(
            remote.record_placement,
            connection.runtime_id,
            connection.incarnation_id,
            result.op_id,
            terminal,
        )
        await asyncio.to_thread(
            remote.update_projection,
            terminal["id"],
            connection.incarnation_id,
            terminal,
            canonical.payload.get("revision", 0),
        )
        if await asyncio.to_thread(connection.store.cancellation_requested, row["op_id"]):
            schedule_compensation(connection, row)
    return True


@router.websocket("/runtime/channel")
async def runtime_channel(websocket: WebSocket):
    token = runtime_token()
    provided = websocket.headers.get(TOKEN_HEADER, "")
    if not token or not hmac.compare_digest(provided, token):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    connection = None
    try:
        hello = decode(await asyncio.wait_for(websocket.receive_text(), 10))
        if (
            not isinstance(hello, Hello)
            or hello.protocol_version != PROTOCOL_VERSION
            or not hello.runtime_incarnation_id
        ):
            await websocket.close(code=1008)
            return
        store = RemoteOperationStore(database.engine)
        epoch = await asyncio.to_thread(
            store.activate_runtime, hello.runtime_id, hello.runtime_incarnation_id
        )
        connection = DurableRuntimeConnection(
            hello.runtime_id,
            hello.runtime_incarnation_id,
            epoch,
            store,
            websocket.send_text,
            websocket.close,
        )
        runtime_registry.register(
            hello.runtime_id, websocket.send_text, websocket.close, connection=connection
        )
        with database.engine.connect() as conn:
            rows = (
                conn.execute(
                    select(RemotePlacementModel.__table__).where(
                        RemotePlacementModel.runtime_id == hello.runtime_id,
                        RemotePlacementModel.incarnation_id == hello.runtime_incarnation_id,
                    )
                )
                .mappings()
                .all()
            )
        for row in rows:
            runtime_registry.place(row["terminal_id"], hello.runtime_id)
        await websocket.send_text(
            encode(
                Hello(
                    protocol_version=PROTOCOL_VERSION,
                    runtime_id=hello.runtime_id,
                    runtime_incarnation_id=hello.runtime_incarnation_id,
                    connection_epoch=epoch,
                )
            )
        )
        runtime_registry.activate(connection)
        while True:
            raw = await websocket.receive_text()
            if len(raw.encode()) > 16 * 1024 * 1024:
                await websocket.close(code=1009)
                return
            frame = decode(raw)
            if isinstance(frame, Result):
                await accept_result(connection, frame)
            elif (
                isinstance(frame, Status)
                and frame.runtime_incarnation_id == connection.incarnation_id
            ):
                if runtime_registry.connection(hello.runtime_id) is not connection:
                    continue
                changed = await asyncio.to_thread(
                    remote.update_projection,
                    frame.terminal_id,
                    connection.incarnation_id,
                    frame.projection,
                    frame.revision,
                )
                if changed and runtime_registry.set_status(
                    frame.terminal_id, hello.runtime_id, frame.status, connection
                ):
                    bus.publish(
                        f"terminal.{frame.terminal_id}.status",
                        {
                            "status": frame.status.value,
                            "turn": frame.projection.get("turn"),
                            "turn_sequence": frame.projection.get("turn_sequence"),
                            "turn_completed": frame.projection.get("turn_completed"),
                        },
                    )
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        if connection is not None:
            runtime_registry.unregister(connection.runtime_id, connection)


@router.get(
    "/runtimes", dependencies=[Depends(require_any_scope(SCOPE_READ, SCOPE_WRITE, SCOPE_ADMIN))]
)
async def list_runtimes():
    return runtime_registry.list_runtimes()


@router.post(
    "/runtimes/{runtime_id}/terminals",
    response_model=Terminal,
    dependencies=[Depends(require_any_scope(SCOPE_WRITE, SCOPE_ADMIN))],
)
async def launch_remote(runtime_id: str, body: LaunchRequest):
    from cli_agent_orchestrator.services.work_launch_mode import managed_launch_required

    if body.managed_work or managed_launch_required():
        from cli_agent_orchestrator.backends.base import UnsupportedWorkEnforcement

        try:
            remote.preflight_managed_remote()
        except UnsupportedWorkEnforcement as exc:
            raise HTTPException(
                400,
                {"code": exc.error_kind, "message": str(exc), "required_level": exc.required_level},
            ) from exc
    conn = runtime_registry.connection(runtime_id)
    if not isinstance(conn, DurableRuntimeConnection):
        raise HTTPException(503, "Runtime is unavailable before dispatch")
    try:
        payload = await conn.call(
            CommandType.LAUNCH,
            body.model_dump(exclude={"op_id"}),
            timeout=LAUNCH_TIMEOUT,
            op_id=body.op_id,
        )
        terminal = Terminal.model_validate(payload["terminal"])
        # Paired transport tests and production accept_result both settle before this point.
        await asyncio.to_thread(
            remote.record_placement,
            runtime_id,
            conn.incarnation_id,
            body.op_id,
            terminal.model_dump(mode="json"),
        )
        await asyncio.to_thread(
            remote.update_projection,
            terminal.id,
            conn.incarnation_id,
            terminal.model_dump(mode="json"),
            payload.get("revision", 0),
        )
        return Terminal.model_validate(
            await asyncio.to_thread(remote.terminal_projection, terminal.id)
        )
    except asyncio.CancelledError:
        await asyncio.shield(asyncio.to_thread(conn.store.request_cancellation, body.op_id))
        row = await asyncio.to_thread(conn.store.get, body.op_id)
        if row and row["state"] == "settled" and row["result"]["ok"]:
            schedule_compensation(conn, row)
        raise
    except RemoteRuntimeError as exc:
        raise HTTPException(
            exc.status_code,
            {
                "code": (
                    "remote_outcome_unknown"
                    if exc.status_code == 504
                    else "remote_unavailable" if exc.status_code == 503 else "remote_command_failed"
                ),
                "message": str(exc),
                "op_id": body.op_id,
                "delivery_may_have_occurred": exc.status_code == 504,
            },
        ) from exc
    except RemoteIdentityConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get(
    "/runtimes/{runtime_id}/operations/{op_id}",
    dependencies=[Depends(require_any_scope(SCOPE_READ, SCOPE_WRITE, SCOPE_ADMIN))],
)
async def get_operation(runtime_id: str, op_id: str, refresh: bool = False):
    row = await asyncio.to_thread(RemoteOperationStore(database.engine).get, op_id)
    if not row or row["runtime_id"] != runtime_id:
        raise HTTPException(404, "Remote operation not found")
    if refresh and row["state"] in {"dispatching", "reconcile"}:
        conn = runtime_registry.connection(runtime_id)
        if (
            isinstance(conn, DurableRuntimeConnection)
            and conn.incarnation_id == row["incarnation_id"]
        ):
            observed = await conn.call(CommandType.INSPECT, {"op_id": op_id})
            if observed.get("operation_result"):
                await accept_result(conn, Result.model_validate(observed["operation_result"]))
                row = await asyncio.to_thread(conn.store.get, op_id)
    # Omit stored prompt/policy payload and backend transcript from diagnostic reads.
    result = {
        key: row[key]
        for key in (
            "op_id",
            "runtime_id",
            "incarnation_id",
            "terminal_id",
            "generation",
            "deadline",
            "command_type",
            "state",
        )
    }
    if row["command_type"] == "launch" and row["state"] == "settled" and row["result"]["ok"]:
        result["terminal_id"] = row["result"]["payload"]["terminal"]["id"]
    result["cancellation_requested"] = await asyncio.to_thread(
        RemoteOperationStore(database.engine).cancellation_requested, op_id
    )
    return result


@router.post(
    "/runtimes/{runtime_id}/operations/{op_id}/cancel",
    dependencies=[Depends(require_any_scope(SCOPE_WRITE, SCOPE_ADMIN))],
)
async def cancel_operation(runtime_id: str, op_id: str):
    store = RemoteOperationStore(database.engine)
    row = await asyncio.to_thread(store.get, op_id)
    if not row or row["runtime_id"] != runtime_id or row["command_type"] != "launch":
        raise HTTPException(404, "Remote launch operation not found")
    await asyncio.to_thread(store.request_cancellation, op_id)
    unsent = await asyncio.to_thread(store.cancel_unsent, op_id)
    conn = runtime_registry.connection(runtime_id)
    if (
        row["state"] == "settled"
        and row["result"]["ok"]
        and isinstance(conn, DurableRuntimeConnection)
        and conn.incarnation_id == row["incarnation_id"]
    ):
        schedule_compensation(conn, row)
    return {
        "op_id": op_id,
        "state": "cancelled" if unsent else "reconcile",
        "cancellation_requested": True,
    }


@router.get("/runtimes/operations", response_model=RuntimeOperations)
def get_runtime_operations(
    principal: Principal = Depends(get_current_principal),
    _scopes: list[str] = Depends(require_any_scope(SCOPE_ADMIN)),
    limit: int = Query(default=100, ge=1, le=100),
    after: str = Query(default="", max_length=512),
):
    """Admin-only durable known-node observations, including disconnected nodes."""
    from cli_agent_orchestrator.services.work_operations import runtime_operations

    try:
        return runtime_operations(
            principal, database.engine, runtime_registry, limit=limit, after=after
        )
    except PermissionError as error:
        raise HTTPException(403, detail="verified administrator required") from error
