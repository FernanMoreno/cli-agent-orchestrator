"""Internal durable launch provisioning; no transport, admission, or provider wiring."""

import hashlib
import json
import re
import secrets
import time
from contextlib import nullcontext
from uuid import uuid4

from pydantic import ValidationError

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_contract import EffectiveWorkContract
from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope
from cli_agent_orchestrator.models.work_origin import (
    OriginAuthorizationRef,
    OriginSubjectRef,
    ProvisionedLaunch,
    ProvisionRef,
)
from cli_agent_orchestrator.models.workflow_managed import (
    ProvisionedWorkflowStep,
    WorkflowStepProvisionRef,
)
from cli_agent_orchestrator.security.auth import Principal
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    SnapshotUnavailable,
)
from cli_agent_orchestrator.services.work_authority import (
    AuthorityDenied,
    GrantConflict,
    WorkAuthority,
)
from cli_agent_orchestrator.services.work_contract import ContractConflict, WorkContracts
from cli_agent_orchestrator.services.work_origin import OriginDenied, WorkOriginAuthority


class ProvisionDenied(PermissionError):
    """A verified principal or durable evidence set cannot provision a launch."""


class ProvisionConflict(ValueError):
    """The requested revision is stale or its immutable history conflicts."""


class ProvisionUnavailable(LookupError):
    """No active, currently valid provision exists for this principal and selector."""


class ProvisionSelectionUnavailable(ProvisionUnavailable):
    """No unique active provision exists for an omitted selection."""


def _identity(value, label):
    if (
        type(value) is not str
        or not value
        or len(value) > 128
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
            for character in value
        )
    ):
        raise ProvisionDenied(f"invalid {label}")
    return value


def _exact_positive(value, label, *, maximum=2**63 - 1):
    if type(value) is not int or not 0 < value <= maximum:
        raise ProvisionDenied(f"invalid {label}")
    return value


def _digest(value, label):
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ProvisionDenied(f"invalid {label}")
    return value


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _workflow_delivery(value, *, adapter_version):
    try:
        envelope = WorkDeliveryEnvelope.model_validate(value)

        def unique_pairs(pairs):
            parsed = {}
            for key, item in pairs:
                if key in parsed:
                    raise ValueError("duplicate delivery key")
                parsed[key] = item
            return parsed

        parsed = json.loads(envelope.payload_json, object_pairs_hook=unique_pairs)
        if not isinstance(parsed, dict):
            raise ValueError("delivery payload must be an object")
        if adapter_version == 1:
            if set(parsed) != {"terminal_id", "agent_profile", "message"}:
                raise ValueError("v1 workflow delivery has the wrong fields")
            from cli_agent_orchestrator.services.work_agent_step import AgentStepPayload

            payload = AgentStepPayload.model_validate(parsed)
        elif adapter_version == 2:
            if set(parsed) != {"agent_profile", "message"}:
                raise ValueError("v2 workflow delivery has the wrong fields")
            if (
                type(parsed["agent_profile"]) is not str
                or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", parsed["agent_profile"]) is None
                or type(parsed["message"]) is not str
                or not parsed["message"]
                or len(parsed["message"]) > 32768
            ):
                raise ValueError("v2 workflow task data is invalid")
            payload = parsed
        else:
            raise ValueError("unsupported workflow delivery adapter")
        if envelope.operation_kind != "agent_step" or envelope.adapter_version != adapter_version:
            raise ValueError("workflow delivery adapter differs from its operation")
        canonical_payload = _canonical(
            payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
        )
        canonical_envelope = WorkDeliveryEnvelope(
            operation_kind=envelope.operation_kind,
            adapter_version=envelope.adapter_version,
            payload_json=canonical_payload,
        )
        encoded = _canonical(canonical_envelope.model_dump(mode="json"))
        return canonical_envelope, encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    except Exception as error:
        raise ProvisionDenied("workflow step requires a closed versioned delivery") from error


class WorkProvisioning:
    """Create, replace, retire, and reconstruct internal v1 launch provisions.

    Every mutation runs in the work repository transaction and validates the
    current job, live grant chain, canonical contract, and frozen snapshot before
    an immutable history row is inserted.  It deliberately does not compose a
    runtime registry or make any route/provider effective.
    """

    def __init__(self, repository: WorkRepository):
        if not isinstance(repository, WorkRepository):
            raise ValueError("verified work repository required")
        self.repository = repository

    @staticmethod
    def _principal(principal, *, admin=False):
        try:
            WorkAuthority._principal(principal, admin=admin)
        except AuthorityDenied as error:
            raise ProvisionDenied("verified principal lacks provisioning authority") from error
        return principal

    @staticmethod
    def _inspection_principal(principal):
        """Authenticate inspection without granting provisioning authority."""
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
            raise ProvisionDenied("verified principal lacks inspection authority")
        return principal

    @staticmethod
    def _registered(connection, principal):
        if not isinstance(principal, Principal):
            raise ProvisionDenied("verified principal required")
        row = connection.execute(
            "SELECT issuer,subject,kind FROM work_principals WHERE id=?", (principal.id,)
        ).fetchone()
        if row is None or tuple(row) != (principal.issuer, principal.subject, principal.kind):
            raise ProvisionDenied("principal is not durably registered")

    @staticmethod
    def _contract(value, *, operation_kind="launch"):
        try:
            contract = WorkContracts._contract(value)
        except (ContractConflict, ValidationError, TypeError, ValueError) as error:
            raise ProvisionDenied("canonical launch contract required") from error
        if contract.operation_kind != operation_kind:
            raise ProvisionDenied(f"provisioning requires a {operation_kind} contract")
        return contract

    def _validate(
        self,
        connection,
        *,
        admin=None,
        subject,
        job_id,
        grant_id,
        grant_revision,
        contract,
        issuer_id=None,
        operation_kind="launch",
        _inspection=False,
    ):
        """Validate one exact durable configuration without inferring absent settings."""
        self.repository._verify(connection)
        if admin is not None:
            self._principal(admin, admin=True)
            self._registered(connection, admin)
        if _inspection:
            if admin is not None:
                raise ProvisionDenied("inspection cannot provision authority")
            self._inspection_principal(subject)
        else:
            self._principal(subject)
        self._registered(connection, subject)
        _identity(job_id, "job id")
        _identity(grant_id, "grant id")
        _exact_positive(grant_revision, "grant revision")
        contract = self._contract(contract, operation_kind=operation_kind)
        try:
            job = self.repository._job(connection, job_id)
            chain, chained_job = WorkAuthority(self.repository)._chain(
                connection, grant_id, grant_revision
            )
        except (AuthorityDenied, GrantConflict, KeyError, ValueError) as error:
            raise ProvisionDenied("job or live grant is unavailable") from error
        if (
            chained_job["id"] != job["id"]
            or chain[0].principal_id != subject.id
            or contract.provider not in job["allowed_providers"]
            or contract.provider not in chain[0].providers
            or not all(
                WorkContracts._permissions(contract).is_subset_of(grant.permissions)
                for grant in chain
            )
            or (admin is not None and job["principal_id"] != admin.id)
            or (issuer_id is not None and job["principal_id"] != issuer_id)
        ):
            raise ProvisionDenied("launch provision exceeds current ownership or grant")
        try:
            snapshot = DelegationSnapshots._load_authorized(
                connection,
                connection.execute(
                    "SELECT * FROM work_delegation_snapshots WHERE id=?", (contract.snapshot.id,)
                ).fetchone(),
            )
        except (SnapshotUnavailable, ValueError) as error:
            raise ProvisionDenied("frozen launch snapshot is unavailable") from error
        if (
            snapshot.job_id != job["id"]
            or snapshot.contract_id != contract.id
            or snapshot.delivered_hash != contract.snapshot.delivered_hash
        ):
            raise ProvisionDenied("frozen launch snapshot does not match its contract")
        return job, contract, snapshot

    @staticmethod
    def _latest(connection, principal_id, selector):
        return connection.execute(
            "SELECT * FROM work_launch_provisions WHERE principal_id=? AND selector=? "
            "ORDER BY revision DESC LIMIT 1",
            (principal_id, selector),
        ).fetchone()

    @staticmethod
    def _ref(row):
        return ProvisionRef(
            schema_version=row["schema_version"],
            id=row["id"],
            principal_id=row["principal_id"],
            selector=row["selector"],
            revision=row["revision"],
        )

    def _view(self, connection, row):
        try:
            contract = self._contract(WorkContracts._from_json(row["contract_json"]))
        except (ContractConflict, ValidationError, TypeError, ValueError, ProvisionDenied) as error:
            raise ProvisionUnavailable("durable launch provision is invalid") from error
        if (
            row["state"] != "active"
            or row["contract_json"] != contract.canonical_json()
            or row["contract_hash"] != contract.canonical_hash()
            or row["contract_id"] != contract.id
            or row["snapshot_id"] != contract.snapshot.id
            or row["snapshot_hash"] != contract.snapshot.delivered_hash
        ):
            raise ProvisionUnavailable("durable launch provision is unavailable")
        # Do not reconstruct a Principal from SQLite.  Validate the stored
        # principal record structurally and fresh grant/snapshot evidence below.
        principal = connection.execute(
            "SELECT issuer,subject,kind FROM work_principals WHERE id=?", (row["principal_id"],)
        ).fetchone()
        if principal is None:
            raise ProvisionUnavailable("durable provision subject is unavailable")
        try:
            chain, job = WorkAuthority(self.repository)._chain(
                connection, row["grant_id"], row["grant_revision"]
            )
            snapshot = DelegationSnapshots._load_authorized(
                connection,
                connection.execute(
                    "SELECT * FROM work_delegation_snapshots WHERE id=?", (row["snapshot_id"],)
                ).fetchone(),
            )
        except (AuthorityDenied, GrantConflict, SnapshotUnavailable, ValueError) as error:
            raise ProvisionUnavailable("durable launch provision is no longer valid") from error
        if (
            job["id"] != row["job_id"]
            or job["principal_id"] != row["issuer_id"]
            or chain[0].principal_id != row["principal_id"]
            or contract.provider not in job["allowed_providers"]
            or contract.provider not in chain[0].providers
            or not all(
                WorkContracts._permissions(contract).is_subset_of(grant.permissions)
                for grant in chain
            )
            or snapshot.job_id != row["job_id"]
            or snapshot.contract_id != contract.id
            or snapshot.delivered_hash != row["snapshot_hash"]
        ):
            raise ProvisionUnavailable("durable launch provision is no longer valid")
        return ProvisionedLaunch(
            schema_version=row["schema_version"],
            ref=self._ref(row),
            job_id=row["job_id"],
            grant_id=row["grant_id"],
            grant_revision=row["grant_revision"],
            contract=contract,
            contract_hash=row["contract_hash"],
            snapshot_id=row["snapshot_id"],
            snapshot_hash=row["snapshot_hash"],
            adapter_version=row["adapter_version"],
            lease_seconds=row["lease_seconds"],
        )

    def _insert(
        self,
        connection,
        *,
        prior,
        subject,
        selector,
        admin,
        job,
        grant_id,
        grant_revision,
        contract,
        snapshot,
        adapter_version,
        lease_seconds,
        state,
    ):
        revision = 1 if prior is None else prior["revision"] + 1
        identifier = uuid4().hex if prior is None else prior["id"]
        connection.execute(
            "INSERT INTO work_launch_provisions "
            "(principal_id,selector,revision,id,schema_version,state,issuer_id,job_id,grant_id,grant_revision,contract_id,contract_json,contract_hash,snapshot_id,snapshot_hash,adapter_version,lease_seconds,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                subject.id,
                selector,
                revision,
                identifier,
                1,
                state,
                admin.id,
                job["id"],
                grant_id,
                grant_revision,
                contract.id,
                contract.canonical_json(),
                contract.canonical_hash(),
                snapshot.id,
                snapshot.delivered_hash,
                adapter_version,
                lease_seconds,
                time.time(),
            ),
        )
        self.repository._append_event(
            connection,
            job_id=job["id"],
            actor_id=admin.id,
            event_type=f"launch.provision.{state}",
            metadata={
                "principal_id": subject.id,
                "provision_id": identifier,
                "revision": revision,
                "contract_hash": contract.canonical_hash(),
            },
        )
        return self._ref(self._latest(connection, subject.id, selector))

    def provision_launch(
        self,
        admin_context,
        *,
        subject,
        selector,
        expected_revision,
        job_id,
        grant_id,
        grant_revision,
        contract,
        adapter_version,
        lease_seconds,
    ):
        """Create revision one or CAS-replace an active internal launch provision."""
        _identity(selector, "selector")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ProvisionConflict("expected provision revision must be a nonnegative integer")
        _exact_positive(adapter_version, "adapter version")
        _exact_positive(lease_seconds, "lease seconds", maximum=3600)
        with self.repository.transaction() as connection:
            prior = self._latest(connection, getattr(subject, "id", None), selector)
            if prior is None:
                if expected_revision != 0:
                    raise ProvisionConflict("provision does not exist at the expected revision")
            elif prior["revision"] != expected_revision:
                raise ProvisionConflict("provision revision changed")
            elif prior["state"] != "active":
                raise ProvisionConflict("retired provision cannot be replaced")
            job, canonical, snapshot = self._validate(
                connection,
                admin=admin_context,
                subject=subject,
                job_id=job_id,
                grant_id=grant_id,
                grant_revision=grant_revision,
                contract=contract,
            )
            return self._insert(
                connection,
                prior=prior,
                subject=subject,
                selector=selector,
                admin=admin_context,
                job=job,
                grant_id=grant_id,
                grant_revision=grant_revision,
                contract=canonical,
                snapshot=snapshot,
                adapter_version=adapter_version,
                lease_seconds=lease_seconds,
                state="active",
            )

    def retire_launch(self, admin_context, *, subject, selector, expected_revision):
        """CAS-retire an active provision by appending immutable history only."""
        _identity(selector, "selector")
        if type(expected_revision) is not int or expected_revision <= 0:
            raise ProvisionConflict("expected provision revision must be positive")
        with self.repository.transaction() as connection:
            prior = self._latest(connection, getattr(subject, "id", None), selector)
            if (
                prior is None
                or prior["revision"] != expected_revision
                or prior["state"] != "active"
                or prior["principal_id"] != getattr(subject, "id", None)
            ):
                raise ProvisionConflict("provision revision changed")
            try:
                stored_contract = WorkContracts._from_json(prior["contract_json"])
            except (ContractConflict, ValidationError, TypeError, ValueError) as error:
                raise ProvisionDenied("stored provision contract is invalid") from error
            job, canonical, snapshot = self._validate(
                connection,
                admin=admin_context,
                subject=subject,
                job_id=prior["job_id"],
                grant_id=prior["grant_id"],
                grant_revision=prior["grant_revision"],
                contract=stored_contract,
            )
            if (
                prior["contract_hash"] != canonical.canonical_hash()
                or prior["snapshot_id"] != snapshot.id
                or prior["snapshot_hash"] != snapshot.delivered_hash
            ):
                raise ProvisionDenied("stored provision evidence is mixed")
            return self._insert(
                connection,
                prior=prior,
                subject=subject,
                selector=selector,
                admin=admin_context,
                job=job,
                grant_id=prior["grant_id"],
                grant_revision=prior["grant_revision"],
                contract=canonical,
                snapshot=snapshot,
                adapter_version=prior["adapter_version"],
                lease_seconds=prior["lease_seconds"],
                state="retired",
            )

    @staticmethod
    def _origin_revision(
        connection,
        *,
        subject_ref,
        authorization_ref,
        required_actions,
        job_id,
        grant_id=None,
        grant_revision=None,
    ):
        try:
            subject_ref = OriginSubjectRef.model_validate(subject_ref)
            authorization_ref = OriginAuthorizationRef.model_validate(authorization_ref)
        except Exception as error:
            raise ProvisionDenied(
                "explicit origin subject and authorization refs are required"
            ) from error
        subject = connection.execute(
            "SELECT * FROM work_origin_subjects WHERE subject_id=? ORDER BY revision DESC LIMIT 1",
            (subject_ref.subject_id,),
        ).fetchone()
        authorization = connection.execute(
            "SELECT * FROM work_origin_authorizations WHERE subject_id=? AND origin_kind=? "
            "ORDER BY revision DESC LIMIT 1",
            (authorization_ref.subject_id, authorization_ref.origin_kind),
        ).fetchone()
        if (
            subject is None
            or subject["revision"] != subject_ref.revision
            or subject["origin_kind"] != subject_ref.kind
            or subject["state"] != "active"
            or authorization is None
            or authorization["revision"] != authorization_ref.revision
            or authorization["subject_revision"] != subject_ref.revision
            or authorization["state"] != "active"
            or authorization["expires_at"] <= time.time()
            or authorization["issuer_id"] != subject["issuer_id"]
            or authorization["job_id"] != job_id
            or (grant_id is not None and authorization["grant_id"] != grant_id)
            or (grant_revision is not None and authorization["grant_revision"] != grant_revision)
        ):
            raise ProvisionDenied(
                "origin subject or authorization is stale, revoked, or unavailable"
            )
        try:
            actions = set(json.loads(authorization["actions"]))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ProvisionDenied("origin authorization is corrupt") from error
        if not required_actions.issubset(actions):
            raise ProvisionDenied("origin authorization lacks required actions")
        return subject_ref, authorization_ref, authorization

    @staticmethod
    def _workflow_provision_ref(row):
        return WorkflowStepProvisionRef(
            id=row["id"],
            principal_id=row["principal_id"],
            workflow_id=row["workflow_id"],
            step_id=row["step_id"],
            revision=row["revision"],
        )

    def provision_workflow_step(
        self,
        admin_context,
        *,
        subject,
        workflow_id,
        step_id,
        expected_revision,
        spec_hash,
        job_id,
        grant_id,
        grant_revision,
        subject_ref,
        authorization_ref,
        receiver_subject_ref,
        receiver_authorization_ref,
        contract,
        delivery,
        output_schema=None,
        adapter_version=1,
        lease_seconds=300,
    ):
        """Append a server-owned `(workflow, step)` selector for one exact source."""
        workflow_id = _identity(workflow_id, "workflow identity")
        step_id = _identity(step_id, "workflow step identity")
        _digest(spec_hash, "workflow spec hash")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ProvisionConflict("workflow provision revision must be nonnegative")
        _exact_positive(adapter_version, "workflow adapter version")
        _exact_positive(lease_seconds, "workflow lease", maximum=3600)
        contract = self._contract(contract, operation_kind="agent_step")
        if adapter_version == 1 and not isinstance(contract, EffectiveWorkContract):
            raise ProvisionDenied("agent_step adapter v1 requires a frozen v1 contract")
        if adapter_version == 2:
            from cli_agent_orchestrator.models.work_contract import EffectiveWorkContractV2

            if (
                not isinstance(contract, EffectiveWorkContractV2)
                or len(contract.executable_identities) != 1
            ):
                raise ProvisionDenied(
                    "agent_step adapter v2 requires one frozen executable identity"
                )
        if adapter_version not in {1, 2}:
            raise ProvisionDenied("unsupported workflow step adapter version")
        delivery, delivery_json, delivery_hash = _workflow_delivery(
            delivery, adapter_version=adapter_version
        )
        output_schema_json, output_schema_hash = self._workflow_output_schema(output_schema)

        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            prior = self._workflow_step_latest(connection, subject.id, workflow_id, step_id)
            if prior is None:
                if expected_revision != 0:
                    raise ProvisionConflict(
                        "workflow step provision is absent at expected revision"
                    )
            elif prior["revision"] != expected_revision or prior["state"] != "active":
                raise ProvisionConflict("workflow step provision revision changed or is retired")
            if (
                self._workflow_step_for_spec(
                    connection, subject.id, workflow_id, step_id, spec_hash
                )
                is not None
            ):
                raise ProvisionConflict("workflow source already has an immutable step provision")
            job, canonical_contract, snapshot = self._validate(
                connection,
                admin=admin_context,
                subject=subject,
                job_id=job_id,
                grant_id=grant_id,
                grant_revision=grant_revision,
                contract=contract,
                operation_kind="agent_step",
            )
            workflow_subject_ref, workflow_authorization_ref, workflow_authorization = (
                self._origin_revision(
                    connection,
                    subject_ref=subject_ref,
                    authorization_ref=authorization_ref,
                    required_actions={"admit_step", "execute"},
                    job_id=job["id"],
                    grant_id=grant_id,
                    grant_revision=grant_revision,
                )
            )
            if (
                workflow_subject_ref.subject_id != subject.id
                or workflow_subject_ref.kind != "workflow"
                or workflow_authorization_ref.subject_id != subject.id
                or workflow_authorization_ref.origin_kind != "workflow"
            ):
                raise ProvisionDenied(
                    "workflow authority does not identify the authenticated subject"
                )
            receiver_subject_ref = OriginSubjectRef.model_validate(receiver_subject_ref)
            receiver_authorization_ref = OriginAuthorizationRef.model_validate(
                receiver_authorization_ref
            )
            if (
                receiver_subject_ref.kind != "receiver"
                or receiver_authorization_ref.subject_id != receiver_subject_ref.subject_id
                or receiver_authorization_ref.origin_kind != "receiver"
            ):
                raise ProvisionDenied("receiver refs do not identify a registered receiver")
            receiver_subject_ref, receiver_authorization_ref, receiver_authorization = (
                self._origin_revision(
                    connection,
                    subject_ref=receiver_subject_ref,
                    authorization_ref=receiver_authorization_ref,
                    required_actions={"task_received", "task_result"},
                    job_id=job["id"],
                )
            )
            try:
                receiver_chain, receiver_job = WorkAuthority(self.repository)._chain(
                    connection,
                    receiver_authorization["grant_id"],
                    receiver_authorization["grant_revision"],
                )
            except (AuthorityDenied, GrantConflict, ValueError) as error:
                raise ProvisionDenied("receiver grant ancestry is unavailable") from error
            if (
                receiver_job["id"] != job["id"]
                or receiver_chain[0].principal_id != receiver_subject_ref.subject_id
            ):
                raise ProvisionDenied("receiver grant does not authorize this Work job")

            revision = 1 if prior is None else prior["revision"] + 1
            provision_id = uuid4().hex if prior is None else prior["id"]
            provisional = ProvisionedWorkflowStep(
                ref=WorkflowStepProvisionRef(
                    id=provision_id,
                    principal_id=subject.id,
                    workflow_id=workflow_id,
                    step_id=step_id,
                    revision=revision,
                ),
                issuer_id=admin_context.id,
                spec_hash=spec_hash,
                workflow_subject_ref=workflow_subject_ref,
                workflow_authorization_ref=workflow_authorization_ref,
                job_id=job["id"],
                grant_id=grant_id,
                grant_revision=grant_revision,
                contract=canonical_contract,
                contract_hash=canonical_contract.canonical_hash(),
                snapshot_id=snapshot.id,
                snapshot_hash=snapshot.delivered_hash,
                delivery_template=delivery,
                delivery_template_hash=delivery_hash,
                adapter_version=adapter_version,
                lease_seconds=lease_seconds,
                output_schema_json=output_schema_json,
                output_schema_hash=output_schema_hash,
                receiver_subject_ref=receiver_subject_ref,
                receiver_authorization_ref=receiver_authorization_ref,
                receiver_grant_id=receiver_authorization["grant_id"],
                receiver_grant_revision=receiver_authorization["grant_revision"],
            )
            provisional = provisional.model_copy(
                update={"provision_fingerprint": provisional.fingerprint()}
            )
            connection.execute(
                "INSERT INTO work_workflow_step_provisions "
                "(principal_id,workflow_id,step_id,revision,id,provision_fingerprint,schema_version,state,issuer_id,spec_hash,"
                "workflow_subject_id,workflow_subject_revision,workflow_authorization_kind,workflow_authorization_revision,job_id,grant_id,grant_revision,"
                "contract_id,contract_json,contract_hash,snapshot_id,snapshot_hash,delivery_json,delivery_template_hash,adapter_version,lease_seconds,"
                "output_schema_json,output_schema_hash,receiver_subject_id,receiver_subject_revision,receiver_authorization_kind,receiver_authorization_revision,"
                "receiver_grant_id,receiver_grant_revision,receiver_received_action,receiver_result_action,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    subject.id,
                    workflow_id,
                    step_id,
                    revision,
                    provision_id,
                    provisional.provision_fingerprint,
                    1,
                    "active",
                    admin_context.id,
                    spec_hash,
                    workflow_subject_ref.subject_id,
                    workflow_subject_ref.revision,
                    "workflow",
                    workflow_authorization_ref.revision,
                    job["id"],
                    grant_id,
                    grant_revision,
                    canonical_contract.id,
                    canonical_contract.canonical_json(),
                    canonical_contract.canonical_hash(),
                    snapshot.id,
                    snapshot.delivered_hash,
                    delivery_json,
                    delivery_hash,
                    adapter_version,
                    lease_seconds,
                    output_schema_json,
                    output_schema_hash,
                    receiver_subject_ref.subject_id,
                    receiver_subject_ref.revision,
                    "receiver",
                    receiver_authorization_ref.revision,
                    receiver_authorization["grant_id"],
                    receiver_authorization["grant_revision"],
                    "task_received",
                    "task_result",
                    time.time(),
                ),
            )
            return provisional.ref

    def resolve_workflow_step(
        self,
        principal,
        *,
        workflow_id,
        step_id,
        spec_hash,
        connection=None,
        expected_ref=None,
        expected_fingerprint=None,
        _inspection=False,
    ):
        """Return one currently authorized exact-source provision or None if absent."""
        workflow_id = _identity(workflow_id, "workflow identity")
        step_id = _identity(step_id, "workflow step identity")
        _digest(spec_hash, "workflow spec hash")
        if type(_inspection) is not bool:
            raise ProvisionDenied("invalid inspection mode")
        if _inspection:
            self._inspection_principal(principal)
        else:
            self._principal(principal)
        if connection is not None and not connection.in_transaction:
            raise ProvisionDenied("workflow provision resolution requires a stable transaction")
        snapshot = (
            self.repository.read_snapshot() if connection is None else nullcontext(connection)
        )
        with snapshot as connection:
            self.repository._verify(connection)
            row = self._workflow_step_for_spec(
                connection, principal.id, workflow_id, step_id, spec_hash
            )
            if row is None:
                any_provision = self._workflow_step_latest(
                    connection, principal.id, workflow_id, step_id
                )
                if any_provision is not None:
                    raise ProvisionUnavailable(
                        "workflow step provision is bound to another source snapshot"
                    )
                if expected_ref is not None or expected_fingerprint is not None:
                    raise ProvisionUnavailable("pinned workflow step provision is unavailable")
                return None
            self._registered(connection, principal)
            if row["state"] != "active":
                raise ProvisionUnavailable(
                    "workflow step provision is retired or bound to another source"
                )
            current_ref = self._workflow_provision_ref(row)
            if (
                expected_ref is not None
                and current_ref != expected_ref
                or expected_fingerprint is not None
                and row["provision_fingerprint"] != expected_fingerprint
            ):
                raise ProvisionUnavailable("workflow step provision revision changed")
            try:
                contract = self._contract(
                    WorkContracts._from_json(row["contract_json"]), operation_kind="agent_step"
                )
                delivery_value = json.loads(row["delivery_json"])
                delivery, delivery_json, delivery_hash = _workflow_delivery(
                    delivery_value, adapter_version=row["adapter_version"]
                )
                schema_json, schema_hash = row["output_schema_json"], row["output_schema_hash"]
                if schema_json is not None:
                    canonical_schema, actual_hash = self._workflow_output_schema(
                        json.loads(schema_json)
                    )
                    if (canonical_schema, actual_hash) != (schema_json, schema_hash):
                        raise ValueError("workflow output schema checksum differs")
                elif schema_hash is not None:
                    raise ValueError("workflow output schema hash has no schema")
                if (
                    delivery_json != row["delivery_json"]
                    or delivery_hash != row["delivery_template_hash"]
                    or contract.canonical_json() != row["contract_json"]
                    or contract.canonical_hash() != row["contract_hash"]
                    or contract.id != row["contract_id"]
                    or contract.snapshot.id != row["snapshot_id"]
                    or contract.snapshot.delivered_hash != row["snapshot_hash"]
                ):
                    raise ValueError("workflow provision fields differ from their frozen hashes")
                job, canonical_contract, snapshot = self._validate(
                    connection,
                    subject=principal,
                    job_id=row["job_id"],
                    grant_id=row["grant_id"],
                    grant_revision=row["grant_revision"],
                    contract=contract,
                    issuer_id=row["issuer_id"],
                    operation_kind="agent_step",
                    _inspection=_inspection,
                )
                workflow_subject_ref, workflow_authorization_ref, _ = self._origin_revision(
                    connection,
                    subject_ref=OriginSubjectRef(
                        subject_id=row["workflow_subject_id"],
                        kind="workflow",
                        revision=row["workflow_subject_revision"],
                    ),
                    authorization_ref=OriginAuthorizationRef(
                        subject_id=row["workflow_subject_id"],
                        origin_kind="workflow",
                        revision=row["workflow_authorization_revision"],
                    ),
                    required_actions={"admit_step", "execute"},
                    job_id=row["job_id"],
                    grant_id=row["grant_id"],
                    grant_revision=row["grant_revision"],
                )
                receiver_subject_ref, receiver_authorization_ref, receiver_authorization = (
                    self._origin_revision(
                        connection,
                        subject_ref=OriginSubjectRef(
                            subject_id=row["receiver_subject_id"],
                            kind="receiver",
                            revision=row["receiver_subject_revision"],
                        ),
                        authorization_ref=OriginAuthorizationRef(
                            subject_id=row["receiver_subject_id"],
                            origin_kind="receiver",
                            revision=row["receiver_authorization_revision"],
                        ),
                        required_actions={"task_received", "task_result"},
                        job_id=row["job_id"],
                        grant_id=row["receiver_grant_id"],
                        grant_revision=row["receiver_grant_revision"],
                    )
                )
                receiver_chain, receiver_job = WorkAuthority(self.repository)._chain(
                    connection, row["receiver_grant_id"], row["receiver_grant_revision"]
                )
                if (
                    receiver_job["id"] != row["job_id"]
                    or receiver_chain[0].principal_id != receiver_subject_ref.subject_id
                ):
                    raise ValueError("workflow receiver grant is no longer valid")
                provision = ProvisionedWorkflowStep(
                    ref=self._workflow_provision_ref(row),
                    provision_fingerprint=row["provision_fingerprint"],
                    issuer_id=row["issuer_id"],
                    spec_hash=row["spec_hash"],
                    workflow_subject_ref=workflow_subject_ref,
                    workflow_authorization_ref=workflow_authorization_ref,
                    job_id=job["id"],
                    grant_id=row["grant_id"],
                    grant_revision=row["grant_revision"],
                    contract=canonical_contract,
                    contract_hash=row["contract_hash"],
                    snapshot_id=snapshot.id,
                    snapshot_hash=snapshot.delivered_hash,
                    delivery_template=delivery,
                    delivery_template_hash=delivery_hash,
                    adapter_version=row["adapter_version"],
                    lease_seconds=row["lease_seconds"],
                    output_schema_json=schema_json,
                    output_schema_hash=schema_hash,
                    receiver_subject_ref=receiver_subject_ref,
                    receiver_authorization_ref=receiver_authorization_ref,
                    receiver_grant_id=receiver_authorization["grant_id"],
                    receiver_grant_revision=receiver_authorization["grant_revision"],
                )
                if not provision.has_valid_fingerprint():
                    raise ValueError("workflow provision fingerprint differs")
                return provision
            except (
                AuthorityDenied,
                GrantConflict,
                SnapshotUnavailable,
                OriginDenied,
                ProvisionDenied,
                ValidationError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as error:
                raise ProvisionUnavailable(
                    "workflow step provision is invalid or no longer authorized"
                ) from error

    @staticmethod
    def _workflow_output_schema(output_schema):
        if output_schema is None:
            return None, None
        if not isinstance(output_schema, dict):
            raise ProvisionDenied("workflow output schema must be a JSON object")
        try:
            encoded = _canonical(output_schema)
            if len(encoded.encode("utf-8")) > 32768:
                raise ValueError("workflow output schema exceeds byte bound")
            import jsonschema

            jsonschema.Draft202012Validator.check_schema(output_schema)
        except Exception as error:
            raise ProvisionDenied("workflow output schema is invalid") from error
        return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _workflow_step_latest(connection, principal_id, workflow_id, step_id):
        return connection.execute(
            "SELECT * FROM work_workflow_step_provisions WHERE principal_id=? AND workflow_id=? "
            "AND step_id=? ORDER BY revision DESC LIMIT 1",
            (principal_id, workflow_id, step_id),
        ).fetchone()

    @staticmethod
    def _workflow_step_for_spec(connection, principal_id, workflow_id, step_id, spec_hash):
        return connection.execute(
            "SELECT * FROM work_workflow_step_provisions WHERE principal_id=? AND workflow_id=? "
            "AND step_id=? AND spec_hash=? ORDER BY revision DESC LIMIT 1",
            (principal_id, workflow_id, step_id, spec_hash),
        ).fetchone()

    def _resolve_launch(self, connection, authenticated_principal, selector, *, expected_ref=None):
        """Resolve with a caller-owned transaction, optionally fencing one exact revision.

        The caller supplies the authenticated ``Principal``.  Stored principal
        fields are inspected only as durable evidence; they never mint a new
        Principal for a request.
        """
        self.repository._verify(connection)
        selector = _identity(selector, "selector")
        self._principal(authenticated_principal)
        self._registered(connection, authenticated_principal)
        if expected_ref is not None:
            if not isinstance(expected_ref, ProvisionRef):
                raise ProvisionUnavailable("durable launch provision is unavailable")
            if (
                expected_ref.principal_id != authenticated_principal.id
                or expected_ref.selector != selector
            ):
                raise ProvisionUnavailable("durable launch provision is unavailable")
        row = self._latest(connection, authenticated_principal.id, selector)
        if row is None or row["state"] != "active":
            raise ProvisionUnavailable("durable launch provision is unavailable")
        provision = self._view(connection, row)
        if expected_ref is not None and provision.ref != expected_ref:
            raise ProvisionUnavailable("durable launch provision is unavailable")
        return provision

    def _resolve_unique_active_launch(self, connection, authenticated_principal):
        """Resolve only when the principal has one latest active selector."""
        self.repository._verify(connection)
        self._principal(authenticated_principal)
        self._registered(connection, authenticated_principal)
        rows = connection.execute(
            "SELECT provision_row.* FROM work_launch_provisions AS provision_row "
            "WHERE provision_row.principal_id=? "
            "AND provision_row.revision=(SELECT MAX(history.revision) "
            "FROM work_launch_provisions AS history "
            "WHERE history.principal_id=provision_row.principal_id "
            "AND history.selector=provision_row.selector) "
            "AND provision_row.state='active' ORDER BY provision_row.selector LIMIT 2",
            (authenticated_principal.id,),
        ).fetchall()
        if len(rows) != 1:
            raise ProvisionSelectionUnavailable("durable launch provision is unavailable")
        return self._view(connection, rows[0])

    def resolve_unique_active_launch(self, authenticated_principal):
        """Resolve one latest active provision for a verified principal."""
        with self.repository.read_snapshot() as connection:
            return self._resolve_unique_active_launch(connection, authenticated_principal)

    def _revalidate_bound_launch(self, connection, *, ref, fingerprint):
        """Validate a persisted launch binding without reconstructing a Principal."""
        self.repository._verify(connection)
        if not isinstance(ref, ProvisionRef) or not isinstance(fingerprint, str):
            raise ProvisionUnavailable("durable launch provision is unavailable")
        row = self._latest(connection, ref.principal_id, ref.selector)
        if row is None or row["state"] != "active":
            raise ProvisionUnavailable("durable launch provision is unavailable")
        provision = self._view(connection, row)
        if provision.ref != ref or provision.fingerprint() != fingerprint:
            raise ProvisionUnavailable("durable launch provision is unavailable")
        return provision

    def resolve_launch(self, authenticated_principal, selector):
        """Reconstruct one current provision; foreign and absent selectors disclose nothing."""
        with self.repository.read_snapshot() as connection:
            return self._resolve_launch(connection, authenticated_principal, selector)

    def derive_plan_step(
        self,
        connection,
        principal,
        seed,
        *,
        workflow_alias,
        step_id,
        source_hash,
        run_id,
        expected_plan_id,
        prompt,
    ):
        """Clone an exact authorized seed into an isolated plan-owned selector.

        Caller must already hold its plan admission transaction. No new grant,
        snapshot, receiver, contract or delivery is accepted from the caller.
        """
        if not connection.in_transaction:
            raise ProvisionDenied("plan provisioning requires caller transaction")
        import hashlib

        proof = connection.execute(
            "SELECT r.principal_id,r.plan_id,p.source_hash FROM workflow_scoped_run r "
            "JOIN workflow_prepared_plan p ON p.prepared_id=r.prepared_id WHERE r.run_id=?",
            (run_id,),
        ).fetchone()
        if (
            proof is None
            or (proof["principal_id"], proof["plan_id"], proof["source_hash"])
            != (principal.id, expected_plan_id, source_hash)
            or workflow_alias
            != ("plan_" + hashlib.sha256((run_id + expected_plan_id).encode()).hexdigest()[:40])
        ):
            raise ProvisionDenied("scoped provisioning requires durable exact run ownership")
        current = self.resolve_workflow_step(
            principal,
            workflow_id=seed.ref.workflow_id,
            step_id=seed.ref.step_id,
            spec_hash=source_hash,
            connection=connection,
            expected_ref=seed.ref,
            expected_fingerprint=seed.provision_fingerprint,
        )
        if current != seed:
            raise ProvisionDenied("plan seed changed")
        _identity(workflow_alias, "scoped workflow identity")
        _identity(step_id, "scoped step identity")
        if not isinstance(prompt, str) or not prompt or len(prompt.encode("utf-8")) > 32768:
            raise ProvisionDenied("scoped delivery exceeds approved payload bounds")
        payload = json.loads(seed.delivery_template.payload_json)
        payload["message"] = prompt
        requested_delivery = seed.delivery_template.model_copy(
            update={"payload_json": _canonical(payload)}
        )
        delivery, delivery_json, delivery_hash = _workflow_delivery(
            requested_delivery, adapter_version=seed.adapter_version
        )
        prior = self.resolve_workflow_step(
            principal,
            workflow_id=workflow_alias,
            step_id=step_id,
            spec_hash=source_hash,
            connection=connection,
        )
        if prior is not None:
            if (
                prior.contract_hash,
                prior.delivery_template_hash,
                prior.grant_id,
                prior.grant_revision,
                prior.snapshot_hash,
            ) != (
                seed.contract_hash,
                delivery_hash,
                seed.grant_id,
                seed.grant_revision,
                seed.snapshot_hash,
            ):
                raise ProvisionConflict("scoped step is bound to another seed")
            return prior
        # Derived rows retain all issuer and receiver authority evidence. Existing
        # schema FK, immutable triggers and source uniqueness continue to apply.
        from uuid import uuid4

        ref = seed.ref.model_copy(
            update={
                "id": uuid4().hex,
                "workflow_id": workflow_alias,
                "step_id": step_id,
                "revision": 1,
            }
        )
        derived = seed.model_copy(
            update={
                "ref": ref,
                "delivery_template": delivery,
                "delivery_template_hash": delivery_hash,
            }
        )
        derived = derived.model_copy(update={"provision_fingerprint": derived.fingerprint()})
        row = dict(
            connection.execute(
                "SELECT * FROM work_workflow_step_provisions WHERE id=?", (seed.ref.id,)
            ).fetchone()
        )
        row.update(
            workflow_id=workflow_alias,
            step_id=step_id,
            revision=1,
            id=ref.id,
            provision_fingerprint=derived.provision_fingerprint,
            delivery_json=delivery_json,
            delivery_template_hash=delivery_hash,
            created_at=time.time(),
        )
        columns = tuple(row)
        connection.execute(
            "INSERT INTO work_workflow_step_provisions ("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("?" for _ in columns)
            + ")",
            tuple(row[key] for key in columns),
        )
        return self.resolve_workflow_step(
            principal,
            workflow_id=workflow_alias,
            step_id=step_id,
            spec_hash=source_hash,
            connection=connection,
            expected_ref=ref,
            expected_fingerprint=derived.provision_fingerprint,
        )
