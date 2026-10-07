"""Durable ordinary remote placement; no managed Work enforcement authority."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, inspect, select, update
from sqlalchemy.dialects.sqlite import insert

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.runtime_channel_schema import (
    RemoteOperationModel,
    RemotePlacementModel,
)
from cli_agent_orchestrator.runtime_channel.protocol import CommandType
from cli_agent_orchestrator.runtime_channel.registry import (
    RuntimeUnavailableError,
    runtime_registry,
)
from cli_agent_orchestrator.runtime_channel.store import RemoteIdentityConflict


def placement(terminal_id: str) -> dict[str, Any] | None:
    engine = database.engine
    if not inspect(engine).has_table(RemotePlacementModel.__tablename__):
        return None
    with engine.connect() as conn:
        row = (
            conn.execute(
                select(RemotePlacementModel.__table__).where(
                    RemotePlacementModel.terminal_id == terminal_id
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None


def launch_was_deleted(runtime_id, incarnation_id, launch_op_id, terminal, *, connection=None):
    """Exact successful deletes are durable tombstones, including ordinary deletes."""
    if connection is None:
        with database.engine.connect() as opened:
            return launch_was_deleted(
                runtime_id, incarnation_id, launch_op_id, terminal, connection=opened
            )
    table = RemoteOperationModel.__table__
    rows = connection.execute(
        select(table.c.request_json, table.c.result_json).where(
            table.c.runtime_id == runtime_id,
            table.c.incarnation_id == incarnation_id,
            table.c.terminal_id == terminal["id"],
            table.c.command_type == "delete",
            table.c.state == "settled",
        )
    )
    for request_json, result_json in rows:
        request, result = json.loads(request_json), json.loads(result_json)
        payload = request["payload"]
        if (
            payload.get("_launch_op_id") == launch_op_id
            and payload.get("_session_incarnation_id") == terminal.get("session_incarnation_id")
            and result.get("ok") is True
            and result.get("payload", {}).get("deleted") is True
        ):
            return True
    return False


def record_placement(runtime_id, incarnation_id, launch_op_id, terminal):
    """Only a durable settled launch may establish central placement."""
    from cli_agent_orchestrator.runtime_channel.store import RemoteOperationStore

    operation = RemoteOperationStore(database.engine).get(launch_op_id)
    cleanup = RemoteOperationStore(database.engine).get("cleanup-" + launch_op_id)
    if (
        cleanup
        and cleanup["state"] == "settled"
        and cleanup["result"]["ok"]
        and cleanup["result"]["payload"].get("deleted") is True
    ):
        raise RemoteIdentityConflict("Canceled remote launch was already dismantled")
    if (
        not operation
        or operation["state"] != "settled"
        or not operation["result"]["ok"]
        or operation["runtime_id"] != runtime_id
        or operation["incarnation_id"] != incarnation_id
        or operation["command_type"] != "launch"
    ):
        raise RemoteIdentityConflict("Remote placement lacks its settled launch proof")
    terminal_id = terminal["id"]
    incarnation = terminal.get("session_incarnation_id")
    if not incarnation:
        raise RemoteIdentityConflict("Remote launch lacks session incarnation")
    encoded = json.dumps(terminal, default=str, allow_nan=False)
    table = RemotePlacementModel.__table__
    with database.engine.begin() as conn:
        # The WebSocket result handler and HTTP waiter share this publication.
        # Acquire SQLite writer ownership before checking either identity row.
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        if launch_was_deleted(runtime_id, incarnation_id, launch_op_id, terminal, connection=conn):
            raise RemoteIdentityConflict("Deleted remote launch cannot publish again")
        existing = (
            conn.execute(select(table).where(table.c.terminal_id == terminal_id)).mappings().first()
        )
        if existing:
            if (existing["runtime_id"], existing["incarnation_id"], existing["launch_op_id"]) != (
                runtime_id,
                incarnation_id,
                launch_op_id,
            ):
                raise RemoteIdentityConflict("Remote terminal ID already belongs to another launch")
            return
        local = conn.execute(
            select(database.TerminalModel.__table__).where(database.TerminalModel.id == terminal_id)
        ).first()
        if local:
            raise RemoteIdentityConflict("Remote terminal ID collides with a local terminal")
        # Row and placement publish atomically, with a namespace distinct from local sessions.
        session = f"remote-{runtime_id}-{terminal['session_name']}"
        conn.execute(
            database.TerminalModel.__table__.insert().values(
                id=terminal_id,
                tmux_session=session,
                tmux_window=terminal["name"],
                provider=terminal["provider"],
                agent_profile=terminal.get("agent_profile"),
                allowed_tools=(
                    json.dumps(terminal["allowed_tools"])
                    if terminal.get("allowed_tools") is not None
                    else None
                ),
                engine=terminal.get("engine"),
                caller_id=terminal.get("caller_id"),
                session_incarnation_id=incarnation,
                deferred_init_external_owner=False,
                deferred_init_runtime_reclaimed=False,
            )
        )
        conn.execute(
            insert(table).values(
                terminal_id=terminal_id,
                runtime_id=runtime_id,
                incarnation_id=incarnation_id,
                launch_op_id=launch_op_id,
                session_incarnation_id=incarnation,
                identity_json=encoded,
                projection_json=encoded,
                revision=0,
            )
        )
        # Keep volatile placement ordering under the same publication writer lock.
        runtime_registry.place(terminal_id, runtime_id)


def update_projection(terminal_id, incarnation_id, projection, revision):
    from cli_agent_orchestrator.models.terminal import Terminal

    if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
        return False
    try:
        projection = Terminal.model_validate(projection).model_dump(mode="json")
    except (ValueError, TypeError):
        return False
    row = placement(terminal_id)
    if (
        not row
        or projection.get("id") != terminal_id
        or projection.get("session_incarnation_id") != row["session_incarnation_id"]
    ):
        return False
    identity = json.loads(row["identity_json"])
    if any(
        projection.get(key) != identity.get(key) for key in ("session_name", "name", "provider")
    ):
        return False
    table = RemotePlacementModel.__table__
    with database.engine.begin() as conn:
        result = conn.execute(
            update(table)
            .where(
                table.c.terminal_id == terminal_id,
                table.c.incarnation_id == incarnation_id,
                table.c.session_incarnation_id == row["session_incarnation_id"],
                table.c.launch_op_id == row["launch_op_id"],
                table.c.revision < revision,
            )
            .values(
                projection_json=json.dumps(projection, default=str, allow_nan=False),
                revision=revision,
            )
        )
        return result.rowcount == 1


def terminal_projection(terminal_id: str) -> dict[str, Any] | None:
    row = placement(terminal_id)
    if not row:
        return None
    result: dict[str, Any] = json.loads(row["projection_json"])
    result.update(runtime_id=row["runtime_id"], runtime_incarnation_id=row["incarnation_id"])
    metadata = database.get_terminal_metadata(terminal_id)
    if metadata:
        result["session_name"] = metadata["tmux_session"]
        result["group"], result["metadata"] = metadata.get("group"), metadata.get("metadata")
    conn = runtime_registry.connection(row["runtime_id"])
    if conn is None or getattr(conn, "incarnation_id", None) != row["incarnation_id"]:
        result["status"] = "unknown"
    return result


def _connection(row):
    conn = runtime_registry.connection(row["runtime_id"])
    if conn is None or getattr(conn, "incarnation_id", None) != row["incarnation_id"]:
        raise RuntimeUnavailableError("Exact remote runtime incarnation is unavailable")
    return conn


def call(terminal_id, kind, payload=None, *, generation=None) -> dict[str, Any]:
    from cli_agent_orchestrator.services.terminal_service import ensure_terminal_is_not_work_owned

    ensure_terminal_is_not_work_owned(terminal_id)
    row = placement(terminal_id)
    if not row:
        raise ValueError("Remote terminal placement not found")
    _connection(row)
    scoped_payload = dict(payload or {})
    scoped_payload["_session_incarnation_id"] = row["session_incarnation_id"]
    scoped_payload["_launch_op_id"] = row["launch_op_id"]
    result: dict[str, Any] = runtime_registry.call_blocking(
        row["runtime_id"],
        kind,
        scoped_payload,
        terminal_id=terminal_id,
        generation=generation,
        incarnation_id=row["incarnation_id"],
    )
    if isinstance(result.get("terminal"), dict):
        update_projection(
            terminal_id, row["incarnation_id"], result["terminal"], result.get("revision", 0)
        )
    elif isinstance(result.get("turn"), dict):
        current = json.loads(row["projection_json"])
        current["turn"] = result["turn"]
        update_projection(terminal_id, row["incarnation_id"], current, result.get("revision", 0))
    return result


async def call_async(terminal_id, kind, payload=None, *, generation=None) -> dict[str, Any]:
    import asyncio

    return await asyncio.to_thread(call, terminal_id, kind, payload, generation=generation)


def delete_remote(terminal_id: str) -> bool:
    from cli_agent_orchestrator import constants
    from cli_agent_orchestrator.services.work_terminal import terminal_dispatch_lock

    with terminal_dispatch_lock(constants.DATABASE_FILE, terminal_id):
        expected = placement(terminal_id)
        if expected is None:
            return False
        result = call(terminal_id, CommandType.DELETE)
        if result.get("deleted") is not True:
            return False
        return remove_confirmed(
            terminal_id,
            runtime_id=expected["runtime_id"],
            incarnation_id=expected["incarnation_id"],
            launch_op_id=expected["launch_op_id"],
            session_incarnation_id=expected["session_incarnation_id"],
        )


def remove_confirmed(
    terminal_id, *, runtime_id, incarnation_id, launch_op_id, session_incarnation_id
) -> bool:
    """Reduce only the captured placement after an exact settled remote teardown."""
    table = RemotePlacementModel.__table__
    terminals = database.TerminalModel.__table__
    with database.engine.begin() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        current = (
            conn.execute(select(table).where(table.c.terminal_id == terminal_id)).mappings().first()
        )
        if current is None or (
            current["runtime_id"],
            current["incarnation_id"],
            current["launch_op_id"],
            current["session_incarnation_id"],
        ) != (runtime_id, incarnation_id, launch_op_id, session_incarnation_id):
            return False
        local = (
            conn.execute(select(terminals).where(terminals.c.id == terminal_id)).mappings().first()
        )
        if local is not None and local["session_incarnation_id"] != session_incarnation_id:
            return False
        conn.execute(delete(RemotePlacementModel).where(table.c.terminal_id == terminal_id))
        conn.execute(
            delete(database.TerminalModel).where(
                terminals.c.id == terminal_id,
                terminals.c.session_incarnation_id == session_incarnation_id,
            )
        )
        # A replacement publisher must acquire the same SQLite writer ownership.
        # Forget the old volatile mapping before that replacement can commit.
        runtime_registry.forget(terminal_id)
    return True


def preflight_managed_remote():
    from cli_agent_orchestrator.backends.base import UnsupportedWorkEnforcement

    raise UnsupportedWorkEnforcement(
        "RemoteRuntime", "approved portable Work enforcement adapter unavailable"
    )


def session_terminals(session_name: str) -> list[dict[str, Any]] | None:
    rows = database.list_terminals_by_session(session_name)
    if not rows or not any(placement(row["id"]) for row in rows):
        return None
    if not all(placement(row["id"]) for row in rows):
        raise RemoteIdentityConflict("Remote and local session identities overlap")
    return rows


def sessions() -> list[dict[str, Any]]:
    if not inspect(database.engine).has_table(RemotePlacementModel.__tablename__):
        return []
    with database.engine.connect() as conn:
        names = (
            conn.execute(
                select(database.TerminalModel.tmux_session)
                .join(
                    RemotePlacementModel,
                    RemotePlacementModel.terminal_id == database.TerminalModel.id,
                )
                .distinct()
            )
            .scalars()
            .all()
        )
    result: list[dict[str, Any]] = []
    for name in names:
        rows = session_terminals(name)
        if rows is None:
            continue
        projections: list[dict[str, Any]] = []
        for row in rows:
            projection = terminal_projection(row["id"])
            if projection is not None:
                projections.append(projection)
        # Inventory and projection reads are separate snapshots. Teardown may
        # remove every terminal after a session name was enumerated.
        if not projections:
            continue
        result.append(
            {
                "id": name,
                "name": name,
                "status": (
                    "active"
                    if any(projection.get("status") != "unknown" for projection in projections)
                    else "detached"
                ),
            }
        )
    return result
