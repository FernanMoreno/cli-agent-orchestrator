"""Native PostgreSQL Work recovery guards fail closed across restore boundaries."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

pytestmark = pytest.mark.postgres


@pytest.fixture
def work_target(postgres_targets):
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

    target = postgres_targets.main
    bootstrap_scope(target, seed_work=False)
    return target


def _bootstrap_work(target):
    from cli_agent_orchestrator.clients.work_repository import WorkRepository, bootstrap_work_store

    bootstrap_work_store(target)
    repository = WorkRepository(target)
    repository.initialize()
    return repository


def test_runtime_work_store_requires_explicit_identity_bootstrap(work_target):
    from cli_agent_orchestrator.clients.work_repository import SchemaMismatch, WorkRepository

    repository = WorkRepository(work_target)
    with pytest.raises(SchemaMismatch):
        repository.initialize()

    repository = _bootstrap_work(work_target)
    with repository.connection() as connection:
        assert repository.assert_execution_allowed(connection).execution_state == "normal"


def test_imported_restore_is_bound_to_the_configured_logical_store(work_target):
    from cli_agent_orchestrator.clients.work_repository import (
        SchemaMismatch,
        WorkRepository,
    )

    repository = _bootstrap_work(work_target)
    metadata = {
        "installation_uuid": uuid4().hex,
        "bundle_digest": "a" * 64,
        "restore_receipt": "b" * 64,
    }

    with pytest.raises(SchemaMismatch, match="UUID differs"):
        repository.block_imported_restore(
            source_store_uuid=uuid4().hex,
            **metadata,
        )
    with repository.connection() as connection:
        assert repository.assert_execution_allowed(connection).execution_state == "normal"

    context = repository.block_imported_restore(
        source_store_uuid=UUID(work_target.store_id).hex,
        **metadata,
    )
    assert context.execution_state == "blocked_restore"
    assert context.bundle_digest == metadata["bundle_digest"]
    with repository.connection() as connection:
        with pytest.raises(SchemaMismatch, match="blocks execution"):
            repository.assert_execution_allowed(connection)


def test_offline_cut_accepts_only_the_repositorys_database_target(work_target):
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.work_authority import AuthorityDenied, WorkAuthority

    repository = _bootstrap_work(work_target)
    authority = WorkAuthority(repository)
    operator = auth._verified_principal(
        "https://offline-cut.test", "operator", [auth.SCOPE_ADMIN], "jwt"
    )
    lease = authority.create_offline_cut(operator, ttl_seconds=60)

    assert authority.verify_offline_cut(lease, source_database=work_target) == lease
    with pytest.raises(AuthorityDenied):
        authority.verify_offline_cut(
            lease, source_database=Path("/archive/work-database")  # type: ignore[arg-type]
        )
