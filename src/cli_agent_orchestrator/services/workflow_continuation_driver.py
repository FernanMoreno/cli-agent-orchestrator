"""Transactional result wakeups and one fenced owner for explicitly enabled runs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from cli_agent_orchestrator.clients.work_continuation_schema import initialize, verify


class DriverRefused(ValueError):
    pass


def enqueue_projected(connection, binding, result_id, content_hash):
    if (
        connection.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_driver'").fetchone()
        is None
    ):
        return
    driver = connection.execute(
        "SELECT state FROM workflow_driver WHERE run_id=?", (binding.run_id,)
    ).fetchone()
    if driver is None or driver["state"] in ("stopping", "stopped"):
        return
    identity = hashlib.sha256((binding.binding_id + "\0" + result_id).encode()).hexdigest()
    connection.execute(
        "INSERT OR IGNORE INTO workflow_continuation_outbox VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            identity,
            binding.binding_id,
            binding.run_id,
            str(binding.run_generation),
            binding.step_id,
            binding.workflow_step_attempt,
            result_id,
            content_hash,
            "ready",
            1,
            None,
            time.time(),
        ),
    )


def process_identity(pid):
    """Boot/start-time/executable tuple; PID alone never proves ownership."""
    proc = Path("/proc") / str(pid)
    stat = proc.joinpath("stat").read_text()
    fields = stat[stat.rfind(")") + 2 :].split()
    return {
        "pid": pid,
        "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "start": fields[19],
        "exe": str(proc.joinpath("exe").resolve(strict=True)),
    }


def process_stopped(identity_json):
    if identity_json is None:
        return True
    try:
        identity = json.loads(identity_json)
        if set(identity) != {"pid", "boot", "start", "exe"} or type(identity["pid"]) is not int:
            return False
        return process_identity(identity["pid"]) != identity
    except FileNotFoundError:
        return True
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return False


class WorkflowContinuationDriver:
    def __init__(self, plans, projector, *, instance_id=None):
        if projector.repository is not plans.repository:
            raise ValueError("continuation repository ownership mismatch")
        self.plans = plans
        self.repository = plans.repository
        self.projector = projector
        self.instance_id = instance_id or uuid4().hex
        self.principals = {}
        self.tasks = {}
        self.stopping = False
        self.coordinator = None
        self._run_cursor = ""
        self._stop_cursor = ""
        with self.repository.transaction() as connection:
            initialize(connection)
        plans.continuation_driver = self

    def enable(self, principal, run_id, *, connection=None):
        self.plans._owner(principal)
        if connection is None:
            self.plans.validate_resume(principal, run_id)
            with self.repository.transaction() as conn:
                self.enable(principal, run_id, connection=conn)
            return
        row, snapshot = self.plans._stored(connection, run_id)
        if row["principal_id"] != principal.id:
            raise DriverRefused("driver_owner_mismatch")
        run = connection.execute(
            "SELECT generation FROM workflow_run WHERE run_id=?", (run_id,)
        ).fetchone()
        connection.execute(
            "INSERT INTO workflow_driver VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                principal.id,
                None,
                1,
                1,
                run["generation"],
                0,
                time.time(),
                "ready",
                row["source_hash"],
                row["plan_id"],
                None,
                None,
                None,
            ),
        )
        self.principals[run_id] = principal

    def owner(self, run_id):
        with self.repository.read_snapshot() as conn:
            row = conn.execute(
                "SELECT owner_principal_id FROM workflow_driver WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise DriverRefused("driver_missing")
        from cli_agent_orchestrator.security import auth

        principal = None if auth.is_auth_enabled() else self.principals.get(run_id)
        if principal is None:
            token = auth.get_local_bearer()
            try:
                principal = (
                    auth.principal_from_token(token) if token else auth.local_operator_principal()
                )
            except Exception as error:
                raise DriverRefused("owner_authentication_required") from error
        self.plans._owner(principal)
        if principal.id != row["owner_principal_id"]:
            raise DriverRefused("owner_authentication_required")
        return principal

    def claim(self, principal, run_id, *, ttl=120):
        if self.stopping:
            raise DriverRefused("driver_service_stopping")
        self.plans.validate_resume(principal, run_id)
        with self.repository.transaction() as conn:
            verify(conn)
            row = conn.execute("SELECT * FROM workflow_driver WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise DriverRefused("driver_missing")
            if row["owner_principal_id"] != principal.id:
                raise DriverRefused("driver_owner_mismatch")
            if row["state"] in ("stopping", "stopped"):
                raise DriverRefused("driver_stopping")
            now = time.time()
            if row["state"] == "driving" and row["lease_expires_at"] > now:
                raise DriverRefused("driver_owned")
            if not process_stopped(row["process_identity_json"]):
                raise DriverRefused("orphan_process_reconciliation_required")
            run = conn.execute(
                "SELECT generation,state FROM workflow_run WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None or run["state"] in ("cancelled", "completed"):
                raise DriverRefused("driver_terminal")
            epoch = row["epoch"] + 1
            changed = conn.execute(
                "UPDATE workflow_driver SET owner_instance=?,epoch=?,revision=revision+1,run_generation=?,lease_expires_at=?,heartbeat_at=?,state='driving',pause_reason=NULL WHERE run_id=? AND revision=?",
                (
                    self.instance_id,
                    epoch,
                    run["generation"],
                    now + ttl,
                    now,
                    run_id,
                    row["revision"],
                ),
            )
            if changed.rowcount != 1:
                raise DriverRefused("driver_owned")
        self.principals[run_id] = principal
        return epoch

    def assert_current(self, run_id, epoch, *, connection=None):
        if connection is None:
            with self.repository.read_snapshot() as conn:
                return self.assert_current(run_id, epoch, connection=conn)
        verify(connection)
        row = connection.execute(
            "SELECT * FROM workflow_driver WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise DriverRefused("driver_missing")
        if row["state"] in ("stopping", "stopped"):
            raise DriverRefused("driver_stopping")
        if (
            row["owner_instance"] != self.instance_id
            or row["epoch"] != epoch
            or row["state"] != "driving"
            or row["lease_expires_at"] <= time.time()
        ):
            raise DriverRefused("driver_fence_changed")
        return row

    def heartbeat(self, run_id, epoch):
        with self.repository.transaction() as conn:
            self.assert_current(run_id, epoch, connection=conn)
            conn.execute(
                "UPDATE workflow_driver SET lease_expires_at=?,heartbeat_at=?,revision=revision+1 WHERE run_id=? AND epoch=?",
                (time.time() + 120, time.time(), run_id, epoch),
            )

    def begin_process(self, run_id, epoch):
        with self.repository.transaction() as conn:
            self.assert_current(run_id, epoch, connection=conn)
            conn.execute(
                "UPDATE workflow_driver SET process_identity_json=?,stop_evidence_ref=NULL,revision=revision+1 WHERE run_id=?",
                ('{"allocation_pending":true}', run_id),
            )

    def attach_process(self, run_id, epoch, pid):
        identity = json.dumps(process_identity(pid), sort_keys=True)
        with self.repository.transaction() as conn:
            self.assert_current(run_id, epoch, connection=conn)
            conn.execute(
                "UPDATE workflow_driver SET process_identity_json=?,stop_evidence_ref=NULL,revision=revision+1 WHERE run_id=?",
                (identity, run_id),
            )

    def record_process_exit(self, run_id, epoch, process):
        if process.returncode is None:
            return
        with self.repository.transaction() as conn:
            row = conn.execute(
                "SELECT epoch,owner_instance FROM workflow_driver WHERE run_id=?", (run_id,)
            ).fetchone()
            if row and row["epoch"] == epoch and row["owner_instance"] == self.instance_id:
                conn.execute(
                    "UPDATE workflow_driver SET process_identity_json=NULL,stop_evidence_ref=?,revision=revision+1 WHERE run_id=?",
                    ("owned-process-reaped:" + str(process.returncode), run_id),
                )

    def release(self, run_id, epoch, *, state="waiting", reason=None):
        with self.repository.transaction() as conn:
            conn.execute(
                "UPDATE workflow_driver SET state=?,pause_reason=?,lease_expires_at=0,revision=revision+1 WHERE run_id=? AND epoch=? AND owner_instance=? AND state=?",
                (state, reason, run_id, epoch, self.instance_id, "driving"),
            )

    def request_stop(self, principal, run_id):
        self.plans._owner(principal)
        with self.repository.transaction() as conn:
            row = conn.execute(
                "SELECT owner_principal_id FROM workflow_driver WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None or row[0] != principal.id:
                raise DriverRefused("driver_owner_mismatch")
            conn.execute(
                "UPDATE workflow_driver SET state='stopping',epoch=epoch+1,lease_expires_at=0,revision=revision+1 WHERE run_id=? AND state!='stopped'",
                (run_id,),
            )
            conn.execute(
                "UPDATE workflow_continuation_outbox SET state='cancelled',revision=revision+1 WHERE run_id=? AND state IN ('ready','claimed','blocked')",
                (run_id,),
            )

    def pause(self, run_id, reason, *, epoch=None):
        with self.repository.transaction() as conn:
            row = conn.execute("SELECT * FROM workflow_driver WHERE run_id=?", (run_id,)).fetchone()
            if row is None or row["state"] in ("stopping", "stopped"):
                return False
            if epoch is not None:
                if row["owner_instance"] != self.instance_id or row["epoch"] != epoch:
                    return False
            elif (
                row["state"] == "driving" and row["lease_expires_at"] > time.time()
            ) or not process_stopped(row["process_identity_json"]):
                # An observer's authority/refusal is never evidence that another
                # owner's execution stopped or that its lease may be revoked.
                return False
            changed = conn.execute(
                "UPDATE workflow_driver SET state='paused',pause_reason=?,lease_expires_at=0,revision=revision+1 WHERE run_id=? AND revision=?",
                (reason, run_id, row["revision"]),
            )
            return changed.rowcount == 1

    def attach_record(self, record, epoch):
        record.continuation_driver = self
        record.continuation_epoch = epoch

    async def drive(self, principal, run_id, *, prepared=None, epoch=None):
        from cli_agent_orchestrator.models.workflow import RunState
        from cli_agent_orchestrator.services import (
            script_runner,
            workflow_journal,
            workflow_service,
        )

        epoch = epoch or await asyncio.to_thread(self.claim, principal, run_id)

        async def heartbeat():
            while True:
                await asyncio.sleep(20)
                await asyncio.to_thread(self.heartbeat, run_id, epoch)

        pulse = asyncio.create_task(heartbeat())
        try:
            if prepared is not None:
                path = await asyncio.to_thread(
                    script_runner._materialize_snapshot, run_id, prepared.spec.source
                )
                record = script_runner.ScriptRunRecord(
                    run_id=run_id,
                    workflow_name=prepared.spec.name,
                    state=RunState.RUNNING,
                    cancelled=False,
                    current_step_id=None,
                    step_states={},
                    process=None,
                    generation="1",
                    started_at=prepared.started_at,
                    finished_at=None,
                    tier="script",
                    run_capability_required=True,
                )
                record.scoped_plan_owner = self.plans
                record.scoped_principal = principal
                self.attach_record(record, epoch)
                workflow_service.run_registry[run_id] = record
                try:
                    result = await script_runner.run_script_workflow_prepared(
                        record,
                        path,
                        script_runner.build_env(run_id, "1", prepared.inputs, resume=False),
                        run_credential=prepared.run_credential,
                    )
                finally:
                    await asyncio.to_thread(script_runner._delete_temp_file, path)
            else:
                row = await asyncio.to_thread(workflow_journal.get_run, run_id)

                def credential(rid, generation):
                    return self.plans.origins.create_run_capability(
                        principal,
                        run_id=rid,
                        workflow_id=row.workflow_name,
                        tier=row.tier,
                        run_generation=int(generation),
                        spec_hash=json.loads(row.spec_snapshot)["content_hash"],
                        ttl_seconds=3600,
                    )

                result = await script_runner.resume_script_run(
                    run_id,
                    run_credential_factory=credential,
                    scoped_plan_owner=self.plans,
                    scoped_principal=principal,
                    continuation_driver=self,
                    continuation_epoch=epoch,
                )
            if self.coordinator:
                await asyncio.to_thread(self.coordinator.observe_run, run_id, result)
            return result
        finally:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)
            await asyncio.to_thread(self.release, run_id, epoch)

    def _read_rows(self, query, parameters=()):
        # Open, verify, read and close SQLite on the same worker thread. Never
        # carry a connection across an await or weaken repository verification.
        with self.repository.read_snapshot() as conn:
            return conn.execute(query, parameters).fetchall()

    def _write(self, query, parameters=(), *, run_id=None, epoch=None):
        with self.repository.transaction() as conn:
            if run_id is not None:
                self.assert_current(run_id, epoch, connection=conn)
            conn.execute(query, parameters)

    def _cleanup_task(self, run_id, task):
        if not task.done():
            return
        if not task.cancelled():
            error = task.exception()  # Retrieve failures before releasing the handle.
            if error is not None:
                import logging

                logging.getLogger(__name__).warning(
                    "Continuation task failed: %s",
                    type(error).__name__,
                )
        # A late callback for an old generation must not remove its successor.
        if self.tasks.get(run_id) is task:
            del self.tasks[run_id]

    def _track_task(self, run_id, task):
        self.tasks[run_id] = task
        task.add_done_callback(lambda finished: self._cleanup_task(run_id, finished))

    async def _page_runs(self, *, stopping=False):
        cursor_name = "_stop_cursor" if stopping else "_run_cursor"
        state_filter = "state='stopping'" if stopping else "state NOT IN ('stopping','stopped')"
        rows = await asyncio.to_thread(
            self._read_rows,
            f"SELECT run_id FROM workflow_driver WHERE {state_filter} AND run_id>? ORDER BY run_id LIMIT 128",
            (getattr(self, cursor_name),),
        )
        setattr(self, cursor_name, rows[-1][0] if rows else "")
        return rows

    async def tick(self):
        if self.stopping:
            return
        # Coordinator-started tasks also enter this shared registry. Reap them
        # here even if they were created outside the driver's callback helper.
        for run_id, task in list(self.tasks.items()):
            self._cleanup_task(run_id, task)
        rows = await self._page_runs()
        runs = [row[0] for row in rows]
        if self.coordinator:
            rows = await self._page_runs(stopping=True)
            for row in rows:
                run_id = row[0]
                try:
                    principal = await asyncio.to_thread(self.owner, run_id)
                    await asyncio.to_thread(self.coordinator.reconcile_stop, principal, run_id)
                except Exception:
                    pass  # Remain visibly stopping until verified stop evidence.
        for run_id in runs:
            if self.coordinator:
                rows = await asyncio.to_thread(
                    self._read_rows,
                    "SELECT state,deadline FROM workflow_coordinator WHERE run_id=?",
                    (run_id,),
                )
                controller = rows[0] if rows else None
                if (
                    controller is not None
                    and controller["state"] not in ("completed", "stopped", "stopping", "escalated")
                    and time.time() >= controller["deadline"]
                ):
                    try:
                        principal = await asyncio.to_thread(self.owner, run_id)
                        await self.coordinator.stop(principal, run_id)
                        await asyncio.to_thread(
                            self._write,
                            "UPDATE workflow_coordinator SET escalation_reason='deadline',revision=revision+1 WHERE run_id=?",
                            (run_id,),
                        )
                    except Exception:
                        await asyncio.to_thread(self.pause, run_id, "deadline_authority_refused")
                    continue
            if run_id in self.tasks and not self.tasks[run_id].done():
                continue
            try:
                await asyncio.to_thread(self.projector.project_pending_for_run, run_id)
                rows = await asyncio.to_thread(
                    self._read_rows,
                    "SELECT * FROM workflow_continuation_outbox WHERE run_id=? AND state IN ('ready','claimed') ORDER BY created_at,id LIMIT 1",
                    (run_id,),
                )
                event = rows[0] if rows else None
                from cli_agent_orchestrator.services import workflow_journal

                if event is None:
                    rows = await asyncio.to_thread(
                        self._read_rows,
                        "SELECT state FROM workflow_driver WHERE run_id=?",
                        (run_id,),
                    )
                    if (
                        rows
                        and rows[0][0] == "ready"
                        and not await asyncio.to_thread(workflow_journal.get_steps, run_id)
                    ):
                        principal = await asyncio.to_thread(self.owner, run_id)
                        epoch = await asyncio.to_thread(self.claim, principal, run_id)
                        self._track_task(
                            run_id,
                            asyncio.create_task(self.drive(principal, run_id, epoch=epoch)),
                        )
                    continue
                steps = await asyncio.to_thread(workflow_journal.get_steps, run_id)
                # A prior driver's next step was already durably attached. This
                # wake is consumed without respawning or retrying pending Work.
                if any(
                    step.step_id != event["step_id"] and step.state == "work_pending"
                    for step in steps
                ):
                    await asyncio.to_thread(
                        self._write,
                        "UPDATE workflow_continuation_outbox SET state='consumed',revision=revision+1 WHERE id=?",
                        (event["id"],),
                    )
                    continue
                principal = await asyncio.to_thread(self.owner, run_id)
                if self.coordinator and not await asyncio.to_thread(
                    self.coordinator.checkpoint, principal, run_id, event
                ):
                    continue
                epoch = await asyncio.to_thread(self.claim, principal, run_id)
                await asyncio.to_thread(
                    self._write,
                    "UPDATE workflow_continuation_outbox SET state='claimed',claim_epoch=?,revision=revision+1 WHERE id=? AND state IN ('ready','claimed')",
                    (epoch, event["id"]),
                    run_id=run_id,
                    epoch=epoch,
                )

                async def owned(
                    principal=principal, run_id=run_id, epoch=epoch, event_id=event["id"]
                ):
                    try:
                        result = await self.drive(principal, run_id, epoch=epoch)
                        await asyncio.to_thread(
                            self._write,
                            "UPDATE workflow_continuation_outbox SET state='consumed',revision=revision+1 WHERE id=? AND state='claimed' AND claim_epoch=?",
                            (event_id, epoch),
                        )
                        return result
                    except Exception as error:
                        import logging

                        logging.getLogger(__name__).warning(
                            "Owned continuation refused: %s", type(error).__name__
                        )
                        await asyncio.to_thread(
                            self.pause, run_id, type(error).__name__, epoch=epoch
                        )

                self._track_task(run_id, asyncio.create_task(owned()))
            except Exception as error:
                await asyncio.to_thread(
                    self.pause,
                    run_id,
                    (
                        str(error)
                        if isinstance(error, DriverRefused)
                        else "continuation_authority_refused"
                    ),
                )

    async def serve(self):
        while not self.stopping:
            await self.tick()
            await asyncio.sleep(1)

    async def shutdown(self):
        self.stopping = True

        # Fence only this instance's active admissions before awaiting process
        # termination. Another owner is untouched; restart still needs exact
        # process-stop proof before acquiring the paused execution.
        def fence_owned():
            with self.repository.transaction() as conn:
                verify(conn)
                conn.execute(
                    "UPDATE workflow_driver SET state='paused',pause_reason='service_shutdown',epoch=epoch+1,lease_expires_at=0,revision=revision+1 WHERE owner_instance=? AND state='driving'",
                    (self.instance_id,),
                )

        await asyncio.to_thread(fence_owned)
        # Owned subprocess termination is handled by the existing runner; no
        # controller lease expiry substitutes for Work cessation proof.
        from cli_agent_orchestrator.services import script_runner, workflow_service

        for run_id, task in list(self.tasks.items()):
            record = workflow_service.run_registry.get(run_id)
            if record and getattr(record, "process", None) and record.process.returncode is None:
                await script_runner._terminate(
                    record.process, script_runner.WORKFLOW_SCRIPT_TERM_GRACE
                )
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)


def check_effect(connection, run_id, generation=None):
    """Same-transaction admission fence; historical result readers never use it."""
    if (
        connection.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_driver'").fetchone()
        is None
    ):
        return
    verify(connection)
    controller = connection.execute(
        "SELECT deadline FROM workflow_coordinator WHERE run_id=?", (run_id,)
    ).fetchone()
    if controller is not None and time.time() >= controller["deadline"]:
        raise DriverRefused("coordinator_deadline")
    row = connection.execute(
        "SELECT state,lease_expires_at,run_generation FROM workflow_driver WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        return
    if row["state"] in ("stopping", "stopped"):
        raise DriverRefused("driver_stopping")
    if row["state"] != "driving" or row["lease_expires_at"] <= time.time():
        raise DriverRefused("driver_fence_changed")
    if generation is not None and str(generation) != row["run_generation"]:
        raise DriverRefused("driver_generation_changed")
