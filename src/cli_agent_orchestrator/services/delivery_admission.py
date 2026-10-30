"""Process-local reservation ownership, independent of delivery or authority."""

import threading
from dataclasses import dataclass
from itertools import count
from typing import Literal

from cli_agent_orchestrator.services.admission_policy import (
    AdmissionDecision,
    AdmissionPolicy,
    CapacityObservation,
    CapacityReason,
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


class DeliveryReservation:
    """A handoff can be cancelled only before one physical worker starts it."""

    def __init__(self, owner: "DeliveryAdmission", key: int, receiver: str):
        self._owner = owner
        self._key = key
        self._receiver = receiver
        self._state: ReservationState = "reserved"

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
            return self._owner._release_locked(self)


class DeliveryAdmission:
    """One ledger shared by local entry paths; not a cluster reservation.

    All outstanding reservations are charged against a fresh resource decision.
    This is conservative when measured headroom already includes started work.
    Re-evaluating an old sample cannot add its capacity to this ledger again.
    """

    def __init__(self, policy: AdmissionPolicy):
        self._policy = policy
        self._guard = threading.RLock()
        self._leases: dict[int, DeliveryReservation] = {}
        self._receivers: dict[str, int] = {}
        self._sequence = count()
        self._started = 0
        self._accepting = True
        self._generation = 0
        self._observation: CapacityObservation | None = None
        self._decision = AdmissionDecision(0, "unknown_capacity")

    @property
    def generation(self) -> int:
        with self._guard:
            return self._generation

    def observe(self, observation: CapacityObservation, *, now: float, generation: int) -> bool:
        validate_clock(now)
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise ValueError("generation must be an integer")
        decision = evaluate_capacity(self._policy, observation, now=now)
        with self._guard:
            if not self._accepting or generation != self._generation:
                return False
            if decision.reason == "future_observation":
                # A corrupt future timestamp must not block later valid samples.
                return False
            if (
                self._observation is not None
                and observation.observed_at <= self._observation.observed_at
            ):
                return False
            self._observation = observation
            self._decision = decision
            return True

    def _current_decision(self, now: float) -> AdmissionDecision:
        if self._observation is None:
            return AdmissionDecision(0, "unknown_capacity")
        # Re-evaluation only checks time/coverage; never adds to an old budget.
        return evaluate_capacity(self._policy, self._observation, now=now)

    def try_reserve(self, receiver: str, *, now: float) -> AdmissionAttempt:
        validate_clock(now)
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
            if len(self._leases) >= decision.available_slots:
                return AdmissionAttempt(None, "resource_exhausted")
            key = next(self._sequence)
            lease = DeliveryReservation(self, key, receiver)
            self._leases[key] = lease
            self._receivers[receiver] = key
            return AdmissionAttempt(lease, "capacity_available")

    def _release_locked(self, lease: DeliveryReservation) -> bool:
        if self._leases.get(lease._key) is not lease:
            return False
        if lease._state == "started":
            self._started -= 1
        self._leases.pop(lease._key)
        if self._receivers.get(lease._receiver) == lease._key:
            self._receivers.pop(lease._receiver)
        lease._state = "released"
        return True

    def stop_accepting(self) -> None:
        with self._guard:
            self._accepting = False
            for lease in tuple(self._leases.values()):
                if lease._state == "reserved":
                    self._release_locked(lease)

    def start_accepting(self) -> None:
        with self._guard:
            if self._leases:
                raise RuntimeError("active delivery reservations must drain before restart")
            self._generation += 1
            self._observation = None
            self._decision = AdmissionDecision(0, "unknown_capacity")
            self._accepting = True

    def snapshot(self, *, now: float) -> AdmissionSnapshot:
        validate_clock(now)
        with self._guard:
            decision = self._current_decision(now)
            limit = decision.available_slots if self._accepting else 0
            inflight = len(self._leases)
            reason: AdmissionReason = decision.reason
            if not self._accepting:
                reason = "stopping"
            elif limit > 0 and inflight >= limit:
                reason = "resource_exhausted"
            age = now - self._observation.observed_at if self._observation else None
            return AdmissionSnapshot(
                accepting=self._accepting,
                generation=self._generation,
                inflight=inflight,
                reserved=inflight - self._started,
                started=self._started,
                limit=limit,
                available_slots=max(0, limit - inflight),
                debt=max(0, inflight - limit),
                reason=reason,
                sample_age_s=age if age is not None and age >= 0 else None,
            )
