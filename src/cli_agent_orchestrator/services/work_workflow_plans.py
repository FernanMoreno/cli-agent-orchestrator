"""Private prepared plans joined to the existing Work authority and admission.

This owns content/selection proofs. WorkProvisioning, WorkWorkflowOrigins and
WorkAdmission continue to own grants, attempts, receipts and execution.
"""

import hashlib
import json
import sqlite3
import time
from contextlib import closing, nullcontext
from dataclasses import dataclass
from uuid import uuid4

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services import execution_manifest
from cli_agent_orchestrator.services import plan_identifier as ids
from cli_agent_orchestrator.services import private_plan_snapshot as private
from cli_agent_orchestrator.services import workflow_spec_service as specs
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    SnapshotUnavailable,
)
from cli_agent_orchestrator.services.execution_scope import (
    freeze_execution_scope,
    parse_scope_declaration,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.launch_material import (
    freeze_launch_material,
    verify_dependency_closure,
)
from cli_agent_orchestrator.services.launch_policy import admit_launch_request
from cli_agent_orchestrator.services.work_authority import WorkAuthority
from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning


class WorkflowPlanRefused(ValueError):
    def __init__(self, kind, status_code=422):
        self.kind = kind
        self.status_code = status_code
        super().__init__(kind)


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS workflow_prepared_plan (prepared_id TEXT PRIMARY KEY,plan_id TEXT NOT NULL REFERENCES workflow_plan_snapshot(plan_id),workflow_name TEXT NOT NULL,tier TEXT NOT NULL,source_hash TEXT NOT NULL,principal_id TEXT NOT NULL,created_at REAL NOT NULL,expires_at REAL NOT NULL,public_manifest_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS workflow_scoped_run (run_id TEXT PRIMARY KEY REFERENCES workflow_run(run_id) ON DELETE CASCADE,prepared_id TEXT NOT NULL REFERENCES workflow_prepared_plan(prepared_id),principal_id TEXT NOT NULL,plan_id TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS workflow_plan_step_alias (run_id TEXT NOT NULL REFERENCES workflow_run(run_id) ON DELETE CASCADE,step_id TEXT NOT NULL,target_key TEXT NOT NULL,agent_profile TEXT NOT NULL,workflow_alias TEXT NOT NULL,seed_id TEXT NOT NULL,seed_revision INTEGER NOT NULL,provision_id TEXT NOT NULL,PRIMARY KEY(run_id,step_id))",
)


@dataclass(frozen=True)
class PreparedRun:
    spec: object
    inputs: dict
    manifest_json: str
    run_credential: str | None
    started_at: str


class WorkWorkflowPlans:
    def __init__(self, repository):
        if type(repository) is not WorkRepository:
            raise TypeError("explicit Work repository required")
        self.repository = repository
        self.provisioning = WorkProvisioning(repository)
        self.origins = None

    def initialize(self):
        from cli_agent_orchestrator import constants
        from cli_agent_orchestrator.clients.database import _migrate_workflow_plan_snapshot
        from cli_agent_orchestrator.services import workflow_journal

        if (
            self.repository.path.resolve()
            != __import__("pathlib").Path(constants.DATABASE_FILE).resolve()
        ):
            raise WorkflowPlanRefused("workflow_store_mismatch", 503)
        _migrate_workflow_plan_snapshot()
        # Journal schema verification occurs before a borrowed write transaction.
        with (
            closing(workflow_journal._connect()) as _owned_connection,
            _owned_connection as connection,
        ):
            pass
        with self.repository.transaction() as connection:
            for statement in _SCHEMA:
                connection.execute(statement)
            for table, fields in (
                ("workflow_plan_snapshot", {"plan_id", "component_set_version"}),
                (
                    "workflow_plan_snapshot_component",
                    {"plan_id", "component_name", "content_digest", "content"},
                ),
                ("workflow_run_plan_snapshot", {"run_id", "plan_id"}),
            ):
                if {
                    item[1] for item in connection.execute("PRAGMA table_info(" + table + ")")
                } != fields:
                    raise WorkflowPlanRefused("private_plan_schema_unavailable", 503)

    def _owner(self, principal):
        WorkAuthority._principal(principal)
        return principal.id

    @staticmethod
    def _fields(document):
        doc = document["approved"]["components"]
        return ids.PlanV2Components(
            tier=doc["tier"],
            artifact_hash=doc["artifact_hash"],
            declaration=doc["declaration"],
            targets=doc["targets"],
            inputs=tuple(ids.InputDigest(**item) for item in doc["inputs"]),
            limits=doc["limits"],
            retry_policy=doc["retry_policy"],
            policy=doc["policy"],
            memory=doc["memory"],
        )

    def prepare(
        self,
        principal,
        name,
        inputs,
        target_mappings,
        binding_selections,
        *,
        scan_dir=None,
        scope_source=None,
        ttl_seconds=3600,
        limits=None,
        retry_policy=None,
    ):
        self._owner(principal)
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
            raise WorkflowPlanRefused("plan_lifetime_invalid")
        self.initialize()
        spec = specs.get_workflow(name, scan_dir)
        view = specs.get_workflow_source(spec.name, scan_dir)
        source = view["content"]
        tier = "script" if hasattr(spec, "source") else "yaml"
        if tier == "script":
            from cli_agent_orchestrator.services.script_lint import lint_script

            if lint_script(source, spec.path).status == "fail":
                raise WorkflowPlanRefused("script_lint_failed")
        elif spec.mode != "sequential":
            from cli_agent_orchestrator.services.workflow_service import _dispatch_reserved_mode

            _dispatch_reserved_mode(spec)
        if tier == "yaml" and scope_source is None:
            raise WorkflowPlanRefused("yaml_scope_required")
        declaration = parse_scope_declaration(source if tier == "script" else scope_source)
        scope = freeze_execution_scope(declaration, target_mappings)
        declared = {
            (target.key, profile.value)
            for target in declaration.targets
            for profile in target.allowed_agent_profiles
        }
        if not isinstance(binding_selections, dict) or set(binding_selections) != {
            key for key, _ in declared
        }:
            raise WorkflowPlanRefused("plan_bindings_incomplete")
        for key, profile in declared:
            if not isinstance(binding_selections[key], dict) or set(binding_selections[key]) != {
                p for k, p in declared if k == key
            }:
                raise WorkflowPlanRefused("plan_bindings_incomplete")
        if not isinstance(inputs, dict):
            raise WorkflowPlanRefused("plan_inputs_invalid")
        # Keep the exact typed input codec separate from public redaction.
        from cli_agent_orchestrator.services.workflow_service import _validate_inputs

        inputs = _validate_inputs(spec, inputs)
        input_digests = ids.digest_inputs(inputs)
        materials = {
            "artifact_hash": source.encode("utf-8"),
            "declaration": declaration.material_bytes,
            "targets": scope.targets_material_bytes,
        }
        for key, value in inputs.items():
            key_material = ids.canonical_key_bytes(key)
            key_digest = ids.digest_bytes(key_material)
            materials["input-key:" + key_digest] = key_material
            materials["input-value:" + key_digest] = ids.canonical_component_bytes(value)
        if limits is None:
            limits = {"max_steps": 256}
        if (
            not isinstance(limits, dict)
            or set(limits) != {"max_steps"}
            or type(limits["max_steps"]) is not int
            or not 1 <= limits["max_steps"] <= 4096
        ):
            raise WorkflowPlanRefused("plan_limits_invalid")
        if retry_policy is None:
            retry_policy = {"policy": "explicit-work-retry"}
        if retry_policy != {"policy": "explicit-work-retry"}:
            raise WorkflowPlanRefused("plan_retry_policy_invalid")
        policy_entries, memories = [], []
        with self.repository.read_snapshot() as connection:
            for target_key, profile_name in sorted(declared):
                selector = binding_selections[target_key][profile_name]
                if not isinstance(selector, str):
                    raise WorkflowPlanRefused("plan_seed_invalid")
                seed = self.provisioning.resolve_workflow_step(
                    principal,
                    workflow_id=spec.name,
                    step_id=selector,
                    spec_hash=view["source_hash"],
                    connection=connection,
                )
                if seed is None:
                    raise WorkflowPlanRefused("plan_work_authority_missing", 403)
                target = next(item for item in scope.bindings if item.key == target_key)
                child_provenance = None
                if hasattr(target, "workcopy_realpath"):
                    if seed.contract.resources.checkout_root != target.workcopy_realpath:
                        raise WorkflowPlanRefused("plan_work_target_mismatch", 403)
                    instance_key = target.instance_key
                else:
                    from cli_agent_orchestrator.services.execution_scope import (
                        _run_git,
                        derive_actual_instance_identity,
                    )

                    parent = next(item for item in scope.bindings if item.key == target.parent_key)
                    child = derive_actual_instance_identity(
                        seed.contract.resources.checkout_root, target_key=target_key
                    )
                    parent_identity = derive_actual_instance_identity(
                        parent.workcopy_realpath, target_key=parent.key
                    )
                    commit = _run_git(
                        child.workcopy_realpath,
                        ("rev-parse", "--verify", "HEAD"),
                        target_key=target_key,
                        nonzero_code="scope_child_head_unavailable",
                    )
                    if (
                        child.git_common_dir_realpath != parent_identity.git_common_dir_realpath
                        or child.instance_key == parent_identity.instance_key
                        or commit != target.intended_baseline_commit
                    ):
                        raise WorkflowPlanRefused("scope_child_provenance_mismatch", 403)
                    instance_key = child.instance_key
                    child_provenance = {
                        "parent_root": parent.workcopy_realpath,
                        "parent_key": parent.key,
                        "parent_instance_key": parent_identity.instance_key,
                        "git_common_dir": parent_identity.git_common_dir_realpath,
                        "baseline_commit": target.intended_baseline_commit,
                        "provenance_key": target.provenance_key,
                    }
                material = freeze_launch_material(profile_name, seed)
                material.update(
                    target_key=target_key,
                    target_digest=target.target_digest,
                    seed_workflow=spec.name,
                    seed_step=selector,
                    target_instance_key=instance_key,
                    child_provenance=child_provenance,
                )
                policy_entries.append(material)
                mode = declaration.target(target_key).memory_mode
                snapshot = DelegationSnapshots._load_authorized(
                    connection,
                    connection.execute(
                        "SELECT * FROM work_delegation_snapshots WHERE id=?", (seed.snapshot_id,)
                    ).fetchone(),
                )
                policy = KnowledgePolicy(
                    self.repository, seed.job_id, seed.grant_id, seed.grant_revision
                )
                policy(connection, principal, "read", snapshot.scope, snapshot.scope_id)
                if mode == "off" and snapshot.content != b"":
                    raise WorkflowPlanRefused("memory_off_requires_empty_snapshot", 403)
                memories.append(
                    {
                        "target_key": target_key,
                        "agent_profile": profile_name,
                        "mode": mode,
                        "snapshot_id": snapshot.id,
                        "snapshot_hash": snapshot.delivered_hash,
                        "job_id": seed.job_id,
                        "content": snapshot.content.decode("utf-8") if mode != "off" else "",
                    }
                )
        if tier == "yaml":
            # Validate deterministic YAML selection before any executable run row.
            for step in spec.steps:
                candidates = [
                    entry
                    for entry in policy_entries
                    if entry["profile_name"] == step.agent
                    and (step.target_key is None or entry["target_key"] == step.target_key)
                ]
                if len(candidates) != 1:
                    raise WorkflowPlanRefused("yaml_target_ambiguous")
                entry = candidates[0]
                admit_launch_request(
                    entry, provider=step.provider, agent=step.agent, engine=step.engine
                )

        materials.update(
            {
                "limits": private.structured_material_bytes(limits),
                "retry_policy": private.structured_material_bytes(retry_policy),
                "policy": private.structured_material_bytes(policy_entries),
                "memory": private.structured_material_bytes(memories),
            }
        )
        components = ids.PlanV2Components(
            tier=tier,
            inputs=input_digests,
            **{
                key: ids.digest_bytes(value)
                for key, value in materials.items()
                if not key.startswith("input-")
            },
        )
        plan_id, checked, expected = private._validated_materials(components, materials)
        public = execution_manifest.serialise_v2_public(
            execution_manifest.build_v2_public(components)
        )
        prepared_id = uuid4().hex
        with self.repository.transaction() as connection:
            private._repair_write_permissions(self.repository.path)
            # Revalidate the pinned seed again in the actual publication transaction.
            for entry in policy_entries:
                self._seed(connection, principal, entry, view["source_hash"])
            private._store_or_verify(connection, plan_id, components, checked, expected)
            now = time.time()
            connection.execute(
                "INSERT INTO workflow_prepared_plan VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    prepared_id,
                    plan_id,
                    spec.name,
                    tier,
                    view["source_hash"],
                    principal.id,
                    now,
                    now + ttl_seconds,
                    public,
                ),
            )
        return {
            "prepared_id": prepared_id,
            "plan_id": plan_id,
            "source_hash": view["source_hash"],
            "expires_at": now + ttl_seconds,
            "public_plan": json.loads(public),
            "scope_summary": scope.public_summary(),
        }

    def review(self, principal, prepared_id):
        with self.repository.read_snapshot() as connection:
            row, snapshot = self._prepared(connection, principal, prepared_id, public_review=True)
            return {
                "prepared_id": prepared_id,
                "plan_id": row["plan_id"],
                "source_hash": row["source_hash"],
                "expires_at": row["expires_at"],
                "public_plan": json.loads(row["public_manifest_json"]),
            }

    def _prepared(
        self, connection, principal, prepared_id, *, allow_expired=False, public_review=False
    ):
        if public_review:
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
                raise WorkflowPlanRefused("prepared_plan_owner_mismatch", 403)
        else:
            self._owner(principal)
        private._verify_read_permissions(self.repository.path)
        row = connection.execute(
            "SELECT * FROM workflow_prepared_plan WHERE prepared_id=?", (prepared_id,)
        ).fetchone()
        if row is None:
            raise WorkflowPlanRefused("prepared_plan_missing", 404)
        if row["principal_id"] != principal.id and not (
            public_review and SCOPE_ADMIN in principal.scopes
        ):
            raise WorkflowPlanRefused("prepared_plan_owner_mismatch", 403)
        if not allow_expired and row["expires_at"] <= time.time():
            raise WorkflowPlanRefused("prepared_plan_expired", 409)
        document = json.loads(row["public_manifest_json"])
        if not execution_manifest.verify_v2_public(document):
            raise WorkflowPlanRefused("prepared_plan_integrity", 409)
        fields = self._fields(document)
        if ids.compute_v2(fields) != row["plan_id"]:
            raise WorkflowPlanRefused("prepared_plan_integrity", 409)
        snapshot = private._load_verified(connection, row["plan_id"], fields)
        return row, snapshot

    def _seed(self, connection, principal, material, source_hash):
        seed = self.provisioning.resolve_workflow_step(
            principal,
            workflow_id=material["seed_workflow"],
            step_id=material["seed_step"],
            spec_hash=source_hash,
            connection=connection,
        )
        if seed is None or (
            seed.ref.id,
            seed.ref.revision,
            seed.provision_fingerprint,
            seed.contract_hash,
            seed.delivery_template_hash,
        ) != (
            material["provision_id"],
            material["provision_revision"],
            material["provision_fingerprint"],
            material["contract_hash"],
            material["delivery_hash"],
        ):
            raise WorkflowPlanRefused("plan_seed_changed", 403)
        verify_dependency_closure(material)
        self._validate_target(material)
        return seed

    @staticmethod
    def _validate_target(material):
        from cli_agent_orchestrator.services.execution_scope import (
            _run_git,
            derive_actual_instance_identity,
        )

        identity = derive_actual_instance_identity(
            material["contract"]["resources"]["checkout_root"], target_key=material["target_key"]
        )
        if identity.instance_key != material["target_instance_key"]:
            raise WorkflowPlanRefused("scope_target_instance_changed", 403)
        child = material.get("child_provenance")
        if child:
            parent = derive_actual_instance_identity(
                child["parent_root"], target_key=child["parent_key"]
            )
            if (
                parent.instance_key != child["parent_instance_key"]
                or identity.git_common_dir_realpath != child["git_common_dir"]
                or parent.git_common_dir_realpath != child["git_common_dir"]
                or _run_git(
                    identity.workcopy_realpath,
                    ("rev-parse", "--verify", "HEAD"),
                    target_key=material["target_key"],
                    nonzero_code="scope_child_head_unavailable",
                )
                != child["baseline_commit"]
            ):
                raise WorkflowPlanRefused("scope_child_provenance_mismatch", 403)

    @staticmethod
    def _inputs(snapshot, fields):
        result = {}
        for item in fields.inputs:
            key = snapshot.material("input-key:" + item.key_digest).decode("utf-8")
            # Keys have a distinct framing codec; decode through the input-key helper.
            result[key] = ids.decode_component_bytes(
                snapshot.material("input-value:" + item.key_digest)
            )
        return result

    def _stored(self, connection, run_id):
        reference = connection.execute(
            "SELECT * FROM workflow_scoped_run WHERE run_id=?", (run_id,)
        ).fetchone()
        if reference is None:
            run = connection.execute(
                "SELECT manifest_json FROM workflow_run WHERE run_id=?", (run_id,)
            ).fetchone()
            if (
                run is not None
                and run["manifest_json"]
                and execution_manifest.parse(run["manifest_json"]) is None
            ):
                raise WorkflowPlanRefused("run_plan_attachment_missing", 409)
            return None
        row = connection.execute(
            "SELECT * FROM workflow_prepared_plan WHERE prepared_id=?", (reference["prepared_id"],)
        ).fetchone()
        if row is None or (row["plan_id"], row["principal_id"]) != (
            reference["plan_id"],
            reference["principal_id"],
        ):
            raise WorkflowPlanRefused("run_plan_integrity", 409)
        private._verify_read_permissions(self.repository.path)
        attachment = connection.execute(
            "SELECT plan_id FROM workflow_run_plan_snapshot WHERE run_id=?", (run_id,)
        ).fetchone()
        if attachment is None or attachment["plan_id"] != row["plan_id"]:
            raise WorkflowPlanRefused("run_plan_attachment_missing", 409)
        document = json.loads(row["public_manifest_json"])
        if not execution_manifest.verify_v2_public(document):
            raise WorkflowPlanRefused("run_plan_integrity", 409)
        fields = self._fields(document)
        snapshot = private._load_verified(connection, row["plan_id"], fields)
        run = connection.execute(
            "SELECT manifest_json,spec_snapshot,tier FROM workflow_run WHERE run_id=?", (run_id,)
        ).fetchone()
        if run is None or run["manifest_json"] != row["public_manifest_json"]:
            raise WorkflowPlanRefused("run_plan_integrity", 409)
        exact = snapshot.material("artifact_hash").decode("utf-8")
        if run["tier"] == "script":
            matches = json.loads(run["spec_snapshot"])["source"] == exact
        else:
            import yaml

            from cli_agent_orchestrator.models.workflow import WorkflowSpec

            matches = (
                run["spec_snapshot"] == WorkflowSpec(**yaml.safe_load(exact)).model_dump_json()
            )
        if not matches:
            raise WorkflowPlanRefused("run_source_integrity", 409)
        return row, snapshot

    def start(
        self,
        principal,
        prepared_id,
        run_id,
        expected_plan_id,
        *,
        scan_dir=None,
        requested_name=None,
        requested_inputs=None,
        transaction_callback=None,
    ):
        import yaml

        from cli_agent_orchestrator.models.workflow import ScriptSpec, WorkflowSpec
        from cli_agent_orchestrator.services import approval_store, workflow_journal

        specs._validate_name(run_id)
        if self.origins is None:
            raise WorkflowPlanRefused("scoped_runtime_unavailable", 503)
        # Approval migration/verification is outside the borrowed transaction.
        try:
            with (
                closing(approval_store._connect()) as _owned_approval_connection,
                _owned_approval_connection as approval_connection,
            ):
                pass
        except sqlite3.Error:
            raise WorkflowPlanRefused("plan_approval_unavailable", 503) from None
        with self.repository.read_snapshot() as prior_connection:
            prior_row, _ = self._prepared(prior_connection, principal, prepared_id)
            workflow_name = prior_row["workflow_name"]
        with specs._authoring_lock(scan_dir):
            view = specs.get_workflow_source(workflow_name, scan_dir)
            with self.repository.transaction() as connection:
                row, snapshot = self._prepared(connection, principal, prepared_id)
                if row["plan_id"] != expected_plan_id:
                    raise WorkflowPlanRefused("prepared_plan_changed", 409)
                if (
                    connection.execute(
                        "SELECT 1 FROM workflow_plan_approval WHERE plan_id=?", (row["plan_id"],)
                    ).fetchone()
                    is None
                ):
                    raise WorkflowPlanRefused("plan_approval_required", 403)
                # Start refuses current source drift; resume reads the authorized exact snapshot.
                if view["source_hash"] != row["source_hash"]:
                    raise WorkflowPlanRefused("prepared_source_changed", 409)
                policy = snapshot.decode_material("policy")
                for entry in policy:
                    self._seed(connection, principal, entry, row["source_hash"])
                source = snapshot.material("artifact_hash").decode("utf-8")
                fields = self._fields(json.loads(row["public_manifest_json"]))
                inputs = self._inputs(snapshot, fields)
                if requested_name is not None and requested_name != row["workflow_name"]:
                    raise WorkflowPlanRefused("prepared_workflow_changed", 409)
                if requested_inputs and ids.canonical_component_bytes(
                    requested_inputs
                ) != ids.canonical_component_bytes(inputs):
                    raise WorkflowPlanRefused("prepared_inputs_changed", 409)
                now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                if row["tier"] == "script":
                    spec = ScriptSpec(
                        name=row["workflow_name"],
                        path=row["workflow_name"] + ".py",
                        source=source,
                        content_hash=row["source_hash"],
                        findings=[],
                        inputs=specs._extract_inputs(source),
                    )
                    spec_snapshot = json.dumps(
                        {"source": source, "path": spec.path, "content_hash": row["source_hash"]}
                    )
                else:
                    spec = WorkflowSpec(**yaml.safe_load(source))
                    spec_snapshot = spec.model_dump_json()
                from cli_agent_orchestrator.services.secret_gate import redact_json_leaves

                public_inputs = redact_json_leaves(inputs)
                workflow_journal.insert_run(
                    run_id,
                    spec.name,
                    spec_snapshot,
                    json.dumps(public_inputs),
                    "running",
                    now,
                    row["tier"],
                    "1",
                    row["public_manifest_json"],
                    connection=connection,
                )
                private._attach(connection, run_id, row["plan_id"])
                connection.execute(
                    "INSERT INTO workflow_scoped_run VALUES (?,?,?,?)",
                    (run_id, prepared_id, principal.id, row["plan_id"]),
                )
                if row["tier"] == "yaml":
                    for step in spec.steps:
                        connection.execute(
                            "INSERT INTO workflow_run_step(run_id,step_id,state,attempts,updated_at) VALUES (?,?,?,0,?)",
                            (run_id, step.id, "pending", now),
                        )
                token = self.origins.create_run_capability(
                    principal,
                    run_id=run_id,
                    workflow_id=spec.name,
                    tier=row["tier"],
                    run_generation=1,
                    spec_hash=row["source_hash"],
                    ttl_seconds=3600,
                    connection=connection,
                )
                if transaction_callback is not None:
                    transaction_callback(connection, run_id, row, inputs)
        return PreparedRun(spec, inputs, row["public_manifest_json"], token, now)

    def authorize_step(self, principal, run_id, step_id, target_key, request):
        self._owner(principal)
        with self.repository.transaction() as connection:
            from cli_agent_orchestrator.services.workflow_continuation_driver import check_effect

            check_effect(connection, run_id)
            stored = self._stored(connection, run_id)
            if stored is None:
                return None
            row, snapshot = stored
            if row["principal_id"] != principal.id:
                raise WorkflowPlanRefused("run_plan_owner_mismatch", 403)
            entries = snapshot.decode_material("policy")
            selected = [
                entry
                for entry in entries
                if (target_key is None or entry["target_key"] == target_key)
                and entry["profile_name"] == request["agent"]
            ]
            if len(selected) != 1:
                raise WorkflowPlanRefused("scope_binding_not_declared", 403)
            entry = selected[0]
            target_key = entry["target_key"]
            admit_launch_request(
                entry,
                **{
                    key: request.get(key)
                    for key in (
                        "provider",
                        "agent",
                        "model",
                        "allowed_tools",
                        "working_directory",
                        "reuse_terminal_id",
                        "use_worktree",
                        "engine",
                    )
                },
            )
            seed = self._seed(connection, principal, entry, row["source_hash"])
            prompt = request.get("prompt")
            if not isinstance(prompt, str) or not prompt or len(prompt.encode("utf-8")) > 32768:
                raise WorkflowPlanRefused("scope_delivery_invalid", 422)
            count = connection.execute(
                "SELECT COUNT(*) FROM workflow_plan_step_alias WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            prior = connection.execute(
                "SELECT * FROM workflow_plan_step_alias WHERE run_id=? AND step_id=?",
                (run_id, step_id),
            ).fetchone()
            if prior is None and count >= snapshot.decode_material("limits")["max_steps"]:
                raise WorkflowPlanRefused("scope_step_limit", 409)
            alias = "plan_" + hashlib.sha256((run_id + row["plan_id"]).encode()).hexdigest()[:40]
            provision = self.provisioning.derive_plan_step(
                connection,
                principal,
                seed,
                workflow_alias=alias,
                step_id=step_id,
                source_hash=row["source_hash"],
                run_id=run_id,
                expected_plan_id=row["plan_id"],
                prompt=prompt,
            )
            if prior is not None and (
                prior["target_key"],
                prior["agent_profile"],
                prior["provision_id"],
            ) != (target_key, request["agent"], provision.ref.id):
                raise WorkflowPlanRefused("scope_step_binding_changed", 409)
            connection.execute(
                "INSERT OR IGNORE INTO workflow_plan_step_alias VALUES (?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    step_id,
                    target_key,
                    request["agent"],
                    alias,
                    seed.ref.id,
                    seed.ref.revision,
                    provision.ref.id,
                ),
            )
        callback = self.origins.resolve_step_admitter(principal, alias, row["source_hash"], step_id)

        async def scoped_admitter(**kwargs):
            kwargs["workflow_id"] = alias
            return await callback(**kwargs)

        return scoped_admitter

    def alias_matches(self, connection, workflow_id, run_id):
        return (
            connection.execute(
                "SELECT 1 FROM workflow_plan_step_alias WHERE run_id=? AND workflow_alias=?",
                (run_id, workflow_id),
            ).fetchone()
            is not None
        )

    @staticmethod
    def _validate_derived_delivery(entry, delivery):
        template = dict(entry["delivery"])
        received = dict(delivery)
        try:
            original_payload = json.loads(template.pop("payload_json"))
            payload = json.loads(received.pop("payload_json"))
            prompt = payload.pop("message")
            original_payload.pop("message")
        except (ValueError, KeyError, TypeError):
            raise WorkflowPlanRefused("scope_material_changed", 403) from None
        if (
            template != received
            or payload != original_payload
            or not isinstance(prompt, str)
            or not prompt
            or len(prompt.encode("utf-8")) > 32768
        ):
            raise WorkflowPlanRefused("scope_material_changed", 403)

    def revalidate_origin(self, connection, handoff):
        from cli_agent_orchestrator.services.workflow_continuation_driver import check_effect

        check_effect(connection, handoff.run_id, handoff.run_generation)
        stored = self._stored(connection, handoff.run_id)
        if stored is None:
            return
        row, snapshot = stored
        if row["principal_id"] != handoff.principal.id:
            raise WorkflowPlanRefused("run_plan_owner_mismatch", 403)
        alias = connection.execute(
            "SELECT * FROM workflow_plan_step_alias WHERE run_id=? AND step_id=?",
            (handoff.run_id, handoff.step_id),
        ).fetchone()
        if alias is None or alias["provision_id"] != handoff.provision.ref.id:
            raise WorkflowPlanRefused("scope_step_proof_missing", 403)
        entry = next(
            (
                entry
                for entry in snapshot.decode_material("policy")
                if entry["target_key"] == alias["target_key"]
                and entry["profile_name"] == alias["agent_profile"]
            ),
            None,
        )
        if entry is None:
            raise WorkflowPlanRefused("scope_binding_not_declared", 403)
        self._seed(connection, handoff.principal, entry, row["source_hash"])
        if entry["contract_hash"] != handoff.provision.contract_hash:
            raise WorkflowPlanRefused("scope_material_changed", 403)
        self._validate_derived_delivery(
            entry, handoff.provision.delivery_template.model_dump(mode="json")
        )

    def revalidate_order(self, connection, binding, *, historical=False):
        return self.revalidate_attempt(
            connection, binding.work_attempt_id, binding.work_generation, historical=historical
        )

    def revalidate_attempt(self, connection, attempt_id, generation, *, historical=False):
        # The caller has already revalidated this exact durable Work order/grant.
        if (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_scoped_run'"
            ).fetchone()
            is None
        ):
            return
        binding = connection.execute(
            "SELECT * FROM work_workflow_step_bindings WHERE work_attempt_id=? AND work_generation=?",
            (attempt_id, generation),
        ).fetchone()
        if binding is None:
            return
        stored = self._stored(connection, binding["run_id"])
        if stored is None:
            return
        row, snapshot = stored
        if row["principal_id"] != binding["principal_id"]:
            raise WorkflowPlanRefused("run_plan_owner_mismatch", 403)
        alias = connection.execute(
            "SELECT * FROM workflow_plan_step_alias WHERE run_id=? AND step_id=?",
            (binding["run_id"], binding["step_id"]),
        ).fetchone()
        if alias is None or alias["provision_id"] != binding["provision_id"]:
            raise WorkflowPlanRefused("scope_step_proof_missing", 403)
        entry = next(
            (
                entry
                for entry in snapshot.decode_material("policy")
                if (entry["target_key"], entry["profile_name"])
                == (alias["target_key"], alias["agent_profile"])
            ),
            None,
        )
        if entry is None or entry["contract_hash"] != binding["contract_hash"]:
            raise WorkflowPlanRefused("scope_material_changed", 403)
        derived = connection.execute(
            "SELECT delivery_json FROM work_workflow_step_provisions WHERE id=?",
            (binding["provision_id"],),
        ).fetchone()
        if derived is None:
            raise WorkflowPlanRefused("scope_step_proof_missing", 403)
        self._validate_derived_delivery(entry, json.loads(derived["delivery_json"]))
        if historical:
            # Reads prove the immutable admitted plan/delivery. Revoking future
            # execution or changing a checkout cannot erase accepted evidence.
            # Every next physical effect still uses the live default below.
            return
        verify_dependency_closure(entry)
        self._validate_target(entry)
        seed = connection.execute(
            "SELECT * FROM work_workflow_step_provisions WHERE id=?", (entry["provision_id"],)
        ).fetchone()
        if (
            seed is None
            or seed["state"] != "active"
            or seed["provision_fingerprint"] != entry["provision_fingerprint"]
        ):
            raise WorkflowPlanRefused("plan_seed_changed", 403)
        for prefix, kind, actions in (
            ("workflow", "workflow", {"admit_step", "execute"}),
            ("receiver", "receiver", {"task_received", "task_result"}),
        ):
            self.provisioning._origin_revision(
                connection,
                subject_ref={
                    "subject_id": seed[prefix + "_subject_id"],
                    "kind": kind,
                    "revision": seed[prefix + "_subject_revision"],
                },
                authorization_ref={
                    "subject_id": seed[prefix + "_subject_id"],
                    "origin_kind": kind,
                    "revision": seed[prefix + "_authorization_revision"],
                },
                required_actions=actions,
                job_id=seed["job_id"],
                grant_id=seed["grant_id"] if prefix == "workflow" else seed["receiver_grant_id"],
                grant_revision=(
                    seed["grant_revision"]
                    if prefix == "workflow"
                    else seed["receiver_grant_revision"]
                ),
            )
        DelegationSnapshots._load_authorized(
            connection,
            connection.execute(
                "SELECT * FROM work_delegation_snapshots WHERE id=?", (binding["snapshot_id"],)
            ).fetchone(),
        )

    def validate_resume(self, principal, run_id):
        with self.repository.read_snapshot() as connection:
            stored = self._stored(connection, run_id)
            if stored is None:
                return None
            row, snapshot = stored
            self._owner(principal)
            if row["principal_id"] != principal.id:
                raise WorkflowPlanRefused("run_plan_owner_mismatch", 403)
            if (
                connection.execute(
                    "SELECT 1 FROM workflow_plan_approval WHERE plan_id=?", (row["plan_id"],)
                ).fetchone()
                is None
            ):
                raise WorkflowPlanRefused("plan_approval_required", 403)
            for entry in snapshot.decode_material("policy"):
                self._seed(connection, principal, entry, row["source_hash"])
            return self._inputs(snapshot, self._fields(json.loads(row["public_manifest_json"])))

    def step_admitters(self, principal, run_id, spec):
        callbacks = {}
        with self.repository.read_snapshot() as connection:
            stored = self._stored(connection, run_id)
            if stored is None:
                raise WorkflowPlanRefused("run_plan_attachment_missing", 409)
            entries = stored[1].decode_material("policy")
        for step in spec.steps:
            candidates = [
                entry
                for entry in entries
                if entry["profile_name"] == step.agent
                and (step.target_key is None or entry["target_key"] == step.target_key)
            ]
            if len(candidates) != 1:
                raise WorkflowPlanRefused("yaml_target_ambiguous")
            target_key = candidates[0]["target_key"]

            def build(step, target_key):
                async def callback(**request):
                    import asyncio

                    description = step.model_dump()
                    description["prompt"] = request.get("prompt")
                    admitter = await asyncio.to_thread(
                        self.authorize_step, principal, run_id, step.id, target_key, description
                    )
                    return await admitter(**request)

                return callback

            callbacks[step.id] = build(step, target_key)
        return callbacks

    @staticmethod
    def requires_scoped_validation(run_id, manifest_json):
        """Durable private references cannot be downgraded to a legacy manifest."""
        if execution_manifest.verify_v2_public(manifest_json):
            return True
        from contextlib import closing

        from cli_agent_orchestrator.services import workflow_journal

        with closing(workflow_journal._connect()) as connection:
            for table in ("workflow_scoped_run", "workflow_run_plan_snapshot"):
                if (
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                    ).fetchone()
                    and connection.execute(
                        "SELECT 1 FROM " + table + " WHERE run_id=? LIMIT 1", (run_id,)
                    ).fetchone()
                ):
                    raise WorkflowPlanRefused("run_manifest_integrity", 409)
        return False

    @staticmethod
    def guard_drive(record, *, source_path=None):
        from pathlib import Path

        from cli_agent_orchestrator.services import workflow_journal

        row = workflow_journal.get_run(record.run_id)
        if not WorkWorkflowPlans.requires_scoped_validation(
            record.run_id, getattr(row, "manifest_json", None)
        ):
            return
        if row is None:
            raise WorkflowPlanRefused("run_manifest_integrity", 409)
        owner = getattr(record, "scoped_plan_owner", None)
        principal = getattr(record, "scoped_principal", None)
        if type(owner) is not WorkWorkflowPlans or principal is None:
            raise WorkflowPlanRefused("scoped_runtime_unavailable", 503)
        generation = getattr(record, "generation", getattr(record, "run_generation", None))
        if str(generation) != row.generation or record.workflow_name != row.workflow_name:
            raise WorkflowPlanRefused("scoped_run_generation_changed", 409)
        inputs = owner.validate_resume(principal, record.run_id)
        with owner.repository.read_snapshot() as conn:
            _, snapshot = owner._stored(conn, record.run_id)
            source = snapshot.material("artifact_hash")
        if source_path is not None and Path(source_path).read_bytes() != source:
            raise WorkflowPlanRefused("run_source_integrity", 409)
        if hasattr(record, "inputs"):
            record.inputs = inputs
        return inputs

    def available_seeds(self, principal, name, *, scan_dir=None):
        """Read only public selector evidence; never elevate a read token."""
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
            raise WorkflowPlanRefused("plan_discovery_denied", 403)
        view = specs.get_workflow_source(name, scan_dir)
        result = []
        with self.repository.read_snapshot() as connection:
            self.provisioning._registered(connection, principal)
            rows = connection.execute(
                "SELECT * FROM work_workflow_step_provisions WHERE principal_id=? "
                "AND workflow_id=? AND spec_hash=? AND state='active' ORDER BY step_id,revision DESC",
                (principal.id, name, view["source_hash"]),
            ).fetchall()
            seen = set()
            for row in rows:
                if row["step_id"] in seen:
                    continue
                seen.add(row["step_id"])
                try:
                    seed = self.provisioning.resolve_workflow_step(
                        principal,
                        workflow_id=name,
                        step_id=row["step_id"],
                        spec_hash=view["source_hash"],
                        connection=connection,
                        _inspection=True,
                    )
                    if seed is None:
                        continue
                    profile = json.loads(seed.delivery_template.payload_json)["agent_profile"]
                except (ValueError, KeyError, TypeError, PermissionError):
                    continue
                result.append(
                    {
                        "step_id": seed.ref.step_id,
                        "provision_id": seed.ref.id,
                        "revision": seed.ref.revision,
                        "source_hash": seed.spec_hash,
                        "provider": seed.contract.provider,
                        "agent_profile": profile,
                        "job_id": seed.job_id,
                        "grant_id": seed.grant_id,
                        "grant_revision": seed.grant_revision,
                        "contract_hash": seed.contract_hash,
                        "snapshot_id": seed.snapshot_id,
                        "snapshot_hash": seed.snapshot_hash,
                    }
                )
        return {"workflow_name": name, "source_hash": view["source_hash"], "seeds": result}

    def provision_seed(self, actor, name, values, *, subject_token=None, scan_dir=None):
        """Authenticate subjects and call the existing provisioning authority."""
        WorkAuthority._principal(actor, admin=True)
        from cli_agent_orchestrator.security.auth import principal_from_token

        try:
            subject = principal_from_token(subject_token) if subject_token is not None else actor
        except Exception:
            # Authentication failures must never return token claims/bytes.
            raise WorkflowPlanRefused("workflow_subject_authentication_failed", 403) from None
        requested = dict(values)
        expected_hash = requested.pop("expected_source_hash")
        if isinstance(requested.get("contract"), dict):
            from cli_agent_orchestrator.services.work_contract import WorkContracts

            requested["contract"] = WorkContracts._from_json(
                json.dumps(
                    requested["contract"],
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
            )
        with specs._authoring_lock(scan_dir):
            source = specs.get_workflow_source(name, scan_dir)
            if source["source_hash"] != expected_hash:
                raise WorkflowPlanRefused("prepared_source_changed", 409)
            ref = self.provisioning.provision_workflow_step(
                actor,
                subject=subject,
                workflow_id=name,
                spec_hash=source["source_hash"],
                **requested,
            )
        return {
            "workflow_name": name,
            "step_id": ref.step_id,
            "provision_id": ref.id,
            "revision": ref.revision,
            "source_hash": source["source_hash"],
        }

    def delete_prepared(self, principal, prepared_id):
        self._owner(principal)
        from cli_agent_orchestrator.security.auth import SCOPE_ADMIN

        with self.repository.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_prepared_plan WHERE prepared_id=?", (prepared_id,)
            ).fetchone()
            if row is None:
                return
            if row["principal_id"] != principal.id and SCOPE_ADMIN not in principal.scopes:
                raise WorkflowPlanRefused("prepared_plan_owner_mismatch", 403)
            if connection.execute(
                "SELECT 1 FROM workflow_scoped_run WHERE prepared_id=? LIMIT 1", (prepared_id,)
            ).fetchone():
                raise WorkflowPlanRefused("prepared_plan_in_use", 409)
            connection.execute(
                "DELETE FROM workflow_prepared_plan WHERE prepared_id=?", (prepared_id,)
            )
            if (
                not connection.execute(
                    "SELECT 1 FROM workflow_prepared_plan WHERE plan_id=?", (row["plan_id"],)
                ).fetchone()
                and not connection.execute(
                    "SELECT 1 FROM workflow_run_plan_snapshot WHERE plan_id=?", (row["plan_id"],)
                ).fetchone()
            ):
                connection.execute(
                    "DELETE FROM workflow_plan_snapshot_component WHERE plan_id=?",
                    (row["plan_id"],),
                )
                connection.execute(
                    "DELETE FROM workflow_plan_snapshot WHERE plan_id=?", (row["plan_id"],)
                )
