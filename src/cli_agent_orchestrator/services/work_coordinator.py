"""Bounded Ralph/Beads checkpoints over the existing approved workflow executor."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from uuid import uuid4

from cli_agent_orchestrator.services.workflow_continuation_driver import DriverRefused, check_effect


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def validate_policy(
    *,
    task,
    criteria,
    min_iterations=1,
    max_iterations=8,
    deadline_seconds=3600,
    stall_limit=3,
    correction_budget=4,
    mode="ralph",
    external_binding_ref="",
):
    if not isinstance(task, dict) or not task or len(canonical(task).encode()) > 8192:
        raise ValueError("coordinator_task_invalid")
    for value, low, high in (
        (min_iterations, 1, 64),
        (max_iterations, min_iterations, 64),
        (deadline_seconds, 1, 86400),
        (stall_limit, 1, 64),
        (correction_budget, 0, 64),
    ):
        if type(value) is not int or not low <= value <= high:
            raise ValueError("coordinator_budget_invalid")
    if (
        mode not in ("ralph", "beads")
        or not isinstance(external_binding_ref, str)
        or len(external_binding_ref) > 512
    ):
        raise ValueError("coordinator_mode_invalid")
    if (
        not isinstance(criteria, list)
        or not 1 <= len(criteria) <= 32
        or len(canonical(criteria).encode()) > 8192
    ):
        raise ValueError("coordinator_criteria_required")
    for item in criteria:
        if not isinstance(item, dict):
            raise ValueError("coordinator_criterion_invalid")
        if item.get("kind") == "output_equals":
            if (
                set(item) != {"kind", "path", "value"}
                or not isinstance(item["path"], list)
                or not 1 <= len(item["path"]) <= 16
                or any(not isinstance(k, str) or not k or len(k) > 128 for k in item["path"])
            ):
                raise ValueError("coordinator_criterion_invalid")
        elif item.get("kind") in ("file_sha256", "file_contains"):
            if (
                set(item) != {"kind", "path", "value", "tool"}
                or item["tool"] != "fs_read"
                or not isinstance(item["path"], str)
                or not item["path"]
                or Path(item["path"]).is_absolute()
                or any(p in ("", "..", ".") for p in item["path"].split("/"))
            ):
                raise ValueError("coordinator_criterion_invalid")
            if item["kind"] == "file_sha256" and (
                not isinstance(item["value"], str)
                or re.fullmatch("[0-9a-f]{64}", item["value"]) is None
            ):
                raise ValueError("coordinator_criterion_invalid")
            if item["kind"] == "file_contains" and (
                not isinstance(item["value"], str) or not 1 <= len(item["value"].encode()) <= 4096
            ):
                raise ValueError("coordinator_criterion_invalid")
        else:
            raise ValueError("coordinator_criterion_invalid")
    from cli_agent_orchestrator.services.secret_gate import scan_for_secrets

    if scan_for_secrets(canonical(task)) or scan_for_secrets(canonical(criteria)):
        raise ValueError("coordinator_secret_material_refused")
    return dict(
        version=1,
        task=task,
        criteria=criteria,
        min_iterations=min_iterations,
        max_iterations=max_iterations,
        deadline_seconds=deadline_seconds,
        stall_limit=stall_limit,
        correction_budget=correction_budget,
        mode=mode,
        external_binding_ref=external_binding_ref,
    )


_TEMPLATE = """import json
from cao_workflow import step, get_inputs, emit_output
from cao_workflow._transport import _read_run_capability, _post
from cao_workflow._identity import _read_identity_env

# The inherited credential is released only after durable process attachment.
_read_run_capability()
inputs = get_inputs()
policy = json.loads(inputs["policy_json"])
run_id, generation, base_url = _read_identity_env("step")
previous = None
for iteration in range(1, policy["max_iterations"] + 1):
    response = _post(base_url + "/ralph/runs/" + run_id + "/context", {"iteration": iteration, "generation": int(generation)})
    if response.status != 200:
        raise RuntimeError("coordinator context refused")
    context = json.loads(response.body)
    prompt = inputs["task_json"] + "\\nIteration: " + str(iteration) + "\\nCriteria: " + json.dumps(policy["criteria"], sort_keys=True)
    prompt += "\\nAuthorized feedback: " + json.dumps(context["feedback"], sort_keys=True)
    if previous is not None:
        prompt += "\\nPrior accepted output: " + json.dumps(previous, sort_keys=True)
    result = step(PROVIDER, AGENT, prompt, step_id="iteration-" + str(iteration), recovery="reconcile", target_key="repo")
    previous = result.output
emit_output({"iterations": policy["max_iterations"]})
"""


class WorkCoordinator:
    def __init__(self, plans, driver, result_service=None):
        if driver.plans is not plans:
            raise ValueError("coordinator owner mismatch")
        self.plans = plans
        self.driver = driver
        self.repository = plans.repository
        self.result_service = result_service or driver.projector.work_service
        if self.result_service.repository is not self.repository:
            raise ValueError("coordinator result owner mismatch")
        driver.coordinator = self

    @staticmethod
    def template(provider, agent, memory="exact-snapshot", *, name=None):
        if any(
            not isinstance(v, str) or re.fullmatch("[A-Za-z0-9_-]{1,64}", v) is None
            for v in (provider, agent)
        ) or memory not in ("off", "exact-snapshot"):
            raise ValueError("coordinator_template_invalid")
        source = (
            "INPUTS = "
            + repr(
                {
                    "task_json": {"type": "string", "required": True},
                    "policy_json": {"type": "string", "required": True},
                }
            )
            + "\n"
        )
        source += (
            "SCOPE = "
            + repr({"version": 1, "targets": {"repo": {"agents": [agent], "memory": memory}}})
            + "\n"
        )
        source += "PROVIDER = " + repr(provider) + "\nAGENT = " + repr(agent) + "\n" + _TEMPLATE
        digest = hashlib.sha256(source.encode()).hexdigest()
        return {
            "workflow_name": name or "ralph-" + digest[:16],
            "content": source,
            "source_hash": digest,
        }

    def publish_template(
        self, principal, provider, agent, memory="exact-snapshot", *, name=None, scan_dir=None
    ):
        self.plans._owner(principal)
        from cli_agent_orchestrator.services import workflow_spec_service as specs

        template = self.template(provider, agent, memory, name=name)
        try:
            current = specs.get_workflow_source(template["workflow_name"], scan_dir)
        except FileNotFoundError:
            current = None
        if current is None:
            specs.create_workflow(template["workflow_name"], template["content"], scan_dir)
        elif current["source_hash"] != template["source_hash"]:
            raise ValueError("coordinator_template_conflict")
        return template

    def _template_policy(self, source):
        import ast

        tree = ast.parse(source)
        values = {}
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ("PROVIDER", "AGENT", "SCOPE")
            ):
                values[node.targets[0].id] = ast.literal_eval(node.value)
        try:
            memory = values["SCOPE"]["targets"]["repo"]["memory"]
            if source != self.template(values["PROVIDER"], values["AGENT"], memory)["content"]:
                raise ValueError("coordinator_template_changed")
        except (KeyError, TypeError):
            raise ValueError("coordinator_template_required") from None
        return values

    def prepare(
        self,
        principal,
        workflow_name,
        task,
        criteria,
        target_mappings,
        binding_selections,
        *,
        scan_dir=None,
        **bounds,
    ):
        from cli_agent_orchestrator.services import workflow_spec_service as specs

        policy = validate_policy(task=task, criteria=criteria, **bounds)
        self._template_policy(specs.get_workflow_source(workflow_name, scan_dir)["content"])
        inputs = {"task_json": canonical(task), "policy_json": canonical(policy)}
        return self.plans.prepare(
            principal,
            workflow_name,
            inputs,
            target_mappings,
            binding_selections,
            scan_dir=scan_dir,
            limits={"max_steps": policy["max_iterations"]},
            ttl_seconds=min(86400, policy["deadline_seconds"]),
        )

    def start(
        self,
        principal,
        prepared_id,
        expected_plan_id,
        run_id,
        *,
        scan_dir=None,
        attach_callback=None,
    ):
        coordinator_id = uuid4().hex

        def attach(connection, rid, prepared_row, inputs):
            snapshot = self.plans._stored(connection, rid)[1]
            self._template_policy(snapshot.material("artifact_hash").decode())
            raw = json.loads(inputs["policy_json"])
            if raw.pop("version", None) != 1:
                raise ValueError("coordinator_policy_invalid")
            policy = validate_policy(**raw)
            if inputs["task_json"] != canonical(policy["task"]):
                raise ValueError("coordinator_inputs_changed")
            self.driver.enable(principal, rid, connection=connection)
            from datetime import datetime

            started_at = connection.execute(
                "SELECT started_at FROM workflow_run WHERE run_id=?", (rid,)
            ).fetchone()[0]
            deadline = datetime.fromisoformat(started_at.replace("Z", "+00:00")).timestamp()
            deadline += policy["deadline_seconds"]
            connection.execute(
                "INSERT INTO workflow_coordinator VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    coordinator_id,
                    rid,
                    principal.id,
                    policy["mode"],
                    prepared_id,
                    expected_plan_id,
                    hashlib.sha256(canonical(policy).encode()).hexdigest(),
                    canonical(policy),
                    "ready",
                    1,
                    0,
                    policy["min_iterations"],
                    policy["max_iterations"],
                    deadline,
                    policy["correction_budget"],
                    None,
                    0,
                    None,
                    policy["external_binding_ref"],
                ),
            )
            if attach_callback:
                attach_callback(connection, coordinator_id, rid)

        prepared = self.plans.start(
            principal,
            prepared_id,
            run_id,
            expected_plan_id,
            scan_dir=scan_dir,
            transaction_callback=attach,
        )
        return {
            "coordinator_id": coordinator_id,
            "run_id": run_id,
            "state": "ready",
            "prepared": prepared,
        }

    async def launch_started(self, principal, value):
        # PreparedRun contains a private capability. Consume it before constructing
        # any public response, including response-loss/reconciliation paths.
        prepared = value.pop("prepared")
        run_id = value["run_id"]
        epoch = await asyncio.to_thread(self.driver.claim, principal, run_id)
        self.driver.tasks[run_id] = asyncio.create_task(
            self.driver.drive(principal, run_id, prepared=prepared, epoch=epoch)
        )
        return {key: value[key] for key in ("coordinator_id", "run_id", "state")}

    def _row(self, connection, principal, identity, *, read_only=False):
        if read_only:
            from cli_agent_orchestrator.security.auth import (
                SCOPE_ADMIN,
                SCOPE_READ,
                SCOPE_WRITE,
                is_verified_principal,
            )

            if not is_verified_principal(principal) or not principal.scopes.intersection(
                {SCOPE_READ, SCOPE_WRITE, SCOPE_ADMIN}
            ):
                raise PermissionError("coordinator_read_authority_required")
        else:
            self.plans._owner(principal)
        row = connection.execute(
            "SELECT * FROM workflow_coordinator WHERE id=? OR run_id=?", (identity, identity)
        ).fetchone()
        if row is None:
            raise LookupError("coordinator_missing")
        if row["owner_principal_id"] != principal.id:
            raise PermissionError("coordinator_owner_mismatch")
        if hashlib.sha256(row["policy_json"].encode()).hexdigest() != row["frozen_policy_hash"]:
            raise ValueError("coordinator_policy_integrity")
        scoped, snapshot = self.plans._stored(connection, row["run_id"])
        original = self.plans._inputs(
            snapshot, self.plans._fields(json.loads(scoped["public_manifest_json"]))
        )
        if (
            scoped["plan_id"] != row["plan_id"]
            or scoped["principal_id"] != row["owner_principal_id"]
            or original.get("policy_json") != row["policy_json"]
        ):
            raise ValueError("coordinator_private_policy_integrity")
        policy = json.loads(row["policy_json"])
        if any(
            row[key] != policy[key]
            for key in (
                "mode",
                "min_iterations",
                "max_iterations",
                "correction_budget",
                "external_binding_ref",
            )
        ):
            raise ValueError("coordinator_private_budget_integrity")
        from datetime import datetime

        started = connection.execute(
            "SELECT started_at FROM workflow_run WHERE run_id=?", (row["run_id"],)
        ).fetchone()[0]
        started_epoch = datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp()
        if row["deadline"] > started_epoch + policy["deadline_seconds"] + 1:
            raise ValueError("coordinator_private_deadline_integrity")
        return row

    def status(self, principal, identity):
        with self.repository.read_snapshot() as conn:
            row = self._row(conn, principal, identity, read_only=True)
            events = conn.execute(
                "SELECT kind,iteration,binding_id,accepted_result_id,content_hash,public_evidence_json FROM workflow_coordinator_events WHERE coordinator_id=? ORDER BY sequence DESC LIMIT 64",
                (row["id"],),
            ).fetchall()
            driver = conn.execute(
                "SELECT state,pause_reason FROM workflow_driver WHERE run_id=?", (row["run_id"],)
            ).fetchone()
        verified = False
        completion_evidence_state = "unverified"
        try:
            if row["state"] == "completed":
                completion = next((e for e in events if e["kind"] == "complete"), None)
                if completion is not None:
                    evidence = json.loads(completion["public_evidence_json"])
                    if (
                        hashlib.sha256(canonical(evidence).encode()).hexdigest()
                        != completion["content_hash"]
                    ):
                        raise ValueError("coordinator_completion_integrity")
                    with self.repository.read_snapshot() as conn:
                        wake = conn.execute(
                            "SELECT * FROM workflow_continuation_outbox WHERE binding_id=? AND accepted_result_id=?",
                            (completion["binding_id"], completion["accepted_result_id"]),
                        ).fetchone()
                    if wake is not None:
                        binding = self.plans.origins.read_step_binding(
                            "script",
                            row["run_id"],
                            int(wake["run_generation"]),
                            wake["step_id"],
                            wake["step_attempt"],
                            historical=True,
                        )
                        accepted = self.result_service.read_accepted_workflow_result(binding)
                        verified = (
                            accepted is not None
                            and accepted.accepted_result_id == completion["accepted_result_id"]
                            and accepted.content_hash
                            == evidence.get("content_hash")
                            == wake["content_hash"]
                        )
        except (ValueError, LookupError, OSError):
            completion_evidence_state = "validation_refused"
        if verified:
            completion_evidence_state = "verified"
        return {
            "coordinator_id": row["id"],
            "run_id": row["run_id"],
            "mode": row["mode"],
            "state": row["state"],
            "revision": row["revision"],
            "iteration": row["current_iteration"],
            "min_iterations": row["min_iterations"],
            "max_iterations": row["max_iterations"],
            "deadline": row["deadline"],
            "escalation_reason": row["escalation_reason"],
            "driver_state": driver["state"],
            "pause_reason": driver["pause_reason"],
            "work_verified_completed": verified,
            "completion_evidence_state": completion_evidence_state,
            "events": [dict(e) for e in events],
        }

    def _event(self, conn, row, request_id, kind, iteration, content, binding=None, result=None):
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError("coordinator_request_id_invalid")
        text = canonical(content)
        digest = hashlib.sha256(text.encode()).hexdigest()
        prior = conn.execute(
            "SELECT * FROM workflow_coordinator_events WHERE coordinator_id=? AND request_id=?",
            (row["id"], request_id),
        ).fetchone()
        if prior:
            if (
                prior["kind"] != kind
                or prior["content_hash"] != digest
                or prior["iteration"] != iteration
            ):
                raise ValueError("coordinator_request_conflict")
            return False
        sequence = conn.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM workflow_coordinator_events WHERE coordinator_id=?",
            (row["id"],),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO workflow_coordinator_events VALUES (?,?,?,?,?,?,?,?,?,?)",
            (row["id"], sequence, request_id, kind, iteration, binding, result, digest, text, None),
        )
        return True

    def feedback(self, principal, identity, request_id, text):
        from cli_agent_orchestrator.services.secret_gate import scan_for_secrets

        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text.encode()) > 4096
            or scan_for_secrets(text)
        ):
            raise ValueError("coordinator_feedback_invalid")
        self.plans.validate_resume(principal, self.status(principal, identity)["run_id"])
        with self.repository.transaction() as conn:
            row = self._row(conn, principal, identity)
            if row["state"] in ("completed", "stopped", "stopping", "escalated"):
                raise ValueError("coordinator_terminal")
            prior = conn.execute(
                "SELECT iteration FROM workflow_coordinator_events WHERE coordinator_id=? AND request_id=?",
                (row["id"], request_id),
            ).fetchone()
            iteration = prior[0] if prior else row["current_iteration"] + 1
            # If this iteration already has an immutable prompt, queue feedback
            # for the next one; never paste into an uncertain/live agent turn.
            if (
                prior is None
                and conn.execute(
                    "SELECT 1 FROM workflow_coordinator_events WHERE coordinator_id=? AND request_id=?",
                    (row["id"], "context-" + str(iteration)),
                ).fetchone()
            ):
                iteration += 1
            count = conn.execute(
                "SELECT count(*) FROM workflow_coordinator_events WHERE coordinator_id=? AND kind='feedback'",
                (row["id"],),
            ).fetchone()[0]
            if prior is None and (
                count >= row["correction_budget"] or iteration > row["max_iterations"]
            ):
                raise ValueError("coordinator_feedback_budget")
            self._event(conn, row, request_id, "feedback", iteration, {"text": text})
        return self.status(principal, identity)

    def context(self, principal, run_id, iteration):
        if type(iteration) is not int:
            raise ValueError("coordinator_iteration_invalid")
        self.plans.validate_resume(principal, run_id)
        with self.repository.transaction() as conn:
            check_effect(conn, run_id)
            row = self._row(conn, principal, run_id)
            if (
                not 1 <= iteration <= row["max_iterations"]
                or iteration > row["current_iteration"] + 1
            ):
                raise ValueError("coordinator_iteration_invalid")
            prior = conn.execute(
                "SELECT public_evidence_json FROM workflow_coordinator_events WHERE coordinator_id=? AND request_id=?",
                (row["id"], "context-" + str(iteration)),
            ).fetchone()
            if prior:
                return json.loads(prior[0])
            feedback = [
                json.loads(e[0])["text"]
                for e in conn.execute(
                    "SELECT public_evidence_json FROM workflow_coordinator_events WHERE coordinator_id=? AND kind='feedback' AND iteration=? ORDER BY sequence",
                    (row["id"], iteration),
                )
            ]
            value = {"feedback": feedback}
            self._event(conn, row, "context-" + str(iteration), "progress", iteration, value)
            return value

    def _criteria(self, principal, run_id, policy, accepted):
        output = accepted.result.output
        proof = []
        satisfied = True
        with self.repository.read_snapshot() as conn:
            scoped, snapshot = self.plans._stored(conn, run_id)
            entries = snapshot.decode_material("policy")
        if len(entries) != 1:
            raise ValueError("coordinator_scope_ambiguous")
        entry = entries[0]
        for criterion in policy["criteria"]:
            if criterion["kind"] == "output_equals":
                actual = output
                try:
                    for key in criterion["path"]:
                        actual = actual[key]
                    valid = canonical(actual) == canonical(criterion["value"])
                except (KeyError, TypeError, IndexError):
                    valid = False
                proof.append({"kind": "output_equals", "satisfied": valid})
            else:
                from cli_agent_orchestrator.services.work_authority import (
                    Permissions,
                    WorkAuthority,
                )

                if "fs_read" not in entry["contract"]["permissions"]["tools"] or not set(
                    entry["allowed_tools"]
                ).intersection({"fs_read", "read", "Read"}):
                    raise PermissionError("coordinator_file_read_not_approved")
                root = Path(entry["contract"]["resources"]["checkout_root"]).resolve(strict=True)
                path = root / criterion["path"]
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError("coordinator_artifact_escape")
                with self.repository.transaction() as conn:
                    seed = self.plans._seed(conn, principal, entry, scoped["source_hash"])
                    WorkAuthority(self.repository)._authorize(
                        conn,
                        principal,
                        job_id=seed.job_id,
                        grant_id=seed.grant_id,
                        expected_grant_revision=seed.grant_revision,
                        provider=entry["provider"],
                        requested_permissions=Permissions(tools=("fs_read",), paths=(str(path),)),
                    )
                try:
                    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        parts = criterion["path"].split("/")
                        for part in parts[:-1]:
                            next_fd = os.open(
                                part,
                                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory_fd,
                            )
                            os.close(directory_fd)
                            directory_fd = next_fd
                        fd = os.open(
                            parts[-1],
                            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                            dir_fd=directory_fd,
                        )
                    finally:
                        os.close(directory_fd)
                    try:
                        import stat

                        before = os.fstat(fd)
                        if not stat.S_ISREG(before.st_mode):
                            raise ValueError("coordinator_artifact_not_regular")
                        content = os.read(fd, 1024 * 1024 + 1)
                        after = os.fstat(fd)
                        stable = lambda value: (
                            value.st_dev,
                            value.st_ino,
                            value.st_size,
                            value.st_mtime_ns,
                            value.st_ctime_ns,
                        )
                        if stable(before) != stable(after) or len(content) > 1024 * 1024:
                            raise ValueError("coordinator_artifact_unstable")
                    finally:
                        os.close(fd)
                    digest = hashlib.sha256(content).hexdigest()
                    valid = (
                        digest == criterion["value"]
                        if criterion["kind"] == "file_sha256"
                        else criterion["value"].encode() in content
                    )
                except FileNotFoundError:
                    digest = None
                    valid = False
                proof.append({"kind": criterion["kind"], "sha256": digest, "satisfied": valid})
            satisfied = satisfied and valid
        return satisfied, proof

    def checkpoint(self, principal, run_id, event):
        from cli_agent_orchestrator.services import workflow_journal

        self.plans.validate_resume(principal, run_id)
        projection = workflow_journal.get_work_step_projection(run_id, event["step_id"])
        if (
            projection is None
            or projection.accepted_result_id != event["accepted_result_id"]
            or projection.content_hash != event["content_hash"]
        ):
            raise ValueError("coordinator_projection_integrity")
        binding = self.plans.origins.read_step_binding(
            "script",
            run_id,
            int(event["run_generation"]),
            event["step_id"],
            event["step_attempt"],
            historical=True,
        )
        accepted = self.result_service.read_accepted_workflow_result(binding)
        if accepted is None or accepted.content_hash != event["content_hash"]:
            raise ValueError("coordinator_accepted_artifact_required")
        with self.repository.read_snapshot() as conn:
            row = self._row(conn, principal, run_id)
            policy = json.loads(row["policy_json"])
        iteration = int(event["step_id"].removeprefix("iteration-"))
        satisfied, proof = self._criteria(principal, run_id, policy, accepted)
        progress = hashlib.sha256(accepted.canonical_bytes).hexdigest()
        with self.repository.transaction() as conn:
            row = self._row(conn, principal, run_id)
            if row["state"] in ("completed", "stopping", "stopped", "escalated"):
                return False
            from cli_agent_orchestrator.services.workflow_continuation_driver import process_stopped

            driver = conn.execute(
                "SELECT * FROM workflow_driver WHERE run_id=?", (run_id,)
            ).fetchone()
            if not process_stopped(driver["process_identity_json"]):
                raise DriverRefused("orphan_process_reconciliation_required")
            if driver["state"] == "driving" and driver["lease_expires_at"] > time.time():
                raise DriverRefused("driver_owned")
            first = self._event(
                conn,
                row,
                "checkpoint-" + event["id"],
                "checkpoint",
                iteration,
                {"criteria": proof, "accepted_hash": progress},
                binding.binding_id,
                accepted.accepted_result_id,
            )
            if first and iteration != row["current_iteration"] + 1:
                raise ValueError("coordinator_iteration_conflict")
            stalled = (
                (row["no_progress_count"] + 1 if row["last_progress_hash"] == progress else 0)
                if first
                else row["no_progress_count"]
            )
            complete = satisfied and iteration >= row["min_iterations"]
            reason = None
            if not complete and (
                time.time() >= row["deadline"]
                or iteration >= row["max_iterations"]
                or stalled >= policy["stall_limit"]
            ):
                reason = (
                    "deadline"
                    if time.time() >= row["deadline"]
                    else "iteration_budget" if iteration >= row["max_iterations"] else "no_progress"
                )
            state = "completed" if complete else "escalated" if reason else "waiting"
            if first and state == "waiting":
                # Retire only this exact accepted checkpoint's active pointer.
                # A replayed result must never clear a subsequent pending step.
                advanced = conn.execute(
                    "UPDATE workflow_run SET current_step_id=NULL WHERE run_id=? AND state='running' AND generation=? AND current_step_id=? AND EXISTS(SELECT 1 FROM workflow_run_step s WHERE s.run_id=workflow_run.run_id AND s.step_id=? AND s.state='completed' AND s.attempts=?)",
                    (
                        run_id,
                        str(event["run_generation"]),
                        event["step_id"],
                        event["step_id"],
                        event["step_attempt"],
                    ),
                )
                if advanced.rowcount != 1:
                    raise ValueError("coordinator_checkpoint_CAS_lost")
            conn.execute(
                "UPDATE workflow_coordinator SET state=?,current_iteration=?,last_progress_hash=?,no_progress_count=?,escalation_reason=?,revision=revision+1 WHERE id=?",
                (state, iteration, progress, stalled, reason, row["id"]),
            )
            if complete or reason:
                self._event(
                    conn,
                    row,
                    "settlement-" + event["id"],
                    "complete" if complete else "escalate",
                    iteration,
                    {
                        "reason": reason,
                        "accepted_result_id": accepted.accepted_result_id,
                        "content_hash": accepted.content_hash,
                    },
                    binding.binding_id,
                    accepted.accepted_result_id,
                )
                conn.execute(
                    "UPDATE workflow_continuation_outbox SET state='consumed',revision=revision+1 WHERE id=?",
                    (event["id"],),
                )
                conn.execute(
                    "UPDATE workflow_driver SET state='stopped',lease_expires_at=0,revision=revision+1 WHERE run_id=?",
                    (run_id,),
                )
                conn.execute(
                    "UPDATE workflow_run SET state=?,finished_at=? WHERE run_id=?",
                    (
                        "completed" if complete else "failed",
                        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        run_id,
                    ),
                )
                return False
        return True

    def observe_run(self, run_id, result):
        if result.state.value == "running":
            return
        with self.repository.transaction() as conn:
            row = conn.execute(
                "SELECT state FROM workflow_coordinator WHERE run_id=?", (run_id,)
            ).fetchone()
            if row and row[0] not in ("completed", "stopped", "stopping", "escalated"):
                conn.execute(
                    "UPDATE workflow_coordinator SET state='paused',escalation_reason='executor_without_checkpoint',revision=revision+1 WHERE run_id=?",
                    (run_id,),
                )

    async def resume(self, principal, identity):
        from cli_agent_orchestrator.services import workflow_journal

        value = await asyncio.to_thread(self.status, principal, identity)
        await asyncio.to_thread(self.plans._owner, principal)
        if value["state"] in ("completed", "stopped", "stopping", "escalated"):
            raise ValueError("coordinator_terminal")
        run_id = value["run_id"]
        await asyncio.to_thread(self.driver.projector.project_pending_for_run, run_id)
        steps = await asyncio.to_thread(workflow_journal.get_steps, run_id)
        if any(step.state == "work_pending" for step in steps):
            raise ValueError("coordinator_work_pending")
        # The same driver lease owns manual and automatic continuations.
        epoch = await asyncio.to_thread(self.driver.claim, principal, run_id)
        self.driver.tasks[run_id] = asyncio.create_task(
            self.driver.drive(principal, run_id, epoch=epoch)
        )
        return {"run_id": run_id, "state": "driving"}

    def _prepare_stop(self, principal, identity):
        status = self.status(principal, identity)
        run_id = status["run_id"]
        self.driver.request_stop(principal, run_id)
        with self.repository.transaction() as conn:
            row = self._row(conn, principal, identity)
            if row["state"] == "completed":
                raise ValueError("coordinator_completed")
            conn.execute(
                "UPDATE workflow_coordinator SET state='stopping',revision=revision+1 WHERE id=?",
                (row["id"],),
            )
            self._event(
                conn,
                row,
                "stop",
                "stop",
                row["current_iteration"],
                {"request": "fence-and-observe"},
            )
        return run_id

    async def stop(self, principal, identity):
        run_id = await asyncio.to_thread(self._prepare_stop, principal, identity)
        from cli_agent_orchestrator.services import script_runner, workflow_service

        record = workflow_service.run_registry.get(run_id)
        if record and getattr(record, "process", None) and record.process.returncode is None:
            await script_runner._terminate(record.process, script_runner.WORKFLOW_SCRIPT_TERM_GRACE)
        task = self.driver.tasks.get(run_id)
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return await asyncio.to_thread(self.reconcile_stop, principal, identity)

    def reconcile_stop(self, principal, identity):
        from cli_agent_orchestrator.services.workflow_continuation_driver import process_stopped

        with self.repository.transaction() as conn:
            row = self._row(conn, principal, identity)
            driver = conn.execute(
                "SELECT * FROM workflow_driver WHERE run_id=?", (row["run_id"],)
            ).fetchone()
            work = conn.execute(
                "SELECT w.state,a.cleanup_state FROM work_workflow_step_bindings b JOIN work_items w ON w.id=b.work_item_id JOIN work_attempts a ON a.id=b.work_attempt_id WHERE b.run_id=?",
                (row["run_id"],),
            ).fetchall()
            stopped = process_stopped(driver["process_identity_json"]) and all(
                w[0] in ("succeeded", "failed", "cancelled") and w[1] == "complete" for w in work
            )
            if stopped and row["state"] == "stopping":
                conn.execute(
                    "UPDATE workflow_coordinator SET state='stopped',revision=revision+1 WHERE id=?",
                    (row["id"],),
                )
                conn.execute(
                    "UPDATE workflow_driver SET state='stopped',stop_evidence_ref='owned-process-and-Work-cleanup',revision=revision+1 WHERE run_id=?",
                    (row["run_id"],),
                )
                conn.execute(
                    "UPDATE workflow_run SET state='cancelled' WHERE run_id=?", (row["run_id"],)
                )
        return self.status(principal, identity)

    def complete(self, principal, identity):
        status = self.status(principal, identity)
        with self.repository.read_snapshot() as conn:
            row = self._row(conn, principal, identity)
            event = conn.execute(
                "SELECT * FROM workflow_continuation_outbox WHERE run_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                (row["run_id"],),
            ).fetchone()
        if event is None:
            raise ValueError("coordinator_accepted_artifact_required")
        # Even completed metadata is never enough: reread exact accepted bytes,
        # live authority and current files before returning verified completion.
        self.plans.validate_resume(principal, row["run_id"])
        binding = self.plans.origins.read_step_binding(
            "script",
            row["run_id"],
            int(event["run_generation"]),
            event["step_id"],
            event["step_attempt"],
            historical=True,
        )
        accepted = self.result_service.read_accepted_workflow_result(binding)
        satisfied, _ = self._criteria(
            principal, row["run_id"], json.loads(row["policy_json"]), accepted
        )
        if (
            not satisfied
            or row["current_iteration"] < row["min_iterations"]
            or row["state"] != "completed"
        ):
            raise ValueError("coordinator_completion_criteria_unsatisfied")
        return self.status(principal, identity)
