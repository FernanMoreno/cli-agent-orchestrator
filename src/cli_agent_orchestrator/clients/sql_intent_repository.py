"""Pre-TCP journal on an existing paid control channel; no connection fallback.

Receipts describe retained attempts. They never prove server absence or release
credits. Scoped recording is not a claim that process bootstrap is covered.
"""

import hashlib
import json
import re
from contextlib import contextmanager
from typing import Callable, Iterator, Sequence
from uuid import uuid4

from psycopg import sql

from cli_agent_orchestrator.clients.admission_repository import (
    ResourceAdmissionRepository,
    binding_digest,
)
from cli_agent_orchestrator.clients.admission_schema import RESOURCE_ADMISSION_MIGRATIONS
from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget, Record
from cli_agent_orchestrator.clients.postgres.connection import (
    ConnectionCursor,
    FundedSqlCommitUncertain,
)
from cli_agent_orchestrator.clients.postgres.migrations import (
    SchemaMismatch,
    verify_component,
    verify_runtime_role,
)
from cli_agent_orchestrator.clients.sql_opening_schema import (
    SQL_OPENING_CHANNELS,
    SQL_OPENING_FENCES,
    SQL_OPENING_INTENTS,
    SQL_OPENING_MIGRATIONS,
    SQL_OPENING_QUOTAS,
    SQL_OPENING_TABLES,
)
from cli_agent_orchestrator.models.admission_domains import canonical_id
from cli_agent_orchestrator.models.resource_envelope import ResourceBinding, envelope_integer
from cli_agent_orchestrator.models.sql_opening_intent import (
    SqlOpeningIntent,
    SqlOpeningPreparationUncertain,
)
from cli_agent_orchestrator.models.sql_process_origin import is_sql_process_application_name
from cli_agent_orchestrator.utils.process_sql_origin import (
    current_sql_application_name,
    sql_opening_recorder,
)


class SqlIntentUnavailable(RuntimeError):
    """Registration is unavailable, never permission to run an unrecorded effect."""


class SqlIntentConflict(SqlIntentUnavailable):
    pass


class SqlIntentCapacityUnavailable(SqlIntentUnavailable):
    pass


class SqlIntentRegistrationSealed(SqlIntentUnavailable):
    pass


class SqlIntentCommitUncertain(SqlIntentUnavailable, SqlOpeningPreparationUncertain):
    def __init__(self, journal_id: str, intent_id: str | None):
        super().__init__("SQL intent commit requires reconciliation")
        self.journal_id = journal_id
        self.intent_id = intent_id


def _receipt(row: Record) -> SqlOpeningIntent:
    return SqlOpeningIntent(
        str(row["intent_id"]),
        str(row["journal_id"]),
        row["ordinal"],
        row["path"],
        row["destination_sha256"],
        row["sql_application_name"],
    )


class SqlIntentRepository:
    def __init__(self, target: DatabaseTarget, *, binding_supplier: Callable[[], ResourceBinding]):
        self.target = target
        self._identity = ResourceAdmissionRepository(target, binding_supplier=binding_supplier)
        self._tables = {
            "quotas": SQL_OPENING_QUOTAS,
            "channels": SQL_OPENING_CHANNELS,
            "intents": SQL_OPENING_INTENTS,
            "fences": SQL_OPENING_FENCES,
            "credits": "_cao_resource_metadata_credits",
            "domains": "_cao_resource_domains",
        }

    def _q(
        self, c: Connection, statement: str, values: Sequence[object] = ()
    ) -> ConnectionCursor[Record]:
        return c.execute(
            sql.SQL(statement).format(
                **{
                    key: sql.Identifier(self.target.schema, name)
                    for key, name in self._tables.items()
                }
            ),
            values,
        )

    @staticmethod
    def _lock(c: Connection, kind: str, key: str) -> None:
        c.execute(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s,0))",
            ("cao-sql-opening:" + kind + ":" + key,),
        )

    def _verify_acl(self, c: Connection) -> None:
        for name in SQL_OPENING_TABLES:
            append = name != SQL_OPENING_QUOTAS
            row = c.execute(
                """SELECT c.relkind IN ('r','p')
                AND has_table_privilege(current_user,c.oid,'SELECT')
                AND has_table_privilege(current_user,c.oid,'INSERT')=%s
                AND (%s OR NOT has_any_column_privilege(current_user,c.oid,'INSERT'))
                AND NOT (has_any_column_privilege(current_user,c.oid,'UPDATE')
                    OR has_table_privilege(current_user,c.oid,'DELETE')
                    OR has_table_privilege(current_user,c.oid,'TRUNCATE')
                    OR has_any_column_privilege(current_user,c.oid,'REFERENCES')
                    OR has_table_privilege(current_user,c.oid,'TRIGGER')
                    OR has_table_privilege(current_user,c.oid,'MAINTAIN'))
                AND NOT EXISTS(SELECT 1 FROM aclexplode(CASE WHEN cardinality(c.relacl)>0
                    THEN c.relacl ELSE acldefault('r',c.relowner) END) a WHERE a.grantee=0)
                AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute att,
                    LATERAL aclexplode(CASE WHEN cardinality(att.attacl)>0 THEN att.attacl END) a
                    WHERE att.attrelid=c.oid AND a.grantee=0)
                FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=%s AND c.relname=%s""",
                (append, append, self.target.schema, name),
            ).fetchone()
            if row is None or row[0] is not True:
                raise SchemaMismatch("SQL intent journal privileges are unsafe")

    @contextmanager
    def _transaction(
        self, channel: Connection, journal: str, request: str | None = None, *, owner: bool = False
    ) -> Iterator[tuple[Connection, ResourceBinding]]:
        if (
            type(channel) is not Connection
            or channel.target.identity != self.target.identity
            or channel.target.scope != self.target.scope
        ):
            raise SqlIntentUnavailable("SQL intent channel belongs to another target")
        binding = self._identity.current_binding()
        try:
            with channel.funded_transaction():
                channel.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                verify_component(channel, SQL_OPENING_MIGRATIONS)
                verify_component(channel, RESOURCE_ADMISSION_MIGRATIONS)
                if owner:
                    owned = channel.execute(
                        """SELECT count(*)=%s AND bool_and(
                        c.relowner=r.oid AND n.nspowner=r.oid) FROM pg_catalog.pg_class c
                        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                        JOIN pg_catalog.pg_roles r ON r.rolname=current_user
                        WHERE n.nspname=%s AND c.relname=ANY(%s)""",
                        (len(SQL_OPENING_TABLES), self.target.schema, list(SQL_OPENING_TABLES)),
                    ).fetchone()
                    if owned is None or owned[0] is not True:
                        raise SqlIntentUnavailable("SQL journal provisioner must own its schema")
                else:
                    verify_runtime_role(channel)
                    self._verify_acl(channel)
                yield channel, binding
                if self._identity.current_binding() != binding:
                    raise SqlIntentUnavailable("SQL journal process binding changed")
        except FundedSqlCommitUncertain as failure:
            raise SqlIntentCommitUncertain(journal, request) from failure

    def _credit(self, c: Connection, credit: str, binding: ResourceBinding) -> None:
        row = self._q(
            c,
            """SELECT m.instance_id,m.process_generation,m.process_pid,
            m.process_started_at,m.binding_sha256 FROM {credits} m
            JOIN {domains} d ON d.domain_id=m.domain_id WHERE m.credit_id=%s
            AND m.amount=1 AND d.kind='storage' AND d.unit='postgres_session'""",
            (credit,),
        ).fetchone()
        expected = (
            binding.instance_id,
            binding.process_generation,
            binding.process_pid,
            binding.process_started_at,
            binding_digest(binding),
        )
        if row is None or (str(row[0]), str(row[1]), row[2], row[3], row[4]) != expected:
            raise SqlIntentUnavailable("SQL journal metadata root does not match its process")

    def _quota(self, c: Connection, journal: str, binding: ResourceBinding) -> Record:
        row = self._q(c, "SELECT * FROM {quotas} WHERE journal_id=%s", (journal,)).fetchone()
        if row is None or (
            str(row["instance_id"]),
            str(row["process_generation"]),
            row["binding_sha256"],
        ) != (binding.instance_id, binding.process_generation, binding_digest(binding)):
            raise SqlIntentUnavailable("SQL journal quota is unavailable for this process")
        self._credit(c, str(row["credit_id"]), binding)
        return row

    def provision(
        self,
        channel: Connection,
        *,
        journal_id: str,
        credit_id: str,
        max_records: int,
        principal_id: str,
        participant_binding: ResourceBinding | None = None,
    ) -> None:
        journal, credit = canonical_id(journal_id), canonical_id(credit_id)
        envelope_integer(max_records, minimum=0)
        if (
            max_records > 2**63 - 1
            or not isinstance(principal_id, str)
            or re.fullmatch(r"principal-[0-9a-f]{64}", principal_id) is None
        ):
            raise ValueError("Invalid SQL journal quota")
        with self._transaction(channel, journal, owner=True) as (c, actor_binding):
            binding = actor_binding if participant_binding is None else participant_binding
            target_hash = hashlib.sha256(
                json.dumps(self.target.identity, separators=(",", ":")).encode()
            ).hexdigest()
            if (
                type(binding) is not ResourceBinding
                or binding.store_id != canonical_id(self.target.store_id)
                or binding.target_identity_sha256 != target_hash
            ):
                raise SqlIntentUnavailable("SQL journal participant belongs to another store")
            self._lock(c, "credit", credit)
            self._lock(c, "journal", journal)
            self._credit(c, credit, binding)
            existing = self._q(
                c,
                """SELECT * FROM {quotas} WHERE journal_id=%s
                OR credit_id=%s OR (instance_id=%s AND process_generation=%s)""",
                (journal, credit, binding.instance_id, binding.process_generation),
            ).fetchall()
            if existing:
                row = existing[0]
                if len(existing) != 1 or (
                    str(row["journal_id"]),
                    str(row["credit_id"]),
                    row["binding_sha256"],
                    row["max_records"],
                    row["actor_principal_id"],
                ) != (journal, credit, binding_digest(binding), max_records, principal_id):
                    raise SqlIntentConflict("SQL journal quota identity changed")
                return
            self._q(
                c,
                """INSERT INTO {quotas} (journal_id,credit_id,instance_id,
                process_generation,binding_sha256,max_records,actor_principal_id)
                VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (
                    journal,
                    credit,
                    binding.instance_id,
                    binding.process_generation,
                    binding_digest(binding),
                    max_records,
                    principal_id,
                ),
            )

    def _claim_channel(self, c: Connection, journal: str, binding: ResourceBinding) -> None:
        own = c.execute("""SELECT pid,backend_start FROM pg_catalog.pg_stat_activity
            WHERE pid=pg_catalog.pg_backend_pid()""").fetchone()
        if own is None or own[1] is None:
            raise SqlIntentUnavailable("SQL control backend identity is unavailable")
        row = self._q(c, "SELECT * FROM {channels} WHERE journal_id=%s", (journal,)).fetchone()
        if row is None:
            self._q(
                c,
                """INSERT INTO {channels} (journal_id,binding_sha256,backend_pid,backend_started_at)
                VALUES(%s,%s,%s,%s)""",
                (journal, binding_digest(binding), own[0], own[1]),
            )
        elif (row["binding_sha256"], row["backend_pid"], row["backend_started_at"]) != (
            binding_digest(binding),
            own[0],
            own[1],
        ):
            raise SqlIntentConflict("SQL journal belongs to its original control backend")

    def record(
        self,
        channel: Connection,
        *,
        journal_id: str,
        intent_id: str,
        path: str,
        destination: DatabaseTarget,
    ) -> SqlOpeningIntent:
        journal, request = canonical_id(journal_id), canonical_id(intent_id)
        if type(path) is not str or path not in {"native", "orm", "audit"}:
            raise ValueError("Unsupported SQL opening path")
        if (
            type(destination) is not DatabaseTarget
            or not isinstance(destination.scope, str)
            or not (1 <= len(destination.scope) <= 128)
        ):
            raise ValueError("SQL intent requires an explicit configured target")
        label = current_sql_application_name()
        if not is_sql_process_application_name(label):
            raise SqlIntentUnavailable("SQL journal requires supervised process attribution")
        digest = hashlib.sha256(
            json.dumps(
                (destination.identity, destination.user, destination.sslmode), separators=(",", ":")
            ).encode()
        ).hexdigest()
        with self._transaction(channel, journal, request) as (c, binding):
            self._lock(c, "journal", journal)
            quota = self._quota(c, journal, binding)
            if self._q(
                c, "SELECT journal_id FROM {fences} WHERE journal_id=%s", (journal,)
            ).fetchone():
                raise SqlIntentRegistrationSealed("SQL journal registration is sealed")
            self._lock(c, "intent", request)
            if self._q(
                c, "SELECT intent_id FROM {intents} WHERE intent_id=%s", (request,)
            ).fetchone():
                raise SqlIntentConflict("A retained SQL intent cannot grant another opening")
            last = self._q(
                c,
                """SELECT ordinal FROM {intents} WHERE journal_id=%s
                ORDER BY ordinal DESC LIMIT 1""",
                (journal,),
            ).fetchone()
            ordinal = 1 if last is None else last[0] + 1
            if ordinal > quota["max_records"]:
                raise SqlIntentCapacityUnavailable("SQL journal record capacity is exhausted")
            self._claim_channel(c, journal, binding)
            row = self._q(
                c,
                """INSERT INTO {intents} (intent_id,journal_id,ordinal,
                binding_sha256,process_pid,process_started_at,sql_application_name,path,
                destination_store_id,destination_scope,destination_sha256)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    request,
                    journal,
                    ordinal,
                    binding_digest(binding),
                    binding.process_pid,
                    binding.process_started_at,
                    label,
                    path,
                    canonical_id(destination.store_id),
                    destination.scope,
                    digest,
                ),
            ).fetchone()
            if row is None:
                raise SchemaMismatch("SQL intent was not persisted")
            receipt = _receipt(row)
        return receipt  # Only after the independent funded commit was acknowledged.

    def read(
        self, channel: Connection, *, journal_id: str, intent_id: str
    ) -> SqlOpeningIntent | None:
        journal, request = canonical_id(journal_id), canonical_id(intent_id)
        with self._transaction(channel, journal) as (c, binding):
            self._quota(c, journal, binding)
            row = self._q(
                c,
                "SELECT * FROM {intents} WHERE journal_id=%s AND intent_id=%s",
                (journal, request),
            ).fetchone()
            if row is not None and (
                row["binding_sha256"],
                row["process_pid"],
                row["process_started_at"],
            ) != (binding_digest(binding), binding.process_pid, binding.process_started_at):
                raise SchemaMismatch("SQL intent has a different original process binding")
            return None if row is None else _receipt(row)

    def fence(self, channel: Connection, *, journal_id: str) -> None:
        journal = canonical_id(journal_id)
        with self._transaction(channel, journal) as (c, binding):
            self._lock(c, "journal", journal)
            self._quota(c, journal, binding)
            self._q(
                c,
                """INSERT INTO {fences}(journal_id,binding_sha256) VALUES(%s,%s)
                ON CONFLICT(journal_id) DO NOTHING""",
                (journal, binding_digest(binding)),
            )
            row = self._q(
                c, "SELECT binding_sha256 FROM {fences} WHERE journal_id=%s", (journal,)
            ).fetchone()
            if row is None or row[0] != binding_digest(binding):
                raise SchemaMismatch("SQL registration fence does not match its process")

    @contextmanager
    def record_openings(self, channel: Connection, *, journal_id: str) -> Iterator[None]:
        journal = canonical_id(journal_id)
        with channel.funded_transaction():
            pass  # Reject escaped/unfunded channels before installing a recorder.

        def before_driver(path: str, destination: object | None) -> None:
            if type(destination) is not DatabaseTarget:
                raise SqlIntentUnavailable("SQL opening did not supply its configured target")
            self.record(
                channel,
                journal_id=journal,
                intent_id=str(uuid4()),
                path=path,
                destination=destination,
            )

        with sql_opening_recorder(before_driver):
            yield
