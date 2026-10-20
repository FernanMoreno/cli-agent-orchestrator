"""Native PostgreSQL contract tests for the private legacy-memory audit pool."""

from __future__ import annotations

import os
import secrets
import select
import signal
import time
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import uuid4

import psycopg
import pytest

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services.legacy_memory_audit_repository import (
    LegacyMemoryAuditConnectionManager,
    PooledAuditLeaseExpired,
    StaleMemoryAuditTarget,
    UnsupportedLibpqEnvironment,
    _legacy_memory_audit_manager,
    _LegacyMemoryAuditWorkRepository,
    _resolve_profile,
)


def _pid(connection) -> int:
    return cast(int, connection.execute("SELECT pg_backend_pid()").fetchone()[0])


@pytest.fixture
def audit_manager():
    manager = LegacyMemoryAuditConnectionManager(max_sessions=1, checkout_timeout=0.1)
    try:
        yield manager
    finally:
        manager.close()


@pytest.fixture(autouse=True)
def retire_global_target(isolated_memory_db):
    yield
    if isolated_memory_db is not None:
        _legacy_memory_audit_manager().retire_target(isolated_memory_db)


def test_private_memory_repository_reuses_one_backend_and_resets_session(
    isolated_memory_db, tmp_path
):
    from cli_agent_orchestrator.services.memory_service import MemoryService

    service = MemoryService(base_dir=tmp_path / "wiki")
    repository = service._legacy_repository()
    assert isinstance(repository, _LegacyMemoryAuditWorkRepository)
    manager = _legacy_memory_audit_manager()
    assert manager.session_count_for(isolated_memory_db) == 1

    with repository.read_snapshot() as snapshot:
        assert snapshot.read_only is True
        initialization_pid = _pid(snapshot)
    assert manager.session_count_for(isolated_memory_db) == 1

    backend_ids = [initialization_pid]
    observed_settings = []
    for index in range(2):
        with repository.audited_write_transaction() as connection:
            backend_ids.append(_pid(connection))
            row = connection.execute(
                "SELECT current_user, current_setting('search_path'), "
                "current_setting('TimeZone'), current_setting('statement_timeout'), "
                "current_setting('lock_timeout'), current_setting('plan_cache_mode')"
            ).fetchone()
            observed_settings.append(tuple(row))
            if index == 0:
                connection.execute("SET search_path TO public")
                connection.execute("SET TIME ZONE 'Pacific/Auckland'")
                connection.execute("SET statement_timeout TO '31s'")
                connection.execute("SET lock_timeout TO '11s'")
                connection.execute("SET plan_cache_mode TO force_generic_plan")

    assert len(set(backend_ids)) == 1
    assert (
        observed_settings
        == [
            (
                isolated_memory_db.user,
                f"pg_catalog, {isolated_memory_db.schema}",
                "UTC",
                "30s",
                "10s",
                "force_custom_plan",
            ),
        ]
        * 2
    )


def test_pool_lease_expires_saved_connection_raw_and_cursor(isolated_memory_db, audit_manager):
    manager = audit_manager
    resolver = lambda: isolated_memory_db
    with manager.checkout(isolated_memory_db, resolver=resolver) as connection:
        raw = connection.raw
        cursor = connection.execute("SELECT 1", prepare=True)
        assert cursor.fetchone()[0] == 1

    with pytest.raises(PooledAuditLeaseExpired):
        connection.execute("SELECT 1", prepare=True)
    with pytest.raises(PooledAuditLeaseExpired):
        raw.execute("SELECT 1")
    with pytest.raises(PooledAuditLeaseExpired):
        cursor.fetchone()


def test_immediate_preparation_survives_pool_session_reset(isolated_memory_db, audit_manager):
    from cli_agent_orchestrator.clients.postgres.migrations import catalog_manifest
    from cli_agent_orchestrator.clients.work_postgres_schema import WORK_POSTGRES_MIGRATIONS

    manager = audit_manager
    resolver = lambda: isolated_memory_db
    tables = tuple(
        sorted({table for migration in WORK_POSTGRES_MIGRATIONS for table in migration.tables})
    )
    backend_pids = []

    with manager.checkout(isolated_memory_db, resolver=resolver) as connection:
        assert catalog_manifest(connection, tables)["columns"]
        backend_pids.append(_pid(connection))
        prepared = connection.execute(
            "SELECT cardinality(parameter_types) AS parameter_count, generic_plans, "
            "custom_plans FROM pg_prepared_statements "
            "WHERE statement LIKE 'WITH target_namespace AS MATERIALIZED%'"
        ).fetchone()
        assert prepared is not None
        assert prepared["parameter_count"] == 0
        assert prepared["generic_plans"] >= 1
        assert prepared["custom_plans"] == 0

    with manager.checkout(isolated_memory_db, resolver=resolver) as connection:
        assert catalog_manifest(connection, tables)["columns"]
        backend_pids.append(_pid(connection))
        prepared = connection.execute(
            "SELECT cardinality(parameter_types) AS parameter_count, generic_plans, "
            "custom_plans FROM pg_prepared_statements "
            "WHERE statement LIKE 'WITH target_namespace AS MATERIALIZED%'"
        ).fetchone()
        assert prepared is not None
        assert prepared["parameter_count"] == 0
        assert prepared["generic_plans"] >= 2
        assert prepared["custom_plans"] == 0
    assert backend_pids[0] == backend_pids[1]


def test_prepared_runtime_role_query_survives_pool_session_reset(isolated_memory_db, audit_manager):
    from cli_agent_orchestrator.clients.postgres.migrations import verify_runtime_role

    manager = audit_manager
    resolver = lambda: isolated_memory_db
    backend_pids = []
    generic_plans = []
    for _ in range(2):
        with manager.checkout(isolated_memory_db, resolver=resolver) as connection:
            backend_pids.append(_pid(connection))
            verify_runtime_role(connection)
            prepared = connection.execute(
                "SELECT cardinality(parameter_types) AS parameter_count, generic_plans, "
                "custom_plans FROM pg_prepared_statements "
                "WHERE statement LIKE 'WITH role_flags AS MATERIALIZED%'"
            ).fetchone()
            assert prepared is not None
            assert prepared["parameter_count"] == 0
            generic_plans.append(prepared["generic_plans"])
            assert prepared["custom_plans"] == 0

    assert backend_pids[0] == backend_pids[1]
    assert generic_plans == [1, 2]


def test_pool_discards_session_after_configuration_nextset_failure(
    isolated_memory_db, audit_manager, monkeypatch
):
    from cli_agent_orchestrator.services.legacy_memory_audit_repository import (
        LegacyMemoryAuditConnectionManager,
    )

    manager = audit_manager
    resolver = lambda: isolated_memory_db
    with manager.checkout(isolated_memory_db, resolver=resolver) as connection:
        original_pid = _pid(connection)

    original_configure = LegacyMemoryAuditConnectionManager._configure_raw

    class FailingCursor:
        def __init__(self, cursor):
            self._cursor = cursor

        def nextset(self):
            raise psycopg.errors.DivisionByZero("synthetic configuration result fault")

        def __getattr__(self, name):
            return getattr(self._cursor, name)

    class FailingRaw:
        def __init__(self, raw):
            self._raw = raw

        def __getattr__(self, name):
            return getattr(self._raw, name)

        def execute(self, *args, **kwargs):
            return FailingCursor(self._raw.execute(*args, **kwargs))

    def fail_configuration_at_nextset(raw, target):
        return original_configure(FailingRaw(raw), target)

    with monkeypatch.context() as patch:
        patch.setattr(
            LegacyMemoryAuditConnectionManager,
            "_configure_raw",
            staticmethod(fail_configuration_at_nextset),
        )
        with pytest.raises(psycopg.errors.DivisionByZero, match="configuration result fault"):
            with manager.checkout(isolated_memory_db, resolver=resolver):
                pytest.fail("the failing configuration must prevent a lease")

    assert manager.session_count_for(isolated_memory_db) == 0
    with manager.checkout(isolated_memory_db, resolver=resolver) as replacement:
        assert _pid(replacement) != original_pid


def test_info_access_is_guarded_and_cursor_exhaustion_keeps_lease_reusable(
    isolated_memory_db, audit_manager
):
    manager = audit_manager
    resolver = lambda: isolated_memory_db
    with manager.checkout(isolated_memory_db, resolver=resolver) as connection:
        info = connection.raw.info
        assert info.dbname == isolated_memory_db.database
        with pytest.raises(AttributeError):
            _ = info.pgconn
        parameter_status = info.parameter_status
        rows = iter(connection.execute("SELECT 1 AS value"))
        assert next(rows)["value"] == 1
        with pytest.raises(StopIteration):
            next(rows)
        backend_pid = _pid(connection)

    with pytest.raises(PooledAuditLeaseExpired):
        _ = info.dbname
    with pytest.raises(PooledAuditLeaseExpired):
        parameter_status("server_version")
    with pytest.raises(PooledAuditLeaseExpired):
        next(rows)

    with manager.checkout(isolated_memory_db, resolver=resolver) as connection:
        assert _pid(connection) == backend_pid


def test_pool_cap_counts_retired_active_generation_until_return(isolated_memory_db, audit_manager):
    manager = audit_manager
    original = isolated_memory_db
    rotated_secret = original.secret_file.with_name("same-password-rotated-file.json")
    rotated_secret.write_text(original.secret_file.read_text(encoding="utf-8"), encoding="utf-8")
    rotated_secret.chmod(0o600)
    rotated = replace(original, secret_file=rotated_secret)
    current = [original]

    with manager.checkout(original, resolver=lambda: current[0]) as old_connection:
        old_pid = _pid(old_connection)
        current[0] = rotated
        with pytest.raises(TimeoutError):
            with manager.checkout(rotated, resolver=lambda: current[0]):
                pytest.fail("a retired checked-out session must still count toward the cap")

    with manager.checkout(rotated, resolver=lambda: current[0]) as new_connection:
        assert _pid(new_connection) != old_pid


def test_pool_refuses_to_reroute_a_stale_repository(isolated_memory_db, audit_manager):
    manager = audit_manager
    changed_target = replace(isolated_memory_db, store_id=str(uuid4()))

    with pytest.raises(StaleMemoryAuditTarget):
        with manager.checkout(isolated_memory_db, resolver=lambda: changed_target):
            pytest.fail("stale logical store identity must fail before checkout")


def test_pool_discards_a_connection_after_transaction_error(isolated_memory_db, audit_manager):
    manager = audit_manager
    resolver = lambda: isolated_memory_db
    with manager.checkout(isolated_memory_db, resolver=resolver) as connection:
        first_pid = _pid(connection)
        with pytest.raises(psycopg.errors.DivisionByZero):
            with connection.transaction():
                connection.execute("SELECT 1 / 0")

    with manager.checkout(isolated_memory_db, resolver=resolver) as replacement:
        assert _pid(replacement) != first_pid


def test_generic_work_repository_keeps_direct_connection_lifecycle(isolated_memory_db):
    from cli_agent_orchestrator.clients.postgres.connection import connect

    with connect(isolated_memory_db) as baseline:
        default_plan_cache_mode = baseline.execute(
            "SELECT current_setting('plan_cache_mode')"
        ).fetchone()[0]
    repository = WorkRepository(isolated_memory_db)
    pids = []
    for _ in range(2):
        with repository.audited_write_transaction() as connection:
            pids.append(_pid(connection))
            assert (
                connection.execute("SELECT current_setting('plan_cache_mode')").fetchone()[0]
                == default_plan_cache_mode
            )
    assert pids[0] != pids[1]


def test_short_lived_memory_services_share_the_bounded_audit_pool(isolated_memory_db, tmp_path):
    from cli_agent_orchestrator.services.memory_service import MemoryService

    pids = []
    for index in range(6):
        service = MemoryService(base_dir=tmp_path / f"wiki-{index}")
        repository = service._legacy_repository()
        with repository.audited_write_transaction() as connection:
            pids.append(_pid(connection))

    assert len(set(pids)) == 1


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork isolation requires POSIX")
def test_child_pool_reset_does_not_break_parent_lease(isolated_memory_db, audit_manager):
    manager = audit_manager
    resolver = lambda: isolated_memory_db
    read_fd, write_fd = os.pipe()
    try:
        with manager.checkout(isolated_memory_db, resolver=resolver) as parent_connection:
            parent_pid = _pid(parent_connection)
            inherited_raw = parent_connection.raw
            inherited_cursor = parent_connection.execute("SELECT 1")
            child = os.fork()
            if child == 0:
                try:
                    os.close(read_fd)
                    for operation in (
                        lambda: parent_connection.execute("SELECT 1"),
                        lambda: inherited_raw.execute("SELECT 1"),
                        lambda: inherited_cursor.fetchone(),
                    ):
                        try:
                            operation()
                        except PooledAuditLeaseExpired:
                            pass
                        else:
                            os._exit(2)
                    with manager.checkout(isolated_memory_db, resolver=resolver) as connection:
                        child_pid = _pid(connection)
                    os.write(write_fd, str(child_pid).encode("ascii"))
                    os._exit(0)
                except BaseException:
                    os._exit(1)
            os.close(write_fd)
            ready, _, _ = select.select([read_fd], [], [], 5)
            if not ready:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
                pytest.fail("child pool checkout did not complete before its deadline")
            child_pid_text = os.read(read_fd, 32)
            waited_pid, status = os.waitpid(child, os.WNOHANG)
            deadline = time.monotonic() + 5
            while waited_pid == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
                waited_pid, status = os.waitpid(child, os.WNOHANG)
            if waited_pid == 0:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
                pytest.fail("child pool process did not exit before its deadline")
            assert os.waitstatus_to_exitcode(status) == 0
            child_pid = int(child_pid_text)
            assert child_pid != parent_pid
            assert _pid(parent_connection) == parent_pid
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass


def test_initialization_read_lease_counts_against_the_same_profile_cap(
    isolated_memory_db, audit_manager
):
    repository = _LegacyMemoryAuditWorkRepository(
        isolated_memory_db,
        manager=audit_manager,
        resolver=lambda: isolated_memory_db,
    )
    other_profile = replace(isolated_memory_db, scope="peer")

    with repository.read_snapshot() as snapshot:
        assert snapshot.read_only is True
        assert audit_manager.session_count_for(isolated_memory_db) == 1
        assert audit_manager.managed_session_count == 1
        with pytest.raises(TimeoutError):
            with audit_manager.checkout(other_profile, resolver=lambda: other_profile):
                pytest.fail("initialization must consume the same physical-session cap")


def test_unrepresented_libpq_environment_is_rejected_before_checkout(
    isolated_memory_db, audit_manager, monkeypatch
):
    monkeypatch.setenv("PGOPTIONS", "-c search_path=public")
    with pytest.raises(UnsupportedLibpqEnvironment):
        with audit_manager.checkout(isolated_memory_db, resolver=lambda: isolated_memory_db):
            pytest.fail("PGOPTIONS must not alter a pooled memory-audit session")
    assert audit_manager.managed_session_count == 0


@pytest.mark.parametrize(
    "filename",
    ("root.crt", "root.crl", "postgresql.crt", "postgresql.key"),
)
def test_tls_default_file_changes_retire_the_profile(
    isolated_memory_db, tmp_path, monkeypatch, filename
):
    home = tmp_path / "profile-home"
    tls_directory = home / ".postgresql"
    tls_directory.mkdir(mode=0o700, parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    for name in ("root.crt", "root.crl", "postgresql.crt", "postgresql.key"):
        path = tls_directory / name
        path.write_bytes(b"initial " + name.encode())
        path.chmod(0o600 if name == "postgresql.key" else 0o644)
    target = replace(isolated_memory_db, sslmode="verify-full")
    process_key = secrets.token_bytes(32)
    original = _resolve_profile(target, process_key)

    changed_file = tls_directory / filename
    changed_file.write_bytes(b"rotated " + filename.encode())
    changed = _resolve_profile(target, process_key)

    assert original.profile.digest != changed.profile.digest


def test_tls_files_are_not_inspected_when_tls_is_disabled(isolated_memory_db, monkeypatch):
    monkeypatch.setattr(
        Path,
        "home",
        classmethod(lambda cls: (_ for _ in ()).throw(AssertionError("unexpected TLS lookup"))),
    )
    profile = _resolve_profile(isolated_memory_db, secrets.token_bytes(32))
    assert profile.profile.base[0] == isolated_memory_db.scope


def test_close_failure_keeps_physical_capacity_reserved(isolated_memory_db, audit_manager):
    manager = audit_manager
    with manager.checkout(isolated_memory_db, resolver=lambda: isolated_memory_db):
        pass
    profile, entry = next(iter(manager._entries.items()))
    original_raw = entry.raw

    class FailedClose:
        closed = False

        def close(self):
            raise OSError("injected close failure")

    entry.raw = cast(psycopg.Connection, FailedClose())
    try:
        with manager._condition:
            with pytest.raises(OSError, match="injected close failure"):
                manager._close_entry_locked(profile, entry)
        assert manager.managed_session_count == 1
        assert manager._entries[profile] is entry
        assert entry.retired is True
    finally:
        entry.raw = original_raw
