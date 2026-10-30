"""Delivers queued inbox messages when terminals become ready.

Consumer: terminal.{id}.status
"""

import asyncio
import contextvars
import logging
import math
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from heapq import heappop, heappush
from itertools import count, groupby, islice
from queue import Empty, SimpleQueue
from typing import Callable, Dict, Iterable, Iterator, Set, Tuple

from cli_agent_orchestrator.backends.base import TerminalNotFoundError
from cli_agent_orchestrator.clients.database import (
    _managed_inbox_target,
    get_pending_messages,
    iter_pending_receiver_ids_by_provider,
    iter_pending_receiver_ids_older_than,
    update_message_status,
)
from cli_agent_orchestrator.constants import (
    EAGER_INBOX_DELIVERY,
    INBOX_RECONCILE_GRACE_SECONDS,
)
from cli_agent_orchestrator.models.inbox import MessageStatus, OrchestrationType
from cli_agent_orchestrator.models.provider import ProviderType
from cli_agent_orchestrator.models.terminal import TerminalInputBlockedError, TerminalStatus
from cli_agent_orchestrator.plugins import PluginRegistry
from cli_agent_orchestrator.providers.manager import provider_manager
from cli_agent_orchestrator.runtime_channel.registry import RuntimeUnavailableError
from cli_agent_orchestrator.services import terminal_service
from cli_agent_orchestrator.services.delivery_admission import (
    DeliveryAdmission,
    DeliveryReservation,
)
from cli_agent_orchestrator.services.event_bus import bus
from cli_agent_orchestrator.services.settings_service import get_server_settings
from cli_agent_orchestrator.services.status_monitor import status_monitor
from cli_agent_orchestrator.utils.event import terminal_id_from_topic

logger = logging.getLogger(__name__)

# Per consumer, shared-executor submissions stay bounded independently of event
# volume. Other delivery paths retain their existing admission and lock rules.
_STATUS_DELIVERY_CONCURRENCY = 4
# Each of the two fallback consumers has the same finite submission ceiling.
# Their parent default-executor workers never wait on that executor's children.
_SWEEP_DELIVERY_CONCURRENCY = _STATUS_DELIVERY_CONCURRENCY


class InboxServiceStoppingError(RuntimeError):
    """Raised when inbox work is requested after shutdown has closed admission."""


class InboxServiceDrainTimeout(TimeoutError):
    """Raised when active inbox work does not finish before shutdown's deadline."""

    def __init__(self, *, active_operations: int, timeout_s: float) -> None:
        self.active_operations = active_operations
        self.timeout_s = timeout_s
        super().__init__(
            f"Inbox service drain timed out after {timeout_s:g}s with "
            f"{active_operations} operation(s) still active"
        )


class _DeliveryLockEntry:
    __slots__ = ("lock", "refs")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.refs = 0


class _PendingStatusEvents:
    """Oldest nonbusy event in O(log Q), with no scans or stale heap heads.

    The consumer owns busy keys. A pop makes its key busy immediately, and
    completed() publishes its next head before any further scheduling.
    """

    def __init__(self) -> None:
        self._events: dict[str, deque[int]] = {}
        self._ready: list[tuple[int, str]] = []
        self._ready_keys: set[str] = set()
        self._ordinals = count()
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def append(self, terminal_id: str, *, busy: bool) -> int:
        ordinal = next(self._ordinals)
        events = self._events.get(terminal_id)
        if events is None:
            events = self._events[terminal_id] = deque()
            if not busy:
                heappush(self._ready, (ordinal, terminal_id))
                self._ready_keys.add(terminal_id)
        events.append(ordinal)
        self._size += 1
        return ordinal

    def pop(self) -> str | None:
        event = self.pop_event()
        return event[1] if event is not None else None

    def pop_event(self) -> tuple[int, str] | None:
        if not self._ready:
            return None
        ordinal, terminal_id = heappop(self._ready)
        self._ready_keys.remove(terminal_id)
        events = self._events[terminal_id]
        events.popleft()
        self._size -= 1
        if not events:
            del self._events[terminal_id]
        return ordinal, terminal_id

    def peek_event(self) -> tuple[int, str] | None:
        return self._ready[0] if self._ready else None

    def park_head(self) -> None:
        """Keep the event and its ordinal; physical completion will wake its key."""
        _, terminal_id = heappop(self._ready)
        self._ready_keys.remove(terminal_id)

    def completed(self, terminal_id: str) -> None:
        events = self._events.get(terminal_id)
        if events and terminal_id not in self._ready_keys:
            heappush(self._ready, (events[0], terminal_id))
            self._ready_keys.add(terminal_id)


class _StatusEventSink:
    """One bounded admission buffer; busy keys never hide accepted ready keys."""

    def __init__(self, capacity: int, is_busy: Callable[[str], bool]) -> None:
        self.maxsize = capacity
        self.pending = _PendingStatusEvents()
        self.ready = asyncio.Event()
        self.closed = False
        self._is_busy = is_busy
        self._requests: dict[int, tuple[contextvars.Context, PluginRegistry | None]] = {}

    def qsize(self) -> int:
        return len(self.pending)

    def empty(self) -> bool:
        return not len(self.pending)

    def put_nowait(self, event: dict) -> None:
        if self.closed:
            return
        try:
            if event["data"]["status"] not in (
                TerminalStatus.IDLE.value,
                TerminalStatus.COMPLETED.value,
            ):
                return
            terminal_id = terminal_id_from_topic(event["topic"])
        except Exception as e:
            logger.error(f"Error in InboxService: {e}")
            return
        self.offer_pending(terminal_id)

    def offer_pending(
        self,
        terminal_id: str,
        request: tuple[contextvars.Context, PluginRegistry | None] | None = None,
    ) -> bool:
        """Offer a wake without claiming any new terminal status or delivery."""
        if self.closed:
            return False
        if len(self.pending) >= self.maxsize:
            raise asyncio.QueueFull
        ordinal = self.pending.append(terminal_id, busy=self._is_busy(terminal_id))
        if request is not None:
            self._requests[ordinal] = request
        self.ready.set()
        return True

    def pop_delivery(
        self,
    ) -> tuple[str, tuple[contextvars.Context, PluginRegistry | None] | None] | None:
        event = self.pending.pop_event()
        if event is None:
            return None
        ordinal, terminal_id = event
        return terminal_id, self._requests.pop(ordinal, None)

    def reserve_delivery(
        self, admission: DeliveryAdmission, *, now: float
    ) -> (
        tuple[str, tuple[contextvars.Context, PluginRegistry | None] | None, DeliveryReservation]
        | None
    ):
        while (event := self.pending.peek_event()) is not None:
            attempt = admission.try_reserve(event[1], now=now)
            if attempt.reservation is not None:
                wake = self.pop_delivery()
                assert wake is not None
                return wake[0], wake[1], attempt.reservation
            if attempt.reason != "receiver_busy":
                return None
            self.pending.park_head()
        return None

    def close(self) -> None:
        self.closed = True
        self.pending = _PendingStatusEvents()
        self._requests.clear()


class InboxService:
    """Delivers one pending message per terminal per IDLE cycle."""

    def __init__(
        self,
        *,
        admission: DeliveryAdmission | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # Explicit server-owned wiring; no request/profile can supply this owner.
        # Unbound instances retain compatibility until the API migration lands.
        self._admission = admission
        self._admission_clock = clock
        # deliver_pending is read(PENDING) → status check → mark DELIVERED → send,
        # with no atomic claim at the DB layer, so two concurrent calls for the
        # SAME terminal can both read the same oldest row before either marks it
        # and deliver one task twice. Concurrent callers are real: the status-event
        # consumer handles status/API wakes, while the OpenCode poller and the
        # reconcile sweep add independent callers. Serialize the whole
        # read→mark→send sequence per terminal; as a side effect this also keeps
        # two pastes from ever interleaving in one pane. Holders and registered
        # waiters share one entry, retired only after the final lease releases
        # its lock. Historical terminal ids must not accumulate in the registry.
        self._delivery_locks_guard = threading.Lock()
        self._delivery_locks: Dict[str, _DeliveryLockEntry] = {}
        self._operations = threading.Condition()
        self._operation_context = threading.local()
        self._accepting_operations = True
        self._active_operations = 0
        self._drain_waiters: Set[Tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = set()
        self._delivery_sink: _StatusEventSink | None = None
        self._delivery_loop: asyncio.AbstractEventLoop | None = None

    def notify_admission_changed(self, terminal_id: str | None = None) -> None:
        """Nonblocking notification from a monitor or a physical worker finally."""
        with self._operations:
            sink, loop = self._delivery_sink, self._delivery_loop
        if sink is None or loop is None:
            return

        def wake() -> None:
            if self._delivery_sink is sink and not sink.closed:
                if terminal_id is not None and not sink._is_busy(terminal_id):
                    sink.pending.completed(terminal_id)
                sink.ready.set()

        try:
            loop.call_soon_threadsafe(wake)
        except RuntimeError:
            # A closed observer loop is not a physical release witness.
            logger.debug("Inbox admission observer loop already closed")

    def _execute_reserved(
        self,
        reservation: DeliveryReservation,
        terminal_id: str,
        num_messages: int,
        registry: PluginRegistry | None,
    ) -> None:
        try:
            # Capacity is already reserved; never wait for a receiver lock.
            with self._delivery_lock(terminal_id, blocking=False) as acquired:
                if not acquired or not reservation.start():
                    return
                try:
                    self._deliver_pending_locked(terminal_id, num_messages, registry)
                finally:
                    # Includes every send, audit, receipt and status write.
                    reservation.finish()
        finally:
            reservation.cancel_before_start()
            self.notify_admission_changed(terminal_id)

    def _submit_reserved(
        self, reservation: DeliveryReservation, terminal_id: str, callback: Callable[[], None]
    ) -> Future[None]:
        """No second executor queue/worker ceiling beyond admitted reservations."""
        future: Future[None] = Future()
        context = contextvars.copy_context()

        def worker() -> None:
            if not future.set_running_or_notify_cancel():
                reservation.cancel_before_start()
                self.notify_admission_changed(terminal_id)
                return
            try:
                result = context.run(callback)
            except BaseException as error:
                future.set_exception(error)
            else:
                future.set_result(result)

        thread = threading.Thread(target=worker, name="cao-inbox-admitted")
        try:
            thread.start()
        except BaseException:
            reservation.cancel_before_start()
            self.notify_admission_changed(terminal_id)
            raise
        return future

    def queue_delivery(self, terminal_id: str, *, registry: PluginRegistry | None) -> bool:
        """Wake the bounded consumer after commit; SQL remains the durable queue.

        Called on the owning API loop. A rejected/full wake never removes the
        pending row; normal status/reconciliation and restart can discover it.
        Ambient context is carried like the former immediate to_thread call,
        and is never an authority source or a substitute for delivery checks.
        """
        with self._operations:
            if not self._accepting_operations:
                raise InboxServiceStoppingError("inbox service is stopping")
            sink = self._delivery_sink
            if sink is None:
                return False
            if asyncio.get_running_loop() is not self._delivery_loop:
                raise RuntimeError("inbox wake must use the owning API loop")
            return sink.offer_pending(terminal_id, (contextvars.copy_context(), registry))

    def start_accepting(self) -> None:
        """Open service admission for a new server lifespan.

        A prior lifespan must have drained before this instance is reused; an
        old worker must never be mistaken for work owned by a replacement API.
        """
        with self._operations:
            if self._active_operations:
                raise RuntimeError(
                    "cannot reopen inbox admission while prior operations are active"
                )
            if self._admission is not None:
                self._accepting_operations = False
                self._admission.start_accepting()
            self._accepting_operations = True

    def stop_accepting(self) -> None:
        """Fence new top-level work while allowing already-owned batches to finish."""
        with self._operations:
            self._accepting_operations = False
            if self._admission is not None:
                self._admission.stop_accepting()

    async def drain(self, *, timeout_s: float) -> None:
        """Wait for every admitted sync operation, including nested batch work.

        Call ``stop_accepting`` first. The worker thread notifies this future
        only after the outermost operation has returned, so cancelling an
        ``asyncio.to_thread`` waiter cannot make shutdown mistake a live worker
        for completed work.
        """
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s)
            or timeout_s <= 0
        ):
            raise ValueError("inbox drain timeout must be a positive finite number of seconds")

        loop = asyncio.get_running_loop()
        with self._operations:
            if self._accepting_operations:
                raise RuntimeError("inbox admission must stop before draining operations")
            if self._active_operations == 0:
                return
            waiter = loop.create_future()
            key = (loop, waiter)
            self._drain_waiters.add(key)

        try:
            await asyncio.wait_for(waiter, timeout=timeout_s)
        except asyncio.TimeoutError:
            with self._operations:
                self._drain_waiters.discard(key)
                active_operations = self._active_operations
            if active_operations == 0:
                return
            raise InboxServiceDrainTimeout(
                active_operations=active_operations,
                timeout_s=timeout_s,
            ) from None
        except BaseException:
            with self._operations:
                self._drain_waiters.discard(key)
            raise

    @staticmethod
    def _resolve_drain_waiter(waiter: asyncio.Future[None]) -> None:
        if not waiter.done():
            waiter.set_result(None)

    def _notify_drainers(
        self,
        waiters: Iterable[Tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]],
    ) -> None:
        for loop, waiter in waiters:
            try:
                loop.call_soon_threadsafe(self._resolve_drain_waiter, waiter)
            except RuntimeError:
                # A cancelled drain removes itself. A closed loop may only
                # remain here during process shutdown; no worker ownership is
                # inferred from that failed notification.
                logger.debug("Inbox drain loop already closed")

    @contextmanager
    def _track_operation(self, *, cancelled: threading.Event | None = None) -> Iterator[None]:
        depth = getattr(self._operation_context, "depth", 0)
        is_outermost = depth == 0
        if is_outermost:
            with self._operations:
                if not self._accepting_operations or (cancelled is not None and cancelled.is_set()):
                    raise InboxServiceStoppingError("inbox service is stopping")
                self._active_operations += 1

        self._operation_context.depth = depth + 1
        try:
            yield
        finally:
            self._operation_context.depth = depth
            if is_outermost:
                with self._operations:
                    self._active_operations -= 1
                    waiters: Tuple[Tuple[asyncio.AbstractEventLoop, asyncio.Future[None]], ...] = ()
                    if self._active_operations == 0:
                        waiters = tuple(self._drain_waiters)
                        self._drain_waiters.clear()
                self._notify_drainers(waiters)

    @contextmanager
    def _delivery_lock(self, terminal_id: str, *, blocking: bool = True) -> Iterator[bool]:
        """Count waiting callers before acquisition; retire after the last release."""
        with self._delivery_locks_guard:
            entry = self._delivery_locks.get(terminal_id)
            if entry is None:
                entry = _DeliveryLockEntry()
                self._delivery_locks[terminal_id] = entry
            entry.refs += 1
        acquired = False
        try:
            acquired = entry.lock.acquire(blocking=blocking)
            yield acquired
        finally:
            if acquired:
                entry.lock.release()
            with self._delivery_locks_guard:
                entry.refs -= 1
                if entry.refs == 0 and self._delivery_locks.get(terminal_id) is entry:
                    del self._delivery_locks[terminal_id]

    def _deliver_status_pending(
        self,
        terminal_id: str,
        cancelled: threading.Event,
        registry: PluginRegistry | None = None,
        reservation: DeliveryReservation | None = None,
    ) -> None:
        # Admission and the invocation's stop fence share the operation lock.
        # A late executor worker cannot join a subsequent server lifespan.
        try:
            with self._track_operation(cancelled=cancelled):
                if reservation is None:
                    self.deliver_pending(terminal_id, registry=registry)
                else:
                    self._execute_reserved(reservation, terminal_id, 1, registry)
        finally:
            if reservation is not None and reservation.cancel_before_start():
                self.notify_admission_changed(terminal_id)

    async def run(self, registry: PluginRegistry | None = None) -> None:
        deliveries: dict[str, asyncio.Future[None]] = {}
        reservations: dict[str, DeliveryReservation] = {}
        capacity = 2 * max(1, get_server_settings()["event_bus_max_queue_size"])
        sink = _StatusEventSink(capacity, lambda key: key in deliveries)
        bus.subscribe_sink("terminal.*.status", sink)
        reader: asyncio.Task | None = None
        cancelled = threading.Event()
        try:
            self._delivery_sink = sink
            self._delivery_loop = asyncio.get_running_loop()
            logger.info("InboxService started")
            while True:
                with self._operations:
                    if not self._accepting_operations:
                        return
                sink.ready.clear()
                # Keep every consumed ready event. Skip busy keys without
                # occupying executor slots waiting for their per-terminal lock.
                while self._admission is not None or len(deliveries) < _STATUS_DELIVERY_CONCURRENCY:
                    if self._admission is not None:
                        reserved = sink.reserve_delivery(
                            self._admission, now=self._admission_clock()
                        )
                        if reserved is None:
                            break
                        terminal_id, request, reservation = reserved
                        delivery_registry = registry if request is None else request[1]

                        def submit(
                            key=terminal_id, lease=reservation, selected_registry=delivery_registry
                        ):
                            return self._submit_reserved(
                                lease,
                                key,
                                lambda: self._deliver_status_pending(
                                    key, cancelled, selected_registry, lease
                                ),
                            )

                        future = submit() if request is None else request[0].run(submit)
                        reservations[terminal_id] = reservation
                        deliveries[terminal_id] = asyncio.wrap_future(future)
                        continue
                    wake = sink.pop_delivery()
                    if wake is None:
                        break
                    terminal_id, request = wake
                    delivery_registry = registry if request is None else request[1]
                    work = asyncio.to_thread(
                        self._deliver_status_pending,
                        terminal_id,
                        cancelled,
                        registry=delivery_registry,
                    )
                    if request is None:
                        task = asyncio.create_task(work, name="inbox-status-delivery")
                    else:
                        # create_task copies the context in which it is called;
                        # to_thread then carries that task's context to its worker.
                        task = request[0].run(
                            asyncio.create_task, work, name="inbox-request-delivery"
                        )
                    deliveries[terminal_id] = task
                if reader is None:
                    reader = asyncio.create_task(sink.ready.wait(), name="inbox-status-reader")
                waiting = set(deliveries.values())
                if reader is not None:
                    waiting.add(reader)
                done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                for terminal_id, delivery in tuple(deliveries.items()):
                    if delivery in done:
                        del deliveries[terminal_id]
                        reservations.pop(terminal_id, None)
                        sink.pending.completed(terminal_id)
                        try:
                            delivery.result()
                        except InboxServiceStoppingError:
                            logger.debug("Inbox status delivery rejected during shutdown")
                        except Exception as e:
                            logger.error(f"Error in InboxService: {e}")
                if reader is not None and reader in done:
                    reader.result()
                    reader = None
        finally:
            with self._operations:
                cancelled.set()
            sink.close()
            if self._delivery_sink is sink:
                self._delivery_sink = None
                self._delivery_loop = None
            bus.unsubscribe("terminal.*.status", sink)
            children = list(deliveries.values())
            if reader is not None:
                children.append(reader)
            for child in children:
                child.cancel()
            for reservation in reservations.values():
                reservation.cancel_before_start()
            # Join asyncio awaiters only. The real threads remain owned by
            # _track_operation and the server's bounded drain, not this gather.
            await asyncio.gather(*children, return_exceptions=True)

    def deliver_pending(
        self,
        terminal_id: str,
        num_messages: int = 1,
        registry: PluginRegistry | None = None,
    ) -> None:
        """Deliver pending message(s) to a ready terminal. Use num_messages=0 for all.

        Status comes from the StatusMonitor (the event-driven source of truth).
        Delivery normally happens on IDLE/COMPLETED; providers that accept input
        mid-turn (``accepts_input_while_processing``) also receive messages while
        PROCESSING/WAITING_USER_ANSWER when ``EAGER_INBOX_DELIVERY`` is on (#251).
        When a plugin registry is supplied, the originating sender and a
        ``send_message`` orchestration type are threaded to ``terminal_service``
        so ``PostSendMessageEvent`` hooks fire with correct attribution.

        Safe to call from any thread: the whole read→mark→send sequence is
        serialized per terminal (see __init__ for why that is load-bearing).
        """
        with self._track_operation():
            if self._admission is not None:
                attempt = self._admission.try_reserve(terminal_id, now=self._admission_clock())
                if attempt.reservation is not None:
                    self._execute_reserved(attempt.reservation, terminal_id, num_messages, registry)
                return
            with self._delivery_lock(terminal_id):
                self._deliver_pending_locked(terminal_id, num_messages, registry)

    def _deliver_pending_locked(
        self,
        terminal_id: str,
        num_messages: int,
        registry: PluginRegistry | None,
    ) -> None:
        limit = num_messages if num_messages > 0 else 100
        messages = get_pending_messages(terminal_id, limit=limit)
        if not messages:
            return

        # A later native batch can appear before debounced status revokes a
        # ready latch. Eager input must not interrupt that batch either.
        try:
            provider = provider_manager.get_provider(terminal_id)
        except Exception:
            return
        if getattr(provider, "has_pending_native_swarm", False) is True:
            return
        status = status_monitor.get_status(terminal_id)
        if status not in (TerminalStatus.IDLE, TerminalStatus.COMPLETED):
            # Not ready on the normal path. Eager delivery (#251) lets providers
            # that accept input mid-turn receive messages while PROCESSING or
            # WAITING_USER_ANSWER; only in that case do we need the provider.
            eager_eligible = False
            if EAGER_INBOX_DELIVERY and status in (
                TerminalStatus.PROCESSING,
                TerminalStatus.WAITING_USER_ANSWER,
            ):
                provider = provider_manager.get_provider(terminal_id)
                eager_eligible = provider is not None and getattr(
                    provider, "accepts_input_while_processing", False
                )
            if not eager_eligible:
                return

        # Mark DELIVERED before sending (#164). send_input() types into the tmux
        # pane; that output flows back through the FIFO/StatusMonitor pipeline and
        # can re-emit an IDLE/COMPLETED status event, re-entering deliver_pending.
        # If the messages were still PENDING then, they would be delivered twice.
        # Marking them DELIVERED first closes that window; the except path resets
        # them to FAILED.
        for message in messages:
            update_message_status(message.id, MessageStatus.DELIVERED)

        # Deliver in contiguous runs of the same sender. With the default
        # num_messages=1 this is a single run; when draining all pending messages
        # (num_messages=0) a batch can span multiple senders, so each run is sent
        # separately to keep PostSendMessageEvent attribution correct — otherwise
        # every message would be attributed to messages[0].sender_id.
        for sender_id, group in groupby(messages, key=lambda m: m.sender_id):
            batch = list(group)
            combined = "\n".join(m.message for m in batch)
            try:
                # Inbox rows are always asynchronous agent-to-agent work.  They
                # must never be treated as an operator's answer merely because
                # a caller did not supply a plugin registry (for example the
                # reconcile sweep or a server started without plugins).  The
                # sole interactive-answer path is the direct terminal-input
                # endpoint, which does not enqueue an inbox row.
                if registry is None:
                    terminal_service.send_input(terminal_id, combined, task_delivery=True)
                else:
                    terminal_service.send_input(
                        terminal_id,
                        combined,
                        registry=registry,
                        sender_id=sender_id,
                        orchestration_type=OrchestrationType.SEND_MESSAGE,
                        task_delivery=True,
                    )
                logger.info(f"Delivered {len(batch)} message(s) to terminal {terminal_id}")
            except TerminalNotFoundError as e:
                # Pane not resolvable yet (e.g. a herdr pane that isn't mapped
                # for this window). Treat as transient: reset to PENDING so the
                # reconcile sweep retries rather than marking FAILED. These were
                # optimistically set to DELIVERED above. (#271 semantic.)
                for message in batch:
                    update_message_status(message.id, MessageStatus.PENDING)
                logger.warning(
                    f"Pane not resolvable for terminal {terminal_id}; leaving "
                    f"{len(batch)} message(s) pending for retry: {e}"
                )
            except RuntimeUnavailableError:
                for message in batch:
                    update_message_status(message.id, MessageStatus.PENDING)
            except TerminalInputBlockedError as e:
                # A blocked terminal is a live reconciliation condition, not
                # a failed message.  Receipt-bearing providers intentionally
                # reject a follow-up until their prior result is durably
                # verified; a trust/login dialog similarly requires an
                # operator answer. A pre-paste block means this batch was
                # never accepted, so preserve it as PENDING for the normal
                # status/reconcile paths. A *post-paste* receipt-write
                # failure is different: the terminal may already be executing
                # this exact batch. Persist RECONCILE instead of putting it
                # back in PENDING, because the orphan sweep only retries
                # PENDING and must never execute an uncertain task twice.
                final_status = (
                    MessageStatus.RECONCILE
                    if e.delivery_may_have_occurred
                    else MessageStatus.PENDING
                )
                for message in batch:
                    update_message_status(message.id, final_status)
                logger.info(
                    "Terminal %s blocked inbox delivery; marking %d message(s) %s: %s",
                    terminal_id,
                    len(batch),
                    final_status.value,
                    e,
                )
            except Exception as e:
                for message in batch:
                    logger.error(f"Failed to deliver message {message.id} to {terminal_id}: {e}")
                    update_message_status(message.id, MessageStatus.FAILED)

    def deliver_managed(self, inbox_id: int, port, *, store_context) -> None:
        """Send one bridge-selected retained row through the active protected port.

        Managed rows never enter the legacy PENDING query and remain RECONCILE
        after an effect because a terminal paste is not a completion receipt.
        """
        target = _managed_inbox_target(inbox_id, store_context=store_context)
        port.send_keys(target.tmux_session, target.tmux_window, target.message.message)

    def _deliver_sweep_receiver(
        self,
        terminal_id: str,
        registry: PluginRegistry | None,
        label: str,
        reservation: DeliveryReservation | None = None,
    ) -> None:
        if reservation is not None:
            # A start handshake can fail after spawning. Independently count
            # physical children before lease.start, even if their parent loses
            # the returned Future and can no longer join it directly.
            try:
                with self._track_operation():
                    self._execute_reserved(reservation, terminal_id, 1, registry)
            except Exception as error:
                logger.debug("%s failed for %s: %s", label, terminal_id, error)
            finally:
                if reservation.cancel_before_start():
                    self.notify_admission_changed(terminal_id)
            return
        # The submitting sweep retains its outer operation until its private
        # executor joins every child. Inherit that admission, including after
        # stop_accepting(), without admitting unrelated top-level work.
        depth = getattr(self._operation_context, "depth", 0)
        self._operation_context.depth = depth + 1
        try:
            with self._delivery_lock(terminal_id, blocking=False) as acquired:
                if acquired:
                    self._deliver_pending_locked(terminal_id, 1, registry)
                # Busy receivers keep their durable PENDING rows. Never occupy
                # every sweep worker waiting on another delivery's same-key lock.
        except Exception as error:
            logger.debug("%s failed for %s: %s", label, terminal_id, error)
        finally:
            self._operation_context.depth = depth

    def _deliver_sweep(
        self, receiver_ids: Iterable[str], registry: PluginRegistry | None, label: str
    ) -> None:
        assert getattr(self._operation_context, "depth", 0) > 0
        if self._admission is not None:
            self._deliver_admitted_sweep(receiver_ids, registry, label)
            return
        receivers = iter(receiver_ids)
        try:
            with ThreadPoolExecutor(
                max_workers=_SWEEP_DELIVERY_CONCURRENCY, thread_name_prefix="cao-inbox-sweep"
            ) as executor:

                def submit(terminal_id: str) -> Future[None]:
                    # Each worker gets a fresh copy, never the parent's cursor.
                    return executor.submit(
                        contextvars.copy_context().run,
                        self._deliver_sweep_receiver,
                        terminal_id,
                        registry,
                        label,
                    )

                pending = {
                    submit(terminal_id)
                    for terminal_id in islice(receivers, _SWEEP_DELIVERY_CONCURRENCY)
                }
                while pending:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        future.result()
                    pending.update(
                        submit(terminal_id) for terminal_id in islice(receivers, len(done))
                    )
        finally:
            # On early failure, join children before releasing the unfinished
            # read cursor, and close it before outer admission/drain can finish.
            close = getattr(receivers, "close", None)
            if close is not None:
                close()

    def _deliver_admitted_sweep(
        self, receiver_ids: Iterable[str], registry: PluginRegistry | None, label: str
    ) -> None:
        assert self._admission is not None
        receivers = iter(receiver_ids)
        pending: set[Future[None]] = set()
        completed: SimpleQueue[Future[None]] = SimpleQueue()
        failure: BaseException | None = None
        try:
            if self._admission.snapshot(now=self._admission_clock()).available_slots:
                for terminal_id in receivers:
                    attempt = self._admission.try_reserve(terminal_id, now=self._admission_clock())
                    if attempt.reservation is None:
                        if attempt.reason == "receiver_busy":
                            continue
                        break
                    lease = attempt.reservation

                    def deliver_receiver(
                        key: str = terminal_id, reservation: DeliveryReservation = lease
                    ) -> None:
                        self._deliver_sweep_receiver(key, registry, label, reservation)

                    future = self._submit_reserved(lease, terminal_id, deliver_receiver)
                    pending.add(future)
                    future.add_done_callback(completed.put)
                    # Retire completed bookkeeping during streaming, not only at
                    # the end; do not scan all active children per receiver.
                    while True:
                        try:
                            finished = completed.get_nowait()
                        except Empty:
                            break
                        pending.remove(finished)
                        finished.result()
        except BaseException as error:
            failure = error
        try:
            close = getattr(receivers, "close", None)
            if close is not None:
                close()
        except BaseException as error:
            if failure is None:
                failure = error
        try:
            self._join_deliveries(pending)
        except BaseException as error:
            if failure is None:
                failure = error
        if failure is not None:
            raise failure

    @staticmethod
    def _join_deliveries(pending: Iterable[Future[None]]) -> None:
        """Join every owned child even when an earlier result raised."""
        failure: BaseException | None = None
        for future in pending:
            try:
                future.result()
            except BaseException as error:
                if failure is None:
                    failure = error
        if failure is not None:
            raise failure

    def poll_opencode_pending_messages(self, registry: PluginRegistry | None = None) -> None:
        """Poll OpenCode terminals for pending inbox messages.

        OpenCode-specific wakeup path for providers whose pipe-pane logs do not
        change after the TUI settles, so the FIFO-driven StatusMonitor may not
        emit an IDLE/COMPLETED transition to trigger delivery on its own.
        """
        with self._track_operation():
            receiver_ids = iter_pending_receiver_ids_by_provider(ProviderType.OPENCODE_CLI.value)
            self._deliver_sweep(receiver_ids, registry, "OpenCode inbox poll")

    def reconcile_orphaned_messages(self, registry: PluginRegistry | None = None) -> None:
        """Re-attempt delivery for messages stuck in PENDING past the grace window.

        Provider-agnostic safety net for issue #131: when a receiving terminal is
        already idle, the immediate (on POST) delivery path may miss on a stale
        status, and an idle terminal produces no new output so the event-driven
        StatusMonitor never emits an IDLE/COMPLETED event to wake delivery —
        leaving the message orphaned. This sweep finds any such message and routes
        it back through the normal delivery gate (``deliver_pending``).

        Only messages older than ``INBOX_RECONCILE_GRACE_SECONDS`` are considered,
        so the sweep never competes with the fast paths for freshly queued
        messages — it only adopts ones they have already missed.
        """
        with self._track_operation():
            receiver_ids = iter_pending_receiver_ids_older_than(INBOX_RECONCILE_GRACE_SECONDS)
            self._deliver_sweep(receiver_ids, registry, "Inbox reconciliation")


inbox_service = InboxService()
