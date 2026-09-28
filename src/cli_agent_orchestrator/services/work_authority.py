"""Durable, deny-by-default work authority; this does not provide backend sandboxing."""

import hashlib
import json
import math
import sqlite3
import time
from dataclasses import dataclass, fields, replace
from pathlib import Path
from uuid import uuid4

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security.auth import (
    SCOPE_ADMIN,
    SCOPE_WRITE,
    Principal,
    is_verified_principal,
)

_ORIGIN_ACTIONS = frozenset(
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


class AuthorityDenied(PermissionError):
    """A verified principal or durable chain does not authorize the requested effect."""


class GrantConflict(ValueError):
    """The supplied grant revision or immutable issuance conflicts with the store."""


@dataclass(frozen=True)
class OfflineCutLease:
    """Opaque, durable server lease for registered Work writers only."""

    id: str
    store_identity: str
    operator_principal_id: str
    owner: str
    scope: str
    epoch: int
    fence: int
    expires_at: float
    revision: int


def public_offline_store_identity(value: str) -> str:
    """One-way manifest/ledger projection; only the server retains the path."""
    if not isinstance(value, str) or not value:
        raise AuthorityDenied("offline cut store identity is invalid")
    return hashlib.sha256(b"t069/offline-cut-store/v1\0" + value.encode("utf-8")).hexdigest()


def _tokens(values) -> frozenset[str]:
    if not isinstance(values, (set, frozenset, list, tuple)) or any(
        not isinstance(value, str) or not value or value != value.strip() or len(value) > 4096
        for value in values
    ):
        raise ValueError(
            "permissions and providers must be explicit collections of nonempty strings"
        )
    return frozenset(values)


@dataclass(frozen=True)
class Permissions:
    """Exact tool/command/network/artifact capabilities and bounded absolute paths.

    Empty means no authority. There are no implicit wildcard or None privileges.
    Input paths are canonicalized by issuance/authorization, while stored path
    boundaries stay frozen rather than following later symlink retargeting.
    """

    tools: frozenset[str] = frozenset()
    paths: frozenset[str] = frozenset()
    commands: frozenset[str] = frozenset()
    network: frozenset[str] = frozenset()
    artifacts: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for field in fields(self):
            object.__setattr__(self, field.name, _tokens(getattr(self, field.name)))
        if any(not Path(path).is_absolute() for path in self.paths):
            raise ValueError("permission paths must be absolute")

    def is_subset_of(self, parent: "Permissions") -> bool:
        return all(
            getattr(self, name) <= getattr(parent, name)
            for name in ("tools", "commands", "network", "artifacts")
        ) and all(
            any(Path(path).is_relative_to(Path(root)) for root in parent.paths)
            for path in self.paths
        )

    def as_dict(self) -> dict[str, list[str]]:
        return {field.name: sorted(getattr(self, field.name)) for field in fields(self)}


def _canonical_permissions(value: Permissions) -> Permissions:
    if not isinstance(value, Permissions):
        raise ValueError("explicit Permissions required")
    return replace(value, paths=frozenset(str(Path(path).resolve()) for path in value.paths))


@dataclass(frozen=True)
class Grant:
    id: str
    revision: int
    job_id: str
    principal_id: str
    parent_grant_id: str | None
    parent_revision: int | None
    providers: frozenset[str]
    permissions: Permissions
    expires_at: float
    enforcement_level: str = "cooperative"


class WorkAuthority:
    """Fresh authority checks against an already initialized, verified work repository.

    Call ``authorize`` immediately before each effect; its return value is not a
    lease or a cached authorization. Backend capability/enforcement checks remain
    necessary. This foundation does not execute effects or claim to sandbox them.
    """

    def __init__(self, repository: WorkRepository):
        self.repository = repository

    @staticmethod
    def _principal(principal: Principal, *, admin: bool = False) -> None:
        if (
            not is_verified_principal(principal)
            or not getattr(principal, "id", None)
            or not getattr(principal, "scopes", frozenset()) & {SCOPE_WRITE, SCOPE_ADMIN}
            or (admin and SCOPE_ADMIN not in principal.scopes)
        ):
            raise AuthorityDenied("verified principal lacks required authority scope")

    @classmethod
    def _offline_cut_operator(cls, principal: Principal) -> None:
        """Cuts require a token-authenticated operator, never local fallback identity."""
        cls._principal(principal, admin=True)
        if principal.kind != "jwt":
            raise AuthorityDenied("offline cut requires an authenticated operator principal")

    @staticmethod
    def _offline_cut_lease(value: dict) -> OfflineCutLease:
        try:
            return OfflineCutLease(
                **{key: value[key] for key in OfflineCutLease.__dataclass_fields__}
            )
        except (KeyError, TypeError, ValueError) as error:
            raise AuthorityDenied("offline cut lease is invalid") from error

    def create_offline_cut(self, principal: Principal, *, ttl_seconds: float) -> OfflineCutLease:
        """Create one internal cut; only a verified admin principal may own it."""
        self._offline_cut_operator(principal)
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self._register(connection, principal)
            return self._offline_cut_lease(
                self.repository._acquire_offline_cut(
                    connection, operator_principal_id=principal.id, ttl=ttl_seconds
                )
            )

    def verify_offline_cut(
        self, lease: OfflineCutLease, *, source_database: Path
    ) -> OfflineCutLease:
        """Revalidate the exact stored lease in a new serialized transaction."""
        if (
            not isinstance(lease, OfflineCutLease)
            or Path(source_database).resolve() != self.repository.path.resolve()
        ):
            raise AuthorityDenied("offline cut must name this server-owned work store")
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            return self._offline_cut_lease(
                self.repository._verify_offline_cut(connection, lease.__dict__)
            )

    def revoke_offline_cut(self, principal: Principal, lease: OfflineCutLease) -> None:
        self._offline_cut_operator(principal)
        if not isinstance(lease, OfflineCutLease) or principal.id != lease.operator_principal_id:
            raise AuthorityDenied("only the authenticated cut operator may revoke it")
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self.repository._revoke_offline_cut(connection, lease.__dict__)

    def _observe_cut(
        self,
        connection: sqlite3.Connection,
        lease: dict,
        *,
        capture_id: str,
        phase: str,
        inventory: dict,
    ) -> dict:
        if not isinstance(inventory, dict) or set(inventory) != {"fingerprint", "profile_version"}:
            raise AuthorityDenied("offline cut inventory observation is invalid")
        observation = {
            "lease": {
                key: lease[key]
                for key in (
                    "id",
                    "store_identity",
                    "operator_principal_id",
                    "owner",
                    "scope",
                    "epoch",
                    "fence",
                    "expires_at",
                    "revision",
                )
            },
            "writer_count": 0,
            "inventory": inventory,
            "observed_at": time.time(),
        }
        observation["lease"]["store_identity"] = public_offline_store_identity(
            lease["store_identity"]
        )
        stored = self.repository._observe_offline_cut(
            connection,
            lease,
            capture_id=capture_id,
            phase=phase,
            observation=json.dumps(
                observation, sort_keys=True, separators=(",", ":"), allow_nan=False
            ),
        )
        return json.loads(stored["observation"])

    def observe_offline_cut(
        self,
        lease: OfflineCutLease,
        *,
        source_database: Path,
        capture_id: str,
        phase: str,
        inventory: dict,
    ) -> dict:
        if (
            not isinstance(lease, OfflineCutLease)
            or Path(source_database).resolve() != self.repository.path.resolve()
        ):
            raise AuthorityDenied("offline cut observation must name this server-owned work store")
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            checked = self.repository._verify_offline_cut(connection, lease.__dict__)
            return self._observe_cut(
                connection, checked, capture_id=capture_id, phase=phase, inventory=inventory
            )

    def publish_offline_cut(
        self,
        lease: OfflineCutLease,
        *,
        source_database: Path,
        capture_id: str,
        inventory: dict,
        publish,
    ) -> OfflineCutLease:
        """Serialize final fence verification, private promotion, fsync and receipt eligibility."""
        if (
            not isinstance(lease, OfflineCutLease)
            or Path(source_database).resolve() != self.repository.path.resolve()
            or not callable(publish)
        ):
            raise AuthorityDenied("offline cut publication must name this server-owned work store")
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            checked = self.repository._verify_offline_cut(connection, lease.__dict__)
            promote_observation = self._observe_cut(
                connection, checked, capture_id=capture_id, phase="promote", inventory=inventory
            )
            publication = publish(self._offline_cut_lease(checked), promote_observation)
            if not isinstance(publication, dict) or set(publication) != {
                "bundle_path",
                "manifest_digest",
                "manifest_size",
            }:
                raise AuthorityDenied("offline cut publication is invalid")
            # The filesystem work above ran under the BEGIN IMMEDIATE lock.  This
            # second check catches TTL expiry before the durable publication CAS.
            self.repository._verify_offline_cut(connection, lease.__dict__)
            return self._offline_cut_lease(
                self.repository._publish_offline_cut(
                    connection, lease.__dict__, capture_id=capture_id, **publication
                )
            )

    def record_offline_cut_rejection(self, lease: OfflineCutLease, *, phase: str) -> None:
        if not isinstance(lease, OfflineCutLease):
            raise AuthorityDenied("offline cut lease is invalid")
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self.repository._record_offline_cut_rejection(
                connection, lease_id=lease.id, phase=phase
            )

    @staticmethod
    def _register(connection: sqlite3.Connection, principal: Principal) -> None:
        if not isinstance(principal, Principal) or not getattr(principal, "id", None):
            raise AuthorityDenied("verified recipient principal required")
        connection.execute(
            "INSERT OR IGNORE INTO work_principals(id,issuer,subject,kind,created_at) VALUES (?,?,?,?,?)",
            (principal.id, principal.issuer, principal.subject, principal.kind, time.time()),
        )
        stored = connection.execute(
            "SELECT issuer,subject,kind FROM work_principals WHERE id=?", (principal.id,)
        ).fetchone()
        if stored is None or tuple(stored) != (principal.issuer, principal.subject, principal.kind):
            raise AuthorityDenied("principal identity conflicts with its durable record")

    @staticmethod
    def _expiry(value: float) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError("expiry must be a finite UNIX timestamp")
        if value <= time.time():
            raise AuthorityDenied("grant expiry must be in the future")
        return float(value)

    @staticmethod
    def _load(connection: sqlite3.Connection, grant_id: str, expected_revision: int) -> Grant:
        if type(expected_revision) is not int or expected_revision <= 0:
            raise GrantConflict("expected grant revision must be positive")
        row = connection.execute(
            "SELECT * FROM work_grants WHERE id=? ORDER BY revision DESC LIMIT 1", (grant_id,)
        ).fetchone()
        if row is None:
            raise AuthorityDenied("grant not found")
        if row["revision"] != expected_revision:
            raise GrantConflict("stale grant revision")
        return Grant(
            id=row["id"],
            revision=row["revision"],
            job_id=row["job_id"],
            principal_id=row["principal_id"],
            parent_grant_id=row["parent_grant_id"],
            parent_revision=row["parent_revision"],
            providers=_tokens(json.loads(row["allowed_providers"])),
            permissions=Permissions(**json.loads(row["permissions"])),
            expires_at=row["expires_at"],
            enforcement_level=row["enforcement_level"],
        )

    def _chain(self, connection, grant_id, expected_revision, *, live=True):
        chain: list[Grant] = []
        seen = set()
        while True:
            grant = self._load(connection, grant_id, expected_revision)
            key = (grant.id, grant.revision)
            if key in seen or len(chain) >= 128:
                raise AuthorityDenied("invalid grant ancestry")
            seen.add(key)
            if live and (
                grant.expires_at <= time.time()
                or connection.execute(
                    "SELECT 1 FROM work_grant_revocations WHERE grant_id=? AND grant_revision=?",
                    key,
                ).fetchone()
            ):
                raise AuthorityDenied("grant ancestry is expired or revoked")
            chain.append(grant)
            if grant.parent_grant_id is None:
                break
            grant_id, expected_revision = grant.parent_grant_id, grant.parent_revision
        job = self.repository._job(connection, chain[0].job_id)
        if (
            chain[-1].id != job["grant_id"]
            or chain[-1].principal_id != job["principal_id"]
            or any(grant.job_id != job["id"] for grant in chain)
            or (live and job["state"] in {"revoked", "failed", "completed"})
        ):
            raise AuthorityDenied("grant chain is not bound to an active job")
        for child, parent in zip(chain, chain[1:]):
            if (
                not child.providers <= parent.providers
                or not child.permissions.is_subset_of(parent.permissions)
                or child.expires_at > parent.expires_at
            ):
                raise AuthorityDenied("grant ancestry widens authority")
        return chain, job

    def _revalidate_origin_authorization(
        self,
        connection,
        *,
        subject_id: str,
        subject_revision: int,
        authorization_kind: str,
        authorization_revision: int,
        grant_id: str,
        grant_revision: int,
        job_id: str,
        action: str,
    ):
        """Revalidate one exact durable origin reference without substituting it.

        Both admission and contract revalidation need this read-only fence.  The
        latest lookups prove the stored revision is still current; they never
        select a replacement subject, authorization, grant, or action.
        """
        if (
            any(
                type(value) is not str or not value
                for value in (subject_id, authorization_kind, grant_id, job_id, action)
            )
            or authorization_kind not in {"child", "receiver"}
            or action not in _ORIGIN_ACTIONS
            or any(
                type(value) is not int or value <= 0
                for value in (
                    subject_revision,
                    authorization_revision,
                    grant_revision,
                )
            )
        ):
            raise AuthorityDenied("managed lineage origin reference is invalid")
        subject = connection.execute(
            "SELECT * FROM work_origin_subjects WHERE subject_id=? "
            "ORDER BY revision DESC LIMIT 1",
            (subject_id,),
        ).fetchone()
        authorization = connection.execute(
            "SELECT * FROM work_origin_authorizations WHERE subject_id=? AND origin_kind=? "
            "ORDER BY revision DESC LIMIT 1",
            (subject_id, authorization_kind),
        ).fetchone()
        if (
            subject is None
            or authorization is None
            or subject["schema_version"] != 1
            or subject["origin_kind"] != authorization_kind
            or subject["state"] != "active"
            or subject["revision"] != subject_revision
            or authorization["schema_version"] != 1
            or authorization["origin_kind"] != authorization_kind
            or authorization["state"] != "active"
            or authorization["revision"] != authorization_revision
            or authorization["subject_revision"] != subject_revision
            or authorization["issuer_id"] != subject["issuer_id"]
            or authorization["job_id"] != job_id
            or authorization["grant_id"] != grant_id
            or authorization["grant_revision"] != grant_revision
            or authorization["expires_at"] <= time.time()
        ):
            raise AuthorityDenied("managed lineage authorization is absent, stale, or revoked")
        try:
            actions = json.loads(authorization["actions"])
            chain, job = self._chain(connection, grant_id, grant_revision)
        except (AuthorityDenied, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AuthorityDenied("managed lineage grant chain is not live") from error
        if (
            not isinstance(actions, list)
            or not actions
            or len(actions) != len(set(actions))
            or any(
                not isinstance(candidate, str) or candidate not in _ORIGIN_ACTIONS
                for candidate in actions
            )
            or action not in actions
            or job["id"] != job_id
            or job["principal_id"] != authorization["issuer_id"]
            or chain[0].principal_id != subject_id
        ):
            raise AuthorityDenied("managed lineage authorization no longer permits this action")
        return subject, authorization, chain, job

    def _event(self, connection, grant: Grant, actor: Principal, event_type: str) -> None:
        connection.execute("UPDATE work_jobs SET revision=revision+1 WHERE id=?", (grant.job_id,))
        self.repository._append_event(
            connection,
            job_id=grant.job_id,
            actor_id=actor.id,
            event_type=event_type,
            metadata={"grant_id": grant.id, "grant_revision": grant.revision},
        )

    def _insert(self, connection, grant: Grant, actor: Principal) -> Grant:
        try:
            connection.execute(
                "INSERT INTO work_grants(id,revision,job_id,principal_id,parent_grant_id,parent_revision,allowed_providers,permissions,enforcement_level,expires_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    grant.id,
                    grant.revision,
                    grant.job_id,
                    grant.principal_id,
                    grant.parent_grant_id,
                    grant.parent_revision,
                    json.dumps(sorted(grant.providers)),
                    json.dumps(grant.permissions.as_dict(), sort_keys=True),
                    grant.enforcement_level,
                    grant.expires_at,
                    time.time(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise GrantConflict("grant snapshot already exists or violates ancestry") from exc
        self._event(connection, grant, actor, "grant.issued")
        return grant

    @staticmethod
    def _managed_subject_may_delegate(
        connection: sqlite3.Connection, principal: Principal, parent: Grant, job: dict
    ) -> bool:
        """Keep legacy grants unchanged, but never let a managed subject bypass origin actions."""
        subject = connection.execute(
            """SELECT * FROM work_origin_subjects WHERE subject_id=?
               ORDER BY revision DESC LIMIT 1""",
            (principal.id,),
        ).fetchone()
        if subject is None:
            return True
        if subject["state"] != "active":
            return False
        authorizations = connection.execute(
            """SELECT authorization.* FROM work_origin_authorizations AS authorization
               WHERE authorization.subject_id=?
                 AND authorization.revision=(
                     SELECT max(current.revision) FROM work_origin_authorizations AS current
                     WHERE current.subject_id=authorization.subject_id
                       AND current.origin_kind=authorization.origin_kind
                 )""",
            (principal.id,),
        ).fetchall()
        for authorization in authorizations:
            try:
                actions = json.loads(authorization["actions"])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if (
                authorization["state"] == "active"
                and authorization["subject_revision"] == subject["revision"]
                and authorization["origin_kind"] == subject["origin_kind"]
                and authorization["issuer_id"] == subject["issuer_id"]
                and authorization["issuer_id"] == job["principal_id"]
                and authorization["job_id"] == job["id"]
                and authorization["grant_id"] == parent.id
                and authorization["grant_revision"] == parent.revision
                and authorization["expires_at"] > time.time()
                and isinstance(actions, list)
                and actions
                and len(actions) == len(set(actions))
                and all(isinstance(action, str) and action in _ORIGIN_ACTIONS for action in actions)
                and "delegate" in actions
            ):
                return True
        return False

    def issue_root(
        self,
        principal: Principal,
        *,
        job_id: str,
        providers,
        permissions: Permissions,
        expires_at: float,
    ) -> Grant:
        self._principal(principal, admin=True)
        providers, permissions, expires_at = (
            _tokens(providers),
            _canonical_permissions(permissions),
            self._expiry(expires_at),
        )
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            job = self.repository._job(connection, job_id)
            if (
                job["principal_id"] != principal.id
                or job["state"] in {"revoked", "failed", "completed"}
                or not providers <= set(job["allowed_providers"])
            ):
                raise AuthorityDenied(
                    "root grant requires job ownership and its provider allowlist"
                )
            self._register(connection, principal)
            return self._insert(
                connection,
                Grant(
                    job["grant_id"],
                    1,
                    job_id,
                    principal.id,
                    None,
                    None,
                    providers,
                    permissions,
                    expires_at,
                ),
                principal,
            )

    def delegate(
        self,
        principal: Principal,
        *,
        parent_grant_id: str,
        expected_parent_revision: int,
        child_principal: Principal,
        providers,
        permissions: Permissions,
        expires_at: float,
    ) -> Grant:
        self._principal(principal)
        providers, permissions, expires_at = (
            _tokens(providers),
            _canonical_permissions(permissions),
            self._expiry(expires_at),
        )
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            chain, job = self._chain(connection, parent_grant_id, expected_parent_revision)
            parent = chain[0]
            if (
                parent.principal_id != principal.id
                or not providers <= parent.providers
                or not providers <= set(job["allowed_providers"])
                or not permissions.is_subset_of(parent.permissions)
                or expires_at > parent.expires_at
            ):
                raise AuthorityDenied("child grant cannot widen its parent's authority")
            if not self._managed_subject_may_delegate(connection, principal, parent, job):
                raise AuthorityDenied("managed origin subject lacks a live delegate authorization")
            self._register(connection, child_principal)
            return self._insert(
                connection,
                Grant(
                    uuid4().hex,
                    1,
                    parent.job_id,
                    child_principal.id,
                    parent.id,
                    parent.revision,
                    providers,
                    permissions,
                    expires_at,
                ),
                principal,
            )

    def authorize(
        self,
        principal: Principal,
        *,
        job_id: str,
        grant_id: str,
        expected_grant_revision: int,
        provider: str,
        requested_permissions: Permissions,
    ) -> Grant:
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            return self._authorize(
                connection,
                principal,
                job_id=job_id,
                grant_id=grant_id,
                expected_grant_revision=expected_grant_revision,
                provider=provider,
                requested_permissions=requested_permissions,
            )

    def _authorize(
        self,
        connection,
        principal: Principal,
        *,
        job_id: str,
        grant_id: str,
        expected_grant_revision: int,
        provider: str,
        requested_permissions: Permissions,
    ) -> Grant:
        """Caller owns verified BEGIN IMMEDIATE; this helper never commits."""
        if not connection.in_transaction:
            raise ValueError("a caller-owned transaction is required")
        self._principal(principal)
        requested_permissions = _canonical_permissions(requested_permissions)
        chain, job = self._chain(connection, grant_id, expected_grant_revision)
        effective_providers = set(job["allowed_providers"])
        for grant in chain:
            effective_providers.intersection_update(grant.providers)
        if (
            job_id != job["id"]
            or chain[0].principal_id != principal.id
            or provider not in effective_providers
            or not all(requested_permissions.is_subset_of(grant.permissions) for grant in chain)
        ):
            raise AuthorityDenied("effect exceeds the principal's durable grant")
        return chain[0]

    def revoke(
        self, principal: Principal, *, grant_id: str, expected_grant_revision: int, reason: str
    ) -> None:
        self._principal(principal)
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 4096:
            raise ValueError("revocation requires a bounded reason")
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            chain, _ = self._chain(connection, grant_id, expected_grant_revision, live=False)
            if principal.id not in {grant.principal_id for grant in chain}:
                raise AuthorityDenied("only grant owner or an ancestor may revoke")
            prior = connection.execute(
                "SELECT actor_principal_id,reason FROM work_grant_revocations WHERE grant_id=? AND grant_revision=?",
                (grant_id, expected_grant_revision),
            ).fetchone()
            if prior:
                if tuple(prior) != (principal.id, reason):
                    raise GrantConflict("revocation already recorded with different arguments")
                return
            self._register(connection, principal)
            connection.execute(
                "INSERT INTO work_grant_revocations VALUES (?,?,?,?,?)",
                (grant_id, expected_grant_revision, principal.id, time.time(), reason),
            )
            self._event(connection, chain[0], principal, "grant.revoked")
