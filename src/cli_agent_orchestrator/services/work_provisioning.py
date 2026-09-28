"""Internal durable launch provisioning; no transport, admission, or provider wiring."""

import time
from uuid import uuid4

from pydantic import ValidationError

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_origin import ProvisionRef, ProvisionedLaunch
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
    def _registered(connection, principal):
        if not isinstance(principal, Principal):
            raise ProvisionDenied("verified principal required")
        row = connection.execute(
            "SELECT issuer,subject,kind FROM work_principals WHERE id=?", (principal.id,)
        ).fetchone()
        if row is None or tuple(row) != (principal.issuer, principal.subject, principal.kind):
            raise ProvisionDenied("principal is not durably registered")

    @staticmethod
    def _contract(value):
        try:
            contract = WorkContracts._contract(value)
        except (ContractConflict, ValidationError, TypeError, ValueError) as error:
            raise ProvisionDenied("canonical launch contract required") from error
        if contract.operation_kind != "launch":
            raise ProvisionDenied("launch provisioning requires a launch contract")
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
    ):
        """Validate one exact durable configuration without inferring absent settings."""
        self.repository._verify(connection)
        if admin is not None:
            self._principal(admin, admin=True)
            self._registered(connection, admin)
        self._principal(subject)
        self._registered(connection, subject)
        _identity(job_id, "job id")
        _identity(grant_id, "grant id")
        _exact_positive(grant_revision, "grant revision")
        contract = self._contract(contract)
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
