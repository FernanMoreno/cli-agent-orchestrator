"""Pre-TCP journal on an existing paid control channel; no connection fallback.

Receipts describe retained attempts. They never prove server absence or release
credits. Scoped recording is not a claim that process bootstrap is covered.
"""

import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import asdict, fields, replace
from typing import TYPE_CHECKING, Callable, Iterator, Sequence
from uuid import uuid4

from psycopg import sql
from psycopg.types.json import Jsonb

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
    SQL_CHILD_FOLLOWUPS,
    SQL_CHILD_FUNDINGS,
    SQL_CHILD_OPENINGS,
    SQL_OPENING_ALL_TABLES,
    SQL_OPENING_CHANNELS,
    SQL_OPENING_FENCES,
    SQL_OPENING_INTENTS,
    SQL_OPENING_MIGRATIONS,
    SQL_OPENING_QUOTAS,
)
from cli_agent_orchestrator.models.admission_domains import canonical_id
from cli_agent_orchestrator.models.resource_envelope import ResourceBinding, envelope_integer
from cli_agent_orchestrator.models.sql_child_subject import VerifiedChildSqlSubject
from cli_agent_orchestrator.models.sql_opening_intent import (
    ChildSqlFollowupIntent,
    ChildSqlOpeningIntent,
    SqlOpeningIntent,
    SqlOpeningPreparationUncertain,
)
from cli_agent_orchestrator.models.sql_process_origin import is_sql_process_application_name
from cli_agent_orchestrator.utils.process_sql_origin import (
    current_sql_application_name,
    sql_opening_recorder,
)

if TYPE_CHECKING:
    from cli_agent_orchestrator.clients.storage_admission import ChildSqlSessionCharge


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


class SqlChildAckUnavailableAfterCommit(SqlIntentUnavailable):
    """The row committed; subject verification or ACK completion is unavailable."""

    def __init__(self, receipt: ChildSqlOpeningIntent):
        super().__init__("Child acknowledgement unavailable after confirmed commit")
        self.receipt = receipt
        self.journal_id = receipt.journal_id
        self.intent_id = receipt.intent_id


class SqlFollowupAckUnavailableAfterCommit(SqlIntentUnavailable):
    """Confirmed follow-up survives loss of its post-commit subject observation."""

    def __init__(self, receipt: ChildSqlFollowupIntent):
        super().__init__("Follow-up acknowledgement unavailable after confirmed commit")
        self.receipt = receipt
        self.journal_id = receipt.journal_id
        self.intent_id = receipt.intent_id


def _receipt(row: Record) -> SqlOpeningIntent:
    return SqlOpeningIntent(
        str(row["intent_id"]),
        str(row["journal_id"]),
        row["ordinal"],
        row["path"],
        row["destination_sha256"],
        row["sql_application_name"],
    )


def _child_receipt(row: Record) -> ChildSqlOpeningIntent:
    try:
        raw = row["subject_binding"]
        if type(raw) is not dict or set(raw) != {f.name for f in fields(ResourceBinding)}:
            raise ValueError("invalid subject shape")
        subject = ResourceBinding(**raw)
        if (
            binding_digest(subject),
            subject.instance_id,
            subject.process_generation,
            subject.process_pid,
            subject.process_started_at,
            subject.store_id,
        ) != (
            row["subject_binding_sha256"],
            str(row["subject_instance_id"]),
            str(row["subject_process_generation"]),
            row["subject_process_pid"],
            row["subject_process_started_at"],
            str(row["destination_store_id"]),
        ):
            raise ValueError("subject projection differs")
        return ChildSqlOpeningIntent(
            intent_id=str(row["intent_id"]),
            journal_id=str(row["journal_id"]),
            ordinal=row["ordinal"],
            path=row["path"],
            destination_sha256=row["destination_sha256"],
            parent_binding_sha256=row["parent_binding_sha256"],
            parent_process_pid=row["parent_process_pid"],
            parent_process_started_at=row["parent_process_started_at"],
            parent_sql_application_name=row["parent_sql_application_name"],
            subject=subject,
            subject_sql_application_name=row["subject_sql_application_name"],
            boot_id=str(row["boot_id"]),
            start_time_ticks=row["start_time_ticks"],
            pid_namespace=(row["pid_namespace_device"], row["pid_namespace_inode"]),
        )
    except (ValueError, TypeError, KeyError) as failure:
        raise SchemaMismatch("Retained child opening has an invalid subject") from failure


def _followup_receipt(row: Record, origin: ChildSqlOpeningIntent) -> ChildSqlFollowupIntent:
    try:
        if (
            str(row["startup_intent_id"]),
            str(row["journal_id"]),
            str(row["destination_store_id"]),
        ) != (origin.intent_id, origin.journal_id, origin.subject.store_id):
            raise ValueError("follow-up anchor differs")
        return ChildSqlFollowupIntent(
            str(row["intent_id"]),
            str(row["journal_id"]),
            row["ordinal"],
            origin.intent_id,
            row["sequence"],
            row["path"],
            row["destination_scope"],
            row["destination_sha256"],
            binding_digest(origin.subject),
            origin.subject_sql_application_name,
        )
    except (ValueError, TypeError, KeyError) as failure:
        raise SchemaMismatch("retained follow-up request is invalid") from failure


class SqlIntentRepository:
    def __init__(self, target: DatabaseTarget, *, binding_supplier: Callable[[], ResourceBinding]):
        self.target = target
        self._identity = ResourceAdmissionRepository(target, binding_supplier=binding_supplier)
        self._tables = {
            "quotas": SQL_OPENING_QUOTAS,
            "channels": SQL_OPENING_CHANNELS,
            "intents": SQL_OPENING_INTENTS,
            "fences": SQL_OPENING_FENCES,
            "children": SQL_CHILD_OPENINGS,
            "followups": SQL_CHILD_FOLLOWUPS,
            "fundings": SQL_CHILD_FUNDINGS,
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
        for name in SQL_OPENING_ALL_TABLES:
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
                        (
                            len(SQL_OPENING_ALL_TABLES),
                            self.target.schema,
                            list(SQL_OPENING_ALL_TABLES),
                        ),
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

    @staticmethod
    def _opening_payload(path: str, destination: DatabaseTarget) -> tuple[str, str]:
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
        return label, digest

    def _append_position(
        self, c: Connection, journal: str, request: str, binding: ResourceBinding
    ) -> int:
        self._lock(c, "journal", journal)
        quota = self._quota(c, journal, binding)
        if self._q(c, "SELECT journal_id FROM {fences} WHERE journal_id=%s", (journal,)).fetchone():
            raise SqlIntentRegistrationSealed("SQL journal registration is sealed")
        self._lock(c, "intent", request)
        if self._q(
            c,
            """SELECT intent_id FROM {intents} WHERE intent_id=%s
            UNION ALL SELECT intent_id FROM {children} WHERE intent_id=%s
            UNION ALL SELECT intent_id FROM {followups} WHERE intent_id=%s LIMIT 1""",
            (request, request, request),
        ).fetchone():
            raise SqlIntentConflict("A retained SQL intent cannot grant another opening")
        last = self._q(
            c,
            """SELECT GREATEST(
            COALESCE((SELECT ordinal FROM {intents} WHERE journal_id=%s ORDER BY ordinal DESC LIMIT 1),0),
            COALESCE((SELECT ordinal FROM {children} WHERE journal_id=%s ORDER BY ordinal DESC LIMIT 1),0),
            COALESCE((SELECT ordinal FROM {followups} WHERE journal_id=%s ORDER BY ordinal DESC LIMIT 1),0))""",
            (journal, journal, journal),
        ).fetchone()
        if last is None:
            raise SchemaMismatch("SQL journal position is unavailable")
        previous = last[0]
        if type(previous) is not int or previous < 0:
            raise SchemaMismatch("SQL journal position is invalid")
        ordinal = previous + 1
        if ordinal > quota["max_records"]:
            raise SqlIntentCapacityUnavailable("SQL journal record capacity is exhausted")
        self._claim_channel(c, journal, binding)
        return ordinal

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
        label, digest = self._opening_payload(path, destination)
        with self._transaction(channel, journal, request) as (c, binding):
            ordinal = self._append_position(c, journal, request, binding)
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

    def record_child_opening(
        self,
        channel: Connection,
        *,
        journal_id: str,
        intent_id: str,
        subject: VerifiedChildSqlSubject,
        destination: DatabaseTarget,
        path: str = "native",
        session_charge: "ChildSqlSessionCharge | None" = None,
    ) -> ChildSqlOpeningIntent:
        journal, request = canonical_id(journal_id), canonical_id(intent_id)
        if type(subject) is not VerifiedChildSqlSubject:
            raise ValueError("child opening requires an authenticated live subject")
        subject.claim()
        label, digest = self._opening_payload(path, destination)
        target_digest = hashlib.sha256(
            json.dumps(destination.identity, separators=(",", ":")).encode()
        ).hexdigest()
        bound = subject.binding
        if (
            bound.store_id != canonical_id(destination.store_id)
            or bound.target_identity_sha256 != target_digest
        ):
            raise SqlIntentUnavailable(
                "child opening destination differs from its verified binding"
            )
        physical = json.dumps(
            (subject.boot_id, subject.pid_namespace, bound.process_pid, subject.start_time_ticks),
            separators=(",", ":"),
        )
        if session_charge is not None:
            channel._claim_child_sql_session(session_charge, subject, request)
        with self._transaction(channel, journal, request) as (c, actor):
            if actor.process_pid == bound.process_pid or (
                actor.instance_id,
                actor.process_generation,
            ) == (bound.instance_id, bound.process_generation):
                raise SqlIntentUnavailable("child opening cannot adopt its parent identity")
            ordinal = self._append_position(c, journal, request, actor)
            # All global subject keys use one stable order across journals.
            for key in sorted(
                {
                    "binding:" + binding_digest(bound),
                    "generation:" + bound.instance_id + ":" + bound.process_generation,
                    "physical:" + physical,
                    "label:" + subject.sql_application_name,
                }
            ):
                self._lock(c, "child", key)
            if self._q(
                c,
                """SELECT intent_id FROM {children}
                WHERE subject_binding_sha256=%s OR (subject_instance_id=%s AND subject_process_generation=%s)
                OR (boot_id=%s AND pid_namespace_device=%s AND pid_namespace_inode=%s
                    AND subject_process_pid=%s AND start_time_ticks=%s)
                OR subject_sql_application_name=%s LIMIT 1""",
                (
                    binding_digest(bound),
                    bound.instance_id,
                    bound.process_generation,
                    subject.boot_id,
                    subject.pid_namespace[0],
                    subject.pid_namespace[1],
                    bound.process_pid,
                    subject.start_time_ticks,
                    subject.sql_application_name,
                ),
            ).fetchone():
                raise SqlIntentConflict("A retained child startup cannot grant another opening")
            subject.check()
            row = self._q(
                c,
                """INSERT INTO {children}
                (intent_id,journal_id,ordinal,parent_binding_sha256,parent_process_pid,
                parent_process_started_at,parent_sql_application_name,
                subject_binding,subject_binding_sha256,subject_instance_id,subject_process_generation,
                subject_process_pid,subject_process_started_at,subject_sql_application_name,
                boot_id,start_time_ticks,pid_namespace_device,pid_namespace_inode,
                path,destination_store_id,destination_scope,destination_sha256)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    request,
                    journal,
                    ordinal,
                    binding_digest(actor),
                    actor.process_pid,
                    actor.process_started_at,
                    label,
                    Jsonb(asdict(bound)),
                    binding_digest(bound),
                    bound.instance_id,
                    bound.process_generation,
                    bound.process_pid,
                    bound.process_started_at,
                    subject.sql_application_name,
                    subject.boot_id,
                    subject.start_time_ticks,
                    subject.pid_namespace[0],
                    subject.pid_namespace[1],
                    path,
                    canonical_id(destination.store_id),
                    destination.scope,
                    digest,
                ),
            ).fetchone()
            if row is None:
                raise SchemaMismatch("Child SQL opening was not persisted")
            receipt = _child_receipt(row)
            if receipt.subject != bound:
                raise SchemaMismatch("Child SQL opening did not retain its original binding")
            if session_charge is not None:
                self._record_session_funding(c, request, session_charge, startup=True)
            subject.check()
        try:
            subject.check()
        except BaseException as failure:
            raise SqlChildAckUnavailableAfterCommit(receipt) from failure
        return replace(receipt, subject=bound)

    def record_child_followup(
        self,
        channel: Connection,
        *,
        journal_id: str,
        startup_intent_id: str,
        intent_id: str,
        sequence: int,
        subject: VerifiedChildSqlSubject,
        destination: DatabaseTarget,
        path: str = "native",
        session_charge: "ChildSqlSessionCharge | None" = None,
    ) -> ChildSqlFollowupIntent:
        """Append one live-subject request; never repeat a startup claim or grant SQL."""
        journal, startup, request = map(canonical_id, (journal_id, startup_intent_id, intent_id))
        if type(sequence) is not int or not 1 <= sequence < 2**63:
            raise ValueError("follow-up sequence must be a positive bounded integer")
        if type(subject) is not VerifiedChildSqlSubject:
            raise ValueError("follow-up requires its live authenticated subject")
        subject.check()
        actor_label, digest = self._opening_payload(path, destination)
        target_digest = hashlib.sha256(
            json.dumps(destination.identity, separators=(",", ":")).encode()
        ).hexdigest()
        if (
            subject.binding.store_id != canonical_id(destination.store_id)
            or subject.binding.target_identity_sha256 != target_digest
        ):
            raise SqlIntentUnavailable("follow-up destination differs from its subject")
        if session_charge is not None:
            channel._claim_child_sql_session(session_charge, subject, request)
        with self._transaction(channel, journal, request) as (c, actor):
            ordinal = self._append_position(c, journal, request, actor)
            anchor = self._q(
                c,
                "SELECT * FROM {children} WHERE intent_id=%s AND journal_id=%s",
                (startup, journal),
            ).fetchone()
            if anchor is None:
                raise SqlIntentUnavailable("follow-up startup anchor is unavailable")
            origin = _child_receipt(anchor)
            if (
                origin.parent_binding_sha256,
                origin.parent_process_pid,
                origin.parent_process_started_at,
            ) != (binding_digest(actor), actor.process_pid, actor.process_started_at):
                raise SqlIntentUnavailable("follow-up belongs to another parent")
            if (
                origin.subject,
                origin.subject_sql_application_name,
                origin.boot_id,
                origin.start_time_ticks,
                origin.pid_namespace,
            ) != (
                subject.binding,
                subject.sql_application_name,
                subject.boot_id,
                subject.start_time_ticks,
                subject.pid_namespace,
            ):
                raise SqlIntentUnavailable("follow-up belongs to another live subject")
            if origin.parent_sql_application_name != actor_label:
                raise SqlIntentUnavailable("follow-up actor label differs from its startup")
            last = self._q(
                c,
                """SELECT sequence FROM {followups}
                WHERE startup_intent_id=%s ORDER BY sequence DESC LIMIT 1""",
                (startup,),
            ).fetchone()
            if sequence != (0 if last is None else last[0]) + 1:
                raise SqlIntentConflict("follow-up sequence must be consecutive")
            row = self._q(
                c,
                """INSERT INTO {followups}(intent_id,journal_id,startup_intent_id,
                ordinal,sequence,path,destination_store_id,destination_scope,destination_sha256)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    request,
                    journal,
                    startup,
                    ordinal,
                    sequence,
                    path,
                    canonical_id(destination.store_id),
                    destination.scope,
                    digest,
                ),
            ).fetchone()
            if row is None:
                raise SchemaMismatch("follow-up request was not persisted")
            receipt = _followup_receipt(row, origin)
            expected = ChildSqlFollowupIntent(
                request,
                journal,
                ordinal,
                startup,
                sequence,
                path,
                destination.scope,
                digest,
                binding_digest(subject.binding),
                subject.sql_application_name,
            )
            if receipt != expected:
                raise SchemaMismatch("follow-up request differs from its persisted row")
            if session_charge is not None:
                self._record_session_funding(c, request, session_charge, startup=False)
            subject.check()
        try:
            subject.check()
        except BaseException as failure:
            raise SqlFollowupAckUnavailableAfterCommit(receipt) from failure
        return receipt

    def _record_session_funding(
        self, channel: Connection, request: str, charge: "ChildSqlSessionCharge", *, startup: bool
    ) -> None:
        charge.check()
        row = self._q(
            channel,
            """INSERT INTO {fundings}
            (intent_id,session_id,startup_intent_id,followup_intent_id)
            VALUES(%s,%s,%s,%s) RETURNING intent_id,session_id""",
            (
                request,
                charge.session_id,
                request if startup else None,
                None if startup else request,
            ),
        ).fetchone()
        if row is None or (str(row[0]), str(row[1])) != (request, charge.session_id):
            raise SchemaMismatch("session funding association was not persisted")
        charge.check()

    def read_child_followup(
        self,
        channel: Connection,
        *,
        journal_id: str,
        intent_id: str,
    ) -> ChildSqlFollowupIntent | None:
        journal, request = canonical_id(journal_id), canonical_id(intent_id)
        with self._transaction(channel, journal) as (c, actor):
            self._quota(c, journal, actor)
            row = self._q(
                c,
                "SELECT * FROM {followups} WHERE journal_id=%s AND intent_id=%s",
                (journal, request),
            ).fetchone()
            if row is None:
                return None
            anchor = self._q(
                c,
                "SELECT * FROM {children} WHERE journal_id=%s AND intent_id=%s",
                (journal, str(row["startup_intent_id"])),
            ).fetchone()
            if anchor is None:
                raise SchemaMismatch("follow-up startup anchor is unavailable")
            origin = _child_receipt(anchor)
            if (
                origin.parent_binding_sha256,
                origin.parent_process_pid,
                origin.parent_process_started_at,
            ) != (binding_digest(actor), actor.process_pid, actor.process_started_at) or str(
                row["destination_store_id"]
            ) != origin.subject.store_id:
                raise SchemaMismatch("follow-up ownership differs from its startup")
            return _followup_receipt(row, origin)

    def read_child_opening(
        self, channel: Connection, *, journal_id: str, intent_id: str
    ) -> ChildSqlOpeningIntent | None:
        journal, request = canonical_id(journal_id), canonical_id(intent_id)
        with self._transaction(channel, journal) as (c, binding):
            self._quota(c, journal, binding)
            row = self._q(
                c,
                "SELECT * FROM {children} WHERE journal_id=%s AND intent_id=%s",
                (journal, request),
            ).fetchone()
            if row is None:
                return None
            if (
                row["parent_binding_sha256"],
                row["parent_process_pid"],
                row["parent_process_started_at"],
            ) != (binding_digest(binding), binding.process_pid, binding.process_started_at):
                raise SchemaMismatch("Child SQL opening has another original parent")
            return _child_receipt(row)

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
