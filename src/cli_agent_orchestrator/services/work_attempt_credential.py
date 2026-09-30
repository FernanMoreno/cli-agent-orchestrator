"""Issue and revalidate per-attempt CAO bearer credentials (T019)."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import os
import secrets
import sqlite3
import stat
import time
from dataclasses import dataclass, field

from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
from cli_agent_orchestrator.services.work_authority import AuthorityDenied
from cli_agent_orchestrator.services.work_contract import ContractConflict, WorkContracts


class WorkAttemptCredentialRejected(WorkConflict):
    """The credential, attempt, installation, grant or lease is no longer live."""


class WorkAttemptCredentialConflict(WorkConflict):
    """An attempt has already consumed its one credential issue."""


@dataclass(frozen=True, slots=True)
class IssuedWorkAttemptCredential:
    """Raw secret returned only to the trusted dispatcher for private handoff."""

    attempt_id: str
    generation: int
    expires_at: float
    secret: bytes = field(repr=False, compare=False)
    receiver_secret: bytes | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class WorkAttemptCredentialContext:
    """Non-secret exact binding recovered after live credential authentication."""

    attempt_id: str
    generation: int
    job_id: str
    work_item_id: str
    principal_id: str
    grant_id: str
    grant_revision: int
    contract_hash: str
    installation_uuid: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class WorkTaskReceiverCredentialContext:
    """Exact independently authorized receiver for one managed delivery."""

    attempt_id: str
    generation: int
    job_id: str
    work_item_id: str
    receiver_subject_id: str
    receiver_subject_revision: int
    receiver_authorization_revision: int
    receiver_grant_id: str
    receiver_grant_revision: int
    delivery_id: str
    delivery_hash: str
    expires_at: float
    binding_id: str | None = None


class WorkAttemptCredentials:
    """Keep only a digest durable and validate the full current binding per request."""

    def __init__(self, repository: WorkRepository, *, max_ttl_seconds: float = 120.0):
        if not isinstance(repository, WorkRepository):
            raise ValueError("verified work repository required")
        if (
            isinstance(max_ttl_seconds, bool)
            or not isinstance(max_ttl_seconds, (int, float))
            or max_ttl_seconds <= 0
        ):
            raise ValueError("credential lifetime must be positive")
        self.repository = repository
        self.max_ttl_seconds = float(max_ttl_seconds)
        self.contracts = WorkContracts(repository)

    @staticmethod
    def _require_transaction(connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection) or not connection.in_transaction:
            raise WorkAttemptCredentialRejected(
                "credential operation requires a caller transaction"
            )
        main = next(
            (row[2] for row in connection.execute("PRAGMA database_list") if row[1] == "main"),
            None,
        )
        if not main:
            raise WorkAttemptCredentialRejected("credential store is unavailable")

    def issue_in_transaction(
        self,
        connection: sqlite3.Connection,
        binding,
        *,
        expected_attempt_revision: int,
        now: float | None = None,
    ) -> IssuedWorkAttemptCredential:
        """Persist a digest atomically with dispatch intent, before any external effect."""
        self._require_transaction(connection)
        self.repository._verify(connection)
        if type(expected_attempt_revision) is not int or expected_attempt_revision <= 0:
            raise WorkAttemptCredentialRejected("sent attempt revision is required")
        issued_at = time.time() if now is None else now
        if type(issued_at) not in {int, float} or issued_at <= 0:
            raise WorkAttemptCredentialRejected("credential issue time is invalid")
        try:
            current = self.contracts._revalidate_order(
                connection, binding.attempt_id, generation=binding.generation
            )
            recovery = self.repository.assert_execution_allowed(connection)
        except (AuthorityDenied, ContractConflict, KeyError, TypeError, ValueError) as error:
            raise WorkAttemptCredentialRejected(
                "attempt binding is not currently authorized"
            ) from error
        attempt = connection.execute(
            "SELECT state,revision,lease_expires_at FROM work_attempts WHERE id=? AND generation=?",
            (binding.attempt_id, binding.generation),
        ).fetchone()
        if (
            current != binding
            or attempt is None
            or (attempt["state"], attempt["revision"]) != ("sent", expected_attempt_revision)
            or attempt["lease_expires_at"] <= issued_at
        ):
            raise WorkAttemptCredentialRejected("credential requires this exact sent attempt")
        expires_at = min(attempt["lease_expires_at"], issued_at + self.max_ttl_seconds)
        if expires_at <= issued_at:
            raise WorkAttemptCredentialRejected("attempt lease cannot support a credential")
        secret = secrets.token_bytes(32)
        digest = hashlib.sha256(secret).hexdigest()
        receiver_secret = None
        workflow_receiver_binding = None
        receiver_origin = connection.execute(
            "SELECT * FROM work_child_origin_bindings "
            "WHERE child_attempt_id=? AND child_generation=?",
            (binding.attempt_id, binding.generation),
        ).fetchone()
        if receiver_origin is not None:
            from cli_agent_orchestrator.services.work_origin import WorkOrigins

            WorkOrigins(self.repository)._revalidate_replay_authorizations(
                connection, receiver_origin
            )
            receiver_secret = secrets.token_bytes(32)
        else:
            from cli_agent_orchestrator.services.work_origin import WorkOrigins

            workflow_origins = WorkOrigins(self.repository)
            workflow_receiver_binding = workflow_origins._workflow_binding_for_attempt(
                connection,
                attempt_id=binding.attempt_id,
                generation=binding.generation,
                work_item_id=binding.work_item_id,
            )
            if workflow_receiver_binding is not None:
                workflow_origins._require_workflow_receiver_action(
                    connection, workflow_receiver_binding, action="task_received"
                )
                workflow_origins._require_workflow_receiver_action(
                    connection, workflow_receiver_binding, action="task_result"
                )
                receiver_secret = secrets.token_bytes(32)
        try:
            connection.execute(
                """INSERT INTO work_attempt_credentials
                   (attempt_id,generation,job_id,work_item_id,principal_id,grant_id,
                    grant_revision,installation_uuid,contract_hash,attempt_revision,
                    lease_expires_at,expires_at,credential_sha256,issued_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    binding.attempt_id,
                    binding.generation,
                    binding.job_id,
                    binding.work_item_id,
                    binding.principal_id,
                    binding.grant_id,
                    binding.grant_revision,
                    recovery.installation_uuid,
                    binding.contract_hash,
                    expected_attempt_revision,
                    attempt["lease_expires_at"],
                    expires_at,
                    digest,
                    issued_at,
                ),
            )
            if receiver_origin is not None and receiver_secret is not None:
                connection.execute(
                    """INSERT INTO work_task_receiver_credentials
                       (attempt_id,generation,job_id,work_item_id,receiver_subject_id,
                        receiver_subject_revision,receiver_authorization_revision,
                        receiver_grant_id,receiver_grant_revision,installation_uuid,
                        attempt_revision,lease_expires_at,expires_at,delivery_id,
                        delivery_hash,credential_sha256,issued_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        binding.attempt_id,
                        binding.generation,
                        binding.job_id,
                        binding.work_item_id,
                        receiver_origin["receiver_subject_id"],
                        receiver_origin["receiver_subject_revision"],
                        receiver_origin["receiver_authorization_revision"],
                        receiver_origin["receiver_grant_id"],
                        receiver_origin["receiver_grant_revision"],
                        recovery.installation_uuid,
                        expected_attempt_revision,
                        attempt["lease_expires_at"],
                        expires_at,
                        receiver_origin["delivery_id"],
                        receiver_origin["delivery_hash"],
                        hashlib.sha256(receiver_secret).hexdigest(),
                        issued_at,
                    ),
                )
            elif workflow_receiver_binding is not None and receiver_secret is not None:
                self.repository._record_workflow_step_receiver_credential(
                    connection,
                    binding_id=workflow_receiver_binding.binding_id,
                    attempt_id=binding.attempt_id,
                    generation=binding.generation,
                    schema_version=1,
                    job_id=binding.job_id,
                    work_item_id=binding.work_item_id,
                    receiver_subject_id=workflow_receiver_binding.receiver_subject_ref.subject_id,
                    receiver_subject_revision=workflow_receiver_binding.receiver_subject_ref.revision,
                    receiver_authorization_revision=workflow_receiver_binding.receiver_authorization_ref.revision,
                    receiver_grant_id=workflow_receiver_binding.receiver_grant_id,
                    receiver_grant_revision=workflow_receiver_binding.receiver_grant_revision,
                    installation_uuid=recovery.installation_uuid,
                    attempt_revision=expected_attempt_revision,
                    lease_expires_at=attempt["lease_expires_at"],
                    expires_at=expires_at,
                    delivery_id=workflow_receiver_binding.delivery_id,
                    delivery_hash=workflow_receiver_binding.delivery_hash,
                    credential_sha256=hashlib.sha256(receiver_secret).hexdigest(),
                    issued_at=issued_at,
                )
        except sqlite3.IntegrityError as error:
            raise WorkAttemptCredentialConflict(
                "attempt or receiver credential issue is already consumed or invalid"
            ) from error
        return IssuedWorkAttemptCredential(
            attempt_id=binding.attempt_id,
            generation=binding.generation,
            expires_at=expires_at,
            secret=secret,
            receiver_secret=receiver_secret,
        )

    def authenticate_in_transaction(
        self,
        connection: sqlite3.Connection,
        secret: bytes,
        *,
        now: float | None = None,
        allow_finished: bool = False,
    ) -> WorkAttemptCredentialContext:
        """Authenticate the bearer and recheck installation, grant, attempt and lease."""
        self._require_transaction(connection)
        if type(secret) is not bytes or len(secret) != 32:
            raise WorkAttemptCredentialRejected("attempt credential is invalid")
        return self.authenticate_digest_in_transaction(
            connection,
            hashlib.sha256(secret).hexdigest(),
            now=now,
            allow_finished=allow_finished,
        )

    def authenticate_digest_in_transaction(
        self,
        connection: sqlite3.Connection,
        digest: str,
        *,
        now: float | None = None,
        allow_finished: bool = False,
    ) -> WorkAttemptCredentialContext:
        """Revalidate a digest retained only in a sealed server context."""
        self._require_transaction(connection)
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise WorkAttemptCredentialRejected("attempt credential digest is invalid")
        checked_at = time.time() if now is None else now
        if type(checked_at) not in {int, float} or checked_at <= 0:
            raise WorkAttemptCredentialRejected("attempt credential time is invalid")
        self.repository._verify(connection)
        row = connection.execute(
            "SELECT * FROM work_attempt_credentials WHERE credential_sha256=?", (digest,)
        ).fetchone()
        if row is None or not hmac.compare_digest(row["credential_sha256"], digest):
            raise WorkAttemptCredentialRejected("attempt credential is unknown")
        try:
            recovery = self.repository.assert_execution_allowed(connection)
            binding = self.contracts._revalidate_order(
                connection, row["attempt_id"], generation=row["generation"]
            )
        except (AuthorityDenied, ContractConflict, KeyError, TypeError, ValueError) as error:
            raise WorkAttemptCredentialRejected(
                "attempt credential authority is no longer live"
            ) from error
        attempt = connection.execute(
            "SELECT state,revision,lease_expires_at FROM work_attempts WHERE id=? AND generation=?",
            (row["attempt_id"], row["generation"]),
        ).fetchone()
        if (
            recovery.installation_uuid != row["installation_uuid"]
            or binding.job_id != row["job_id"]
            or binding.work_item_id != row["work_item_id"]
            or binding.principal_id != row["principal_id"]
            or binding.grant_id != row["grant_id"]
            or binding.grant_revision != row["grant_revision"]
            or binding.contract_hash != row["contract_hash"]
            or attempt is None
            or attempt["state"]
            not in (
                {"sent", "acknowledged", "running", "finished"}
                if allow_finished
                else {"sent", "acknowledged", "running"}
            )
            or attempt["revision"] < row["attempt_revision"]
            or attempt["lease_expires_at"] != row["lease_expires_at"]
            or row["expires_at"] > row["lease_expires_at"]
            or min(row["expires_at"], attempt["lease_expires_at"]) <= checked_at
        ):
            raise WorkAttemptCredentialRejected("attempt credential binding has expired or changed")
        return WorkAttemptCredentialContext(
            attempt_id=row["attempt_id"],
            generation=row["generation"],
            job_id=row["job_id"],
            work_item_id=row["work_item_id"],
            principal_id=row["principal_id"],
            grant_id=row["grant_id"],
            grant_revision=row["grant_revision"],
            contract_hash=row["contract_hash"],
            installation_uuid=row["installation_uuid"],
            expires_at=row["expires_at"],
        )

    def authenticate_receiver_in_transaction(
        self,
        connection: sqlite3.Connection,
        secret: bytes,
        *,
        now: float | None = None,
        allow_finished: bool = False,
    ) -> WorkTaskReceiverCredentialContext:
        """Authenticate receiver role separately from the attempt's executor."""
        self._require_transaction(connection)
        if type(secret) is not bytes or len(secret) != 32:
            raise WorkAttemptCredentialRejected("receiver credential is invalid")
        digest = hashlib.sha256(secret).hexdigest()
        checked_at = time.time() if now is None else now
        if type(checked_at) not in {int, float} or checked_at <= 0:
            raise WorkAttemptCredentialRejected("receiver credential time is invalid")
        self.repository._verify(connection)
        row = connection.execute(
            "SELECT * FROM work_task_receiver_credentials WHERE credential_sha256=?",
            (digest,),
        ).fetchone()
        if row is None:
            return self._authenticate_workflow_receiver_in_transaction(
                connection,
                digest,
                checked_at=checked_at,
                allow_finished=allow_finished,
            )
        if row is None or not hmac.compare_digest(row["credential_sha256"], digest):
            raise WorkAttemptCredentialRejected("receiver credential is unknown")
        attempt_row = connection.execute(
            "SELECT credential_sha256 FROM work_attempt_credentials "
            "WHERE attempt_id=? AND generation=?",
            (row["attempt_id"], row["generation"]),
        ).fetchone()
        if attempt_row is None:
            raise WorkAttemptCredentialRejected("receiver attempt binding is unavailable")
        attempt = self.authenticate_digest_in_transaction(
            connection,
            attempt_row["credential_sha256"],
            now=checked_at,
            allow_finished=allow_finished,
        )
        origin = connection.execute(
            "SELECT * FROM work_child_origin_bindings WHERE child_attempt_id=? "
            "AND child_generation=?",
            (row["attempt_id"], row["generation"]),
        ).fetchone()
        if origin is None:
            raise WorkAttemptCredentialRejected("receiver lineage binding is unavailable")
        live_attempt = connection.execute(
            "SELECT state,revision,lease_expires_at FROM work_attempts "
            "WHERE id=? AND generation=?",
            (row["attempt_id"], row["generation"]),
        ).fetchone()
        try:
            from cli_agent_orchestrator.services.work_origin import WorkOrigins

            WorkOrigins(self.repository)._revalidate_replay_authorizations(connection, origin)
        except Exception as error:
            raise WorkAttemptCredentialRejected("receiver authority is no longer live") from error
        if (
            attempt.attempt_id != row["attempt_id"]
            or attempt.generation != row["generation"]
            or attempt.grant_id != origin["child_grant_id"]
            or attempt.grant_revision != origin["child_grant_revision"]
            or (row["job_id"], row["work_item_id"]) != (attempt.job_id, attempt.work_item_id)
            or (
                row["receiver_subject_id"],
                row["receiver_subject_revision"],
                row["receiver_authorization_revision"],
                row["receiver_grant_id"],
                row["receiver_grant_revision"],
                row["delivery_id"],
                row["delivery_hash"],
            )
            != (
                origin["receiver_subject_id"],
                origin["receiver_subject_revision"],
                origin["receiver_authorization_revision"],
                origin["receiver_grant_id"],
                origin["receiver_grant_revision"],
                origin["delivery_id"],
                origin["delivery_hash"],
            )
            or row["installation_uuid"] != attempt.installation_uuid
            or row["expires_at"] != attempt.expires_at
            or live_attempt is None
            or row["attempt_revision"] > live_attempt["revision"]
            or row["lease_expires_at"] != live_attempt["lease_expires_at"]
            or min(row["expires_at"], row["lease_expires_at"]) <= checked_at
        ):
            raise WorkAttemptCredentialRejected("receiver credential binding is stale")
        return WorkTaskReceiverCredentialContext(
            attempt_id=row["attempt_id"],
            generation=row["generation"],
            job_id=row["job_id"],
            work_item_id=row["work_item_id"],
            receiver_subject_id=row["receiver_subject_id"],
            receiver_subject_revision=row["receiver_subject_revision"],
            receiver_authorization_revision=row["receiver_authorization_revision"],
            receiver_grant_id=row["receiver_grant_id"],
            receiver_grant_revision=row["receiver_grant_revision"],
            delivery_id=row["delivery_id"],
            delivery_hash=row["delivery_hash"],
            expires_at=row["expires_at"],
        )

    def _authenticate_workflow_receiver_in_transaction(
        self,
        connection: sqlite3.Connection,
        digest: str,
        *,
        checked_at: float,
        allow_finished: bool,
    ) -> WorkTaskReceiverCredentialContext:
        row = connection.execute(
            "SELECT * FROM work_workflow_step_receiver_credentials " "WHERE credential_sha256=?",
            (digest,),
        ).fetchone()
        if row is None or not hmac.compare_digest(row["credential_sha256"], digest):
            raise WorkAttemptCredentialRejected("receiver credential is unknown")
        attempt_row = connection.execute(
            "SELECT credential_sha256 FROM work_attempt_credentials "
            "WHERE attempt_id=? AND generation=?",
            (row["attempt_id"], row["generation"]),
        ).fetchone()
        if attempt_row is None:
            raise WorkAttemptCredentialRejected("workflow receiver attempt is unavailable")
        attempt = self.authenticate_digest_in_transaction(
            connection,
            attempt_row["credential_sha256"],
            now=checked_at,
            allow_finished=allow_finished,
        )
        from cli_agent_orchestrator.services.work_origin import WorkOrigins

        workflow_origins = WorkOrigins(self.repository)
        try:
            binding = workflow_origins._workflow_binding_for_attempt(
                connection,
                attempt_id=row["attempt_id"],
                generation=row["generation"],
                work_item_id=row["work_item_id"],
            )
            if binding is None:
                raise WorkAttemptCredentialRejected("workflow receiver binding is unavailable")
            workflow_origins._require_workflow_receiver_action(
                connection, binding, action="task_received"
            )
            workflow_origins._require_workflow_receiver_action(
                connection, binding, action="task_result"
            )
        except WorkAttemptCredentialRejected:
            raise
        except Exception as error:
            raise WorkAttemptCredentialRejected(
                "workflow receiver authority is no longer live"
            ) from error
        live_attempt = connection.execute(
            "SELECT state,revision,lease_expires_at FROM work_attempts "
            "WHERE id=? AND generation=?",
            (row["attempt_id"], row["generation"]),
        ).fetchone()
        allowed_states = {"sent", "acknowledged", "running"}
        if allow_finished:
            allowed_states.add("finished")
        if (
            attempt.attempt_id != row["attempt_id"]
            or attempt.generation != row["generation"]
            or attempt.job_id != row["job_id"]
            or attempt.work_item_id != row["work_item_id"]
            or attempt.grant_id != binding.grant_id
            or attempt.grant_revision != binding.grant_revision
            or attempt.contract_hash != binding.contract_hash
            or binding.binding_id != row["binding_id"]
            or (binding.work_attempt_id, binding.work_generation, binding.work_item_id)
            != (row["attempt_id"], row["generation"], row["work_item_id"])
            or (
                row["receiver_subject_id"],
                row["receiver_subject_revision"],
                row["receiver_authorization_revision"],
                row["receiver_grant_id"],
                row["receiver_grant_revision"],
                row["delivery_id"],
                row["delivery_hash"],
            )
            != (
                binding.receiver_subject_ref.subject_id,
                binding.receiver_subject_ref.revision,
                binding.receiver_authorization_ref.revision,
                binding.receiver_grant_id,
                binding.receiver_grant_revision,
                binding.delivery_id,
                binding.delivery_hash,
            )
            or row["installation_uuid"] != attempt.installation_uuid
            or row["expires_at"] != attempt.expires_at
            or live_attempt is None
            or live_attempt["state"] not in allowed_states
            or row["attempt_revision"] > live_attempt["revision"]
            or row["lease_expires_at"] != live_attempt["lease_expires_at"]
            or row["delivery_id"] != binding.delivery_id
            or row["delivery_hash"] != binding.delivery_hash
            or min(row["expires_at"], row["lease_expires_at"]) <= checked_at
        ):
            raise WorkAttemptCredentialRejected("workflow receiver credential is stale")
        return WorkTaskReceiverCredentialContext(
            attempt_id=row["attempt_id"],
            generation=row["generation"],
            job_id=row["job_id"],
            work_item_id=row["work_item_id"],
            receiver_subject_id=row["receiver_subject_id"],
            receiver_subject_revision=row["receiver_subject_revision"],
            receiver_authorization_revision=row["receiver_authorization_revision"],
            receiver_grant_id=row["receiver_grant_id"],
            receiver_grant_revision=row["receiver_grant_revision"],
            delivery_id=row["delivery_id"],
            delivery_hash=row["delivery_hash"],
            expires_at=row["expires_at"],
            binding_id=row["binding_id"],
        )


def create_attempt_credential_descriptor(secret: bytes) -> int:
    """Place the bearer in a sealed, close-on-exec anonymous descriptor."""
    if type(secret) is not bytes or len(secret) != 32 or not hasattr(os, "memfd_create"):
        raise WorkAttemptCredentialRejected("private credential descriptor is unavailable")
    flags = getattr(os, "MFD_CLOEXEC", 0x0001) | getattr(os, "MFD_ALLOW_SEALING", 0x0002)
    try:
        descriptor = os.memfd_create("cao-work-attempt", flags)
        if os.write(descriptor, secret) != len(secret):
            raise OSError("short private credential write")
        required_seals = (
            getattr(fcntl, "F_SEAL_WRITE", 0x0008)
            | getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
            | getattr(fcntl, "F_SEAL_GROW", 0x0004)
            | getattr(fcntl, "F_SEAL_SEAL", 0x0001)
        )
        fcntl.fcntl(descriptor, getattr(fcntl, "F_ADD_SEALS", 1033), required_seals)
        if (
            fcntl.fcntl(descriptor, getattr(fcntl, "F_GET_SEALS", 1034)) & required_seals
            != required_seals
        ):
            raise OSError("private credential descriptor could not be sealed")
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except OSError as error:
        try:
            os.close(descriptor)
        except (UnboundLocalError, OSError):
            pass
        raise WorkAttemptCredentialRejected("private credential descriptor setup failed") from error


def validate_attempt_credential_descriptor(descriptor: int) -> None:
    """Check that the descriptor is the sealed anonymous 32-byte handoff."""
    if type(descriptor) is not int or descriptor < 0:
        raise WorkAttemptCredentialRejected("private credential descriptor is invalid")
    required_seals = (
        getattr(fcntl, "F_SEAL_WRITE", 0x0008)
        | getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
        | getattr(fcntl, "F_SEAL_GROW", 0x0004)
        | getattr(fcntl, "F_SEAL_SEAL", 0x0001)
    )
    try:
        identity = os.fstat(descriptor)
        seals = fcntl.fcntl(descriptor, getattr(fcntl, "F_GET_SEALS", 1034))
        if (
            not stat.S_ISREG(identity.st_mode)
            or identity.st_size != 32
            or seals & required_seals != required_seals
        ):
            raise OSError("private credential descriptor identity is not sealed")
    except OSError as error:
        raise WorkAttemptCredentialRejected(
            "private credential descriptor cannot be verified"
        ) from error


def read_attempt_credential_descriptor(descriptor: int) -> bytes:
    """Read only a sealed 32-byte memfd; never accept a path or environment value."""
    validate_attempt_credential_descriptor(descriptor)
    try:
        secret = os.pread(descriptor, 33, 0)
    except OSError as error:
        raise WorkAttemptCredentialRejected(
            "private credential descriptor cannot be read"
        ) from error
    if len(secret) != 32:
        raise WorkAttemptCredentialRejected("private credential descriptor content is invalid")
    return secret
