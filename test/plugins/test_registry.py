"""Tests for plugin registry discovery, dispatch, and lifecycle behavior."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest

from cli_agent_orchestrator.plugins import CaoPlugin, PluginRegistry, hook
from cli_agent_orchestrator.plugins.events import PostSendMessageEvent


@dataclass
class FakeEntryPoint:
    """Simple synthetic entry point for registry tests."""

    name: str
    loaded: object

    def load(self) -> object:
        """Return the configured object for this fake entry point."""

        return self.loaded


def make_entry_point(name: str, loaded: object) -> FakeEntryPoint:
    """Construct a fake entry point for a test plugin class or object."""

    return FakeEntryPoint(name=name, loaded=loaded)


class TestPluginRegistryLoad:
    """Tests for plugin discovery and registration."""

    @pytest.mark.asyncio
    async def test_load_with_no_entry_points_emits_info_and_keeps_dispatch_empty(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No registered plugins should leave the registry empty and log INFO."""

        registry = PluginRegistry()

        with patch("importlib.metadata.entry_points", return_value=[]):
            with caplog.at_level(logging.INFO, logger="cli_agent_orchestrator.plugins.registry"):
                await registry.load()

        assert registry._plugins == []
        assert registry._dispatch == {}
        assert "No CAO plugins registered" in caplog.text

    @pytest.mark.asyncio
    async def test_load_single_plugin_with_one_hook_dispatches_matching_event(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A single registered hook should receive matching dispatched events."""

        received: list[str] = []

        class SingleHookPlugin(CaoPlugin):
            @hook("post_send_message")
            async def on_message(self, event: PostSendMessageEvent) -> None:
                received.append(event.message)

        registry = PluginRegistry()

        with patch(
            "importlib.metadata.entry_points",
            return_value=[make_entry_point("single-hook", SingleHookPlugin)],
        ):
            with caplog.at_level(logging.INFO, logger="cli_agent_orchestrator.plugins.registry"):
                await registry.load()

        await registry.dispatch("post_send_message", PostSendMessageEvent(message="hello"))

        assert received == ["hello"]
        assert len(registry._plugins) == 1
        assert len(registry._dispatch["post_send_message"]) == 1
        assert "Loaded CAO plugin: single-hook" in caplog.text

    @pytest.mark.asyncio
    async def test_load_single_plugin_with_two_hooks_for_same_event_invokes_both(self) -> None:
        """Two hooks on the same plugin should both be registered and called."""

        received: list[str] = []

        class DoubleHookPlugin(CaoPlugin):
            @hook("post_send_message")
            async def first(self, event: PostSendMessageEvent) -> None:
                received.append(f"first:{event.message}")

            @hook("post_send_message")
            async def second(self, event: PostSendMessageEvent) -> None:
                received.append(f"second:{event.message}")

        registry = PluginRegistry()

        with patch(
            "importlib.metadata.entry_points",
            return_value=[make_entry_point("double-hook", DoubleHookPlugin)],
        ):
            await registry.load()

        await registry.dispatch("post_send_message", PostSendMessageEvent(message="hello"))

        assert set(received) == {"first:hello", "second:hello"}
        assert len(received) == 2
        assert len(registry._dispatch["post_send_message"]) == 2

    @pytest.mark.asyncio
    async def test_load_multiple_plugins_for_same_event_invokes_all(self) -> None:
        """Hooks from multiple plugins should all receive the event."""

        received: list[str] = []

        class FirstPlugin(CaoPlugin):
            @hook("post_send_message")
            async def on_message(self, event: PostSendMessageEvent) -> None:
                received.append(f"first:{event.message}")

        class SecondPlugin(CaoPlugin):
            @hook("post_send_message")
            async def on_message(self, event: PostSendMessageEvent) -> None:
                received.append(f"second:{event.message}")

        registry = PluginRegistry()

        with patch(
            "importlib.metadata.entry_points",
            return_value=[
                make_entry_point("first", FirstPlugin),
                make_entry_point("second", SecondPlugin),
            ],
        ):
            await registry.load()

        await registry.dispatch("post_send_message", PostSendMessageEvent(message="hello"))

        assert set(received) == {"first:hello", "second:hello"}
        assert len(registry._plugins) == 2
        assert len(registry._dispatch["post_send_message"]) == 2

    @pytest.mark.asyncio
    async def test_load_skips_plugin_when_setup_raises_and_loads_remaining(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A setup failure should log a warning and not block later plugins."""

        received: list[str] = []
        lifecycle: list[str] = []

        class FailingSetupPlugin(CaoPlugin):
            async def setup(self) -> None:
                lifecycle.append("acquired")
                raise RuntimeError("setup failed")

            async def teardown(self) -> None:
                lifecycle.append("released")

            @hook("post_send_message")
            async def on_message(self, event: PostSendMessageEvent) -> None:
                received.append(f"failing:{event.message}")

        class HealthyPlugin(CaoPlugin):
            @hook("post_send_message")
            async def on_message(self, event: PostSendMessageEvent) -> None:
                received.append(f"healthy:{event.message}")

        registry = PluginRegistry()

        with patch(
            "importlib.metadata.entry_points",
            return_value=[
                make_entry_point("failing-setup", FailingSetupPlugin),
                make_entry_point("healthy", HealthyPlugin),
            ],
        ):
            with caplog.at_level(logging.WARNING, logger="cli_agent_orchestrator.plugins.registry"):
                await registry.load()

        await registry.dispatch("post_send_message", PostSendMessageEvent(message="hello"))

        assert lifecycle == ["acquired", "released"]
        assert received == ["healthy:hello"]
        assert len(registry._plugins) == 1
        assert "Failed to load plugin 'failing-setup'" in caplog.text
        assert caplog.records[-1].exc_info is not None

    @pytest.mark.asyncio
    async def test_cancelled_setup_is_cleaned_before_cancellation_propagates(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A partial setup is torn down before cancellation escapes, even if cleanup fails."""

        setup_started = asyncio.Event()
        lifecycle: list[str] = []

        class BlockingSetupPlugin(CaoPlugin):
            async def setup(self) -> None:
                lifecycle.append("acquired")
                setup_started.set()
                await asyncio.Future()

            async def teardown(self) -> None:
                lifecycle.append("released")
                raise RuntimeError("cleanup failed")

        registry = PluginRegistry()

        with (
            patch(
                "importlib.metadata.entry_points",
                return_value=[make_entry_point("blocking-setup", BlockingSetupPlugin)],
            ),
            caplog.at_level(logging.WARNING, logger="cli_agent_orchestrator.plugins.registry"),
        ):
            load_task = asyncio.create_task(registry.load())
            await setup_started.wait()
            load_task.cancel()

            with pytest.raises(asyncio.CancelledError):
                await load_task

        assert lifecycle == ["acquired", "released"]
        assert registry._plugins == []
        assert registry._dispatch == {}
        assert "cleanup failed" in caplog.text

    @pytest.mark.asyncio
    async def test_setup_cancellation_drains_cleanup_after_repeated_caller_cancellation(
        self,
    ) -> None:
        """Repeated cancellation during failed-setup cleanup does not abandon the plugin."""

        setup_started = asyncio.Event()
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()
        lifecycle: list[str] = []

        class BlockingSetupPlugin(CaoPlugin):
            async def setup(self) -> None:
                lifecycle.append("setup-started")
                setup_started.set()
                await asyncio.Future()

            async def teardown(self) -> None:
                lifecycle.append("cleanup-started")
                cleanup_started.set()
                await release_cleanup.wait()
                lifecycle.append("cleanup-finished")

        registry = PluginRegistry()
        cancellation_messages: list[str] = []

        async def load_and_record_cancellation() -> None:
            try:
                await registry.load()
            except asyncio.CancelledError as error:
                # Python 3.10 drops this message at the Task-to-awaiter boundary.
                cancellation_messages.append(str(error))
                raise

        with patch(
            "importlib.metadata.entry_points",
            return_value=[make_entry_point("blocking-setup", BlockingSetupPlugin)],
        ):
            load_task = asyncio.create_task(load_and_record_cancellation())
            await setup_started.wait()
            load_task.cancel("setup cancellation")
            await cleanup_started.wait()

            load_task.cancel("first cleanup cancellation")
            await asyncio.sleep(0)
            load_task.cancel("second cleanup cancellation")
            release_cleanup.set()

            with pytest.raises(asyncio.CancelledError):
                await load_task

        assert load_task.cancelled()
        assert cancellation_messages == ["setup cancellation"]
        assert lifecycle == ["setup-started", "cleanup-started", "cleanup-finished"]
        assert registry._plugins == []
        assert registry._dispatch == {}

    @pytest.mark.asyncio
    async def test_load_skips_non_plugin_entry_point_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A non-CaoPlugin entry point should be skipped and logged."""

        class NotAPlugin:
            pass

        registry = PluginRegistry()

        with patch(
            "importlib.metadata.entry_points",
            return_value=[make_entry_point("not-a-plugin", NotAPlugin)],
        ):
            with caplog.at_level(logging.WARNING, logger="cli_agent_orchestrator.plugins.registry"):
                await registry.load()

        assert registry._plugins == []
        assert registry._dispatch == {}
        assert "not a CaoPlugin subclass, skipping" in caplog.text


class TestPluginRegistryDispatch:
    """Tests for dispatch-time behavior and error isolation."""

    @pytest.mark.asyncio
    async def test_dispatch_logs_warning_and_continues_when_hook_raises(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A failing hook should not prevent other matching hooks from running."""

        received: list[str] = []

        class FailingHookPlugin(CaoPlugin):
            @hook("post_send_message")
            async def broken(self, event: PostSendMessageEvent) -> None:
                received.append("broken")
                raise RuntimeError("dispatch failed")

            @hook("post_send_message")
            async def healthy(self, event: PostSendMessageEvent) -> None:
                received.append("healthy")

        registry = PluginRegistry()

        with patch(
            "importlib.metadata.entry_points",
            return_value=[make_entry_point("failing-hook", FailingHookPlugin)],
        ):
            await registry.load()

        with caplog.at_level(logging.WARNING, logger="cli_agent_orchestrator.plugins.registry"):
            await registry.dispatch("post_send_message", PostSendMessageEvent(message="hello"))

        assert set(received) == {"broken", "healthy"}
        assert "raised an error for event 'post_send_message'" in caplog.text
        assert caplog.records[-1].exc_info is not None

    @pytest.mark.asyncio
    async def test_dispatch_with_no_registered_handlers_is_no_op(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Dispatching an unhandled event should do nothing and not error."""

        registry = PluginRegistry()

        with caplog.at_level(logging.WARNING, logger="cli_agent_orchestrator.plugins.registry"):
            await registry.dispatch("post_send_message", PostSendMessageEvent(message="hello"))

        assert registry._dispatch == {}
        assert caplog.records == []


class TestPluginRegistryTeardown:
    """Tests for plugin teardown behavior."""

    @pytest.mark.asyncio
    async def test_teardown_logs_warning_and_continues_after_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A teardown failure should not prevent later plugins from tearing down."""

        torn_down: list[str] = []

        class FailingTeardownPlugin(CaoPlugin):
            async def teardown(self) -> None:
                torn_down.append("failing")
                raise RuntimeError("teardown failed")

        class HealthyTeardownPlugin(CaoPlugin):
            async def teardown(self) -> None:
                torn_down.append("healthy")

        registry = PluginRegistry()

        with patch(
            "importlib.metadata.entry_points",
            return_value=[
                make_entry_point("failing", FailingTeardownPlugin),
                make_entry_point("healthy", HealthyTeardownPlugin),
            ],
        ):
            await registry.load()

        with caplog.at_level(logging.WARNING, logger="cli_agent_orchestrator.plugins.registry"):
            await registry.teardown()

        assert set(torn_down) == {"failing", "healthy"}
        assert "Plugin teardown failed for FailingTeardownPlugin" in caplog.text
        assert caplog.records[-1].exc_info is not None

    @pytest.mark.asyncio
    async def test_teardown_isolates_plugin_raised_cancelled_error_with_stale_task_cancellation(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A plugin's direct CancelledError is isolated after an earlier task cancellation was caught."""

        torn_down: list[str] = []

        class PluginRaisingCancelledError(CaoPlugin):
            async def teardown(self) -> None:
                torn_down.append("self-cancelling")
                raise asyncio.CancelledError("plugin cancelled itself")

        class HealthyPlugin(CaoPlugin):
            async def teardown(self) -> None:
                torn_down.append("healthy")

        registry = PluginRegistry()

        with patch(
            "importlib.metadata.entry_points",
            return_value=[
                make_entry_point("raises-cancelled-error", PluginRaisingCancelledError),
                make_entry_point("healthy", HealthyPlugin),
            ],
        ):
            await registry.load()

        async def teardown_after_catching_cancellation() -> None:
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
            await registry.teardown()

        teardown_task = asyncio.create_task(teardown_after_catching_cancellation())
        with caplog.at_level(logging.WARNING, logger="cli_agent_orchestrator.plugins.registry"):
            await teardown_task

        assert torn_down == ["self-cancelling", "healthy"]
        assert "Plugin teardown failed for PluginRaisingCancelledError" in caplog.text
        assert caplog.records[-1].exc_info is not None

    @pytest.mark.asyncio
    async def test_teardown_finishes_later_plugins_before_propagating_caller_cancellation(
        self,
    ) -> None:
        """Cancellation during one plugin teardown is re-raised after later plugins run."""

        teardown_started = asyncio.Event()
        torn_down: list[str] = []

        class BlockingPlugin(CaoPlugin):
            async def teardown(self) -> None:
                torn_down.append("blocking")
                teardown_started.set()
                await asyncio.Future()

        class HealthyPlugin(CaoPlugin):
            async def teardown(self) -> None:
                torn_down.append("healthy")

        registry = PluginRegistry()

        with patch(
            "importlib.metadata.entry_points",
            return_value=[
                make_entry_point("blocking", BlockingPlugin),
                make_entry_point("healthy", HealthyPlugin),
            ],
        ):
            await registry.load()

        teardown_task = asyncio.create_task(registry.teardown())
        await teardown_started.wait()
        teardown_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await teardown_task

        assert torn_down == ["blocking", "healthy"]

    @pytest.mark.asyncio
    async def test_teardown_preserves_caller_cancellation_when_plugin_fails_during_drain(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A cleanup error after caller cancellation does not mask it or stop later plugins."""

        teardown_started = asyncio.Event()
        torn_down: list[str] = []

        class FailingCancellationCleanupPlugin(CaoPlugin):
            async def teardown(self) -> None:
                torn_down.append("failing-cancellation-cleanup")
                teardown_started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    raise RuntimeError("cleanup failed after caller cancellation")

        class HealthyPlugin(CaoPlugin):
            async def teardown(self) -> None:
                torn_down.append("healthy")

        registry = PluginRegistry()

        with patch(
            "importlib.metadata.entry_points",
            return_value=[
                make_entry_point("failing-cancellation-cleanup", FailingCancellationCleanupPlugin),
                make_entry_point("healthy", HealthyPlugin),
            ],
        ):
            await registry.load()

        teardown_task = asyncio.create_task(registry.teardown())
        await teardown_started.wait()
        teardown_task.cancel()

        with caplog.at_level(logging.WARNING, logger="cli_agent_orchestrator.plugins.registry"):
            with pytest.raises(asyncio.CancelledError):
                await teardown_task

        assert torn_down == ["failing-cancellation-cleanup", "healthy"]
        assert "Plugin teardown failed for FailingCancellationCleanupPlugin" in caplog.text

    @pytest.mark.asyncio
    async def test_teardown_isolates_plugin_self_cancellation_from_registry_caller(self) -> None:
        """A plugin canceling its own teardown task does not cancel the registry caller."""

        torn_down: list[str] = []

        class CancellationRequestingPlugin(CaoPlugin):
            async def teardown(self) -> None:
                torn_down.append("cancellation-requesting")
                task = asyncio.current_task()
                assert task is not None
                task.cancel()
                await asyncio.sleep(0)

        class HealthyPlugin(CaoPlugin):
            async def teardown(self) -> None:
                torn_down.append("healthy")

        registry = PluginRegistry()

        with patch(
            "importlib.metadata.entry_points",
            return_value=[
                make_entry_point("cancellation-requesting", CancellationRequestingPlugin),
                make_entry_point("healthy", HealthyPlugin),
            ],
        ):
            await registry.load()

        await registry.teardown()

        assert torn_down == ["cancellation-requesting", "healthy"]

    @pytest.mark.asyncio
    async def test_teardown_propagates_caller_cancellation_swallowed_by_plugin(self) -> None:
        """A plugin cannot suppress caller cancellation by catching it and returning."""

        teardown_started = asyncio.Event()
        torn_down: list[str] = []

        class CancellationSwallowingPlugin(CaoPlugin):
            async def teardown(self) -> None:
                torn_down.append("cancellation-swallowing")
                teardown_started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    return

        class HealthyPlugin(CaoPlugin):
            async def teardown(self) -> None:
                torn_down.append("healthy")

        registry = PluginRegistry()

        with patch(
            "importlib.metadata.entry_points",
            return_value=[
                make_entry_point("cancellation-swallowing", CancellationSwallowingPlugin),
                make_entry_point("healthy", HealthyPlugin),
            ],
        ):
            await registry.load()

        teardown_task = asyncio.create_task(registry.teardown())
        await teardown_started.wait()
        teardown_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await teardown_task

        assert torn_down == ["cancellation-swallowing", "healthy"]

    @pytest.mark.asyncio
    async def test_teardown_drains_plugin_after_repeated_caller_cancellation(self) -> None:
        """Repeated caller cancellation still drains the plugin and reaches later plugins."""

        teardown_started = asyncio.Event()
        first_cancellation_caught = asyncio.Event()
        second_cancellation_caught = asyncio.Event()
        release_after_cancellation = asyncio.Event()
        torn_down: list[str] = []

        class CancellationBlockingPlugin(CaoPlugin):
            async def teardown(self) -> None:
                torn_down.append("cancellation-blocking")
                teardown_started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    first_cancellation_caught.set()
                    try:
                        await release_after_cancellation.wait()
                    except asyncio.CancelledError:
                        second_cancellation_caught.set()

        class HealthyPlugin(CaoPlugin):
            async def teardown(self) -> None:
                torn_down.append("healthy")

        registry = PluginRegistry()

        with patch(
            "importlib.metadata.entry_points",
            return_value=[
                make_entry_point("cancellation-blocking", CancellationBlockingPlugin),
                make_entry_point("healthy", HealthyPlugin),
            ],
        ):
            await registry.load()

        teardown_task = asyncio.create_task(registry.teardown())
        await teardown_started.wait()
        teardown_task.cancel()
        await first_cancellation_caught.wait()
        teardown_task.cancel()
        try:
            await asyncio.wait_for(second_cancellation_caught.wait(), timeout=2)
        except asyncio.TimeoutError:
            pass
        finally:
            release_after_cancellation.set()

        with pytest.raises(asyncio.CancelledError):
            await teardown_task

        assert second_cancellation_caught.is_set()
        assert torn_down == ["cancellation-blocking", "healthy"]

    @pytest.mark.asyncio
    async def test_teardown_starts_child_created_just_before_caller_cancellation(self) -> None:
        """Cancellation in the create-task/first-step gap still drains every plugin."""

        torn_down: list[str] = []
        child_tasks: list[asyncio.Task[Any]] = []

        class FirstPlugin(CaoPlugin):
            async def teardown(self) -> None:
                torn_down.append("first-started")
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    pass
                torn_down.append("first-finished")

        class LaterPlugin(CaoPlugin):
            async def teardown(self) -> None:
                torn_down.append("later")

        registry = PluginRegistry()

        with patch(
            "importlib.metadata.entry_points",
            return_value=[
                make_entry_point("first", FirstPlugin),
                make_entry_point("later", LaterPlugin),
            ],
        ):
            await registry.load()

        cancellation_messages: list[str] = []

        async def teardown_and_record_cancellation() -> None:
            try:
                await registry.teardown()
            except asyncio.CancelledError as error:
                # Inspect the registry's message before Python 3.10 discards it.
                cancellation_messages.append(str(error))
                raise

        teardown_task = asyncio.create_task(teardown_and_record_cancellation())
        create_task = asyncio.create_task

        def record_child_task(coroutine: Any, *, name: str | None = None) -> asyncio.Task[Any]:
            child = create_task(coroutine, name=name)
            child_tasks.append(child)
            return child

        real_shield = asyncio.shield
        cancel_first_wait = True

        async def shield_with_caller_cancellation(awaitable: Any) -> Any:
            nonlocal cancel_first_wait
            if cancel_first_wait:
                cancel_first_wait = False
                raise asyncio.CancelledError("cancel after child creation")
            return await real_shield(awaitable)

        with (
            patch(
                "cli_agent_orchestrator.plugins.registry.asyncio.create_task",
                side_effect=record_child_task,
            ),
            patch(
                "cli_agent_orchestrator.plugins.registry.asyncio.shield",
                side_effect=shield_with_caller_cancellation,
            ),
        ):
            with pytest.raises(asyncio.CancelledError):
                await teardown_task

        assert teardown_task.cancelled()
        assert cancellation_messages == ["cancel after child creation"]
        assert torn_down == ["first-started", "first-finished", "later"]
        assert child_tasks and all(task.done() for task in child_tasks)
