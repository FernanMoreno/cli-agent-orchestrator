"""Owned, bounded late evidence observations; no task input or retry authority."""

from __future__ import annotations

import asyncio
import logging
import time

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.services import terminal_service
from cli_agent_orchestrator.services.terminal_service import ensure_terminal_is_not_work_owned

logger = logging.getLogger(__name__)


def observe_once(
    *, now: float | None = None, after: str = "", cursor: list[str] | None = None
) -> int:
    settled = 0
    candidates = database.list_late_turn_candidates(now=now, after=after)
    if cursor is not None:
        cursor[:] = [candidates[-1]["terminal_id"] if candidates else ""]
    for candidate in candidates:
        terminal_id, generation = candidate["terminal_id"], candidate["generation"]
        try:
            # Work results have their own authenticated receiver/reducer path.
            # An ordinary late read cannot acquire its authority.
            ensure_terminal_is_not_work_owned(terminal_id)
            if not database.claim_late_turn_observation(
                terminal_id, generation, now=time.time() if now is None else now
            ):
                continue
            terminal_service.get_output(
                terminal_id,
                terminal_service.OutputMode.LAST,
                expected_generation=generation,
                ordinary_observation=True,
            )
            recovery = database.get_terminal_turn_recovery(terminal_id, generation)
            if recovery is not None and recovery["state"] == "verified":
                settled += 1
        except Exception:
            # Errors preserve the original reconcile state and immutable result.
            # No exception text/transcript is published as successful output.
            logger.debug("Late evidence observation retained for %s", terminal_id, exc_info=True)
    return settled


async def run() -> None:
    """At most one owned read batch, drained before lifespan replacement."""
    pending = None
    cursor = [""]
    try:
        while True:
            pending = asyncio.create_task(
                asyncio.to_thread(observe_once, after=cursor[0], cursor=cursor)
            )
            try:
                await asyncio.shield(pending)
            except Exception:
                logger.warning(
                    "Late receipt observation unavailable; retaining evidence for next sweep",
                    exc_info=True,
                )
            finally:
                if pending.done():
                    pending = None
            await asyncio.sleep(60)
    finally:
        if pending is not None:
            await asyncio.shield(asyncio.gather(pending, return_exceptions=True))
