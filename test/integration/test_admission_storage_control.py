"""Protected control credits cover real SQL and retain exact failed closes."""

from test.integration.test_admission_storage_retirement import failed_connection, live
from test.integration.test_storage_adapters import native_driver_spy
from test.services.test_admission_storage_control import protected_monitor

import pytest

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.storage_admission import (
    StorageAdmissionUnavailable,
    retry_storage_closes,
    storage_close_uncertainty,
)
from cli_agent_orchestrator.services.admission_postgres import PostgresPressureSampler

pytestmark = pytest.mark.postgres


def test_three_real_sql_stages_are_charged_before_connect(postgres_targets, monkeypatch):
    import psycopg

    f = protected_monitor()
    target = postgres_targets.main
    f.monitor._postgres = PostgresPressureSampler(
        target_supplier=lambda: target, clock=lambda: f.clock[0]
    )
    original, opened = psycopg.connect, []

    def physical(*args, **kwargs):
        status = f.owner.snapshot(now=f.clock[0])
        assert status.control_inflight == status.control_connection_loans == 1
        assert status.storage_connection_loans == 1
        assert status.committed.storage_slots == 0
        assert all(raw.closed for raw in opened)
        raw = original(*args, **kwargs)
        opened.append(raw)
        return raw

    def read(target, instance):
        with connect(target, readonly=True) as reader:
            assert reader.execute("SELECT 41 + 1").fetchone()[0] == 42
        return f.record

    f.repository.resolve_target = lambda: target
    f.repository.read_latest = read
    try:
        with native_driver_spy(monkeypatch, physical):
            assert f.monitor.refresh().published
        assert len(opened) == 3
        assert all(raw.closed for raw in opened)
        status = f.owner.snapshot(now=f.clock[0])
        assert status.inflight == status.storage_connection_loans == 0
        probe = f.monitor.diagnostic(now=f.clock[0]).postgres
        assert probe.sample.reason == "pressure_observed"
        assert probe.sample.observed_backends >= 1  # Includes the probe's own session.
        assert probe.sample.observation.available_slots == (
            probe.sample.ordinary_connection_budget
            - probe.sample.policy_reserved_slots
            - probe.sample.observed_backends
        )
    finally:
        for raw in opened:
            if not raw.closed:
                raw.close()


def test_control_failed_close_prevents_reuse_until_exact_pid_retires(postgres_targets, monkeypatch):
    f = protected_monitor()
    permit = f.monitor._storage_control
    connection = raw = None
    try:
        with pytest.raises(StorageAdmissionUnavailable):
            with permit.operation(clock=lambda: f.clock[0]):
                connection, raw, pid = failed_connection(
                    postgres_targets.main, monkeypatch, clock=lambda: f.clock[0]
                )
        assert storage_close_uncertainty()
        assert live(postgres_targets.main, pid)
        status = f.owner.snapshot(now=f.clock[0])
        assert status.control_inflight == status.control_connection_loans == 1
        assert status.storage_retirement_pending == 1
        with pytest.raises(StorageAdmissionUnavailable):
            with permit.operation(clock=lambda: f.clock[0]):
                pytest.fail("uncertain control opened another session")
        assert raw.calls == 1
        assert retry_storage_closes().clean
        assert raw.calls == 2
        assert not live(postgres_targets.main, pid)
        assert f.owner.snapshot(now=f.clock[0]).inflight == 0
        with permit.operation(clock=lambda: f.clock[0]):
            with connect(postgres_targets.main, readonly=True) as reader:
                assert reader.execute("SELECT 43").fetchone()[0] == 43
    finally:
        if raw is not None and not raw.native.closed:
            raw.native.close()
        retry_storage_closes()


def test_registered_read_waits_for_real_probe_without_second_control_session(
    postgres_targets, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    import psycopg

    f = protected_monitor()
    target = postgres_targets.main
    f.monitor._postgres = PostgresPressureSampler(
        target_supplier=lambda: target, clock=lambda: f.clock[0]
    )
    entered, release, waiting = Event(), Event(), Event()
    guard = f.monitor._refresh_guard

    class ObservedGuard:
        def acquire(self, *, blocking=True):
            return guard.acquire(blocking=blocking)

        def release(self):
            guard.release()

        def locked(self):
            return guard.locked()

        def __enter__(self):
            waiting.set()
            guard.acquire()

        def __exit__(self, *args):
            guard.release()

    f.monitor._refresh_guard = ObservedGuard()
    original, opened = psycopg.connect, []

    def physical(*args, **kwargs):
        state = f.owner.snapshot(now=f.clock[0])
        assert state.control_inflight == state.control_connection_loans == 1
        assert state.storage_connection_loans == 1
        assert all(raw.closed for raw in opened)
        raw = original(*args, **kwargs)
        opened.append(raw)
        if len(opened) == 1:
            entered.set()
            assert release.wait(15)
        return raw

    def read(target, instance):
        with connect(target, readonly=True) as reader:
            assert reader.execute("SELECT 44").fetchone()[0] == 44
        return f.record

    def registered():
        with f.monitor.authority_read():
            return f.monitor.control_call(lambda: read(target, f.binding.instance_id))

    f.repository.resolve_target = lambda: target
    f.repository.read_latest = read
    try:
        with native_driver_spy(monkeypatch, physical), ThreadPoolExecutor(max_workers=2) as workers:
            probe = workers.submit(f.monitor.refresh)
            try:
                assert entered.wait(10)
                reader = workers.submit(registered)
                assert waiting.wait(10)
                assert guard.locked()
                assert not reader.done()
                assert len(opened) == 1
            finally:
                release.set()
            assert probe.result(timeout=15).published
            generation = f.owner.generation
            assert reader.result(timeout=15) == f.record
            assert f.owner.generation == generation
        assert len(opened) == 4
        assert all(raw.closed for raw in opened)
        assert f.owner.snapshot(now=f.clock[0]).inflight == 0
    finally:
        release.set()
        for raw in opened:
            if not raw.closed:
                raw.close()
