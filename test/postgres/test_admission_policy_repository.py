"""Append-only admission policy persistence and PostgreSQL role boundaries."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from typing import NoReturn
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.admission_policy_repository import (
    AdmissionPolicyRepository,
    PolicyNotFound,
    PolicyRevisionConflict,
)
from cli_agent_orchestrator.clients.admission_policy_schema import (
    ADMISSION_POLICY_MIGRATIONS,
    ADMISSION_POLICY_TABLE,
)
from cli_agent_orchestrator.clients.postgres import DatabaseTarget, connect
from cli_agent_orchestrator.clients.postgres.config import ConfigurationError
from cli_agent_orchestrator.clients.postgres.migrations import (
    ComponentMigration,
    migrate_component,
    verify_component,
    verify_runtime_role,
)
from cli_agent_orchestrator.clients.postgres.registry import component_migrations
from cli_agent_orchestrator.models.admission_policy_control import (
    AdmissionPolicyRecord,
    AdmissionPolicyValues,
)

_PRINCIPAL = "principal-" + "a" * 64
_OTHER_PRINCIPAL = "principal-" + "b" * 64


def _target(scope: str = "main") -> DatabaseTarget:
    return DatabaseTarget(
        host="127.0.0.1",
        port=5432,
        database="offline-target",
        user="offline-runtime",
        schema="offline_store",
        store_id=str(uuid4()),
        secret_file=Path("/not/read/offline-secret.json"),
        scope=scope,
        migration_user="offline_owner",
        migration_secret_file=Path("/not/read/offline-owner-secret.json"),
    )


def _values(**overrides: object) -> AdmissionPolicyValues:
    return AdmissionPolicyValues(**overrides)


@pytest.fixture
def policy_target(postgres_targets):
    target = postgres_targets.main
    migrate_component(target, ADMISSION_POLICY_MIGRATIONS)
    return target


@pytest.mark.offline_database
def test_policy_history_component_is_additive_and_registered_in_main_only():
    assert len(ADMISSION_POLICY_MIGRATIONS) == 1
    migration = ADMISSION_POLICY_MIGRATIONS[0]
    assert (migration.component, migration.scope, migration.version) == (
        "admission_policy",
        "main",
        1,
    )
    assert migration.tables == (ADMISSION_POLICY_TABLE,)
    assert ADMISSION_POLICY_MIGRATIONS in component_migrations("main")
    for scope in ("auth", "peer"):
        assert not any(
            group[0].component == "admission_policy" for group in component_migrations(scope)
        )


@pytest.mark.offline_database
def test_repository_pins_canonical_profile_and_resolves_only_main_target():
    target = _target()
    repository = AdmissionPolicyRepository(profile_id=str(uuid4()), target_supplier=lambda: target)
    assert repository.resolve_target() is target
    with pytest.raises(ValueError):
        AdmissionPolicyRepository(profile_id="not-a-uuid")

    auth_repository = AdmissionPolicyRepository(
        profile_id=str(uuid4()), target_supplier=lambda: _target("auth")
    )
    with pytest.raises(ConfigurationError):
        auth_repository.resolve_target()


@pytest.mark.offline_database
def test_write_validation_and_history_bounds_fail_before_opening_sql():
    target = _target()

    def no_sql(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("invalid input must fail before opening a connection")

    repository = AdmissionPolicyRepository(
        profile_id=str(uuid4()), target_supplier=lambda: target, connect_factory=no_sql
    )
    with pytest.raises(ValueError):
        repository.append(
            target,
            policy=object(),  # type: ignore[arg-type]
            expected_revision=0,
            principal_id=_PRINCIPAL,
        )
    with pytest.raises(ValueError):
        repository.append(
            target,
            policy=_values(),
            expected_revision=True,
            principal_id=_PRINCIPAL,
        )
    with pytest.raises(ValueError):
        repository.revoke(target, expected_revision=-1, principal_id=_PRINCIPAL)
    with pytest.raises(AssertionError, match="invalid input must fail before opening a connection"):
        repository.revoke(target, expected_revision=0, principal_id=_PRINCIPAL)
    with pytest.raises(ValueError):
        repository.history(target, limit=101)
    with pytest.raises(ValueError):
        repository.history(target, before_revision=True)


@pytest.mark.postgres
def test_policy_repository_persists_revisions_and_reads_latest(policy_target):
    target = policy_target
    profile_id = str(uuid4())
    repository = AdmissionPolicyRepository(profile_id=profile_id, target_supplier=lambda: target)

    assert repository.read_latest(target) is None
    first = repository.append(
        target,
        policy=_values(),
        expected_revision=0,
        principal_id=_PRINCIPAL,
    )
    second = repository.append(
        target,
        policy=_values(memory_reserve_percent=35, max_sample_age_s=4.0),
        expected_revision=1,
        principal_id=_OTHER_PRINCIPAL,
    )

    assert first.profile_id == profile_id
    assert first.revision == 1 and first.active is True
    assert first.policy == _values()
    assert first.actor_principal_id == _PRINCIPAL
    assert first.recorded_at.tzinfo is not None
    assert second.revision == 2 and second.policy.memory_reserve_percent == 35
    assert repository.read_latest(target) == second


@pytest.mark.postgres
def test_policy_history_uses_bounded_descending_keyset_pages(policy_target):
    target = policy_target
    repository = AdmissionPolicyRepository(profile_id=str(uuid4()), target_supplier=lambda: target)
    for expected_revision in range(3):
        repository.append(
            target,
            policy=_values(memory_reserve_percent=20 + expected_revision),
            expected_revision=expected_revision,
            principal_id=_PRINCIPAL,
        )

    first_page = repository.history(target, limit=2)
    assert [entry.revision for entry in first_page.entries] == [3, 2]
    assert first_page.next_before_revision == 2
    second_page = repository.history(
        target, limit=2, before_revision=first_page.next_before_revision
    )
    assert [entry.revision for entry in second_page.entries] == [1]
    assert second_page.next_before_revision is None


@pytest.mark.postgres
def test_policy_compare_and_swap_and_revoke_keep_history(policy_target):
    target = policy_target
    profile_id = str(uuid4())
    repository = AdmissionPolicyRepository(profile_id=profile_id, target_supplier=lambda: target)
    values = _values(memory_reserve_percent=33)
    created = repository.append(
        target,
        policy=values,
        expected_revision=0,
        principal_id=_PRINCIPAL,
    )

    with pytest.raises(PolicyRevisionConflict):
        repository.append(
            target,
            policy=_values(memory_reserve_percent=44),
            expected_revision=0,
            principal_id=_OTHER_PRINCIPAL,
        )
    with pytest.raises(PolicyRevisionConflict):
        repository.revoke(target, expected_revision=0, principal_id=_OTHER_PRINCIPAL)
    assert repository.read_latest(target) == created

    revoked = repository.revoke(
        target, expected_revision=created.revision, principal_id=_OTHER_PRINCIPAL
    )
    assert revoked.revision == 2
    assert revoked.active is False
    assert revoked.policy == created.policy
    assert revoked.actor_principal_id == _OTHER_PRINCIPAL

    missing = AdmissionPolicyRepository(profile_id=str(uuid4()), target_supplier=lambda: target)
    with pytest.raises(PolicyNotFound):
        missing.revoke(target, expected_revision=0, principal_id=_PRINCIPAL)


@pytest.mark.postgres
def test_concurrent_policy_compare_and_swap_writers_create_exactly_one_revision(
    policy_target,
):
    target = policy_target
    profile_id = str(uuid4())
    repository = AdmissionPolicyRepository(profile_id=profile_id, target_supplier=lambda: target)
    start = Barrier(2)

    def append_after_barrier(policy: AdmissionPolicyValues):
        start.wait(timeout=10)
        try:
            return repository.append(
                target,
                policy=policy,
                expected_revision=0,
                principal_id=_PRINCIPAL,
            )
        except PolicyRevisionConflict as conflict:
            return conflict

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(append_after_barrier, _values(memory_reserve_percent=31)),
            executor.submit(append_after_barrier, _values(memory_reserve_percent=41)),
        )
        results = tuple(future.result(timeout=30) for future in futures)

    assert sum(type(result) is AdmissionPolicyRecord for result in results) == 1
    assert sum(type(result) is PolicyRevisionConflict for result in results) == 1
    latest = repository.read_latest(target)
    assert latest is not None and latest.revision == 1
    assert [entry.revision for entry in repository.history(target).entries] == [1]


def _later_component_migration() -> tuple[ComponentMigration, ...]:
    return (
        ComponentMigration(
            component="admission_policy_acl_regression",
            scope="main",
            version=1,
            statements=("SELECT 1",),
            tables=(),
        ),
    )


@pytest.mark.postgres
def test_policy_table_is_runtime_readonly_and_protected_against_later_grants(
    policy_target,
):
    target = policy_target
    profile_id = str(uuid4())
    repository = AdmissionPolicyRepository(profile_id=profile_id, target_supplier=lambda: target)
    repository.append(
        target,
        policy=_values(),
        expected_revision=0,
        principal_id=_PRINCIPAL,
    )
    migrate_component(target, _later_component_migration())

    with connect(target) as runtime:
        verify_runtime_role(runtime)
        verify_component(runtime, ADMISSION_POLICY_MIGRATIONS)
        assert (
            runtime.execute(
                sql.SQL("SELECT count(*) FROM {} WHERE profile_id=%s").format(
                    sql.Identifier(target.schema, ADMISSION_POLICY_TABLE)
                ),
                (profile_id,),
            ).fetchone()[0]
            == 1
        )

    statements = (
        "INSERT INTO {} DEFAULT VALUES",
        "UPDATE {} SET active=false",
        "DELETE FROM {}",
        "TRUNCATE {}",
    )
    for statement in statements:
        with connect(target) as runtime:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                runtime.execute(
                    sql.SQL(statement).format(sql.Identifier(target.schema, ADMISSION_POLICY_TABLE))
                )

    with connect(target.migration_target()) as owner:
        owner.execute(
            sql.SQL("GRANT UPDATE(active) ON {} TO {}").format(
                sql.Identifier(target.schema, ADMISSION_POLICY_TABLE),
                sql.Identifier(target.user),
            )
        )
    migrate_component(target, _later_component_migration())
    with connect(target) as runtime:
        verify_runtime_role(runtime)
        assert (
            runtime.execute(
                "SELECT has_any_column_privilege(current_user,%s,'UPDATE')",
                (f"{target.schema}.{ADMISSION_POLICY_TABLE}",),
            ).fetchone()[0]
            is False
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with connect(target) as runtime:
            runtime.execute(
                sql.SQL("UPDATE {} SET active=false").format(
                    sql.Identifier(target.schema, ADMISSION_POLICY_TABLE)
                )
            )


@pytest.mark.offline_database
def test_missing_owner_configuration_is_proven_before_any_sql_attempt():
    from cli_agent_orchestrator.clients import admission_policy_repository as policy_module

    error_type = getattr(policy_module, "PolicyWriteNotStarted", None)
    assert isinstance(error_type, type), "pre-write refusal requires an explicit proven type"
    opened = []

    def no_sql(*args, **kwargs):
        opened.append(True)
        raise AssertionError("missing owner must be rejected before SQL")

    repository = AdmissionPolicyRepository(profile_id=str(uuid4()), connect_factory=no_sql)
    with pytest.raises(error_type):
        repository.append(
            replace(_target(), migration_user=None, migration_secret_file=None),
            policy=_values(),
            expected_revision=0,
            principal_id=_PRINCIPAL,
        )
    assert opened == []


@pytest.mark.offline_database
def test_schema_refusal_from_unreturned_open_remains_uncertain():
    from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch

    def unknown_open(*args, **kwargs):
        raise SchemaMismatch("unconfirmed-open-canary")

    repository = AdmissionPolicyRepository(profile_id=str(uuid4()), connect_factory=unknown_open)
    with pytest.raises(SchemaMismatch):
        repository.append(_target(), policy=_values(), expected_revision=0, principal_id=_PRINCIPAL)


@pytest.mark.offline_database
def test_schema_refusal_with_unconfirmed_context_close_remains_uncertain():
    from contextlib import contextmanager
    from types import SimpleNamespace

    from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch

    @contextmanager
    def unknown_close(*args, **kwargs):
        yield SimpleNamespace(raw=SimpleNamespace(closed=False))

    repository = AdmissionPolicyRepository(profile_id=str(uuid4()), connect_factory=unknown_close)

    def refused(reader):
        raise SchemaMismatch("schema-prefix-canary")

    repository._verify_reader = refused
    with pytest.raises(SchemaMismatch):
        repository.append(_target(), policy=_values(), expected_revision=0, principal_id=_PRINCIPAL)


@pytest.mark.postgres
def test_confirmed_closed_schema_prefix_is_proven_not_written(policy_target):
    from cli_agent_orchestrator.clients.admission_policy_repository import PolicyWriteNotStarted
    from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch

    connections = []

    def tracked(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    repository = AdmissionPolicyRepository(profile_id=str(uuid4()), connect_factory=tracked)

    def refused(reader):
        raise SchemaMismatch("confirmed-schema-prefix-canary")

    repository._verify_reader = refused
    with pytest.raises(PolicyWriteNotStarted):
        repository.append(
            policy_target, policy=_values(), expected_revision=0, principal_id=_PRINCIPAL
        )
    assert len(connections) == 1 and connections[0].raw.closed is True
    clean = AdmissionPolicyRepository(profile_id=repository.profile_id)
    assert clean.read_latest(policy_target) is None
