"""Plugin discovery, registration, dispatch, and lifecycle management."""

import asyncio
import importlib.metadata
import inspect
import logging
from typing import Any

from cli_agent_orchestrator.plugins.base import _HOOK_EVENT_ATTR, CaoPlugin, McpServerStartupError
from cli_agent_orchestrator.plugins.events import CaoEvent

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "cao.plugins"


class PluginRegistry:
    """Registry for discovered CAO plugins and their hook handlers."""

    def __init__(self) -> None:
        """Initialize an empty plugin registry."""

        self._plugins: list[CaoPlugin] = []
        self._dispatch: dict[str, list[Any]] = {}

    async def load(self) -> None:
        """Discover, instantiate, and set up all registered CAO plugins."""

        entry_points = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
        for entry_point in entry_points:
            try:
                plugin_class = entry_point.load()
                if not (isinstance(plugin_class, type) and issubclass(plugin_class, CaoPlugin)):
                    logger.warning(
                        "Plugin entry point '%s' is not a CaoPlugin subclass, skipping",
                        entry_point.name,
                    )
                    continue

                plugin = plugin_class()
                try:
                    await plugin.setup()
                except BaseException:
                    cleanup_task = asyncio.create_task(plugin.teardown())
                    while not cleanup_task.done():
                        try:
                            await asyncio.shield(cleanup_task)
                        except asyncio.CancelledError:
                            continue
                        except BaseException:
                            break

                    try:
                        cleanup_task.result()
                    except BaseException:
                        logger.warning(
                            "Plugin setup cleanup failed for %s",
                            type(plugin).__name__,
                            exc_info=True,
                        )
                    raise
                self._register(plugin)
                logger.info("Loaded CAO plugin: %s", entry_point.name)
            except Exception:
                logger.warning(
                    "Failed to load plugin '%s'",
                    entry_point.name,
                    exc_info=True,
                )

        if not self._plugins:
            logger.info("No CAO plugins registered (cao.plugins entry point group is empty)")

    def _register(self, plugin: CaoPlugin) -> None:
        """Register a plugin instance and index any decorated hook methods."""

        self._plugins.append(plugin)
        for _, method in inspect.getmembers(plugin, predicate=inspect.ismethod):
            event_type = getattr(method, _HOOK_EVENT_ATTR, None)
            if event_type is not None:
                self._dispatch.setdefault(event_type, []).append(method)

    async def dispatch(self, event_type: str, event: CaoEvent) -> None:
        """Dispatch an event to all matching plugin hook handlers."""

        for handler in self._dispatch.get(event_type, []):
            try:
                await handler(event)
            except Exception:
                logger.warning(
                    "Hook '%s' raised an error for event '%s'",
                    handler.__qualname__,
                    event_type,
                    exc_info=True,
                )

    async def teardown(self) -> None:
        """Call teardown() on every loaded plugin, continuing after failures."""

        caller_cancellation: asyncio.CancelledError | None = None

        for plugin in self._plugins:
            teardown_started: asyncio.Future[None] = asyncio.get_running_loop().create_future()

            async def run_teardown() -> asyncio.CancelledError | None:
                teardown_started.set_result(None)
                try:
                    await plugin.teardown()
                except asyncio.CancelledError as error:
                    return error
                return None

            teardown_task = asyncio.create_task(run_teardown())
            try:
                plugin_cancellation = await asyncio.shield(teardown_task)
            except asyncio.CancelledError as error:
                if caller_cancellation is None:
                    caller_cancellation = error

                pending_cancellations = 1
                while not teardown_started.done() and not teardown_task.done():
                    try:
                        await asyncio.shield(teardown_started)
                    except asyncio.CancelledError:
                        pending_cancellations += 1

                for _ in range(pending_cancellations):
                    teardown_task.cancel()

                while not teardown_task.done():
                    try:
                        await asyncio.shield(teardown_task)
                    except asyncio.CancelledError:
                        teardown_task.cancel()
                        continue
                    except Exception:
                        break

                try:
                    plugin_cancellation = teardown_task.result()
                except asyncio.CancelledError as plugin_error:
                    plugin_cancellation = plugin_error
                except Exception:
                    logger.warning(
                        "Plugin teardown failed for %s",
                        type(plugin).__name__,
                        exc_info=True,
                    )
                    plugin_cancellation = None
                if plugin_cancellation is not None:
                    logger.warning(
                        "Plugin teardown failed for %s",
                        type(plugin).__name__,
                        exc_info=(
                            type(plugin_cancellation),
                            plugin_cancellation,
                            plugin_cancellation.__traceback__,
                        ),
                    )
                continue
            except Exception:
                logger.warning(
                    "Plugin teardown failed for %s",
                    type(plugin).__name__,
                    exc_info=True,
                )
            else:
                if plugin_cancellation is not None:
                    logger.warning(
                        "Plugin teardown failed for %s",
                        type(plugin).__name__,
                        exc_info=(
                            type(plugin_cancellation),
                            plugin_cancellation,
                            plugin_cancellation.__traceback__,
                        ),
                    )

        if caller_cancellation is not None:
            raise caller_cancellation


def register_mcp_server_surfaces(mcp: Any) -> None:
    """Let every discovered ``cao.plugins`` plugin register MCP-server surfaces.

    Discovers the entry-point group and calls ``on_mcp_server(mcp)`` on each
    plugin **synchronously** — independent of the async event-dispatch lifecycle
    (``setup``/``teardown``), which belongs to the HTTP API. Best-effort: a
    failing or non-conforming entry point is logged and skipped, so one broken
    plugin never blocks MCP server startup.

    Called once from the MCP server module at import/startup. The built-in
    ``mcp_apps`` plugin uses this hook to register the MCP Apps tools, the
    ``ui://cao/*`` resources, the topology widget, and the SEP-2133 capability —
    all default-off unless ``CAO_MCP_APPS_ENABLED`` is set.
    """

    entry_points = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    for entry_point in entry_points:
        try:
            plugin_class = entry_point.load()
            if not (isinstance(plugin_class, type) and issubclass(plugin_class, CaoPlugin)):
                continue
            plugin_class().on_mcp_server(mcp)
        except McpServerStartupError:
            raise
        except Exception:
            logger.warning(
                "Plugin '%s' on_mcp_server registration failed",
                entry_point.name,
                exc_info=True,
            )
