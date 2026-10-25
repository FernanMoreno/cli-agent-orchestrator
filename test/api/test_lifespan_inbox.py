"""Tests for the herdr inbox-watcher branch of the FastAPI lifespan.

After the event-driven refactor (#273), the tmux backend delivers inbox messages
through the in-process FIFO -> EventBus pipeline (StatusMonitor / LogWriter /
InboxService consumers started unconditionally in the lifespan), so there is no
longer a tmux ``PollingObserver`` watchdog branch. The lifespan starts an extra
watcher ONLY for the herdr backend:

- HerdrBackend -> HerdrInboxService (socket events), scheduled as an asyncio task
- anything else -> nothing extra (the EventBus consumers above handle it)

These tests pin the herdr startup wiring and shutdown teardown. They do not
open a herdr socket or database. The final regression test deliberately uses
real asyncio tasks and a real, bounded worker thread.

Mocking notes:
- Most tests replace ``asyncio.create_task`` with a fake that returns a
  ``_FakeTask``. A real task can't be created from ``MagicMock().start()`` (not
  a coroutine), and those tests need to spy on ``.cancel()`` while still being
  awaitable for the ``await task`` in shutdown. ``_FakeTask`` raises
  ``CancelledError`` once cancelled, exercising the cancellation branch.
- The shutdown regression test opts into real tasks and uses a real worker
  thread to pin the ``asyncio.to_thread`` cancellation boundary.
- ``MagicMock(spec=HerdrBackend)`` satisfies ``isinstance(backend, HerdrBackend)``.
"""

import asyncio
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from cli_agent_orchestrator.api.main import app, lifespan
from cli_agent_orchestrator.backends.herdr_backend import HerdrBackend
from cli_agent_orchestrator.plugins import PluginRegistry

# --- Test doubles -------------------------------------------------------


class _FakeTask:
    """Stand-in for asyncio.Task: awaitable and records ``cancel()`` calls.

    Mirrors the one behavior shutdown depends on: awaiting a task that has been
    cancelled raises ``CancelledError``. ``source`` keeps the original argument
    passed to ``create_task`` so a specific task can be located in assertions.
    """

    def __init__(self, source: object) -> None:
        self.source = source
        # Close real coroutines so Python does not warn "never awaited".
        if asyncio.iscoroutine(source):
            source.close()
        self.cancel = MagicMock(name="cancel")

    def __await__(self):
        async def _body() -> None:
            if self.cancel.called:
                raise asyncio.CancelledError()
            return None

        return _body().__await__()


def _make_fake_create_task(tasks: list) -> object:
    """Build a ``create_task`` replacement that records every created task."""

    def _fake_create_task(coro, *_args, **_kwargs) -> _FakeTask:
        task = _FakeTask(coro)
        tasks.append(task)
        return task

    return _fake_create_task


def _find_task(tasks: list, source: object) -> "_FakeTask | None":
    """Return the recorded task created from ``source``, or None."""
    for task in tasks:
        if task.source is source:
            return task
    return None


async def _fake_flow_daemon() -> None:
    """Replacement for flow_daemon so create_task gets a closeable coroutine."""


async def _fake_opencode_daemon(registry: object) -> None:
    """Replacement for opencode_inbox_delivery_daemon (takes a registry arg)."""
    del registry


@contextmanager
def _patched_lifespan(backend: object, tasks: list | None):
    """Patch every external dependency the lifespan touches at startup/shutdown.

    Yields a namespace of the mocks tests assert against.
    """
    with ExitStack() as stack:

        def patch_main(name: str, **kwargs):
            return stack.enter_context(patch(f"cli_agent_orchestrator.api.main.{name}", **kwargs))

        # Intercept task creation for wiring tests; ``None`` opts into real
        # tasks for the worker-thread shutdown regression below.
        if tasks is not None:
            stack.enter_context(patch("asyncio.create_task", _make_fake_create_task(tasks)))

        # No-op the side-effecting startup calls.
        patch_main("setup_logging")
        patch_main("init_db")
        stack.enter_context(
            patch("cli_agent_orchestrator.services.local_peer_registry.register_instance")
        )
        stack.enter_context(
            patch("cli_agent_orchestrator.services.local_peer_registry.unregister_instance")
        )
        stack.enter_context(
            patch(
                "cli_agent_orchestrator.services.local_peer_service.reconcile_local_peer_tasks_at_startup"
            )
        )
        stack.enter_context(
            patch(
                "cli_agent_orchestrator.services.memory_reconciliation.reconcile_memory_startup",
                return_value=None,
            )
        )
        patch_main("cleanup_old_data")
        patch_main("flow_daemon", new=_fake_flow_daemon)
        patch_main("opencode_inbox_delivery_daemon", new=_fake_opencode_daemon)

        namespace = SimpleNamespace(
            get_backend=patch_main("get_backend", return_value=backend),
            herdr_cls=patch_main("HerdrInboxService"),
            set_svc=patch_main("set_herdr_inbox_service"),
            load=stack.enter_context(patch.object(PluginRegistry, "load", new_callable=AsyncMock)),
            teardown=stack.enter_context(
                patch.object(PluginRegistry, "teardown", new_callable=AsyncMock)
            ),
        )
        yield namespace


class TestLifespanInboxWiring:
    """Startup + shutdown wiring for the herdr inbox watcher."""

    @pytest.mark.asyncio
    async def test_herdr_backend_starts_service_and_sets_registry(self) -> None:
        """Herdr backend builds the service, registers it, and schedules start()."""
        # Arrange
        tasks: list = []
        backend = MagicMock(spec=HerdrBackend)
        backend.herdr_session = "cao"

        # Act
        with _patched_lifespan(backend, tasks) as mocks:
            async with lifespan(app):
                # Assert (startup)
                mocks.herdr_cls.assert_called_once_with(
                    herdr_session="cao",
                    delivery_callback=ANY,
                )
                # The delivery callback must be a callable closure.
                callback = mocks.herdr_cls.call_args.kwargs["delivery_callback"]
                assert callable(callback)

                svc = mocks.herdr_cls.return_value
                mocks.set_svc.assert_called_once_with(svc)

                # svc.start() was scheduled as a task.
                svc.start.assert_called_once()
                assert _find_task(tasks, svc.start.return_value) is not None

    @pytest.mark.asyncio
    async def test_tmux_backend_skips_herdr_service(self) -> None:
        """Non-herdr backend starts no extra herdr watcher.

        The tmux path relies on the EventBus consumers started unconditionally
        earlier in the lifespan; no HerdrInboxService is constructed.
        """
        # Arrange
        tasks: list = []
        backend = MagicMock()  # not a HerdrBackend instance

        # Act
        with _patched_lifespan(backend, tasks) as mocks:
            async with lifespan(app):
                # Assert (startup): the herdr path must not run.
                mocks.herdr_cls.assert_not_called()
                mocks.set_svc.assert_not_called()

    @pytest.mark.asyncio
    async def test_herdr_shutdown_cancels_task_and_clears_registry(self) -> None:
        """On exit the herdr path cancels its task and clears the registry."""
        # Arrange
        tasks: list = []
        backend = MagicMock(spec=HerdrBackend)
        backend.herdr_session = "cao"

        # Act
        with _patched_lifespan(backend, tasks) as mocks:
            async with lifespan(app):
                svc = mocks.herdr_cls.return_value
            # Assert (shutdown — after context exit)
            herdr_task = _find_task(tasks, svc.start.return_value)
            assert herdr_task is not None
            herdr_task.cancel.assert_called_once()
            # Registry is cleared back to None on shutdown.
            mocks.set_svc.assert_any_call(None)

    @pytest.mark.asyncio
    async def test_tmux_shutdown_has_no_herdr_task(self) -> None:
        """On exit the tmux path has no herdr task to cancel."""
        # Arrange
        tasks: list = []
        backend = MagicMock()  # not a HerdrBackend instance

        # Act
        with _patched_lifespan(backend, tasks) as mocks:
            async with lifespan(app):
                pass
            # Assert (shutdown — after context exit): no herdr task was created.
            mocks.herdr_cls.assert_not_called()
            assert _find_task(tasks, mocks.herdr_cls.return_value.start.return_value) is None


@pytest.mark.asyncio
@pytest.mark.offline_database
async def test_lifespan_drains_inbox_delivery_thread_before_stopping_fifo(monkeypatch) -> None:
    """A cancelled InboxService awaiter must drain before FIFO shutdown.

    ``asyncio.to_thread`` cancellation stops only the coroutine awaiting the
    worker; the real worker keeps running. Hold that worker at an Event barrier,
    exit the production lifespan, and record whether FIFO shutdown starts
    before the worker is released. The bounded release path also lets a fixed
    shutdown wait for the worker without hanging this regression test.
    """
    with _patched_real_inbox_lifespan(monkeypatch) as (state, mocks):
        release_task = None
        try:
            async with lifespan(app):
                await _start_blocked_inbox_delivery(state)
                release_task = asyncio.create_task(_release_after_fifo_attempt(state))

            assert state.fifo_shutdown_worker_state == [True], (
                "FIFO shutdown ran before the cancelled InboxService delivery worker drained; "
                f"worker_finished_at_fifo_stop={state.fifo_shutdown_worker_state}"
            )
            mocks.teardown.assert_awaited_once()
        finally:
            state.release_worker.set()
            if release_task is not None:
                if not release_task.done():
                    release_task.cancel()
                await asyncio.gather(release_task, return_exceptions=True)
            if state.worker_started.is_set():
                assert await asyncio.to_thread(
                    state.worker_finished.wait, state.worker_timeout
                ), "inbox worker thread did not stop after its barrier was released"
            state.service.start_accepting()


@pytest.mark.asyncio
@pytest.mark.offline_database
async def test_lifespan_refuses_fifo_teardown_when_inbox_drain_times_out(monkeypatch) -> None:
    """An unresolved worker keeps shared FIFO/plugin resources open on timeout."""
    from cli_agent_orchestrator.services.inbox_service import InboxServiceDrainTimeout

    with _patched_real_inbox_lifespan(
        monkeypatch,
        timeout_override=0.05,
    ) as (state, mocks):
        try:
            with pytest.raises(InboxServiceDrainTimeout) as raised:
                async with lifespan(app):
                    await _start_blocked_inbox_delivery(state)

            assert raised.value.active_operations == 1
            assert not state.worker_finished.is_set()
            assert state.fifo_shutdown_worker_state == []
            mocks.teardown.assert_not_awaited()
        finally:
            state.release_worker.set()
            if state.worker_started.is_set():
                assert await asyncio.to_thread(
                    state.worker_finished.wait, state.worker_timeout
                ), "inbox worker thread did not stop after its barrier was released"
            state.service.start_accepting()


@contextmanager
def _patched_real_inbox_lifespan(monkeypatch, *, timeout_override: float | None = None):
    """Patch unrelated API startup work while retaining real lifecycle tasks."""
    import importlib
    import threading

    from cli_agent_orchestrator.api import main as main_module
    from cli_agent_orchestrator.backends import work_registry
    from cli_agent_orchestrator.clients import work_repository as work_repository_module
    from cli_agent_orchestrator.clients.postgres import DatabaseTarget
    from cli_agent_orchestrator.services import log_writer as log_writer_module
    from cli_agent_orchestrator.services import status_monitor as status_monitor_module
    from cli_agent_orchestrator.services import work_launch_mode
    from cli_agent_orchestrator.services.event_bus import EventBus

    inbox_module = importlib.import_module("cli_agent_orchestrator.services.inbox_service")
    late_turn_observer = importlib.import_module(
        "cli_agent_orchestrator.services.late_turn_observer"
    )
    observation_recovery = importlib.import_module(
        "cli_agent_orchestrator.services.terminal_observation_recovery"
    )
    runtime_channel_server = importlib.import_module(
        "cli_agent_orchestrator.runtime_channel.server"
    )
    interactions_service = importlib.import_module(
        "cli_agent_orchestrator.services.interactions.service"
    )
    settings_service = importlib.import_module("cli_agent_orchestrator.services.settings_service")

    worker_started = threading.Event()
    release_worker = threading.Event()
    worker_finished = threading.Event()
    inbox_subscribed = asyncio.Event()
    fifo_shutdown_started = threading.Event()
    fifo_shutdown_worker_state: list[bool] = []
    worker_timeout = 5.0
    event_bus = EventBus()
    real_subscribe = event_bus.subscribe_sink

    def _tracked_subscribe(pattern: str, queue):
        real_subscribe(pattern, queue)
        if pattern == "terminal.*.status":
            inbox_subscribed.set()
        return queue

    event_bus.subscribe_sink = _tracked_subscribe
    monkeypatch.setattr(main_module, "bus", event_bus)
    monkeypatch.setattr(inbox_module, "bus", event_bus)

    def _blocked_delivery(terminal_id: str, num_messages: int, registry) -> None:
        del terminal_id, num_messages, registry
        worker_started.set()
        release_worker.wait(worker_timeout)
        worker_finished.set()

    monkeypatch.setattr(inbox_module.inbox_service, "_deliver_pending_locked", _blocked_delivery)

    def _stop_all_readers() -> None:
        fifo_shutdown_worker_state.append(worker_finished.is_set())
        fifo_shutdown_started.set()

    monkeypatch.setattr(main_module.fifo_manager, "stop_all_readers", _stop_all_readers)
    monkeypatch.setattr(main_module.fifo_manager, "stop_watchdog", lambda: None)

    async def _wait_forever(*_args, **_kwargs) -> None:
        await asyncio.Future()

    async def _shutdown_runtime_channel() -> None:
        return None

    monkeypatch.setattr(status_monitor_module.status_monitor, "run", _wait_forever)
    monkeypatch.setattr(log_writer_module.log_writer, "run", _wait_forever)
    monkeypatch.setattr(main_module, "cleanup_expired_memories", _wait_forever)
    monkeypatch.setattr(main_module, "inbox_reconciliation_daemon", _wait_forever)
    monkeypatch.setattr(late_turn_observer, "run", _wait_forever)
    monkeypatch.setattr(observation_recovery, "run", _wait_forever)
    monkeypatch.setattr(main_module, "_recover_t097_proxy_effects_at_startup", lambda: 0)
    monkeypatch.setattr(main_module, "_seed_default_skills_at_startup", lambda: None)
    monkeypatch.setattr(main_module, "_sweep_workflow_runs_at_startup", lambda: None)
    monkeypatch.setattr(runtime_channel_server, "shutdown", _shutdown_runtime_channel)
    monkeypatch.setattr(
        main_module.terminal_service,
        "recover_interrupted_deferred_init_external_owners",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(work_launch_mode, "managed_launch_required", lambda: False)
    monkeypatch.setattr(work_registry, "local_work_backends_for", lambda _repository: {})
    monkeypatch.setattr(DatabaseTarget, "from_settings", classmethod(lambda cls, *a, **k: object()))
    monkeypatch.setattr(work_repository_module, "WorkRepository", lambda _target: object())
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.work_launch_gateway.build_durable_launch_gateway",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(main_module, "_initialize_managed_workflow_runtime", _no_op_async)
    monkeypatch.setattr(
        interactions_service,
        "get_default_interaction_service",
        lambda: SimpleNamespace(initialize=lambda: None),
    )
    monkeypatch.setattr(main_module, "get_backend", lambda: object())
    monkeypatch.setattr(main_module, "shutdown_telemetry", lambda: None)
    monkeypatch.setattr(main_module, "init_telemetry", lambda *_args, **_kwargs: None)

    if timeout_override is not None:
        monkeypatch.setattr(
            settings_service,
            "get_server_settings",
            lambda: {
                "event_bus_max_queue_size": 1024,
                "provider_init_timeout": timeout_override,
                "mcp_request_timeout": timeout_override,
            },
        )

    prior_app_state = app.state._state.copy()
    app.state.workflow_continuation_driver = None
    backend = MagicMock()
    with _patched_lifespan(backend, None) as mocks:
        try:
            yield (
                SimpleNamespace(
                    service=inbox_module.inbox_service,
                    event_bus=event_bus,
                    inbox_subscribed=inbox_subscribed,
                    worker_started=worker_started,
                    release_worker=release_worker,
                    worker_finished=worker_finished,
                    fifo_shutdown_started=fifo_shutdown_started,
                    fifo_shutdown_worker_state=fifo_shutdown_worker_state,
                    worker_timeout=worker_timeout,
                ),
                mocks,
            )
        finally:
            app.state._state.clear()
            app.state._state.update(prior_app_state)


async def _start_blocked_inbox_delivery(state: SimpleNamespace) -> None:
    await asyncio.wait_for(state.inbox_subscribed.wait(), timeout=state.worker_timeout)
    state.event_bus.publish(
        "terminal.shutdown-drain.status",
        {"status": "idle"},
    )
    assert await asyncio.to_thread(state.worker_started.wait, state.worker_timeout)


async def _release_after_fifo_attempt(state: SimpleNamespace) -> None:
    try:
        await asyncio.to_thread(
            state.fifo_shutdown_started.wait,
            1.0,
        )
    finally:
        # A correct drain waits for this worker before attempting FIFO shutdown.
        # Release it after the bounded event wait so lifespan shutdown can finish.
        state.release_worker.set()


async def _no_op_async(*_args, **_kwargs) -> None:
    return None
