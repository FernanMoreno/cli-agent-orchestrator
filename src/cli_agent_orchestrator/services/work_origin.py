"""Internal durable origin authority; no transport or native child is wired here."""

import hashlib
import hmac
import json
import math
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic import field_validator

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_origin import (
    Digest,
    FrozenOriginModel,
    ManagedLineageIntent,
    OriginAuthorization,
    OriginAuthorizationRef,
    OriginSubjectRef,
    Identity,
    Positive,
    WorkAttemptRef,
    lineage_integrity_fingerprint,
)
from cli_agent_orchestrator.security.auth import SCOPE_WRITE, Principal, _verified_principal
from cli_agent_orchestrator.services.work_authority import (
    AuthorityDenied,
    Permissions,
    WorkAuthority,
)
from cli_agent_orchestrator.services.work_contract import ContractConflict, WorkContracts
from cli_agent_orchestrator.services.work_delivery import WorkDeliveries


class OriginDenied(PermissionError):
    """The verified principal or durable origin history does not authorize this action."""


class OriginConflict(ValueError):
    """A requested origin-history revision is stale or contradictory."""


def _canonical(value) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _identity(value, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise OriginDenied(f"invalid lineage {label}")
    return value


_TASK_RECEIPT_DOMAIN = "cao.work.task-received.v1"


class TaskReceivedReceiptV1(FrozenOriginModel):
    """Opaque internal proof that a registered receiver accepted one delivery."""

    schema_version: Literal[1] = 1
    attempt: WorkAttemptRef
    attempt_revision: Positive
    receiver_subject_ref: OriginSubjectRef
    receiver_authorization_ref: OriginAuthorizationRef
    delivery_id: Identity
    delivery_hash: Digest
    nonce: Identity
    proof: Digest

    @field_validator("schema_version", "attempt_revision", mode="before")
    @classmethod
    def exact_integer(cls, value):
        if type(value) is not int:
            raise ValueError("task receipt versions and revisions must be integers")
        return value

    def fingerprint(self) -> str:
        """Bind idempotency to every immutable receipt field, including its proof."""
        return hashlib.sha256(
            _canonical(
                {
                    "attempt": self.attempt.model_dump(mode="json"),
                    "attempt_revision": self.attempt_revision,
                    "delivery_hash": self.delivery_hash,
                    "delivery_id": self.delivery_id,
                    "domain": _TASK_RECEIPT_DOMAIN,
                    "nonce": self.nonce,
                    "proof": self.proof,
                    "receiver_authorization_ref": self.receiver_authorization_ref.model_dump(
                        mode="json"
                    ),
                    "receiver_subject_ref": self.receiver_subject_ref.model_dump(mode="json"),
                    "schema_version": self.schema_version,
                }
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class _AuthenticatedLineageContext:
    """Server-created identity set; callers cannot substitute IDs for it."""

    requester: Principal
    subjects: tuple[Principal, ...]
    credential_sha256: str | None
    _runtime: object = field(repr=False, compare=False)
    _seal: str = field(repr=False, compare=False)


@dataclass(frozen=True)
class _ManagedLineageHandoff:
    """Private resolve→admission handoff, sealed to one WorkOrigins runtime."""

    context: _AuthenticatedLineageContext
    parent_attempt_ref: WorkAttemptRef
    child_subject_ref: OriginSubjectRef
    child_authorization_ref: OriginAuthorizationRef
    receiver_subject_ref: OriginSubjectRef
    receiver_authorization_ref: OriginAuthorizationRef
    intent: ManagedLineageIntent
    idempotency_key: str
    kind: str
    request_hash: str
    _origin: object = field(repr=False, compare=False)
    _seal: str = field(repr=False, compare=False)


class WorkOriginAuthority:
    """Issue, revoke, and freshly resolve bounded origin actions inside the work store."""

    _KINDS = frozenset({"child", "workflow", "receiver"})
    _ACTIONS = frozenset(
        {
            "launch",
            "admit_child",
            "admit_handoff",
            "admit_step",
            "execute",
            "task_received",
            "delegate",
        }
    )

    def __init__(self, repository: WorkRepository):
        self.repository = repository
        self._authority = WorkAuthority(repository)

    @staticmethod
    def _revision(value: int, *, initial: bool = False) -> int:
        if type(value) is not int or value < (0 if initial else 1):
            raise OriginConflict("origin revision is invalid")
        return value

    @classmethod
    def _kind(cls, value: str) -> str:
        if not isinstance(value, str) or value not in cls._KINDS:
            raise OriginDenied("origin kind is not supported")
        return value

    @classmethod
    def _actions(cls, values) -> tuple[str, ...]:
        if not isinstance(values, (set, frozenset, list, tuple)):
            raise OriginDenied("origin actions must be an explicit collection")
        actions = tuple(values)
        if (
            not actions
            or len(actions) != len(set(actions))
            or any(not isinstance(action, str) or action not in cls._ACTIONS for action in actions)
        ):
            raise OriginDenied("origin actions are invalid or contradictory")
        return tuple(sorted(actions))

    @classmethod
    def _action(cls, value: str) -> str:
        if not isinstance(value, str) or value not in cls._ACTIONS:
            raise OriginDenied("origin action is not supported")
        return value

    @staticmethod
    def _expiry(value: float) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise OriginDenied("origin expiry must be a finite timestamp")
        if value <= time.time():
            raise OriginDenied("origin authorization is already expired")
        return float(value)

    @staticmethod
    def _principal(principal: Principal, *, admin: bool = False) -> None:
        try:
            WorkAuthority._principal(principal, admin=admin)
        except AuthorityDenied as error:
            raise OriginDenied(
                "a verified principal with the required scope is required"
            ) from error

    def _registered(
        self, connection: sqlite3.Connection, principal: Principal, *, admin=False
    ) -> None:
        self._principal(principal, admin=admin)
        row = connection.execute(
            "SELECT issuer,subject,kind FROM work_principals WHERE id=?", (principal.id,)
        ).fetchone()
        if row is None or tuple(row) != (principal.issuer, principal.subject, principal.kind):
            raise OriginDenied("verified principal is not durably registered")

    def _subject(self, connection: sqlite3.Connection, subject: Principal, origin_kind: str):
        self._registered(connection, subject)
        row = connection.execute(
            "SELECT * FROM work_origin_subjects WHERE subject_id=? ORDER BY revision DESC LIMIT 1",
            (subject.id,),
        ).fetchone()
        if row is None or row["origin_kind"] != origin_kind or row["state"] != "active":
            raise OriginDenied("origin subject is not currently registered")
        return row

    @staticmethod
    def _authorization(row) -> OriginAuthorization:
        try:
            actions = WorkOriginAuthority._actions(json.loads(row["actions"]))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise OriginDenied("origin authorization actions are corrupt") from error
        return OriginAuthorization(
            ref=OriginAuthorizationRef(
                subject_id=row["subject_id"],
                origin_kind=row["origin_kind"],
                revision=row["revision"],
            ),
            subject_revision=row["subject_revision"],
            issuer_id=row["issuer_id"],
            job_id=row["job_id"],
            grant_id=row["grant_id"],
            grant_revision=row["grant_revision"],
            actions=actions,
            expires_at=row["expires_at"],
        )

    @staticmethod
    def _current_authorization(connection, *, subject_id: str, origin_kind: str):
        return connection.execute(
            """SELECT * FROM work_origin_authorizations
               WHERE subject_id=? AND origin_kind=? ORDER BY revision DESC LIMIT 1""",
            (subject_id, origin_kind),
        ).fetchone()

    def register_subject(
        self,
        owner: Principal,
        *,
        verified_subject: Principal,
        kind: str,
        issuer_id: str,
        expected_revision: int,
    ) -> OriginSubjectRef:
        """Append an explicit subject registration; this never registers a supplied identity."""
        kind = self._kind(kind)
        expected_revision = self._revision(expected_revision, initial=True)
        if not isinstance(issuer_id, str) or issuer_id != owner.id:
            raise OriginDenied("origin issuer must be the verified owner")
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self._registered(connection, owner, admin=True)
            self._registered(connection, verified_subject)
            prior = connection.execute(
                "SELECT * FROM work_origin_subjects WHERE subject_id=? ORDER BY revision DESC LIMIT 1",
                (verified_subject.id,),
            ).fetchone()
            if prior is None:
                if expected_revision != 0:
                    raise OriginConflict("origin subject creation is stale")
                revision = 1
            else:
                if (
                    expected_revision == 0
                    or prior["revision"] != expected_revision
                    or prior["origin_kind"] != kind
                    or prior["state"] != "active"
                ):
                    raise OriginConflict("origin subject history is stale or contradictory")
                if prior["issuer_id"] != owner.id:
                    raise OriginDenied("origin subject issuer cannot be transferred")
                revision = expected_revision + 1
            try:
                connection.execute(
                    """INSERT INTO work_origin_subjects
                       (subject_id,revision,schema_version,origin_kind,state,issuer_id,created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (verified_subject.id, revision, 1, kind, "active", owner.id, time.time()),
                )
            except sqlite3.IntegrityError as error:
                raise OriginConflict("origin subject snapshot conflicts") from error
        return OriginSubjectRef(subject_id=verified_subject.id, kind=kind, revision=revision)

    def authorize(
        self,
        owner: Principal,
        *,
        subject: Principal,
        origin_kind: str,
        grant_id: str,
        grant_revision: int,
        actions,
        expires_at: float,
        expected_revision: int,
    ) -> OriginAuthorization:
        """Append one bounded action set for the subject's live grant chain."""
        origin_kind = self._kind(origin_kind)
        grant_revision = self._revision(grant_revision)
        expected_revision = self._revision(expected_revision, initial=True)
        actions = self._actions(actions)
        expires_at = self._expiry(expires_at)
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self._registered(connection, owner, admin=True)
            subject_row = self._subject(connection, subject, origin_kind)
            if subject_row["issuer_id"] != owner.id:
                raise OriginDenied("only the origin subject issuer may authorize it")
            try:
                chain, job = self._authority._chain(connection, grant_id, grant_revision)
            except (AuthorityDenied, KeyError, ValueError) as error:
                raise OriginDenied("origin grant chain is not live") from error
            if (
                job["principal_id"] != owner.id
                or chain[0].principal_id != subject.id
                or expires_at > chain[0].expires_at
            ):
                raise OriginDenied(
                    "origin grant must belong to the owner, subject, and live ancestry"
                )
            prior = self._current_authorization(
                connection, subject_id=subject.id, origin_kind=origin_kind
            )
            if prior is not None and prior["issuer_id"] != owner.id:
                raise OriginDenied("origin authorization issuer history is incoherent")
            if (prior is None and expected_revision != 0) or (
                prior is not None and prior["revision"] != expected_revision
            ):
                raise OriginConflict("origin authorization revision is stale")
            revision = expected_revision + 1
            try:
                connection.execute(
                    """INSERT INTO work_origin_authorizations
                       (subject_id,origin_kind,revision,schema_version,state,issuer_id,subject_revision,
                        job_id,grant_id,grant_revision,actions,expires_at,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        subject.id,
                        origin_kind,
                        revision,
                        1,
                        "active",
                        owner.id,
                        subject_row["revision"],
                        job["id"],
                        grant_id,
                        grant_revision,
                        json.dumps(actions, separators=(",", ":")),
                        expires_at,
                        time.time(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise OriginConflict("origin authorization snapshot conflicts") from error
            row = self._current_authorization(
                connection, subject_id=subject.id, origin_kind=origin_kind
            )
        return self._authorization(row)

    def revoke(
        self,
        *,
        owner: Principal,
        subject: Principal,
        origin_kind: str,
        expected_revision: int,
    ) -> OriginAuthorizationRef:
        """Append a revoked revision; revoke never mutates the prior authorization."""
        origin_kind = self._kind(origin_kind)
        expected_revision = self._revision(expected_revision)
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self._registered(connection, owner, admin=True)
            self._registered(connection, subject)
            prior = self._current_authorization(
                connection, subject_id=subject.id, origin_kind=origin_kind
            )
            if (
                prior is None
                or prior["revision"] != expected_revision
                or prior["state"] != "active"
            ):
                raise OriginConflict("origin authorization revision is stale")
            job = self.repository._job(connection, prior["job_id"])
            if job["principal_id"] != owner.id or prior["issuer_id"] != owner.id:
                raise OriginDenied("only the original job owner may revoke this authorization")
            revision = expected_revision + 1
            try:
                connection.execute(
                    """INSERT INTO work_origin_authorizations
                       (subject_id,origin_kind,revision,schema_version,state,issuer_id,subject_revision,
                        job_id,grant_id,grant_revision,actions,expires_at,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        subject.id,
                        origin_kind,
                        revision,
                        1,
                        "revoked",
                        owner.id,
                        prior["subject_revision"],
                        prior["job_id"],
                        prior["grant_id"],
                        prior["grant_revision"],
                        prior["actions"],
                        prior["expires_at"],
                        time.time(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise OriginConflict("origin revocation snapshot conflicts") from error
        return OriginAuthorizationRef(
            subject_id=subject.id, origin_kind=origin_kind, revision=revision
        )

    def resolve(
        self,
        subject: Principal,
        *,
        origin_kind: str,
        job_id: str,
        grant_id: str,
        grant_revision: int,
        action: str,
    ) -> OriginAuthorization:
        """Resolve only the current active record and freshly revalidate its ancestry."""
        origin_kind = self._kind(origin_kind)
        grant_revision = self._revision(grant_revision)
        action = self._action(action)
        with self.repository.read_snapshot() as connection:
            subject_row = self._subject(connection, subject, origin_kind)
            row = self._current_authorization(
                connection, subject_id=subject.id, origin_kind=origin_kind
            )
            if (
                row is None
                or row["state"] != "active"
                or row["subject_revision"] != subject_row["revision"]
                or row["issuer_id"] != subject_row["issuer_id"]
                or row["job_id"] != job_id
                or row["grant_id"] != grant_id
                or row["grant_revision"] != grant_revision
                or row["expires_at"] <= time.time()
            ):
                raise OriginDenied("origin authorization is absent, stale, or expired")
            try:
                chain, job = self._authority._chain(connection, grant_id, grant_revision)
            except (AuthorityDenied, KeyError, ValueError) as error:
                raise OriginDenied("origin grant chain is not live") from error
            authorization = self._authorization(row)
            if (
                job["id"] != job_id
                or job["principal_id"] != authorization.issuer_id
                or chain[0].principal_id != subject.id
                or action not in authorization.actions
            ):
                raise OriginDenied("origin authorization no longer matches its live grant")
            return authorization


class WorkOrigins:
    """Resolve and consume sealed internal child/handoff origins.

    This service is deliberately not a transport boundary.  Its context factory
    is invoked only after server authentication, and the resulting handoff is
    accepted only by the originating runtime and its paired WorkAdmission.
    """

    _KINDS = frozenset({"child", "handoff"})

    def __init__(self, repository: WorkRepository):
        if not isinstance(repository, WorkRepository):
            raise ValueError("verified work repository required")
        self.repository = repository
        self.authority = WorkAuthority(repository)
        self.origin_authority = WorkOriginAuthority(repository)
        self.contracts = WorkContracts(repository)
        self._runtime_identity = object()
        self._handoff_secret = secrets.token_bytes(32)
        self._admission = None

    def _bind_admission(self, admission) -> None:
        """Pair private origin tools with their one runtime admission owner."""
        from cli_agent_orchestrator.services.work_admission import WorkAdmission

        if (
            not isinstance(admission, WorkAdmission)
            or admission.repository is not self.repository
            or (self._admission is not None and self._admission is not admission)
        ):
            raise OriginDenied("managed origin endpoint belongs to another Work runtime")
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

    def _context_payload(self, context: _AuthenticatedLineageContext) -> str:
        return _canonical(
            {
                "requester": self._principal_payload(context.requester),
                "subjects": [self._principal_payload(subject) for subject in context.subjects],
                "credential_sha256": context.credential_sha256,
            }
        )

    def _seal_context(self, context: _AuthenticatedLineageContext) -> str:
        return hmac.new(
            self._handoff_secret,
            self._context_payload(context).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def authenticated_context(self, requester: Principal, *subjects: Principal):
        """Create an opaque server context from already verified principal objects."""
        try:
            WorkAuthority._principal(requester)
            if any(not isinstance(subject, Principal) for subject in subjects):
                raise AuthorityDenied("verified lineage subjects are required")
            identities = (requester, *subjects)
            if len({principal.id for principal in identities}) != len(identities):
                raise AuthorityDenied("lineage identities must be distinct")
            context = _AuthenticatedLineageContext(
                requester=requester,
                subjects=tuple(subjects),
                credential_sha256=None,
                _runtime=self._runtime_identity,
                _seal="",
            )
            return _AuthenticatedLineageContext(
                requester=requester,
                subjects=tuple(subjects),
                credential_sha256=None,
                _runtime=self._runtime_identity,
                _seal=self._seal_context(context),
            )
        except AuthorityDenied as error:
            raise OriginDenied("verified lineage authentication context is required") from error

    def _context(self, context) -> _AuthenticatedLineageContext:
        if (
            not isinstance(context, _AuthenticatedLineageContext)
            or context._runtime is not self._runtime_identity
            or not hmac.compare_digest(context._seal, self._seal_context(context))
        ):
            raise OriginDenied("managed lineage requires authenticated server context")
        return context

    def _credential_context(self, requester, subjects, digest):
        """Seal identities resolved from one live attempt credential."""
        try:
            WorkAuthority._principal(requester)
            if any(not isinstance(subject, Principal) for subject in subjects):
                raise AuthorityDenied("verified lineage subjects are required")
            identities = (requester, *subjects)
            if len({principal.id for principal in identities}) != len(identities):
                raise AuthorityDenied("lineage identities must be distinct")
            context = _AuthenticatedLineageContext(
                requester=requester,
                subjects=tuple(subjects),
                credential_sha256=digest,
                _runtime=self._runtime_identity,
                _seal="",
            )
            return _AuthenticatedLineageContext(
                requester=requester,
                subjects=tuple(subjects),
                credential_sha256=digest,
                _runtime=self._runtime_identity,
                _seal=self._seal_context(context),
            )
        except AuthorityDenied as error:
            raise OriginDenied("attempt credential context is invalid") from error

    @staticmethod
    def _credential_principal(connection, principal_id):
        """Rebuild a narrow internal principal only from durable identity rows."""
        row = connection.execute(
            "SELECT issuer,subject,kind FROM work_principals WHERE id=?", (principal_id,)
        ).fetchone()
        if row is None:
            raise OriginDenied("attempt principal is no longer registered")
        try:
            principal = _verified_principal(row["issuer"], row["subject"], [SCOPE_WRITE], row["kind"])
        except Exception as error:
            raise OriginDenied("attempt principal identity is corrupt") from error
        if principal.id != principal_id:
            raise OriginDenied("attempt principal identity does not match its registration")
        return principal

    def _preprovisioned_subject(self, connection, subject_id, expected_kind):
        """Resolve current subject and authorization refs; callers cannot supply actors."""
        subject_id = _identity(subject_id, "preprovisioned subject")
        subject = connection.execute(
            "SELECT * FROM work_origin_subjects WHERE subject_id=? "
            "ORDER BY revision DESC LIMIT 1",
            (subject_id,),
        ).fetchone()
        authorization = self.origin_authority._current_authorization(
            connection, subject_id=subject_id, origin_kind=expected_kind
        )
        if (
            subject is None
            or subject["origin_kind"] != expected_kind
            or subject["state"] != "active"
            or authorization is None
            or authorization["state"] != "active"
        ):
            raise OriginDenied("lineage subject is not actively preprovisioned")
        return (
            self._credential_principal(connection, subject_id),
            OriginSubjectRef(
                subject_id=subject_id,
                kind=expected_kind,
                revision=subject["revision"],
            ),
            OriginAuthorizationRef(
                subject_id=subject_id,
                origin_kind=expected_kind,
                revision=authorization["revision"],
            ),
        )

    def admit_from_attempt_credential(
        self,
        credential: bytes,
        *,
        child_subject_id: str,
        receiver_subject_id: str,
        intent: ManagedLineageIntent,
        idempotency_key: str,
        kind: str,
    ):
        """Admit lineage from the private per-attempt endpoint, with no caller principals."""
        from cli_agent_orchestrator.services.work_attempt_credential import (
            WorkAttemptCredentialRejected,
            WorkAttemptCredentials,
        )

        if type(credential) is not bytes or len(credential) != 32:
            raise OriginDenied("attempt credential is invalid")
        credentials = WorkAttemptCredentials(self.repository)
        digest = hashlib.sha256(credential).hexdigest()
        try:
            with self.repository.read_snapshot() as connection:
                authenticated = credentials.authenticate_in_transaction(connection, credential)
                parent_attempt_ref = WorkAttemptRef(
                    work_item_id=authenticated.work_item_id,
                    attempt_id=authenticated.attempt_id,
                    generation=authenticated.generation,
                )
                requester = self._credential_principal(connection, authenticated.principal_id)
                child, child_ref, child_auth_ref = self._preprovisioned_subject(
                    connection, child_subject_id, "child"
                )
                receiver, receiver_ref, receiver_auth_ref = self._preprovisioned_subject(
                    connection, receiver_subject_id, "receiver"
                )
                context = self._credential_context(
                    requester, (child, receiver), digest
                )
        except WorkAttemptCredentialRejected as error:
            raise OriginDenied("attempt credential is expired, revoked or stale") from error
        return self.admit(
            authenticated_context=context,
            parent_attempt_ref=parent_attempt_ref,
            child_subject_ref=child_ref,
            child_authorization_ref=child_auth_ref,
            receiver_subject_ref=receiver_ref,
            receiver_authorization_ref=receiver_auth_ref,
            intent=intent,
            idempotency_key=idempotency_key,
            kind=kind,
        )

    def handle_mcp_request(
        self, request: dict, credential: bytes, *, receiver_credential: bytes | None = None
    ) -> dict:
        """Dispatch attempt-bound lineage and independently authenticated ACK tools."""
        request_id = request.get("id") if type(request) is dict else None
        try:
            if (
                type(request) is not dict
                or request.get("jsonrpc") != "2.0"
                or request.get("method") != "tools/call"
                or type(request.get("params")) is not dict
                or set(request["params"]) != {"name", "arguments"}
                or type(request["params"].get("arguments")) is not dict
            ):
                raise OriginDenied("managed Work proxy request is invalid")
            name = request["params"]["name"]
            arguments = request["params"]["arguments"]
            if name == "cao.work.task_received":
                if arguments or type(receiver_credential) is not bytes:
                    raise OriginDenied("receiver acknowledgement requires its separate credential")
                work = self.accept_task_received_with_credentials(
                    attempt_credential=credential,
                    receiver_credential=receiver_credential,
                )
                attempt = work["attempts"][-1]
                result = {
                    "attempt_id": attempt["id"],
                    "generation": attempt["generation"],
                    "state": attempt["state"],
                    "work_item_id": work["id"],
                }
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    result, sort_keys=True, separators=(",", ":")
                                ),
                            }
                        ]
                    },
                }
            kind = {"cao.work.child": "child", "cao.work.handoff": "handoff"}.get(
                name
            )
            if kind is None or self._admission is None:
                raise OriginDenied("managed Work proxy tool is unavailable")
            if set(arguments) != {
                "child_subject_id",
                "receiver_subject_id",
                "intent",
                "idempotency_key",
            }:
                raise OriginDenied("managed Work proxy arguments are invalid")
            intent = ManagedLineageIntent.model_validate_json(
                json.dumps(arguments["intent"], allow_nan=False)
            )
            handoff = self.admit_from_attempt_credential(
                credential,
                child_subject_id=arguments["child_subject_id"],
                receiver_subject_id=arguments["receiver_subject_id"],
                intent=intent,
                idempotency_key=arguments["idempotency_key"],
                kind=kind,
            )
            child = self._admission.admit_managed_lineage(handoff)
            attempt = child["attempts"][0]
            result = {
                "work_item_id": child["id"],
                "attempt_id": attempt["id"],
                "generation": attempt["generation"],
                "state": child["state"],
            }
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, sort_keys=True, separators=(",", ":")),
                        }
                    ]
                },
            }
        except Exception:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32001, "message": "Managed Work request rejected"},
            }

    @staticmethod
    def _kind(value: str) -> str:
        if not isinstance(value, str) or value not in WorkOrigins._KINDS:
            raise OriginDenied("managed lineage kind is not supported")
        return value

    @staticmethod
    def _intent(value) -> ManagedLineageIntent:
        if not isinstance(value, ManagedLineageIntent):
            raise OriginDenied("managed lineage intent is invalid")
        return value

    @staticmethod
    def _ref(value, expected_type, label: str):
        if not isinstance(value, expected_type):
            raise OriginDenied(f"managed lineage {label} is invalid")
        return value

    def _handoff_payload(self, handoff: _ManagedLineageHandoff) -> str:
        return _canonical(
            {
                "child_authorization_ref": handoff.child_authorization_ref.model_dump(mode="json"),
                "child_subject_ref": handoff.child_subject_ref.model_dump(mode="json"),
                "context": self._context_payload(handoff.context),
                "idempotency_key": handoff.idempotency_key,
                "intent": {
                    "contract": handoff.intent.contract.canonical_json(),
                    "delivery": handoff.intent.delivery.model_dump(mode="json"),
                    "handoff_id": handoff.intent.handoff_id,
                    "lease_seconds": handoff.intent.lease_seconds,
                    "native_child_id": handoff.intent.native_child_id,
                    "terminal_id": handoff.intent.terminal_id,
                },
                "kind": handoff.kind,
                "parent_attempt_ref": handoff.parent_attempt_ref.model_dump(mode="json"),
                "receiver_authorization_ref": handoff.receiver_authorization_ref.model_dump(
                    mode="json"
                ),
                "receiver_subject_ref": handoff.receiver_subject_ref.model_dump(mode="json"),
                "request_hash": handoff.request_hash,
            }
        )

    def _seal_handoff(self, handoff: _ManagedLineageHandoff) -> str:
        return hmac.new(
            self._handoff_secret,
            self._handoff_payload(handoff).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _request_hash(
        self,
        *,
        context: _AuthenticatedLineageContext,
        parent_attempt_ref: WorkAttemptRef,
        child_subject_ref: OriginSubjectRef,
        child_authorization_ref: OriginAuthorizationRef,
        receiver_subject_ref: OriginSubjectRef,
        receiver_authorization_ref: OriginAuthorizationRef,
        intent: ManagedLineageIntent,
        idempotency_key: str,
        kind: str,
    ) -> str:
        return hashlib.sha256(
            _canonical(
                {
                    "child_authorization_ref": child_authorization_ref.model_dump(mode="json"),
                    "child_subject_ref": child_subject_ref.model_dump(mode="json"),
                    "contract": intent.contract.canonical_json(),
                    "delivery": intent.delivery.model_dump(mode="json"),
                    "idempotency_key": idempotency_key,
                    "kind": kind,
                    "parent_attempt_ref": parent_attempt_ref.model_dump(mode="json"),
                    "receiver_authorization_ref": receiver_authorization_ref.model_dump(
                        mode="json"
                    ),
                    "receiver_subject_ref": receiver_subject_ref.model_dump(mode="json"),
                    "requester_id": context.requester.id,
                }
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _context_subject(context: _AuthenticatedLineageContext, subject_id: str) -> Principal:
        for principal in context.subjects:
            if principal.id == subject_id:
                return principal
        raise OriginDenied("managed lineage subject is not authenticated by the server")

    @staticmethod
    def _task_receipt_payload(receipt: TaskReceivedReceiptV1) -> str:
        """Serialize the receiver proof input without treating it as a bearer token."""
        return _canonical(
            {
                "attempt": receipt.attempt.model_dump(mode="json"),
                "attempt_revision": receipt.attempt_revision,
                "delivery_hash": receipt.delivery_hash,
                "delivery_id": receipt.delivery_id,
                "domain": _TASK_RECEIPT_DOMAIN,
                "nonce": receipt.nonce,
                "receiver_authorization_ref": receipt.receiver_authorization_ref.model_dump(
                    mode="json"
                ),
                "receiver_subject_ref": receipt.receiver_subject_ref.model_dump(mode="json"),
                "schema_version": receipt.schema_version,
            }
        )

    def _seal_task_receipt(self, receipt: TaskReceivedReceiptV1) -> str:
        return hmac.new(
            self._handoff_secret,
            self._task_receipt_payload(receipt).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _task_received_origin(
        self,
        connection: sqlite3.Connection,
        *,
        attempt_ref: WorkAttemptRef,
        receiver_subject_ref: OriginSubjectRef,
        receiver_authorization_ref: OriginAuthorizationRef,
        attempt_revision: int | None = None,
        delivery_id: str | None = None,
        delivery_hash: str | None = None,
    ):
        """Revalidate the exact v23 lineage and its live receiver authority."""
        origin = connection.execute(
            "SELECT * FROM work_child_origin_bindings WHERE child_attempt_id=? "
            "AND child_generation=?",
            (attempt_ref.attempt_id, attempt_ref.generation),
        ).fetchone()
        if origin is None:
            raise OriginDenied("task receipt has no managed lineage origin")
        if (
            origin["child_work_item_id"] != attempt_ref.work_item_id
            or origin["receiver_subject_id"] != receiver_subject_ref.subject_id
            or origin["receiver_subject_revision"] != receiver_subject_ref.revision
            or receiver_subject_ref.kind != "receiver"
            or origin["receiver_authorization_kind"] != "receiver"
            or receiver_authorization_ref.subject_id != receiver_subject_ref.subject_id
            or receiver_authorization_ref.origin_kind != "receiver"
            or origin["receiver_authorization_revision"]
            != receiver_authorization_ref.revision
            or (delivery_id is not None and origin["delivery_id"] != delivery_id)
            or (delivery_hash is not None and origin["delivery_hash"] != delivery_hash)
        ):
            raise OriginDenied("task receipt does not match its managed delivery")
        self._revalidate_integrity(connection, origin)
        self._revalidate_replay_authorizations(connection, origin)
        attempt = connection.execute(
            "SELECT * FROM work_attempts WHERE id=?", (attempt_ref.attempt_id,)
        ).fetchone()
        if attempt is None or (
            attempt["work_item_id"],
            attempt["generation"],
            attempt["state"],
        ) != (attempt_ref.work_item_id, attempt_ref.generation, "sent"):
            raise OriginDenied("task receipt attempt is stale or not sent")
        if attempt_revision is not None and attempt["revision"] != attempt_revision:
            raise OriginDenied("task receipt attempt revision changed")
        return origin, attempt

    @staticmethod
    def _receiver_acceptance_hash(
        *,
        attempt_ref,
        receiver_subject_ref,
        receiver_authorization_ref,
        delivery_id,
        delivery_hash,
    ) -> str:
        payload = _canonical(
            {
                "attempt": attempt_ref.model_dump(mode="json"),
                "delivery_hash": delivery_hash,
                "delivery_id": delivery_id,
                "receiver_authorization_ref": receiver_authorization_ref.model_dump(mode="json"),
                "receiver_subject_ref": receiver_subject_ref.model_dump(mode="json"),
                "schema_version": 1,
            }
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _record_task_received_acceptance_in_transaction(
        self,
        connection,
        *,
        attempt_ref,
        receiver_subject_ref,
        receiver_authorization_ref,
        origin,
    ):
        """Persist receiver acceptance inside the caller's Work transaction."""
        digest = self._receiver_acceptance_hash(
            attempt_ref=attempt_ref,
            receiver_subject_ref=receiver_subject_ref,
            receiver_authorization_ref=receiver_authorization_ref,
            delivery_id=origin["delivery_id"],
            delivery_hash=origin["delivery_hash"],
        )
        prior = connection.execute(
            "SELECT * FROM work_task_receiver_acceptances "
            "WHERE attempt_id=? AND generation=?",
            (attempt_ref.attempt_id, attempt_ref.generation),
        ).fetchone()
        expected = (
            receiver_subject_ref.subject_id,
            receiver_subject_ref.revision,
            receiver_authorization_ref.revision,
            origin["delivery_id"],
            origin["delivery_hash"],
            digest,
        )
        if prior is not None:
            actual = (
                prior["receiver_subject_id"],
                prior["receiver_subject_revision"],
                prior["receiver_authorization_revision"],
                prior["delivery_id"],
                prior["delivery_hash"],
                prior["acceptance_sha256"],
            )
            if actual != expected:
                raise OriginConflict("receiver acceptance replay contradicts durable evidence")
            return dict(prior)
        connection.execute(
            """INSERT INTO work_task_receiver_acceptances
               (attempt_id,generation,receiver_subject_id,receiver_subject_revision,
                receiver_authorization_revision,delivery_id,delivery_hash,
                acceptance_sha256,accepted_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                attempt_ref.attempt_id,
                attempt_ref.generation,
                *expected[:5],
                digest,
                time.time(),
            ),
        )
        return dict(
            connection.execute(
                "SELECT * FROM work_task_receiver_acceptances "
                "WHERE attempt_id=? AND generation=?",
                (attempt_ref.attempt_id, attempt_ref.generation),
            ).fetchone()
        )

    def accept_task_received_with_credentials(
        self, *, attempt_credential: bytes, receiver_credential: bytes
    ) -> dict:
        """Atomically persist receiver acceptance and the Work ACK transition."""
        from uuid import uuid4

        from cli_agent_orchestrator.services.work_attempt_credential import (
            WorkAttemptCredentialRejected,
            WorkAttemptCredentials,
        )

        credentials = WorkAttemptCredentials(self.repository)
        try:
            with self.repository.transaction() as connection:
                self.repository._verify(connection)
                executor = credentials.authenticate_in_transaction(
                    connection, attempt_credential
                )
                receiver = credentials.authenticate_receiver_in_transaction(
                    connection, receiver_credential
                )
                if (
                    executor.attempt_id,
                    executor.generation,
                    executor.work_item_id,
                ) != (receiver.attempt_id, receiver.generation, receiver.work_item_id):
                    raise OriginDenied("receiver credential belongs to another Work attempt")
                attempt_ref = WorkAttemptRef(
                    work_item_id=executor.work_item_id,
                    attempt_id=executor.attempt_id,
                    generation=executor.generation,
                )
                receiver_subject_ref = OriginSubjectRef(
                    subject_id=receiver.receiver_subject_id,
                    kind="receiver",
                    revision=receiver.receiver_subject_revision,
                )
                receiver_authorization_ref = OriginAuthorizationRef(
                    subject_id=receiver.receiver_subject_id,
                    origin_kind="receiver",
                    revision=receiver.receiver_authorization_revision,
                )
                attempt = connection.execute(
                    "SELECT state,revision FROM work_attempts WHERE id=? AND generation=?",
                    (attempt_ref.attempt_id, attempt_ref.generation),
                ).fetchone()
                if attempt is None:
                    raise OriginDenied("receiver attempt no longer exists")
                if attempt["state"] == "acknowledged":
                    origin = connection.execute(
                        "SELECT * FROM work_child_origin_bindings "
                        "WHERE child_attempt_id=? AND child_generation=?",
                        (attempt_ref.attempt_id, attempt_ref.generation),
                    ).fetchone()
                    if origin is None:
                        raise OriginDenied("acknowledged receiver lineage is unavailable")
                    self._revalidate_integrity(connection, origin)
                    self._revalidate_replay_authorizations(connection, origin)
                    if (
                        origin["receiver_subject_id"] != receiver.receiver_subject_id
                        or origin["delivery_id"] != receiver.delivery_id
                        or origin["delivery_hash"] != receiver.delivery_hash
                    ):
                        raise OriginDenied("receiver replay differs from its durable delivery")
                    receipt_row = connection.execute(
                        "SELECT 1 FROM work_task_received_receipts "
                        "WHERE attempt_id=? AND generation=?",
                        (attempt_ref.attempt_id, attempt_ref.generation),
                    ).fetchone()
                    acceptance = connection.execute(
                        "SELECT * FROM work_task_receiver_acceptances "
                        "WHERE attempt_id=? AND generation=?",
                        (attempt_ref.attempt_id, attempt_ref.generation),
                    ).fetchone()
                    if receipt_row is None or acceptance is None:
                        raise OriginConflict("acknowledged Work has incomplete receiver evidence")
                    replay = TaskReceivedReceiptV1(
                        attempt=attempt_ref,
                        attempt_revision=attempt["revision"],
                        receiver_subject_ref=receiver_subject_ref,
                        receiver_authorization_ref=receiver_authorization_ref,
                        delivery_id=receiver.delivery_id,
                        delivery_hash=receiver.delivery_hash,
                        nonce="receiver-replay",
                        proof="0" * 64,
                    )
                    self._require_receiver_acceptance(connection, replay, origin)
                    return self.repository._work(connection, attempt_ref.work_item_id)
                if attempt["state"] != "sent":
                    raise OriginDenied("receiver can acknowledge only the current sent attempt")
                origin, live_attempt = self._task_received_origin(
                    connection,
                    attempt_ref=attempt_ref,
                    receiver_subject_ref=receiver_subject_ref,
                    receiver_authorization_ref=receiver_authorization_ref,
                    delivery_id=receiver.delivery_id,
                    delivery_hash=receiver.delivery_hash,
                )
                self._record_task_received_acceptance_in_transaction(
                    connection,
                    attempt_ref=attempt_ref,
                    receiver_subject_ref=receiver_subject_ref,
                    receiver_authorization_ref=receiver_authorization_ref,
                    origin=origin,
                )
                receipt = TaskReceivedReceiptV1(
                    attempt=attempt_ref,
                    attempt_revision=live_attempt["revision"],
                    receiver_subject_ref=receiver_subject_ref,
                    receiver_authorization_ref=receiver_authorization_ref,
                    delivery_id=receiver.delivery_id,
                    delivery_hash=receiver.delivery_hash,
                    nonce=uuid4().hex,
                    proof="0" * 64,
                )
                receipt = receipt.model_copy(
                    update={"proof": self._seal_task_receipt(receipt)}
                )
                validated = self.validate_task_received_receipt(connection, receipt)
                return self.repository._record_task_received_receipt(connection, **validated)
        except WorkAttemptCredentialRejected as error:
            raise OriginDenied("receiver acceptance credential is expired or stale") from error

    def record_task_received_acceptance(
        self,
        *,
        authenticated_context,
        attempt_ref,
        receiver_subject_ref,
        receiver_authorization_ref,
    ) -> dict:
        """Persist exact receiver acknowledgement before any Work receipt is issued."""
        context = self._context(authenticated_context)
        attempt_ref = self._ref(attempt_ref, WorkAttemptRef, "receiver acceptance attempt")
        receiver_subject_ref = self._ref(
            receiver_subject_ref, OriginSubjectRef, "receiver acceptance subject"
        )
        receiver_authorization_ref = self._ref(
            receiver_authorization_ref,
            OriginAuthorizationRef,
            "receiver acceptance authorization",
        )
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            try:
                self.origin_authority._registered(connection, context.requester)
                origin, _ = self._task_received_origin(
                    connection,
                    attempt_ref=attempt_ref,
                    receiver_subject_ref=receiver_subject_ref,
                    receiver_authorization_ref=receiver_authorization_ref,
                )
            except (AuthorityDenied, OriginDenied) as error:
                raise OriginDenied("receiver acceptance is not currently authorized") from error
            if (
                context.requester.id != origin["receiver_subject_id"]
                or context.requester.id != receiver_subject_ref.subject_id
            ):
                raise OriginDenied("receiver acceptance requires the exact authenticated receiver")
            digest = self._receiver_acceptance_hash(
                attempt_ref=attempt_ref,
                receiver_subject_ref=receiver_subject_ref,
                receiver_authorization_ref=receiver_authorization_ref,
                delivery_id=origin["delivery_id"],
                delivery_hash=origin["delivery_hash"],
            )
            prior = connection.execute(
                "SELECT * FROM work_task_receiver_acceptances "
                "WHERE attempt_id=? AND generation=?",
                (attempt_ref.attempt_id, attempt_ref.generation),
            ).fetchone()
            expected = (
                receiver_subject_ref.subject_id,
                receiver_subject_ref.revision,
                receiver_authorization_ref.revision,
                origin["delivery_id"],
                origin["delivery_hash"],
                digest,
            )
            if prior is not None:
                actual = (
                    prior["receiver_subject_id"],
                    prior["receiver_subject_revision"],
                    prior["receiver_authorization_revision"],
                    prior["delivery_id"],
                    prior["delivery_hash"],
                    prior["acceptance_sha256"],
                )
                if actual != expected:
                    raise OriginConflict("receiver acceptance replay contradicts durable evidence")
                return dict(prior)
            connection.execute(
                """INSERT INTO work_task_receiver_acceptances
                   (attempt_id,generation,receiver_subject_id,receiver_subject_revision,
                    receiver_authorization_revision,delivery_id,delivery_hash,
                    acceptance_sha256,accepted_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    attempt_ref.attempt_id,
                    attempt_ref.generation,
                    *expected[:5],
                    digest,
                    time.time(),
                ),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM work_task_receiver_acceptances "
                    "WHERE attempt_id=? AND generation=?",
                    (attempt_ref.attempt_id, attempt_ref.generation),
                ).fetchone()
            )

    def _require_receiver_acceptance(self, connection, receipt, origin) -> None:
        acceptance = connection.execute(
            "SELECT * FROM work_task_receiver_acceptances WHERE attempt_id=? AND generation=?",
            (receipt.attempt.attempt_id, receipt.attempt.generation),
        ).fetchone()
        expected_hash = self._receiver_acceptance_hash(
            attempt_ref=receipt.attempt,
            receiver_subject_ref=receipt.receiver_subject_ref,
            receiver_authorization_ref=receipt.receiver_authorization_ref,
            delivery_id=origin["delivery_id"],
            delivery_hash=origin["delivery_hash"],
        )
        if acceptance is None or (
            acceptance["receiver_subject_id"],
            acceptance["receiver_subject_revision"],
            acceptance["receiver_authorization_revision"],
            acceptance["delivery_id"],
            acceptance["delivery_hash"],
            acceptance["acceptance_sha256"],
        ) != (
            receipt.receiver_subject_ref.subject_id,
            receipt.receiver_subject_ref.revision,
            receipt.receiver_authorization_ref.revision,
            origin["delivery_id"],
            origin["delivery_hash"],
            expected_hash,
        ):
            raise OriginDenied("task receipt requires exact durable receiver acceptance")

    def issue_task_received_receipt(
        self,
        *,
        authenticated_context,
        attempt_ref,
        receiver_subject_ref,
        receiver_authorization_ref,
        nonce,
    ) -> TaskReceivedReceiptV1:
        """Issue a runtime-sealed receipt after server authentication only.

        This is internal server composition, not a route or a client capability:
        an arbitrary body cannot manufacture the runtime proof, and acceptance
        always rechecks the current durable grant chain.
        """
        context = self._context(authenticated_context)
        attempt_ref = self._ref(attempt_ref, WorkAttemptRef, "task receipt attempt")
        receiver_subject_ref = self._ref(
            receiver_subject_ref, OriginSubjectRef, "task receipt receiver subject"
        )
        receiver_authorization_ref = self._ref(
            receiver_authorization_ref,
            OriginAuthorizationRef,
            "task receipt receiver authorization",
        )
        nonce = _identity(nonce, "task receipt nonce")
        with self.repository.read_snapshot() as connection:
            self.origin_authority._registered(connection, context.requester)
            receiver = self._context_subject(context, receiver_subject_ref.subject_id)
            origin, attempt = self._task_received_origin(
                connection,
                attempt_ref=attempt_ref,
                receiver_subject_ref=receiver_subject_ref,
                receiver_authorization_ref=receiver_authorization_ref,
            )
            if (
                context.requester.id != origin["requester_principal_id"]
                or receiver.id != origin["receiver_subject_id"]
            ):
                raise OriginDenied("task receipt identities are not authenticated for this lineage")
            provisional = TaskReceivedReceiptV1(
                attempt=attempt_ref,
                attempt_revision=attempt["revision"],
                receiver_subject_ref=receiver_subject_ref,
                receiver_authorization_ref=receiver_authorization_ref,
                delivery_id=origin["delivery_id"],
                delivery_hash=origin["delivery_hash"],
                nonce=nonce,
                proof="0" * 64,
            )
            self._require_receiver_acceptance(connection, provisional, origin)
            return provisional.model_copy(update={"proof": self._seal_task_receipt(provisional)})

    def validate_task_received_receipt(
        self, connection: sqlite3.Connection, receipt
    ) -> dict:
        """Validate a sealed receipt in the caller's ACK transaction."""
        if not connection.in_transaction:
            raise ValueError("caller-owned transaction required")
        if not isinstance(receipt, TaskReceivedReceiptV1) or not hmac.compare_digest(
            receipt.proof, self._seal_task_receipt(receipt)
        ):
            raise OriginDenied("task receipt was not issued by this authenticated server runtime")
        origin, attempt = self._task_received_origin(
            connection,
            attempt_ref=receipt.attempt,
            receiver_subject_ref=receipt.receiver_subject_ref,
            receiver_authorization_ref=receipt.receiver_authorization_ref,
            attempt_revision=receipt.attempt_revision,
            delivery_id=receipt.delivery_id,
            delivery_hash=receipt.delivery_hash,
        )
        self._require_receiver_acceptance(connection, receipt, origin)
        return {
            "attempt_id": receipt.attempt.attempt_id,
            "generation": receipt.attempt.generation,
            "attempt_revision": receipt.attempt_revision,
            "work_item_id": receipt.attempt.work_item_id,
            "receiver_subject_id": origin["receiver_subject_id"],
            "receiver_subject_revision": origin["receiver_subject_revision"],
            "receiver_authorization_kind": origin["receiver_authorization_kind"],
            "receiver_authorization_revision": origin["receiver_authorization_revision"],
            "receiver_grant_id": origin["receiver_grant_id"],
            "receiver_grant_revision": origin["receiver_grant_revision"],
            "delivery_id": origin["delivery_id"],
            "delivery_hash": origin["delivery_hash"],
            "nonce": receipt.nonce,
            "receipt_hash": receipt.fingerprint(),
        }

    def _authorization(
        self,
        connection,
        *,
        subject_ref: OriginSubjectRef,
        authorization_ref: OriginAuthorizationRef,
        expected_kind: str,
        action: str,
        job_id: str,
    ):
        if (
            subject_ref.kind != expected_kind
            or authorization_ref.subject_id != subject_ref.subject_id
            or authorization_ref.origin_kind != expected_kind
        ):
            raise OriginDenied("managed lineage references contradict their expected role")
        authorization = self._authorization_grant(
            connection,
            subject_id=subject_ref.subject_id,
            origin_kind=expected_kind,
            revision=authorization_ref.revision,
        )
        try:
            return self.authority._revalidate_origin_authorization(
                connection,
                subject_id=subject_ref.subject_id,
                subject_revision=subject_ref.revision,
                authorization_kind=expected_kind,
                authorization_revision=authorization_ref.revision,
                grant_id=authorization["grant_id"],
                grant_revision=authorization["grant_revision"],
                job_id=job_id,
                action=action,
            )
        except (AuthorityDenied, KeyError, TypeError, ValueError) as error:
            raise OriginDenied("managed lineage grant chain is not live") from error

    @staticmethod
    def _authorization_grant(connection, *, subject_id: str, origin_kind: str, revision: int):
        """Read only the exact persisted grant pair selected by the sealed ref."""
        authorization = connection.execute(
            "SELECT grant_id,grant_revision FROM work_origin_authorizations "
            "WHERE subject_id=? AND origin_kind=? AND revision=?",
            (subject_id, origin_kind, revision),
        ).fetchone()
        if authorization is None:
            raise OriginDenied("managed lineage authorization disappeared")
        return authorization

    def _resolve(
        self,
        connection,
        *,
        authenticated_context,
        parent_attempt_ref,
        child_subject_ref,
        child_authorization_ref,
        receiver_subject_ref,
        receiver_authorization_ref,
        intent,
        idempotency_key,
        kind,
    ) -> _ManagedLineageHandoff:
        context = self._context(authenticated_context)
        parent_attempt_ref = self._ref(parent_attempt_ref, WorkAttemptRef, "parent attempt")
        child_subject_ref = self._ref(child_subject_ref, OriginSubjectRef, "child subject")
        child_authorization_ref = self._ref(
            child_authorization_ref, OriginAuthorizationRef, "child authorization"
        )
        receiver_subject_ref = self._ref(receiver_subject_ref, OriginSubjectRef, "receiver subject")
        receiver_authorization_ref = self._ref(
            receiver_authorization_ref, OriginAuthorizationRef, "receiver authorization"
        )
        intent = self._intent(intent)
        idempotency_key = _identity(idempotency_key, "idempotency key")
        kind = self._kind(kind)
        if context.credential_sha256 is not None:
            from cli_agent_orchestrator.services.work_attempt_credential import (
                WorkAttemptCredentialRejected,
                WorkAttemptCredentials,
            )

            try:
                credential = WorkAttemptCredentials(self.repository).authenticate_digest_in_transaction(
                    connection, context.credential_sha256
                )
            except WorkAttemptCredentialRejected as error:
                raise OriginDenied("attempt credential authority is no longer live") from error
            if (
                (credential.attempt_id, credential.generation, credential.work_item_id)
                != (
                    parent_attempt_ref.attempt_id,
                    parent_attempt_ref.generation,
                    parent_attempt_ref.work_item_id,
                )
                or credential.principal_id != context.requester.id
            ):
                raise OriginDenied("attempt credential does not authorize this parent attempt")
        contract = self.contracts._contract(intent.contract)
        delivery = WorkDeliveries.envelope(intent.delivery, contract.operation_kind)
        intent = intent.model_copy(update={"contract": contract, "delivery": delivery})
        parent = self.repository._work(connection, parent_attempt_ref.work_item_id)
        if parent_attempt_ref.attempt_id not in {item["id"] for item in parent["attempts"]}:
            raise OriginDenied("managed lineage parent attempt is not part of the requested work")
        parent_order = self.contracts._revalidate_order(
            connection,
            parent_attempt_ref.attempt_id,
            generation=parent_attempt_ref.generation,
        )
        if (
            parent_order.work_item_id != parent_attempt_ref.work_item_id
            or parent_order.principal_id != context.requester.id
        ):
            raise OriginDenied("managed lineage requester is not the exact parent executor")
        self.origin_authority._registered(connection, context.requester)
        child_principal = self._context_subject(context, child_subject_ref.subject_id)
        receiver_principal = self._context_subject(context, receiver_subject_ref.subject_id)
        child_subject, child_authorization, child_chain, _ = self._authorization(
            connection,
            subject_ref=child_subject_ref,
            authorization_ref=child_authorization_ref,
            expected_kind="child",
            action="admit_child" if kind == "child" else "admit_handoff",
            job_id=parent_order.job_id,
        )
        receiver_subject, receiver_authorization, _, _ = self._authorization(
            connection,
            subject_ref=receiver_subject_ref,
            authorization_ref=receiver_authorization_ref,
            expected_kind="receiver",
            action="task_received",
            job_id=parent_order.job_id,
        )
        if (
            child_principal.id != child_subject["subject_id"]
            or receiver_principal.id != receiver_subject["subject_id"]
            or not any(
                (grant.id, grant.revision) == (parent_order.grant_id, parent_order.grant_revision)
                for grant in child_chain
            )
            or not self.contracts._permissions(contract).is_subset_of(
                self.contracts._permissions(parent_order.contract)
            )
            or (
                contract.snapshot.id,
                contract.snapshot.delivered_hash,
            )
            != (
                parent_order.contract.snapshot.id,
                parent_order.contract.snapshot.delivered_hash,
            )
        ):
            raise OriginDenied("managed lineage cannot widen parent authority or replace snapshot")
        request_hash = self._request_hash(
            context=context,
            parent_attempt_ref=parent_attempt_ref,
            child_subject_ref=child_subject_ref,
            child_authorization_ref=child_authorization_ref,
            receiver_subject_ref=receiver_subject_ref,
            receiver_authorization_ref=receiver_authorization_ref,
            intent=intent,
            idempotency_key=idempotency_key,
            kind=kind,
        )
        provisional = _ManagedLineageHandoff(
            context=context,
            parent_attempt_ref=parent_attempt_ref,
            child_subject_ref=child_subject_ref,
            child_authorization_ref=child_authorization_ref,
            receiver_subject_ref=receiver_subject_ref,
            receiver_authorization_ref=receiver_authorization_ref,
            intent=intent,
            idempotency_key=idempotency_key,
            kind=kind,
            request_hash=request_hash,
            _origin=self,
            _seal="",
        )
        return _ManagedLineageHandoff(
            **{
                **provisional.__dict__,
                "_seal": self._seal_handoff(provisional),
            }
        )

    def admit(
        self,
        *,
        authenticated_context,
        parent_attempt_ref,
        child_subject_ref,
        child_authorization_ref,
        receiver_subject_ref,
        receiver_authorization_ref,
        intent,
        idempotency_key,
        kind,
    ):
        """Resolve one internal managed lineage handoff without writing work."""
        with self.repository.read_snapshot() as connection:
            return self._resolve(
                connection,
                authenticated_context=authenticated_context,
                parent_attempt_ref=parent_attempt_ref,
                child_subject_ref=child_subject_ref,
                child_authorization_ref=child_authorization_ref,
                receiver_subject_ref=receiver_subject_ref,
                receiver_authorization_ref=receiver_authorization_ref,
                intent=intent,
                idempotency_key=idempotency_key,
                kind=kind,
            )

    def _checked_handoff(self, handoff) -> _ManagedLineageHandoff:
        if (
            not isinstance(handoff, _ManagedLineageHandoff)
            or handoff._origin is not self
            or not hmac.compare_digest(handoff._seal, self._seal_handoff(handoff))
        ):
            raise OriginDenied("managed lineage handoff is not sealed by this runtime")
        self._context(handoff.context)
        return handoff

    @staticmethod
    def _request(handoff: _ManagedLineageHandoff, *, job_id: str, actor_id: str) -> dict:
        contract = handoff.intent.contract
        return {
            "job_id": job_id,
            "operation_kind": contract.operation_kind,
            "idempotency_key": handoff.idempotency_key,
            "request_hash": handoff.request_hash,
            "contract_id": contract.id,
            "snapshot_id": contract.snapshot.id,
            "provider": contract.provider,
            "actor_id": actor_id,
            "lease_seconds": handoff.intent.lease_seconds,
            "parent_work_item_id": handoff.parent_attempt_ref.work_item_id,
        }

    @staticmethod
    def _integrity_fingerprint(origin) -> str:
        try:
            return lineage_integrity_fingerprint(origin)
        except (TypeError, ValueError) as error:
            raise OriginConflict("managed lineage binding cannot be fingerprinted") from error

    def _persist_integrity(self, connection, origin) -> None:
        """Write the v23 companion in the caller's admission transaction only."""
        connection.execute(
            "INSERT INTO work_lineage_integrity "
            "(child_attempt_id,child_generation,schema_version,canonicalization_version,fingerprint) "
            "VALUES (?,?,?,?,?)",
            (
                origin["child_attempt_id"],
                origin["child_generation"],
                1,
                1,
                self._integrity_fingerprint(origin),
            ),
        )

    def _revalidate_integrity(self, connection, origin) -> None:
        integrity = connection.execute(
            "SELECT * FROM work_lineage_integrity WHERE child_attempt_id=? "
            "AND child_generation=?",
            (origin["child_attempt_id"], origin["child_generation"]),
        ).fetchone()
        if integrity is None or (
            integrity["schema_version"],
            integrity["canonicalization_version"],
            integrity["fingerprint"],
        ) != (1, 1, self._integrity_fingerprint(origin)):
            raise OriginConflict("managed lineage integrity is unavailable or contradictory")

    def _revalidate_replay_authorizations(self, connection, origin) -> None:
        """Require the binding's exact grants to remain the selected live grants."""
        try:
            self.authority._revalidate_origin_authorization(
                connection,
                subject_id=origin["child_subject_id"],
                subject_revision=origin["child_subject_revision"],
                authorization_kind=origin["child_authorization_kind"],
                authorization_revision=origin["child_authorization_revision"],
                grant_id=origin["child_grant_id"],
                grant_revision=origin["child_grant_revision"],
                job_id=origin["job_id"],
                action="admit_child" if origin["kind"] == "child" else "admit_handoff",
            )
            self.authority._revalidate_origin_authorization(
                connection,
                subject_id=origin["receiver_subject_id"],
                subject_revision=origin["receiver_subject_revision"],
                authorization_kind=origin["receiver_authorization_kind"],
                authorization_revision=origin["receiver_authorization_revision"],
                grant_id=origin["receiver_grant_id"],
                grant_revision=origin["receiver_grant_revision"],
                job_id=origin["job_id"],
                action="task_received",
            )
        except AuthorityDenied as error:
            raise OriginConflict(
                "managed lineage replay grant no longer matches its authorization"
            ) from error

    def _replay(self, connection, admission, handoff: _ManagedLineageHandoff):
        row = connection.execute(
            "SELECT * FROM work_child_origin_bindings WHERE requester_principal_id=? "
            "AND parent_attempt_id=? AND parent_generation=? AND kind=? AND idempotency_key=?",
            (
                handoff.context.requester.id,
                handoff.parent_attempt_ref.attempt_id,
                handoff.parent_attempt_ref.generation,
                handoff.kind,
                handoff.idempotency_key,
            ),
        ).fetchone()
        if row is None:
            conflicting = connection.execute(
                "SELECT id FROM work_items WHERE job_id=(SELECT job_id FROM work_items WHERE id=?) "
                "AND operation_kind=? AND idempotency_key=?",
                (
                    handoff.parent_attempt_ref.work_item_id,
                    handoff.intent.contract.operation_kind,
                    handoff.idempotency_key,
                ),
            ).fetchone()
            if conflicting is not None:
                raise OriginConflict("lineage key names an operation without matching origin")
            return None
        expected = (
            1,
            handoff.kind,
            handoff.parent_attempt_ref.work_item_id,
            handoff.parent_attempt_ref.attempt_id,
            handoff.parent_attempt_ref.generation,
            handoff.child_subject_ref.subject_id,
            handoff.child_subject_ref.revision,
            handoff.child_authorization_ref.revision,
            handoff.intent.contract.canonical_hash(),
            handoff.intent.contract.snapshot.id,
            handoff.intent.contract.snapshot.delivered_hash,
            handoff.receiver_subject_ref.subject_id,
            handoff.receiver_subject_ref.revision,
            handoff.receiver_authorization_ref.revision,
            handoff.request_hash,
            handoff.idempotency_key,
            handoff.intent.native_child_id,
            handoff.intent.terminal_id,
            handoff.intent.handoff_id,
        )
        actual = (
            row["schema_version"],
            row["kind"],
            row["parent_work_item_id"],
            row["parent_attempt_id"],
            row["parent_generation"],
            row["child_subject_id"],
            row["child_subject_revision"],
            row["child_authorization_revision"],
            row["child_contract_hash"],
            row["snapshot_id"],
            row["snapshot_hash"],
            row["receiver_subject_id"],
            row["receiver_subject_revision"],
            row["receiver_authorization_revision"],
            row["request_hash"],
            row["idempotency_key"],
            row["native_child_id"],
            row["terminal_id"],
            row["handoff_id"],
        )
        if actual != expected:
            raise OriginConflict("managed lineage key conflicts with durable origin")
        self._revalidate_integrity(connection, row)
        self._revalidate_replay_authorizations(connection, row)
        work = self.repository._work(connection, row["child_work_item_id"])
        marker = connection.execute(
            "SELECT lineage_protocol FROM work_items WHERE id=?", (work["id"],)
        ).fetchone()
        if marker is None or marker["lineage_protocol"] != "managed-v1":
            raise OriginConflict("managed lineage replay has no protocol marker")
        return work

    def _persist(self, connection, admission, handoff: _ManagedLineageHandoff, prepared_delivery):
        parent = self.repository._work(connection, handoff.parent_attempt_ref.work_item_id)
        request = self._request(
            handoff, job_id=parent["job_id"], actor_id=handoff.context.requester.id
        )
        work = self.repository._admit_work(connection, **request)
        original = work["attempts"][0]
        marker = connection.execute(
            "UPDATE work_items SET lineage_protocol='managed-v1' "
            "WHERE id=? AND lineage_protocol='legacy'",
            (work["id"],),
        )
        if marker.rowcount != 1:
            raise OriginConflict("managed lineage protocol marker changed during admission")
        child_principal = self._context_subject(
            handoff.context, handoff.child_subject_ref.subject_id
        )
        authorization = connection.execute(
            "SELECT * FROM work_origin_authorizations WHERE subject_id=? AND origin_kind='child' "
            "AND revision=?",
            (handoff.child_subject_ref.subject_id, handoff.child_authorization_ref.revision),
        ).fetchone()
        if authorization is None:
            raise OriginConflict("managed lineage child authorization disappeared")
        binding = admission.contracts._bind(
            connection,
            principal=child_principal,
            attempt_id=original["id"],
            generation=original["generation"],
            expected_attempt_revision=original["revision"],
            grant_id=authorization["grant_id"],
            expected_grant_revision=authorization["grant_revision"],
            contract=handoff.intent.contract,
        )
        admission.deliveries._bind(
            connection,
            binding,
            prepared_delivery,
            request=handoff.intent.delivery,
        )
        delivery = connection.execute(
            "SELECT delivery_hash FROM work_delivery_orders WHERE attempt_id=? AND generation=?",
            (binding.attempt_id, binding.generation),
        ).fetchone()
        if delivery is None:
            raise OriginConflict("managed lineage has no durable delivery order")
        parent_binding = admission.contracts._revalidate_order(
            connection,
            handoff.parent_attempt_ref.attempt_id,
            generation=handoff.parent_attempt_ref.generation,
        )
        receiver = connection.execute(
            "SELECT * FROM work_origin_authorizations WHERE subject_id=? AND origin_kind='receiver' "
            "AND revision=?",
            (handoff.receiver_subject_ref.subject_id, handoff.receiver_authorization_ref.revision),
        ).fetchone()
        if receiver is None:
            raise OriginConflict("managed lineage receiver authorization disappeared")
        delivery_id = f"{binding.attempt_id}:{binding.generation}"
        connection.execute(
            """INSERT INTO work_child_origin_bindings
               (child_attempt_id,child_generation,schema_version,kind,job_id,parent_work_item_id,
                parent_attempt_id,parent_generation,child_work_item_id,requester_principal_id,
                executor_principal_id,child_subject_id,child_subject_revision,
                child_authorization_kind,child_authorization_revision,child_grant_id,
                child_grant_revision,parent_contract_hash,child_contract_hash,snapshot_id,
                snapshot_hash,receiver_subject_id,receiver_subject_revision,
                receiver_authorization_kind,receiver_authorization_revision,receiver_grant_id,
                receiver_grant_revision,delivery_id,delivery_hash,request_hash,idempotency_key,
                native_child_id,terminal_id,handoff_id,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                binding.attempt_id,
                binding.generation,
                1,
                handoff.kind,
                binding.job_id,
                handoff.parent_attempt_ref.work_item_id,
                handoff.parent_attempt_ref.attempt_id,
                handoff.parent_attempt_ref.generation,
                binding.work_item_id,
                handoff.context.requester.id,
                parent_binding.principal_id,
                handoff.child_subject_ref.subject_id,
                handoff.child_subject_ref.revision,
                "child",
                handoff.child_authorization_ref.revision,
                authorization["grant_id"],
                authorization["grant_revision"],
                parent_binding.contract_hash,
                binding.contract_hash,
                binding.contract.snapshot.id,
                binding.contract.snapshot.delivered_hash,
                handoff.receiver_subject_ref.subject_id,
                handoff.receiver_subject_ref.revision,
                "receiver",
                handoff.receiver_authorization_ref.revision,
                receiver["grant_id"],
                receiver["grant_revision"],
                delivery_id,
                delivery["delivery_hash"],
                handoff.request_hash,
                handoff.idempotency_key,
                handoff.intent.native_child_id,
                handoff.intent.terminal_id,
                handoff.intent.handoff_id,
                time.time(),
            ),
        )
        origin = connection.execute(
            "SELECT * FROM work_child_origin_bindings WHERE child_attempt_id=? "
            "AND child_generation=?",
            (binding.attempt_id, binding.generation),
        ).fetchone()
        if origin is None:
            raise OriginConflict("managed lineage binding disappeared during admission")
        self._persist_integrity(connection, origin)
        connection.execute(
            "INSERT INTO work_lineage_projection_intents VALUES (?,?,?,?,?)",
            (binding.attempt_id, binding.generation, 1, "planned", time.time()),
        )
        admission.scheduler._enqueue(
            connection,
            attempt_id=original["id"],
            generation=original["generation"],
            expected_attempt_revision=original["revision"],
            units=binding.contract.resources.units,
            dependencies=binding.contract.resources.dependencies,
            actor_id=handoff.context.requester.id,
        )
        return self.repository._work(connection, work["id"])

    def _consume(self, admission, handoff):
        """WorkAdmission's private managed path; all rows commit in one transaction."""
        handoff = self._checked_handoff(handoff)
        if admission.repository is not self.repository:
            raise OriginDenied("managed lineage handoff belongs to another store")
        resolution = dict(
            authenticated_context=handoff.context,
            parent_attempt_ref=handoff.parent_attempt_ref,
            child_subject_ref=handoff.child_subject_ref,
            child_authorization_ref=handoff.child_authorization_ref,
            receiver_subject_ref=handoff.receiver_subject_ref,
            receiver_authorization_ref=handoff.receiver_authorization_ref,
            intent=handoff.intent,
            idempotency_key=handoff.idempotency_key,
            kind=handoff.kind,
        )
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            current = self._resolve(connection, **resolution)
            if not hmac.compare_digest(current._seal, handoff._seal):
                raise OriginDenied("managed lineage handoff changed before admission")
            replay = self._replay(connection, admission, handoff)
            if replay is not None:
                return replay
        prepared_delivery = admission.deliveries.prepare(
            handoff.intent.delivery,
            handoff.intent.contract.operation_kind,
            contract=handoff.intent.contract,
        )
        admission._preflight(handoff.intent.contract)
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            current = self._resolve(connection, **resolution)
            if not hmac.compare_digest(current._seal, handoff._seal):
                raise OriginDenied("managed lineage handoff changed before final admission")
            replay = self._replay(connection, admission, handoff)
            if replay is not None:
                return replay
            return self._persist(connection, admission, handoff, prepared_delivery)
