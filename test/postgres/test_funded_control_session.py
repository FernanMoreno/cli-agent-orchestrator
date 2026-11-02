"""Original funded control-session behavior on the isolated native PostgreSQL fixture."""

import asyncio
import copy
import importlib
import importlib.util
import os
import pickle
import queue
import select
import signal
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from test.postgres.test_sql_root_genesis import funded, marked

import psycopg
import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable

pytestmark = pytest.mark.postgres


def api():
    name = "cli_agent_orchestrator.clients.funded_control_session"
    assert importlib.util.find_spec(name) is not None, "missing funded control session"
    return importlib.import_module(name)


def collect_fork_child(child_pid, read_fd, write_fd):
    os.close(write_fd)
    child_status = None
    try:
        ready, _, _ = select.select([read_fd], [], [], 10)
        assert ready, "fork child did not report its worker check"
        result = os.read(read_fd, 16)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            waited_pid, status = os.waitpid(child_pid, os.WNOHANG)
            if waited_pid == child_pid:
                child_status = status
                return result, status
            time.sleep(0.02)
        pytest.fail("fork child did not exit")
    finally:
        os.close(read_fd)
        if child_status is None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(child_pid, 0)


@pytest.fixture
def funded_channel(postgres_targets, monkeypatch):
    # test.conftest registers postgres.fixtures: postgres_targets -> _postgres_worker -> service.
    target = postgres_targets.main
    with marked(monkeypatch):
        with funded(target) as channel:
            yield target, channel


def test_sequential_transactions_keep_backend_and_readonly_and_rollback_are_enforced(
    funded_channel, monkeypatch
):
    from cli_agent_orchestrator.clients.postgres import connection as native

    module = api()
    target, channel = funded_channel
    with module.FundedControlSession(channel) as session:
        backend_ids = []

        def forbidden_connect(*args, **kwargs):
            raise AssertionError("funded control session opened a second PostgreSQL connection")

        with monkeypatch.context() as patch:
            patch.setattr(native.psycopg, "connect", forbidden_connect)
            with session.transaction(readonly=True) as current:
                isolation, read_only, backend = current.execute(
                    "SELECT current_setting('transaction_isolation'), "
                    "current_setting('transaction_read_only'), pg_backend_pid()"
                ).fetchone()
                backend_ids.append(backend)
                assert isolation == "repeatable read"
                assert read_only == "on"

            with pytest.raises(psycopg.DatabaseError):
                with session.transaction(readonly=True) as current:
                    backend_ids.append(current.execute("SELECT pg_backend_pid()").fetchone()[0])
                    current.execute("CREATE TEMP TABLE funded_control_readonly_denied(id INTEGER)")

            rollback_failure = ValueError("controlled body failure")
            with pytest.raises(ValueError) as caught:
                with session.transaction() as current:
                    backend_ids.append(current.execute("SELECT pg_backend_pid()").fetchone()[0])
                    raise rollback_failure
            assert caught.value is rollback_failure

            with session.transaction() as current:
                backend_ids.append(current.execute("SELECT pg_backend_pid()").fetchone()[0])

        assert len(backend_ids) == 4
        assert len(set(backend_ids)) == 1
        assert session.target is target


def test_target_mismatch_and_busy_or_nested_requests_deny_before_sql(funded_channel, monkeypatch):
    module = api()
    target, channel = funded_channel
    with module.FundedControlSession(channel) as session:

        def forbidden_sql(*args, **kwargs):
            raise AssertionError("target validation issued SQL")

        with monkeypatch.context() as patch:
            patch.setattr(channel, "execute", forbidden_sql)
            for mismatch in (
                replace(target, user=target.user + "_other"),
                replace(target, scope="auth"),
                replace(target, sslmode="require"),
            ):
                with pytest.raises(StorageAdmissionUnavailable):
                    session.check_target(mismatch)

        with session.transaction() as current:
            backend = current.raw.info.backend_pid
            # Worker checks remain valid while the outer transaction is busy.
            session.check_worker()
            with pytest.raises(StorageAdmissionUnavailable):
                session.check_target(target)
            with monkeypatch.context() as patch:
                patch.setattr(
                    channel,
                    "funded_transaction",
                    lambda: pytest.fail("nested funded transaction was attempted"),
                )
                with pytest.raises(StorageAdmissionUnavailable):
                    with session.transaction():
                        pytest.fail("nested funded transactions must be refused")
            assert current.raw.info.backend_pid == backend

        with session.transaction() as current:
            assert (
                current.execute("SELECT pg_backend_pid()").fetchone()[0]
                == current.raw.info.backend_pid
            )


def test_foreign_thread_task_and_scope_are_denied_without_poisoning(funded_channel):
    from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
    from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

    module = api()
    target, channel = funded_channel
    with module.FundedControlSession(channel) as session:
        errors: queue.SimpleQueue[BaseException] = queue.SimpleQueue()

        def foreign_thread():
            for action in (
                session.check_worker,
                lambda: session.__exit__(None, None, None),
            ):
                try:
                    action()
                except BaseException as failure:
                    errors.put(failure)

        worker = threading.Thread(target=foreign_thread)
        worker.start()
        worker.join(timeout=10)
        assert not worker.is_alive()
        failures = (errors.get_nowait(), errors.get_nowait())
        assert all(isinstance(failure, StorageAdmissionUnavailable) for failure in failures)
        assert errors.empty()

        async def foreign_task():
            with pytest.raises(StorageAdmissionUnavailable):
                session.check_worker()
            with pytest.raises(StorageAdmissionUnavailable):
                session.__exit__(None, None, None)

        asyncio.run(foreign_task())

        other_owner, other_control = DeliveryAdmission.with_storage_control(AdmissionPolicy())
        with other_control.operation(clock=lambda: 10.0):
            with pytest.raises(StorageAdmissionUnavailable):
                session.check_worker()
        assert other_owner.snapshot(now=10.0).storage_connection_loans == 0

        with session.transaction() as current:
            assert (
                current.execute("SELECT pg_backend_pid()").fetchone()[0]
                == current.raw.info.backend_pid
            )


def test_funded_transaction_entry_failure_poisons_after_manual_owner_rollback(
    funded_channel, monkeypatch
):
    from psycopg.pq import TransactionStatus

    module = api()
    _, channel = funded_channel
    failure = RuntimeError("controlled funded transaction entry failure")

    class FailingEntry:
        def __enter__(self):
            channel.execute("BEGIN")
            raise failure

        def __exit__(self, exc_type, exc, traceback):
            return False

    with module.FundedControlSession(channel) as session:
        with monkeypatch.context() as patch:
            patch.setattr(channel, "funded_transaction", lambda: FailingEntry())
            with pytest.raises(RuntimeError) as caught:
                with session.transaction():
                    pytest.fail("funded_transaction entry failure must precede the body")
        assert caught.value is failure
        assert channel.raw.info.transaction_status == TransactionStatus.INTRANS
        channel.rollback()
        assert channel.raw.info.transaction_status == TransactionStatus.IDLE
        with pytest.raises(StorageAdmissionUnavailable):
            session.check_worker()

    assert channel.raw.closed is False


def test_session_cannot_cross_owner_close_or_revive_after_its_context(funded_channel):
    module = api()
    _, channel = funded_channel
    session = module.FundedControlSession(channel)
    with pytest.raises(StorageAdmissionUnavailable):
        session.check_worker()
    with pytest.raises(StorageAdmissionUnavailable):
        with session.transaction():
            pytest.fail("a session must be entered before it can start a transaction")

    with session:
        with session.transaction() as current:
            assert current.execute("SELECT 1").fetchone()[0] == 1
    assert channel.raw.closed is False
    with pytest.raises(StorageAdmissionUnavailable):
        session.check_worker()
    with pytest.raises(StorageAdmissionUnavailable):
        with session:
            pass


def test_closed_original_owner_invalidates_an_active_session(postgres_targets, monkeypatch):
    module = api()
    target = postgres_targets.main
    with marked(monkeypatch):
        owner_context = funded(target)
        channel = owner_context.__enter__()
        try:
            with module.FundedControlSession(channel) as session:
                owner_context.__exit__(None, None, None)
                assert channel.raw.closed is True
                with pytest.raises(StorageAdmissionUnavailable):
                    session.check_worker()
        finally:
            if not channel.raw.closed:
                owner_context.__exit__(None, None, None)


def test_fork_copy_deepcopy_and_pickle_cannot_transfer_the_session(funded_channel):
    module = api()
    _, channel = funded_channel
    with module.FundedControlSession(channel) as session:
        with pytest.raises(TypeError):
            copy.copy(session)
        with pytest.raises(TypeError):
            copy.deepcopy(session)
        with pytest.raises(TypeError):
            pickle.dumps(session)

        if not hasattr(os, "fork"):
            pytest.skip("fork is unavailable on this platform")
        read_fd, write_fd = os.pipe()
        child_pid = os.fork()
        if child_pid == 0:
            os.close(read_fd)
            try:
                session.__exit__(None, None, None)
            except StorageAdmissionUnavailable:
                if session._exited:
                    os.write(write_fd, b"mutated")
                else:
                    try:
                        session.check_worker()
                    except StorageAdmissionUnavailable:
                        os.write(write_fd, b"denied")
                    else:
                        os.write(write_fd, b"accepted")
            else:
                os.write(write_fd, b"exit-accepted")
            os._exit(0)

        result, status = collect_fork_child(child_pid, read_fd, write_fd)
        assert os.waitstatus_to_exitcode(status) == 0
        assert result == b"denied"
        with session.transaction() as current:
            assert (
                current.execute("SELECT pg_backend_pid()").fetchone()[0]
                == current.raw.info.backend_pid
            )


def test_lost_commit_ack_retains_row_and_permanently_poisons_session(
    funded_channel, postgres_service, monkeypatch
):
    from cli_agent_orchestrator.clients.postgres.connection import FundedSqlCommitUncertain

    module = api()
    target, channel = funded_channel
    table_name = "funded_control_ack_probe"
    with channel.funded_transaction():
        temp_allowed = channel.execute(
            "SELECT has_database_privilege(current_user,current_database(),'TEMP')"
        ).fetchone()[0]
    if temp_allowed:
        with channel.funded_transaction():
            channel.execute(
                sql.SQL("CREATE TEMP TABLE {} (value INTEGER NOT NULL)").format(
                    sql.Identifier(table_name)
                )
            )
        probe_table = sql.Identifier("pg_temp", table_name)
    else:
        if target.migration_user is None:
            pytest.fail("the PostgreSQL fixture omitted its schema owner role")
        probe_table = sql.Identifier(target.schema, table_name)
        with postgres_service.connect_admin(target.database) as admin:
            admin.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(target.migration_user)))
            admin.execute(sql.SQL("CREATE TABLE {} (value INTEGER NOT NULL)").format(probe_table))
            admin.execute(
                sql.SQL("GRANT INSERT, SELECT ON TABLE {} TO {}").format(
                    probe_table, sql.Identifier(target.user)
                )
            )

    original = channel.funded_transaction

    @contextmanager
    def lose_commit_ack():
        with original() as current:
            yield current
        raise FundedSqlCommitUncertain("controlled lost commit acknowledgement")

    with module.FundedControlSession(channel) as session:
        with monkeypatch.context() as patch:
            patch.setattr(channel, "funded_transaction", lose_commit_ack)
            with pytest.raises(FundedSqlCommitUncertain):
                with session.transaction() as current:
                    current.execute(
                        sql.SQL("INSERT INTO {}(value) VALUES (42)").format(probe_table)
                    )
        with pytest.raises(StorageAdmissionUnavailable):
            with session.transaction():
                pytest.fail("a commit-uncertain session must be poisoned")

    assert channel.raw.closed is False
    with original() as current:
        assert (
            current.execute(sql.SQL("SELECT value FROM {}").format(probe_table)).fetchone()[0] == 42
        )


def test_failed_observation_after_confirmed_commit_is_uncertain(funded_channel, monkeypatch):
    from cli_agent_orchestrator.clients.postgres.connection import FundedSqlCommitUncertain

    module = api()
    _, channel = funded_channel
    with module.FundedControlSession(channel) as session:
        original = session._check_idle
        checks = []

        def lost_final_observation():
            original()
            checks.append(True)
            if len(checks) == 2:
                raise StorageAdmissionUnavailable()

        with monkeypatch.context() as patch:
            patch.setattr(session, "_check_idle", lost_final_observation)
            with pytest.raises(FundedSqlCommitUncertain):
                with session.transaction() as current:
                    assert current.execute("SELECT 42").fetchone()[0] == 42
        assert len(checks) == 2
        with pytest.raises(StorageAdmissionUnavailable):
            session.check_worker()
    assert channel.raw.closed is False
