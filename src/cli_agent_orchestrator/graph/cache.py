"""Per-owner and projection GraphView cache.

Issue #348, perf follow-up.

DELIBERATE ADR REVERSAL. The original graph-layer design record specified
"lint-on-demand, no caching machinery" (ADR-7): every ``/graph/{provider}``
request re-ran ``wiki_lint.run_lint`` in-request. Profiling the shipped
``memory`` provider on ``scope=global`` measured that projection at ~30s
typical and up to ~148s under load — worse than the frontend's 120s timeout,
so the UI aborted before the server answered. The dominant cost is NOT the LLM
contradiction detector (only ~8.5s / 3 pairs / 0 findings on global) but the
ripgrep-based ``stale_claim`` detector (~20s: ~95 ``rg`` subprocess spawns over
the whole repo). Caching the *projected* GraphView sidesteps the entire run_lint
cost on repeat views regardless of which detector dominates.

This module lives in the graph layer ONLY — it does not touch the shipped
``wiki_lint`` / ``memory_service`` modules (their pure-read / no-cache contracts
are preserved). A ``memory`` GraphProvider opts in by wrapping its build in
``get_or_build``.

Staleness tradeoff (chosen: SHORT TTL, not write-invalidation): wiring
invalidation into the memory write path (``memory_service.store`` / ``forget`` /
``consolidate``) would mean editing a shipped module and reaching across the
graph-layer boundary into it — invasive, and it couples the graph cache to the
memory service's internals. Instead we use a short TTL (``DEFAULT_TTL_S``, 5
min): the graph can be up to TTL seconds stale after a memory edit, which for a
human-viewed knowledge graph is an acceptable price for a self-contained,
boundary-respecting cache. ``invalidate`` is still exposed so a future write-path
hook can wire proactive invalidation without changing this module's shape.

Fingerprint-bearing providers can produce a new key after each configuration
change. Expired entries are therefore swept on every lookup, and per-key locks
are reference-counted and reclaimed once no caller or live entry needs them.
Completed entries are additionally capped with LRU eviction. Cache-owned tasks
survive caller cancellation; explicit shutdown cancels and drains their coroutines.
Blocking work already running in to_thread cannot be forcibly stopped.
"""

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from typing import Any, Awaitable, Callable, Hashable, Literal, Optional

from cli_agent_orchestrator.graph.models import GraphView

# 5 minutes. First request within a window pays the full projection cost;
# repeats return the cached GraphView instantly. Also the maximum staleness a
# viewer can observe after a memory write, given we chose TTL over
# write-invalidation (see module docstring).
DEFAULT_TTL_S = 300.0
GRAPH_BUILD_MAX_S = 600.0
GRAPH_BUILD_CONCURRENCY = 2
GRAPH_BUILD_QUEUE_MAX = 2
GRAPH_CACHE_MAX_ENTRIES = 64
logger = logging.getLogger(__name__)

# Providers include persistent owner identity as well as normalized projection
# parameters (scope, scope_id, lint_enabled). The cache is agnostic to the
# provider's identity fields; the complete key also partitions single-flight.
CacheKey = tuple[Hashable, ...]


@dataclass
class _Entry:
    view: GraphView
    created_monotonic: float
    as_of: str  # ISO-8601 UTC wall-clock of the build, surfaced as meta.as_of


BuildState = Literal[
    "in_progress",
    "started",
    "queued",
    "rejected_queue_full",
    "failed_deadline",
]


@dataclass
class BuildStatus:
    """Observable state for one detached projection build."""

    state: BuildState
    started_monotonic: float
    started_at: str


class GraphBuildDeadlineError(asyncio.TimeoutError):
    """A graph build exceeded ``GRAPH_BUILD_MAX_S`` and made its key retryable."""


class GraphBuildQueueFullError(Exception):
    """No detached task was created because the bounded build queue is full."""

    def __init__(self, build_status: dict[str, Any]) -> None:
        super().__init__("graph build queue is full")
        self.build_status = build_status


class GraphViewCache:
    """Bounded TTL cache with cache-owned single-flight tasks.

    Atomic task admission deduplicates cold keys on one event loop. Compatibility
    callers retain per-key locks and the tuple-returning get_or_build API. Tasks
    remain owned here when a waiting caller times out or cancels. Deadlines bound
    coroutine lifetime, not blocking executor bodies that have already started.
    """

    def __init__(
        self,
        ttl_s: float = DEFAULT_TTL_S,
        *,
        clock: Callable[[], float] = time.monotonic,
        build_max_s: float = GRAPH_BUILD_MAX_S,
        max_concurrent_builds: int = GRAPH_BUILD_CONCURRENCY,
        max_pending_builds: int = GRAPH_BUILD_QUEUE_MAX,
        max_entries: int = GRAPH_CACHE_MAX_ENTRIES,
    ) -> None:
        if max_concurrent_builds < 1 or max_pending_builds < 0 or max_entries < 1:
            raise ValueError("invalid graph cache admission limits")
        if build_max_s <= 0:
            raise ValueError("build_max_s must be positive")
        self._build_max_s = build_max_s
        self._max_entries = max_entries
        self._inflight: dict[CacheKey, asyncio.Task[GraphView]] = {}
        self._closed = False
        self._statuses: dict[CacheKey, BuildStatus] = {}
        self._failed_deadlines: OrderedDict[CacheKey, BuildStatus] = OrderedDict()
        self._build_slots = asyncio.Semaphore(max_concurrent_builds)
        self._active_builds = 0
        self._max_concurrent_builds = max_concurrent_builds
        self._max_pending_builds = max_pending_builds
        self._ttl = ttl_s
        self._clock = clock
        self._entries: OrderedDict[CacheKey, _Entry] = OrderedDict()
        self._locks: dict[CacheKey, asyncio.Lock] = {}
        self._lock_users: dict[CacheKey, int] = {}
        # Guards mutation of the ``_locks`` map itself so two coroutines racing
        # to create/reference the per-key lock can't each use a different one.
        self._locks_guard = asyncio.Lock()

    async def _lock_for(self, key: CacheKey) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            self._lock_users[key] = self._lock_users.get(key, 0) + 1
            return lock

    def _release_lock_user(self, key: CacheKey, lock: asyncio.Lock) -> None:
        """Release one caller's lock reference without yielding the event loop."""
        users = self._lock_users.get(key)
        if users is None:
            return
        if users > 1:
            self._lock_users[key] = users - 1
            return
        self._lock_users.pop(key, None)
        if key not in self._entries and self._locks.get(key) is lock:
            self._locks.pop(key, None)

    def _prune_unused_lock(self, key: CacheKey) -> None:
        if self._lock_users.get(key, 0) == 0 and key not in self._entries:
            self._locks.pop(key, None)
            self._lock_users.pop(key, None)

    def _sweep(self) -> None:
        """Reclaim expired entries and every unreferenced orphan lock."""
        now = self._clock()
        expired = [
            key
            for key, entry in self._entries.items()
            if now - entry.created_monotonic >= self._ttl
        ]
        for key in expired:
            self._entries.pop(key, None)
        for key in tuple(self._locks):
            self._prune_unused_lock(key)
        for key, users in tuple(self._lock_users.items()):
            if users == 0 and key not in self._locks:
                self._lock_users.pop(key, None)

    def _fresh(self, key: CacheKey) -> Optional[_Entry]:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._clock() - entry.created_monotonic >= self._ttl:
            # Evict the expired entry and its lock only when no caller holds a
            # reference. Referenced locks survive until the final caller exits.
            del self._entries[key]
            self._prune_unused_lock(key)
            return None
        self._entries.move_to_end(key)
        return entry

    async def get_or_build(
        self, key: CacheKey, builder: Callable[[], Awaitable[GraphView]]
    ) -> tuple[GraphView, bool, str]:
        """Return ``(view, cached, as_of)`` for ``key``.

        ``cached`` is True when a fresh entry was served without calling
        ``builder``. ``builder`` is invoked at most once per (key, window) even
        under concurrent callers.
        """
        if self._closed:
            raise RuntimeError("graph cache is shut down")
        self._sweep()
        entry = self._fresh(key)
        if entry is not None:
            return entry.view, True, entry.as_of

        lock: Optional[asyncio.Lock] = None
        try:
            lock = await self._lock_for(key)
            async with lock:
                # Re-check under the lock: a concurrent caller may have built it
                # while we waited (single-flight — this is where the herd collapses
                # onto one build).
                entry = self._fresh(key)
                if entry is not None:
                    return entry.view, True, entry.as_of

                result = await asyncio.shield(self.get_or_build_task(key, builder))
                entry = self._entries.get(key)
                return (
                    entry.view if entry is not None else result,
                    bool(result.meta["cached"]),
                    str(result.meta["as_of"]),
                )
        finally:
            if lock is not None:
                self._release_lock_user(key, lock)

    def _evict_lru_entries(self) -> None:
        """Enforce the completed-entry cap without evicting active keys."""
        while len(self._entries) > self._max_entries:
            candidate = next(
                (entry_key for entry_key in self._entries if entry_key not in self._inflight),
                None,
            )
            if candidate is None:
                # Correctness wins over the memory target. Admission hard-caps
                # this temporary overshoot at the number of in-flight builds.
                return
            self._entries.pop(candidate)
            self._prune_unused_lock(candidate)

    def _record_failed_deadline(self, key: CacheKey, status: BuildStatus) -> None:
        """Keep recent deadline diagnostics bounded independently of views."""
        self._failed_deadlines[key] = status
        self._failed_deadlines.move_to_end(key)
        while len(self._failed_deadlines) > self._max_entries:
            self._failed_deadlines.popitem(last=False)

    def get_or_build_task(
        self, key: CacheKey, builder: Callable[[], Awaitable[GraphView]]
    ) -> asyncio.Future[GraphView]:
        """Return the cache-owned future for ``key``, starting it if needed.

        Calling this method is atomic on the event-loop thread: there is no
        await between checking and inserting ``_inflight``. Consequently every
        concurrent or retrying caller receives the same task for a cold key.
        Fresh hits use an already-resolved Future and do not enter the detached
        task registry.
        """
        if self._closed:
            raise RuntimeError("graph cache is shut down")
        self._sweep()
        entry = self._fresh(key)
        if entry is not None:
            future = asyncio.get_running_loop().create_future()
            future.set_result(self._with_provenance(entry.view, cached=True, as_of=entry.as_of))
            return future

        task = self._inflight.get(key)
        if task is not None:
            return task

        started_at = datetime.now(timezone.utc).isoformat()
        started_monotonic = self._clock()
        if len(self._inflight) >= self._max_concurrent_builds + self._max_pending_builds:
            raise GraphBuildQueueFullError({"build_state": "rejected_queue_full"})

        initial_state: BuildState = (
            "started" if len(self._inflight) < self._max_concurrent_builds else "queued"
        )
        self._statuses[key] = BuildStatus(
            state=initial_state,
            started_monotonic=started_monotonic,
            started_at=started_at,
        )
        self._failed_deadlines.pop(key, None)
        task = asyncio.create_task(self._run_build(key, builder), name=f"graph-build:{key!r}")
        self._inflight[key] = task
        task.add_done_callback(partial(self._build_done, key))
        return task

    async def _run_build(
        self, key: CacheKey, builder: Callable[[], Awaitable[GraphView]]
    ) -> GraphView:
        async with self._build_slots:
            self._active_builds += 1
            status = self._statuses[key]
            status.state = "in_progress"
            try:
                view = await asyncio.wait_for(builder(), timeout=self._build_max_s)
            except asyncio.TimeoutError as exc:
                status.state = "failed_deadline"
                self._record_failed_deadline(key, status)
                raise GraphBuildDeadlineError(
                    f"graph build exceeded {self._build_max_s:g} seconds"
                ) from exc
            finally:
                self._active_builds -= 1

        as_of = datetime.now(timezone.utc).isoformat()
        self._entries[key] = _Entry(
            view=view,
            created_monotonic=self._clock(),
            as_of=as_of,
        )
        self._entries.move_to_end(key)
        self._evict_lru_entries()
        return self._with_provenance(view, cached=False, as_of=as_of)

    def _build_done(self, key: CacheKey, task: asyncio.Task[GraphView]) -> None:
        """Retrieve failures and release the cache's strong task reference."""
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            exc = None
        if exc is not None:
            logger.error(
                "detached graph build failed for %r: %r",
                key,
                exc,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        if self._inflight.get(key) is task:
            self._inflight.pop(key, None)
            self._statuses.pop(key, None)
            self._evict_lru_entries()
            self._prune_unused_lock(key)

    def build_status(self, key: CacheKey) -> Optional[dict[str, Any]]:
        """Return additive timeout metadata for an active or deadline-failed key."""
        status = self._statuses.get(key) or self._failed_deadlines.get(key)
        if status is None:
            return None
        return self._status_dict(status)

    def _status_dict(self, status: BuildStatus) -> dict[str, Any]:
        return {
            "build_state": status.state,
            "build_elapsed_s": max(0.0, self._clock() - status.started_monotonic),
            "build_started_at": status.started_at,
        }

    def inflight_task(self, key: CacheKey) -> Optional[asyncio.Task[GraphView]]:
        """Return the strongly-referenced task for shutdown tracking."""
        return self._inflight.get(key)

    @staticmethod
    def _with_provenance(view: GraphView, *, cached: bool, as_of: str) -> GraphView:
        return GraphView(
            nodes=view.nodes,
            edges=view.edges,
            meta=make_meta(view.meta, cached=cached, as_of=as_of),
        )

    async def shutdown(self) -> None:
        """Cancel and drain detached tasks; blocking executor work may outlive them."""
        self._closed = True
        tasks = tuple(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.clear()
        self._failed_deadlines.clear()

    def invalidate(self, key: CacheKey) -> None:
        """Drop a single key's entry (no-op if absent).

        Exposed for a future write-path hook; unused today (we chose short-TTL
        over write-invalidation — see module docstring).
        """
        self._entries.pop(key, None)
        self._prune_unused_lock(key)

    def clear(self) -> None:
        """Drop cached entries and unreferenced locks.

        In-flight callers retain their shared lock and may repopulate the cache,
        preserving the established clear-during-build serialization behavior.
        """
        self._entries.clear()
        for key in tuple(self._locks):
            self._prune_unused_lock(key)
        for key, users in tuple(self._lock_users.items()):
            if users == 0:
                self._lock_users.pop(key, None)


def make_meta(base: dict[str, Any], *, cached: bool, as_of: str) -> dict[str, Any]:
    """Return a copy of ``base`` meta annotated with cache provenance.

    Never mutates ``base`` (the cached GraphView's own meta must stay
    untouched, since the same instance is served to every hit).
    """
    return {**base, "cached": cached, "as_of": as_of}
