"""Existing SQL sessions survive a terminal fence; later physical attempts do not."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.services.legacy_memory_audit_repository import (
    LegacyMemoryAuditConnectionManager,
)
from cli_agent_orchestrator.utils import process_sql_origin as origin

pytestmark = pytest.mark.postgres


@pytest.fixture
def marked_process(postgres_targets, monkeypatch):
    # Restore the terminal fence before the dependent PostgreSQL fixture cleans up.
    with monkeypatch.context() as patch:
        patch.setattr(origin, "_STATE", origin._State(os.getpid()))
        patch.setenv(origin.RESOURCE_BINDING_FD_ENV, "123")
        yield origin


@pytest.mark.parametrize("path", ["native", "orm", "audit"])
def test_every_physical_path_is_refused_before_calling_the_driver(
    postgres_targets, marked_process, monkeypatch, path
):
    import psycopg

    target = postgres_targets.main
    manager = LegacyMemoryAuditConnectionManager(max_sessions=1)
    called = []

    def forbidden(**kwargs):
        called.append(True)
        raise AssertionError("terminal process reached psycopg.connect")

    marked_process.seal_sql_process_openings()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(psycopg, "connect", forbidden)
            with pytest.raises(marked_process.SqlProcessOpeningsSealed):
                if path == "native":
                    with connect(target):
                        pytest.fail("native opening bypassed the fence")
                elif path == "orm":
                    raw = database._native_orm_connection()
                    try:
                        pytest.fail("ORM opening bypassed the fence")
                    finally:
                        raw.close()
                else:
                    with manager.checkout(target, readonly=True):
                        pytest.fail("audit opening bypassed the fence")
        assert not called
        assert manager.managed_session_count == 0 and not manager._opening
        assert marked_process._STATE.opening == marked_process._STATE.unconfirmed == 0
    finally:
        manager.close()


def test_existing_native_orm_and_audit_sessions_remain_usable_after_the_fence(
    postgres_targets, marked_process
):
    from sqlalchemy import create_engine

    from cli_agent_orchestrator.clients.postgres.storage_pool import StorageQueuePool

    target = postgres_targets.main
    engine = create_engine(
        "postgresql+psycopg://",
        creator=database._native_orm_connection,
        poolclass=StorageQueuePool,
        pool_size=1,
        max_overflow=0,
        pool_pre_ping=True,
    )
    manager = LegacyMemoryAuditConnectionManager(max_sessions=1)
    try:
        with connect(target) as native:
            native_pid = native.execute("SELECT pg_backend_pid()").fetchone()[0]
            with engine.connect() as connection:
                orm_pid = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
            with manager.checkout(target, readonly=True) as connection:
                audit_pid = connection.execute("SELECT pg_backend_pid()").fetchone()[0]
            assert len({native_pid, orm_pid, audit_pid}) == 3
            marked_process.seal_sql_process_openings()
            assert native.execute("SELECT pg_backend_pid(),42").fetchone() == (native_pid, 42)
            with engine.connect() as connection:
                assert connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one() == orm_pid
                assert connection.exec_driver_sql("SELECT 42").scalar_one() == 42
            with manager.checkout(target, readonly=True) as connection:
                assert connection.execute("SELECT pg_backend_pid(),42").fetchone() == (
                    audit_pid,
                    42,
                )
            assert engine.pool.checkedout() == 0
            engine.dispose()
            with pytest.raises(marked_process.SqlProcessOpeningsSealed):
                with engine.connect():
                    pytest.fail("empty pool bypassed the terminal fence")
            assert engine.pool.checkedout() == 0
    finally:
        manager.close()
        engine.dispose()


def test_an_attempt_admitted_before_the_fence_can_open_tcp_afterward(
    postgres_targets, marked_process, monkeypatch
):
    import psycopg

    target = postgres_targets.main
    original = psycopg.connect
    entered, release = Event(), Event()

    def delayed(**kwargs):
        entered.set()
        assert release.wait(15)
        return original(**kwargs)

    def worker():
        with connect(target) as connection:
            return connection.execute("SELECT pg_backend_pid()").fetchone()[0]

    with monkeypatch.context() as patch:
        patch.setattr(psycopg, "connect", delayed)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(worker)
            try:
                assert entered.wait(15)
                assert marked_process._STATE.opening == 1
                marked_process.seal_sql_process_openings()
                assert not pending.done() and marked_process._STATE.opening == 1
                with pytest.raises(marked_process.SqlProcessOpeningsSealed):
                    with connect(target):
                        pytest.fail("later attempt reached the connector")
            finally:
                release.set()
                backend_pid = pending.result(timeout=15)
    assert backend_pid > 0 and marked_process._STATE.opening == 0
