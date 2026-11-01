"""Shared ledger for cooperating trusted servers; no client-selected owner identity.

Authority writers use explicit owner credentials. Runtime operations use only the
configured shared main store and a private server binding supplier. This module
is a persistence protocol, not permission to deliver work or proof of completion.
"""

import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import asdict
from typing import Callable, Iterator, Sequence, cast
from uuid import NAMESPACE_OID, uuid5

import psutil
from psycopg import InterfaceError, sql

from cli_agent_orchestrator.clients.admission_schema import (
    _BASE_AUTHORITY_TABLES,
    ADMIN_ACTION_TABLES,
    AUTHORITY_TABLES,
    LEDGER_TABLES,
    METADATA_CREDIT_TABLES,
    RESOURCE_ADMISSION_MIGRATIONS,
)
from cli_agent_orchestrator.clients.postgres import (
    Connection,
    DatabaseError,
    DatabaseTarget,
    Record,
    connect,
)
from cli_agent_orchestrator.clients.postgres.connection import ConnectionCursor
from cli_agent_orchestrator.clients.postgres.migrations import (
    SchemaMismatch,
    verify_component,
    verify_runtime_role,
)
from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.models.admission_domains import (
    DomainRecord,
    DomainRequest,
    DomainSpec,
    MetadataCoverage,
    MetadataCredit,
    ReservationState,
    SharedAbortReceipt,
    SharedDebtCursor,
    SharedDebtEntry,
    SharedDebtPage,
    SharedReservation,
    SharedResourceCost,
    UsageMismatch,
    canonical_id,
)
from cli_agent_orchestrator.models.resource_envelope import ResourceBinding, envelope_integer


class SharedAdmissionDenied(RuntimeError):
    """New admission is unavailable; this does not prove transaction rollback."""


class SharedCommitUncertain(SharedAdmissionDenied):
    """The commit completed but its grant cannot be acknowledged safely.

    Keep the same operation ID and its durable charge until it is reconciled.
    """


class SharedRevisionConflict(RuntimeError):
    """The exact request, owner, state or authority revision changed."""


class SharedActionCommitUncertain(SharedCommitUncertain):
    """The caller retains these exact identities through uncertain completion."""

    def __init__(self, action_id: str, operation_id: str) -> None:
        super().__init__("Administrative resource action requires reconciliation")
        self.action_id = canonical_id(action_id)
        self.operation_id = canonical_id(operation_id)


class SharedCapacityUnavailable(RuntimeError):
    """At least one known domain cannot cover the whole operation."""


_DOMAIN_FAMILY = 0x43414F44
_OPERATION_FAMILY = 0x43414F4F
_MEMBERSHIP_FAMILY = 0x43414F4D
_CREDIT_FAMILY = 0x43414F43
_ACTION_FAMILY = 0x43414F41
_TABLES = dict(
    zip(
        (
            "domains",
            "domain_history",
            "members",
            "member_history",
            "usage",
            "operations",
            "allocations",
        ),
        _BASE_AUTHORITY_TABLES + LEDGER_TABLES,
        strict=True,
    )
)
_TABLES.update(
    zip(("metadata_credits", "metadata_credit_history"), METADATA_CREDIT_TABLES, strict=True)
)
_TABLES["admin_actions"] = ADMIN_ACTION_TABLES[0]
_TRANSITIONS = {
    "preparing": {"active", "released"},
    "active": {"releasing", "uncertain"},
    "releasing": {"released", "uncertain"},
    "uncertain": {"releasing"},
    "released": set(),
}


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def binding_digest(binding: ResourceBinding) -> str:
    return hashlib.sha256(_json(asdict(binding)).encode()).hexdigest()


def _receipt(row: Record) -> SharedReservation:
    if row["state"] not in _TRANSITIONS:
        raise SchemaMismatch("Invalid shared resource state")
    return SharedReservation(
        str(row["operation_id"]), row["revision"], cast(ReservationState, row["state"])
    )


def _credit(row: Record) -> MetadataCredit:
    try:
        return MetadataCredit(
            str(row["credit_id"]),
            str(row["domain_id"]),
            str(row["instance_id"]),
            str(row["process_generation"]),
            row["process_pid"],
            row["process_started_at"],
            row["binding_sha256"],
            row["amount"],
            row["revision"],
        )
    except ValueError:
        raise SchemaMismatch("Invalid metadata credit record") from None


class ResourceAdmissionRepository:
    def __init__(self, target: DatabaseTarget, *, binding_supplier: Callable[[], ResourceBinding]):
        if not isinstance(target, DatabaseTarget) or target.scope != "main":
            raise ValueError("Shared admission requires one configured main coordinator")
        self.target = target
        self._binding_supplier = binding_supplier
        self._pid = os.getpid()
        self._coordinator_store_id = canonical_id(target.store_id)
        self._audit_lock = threading.Lock()
        self._usage_guard = threading.Lock()
        self._usage_known = False
        self._usage_uncertain = True

    def _check_pid(self) -> None:
        if os.getpid() != self._pid:
            raise SharedAdmissionDenied("Shared admission belongs to another process")

    def current_binding(self) -> ResourceBinding:
        self._check_pid()
        value = self._binding_supplier()
        target_hash = hashlib.sha256(
            json.dumps(self.target.identity, separators=(",", ":")).encode()
        ).hexdigest()
        if (
            type(value) is not ResourceBinding
            or value.process_pid != self._pid
            or value.process_started_at != psutil.Process().create_time()
            or value.store_id != self._coordinator_store_id
            or value.target_identity_sha256 != target_hash
        ):
            raise SharedAdmissionDenied("Shared admission process binding is unavailable")
        return value

    def _query(
        self, connection: Connection, statement: str, parameters: Sequence[object] = ()
    ) -> ConnectionCursor[Record]:
        return connection.execute(
            sql.SQL(statement).format(
                **{key: sql.Identifier(self.target.schema, name) for key, name in _TABLES.items()}
            ),
            parameters,
        )

    def _verify(self, connection: Connection) -> None:
        verify_runtime_role(connection)
        verify_component(connection, RESOURCE_ADMISSION_MIGRATIONS)
        unsafe = connection.execute(
            """SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relname=ANY(%s) AND (
                c.relkind NOT IN ('r','p') OR has_table_privilege(current_user,c.oid,'TRUNCATE')
                OR has_any_column_privilege(current_user,c.oid,'REFERENCES')
                OR has_table_privilege(current_user,c.oid,'TRIGGER')
                OR has_table_privilege(current_user,c.oid,'MAINTAIN')))""",
            (self.target.schema, list(LEDGER_TABLES)),
        ).fetchone()
        if unsafe is None or unsafe[0] is not False:
            raise SchemaMismatch("Shared ledger runtime privileges are unsafe")

    @contextmanager
    def _runtime(self, *, require_usage: bool = False) -> Iterator[Connection]:
        self._check_pid()
        with connect(self.target) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            self._verify(connection)
            yield connection
            if require_usage:
                self._check_usage_ready()  # Before commit, invalidation rolls back.
        if require_usage:
            try:
                self._check_usage_ready()
            except SharedAdmissionDenied:
                # A committed operation is not a rollback, and never starts an
                # effect through an unacknowledged grant. Preserve its exact ID.
                raise SharedCommitUncertain(
                    "Shared admission commit requires reconciliation"
                ) from None

    @staticmethod
    def _check_provisioner(principal_id: str) -> None:
        if (
            not isinstance(principal_id, str)
            or re.fullmatch(r"principal-[0-9a-f]{64}", principal_id) is None
        ):
            raise ValueError("Invalid provisioner identity")

    @contextmanager
    def _owner(self, principal_id: str, *, require_usage: bool = False):
        self._check_pid()
        self._check_provisioner(principal_id)
        with connect(self.target, readonly=True) as reader:
            self._verify(reader)
        with connect(self.target.migration_target()) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            verify_component(connection, RESOURCE_ADMISSION_MIGRATIONS)
            owned = connection.execute(
                """SELECT count(*)=%s AND bool_and(c.relowner=r.oid AND n.nspowner=r.oid)
                FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_catalog.pg_roles r ON r.rolname=current_user
                WHERE n.nspname=%s AND c.relname=ANY(%s)""",
                (
                    len(AUTHORITY_TABLES + LEDGER_TABLES),
                    self.target.schema,
                    list(AUTHORITY_TABLES + LEDGER_TABLES),
                ),
            ).fetchone()
            if owned is None or owned[0] is not True:
                raise SchemaMismatch("Shared resource provisioner must own the coordinator schema")
            yield connection
            if require_usage:
                self._check_usage_ready()
        if require_usage:
            try:
                self._check_usage_ready()
            except SharedAdmissionDenied:
                raise SharedCommitUncertain(
                    "Shared resource credit commit requires reconciliation"
                ) from None

    def _lock_key(self, identity: str) -> int:
        # Hashes identify locks only, never domains. Collisions serialize safely.
        payload = _json(
            (
                "resource-admission-v1",
                self._coordinator_store_id,
                self.target.schema,
                canonical_id(identity),
            )
        )
        return int.from_bytes(hashlib.sha256(payload.encode()).digest()[:4], "big", signed=True)

    def _lock(self, connection: Connection, family: int, identities: Sequence[str]) -> None:
        for key in sorted({self._lock_key(identity) for identity in identities}):
            connection.execute("SELECT pg_catalog.pg_advisory_xact_lock(%s,%s)", (family, key))

    def _lock_membership(self, connection: Connection, binding: ResourceBinding) -> None:
        # Membership writers and complete-map readers share this gate. Always
        # acquire before operation and domain locks; the UUID is realm-scoped
        # by _lock_key, and a collision only adds serialization.
        identity = str(
            uuid5(NAMESPACE_OID, _json((binding.instance_id, binding.process_generation)))
        )
        self._lock(connection, _MEMBERSHIP_FAMILY, (identity,))

    def _membership_basis(
        self, connection: Connection, binding: ResourceBinding
    ) -> tuple[list[Record], str]:
        members = self._query(
            connection,
            """SELECT * FROM {members} WHERE instance_id=%s AND process_generation=%s
            ORDER BY domain_id""",
            (binding.instance_id, binding.process_generation),
        ).fetchall()
        if not members:
            raise SharedAdmissionDenied("Complete resource membership is unavailable")
        self._lock(connection, _DOMAIN_FAMILY, tuple(str(m["domain_id"]) for m in members))
        domains = []
        basis = []
        for member in members:
            domain = self._domain(connection, str(member["domain_id"]))
            if (
                not member["active"]
                or member["binding_sha256"] != binding_digest(binding)
                or not domain["active"]
            ):
                raise SharedAdmissionDenied("Complete resource membership is unavailable")
            domains.append(domain)
            basis.append(
                (
                    str(domain["domain_id"]),
                    str(domain["incarnation"]),
                    domain["revision"],
                    domain["kind"],
                    domain["unit"],
                    member["revision"],
                    member["binding_sha256"],
                )
            )
        return domains, hashlib.sha256(_json(basis).encode()).hexdigest()

    @staticmethod
    def _cost_requests(
        domains: Sequence[Record], cost: SharedResourceCost
    ) -> tuple[DomainRequest, ...]:
        amounts = {
            "bytes": cost.memory_bytes,
            "millicores": cost.cpu_millicores,
            "postgres_session": cost.storage_slots,
        }
        for unit, amount in amounts.items():
            required = {"storage"} if unit == "postgres_session" else {"host", "node"}
            present = {d["kind"] for d in domains if d["unit"] == unit}
            if amount and not required.issubset(present):
                raise SharedAdmissionDenied("Resource footprint is not completely covered")
        return tuple(
            DomainRequest(
                str(d["domain_id"]), str(d["incarnation"]), d["revision"], amounts[d["unit"]]
            )
            for d in domains
            if amounts[d["unit"]]
        )

    def _domain(self, connection: Connection, domain_id: str) -> Record:
        row = self._query(
            connection, "SELECT * FROM {domains} WHERE domain_id=%s", (domain_id,)
        ).fetchone()
        if row is None:
            raise SharedAdmissionDenied("Resource domain is not provisioned")
        return row

    def _used(self, connection: Connection, domain_id: str) -> int:
        row = self._query(
            connection, "SELECT committed FROM {usage} WHERE domain_id=%s", (domain_id,)
        ).fetchone()
        if row is None:
            raise SchemaMismatch("Shared resource counter is missing")
        value = row[0]
        envelope_integer(value, minimum=0)
        return cast(int, value)

    def _change_used(self, connection: Connection, domain_id: str, before: int, after: int) -> None:
        envelope_integer(after, minimum=0)
        updated = self._query(
            connection,
            "UPDATE {usage} SET committed=%s WHERE domain_id=%s AND committed=%s RETURNING domain_id",
            (after, domain_id, before),
        ).fetchone()
        if updated is None:
            raise SharedRevisionConflict("Shared resource counter changed")

    def provision_domain(
        self, spec: DomainSpec, *, expected_revision: int, principal_id: str, active: bool = True
    ) -> DomainRecord:
        envelope_integer(expected_revision, minimum=0)
        if type(spec) is not DomainSpec or type(active) is not bool:
            raise ValueError("Invalid shared domain provision")
        with self._owner(principal_id) as connection:
            self._lock(connection, _DOMAIN_FAMILY, (spec.domain_id,))
            current = self._query(
                connection, "SELECT * FROM {domains} WHERE domain_id=%s", (spec.domain_id,)
            ).fetchone()
            if (current["revision"] if current else 0) != expected_revision:
                raise SharedRevisionConflict("Resource domain revision changed")
            if current is not None:
                if (
                    str(current["incarnation"]),
                    current["kind"],
                    current["unit"],
                    current["physical_binding_sha256"],
                ) != (spec.incarnation, spec.kind, spec.unit, spec.physical_binding_sha256):
                    raise SharedRevisionConflict("Resource identity cannot be replaced")
                self._used(connection, spec.domain_id)  # Never repair missing counters to zero.
            values = (*asdict(spec).values(), expected_revision + 1, active, principal_id)
            self._query(
                connection,
                """INSERT INTO {domains}
                (domain_id,incarnation,kind,unit,capacity,physical_binding_sha256,revision,active,actor_principal_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (domain_id) DO UPDATE SET capacity=EXCLUDED.capacity,
                    revision=EXCLUDED.revision,active=EXCLUDED.active,
                    actor_principal_id=EXCLUDED.actor_principal_id,updated_at=CURRENT_TIMESTAMP""",
                values,
            )
            if current is None:
                self._query(connection, "INSERT INTO {usage} VALUES (%s,0)", (spec.domain_id,))
            self._query(
                connection,
                "INSERT INTO {domain_history} (domain_id,revision,payload_json) VALUES (%s,%s,%s::jsonb)",
                (
                    spec.domain_id,
                    expected_revision + 1,
                    _json(dict(spec=asdict(spec), active=active, actor=principal_id)),
                ),
            )
        return DomainRecord(spec, expected_revision + 1, active)

    def provision_member(
        self,
        domain_id: str,
        binding: ResourceBinding,
        *,
        expected_revision: int,
        principal_id: str,
        active: bool = True,
        metadata_credit_id: str | None = None,
    ) -> int:
        domain_id = canonical_id(domain_id)
        envelope_integer(expected_revision, minimum=0)
        if (
            type(binding) is not ResourceBinding
            or binding.store_id != self._coordinator_store_id
            or type(active) is not bool
        ):
            raise ValueError("Invalid resource membership provision")
        if metadata_credit_id is not None:
            metadata_credit_id = canonical_id(metadata_credit_id)
            if not active:
                raise ValueError("Metadata enrollment requires active membership")
            self._check_provisioner(principal_id)
            self._require_usage_basis()
        with self._owner(principal_id, require_usage=metadata_credit_id is not None) as connection:
            self._lock_membership(connection, binding)
            if metadata_credit_id is not None:
                self._lock(connection, _CREDIT_FAMILY, (metadata_credit_id,))
            self._lock(connection, _DOMAIN_FAMILY, (domain_id,))
            domain = self._domain(connection, domain_id)
            if domain["kind"] == "storage" and active and metadata_credit_id is None:
                credited_node = self._query(
                    connection,
                    "SELECT EXISTS(SELECT 1 FROM {metadata_credits} WHERE instance_id=%s AND process_generation=%s)",
                    (binding.instance_id, binding.process_generation),
                ).fetchone()
                present = self._query(
                    connection,
                    "SELECT credit_id FROM {metadata_credits} WHERE domain_id=%s AND instance_id=%s AND process_generation=%s",
                    (domain_id, binding.instance_id, binding.process_generation),
                ).fetchone()
                if credited_node is not None and credited_node[0] and present is None:
                    raise SharedAdmissionDenied(
                        "Storage membership requires joint metadata enrollment"
                    )
            key = (domain_id, binding.instance_id, binding.process_generation)
            current = self._query(
                connection,
                "SELECT revision FROM {members} WHERE domain_id=%s AND instance_id=%s AND process_generation=%s",
                key,
            ).fetchone()
            if (current[0] if current else 0) != expected_revision:
                raise SharedRevisionConflict("Resource membership revision changed")
            self._query(
                connection,
                """INSERT INTO {members}
                (domain_id,instance_id,process_generation,binding_sha256,revision,active,actor_principal_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (domain_id,instance_id,process_generation)
                DO UPDATE SET binding_sha256=EXCLUDED.binding_sha256,revision=EXCLUDED.revision,
                active=EXCLUDED.active,actor_principal_id=EXCLUDED.actor_principal_id,updated_at=CURRENT_TIMESTAMP""",
                (*key, binding_digest(binding), expected_revision + 1, active, principal_id),
            )
            self._query(
                connection,
                "INSERT INTO {member_history} (domain_id,instance_id,process_generation,revision,payload_json) VALUES (%s,%s,%s,%s,%s::jsonb)",
                (
                    *key,
                    expected_revision + 1,
                    _json(dict(binding=asdict(binding), active=active, actor=principal_id)),
                ),
            )
            if metadata_credit_id is not None:
                self._grant_metadata_credit_locked(
                    connection, metadata_credit_id, domain_id, binding, principal_id
                )
        return expected_revision + 1

    def grant_metadata_credit(
        self, credit_id: str, domain_id: str, binding: ResourceBinding, *, principal_id: str
    ) -> MetadataCredit:
        """Charge one durable storage slot to an exact process view, once.

        An exact retry acknowledges the existing credit without another charge.
        Membership revocation never releases it; there is no retirement here.
        """
        credit_id = canonical_id(credit_id)
        domain_id = canonical_id(domain_id)
        if type(binding) is not ResourceBinding or binding.store_id != self._coordinator_store_id:
            raise ValueError("Invalid metadata credit binding")
        self._check_provisioner(principal_id)
        self._require_usage_basis()
        with self._owner(principal_id, require_usage=True) as connection:
            self._lock_membership(connection, binding)
            self._lock(connection, _CREDIT_FAMILY, (credit_id,))
            self._lock(connection, _DOMAIN_FAMILY, (domain_id,))
            return self._grant_metadata_credit_locked(
                connection, credit_id, domain_id, binding, principal_id
            )

    def _grant_metadata_credit_locked(
        self,
        connection: Connection,
        credit_id: str,
        domain_id: str,
        binding: ResourceBinding,
        principal_id: str,
    ) -> MetadataCredit:
        """Caller owns membership, credit and domain locks in that order."""
        digest = binding_digest(binding)
        expected = (
            credit_id,
            domain_id,
            binding.instance_id,
            binding.process_generation,
            binding.process_pid,
            float(binding.process_started_at),
            digest,
            1,
        )
        existing = self._query(
            connection,
            """SELECT * FROM {metadata_credits} WHERE credit_id=%s
            OR (domain_id=%s AND instance_id=%s AND process_generation=%s)""",
            (credit_id, domain_id, binding.instance_id, binding.process_generation),
        ).fetchall()
        if existing:
            credit = _credit(existing[0])
            if (
                len(existing) != 1
                or (
                    credit.credit_id,
                    credit.domain_id,
                    credit.instance_id,
                    credit.process_generation,
                    credit.process_pid,
                    credit.process_started_at,
                    credit.binding_sha256,
                    credit.amount,
                )
                != expected
            ):
                raise SharedRevisionConflict("Metadata credit identity or payload changed")
            return credit
        domain = self._domain(connection, domain_id)
        member = self._query(
            connection,
            "SELECT * FROM {members} WHERE domain_id=%s AND instance_id=%s AND process_generation=%s",
            (domain_id, binding.instance_id, binding.process_generation),
        ).fetchone()
        if (
            domain["kind"] != "storage"
            or domain["unit"] != "postgres_session"
            or not domain["active"]
            or member is None
            or not member["active"]
            or member["binding_sha256"] != digest
        ):
            raise SharedAdmissionDenied("Metadata credit authority is unavailable")
        used = self._used(connection, domain_id)
        if used + 1 > domain["capacity"]:
            raise SharedCapacityUnavailable("Shared resource capacity is exhausted")
        self._query(
            connection,
            """INSERT INTO {metadata_credits}
            (credit_id,domain_id,instance_id,process_generation,process_pid,process_started_at,binding_sha256,amount,revision,actor_principal_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,%s)""",
            (*expected, principal_id),
        )
        self._query(
            connection,
            "INSERT INTO {metadata_credit_history} (credit_id,revision,payload_json) VALUES (%s,1,%s::jsonb)",
            (
                credit_id,
                _json(
                    dict(
                        domain_id=domain_id,
                        binding=asdict(binding),
                        amount=1,
                        actor=principal_id,
                    )
                ),
            ),
        )
        self._change_used(connection, domain_id, used, used + 1)
        return MetadataCredit(*expected, 1)

    def metadata_credits_current(self) -> tuple[MetadataCredit, ...]:
        """Credits held by this exact private process view.

        Membership revocation does not hide a credit, so completion can proceed;
        new admissions validate membership separately. A credit for this
        incarnation under another PID, birth or view is never adopted.
        """
        binding = self.current_binding()
        digest = binding_digest(binding)
        with connect(self.target, readonly=True) as connection:
            self._verify(connection)
            rows = self._query(
                connection,
                """SELECT c.*,d.kind,d.unit FROM {metadata_credits} c
                JOIN {domains} d USING (domain_id)
                WHERE c.instance_id=%s AND c.process_generation=%s ORDER BY c.domain_id""",
                (binding.instance_id, binding.process_generation),
            ).fetchall()
        credits = []
        for row in rows:
            if row["kind"] != "storage" or row["unit"] != "postgres_session":
                raise SchemaMismatch("Metadata credit domain is not storage")
            credit = _credit(row)
            if (credit.process_pid, credit.process_started_at, credit.binding_sha256) != (
                binding.process_pid,
                binding.process_started_at,
                digest,
            ):
                raise SharedAdmissionDenied("Metadata credit belongs to another process view")
            credits.append(credit)
        if self.current_binding() != binding:
            raise SharedAdmissionDenied("Resource process binding changed during credit read")
        return tuple(credits)

    def _metadata_coverage_locked(
        self,
        connection: Connection,
        binding: ResourceBinding,
        domains: Sequence[Record],
        fingerprint: str,
    ) -> MetadataCoverage:
        storage = tuple(str(d["domain_id"]) for d in domains if d["kind"] == "storage")
        if not storage:
            raise SharedAdmissionDenied("Shared metadata storage coverage is unavailable")
        rows = self._query(
            connection,
            "SELECT * FROM {metadata_credits} WHERE instance_id=%s AND process_generation=%s ORDER BY domain_id",
            (binding.instance_id, binding.process_generation),
        ).fetchall()
        credits = tuple(_credit(row) for row in rows)
        if {c.domain_id for c in credits} != set(storage) or any(
            (c.process_pid, c.process_started_at, c.binding_sha256)
            != (binding.process_pid, binding.process_started_at, binding_digest(binding))
            for c in credits
        ):
            raise SharedAdmissionDenied("Shared metadata coverage is incomplete")
        for domain in domains:
            if (
                domain["kind"] == "storage"
                and self._used(connection, str(domain["domain_id"])) > domain["capacity"]
            ):
                raise SharedCapacityUnavailable("Shared metadata domain has active debt")
        return MetadataCoverage(binding, fingerprint, storage, credits)

    def metadata_coverage_current(self) -> MetadataCoverage:
        """Fresh complete-set confirmation; a value is not activation authority."""
        binding = self.current_binding()
        self._require_usage_basis()
        with self._runtime() as connection:
            self._lock_membership(connection, binding)
            domains, fingerprint = self._membership_basis(connection, binding)
            coverage = self._metadata_coverage_locked(connection, binding, domains, fingerprint)
            if self.current_binding() != binding:
                raise SharedAdmissionDenied("Metadata process view changed during confirmation")
            self._check_usage_ready()
        self._check_usage_ready()
        return coverage

    def read_operation(self, operation_id: str) -> SharedReservation | None:
        """Reconcile after the prior transaction's op lock, never infer rollback."""
        operation_id = canonical_id(operation_id)
        binding = self.current_binding()
        with self._runtime() as connection:
            self._lock(connection, _OPERATION_FAMILY, (operation_id,))
            row = self._query(
                connection, "SELECT * FROM {operations} WHERE operation_id=%s", (operation_id,)
            ).fetchone()
            if row is None:
                return None
            self._check_owner(row, binding)
            return _receipt(row)

    def admin_abort_preparing(
        self,
        operation_id: str,
        *,
        action_id: str,
        expected_revision: int,
        principal_id: str,
        expected_binding: ResourceBinding | None = None,
    ) -> SharedAbortReceipt:
        """CAS-fence an unstarted mapped effect, with immutable owner-written audit.

        This never infers process death or releases metadata. The application
        caller must own its already-enrolled custodian control session; changing
        SQL role does not change physical storage admission or add a session.
        """
        operation_id, action_id = canonical_id(operation_id), canonical_id(action_id)
        envelope_integer(expected_revision)
        if expected_revision != 1:
            raise ValueError("Administrative abort supports preparing revision one")
        self._check_provisioner(principal_id)
        binding = self.current_binding()
        if expected_binding is not None and (
            type(expected_binding) is not ResourceBinding or binding != expected_binding
        ):
            raise SharedAdmissionDenied("Custodian binding lacks its funded coverage")
        self._require_usage_basis()
        write_started = False
        receipt: SharedAbortReceipt
        try:
            with self._owner(principal_id, require_usage=True) as connection:
                self._lock(connection, _ACTION_FAMILY, (action_id,))
                existing = self._query(
                    connection, "SELECT * FROM {admin_actions} WHERE action_id=%s", (action_id,)
                ).fetchone()
                if existing is not None and (
                    str(existing["operation_id"]) != operation_id
                    or existing["actor_principal_id"] != principal_id
                    or existing["from_revision"] != expected_revision
                ):
                    raise SharedRevisionConflict("Administrative action payload changed")
                self._lock(connection, _OPERATION_FAMILY, (operation_id,))
                row = self._query(
                    connection, "SELECT * FROM {operations} WHERE operation_id=%s", (operation_id,)
                ).fetchone()
                if row is None:
                    raise SharedAdmissionDenied("Resource operation is unavailable")
                if existing is not None:
                    if row["state"] != "released" or row["revision"] != 2:
                        raise SchemaMismatch("Administrative action outcome is inconsistent")
                    receipt = SharedAbortReceipt(action_id, operation_id)
                else:
                    if row["state"] != "preparing" or row["revision"] != expected_revision:
                        raise SharedRevisionConflict(
                            "Resource preparation state or revision changed"
                        )
                    if (
                        not row["metadata_required"]
                        or row["cost_sha256"] is None
                        or row["membership_sha256"] is None
                    ):
                        raise SharedAdmissionDenied(
                            "Resource preparation lacks the managed start contract"
                        )
                    allocations = self._query(
                        connection,
                        "SELECT * FROM {allocations} WHERE operation_id=%s ORDER BY domain_id",
                        (operation_id,),
                    ).fetchall()
                    if not allocations:
                        raise SchemaMismatch("Resource operation has no allocations")
                    self._lock(
                        connection,
                        _DOMAIN_FAMILY,
                        tuple(str(value["domain_id"]) for value in allocations),
                    )
                    releases = []
                    snapshot = []
                    for value in allocations:
                        domain_id = str(value["domain_id"])
                        domain = self._domain(connection, domain_id)
                        amount = value["amount"]
                        envelope_integer(amount)
                        used = self._used(connection, domain_id)
                        if used < amount:
                            raise SchemaMismatch("Shared resource counter underflow")
                        releases.append((domain_id, used, amount))
                        snapshot.append(
                            dict(
                                domain_id=domain_id,
                                incarnation=str(value["incarnation"]),
                                domain_revision=value["domain_revision"],
                                member_revision=value["member_revision"],
                                amount=amount,
                                unit=domain["unit"],
                                kind=domain["kind"],
                            )
                        )
                    if self.current_binding() != binding:
                        raise SharedAdmissionDenied("Custodian resource view changed before abort")
                    write_started = True
                    for domain_id, used, amount in releases:
                        self._change_used(connection, domain_id, used, used - amount)
                    updated = self._query(
                        connection,
                        "UPDATE {operations} SET state='released',revision=2 WHERE operation_id=%s AND state='preparing' AND revision=%s RETURNING *",
                        (operation_id, expected_revision),
                    ).fetchone()
                    if updated is None:
                        raise SharedRevisionConflict("Resource preparation revision changed")
                    self._query(
                        connection,
                        """INSERT INTO {admin_actions}
                        (action_id,operation_id,actor_principal_id,custodian_binding_json,from_state,from_revision,to_state,to_revision,allocations_json)
                        VALUES (%s,%s,%s,%s::jsonb,'preparing',1,'released',2,%s::jsonb)""",
                        (
                            action_id,
                            operation_id,
                            principal_id,
                            _json(asdict(binding)),
                            _json(snapshot),
                        ),
                    )
                    receipt = SharedAbortReceipt(action_id, operation_id)
                if self.current_binding() != binding:
                    raise SharedAdmissionDenied("Custodian resource view changed before commit")
        except SharedCommitUncertain:
            raise SharedActionCommitUncertain(action_id, operation_id) from None
        except (DatabaseError, InterfaceError, OSError, StorageAdmissionUnavailable):
            if write_started:
                raise SharedActionCommitUncertain(action_id, operation_id) from None
            raise
        try:
            current = self.current_binding() == binding
        except Exception:
            current = False
        if not current:
            raise SharedActionCommitUncertain(action_id, operation_id)
        return receipt

    def outstanding_resources(
        self,
        *,
        after: SharedDebtCursor | None = None,
        limit: int = 100,
        expected_binding: ResourceBinding | None = None,
    ) -> SharedDebtPage:
        """Discover durable headers without adopting, locking or freeing them.

        One SELECT gives a coherent page. Random UUID inserts below a cursor
        need a fresh pass; exhausting this pass proves neither absence nor death.
        The trusted administrative caller owns this read's physical SQL credit.
        """
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Shared debt page requires a bounded integer limit")
        if after is not None and type(after) is not SharedDebtCursor:
            raise ValueError("Shared debt page requires a typed keyset cursor")
        binding = self.current_binding()
        if expected_binding is not None and (
            type(expected_binding) is not ResourceBinding or binding != expected_binding
        ):
            raise SharedAdmissionDenied("Custodian binding lacks its funded coverage")
        rank = -1 if after is None else (0 if after.kind == "metadata_credit" else 1)
        after_id = after.entry_id if after is not None else "00000000-0000-0000-0000-000000000000"
        with connect(self.target, readonly=True) as connection, connection.transaction():
            self._verify(connection)
            rows = self._query(
                connection,
                """WITH credit_page AS (
                    SELECT 0 AS kind_order,'metadata_credit'::text AS kind,
                        credit_id AS entry_id,instance_id,process_generation,process_pid,
                        process_started_at,binding_sha256,revision,'charged'::text AS state
                    FROM {metadata_credits}
                    WHERE (%s<0 OR (%s=0 AND credit_id>%s::uuid))
                    ORDER BY credit_id LIMIT %s
                ), operation_page AS (
                    SELECT 1 AS kind_order,'operation'::text AS kind,
                        operation_id AS entry_id,instance_id,process_generation,process_pid,
                        process_started_at,binding_sha256,revision,state
                    FROM {operations}
                    WHERE state<>'released' AND (%s<1 OR (%s=1 AND operation_id>%s::uuid))
                    ORDER BY operation_id LIMIT %s
                ), debt_page AS (
                    SELECT * FROM credit_page UNION ALL SELECT * FROM operation_page
                ) SELECT * FROM debt_page ORDER BY kind_order,entry_id LIMIT %s""",
                (rank, rank, after_id, limit + 1, rank, rank, after_id, limit + 1, limit + 1),
            ).fetchall()
        if self.current_binding() != binding:
            raise SharedAdmissionDenied("Resource process binding changed during discovery")
        entries = []
        for row in rows[:limit]:
            try:
                entry = SharedDebtEntry(
                    row["kind"],
                    str(row["entry_id"]),
                    str(row["instance_id"]),
                    str(row["process_generation"]),
                    row["revision"],
                    row["state"],
                    (
                        str(row["instance_id"]),
                        str(row["process_generation"]),
                        row["process_pid"],
                        row["process_started_at"],
                        row["binding_sha256"],
                    )
                    == (
                        binding.instance_id,
                        binding.process_generation,
                        binding.process_pid,
                        binding.process_started_at,
                        binding_digest(binding),
                    ),
                )
            except ValueError:
                raise SchemaMismatch("Invalid shared resource debt record") from None
            entries.append(entry)
        cursor = (
            SharedDebtCursor(entries[-1].kind, entries[-1].entry_id) if len(rows) > limit else None
        )
        return SharedDebtPage(tuple(entries), cursor)

    @staticmethod
    def _check_owner(row: Record, binding: ResourceBinding) -> None:
        if (
            str(row["instance_id"]),
            str(row["process_generation"]),
            row["process_pid"],
            row["process_started_at"],
        ) != (
            binding.instance_id,
            binding.process_generation,
            binding.process_pid,
            binding.process_started_at,
        ):
            raise SharedAdmissionDenied("Resource operation belongs to another process incarnation")

    def audit_usage(self) -> tuple[UsageMismatch, ...]:
        """Independent single-snapshot check. Never repair rows or free operations.

        Initialization checks once; trusted control can explicitly recheck. No
        history aggregation occurs on the ordinary reservation path afterward.
        """
        return self._audit_usage(initial=False)

    def _audit_usage(self, *, initial: bool) -> tuple[UsageMismatch, ...]:
        self._check_pid()  # An inherited lock must never be acquired after fork.
        with self._audit_lock:
            with self._usage_guard:
                if initial and self._usage_known:
                    return ()
                self._usage_known = True
                self._usage_uncertain = True
            # One SQL statement sees counter, state and allocations at the same
            # MVCC snapshot, including concurrent atomic reserve/release commits.
            with connect(self.target, readonly=True) as connection:
                self._verify(connection)
                rows = self._query(
                    connection,
                    """WITH allocated AS (
                    SELECT a.domain_id,sum(a.amount) AS quantity
                    FROM {operations} o JOIN {allocations} a USING (operation_id)
                    WHERE o.state<>'released' GROUP BY a.domain_id
                ), credited AS (
                    SELECT domain_id,sum(amount) AS quantity FROM {metadata_credits}
                    GROUP BY domain_id
                ) SELECT d.domain_id,u.committed,
                    COALESCE(a.quantity,0)+COALESCE(c.quantity,0) AS allocated
                FROM {domains} d LEFT JOIN {usage} u USING (domain_id)
                LEFT JOIN allocated a USING (domain_id)
                LEFT JOIN credited c USING (domain_id)
                WHERE u.domain_id IS NULL
                    OR u.committed<>COALESCE(a.quantity,0)+COALESCE(c.quantity,0)
                ORDER BY d.domain_id""",
                ).fetchall()
                mismatches = tuple(
                    UsageMismatch(str(row["domain_id"]), row["committed"], int(row["allocated"]))
                    for row in rows
                )
            with self._usage_guard:
                self._usage_uncertain = bool(mismatches)
            return mismatches

    def _check_usage_ready(self) -> None:
        self._check_pid()
        with self._usage_guard:
            if not self._usage_known or self._usage_uncertain:
                raise SharedAdmissionDenied("Shared resource accounting requires verification")

    def _require_usage_basis(self) -> None:
        self._check_pid()
        with self._usage_guard:
            known, uncertain = self._usage_known, self._usage_uncertain
        if not known:
            self._audit_usage(initial=True)
            with self._usage_guard:
                uncertain = self._usage_uncertain
        if uncertain:
            raise SharedAdmissionDenied("Shared resource accounting requires verification")

    def reserve(self, operation_id: str, requests: Sequence[DomainRequest]) -> SharedReservation:
        """Low-level exact-domain protocol; does not promise complete mapping."""
        return self._reserve(operation_id, requests, cost=None)

    def reserve_for_cost(
        self, operation_id: str, cost: SharedResourceCost, *, require_metadata: bool = False
    ) -> SharedReservation:
        """Map a trusted server footprint to the private incarnation's entire set.

        An idempotent response acknowledges existing state, never another effect.
        Delivery wiring must retain the operation ID through uncertain commits.
        """
        if type(cost) is not SharedResourceCost or type(require_metadata) is not bool:
            raise ValueError("Shared mapping requires a typed server footprint")
        return self._reserve(operation_id, (), cost=cost, require_metadata=require_metadata)

    def _reserve(
        self,
        operation_id: str,
        requests: Sequence[DomainRequest],
        *,
        cost: SharedResourceCost | None,
        require_metadata: bool = False,
    ) -> SharedReservation:
        operation_id = canonical_id(operation_id)
        binding = self.current_binding()
        requests = tuple(requests)
        if cost is None and (
            not requests or any(type(value) is not DomainRequest for value in requests)
        ):
            raise ValueError("Resource operation requires distinct typed domain quantities")
        requests = tuple(sorted(requests, key=lambda value: value.domain_id))
        if len({value.domain_id for value in requests}) != len(requests):
            raise ValueError("Resource operation requires distinct typed domain quantities")
        self._require_usage_basis()
        request_hash = hashlib.sha256(
            _json([asdict(value) for value in requests]).encode()
        ).hexdigest()
        cost_hash = (
            hashlib.sha256(_json(asdict(cost)).encode()).hexdigest() if cost is not None else None
        )
        membership_hash = None
        with self._runtime(require_usage=True) as connection:
            if cost is not None:
                self._lock_membership(connection, binding)
            self._lock(connection, _OPERATION_FAMILY, (operation_id,))
            existing = self._query(
                connection, "SELECT * FROM {operations} WHERE operation_id=%s", (operation_id,)
            ).fetchone()
            if existing is not None:
                self._check_owner(existing, binding)
                if (
                    existing["cost_sha256"] != cost_hash
                    or existing["metadata_required"] is not require_metadata
                    or (cost is None and existing["request_sha256"] != request_hash)
                    or (cost is not None and existing["membership_sha256"] is None)
                ):
                    raise SharedRevisionConflict("Resource operation payload changed")
                return _receipt(existing)
            if cost is not None:
                domains, membership_hash = self._membership_basis(connection, binding)
                if require_metadata:
                    self._metadata_coverage_locked(connection, binding, domains, membership_hash)
                requests = self._cost_requests(domains, cost)
                request_hash = hashlib.sha256(
                    _json([asdict(value) for value in requests]).encode()
                ).hexdigest()
            self._lock(connection, _DOMAIN_FAMILY, tuple(value.domain_id for value in requests))
            confirmed = []
            for value in requests:
                domain = self._domain(connection, value.domain_id)
                member = self._query(
                    connection,
                    "SELECT * FROM {members} WHERE domain_id=%s AND instance_id=%s AND process_generation=%s",
                    (value.domain_id, binding.instance_id, binding.process_generation),
                ).fetchone()
                if (
                    member is None
                    or not member["active"]
                    or member["binding_sha256"] != binding_digest(binding)
                    or not domain["active"]
                ):
                    raise SharedAdmissionDenied("Resource domain membership is unavailable")
                if (
                    str(domain["incarnation"]) != value.incarnation
                    or domain["revision"] != value.domain_revision
                ):
                    raise SharedRevisionConflict("Resource domain basis changed")
                used = self._used(connection, value.domain_id)
                if used + value.amount > domain["capacity"]:
                    raise SharedCapacityUnavailable("Shared resource capacity is exhausted")
                confirmed.append((value, member["revision"], used))
            self._query(
                connection,
                """INSERT INTO {operations}
                (operation_id,instance_id,process_generation,process_pid,process_started_at,binding_sha256,request_sha256,state,revision,membership_sha256,cost_sha256,metadata_required)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'preparing',1,%s,%s,%s)""",
                (
                    operation_id,
                    binding.instance_id,
                    binding.process_generation,
                    binding.process_pid,
                    binding.process_started_at,
                    binding_digest(binding),
                    request_hash,
                    membership_hash,
                    cost_hash,
                    require_metadata,
                ),
            )
            for value, member_revision, used in confirmed:
                self._query(
                    connection,
                    "INSERT INTO {allocations} VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        operation_id,
                        value.domain_id,
                        value.incarnation,
                        value.domain_revision,
                        member_revision,
                        value.amount,
                    ),
                )
                self._change_used(connection, value.domain_id, used, used + value.amount)
            if self.current_binding() != binding:
                raise SharedAdmissionDenied("Resource process binding changed during admission")
        return SharedReservation(operation_id, 1, "preparing")

    def transition(
        self, operation_id: str, *, expected_revision: int, state: ReservationState
    ) -> SharedReservation:
        operation_id = canonical_id(operation_id)
        envelope_integer(expected_revision)
        if state not in _TRANSITIONS:
            raise ValueError("Invalid resource operation state")
        binding = self.current_binding()
        if state == "active":
            self._require_usage_basis()
        with self._runtime(require_usage=state == "active") as connection:
            if state == "active":
                self._lock_membership(connection, binding)
            self._lock(connection, _OPERATION_FAMILY, (operation_id,))
            row = self._query(
                connection, "SELECT * FROM {operations} WHERE operation_id=%s", (operation_id,)
            ).fetchone()
            if row is None:
                raise SharedAdmissionDenied("Resource operation is unavailable")
            self._check_owner(row, binding)
            if row["revision"] == expected_revision + 1 and row["state"] == state:
                return _receipt(row)  # Exact CAS acknowledgement retry, no new transition.
            if row["revision"] != expected_revision or state not in _TRANSITIONS[row["state"]]:
                raise SharedRevisionConflict("Resource operation state or revision changed")
            allocations = self._query(
                connection,
                "SELECT * FROM {allocations} WHERE operation_id=%s ORDER BY domain_id",
                (operation_id,),
            ).fetchall()
            if not allocations:
                raise SchemaMismatch("Resource operation has no allocations")
            if state == "active" and row["membership_sha256"] is not None:
                domains, current_membership = self._membership_basis(connection, binding)
                if row["membership_sha256"] != current_membership:
                    raise SharedRevisionConflict(
                        "Complete resource membership changed before start"
                    )
                if row["metadata_required"]:
                    self._metadata_coverage_locked(connection, binding, domains, current_membership)
            self._lock(
                connection, _DOMAIN_FAMILY, tuple(str(value["domain_id"]) for value in allocations)
            )
            if state == "active":
                if row["binding_sha256"] != binding_digest(binding):
                    raise SharedAdmissionDenied("Resource operation view changed before start")
                for value in allocations:
                    domain_id = str(value["domain_id"])
                    domain = self._domain(connection, domain_id)
                    member = self._query(
                        connection,
                        "SELECT * FROM {members} WHERE domain_id=%s AND instance_id=%s AND process_generation=%s",
                        (domain_id, binding.instance_id, binding.process_generation),
                    ).fetchone()
                    if (
                        not domain["active"]
                        or member is None
                        or not member["active"]
                        or member["binding_sha256"] != binding_digest(binding)
                    ):
                        raise SharedAdmissionDenied("Resource authority was withdrawn before start")
                    if (
                        str(domain["incarnation"]) != str(value["incarnation"])
                        or domain["revision"] != value["domain_revision"]
                        or member["revision"] != value["member_revision"]
                    ):
                        raise SharedRevisionConflict(
                            "Resource authority basis changed before start"
                        )
                    if self._used(connection, domain_id) > domain["capacity"]:
                        raise SharedCapacityUnavailable("Shared resource domain has active debt")
                if self.current_binding() != binding:
                    raise SharedAdmissionDenied("Resource process binding changed before start")
            if state == "released":
                for value in allocations:
                    domain_id = str(value["domain_id"])
                    used = self._used(connection, domain_id)
                    if used < value["amount"]:
                        raise SchemaMismatch("Shared resource counter underflow")
                    self._change_used(connection, domain_id, used, used - value["amount"])
            updated = self._query(
                connection,
                "UPDATE {operations} SET state=%s,revision=revision+1 WHERE operation_id=%s AND revision=%s RETURNING *",
                (state, operation_id, expected_revision),
            ).fetchone()
            if updated is None:
                raise SharedRevisionConflict("Resource operation revision changed")
            return _receipt(updated)

    def used(self, domain_id: str) -> int:
        domain_id = canonical_id(domain_id)
        self._check_pid()
        with connect(self.target, readonly=True) as connection:
            self._verify(connection)
            self._domain(connection, domain_id)
            return self._used(connection, domain_id)
