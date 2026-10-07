"""Read-only operational observations; expiry and connectivity never prove stop."""

import time
from urllib.parse import quote

from sqlalchemy import func, select

from cli_agent_orchestrator.clients.runtime_channel_schema import (
    RemoteOperationModel,
    RemoteRuntimeModel,
)
from cli_agent_orchestrator.clients.work_repository import _stored_recovery_context
from cli_agent_orchestrator.security.auth import (
    SCOPE_ADMIN,
    SCOPE_READ,
    SCOPE_WRITE,
    is_verified_principal,
)


def project_operations(connection, principal, job, work) -> dict:
    """Called only inside WorkQueries' verified, owner-checked snapshot."""
    attempt = work["attempts"][-1] if work["attempts"] else None
    context = _stored_recovery_context(connection)
    allowed = context.execution_state == "normal"
    capacity = None
    reservations = None
    expired = False
    if attempt:
        expired = (
            attempt["lease_expires_at"] is not None and attempt["lease_expires_at"] <= time.time()
        )
        row = connection.execute(
            "SELECT id,state,units,revision,stop_evidence_ref FROM work_scheduler_requests WHERE attempt_id=? AND generation=?",
            (attempt["id"], attempt["generation"]),
        ).fetchone()
        if row:
            capacity = dict(zip(("id", "state", "units", "revision", "stop_evidence_ref"), row))
        row = connection.execute(
            "SELECT s.id,s.state,s.revision,s.stop_evidence_ref,count(p.resource_key) FROM work_reservation_sets s LEFT JOIN work_path_reservations p ON p.reservation_set_id=s.id WHERE s.attempt_id=? AND s.generation=? GROUP BY s.id",
            (attempt["id"], attempt["generation"]),
        ).fetchone()
        if row:
            reservations = dict(
                zip(("id", "state", "revision", "stop_evidence_ref", "path_count"), row)
            )
    cleanup = attempt["cleanup_state"] if attempt else "not_requested"
    retained = bool(capacity and capacity["state"] == "held") or bool(
        reservations and reservations["state"] == "active"
    )
    action = "inspect_work_evidence"
    if not allowed:
        action = "restore_remains_blocked"
    elif retained and (expired or work["state"] == "reconcile" or cleanup in {"pending", "failed"}):
        action = "reconcile_stop_and_cleanup_proof"
    if allowed and cleanup in {"pending", "failed"} and action == "inspect_work_evidence":
        action = "reconcile_cleanup_proof"
    elif allowed and expired and action == "inspect_work_evidence":
        action = "inspect_expired_attempt_stop_proof"
    actions = [
        {
            "kind": "inspect_work",
            "required_scopes": [SCOPE_READ, SCOPE_WRITE, SCOPE_ADMIN],
            "method": "GET",
            "path": "/work-items/" + quote(work["id"], safe=""),
            "requires_current_validation": False,
        }
    ]
    controller = None
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    # The external workflow journal is optional in a standalone Work store.
    # Presence is not ownership: only a sealed scoped-run owner gets its routes.
    if (
        attempt
        and {
            "workflow_scoped_run",
            "workflow_run",
            "workflow_coordinator",
            "workflow_run_step",
            "workflow_driver",
        }
        <= tables
    ):
        row = connection.execute(
            "SELECT b.run_id,b.step_id,b.run_generation,b.workflow_step_attempt,r.state,r.generation,s.state,s.attempts,c.id,c.state,d.state,d.lease_expires_at,d.plan_id,d.source_hash "
            "FROM work_workflow_step_bindings b JOIN workflow_scoped_run o ON o.run_id=b.run_id "
            "JOIN workflow_run r ON r.run_id=b.run_id LEFT JOIN workflow_run_step s ON s.run_id=b.run_id AND s.step_id=b.step_id "
            "LEFT JOIN workflow_coordinator c ON c.run_id=b.run_id AND c.owner_principal_id=o.principal_id "
            "LEFT JOIN workflow_driver d ON d.run_id=b.run_id AND d.owner_principal_id=o.principal_id "
            "WHERE b.work_item_id=? AND b.work_attempt_id=? AND b.work_generation=? AND o.principal_id=?",
            (work["id"], attempt["id"], attempt["generation"], principal.id),
        ).fetchone()
        if row:
            (
                run,
                step,
                generation,
                step_attempt,
                run_state,
                current_generation,
                step_state,
                current_step_attempt,
                coordinator_id,
                coordinator_state,
                driver_state,
                driver_lease,
                plan_id,
                source_hash,
            ) = row
            controller = {
                "run_id": run,
                "run_generation": current_generation,
                "coordinator_id": coordinator_id,
                "state": coordinator_state or run_state,
                "driver_state": driver_state,
                "driver_lease_expired": driver_lease is not None and driver_lease <= time.time(),
                "plan_id": plan_id,
                "source_hash": source_hash,
            }
            exact = (
                str(generation) == str(current_generation) and step_attempt == current_step_attempt
            )
            if (
                allowed
                and coordinator_id
                and coordinator_state in {"paused", "ready", "waiting", "running"}
                and driver_state in {"paused", "ready", "waiting"}
            ):
                actions.append(
                    {
                        "kind": "resume_workflow",
                        "required_scopes": [SCOPE_WRITE, SCOPE_ADMIN],
                        "method": "POST",
                        "path": "/workflows/runs/" + quote(run, safe="") + "/resume",
                        "requires_current_validation": True,
                    }
                )
            if (
                allowed
                and exact
                and run_state == "running"
                and step_state == "failed"
                and attempt["state"] == "failed"
                and SCOPE_ADMIN in principal.scopes
            ):
                actions.append(
                    {
                        "kind": "retry_managed_step",
                        "required_scopes": [SCOPE_ADMIN],
                        "method": "POST",
                        "path": "/workflows/runs/"
                        + quote(run, safe="")
                        + "/steps/"
                        + quote(step, safe="")
                        + "/retry",
                        "requires_current_validation": True,
                        "expectations": {
                            "run_generation": generation,
                            "workflow_step_attempt": step_attempt,
                            "work_attempt_id": attempt["id"],
                            "work_generation": attempt["generation"],
                        },
                    }
                )
    return {
        "schema_version": 1,
        "job_id": job["id"],
        "work_item_id": work["id"],
        "revision": work["revision"],
        "attempt_id": attempt["id"] if attempt else None,
        "generation": attempt["generation"] if attempt else None,
        "work_state": work["state"],
        "attempt_state": attempt["state"] if attempt else None,
        "execution_state": context.execution_state,
        "execution_allowed": allowed,
        "lease_expired": expired,
        "process_state": "unknown",
        "cleanup_state": cleanup,
        "capacity": capacity,
        "reservations": reservations,
        "release_allowed": False,
        "reactivation_allowed": False,
        "required_action": action,
        "controller": controller,
        "actions": actions,
    }


def runtime_operations(principal, engine, registry, *, limit=100, after=""):
    """Global durable node inventory has no owner column: require verified admin."""
    if not is_verified_principal(principal) or SCOPE_ADMIN not in principal.scopes:
        raise PermissionError("verified administrator required")
    if (
        type(limit) is not int
        or not 1 <= limit <= 100
        or not isinstance(after, str)
        or len(after) > 512
    ):
        raise ValueError("bounded runtime cursor required")
    runtimes = RemoteRuntimeModel.__table__
    operations = RemoteOperationModel.__table__
    with engine.connect() as connection, connection.begin():
        rows = (
            connection.execute(
                select(runtimes)
                .where(runtimes.c.runtime_id > after)
                .order_by(runtimes.c.runtime_id)
                .limit(limit + 1)
            )
            .mappings()
            .all()
        )
        selected = rows[:limit]
        counts = {}
        if selected:
            counts = dict(
                connection.execute(
                    select(operations.c.runtime_id, func.count())
                    .where(
                        operations.c.runtime_id.in_([r["runtime_id"] for r in selected]),
                        operations.c.state.in_(["prepared", "dispatching", "reconcile"]),
                    )
                    .group_by(operations.c.runtime_id)
                ).all()
            )
        nodes = []
        for row in selected:
            observed = registry.observe_runtime(
                row["runtime_id"], row["incarnation_id"], row["connection_epoch"]
            )
            # Existing inspection is addressed by op_id. Publish only bounded
            # coordinates so a lost response can be reconciled without replay.
            pending = (
                connection.execute(
                    select(operations.c.op_id, operations.c.incarnation_id, operations.c.state)
                    .where(
                        operations.c.runtime_id == row["runtime_id"],
                        operations.c.state.in_(["prepared", "dispatching", "reconcile"]),
                    )
                    .order_by(operations.c.deadline, operations.c.op_id)
                    .limit(5)
                )
                .mappings()
                .all()
            )
            samples = [
                {
                    "op_id": item["op_id"],
                    "incarnation_id": item["incarnation_id"],
                    "state": item["state"],
                    "inspect_path": "/runtimes/"
                    + quote(row["runtime_id"], safe="")
                    + "/operations/"
                    + quote(item["op_id"], safe=""),
                }
                for item in pending
            ]
            nodes.append(
                {
                    "runtime_id": row["runtime_id"],
                    "incarnation_id": row["incarnation_id"],
                    "connection_epoch": row["connection_epoch"],
                    "connection_state": observed,
                    "unresolved_operation_count": counts.get(row["runtime_id"], 0),
                    "unresolved_operations": samples,
                    "operations_truncated": counts.get(row["runtime_id"], 0) > len(samples),
                    "required_action": (
                        "inspect_exact_operation_evidence"
                        if observed == "connected"
                        else "reconnect_authenticated_current_incarnation_and_inspect"
                    ),
                    "automatic_replay_allowed": False,
                    "protected_work_supported": False,
                }
            )
    return {
        "schema_version": 1,
        "nodes": nodes,
        "next_cursor": selected[-1]["runtime_id"] if len(rows) > limit else None,
    }
