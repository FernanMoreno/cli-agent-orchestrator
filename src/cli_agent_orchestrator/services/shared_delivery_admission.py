"""Shared metadata-credit delivery coordinator, bound to one exact local owner.

This module never creates a durable credit, pool or identity. It consumes an
already enrolled metadata credit set through a private repository port and
maps exact local delivery leases to shared reservations. Elapsed time, GC and
timeouts never release a shared charge; only a read/acknowledged release plus
the local physical retirement witness does.
"""

import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from cli_agent_orchestrator.clients.admission_repository import SharedCommitUncertain
from cli_agent_orchestrator.clients.storage_admission import (
    _current_storage_owner,
    retry_storage_closes,
    storage_close_uncertainty,
    storage_process_current,
)
from cli_agent_orchestrator.models.admission_domains import (
    MetadataCoverage,
    ReservationState,
    SharedReservation,
    SharedResourceCost,
)
from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.models.resource_generation import (
    GenerationSeal,
    TerminalGenerationAuthority,
)
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.delivery_admission import (
    DeliveryAdmission,
    DeliveryReservation,
    DeliveryRetirementHold,
    StorageControlPermit,
    _StorageControlReservation,
)

AttemptStage = Literal["preparing", "active", "uncertain", "failed"]

# Exact successor for reconciliation; the revision always comes from a receipt.
_RELEASE_NEXT: dict[ReservationState, ReservationState] = {
    "preparing": "released",
    "active": "releasing",
    "uncertain": "releasing",
    "releasing": "released",
}


class SharedResourceRepository(Protocol):
    """Private backend port; implementations hold the exact operation lock."""

    def current_binding(self) -> ResourceBinding: ...

    def seal_current_generation(self, authority: TerminalGenerationAuthority) -> GenerationSeal: ...

    def metadata_coverage_current(self) -> MetadataCoverage: ...

    def reserve_for_cost(
        self, operation_id: str, cost: SharedResourceCost, *, require_metadata: bool = True
    ) -> SharedReservation: ...

    def read_operation(self, operation_id: str) -> SharedReservation | None: ...

    def transition(
        self, operation_id: str, *, expected_revision: int, state: ReservationState
    ) -> SharedReservation: ...

    def fence_physically_closed(
        self, operation_id: str, *, expected_revision: int, original_binding: ResourceBinding
    ) -> SharedReservation: ...


class SharedDeliveryUnavailable(RuntimeError):
    """Fixed, non-secret denial; private causes are never chained."""

    def __init__(self) -> None:
        super().__init__("Shared delivery resources are unavailable")


class SharedDeliveryDrainIncomplete(RuntimeError):
    """Cleanup still owns shared charges or physical work; nothing was retired."""

    def __init__(self) -> None:
        super().__init__("Shared delivery cleanup has not finished")


def _deny() -> SharedDeliveryUnavailable:
    return SharedDeliveryUnavailable()


@dataclass(eq=False, repr=False)
class _Attempt:
    operation_id: str
    cost: SharedResourceCost
    lease: DeliveryReservation
    hold: DeliveryRetirementHold
    stage: AttemptStage = "preparing"
    preparer: int = field(default_factory=threading.get_ident)
    settled: threading.Event = field(default_factory=threading.Event)
    authorized: bool = False
    released_confirmed: bool = False


@dataclass(eq=False, repr=False)
class _Entry:
    owner: DeliveryAdmission
    control: StorageControlPermit
    retirement: Callable[[], bool]
    keys: tuple[tuple, ...]
    coordinator: "SharedDeliveryCoordinator | None" = None


class _NodeRegistry:
    """Process-bound map of binding/credit keys to one strong owner entry."""

    def __init__(self) -> None:
        self._pid = os.getpid()
        self._guard = threading.Lock()
        self._entries: dict[tuple, _Entry] = {}
        self._grants: dict[int, _CoverageGrant] = {}

    def current(self) -> None:
        # Never touch an inherited lock: a foreign copy is denied, not reset.
        if self._pid != os.getpid():
            raise _deny()

    def claim(
        self,
        owner: DeliveryAdmission,
        control: StorageControlPermit,
        retirement: Callable[[], bool],
        keys: tuple[tuple, ...],
    ) -> _Entry:
        """Reserve keys before SQL; a foreign owner yields only to its retired witness.

        A failed, never-enabled claim stays registered with its strong owner:
        only the same exact owner/control may retry it.
        """
        while True:
            self.current()
            with self._guard:
                mine = next((e for e in self._entries.values() if e.owner is owner), None)
                if mine is not None and (
                    mine.coordinator is not None or mine.control is not control
                ):
                    raise _deny()  # One coordinator per owner, never nested.
                rival = next(
                    (self._entries[k] for k in keys if self._entries.get(k) not in (None, mine)),
                    None,
                )
                if rival is None:
                    entry = mine if mine is not None else _Entry(owner, control, retirement, ())
                    for key in keys:
                        self._entries[key] = entry
                    entry.keys = tuple(dict.fromkeys(entry.keys + keys))
                    return entry
                callback = rival.retirement
            try:
                retired = callback() is True  # Outside every guard.
            except Exception:
                retired = False
            if not retired:
                raise _deny()
            self.current()
            with self._guard:
                for key in rival.keys:
                    if self._entries.get(key) is rival:
                        del self._entries[key]

    def issue(self, grant: "_CoverageGrant") -> None:
        self.current()
        with self._guard:
            self._grants[id(grant)] = grant

    def redeem(self, grant: object) -> bool:
        self.current()
        with self._guard:
            if type(grant) is not _CoverageGrant or self._grants.get(id(grant)) is not grant:
                return False
            del self._grants[id(grant)]
            return True


_REGISTRY = _NodeRegistry()


def _registry() -> _NodeRegistry:
    global _REGISTRY
    registry = _REGISTRY
    if registry._pid != os.getpid():
        # Explicit safe option only: a confirmed storage detach admits a fresh,
        # empty child registry. Inherited coordinators stay foreign.
        if not storage_process_current():
            raise _deny()
        registry = _REGISTRY = _NodeRegistry()
    return registry


@dataclass(frozen=True, eq=False, repr=False)
class _CoverageGrant:
    """Opaque, single-use; copies and pickles are not redeemable."""

    coverage: MetadataCoverage
    entry: _Entry

    def __reduce__(self):  # pragma: no cover - defensive
        raise TypeError("coverage grant is not transferable")

    def __copy__(self):
        raise TypeError("coverage grant is not transferable")

    def __deepcopy__(self, memo):
        raise TypeError("coverage grant is not transferable")


def _binding_keys(binding: ResourceBinding) -> tuple[tuple, ...]:
    # One budget per exact process binding; a realm may host many bindings.
    process = (
        "binding",
        binding.store_id,
        binding.target_identity_sha256,
        binding.instance_id,
        binding.process_generation,
        binding.process_pid,
        binding.process_started_at,
    )
    return (process,)


def _credit_keys(binding: ResourceBinding, coverage: MetadataCoverage) -> tuple[tuple, ...]:
    realm = (binding.store_id, binding.target_identity_sha256)
    return tuple(("credit", *realm, credit.credit_id) for credit in coverage.credits)


def _exact_control(owner: DeliveryAdmission) -> _StorageControlReservation:
    """Inside the exact current control scope with no normal data lease."""
    current = _current_storage_owner()
    with owner._guard:
        lease = owner._control_lease
        if (
            lease is None
            or current is not lease
            or any(item is not lease for item in owner._leases.values())
        ):
            raise _deny()
        return lease


def authorize_shared_delivery(
    *,
    principal: object,
    repository: SharedResourceRepository,
    owner: DeliveryAdmission,
    control: StorageControlPermit,
    retirement: Callable[[], bool],
    on_retired: Callable[[str | None], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    retry_interval_s: float = 1.0,
) -> "SharedDeliveryCoordinator":
    """Called by root inside its existing control.operation() scope."""
    ResourceBootstrap._administrator(principal)  # Before repository, binding or SQL.
    if (
        type(owner) is not DeliveryAdmission
        or type(control) is not StorageControlPermit
        or not callable(retirement)
        or not callable(clock)
        or (on_retired is not None and not callable(on_retired))
        or type(retry_interval_s) is not float
        or not 0 < retry_interval_s < float("inf")
    ):
        raise _deny()
    registry = _registry()
    try:
        if not control.belongs_to(owner):
            raise _deny()
        control_lease = _exact_control(owner)
    except SharedDeliveryUnavailable:
        raise
    except Exception:
        raise _deny() from None
    try:
        before = repository.current_binding()
        if type(before) is not ResourceBinding or before.process_pid != os.getpid():
            raise _deny()
        # Preflight before first SQL. A failed claim is never abandoned: its
        # control session may still be physically alive until root retires it.
        entry = registry.claim(owner, control, retirement, _binding_keys(before))
        coverage = repository.metadata_coverage_current()
        after = repository.current_binding()
        if type(coverage) is not MetadataCoverage or coverage.binding != before or after != before:
            raise _deny()
        registry.claim(owner, control, retirement, _credit_keys(before, coverage))
        with owner._guard:
            if owner._control_lease is not control_lease or control_lease._storage_connections:
                raise _deny()  # A live control loan must not be enabled over.
        grant = _CoverageGrant(coverage, entry)
        registry.issue(grant)
        coordinator = SharedDeliveryCoordinator(
            grant,
            repository=repository,
            clock=clock,
            retry_interval_s=retry_interval_s,
            on_retired=on_retired,
        )
    except SharedDeliveryUnavailable:
        raise
    except Exception:
        raise _deny() from None
    return coordinator


class SharedDeliveryCoordinator:
    """One exact owner's shared delivery mapping and its cleanup worker."""

    def __init__(
        self,
        grant: object,
        *,
        repository: SharedResourceRepository,
        clock: Callable[[], float],
        retry_interval_s: float,
        on_retired: Callable[[str | None], None] | None = None,
    ):
        registry = _registry()
        if not registry.redeem(grant):
            raise _deny()
        assert type(grant) is _CoverageGrant
        entry = grant.entry
        self._registry = registry
        self._entry = entry
        self._coverage = grant.coverage
        self._owner = entry.owner
        self._control = entry.control
        self._repository = repository
        self._clock = clock
        self._interval = retry_interval_s
        self._on_retired = on_retired
        self._pid = os.getpid()
        self._guard = threading.Lock()
        self._attempts: dict[int, _Attempt] = {}
        self._by_hold: dict[int, _Attempt] = {}
        self._uncertain = 0
        self._accepting = True
        self._error: str | None = None
        self._worker = threading.Thread(
            target=self._run, name="cao-shared-delivery-cleanup", daemon=True
        )
        registry.current()
        with registry._guard:
            if entry.coordinator is not None:
                raise _deny()
            entry.coordinator = self  # Strong; only the root retirement callback reclaims.
        try:
            self._worker.start()
        except Exception:
            # Zero attempts exist yet; the claim stays for an exact-owner retry.
            self._accepting = False
            self._owner.wake_retirement_worker()
            try:
                self._worker.join()  # Outside guards; start may have launched before raising.
            except RuntimeError:
                if self._worker.ident is not None:
                    raise _deny() from None  # No terminal witness: retain the strong claim.
                # Thread.join refuses an initialized thread that never started.
            registry.current()
            with registry._guard:
                if entry.coordinator is self:
                    entry.coordinator = None
            raise _deny() from None

    # -- diagnostics (no SQL) -------------------------------------------------

    @property
    def coverage(self) -> MetadataCoverage:
        return self._coverage

    @property
    def pending_count(self) -> int:
        with self._checked():
            return len(self._attempts)

    @property
    def accepting(self) -> bool:
        with self._checked():
            return self._accepting

    @property
    def worker_alive(self) -> bool:
        return self._worker.is_alive()

    @property
    def error(self) -> str | None:
        with self._checked():
            return self._error

    @property
    def uncertain_count(self) -> int:
        with self._checked():
            return self._uncertain

    def _checked(self) -> threading.Lock:
        if self._pid != os.getpid():
            raise _deny()
        return self._guard

    # -- preparation ---------------------------------------------------------

    def owns_prepared_storage(self, lease: object) -> bool:
        """Positive exact-lease confirmation without SQL, waits or new admission."""
        with self._checked():
            attempt = self._attempts.get(id(lease))
            prepared = (
                self._accepting
                and attempt is not None
                and attempt.lease is lease
                and attempt.authorized
                and attempt.stage == "active"
                and attempt.hold is getattr(lease, "_retirement_hold", None)
            )
        return prepared and self._owner.owns_normal_storage(lease)

    def prepare(self, lease: DeliveryReservation) -> None:
        """Synchronous physical worker only; never call on an event loop."""
        owner = self._owner
        while True:
            with self._checked():
                attempt = self._attempts.get(id(lease))
                if attempt is not None and attempt.lease is not lease:
                    raise _deny()
                if attempt is None:
                    # Check, hold and insert are one memory step; the hold is RAM only.
                    if (
                        not self._accepting
                        or not isinstance(lease, DeliveryReservation)
                        or isinstance(lease, _StorageControlReservation)
                        or lease._owner is not owner
                        or not owner.owns_normal_storage(lease)
                    ):
                        raise _deny()
                    footprint = lease.footprint
                    try:
                        cost = SharedResourceCost(
                            footprint.memory_bytes,
                            footprint.cpu_millicores,
                            footprint.storage_slots,
                        )
                        hold = lease.hold_retirement()
                    except Exception:
                        raise _deny() from None
                    attempt = _Attempt(str(uuid.uuid4()), cost, lease, hold)
                    self._attempts[id(lease)] = attempt
                    self._by_hold[id(hold)] = attempt
                    break
                if attempt.authorized and attempt.stage == "active":
                    settled = None
                elif attempt.stage != "preparing" or attempt.preparer == threading.get_ident():
                    raise _deny()
                else:
                    settled = attempt.settled
            if settled is None:
                # Reentry: no SQL, charge or permit, but only while exact and nonretiring.
                if owner.owns_normal_storage(lease):
                    return
                raise _deny()
            settled.wait()  # Outside guards; a later loop rereads the outcome.
        stage: AttemptStage = "failed"
        try:
            with self._control.operation(clock=self._clock, wait=True):
                try:
                    receipt = self._repository.reserve_for_cost(
                        attempt.operation_id, attempt.cost, require_metadata=True
                    )
                    self._receipt(attempt, receipt, "preparing")
                    receipt = self._repository.transition(
                        attempt.operation_id, expected_revision=receipt.revision, state="active"
                    )
                    self._receipt(attempt, receipt, "active")
                    stage = "active"
                except SharedCommitUncertain:
                    stage = "uncertain"
                    raise
        except Exception:
            self._settle(attempt, "uncertain" if stage != "failed" else stage)
            raise _deny() from None
        if storage_close_uncertainty():
            self._settle(attempt, "uncertain")  # Control closed uncertain: no body.
            raise _deny()
        self._settle(attempt, "active", authorized=True)

    @staticmethod
    def _receipt(attempt: _Attempt, receipt: object, state: ReservationState) -> None:
        if (
            type(receipt) is not SharedReservation
            or receipt.operation_id != attempt.operation_id
            or receipt.state != state
        ):
            raise _deny()

    def _settle(self, attempt: _Attempt, stage: AttemptStage, *, authorized: bool = False) -> None:
        with self._checked():
            self._uncertain += (stage == "uncertain") - (attempt.stage == "uncertain")
            attempt.stage = stage
            attempt.authorized = authorized
            if not authorized:
                self._error = "shared delivery preparation unavailable"
        attempt.settled.set()

    # -- lifecycle -----------------------------------------------------------

    def stop_accepting(self) -> None:
        with self._checked():
            self._accepting = False
        self._owner.wake_retirement_worker()

    def drain(self, timeout_s: float) -> None:
        """Join outside guards; failure keeps every charge, worker and registry key."""
        if type(timeout_s) not in (int, float) or not 0 <= timeout_s < float("inf"):
            raise ValueError("drain timeout must be finite")
        self.stop_accepting()
        self._worker.join(timeout_s)
        with self._checked():
            pending = bool(self._attempts)
        if self._worker.is_alive() or pending:
            raise SharedDeliveryDrainIncomplete()

    # -- cleanup worker ------------------------------------------------------

    def _run(self) -> None:
        """Consume only holds the owner queue reports ready, plus a retained retry set."""
        owner = self._owner
        retry: dict[int, _Attempt] = {}
        due = 0.0
        while True:
            try:
                item = owner.wait_retirement_ready(timeout_s=self._interval)
                owner.retry_physical_closes()  # Also runs while shared drain owns late holds.
            except Exception:
                return  # Foreign process: keep every charge.
            fresh = None
            if item is not None:
                with self._guard:
                    fresh = self._by_hold.get(id(item))
                if fresh is not None and fresh.hold is not item:
                    fresh = None
            now = time.monotonic()
            retry_due = bool(retry) and now >= due
            if fresh is not None or retry_due:
                try:
                    retry_storage_closes()
                except Exception:
                    pass
            if fresh is not None:
                if self._retire(fresh):
                    retry.pop(id(fresh), None)
                else:
                    retry[id(fresh)] = fresh
            if retry_due:
                due = now + self._interval  # Finite liveness interval, never a release.
                for key, attempt in tuple(retry.items()):
                    if attempt is not fresh and self._retire(attempt):
                        del retry[key]
            with self._guard:
                # A late native hold remains in _attempts until physical retirement;
                # this worker cannot exit ahead of the hold it must later consume.
                if not self._accepting and not self._attempts:
                    return

    def _retire(self, attempt: _Attempt) -> bool:
        hold = attempt.hold
        try:
            if not attempt.settled.is_set() or not hold.physical_ready:
                return False
            if storage_close_uncertainty():
                return False  # Retain and retry; never a final failure or busy loop.
            with self._control.operation(clock=self._clock, retirement=hold, wait=True):
                if not attempt.released_confirmed:
                    receipt = self._repository.read_operation(attempt.operation_id)
                    while receipt is not None and receipt.state != "released":
                        self._check_read(attempt, receipt)
                        if receipt.state in {"active", "uncertain"}:
                            receipt = self._repository.fence_physically_closed(
                                attempt.operation_id,
                                expected_revision=receipt.revision,
                                original_binding=self._coverage.binding,
                            )
                        else:
                            receipt = self._repository.transition(
                                attempt.operation_id,
                                expected_revision=receipt.revision,
                                state=_RELEASE_NEXT[receipt.state],
                            )
                    if receipt is not None:
                        self._check_read(attempt, receipt)
                    with self._guard:
                        attempt.released_confirmed = True
        except Exception:
            with self._guard:
                self._error = "shared delivery cleanup retained"
            return False
        if not hold.finish():
            return False
        with self._guard:
            if self._attempts.get(id(attempt.lease)) is attempt:
                del self._attempts[id(attempt.lease)]
            if self._by_hold.get(id(hold)) is attempt:
                del self._by_hold[id(hold)]
            self._uncertain -= attempt.stage == "uncertain"
        if self._on_retired is not None:
            try:
                # Notification only, after both physical release and removal; no guard held.
                self._on_retired(attempt.lease._receiver)
            except Exception:
                with self._checked():
                    self._error = "shared delivery notification unavailable"
        return True

    @staticmethod
    def _check_read(attempt: _Attempt, receipt: object) -> None:
        if (
            type(receipt) is not SharedReservation
            or receipt.operation_id != attempt.operation_id
            or (receipt.state != "released" and receipt.state not in _RELEASE_NEXT)
        ):
            raise _deny()


__all__ = [
    "SharedDeliveryCoordinator",
    "SharedDeliveryDrainIncomplete",
    "SharedDeliveryUnavailable",
    "SharedResourceRepository",
    "authorize_shared_delivery",
]
