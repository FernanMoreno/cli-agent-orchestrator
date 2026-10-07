"""Public recovery for every receipt-backed terminal, including supervisors."""

import asyncio
import re

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.backends.registry import get_backend
from cli_agent_orchestrator.clients.database import (
    begin_terminal_turn_cancellation,
    finish_terminal_turn_cancellation,
    get_terminal_metadata,
    get_terminal_turn_receipt,
    get_terminal_turn_recovery,
    transition_native_child,
)
from cli_agent_orchestrator.constants import FIFO_DIR
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.base import TurnResultUnavailableError
from cli_agent_orchestrator.providers.manager import provider_manager
from cli_agent_orchestrator.services.event_bus import bus
from cli_agent_orchestrator.services.status_monitor import status_monitor
from cli_agent_orchestrator.services.terminal_service import ensure_terminal_is_not_work_owned
from cli_agent_orchestrator.services.work_terminal import terminal_dispatch_lock


class TurnRecoveryConflict(Exception):
    """The requested generation/action no longer owns this terminal."""


_cancel_jobs: set[asyncio.Task] = set()


def _cancel_finished(task: asyncio.Task) -> None:
    _cancel_jobs.discard(task)
    if not task.cancelled():
        task.exception()  # Consume failures even if the HTTP caller disconnected.


def get_turn(terminal_id: str) -> dict:
    from cli_agent_orchestrator.services import remote_terminal_service as remote

    if remote.placement(terminal_id):
        projection = remote.terminal_projection(terminal_id)
        if projection is None:
            raise ValueError("terminal not found")
        remote_turn = projection.get("turn")
        if remote_turn is not None and not isinstance(remote_turn, dict):
            raise RuntimeError("Remote response has a malformed turn")
        return remote_turn or {
            "terminal_id": terminal_id,
            "generation": None,
            "state": "none",
            "allowed_actions": [],
        }

    metadata = get_terminal_metadata(terminal_id)
    if metadata is None:
        raise ValueError("terminal not found")
    receipt = get_terminal_turn_receipt(terminal_id)
    recovery = (
        get_terminal_turn_recovery(terminal_id, receipt["generation"])
        if receipt is not None
        else None
    )
    state = "none"
    if receipt is not None:
        state = "verified" if receipt["phase"] == "result_verified" else "pending"
    if recovery is not None:
        state = recovery["state"]
    return {
        "terminal_id": terminal_id,
        "provider": metadata["provider"],
        "generation": receipt["generation"] if receipt is not None else None,
        "state": state,
        "reason": recovery.get("reason") if recovery is not None else None,
        "attempts": recovery.get("attempts", 0) if recovery is not None else 0,
        "allowed_actions": (
            ["verify", "cancel"]
            if state in {"pending", "verifying", "reconcile"}
            else ["cancel"] if state == "cancelling" else []
        ),
    }


def _check_generation(terminal_id: str, generation: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", generation):
        raise TurnRecoveryConflict("invalid turn generation")
    turn = get_turn(terminal_id)
    if turn["generation"] != generation:
        raise TurnRecoveryConflict("turn generation changed; inspect the current turn")
    return turn


def verify_turn(terminal_id: str, generation: str) -> dict:
    """Read existing evidence once; never type or spend another automatic budget."""
    from cli_agent_orchestrator.services import remote_terminal_service as remote

    if remote.placement(terminal_id):
        from cli_agent_orchestrator.runtime_channel.protocol import CommandType

        remote_turn = remote.call(terminal_id, CommandType.VERIFY, generation=generation)["turn"]
        if not isinstance(remote_turn, dict):
            raise RuntimeError("Remote response has a malformed turn")
        return remote_turn

    from cli_agent_orchestrator.services import terminal_service

    ensure_terminal_is_not_work_owned(terminal_id)
    turn = _check_generation(terminal_id, generation)
    if turn["state"] == "verified":
        return turn
    if "verify" not in turn["allowed_actions"]:
        raise TurnRecoveryConflict("turn cannot be verified in its current state")
    try:
        terminal_service.get_output(
            terminal_id, terminal_service.OutputMode.LAST, expected_generation=generation
        )
    except TurnResultUnavailableError:
        pass
    return _check_generation(terminal_id, generation)


async def cancel_turn(terminal_id: str, generation: str) -> dict:
    """Fence result settlement before replacing the old execution's pane.

    The old receipt remains in the provider until the new CLI is ready, so
    neither inbox nor an external sender can deliver a new task during reset.
    A transport/startup failure leaves the terminal unavailable with its audit
    history and messages intact. The original task is never resent.
    """
    from cli_agent_orchestrator.services import remote_terminal_service as remote

    if remote.placement(terminal_id):
        from cli_agent_orchestrator.runtime_channel.protocol import CommandType

        remote_turn = (
            await remote.call_async(terminal_id, CommandType.CANCEL, generation=generation)
        )["turn"]
        if not isinstance(remote_turn, dict):
            raise RuntimeError("Remote response has a malformed turn")
        return remote_turn

    job = asyncio.create_task(_cancel_serialized(terminal_id, generation))
    _cancel_jobs.add(job)
    job.add_done_callback(_cancel_finished)
    # A disconnected caller must not abandon an acquired flock or release
    # it while the reset worker is still changing the owned pane.
    return await asyncio.shield(job)


async def _cancel_serialized(terminal_id: str, generation: str) -> dict:
    lock = terminal_dispatch_lock(constants.DATABASE_FILE, terminal_id)
    acquisition = asyncio.create_task(asyncio.to_thread(lock.__enter__))
    try:
        await asyncio.shield(acquisition)
    except asyncio.CancelledError:
        acquisition.add_done_callback(lambda _task: lock.release())
        raise
    try:
        turn = await _cancel_locked(terminal_id, generation)
    finally:
        lock.__exit__(None, None, None)
    observed = status_monitor.get_status(terminal_id)
    if observed in {TerminalStatus.IDLE, TerminalStatus.COMPLETED}:
        # Startup emitted readiness before the local receipt was released.
        # Publish after dropping dispatch ownership so pending inbox delivery
        # follows its normal ready-state path without racing the reset.
        bus.publish(f"terminal.{terminal_id}.status", {"status": observed.value})
    return turn


async def _cancel_locked(terminal_id: str, generation: str) -> dict:
    ensure_terminal_is_not_work_owned(terminal_id)
    metadata = get_terminal_metadata(terminal_id)
    if metadata is None:
        raise ValueError("terminal not found")
    if not await asyncio.to_thread(begin_terminal_turn_cancellation, terminal_id, generation):
        raise TurnRecoveryConflict("turn generation changed or already settled")
    backend = get_backend()
    provider = provider_manager.get_provider(terminal_id)
    try:
        await asyncio.to_thread(
            backend.reset_window,
            metadata["tmux_session"],
            metadata["tmux_window"],
            terminal_id=terminal_id,
        )
        await asyncio.to_thread(transition_native_child, terminal_id, "cancelled")
        status_monitor.reset_buffer(terminal_id)
        # respawn-pane can detach the old output pipe. Reattach its existing
        # FIFO rather than create a second reader or lose the durable log.
        if not backend.supports_event_inbox():
            fifo_path = FIFO_DIR / f"{terminal_id}.fifo"
            if fifo_path.exists():
                await asyncio.to_thread(
                    backend.stop_pipe_pane, metadata["tmux_session"], metadata["tmux_window"]
                )
                await asyncio.to_thread(
                    backend.pipe_pane,
                    metadata["tmux_session"],
                    metadata["tmux_window"],
                    str(fifo_path),
                )
        assert provider is not None
        setattr(provider, "_initialized", False)
        if await provider.initialize() is not True:
            raise RuntimeError("provider did not become ready after turn cancellation")
        if not await asyncio.to_thread(finish_terminal_turn_cancellation, terminal_id, generation):
            raise TurnRecoveryConflict("turn cancellation could not be confirmed")
        provider.mark_turn_receipt_cancelled()
    except Exception:
        status_monitor.publish_receipt_reconciliation(terminal_id)
        raise
    return get_turn(terminal_id)
