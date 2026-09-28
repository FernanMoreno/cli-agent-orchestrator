"""Cooperative write reservations with durable ownership and explicit stopped-writer release."""

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from uuid import uuid4

from cli_agent_orchestrator.clients.work_repository import WorkRepository


class ReservationConflict(ValueError):
    """A resource, owner fence or stopped-writer proof cannot authorize this operation."""


def _positive(value: int) -> None:
    if type(value) is not int or value <= 0:
        raise ReservationConflict("generation and revisions must be positive integers")


def _identity(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ReservationConflict("bounded actor/evidence identity required")


@dataclass(frozen=True)
class WriterIdentity:
    job_id: str
    work_item_id: str
    attempt_id: str
    generation: int
    revision: int
    state: str
    lease_expires_at: float


@dataclass(frozen=True)
class StoppedWriter:
    """Backend-produced evidence of irreversible cessation for one fenced writer.

    Only the constructor-injected server verifier supplies this value. It is not
    accepted as a client request field or inferred from expiry/idle/cancellation.
    """

    attempt_id: str
    generation: int
    attempt_revision: int
    evidence_ref: str

    def __post_init__(self) -> None:
        _identity(self.attempt_id)
        _positive(self.generation)
        _positive(self.attempt_revision)
        _identity(self.evidence_ref)


@dataclass(frozen=True)
class ReservationSet:
    id: str
    job_id: str
    work_item_id: str
    attempt_id: str
    generation: int
    revision: int
    checkout_root: str
    paths: tuple[str, ...]
    expires_at: float
    state: str
    stop_evidence_ref: str | None


class WorkReservations:
    """Trusted internal boundary; callers must already authorize the checkout/paths.

    Covers canonical namespace ancestry and direct inode aliases of existing
    requested paths. Directory reservations do NOT snapshot descendant inodes,
    nor detect aliases created after acquisition. Backend isolation is required
    for those guarantees; this cooperative store is not a filesystem sandbox.

    Expiry denies new effects but never frees ownership. All resource mutations
    and their events use one repository transaction. Server stop verification may
    perform I/O outside that transaction, followed by fresh terminal-state CAS.
    """

    def __init__(
        self,
        repository: WorkRepository,
        *,
        stop_verifier: Callable[[WriterIdentity], StoppedWriter | None] | None = None,
    ):
        self.repository = repository
        self._stop_verifier = stop_verifier

    @staticmethod
    def _paths(checkout_root: Path, paths: Iterable[Path | str]):
        if isinstance(paths, (str, bytes)):
            raise ReservationConflict("paths must be an explicit collection")
        try:
            root = Path(checkout_root).resolve(strict=True)
            if not root.is_dir():
                raise ReservationConflict("checkout root must be a directory")
            canonical = set()
            for item in paths:
                path = Path(item)
                path = (path if path.is_absolute() else root / path).resolve()
                if not path.is_relative_to(root):
                    raise ReservationConflict("reservation path escapes the authorized checkout")
                canonical.add(path)
            if not canonical:
                raise ReservationConflict("at least one path is required")
            resources = []
            for path in sorted(canonical):
                try:
                    info = path.stat()
                    resources.append((str(path), info.st_dev, info.st_ino))
                except FileNotFoundError:
                    resources.append((str(path), None, None))
            return str(root), resources
        except (OSError, RuntimeError) as exc:
            raise ReservationConflict("reservation paths cannot be resolved safely") from exc

    @staticmethod
    def _expiry(expires_at, owner: WriterIdentity):
        if (
            isinstance(expires_at, bool)
            or not isinstance(expires_at, (int, float))
            or not math.isfinite(expires_at)
            or expires_at <= time.time()
            or expires_at > owner.lease_expires_at
        ):
            raise ReservationConflict("reservation lease must be live and within its attempt lease")
        return float(expires_at)

    @staticmethod
    def _owner(
        connection,
        *,
        job_id,
        work_item_id,
        attempt_id,
        generation,
        expected_attempt_revision,
        active,
    ):
        _positive(generation)
        _positive(expected_attempt_revision)
        row = connection.execute(
            "SELECT a.*,w.job_id,w.state AS work_state,j.state AS job_state FROM work_attempts a JOIN work_items w ON w.id=a.work_item_id JOIN work_jobs j ON j.id=w.job_id WHERE a.id=?",
            (attempt_id,),
        ).fetchone()
        if (
            row is None
            or row["job_id"] != job_id
            or row["work_item_id"] != work_item_id
            or row["generation"] != generation
            or row["revision"] != expected_attempt_revision
        ):
            raise ReservationConflict("reservation owner identity or revision changed")
        if active:
            latest = connection.execute(
                "SELECT id FROM work_attempts WHERE work_item_id=? ORDER BY generation DESC LIMIT 1",
                (work_item_id,),
            ).fetchone()
            if (
                latest["id"] != attempt_id
                or row["state"] not in {"planned", "sent", "acknowledged", "running"}
                or row["work_state"] in {"succeeded", "failed", "cancelled", "reconcile"}
                or row["job_state"] in {"revoked", "completed", "failed"}
                or row["lease_expires_at"] <= time.time()
            ):
                raise ReservationConflict("attempt no longer permits reserved effects")
        elif row["state"] not in {"finished", "failed", "cancelled"}:
            raise ReservationConflict(
                "writer must be terminal and unable to restart before release"
            )
        return WriterIdentity(
            job_id,
            work_item_id,
            attempt_id,
            generation,
            row["revision"],
            row["state"],
            row["lease_expires_at"],
        )

    @staticmethod
    def _load(connection, reservation_id):
        row = connection.execute(
            "SELECT * FROM work_reservation_sets WHERE id=?", (reservation_id,)
        ).fetchone()
        if row is None:
            raise ReservationConflict("reservation set not found")
        paths = tuple(
            item[0]
            for item in connection.execute(
                "SELECT resource_key FROM work_path_reservations WHERE reservation_set_id=? ORDER BY resource_key",
                (reservation_id,),
            )
        )
        return ReservationSet(
            row["id"],
            row["job_id"],
            row["work_item_id"],
            row["attempt_id"],
            row["generation"],
            row["revision"],
            row["checkout_root"],
            paths,
            row["expires_at"],
            row["state"],
            row["stop_evidence_ref"],
        )

    def _checked(
        self,
        connection,
        reservation_id,
        *,
        generation,
        expected_revision,
        expected_attempt_revision,
        active,
    ):
        _positive(expected_revision)
        value = self._load(connection, reservation_id)
        if (
            value.generation != generation
            or value.revision != expected_revision
            or value.state != "active"
        ):
            raise ReservationConflict("reservation generation, revision or ownership changed")
        owner = self._owner(
            connection,
            job_id=value.job_id,
            work_item_id=value.work_item_id,
            attempt_id=value.attempt_id,
            generation=generation,
            expected_attempt_revision=expected_attempt_revision,
            active=active,
        )
        if active and value.expires_at <= time.time():
            raise ReservationConflict(
                "expired reservation remains owned but cannot authorize effects"
            )
        return value, owner

    def _event(self, connection, value, actor_id, event_type):
        connection.execute(
            "UPDATE work_items SET revision=revision+1 WHERE id=?", (value.work_item_id,)
        )
        self.repository._append_event(
            connection,
            job_id=value.job_id,
            work_item_id=value.work_item_id,
            attempt_id=value.attempt_id,
            actor_id=actor_id,
            event_type=event_type,
            metadata={"reservation_set_id": value.id, "reservation_revision": value.revision},
        )

    def get(self, reservation_id: str) -> ReservationSet:
        """Read frozen canonical paths without resolving the current filesystem."""
        with self.repository.connection() as connection:
            connection.execute("BEGIN")
            self.repository._verify(connection)
            return self._load(connection, reservation_id)

    def reserve(
        self,
        *,
        job_id: str,
        work_item_id: str,
        attempt_id: str,
        generation: int,
        expected_attempt_revision: int,
        checkout_root: Path,
        paths: Iterable[Path | str],
        expires_at: float,
        actor_id: str,
    ) -> ReservationSet:
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            return self._reserve(
                connection,
                job_id=job_id,
                work_item_id=work_item_id,
                attempt_id=attempt_id,
                generation=generation,
                expected_attempt_revision=expected_attempt_revision,
                checkout_root=checkout_root,
                paths=paths,
                expires_at=expires_at,
                actor_id=actor_id,
            )

    def _reserve(
        self,
        connection,
        *,
        job_id: str,
        work_item_id: str,
        attempt_id: str,
        generation: int,
        expected_attempt_revision: int,
        checkout_root: Path,
        paths: Iterable[Path | str],
        expires_at: float,
        actor_id: str,
    ) -> ReservationSet:
        """Caller owns verified BEGIN IMMEDIATE; this helper never commits."""
        if not connection.in_transaction:
            raise ValueError("a caller-owned transaction is required")
        _identity(actor_id)
        root, resources = self._paths(checkout_root, paths)
        owner = self._owner(
            connection,
            job_id=job_id,
            work_item_id=work_item_id,
            attempt_id=attempt_id,
            generation=generation,
            expected_attempt_revision=expected_attempt_revision,
            active=True,
        )
        expires_at = self._expiry(expires_at, owner)
        existing = connection.execute(
            "SELECT id FROM work_reservation_sets WHERE attempt_id=? AND generation=?",
            (attempt_id, generation),
        ).fetchone()
        if existing:
            value = self._load(connection, existing["id"])
            if (
                value.state != "active"
                or value.expires_at <= time.time()
                or value.checkout_root != root
                or value.paths != tuple(item[0] for item in resources)
            ):
                raise ReservationConflict(
                    "attempt already has a different or settled reservation set"
                )
            return value
        # Expired or terminal-owner sets still conflict until explicitly released.
        occupied = connection.execute(
            "SELECT p.resource_key,p.device,p.inode FROM work_path_reservations p JOIN work_reservation_sets s ON s.id=p.reservation_set_id WHERE s.state='active'"
        ).fetchall()
        for path, device, inode in resources:
            for prior in occupied:
                if (
                    Path(path).is_relative_to(Path(prior["resource_key"]))
                    or Path(prior["resource_key"]).is_relative_to(Path(path))
                    or (
                        device is not None and device == prior["device"] and inode == prior["inode"]
                    )
                ):
                    raise ReservationConflict("path or existing inode is already reserved")
        identifier = uuid4().hex
        connection.execute(
            "INSERT INTO work_reservation_sets(id,job_id,work_item_id,attempt_id,generation,checkout_root,expires_at,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                identifier,
                job_id,
                work_item_id,
                attempt_id,
                generation,
                root,
                expires_at,
                time.time(),
            ),
        )
        connection.executemany(
            "INSERT INTO work_path_reservations(reservation_set_id,resource_key,device,inode) VALUES (?,?,?,?)",
            [(identifier, *resource) for resource in resources],
        )
        value = self._load(connection, identifier)
        self._event(connection, value, actor_id, "paths.reserved")
        return value

    def assert_held(
        self,
        reservation_id: str,
        *,
        generation: int,
        expected_revision: int,
        expected_attempt_revision: int,
    ) -> ReservationSet:
        with self.repository.connection() as connection:
            connection.execute("BEGIN")
            self.repository._verify(connection)
            value, _ = self._checked(
                connection,
                reservation_id,
                generation=generation,
                expected_revision=expected_revision,
                expected_attempt_revision=expected_attempt_revision,
                active=True,
            )
            return value

    def renew(
        self,
        reservation_id: str,
        *,
        generation: int,
        expected_revision: int,
        expected_attempt_revision: int,
        expires_at: float,
        actor_id: str,
    ) -> ReservationSet:
        _identity(actor_id)
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            value, owner = self._checked(
                connection,
                reservation_id,
                generation=generation,
                expected_revision=expected_revision,
                expected_attempt_revision=expected_attempt_revision,
                active=True,
            )
            expires_at = self._expiry(max(value.expires_at, self._expiry(expires_at, owner)), owner)
            changed = connection.execute(
                "UPDATE work_reservation_sets SET expires_at=?,revision=revision+1 WHERE id=? AND generation=? AND revision=? AND state='active'",
                (expires_at, reservation_id, generation, expected_revision),
            ).rowcount
            if changed != 1:
                raise ReservationConflict("reservation renewal lost its revision fence")
            value = self._load(connection, reservation_id)
            self._event(connection, value, actor_id, "paths.renewed")
            return value

    def release(
        self,
        reservation_id: str,
        *,
        generation: int,
        expected_revision: int,
        expected_attempt_revision: int,
        actor_id: str,
    ) -> ReservationSet:
        _identity(actor_id)
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            _, owner = self._checked(
                connection,
                reservation_id,
                generation=generation,
                expected_revision=expected_revision,
                expected_attempt_revision=expected_attempt_revision,
                active=False,
            )
        proof = self._stop_verifier(owner) if self._stop_verifier is not None else None
        if not isinstance(proof, StoppedWriter) or (
            proof.attempt_id,
            proof.generation,
            proof.attempt_revision,
        ) != (owner.attempt_id, owner.generation, owner.revision):
            raise ReservationConflict(
                "fresh server evidence of irreversible writer cessation is required"
            )
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self._checked(
                connection,
                reservation_id,
                generation=generation,
                expected_revision=expected_revision,
                expected_attempt_revision=expected_attempt_revision,
                active=False,
            )
            changed = connection.execute(
                "UPDATE work_reservation_sets SET state='released',revision=revision+1,stop_evidence_ref=?,released_at=? WHERE id=? AND generation=? AND revision=? AND state='active'",
                (proof.evidence_ref, time.time(), reservation_id, generation, expected_revision),
            ).rowcount
            if changed != 1:
                raise ReservationConflict("reservation release lost its revision fence")
            value = self._load(connection, reservation_id)
            self._event(connection, value, actor_id, "paths.released")
            return value
