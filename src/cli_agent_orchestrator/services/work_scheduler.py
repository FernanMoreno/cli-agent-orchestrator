"""Trusted internal scheduler: durable slots and declared, nonrefundable admission units.

This is not provider billing, token metering, execution state or grant authority.
Callers authorize requests before enqueue. Production dispatch must coordinate
claim, path reservations and attempt transition in one repository transaction;
``_claim_next`` is the transaction-local seam for that integration.
"""

import json
import time
from dataclasses import dataclass
from uuid import uuid4

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services.work_reservations import (
    ReservationConflict,
    StoppedWriter,
    WorkReservations,
    WriterIdentity,
)


class SchedulerConflict(ValueError):
    """Invalid or stale admission policy, owner or request."""


class SchedulerBackpressure(SchedulerConflict):
    """The globally bounded waiting queue is full."""


class SchedulerSelectionChanged(SchedulerConflict):
    """The locked claim no longer matches the caller's preview candidate."""


class DependencyCycle(SchedulerConflict):
    def __init__(self, participants):
        self.participants = tuple(participants)
        super().__init__("dependency cycle: " + ", ".join(self.participants))


def _integer(value, *, minimum=1):
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise SchedulerConflict("bounded integer required")


def _actor(actor_id):
    if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 512:
        raise SchedulerConflict("bounded actor identity required")


@dataclass(frozen=True)
class ScheduledReservation:
    id: str
    job_id: str
    work_item_id: str
    attempt_id: str
    generation: int
    revision: int
    units: int
    state: str
    queued_at: float
    expires_at: float


class WorkScheduler:
    def __init__(self, repository: WorkRepository, *, stop_verifier=None):
        self.repository = repository
        self._stop_verifier = stop_verifier

    @staticmethod
    def _policy(connection):
        row = connection.execute("SELECT * FROM work_scheduler_policy WHERE singleton=1").fetchone()
        if row is None:
            raise SchedulerConflict("scheduler policy must be explicitly configured")
        return row

    def _lock_aggregate(self, connection):
        """Serialize queue, capacity, policy and dispatch-sequence mutations."""
        if not connection.in_transaction:
            raise SchedulerConflict("scheduler mutation requires an active transaction")
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"cao:work-scheduler:{connection.target.store_id}",),
        )

    def configure(self, *, capacity, max_queue, aging_seconds, expected_policy_revision):
        for value in (capacity, max_queue, aging_seconds):
            _integer(value)
        _integer(expected_policy_revision, minimum=0)
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self._lock_aggregate(connection)
            row = connection.execute(
                "SELECT * FROM work_scheduler_policy WHERE singleton=1"
            ).fetchone()
            revision = row["revision"] if row else 0
            if revision != expected_policy_revision:
                raise SchedulerConflict("scheduler policy revision changed")
            counts = dict(
                connection.execute(
                    "SELECT state,count(*) FROM work_scheduler_requests GROUP BY state"
                ).fetchall()
            )
            if capacity < counts.get("held", 0) or max_queue < counts.get("queued", 0):
                raise SchedulerConflict("policy cannot undercut existing reservations or queue")
            connection.execute(
                "INSERT INTO work_scheduler_policy(singleton,revision,capacity,max_queue,aging_seconds) "
                "VALUES (1,%s,%s,%s,%s) ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,"
                "capacity=excluded.capacity,max_queue=excluded.max_queue,aging_seconds=excluded.aging_seconds",
                (revision + 1, capacity, max_queue, aging_seconds),
            )
            return revision + 1

    @staticmethod
    def _row(connection, identifier):
        row = connection.execute(
            "SELECT * FROM work_scheduler_requests WHERE id=%s", (identifier,)
        ).fetchone()
        if row is None:
            raise SchedulerConflict("scheduler request not found")
        return row

    @staticmethod
    def _view(row):
        return ScheduledReservation(
            **{field: row[field] for field in ScheduledReservation.__dataclass_fields__}
        )

    @staticmethod
    def _owner(connection, row, *, expected_attempt_revision, active):
        try:
            return WorkReservations._owner(
                connection,
                job_id=row["job_id"],
                work_item_id=row["work_item_id"],
                attempt_id=row["attempt_id"],
                generation=row["generation"],
                expected_attempt_revision=expected_attempt_revision,
                active=active,
            )
        except ReservationConflict as error:
            raise SchedulerConflict(str(error)) from error

    @staticmethod
    def _budget(connection, job_id):
        job = WorkRepository._job(connection, job_id)
        budget = job["budget"]
        if not isinstance(budget, dict):
            raise SchedulerConflict("job must declare scheduler_units budget")
        units = budget.get("scheduler_units")
        _integer(units, minimum=0)
        return units

    def _event(self, connection, row, actor_id, event_type, *, metadata=None):
        connection.execute(
            "UPDATE work_items SET revision=revision+1 WHERE id=%s", (row["work_item_id"],)
        )
        event_metadata = {
            "scheduler_reservation_id": row["id"],
            "reservation_revision": row["revision"],
        }
        if metadata:
            event_metadata.update(metadata)
        self.repository._append_event(
            connection,
            job_id=row["job_id"],
            work_item_id=row["work_item_id"],
            attempt_id=row["attempt_id"],
            actor_id=actor_id,
            event_type=event_type,
            metadata=event_metadata,
        )

    @staticmethod
    def _dependencies(connection, job_id, work_item_id, dependencies):
        # Work-level edges persist across retry generations; requests freeze the set.
        for dependency in dependencies:
            row = connection.execute(
                "SELECT job_id FROM work_items WHERE id=%s", (dependency,)
            ).fetchone()
            if row is None or row["job_id"] != job_id:
                raise SchedulerConflict("dependencies must exist within the same job")
        graph = {}
        for item, dependency in connection.execute(
            "SELECT work_item_id,dependency_id FROM work_scheduler_dependencies WHERE job_id=%s",
            (job_id,),
        ):
            graph.setdefault(item, set()).add(dependency)
        graph.setdefault(work_item_id, set()).update(dependencies)
        # Iterative DFS avoids recursion limits for a long, valid dependency chain.
        done, active, path = set(), set(), []
        stack = [(work_item_id, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                active.remove(node)
                path.pop()
                done.add(node)
            elif node in active:
                raise DependencyCycle(path[path.index(node) :])
            elif node not in done:
                active.add(node)
                path.append(node)
                stack.append((node, True))
                stack.extend((child, False) for child in sorted(graph.get(node, ()), reverse=True))

    def enqueue(
        self, *, attempt_id, generation, expected_attempt_revision, units, dependencies=(), actor_id
    ):
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            observed = connection.execute(
                "SELECT w.job_id FROM work_attempts a "
                "JOIN work_items w ON w.id=a.work_item_id WHERE a.id=%s",
                (attempt_id,),
            ).fetchone()
            if observed is None:
                raise SchedulerConflict("attempt owner not found or generation changed")
            job_id = observed["job_id"]
            self.repository._lock_job_for_write(connection, job_id)
            current = connection.execute(
                "SELECT w.job_id FROM work_attempts a "
                "JOIN work_items w ON w.id=a.work_item_id WHERE a.id=%s",
                (attempt_id,),
            ).fetchone()
            if current is None or current["job_id"] != job_id:
                raise SchedulerConflict("attempt owner changed before enqueue")
            return self._enqueue(
                connection,
                attempt_id=attempt_id,
                generation=generation,
                expected_attempt_revision=expected_attempt_revision,
                units=units,
                dependencies=dependencies,
                actor_id=actor_id,
            )

    def _enqueue(
        self,
        connection,
        *,
        attempt_id,
        generation,
        expected_attempt_revision,
        units,
        dependencies=(),
        actor_id,
    ):
        """Caller owns the verified transaction; this helper never commits."""
        if not connection.in_transaction:
            raise ValueError("a caller-owned transaction is required")
        self._lock_aggregate(connection)
        _integer(generation)
        _integer(expected_attempt_revision)
        _integer(units)
        _actor(actor_id)
        if isinstance(dependencies, (str, bytes)):
            raise SchedulerConflict("dependencies must be an explicit collection")
        dependencies = tuple(dependencies)
        if any(
            not isinstance(item, str) or not item.strip() or len(item) > 512
            for item in dependencies
        ):
            raise SchedulerConflict("invalid dependency identity")
        dependencies = tuple(sorted(set(dependencies)))
        encoded = json.dumps(dependencies, separators=(",", ":"))
        policy = self._policy(connection)
        owner_row = connection.execute(
            "SELECT a.*,w.job_id FROM work_attempts a JOIN work_items w ON w.id=a.work_item_id WHERE a.id=%s",
            (attempt_id,),
        ).fetchone()
        if owner_row is None or owner_row["generation"] != generation:
            raise SchedulerConflict("attempt owner not found or generation changed")
        # Existing requests remain readable idempotently, never re-arbitrated.
        existing = connection.execute(
            "SELECT * FROM work_scheduler_requests WHERE attempt_id=%s AND generation=%s",
            (attempt_id, generation),
        ).fetchone()
        if existing:
            if existing["units"] != units or existing["dependencies"] != encoded:
                raise SchedulerConflict(
                    "attempt request is already frozen with different arguments"
                )
            if owner_row["revision"] != expected_attempt_revision:
                raise SchedulerConflict("attempt revision changed")
            return self._view(existing)
        owner = self._owner(
            connection,
            dict(owner_row, attempt_id=attempt_id),
            expected_attempt_revision=expected_attempt_revision,
            active=True,
        )
        if owner.state != "planned":
            raise SchedulerConflict("new scheduling must precede delivery")
        if units > self._budget(connection, owner.job_id):
            raise SchedulerConflict("request exceeds declared job budget")
        previous = connection.execute(
            "SELECT dependencies FROM work_scheduler_requests WHERE work_item_id=%s LIMIT 1",
            (owner.work_item_id,),
        ).fetchone()
        if previous and previous[0] != encoded:
            raise SchedulerConflict("work dependency graph is already frozen")
        self._dependencies(connection, owner.job_id, owner.work_item_id, dependencies)
        queued = connection.execute(
            "SELECT count(*) FROM work_scheduler_requests WHERE state='queued'"
        ).fetchone()[0]
        if queued >= policy["max_queue"]:
            raise SchedulerBackpressure("scheduler waiting queue is full")
        identifier = uuid4().hex
        connection.execute(
            "INSERT INTO work_scheduler_requests(id,job_id,work_item_id,attempt_id,generation,units,dependencies,queued_at,expires_at,enqueue_frontier) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                identifier,
                owner.job_id,
                owner.work_item_id,
                attempt_id,
                generation,
                units,
                encoded,
                time.time(),
                owner.lease_expires_at,
                policy["dispatch_sequence"],
            ),
        )
        connection.executemany(
            "INSERT INTO work_scheduler_dependencies(job_id,work_item_id,dependency_id) "
            "VALUES (%s,%s,%s) ON CONFLICT(work_item_id,dependency_id) DO NOTHING",
            [(owner.job_id, owner.work_item_id, dependency) for dependency in dependencies],
        )
        row = self._row(connection, identifier)
        self._event(connection, row, actor_id, "scheduler.queued")
        return self._view(row)

    def claim_next(self, *, actor_id):
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            # The public claim helper has no WorkAdmission pass to lock each
            # candidate's authority chain first. Snapshot queued owners in the
            # same durable order used by WorkAdmission._eligible, lock their
            # parent jobs before _claim_next takes the aggregate, then let the
            # normal selection query and queued-row CAS revalidate this set.
            # Requests enqueued after this snapshot wait for the next claim.
            queued = connection.execute(
                "SELECT job_id,attempt_id FROM work_scheduler_requests "
                "WHERE state='queued' ORDER BY queue_sequence"
            ).fetchall()
            eligible_attempts = set()
            locked_jobs = set()
            for row in queued:
                if row["job_id"] not in locked_jobs:
                    self.repository._lock_job_for_write(connection, row["job_id"])
                    locked_jobs.add(row["job_id"])
                eligible_attempts.add(row["attempt_id"])
            return self._claim_next(
                connection,
                actor_id=actor_id,
                eligible_attempts=frozenset(eligible_attempts),
            )

    @staticmethod
    def _validate_eligible_attempts(eligible_attempts: frozenset[str] | None) -> None:
        if eligible_attempts is not None and (
            type(eligible_attempts) is not frozenset
            or any(
                type(value) is not str or not value.strip() or len(value) > 512
                for value in eligible_attempts
            )
        ):
            raise SchedulerConflict("eligible attempts require a frozenset of bounded string IDs")

    def _select_candidate(self, connection, *, eligible_attempts, policy):
        """Return the current best candidate without mutating scheduler state."""
        held = connection.execute(
            "SELECT count(*) FROM work_scheduler_requests WHERE state='held'"
        ).fetchone()[0]
        if held >= policy["capacity"]:
            return None
        rows = connection.execute(
            "SELECT q.*,a.revision AS attempt_revision,j.priority FROM work_scheduler_requests q "
            "JOIN work_attempts a ON a.id=q.attempt_id JOIN work_jobs j ON j.id=q.job_id "
            "WHERE q.state='queued' ORDER BY q.queue_sequence"
        ).fetchall()
        candidates = []
        now = time.time()
        for row in rows:
            if eligible_attempts is not None and row["attempt_id"] not in eligible_attempts:
                continue
            try:
                owner = self._owner(
                    connection, row, expected_attempt_revision=row["attempt_revision"], active=True
                )
                budget = self._budget(connection, row["job_id"])
            except SchedulerConflict:
                continue
            if owner.state != "planned" or row["expires_at"] <= now:
                continue
            spent = sum(
                item[0]
                for item in connection.execute(
                    "SELECT units FROM work_scheduler_requests WHERE job_id=%s AND state IN ('held','released')",
                    (row["job_id"],),
                )
            )
            if spent + row["units"] > budget:
                continue
            unmet = connection.execute(
                "SELECT 1 FROM work_scheduler_dependencies d JOIN work_items w ON w.id=d.dependency_id "
                "WHERE d.work_item_id=%s AND w.state!='succeeded' LIMIT 1",
                (row["work_item_id"],),
            ).fetchone()
            if not unmet:
                candidates.append(row)
        if not candidates:
            return None
        served = dict(
            connection.execute(
                "SELECT job_id,max(dispatch_sequence) FROM work_scheduler_requests GROUP BY job_id"
            ).fetchall()
        )
        # Aging chooses a base-priority group. Within each group, strict job
        # round-robin wins over arrival age, then FIFO chooses that job's request.
        scores: dict[int, int] = {}
        for row in candidates:
            priority = row["priority"]
            scores[priority] = max(
                scores.get(priority, priority),
                priority + int(max(0, now - row["queued_at"]) // policy["aging_seconds"]),
            )
        priority = max(scores, key=lambda value: (scores[value], value))
        frontiers: dict[str, int] = {}
        for candidate in candidates:
            job_id = candidate["job_id"]
            frontiers[job_id] = min(
                frontiers.get(job_id, candidate["enqueue_frontier"]), candidate["enqueue_frontier"]
            )
        return min(
            (row for row in candidates if row["priority"] == priority),
            # New jobs join at their durable enqueue frontier, not sequence zero.
            # Otherwise continuous newcomers starve an already-served waiting job.
            key=lambda row: (
                max(served.get(row["job_id"]) or 0, frontiers[row["job_id"]]),
                row["queue_sequence"],
            ),
        )

    def _peek_next(self, connection, *, actor_id, eligible_attempts: frozenset[str] | None = None):
        """Read a non-authoritative preview; never lock or mutate scheduler state."""
        if not connection.in_transaction:
            raise SchedulerConflict("scheduler preview requires an active transaction")
        self._validate_eligible_attempts(eligible_attempts)
        _actor(actor_id)
        policy = self._policy(connection)
        row = self._select_candidate(connection, eligible_attempts=eligible_attempts, policy=policy)
        return self._view(row) if row is not None else None

    def _claim_next(
        self,
        connection,
        *,
        actor_id,
        eligible_attempts: frozenset[str] | None = None,
        expected_selection: tuple[str, int] | None = None,
    ):
        """Claim within the caller's verified transaction; never commit here.

        An admission coordinator computes eligible_attempts from current grants,
        bindings and path conflicts in this SAME transaction. This set is only
        an intersection, not authority. None preserves legacy selection; an
        empty frozenset selects nothing and consumes no slot, turn or budget.
        When expected_selection is supplied, a mismatch is rejected before any
        policy, request, item or event mutation.
        """
        if not connection.in_transaction:
            raise SchedulerConflict("scheduler claim requires an active transaction")
        self._lock_aggregate(connection)
        self._validate_eligible_attempts(eligible_attempts)
        _actor(actor_id)
        if expected_selection is not None:
            if (
                type(expected_selection) is not tuple
                or len(expected_selection) != 2
                or type(expected_selection[0]) is not str
                or not expected_selection[0].strip()
                or len(expected_selection[0]) > 512
                or type(expected_selection[1]) is not int
            ):
                raise SchedulerConflict(
                    "expected selection requires a bounded attempt and generation"
                )
            _integer(expected_selection[1])
        policy = self._policy(connection)
        row = self._select_candidate(connection, eligible_attempts=eligible_attempts, policy=policy)
        if row is None:
            return None
        if (
            expected_selection is not None
            and (row["attempt_id"], row["generation"]) != expected_selection
        ):
            raise SchedulerSelectionChanged("scheduler selection changed before claim")
        sequence = policy["dispatch_sequence"] + 1
        connection.execute(
            "UPDATE work_scheduler_policy SET dispatch_sequence=%s WHERE singleton=1", (sequence,)
        )
        changed = connection.execute(
            "UPDATE work_scheduler_requests SET state='held',revision=revision+1,dispatch_sequence=%s "
            "WHERE id=%s AND state='queued' AND revision=%s",
            (sequence, row["id"], row["revision"]),
        ).rowcount
        if changed != 1:
            raise SchedulerConflict("queued request changed during claim")
        row = self._row(connection, row["id"])
        self._event(connection, row, actor_id, "scheduler.claimed")
        return self._view(row)

    def get_by_attempt(self, attempt_id):
        with self.repository.read_snapshot() as connection:
            row = connection.execute(
                "SELECT * FROM work_scheduler_requests WHERE attempt_id=%s", (attempt_id,)
            ).fetchone()
            if row is None:
                raise SchedulerConflict("scheduler request not found")
            return self._view(row)

    def _checked(
        self,
        connection,
        identifier,
        *,
        generation,
        expected_revision,
        expected_attempt_revision,
        active,
    ):
        _integer(generation)
        _integer(expected_revision)
        row = self._row(connection, identifier)
        if (
            row["generation"] != generation
            or row["revision"] != expected_revision
            or row["state"] != "held"
        ):
            raise SchedulerConflict("scheduler reservation fence changed")
        owner = self._owner(
            connection, row, expected_attempt_revision=expected_attempt_revision, active=active
        )
        if active and row["expires_at"] <= time.time():
            raise SchedulerConflict("expired reservation cannot authorize effects")
        return row, owner

    def assert_held(self, identifier, *, generation, expected_revision, expected_attempt_revision):
        with self.repository.read_snapshot() as connection:
            row, _ = self._checked(
                connection,
                identifier,
                generation=generation,
                expected_revision=expected_revision,
                expected_attempt_revision=expected_attempt_revision,
                active=True,
            )
            return self._view(row)

    def withdraw_queued(
        self, identifier, *, generation, expected_revision, expected_attempt_revision, actor_id
    ):
        """Retire a terminal attempt's never-claimed request; cannot free a slot."""
        _integer(generation)
        _integer(expected_revision)
        _actor(actor_id)
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            return self._withdraw_queued(
                connection,
                identifier,
                generation=generation,
                expected_revision=expected_revision,
                expected_attempt_revision=expected_attempt_revision,
                actor_id=actor_id,
            )

    def _withdraw_queued(
        self,
        connection,
        identifier,
        *,
        generation,
        expected_revision,
        expected_attempt_revision,
        actor_id,
    ):
        """Caller owns the verified transaction; this helper never commits."""
        if not connection.in_transaction:
            raise ValueError("a caller-owned transaction is required")
        _integer(generation)
        _integer(expected_revision)
        _actor(actor_id)
        observed = self._row(connection, identifier)
        self.repository._lock_job_for_write(connection, observed["job_id"])
        # A dispatcher may retire several invalid requests across jobs in this
        # one transaction. Withdrawal only decrements queued occupancy; it
        # does not consume held capacity or dispatch sequence, and its request
        # CAS arbitrates with claims. Avoid the global aggregate lock here so
        # a later withdrawal never acquires a job lock after that lock. An
        # enqueue racing an uncommitted withdrawal still sees the old queued
        # row and can only conservatively report backpressure.
        row = self._row(connection, identifier)
        if row["job_id"] != observed["job_id"]:
            raise SchedulerConflict("queued request job changed before withdrawal")
        if (
            row["generation"] != generation
            or row["revision"] != expected_revision
            or row["state"] != "queued"
        ):
            raise SchedulerConflict("queued request fence changed")
        self._owner(
            connection, row, expected_attempt_revision=expected_attempt_revision, active=False
        )
        changed = connection.execute(
            "UPDATE work_scheduler_requests SET state='withdrawn',revision=revision+1 "
            "WHERE id=%s AND generation=%s AND revision=%s AND state='queued'",
            (identifier, generation, expected_revision),
        ).rowcount
        if changed != 1:
            raise SchedulerConflict("queued request fence changed")
        row = self._row(connection, identifier)
        self._event(connection, row, actor_id, "scheduler.withdrawn")
        return self._view(row)

    def release(
        self, identifier, *, generation, expected_revision, expected_attempt_revision, actor_id
    ):
        _actor(actor_id)
        arguments = dict(
            generation=generation,
            expected_revision=expected_revision,
            expected_attempt_revision=expected_attempt_revision,
            active=False,
        )
        with self.repository.read_snapshot() as connection:
            _, owner = self._checked(connection, identifier, **arguments)
        proof = self._stop_verifier(owner) if self._stop_verifier is not None else None
        if not isinstance(proof, StoppedWriter) or (
            proof.attempt_id,
            proof.generation,
            proof.attempt_revision,
        ) != (owner.attempt_id, owner.generation, owner.revision):
            raise SchedulerConflict("server proof of irreversible writer cessation required")
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            observed = self._row(connection, identifier)
            self.repository._lock_job_for_write(connection, observed["job_id"])
            self._lock_aggregate(connection)
            row, current_owner = self._checked(connection, identifier, **arguments)
            if row["job_id"] != observed["job_id"]:
                raise SchedulerConflict("scheduler request job changed before release")
            if (
                proof.attempt_id,
                proof.generation,
                proof.attempt_revision,
            ) != (
                current_owner.attempt_id,
                current_owner.generation,
                current_owner.revision,
            ):
                raise SchedulerConflict("server proof no longer matches the stopped writer")
            changed = connection.execute(
                "UPDATE work_scheduler_requests SET state='released',revision=revision+1,stop_evidence_ref=%s "
                "WHERE id=%s AND generation=%s AND revision=%s AND state='held'",
                (proof.evidence_ref, identifier, generation, expected_revision),
            ).rowcount
            if changed != 1:
                raise SchedulerConflict("scheduler reservation fence changed")
            row = self._row(connection, identifier)
            self._event(connection, row, actor_id, "scheduler.released")
            return self._view(row)

    def _replacement_checked(
        self,
        connection,
        identifier,
        *,
        generation,
        expected_revision,
        expected_attempt_revision,
        expected_work_revision,
    ):
        """Fence the one reconcile writer which may be replaced after stopping.

        This is deliberately separate from ``_owner(active=False)``: its
        terminal-only rule remains the compatibility contract for ordinary
        releases.  Reconcile can use this narrower exception only when every
        scheduler, attempt and work fence is still exact.
        """
        _integer(generation)
        _integer(expected_revision)
        _integer(expected_attempt_revision)
        _integer(expected_work_revision)
        row = self._row(connection, identifier)
        if (
            row["generation"] != generation
            or row["revision"] != expected_revision
            or row["state"] != "held"
        ):
            raise SchedulerConflict("scheduler reservation fence changed")
        owner = connection.execute(
            "SELECT a.*,w.job_id,w.state AS work_state,w.revision AS work_revision,"
            "w.accepted_result_id,j.state AS job_state "
            "FROM work_attempts a JOIN work_items w ON w.id=a.work_item_id "
            "JOIN work_jobs j ON j.id=w.job_id WHERE a.id=%s",
            (row["attempt_id"],),
        ).fetchone()
        latest = connection.execute(
            "SELECT id FROM work_attempts WHERE work_item_id=%s ORDER BY generation DESC LIMIT 1",
            (row["work_item_id"],),
        ).fetchone()
        if (
            owner is None
            or latest is None
            or latest["id"] != row["attempt_id"]
            or owner["job_id"] != row["job_id"]
            or owner["work_item_id"] != row["work_item_id"]
            or owner["generation"] != generation
            or owner["revision"] != expected_attempt_revision
            or owner["state"] != "reconcile"
            or owner["cleanup_state"] == "pending"
            or owner["work_state"] != "reconcile"
            or owner["work_revision"] != expected_work_revision
            or owner["accepted_result_id"] is not None
            or owner["job_state"] in {"revoked", "completed", "failed"}
        ):
            raise SchedulerConflict("reconcile writer or work fence changed")
        return row, WriterIdentity(
            row["job_id"],
            row["work_item_id"],
            row["attempt_id"],
            generation,
            owner["revision"],
            owner["state"],
            owner["lease_expires_at"],
        )

    def release_for_replacement(
        self,
        identifier,
        *,
        generation,
        expected_revision,
        expected_attempt_revision,
        expected_work_revision,
        actor_id,
    ) -> dict:
        """Durably release a reconciled slot after exact external stop evidence.

        The external verifier is intentionally outside Work transactions.  A
        fresh write transaction then rechecks every captured fence and returns
        the work snapshot from the release commit for the caller's retry CAS.
        """
        _actor(actor_id)
        arguments = dict(
            generation=generation,
            expected_revision=expected_revision,
            expected_attempt_revision=expected_attempt_revision,
            expected_work_revision=expected_work_revision,
        )
        with self.repository.read_snapshot() as connection:
            _, owner = self._replacement_checked(connection, identifier, **arguments)
        try:
            proof = self._stop_verifier(owner) if self._stop_verifier is not None else None
        except Exception as error:
            raise SchedulerConflict("server stop verification failed") from error
        if not isinstance(proof, StoppedWriter) or (
            proof.attempt_id,
            proof.generation,
            proof.attempt_revision,
        ) != (owner.attempt_id, owner.generation, owner.revision):
            raise SchedulerConflict("server proof of irreversible writer cessation required")
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            observed = self._row(connection, identifier)
            # Admission revalidates grant authority under the job row lock
            # before it takes the scheduler aggregate lock.  Take that same
            # parent lock first here so the release event's job FK cannot wait
            # on admission while admission waits on our aggregate lock.
            self.repository._lock_job_for_write(connection, observed["job_id"])
            self._lock_aggregate(connection)
            row, verified_owner = self._replacement_checked(connection, identifier, **arguments)
            if row["job_id"] != observed["job_id"]:
                raise SchedulerConflict("scheduler request job changed before release")
            if (
                proof.attempt_id,
                proof.generation,
                proof.attempt_revision,
            ) != (
                verified_owner.attempt_id,
                verified_owner.generation,
                verified_owner.revision,
            ):
                raise SchedulerConflict("server proof no longer matches the reconciled writer")
            changed = connection.execute(
                "UPDATE work_scheduler_requests SET state='released',revision=revision+1,"
                "stop_evidence_ref=%s WHERE id=%s AND state='held' AND revision=%s",
                (proof.evidence_ref, identifier, expected_revision),
            ).rowcount
            if changed != 1:
                raise SchedulerConflict("scheduler reservation fence changed")
            row = self._row(connection, identifier)
            self._event(
                connection,
                row,
                actor_id,
                "scheduler.released",
                metadata={"stop_evidence_ref": proof.evidence_ref},
            )
            return self.repository._work(connection, row["work_item_id"])
