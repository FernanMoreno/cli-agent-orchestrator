"""Transaction-local knowledge authorization using the existing durable grant chain.

This policy is a trusted server dependency, never a request DTO or a replacement
for transport authentication. Legacy file memory is restricted to the local operator.
"""

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security.auth import SCOPE_ADMIN, SCOPE_READ, SCOPE_WRITE, Principal
from cli_agent_orchestrator.services.secret_gate import redact_secrets
from cli_agent_orchestrator.services.work_authority import (
    AuthorityDenied,
    GrantConflict,
    WorkAuthority,
)


class KnowledgeAccessDenied(PermissionError):
    """The current principal, grant or scope does not authorize this operation."""


def redact_knowledge_content(content: object) -> tuple[str, list[str]]:
    """Redact the complete input; consumers apply byte/character budgets afterwards."""
    if not isinstance(content, str):
        raise ValueError("knowledge content must be text")
    return redact_secrets(content)


def redact_knowledge_data(value):
    """Apply the same text policy to JSON metadata without mutating its source."""
    if isinstance(value, str):
        return redact_knowledge_content(value)[0]
    if isinstance(value, list):
        return [redact_knowledge_data(item) for item in value]
    if isinstance(value, dict):
        return {
            redact_knowledge_data(key): redact_knowledge_data(item) for key, item in value.items()
        }
    return value


def require_legacy_operator(principal=None):
    """Legacy files are private local compatibility, never a shared grant bypass."""
    if principal is None:
        from cli_agent_orchestrator.security.auth import local_operator_principal

        principal = local_operator_principal()
    if not isinstance(principal, Principal) or principal.kind != "local_operator":
        raise KnowledgeAccessDenied(
            "legacy memory requires a verified local operator; use v1 knowledge"
        )
    return principal


@dataclass(frozen=True)
class KnowledgePolicy:
    """Bind policy to an authenticated job/grant context and the same SQLite store.

    Tools are explicit grant capabilities: knowledge.read (also instructions),
    knowledge.propose, knowledge.review and knowledge.tombstone. Scope and tool
    permission must both hold; admin never bypasses a revoked or mismatched grant.
    """

    repository: WorkRepository
    job_id: str
    grant_id: str
    expected_grant_revision: int

    def audit(self, connection, principal, action, target, outcome):
        """Commit with the caller's operation; never persist request/content bytes."""
        if not connection.in_transaction or not isinstance(principal, Principal):
            raise KnowledgeAccessDenied("verified transactional audit required")

        def digest(value):
            return hashlib.sha256(
                json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()

        connection.execute(
            "INSERT INTO work_knowledge_access_audit "
            "(actor_id,authority_hash,action,target_hash,outcome,occurred_at) VALUES (?,?,?,?,?,?)",
            (
                principal.id,
                digest([self.job_id, self.grant_id, self.expected_grant_revision]),
                action,
                digest(target),
                outcome,
                time.time(),
            ),
        )

    def audit_denied(self, principal, action, target):
        if not isinstance(principal, Principal):
            return  # Unauthenticated requests belong to transport authentication logs.
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self.audit(connection, principal, action, target, "denied")

    def __call__(
        self,
        connection: sqlite3.Connection,
        principal: Principal,
        action: str,
        scope: str,
        scope_id: str,
    ) -> None:
        actions = {
            "read": ("knowledge.read", {SCOPE_READ, SCOPE_WRITE, SCOPE_ADMIN}),
            "instructions": ("knowledge.read", {SCOPE_READ, SCOPE_WRITE, SCOPE_ADMIN}),
            "propose": ("knowledge.propose", {SCOPE_WRITE, SCOPE_ADMIN}),
            "review": ("knowledge.review", {SCOPE_ADMIN}),
            "tombstone": ("knowledge.tombstone", {SCOPE_ADMIN}),
        }
        if (
            not isinstance(principal, Principal)
            or action not in actions
            or not principal.scopes & actions[action][1]
            or scope not in {"project", "job"}
        ):
            raise KnowledgeAccessDenied("verified knowledge authority required")
        if not connection.in_transaction:
            raise KnowledgeAccessDenied("knowledge policy requires the caller's transaction")
        databases = connection.execute("PRAGMA database_list").fetchall()
        main = next((row[2] for row in databases if row[1] == "main"), None)
        if not main or Path(main).resolve() != self.repository.path.resolve():
            raise KnowledgeAccessDenied("knowledge transaction belongs to another authority")
        self.repository._verify(connection)
        try:
            chain, job = WorkAuthority(self.repository)._chain(
                connection,
                self.grant_id,
                self.expected_grant_revision,
            )
        except (AuthorityDenied, GrantConflict, KeyError) as error:
            raise KnowledgeAccessDenied("knowledge grant is not current") from error
        required = actions[action][0]
        if (
            job["id"] != self.job_id
            or chain[0].principal_id != principal.id
            or scope_id != (job["project_id"] if scope == "project" else job["id"])
            or any(required not in grant.permissions.tools for grant in chain)
        ):
            raise KnowledgeAccessDenied("knowledge operation exceeds its durable grant")


class LegacyMemoryAuditError(RuntimeError):
    """Audit persistence failed; operation identity supports local reconciliation."""

    def __init__(self, operation_id: str, phase: str):
        self.operation_id = operation_id
        self.phase = phase
        outcome = (
            "operation not started"
            if phase in {"authorized", "denied"}
            else "potentially partial outcome"
        )
        super().__init__(f"legacy memory audit failed; {outcome}; operation_id={operation_id}")


_ENCLOSING_LEGACY_AUDIT: ContextVar[tuple[WorkRepository, str, str] | None] = ContextVar(
    "enclosing_legacy_database_audit", default=None
)


def join_legacy_database_audit(principal, db):
    """Reuse a durable outer intent only for its verified SQLite transaction."""
    active = _ENCLOSING_LEGACY_AUDIT.get()
    if active is None:
        raise KnowledgeAccessDenied(
            "shared legacy transaction requires an enclosing audited operation"
        )
    repository, actor_id, operation_id = active
    require_legacy_operator(principal)
    if principal.id != actor_id:
        raise KnowledgeAccessDenied("shared legacy transaction belongs to another authority")
    connection = db.connection().connection.driver_connection
    databases = connection.execute("PRAGMA database_list").fetchall()
    main = next((row[2] for row in databases if row[1] == "main"), None)
    if not main or Path(main).resolve() != repository.path.resolve():
        raise KnowledgeAccessDenied("shared legacy transaction belongs to another authority")
    repository._verify(connection)
    return operation_id


@contextmanager
def legacy_memory_access(repository: WorkRepository, principal: Principal, action: str, target):
    """Commit intent before file effects; fail closed if either audit commit fails.

    The caller initializes the shared repository. Targets contain identifiers only;
    neither their plaintext nor operation results/errors enter the durable audit.
    """
    if not isinstance(principal, Principal):
        raise KnowledgeAccessDenied("legacy memory requires a verified local operator")
    if action not in {
        "store",
        "recall",
        "forget",
        "compact",
        "context",
        "curated_context",
        "export",
        "import",
        "graph",
        "relationships",
        "repair",
    }:
        raise ValueError("invalid legacy memory action")
    try:
        encoded = json.dumps(target, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("invalid legacy memory target identifiers") from None
    target_hash = hashlib.sha256(encoded.encode()).hexdigest()
    operation_id = uuid4().hex
    authorized_at = time.time()

    def append(phase):
        try:
            with repository.transaction() as connection:
                repository._verify(connection)
                connection.execute(
                    "INSERT INTO work_memory_access_audit "
                    "(operation_id,actor_id,action,target_hash,phase,occurred_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        operation_id,
                        principal.id,
                        action,
                        target_hash,
                        phase,
                        max(authorized_at, time.time()),
                    ),
                )
        except Exception:
            raise LegacyMemoryAuditError(operation_id, phase) from None

    try:
        require_legacy_operator(principal)
    except KnowledgeAccessDenied:
        append("denied")
        raise
    append("authorized")
    audit_token = _ENCLOSING_LEGACY_AUDIT.set((repository, principal.id, operation_id))
    try:
        yield operation_id
    except LegacyMemoryAuditError:
        append("failed")
        raise LegacyMemoryAuditError(operation_id, "failed") from None
    except BaseException:
        append("failed")
        raise
    else:
        append("completed")
    finally:
        _ENCLOSING_LEGACY_AUDIT.reset(audit_token)


def audit_legacy_memory_denied(
    repository: WorkRepository, principal: Principal, action: str, target
):
    """Record a verified nonlocal denial using the same fail-closed policy boundary."""
    if not isinstance(principal, Principal) or principal.kind == "local_operator":
        raise KnowledgeAccessDenied("verified nonlocal legacy denial required")
    try:
        with legacy_memory_access(repository, principal, action, target):
            raise AssertionError("nonlocal legacy authorization is invalid")
    except KnowledgeAccessDenied:
        pass
