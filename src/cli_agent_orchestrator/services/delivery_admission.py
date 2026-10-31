"""Process-local reservation ownership, independent of delivery or authority."""

import os
import threading
from dataclasses import dataclass
from itertools import count
from typing import Literal

from cli_agent_orchestrator.clients.storage_admission import (
    StorageAdmissionUnavailable,
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


@dataclass(frozen=True)
class AdmissionAttempt:
    reservation: "DeliveryReservation | None"
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
        self, owner: "DeliveryAdmission", key: int, receiver: str, footprint: ResourceAmounts
    ):
        self._owner = owner
        self._key = key
        self._receiver = receiver
        self._state: ReservationState = "reserved"
        self._footprint = footprint
        self._remaining = footprint
        self._storage_stage: StorageStageReservation | None = None
        self._storage_connections: set[StorageConnectionReservation] = set()
        self._retirement_requested = False

    @property
    def storage_process_current(self) -> bool:
        return self._owner._pid == os.getpid()

    @property
    def footprint(self) -> ResourceAmounts:
        return self._footprint

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
            return True

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
            if self._state == "released" or self._retirement_requested:
                return False
            if self._state != "started":
                raise RuntimeError("delivery reservation has not started")
            self._retirement_requested = True
            owner._storage_retirement_pending += 1
            if self._storage_stage is not None or self._storage_connections:
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
        self._committed = ResourceAmounts()
        self._pid = os.getpid()
        self._process_guard = threading.RLock()
        self._leases: dict[int, DeliveryReservation] = {}
        self._pending: dict[int, DeliveryReservation] = {}
        self._receivers: dict[str, int] = {}
        self._sequence = count()
        self._started = 0
        self._storage_connection_loans = 0
        self._additional_storage_connections = 0
        self._storage_retirement_pending = 0
        self._accepting = True
        self._generation = 0
        self._observation: CapacityObservation | None = None
        self._observation_ceiling: ResourceAmounts | None = None
        self._decision = AdmissionDecision(0, "unknown_capacity")

    @property
    def _guard(self) -> threading.RLock:
        if self._pid != os.getpid():
            raise StorageAdmissionUnavailable()
        return self._process_guard

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

    def _current_decision(self, now: float, *, charged: bool = True) -> AdmissionDecision:
        committed = self._committed if charged else ResourceAmounts()
        if self._observation is None or storage_close_uncertainty():
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

    def _release_locked(self, lease: DeliveryReservation) -> bool:
        if self._leases.get(lease._key) is not lease:
            return False
        if lease._storage_connections or lease._storage_stage is not None:
            raise RuntimeError("active storage stage must finish physically first")
        if lease._retirement_requested:
            self._storage_retirement_pending -= 1
        if lease._state == "started":
            self._started -= 1
        self._committed = self._committed - lease._remaining
        lease._remaining = ResourceAmounts()
        self._leases.pop(lease._key)
        self._pending.pop(lease._key, None)
        if self._receivers.get(lease._receiver) == lease._key:
            self._receivers.pop(lease._receiver)
        lease._state = "released"
        return True

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
            decision = self._current_decision(now)
            limit = (
                self._current_decision(now, charged=False).available_slots if self._accepting else 0
            )
            inflight = len(self._leases)
            reason: AdmissionReason = decision.reason
            if not self._accepting:
                reason = "stopping"
            debt = decision.resource_debt
            if not self._accepting:
                debt = self._committed
            age = now - self._observation.observed_at if self._observation else None
            return AdmissionSnapshot(
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
                    self._committed.storage_slots - self._additional_storage_connections
                ),
                storage_retirement_pending=self._storage_retirement_pending,
            )
