"""Disconnected diagnostic controller for PG-bound HTTP TEXT cancellation.

API and capture callbacks must be synchronous and bound to the supplied run
deadline. The runner owns exact executable/argv/terminal verification and the
full descendant retirement/cleanup gate; this helper proves its selected PID.
"""

from __future__ import annotations

import asyncio
import os
import re
import select
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from native_http_stream_activity import Snapshot, growing

GENERATION_RE = re.compile(r"[a-f0-9]{32}\Z")
SAMPLE_POLL_SECONDS = 0.75  # Align with the observer's regular publication cadence.
PIDFD_POLL_SECONDS = 0.05


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    uid: int
    birth: int


@dataclass(frozen=True)
class CancellationProof:
    first: Snapshot
    second: Snapshot
    final: Snapshot
    process: ProcessIdentity


class ControllerError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def process_identity(pid: int) -> ProcessIdentity | None:
    process = Path("/proc") / str(pid)
    try:
        fields = (process / "stat").read_bytes().rsplit(b")", 1)[1].split()
        if fields[0] == b"Z":
            return None
        return ProcessIdentity(pid, process.stat().st_uid, int(fields[19]))
    except (FileNotFoundError, ProcessLookupError):
        return None


def _active_metadata(metadata: Any, terminal_id: str) -> str | None:
    if type(metadata) is not dict or type(metadata.get("turn")) is not dict:
        return None
    turn = metadata["turn"]
    generation = turn.get("generation")
    fields = (
        metadata.get("id"),
        metadata.get("provider"),
        metadata.get("status"),
        turn.get("terminal_id"),
        turn.get("provider"),
        turn.get("state"),
    )
    expected = (terminal_id, "opencode_cli", "processing", terminal_id, "opencode_cli", "pending")
    return (
        generation
        if fields == expected and type(generation) is str and GENERATION_RE.fullmatch(generation)
        else None
    )


def _same_binding(a: Any, b: Any) -> bool:
    if type(a) is not Snapshot or type(b) is not Snapshot:
        return False
    return (
        a.terminal_id,
        a.generation,
        a.receipt_sha256,
        a.directory_identity,
        a.request_ordinal,
    ) == (b.terminal_id, b.generation, b.receipt_sha256, b.directory_identity, b.request_ordinal)


def _not_regressed(previous: Snapshot, current: Snapshot) -> bool:
    return (
        current.sequence >= previous.sequence
        and 0 < previous.text_bytes <= current.text_bytes
        and 0 < previous.text_events <= current.text_events
        and current.wire_bytes >= previous.wire_bytes
        and current.event_count >= previous.event_count
    )


def pidfd_exited(pidfd: int) -> bool:
    return bool(select.select([pidfd], [], [], 0)[0])


def _fresh_snapshot(*, terminal_id, deadline, refresh_api, capture):
    if time.monotonic() >= deadline:
        return None
    metadata = refresh_api()
    generation = _active_metadata(metadata, terminal_id)
    if generation is None or time.monotonic() >= deadline:
        return None
    snapshot = capture(metadata)
    return (
        snapshot
        if (
            time.monotonic() < deadline
            and type(snapshot) is Snapshot
            and snapshot.terminal_id == terminal_id
            and snapshot.generation == generation
        )
        else None
    )


async def _wait_for_pidfd_exit(pidfd, deadline, *, pidfd_exited_fn, sleep_fn):
    while time.monotonic() < deadline:
        if pidfd_exited_fn(pidfd):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await sleep_fn(min(PIDFD_POLL_SECONDS, remaining))
    return False


async def cancel_if_growing_http_text(
    *,
    terminal_id: str,
    deadline: float,
    refresh_api: Callable[[], dict[str, Any]],
    capture: Callable[[dict[str, Any]], Snapshot | None],
    owned_process: Callable[[], ProcessIdentity | None],
    cancel: Callable[[], Awaitable[bool]],
    process_identity_fn: Callable[[int], ProcessIdentity | None] = process_identity,
    pidfd_open: Callable[[int], int] = os.pidfd_open,
    pidfd_exited_fn: Callable[[int], bool] = pidfd_exited,
    close_pidfd: Callable[[int], None] = os.close,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> CancellationProof | None:
    """Require two growing samples, fresh PG evidence and a live owned PID."""

    first = _fresh_snapshot(
        terminal_id=terminal_id,
        deadline=deadline,
        refresh_api=refresh_api,
        capture=capture,
    )
    if first is None:
        return None
    second = None
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        await sleep_fn(min(SAMPLE_POLL_SECONDS, remaining))
        if time.monotonic() >= deadline:
            return None
        current = _fresh_snapshot(
            terminal_id=terminal_id,
            deadline=deadline,
            refresh_api=refresh_api,
            capture=capture,
        )
        if current is None:
            return None
        if growing(first, current):
            second = current
            break
    if second is None:
        return None
    # Pin the exact worker before the last API+PG capture. Its pidfd fences exit
    # during those bounded synchronous reads.
    identity = owned_process()
    if (
        type(identity) is not ProcessIdentity
        or identity.pid <= 1
        or identity.uid != os.getuid()
        or identity.birth <= 0
    ):
        return None
    pidfd = pidfd_open(identity.pid)
    try:
        final = _fresh_snapshot(
            terminal_id=terminal_id,
            deadline=deadline,
            refresh_api=refresh_api,
            capture=capture,
        )
        if (
            final is None
            or not _same_binding(second, final)
            or not _not_regressed(second, final)
            or time.monotonic() >= deadline
        ):
            return None
        if _active_metadata(refresh_api(), terminal_id) != final.generation:
            return None
        # Full executable/argv/terminal revalidation and pidfd liveness are the
        # last synchronous checks before invoking the cancellation callback.
        checked = owned_process()
        if (
            checked != identity
            or process_identity_fn(identity.pid) != identity
            or pidfd_exited_fn(pidfd)
            or time.monotonic() >= deadline
        ):
            return None
        if await cancel() is not True:
            raise ControllerError("http_stream_cancel_receipt_invalid")
        stopped = await _wait_for_pidfd_exit(
            pidfd, deadline, pidfd_exited_fn=pidfd_exited_fn, sleep_fn=sleep_fn
        )
        if not stopped or process_identity_fn(identity.pid) == identity:
            raise ControllerError("http_stream_native_process_not_stopped")
        return CancellationProof(first, second, final, identity)
    finally:
        close_pidfd(pidfd)
