"""The real app lifespan must own admission; Herdr must hand off without SQL."""

import asyncio
from test.api.test_work_launch_composition import _StartupTaskTracker, composed_server
from test.services.test_inbox_admission_runtime import operational_fixture
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from cli_agent_orchestrator.api import main
from cli_agent_orchestrator.backends.herdr_backend import HerdrBackend
from cli_agent_orchestrator.services import inbox_admission_runtime as module
from cli_agent_orchestrator.services.inbox_service import InboxService


@pytest.fixture
def wired_server(composed_server, monkeypatch):
    with monkeypatch.context() as patch:
        inbox = InboxService()
        captured = []
        tracker = _StartupTaskTracker()
        patch.setattr(main, "asyncio", tracker)

        def factory(selected):
            assert selected is inbox
            f = operational_fixture()
            value = module.InboxAdmissionRuntime(
                inbox,
                monitor=f.monitor,
                bootstrap=f.bootstrap,
                clock=lambda: f.clock[0],
                drain_timeout_s=10,
            )
            captured.append(value)
            return value

        patch.setattr(main, "inbox_service", inbox)
        patch.setattr(module, "runtime_from_environment", factory)
        # Peer-discovery signing keys are orthogonal to this composition test.
        # Do not read/create anything in an operator profile during TestClient.
        patch.setattr(
            "cli_agent_orchestrator.services.local_peer_registry.register_instance", lambda: None
        )
        patch.setattr(
            "cli_agent_orchestrator.services.local_peer_registry.unregister_instance", lambda: None
        )
        patch.setattr(
            "cli_agent_orchestrator.services.local_peer_service.reconcile_local_peer_tasks_at_startup",
            lambda: None,
        )
        yield SimpleNamespace(inbox=inbox, captured=captured, patch=patch, tracker=tracker)


def test_main_lifespan_binds_and_physically_retires_admission(wired_server):
    with TestClient(main.app):
        assert len(wired_server.captured) == 1
        value = wired_server.captured[0]
        assert main.app.state.inbox_admission_runtime is value
        assert wired_server.inbox._admission is value.monitor.owner
        work = main.app.state.durable_launch_gateway._launch_runtime_provider._runtime._admission
        assert work._resource_admission is value.monitor.owner
        assert work._resource_clock() == value.clock()
        assert value.available and not value.retired
    assert value.retired
    assert not hasattr(main.app.state, "inbox_admission_runtime")


def test_failed_startup_retires_partially_started_admission(wired_server):
    def fail():
        raise RuntimeError("controlled-post-admission-startup-failure")

    wired_server.patch.setattr(main, "get_backend", fail)
    with pytest.raises(RuntimeError, match="controlled-post-admission-startup-failure"):
        with TestClient(main.app):
            pytest.fail("startup must not yield")
    assert len(wired_server.captured) == 1
    assert wired_server.captured[0].retired
    assert not hasattr(main.app.state, "inbox_admission_runtime")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["exception", "cancelled"])
async def test_exceptional_lifespan_exit_still_retires_resource_runtime(wired_server, kind):
    entered = asyncio.Event()

    async def serve():
        async with main.lifespan(main.app):
            entered.set()
            if kind == "exception":
                raise RuntimeError("controlled-lifespan-body-failure")
            await asyncio.Future()

    task = asyncio.create_task(serve())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        if kind == "cancelled":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RuntimeError, match="controlled-lifespan-body-failure"):
                await task
        assert wired_server.captured[0].retired
        assert not hasattr(main.app.state, "inbox_admission_runtime")
        assert all(owned.done() for owned in wired_server.tracker.tasks)
        assert wired_server.inbox._delivery_sink is None
    finally:
        # Join only tasks captured as created by this private app lifespan.
        # No historical/external jobs participate in this pytest loop.
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for owned in wired_server.tracker.tasks:
            owned.cancel()
        await asyncio.gather(*wired_server.tracker.tasks, return_exceptions=True)
        for value in wired_server.captured:
            await value.stop()
            if getattr(main.app.state, "inbox_admission_runtime", None) is value:
                delattr(main.app.state, "inbox_admission_runtime")


def test_next_compatibility_lifetime_unbinds_retired_resource_owner(wired_server):
    with TestClient(main.app):
        assert wired_server.inbox._admission is not None
    assert wired_server.captured[0].retired
    wired_server.patch.setattr(module, "runtime_from_environment", lambda inbox: None)
    with TestClient(main.app):
        assert wired_server.inbox._admission is None
        assert not hasattr(main.app.state, "inbox_admission_runtime")


def test_herdr_callback_queues_common_consumer_instead_of_inline_delivery(wired_server):
    callbacks, queued = [], []

    class Backend(HerdrBackend):
        def __init__(self):
            self._herdr_session = "controlled-admission-session"

    class Herdr:
        def __init__(self, *, herdr_session, delivery_callback):
            callbacks.append(delivery_callback)

        async def start(self):
            await asyncio.Future()

    def inline(*_a, **_k):
        raise AssertionError("Herdr must not execute SQL/input inline")

    wired_server.patch.setattr(main, "get_backend", Backend)
    wired_server.patch.setattr(main, "HerdrInboxService", Herdr)
    wired_server.patch.setattr(wired_server.inbox, "deliver_pending", inline)
    wired_server.patch.setattr(
        wired_server.inbox,
        "queue_delivery",
        lambda tid, *, registry: queued.append((tid, registry)) or True,
    )
    with TestClient(main.app) as client:
        assert len(callbacks) == 1
        client.portal.call(callbacks[0], "controlled-worker")
        assert queued == [("controlled-worker", main.app.state.plugin_registry)]


@pytest.mark.asyncio
async def test_work_dispatcher_retires_before_resource_runtime(wired_server):
    from cli_agent_orchestrator.backends import work_registry
    from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend

    entered = asyncio.Event()
    retired_at_dispatcher_exit = []

    async def dispatcher(gateway):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            value = wired_server.captured[0]
            retired_at_dispatcher_exit.append(value.retired)
            assert not value.available

    wired_server.patch.setattr(
        work_registry, "local_work_backends_for", lambda _: {"test": TmuxBackend()}
    )
    wired_server.patch.setattr(main, "_run_registered_work_dispatcher", dispatcher)
    async with main.lifespan(main.app):
        await asyncio.wait_for(entered.wait(), 10)
    assert retired_at_dispatcher_exit == [False]
    assert wired_server.captured[0].retired
    assert not hasattr(main.app.state, "inbox_admission_runtime")
