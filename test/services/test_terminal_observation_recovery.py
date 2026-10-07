"""Bounded restart inventory and cancellation retain observation ownership."""

import asyncio
import threading

import pytest

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.services import terminal_observation_recovery as recovery


def test_inventory_pages_skip_reclaimed_rows_and_isolate_unavailable_targets(
    isolated_memory_db, monkeypatch
):
    for i in range(20):
        db.create_terminal(f"{i:02}", "recovery", f"worker-{i}", "codex")
    db.update_terminal_deferred_init_runtime_reclaimed("00", True)
    observed = []

    def restore(terminal_id):
        observed.append(terminal_id)
        if terminal_id == "02":
            raise OSError("temporary backend failure")
        return True

    monkeypatch.setattr(recovery.terminal_service, "restore_terminal_observation", restore)
    cursor = [""]
    assert recovery.restore_once(cursor=cursor) == 15
    assert cursor == ["16"]
    assert recovery.restore_once(after=cursor[0], cursor=cursor) == 3
    assert cursor == ["19"]
    assert recovery.restore_once(after=cursor[0], cursor=cursor) == 0
    assert cursor == [""]
    assert observed == [f"{i:02}" for i in range(1, 20)]


@pytest.mark.asyncio
async def test_shutdown_drains_inflight_observation_before_return(monkeypatch):
    started = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()
    loop = asyncio.get_running_loop()

    def restore_once(**kwargs):
        loop.call_soon_threadsafe(started.set)
        release.wait(timeout=5)
        finished.set()
        return 0

    monkeypatch.setattr(recovery, "restore_once", restore_once)
    task = asyncio.create_task(recovery.run())
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert not finished.is_set()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert finished.is_set()
        assert task.cancelled()
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_inventory_failures_back_off_then_resume_maintenance(monkeypatch):
    delays, attempts = [], []

    def restore_once(**kwargs):
        attempts.append(kwargs["after"])
        if len(attempts) <= 6:
            kwargs["cursor"][:] = ["stale-page"]
            raise OSError("persistent inventory outage")
        kwargs["cursor"][:] = ["next-page" if len(attempts) == 7 else ""]
        return 1

    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 9:
            raise asyncio.CancelledError

    monkeypatch.setattr(recovery, "restore_once", restore_once)
    monkeypatch.setattr(recovery.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await recovery.run()
    assert delays == [1, 2, 4, 8, 16, 30, 60, 0, 60]
    assert attempts[6] == ""


@pytest.mark.asyncio
async def test_target_failures_are_visible_to_backoff_and_success_resets_it(monkeypatch):
    delays, calls = [], []
    monkeypatch.setattr(
        recovery.database, "list_terminal_observation_candidates", lambda **kwargs: ["t"]
    )

    def restore(terminal_id):
        calls.append(terminal_id)
        if len(calls) != 3:
            raise OSError("backend unavailable")
        return True

    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 4:
            raise asyncio.CancelledError

    monkeypatch.setattr(recovery.terminal_service, "restore_terminal_observation", restore)
    monkeypatch.setattr(recovery.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await recovery.run()
    assert delays == [1, 2, 0, 1]
    assert calls == ["t"] * 4


@pytest.mark.asyncio
async def test_cooldown_preserves_progress_beyond_six_failing_pages(
    isolated_memory_db, monkeypatch
):
    for index in range(112):
        db.create_terminal(f"{index:03}", "recovery", f"worker-{index}", "codex")
    observed, delays = [], []

    def restore(terminal_id):
        observed.append(terminal_id)
        if int(terminal_id) < 96:
            raise OSError("unavailable target")
        return True

    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 9:
            raise asyncio.CancelledError

    monkeypatch.setattr(recovery.terminal_service, "restore_terminal_observation", restore)
    monkeypatch.setattr(recovery.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await recovery.run()
    assert delays == [1, 2, 4, 8, 16, 30, 60, 0, 60]
    assert observed == [f"{index:03}" for index in range(112)]


@pytest.mark.asyncio
async def test_persistent_outage_stays_on_maintenance_until_success(monkeypatch):
    delays = []

    def restore_once(**kwargs):
        raise OSError("persistent inventory outage")

    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 9:
            raise asyncio.CancelledError

    monkeypatch.setattr(recovery, "restore_once", restore_once)
    monkeypatch.setattr(recovery.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await recovery.run()
    assert delays == [1, 2, 4, 8, 16, 30, 60, 60, 60]
