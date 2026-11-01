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

import psutil
from psycopg import sql

from cli_agent_orchestrator.clients.admission_schema import (
    AUTHORITY_TABLES,
    LEDGER_TABLES,
    RESOURCE_ADMISSION_MIGRATIONS,
)
from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget, Record, connect
from cli_agent_orchestrator.clients.postgres.connection import ConnectionCursor
from cli_agent_orchestrator.clients.postgres.migrations import (
    SchemaMismatch,
    verify_component,
    verify_runtime_role,
)
from cli_agent_orchestrator.models.admission_domains import (
    DomainRecord,
    DomainRequest,
    DomainSpec,
    ReservationState,
    SharedReservation,
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


class SharedCapacityUnavailable(RuntimeError):
    """At least one known domain cannot cover the whole operation."""


_DOMAIN_FAMILY = 0x43414F44
_OPERATION_FAMILY = 0x43414F4F
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
        AUTHORITY_TABLES + LEDGER_TABLES,
    )
)
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

    @contextmanager
    def _owner(self, principal_id: str):
        self._check_pid()
        if (
            not isinstance(principal_id, str)
            or re.fullmatch(r"principal-[0-9a-f]{64}", principal_id) is None
        ):
            raise ValueError("Invalid provisioner identity")
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
    ) -> int:
        domain_id = canonical_id(domain_id)
        envelope_integer(expected_revision, minimum=0)
        if (
            type(binding) is not ResourceBinding
            or binding.store_id != self._coordinator_store_id
            or type(active) is not bool
        ):
            raise ValueError("Invalid resource membership provision")
        with self._owner(principal_id) as connection:
            self._lock(connection, _DOMAIN_FAMILY, (domain_id,))
            self._domain(connection, domain_id)
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
        return expected_revision + 1

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
                ) SELECT d.domain_id,u.committed,COALESCE(a.quantity,0) AS allocated
                FROM {domains} d LEFT JOIN {usage} u USING (domain_id)
                LEFT JOIN allocated a USING (domain_id)
                WHERE u.domain_id IS NULL OR u.committed<>COALESCE(a.quantity,0)
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
        operation_id = canonical_id(operation_id)
        binding = self.current_binding()
        requests = tuple(requests)
        if not requests or any(type(value) is not DomainRequest for value in requests):
            raise ValueError("Resource operation requires distinct typed domain quantities")
        requests = tuple(sorted(requests, key=lambda value: value.domain_id))
        if len({value.domain_id for value in requests}) != len(requests):
            raise ValueError("Resource operation requires distinct typed domain quantities")
        self._require_usage_basis()
        request_hash = hashlib.sha256(
            _json([asdict(value) for value in requests]).encode()
        ).hexdigest()
        with self._runtime(require_usage=True) as connection:
            self._lock(connection, _OPERATION_FAMILY, (operation_id,))
            existing = self._query(
                connection, "SELECT * FROM {operations} WHERE operation_id=%s", (operation_id,)
            ).fetchone()
            if existing is not None:
                self._check_owner(existing, binding)
                if existing["request_sha256"] != request_hash:
                    raise SharedRevisionConflict("Resource operation payload changed")
                return _receipt(existing)
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
                (operation_id,instance_id,process_generation,process_pid,process_started_at,binding_sha256,request_sha256,state,revision)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'preparing',1)""",
                (
                    operation_id,
                    binding.instance_id,
                    binding.process_generation,
                    binding.process_pid,
                    binding.process_started_at,
                    binding_digest(binding),
                    request_hash,
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
