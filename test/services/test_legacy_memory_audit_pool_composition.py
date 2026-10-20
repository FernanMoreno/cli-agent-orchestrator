"""Composition checks for the bounded legacy-memory audit connection path."""

from __future__ import annotations

import asyncio
import os
import select
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import psycopg
import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security.auth import _verified_principal
from cli_agent_orchestrator.services import knowledge_policy
from cli_agent_orchestrator.services.legacy_memory_audit_repository import (
    LegacyMemoryAuditConnectionManager,
    PooledAuditLeaseExpired,
    _legacy_memory_audit_manager,
    _LegacyMemoryAuditWorkRepository,
)

pytestmark = pytest.mark.usefixtures("isolated_memory_db")


def _pid(connection) -> int:
    return connection.execute("SELECT pg_backend_pid()").fetchone()[0]


def _audit_rows(target) -> list[dict]:
    with connect(target) as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT operation_id,actor_id,action,target_hash,phase,occurred_at "
                "FROM work_memory_access_audit ORDER BY id"
            )
        ]


@pytest.fixture(autouse=True)
def retire_global_audit_target(isolated_memory_db):
    yield
    if isolated_memory_db is not None:
        _legacy_memory_audit_manager().retire_target(isolated_memory_db)


@pytest.fixture
def composition_manager():
    manager = LegacyMemoryAuditConnectionManager(max_sessions=1, checkout_timeout=0.2)
    try:
        yield manager
    finally:
        manager.close()


def test_memory_service_keeps_21_verifications_and_20_audit_commits(
    isolated_memory_db, tmp_path, monkeypatch
):
    from cli_agent_orchestrator.services.legacy_memory_audit_repository import (
        _PooledAuditConnection,
    )
    from cli_agent_orchestrator.services.memory_service import MemoryService

    manager = _legacy_memory_audit_manager()
    manager.retire_target(isolated_memory_db)
    events: list[tuple[str, bool, bool]] = []
    commits: list[bool] = []
    original_verify = WorkRepository._verify
    original_fence = WorkRepository._lock_store_for_write
    original_commit = _PooledAuditConnection.commit

    def traced_verify(connection, *, version=None):
        events.append(("verify", connection.in_transaction, connection.read_only))
        return original_verify(connection, version=version)

    def traced_fence(connection):
        result = original_fence(connection)
        events.append(("fence", connection.in_transaction, connection.read_only))
        return result

    def traced_commit(connection):
        commits.append(connection.read_only)
        return original_commit(connection)

    monkeypatch.setattr(WorkRepository, "_verify", staticmethod(traced_verify))
    monkeypatch.setattr(WorkRepository, "_lock_store_for_write", staticmethod(traced_fence))
    monkeypatch.setattr(_PooledAuditConnection, "commit", traced_commit)

    service = MemoryService(base_dir=tmp_path / "wiki")

    async def store_ten_memories():
        for index in range(10):
            await service.store(
                content=f"Composition memory {index}",
                scope="global",
                memory_type="reference",
                key=f"composition-memory-{index:02d}",
            )

    asyncio.run(store_ten_memories())

    assert len(events) == 41
    assert events[0] == ("verify", True, True)
    assert events[1:] == [
        event for _ in range(20) for event in (("fence", True, False), ("verify", True, False))
    ]
    assert commits == [True] + [False] * 20
    rows = _audit_rows(isolated_memory_db)
    assert len(rows) == 20
    by_operation: dict[str, list[str]] = {}
    for row in rows:
        by_operation.setdefault(row["operation_id"], []).append(row["phase"])
    assert len(by_operation) == 10
    assert all(sorted(phases) == ["authorized", "completed"] for phases in by_operation.values())
    assert manager.session_count_for(isolated_memory_db) == 1


def test_concurrent_memory_service_factory_initialization_uses_shared_cap(
    isolated_memory_db, tmp_path
):
    from cli_agent_orchestrator.services.memory_service import MemoryService

    manager = _legacy_memory_audit_manager()
    manager.retire_target(isolated_memory_db)

    def initialize(index: int):
        service = MemoryService(base_dir=tmp_path / f"factory-{index}")
        repository = service._legacy_repository()
        return repository.target.identity

    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="memory-audit-factory") as pool:
        identities = list(pool.map(initialize, range(8)))

    assert identities == [isolated_memory_db.identity] * 8
    assert manager.managed_session_count <= 4
    assert manager.session_count_for(isolated_memory_db) == 1


def test_reused_private_repository_rejects_schema_drift_before_side_effect(
    isolated_memory_db, composition_manager
):
    repository = _LegacyMemoryAuditWorkRepository(
        isolated_memory_db,
        manager=composition_manager,
        resolver=lambda: isolated_memory_db,
    )
    repository.initialize()
    principal = _verified_principal(
        "urn:cao:local-operator", "operator", ["admin"], "local_operator"
    )
    with knowledge_policy.legacy_memory_access(repository, principal, "store", {"key": "warm"}):
        pass

    assert composition_manager.session_count_for(isolated_memory_db) == 1
    before = _audit_rows(isolated_memory_db)
    marker = "t055_pool_composition_drift"
    changed = False
    try:
        with connect(isolated_memory_db.migration_target()) as owner:
            with owner.transaction():
                owner.execute(
                    sql.SQL("ALTER TABLE {} ADD COLUMN {} TEXT").format(
                        sql.Identifier(isolated_memory_db.schema, "work_memory_access_audit"),
                        sql.Identifier(marker),
                    )
                )
        changed = True

        effects: list[object] = []
        with pytest.raises(knowledge_policy.LegacyMemoryAuditError) as caught:
            with knowledge_policy.legacy_memory_access(
                repository, principal, "store", {"key": "must-not-start"}
            ) as operation_id:
                effects.append(operation_id)

        assert caught.value.phase == "authorized"
        assert effects == []
        assert _audit_rows(isolated_memory_db) == before
    finally:
        if changed:
            with connect(isolated_memory_db.migration_target()) as owner:
                with owner.transaction():
                    owner.execute(
                        sql.SQL("ALTER TABLE {} DROP COLUMN {}").format(
                            sql.Identifier(isolated_memory_db.schema, "work_memory_access_audit"),
                            sql.Identifier(marker),
                        )
                    )


def test_memory_audit_does_not_commit_or_close_caller_sqlalchemy_transaction(
    isolated_memory_db, tmp_path
):
    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.services.memory_service import MemoryService

    service = MemoryService(base_dir=tmp_path / "wiki", db_engine=database.engine)
    caller = service._get_db_session()
    try:
        caller.begin()
        connection = caller.connection()
        caller_pid = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
        connection.exec_driver_sql("SET LOCAL application_name TO 't055-caller-owned'")

        asyncio.run(
            service.store(
                content="Independent audit connection",
                scope="global",
                memory_type="reference",
                key="caller-transaction-ownership",
            )
        )

        assert caller.in_transaction()
        assert caller.connection() is connection
        assert connection.exec_driver_sql("SELECT pg_backend_pid()").scalar_one() == caller_pid
        assert (
            connection.exec_driver_sql("SELECT current_setting('application_name')").scalar_one()
            == "t055-caller-owned"
        )
    finally:
        caller.rollback()
        caller.close()


def test_uncertain_authorized_commit_is_not_retried(
    isolated_memory_db, composition_manager, monkeypatch
):
    from cli_agent_orchestrator.services.legacy_memory_audit_repository import (
        _PooledAuditConnection,
    )

    repository = _LegacyMemoryAuditWorkRepository(
        isolated_memory_db,
        manager=composition_manager,
        resolver=lambda: isolated_memory_db,
    )
    repository.initialize()
    principal = _verified_principal(
        "urn:cao:local-operator", "operator", ["admin"], "local_operator"
    )
    real_commit = _PooledAuditConnection.commit
    write_commits = 0

    def commit_then_lose_ack(connection):
        nonlocal write_commits
        is_readonly = connection.read_only
        real_commit(connection)
        if not is_readonly:
            write_commits += 1
            if write_commits == 1:
                raise OSError("synthetic lost commit acknowledgement")

    monkeypatch.setattr(_PooledAuditConnection, "commit", commit_then_lose_ack)
    effects: list[object] = []

    with pytest.raises(knowledge_policy.LegacyMemoryAuditError) as caught:
        with knowledge_policy.legacy_memory_access(
            repository, principal, "store", {"key": "uncertain"}
        ) as operation_id:
            effects.append(operation_id)

    assert caught.value.phase == "authorized"
    assert write_commits == 1
    assert effects == []
    rows = _audit_rows(isolated_memory_db)
    assert len(rows) == 1
    assert rows[0]["operation_id"] == caught.value.operation_id
    assert rows[0]["phase"] == "authorized"


def test_checkout_re_resolves_profile_after_waiting_for_a_lease(
    isolated_memory_db, tmp_path, monkeypatch
):
    manager = LegacyMemoryAuditConnectionManager(max_sessions=1, checkout_timeout=2.0)
    rotated_secret = tmp_path / "rotated-runtime-secret.json"
    rotated_secret.write_bytes(isolated_memory_db.secret_file.read_bytes())
    rotated_secret.chmod(0o600)
    rotated_target = replace(isolated_memory_db, secret_file=rotated_secret)
    current_target = [isolated_memory_db]
    waiting = threading.Event()
    original_wait = manager._condition.wait

    def signal_wait(timeout=None):
        waiting.set()
        return original_wait(timeout)

    monkeypatch.setattr(manager._condition, "wait", signal_wait)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="rotation-waiter") as pool:
            with manager.checkout(
                isolated_memory_db, resolver=lambda: isolated_memory_db
            ) as holder:
                previous_pid = _pid(holder)

                def use_current_profile():
                    with manager.checkout(
                        isolated_memory_db, resolver=lambda: current_target[0]
                    ) as connection:
                        return _pid(connection)

                result = pool.submit(use_current_profile)
                assert waiting.wait(timeout=5), "checkout never waited for the occupied session"
                current_target[0] = rotated_target

            new_pid = result.result(timeout=5)
        assert new_pid != previous_pid
    finally:
        manager.close()


def test_close_failure_does_not_release_the_physical_session_cap(
    isolated_memory_db, tmp_path, monkeypatch
):
    import cli_agent_orchestrator.services.legacy_memory_audit_repository as pool_module

    manager = LegacyMemoryAuditConnectionManager(max_sessions=1, checkout_timeout=0.05)
    other_target = replace(isolated_memory_db, scope="peer")
    opened: list[object] = []
    real_connect = pool_module.psycopg.connect
    raw = None

    def track_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    try:
        with manager.checkout(isolated_memory_db, resolver=lambda: isolated_memory_db) as pooled:
            _pid(pooled)
        raw = next(iter(manager._entries.values())).raw
        real_close = type(raw).close

        def fail_close(connection):
            if connection is raw:
                raise OSError("synthetic close failure")
            return real_close(connection)

        monkeypatch.setattr(type(raw), "close", fail_close)
        monkeypatch.setattr(pool_module.psycopg, "connect", track_connect)
        try:
            manager.retire_target(isolated_memory_db)
        except OSError:
            pass

        assert manager.managed_session_count == 1
        with pytest.raises((TimeoutError, OSError)):
            with manager.checkout(other_target, resolver=lambda: other_target):
                pytest.fail("a new physical session exceeded the hard cap")
        assert opened == []
    finally:
        monkeypatch.undo()
        manager.close()
        if raw is not None and not raw.closed:
            type(raw).close(raw)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork isolation requires POSIX")
def test_fork_child_cannot_use_inherited_lease_and_parent_session_survives(
    isolated_memory_db, composition_manager
):
    manager = composition_manager
    read_fd, write_fd = os.pipe()
    try:
        with manager.checkout(isolated_memory_db, resolver=lambda: isolated_memory_db) as pooled:
            parent_pid = _pid(pooled)
            raw = pooled.raw
            cursor = pooled.execute("SELECT pg_backend_pid()")
            child_pid = os.fork()
            if child_pid == 0:
                try:
                    os.close(read_fd)

                    def rejected(call) -> bool:
                        try:
                            call()
                        except PooledAuditLeaseExpired:
                            return True
                        return False

                    connection_rejected = rejected(
                        lambda: pooled.execute("SELECT pg_backend_pid()")
                    )
                    raw_rejected = rejected(lambda: raw.execute("SELECT pg_backend_pid()"))
                    cursor_rejected = rejected(cursor.fetchone)
                    inherited_rejected = all((connection_rejected, raw_rejected, cursor_rejected))
                    with manager.checkout(
                        isolated_memory_db, resolver=lambda: isolated_memory_db
                    ) as fresh:
                        fresh_pid = _pid(fresh)
                    payload = f"{int(inherited_rejected)}:{fresh_pid}".encode("ascii")
                    os.write(write_fd, payload)
                    os._exit(0)
                except BaseException as error:
                    diagnostic = f"error:{type(error).__name__}:{error}".encode("utf-8", "replace")
                    os.write(write_fd, diagnostic[:64])
                    os._exit(1)

            os.close(write_fd)
            ready, _, _ = select.select([read_fd], [], [], 5)
            if not ready:
                os.kill(child_pid, signal.SIGKILL)
                os.waitpid(child_pid, 0)
                pytest.fail("fork child did not finish its lease checks")
            payload = os.read(read_fd, 64).decode("ascii")
            waited_pid, status = os.waitpid(child_pid, os.WNOHANG)
            deadline = time.monotonic() + 5
            while waited_pid == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
                waited_pid, status = os.waitpid(child_pid, os.WNOHANG)
            if waited_pid == 0:
                os.kill(child_pid, signal.SIGKILL)
                os.waitpid(child_pid, 0)
                pytest.fail("fork child process did not exit")
            assert os.waitstatus_to_exitcode(status) == 0, payload
            rejected_text, fresh_pid_text = payload.split(":", maxsplit=1)
            assert rejected_text == "1"
            assert int(fresh_pid_text) != parent_pid
            assert _pid(pooled) == parent_pid
    finally:
        for descriptor in (read_fd, write_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork isolation requires POSIX")
def test_child_manager_fails_closed_when_socket_detach_fails(
    isolated_memory_db, composition_manager, monkeypatch
):
    read_fd, write_fd = os.pipe()
    child_pid: int | None = None
    try:
        with composition_manager.checkout(
            isolated_memory_db, resolver=lambda: isolated_memory_db
        ) as pooled:
            parent_backend = _pid(pooled)

            parent_pid = os.getpid()
            real_dup2 = os.dup2

            def fail_dup2(source, target, *args, **kwargs):
                if os.getpid() != parent_pid:
                    raise OSError("synthetic child socket detach failure")
                return real_dup2(source, target, *args, **kwargs)

            monkeypatch.setattr(os, "dup2", fail_dup2)
            child_pid = os.fork()
            if child_pid == 0:
                try:
                    os.close(read_fd)
                    try:
                        with composition_manager.checkout(
                            isolated_memory_db, resolver=lambda: isolated_memory_db
                        ):
                            os.write(write_fd, b"opened")
                            os._exit(2)
                    except RuntimeError:
                        os.write(write_fd, b"closed")
                        os._exit(0)
                except BaseException as error:
                    diagnostic = f"error:{type(error).__name__}:{error}".encode("utf-8", "replace")
                    os.write(write_fd, diagnostic[:32])
                    os._exit(1)

            os.close(write_fd)
            ready, _, _ = select.select([read_fd], [], [], 5)
            if not ready:
                os.kill(child_pid, signal.SIGKILL)
                os.waitpid(child_pid, 0)
                child_pid = None
                pytest.fail("fork child did not report detach-failure handling")
            child_payload = os.read(read_fd, 32)
            assert child_payload == b"closed", child_payload.decode("utf-8", "replace")
            waited_pid, status = os.waitpid(child_pid, os.WNOHANG)
            deadline = time.monotonic() + 5
            while waited_pid == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
                waited_pid, status = os.waitpid(child_pid, os.WNOHANG)
            if waited_pid == 0:
                os.kill(child_pid, signal.SIGKILL)
                os.waitpid(child_pid, 0)
                child_pid = None
                pytest.fail("fork child did not exit after fail-closed checkout")
            child_pid = None
            assert os.waitstatus_to_exitcode(status) == 0
            assert _pid(pooled) == parent_backend
    finally:
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(child_pid, 0)
        for descriptor in (read_fd, write_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork isolation requires POSIX")
def test_forked_lease_pid_guard_precedes_a_parent_thread_lock(
    isolated_memory_db, composition_manager
):
    read_fd, write_fd = os.pipe()
    lock_held = threading.Event()
    release_lock = threading.Event()
    worker: threading.Thread | None = None
    child_pid: int | None = None
    try:
        with composition_manager.checkout(
            isolated_memory_db, resolver=lambda: isolated_memory_db
        ) as pooled:
            parent_backend = _pid(pooled)
            raw = pooled.raw
            cursor = pooled.execute("SELECT pg_backend_pid()")
            assert cursor.fetchone()[0] == parent_backend
            iterator = iter(pooled.execute("SELECT 1 UNION ALL SELECT 2"))
            transaction = pooled.transaction()
            transaction.__enter__()
            lease_lock = pooled._lease_state.lock

            def hold_parent_lease_lock() -> None:
                with lease_lock:
                    lock_held.set()
                    release_lock.wait(timeout=10)

            worker = threading.Thread(target=hold_parent_lease_lock, daemon=True)
            worker.start()
            assert lock_held.wait(timeout=5), "parent helper did not acquire the lease lock"

            child_pid = os.fork()
            if child_pid == 0:
                try:
                    os.close(read_fd)
                    inherited_calls = (
                        lambda: pooled.execute("SELECT pg_backend_pid()"),
                        lambda: raw.execute("SELECT pg_backend_pid()"),
                        cursor.fetchone,
                        lambda: next(iterator),
                        lambda: transaction.__exit__(
                            RuntimeError, RuntimeError("child cleanup probe"), None
                        ),
                    )
                    for call in inherited_calls:
                        try:
                            call()
                        except PooledAuditLeaseExpired:
                            continue
                        else:
                            os.write(write_fd, b"unsafe")
                            os._exit(2)
                    for cleanup in (
                        pooled._lease_state.expire,
                        pooled._lease_state.invalidate,
                        pooled._lease_state.mark_tainted,
                    ):
                        try:
                            cleanup()
                        except PooledAuditLeaseExpired:
                            pass
                        except BaseException:
                            os.write(write_fd, b"unsafe")
                            os._exit(2)
                    os.write(write_fd, b"expired")
                    os._exit(0)
                except BaseException as error:
                    diagnostic = f"error:{type(error).__name__}:{error}".encode("utf-8", "replace")
                    os.write(write_fd, diagnostic[:32])
                    os._exit(1)

            os.close(write_fd)
            ready, _, _ = select.select([read_fd], [], [], 5)
            if not ready:
                os.kill(child_pid, signal.SIGKILL)
                os.waitpid(child_pid, 0)
                child_pid = None
                pytest.fail("fork child blocked on a parent-owned lease lock")
            assert os.read(read_fd, 32) == b"expired"
            waited_pid, status = os.waitpid(child_pid, os.WNOHANG)
            deadline = time.monotonic() + 5
            while waited_pid == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
                waited_pid, status = os.waitpid(child_pid, os.WNOHANG)
            if waited_pid == 0:
                os.kill(child_pid, signal.SIGKILL)
                os.waitpid(child_pid, 0)
                child_pid = None
                pytest.fail("fork child did not exit after rejecting the inherited lease")
            child_pid = None
            assert os.waitstatus_to_exitcode(status) == 0
            release_lock.set()
            worker.join(timeout=5)
            parent_error = RuntimeError("parent cleanup probe")
            assert transaction.__exit__(RuntimeError, parent_error, None) is False
            assert not pooled.in_transaction
            assert _pid(pooled) == parent_backend
    finally:
        release_lock.set()
        if worker is not None:
            worker.join(timeout=5)
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(child_pid, 0)
        for descriptor in (read_fd, write_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass


def test_guarded_info_is_allowlisted_and_saved_methods_expire(
    isolated_memory_db, composition_manager
):
    with composition_manager.checkout(
        isolated_memory_db, resolver=lambda: isolated_memory_db
    ) as connection:
        info = connection.raw.info
        parameter_status = info.parameter_status
        cursor = connection.execute("SELECT 1")
        assert info.dbname == isolated_memory_db.database
        with pytest.raises(AttributeError):
            _ = info.pgconn
        with pytest.raises(AttributeError):
            _ = cursor.stream
        with pytest.raises(AttributeError):
            _ = cursor.copy

    with pytest.raises(PooledAuditLeaseExpired):
        parameter_status("client_encoding")
