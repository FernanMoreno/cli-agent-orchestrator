"""Explicit, durable Work origins for individual workflow step attempts.

Workflow identity is bound through a reserved Work idempotency namespace and the
existing immutable attempt/contract binding.  The Work item key commits to the
run generation, step and step attempt; every Work attempt generation is then
bound by ``work_dispatch_bindings`` to its exact grant, contract and snapshot.
This module admits work only.  It never interprets workflow journal state or
terminal telemetry as a Work receipt or result.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from uuid import uuid4

from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
from cli_agent_orchestrator.models.work_contract import (
    EffectiveWorkContract,
    EffectiveWorkContractV2,
)
from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope
from cli_agent_orchestrator.models.work_origin import (
    OriginAuthorizationRef,
    OriginSubjectRef,
)
from cli_agent_orchestrator.models.workflow_managed import (
    ProvisionedWorkflowStep,
    WorkflowStepBinding,
    WorkflowStepProvisionRef,
    WorkflowStepRetryAuthorization,
)
from cli_agent_orchestrator.security.auth import SCOPE_WRITE, Principal, _verified_principal
from cli_agent_orchestrator.services.work_authority import AuthorityDenied, WorkAuthority
from cli_agent_orchestrator.services.work_origin import (
    OriginDenied,
    WorkOriginAuthority,
)
from cli_agent_orchestrator.services.work_provisioning import (
    ProvisionDenied,
    ProvisionUnavailable,
    WorkProvisioning,
)

_KEY_PREFIX = "workflow-step-v1:"
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_WORKFLOW_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _positive(value, label: str) -> int:
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise OriginDenied(f"workflow {label} must be a positive bounded integer")
    return value


def _identity(value, label: str) -> str:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise OriginDenied(f"workflow {label} is invalid")
    return value


def _digest(value, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise OriginDenied(f"workflow {label} is invalid")
    return value


def _workflow_key(value, label: str) -> str:
    if not isinstance(value, str) or _WORKFLOW_KEY_RE.fullmatch(value) is None:
        raise OriginDenied(f"workflow {label} is invalid")
    return value


def _step_idempotency_key(
    *,
    tier: str,
    run_id: str,
    run_generation: int,
    workflow_id: str,
    spec_hash: str,
    step_id: str,
    workflow_step_attempt: int,
) -> str:
    identity = {
        "run_generation": run_generation,
        "run_id": run_id,
        "spec_hash": spec_hash,
        "step_id": step_id,
        "tier": tier,
        "workflow_id": workflow_id,
        "workflow_step_attempt": workflow_step_attempt,
    }
    return _KEY_PREFIX + hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _WorkflowStepHandoff:
    principal: Principal
    tier: str
    run_id: str
    run_generation: int
    workflow_id: str
    spec_hash: str
    step_id: str
    workflow_step_attempt: int
    provision: ProvisionedWorkflowStep
    delivery: WorkDeliveryEnvelope
    contract: EffectiveWorkContract | EffectiveWorkContractV2
    idempotency_key: str
    request_hash: str
    _origin: object = field(repr=False, compare=False)
    _seal: str = field(repr=False, compare=False)

    @property
    def job_id(self):
        return self.provision.job_id

    @property
    def grant_id(self):
        return self.provision.grant_id

    @property
    def grant_revision(self):
        return self.provision.grant_revision

    @property
    def lease_seconds(self):
        return self.provision.lease_seconds


class WorkWorkflowOrigins:
    """Pair explicit workflow subject/grant authority with one WorkAdmission.

    A workflow step must provide its registered subject and authorization
    revisions on every admission and recovery.  No lookup by workflow name,
    run ID, terminal ID or journal row can create this authority.
    """

    def __init__(self, repository: WorkRepository):
        if not isinstance(repository, WorkRepository):
            raise ValueError("verified Work repository required")
        self.repository = repository
        self.origin_authority = WorkOriginAuthority(repository)
        self.authority = WorkAuthority(repository)
        self.provisioning = WorkProvisioning(repository)
        self._secret = secrets.token_bytes(32)
        self._admission = None

    @staticmethod
    def _binding_from_row(row) -> WorkflowStepBinding:
        try:
            binding = WorkflowStepBinding(
                binding_id=row["binding_id"],
                binding_fingerprint=row["binding_fingerprint"],
                tier=row["tier"],
                run_id=row["run_id"],
                run_generation=row["run_generation"],
                workflow_id=row["workflow_id"],
                spec_hash=row["spec_hash"],
                step_id=row["step_id"],
                workflow_step_attempt=row["workflow_step_attempt"],
                principal_id=row["principal_id"],
                provision_id=row["provision_id"],
                provision_revision=row["provision_revision"],
                provision_fingerprint=row["provision_fingerprint"],
                workflow_subject_ref=OriginSubjectRef(
                    subject_id=row["workflow_subject_id"],
                    kind="workflow",
                    revision=row["workflow_subject_revision"],
                ),
                workflow_authorization_ref=OriginAuthorizationRef(
                    subject_id=row["workflow_subject_id"],
                    origin_kind="workflow",
                    revision=row["workflow_authorization_revision"],
                ),
                job_id=row["job_id"],
                grant_id=row["grant_id"],
                grant_revision=row["grant_revision"],
                contract_id=row["contract_id"],
                contract_hash=row["contract_hash"],
                snapshot_id=row["snapshot_id"],
                snapshot_hash=row["snapshot_hash"],
                delivery_id=row["delivery_id"],
                delivery_hash=row["delivery_hash"],
                work_item_id=row["work_item_id"],
                work_attempt_id=row["work_attempt_id"],
                work_generation=row["work_generation"],
                request_hash=row["request_hash"],
                idempotency_key=row["idempotency_key"],
                output_schema_json=row["output_schema_json"],
                output_schema_hash=row["output_schema_hash"],
                receiver_subject_ref=OriginSubjectRef(
                    subject_id=row["receiver_subject_id"],
                    kind="receiver",
                    revision=row["receiver_subject_revision"],
                ),
                receiver_authorization_ref=OriginAuthorizationRef(
                    subject_id=row["receiver_subject_id"],
                    origin_kind="receiver",
                    revision=row["receiver_authorization_revision"],
                ),
                receiver_grant_id=row["receiver_grant_id"],
                receiver_grant_revision=row["receiver_grant_revision"],
                receiver_received_action=row["receiver_received_action"],
                receiver_result_action=row["receiver_result_action"],
                created_at=row["created_at"],
            )
        except Exception as error:
            raise WorkConflict("workflow step binding is malformed") from error
        if not hmac.compare_digest(binding.binding_fingerprint, binding.computed_fingerprint()):
            raise WorkConflict("workflow step binding fingerprint differs")
        schema_json, schema_hash = binding.output_schema_json, binding.output_schema_hash
        if schema_json is not None:
            try:
                parsed = json.loads(schema_json)
                if not isinstance(parsed, dict) or _canonical(parsed) != schema_json:
                    raise ValueError("output schema is not canonical")
                if hashlib.sha256(schema_json.encode("utf-8")).hexdigest() != schema_hash:
                    raise ValueError("output schema digest differs")
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise WorkConflict("workflow output schema binding is malformed") from error
        return binding

    def _read_binding(self, connection, predicate: str, identity: tuple):
        if not connection.in_transaction:
            raise WorkConflict("workflow binding reads require a stable SQLite snapshot")
        self.repository._verify(connection)
        row = connection.execute(
            "SELECT * FROM work_workflow_step_bindings WHERE " + predicate,
            identity,
        ).fetchone()
        if row is None:
            return None
        binding = self._binding_from_row(row)
        provision = connection.execute(
            "SELECT * FROM work_workflow_step_provisions "
            "WHERE principal_id=? AND workflow_id=? AND step_id=? AND revision=? AND id=?",
            (
                binding.principal_id,
                binding.workflow_id,
                binding.step_id,
                binding.provision_revision,
                binding.provision_id,
            ),
        ).fetchone()
        work_binding = connection.execute(
            "SELECT binding.principal_id,binding.job_id,binding.grant_id,binding.grant_revision,"
            "binding.contract_id,binding.contract_hash,binding.snapshot_id,"
            "snapshot.delivered_hash AS snapshot_hash,binding.attempt_id,binding.generation,"
            "binding.work_item_id FROM work_dispatch_bindings AS binding "
            "JOIN work_delegation_snapshots AS snapshot ON snapshot.id=binding.snapshot_id "
            "WHERE binding.attempt_id=? AND binding.generation=?",
            (binding.work_attempt_id, binding.work_generation),
        ).fetchone()
        item = connection.execute(
            "SELECT job_id,request_hash,idempotency_key FROM work_items WHERE id=?",
            (binding.work_item_id,),
        ).fetchone()
        order = connection.execute(
            "SELECT delivery_hash,request_hash FROM work_delivery_orders "
            "WHERE attempt_id=? AND generation=?",
            (binding.work_attempt_id, binding.work_generation),
        ).fetchone()
        if (
            provision is None
            or provision["state"] != "active"
            or provision["provision_fingerprint"] != binding.provision_fingerprint
            or provision["spec_hash"] != binding.spec_hash
            or provision["contract_id"] != binding.contract_id
            or provision["contract_hash"] != binding.contract_hash
            or provision["snapshot_id"] != binding.snapshot_id
            or provision["snapshot_hash"] != binding.snapshot_hash
            or provision["workflow_subject_id"] != binding.workflow_subject_ref.subject_id
            or provision["workflow_subject_revision"] != binding.workflow_subject_ref.revision
            or provision["workflow_authorization_revision"]
            != binding.workflow_authorization_ref.revision
            or provision["job_id"] != binding.job_id
            or provision["grant_id"] != binding.grant_id
            or provision["grant_revision"] != binding.grant_revision
            or provision["output_schema_json"] != binding.output_schema_json
            or provision["output_schema_hash"] != binding.output_schema_hash
            or provision["receiver_subject_id"] != binding.receiver_subject_ref.subject_id
            or provision["receiver_subject_revision"] != binding.receiver_subject_ref.revision
            or provision["receiver_authorization_revision"]
            != binding.receiver_authorization_ref.revision
            or provision["receiver_grant_id"] != binding.receiver_grant_id
            or provision["receiver_grant_revision"] != binding.receiver_grant_revision
            or work_binding is None
            or (
                work_binding["principal_id"],
                work_binding["job_id"],
                work_binding["grant_id"],
                work_binding["grant_revision"],
                work_binding["contract_id"],
                work_binding["contract_hash"],
                work_binding["snapshot_id"],
                work_binding["snapshot_hash"],
                work_binding["attempt_id"],
                work_binding["generation"],
                work_binding["work_item_id"],
            )
            != (
                binding.principal_id,
                binding.job_id,
                binding.grant_id,
                binding.grant_revision,
                binding.contract_id,
                binding.contract_hash,
                binding.snapshot_id,
                binding.snapshot_hash,
                binding.work_attempt_id,
                binding.work_generation,
                binding.work_item_id,
            )
            or item is None
            or (item["job_id"], item["request_hash"], item["idempotency_key"])
            != (binding.job_id, binding.request_hash, binding.idempotency_key)
            or order is None
            or order["delivery_hash"] != binding.delivery_hash
            or binding.delivery_id != f"{binding.work_attempt_id}:{binding.work_generation}"
            or binding.idempotency_key
            != _step_idempotency_key(
                tier=binding.tier,
                run_id=binding.run_id,
                run_generation=binding.run_generation,
                workflow_id=binding.workflow_id,
                spec_hash=binding.spec_hash,
                step_id=binding.step_id,
                workflow_step_attempt=binding.workflow_step_attempt,
            )
        ):
            raise WorkConflict("workflow step binding differs from its Work attempt or provision")
        return binding

    def read_binding_for_attempt(
        self, attempt_id: str, generation: int, work_item_id: str, *, connection=None
    ) -> WorkflowStepBinding | None:
        """Read one immutable workflow binding from this exact Work attempt."""
        _identity(attempt_id, "Work attempt identity")
        _identity(work_item_id, "Work item identity")
        _positive(generation, "Work generation")
        if connection is not None:
            return self._read_binding(
                connection,
                "work_attempt_id=? AND work_generation=? AND work_item_id=?",
                (attempt_id, generation, work_item_id),
            )
        with self.repository.read_snapshot() as snapshot:
            return self._read_binding(
                snapshot,
                "work_attempt_id=? AND work_generation=? AND work_item_id=?",
                (attempt_id, generation, work_item_id),
            )

    def read_step_binding(
        self,
        tier: str,
        run_id: str,
        run_generation: int,
        step_id: str,
        workflow_step_attempt: int,
        *,
        connection=None,
    ) -> WorkflowStepBinding | None:
        """Read a run-step binding by its durable composite workflow identity."""
        if tier not in {"yaml", "script"}:
            raise WorkConflict("workflow binding tier is invalid")
        run_id = _workflow_key(run_id, "run identity")
        step_id = _workflow_key(step_id, "step identity")
        _positive(run_generation, "run generation")
        _positive(workflow_step_attempt, "step attempt")
        args = (tier, run_id, run_generation, step_id, workflow_step_attempt)
        if connection is not None:
            return self._read_binding(
                connection,
                "tier=? AND run_id=? AND run_generation=? AND step_id=? AND workflow_step_attempt=?",
                args,
            )
        with self.repository.read_snapshot() as snapshot:
            return self._read_binding(
                snapshot,
                "tier=? AND run_id=? AND run_generation=? AND step_id=? AND workflow_step_attempt=?",
                args,
            )

    def _bind_admission(self, admission) -> None:
        from cli_agent_orchestrator.services.work_admission import WorkAdmission

        if (
            not isinstance(admission, WorkAdmission)
            or admission.repository is not self.repository
            or (self._admission is not None and self._admission is not admission)
        ):
            raise OriginDenied("workflow origin endpoint belongs to another Work runtime")
        self._admission = admission

    @staticmethod
    def _principal_payload(principal: Principal) -> dict:
        return {
            "id": principal.id,
            "issuer": principal.issuer,
            "kind": principal.kind,
            "scopes": sorted(principal.scopes),
            "subject": principal.subject,
        }

    @staticmethod
    def _run_spec_hash(tier: str, spec_snapshot: str) -> str:
        if not isinstance(spec_snapshot, str):
            raise OriginDenied("workflow source snapshot is unavailable")
        if tier == "yaml":
            source = spec_snapshot.encode("utf-8")
        elif tier == "script":
            try:
                snapshot = json.loads(spec_snapshot)
                script_source = snapshot["source"]
                if type(script_source) is not str:
                    raise ValueError("script source is not text")
                source = script_source.encode("utf-8")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise OriginDenied("script workflow source snapshot is unavailable") from error
        else:
            raise OriginDenied("workflow tier is invalid")
        return hashlib.sha256(source).hexdigest()

    def _run_record(
        self,
        connection,
        *,
        workflow_id,
        spec_hash,
        tier,
        run_id,
        run_generation,
    ):
        row = connection.execute(
            "SELECT run_id,workflow_name,spec_snapshot,state,tier,generation,current_step_id "
            "FROM workflow_run WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if (
            row is None
            or row["workflow_name"] != workflow_id
            or row["state"] != "running"
            or row["tier"] != tier
            or row["generation"] != str(run_generation)
            or self._run_spec_hash(tier, row["spec_snapshot"]) != spec_hash
        ):
            raise OriginDenied("workflow run does not match its durable source and generation")
        return row

    def _step_marker(self, connection, handoff):
        run = self._run_record(
            connection,
            workflow_id=handoff.workflow_id,
            spec_hash=handoff.spec_hash,
            tier=handoff.tier,
            run_id=handoff.run_id,
            run_generation=handoff.run_generation,
        )
        step = connection.execute(
            "SELECT state,attempts FROM workflow_run_step WHERE run_id=? AND step_id=?",
            (handoff.run_id, handoff.step_id),
        ).fetchone()
        if (
            run["current_step_id"] != handoff.step_id
            or step is None
            or step["state"] != "work_pending"
            or step["attempts"] != handoff.workflow_step_attempt
        ):
            raise OriginDenied("workflow step lacks its durable pre-admission Work marker")

    def _make_handoff(
        self,
        *,
        principal,
        provision,
        tier,
        run_id,
        run_generation,
        step_id,
        workflow_step_attempt,
    ) -> _WorkflowStepHandoff:
        try:
            WorkAuthority._principal(principal)
        except AuthorityDenied as error:
            raise OriginDenied("verified workflow subject is required") from error
        if (
            not isinstance(provision, ProvisionedWorkflowStep)
            or not provision.has_valid_fingerprint()
        ):
            raise OriginDenied("current server-provisioned workflow step is required")
        if provision.ref.principal_id != principal.id:
            raise OriginDenied("workflow provision belongs to another authenticated principal")
        if tier not in {"yaml", "script"}:
            raise OriginDenied("workflow tier is invalid")
        run_id = _workflow_key(run_id, "run identity")
        run_generation = _positive(run_generation, "run generation")
        step_id = _workflow_key(step_id, "step identity")
        workflow_step_attempt = _positive(workflow_step_attempt, "step attempt")
        contract = provision.contract
        delivery = provision.delivery_template
        if (
            contract.operation_kind != "agent_step"
            or contract.canonical_hash() != provision.contract_hash
            or delivery.operation_kind != "agent_step"
            or delivery.adapter_version != provision.adapter_version
            or provision.ref.step_id != step_id
        ):
            raise OriginDenied("workflow provision does not match its frozen step contract")
        identity = {
            "authorization_revision": provision.workflow_authorization_ref.revision,
            "contract_hash": provision.contract_hash,
            "delivery_template_hash": provision.delivery_template_hash,
            "grant_id": provision.grant_id,
            "grant_revision": provision.grant_revision,
            "job_id": provision.job_id,
            "lease_seconds": provision.lease_seconds,
            "output_schema_hash": provision.output_schema_hash,
            "principal_id": principal.id,
            "provision_fingerprint": provision.provision_fingerprint,
            "provision_id": provision.ref.id,
            "provision_revision": provision.ref.revision,
            "receiver_authorization_revision": provision.receiver_authorization_ref.revision,
            "receiver_grant_id": provision.receiver_grant_id,
            "receiver_grant_revision": provision.receiver_grant_revision,
            "receiver_subject_id": provision.receiver_subject_ref.subject_id,
            "receiver_subject_revision": provision.receiver_subject_ref.revision,
            "run_generation": run_generation,
            "run_id": run_id,
            "snapshot_hash": provision.snapshot_hash,
            "snapshot_id": provision.snapshot_id,
            "spec_hash": provision.spec_hash,
            "step_id": step_id,
            "tier": tier,
            "workflow_authorization_revision": provision.workflow_authorization_ref.revision,
            "workflow_id": provision.ref.workflow_id,
            "workflow_step_attempt": workflow_step_attempt,
            "workflow_subject_revision": provision.workflow_subject_ref.revision,
        }
        idempotency_key = _step_idempotency_key(
            tier=tier,
            run_id=run_id,
            run_generation=run_generation,
            workflow_id=provision.ref.workflow_id,
            spec_hash=provision.spec_hash,
            step_id=step_id,
            workflow_step_attempt=workflow_step_attempt,
        )
        request_hash = hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()
        unsigned = {
            **identity,
            "delivery": delivery.model_dump(mode="json"),
            "idempotency_key": idempotency_key,
            "principal": self._principal_payload(principal),
        }
        seal = hmac.new(
            self._secret, _canonical(unsigned).encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return _WorkflowStepHandoff(
            principal=principal,
            tier=tier,
            run_id=run_id,
            run_generation=run_generation,
            workflow_id=provision.ref.workflow_id,
            spec_hash=provision.spec_hash,
            step_id=step_id,
            workflow_step_attempt=workflow_step_attempt,
            provision=provision,
            delivery=delivery,
            contract=contract,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            _origin=self,
            _seal=seal,
        )

    def _payload(self, handoff):
        provision = handoff.provision
        return {
            "authorization_revision": provision.workflow_authorization_ref.revision,
            "contract_hash": provision.contract_hash,
            "delivery": handoff.delivery.model_dump(mode="json"),
            "delivery_template_hash": provision.delivery_template_hash,
            "grant_id": provision.grant_id,
            "grant_revision": provision.grant_revision,
            "idempotency_key": handoff.idempotency_key,
            "job_id": provision.job_id,
            "lease_seconds": provision.lease_seconds,
            "output_schema_hash": provision.output_schema_hash,
            "principal": self._principal_payload(handoff.principal),
            "principal_id": handoff.principal.id,
            "provision_fingerprint": provision.provision_fingerprint,
            "provision_id": provision.ref.id,
            "provision_revision": provision.ref.revision,
            "receiver_authorization_revision": provision.receiver_authorization_ref.revision,
            "receiver_grant_id": provision.receiver_grant_id,
            "receiver_grant_revision": provision.receiver_grant_revision,
            "receiver_subject_id": provision.receiver_subject_ref.subject_id,
            "receiver_subject_revision": provision.receiver_subject_ref.revision,
            "run_generation": handoff.run_generation,
            "run_id": handoff.run_id,
            "snapshot_hash": provision.snapshot_hash,
            "snapshot_id": provision.snapshot_id,
            "spec_hash": provision.spec_hash,
            "step_id": handoff.step_id,
            "tier": handoff.tier,
            "workflow_authorization_revision": provision.workflow_authorization_ref.revision,
            "workflow_id": handoff.workflow_id,
            "workflow_step_attempt": handoff.workflow_step_attempt,
            "workflow_subject_revision": provision.workflow_subject_ref.revision,
        }

    def _accept_handoff(self, handoff):
        if (
            not isinstance(handoff, _WorkflowStepHandoff)
            or handoff._origin is not self
            or not hmac.compare_digest(
                handoff._seal,
                hmac.new(
                    self._secret,
                    _canonical(self._payload(handoff)).encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest(),
            )
        ):
            raise WorkConflict("managed workflow step requires a sealed provision-backed origin")
        return handoff

    def _revalidate(self, connection, handoff):
        """Recheck the exact provision, run snapshot, and pending step in admission's tx."""
        if not connection.in_transaction:
            raise WorkConflict("workflow origin validation requires caller transaction")
        self.repository._verify(connection)
        self._accept_handoff(handoff)
        try:
            provision = self.provisioning.resolve_workflow_step(
                handoff.principal,
                workflow_id=handoff.workflow_id,
                step_id=handoff.step_id,
                spec_hash=handoff.spec_hash,
                connection=connection,
                expected_ref=handoff.provision.ref,
                expected_fingerprint=handoff.provision.provision_fingerprint,
            )
        except (ProvisionDenied, ProvisionUnavailable) as error:
            raise OriginDenied("workflow step provision is absent, changed, or revoked") from error
        if provision is None or provision != handoff.provision:
            raise OriginDenied("workflow step provision is absent, changed, or revoked")
        self._step_marker(connection, handoff)
        try:
            chain, job = self.authority._chain(
                connection, provision.grant_id, provision.grant_revision
            )
        except Exception as error:
            raise OriginDenied("workflow grant ancestry is not live") from error
        if (
            job["id"] != provision.job_id
            or chain[0].principal_id != handoff.principal.id
            or provision.contract.provider not in chain[0].providers
            or handoff.delivery != provision.delivery_template
        ):
            raise OriginDenied("workflow authority does not permit this managed step")
        return provision

    def requires_run_capability(self, principal, workflow_id, spec_hash):
        """Select managed execution from durable provisions, rejecting stale authority."""
        self.provisioning._principal(principal)
        # Validate selectors even when no provision exists; absence grants no Work authority.
        from cli_agent_orchestrator.services.work_provisioning import _digest, _identity

        workflow_id = _identity(workflow_id, "workflow identity")
        _digest(spec_hash, "workflow spec hash")
        with self.repository.read_snapshot() as connection:
            self.repository._verify(connection)
            steps = connection.execute(
                "SELECT DISTINCT step_id FROM work_workflow_step_provisions "
                "WHERE principal_id=? AND workflow_id=?",
                (principal.id, workflow_id),
            ).fetchall()
            for row in steps:
                self.provisioning.resolve_workflow_step(
                    principal,
                    workflow_id=workflow_id,
                    step_id=row[0],
                    spec_hash=spec_hash,
                    connection=connection,
                )
            return bool(steps)

    def resolve_step_admitter(self, principal, workflow_id, spec_hash, step_id):
        """Resolve one server-provisioned step and return its sealed async callback."""
        provision = self.provisioning.resolve_workflow_step(
            principal,
            workflow_id=workflow_id,
            step_id=step_id,
            spec_hash=spec_hash,
        )
        if provision is None:
            return None

        async def admitter(**request):
            import asyncio

            return await asyncio.to_thread(
                self._admit_from_callback,
                principal,
                provision,
                request,
            )

        return admitter

    def _admit_from_callback(self, principal, provision, request):
        allowed = {
            "tier",
            "run_id",
            "run_generation",
            "step_id",
            "workflow_id",
            "spec_hash",
            "step_attempt",
            "workflow_step_attempt",
            "recover",
            "prompt",
            "step",
            "record",
            "inputs",
            "call_fingerprint",
            "call_data",
        }
        if set(request) - allowed:
            raise OriginDenied("workflow callback supplied unsupported selector fields")
        recover = request.pop("recover", False)
        if type(recover) is not bool:
            raise OriginDenied("workflow recovery flag is invalid")
        tier = request.get("tier")
        run_id = request.get("run_id")
        run_generation = request.get("run_generation")
        step_id = request.get("step_id")
        workflow_id = request.get("workflow_id")
        spec_hash = request.get("spec_hash")
        step_attempt = request.get("step_attempt")
        workflow_step_attempt = request.get("workflow_step_attempt")
        if (
            step_attempt is not None
            and workflow_step_attempt is not None
            and step_attempt != workflow_step_attempt
        ):
            raise OriginDenied("workflow step attempt is contradictory")
        attempt = workflow_step_attempt if workflow_step_attempt is not None else step_attempt
        if attempt is None:
            raise OriginDenied("workflow step attempt is required")
        if step_id != provision.ref.step_id:
            raise OriginDenied("workflow callback step does not match its provision")
        if workflow_id is not None and workflow_id != provision.ref.workflow_id:
            raise OriginDenied("workflow callback name does not match its provision")
        if spec_hash is not None and spec_hash != provision.spec_hash:
            raise OriginDenied("workflow callback source does not match its provision")
        if recover:
            work = self.recover_step(
                principal,
                provision.ref.workflow_id,
                provision.spec_hash,
                tier,
                run_id,
                run_generation,
                step_id,
                attempt,
            )
            if work is not None:
                return work
        return self.admit_step(
            principal,
            provision.ref.workflow_id,
            provision.spec_hash,
            tier,
            run_id,
            run_generation,
            step_id,
            attempt,
            expected_provision=provision,
        )

    def admit_step(
        self,
        principal=None,
        workflow_id=None,
        spec_hash=None,
        tier=None,
        run_id=None,
        run_generation=None,
        step_id=None,
        workflow_step_attempt=None,
        *,
        expected_provision=None,
        **legacy,
    ):
        """Admit from one exact provision, including the constrained legacy call shape."""
        if self._admission is None:
            raise WorkConflict("managed workflow WorkAdmission is unavailable")
        if legacy:
            allowed = {
                "subject_ref",
                "authorization_ref",
                "job_id",
                "grant_id",
                "grant_revision",
                "contract",
                "delivery",
                "lease_seconds",
                "step_attempt",
            }
            if set(legacy) - allowed:
                raise OriginDenied("workflow admission supplied unsupported authority fields")
            old_attempt = legacy.pop("step_attempt", None)
            if (
                workflow_step_attempt is not None
                and old_attempt is not None
                and old_attempt != workflow_step_attempt
            ):
                raise OriginDenied("workflow step attempt is contradictory")
            if workflow_step_attempt is None:
                workflow_step_attempt = old_attempt
            if principal is None:
                raise OriginDenied("verified workflow principal is required")
            if tier is None or spec_hash is None:
                if not isinstance(run_id, str) or run_generation is None:
                    raise OriginDenied("durable workflow source identity is required")
                with self.repository.read_snapshot() as connection:
                    self.repository._verify(connection)
                    durable = connection.execute(
                        "SELECT workflow_name,spec_snapshot,state,tier,generation FROM workflow_run "
                        "WHERE run_id=?",
                        (run_id,),
                    ).fetchone()
                    if (
                        durable is None
                        or durable["workflow_name"] != workflow_id
                        or durable["state"] != "running"
                        or durable["generation"] != str(run_generation)
                    ):
                        raise OriginDenied(
                            "workflow run does not match its durable source and generation"
                        )
                    durable_tier = durable["tier"]
                    durable_hash = self._run_spec_hash(durable_tier, durable["spec_snapshot"])
                if tier is None:
                    tier = durable_tier
                if spec_hash is None:
                    spec_hash = durable_hash
        try:
            provision = self.provisioning.resolve_workflow_step(
                principal,
                workflow_id=workflow_id,
                step_id=step_id,
                spec_hash=spec_hash,
                expected_ref=None if expected_provision is None else expected_provision.ref,
                expected_fingerprint=(
                    None if expected_provision is None else expected_provision.provision_fingerprint
                ),
            )
        except (ProvisionDenied, ProvisionUnavailable) as error:
            raise OriginDenied("workflow step provision is absent, changed, or revoked") from error
        if provision is None:
            raise OriginDenied("workflow step has no exact-source provision")
        if legacy:
            try:
                subject_ref = OriginSubjectRef.model_validate(legacy["subject_ref"])
                authorization_ref = OriginAuthorizationRef.model_validate(
                    legacy["authorization_ref"]
                )
                legacy_delivery = self._admission.deliveries.prepare(
                    legacy["delivery"], "agent_step", contract=provision.contract
                )
            except (KeyError, TypeError, ValueError) as error:
                raise OriginDenied(
                    "workflow admission requires exact registered origin refs"
                ) from error
            checks = {
                "subject_ref": subject_ref == provision.workflow_subject_ref,
                "authorization_ref": authorization_ref == provision.workflow_authorization_ref,
                "job_id": legacy.get("job_id") == provision.job_id,
                "grant_id": legacy.get("grant_id") == provision.grant_id,
                "grant_revision": legacy.get("grant_revision") == provision.grant_revision,
                "contract": legacy.get("contract") == provision.contract,
                "delivery": legacy_delivery == provision.delivery_template,
                "lease_seconds": legacy.get("lease_seconds") == provision.lease_seconds,
            }
            if not all(checks.values()):
                mismatch = ",".join(name for name, matches in checks.items() if not matches)
                raise OriginDenied(
                    "legacy workflow fields differ from the exact server provision: " + mismatch
                )
        handoff = self._make_handoff(
            principal=principal,
            provision=provision,
            tier=tier,
            run_id=run_id,
            run_generation=run_generation,
            step_id=step_id,
            workflow_step_attempt=workflow_step_attempt,
        )
        return self._admission.admit(
            principal=handoff.principal,
            job_id=provision.job_id,
            idempotency_key=handoff.idempotency_key,
            request_hash=handoff.request_hash,
            grant_id=provision.grant_id,
            expected_grant_revision=provision.grant_revision,
            contract=provision.contract,
            delivery=provision.delivery_template,
            lease_seconds=provision.lease_seconds,
            _workflow_origin=handoff,
        )

    def _bind_admitted(self, connection, handoff, *, work, contract_binding):
        """Persist the immutable workflow binding in WorkAdmission's transaction."""
        self._revalidate(connection, handoff)
        if (
            work["id"] is None
            or work["request_hash"] != handoff.request_hash
            or work["contract_id"] != handoff.contract.id
            or work["snapshot_id"] != handoff.contract.snapshot.id
            or len(work["attempts"]) != 1
        ):
            raise WorkConflict("new Work item differs from its workflow handoff")
        attempt = work["attempts"][0]
        if (
            contract_binding.work_item_id != work["id"]
            or contract_binding.attempt_id != attempt["id"]
            or contract_binding.generation != attempt["generation"]
            or contract_binding.contract_hash != handoff.provision.contract_hash
        ):
            raise WorkConflict("Work contract binding differs from its workflow provision")
        order = connection.execute(
            "SELECT delivery_hash,request_hash FROM work_delivery_orders "
            "WHERE attempt_id=? AND generation=?",
            (attempt["id"], attempt["generation"]),
        ).fetchone()
        if order is None or not isinstance(order["delivery_hash"], str):
            raise WorkConflict("workflow delivery was not durably bound")
        provision = handoff.provision
        provisional = WorkflowStepBinding(
            binding_id=uuid4().hex,
            binding_fingerprint="0" * 64,
            tier=handoff.tier,
            run_id=handoff.run_id,
            run_generation=handoff.run_generation,
            workflow_id=handoff.workflow_id,
            spec_hash=handoff.spec_hash,
            step_id=handoff.step_id,
            workflow_step_attempt=handoff.workflow_step_attempt,
            principal_id=handoff.principal.id,
            provision_id=provision.ref.id,
            provision_revision=provision.ref.revision,
            provision_fingerprint=provision.provision_fingerprint,
            workflow_subject_ref=provision.workflow_subject_ref,
            workflow_authorization_ref=provision.workflow_authorization_ref,
            job_id=provision.job_id,
            grant_id=provision.grant_id,
            grant_revision=provision.grant_revision,
            contract_id=provision.contract.id,
            contract_hash=provision.contract_hash,
            snapshot_id=provision.snapshot_id,
            snapshot_hash=provision.snapshot_hash,
            delivery_id=f"{attempt['id']}:{attempt['generation']}",
            delivery_hash=order["delivery_hash"],
            work_item_id=work["id"],
            work_attempt_id=attempt["id"],
            work_generation=attempt["generation"],
            request_hash=handoff.request_hash,
            idempotency_key=handoff.idempotency_key,
            output_schema_json=provision.output_schema_json,
            output_schema_hash=provision.output_schema_hash,
            receiver_subject_ref=provision.receiver_subject_ref,
            receiver_authorization_ref=provision.receiver_authorization_ref,
            receiver_grant_id=provision.receiver_grant_id,
            receiver_grant_revision=provision.receiver_grant_revision,
            receiver_received_action="task_received",
            receiver_result_action="task_result",
            created_at=time.time(),
        )
        binding = provisional.model_copy(
            update={"binding_fingerprint": provisional.computed_fingerprint()}
        )
        connection.execute(
            "INSERT INTO work_workflow_step_bindings "
            "(binding_id,schema_version,tier,run_id,run_generation,binding_fingerprint,workflow_id,spec_hash,step_id,"
            "workflow_step_attempt,principal_id,provision_id,provision_revision,provision_fingerprint,workflow_subject_id,"
            "workflow_subject_revision,workflow_authorization_kind,workflow_authorization_revision,job_id,grant_id,grant_revision,"
            "contract_id,contract_hash,snapshot_id,snapshot_hash,delivery_id,delivery_hash,work_item_id,work_attempt_id,work_generation,"
            "request_hash,idempotency_key,output_schema_json,output_schema_hash,receiver_subject_id,receiver_subject_revision,"
            "receiver_authorization_kind,receiver_authorization_revision,receiver_grant_id,receiver_grant_revision,"
            "receiver_received_action,receiver_result_action,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                binding.binding_id,
                1,
                binding.tier,
                binding.run_id,
                binding.run_generation,
                binding.binding_fingerprint,
                binding.workflow_id,
                binding.spec_hash,
                binding.step_id,
                binding.workflow_step_attempt,
                binding.principal_id,
                binding.provision_id,
                binding.provision_revision,
                binding.provision_fingerprint,
                binding.workflow_subject_ref.subject_id,
                binding.workflow_subject_ref.revision,
                "workflow",
                binding.workflow_authorization_ref.revision,
                binding.job_id,
                binding.grant_id,
                binding.grant_revision,
                binding.contract_id,
                binding.contract_hash,
                binding.snapshot_id,
                binding.snapshot_hash,
                binding.delivery_id,
                binding.delivery_hash,
                binding.work_item_id,
                binding.work_attempt_id,
                binding.work_generation,
                binding.request_hash,
                binding.idempotency_key,
                binding.output_schema_json,
                binding.output_schema_hash,
                binding.receiver_subject_ref.subject_id,
                binding.receiver_subject_ref.revision,
                "receiver",
                binding.receiver_authorization_ref.revision,
                binding.receiver_grant_id,
                binding.receiver_grant_revision,
                binding.receiver_received_action,
                binding.receiver_result_action,
                binding.created_at,
            ),
        )
        return binding

    def _confirm_replay(self, connection, handoff, work):
        if (
            work["request_hash"] != handoff.request_hash
            or work["job_id"] != handoff.provision.job_id
            or work["operation_kind"] != "agent_step"
            or work["contract_id"] != handoff.provision.contract.id
            or work["snapshot_id"] != handoff.provision.snapshot_id
            or len(work["attempts"]) != 1
        ):
            raise WorkConflict("workflow replay differs from its exact provision")
        attempt = work["attempts"][0]
        binding = self.read_binding_for_attempt(
            attempt["id"], attempt["generation"], work["id"], connection=connection
        )
        if (
            binding is None
            or binding.tier != handoff.tier
            or binding.run_id != handoff.run_id
            or binding.run_generation != handoff.run_generation
            or binding.workflow_id != handoff.workflow_id
            or binding.spec_hash != handoff.spec_hash
            or binding.step_id != handoff.step_id
            or binding.workflow_step_attempt != handoff.workflow_step_attempt
            or binding.provision_id != handoff.provision.ref.id
            or binding.provision_revision != handoff.provision.ref.revision
            or binding.provision_fingerprint != handoff.provision.provision_fingerprint
            or binding.request_hash != handoff.request_hash
            or binding.idempotency_key != handoff.idempotency_key
        ):
            raise WorkConflict("workflow replay has no matching immutable step binding")
        self._revalidate(connection, handoff)

    def recover_step(
        self,
        principal,
        workflow_id=None,
        spec_hash=None,
        tier=None,
        run_id=None,
        run_generation=None,
        step_id=None,
        workflow_step_attempt=None,
        **legacy,
    ):
        """Read and revalidate one exact binding; never create or dispatch Work."""
        allowed = {
            "subject_ref",
            "authorization_ref",
            "job_id",
            "grant_id",
            "grant_revision",
            "contract",
            "delivery",
            "lease_seconds",
            "step_attempt",
        }
        if set(legacy) - allowed:
            raise OriginDenied("workflow recovery supplied unsupported authority fields")
        old_attempt = legacy.pop("step_attempt", None)
        if (
            workflow_step_attempt is not None
            and old_attempt is not None
            and old_attempt != workflow_step_attempt
        ):
            raise OriginDenied("workflow step attempt is contradictory")
        if workflow_step_attempt is None:
            workflow_step_attempt = old_attempt
        if tier is None or spec_hash is None or workflow_id is None:
            if not isinstance(run_id, str) or run_generation is None:
                raise OriginDenied("durable workflow source identity is required")
            with self.repository.read_snapshot() as snapshot:
                durable = snapshot.execute(
                    "SELECT workflow_name,spec_snapshot,state,tier,generation FROM workflow_run "
                    "WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if (
                    durable is None
                    or durable["state"] != "running"
                    or durable["generation"] != str(run_generation)
                    or (workflow_id is not None and durable["workflow_name"] != workflow_id)
                ):
                    raise OriginDenied(
                        "workflow run does not match its durable source and generation"
                    )
                workflow_id = workflow_id or durable["workflow_name"]
                tier = tier or durable["tier"]
                spec_hash = spec_hash or self._run_spec_hash(tier, durable["spec_snapshot"])
        workflow_id = _identity(workflow_id, "workflow identity")
        _digest(spec_hash, "workflow spec hash")
        tier = tier if tier in {"yaml", "script"} else None
        if tier is None:
            raise OriginDenied("workflow tier is invalid")
        run_id = _workflow_key(run_id, "run identity")
        run_generation = _positive(run_generation, "run generation")
        step_id = _workflow_key(step_id, "step identity")
        workflow_step_attempt = _positive(workflow_step_attempt, "step attempt")
        with self.repository.read_snapshot() as connection:
            self.repository._verify(connection)
            self._run_record(
                connection,
                workflow_id=workflow_id,
                spec_hash=spec_hash,
                tier=tier,
                run_id=run_id,
                run_generation=run_generation,
            )
            binding = self.read_step_binding(
                tier,
                run_id,
                run_generation,
                step_id,
                workflow_step_attempt,
                connection=connection,
            )
            if binding is None:
                return None
            if (
                binding.workflow_id != workflow_id
                or binding.spec_hash != spec_hash
                or binding.principal_id != getattr(principal, "id", None)
            ):
                raise OriginDenied("workflow binding belongs to another run source or principal")
            try:
                provision = self.provisioning.resolve_workflow_step(
                    principal,
                    workflow_id=workflow_id,
                    step_id=step_id,
                    spec_hash=spec_hash,
                    connection=connection,
                    expected_ref=WorkflowStepProvisionRef(
                        id=binding.provision_id,
                        principal_id=binding.principal_id,
                        workflow_id=binding.workflow_id,
                        step_id=binding.step_id,
                        revision=binding.provision_revision,
                    ),
                    expected_fingerprint=binding.provision_fingerprint,
                )
            except (ProvisionDenied, ProvisionUnavailable) as error:
                raise OriginDenied("workflow binding provision is absent or revoked") from error
            if provision is None:
                raise OriginDenied("workflow binding provision is absent or revoked")
            if legacy:
                from cli_agent_orchestrator.services.work_delivery import WorkDeliveries

                try:
                    normalized_delivery = WorkDeliveries.envelope(
                        legacy.get("delivery"), "agent_step"
                    )
                except Exception as error:
                    raise OriginDenied("legacy recovery delivery is invalid") from error
                checks = {
                    "subject_ref": legacy.get("subject_ref") == provision.workflow_subject_ref,
                    "authorization_ref": legacy.get("authorization_ref")
                    == provision.workflow_authorization_ref,
                    "job_id": legacy.get("job_id") == provision.job_id,
                    "grant_id": legacy.get("grant_id") == provision.grant_id,
                    "grant_revision": legacy.get("grant_revision") == provision.grant_revision,
                    "contract": legacy.get("contract") == provision.contract,
                    "delivery": normalized_delivery == provision.delivery_template,
                    "lease_seconds": legacy.get("lease_seconds") == provision.lease_seconds,
                }
                for name, matches in checks.items():
                    if name in legacy and not matches:
                        raise OriginDenied(
                            "legacy recovery fields differ from the exact server provision"
                        )
            handoff = self._make_handoff(
                principal=principal,
                provision=provision,
                tier=tier,
                run_id=run_id,
                run_generation=run_generation,
                step_id=step_id,
                workflow_step_attempt=workflow_step_attempt,
            )
            self._revalidate(connection, handoff)
            work = self.repository._work(connection, binding.work_item_id)
            self._confirm_replay(connection, handoff, work)
            return work

    @staticmethod
    def _retry_authorization_from_row(row) -> WorkflowStepRetryAuthorization:
        try:
            authorization = WorkflowStepRetryAuthorization(
                authorization_id=row["authorization_id"],
                authorization_fingerprint=row["authorization_fingerprint"],
                binding_id=row["binding_id"],
                binding_fingerprint=row["binding_fingerprint"],
                tier=row["tier"],
                run_id=row["run_id"],
                run_generation=row["run_generation"],
                workflow_id=row["workflow_id"],
                spec_hash=row["spec_hash"],
                step_id=row["step_id"],
                workflow_step_attempt=row["workflow_step_attempt"],
                principal_id=row["principal_id"],
                provision_id=row["provision_id"],
                provision_revision=row["provision_revision"],
                provision_fingerprint=row["provision_fingerprint"],
                work_item_id=row["work_item_id"],
                work_attempt_id=row["work_attempt_id"],
                work_generation=row["work_generation"],
                authorized_at=row["authorized_at"],
            )
        except Exception as error:
            raise WorkConflict("workflow retry authorization is malformed") from error
        if not hmac.compare_digest(
            authorization.authorization_fingerprint,
            authorization.computed_fingerprint(),
        ):
            raise WorkConflict("workflow retry authorization fingerprint differs")
        return authorization

    def _validate_retry_context(
        self,
        connection,
        principal,
        *,
        workflow_id,
        spec_hash,
        tier,
        run_id,
        run_generation,
        step_id,
        workflow_step_attempt,
        work_attempt_id,
        work_generation,
        allow_pending_next,
    ):
        if not connection.in_transaction:
            raise WorkConflict("workflow retry authorization requires a stable transaction")
        self.repository._verify(connection)
        try:
            WorkAuthority._principal(principal)
            self.provisioning._registered(connection, principal)
        except (AuthorityDenied, OriginDenied, ProvisionDenied) as error:
            raise OriginDenied("verified workflow principal is no longer registered") from error
        binding = self.read_step_binding(
            tier,
            run_id,
            run_generation,
            step_id,
            workflow_step_attempt,
            connection=connection,
        )
        if (
            binding is None
            or binding.workflow_id != workflow_id
            or binding.spec_hash != spec_hash
            or binding.principal_id != principal.id
            or binding.work_attempt_id != work_attempt_id
            or binding.work_generation != work_generation
        ):
            raise OriginDenied("workflow retry does not match its exact prior binding")
        try:
            provision = self.provisioning.resolve_workflow_step(
                principal,
                workflow_id=workflow_id,
                step_id=step_id,
                spec_hash=spec_hash,
                connection=connection,
                expected_ref=WorkflowStepProvisionRef(
                    id=binding.provision_id,
                    principal_id=binding.principal_id,
                    workflow_id=binding.workflow_id,
                    step_id=binding.step_id,
                    revision=binding.provision_revision,
                ),
                expected_fingerprint=binding.provision_fingerprint,
            )
        except (ProvisionDenied, ProvisionUnavailable) as error:
            raise OriginDenied(
                "workflow retry provision or authority is absent or revoked"
            ) from error
        if provision is None:
            raise OriginDenied("workflow retry provision is absent or revoked")
        self._run_record(
            connection,
            workflow_id=workflow_id,
            spec_hash=spec_hash,
            tier=tier,
            run_id=run_id,
            run_generation=run_generation,
        )
        step = connection.execute(
            "SELECT state,attempts FROM workflow_run_step WHERE run_id=? AND step_id=?",
            (run_id, step_id),
        ).fetchone()
        state_is_failed = (
            step is not None
            and step["state"] == "failed"
            and step["attempts"] == workflow_step_attempt
        )
        state_is_replay = (
            allow_pending_next
            and step is not None
            and step["state"] == "work_pending"
            and step["attempts"] == workflow_step_attempt + 1
        )
        run = connection.execute(
            "SELECT current_step_id FROM workflow_run WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if (
            run is None
            or run["current_step_id"] != step_id
            or not (state_is_failed or state_is_replay)
        ):
            raise OriginDenied("workflow retry journal state is stale or no longer retryable")
        from cli_agent_orchestrator.services.work_service import WorkService

        work_state = WorkService(self.repository).read_workflow_step_state(
            binding, connection=connection
        )
        if (
            work_state.binding_id != binding.binding_id
            or work_state.work_item_id != binding.work_item_id
            or work_state.work_attempt_id != work_attempt_id
            or work_state.work_generation != work_generation
            or work_state.work_state != "failed"
            or work_state.attempt_state != "failed"
            or work_state.current_attempt_id != work_attempt_id
            or work_state.current_generation != work_generation
            or work_state.current_attempt_state != "failed"
            or work_state.accepted_result_id is not None
        ):
            raise OriginDenied("workflow retry requires the exact current failed Work attempt")
        return binding, provision

    def _read_retry_authorization(
        self,
        connection,
        principal,
        *,
        workflow_id,
        spec_hash,
        tier,
        run_id,
        run_generation,
        step_id,
        workflow_step_attempt,
        work_attempt_id,
        work_generation,
        allow_pending_next,
    ):
        binding, _provision = self._validate_retry_context(
            connection,
            principal,
            workflow_id=workflow_id,
            spec_hash=spec_hash,
            tier=tier,
            run_id=run_id,
            run_generation=run_generation,
            step_id=step_id,
            workflow_step_attempt=workflow_step_attempt,
            work_attempt_id=work_attempt_id,
            work_generation=work_generation,
            allow_pending_next=allow_pending_next,
        )
        row = connection.execute(
            "SELECT * FROM work_workflow_step_retry_authorizations WHERE binding_id=?",
            (binding.binding_id,),
        ).fetchone()
        if row is None:
            return None
        authorization = self._retry_authorization_from_row(row)
        if (
            authorization.binding_fingerprint != binding.binding_fingerprint
            or authorization.tier != tier
            or authorization.run_id != run_id
            or authorization.run_generation != run_generation
            or authorization.workflow_id != workflow_id
            or authorization.spec_hash != spec_hash
            or authorization.step_id != step_id
            or authorization.workflow_step_attempt != workflow_step_attempt
            or authorization.principal_id != principal.id
            or authorization.provision_id != binding.provision_id
            or authorization.provision_revision != binding.provision_revision
            or authorization.provision_fingerprint != binding.provision_fingerprint
            or authorization.work_item_id != binding.work_item_id
            or authorization.work_attempt_id != work_attempt_id
            or authorization.work_generation != work_generation
        ):
            raise WorkConflict("workflow retry authorization differs from its immutable binding")
        return authorization

    def authorize_step_retry(
        self,
        principal,
        *,
        workflow_id,
        spec_hash,
        tier,
        run_id,
        run_generation,
        step_id,
        workflow_step_attempt,
        work_attempt_id,
        work_generation,
    ) -> WorkflowStepRetryAuthorization:
        """Authorize one retry only from an exact current failed+failed binding."""
        workflow_id = _identity(workflow_id, "workflow identity")
        _digest(spec_hash, "workflow spec hash")
        if tier not in {"yaml", "script"}:
            raise OriginDenied("workflow tier is invalid")
        run_id = _workflow_key(run_id, "run identity")
        run_generation = _positive(run_generation, "run generation")
        step_id = _workflow_key(step_id, "step identity")
        workflow_step_attempt = _positive(workflow_step_attempt, "step attempt")
        work_attempt_id = _identity(work_attempt_id, "Work attempt identity")
        work_generation = _positive(work_generation, "Work generation")
        with self.repository.transaction() as connection:
            existing = self._read_retry_authorization(
                connection,
                principal,
                workflow_id=workflow_id,
                spec_hash=spec_hash,
                tier=tier,
                run_id=run_id,
                run_generation=run_generation,
                step_id=step_id,
                workflow_step_attempt=workflow_step_attempt,
                work_attempt_id=work_attempt_id,
                work_generation=work_generation,
                allow_pending_next=True,
            )
            if existing is not None:
                return existing
            binding, provision = self._validate_retry_context(
                connection,
                principal,
                workflow_id=workflow_id,
                spec_hash=spec_hash,
                tier=tier,
                run_id=run_id,
                run_generation=run_generation,
                step_id=step_id,
                workflow_step_attempt=workflow_step_attempt,
                work_attempt_id=work_attempt_id,
                work_generation=work_generation,
                allow_pending_next=False,
            )
            provisional = WorkflowStepRetryAuthorization(
                authorization_id=uuid4().hex,
                authorization_fingerprint="0" * 64,
                binding_id=binding.binding_id,
                binding_fingerprint=binding.binding_fingerprint,
                tier=tier,
                run_id=run_id,
                run_generation=run_generation,
                workflow_id=workflow_id,
                spec_hash=spec_hash,
                step_id=step_id,
                workflow_step_attempt=workflow_step_attempt,
                principal_id=principal.id,
                provision_id=provision.ref.id,
                provision_revision=provision.ref.revision,
                provision_fingerprint=provision.provision_fingerprint,
                work_item_id=binding.work_item_id,
                work_attempt_id=work_attempt_id,
                work_generation=work_generation,
                authorized_at=time.time(),
            )
            authorization = provisional.model_copy(
                update={"authorization_fingerprint": provisional.computed_fingerprint()}
            )
            connection.execute(
                "INSERT INTO work_workflow_step_retry_authorizations "
                "(authorization_id,authorization_fingerprint,binding_id,binding_fingerprint,tier,run_id,"
                "run_generation,workflow_id,spec_hash,step_id,workflow_step_attempt,principal_id,provision_id,"
                "provision_revision,provision_fingerprint,work_item_id,work_attempt_id,work_generation,"
                "authorized_at,schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    authorization.authorization_id,
                    authorization.authorization_fingerprint,
                    authorization.binding_id,
                    authorization.binding_fingerprint,
                    authorization.tier,
                    authorization.run_id,
                    authorization.run_generation,
                    authorization.workflow_id,
                    authorization.spec_hash,
                    authorization.step_id,
                    authorization.workflow_step_attempt,
                    authorization.principal_id,
                    authorization.provision_id,
                    authorization.provision_revision,
                    authorization.provision_fingerprint,
                    authorization.work_item_id,
                    authorization.work_attempt_id,
                    authorization.work_generation,
                    authorization.authorized_at,
                    1,
                ),
            )
            return authorization

    def read_step_retry_authorization(
        self,
        principal,
        *,
        workflow_id,
        spec_hash,
        tier,
        run_id,
        run_generation,
        step_id,
        workflow_step_attempt,
        work_attempt_id,
        work_generation,
        connection=None,
    ) -> WorkflowStepRetryAuthorization | None:
        """Re-read and revalidate one retry authorization inside a stable snapshot."""
        workflow_id = _identity(workflow_id, "workflow identity")
        _digest(spec_hash, "workflow spec hash")
        if tier not in {"yaml", "script"}:
            raise OriginDenied("workflow tier is invalid")
        run_id = _workflow_key(run_id, "run identity")
        run_generation = _positive(run_generation, "run generation")
        step_id = _workflow_key(step_id, "step identity")
        workflow_step_attempt = _positive(workflow_step_attempt, "step attempt")
        work_attempt_id = _identity(work_attempt_id, "Work attempt identity")
        work_generation = _positive(work_generation, "Work generation")
        if connection is not None:
            return self._read_retry_authorization(
                connection,
                principal,
                workflow_id=workflow_id,
                spec_hash=spec_hash,
                tier=tier,
                run_id=run_id,
                run_generation=run_generation,
                step_id=step_id,
                workflow_step_attempt=workflow_step_attempt,
                work_attempt_id=work_attempt_id,
                work_generation=work_generation,
                allow_pending_next=True,
            )
        with self.repository.read_snapshot() as snapshot:
            return self._read_retry_authorization(
                snapshot,
                principal,
                workflow_id=workflow_id,
                spec_hash=spec_hash,
                tier=tier,
                run_id=run_id,
                run_generation=run_generation,
                step_id=step_id,
                workflow_step_attempt=workflow_step_attempt,
                work_attempt_id=work_attempt_id,
                work_generation=work_generation,
                allow_pending_next=True,
            )

    def resolve_step_retry_authorization(
        self,
        principal,
        workflow_id,
        spec_hash,
        tier,
        run_id,
        run_generation,
        step_id,
        prior_workflow_step_attempt,
        work_attempt_id,
        work_generation,
    ) -> WorkflowStepRetryAuthorization | None:
        """Look up the exact durable authorization by the pre-retry fence tuple."""
        return self.read_step_retry_authorization(
            principal,
            workflow_id=workflow_id,
            spec_hash=spec_hash,
            tier=tier,
            run_id=run_id,
            run_generation=run_generation,
            step_id=step_id,
            workflow_step_attempt=prior_workflow_step_attempt,
            work_attempt_id=work_attempt_id,
            work_generation=work_generation,
        )

    def create_run_capability(
        self,
        principal,
        *,
        run_id,
        workflow_id,
        tier,
        run_generation,
        spec_hash,
        ttl_seconds=300,
    ) -> str:
        """Issue one hashed, expiring capability for an exact durable run generation."""
        try:
            WorkAuthority._principal(principal)
        except AuthorityDenied as error:
            raise OriginDenied("verified workflow principal is required") from error
        run_id = _workflow_key(run_id, "run identity")
        workflow_id = _identity(workflow_id, "workflow identity")
        run_generation = _positive(run_generation, "run generation")
        _digest(spec_hash, "workflow spec hash")
        if tier not in {"yaml", "script"}:
            raise OriginDenied("workflow tier is invalid")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise OriginDenied("workflow run capability lifetime is invalid")
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        now = time.time()
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self.provisioning._registered(connection, principal)
            self._run_record(
                connection,
                workflow_id=workflow_id,
                spec_hash=spec_hash,
                tier=tier,
                run_id=run_id,
                run_generation=run_generation,
            )
            revision = connection.execute(
                "SELECT COALESCE(MAX(revision),0)+1 FROM work_workflow_run_capabilities "
                "WHERE run_id=?",
                (run_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO work_workflow_run_capabilities "
                "(run_id,revision,schema_version,principal_id,workflow_id,tier,run_generation,spec_hash,"
                "credential_sha256,state,issued_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    revision,
                    1,
                    principal.id,
                    workflow_id,
                    tier,
                    run_generation,
                    spec_hash,
                    digest,
                    "active",
                    now,
                    now + ttl_seconds,
                ),
            )
        return token

    def authenticate_run_capability(self, run_id, run_generation, token) -> Principal:
        """Resolve a run capability only while its durable generation remains current."""
        run_id = _workflow_key(run_id, "run identity")
        run_generation = _positive(run_generation, "run generation")
        if type(token) is not str or not 32 <= len(token) <= 512:
            raise OriginDenied("workflow run capability is invalid")
        try:
            digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        except UnicodeEncodeError as error:
            raise OriginDenied("workflow run capability is invalid") from error
        with self.repository.read_snapshot() as connection:
            self.repository._verify(connection)
            row = connection.execute(
                "SELECT * FROM work_workflow_run_capabilities WHERE run_id=? AND run_generation=? "
                "AND credential_sha256=?",
                (run_id, run_generation, digest),
            ).fetchone()
            latest = connection.execute(
                "SELECT MAX(revision) FROM work_workflow_run_capabilities WHERE run_id=?",
                (run_id,),
            ).fetchone()[0]
            if (
                row is None
                or row["revision"] != latest
                or row["state"] != "active"
                or row["expires_at"] <= time.time()
            ):
                raise OriginDenied("workflow run capability is absent, stale, or revoked")
            self._run_record(
                connection,
                workflow_id=row["workflow_id"],
                spec_hash=row["spec_hash"],
                tier=row["tier"],
                run_id=run_id,
                run_generation=run_generation,
            )
            try:
                identity = connection.execute(
                    "SELECT issuer,subject,kind FROM work_principals WHERE id=?",
                    (row["principal_id"],),
                ).fetchone()
                if identity is None:
                    raise OriginDenied("workflow capability principal is unavailable")
                principal = _verified_principal(
                    identity["issuer"],
                    identity["subject"],
                    [SCOPE_WRITE],
                    identity["kind"],
                )
                self.provisioning._registered(connection, principal)
            except (AuthorityDenied, OriginDenied, ProvisionDenied) as error:
                raise OriginDenied("workflow run capability principal is unavailable") from error
            return principal
