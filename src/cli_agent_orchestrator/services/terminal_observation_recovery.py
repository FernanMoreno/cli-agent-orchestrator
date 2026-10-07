"""Reconstruct native observation after restart without replaying agent input."""

from __future__ import annotations

import asyncio
import logging

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.services import terminal_service

logger = logging.getLogger(__name__)


def restore_once(
    *,
    after: str = "",
    cursor: list[str] | None = None,
    failures: list[str] | None = None,
) -> int:
    candidates = database.list_terminal_observation_candidates(after=after)
    if cursor is not None:
        cursor[:] = [candidates[-1] if candidates else ""]
    restored = 0
    for terminal_id in candidates:
        try:
            restored += int(terminal_service.restore_terminal_observation(terminal_id))
        except Exception:
            if failures is not None:
                failures.append(terminal_id)
            # An unavailable backend/credential/store preserves the same task.
            # A later sweep may restore observation; it may never resend input.
            logger.debug("Observation remains unavailable for %s", terminal_id, exc_info=True)
    return restored


async def run() -> None:
    cursor = [""]
    pending = None
    consecutive_failures = 0
    try:
        while True:
            failures: list[str] = []
            restored = 0
            pending = asyncio.create_task(
                asyncio.to_thread(
                    restore_once,
                    after=cursor[0],
                    cursor=cursor,
                    failures=failures,
                )
            )
            try:
                restored = await asyncio.shield(pending)
            except Exception:
                failures.append("inventory")
                cursor[:] = [""]
                logger.warning("Native observation reconstruction unavailable", exc_info=True)
            finally:
                if pending.done():
                    pending = None
            if failures and not restored:
                if consecutive_failures < 6:
                    consecutive_failures += 1
                    await asyncio.sleep(min(2 ** (consecutive_failures - 1), 30))
                    if consecutive_failures < 6:
                        continue
                # Finish this retry window on the existing maintenance cadence.
                # Keep target-page progress so an unavailable prefix cannot
                # starve later terminals. Inventory query errors reset above.
                # Observation recovery never replays input.
                await asyncio.sleep(60)
                continue
            if restored:
                consecutive_failures = 0
            # Drain bounded inventory pages at startup without a minute per
            # page. Retry a finished inventory on the next maintenance sweep.
            await asyncio.sleep(0 if cursor[0] else 60)
    finally:
        if pending is not None:
            await asyncio.shield(asyncio.gather(pending, return_exceptions=True))
