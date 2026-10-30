"""One local sampling lifecycle; telemetry and authority never replace leases."""

import asyncio
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Callable, Iterator, Literal, Protocol

from cli_agent_orchestrator.clients.admission_bootstrap_repository import (
    EnvelopeNotFound,
    EnvelopeRevisionConflict,
)
from cli_agent_orchestrator.services.admission_bootstrap import (
    ResourceBootstrap,
    VerifiedResourceAttestation,
    is_verified_resource_attestation,
)
from cli_agent_orchestrator.services.admission_policy import (
    ResourceAmounts,
    evaluate_capacity,
    validate_clock,
)
from cli_agent_orchestrator.services.admission_postgres import PostgresPressureSampler
from cli_agent_orchestrator.services.admission_resources import (
    LinuxResourceSampler,
    ResourceSample,
    StorageObservation,
)
from cli_agent_orchestrator.services.admission_settings import (
    AdmissionConfiguration,
    AdmissionPolicyLoader,
)
from cli_agent_orchestrator.services.delivery_admission import AdmissionReason, DeliveryAdmission

MonitorReason = (
    AdmissionReason
    | Literal[
        "recovering",
        "policy_unavailable",
        "authority_unavailable",
        "generation_changed",
        "probe_busy",
        "monitor_failed",
        "duplicate_observation",
        "authority_changing",
        "authority_uncertain",
    ]
)


class AuthorityChangeUnavailable(RuntimeError):
    """No new control write is allowed in this lifetime/outcome state."""


@dataclass
class AuthorityMutationState:
    """Process-owned latch; rebuilding a runtime never proves an uncertain write retired."""

    uncertain: bool = False


class ResourceSampler(Protocol):
    def reset_history(self) -> None: ...
    def sample(self, *, storage: StorageObservation) -> ResourceSample: ...


@dataclass(frozen=True)
class MonitorResult:
    reason: MonitorReason
    published: bool = False


def _minimum(a: ResourceAmounts, b: ResourceAmounts) -> ResourceAmounts:
    return ResourceAmounts(
        min(a.memory_bytes, b.memory_bytes),
        min(a.cpu_millicores, b.cpu_millicores),
        min(a.storage_slots, b.storage_slots),
    )


class _Recovery:
    """O(1) state: reductions now, sustained conservative growth by resource."""

    def __init__(self) -> None:
        self.current = ResourceAmounts()
        self.candidate: ResourceAmounts | None = None
        self.count = 0
        self.marker: float | None = None

    def apply(self, capacity: ResourceAmounts, *, marker: float, required: int) -> bool:
        if self.marker is not None and marker <= self.marker:
            return False
        self.marker = marker
        reduced = _minimum(self.current, capacity)
        if reduced != self.current:
            self.current = reduced
            self.candidate = None
            self.count = 0
        if capacity == self.current:
            self.candidate = None
            self.count = 0
            return False
        self.candidate = capacity if self.candidate is None else _minimum(self.candidate, capacity)
        self.count += 1
        if self.count >= required:
            self.current = self.candidate
            self.candidate = None
            self.count = 0
        return self.current != capacity


class AdmissionMonitor:
    """A persistent probe owner; no provider calls, per-message I/O or node claims.

    Construction is trusted server wiring. The local storage supplier returns
    stage capacity after external/retiring use, before this ledger's own leases.
    Sampling never replaces the PG sampler, including after an uncertain open.
    """

    def __init__(
        self,
        owner: DeliveryAdmission,
        *,
        policy_loader: AdmissionPolicyLoader,
        bootstrap: ResourceBootstrap,
        postgres: PostgresPressureSampler,
        scope_id: str,
        local_storage_capacity: Callable[[], int | None],
        clock: Callable[[], float] = time.monotonic,
        resource_factory: Callable[[VerifiedResourceAttestation], ResourceSampler] | None = None,
        authority_state: AuthorityMutationState | None = None,
    ):
        if not isinstance(scope_id, str) or not scope_id.strip():
            raise ValueError("monitor requires an operator source scope")
        self.owner = owner
        self._policy_loader = policy_loader
        self._bootstrap = bootstrap
        self._postgres = postgres
        self._scope_id = scope_id
        self._local_storage_capacity = local_storage_capacity
        self._clock = clock
        self._resource_factory = resource_factory or self._linux_sampler
        self._resources: ResourceSampler | None = None
        self._resource_identity: object = None
        self._configuration: AdmissionConfiguration | None = None
        self._recovery = _Recovery()
        self._last_cycle_at: float | None = None
        self._refresh_guard = threading.Lock()
        self._lifecycle_guard = threading.Lock()
        self._lifecycle = 0
        self._retired = threading.Event()
        self._retired.set()
        self._stopped = False
        self._interval = 1.0
        self._runner: asyncio.Task[None] | None = None
        self._physical: asyncio.Task[MonitorResult] | None = None
        self._drain: asyncio.Task[bool] | None = None
        self._authority_state = (
            authority_state if authority_state is not None else AuthorityMutationState()
        )
        self._authority_tokens: set[object] = set()
        self._on_change: Callable[[], None] | None = None

    @property
    def retired(self) -> bool:
        with self._lifecycle_guard:
            return (
                self._stopped
                and self._retired.is_set()
                and not self._authority_tokens
                and self._runner is None
                and self._physical is None
                and self._drain is None
                and not self._refresh_guard.locked()
            )

    def set_on_change(self, callback: Callable[[], None]) -> None:
        with self._lifecycle_guard:
            if self._runner is not None or self._refresh_guard.locked():
                raise RuntimeError("monitor observer must be wired before sampling")
            self._on_change = callback

    def begin_authority_change(self) -> object:
        with self._lifecycle_guard:
            if self._authority_state.uncertain:
                raise AuthorityChangeUnavailable("resource authority outcome is uncertain")
            if self._stopped:
                raise AuthorityChangeUnavailable("resource authority writer is stopped")
            token = object()
            self._authority_tokens.add(token)
            self.owner.invalidate(expected_generation=self.owner.generation)
            return token

    def cancel_authority_change(self, token: object) -> None:
        """Only for a control Future proven cancelled before its worker starts."""
        with self._lifecycle_guard:
            self._authority_tokens.discard(token)
            self.owner.invalidate(expected_generation=self.owner.generation)

    @contextmanager
    def authority_change(self, token: object) -> Iterator[None]:
        entered = False
        acquired = False
        try:
            self._refresh_guard.acquire()
            acquired = True
            with self._lifecycle_guard:
                if self._stopped or token not in self._authority_tokens:
                    raise AuthorityChangeUnavailable("resource authority writer is stopped")
                if self._authority_state.uncertain:
                    raise AuthorityChangeUnavailable("resource authority outcome is uncertain")
                self._retired.clear()
                self.owner.invalidate(expected_generation=self.owner.generation)
                entered = True
            yield
        except BaseException as error:
            if entered and type(error) not in (EnvelopeRevisionConflict, EnvelopeNotFound):
                with self._lifecycle_guard:
                    self._authority_state.uncertain = True
            raise
        finally:
            with self._lifecycle_guard:
                self.owner.invalidate(expected_generation=self.owner.generation)
                self._authority_tokens.discard(token)
            if acquired:
                self._retired.set()
                self._refresh_guard.release()

    def _linux_sampler(self, attestation: VerifiedResourceAttestation) -> LinuxResourceSampler:
        return LinuxResourceSampler(
            clock=self._clock,
            complete_hierarchy=attestation.claims.complete_hierarchy,
            root_is_host=attestation.claims.root_is_host,
        )

    def _invalidate(
        self, generation: int, reason: MonitorReason, *, reset_resources: bool = False
    ) -> MonitorResult:
        self.owner.invalidate(expected_generation=generation)
        self._recovery = _Recovery()
        if reset_resources:
            self._resources = None
            self._resource_identity = None
        return MonitorResult(reason)

    def refresh(self) -> MonitorResult:
        """Synchronous physical cycle, serialized independently of its awaiter."""
        with self._lifecycle_guard:
            lifecycle = self._lifecycle
            if self._stopped:
                return MonitorResult("stopping")
            if self._authority_state.uncertain:
                self.owner.invalidate(expected_generation=self.owner.generation)
                return MonitorResult("authority_uncertain")
            if self._authority_tokens:
                return MonitorResult("authority_changing")
        if not self._refresh_guard.acquire(blocking=False):
            return MonitorResult("probe_busy")
        self._retired.clear()
        generation = self.owner.generation  # Capture before any configuration I/O.
        try:
            with self._lifecycle_guard:
                if self._stopped or lifecycle != self._lifecycle:
                    return MonitorResult("stopping")
            return self._cycle(generation)
        except Exception:
            # Close stale admission even for a programming error, then expose it
            # to the owning caller instead of converting it to a healthy sample.
            self._invalidate(generation, "monitor_failed")
            raise
        finally:
            self._retired.set()
            self._refresh_guard.release()

    def _cycle(self, generation: int) -> MonitorResult:
        cycle_at = self._clock()
        validate_clock(cycle_at)
        loaded = self._policy_loader.load(scope_id=self._scope_id)
        configuration = loaded.configuration
        if configuration is None:
            return self._invalidate(generation, "policy_unavailable")
        attestation = self._bootstrap.load_current().attestation
        if not is_verified_resource_attestation(attestation):
            return self._invalidate(generation, "authority_unavailable", reset_resources=True)
        assert attestation is not None
        budget = ResourceAmounts(
            attestation.claims.memory_budget_bytes,
            attestation.claims.cpu_budget_millicores,
            attestation.claims.storage_budget_slots,
        )
        configured = self.owner.reconfigure(
            configuration.policy, budget=budget, expected_generation=generation
        )
        if configured is None:
            return MonitorResult("generation_changed")
        generation = configured  # Configuration CAS completed before resource I/O.
        changed = configuration != self._configuration
        self._configuration = configuration
        self._interval = configuration.sample_interval_s
        identity = (attestation.binding, attestation.claims.root_is_host)
        if identity != self._resource_identity or self._resources is None:
            self._resources = self._resource_factory(attestation)
            self._resource_identity = identity
            changed = True
        gap = self._last_cycle_at is not None and (
            cycle_at - self._last_cycle_at > configuration.policy.max_sample_age_s
            or cycle_at < self._last_cycle_at
        )
        self._last_cycle_at = cycle_at
        if changed or gap:
            self._recovery = _Recovery()
        if gap:
            self._resources.reset_history()
        if not self._postgres.configure(configuration.postgres):
            return self._invalidate(generation, "probe_busy")
        pressure = self._postgres.sample().observation
        local_slots = self._local_storage_capacity()
        # StorageObservation validates strict finite integer units before use.
        local = StorageObservation(self._clock(), local_slots)
        slots = (
            min(pressure.available_slots, local.available_slots)
            if pressure.available_slots is not None and local.available_slots is not None
            else None
        )
        storage = StorageObservation(min(pressure.observed_at, local.observed_at), slots)
        resource_started_at = self._clock()
        sample = self._resources.sample(storage=storage).observation
        checked = self._bootstrap.load_current().attestation
        if (
            not is_verified_resource_attestation(checked)
            or checked is None
            or (
                checked.binding != attestation.binding
                or checked.claims != attestation.claims
                or checked.revision != attestation.revision
            )
        ):
            return self._invalidate(generation, "authority_unavailable", reset_resources=True)
        now = self._clock()
        validate_clock(now)
        if any(
            at > now
            for at in (
                cycle_at,
                pressure.observed_at,
                local.observed_at,
                resource_started_at,
                sample.observed_at,
                attestation.verified_at,
                checked.verified_at,
            )
        ):
            return self._invalidate(generation, "future_observation")
        observation = replace(
            sample,
            observed_at=min(
                sample.observed_at,
                storage.observed_at,
                resource_started_at,
                attestation.verified_at,
                checked.verified_at,
            ),
        )
        decision = evaluate_capacity(configuration.policy, observation, now=now, budget=budget)
        if decision.reason not in ("capacity_available", "resource_exhausted"):
            return self._invalidate(generation, decision.reason)
        recovering = self._recovery.apply(
            decision.resource_capacity,
            marker=cycle_at,
            required=configuration.healthy_samples_before_growth,
        )
        with self._lifecycle_guard:
            if self._authority_tokens or self._authority_state.uncertain:
                return MonitorResult("authority_changing")
            published = self.owner.observe(
                observation, now=now, generation=generation, ceiling=self._recovery.current
            )
        if not published:
            if self.owner.generation != generation:
                self._recovery = _Recovery()
                return MonitorResult("generation_changed")
            return MonitorResult("duplicate_observation")
        status = self.owner.snapshot(now=now)
        return MonitorResult("recovering" if recovering else status.reason, True)

    async def start(self) -> None:
        with self._lifecycle_guard:
            if (
                self._runner is not None
                or self._physical is not None
                or self._drain is not None
                or self._refresh_guard.locked()
            ):
                raise RuntimeError("previous monitor work must retire before start")
            self.owner.start_accepting()
            self._lifecycle += 1
            self._stopped = False
            self._resources = None
            self._resource_identity = None
            self._configuration = None
            self._last_cycle_at = None
            self._recovery = _Recovery()
            self._runner = asyncio.create_task(self._run())

    def _stop_admission(self) -> None:
        with self._lifecycle_guard:
            self._lifecycle += 1
            self._stopped = True
            self.owner.stop_accepting()

    def stop_accepting(self) -> None:
        self._stop_admission()

    async def _run(self) -> None:
        try:
            while not self._stopped:
                self._physical = asyncio.create_task(asyncio.to_thread(self.refresh))
                await asyncio.shield(self._physical)
                self._physical = None
                if self._on_change is not None:
                    self._on_change()
                await asyncio.sleep(self._interval)
        except Exception:
            self._stop_admission()
            raise

    async def stop(self) -> None:
        self._stop_admission()
        runner = self._runner
        failure: Exception | None = None
        try:
            if runner is not None:
                runner.cancel()
                try:
                    await runner
                except asyncio.CancelledError:
                    task = asyncio.current_task()
                    if task is not None and task.cancelling():
                        raise
                except Exception as error:
                    failure = error
            physical = self._physical
            if physical is not None:
                try:
                    await asyncio.shield(physical)
                except Exception as error:
                    if failure is None:
                        failure = error
            # A trusted caller may invoke synchronous refresh directly. Its
            # retirement witness is independent of an asyncio runner handle.
            if not self._retired.is_set():
                if self._drain is None:
                    self._drain = asyncio.create_task(asyncio.to_thread(self._retired.wait))
                await asyncio.shield(self._drain)
        finally:
            # A cancelled stop retains every still-live physical handle. A
            # later stop can join it; start cannot multiply outstanding probes.
            if self._retired.is_set():
                if self._physical is None or self._physical.done():
                    self._physical = None
                if self._runner is None or self._runner.done():
                    self._runner = None
                if self._drain is not None and self._drain.done():
                    self._drain = None
        if failure is not None:
            raise failure
