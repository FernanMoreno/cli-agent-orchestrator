"""Process-local reservation ownership, independent of delivery or authority."""

import asyncio
import os
import queue
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import count
from typing import Literal, Protocol

from cli_agent_orchestrator.clients.storage_admission import (
    StorageAdmissionUnavailable,
    check_storage_admission,
    physical_storage_scope,
    storage_close_uncertainty,
)
from cli_agent_orchestrator.services.admission_policy import (
    AdmissionDecision,
    AdmissionPolicy,
    CapacityObservation,
    CapacityReason,
    ResourceAmounts,
    evaluate_capacity,
    validate_clock,
)

AdmissionReason = CapacityReason | Literal["receiver_busy", "stopping"]
ReservationState = Literal["reserved", "started", "released"]


class SharedStorageCoordinator(Protocol):
    _owner: "DeliveryAdmission"

    def prepare(self, lease: "DeliveryReservation") -> None: ...


@dataclass(frozen=True)
class AdmissionAttempt:
    reservation: "DeliveryReservation | None"
    reason: AdmissionReason


@dataclass(frozen=True)
class StorageAdmissionAttempt:
    reservation: "StorageReservation | None"
    reason: AdmissionReason


@dataclass(frozen=True)
class AdmissionSnapshot:
    accepting: bool
    generation: int
    inflight: int
    reserved: int
    started: int
    limit: int
    available_slots: int
    debt: int
    reason: AdmissionReason
    sample_age_s: float | None
    committed: ResourceAmounts
    resource_debt: ResourceAmounts
    storage_connection_loans: int = 0
    # Retained admitted credits include reserved handoffs not yet started.
    storage_completion_credits: int = 0
    storage_retirement_pending: int = 0
    storage_only_inflight: int = 0
    storage_only_started: int = 0
    control_storage_slots: int = 0
    control_inflight: int = 0
    control_connection_loans: int = 0

    @property
    def delivery_inflight(self) -> int:
        return self.inflight - self.storage_only_inflight

    @property
    def delivery_started(self) -> int:
        return self.started - self.storage_only_started


@dataclass(frozen=True)
class AdmissionLedgerDiagnostic:
    """One coherent local basis; never a grant or a count of live agents."""

    snapshot: AdmissionSnapshot
    policy: AdmissionPolicy
    budget: ResourceAmounts | None
    observation: CapacityObservation | None
    ceiling: ResourceAmounts | None
    decision: AdmissionDecision


@dataclass(frozen=True, eq=False)
class DeliveryRetirementHold:
    """Exact local hold, consumed only after trusted shared retirement.

    A notification is not evidence. Only the lease owner establishes physical
    readiness; no callback, SQL or wait runs while it holds the ledger guard.
    The caller must reconcile uncertain shared results before calling finish.
    """

    _lease: "DeliveryReservation"
    _signal: threading.Event = field(default_factory=threading.Event, init=False)
    _cleanup_leases: set["DeliveryReservation"] = field(default_factory=set, init=False)

    def _ready_locked(self, owner: "DeliveryAdmission") -> bool:
        lease = self._lease
        return (
            lease._owner is owner
            and owner._leases.get(lease._key) is lease
            and lease._retirement_hold is self
            and lease._state == "started"
            and lease._retirement_requested
            and lease._retirement_ready
            and lease._storage_stage is None
            and not lease._storage_connections
            and not lease._physical_effects
        )

    @property
    def physical_ready(self) -> bool:
        owner = self._lease._owner
        with owner._guard:
            return self._ready_locked(owner)

    def finish(self) -> bool:
        owner = self._lease._owner
        with owner._guard:
            if not self._ready_locked(owner) or self._cleanup_leases:
                return False
            self._lease._retirement_hold = None
            return owner._release_locked(self._lease)


@dataclass(frozen=True, eq=False)
class PhysicalEffectReservation:
    """Exact native owner; thread completion never confirms this token."""

    _lease: "DeliveryReservation"
    _owners: list[tuple[object, Callable[[], bool]]] = field(
        default_factory=list, init=False, repr=False
    )

    def __copy__(self):
        raise TypeError("physical effect ownership is not transferable")

    def __deepcopy__(self, memo):
        raise TypeError("physical effect ownership is not transferable")

    def __reduce__(self):
        raise TypeError("physical effect ownership is not transferable")

    def _owner(self) -> "DeliveryAdmission":
        owner = self._lease._owner
        if owner._pid != os.getpid():
            raise StorageAdmissionUnavailable()
        return owner

    def retain(self, native_owner: object, closed: Callable[[], bool]) -> None:
        owner = self._owner()
        with owner._guard:
            if self not in self._lease._physical_effects:
                raise StorageAdmissionUnavailable()
            if not callable(closed):
                raise ValueError("native owner requires a closure observer")
            if not any(value[0] is native_owner for value in self._owners):
                self._owners.append((native_owner, closed))

    def finish(self) -> bool:
        owner = self._owner()
        lease = self._lease
        with owner._guard:
            if owner._leases.get(lease._key) is not lease or self not in lease._physical_effects:
                return False
            owners = tuple(self._owners)
        # Kernel/native observations run outside the admission guard.
        try:
            if any(closed() is not True for _, closed in owners):
                return False
        except Exception:
            return False
        with owner._guard:
            if owner._leases.get(lease._key) is not lease or self not in lease._physical_effects:
                return False
            if len(self._owners) != len(owners) or any(
                a is not b for a, b in zip(self._owners, owners)
            ):
                return False
            lease._physical_effects.remove(self)
            self._owners.clear()
            if (
                lease._retirement_requested
                and not lease._physical_effects
                and not lease._storage_connections
                and lease._storage_stage is None
            ):
                owner._release_locked(lease)
            return True


@dataclass(frozen=True, eq=False)
class StorageStageReservation:
    """One physical storage stage; copies cannot retire the registered owner."""

    _lease: "DeliveryReservation"

    def finish(self) -> bool:
        lease = self._lease
        owner = lease._owner
        with owner._guard:
            if (
                lease._state != "started"
                or owner._leases.get(lease._key) is not lease
                or lease._storage_stage is not self
            ):
                return False
            lease._storage_stage = None
            lease._release_storage_locked()
            if lease._retirement_requested:
                owner._release_locked(lease)
            return True


@dataclass(frozen=True, eq=False)
class StorageConnectionReservation:
    """An exact physical loan; the initial closing credit stays reserved."""

    _lease: "DeliveryReservation"
    _additional: bool

    def finish(self) -> bool:
        lease = self._lease
        owner = lease._owner
        with owner._guard:
            if (
                lease._state != "started"
                or owner._leases.get(lease._key) is not lease
                or self not in lease._storage_connections
            ):
                return False
            lease._storage_connections.remove(self)
            owner._storage_connection_loans -= 1
            if self._additional:
                owner._additional_storage_connections -= 1
                unit = ResourceAmounts(storage_slots=1)
                owner._committed = owner._committed - unit
                lease._remaining = lease._remaining - unit
            if lease._retirement_requested and not lease._storage_connections:
                owner._release_locked(lease)
            return True


class DeliveryReservation:
    """A handoff can be cancelled only before one physical worker starts it."""

    def __init__(
        self, owner: "DeliveryAdmission", key: int, receiver: str | None, footprint: ResourceAmounts
    ):
        self._owner = owner
        self._key = key
        self._receiver = receiver
        self._state: ReservationState = "reserved"
        self._footprint = footprint
        self._remaining = footprint
        self._storage_stage: StorageStageReservation | None = None
        self._storage_connections: set[StorageConnectionReservation] = set()
        self._physical_effects: set[PhysicalEffectReservation] = set()
        self._retirement_requested = False
        self._retirement_hold: DeliveryRetirementHold | None = None
        self._retirement_ready = False

    @property
    def storage_process_current(self) -> bool:
        return self._owner._pid == os.getpid()

    @property
    def footprint(self) -> ResourceAmounts:
        return self._footprint

    def prepare_storage_scope(self) -> None:
        """Trusted physical-worker hook; control SQL must not prepare recursively."""
        owner = self._owner
        with owner._guard:
            if owner._control_lease is self:
                return
            if (
                owner._leases.get(self._key) is not self
                or self._state != "started"
                or self._retirement_requested
            ):
                raise StorageAdmissionUnavailable()
            coordinator = owner._shared_coordinator
            if owner._shared_required and (not owner._shared_ready or coordinator is None):
                raise StorageAdmissionUnavailable()
        if coordinator is None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise StorageAdmissionUnavailable()  # SQL belongs in the owned worker.
        coordinator.prepare(self)

    @property
    def state(self) -> ReservationState:
        with self._owner._guard:
            return self._state

    def start(self) -> bool:
        owner = self._owner
        with owner._guard:
            if (
                self._state != "reserved"
                or not owner._accepting
                or owner._leases.get(self._key) is not self
            ):
                return False
            self._state = "started"
            owner._pending.pop(self._key)
            owner._started += 1
            if isinstance(self, StorageReservation):
                owner._storage_only_started += 1
            return True

    def hold_retirement(self) -> DeliveryRetirementHold:
        """Register before shared preparation; an uncertain commit must stay held."""
        owner = self._owner
        with owner._guard:
            if (
                self._state != "started"
                or owner._leases.get(self._key) is not self
                or self._retirement_requested
                or self._retirement_hold is not None
                or self._storage_connections
                or self._storage_stage is not None
                or owner._control_lease is self
            ):
                raise RuntimeError("retirement hold requires an exact started data lease")
            hold = DeliveryRetirementHold(self)
            self._retirement_hold = hold
            return hold

    def hold_physical_effect(self) -> PhysicalEffectReservation:
        """Private native backend hook, registered before possible external start."""
        owner = self._owner
        if owner._pid != os.getpid():
            raise StorageAdmissionUnavailable()
        with owner._guard:
            if (
                owner._leases.get(self._key) is not self
                or self._state != "started"
                or self._retirement_requested
                or owner._control_lease is self
            ):
                raise StorageAdmissionUnavailable()
            token = PhysicalEffectReservation(self)
            self._physical_effects.add(token)
            return token

    def cancel_before_start(self) -> bool:
        with self._owner._guard:
            if self._state != "reserved":
                return False
            return self._owner._release_locked(self)

    def finish(self) -> bool:
        """Called by the synchronous worker's finally, never its awaiter."""
        with self._owner._guard:
            if self._state == "released":
                return False
            if self._state != "started":
                raise RuntimeError("delivery reservation has not started")
            if self._retirement_hold is not None or self._physical_effects:
                return self.finish_after_storage()
            if self._storage_stage is not None or self._storage_connections:
                raise RuntimeError("active storage stage must finish physically first")
            return self._owner._release_locked(self)

    def finish_after_storage(self) -> bool:
        """Physical body is done; retain costs until its exact loans retire.

        This is a handoff to already owned physical cleanup, not a timeout or
        cancellation proof. No new loan is allowed after the handoff. A failed
        close must keep its strong handle/token in the close registry; only a
        later physical witness lets the last token release this invocation.
        """
        owner = self._owner
        with owner._guard:
            if (
                owner._leases.get(self._key) is not self
                or self._state == "released"
                or self._retirement_requested
            ):
                return False
            if self._state != "started":
                raise RuntimeError("delivery reservation has not started")
            self._retirement_requested = True
            owner._storage_retirement_pending += 1
            if (
                self._storage_stage is not None
                or self._storage_connections
                or self._physical_effects
            ):
                return False
            return owner._release_locked(self)

    def try_acquire_storage_connection(self, *, now: float) -> StorageConnectionReservation | None:
        """Borrow an existing closing credit, or charge one additional connection.

        Reusing an already admitted credit never creates new allowance, including
        during drain or after a stale observation. Additional physical loans need
        a fresh decision and cannot grow after admission closes. The borrower must
        retain its exact token through setup, SQL, reset and physical return/close.
        """
        validate_clock(now)
        owner = self._owner
        with owner._guard:
            if (
                self._state != "started"
                or owner._leases.get(self._key) is not self
                or self._storage_stage is not None
                or self._retirement_requested
            ):
                return None
            if (
                self._retirement_hold is not None
                and len(self._storage_connections) >= self._footprint.storage_slots
            ):
                return None  # No unaccounted growth beyond the mapped footprint.
            additional = self._remaining.storage_slots == len(self._storage_connections)
            if additional:
                if not owner._accepting:
                    return None
                decision = owner._current_decision(now)
                if (
                    decision.reason not in ("capacity_available", "resource_exhausted")
                    or decision.resource_capacity.storage_slots <= owner._committed.storage_slots
                ):
                    return None
            connection = StorageConnectionReservation(self, additional)
            if additional:
                unit = ResourceAmounts(storage_slots=1)
                owner._committed = owner._committed + unit
                self._remaining = self._remaining + unit
            self._storage_connections.add(connection)
            owner._storage_connection_loans += 1
            if additional:
                owner._additional_storage_connections += 1
            return connection

    def take_storage_stage(self, *, now: float) -> StorageStageReservation | None:
        """Claim already charged storage or reserve storage alone for reentry.

        This does not start SQL or wait for capacity. The physical caller owns
        the token through completion, including uncertain cleanup; an awaiter
        cannot release it. Memory, CPU and receiver ownership remain charged.
        """
        validate_clock(now)
        owner = self._owner
        with owner._guard:
            if (
                self._state != "started"
                or owner._leases.get(self._key) is not self
                or self._storage_stage is not None
                or self._storage_connections
                or self._retirement_requested
            ):
                return None
            needs_storage = not self._remaining.storage_slots
            if needs_storage:
                if self._retirement_hold is not None and not self._footprint.storage_slots:
                    return None
                if not owner._accepting:
                    return None
                decision = owner._current_decision(now)
                if (
                    decision.reason not in ("capacity_available", "resource_exhausted")
                    or decision.resource_capacity.storage_slots <= owner._committed.storage_slots
                ):
                    return None
            stage = StorageStageReservation(self)
            if needs_storage:
                unit = ResourceAmounts(storage_slots=1)
                owner._committed = owner._committed + unit
                self._remaining = self._remaining + unit
            self._storage_stage = stage
            return stage

    def release_storage(self) -> bool:
        """Physical storage-stage completion, never cancellation of its awaiter."""
        owner = self._owner
        with owner._guard:
            if (
                self._state != "started"
                or owner._leases.get(self._key) is not self
                or self._storage_stage is not None
                or self._storage_connections
                or not self._remaining.storage_slots
            ):
                return False
            return self._release_storage_locked()

    def _release_storage_locked(self) -> bool:
        released = ResourceAmounts(storage_slots=self._remaining.storage_slots)
        self._owner._committed = self._owner._committed - released
        self._remaining = self._remaining - released
        return True


class StorageReservation(DeliveryReservation):
    """Storage operation using the common physical lifecycle, without a receiver."""

    def __init__(self, owner: "DeliveryAdmission", key: int, footprint: ResourceAmounts):
        super().__init__(owner, key, None, footprint)


class _StorageControlReservation(StorageReservation):
    """Already started infrastructure credit, excluded from normal data costs."""

    def __init__(
        self,
        owner: "DeliveryAdmission",
        key: int,
        footprint: ResourceAmounts,
        completion: DeliveryRetirementHold | None = None,
    ):
        super().__init__(owner, key, footprint)
        self._completion_hold = completion

    def try_acquire_storage_connection(self, *, now: float) -> StorageConnectionReservation | None:
        validate_clock(now)
        owner = self._owner
        with owner._guard:
            if (
                owner._control_lease is not self
                or owner._leases.get(self._key) is not self
                or self._state != "started"
                or self._retirement_requested
                or self._storage_connections
            ):
                return None
            loan = StorageConnectionReservation(self, False)
            self._storage_connections.add(loan)
            owner._storage_connection_loans += 1
            return loan

    def take_storage_stage(self, *, now: float) -> StorageStageReservation | None:
        validate_clock(now)
        return None  # Control cannot convert its infrastructure credit into a data stage.


class StorageControlPermit:
    """Private server wiring capability; exact identity, never a capacity claim."""

    def __init__(self, owner: "DeliveryAdmission"):
        self._owner = owner
        self._lane_gate = threading.Lock()
        self._lane_owner: int | None = None

    def belongs_to(self, owner: "DeliveryAdmission") -> bool:
        return self._owner is owner and owner._control_permit is self

    @contextmanager
    def _serial(self, *, wait: bool) -> Iterator[int]:
        owner = self._owner
        with owner._guard:  # Reject a child before touching inherited lane locks.
            if type(wait) is not bool:
                raise ValueError("control wait must be boolean")
            if not self.belongs_to(owner) or self._lane_owner == threading.get_ident():
                raise StorageAdmissionUnavailable()
            generation = owner._generation
        if not self._lane_gate.acquire(blocking=wait):
            raise StorageAdmissionUnavailable()
        try:
            with owner._guard:
                self._lane_owner = threading.get_ident()
            yield generation
        finally:
            with owner._guard:
                self._lane_owner = None
            self._lane_gate.release()

    @contextmanager
    def operation(
        self,
        *,
        clock: Callable[[], float],
        retirement: DeliveryRetirementHold | None = None,
        wait: bool = False,
    ) -> Iterator[None]:
        owner = self._owner
        validate_clock(clock())
        with self._serial(wait=wait) as generation:
            with owner._guard:
                completion = type(
                    retirement
                ) is DeliveryRetirementHold and retirement._ready_locked(owner)
                if (
                    not self.belongs_to(owner)
                    or (not owner._accepting and not completion)
                    or (owner._generation != generation and not completion)
                    or (retirement is not None and not completion)
                    or owner._control_lease is not None
                    or storage_close_uncertainty()
                ):
                    raise StorageAdmissionUnavailable()
                key = next(owner._sequence)
                lease = _StorageControlReservation(owner, key, ResourceAmounts(), retirement)
                if retirement is not None:
                    retirement._cleanup_leases.add(lease)
                lease._state = "started"
                owner._control_lease = lease
                owner._leases[key] = lease
                owner._started += 1
                owner._storage_only_inflight += 1
                owner._storage_only_started += 1
            try:
                with physical_storage_scope(lease, clock=clock):
                    try:
                        yield
                    finally:
                        check_storage_admission()
            finally:
                lease.finish_after_storage()


class DeliveryAdmission:
    """One ledger shared by local entry paths; not a cluster reservation.

    All outstanding reservations are charged against a fresh resource decision.
    This is conservative when measured headroom already includes started work.
    Re-evaluating an old sample cannot add its capacity to this ledger again.
    """

    def __init__(self, policy: AdmissionPolicy, *, budget: ResourceAmounts | None = None):
        self._validate_configuration(policy, budget)
        self._policy = policy
        self._budget = budget
        self._control_permit: StorageControlPermit | None = None
        self._control_lease: _StorageControlReservation | None = None
        self._committed = ResourceAmounts()
        self._pid = os.getpid()
        self._process_guard = threading.RLock()
        self._leases: dict[int, DeliveryReservation] = {}
        self._retirement_queue: queue.SimpleQueue[DeliveryRetirementHold | None] = (
            queue.SimpleQueue()
        )
        self._shared_required = False
        self._shared_ready = False
        self._shared_coordinator: SharedStorageCoordinator | None = None
        self._pending: dict[int, DeliveryReservation] = {}
        self._receivers: dict[str, int] = {}
        self._sequence = count()
        self._started = 0
        self._storage_only_inflight = 0
        self._storage_only_started = 0
        self._storage_connection_loans = 0
        self._additional_storage_connections = 0
        self._storage_retirement_pending = 0
        self._accepting = True
        self._generation = 0
        self._observation: CapacityObservation | None = None
        self._observation_ceiling: ResourceAmounts | None = None
        self._decision = AdmissionDecision(0, "unknown_capacity")

    @classmethod
    def with_storage_control(
        cls, policy: AdmissionPolicy
    ) -> tuple["DeliveryAdmission", StorageControlPermit]:
        """Trusted process activation, before binding its consumers."""
        owner = cls(policy)
        permit = StorageControlPermit(owner)
        owner._control_permit = permit
        return owner, permit

    @property
    def control_storage_slots(self) -> int:
        with self._guard:
            return int(self._control_permit is not None)

    @property
    def _guard(self) -> threading.RLock:
        if self._pid != os.getpid():
            raise StorageAdmissionUnavailable()
        return self._process_guard

    @property
    def shared_required(self) -> bool:
        with self._guard:
            return self._shared_required

    @property
    def shared_ready(self) -> bool:
        with self._guard:
            return self._shared_ready and self._accepting

    def require_shared_admission(self) -> None:
        """Configuration closes data; it never issues resource authority."""
        with self._guard:
            if self._shared_required:
                return
            if any(lease is not self._control_lease for lease in self._leases.values()):
                raise RuntimeError("normal operations must retire before shared activation")
            self._shared_required = True
            self._shared_ready = False

    def bind_shared_admission(self, coordinator: SharedStorageCoordinator) -> None:
        if coordinator._owner is not self:
            raise ValueError("shared coordinator belongs to another owner")
        with self._guard:
            if (
                not self._shared_required
                or not self._accepting
                or any(lease is not self._control_lease for lease in self._leases.values())
                or self._shared_coordinator not in (None, coordinator)
            ):
                raise RuntimeError("shared admission cannot replace a live owner")
            self._shared_coordinator = coordinator
            self._shared_ready = True

    @staticmethod
    def _validate_configuration(policy: AdmissionPolicy, budget: ResourceAmounts | None) -> None:
        if not isinstance(policy, AdmissionPolicy) or (
            budget is not None and not isinstance(budget, ResourceAmounts)
        ):
            raise ValueError("admission configuration must use validated policy and amounts")

    def reconfigure(
        self,
        policy: AdmissionPolicy,
        *,
        expected_generation: int,
        budget: ResourceAmounts | None = None,
    ) -> int | None:
        """Fence old I/O and retire handoffs; running costs remain unchanged.

        Omitting a budget retains the current allowance, never removes it.
        The caller must capture expected_generation before configuration I/O.
        """
        self._validate_configuration(policy, budget)
        if (
            isinstance(expected_generation, bool)
            or not isinstance(expected_generation, int)
            or expected_generation < 0
        ):
            raise ValueError("generation must be a nonnegative integer")
        with self._guard:
            if not self._accepting or expected_generation != self._generation:
                return None
            candidate_budget = self._budget if budget is None else budget
            if policy == self._policy and candidate_budget == self._budget:
                return self._generation
            self._policy = policy
            self._budget = candidate_budget
            self._generation += 1
            self._observation = None
            self._observation_ceiling = None
            for lease in tuple(self._pending.values()):
                self._release_locked(lease)
            return self._generation

    def invalidate(self, *, expected_generation: int) -> bool:
        """Discard authority/resource knowledge without retiring started work."""
        if isinstance(expected_generation, bool) or not isinstance(expected_generation, int):
            raise ValueError("generation must be an integer")
        with self._guard:
            if not self._accepting or expected_generation != self._generation:
                return False
            self._generation += 1
            self._observation = None
            self._observation_ceiling = None
            for lease in tuple(self._pending.values()):
                self._release_locked(lease)
            return True

    @property
    def generation(self) -> int:
        with self._guard:
            return self._generation

    def observe(
        self,
        observation: CapacityObservation,
        *,
        now: float,
        generation: int,
        ceiling: ResourceAmounts | None = None,
    ) -> bool:
        validate_clock(now)
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise ValueError("generation must be an integer")
        if ceiling is not None and not isinstance(ceiling, ResourceAmounts):
            raise ValueError("observation ceiling must use validated amounts")
        with self._guard:
            if not self._accepting or generation != self._generation:
                return False
            decision = evaluate_capacity(self._policy, observation, now=now, budget=self._budget)
            if decision.reason == "future_observation":
                # A corrupt future timestamp must not block later valid samples.
                return False
            if (
                self._observation is not None
                and observation.observed_at <= self._observation.observed_at
            ):
                return False
            self._observation = observation
            self._observation_ceiling = ceiling
            self._decision = decision
            # Newest unstarted handoffs yield first under pressure. Started
            # work is never scanned or retired by a resource observation.
            if self._current_decision(now).resource_debt != ResourceAmounts():
                for lease in reversed(tuple(self._pending.values())):
                    self._release_locked(lease)
                    if self._current_decision(now).resource_debt == ResourceAmounts():
                        break
            return True

    def _current_decision(
        self, now: float, *, charged: bool = True, close_uncertain: bool | None = None
    ) -> AdmissionDecision:
        committed = self._committed if charged else ResourceAmounts()
        if (
            (self._shared_required and not self._shared_ready)
            or self._observation is None
            or (storage_close_uncertainty() if close_uncertain is None else close_uncertain)
        ):
            return AdmissionDecision(0, "unknown_capacity", resource_debt=committed)
        budget = self._budget
        if self._observation_ceiling is not None:
            ceiling = self._observation_ceiling
            budget = (
                ceiling
                if budget is None
                else ResourceAmounts(
                    min(budget.memory_bytes, ceiling.memory_bytes),
                    min(budget.cpu_millicores, ceiling.cpu_millicores),
                    min(budget.storage_slots, ceiling.storage_slots),
                )
            )
        # Re-evaluation checks freshness and subtracts immutable committed costs.
        return evaluate_capacity(
            self._policy, self._observation, now=now, committed=committed, budget=budget
        )

    def owns_normal_storage(self, lease: object) -> bool:
        """Exact registered normal lease; a control credit cannot serve data SQL."""
        with self._guard:
            return (
                isinstance(lease, DeliveryReservation)
                and lease._owner is self
                and self._leases.get(lease._key) is lease
                and lease is not self._control_lease
                and lease._state == "started"
                and not lease._retirement_requested
            )

    def can_reserve(self, receiver: str, *, now: float) -> bool:
        """Advisory eligibility only; an effect still needs try_reserve and start."""
        validate_clock(now)
        if not isinstance(receiver, str) or not receiver.strip():
            raise ValueError("receiver must be a nonempty identity")
        with self._guard:
            return bool(
                self._accepting
                and receiver not in self._receivers
                and self._current_decision(now).available_slots
            )

    def try_reserve(self, receiver: str, *, now: float, storage_slots: int = 1) -> AdmissionAttempt:
        """Reserve the trusted operation's complete minimum physical footprint.

        Internal callers declare required overlapping storage stages. This is
        consumption per operation, never a global delivery concurrency limit.
        """
        validate_clock(now)
        ResourceAmounts(storage_slots=storage_slots)
        if storage_slots == 0:
            raise ValueError("storage_slots must be an integer >= 1")
        if not isinstance(receiver, str) or not receiver.strip():
            raise ValueError("receiver must be a nonempty identity")
        with self._guard:
            if not self._accepting:
                return AdmissionAttempt(None, "stopping")
            if receiver in self._receivers:
                return AdmissionAttempt(None, "receiver_busy")
            decision = self._current_decision(now)
            if not decision.available_slots:
                return AdmissionAttempt(None, decision.reason)
            if (
                self._committed.storage_slots + storage_slots
                > decision.resource_capacity.storage_slots
            ):
                return AdmissionAttempt(None, "resource_exhausted")
            key = next(self._sequence)
            footprint = ResourceAmounts(
                self._policy.memory_per_delivery_bytes,
                self._policy.cpu_per_delivery_millicores,
                storage_slots,
            )
            lease = DeliveryReservation(self, key, receiver, footprint)
            self._committed = self._committed + footprint
            self._leases[key] = lease
            self._pending[key] = lease
            self._receivers[receiver] = key
            return AdmissionAttempt(lease, "capacity_available")

    def try_reserve_storage(self, *, now: float, storage_slots: int = 1) -> StorageAdmissionAttempt:
        """Charge a trusted storage operation against this same resource ledger.

        CPU/RAM delivery headroom does not grant or prevent a storage-only
        operation. Fresh authority and its full storage quantities still apply.
        """
        validate_clock(now)
        footprint = ResourceAmounts(storage_slots=storage_slots)
        if storage_slots == 0:
            raise ValueError("storage_slots must be an integer >= 1")
        with self._guard:
            if not self._accepting:
                return StorageAdmissionAttempt(None, "stopping")
            decision = self._current_decision(now)
            if decision.reason not in {"capacity_available", "resource_exhausted"}:
                return StorageAdmissionAttempt(None, decision.reason)
            if (
                self._committed.storage_slots + storage_slots
                > decision.resource_capacity.storage_slots
            ):
                return StorageAdmissionAttempt(None, "resource_exhausted")
            key = next(self._sequence)
            lease = StorageReservation(self, key, footprint)
            self._committed = self._committed + footprint
            self._leases[key] = lease
            self._pending[key] = lease
            self._storage_only_inflight += 1
            return StorageAdmissionAttempt(lease, "capacity_available")

    def _release_locked(self, lease: DeliveryReservation) -> bool:
        if self._leases.get(lease._key) is not lease:
            return False
        if lease._physical_effects:
            return False
        if lease._storage_connections or lease._storage_stage is not None:
            raise RuntimeError("active storage stage must finish physically first")
        if lease._retirement_hold is not None:
            if not lease._retirement_requested:
                raise RuntimeError("held retirement requires physical body completion")
            if not lease._retirement_ready:
                self._retirement_queue.put(lease._retirement_hold)
            lease._retirement_ready = True
            lease._retirement_hold._signal.set()  # Notification only; no external callback.
            return False
        completion = (
            lease._completion_hold if isinstance(lease, _StorageControlReservation) else None
        )
        if completion is not None and lease not in completion._cleanup_leases:
            raise RuntimeError("control completion ownership is unavailable")
        if lease._retirement_requested:
            self._storage_retirement_pending -= 1
        if lease._state == "started":
            self._started -= 1
            if isinstance(lease, StorageReservation):
                self._storage_only_started -= 1
        if isinstance(lease, StorageReservation):
            self._storage_only_inflight -= 1
        if self._control_lease is lease:
            self._control_lease = None
        self._committed = self._committed - lease._remaining
        lease._remaining = ResourceAmounts()
        self._leases.pop(lease._key)
        self._pending.pop(lease._key, None)
        if lease._receiver is not None and self._receivers.get(lease._receiver) == lease._key:
            self._receivers.pop(lease._receiver)
        lease._state = "released"
        if completion is not None:
            completion._cleanup_leases.remove(lease)
        return True

    def wait_retirement_ready(self, *, timeout_s: float) -> DeliveryRetirementHold | None:
        validate_clock(timeout_s)
        self._guard  # PID validation before touching inherited queue locks.
        try:
            return self._retirement_queue.get(timeout=timeout_s)
        except queue.Empty:
            return None

    def wake_retirement_worker(self) -> None:
        self._guard
        self._retirement_queue.put(None)

    def stop_accepting(self) -> None:
        with self._guard:
            self._accepting = False
            for lease in tuple(self._pending.values()):
                self._release_locked(lease)

    def start_accepting(self) -> None:
        with self._guard:
            if self._leases:
                raise RuntimeError("active delivery reservations must drain before restart")
            self._generation += 1
            self._observation = None
            self._observation_ceiling = None
            self._decision = AdmissionDecision(0, "unknown_capacity")
            self._accepting = True

    def snapshot(self, *, now: float) -> AdmissionSnapshot:
        validate_clock(now)
        with self._guard:
            return self._snapshot_basis_locked(now)[0]

    def _snapshot_basis_locked(self, now: float) -> tuple[AdmissionSnapshot, AdmissionDecision]:
        """Linearize the external close flag once with the guarded ledger basis."""
        uncertain = storage_close_uncertainty()
        decision = self._current_decision(now, close_uncertain=uncertain)
        limit = (
            self._current_decision(now, charged=False, close_uncertain=uncertain).available_slots
            if self._accepting
            else 0
        )
        inflight = len(self._leases)
        reason: AdmissionReason = decision.reason
        if not self._accepting:
            reason = "stopping"
        debt = decision.resource_debt
        if not self._accepting:
            debt = self._committed
        age = now - self._observation.observed_at if self._observation else None
        snapshot = AdmissionSnapshot(
            accepting=self._accepting,
            generation=self._generation,
            inflight=inflight,
            reserved=inflight - self._started,
            started=self._started,
            limit=limit,
            available_slots=decision.available_slots if self._accepting else 0,
            debt=max(
                (debt.memory_bytes + self._policy.memory_per_delivery_bytes - 1)
                // self._policy.memory_per_delivery_bytes,
                (debt.cpu_millicores + self._policy.cpu_per_delivery_millicores - 1)
                // self._policy.cpu_per_delivery_millicores,
                debt.storage_slots,
            ),
            reason=reason,
            sample_age_s=age if age is not None and age >= 0 else None,
            committed=self._committed,
            resource_debt=debt,
            storage_connection_loans=self._storage_connection_loans,
            storage_completion_credits=(
                self._committed.storage_slots
                - self._additional_storage_connections
                + int(self._control_lease is not None)
            ),
            storage_retirement_pending=self._storage_retirement_pending,
            storage_only_inflight=self._storage_only_inflight,
            storage_only_started=self._storage_only_started,
            control_storage_slots=int(self._control_permit is not None),
            control_inflight=int(self._control_lease is not None),
            control_connection_loans=(
                len(self._control_lease._storage_connections) if self._control_lease else 0
            ),
        )
        return snapshot, decision

    def diagnostic(self, *, now: float) -> AdmissionLedgerDiagnostic:
        """Copy immutable quantities and O(1) counters under the same guard."""
        validate_clock(now)
        with self._guard:
            snapshot, decision = self._snapshot_basis_locked(now)
            return AdmissionLedgerDiagnostic(
                snapshot=snapshot,
                policy=self._policy,
                budget=self._budget,
                observation=self._observation,
                ceiling=self._observation_ceiling,
                decision=decision,
            )
