"""Shutdown owns exact failed closes; a borrowed facade owns no native handle."""

import asyncio
import time
from test.integration.test_storage_adapters import native_driver_spy
from test.services.test_admission_monitor import fixture
from threading import Event

import pytest

from cli_agent_orchestrator.clients.postgres import Connection, connect
from cli_agent_orchestrator.clients.storage_admission import (
    physical_storage_scope,
    retry_storage_closes,
    storage_close_tracks_handle,
    storage_close_uncertainty,
)
from cli_agent_orchestrator.services.inbox_admission_runtime import (
    AdmissionRuntimeUnavailable,
    InboxAdmissionRuntime,
)
from cli_agent_orchestrator.services.inbox_service import InboxService

pytestmark = pytest.mark.postgres


class CloseFault:
    def __init__(self, raw, *, allow=True, block=False, on_retry=lambda: None):
        self.native = raw
        self.allow = allow
        self.calls = 0
        self.release = Event()
        if not block:
            self.release.set()
        self.on_retry = on_retry

    def __getattr__(self, name):
        return getattr(self.native, name)

    def close(self):
        self.calls += 1
        if self.calls == 1:
            raise OSError("injected first physical close failure")
        self.on_retry()
        assert self.release.wait(15), "physical close retry was not released"
        if not self.allow:
            raise OSError("injected persistent physical close failure")
        self.native.close()


def live(target, pid):
    with connect(target, readonly=True) as witness:
        return witness.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=%s)", (pid,)
        ).fetchone()[0]


def failed_connection(
    target,
    monkeypatch,
    *,
    lease=None,
    block=False,
    allow=True,
    on_retry=lambda: None,
    clock=time.monotonic,
):
    import psycopg

    original = psycopg.connect
    raw = []

    def physical(*args, **kwargs):
        value = CloseFault(original(*args, **kwargs), allow=allow, block=block, on_retry=on_retry)
        raw.append(value)
        return value

    with native_driver_spy(monkeypatch, physical):
        with physical_storage_scope(lease, clock=clock):
            connection = connect(target)
            pid = connection.execute("SELECT pg_backend_pid()").fetchone()[0]
            with pytest.raises(OSError, match="first physical"):
                connection.close()
    return connection, raw[0], pid


async def bound_runtime():
    f = fixture()
    value = InboxAdmissionRuntime(
        InboxService(clock=lambda: f.clock[0]),
        monitor=f.monitor,
        bootstrap=f.bootstrap,
        clock=lambda: f.clock[0],
        drain_timeout_s=10,
    )
    await value.start()
    runner = f.monitor._runner
    if runner is not None:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        f.monitor._runner = None
    if f.monitor._physical is not None:
        await asyncio.gather(f.monitor._physical, return_exceptions=True)
        f.monitor._physical = None
    for _ in range(3):
        f.clock[0] += 1.0
        f.monitor.refresh()
    # The exact same clock owns later loans; no future/stale observation is used.
    assert f.owner.snapshot(now=f.clock[0]).available_slots > 0
    return value, f


async def retry_entered_or_stop(task, entered):
    waiter = asyncio.create_task(entered.wait())
    try:
        done, _ = await asyncio.wait(
            {task, waiter}, timeout=15, return_when=asyncio.FIRST_COMPLETED
        )
        assert waiter in done, "runtime finished before attempting its pending physical close"
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


def test_borrowed_close_preserves_native_owner_and_does_not_register_it(postgres_targets):
    owned = connect(postgres_targets.main)
    try:
        borrowed = Connection.borrow(owned.raw, postgres_targets.main)
        borrowed.close()
        assert owned.raw.closed is False
        assert not storage_close_tracks_handle(owned.raw)
        assert owned.execute("SELECT 51").fetchone()[0] == 51
    finally:
        owned.close()


def test_owned_loanless_close_is_retained_and_retries_the_same_backend(
    postgres_targets, monkeypatch
):
    assert not storage_close_uncertainty()
    connection, raw, pid = failed_connection(postgres_targets.main, monkeypatch)
    try:
        assert live(postgres_targets.main, pid)
        assert storage_close_tracks_handle(raw)
        result = retry_storage_closes()
        assert result.clean and raw.calls == 2
        assert raw.native.closed is True
        assert not live(postgres_targets.main, pid)
    finally:
        raw.allow = True
        raw.release.set()
        raw.native.close()
        assert retry_storage_closes().clean


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.asyncio
async def test_runtime_joins_exact_close_retry_before_retirement(
    postgres_targets, monkeypatch, cancel
):
    assert not storage_close_uncertainty()
    value, f = await bound_runtime()
    lease = f.owner.try_reserve("owned-close", now=f.clock[0], storage_slots=2).reservation
    assert lease is not None and lease.start()
    entered = asyncio.Event()
    loop = asyncio.get_running_loop()
    connection, raw, pid = failed_connection(
        postgres_targets.main,
        monkeypatch,
        lease=lease,
        block=True,
        on_retry=lambda: loop.call_soon_threadsafe(entered.set),
        clock=lambda: f.clock[0],
    )
    assert not lease.finish_after_storage()
    stopped = asyncio.create_task(value.stop())
    try:
        await retry_entered_or_stop(stopped, entered)
        assert live(postgres_targets.main, pid)
        assert not value.retired
        assert value.inbox._admission is f.owner
        assert f.owner.snapshot(now=f.clock[0]).storage_connection_loans == 1
        if cancel:
            stopped.cancel()
            await asyncio.sleep(0)
            stopped.cancel()
            await asyncio.sleep(0)
            assert not stopped.done()
            assert not value.retired
        raw.release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(stopped), 15)
            assert not value.retired
            await value.stop()
        else:
            await asyncio.wait_for(asyncio.shield(stopped), 15)
        assert raw.calls == 2
        assert raw.native.closed is True
        assert not live(postgres_targets.main, pid)
        assert value.retired
        assert f.owner.snapshot(now=f.clock[0]).inflight == 0
        assert not storage_close_uncertainty()
    finally:
        raw.allow = True
        raw.release.set()
        await asyncio.gather(stopped, return_exceptions=True)
        raw.native.close()
        assert retry_storage_closes().clean
        await value.stop()


@pytest.mark.parametrize("admitted", [False, True])
@pytest.mark.asyncio
async def test_persistent_close_keeps_binding_even_without_a_loan(
    postgres_targets, monkeypatch, admitted
):
    assert not storage_close_uncertainty()
    value, f = await bound_runtime()
    lease = (
        f.owner.try_reserve("owned-close", now=f.clock[0], storage_slots=2).reservation
        if admitted
        else None
    )
    if lease is not None:
        assert lease.start()
    connection, raw, pid = failed_connection(
        postgres_targets.main, monkeypatch, lease=lease, allow=False, clock=lambda: f.clock[0]
    )
    if lease is not None:
        assert not lease.finish_after_storage()
    try:
        with pytest.raises(AdmissionRuntimeUnavailable):
            await value.stop()
        assert raw.calls == 2
        assert not value.retired
        assert live(postgres_targets.main, pid)
        assert storage_close_uncertainty()
        with pytest.raises(RuntimeError, match="retir"):
            value.inbox.bind_admission(None)
        raw.allow = True
        await value.stop()
        assert raw.calls == 3
        assert raw.native.closed is True
        assert not live(postgres_targets.main, pid)
        assert value.retired
    finally:
        raw.allow = True
        raw.release.set()
        raw.native.close()
        assert retry_storage_closes().clean
        await value.stop()


def test_pressure_probe_retirement_has_one_physical_retry_and_exact_pid_witness(
    postgres_targets, monkeypatch
):
    import psycopg

    from cli_agent_orchestrator.services.admission_postgres import PostgresPressureSampler

    assert not storage_close_uncertainty()
    original = psycopg.connect
    opened = []

    def physical(*args, **kwargs):
        raw = CloseFault(original(*args, **kwargs))
        opened.append(raw)
        return raw

    sampler = PostgresPressureSampler(target_supplier=lambda: postgres_targets.main)
    try:
        with native_driver_spy(monkeypatch, physical):
            assert sampler.sample().reason == "probe_cleanup_unconfirmed"
        raw = opened[0]
        pid = raw.native.info.backend_pid
        assert storage_close_tracks_handle(raw)
        assert live(postgres_targets.main, pid)
        assert not sampler.retire_owned()
        assert raw.calls == 1
        assert retry_storage_closes().clean
        assert raw.calls == 2
        assert sampler.retire_owned()
        assert raw.calls == 2 and len(opened) == 1
        assert not live(postgres_targets.main, pid)
    finally:
        for raw in opened:
            raw.allow = True
            raw.release.set()
            raw.native.close()
        assert retry_storage_closes().clean
