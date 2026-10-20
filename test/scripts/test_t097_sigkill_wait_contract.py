"""Keep the T097 proxy rendezvous sensitive to early dispatch failures."""

from __future__ import annotations

import asyncio
import inspect
from test.integration.t097.test_proxy_sigkill_t097_restored import (
    _add_cleanup_note_if_supported,
    _post_sigkill_cleanup_timeout_seconds,
    _wait_for_upstream_or_dispatch,
)

import pytest

pytestmark = pytest.mark.offline_database


def test_cleanup_diagnostic_does_not_mask_primary_error_without_add_note():
    class LegacyError(RuntimeError):
        # Python 3.10's BaseException has no add_note; this also exercises the
        # guard on newer Python versions without depending on interpreter age.
        add_note = None

    primary_error = LegacyError("proxy body failed")

    with pytest.raises(LegacyError) as caught:
        try:
            raise primary_error
        except BaseException as body_error:
            try:
                raise RuntimeError("secondary cleanup failed")
            except BaseException as cleanup_error:
                _add_cleanup_note_if_supported(
                    body_error,
                    f"cleanup also raised {type(cleanup_error).__name__}",
                )
            raise

    assert caught.value is primary_error


def test_cleanup_diagnostic_uses_add_note_when_available():
    class NoteCapableError(RuntimeError):
        def __init__(self) -> None:
            super().__init__("proxy body failed")
            self.notes: list[str] = []

        def add_note(self, note: str) -> None:
            self.notes.append(note)

    error = NoteCapableError()
    _add_cleanup_note_if_supported(error, "cleanup also raised RuntimeError")

    assert error.notes == ["cleanup also raised RuntimeError"]


def test_cleanup_note_failure_does_not_mask_primary_error():
    class BrokenNoteError(RuntimeError):
        def add_note(self, _note: str) -> None:
            raise RuntimeError("diagnostic attachment failed")

    primary_error = BrokenNoteError("proxy body failed")
    with pytest.raises(BrokenNoteError) as caught:
        try:
            raise primary_error
        except BaseException as body_error:
            _add_cleanup_note_if_supported(body_error, "cleanup also failed")
            raise

    assert caught.value is primary_error


def test_post_sigkill_cleanup_wait_limit_retains_existing_phase_values(monkeypatch):
    monkeypatch.setenv("T097_TEST_WORKER_TIMEOUT_SECONDS", "45")
    assert _post_sigkill_cleanup_timeout_seconds() == 65


def test_initial_callback_wait_has_no_synthetic_timeout_parameter():
    assert "timeout_seconds" not in inspect.signature(_wait_for_upstream_or_dispatch).parameters


def test_wait_returns_when_upstream_callback_starts_without_dispatch_completion(monkeypatch):
    async def exercise() -> None:
        upstream_started = asyncio.Event()
        dispatch = asyncio.get_running_loop().create_future()
        real_wait = asyncio.wait
        observed_timeouts = []

        async def record_wait(awaitables, *, timeout=None, return_when=asyncio.ALL_COMPLETED):
            observed_timeouts.append(timeout)
            return await real_wait(awaitables, timeout=timeout, return_when=return_when)

        monkeypatch.setattr(asyncio, "wait", record_wait)
        asyncio.get_running_loop().call_soon(upstream_started.set)
        await _wait_for_upstream_or_dispatch(upstream_started, dispatch)
        assert observed_timeouts == [None]
        assert not dispatch.done()
        dispatch.cancel()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "error",
    [RuntimeError("dispatch setup failed before upstream"), TimeoutError("dispatch timed out")],
)
def test_wait_for_upstream_preserves_early_dispatch_exception_identity(error):
    async def exercise() -> None:
        upstream_started = asyncio.Event()

        async def fail_dispatch() -> object:
            raise error

        dispatch = asyncio.create_task(fail_dispatch())
        with pytest.raises(type(error)) as caught:
            await _wait_for_upstream_or_dispatch(upstream_started, dispatch)
        assert caught.value is error

    asyncio.run(exercise())


def test_wait_fails_if_dispatch_completes_before_upstream_callback():
    async def exercise() -> None:
        upstream_started = asyncio.Event()
        dispatch = asyncio.get_running_loop().create_future()
        dispatch.set_result({"status": "completed"})
        with pytest.raises(AssertionError, match="dispatch completed before"):
            await _wait_for_upstream_or_dispatch(upstream_started, dispatch)

    asyncio.run(exercise())


def test_cancellation_cleans_up_the_upstream_event_waiter():
    async def exercise() -> None:
        waiter_cancelled = asyncio.Event()

        class ObservedEvent(asyncio.Event):
            async def wait(self) -> bool:
                try:
                    return await super().wait()
                except asyncio.CancelledError:
                    waiter_cancelled.set()
                    raise

        upstream_started = ObservedEvent()
        dispatch = asyncio.get_running_loop().create_future()
        wait_task = asyncio.create_task(_wait_for_upstream_or_dispatch(upstream_started, dispatch))
        await asyncio.sleep(0)
        wait_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await wait_task
        await asyncio.wait_for(waiter_cancelled.wait(), timeout=1)
        assert not dispatch.done()
        dispatch.cancel()

    asyncio.run(exercise())
