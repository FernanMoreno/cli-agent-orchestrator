"""The terminal local opening fence is neither session inventory nor closure proof."""

import asyncio
import importlib
import os
import select
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = pytest.mark.offline_database


def fresh(monkeypatch, *, marked=True):
    origin = importlib.import_module("cli_agent_orchestrator.utils.process_sql_origin")
    assert callable(
        getattr(origin, "sql_process_opening", None)
    ), "missing physical SQL opening gate"
    assert callable(
        getattr(origin, "seal_sql_process_openings", None)
    ), "missing terminal SQL fence"
    monkeypatch.setattr(origin, "_STATE", origin._State(os.getpid()))
    monkeypatch.delenv(origin.RESOURCE_BINDING_FD_ENV, raising=False)
    if marked:
        monkeypatch.setenv(origin.RESOURCE_BINDING_FD_ENV, "123")
    return origin


def test_terminal_fence_is_irreversible_and_latches_after_the_fd_is_consumed(monkeypatch):
    origin = fresh(monkeypatch)
    name = origin.current_sql_application_name()
    monkeypatch.delenv(origin.RESOURCE_BINDING_FD_ENV)
    assert origin.seal_sql_process_openings() is True
    assert origin.seal_sql_process_openings() is True
    with pytest.raises(origin.SqlProcessOpeningsSealed):
        with origin.sql_process_opening():
            pytest.fail("sealed process reached the physical connector")
    assert origin.current_sql_application_name() == name
    assert origin._STATE.opening == 0


def test_unmarked_process_does_not_get_a_terminal_fence(monkeypatch):
    origin = fresh(monkeypatch, marked=False)
    assert origin.seal_sql_process_openings() is False
    with origin.sql_process_opening():
        assert not origin._STATE.enabled
    assert origin.current_sql_application_name() == "caos"


@pytest.mark.parametrize("failure", [OSError("connect failed"), MemoryError("connect failed")])
def test_connect_failure_keeps_uncertainty_after_the_call_finishes(monkeypatch, failure):
    origin = fresh(monkeypatch)
    with pytest.raises(type(failure)) as failed:
        with origin.sql_process_opening():
            assert origin._STATE.opening == 1
            raise failure
    assert failed.value is failure
    assert origin._STATE.opening == 0 and origin._STATE.unconfirmed == 1
    origin.seal_sql_process_openings()
    assert origin._STATE.unconfirmed == 1


def test_fork_snapshot_during_failure_transfer_never_loses_uncertainty(monkeypatch):
    origin = fresh(monkeypatch)
    snapshots = []

    class ForkSnapshot(origin._State):
        def __setattr__(self, name, value):
            object.__setattr__(self, name, value)
            if name == "opening" and value == 0 and getattr(self, "observe", False):
                # The at-fork callback cannot take this held guard. Sample the
                # exact intermediate state it would inherit after this write.
                snapshots.append(origin._successor(self, self.pid + 1).unconfirmed)

    state = ForkSnapshot(os.getpid())
    state.observe = True
    monkeypatch.setattr(origin, "_STATE", state)
    with pytest.raises(OSError, match="controlled failure"):
        with origin.sql_process_opening():
            raise OSError("controlled failure")
    assert snapshots == [1], "fork inherited neither the opening nor its failure uncertainty"


def test_fence_does_not_wait_for_an_already_admitted_connection_call(monkeypatch):
    origin = fresh(monkeypatch)
    entered, release = threading.Event(), threading.Event()

    def connector():
        with origin.sql_process_opening():
            entered.set()
            assert release.wait(15)

    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(connector)
        try:
            assert entered.wait(15)
            assert origin._STATE.opening == 1
            assert origin.seal_sql_process_openings() is True
            assert origin._STATE.opening == 1 and not pending.done()
            with pytest.raises(origin.SqlProcessOpeningsSealed):
                with origin.sql_process_opening():
                    pytest.fail("later opening bypassed the terminal fence")
        finally:
            release.set()
            pending.result(timeout=15)
    assert origin._STATE.opening == 0


def test_all_threads_are_denied_after_the_terminal_fence(monkeypatch):
    origin = fresh(monkeypatch)
    origin.seal_sql_process_openings()

    def refused(_):
        with pytest.raises(origin.SqlProcessOpeningsSealed):
            with origin.sql_process_opening():
                pytest.fail("thread bypassed the terminal fence")

    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(refused, range(32)))
    assert origin._STATE.opening == origin._STATE.unconfirmed == 0


@pytest.mark.asyncio
async def test_cancelling_the_waiter_does_not_retire_the_physical_opening(monkeypatch):
    origin = fresh(monkeypatch)
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), threading.Event()

    def connector():
        with origin.sql_process_opening():
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(15)

    with ThreadPoolExecutor(max_workers=1) as worker:
        physical = worker.submit(connector)

        async def wait():
            await asyncio.wrap_future(physical)

        waiter = asyncio.create_task(wait())
        try:
            await asyncio.wait_for(entered.wait(), 15)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert origin._STATE.opening == 1 and not physical.done()
            origin.seal_sql_process_openings()
            assert origin._STATE.opening == 1
        finally:
            release.set()
            physical.result(timeout=15)
            if not waiter.done():
                await waiter
    assert origin._STATE.opening == origin._STATE.unconfirmed == 0


def test_foreign_pid_keeps_the_fence_without_taking_an_inherited_mutex(monkeypatch):
    origin = fresh(monkeypatch)
    inherited = origin._State(os.getpid() + 1, enabled=True, sealed=True, opening=3)
    monkeypatch.setattr(origin, "_STATE", inherited)
    inherited.guard.acquire()
    try:
        with pytest.raises(origin.SqlProcessOpeningsSealed):
            with origin.sql_process_opening():
                pytest.fail("PID fallback reopened a terminal process")
        assert origin._STATE is not inherited and origin._STATE.opening == 0
    finally:
        inherited.guard.release()


@pytest.mark.parametrize("active", [False, True])
def test_real_fork_preserves_the_terminal_fence_and_renews_the_sql_name(monkeypatch, active):
    origin = fresh(monkeypatch)
    parent_name = origin.current_sql_application_name()
    opening = origin.sql_process_opening()
    if active:
        opening.__enter__()
    origin.seal_sql_process_openings()
    reader, writer = os.pipe()
    child = os.fork()
    if not child:
        os.close(reader)
        try:
            with pytest.raises(origin.SqlProcessOpeningsSealed):
                with origin.sql_process_opening():
                    os._exit(21)
            assert origin.current_sql_application_name() != parent_name
            assert origin._STATE.opening == 0
            assert origin._STATE.unconfirmed >= int(active)
            os.write(writer, b"closed")
            os._exit(0)
        except BaseException:
            os._exit(22)
    os.close(writer)
    pin = os.pidfd_open(child)
    ready = []
    try:
        ready, _, _ = select.select([reader], [], [], 15)
        assert ready and os.read(reader, 16) == b"closed"
    finally:
        os.close(reader)
        if not ready:
            import signal

            signal.pidfd_send_signal(pin, signal.SIGKILL)
        os.close(pin)
        try:
            assert os.waitpid(child, 0) == (child, 0)
        finally:
            if active:
                opening.__exit__(None, None, None)


@pytest.mark.parametrize("failure_kind", ["none", "error", "memory", "channel", "cancel"])
def test_bridge_main_fences_only_after_terminal_cleanup_even_on_early_error(
    monkeypatch, failure_kind
):
    origin = fresh(monkeypatch)
    from cli_agent_orchestrator.runtime_channel import bridge
    from cli_agent_orchestrator.utils import logging

    failure = {
        "none": None,
        "error": OSError("controlled startup error"),
        "memory": MemoryError("controlled startup error"),
        "channel": bridge.ChannelRefused("controlled channel refusal"),
        "cancel": asyncio.CancelledError("controlled startup cancellation"),
    }[failure_kind]
    order = []

    async def lifecycle():
        try:
            with origin.sql_process_opening():
                order.append("startup")
            if failure is not None:
                raise failure
        finally:
            with origin.sql_process_opening():
                order.append("funded cleanup")

    monkeypatch.setattr(bridge, "_amain", lifecycle)
    monkeypatch.setattr(logging, "setup_logging", lambda: None)
    if failure is None:
        bridge.main()
    elif failure_kind == "channel":
        with pytest.raises(SystemExit, match="controlled channel refusal"):
            bridge.main()
    else:
        with pytest.raises(type(failure)) as failed:
            bridge.main()
        assert failed.value is failure
    assert order == ["startup", "funded cleanup"]
    with pytest.raises(origin.SqlProcessOpeningsSealed):
        with origin.sql_process_opening():
            pytest.fail("Bridge exited without its terminal physical-opening fence")
