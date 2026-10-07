"""Regressions for detached graph ownership, budgets and API envelopes."""

import asyncio

import pytest
from fastapi import HTTPException

from cli_agent_orchestrator.api import main as api_main
from cli_agent_orchestrator.graph import cache as cache_module
from cli_agent_orchestrator.graph.cache import GraphViewCache
from cli_agent_orchestrator.graph.models import GraphView, Node


@pytest.fixture(autouse=True)
def isolated_graph_lifecycle(monkeypatch):
    monkeypatch.setattr(api_main.app.state, "graph_build_stopping", False, raising=False)
    monkeypatch.setattr(api_main.app.state, "graph_build_tasks", set(), raising=False)


def view(name):
    return GraphView(nodes=[Node(id=name, kind="topic", label=name)], edges=[])


@pytest.mark.asyncio
async def test_cancelled_request_retry_joins_original_build():
    cache = GraphViewCache()
    started, release = asyncio.Event(), asyncio.Event()
    builds = 0

    async def build():
        nonlocal builds
        builds += 1
        started.set()
        await release.wait()
        return view("original")

    caller = asyncio.create_task(cache.get_or_build(("owner",), build))
    await started.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    retry = asyncio.create_task(cache.get_or_build(("owner",), build))
    await asyncio.sleep(0)
    release.set()
    result, _, _ = await retry
    assert builds == 1
    assert result.nodes[0].id == "original"


@pytest.mark.asyncio
async def test_admission_bounds_active_and_queued_work():
    cache = GraphViewCache(max_concurrent_builds=1, max_pending_builds=1)
    release, started = asyncio.Event(), asyncio.Event()
    builds = []

    async def build(name):
        builds.append(name)
        started.set()
        await release.wait()
        return view(name)

    first = cache.get_or_build_task(("a",), lambda: build("a"))
    await started.wait()
    second = cache.get_or_build_task(("b",), lambda: build("b"))
    try:
        assert cache.build_status(("b",))["build_state"] == "queued"
        with pytest.raises(cache_module.GraphBuildQueueFullError) as exc:
            cache.get_or_build_task(("c",), lambda: build("c"))
        assert exc.value.build_status == {"build_state": "rejected_queue_full"}
        assert cache.inflight_task(("c",)) is None
        assert builds == ["a"]
    finally:
        release.set()
        await asyncio.gather(first, second)
    assert builds == ["a", "b"]


@pytest.mark.asyncio
async def test_deadline_releases_key_and_bounds_diagnostics():
    cache = GraphViewCache(build_max_s=0.005, max_entries=2)
    for i in range(4):
        with pytest.raises(cache_module.GraphBuildDeadlineError):
            await cache.get_or_build_task((i,), lambda: asyncio.Event().wait())
    assert cache.build_status((0,)) is None
    assert cache.build_status((3,))["build_state"] == "failed_deadline"
    assert cache.inflight_task((3,)) is None
    result = await cache.get_or_build_task((3,), lambda: async_view("recovered"))
    assert result.nodes[0].id == "recovered"


async def async_view(name):
    return view(name)


@pytest.mark.asyncio
async def test_entry_cap_evicts_least_recently_used_without_owner_mix():
    cache = GraphViewCache(max_entries=2)
    calls = []

    async def build(name):
        calls.append(name)
        return view(name)

    for owner in ["a", "b", "a", "c", "a", "b"]:
        result, _, _ = await cache.get_or_build((owner,), lambda: build(owner))
        assert result.nodes[0].id == owner
    assert calls == ["a", "b", "c", "b"]
    assert len(cache._entries) == 2


@pytest.mark.asyncio
async def test_shutdown_cancels_and_drains_owned_builds():
    cache = GraphViewCache()
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def build():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = cache.get_or_build_task(("owner",), build)
    await started.wait()
    await cache.shutdown()
    assert cancelled.is_set()
    assert task.cancelled()
    assert cache.inflight_task(("owner",)) is None


@pytest.mark.asyncio
async def test_cache_owned_api_timeout_keeps_work_and_protects_envelope():
    cache = GraphViewCache()
    release = asyncio.Event()

    class Provider:
        async def project(self, **filters):
            raise AssertionError("must use detached hook")

        def project_inflight(self, **filters):
            async def build():
                await release.wait()
                return view("done")

            return cache.get_or_build_task(("owner",), build)

        def projection_status(self, **filters):
            return {"build_state": "in_progress", "kind": "wrong", "provider": "wrong"}

    provider = Provider()
    try:
        with pytest.raises(HTTPException) as caught:
            await api_main._project_graph_with_timeout(
                provider, {}, provider="test", timeout_s=0.005
            )
        detail = caught.value.detail
        assert detail["provider"] == "test"
        assert detail["kind"] == "graph_projection_timeout"
        assert detail["retryable"] is True
        assert detail["retry_after_s"] == 5
        assert caught.value.headers == {"Retry-After": "5"}
        assert detail["build_state"] == "in_progress"
        assert not cache.inflight_task(("owner",)).cancelled()
    finally:
        release.set()
        await cache.shutdown()


@pytest.mark.asyncio
async def test_generic_api_timeout_cancels_request_owned_projection():
    cancelled = asyncio.Event()

    class Provider:
        async def project(self, **filters):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    with pytest.raises(HTTPException) as caught:
        await api_main._project_graph_with_timeout(Provider(), {}, provider="test", timeout_s=0.005)
    assert cancelled.is_set()
    assert caught.value.detail["retryable"] is True


@pytest.mark.asyncio
async def test_api_graph_shutdown_drains_tracked_tasks():
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(work())
    await started.wait()
    api_main.app.state.graph_build_tasks = {task}
    await api_main._shutdown_graph_builds(api_main.app)
    assert cancelled.is_set()
    assert api_main.app.state.graph_build_tasks == set()


@pytest.mark.asyncio
async def test_queue_rejection_metadata_cannot_override_timeout_contract():
    class Provider:
        def project_inflight(self, **filters):
            raise cache_module.GraphBuildQueueFullError(
                {
                    "build_state": "rejected_queue_full",
                    "kind": "wrong",
                    "provider": "wrong",
                    "retryable": False,
                    "retry_after_s": -1,
                }
            )

    with pytest.raises(HTTPException) as caught:
        await api_main._project_graph_with_timeout(Provider(), {}, provider="safe", timeout_s=0.005)
    assert caught.value.detail["kind"] == "graph_projection_timeout"
    assert caught.value.detail["provider"] == "safe"
    assert caught.value.detail["retryable"] is True
    assert caught.value.detail["retry_after_s"] == 5


@pytest.mark.asyncio
async def test_retries_register_detached_failure_callback_only_once(caplog):
    release = asyncio.Event()

    async def build():
        await release.wait()
        raise RuntimeError("later build failure")

    task = asyncio.create_task(build())

    class Provider:
        def project_inflight(self, **filters):
            return task

    for _ in range(2):
        with pytest.raises(HTTPException):
            await api_main._project_graph_with_timeout(
                Provider(), {}, provider="one", timeout_s=0.005
            )
    release.set()
    await asyncio.gather(task, return_exceptions=True)
    messages = [
        r.message for r in caplog.records if "detached graph projection failed" in r.message
    ]
    assert len(messages) == 1


@pytest.mark.asyncio
async def test_cache_shutdown_fences_admission_while_draining():
    cache = GraphViewCache()
    started, draining, release_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
    late_tasks = []

    async def build():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            draining.set()
            await release_cleanup.wait()

    original = cache.get_or_build_task(("original",), build)
    await started.wait()
    shutdown = asyncio.create_task(cache.shutdown())
    await draining.wait()
    try:
        with pytest.raises(RuntimeError, match="shut|clos"):
            late_tasks.append(cache.get_or_build_task(("late",), lambda: asyncio.Event().wait()))
    finally:
        release_cleanup.set()
        await shutdown
        for task in late_tasks:
            task.cancel()
        await asyncio.gather(*late_tasks, return_exceptions=True)
    assert original.cancelled()
    assert not cache._inflight
    with pytest.raises(RuntimeError, match="shut|clos"):
        cache.get_or_build_task(("after_shutdown",), lambda: async_view("unexpected"))


@pytest.mark.asyncio
async def test_api_graph_shutdown_fences_provider_admission_while_draining(monkeypatch):
    started, draining, release_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
    provider_calls, late_tasks = [], []
    monkeypatch.setattr(api_main.app.state, "graph_build_tasks", set(), raising=False)
    monkeypatch.setattr(api_main.app.state, "graph_build_stopping", False, raising=False)

    async def build():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            draining.set()
            await release_cleanup.wait()

    original = asyncio.create_task(build())
    await started.wait()
    api_main.app.state.graph_build_tasks.add(original)

    class Provider:
        def project_inflight(self, **filters):
            provider_calls.append(filters)
            task = asyncio.create_task(async_view("unexpected"))
            late_tasks.append(task)
            return task

    shutdown = asyncio.create_task(api_main._shutdown_graph_builds(api_main.app))
    await draining.wait()
    try:
        with pytest.raises(HTTPException) as caught:
            await api_main._project_graph_with_timeout(Provider(), {}, provider="late")
        assert caught.value.status_code == 503
        assert caught.value.detail["kind"] == "graph_shutting_down"
        assert caught.value.detail["retryable"] is False
        assert provider_calls == []
    finally:
        release_cleanup.set()
        await shutdown
        await asyncio.gather(*late_tasks, return_exceptions=True)
    assert original.cancelled()
    assert api_main.app.state.graph_build_tasks == set()


@pytest.mark.asyncio
async def test_api_graph_lifecycle_restart_reopens_same_app(monkeypatch):
    monkeypatch.setattr(api_main.app.state, "graph_build_tasks", set(), raising=False)
    monkeypatch.setattr(api_main.app.state, "graph_build_stopping", False, raising=False)
    provider_calls = []

    class Provider:
        async def project(self, **filters):
            provider_calls.append(filters)
            return view("restarted")

    for _ in range(2):
        await api_main._shutdown_graph_builds(api_main.app)
        with pytest.raises(HTTPException):
            await api_main._project_graph_with_timeout(Provider(), {}, provider="lifecycle")
        api_main._start_graph_builds(api_main.app)
        result = await api_main._project_graph_with_timeout(Provider(), {}, provider="lifecycle")
        assert result.nodes[0].id == "restarted"
        assert api_main.app.state.graph_build_tasks == set()
    assert len(provider_calls) == 2


@pytest.mark.asyncio
async def test_api_graph_restart_refuses_to_forget_live_task(monkeypatch):
    task = asyncio.create_task(asyncio.Event().wait())
    registry = {task}
    monkeypatch.setattr(api_main.app.state, "graph_build_tasks", registry, raising=False)
    monkeypatch.setattr(api_main.app.state, "graph_build_stopping", True, raising=False)
    try:
        with pytest.raises(RuntimeError, match="live|active|running|drain"):
            api_main._start_graph_builds(api_main.app)
        assert api_main.app.state.graph_build_tasks is registry
        assert api_main.app.state.graph_build_stopping is True
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
