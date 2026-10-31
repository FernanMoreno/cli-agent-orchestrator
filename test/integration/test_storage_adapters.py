"""Physical PostgreSQL boundaries share the admitted worker's storage ledger."""

from contextlib import contextmanager
from test.postgres.fixtures import make_orm_engine
from test.services.test_delivery_admission import create, observe, reserve

import pytest
from sqlalchemy import event, text

from cli_agent_orchestrator.clients.postgres import Connection, connect
from cli_agent_orchestrator.clients.storage_admission import (
    StorageAdmissionUnavailable,
    check_storage_admission,
    physical_storage_scope,
)
from cli_agent_orchestrator.services.legacy_memory_audit_repository import (
    LegacyMemoryAuditConnectionManager,
)

pytestmark = pytest.mark.postgres


@contextmanager
def native_driver_spy(monkeypatch, physical):
    from cli_agent_orchestrator.clients.postgres import connection as module

    original = module.psycopg

    class DriverPort:
        connect = staticmethod(physical)

        def __getattr__(self, name):
            return getattr(original, name)

    # Narrow module port plus lexical restoration: fixture teardown and lazy
    # imports must not retain a global psycopg.connect replacement.
    with monkeypatch.context() as patch:
        patch.setattr(module, "psycopg", DriverPort())
        yield


@contextmanager
def admitted(slots=3):
    owner = create()
    observe(owner, slots)
    lease = reserve(owner, "storage-adapter")
    assert lease.start()
    try:
        with physical_storage_scope(lease, clock=lambda: 10.0):
            yield owner, lease
    finally:
        lease.finish_after_storage()


def test_native_gate_precedes_connect_and_covers_setup_and_physical_close(
    postgres_targets, monkeypatch
):
    import psycopg

    original, raw_connections = psycopg.connect, []
    with admitted() as (owner, lease):

        def physical(*args, **kwargs):
            assert owner.snapshot(now=10.0).storage_connection_loans == 1
            raw = original(*args, **kwargs)
            raw_connections.append(raw)
            return raw

        with native_driver_spy(monkeypatch, physical):
            with connect(postgres_targets.main, readonly=True) as connection:
                assert connection.execute("SELECT 42").fetchone()[0] == 42
                assert owner.snapshot(now=10.0).storage_connection_loans == 1
        assert raw_connections[0].closed is True
        assert owner.snapshot(now=10.0).storage_connection_loans == 0
        assert owner.snapshot(now=10.0).storage_completion_credits == 1
    assert owner.snapshot(now=10.0).inflight == 0


def test_native_nested_refusal_does_not_open_and_closing_credit_survives_stop(
    postgres_targets, monkeypatch
):
    import psycopg

    original, opened = psycopg.connect, []

    def physical(*args, **kwargs):
        raw = original(*args, **kwargs)
        opened.append(raw)
        return raw

    try:
        with admitted(1) as (owner, lease), native_driver_spy(monkeypatch, physical):
            with connect(postgres_targets.main, readonly=True):
                with pytest.raises(StorageAdmissionUnavailable):
                    connect(postgres_targets.main, readonly=True)
                assert len(opened) == 1
                assert owner.snapshot(now=10.0).storage_connection_loans == 1
            owner.stop_accepting()
            with connect(postgres_targets.main, readonly=True) as closing:
                assert closing.execute("SELECT 43").fetchone()[0] == 43
                assert owner.snapshot(now=10.0).storage_connection_loans == 1
            with pytest.raises(StorageAdmissionUnavailable):
                check_storage_admission()
        assert all(raw.closed for raw in opened)
        assert owner.snapshot(now=10.0).inflight == 0
    finally:
        for raw in opened:
            if not raw.closed:
                raw.close()


def test_borrowed_native_facade_does_not_add_or_retire_another_loan(postgres_targets):
    with admitted(1) as (owner, lease):
        with connect(postgres_targets.main, readonly=True) as owned:
            borrowed = Connection.borrow(owned.raw, postgres_targets.main)
            assert borrowed.execute("SELECT 44").fetchone()[0] == 44
            assert owner.snapshot(now=10.0).storage_connection_loans == 1
            assert borrowed.raw is owned.raw and not owned.raw.closed
        assert owner.snapshot(now=10.0).storage_connection_loans == 0
    assert owner.snapshot(now=10.0).inflight == 0


def test_orm_gate_covers_cold_setup_warm_ping_and_physical_return(postgres_targets):
    engine = make_orm_engine(postgres_targets.main)
    setup, pings = [], []
    try:
        with admitted(1) as (owner, lease):

            @event.listens_for(engine, "connect")
            def configured(raw, record):
                setup.append(raw)
                assert owner.snapshot(now=10.0).storage_connection_loans == 1

            original_ping = engine.dialect.do_ping

            def ping(raw):
                assert owner.snapshot(now=10.0).storage_connection_loans == 1
                pings.append(raw)
                return original_ping(raw)

            engine.dialect.do_ping = ping
            with engine.connect() as connection:
                first = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
                assert owner.snapshot(now=10.0).storage_connection_loans == 1
            assert owner.snapshot(now=10.0).storage_connection_loans == 0
            owner.stop_accepting()
            with engine.connect() as connection:
                assert connection.execute(text("SELECT pg_backend_pid()")).scalar_one() == first
                assert owner.snapshot(now=10.0).storage_connection_loans == 1
            assert len(setup) == len(pings) == 1
            assert owner.snapshot(now=10.0).storage_connection_loans == 0
        assert owner.snapshot(now=10.0).inflight == 0
    finally:
        engine.dispose()
        for raw in setup:
            if not raw.closed:
                raw.close()


def test_orm_setup_failure_returns_loan_only_after_confirmed_close(postgres_targets):
    engine = make_orm_engine(postgres_targets.main)
    handles = []
    try:
        with admitted(1) as (owner, lease):

            @event.listens_for(engine, "connect")
            def broken_setup(raw, record):
                handles.append(raw)
                assert owner.snapshot(now=10.0).storage_connection_loans == 1
                raise RuntimeError("controlled setup failure")

            with pytest.raises(RuntimeError, match="controlled setup failure"):
                engine.connect()
            assert handles[0].closed is True
            assert owner.snapshot(now=10.0).storage_connection_loans == 0
            assert owner.snapshot(now=10.0).storage_completion_credits == 1
        assert owner.snapshot(now=10.0).inflight == 0
    finally:
        engine.dispose()
        for raw in handles:
            if not raw.closed:
                raw.close()


def test_audit_pool_holds_a_loan_through_reset_and_returns_an_idle_session(postgres_targets):
    manager = LegacyMemoryAuditConnectionManager()
    try:
        with admitted(1) as (owner, lease):
            with manager.checkout(postgres_targets.main, readonly=True) as connection:
                first = connection.execute("SELECT pg_backend_pid()").fetchone()[0]
                assert owner.snapshot(now=10.0).storage_connection_loans == 1
            assert manager.managed_session_count == 1
            assert manager.admission_slots() == manager.max_sessions == 4
            assert manager.checkout_timeout == 2.0
            assert owner.snapshot(now=10.0).storage_connection_loans == 0
            owner.stop_accepting()
            with manager.checkout(postgres_targets.main, readonly=True) as connection:
                assert connection.execute("SELECT pg_backend_pid()").fetchone()[0] == first
                assert owner.snapshot(now=10.0).storage_connection_loans == 1
            assert owner.snapshot(now=10.0).storage_connection_loans == 0
        assert owner.snapshot(now=10.0).inflight == 0
    finally:
        manager.close()


def test_denied_audit_stage_never_reserves_or_opens_a_pool_entry(postgres_targets):
    manager = LegacyMemoryAuditConnectionManager()
    try:
        with admitted(1) as (owner, lease):
            with connect(postgres_targets.main, readonly=True):
                with pytest.raises(StorageAdmissionUnavailable):
                    with manager.checkout(postgres_targets.main, readonly=True):
                        pytest.fail("denied audit stage reached application SQL")
                assert manager.managed_session_count == 0
                assert owner.snapshot(now=10.0).storage_connection_loans == 1
            assert owner.snapshot(now=10.0).storage_connection_loans == 0
        assert owner.snapshot(now=10.0).inflight == 0
    finally:
        manager.close()


def _close_in_fork(raw):
    import os
    import signal
    import time

    child = None
    try:
        child = os.fork()
        if child == 0:
            try:
                # Force the same libpq finalization as GC of inherited handles.
                # This must affect only the child's private descriptor table.
                raw.close()
                os._exit(0)
            except BaseException:
                os._exit(2)
        deadline = time.monotonic() + 5
        while True:
            waited, status = os.waitpid(child, os.WNOHANG)
            if waited:
                child = None
                assert os.waitstatus_to_exitcode(status) == 0
                break
            if time.monotonic() >= deadline:
                pytest.fail("owned fork child did not retire")
            time.sleep(0.01)
    finally:
        if child is not None:
            os.kill(child, signal.SIGKILL)
            os.waitpid(child, 0)


@pytest.mark.parametrize("kind", ["native", "orm"])
def test_child_libpq_finalization_cannot_terminate_parent_backend(postgres_targets, kind):
    engine = make_orm_engine(postgres_targets.main) if kind == "orm" else None
    connection = engine.connect() if engine is not None else connect(postgres_targets.main)
    raw = connection.connection.driver_connection if engine is not None else connection.raw
    try:
        backend = raw.execute("SELECT pg_backend_pid()").fetchone()[0]
        raw.rollback()
        _close_in_fork(raw)
        assert raw.execute("SELECT pg_backend_pid()").fetchone()[0] == backend
        raw.rollback()
    finally:
        connection.close()
        if engine is not None:
            engine.dispose()


def test_audit_open_handle_is_detached_before_publication(postgres_targets, monkeypatch):
    from cli_agent_orchestrator.services import legacy_memory_audit_repository as module

    original_driver = module.psycopg
    manager = LegacyMemoryAuditConnectionManager()
    opened, forked = [], []
    original_resolve = manager._resolve

    def physical(*args, **kwargs):
        raw = original_driver.connect(*args, **kwargs)
        opened.append(raw)
        return raw

    class DriverPort:
        connect = staticmethod(physical)

        def __getattr__(self, name):
            return getattr(original_driver, name)

    def resolve(*args, **kwargs):
        if opened and not forked:
            assert manager.managed_session_count == 1
            assert manager._entries == {} and manager._opening
            forked.append(True)
            _close_in_fork(opened[0])
        return original_resolve(*args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(module, "psycopg", DriverPort())
            patch.setattr(manager, "_resolve", resolve)
            with manager.checkout(postgres_targets.main, readonly=True) as connection:
                assert connection.execute("SELECT 42").fetchone()[0] == 42
            assert forked == [True]
    finally:
        manager.close()
        for raw in opened:
            if not raw.closed:
                raw.close()


@pytest.mark.parametrize("kind", ["audit", "orm"])
def test_unbound_pool_exhaustion_preserves_existing_timeout_contract(postgres_targets, kind):
    if kind == "audit":
        manager = LegacyMemoryAuditConnectionManager(max_sessions=1, checkout_timeout=0.01)
        try:
            with manager.checkout(postgres_targets.main):
                with pytest.raises(TimeoutError):
                    with manager.checkout(postgres_targets.main):
                        pytest.fail("exhausted legacy pool opened another session")
        finally:
            manager.close()
    else:
        import psycopg
        from sqlalchemy import create_engine
        from sqlalchemy.exc import TimeoutError as PoolTimeout

        from cli_agent_orchestrator.clients.postgres.storage_pool import StorageQueuePool

        def physical():
            return psycopg.connect(**postgres_targets.main.connection_parameters())

        engine = create_engine(
            "postgresql+psycopg://",
            creator=physical,
            poolclass=StorageQueuePool,
            pool_size=1,
            max_overflow=0,
            pool_timeout=0.01,
        )
        try:
            with engine.connect():
                with pytest.raises(PoolTimeout):
                    engine.connect()
        finally:
            engine.dispose()
