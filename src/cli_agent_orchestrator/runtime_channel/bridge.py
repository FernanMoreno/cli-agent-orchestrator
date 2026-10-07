"""cao-bridge: the execution-only process in an execution runtime (#745).

Runs beside the agents' tmux server, in a pod that has no cao-server. It dials
the central server's ``/runtime/channel`` (outbound only), runs each command it
receives through the local terminal service, and pushes the status the local
status monitor derives. Nothing here serves HTTP.

Configuration:

- ``CAO_BRIDGE_SERVER_URL``: e.g. ``ws://cao-server:9889/runtime/channel``
- ``CAO_BRIDGE_RUNTIME_ID``: this runtime's id (in Kubernetes, the pod name)
- ``CAO_RUNTIME_TOKEN_FILE`` or ``CAO_RUNTIME_TOKEN``: the shared channel token
- ``CAO_BRIDGE_READY_FILE`` (optional): created while the channel is up, for a
  readiness probe
"""

import asyncio
import contextlib
import logging
import os
import re
import signal
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Set, Tuple

import websockets
from websockets.asyncio.client import ClientConnection, connect

from cli_agent_orchestrator.constants import CAO_HOME_DIR, DEFAULT_PROVIDER
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.runtime_channel.protocol import (
    PROTOCOL_VERSION,
    RUNTIME_ID_PATTERN,
    Command,
    CommandType,
    Hello,
    Result,
    Status,
    decode,
    encode,
)
from cli_agent_orchestrator.runtime_channel.token import TOKEN_HEADER, runtime_token
from cli_agent_orchestrator.services.event_bus import bus

logger = logging.getLogger(__name__)

BACKOFF_INITIAL = 1.0
BACKOFF_MAX = 30.0
# A connection that stays up this long after its hello resets the backoff.
STABLE_CONNECTION = 10.0
_STATUS_TOPIC = re.compile(r"^terminal\.([^.]+)\.status$")


class ChannelRefused(Exception):
    """The server refused this runtime (token or protocol version). Retrying cannot help."""


class Bridge:
    def __init__(
        self,
        server_url: str,
        runtime_id: str,
        token: str,
        ready_file: Optional[Path] = None,
        *,
        store=None,
        incarnation_id: Optional[str] = None,
    ):
        self.server_url = server_url
        self.runtime_id = runtime_id
        self._token = token
        self._ready_file = ready_file
        self._ws: Optional[ClientConnection] = None
        self._send_lock = asyncio.Lock()
        # terminal id -> (lock, number of commands holding or awaiting it)
        self._terminal_locks: Dict[str, Tuple[asyncio.Lock, int]] = {}
        # Loop time the current connection completed its hello (see run()).
        self._helloed_at: Optional[float] = None
        # Results that could not be sent (no channel at the time), delivered
        # after the next hello: the server acts on results it no longer awaits.
        self._unsent: List[Result] = []
        # Strong references: the event loop holds only weak ones to tasks.
        self._tasks: Set["asyncio.Task[None]"] = set()
        self._stop = asyncio.Event()
        self.incarnation_id = incarnation_id or uuid.uuid4().hex
        if store is None:
            from cli_agent_orchestrator.clients.database import engine
            from cli_agent_orchestrator.runtime_channel.store import RemoteOperationStore

            store = RemoteOperationStore(engine)
        self.store = store
        self.connection_epoch: int | None = None

    def admit_epoch(self, epoch: int) -> None:
        self.store.admit_epoch(self.runtime_id, self.incarnation_id, epoch)
        self.connection_epoch = epoch

    # --- outbound ---

    async def _send(self, frame) -> None:
        ws = self._ws
        if ws is not None:
            async with self._send_lock:
                try:
                    await ws.send(encode(frame))
                    return
                except websockets.exceptions.ConnectionClosed:
                    pass
        if isinstance(frame, Result):
            self._unsent.append(frame)

    def _status_of(self, terminal_id: str) -> TerminalStatus:
        from cli_agent_orchestrator.services.status_monitor import status_monitor

        return status_monitor.get_status(terminal_id)

    async def _push_status(self, terminal_id: str, skip_unknown: bool = False) -> None:
        """Capture status and allocate its revision under the command lock.

        Terminal effects and observation revisions share one causal order; an
        older snapshot cannot receive a revision newer than its input receipt.
        The terminal lock precedes the send lock in both paths.
        """
        ws = self._ws
        if ws is None:
            return
        async with self._terminal_turn(terminal_id):
            async with self._send_lock:
                # Off the loop (it may capture the pane), still under the send lock
                # so status frames cannot be reordered.
                status = await asyncio.to_thread(self._status_of, terminal_id)
                if skip_unknown and status == TerminalStatus.UNKNOWN:
                    return
                from cli_agent_orchestrator.services import terminal_service

                projection = _jsonable(
                    await asyncio.to_thread(terminal_service.get_terminal, terminal_id)
                )
                revision = await asyncio.to_thread(
                    self.store.next_revision, terminal_id, self.incarnation_id
                )
                frame = Status(
                    terminal_id=terminal_id,
                    status=status,
                    projection=projection,
                    revision=revision,
                    runtime_incarnation_id=self.incarnation_id,
                )
                try:
                    await ws.send(encode(frame))
                except websockets.exceptions.ConnectionClosed:
                    pass

    async def forward_status(self) -> None:
        """Push every status change the local status monitor publishes."""
        queue = bus.subscribe("terminal.*.status")
        try:
            while True:
                event = await queue.get()
                match = _STATUS_TOPIC.match(event["topic"])
                if match:
                    try:
                        await self._push_status(match.group(1))
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - one bad status must not end forwarding
                        logger.warning(
                            "could not push the status of terminal %s",
                            match.group(1),
                            exc_info=True,
                        )
        finally:
            bus.unsubscribe("terminal.*.status", queue)

    # --- commands ---

    @contextlib.asynccontextmanager
    async def _terminal_turn(self, terminal_id: str) -> AsyncIterator[None]:
        """Wait for this terminal's earlier commands. The lock is dropped only
        once nothing holds or awaits it."""
        lock, users = self._terminal_locks.get(terminal_id, (asyncio.Lock(), 0))
        self._terminal_locks[terminal_id] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            lock, users = self._terminal_locks[terminal_id]
            if users == 1:
                del self._terminal_locks[terminal_id]
            else:
                self._terminal_locks[terminal_id] = (lock, users - 1)

    async def handle(self, command: Command) -> None:
        """Durable receive claim precedes every possible native effect."""
        claimed = False
        try:
            if (
                command.runtime_incarnation_id != self.incarnation_id
                or command.connection_epoch != self.connection_epoch
                or command.deadline is None
                or command.deadline <= time.time()
            ):
                raise ValueError("Remote command authority expired or changed")
            row = await asyncio.to_thread(
                self.store.prepare,
                command.op_id,
                self.runtime_id,
                self.incarnation_id,
                command.type.value,
                command.payload,
                terminal_id=command.terminal_id,
                generation=command.generation,
                deadline=command.deadline,
            )
            if row["state"] == "settled":
                await self._send(Result.model_validate(row["result"]))
                return
            claimed = await asyncio.to_thread(
                self.store.claim, command.op_id, connection_epoch=self.connection_epoch
            )
            if not claimed:
                result = Result(
                    op_id=command.op_id,
                    ok=False,
                    error="Operation requires reconciliation",
                    error_kind="outcome_unknown",
                    runtime_incarnation_id=self.incarnation_id,
                )
                await self._send(result)
                return
            # Cancellation/reset holds its own dispatch lock in the service.
            async with self._terminal_turn(command.terminal_id or command.op_id):
                if not await asyncio.to_thread(
                    self.store.effect_is_current,
                    command.op_id,
                    connection_epoch=command.connection_epoch,
                ):
                    raise ValueError("Remote effect authority changed before execution")
                from cli_agent_orchestrator.backends.registry import (
                    get_backend,
                    ordinary_remote_backend_scope,
                )
                from cli_agent_orchestrator.runtime_channel.backend import OrdinaryRemoteEffectFence

                with ordinary_remote_backend_scope(
                    OrdinaryRemoteEffectFence(
                        get_backend(), self.store, command.op_id, command.connection_epoch
                    )
                ):
                    payload = await self.execute(command)
                observed_id = command.terminal_id or payload.get("terminal", {}).get("id")
                if observed_id:
                    payload["revision"] = await asyncio.to_thread(
                        self.store.next_revision, observed_id, self.incarnation_id
                    )
            result = Result(
                op_id=command.op_id,
                ok=True,
                payload=payload,
                runtime_incarnation_id=self.incarnation_id,
            )
        except asyncio.CancelledError:
            if claimed:
                await asyncio.shield(asyncio.to_thread(self.store.mark_unknown, command.op_id))
            raise
        except Exception as exc:
            from cli_agent_orchestrator.runtime_channel.errors import encode_error

            kind, detail = encode_error(exc)
            result = Result(
                op_id=command.op_id,
                ok=False,
                error=str(exc),
                error_kind=kind,
                error_detail=detail,
                runtime_incarnation_id=self.incarnation_id,
            )
        if claimed:
            await asyncio.to_thread(
                self.store.settle,
                command.op_id,
                self.runtime_id,
                self.incarnation_id,
                result.model_dump(mode="json"),
            )
        await self._send(result)

    async def execute(self, command: Command) -> Dict[str, Any]:
        # Imported here: the provider stack is only needed once work arrives.
        from cli_agent_orchestrator.clients.database import get_terminal_metadata
        from cli_agent_orchestrator.models.inbox import OrchestrationType
        from cli_agent_orchestrator.services import terminal_service
        from cli_agent_orchestrator.utils.agent_profiles import resolve_provider

        payload = command.payload
        if command.type == CommandType.INSPECT and command.terminal_id is None:
            operation = await asyncio.to_thread(self.store.get, payload.get("op_id", ""))
            if operation is None or operation["incarnation_id"] != self.incarnation_id:
                raise ValueError("Remote operation identity not found")
            return {"operation_result": operation["result"], "state": operation["state"]}
        if command.type == CommandType.LAUNCH:
            if payload.get("managed_work"):
                from cli_agent_orchestrator.backends.base import UnsupportedWorkEnforcement

                raise UnsupportedWorkEnforcement("RemoteRuntime")
            profile = payload["agent_profile"]
            provider = payload.get("provider") or resolve_provider(profile, DEFAULT_PROVIDER)
            terminal = await terminal_service.create_terminal(
                provider=provider,
                agent_profile=profile,
                new_session=True,
                working_directory=payload.get("working_directory"),
                allowed_tools=payload.get("allowed_tools"),
                model=payload.get("model"),
                engine=payload.get("engine"),
                idempotency_key="remote:" + command.op_id,
            )
            try:
                local = await asyncio.to_thread(terminal_service.get_terminal, terminal.id)
                return {"terminal": _jsonable(local)}
            except Exception as exc:
                # The agent runs but the server would never learn of it: stop it.
                stopped = False
                try:
                    from cli_agent_orchestrator.backends.base import TerminalCleanupResult
                    from cli_agent_orchestrator.backends.registry import get_backend

                    metadata = await asyncio.to_thread(get_terminal_metadata, terminal.id)
                    exact = await asyncio.to_thread(
                        get_backend().cleanup_terminal_exact,
                        terminal.id,
                        metadata.get("tmux_session") if metadata else None,
                        metadata.get("tmux_window") if metadata else None,
                    )
                    if isinstance(exact, TerminalCleanupResult) and exact.reclaimed and metadata:
                        dismantled = await asyncio.to_thread(
                            terminal_service.dismantle_terminal_runtime,
                            terminal.id,
                            metadata,
                            kill_window=False,
                        )
                        stopped = dismantled and await asyncio.to_thread(
                            terminal_service.delete_terminal_row, terminal.id, metadata
                        )
                except Exception:  # noqa: BLE001 - reported in the error below
                    logger.exception("could not stop unreported terminal %s", terminal.id)
                detail = f"launched terminal {terminal.id} but could not report it ({exc})"
                if not stopped:
                    detail += "; it may still be running"
                raise RuntimeError(detail) from exc

        terminal_id = command.terminal_id
        if not terminal_id:
            raise ValueError(f"{command.type.value} requires a terminal_id")

        from cli_agent_orchestrator import constants
        from cli_agent_orchestrator.services.turn_recovery_service import (
            cancel_turn,
            get_turn,
            verify_turn,
        )
        from cli_agent_orchestrator.services.work_terminal import terminal_dispatch_lock

        # An authenticated transport is not authority to act on native Work.
        terminal_service.ensure_terminal_is_not_work_owned(terminal_id)
        metadata = await asyncio.to_thread(get_terminal_metadata, terminal_id)
        launch = await asyncio.to_thread(self.store.get, payload.get("_launch_op_id", ""))
        if (
            (metadata is None and command.type != CommandType.DELETE)
            or (
                metadata is not None
                and metadata.get("session_incarnation_id") != payload.get("_session_incarnation_id")
            )
            or not launch
            or launch["command_type"] != "launch"
            or launch["state"] != "settled"
            or not launch["result"]["ok"]
            or launch["result"].get("payload", {}).get("terminal", {}).get("session_incarnation_id")
            != payload.get("_session_incarnation_id")
            or launch["result"].get("payload", {}).get("terminal", {}).get("id") != terminal_id
        ):
            raise ValueError("Exact remote terminal launch/session identity changed")
        if command.type == CommandType.CANCEL:
            return {
                "turn": await cancel_turn(
                    terminal_id, command.generation or payload.get("generation", "")
                )
            }
        if command.type == CommandType.VERIFY:
            return {
                "turn": await asyncio.to_thread(
                    verify_turn, terminal_id, command.generation or payload.get("generation", "")
                )
            }
        if command.type == CommandType.INSPECT:
            return {
                "terminal": _jsonable(
                    await asyncio.to_thread(terminal_service.get_terminal, terminal_id)
                )
            }

        # Serialize native ordinary effects with Work dispatch/recovery ownership.
        def guarded(function, *args, **kwargs):
            if function.__name__ in {"send_input", "send_special_key", "exit_terminal_cli"}:
                # Sends own the same flock in their existing service decorator.
                return function(*args, **kwargs)
            with terminal_dispatch_lock(constants.DATABASE_FILE, terminal_id):
                terminal_service.ensure_terminal_is_not_work_owned(terminal_id)
                return function(*args, **kwargs)

        if command.type == CommandType.INPUT:
            provider = terminal_service.provider_manager.get_provider(terminal_id)
            if (
                payload.get("task_delivery")
                and getattr(provider, "has_pending_native_swarm", False) is True
            ):
                from cli_agent_orchestrator.models.terminal import TerminalInputBlockedError

                raise TerminalInputBlockedError("Native swarm is still running", action="reconcile")
            orchestration = payload.get("orchestration_type")
            success = await asyncio.to_thread(
                guarded,
                terminal_service.send_input,
                terminal_id,
                payload["message"],
                sender_id=payload.get("sender_id"),
                orchestration_type=OrchestrationType(orchestration) if orchestration else None,
                frozen_memory=payload.get("frozen_memory"),
                task_delivery=payload.get("task_delivery", False),
                attach_turn_receipt=payload.get("attach_turn_receipt", True),
            )
            return {
                "success": success,
                "turn_sequence": success,
                "terminal": _jsonable(
                    await asyncio.to_thread(terminal_service.get_terminal, terminal_id)
                ),
            }
        if command.type == CommandType.KEY:
            success = await asyncio.to_thread(
                guarded, terminal_service.send_special_key, terminal_id, payload["key"]
            )
            return {"success": success}
        if command.type == CommandType.OUTPUT:
            if payload.get("mode") == "range":
                return {
                    "output": await asyncio.to_thread(
                        terminal_service.read_output_range,
                        terminal_id,
                        payload["offset"],
                        payload["length"],
                    )
                }
            mode = terminal_service.OutputMode(payload.get("mode", "full"))
            output = await asyncio.to_thread(
                terminal_service.get_output,
                terminal_id,
                mode,
                expected_generation=command.generation,
                ordinary_observation=True,
            )
            return {
                "output": output,
                "terminal": _jsonable(
                    await asyncio.to_thread(terminal_service.get_terminal, terminal_id)
                ),
            }
        if command.type == CommandType.WORKING_DIRECTORY:
            directory = await asyncio.to_thread(terminal_service.get_working_directory, terminal_id)
            return {"working_directory": directory}
        if command.type == CommandType.EXIT:
            await asyncio.to_thread(guarded, terminal_service.exit_terminal_cli, terminal_id)
            return {}
        if command.type == CommandType.DELETE:
            from cli_agent_orchestrator.backends.base import TerminalCleanupResult
            from cli_agent_orchestrator.backends.registry import get_backend

            metadata = await asyncio.to_thread(get_terminal_metadata, terminal_id)
            exact = await asyncio.to_thread(
                guarded,
                get_backend().cleanup_terminal_exact,
                terminal_id,
                metadata.get("tmux_session") if metadata else None,
                metadata.get("tmux_window") if metadata else None,
            )
            if not isinstance(exact, TerminalCleanupResult) or exact.outcome.value == "unknown":
                from cli_agent_orchestrator.runtime_channel.registry import (
                    RemoteOutcomeUnknownError,
                )

                raise RemoteOutcomeUnknownError("Exact remote terminal teardown remains uncertain")
            if not exact.reclaimed:
                raise RuntimeError("Exact remote terminal remains present")
            if metadata is None:
                return {"deleted": True, "absent": True}
            dismantled = await asyncio.to_thread(
                guarded,
                terminal_service.dismantle_terminal_runtime,
                terminal_id,
                metadata,
                kill_window=False,
            )
            deleted = dismantled and await asyncio.to_thread(
                guarded, terminal_service.delete_terminal_row, terminal_id, metadata
            )
            return {"deleted": bool(deleted)}
        raise ValueError(f"unsupported command: {command.type.value}")

    # --- connection ---

    def _current_statuses(self) -> Dict[str, TerminalStatus]:
        """Every terminal this runtime runs, with its status; unknown identities
        remain quarantined rather than authorizing cleanup."""
        from cli_agent_orchestrator.clients.database import list_all_terminals

        return {row["id"]: self._status_of(row["id"]) for row in list_all_terminals()}

    async def serve(self, ws: ClientConnection) -> None:
        """Run one connection: hello exchange, then commands until it closes."""
        statuses = await asyncio.to_thread(self._current_statuses)
        await ws.send(
            encode(
                Hello(
                    protocol_version=PROTOCOL_VERSION,
                    runtime_id=self.runtime_id,
                    statuses=statuses,
                    connection_epoch=None,
                    runtime_incarnation_id=self.incarnation_id,
                )
            )
        )
        reply = decode(await ws.recv())
        if not isinstance(reply, Hello) or reply.protocol_version != PROTOCOL_VERSION:
            raise ChannelRefused(
                f"protocol mismatch: server speaks {getattr(reply, 'protocol_version', '?')}, "
                f"this runtime {PROTOCOL_VERSION}"
            )
        if reply.runtime_incarnation_id != self.incarnation_id or reply.connection_epoch is None:
            raise ChannelRefused("Server did not acknowledge exact runtime incarnation")
        await asyncio.to_thread(self.admit_epoch, reply.connection_epoch)
        self._ws = ws
        self._helloed_at = asyncio.get_running_loop().time()
        self._mark_ready(True)
        logger.info("runtime %s connected to %s", self.runtime_id, self.server_url)
        # Everything from here on runs inside the guard: however the connected
        # state ends, the runtime stops reporting Ready.
        try:
            # Changes during the hello exchange were not forwarded (nothing was
            # connected yet), so send each terminal's status as it is now.
            for terminal_id in statuses:
                await self._push_status(terminal_id)
            unsent, self._unsent = self._unsent, []
            for result in unsent:
                await self._send(result)
            for result in await asyncio.to_thread(
                self.store.unsettled_results, self.runtime_id, self.incarnation_id
            ):
                await self._send(Result.model_validate(result))
            async for raw in ws:
                if len(raw) > 16 * 1024 * 1024:
                    raise ChannelRefused("Remote command frame exceeds transport bound")
                frame = decode(raw)
                if isinstance(frame, Command):
                    task = asyncio.create_task(self.handle(frame))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                else:
                    logger.warning("ignoring unexpected %s frame from the server", frame.kind)
        finally:
            self._ws = None
            self._mark_ready(False)

    async def run(self) -> None:
        """Keep a channel open until stopped, reconnecting with backoff."""
        backoff = BACKOFF_INITIAL
        self._mark_ready(False)
        while not self._stop.is_set():
            self._helloed_at = None
            try:
                async with connect(
                    self.server_url,
                    additional_headers={TOKEN_HEADER: self._token},
                    max_size=16 * 1024 * 1024,
                ) as ws:
                    await self.serve(ws)
            except ChannelRefused:
                raise
            except websockets.exceptions.InvalidStatus as exc:
                if exc.response.status_code in (401, 403):
                    raise ChannelRefused(
                        f"server refused the runtime token ({exc.response.status_code})"
                    ) from exc
                logger.warning("runtime channel rejected: %s", exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - network errors are retried
                logger.warning("runtime channel lost: %s", exc)
            helloed_at = self._helloed_at
            if (
                helloed_at is not None
                and asyncio.get_running_loop().time() - helloed_at >= STABLE_CONNECTION
            ):
                # Only a connection that held: a server that accepts the upgrade
                # (or even completes the hello) and then drops the channel still
                # gets exponential backoff.
                backoff = BACKOFF_INITIAL
            if self._stop.is_set():
                break
            logger.info("reconnecting in %.0fs", backoff)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, BACKOFF_MAX)

    def stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None:
            asyncio.get_running_loop().create_task(ws.close())

    def _mark_ready(self, ready: bool) -> None:
        if self._ready_file is None:
            return
        try:
            if ready:
                self._ready_file.parent.mkdir(parents=True, exist_ok=True)
                self._ready_file.write_text(f"{os.getpid()}\n")
            else:
                self._ready_file.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("could not update readiness file %s: %s", self._ready_file, exc)


def _jsonable(terminal: Dict[str, Any]) -> Dict[str, Any]:
    """Preserve additive receipt, causal and session identity wire fields."""
    from cli_agent_orchestrator.models.terminal import Terminal
    from cli_agent_orchestrator.services import terminal_service

    result = Terminal.model_validate(terminal).model_dump(mode="json")
    provider = terminal_service.provider_manager.get_provider(result["id"])
    result["native_swarm_pending"] = getattr(provider, "has_pending_native_swarm", False) is True
    return result


def _drop_stale_rows() -> int:
    """Keep historical identity/receipt evidence; hello status may be UNKNOWN.

    An absent reusable session label is not exact terminal teardown proof.
    No rows are erased during bridge startup.
    """
    return 0


def restore_incarnation(runtime_id, store, backend):
    """Only exact native ownership can reuse a prior runtime lifetime."""
    from cli_agent_orchestrator.backends.base import TerminalCleanupOutcome
    from cli_agent_orchestrator.clients.database import list_all_terminals

    previous = store.get_runtime(runtime_id)
    if previous:
        try:
            rows = list_all_terminals()
            if rows and all(
                backend.cleanup_terminal_exact(
                    row["id"], row["tmux_session"], row["tmux_window"], close=False
                ).outcome
                == TerminalCleanupOutcome.STILL_PRESENT
                for row in rows
            ):
                return previous["incarnation_id"]
        except Exception:
            logger.warning(
                "Previous runtime ownership cannot be reconstructed; retaining historical evidence"
            )
    return uuid.uuid4().hex


async def _amain() -> None:
    server_url = os.environ.get("CAO_BRIDGE_SERVER_URL", "").strip()
    runtime_id = os.environ.get("CAO_BRIDGE_RUNTIME_ID", "").strip()
    token = runtime_token()
    if runtime_id and not re.fullmatch(RUNTIME_ID_PATTERN, runtime_id):
        raise SystemExit(
            f"CAO_BRIDGE_RUNTIME_ID={runtime_id!r} is not a valid runtime id: it is "
            "addressed as /runtimes/{runtime_id}, so use letters, digits, '.', '_' and "
            "'-' (starting with a letter or digit, at most 128 characters)"
        )
    if not server_url or not runtime_id or not token:
        raise SystemExit(
            "cao-bridge requires CAO_BRIDGE_SERVER_URL, CAO_BRIDGE_RUNTIME_ID and "
            "CAO_RUNTIME_TOKEN_FILE (or CAO_RUNTIME_TOKEN)"
        )
    ready = os.environ.get("CAO_BRIDGE_READY_FILE", "").strip()
    ready_file = Path(ready) if ready else CAO_HOME_DIR / "bridge-ready"

    from cli_agent_orchestrator.clients.database import init_runtime_db
    from cli_agent_orchestrator.services.log_writer import log_writer
    from cli_agent_orchestrator.services.status_monitor import status_monitor

    init_runtime_db()
    # Before the first hello, which lists every row as a running terminal.
    await asyncio.to_thread(_drop_stale_rows)
    loop = asyncio.get_running_loop()
    bus.set_loop(loop)
    from cli_agent_orchestrator.backends.registry import get_backend
    from cli_agent_orchestrator.clients.database import engine
    from cli_agent_orchestrator.runtime_channel.store import RemoteOperationStore

    store = RemoteOperationStore(engine)
    incarnation = await asyncio.to_thread(restore_incarnation, runtime_id, store, get_backend())
    bridge = Bridge(
        server_url,
        runtime_id,
        token,
        ready_file=ready_file,
        store=store,
        incarnation_id=incarnation,
    )
    tasks = [
        asyncio.create_task(status_monitor.run()),
        asyncio.create_task(log_writer.run()),
        asyncio.create_task(bridge.forward_status()),
    ]
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, bridge.stop)
    try:
        await bridge.run()
    finally:
        if bridge._tasks:
            await asyncio.shield(asyncio.gather(*list(bridge._tasks), return_exceptions=True))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    from cli_agent_orchestrator.utils.logging import setup_logging

    setup_logging()
    try:
        asyncio.run(_amain())
    except ChannelRefused as exc:
        raise SystemExit(f"cao-bridge: {exc}")


if __name__ == "__main__":
    main()
