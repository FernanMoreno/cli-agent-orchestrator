"""Focused offline contracts for the private T187 paired controller draft."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import time

import pytest
from native_http_stream_activity import Snapshot
from native_http_stream_controller import (
    ControllerError,
    ProcessIdentity,
    cancel_if_growing_http_text,
)

pytestmark = pytest.mark.offline_database

TERMINAL = "0123abcd"
GENERATION = "a" * 32
RECEIPT = "b" * 64
OWNER = ProcessIdentity(4242, os.getuid(), 123456)


def metadata(*, status="processing", generation=GENERATION):
    return {
        "id": TERMINAL,
        "provider": "opencode_cli",
        "status": status,
        "turn": {
            "terminal_id": TERMINAL,
            "provider": "opencode_cli",
            "state": "pending",
            "generation": generation,
        },
    }


def snapshot(sequence, text_bytes, *, generation=GENERATION, receipt=RECEIPT):
    return Snapshot(
        TERMINAL,
        generation,
        receipt,
        (12, 34),
        3,
        sequence,
        text_bytes,
        sequence,
        200 + sequence,
        sequence,
    )


def harness(
    samples,
    *,
    api_values=None,
    owner_values=None,
    exited_after_cancel=True,
    wait_deadline=None,
    cancel_ok=True,
):
    events = []
    api_values = list(api_values or [metadata()] * 5)
    owner_values = list(owner_values or [OWNER, OWNER])
    samples = list(samples)
    cancelled = {"value": False}
    closed = []

    def refresh_api():
        events.append("api")
        return api_values.pop(0)

    def capture(value):
        events.append("capture")
        return samples.pop(0)

    def owned_process():
        events.append("owned_process")
        return owner_values.pop(0)

    def current_process(pid):
        events.append("process_identity")
        return None if cancelled["value"] else OWNER

    def open_pidfd(pid):
        events.append("pidfd_open")
        return 77

    def pidfd_ready(fd):
        events.append("pidfd_check")
        return cancelled["value"] and exited_after_cancel

    async def poll_sleep(seconds):
        events.append("sleep")
        if wait_deadline is not None:
            await asyncio.sleep(min(seconds, wait_deadline))
        return None

    async def cancel():
        events.append("cancel")
        cancelled["value"] = True
        return cancel_ok

    def close(fd):
        events.append("close_pidfd")
        closed.append(fd)

    async def run(deadline=None):
        return await cancel_if_growing_http_text(
            terminal_id=TERMINAL,
            deadline=deadline or time.monotonic() + 10,
            refresh_api=refresh_api,
            capture=capture,
            owned_process=owned_process,
            cancel=cancel,
            process_identity_fn=current_process,
            pidfd_open=open_pidfd,
            pidfd_exited_fn=pidfd_ready,
            close_pidfd=close,
            sleep_fn=poll_sleep,
        )

    return run, events, closed


def test_growth_poll_then_final_api_pg_capture_after_pidfd():
    first, second = snapshot(1, 26), snapshot(2, 81)
    run, events, closed = harness([first, first, second, second])

    proof = asyncio.run(run())

    assert proof is not None
    assert (proof.first, proof.second, proof.final) == (first, second, second)
    assert closed == [77]
    captures = [i for i, event in enumerate(events) if event == "capture"]
    assert len(captures) == 4
    # Producer cadence is polled; the final PG read follows the pinned PID,
    # and the last API projection and ownership check precede cancellation.
    assert events.index("pidfd_open") < captures[-1] < events.index("cancel")
    assert events[captures[-1] + 1] == "api"
    assert events[events.index("cancel") - 1] == "pidfd_check"
    assert events[-1] == "close_pidfd"


def test_finish_after_growth_refuses_final_capture_and_closes_pidfd():
    first, second = snapshot(1, 26), snapshot(2, 81)
    run, events, closed = harness(
        [first, second],
        api_values=[metadata(), metadata(), metadata(status="completed")],
    )

    assert asyncio.run(run()) is None
    assert events[-2:] == ["api", "close_pidfd"]
    assert "cancel" not in events
    assert closed == [77]


def test_pg_capture_race_after_pidfd_refuses_cancel_and_closes_fd():
    first, second = snapshot(1, 26), snapshot(2, 81)
    run, events, closed = harness([first, second, None])

    assert asyncio.run(run()) is None
    assert "cancel" not in events
    assert closed == [77]


def test_final_receipt_change_or_process_replacement_refuses_cancel():
    first, second = snapshot(1, 26), snapshot(2, 81)
    run, events, closed = harness([first, second, snapshot(2, 81, receipt="c" * 64)])
    assert asyncio.run(run()) is None
    assert "cancel" not in events
    assert closed == [77]

    run, events, closed = harness(
        [first, second, second],
        owner_values=[OWNER, dataclasses.replace(OWNER, birth=OWNER.birth + 1)],
    )
    assert asyncio.run(run()) is None
    assert "cancel" not in events
    assert closed == [77]


def test_live_pidfd_precondition_and_physical_stop_deadline():
    first, second = snapshot(1, 26), snapshot(2, 81)
    run, events, closed = harness(
        [first, second, second], exited_after_cancel=False, wait_deadline=0.01
    )

    with pytest.raises(ControllerError, match="http_stream_native_process_not_stopped"):
        asyncio.run(run(time.monotonic() + 0.03))
    assert "cancel" in events
    assert closed == [77]


def test_no_growth_never_opens_pidfd_or_cancels_before_deadline():
    first = snapshot(1, 26)
    run, events, closed = harness(
        [first] * 12,
        wait_deadline=0.01,
    )

    assert asyncio.run(run(time.monotonic() + 0.03)) is None
    assert "pidfd_open" not in events
    assert "cancel" not in events
    assert closed == []


@pytest.mark.parametrize("receipt", [1, "ok", {"worker_stopped": True}])
def test_truthy_untyped_cancel_receipt_is_rejected(receipt):
    first, second = snapshot(1, 26), snapshot(2, 81)
    run, _, closed = harness([first, second, second], cancel_ok=receipt)
    with pytest.raises(ControllerError, match="http_stream_cancel_receipt_invalid"):
        asyncio.run(run())
    assert closed == [77]


def test_api_completion_after_final_pg_capture_refuses_cancel():
    first, second = snapshot(1, 26), snapshot(2, 81)
    run, events, closed = harness(
        [first, second, second],
        api_values=[metadata(), metadata(), metadata(), metadata(status="completed")],
    )
    assert asyncio.run(run()) is None
    assert "cancel" not in events
    assert closed == [77]


def test_already_expired_retirement_cannot_credit_a_readable_pidfd():
    import native_http_stream_controller as controller

    assert (
        asyncio.run(
            controller._wait_for_pidfd_exit(
                77,
                time.monotonic() - 1,
                pidfd_exited_fn=lambda _: True,
                sleep_fn=asyncio.sleep,
            )
        )
        is False
    )


@pytest.fixture
def owned_python_process():
    import subprocess
    import sys

    import native_http_stream_controller as controller

    child = subprocess.Popen(
        [sys.executable, "-I", "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        identity = controller.process_identity(child.pid)
        assert identity is not None
        yield child, identity
    finally:
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=2)


@pytest.mark.parametrize("interrupt_wait", [False, True])
def test_actual_pidfd_retirement_and_interrupted_wait(owned_python_process, interrupt_wait):
    import errno
    import sys
    from pathlib import Path

    import native_http_stream_controller as controller

    child, identity = owned_python_process
    first, second = snapshot(1, 26), snapshot(2, 81)
    samples = iter([first, second, second])
    opened, closed = [], []
    waiting = asyncio.Event()
    cancel_started = False

    def owned():
        if controller.process_identity(child.pid) != identity:
            return None
        if (Path("/proc") / str(child.pid) / "exe").resolve() != Path(sys.executable).resolve():
            return None
        return identity

    def pin(pid):
        fd = os.pidfd_open(pid)
        opened.append(fd)
        return fd

    def close(fd):
        closed.append(fd)
        os.close(fd)

    async def cancel():
        nonlocal cancel_started
        cancel_started = True
        if not interrupt_wait:
            child.terminate()
        return True

    async def sleep(seconds):
        if cancel_started:
            waiting.set()
        await asyncio.sleep(seconds)

    async def run():
        task = asyncio.create_task(
            controller.cancel_if_growing_http_text(
                terminal_id=TERMINAL,
                deadline=time.monotonic() + 3,
                refresh_api=metadata,
                capture=lambda _: next(samples),
                owned_process=owned,
                cancel=cancel,
                pidfd_open=pin,
                close_pidfd=close,
                sleep_fn=sleep,
            )
        )
        try:
            if interrupt_wait:
                await asyncio.wait_for(waiting.wait(), timeout=2)
                os.fstat(opened[0])  # Still held by the one active coroutine.
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert child.poll() is None
            else:
                proof = await task
                assert proof is not None and proof.process == identity
                child.wait(timeout=1)
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    asyncio.run(run())
    assert len(opened) == 1 and closed == opened
    with pytest.raises(OSError) as error:
        os.fstat(opened[0])
    assert error.value.errno == errno.EBADF
