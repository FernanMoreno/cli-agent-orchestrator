"""Resolve local context once, redact, freeze durably, then return exact stored bytes.

Snapshots are contextual evidence, never automatically approved instructions.
The opaque contract_id is the same identity used by work_items, not a newly
invented contract registry. Call freeze BEFORE admit_work; admission binds its
returned id immutably. Child callers reuse the parent's existing snapshot.
"""

import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass
from uuid import uuid4

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security.auth import Principal
from cli_agent_orchestrator.services.knowledge_policy import KnowledgeAccessDenied, KnowledgePolicy
from cli_agent_orchestrator.services.secret_gate import redact_secrets, scan_for_secrets


class SnapshotConflict(ValueError):
    """An existing snapshot binding cannot be repurposed."""


class SnapshotUnavailable(RuntimeError):
    """Required snapshot cannot be delivered; callers must not resolve live instead."""


def _identity(value):
    if (
        type(value) is not str
        or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value)
        or scan_for_secrets(value)
    ):
        raise SnapshotConflict("bounded nonsecret snapshot identity required")
    return value


@dataclass(frozen=True, order=True)
class SourceRevision:
    record_id: str
    revision: int

    def __post_init__(self):
        _identity(self.record_id)
        if type(self.revision) is not int or not 0 < self.revision <= 2**63 - 1:
            raise SnapshotConflict("positive source revision required")


@dataclass(frozen=True)
class ResolvedSnapshot:
    """Server resolver output, not a caller-supplied authority DTO."""

    content: str
    revision_refs: tuple[SourceRevision, ...] = ()

    def __post_init__(self):
        if (
            type(self.content) is not str
            or type(self.revision_refs) is not tuple
            or len(self.revision_refs) > 64
            or any(type(ref) is not SourceRevision for ref in self.revision_refs)
        ):
            raise SnapshotConflict("bounded typed context and source references required")
        if len(set(self.revision_refs)) != len(self.revision_refs):
            raise SnapshotConflict("duplicate source revisions")


@dataclass(frozen=True)
class Snapshot:
    id: str
    schema_version: int
    job_id: str
    contract_id: str
    scope: str
    scope_id: str
    source_hash: str
    delivered_hash: str
    content: bytes
    redacted: bool
    truncated: bool
    revision_refs: tuple[SourceRevision, ...]


@dataclass(frozen=True)
class LegacySnapshotAbsence:
    """No evidence was frozen on the legacy parent; never a live-resolution request."""

    parent_work_id: str
    reason: str = "legacy_parent_has_no_snapshot"


class DelegationSnapshots:
    """Bounded SQLite snapshots guarded by the concrete server KnowledgePolicy.

    Resolver(connection, principal) MUST be synchronous, local and bounded; it
    must not open another transaction, access a vault/network/CLI, commit, or send
    bytes. BEGIN IMMEDIATE serializes sibling resolution through the unique
    binding, including an empty resolution. A crashed/rolled-back transaction may
    resolve again; no content was delivered. No process-global cache is trusted.
    """

    def __init__(
        self,
        repository: WorkRepository,
        *,
        policy: KnowledgePolicy,
        max_content_bytes=65536,
        max_input_bytes=1048576,
    ):
        if type(policy) is not KnowledgePolicy:
            raise KnowledgeAccessDenied("concrete server knowledge policy required")
        if (
            type(max_content_bytes) is not int
            or not 0 < max_content_bytes <= 65536
            or type(max_input_bytes) is not int
            or not 0 < max_input_bytes <= 1048576
        ):
            raise SnapshotConflict("invalid snapshot byte bounds")
        self.repository = repository
        self.policy = policy
        self.max_content_bytes = max_content_bytes
        self.max_input_bytes = max_input_bytes

    def _authorize(self, connection, principal, job_id, scope, scope_id):
        if job_id != self.policy.job_id:
            raise KnowledgeAccessDenied("snapshot belongs to another job")
        self.policy(connection, principal, "read", scope, scope_id)

    @staticmethod
    def _check_sources(connection, scope, scope_id, refs):
        for ref in refs:
            row = connection.execute(
                "SELECT k.scope,k.scope_id,t.record_id AS withdrawn FROM work_knowledge_revisions r JOIN work_knowledge_records k ON k.id=r.record_id LEFT JOIN work_knowledge_tombstones t ON t.record_id=r.record_id AND t.revision=r.revision WHERE r.record_id=? AND r.revision=?",
                (ref.record_id, ref.revision),
            ).fetchone()
            if row is None or row["withdrawn"] is not None:
                raise SnapshotUnavailable("snapshot source missing or withdrawn")
            if (row["scope"], row["scope_id"]) != (scope, scope_id):
                raise KnowledgeAccessDenied("source revision exceeds snapshot scope")

    def _sources(self, connection, principal, scope, scope_id, refs):
        self._check_sources(connection, scope, scope_id, refs)
        for _ in refs:
            self.policy(connection, principal, "read", scope, scope_id)

    def _load(self, connection, principal, row) -> Snapshot:
        if row is None:
            raise SnapshotUnavailable("required snapshot missing")
        self._authorize(connection, principal, row["job_id"], row["scope"], row["scope_id"])
        return self._load_authorized(connection, row)

    @staticmethod
    def _load_authorized(connection: sqlite3.Connection, row) -> Snapshot:
        """Validate persisted evidence only; this helper grants NO authority.

        Trusted durable-order consumers must FIRST validate their current grant
        chain, knowledge.read capability and this snapshot's job/scope, in the
        SAME transaction. Public snapshot access continues through _load and
        KnowledgePolicy; do not expose this helper as an unauthenticated API.
        """
        if not connection.in_transaction:
            raise SnapshotUnavailable("snapshot validation requires an active transaction")
        if row is None:
            raise SnapshotUnavailable("required snapshot missing")
        refs = tuple(
            SourceRevision(item[0], item[1])
            for item in connection.execute(
                "SELECT record_id,revision FROM work_snapshot_sources WHERE snapshot_id=? ORDER BY record_id,revision",
                (row["id"],),
            )
        )
        DelegationSnapshots._check_sources(connection, row["scope"], row["scope_id"], refs)
        content = row["content"]
        if (
            row["schema_version"] != 1
            or type(content) is not bytes
            or len(content) > 65536
            or hashlib.sha256(content).hexdigest() != row["delivered_hash"]
        ):
            raise SnapshotUnavailable("snapshot integrity or version check failed")
        return Snapshot(
            row["id"],
            1,
            row["job_id"],
            row["contract_id"],
            row["scope"],
            row["scope_id"],
            row["source_hash"],
            row["delivered_hash"],
            content,
            bool(row["redacted"]),
            bool(row["truncated"]),
            refs,
        )

    def freeze(
        self,
        *,
        principal: Principal,
        job_id,
        contract_id,
        binding_key,
        request_hash,
        scope,
        scope_id,
        resolver,
    ):
        for value in (job_id, contract_id, binding_key, scope_id):
            _identity(value)
        if type(request_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}", request_hash):
            raise SnapshotConflict("canonical request hash required")
        try:
            with self.repository.transaction() as connection:
                self.repository._verify(connection)
                self._authorize(connection, principal, job_id, scope, scope_id)
                existing = connection.execute(
                    "SELECT * FROM work_delegation_snapshots WHERE job_id=? AND contract_id=? AND binding_key=?",
                    (job_id, contract_id, binding_key),
                ).fetchone()
                if existing:
                    if (existing["request_hash"], existing["scope"], existing["scope_id"]) != (
                        request_hash,
                        scope,
                        scope_id,
                    ):
                        raise SnapshotConflict(
                            "snapshot binding is frozen with different arguments"
                        )
                    result = self._load(connection, principal, existing)
                else:
                    try:
                        resolved = resolver(connection, principal)
                    except Exception:
                        # Resolver diagnostics can contain raw source text/secrets.
                        raise SnapshotUnavailable("local snapshot resolution failed") from None
                    if type(resolved) is not ResolvedSnapshot:
                        raise SnapshotUnavailable("typed server resolution required")
                    raw = resolved.content.encode("utf-8")
                    if len(raw) > self.max_input_bytes:
                        raise SnapshotUnavailable("resolved snapshot exceeds input bound")
                    self._authorize(connection, principal, job_id, scope, scope_id)
                    self._sources(connection, principal, scope, scope_id, resolved.revision_refs)
                    cleaned, _ = redact_secrets(resolved.content)
                    full = cleaned.encode("utf-8")
                    content = (
                        full[: self.max_content_bytes]
                        .decode("utf-8", errors="ignore")
                        .encode("utf-8")
                    )
                    identifier = uuid4().hex
                    connection.execute(
                        "INSERT INTO work_delegation_snapshots VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            identifier,
                            job_id,
                            contract_id,
                            binding_key,
                            request_hash,
                            scope,
                            scope_id,
                            principal.id,
                            hashlib.sha256(raw).hexdigest(),
                            hashlib.sha256(content).hexdigest(),
                            content,
                            int(cleaned != resolved.content),
                            int(content != full),
                            time.time(),
                        ),
                    )
                    connection.executemany(
                        "INSERT INTO work_snapshot_sources VALUES (?,?,?)",
                        [
                            (identifier, ref.record_id, ref.revision)
                            for ref in resolved.revision_refs
                        ],
                    )
                    connection.execute(
                        "UPDATE work_jobs SET revision=revision+1 WHERE id=?", (job_id,)
                    )
                    self.repository._append_event(
                        connection,
                        job_id=job_id,
                        actor_id=principal.id,
                        event_type="snapshot.frozen",
                        metadata={
                            "snapshot_id": identifier,
                            "source_hash": hashlib.sha256(raw).hexdigest(),
                            "delivered_hash": hashlib.sha256(content).hexdigest(),
                        },
                    )
                    result = self._load(
                        connection,
                        principal,
                        connection.execute(
                            "SELECT * FROM work_delegation_snapshots WHERE id=?", (identifier,)
                        ).fetchone(),
                    )
            # Context manager COMMIT has completed before content can reach caller.
            return result
        except sqlite3.Error:
            raise SnapshotUnavailable("required snapshot could not be persisted") from None

    def read(self, principal: Principal, snapshot_id: str) -> Snapshot:
        _identity(snapshot_id)
        with self.repository.read_snapshot() as connection:
            return self._load(
                connection,
                principal,
                connection.execute(
                    "SELECT * FROM work_delegation_snapshots WHERE id=?", (snapshot_id,)
                ).fetchone(),
            )

    @staticmethod
    def _validate_ancestry(connection, snapshot: Snapshot, work_item_id: str) -> None:
        """Prove inherited context reaches its origin through durable work rows.

        This is shared structural validation, not authority. Callers must verify
        the principal/order and load authorized snapshot evidence in this same
        transaction before using it. Every hop must retain the same job/snapshot.
        """
        if not connection.in_transaction:
            raise SnapshotConflict("snapshot ancestry requires an active transaction")
        current_id = work_item_id
        visited = set()
        while current_id is not None:
            if current_id in visited:
                raise SnapshotConflict("snapshot ancestry contains a cycle")
            visited.add(current_id)
            current = connection.execute(
                "SELECT job_id,contract_id,snapshot_id,parent_work_item_id FROM work_items WHERE id=?",
                (current_id,),
            ).fetchone()
            if (
                current is None
                or current["job_id"] != snapshot.job_id
                or current["snapshot_id"] != snapshot.id
            ):
                raise SnapshotConflict("snapshot inheritance requires its durable parent chain")
            if current["contract_id"] == snapshot.contract_id:
                return
            current_id = current["parent_work_item_id"]
        raise SnapshotConflict("snapshot origin is not an ancestor of this work")

    def child_snapshot(
        self, principal: Principal, parent_work_id: str
    ) -> Snapshot | LegacySnapshotAbsence:
        _identity(parent_work_id)
        with self.repository.read_snapshot() as connection:
            parent = connection.execute(
                "SELECT job_id,contract_id,snapshot_id FROM work_items WHERE id=?",
                (parent_work_id,),
            ).fetchone()
            if parent is None:
                raise SnapshotUnavailable("parent work not found")
            self._authorize(connection, principal, parent["job_id"], "job", parent["job_id"])
            if parent["snapshot_id"] is None:
                return LegacySnapshotAbsence(parent_work_id)
            result = self._load(
                connection,
                principal,
                connection.execute(
                    "SELECT * FROM work_delegation_snapshots WHERE id=?", (parent["snapshot_id"],)
                ).fetchone(),
            )
            self._validate_ancestry(connection, result, parent_work_id)
            return result
