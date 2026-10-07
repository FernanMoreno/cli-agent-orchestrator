"""External task assignments correlate approved Work runs without new authority."""

from __future__ import annotations

import hashlib
import re
import time
from contextlib import contextmanager

from cli_agent_orchestrator.services import beads_service as beads
from cli_agent_orchestrator.services.work_coordinator import canonical


class BeadsAssignments:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.repository = coordinator.repository

    @contextmanager
    def _lock(self, workspace):
        from cli_agent_orchestrator import constants
        from cli_agent_orchestrator.services.work_terminal import terminal_dispatch_lock

        identity = "beads-" + hashlib.sha256(str(workspace.root).encode()).hexdigest()
        with terminal_dispatch_lock(constants.DATABASE_FILE, identity):
            yield

    def _material(self, workspace_id, task_id):
        from cli_agent_orchestrator.clients.beads import (
            _ancestors,
            resolve_context_files,
            resolve_workspace,
        )

        workspace, adapter = beads.client_for(workspace_id)
        task = adapter.get(task_id)
        if task is None:
            raise LookupError("beads_task_missing")
        if task.status not in ("open", "wip"):
            raise beads.BeadsConflict("beads_task_not_ready")
        for dependency in task.blocked_by:
            prior = adapter.get(dependency)
            if prior is None or prior.status != "closed":
                raise beads.BeadsConflict("beads_dependency_not_ready")
        material = {
            "task": beads.task_dto(task),
            "ancestors": [beads.task_dto(row) for row in _ancestors(task, adapter)],
            "context_files": resolve_context_files(task, adapter),
            "working_directory": resolve_workspace(task, adapter, str(workspace.root)),
        }
        if len(canonical(material).encode()) > 8192:
            raise ValueError("beads_task_material_limit")
        return workspace, material

    @staticmethod
    def _workspace_identity(workspace):
        return canonical([workspace.revision, *workspace.identity])

    def _row(self, connection, principal, identity, *, read_only=False):
        if read_only:
            from cli_agent_orchestrator.security.auth import (
                SCOPE_ADMIN,
                SCOPE_READ,
                SCOPE_WRITE,
                is_verified_principal,
            )

            if not is_verified_principal(principal) or not principal.scopes & {
                SCOPE_READ,
                SCOPE_WRITE,
                SCOPE_ADMIN,
            }:
                raise PermissionError("beads_read_authority_required")
        else:
            self.coordinator.plans._owner(principal)
        row = connection.execute(
            "SELECT * FROM beads_work_bindings WHERE binding_id=?", (identity,)
        ).fetchone()
        if row is None or row["owner"] != principal.id:
            raise LookupError("beads_binding_missing")
        return row

    def prepare(
        self,
        principal,
        workspace_id,
        task_id,
        *,
        operation_key,
        expected_hash,
        workflow_name,
        criteria,
        binding_selections,
        scan_dir=None,
        **bounds,
    ):
        self.coordinator.plans._owner(principal)
        if (
            not isinstance(operation_key, str)
            or re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", operation_key) is None
        ):
            raise ValueError("beads_operation_key_invalid")
        identity = (
            "beads_work_"
            + hashlib.sha256(
                canonical([principal.id, workspace_id, task_id, operation_key]).encode()
            ).hexdigest()
        )
        request_hash = hashlib.sha256(
            canonical([expected_hash, workflow_name, criteria, binding_selections, bounds]).encode()
        ).hexdigest()
        with self.repository.read_snapshot() as connection:
            existing = connection.execute(
                "SELECT request_hash FROM beads_work_bindings WHERE binding_id=?", (identity,)
            ).fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise beads.BeadsConflict("beads_assignment_material_changed")
            return self.status(principal, identity)
        workspace, material = self._material(workspace_id, task_id)
        with self._lock(workspace):
            workspace, material = self._material(workspace_id, task_id)
            if material["task"]["material_hash"] != expected_hash:
                raise beads.BeadsConflict("beads_task_material_changed")
            prepared = self.coordinator.prepare(
                principal,
                workflow_name,
                material,
                criteria,
                {"repo": material["working_directory"]},
                binding_selections,
                scan_dir=scan_dir,
                mode="beads",
                external_binding_ref=identity,
                **bounds,
            )
            with self.repository.transaction() as connection:
                prior = connection.execute(
                    "SELECT request_hash FROM beads_work_bindings WHERE binding_id=?", (identity,)
                ).fetchone()
                if prior:
                    if prior["request_hash"] != request_hash:
                        raise beads.BeadsConflict("beads_assignment_material_changed")
                else:
                    connection.execute(
                        "INSERT INTO beads_work_bindings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            identity,
                            principal.id,
                            workspace_id,
                            task_id,
                            self._workspace_identity(workspace),
                            hashlib.sha256(canonical(material).encode()).hexdigest(),
                            canonical(material),
                            prepared["prepared_id"],
                            prepared["plan_id"],
                            None,
                            None,
                            operation_key,
                            request_hash,
                            "prepared",
                            1,
                            time.time(),
                        ),
                    )
        return self.status(principal, identity)

    def status(self, principal, identity):
        with self.repository.read_snapshot() as connection:
            row = dict(self._row(connection, principal, identity, read_only=True))
        value = {
            key: row[key]
            for key in (
                "binding_id",
                "workspace_id",
                "task_id",
                "prepared_id",
                "plan_id",
                "run_id",
                "coordinator_id",
                "state",
                "revision",
            )
        }
        value["work_verified_completed"] = False
        if row["coordinator_id"]:
            coordinator = self.coordinator.status(principal, row["coordinator_id"])
            value["coordinator"] = coordinator
            from cli_agent_orchestrator.security.auth import SCOPE_ADMIN, SCOPE_WRITE

            can_revalidate = bool(principal.scopes & {SCOPE_WRITE, SCOPE_ADMIN})
            if coordinator["work_verified_completed"] and can_revalidate:
                try:
                    self.coordinator.complete(principal, row["coordinator_id"])
                except (ValueError, PermissionError, LookupError):
                    coordinator["work_verified_completed"] = False
            value["work_verified_completed"] = coordinator["work_verified_completed"]
            if value["work_verified_completed"] and can_revalidate:
                with self.repository.transaction() as connection:
                    self._row(connection, principal, identity)
                    connection.execute(
                        "UPDATE beads_work_bindings SET state='completed',revision=revision+1 WHERE binding_id=? AND state='assigned'",
                        (identity,),
                    )
            value["state"] = (
                "completed" if value["work_verified_completed"] else coordinator["state"]
            )
        return value

    def describe_tasks(self, principal, workspace_id, tasks):
        """Project owner-scoped Work evidence without altering task material or authority."""
        import json

        from cli_agent_orchestrator.security.auth import (
            SCOPE_ADMIN,
            SCOPE_READ,
            SCOPE_WRITE,
            is_verified_principal,
        )

        if not is_verified_principal(principal) or not principal.scopes & {
            SCOPE_READ,
            SCOPE_WRITE,
            SCOPE_ADMIN,
        }:
            raise PermissionError("beads_read_authority_required")
        values = [dict(task) for task in tasks]
        if len(values) > 4096:
            raise ValueError("beads_task_projection_limit")
        workspace, _ = beads.client_for(workspace_id)
        workspace_identity = self._workspace_identity(workspace)
        for task in values:
            task["work_verified_completed"] = False
            task["work_assignment"] = None
            task["work_attempts"] = []
            with self.repository.read_snapshot() as connection:
                row = connection.execute(
                    "SELECT * FROM beads_work_bindings WHERE owner=? AND workspace_id=? AND task_id=? "
                    "ORDER BY created_at DESC,binding_id DESC LIMIT 1",
                    (principal.id, workspace_id, task["id"]),
                ).fetchone()
                if row is None:
                    continue
                row = dict(row)
                if row["workspace_identity"] != workspace_identity:
                    continue
                attempts = (
                    connection.execute(
                        "SELECT a.id,a.terminal_id,a.state,a.generation,b.step_id,b.run_generation "
                        "FROM work_workflow_step_bindings b JOIN work_attempts a ON a.id=b.work_attempt_id "
                        "WHERE b.run_id=? ORDER BY b.run_generation,b.workflow_step_attempt,a.id LIMIT 65",
                        (row["run_id"],),
                    ).fetchall()
                    if row["run_id"]
                    else []
                )
            task["work_assignment"] = {
                key: row[key]
                for key in (
                    "binding_id",
                    "prepared_id",
                    "plan_id",
                    "run_id",
                    "coordinator_id",
                    "state",
                )
            }
            task["work_attempts"] = [dict(attempt) for attempt in attempts[:64]]
            task["work_attempts_truncated"] = len(attempts) > 64
            if row["coordinator_id"]:
                status = self.coordinator.status(principal, row["coordinator_id"])
                task["work_assignment"]["state"] = status["state"]
                # A completed historical run must not certify subsequently edited task content.
                frozen_task = json.loads(row["material_json"])["task"]
                same_content = all(
                    task.get(key) == value
                    for key, value in frozen_task.items()
                    if key not in ("material_hash", "external_status", "work_verified_completed")
                )
                task["work_verified_completed"] = bool(
                    status["work_verified_completed"] and same_content
                )
        return values

    def start(self, principal, identity, expected_plan_id, run_id, *, scan_dir=None):
        with self.repository.read_snapshot() as connection:
            row = dict(self._row(connection, principal, identity))
        if row["plan_id"] != expected_plan_id:
            raise beads.BeadsConflict("beads_assignment_plan_changed")
        if row["run_id"]:
            if row["run_id"] != run_id:
                raise beads.BeadsConflict("beads_assignment_run_changed")
            return self.status(principal, identity)
        workspace, material = self._material(row["workspace_id"], row["task_id"])
        with self._lock(workspace):
            workspace, material = self._material(row["workspace_id"], row["task_id"])
            if (
                self._workspace_identity(workspace) != row["workspace_identity"]
                or hashlib.sha256(canonical(material).encode()).hexdigest() != row["material_hash"]
            ):
                raise beads.BeadsConflict("beads_assignment_material_changed")

            def attach(connection, coordinator_id, attached_run):
                current = self._row(connection, principal, identity)
                if current["state"] != "prepared" or current["run_id"] is not None:
                    raise beads.BeadsConflict("beads_assignment_already_started")
                count = connection.execute(
                    "SELECT COUNT(*) FROM beads_work_bindings WHERE workspace_id=? AND state IN ('assigned','stopping','reconcile')",
                    (row["workspace_id"],),
                ).fetchone()[0]
                if connection.execute(
                    "SELECT 1 FROM beads_work_bindings WHERE workspace_id=? AND task_id=? AND state IN ('assigned','stopping','reconcile')",
                    (row["workspace_id"], row["task_id"]),
                ).fetchone():
                    raise beads.BeadsConflict("beads_task_already_assigned")
                if count >= 32:
                    raise beads.BeadsConflict("beads_workspace_capacity")
                connection.execute(
                    "UPDATE beads_work_bindings SET coordinator_id=?,run_id=?,state='assigned',revision=revision+1 WHERE binding_id=?",
                    (coordinator_id, attached_run, identity),
                )

            started = self.coordinator.start(
                principal,
                row["prepared_id"],
                expected_plan_id,
                run_id,
                scan_dir=scan_dir,
                attach_callback=attach,
            )
        return {**self.status(principal, identity), "_prepared": started["prepared"]}

    async def unassign(self, principal, identity):
        with self.repository.read_snapshot() as connection:
            row = dict(self._row(connection, principal, identity))
        if not row["coordinator_id"]:
            return self.status(principal, identity)
        # Fence later effects before requesting actual termination. Unknown stop
        # retains the active binding, preventing a second worker on the task.
        with self.repository.transaction() as connection:
            self._row(connection, principal, identity)
            connection.execute(
                "UPDATE beads_work_bindings SET state='stopping',revision=revision+1 WHERE binding_id=? AND state='assigned'",
                (identity,),
            )
        result = await self.coordinator.stop(principal, row["coordinator_id"])
        if result["state"] == "stopped":
            with self.repository.transaction() as connection:
                connection.execute(
                    "UPDATE beads_work_bindings SET state='stopped',revision=revision+1 WHERE binding_id=? AND state='stopping'",
                    (identity,),
                )
        return self.status(principal, identity)
