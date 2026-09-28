"""Reviewed knowledge history on the work store, without legacy memory promotion.

Authorization is a required server-owned, transaction-local policy dependency.
Content is redacted before truncation; tombstones hide rather than physically erase
immutable history. Consumers must explicitly use ``instructions`` for authority.
"""

import hashlib
import json
import math
import re
import secrets
import sqlite3
import time
from typing import Callable

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.memory import (
    KnowledgeDecision,
    KnowledgeRevision,
    KnowledgeScope,
)
from cli_agent_orchestrator.security.auth import Principal
from cli_agent_orchestrator.services.secret_gate import scan_for_secrets
from cli_agent_orchestrator.services.knowledge_policy import (
    KnowledgePolicy,
    redact_knowledge_content,
)
from cli_agent_orchestrator.services.work_authority import WorkAuthority


class KnowledgeDenied(PermissionError):
    """No verified server policy authorizes this access."""


class KnowledgeConflict(ValueError):
    """The record changed or the requested transition is not current."""


class KnowledgeCursorExpired(RuntimeError):
    """A durable recovery cursor no longer names a safe continuation."""


class KnowledgeCursorInvalid(ValueError):
    """The opaque recovery cursor does not have a supported wire format."""


KnowledgeAuthorizer = Callable[[sqlite3.Connection, Principal, str, KnowledgeScope, str], None]


def _enum(enum_type, value):
    # Enum's default ValueError echoes untrusted input, including possible secrets.
    try:
        return enum_type(value)
    except (ValueError, TypeError):
        raise ValueError("invalid knowledge enum value") from None


def _identifier(value: str) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value)
        or scan_for_secrets(value)
    ):
        raise ValueError("invalid knowledge reference")
    return value


def _references(values) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or len(values) > 64:
        raise ValueError("bounded evidence references required")
    result = tuple(_identifier(value) for value in values)
    if len(set(result)) != len(result):
        raise ValueError("duplicate evidence references")
    return result


class KnowledgeRevisions:
    def __init__(
        self,
        repository: WorkRepository,
        *,
        authorize: KnowledgeAuthorizer | None = None,
        max_content_bytes: int = 16384,
        max_input_bytes: int = 1048576,
        cursor_ttl_seconds: int = 300,
        max_cursor_limit: int = 100,
        clock: Callable[[], float] = time.time,
    ):
        if any(
            type(v) is not int or not 0 < v <= 1048576 for v in (max_content_bytes, max_input_bytes)
        ):
            raise ValueError("invalid knowledge byte bounds")
        if type(cursor_ttl_seconds) is not int or not 1 <= cursor_ttl_seconds <= 86400:
            raise ValueError("invalid knowledge cursor ttl")
        if type(max_cursor_limit) is not int or not 1 <= max_cursor_limit <= 100:
            raise ValueError("invalid knowledge cursor limit")
        if not callable(clock):
            raise ValueError("knowledge clock must be callable")
        self.repository = repository
        self.authorize = authorize
        self.max_content_bytes = max_content_bytes
        self.max_input_bytes = max_input_bytes
        self.cursor_ttl_seconds = cursor_ttl_seconds
        self.max_cursor_limit = max_cursor_limit
        self.clock = clock

    def _authorize(
        self, connection, principal, action, scope, scope_id, *, write=False, target=None
    ):
        if (
            not isinstance(principal, Principal)
            or not getattr(principal, "id", None)
            or not callable(self.authorize)
        ):
            raise KnowledgeDenied("verified knowledge policy required")
        if self.authorize(connection, principal, action, scope, scope_id) is not None:
            raise KnowledgeDenied("knowledge policy must explicitly authorize")
        if write:
            WorkAuthority._register(connection, principal)
        if isinstance(self.authorize, KnowledgePolicy):
            self.authorize.audit(
                connection, principal, action, [scope, scope_id, target], "allowed"
            )

    @staticmethod
    def _head(connection, record_id):
        row = connection.execute(
            "SELECT * FROM work_knowledge_records WHERE id=?", (_identifier(record_id),)
        ).fetchone()
        if row is None:
            raise KeyError("knowledge record not found")
        return row

    @staticmethod
    def _cas(connection, head, expected_version):
        if type(expected_version) is not int or head["version"] != expected_version:
            raise KnowledgeConflict("knowledge version changed")
        updated = connection.execute(
            "UPDATE work_knowledge_records SET version=version+1 WHERE id=? AND version=?",
            (head["id"], expected_version),
        )
        if updated.rowcount != 1:
            raise KnowledgeConflict("knowledge version changed")
        return expected_version + 1

    @staticmethod
    def _artifact(connection, reference, scope, scope_id):
        row = connection.execute(
            "SELECT r.id,a.id AS attempt_id,w.id AS work_item_id,j.id AS job_id,j.project_id "
            "FROM work_results r JOIN work_attempts a ON a.id=r.attempt_id "
            "JOIN work_items w ON w.id=a.work_item_id JOIN work_jobs j ON j.id=w.job_id WHERE r.id=?",
            (_identifier(reference),),
        ).fetchone()
        if (
            row is None
            or row["job_id" if scope == KnowledgeScope.JOB else "project_id"] != scope_id
        ):
            raise ValueError("evidence reference does not belong to knowledge scope")
        return row

    def _provenance(
        self,
        connection,
        scope,
        scope_id,
        work_item_id,
        attempt_id,
        source_artifact_id,
        evidence_refs,
    ):
        if source_artifact_id is not None:
            source = self._artifact(connection, source_artifact_id, scope, scope_id)
            if (work_item_id is not None and work_item_id != source["work_item_id"]) or (
                attempt_id is not None and attempt_id != source["attempt_id"]
            ):
                raise ValueError("inconsistent knowledge provenance")
            work_item_id, attempt_id = source["work_item_id"], source["attempt_id"]
        if attempt_id is not None:
            attempt = connection.execute(
                "SELECT work_item_id FROM work_attempts WHERE id=?", (_identifier(attempt_id),)
            ).fetchone()
            if attempt is None or (work_item_id is not None and work_item_id != attempt[0]):
                raise ValueError("inconsistent knowledge attempt")
            work_item_id = attempt[0]
        if work_item_id is not None:
            work = connection.execute(
                "SELECT j.id,j.project_id FROM work_items w JOIN work_jobs j ON j.id=w.job_id WHERE w.id=?",
                (_identifier(work_item_id),),
            ).fetchone()
            if (
                work is None
                or work["id" if scope == KnowledgeScope.JOB else "project_id"] != scope_id
            ):
                raise ValueError("work does not belong to knowledge scope")
        for reference in evidence_refs:
            self._artifact(connection, reference, scope, scope_id)
        return work_item_id, attempt_id

    @staticmethod
    def _event(connection, record_id, version, revision, operation, principal):
        connection.execute(
            "INSERT INTO work_knowledge_events VALUES (?,?,?,?,?,?)",
            (record_id, version, revision, operation, principal.id, time.time()),
        )

    @staticmethod
    def _decision(connection, record_id, revision, version, decision, principal, refs=()):
        connection.execute(
            "INSERT INTO work_knowledge_decisions VALUES (?,?,?,?,?,?)",
            (record_id, revision, version, decision, principal.id, json.dumps(refs)),
        )

    @staticmethod
    def _read(connection, head, revision):
        if type(revision) is not int or revision <= 0:
            raise ValueError("positive knowledge revision required")
        row = connection.execute(
            "SELECT * FROM work_knowledge_revisions WHERE record_id=? AND revision=?",
            (head["id"], revision),
        ).fetchone()
        if row is None:
            raise KeyError("knowledge revision not found")
        decision = connection.execute(
            "SELECT decision FROM work_knowledge_decisions WHERE record_id=? AND revision=? ORDER BY event_sequence DESC LIMIT 1",
            (head["id"], revision),
        ).fetchone()
        tombstone = (
            connection.execute(
                "SELECT 1 FROM work_knowledge_tombstones WHERE record_id=? AND revision=?",
                (head["id"], revision),
            ).fetchone()
            is not None
        )
        values = dict(row)
        values.update(
            record_version=head["version"],
            scope=head["scope"],
            scope_id=head["scope_id"],
            evidence_refs=tuple(json.loads(row["evidence_refs"])),
            decision=decision[0],
            tombstone=tombstone,
        )
        if tombstone:
            values["content"] = None
        return KnowledgeRevision(**values)

    def propose_revision(
        self,
        principal,
        *,
        scope,
        scope_id,
        record_id,
        expected_version,
        content,
        work_item_id=None,
        attempt_id=None,
        source_artifact_id=None,
        evidence_refs=(),
        confidence,
        fresh_until,
        legacy=False,
    ):
        scope = _enum(KnowledgeScope, scope)
        scope_id, record_id = _identifier(scope_id), _identifier(record_id)
        evidence_refs = _references(evidence_refs)
        if type(expected_version) is not int or expected_version < 0 or type(legacy) is not bool:
            raise ValueError("invalid proposal metadata")
        for value, lower, upper in ((confidence, 0, 1), (fresh_until, 0, 1e15)):
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not lower <= value <= upper
            ):
                raise ValueError("finite confidence and freshness required")
        if fresh_until <= 0 or fresh_until >= 1e15:
            raise ValueError("invalid freshness timestamp")
        if not isinstance(content, str):
            raise ValueError("knowledge content must be text")
        raw = content.encode("utf-8")
        if len(raw) > self.max_input_bytes:
            raise ValueError("knowledge input exceeds byte limit")
        redacted, patterns = redact_knowledge_content(content)
        sanitized = redacted.encode("utf-8")
        delivered = sanitized[: self.max_content_bytes].decode("utf-8", errors="ignore")
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self._authorize(
                connection,
                principal,
                "propose",
                scope,
                scope_id,
                write=True,
                target=[record_id, expected_version],
            )
            work_item_id, attempt_id = self._provenance(
                connection,
                scope,
                scope_id,
                work_item_id,
                attempt_id,
                source_artifact_id,
                evidence_refs,
            )
            head = connection.execute(
                "SELECT * FROM work_knowledge_records WHERE id=?", (record_id,)
            ).fetchone()
            if head is None:
                if expected_version != 0:
                    raise KnowledgeConflict("knowledge record does not exist")
                revision, version, supersedes = 1, 1, None
                connection.execute(
                    "INSERT INTO work_knowledge_records VALUES (?,?,?,?,?)",
                    (record_id, scope.value, scope_id, revision, version),
                )
            else:
                if (head["scope"], head["scope_id"]) != (scope.value, scope_id):
                    raise KnowledgeConflict("knowledge scope is immutable")
                version = self._cas(connection, head, expected_version)
                supersedes = head["head_revision"]
                revision = supersedes + 1
                connection.execute(
                    "UPDATE work_knowledge_records SET head_revision=? WHERE id=?",
                    (revision, record_id),
                )
            connection.execute(
                "INSERT INTO work_knowledge_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record_id,
                    revision,
                    principal.id,
                    work_item_id,
                    attempt_id,
                    source_artifact_id,
                    hashlib.sha256(raw).hexdigest(),
                    hashlib.sha256(delivered.encode()).hexdigest(),
                    json.dumps(evidence_refs),
                    confidence,
                    fresh_until,
                    delivered,
                    int(bool(patterns)),
                    int(len(sanitized) > self.max_content_bytes),
                    int(legacy),
                    supersedes,
                    time.time(),
                ),
            )
            self._event(connection, record_id, version, revision, "proposed", principal)
            self._decision(connection, record_id, revision, version, "proposed", principal)
            if supersedes is not None:
                self._decision(connection, record_id, supersedes, version, "superseded", principal)
            return self._read(connection, self._head(connection, record_id), revision)

    def review_revision(
        self, principal, record_id, revision, decision, *, expected_version, examined_refs=()
    ):
        decision = _enum(KnowledgeDecision, decision)
        if decision not in (
            KnowledgeDecision.VERIFIED,
            KnowledgeDecision.APPROVED,
            KnowledgeDecision.REJECTED,
        ):
            raise ValueError("review decision must be verified, approved or rejected")
        examined_refs = _references(examined_refs)
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            head = self._head(connection, record_id)
            scope = KnowledgeScope(head["scope"])
            self._authorize(
                connection,
                principal,
                "review",
                scope,
                head["scope_id"],
                write=True,
                target=[record_id, revision],
            )
            current = self._read(connection, head, revision)
            if (
                revision != head["head_revision"]
                or current.tombstone
                or current.decision in (KnowledgeDecision.REJECTED, KnowledgeDecision.SUPERSEDED)
            ):
                raise KnowledgeConflict("knowledge revision is not reviewable")
            if (
                current.decision == KnowledgeDecision.APPROVED
                and decision != KnowledgeDecision.REJECTED
            ):
                raise KnowledgeConflict("approved knowledge can only be rejected")
            if decision != KnowledgeDecision.REJECTED and not examined_refs:
                raise ValueError("examined evidence is required")
            if not set(examined_refs).issubset(current.evidence_refs):
                raise ValueError("examined evidence is not part of this revision")
            for reference in examined_refs:
                self._artifact(connection, reference, scope, head["scope_id"])
            version = self._cas(connection, head, expected_version)
            self._event(connection, record_id, version, revision, "reviewed", principal)
            self._decision(
                connection, record_id, revision, version, decision.value, principal, examined_refs
            )
            return self._read(connection, self._head(connection, record_id), revision)

    def tombstone_revision(self, principal, record_id, revision, *, expected_version):
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            head = self._head(connection, record_id)
            self._authorize(
                connection,
                principal,
                "tombstone",
                KnowledgeScope(head["scope"]),
                head["scope_id"],
                write=True,
                target=[record_id, revision],
            )
            current = self._read(connection, head, revision)
            if current.tombstone:
                raise KnowledgeConflict("knowledge revision already tombstoned")
            version = self._cas(connection, head, expected_version)
            self._event(connection, record_id, version, revision, "tombstoned", principal)
            connection.execute(
                "INSERT INTO work_knowledge_tombstones VALUES (?,?,?,?)",
                (record_id, revision, version, principal.id),
            )
            return self._read(connection, self._head(connection, record_id), revision)

    def read_revision(self, principal, record_id, revision=None):
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            head = self._head(connection, record_id)
            self._authorize(
                connection,
                principal,
                "read",
                KnowledgeScope(head["scope"]),
                head["scope_id"],
                target=[record_id, head["head_revision"] if revision is None else revision],
            )
            return self._read(
                connection, head, head["head_revision"] if revision is None else revision
            )

    @staticmethod
    def _checkpoint(connection, scope, scope_id):
        """Digest every current record head; never infer a scope cut from one row."""
        heads = connection.execute(
            "SELECT id,head_revision,version FROM work_knowledge_records "
            "WHERE scope=? AND scope_id=? ORDER BY id COLLATE BINARY",
            (scope.value, scope_id),
        ).fetchall()
        payload = [
            scope.value,
            scope_id,
            [[row["id"], row["head_revision"], row["version"]] for row in heads],
        ]
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()

    @staticmethod
    def _cursor_hash(cursor):
        return hashlib.sha256(cursor.encode("ascii")).hexdigest()

    @staticmethod
    def _parse_cursor(cursor):
        if not isinstance(cursor, str):
            raise KnowledgeCursorInvalid("invalid knowledge cursor")
        parsed = re.fullmatch(r"kcr([1-9][0-9]*)_([A-Za-z0-9_-]{43})", cursor)
        if parsed is None or int(parsed.group(1)) != 1:
            raise KnowledgeCursorInvalid("invalid knowledge cursor")
        return cursor

    @staticmethod
    def _recovery_rows(connection, scope, scope_id, after_record_id, after_revision, limit):
        query = (
            "SELECT r.record_id,r.revision FROM work_knowledge_revisions r "
            "JOIN work_knowledge_records h ON h.id=r.record_id "
            "WHERE h.scope=? AND h.scope_id=?"
        )
        parameters = [scope.value, scope_id]
        if after_record_id is not None:
            query += (
                " AND (r.record_id COLLATE BINARY>? COLLATE BINARY "
                "OR (r.record_id=? AND r.revision>?))"
            )
            parameters.extend((after_record_id, after_record_id, after_revision))
        query += " ORDER BY r.record_id COLLATE BINARY,r.revision ASC LIMIT ?"
        parameters.append(limit + 1)
        return connection.execute(query, parameters).fetchall()

    def _load_cursor(
        self,
        connection,
        *,
        cursor,
        principal,
        scope,
        scope_id,
        job_id,
        grant_id,
        grant_revision,
        limit,
        now,
    ):
        cursor = self._parse_cursor(cursor)
        row = connection.execute(
            "SELECT * FROM work_knowledge_cursors WHERE token_hash=?",
            (self._cursor_hash(cursor),),
        ).fetchone()
        if row is None:
            raise KnowledgeCursorExpired("knowledge cursor is unavailable")
        if row["schema_version"] != 1:
            raise KnowledgeCursorInvalid("invalid knowledge cursor")
        if now >= row["expires_at"]:
            raise KnowledgeCursorExpired("knowledge cursor is expired")
        if (
            row["principal_id"] != principal.id
            or row["scope"] != scope.value
            or row["scope_id"] != scope_id
            or row["job_id"] != job_id
            or row["grant_id"] != grant_id
            or row["grant_revision"] != grant_revision
            or row["limit_value"] != limit
        ):
            raise KnowledgeDenied("knowledge cursor context is not authorized")
        return cursor, row

    def recovery_page(
        self,
        principal,
        *,
        scope,
        scope_id,
        job_id,
        grant_id,
        grant_revision,
        limit,
        cursor=None,
    ):
        """Return a bounded, authorized recovery page from immutable history.

        A page has no multi-node snapshot promise: any later mutation of the
        selected scope makes its persisted checkpoint expire before continuation.
        """
        scope, scope_id = _enum(KnowledgeScope, scope), _identifier(scope_id)
        if (
            type(limit) is not int
            or isinstance(limit, bool)
            or not 1 <= limit <= self.max_cursor_limit
        ):
            raise ValueError("invalid knowledge recovery limit")
        for value, label in ((job_id, "job_id"), (grant_id, "grant_id")):
            _identifier(value)
        if type(grant_revision) is not int or grant_revision <= 0:
            raise ValueError("invalid knowledge grant revision")
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self._authorize(
                connection,
                principal,
                "read",
                scope,
                scope_id,
                target=["recovery", limit],
            )
            checkpoint = self._checkpoint(connection, scope, scope_id)
            now = self.clock()
            if (
                type(now) not in (int, float)
                or not math.isfinite(now)
                or not 0 < now < 1e15
            ):
                raise ValueError("invalid knowledge clock")
            if cursor is None:
                cursor_row = None
                expires_at = now + self.cursor_ttl_seconds
                after_record_id, after_revision = None, None
            else:
                cursor, cursor_row = self._load_cursor(
                    connection,
                    cursor=cursor,
                    principal=principal,
                    scope=scope,
                    scope_id=scope_id,
                    job_id=job_id,
                    grant_id=grant_id,
                    grant_revision=grant_revision,
                    limit=limit,
                    now=now,
                )
                if cursor_row["checkpoint"] != checkpoint:
                    raise KnowledgeCursorExpired("knowledge cursor checkpoint changed")
                expires_at = cursor_row["expires_at"]
                after_record_id, after_revision = (
                    cursor_row["last_record_id"],
                    cursor_row["last_revision"],
                )
            rows = self._recovery_rows(
                connection, scope, scope_id, after_record_id, after_revision, limit
            )
            page_rows, has_more = rows[:limit], len(rows) > limit
            revisions = tuple(
                self._read(connection, self._head(connection, row["record_id"]), row["revision"])
                for row in page_rows
            )
            if page_rows:
                last_record_id, last_revision = page_rows[-1]["record_id"], page_rows[-1]["revision"]
            else:
                last_record_id, last_revision = after_record_id, after_revision
            next_cursor = None
            if cursor_row is None and has_more:
                cursor = "kcr1_" + secrets.token_urlsafe(32)
                connection.execute(
                    "INSERT INTO work_knowledge_cursors "
                    "(token_hash,schema_version,principal_id,scope,scope_id,job_id,grant_id,grant_revision,"
                    "checkpoint,last_record_id,last_revision,limit_value,expires_at,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        self._cursor_hash(cursor),
                        1,
                        principal.id,
                        scope.value,
                        scope_id,
                        job_id,
                        grant_id,
                        grant_revision,
                        checkpoint,
                        last_record_id,
                        last_revision,
                        limit,
                        expires_at,
                        now,
                    ),
                )
                next_cursor = cursor
            elif cursor_row is not None:
                connection.execute(
                    "UPDATE work_knowledge_cursors SET last_record_id=?,last_revision=? WHERE token_hash=?",
                    (last_record_id, last_revision, self._cursor_hash(cursor)),
                )
                if has_more:
                    next_cursor = cursor
            return {
                "schema_version": 1,
                "revisions": revisions,
                "checkpoint": checkpoint,
                "next_cursor": next_cursor,
                "expires_at": expires_at,
                "retention_policy": "immutable_history_tombstones_retain_metadata_cursor_ttl_only",
            }

    def instructions(self, principal, *, scope, scope_id) -> tuple[KnowledgeRevision, ...]:
        scope, scope_id = _enum(KnowledgeScope, scope), _identifier(scope_id)
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self._authorize(connection, principal, "instructions", scope, scope_id)
            now = time.time()
            heads = connection.execute(
                "SELECT * FROM work_knowledge_records WHERE scope=? AND scope_id=? ORDER BY id",
                (scope.value, scope_id),
            ).fetchall()
            revisions = (self._read(connection, head, head["head_revision"]) for head in heads)
            return tuple(
                row
                for row in revisions
                if row.decision == KnowledgeDecision.APPROVED
                and not row.tombstone
                and row.fresh_until > now
            )
