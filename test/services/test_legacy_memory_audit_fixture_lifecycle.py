"""Per-test PostgreSQL schemas must not evict the worker audit session."""

from __future__ import annotations


def test_sibling_schema_cleanup_preserves_worker_memory_audit_session(
    request, monkeypatch, tmp_path, isolated_memory_db
):
    """The real target fixture retires only sessions for schemas it owns."""
    from test.postgres.fixtures import postgres_targets as postgres_targets_fixture

    from cli_agent_orchestrator.services.knowledge_policy import (
        legacy_memory_access,
        require_legacy_operator,
    )
    from cli_agent_orchestrator.services.legacy_memory_audit_repository import (
        _legacy_memory_audit_manager,
    )
    from cli_agent_orchestrator.services.memory_service import MemoryService

    manager = _legacy_memory_audit_manager()
    manager.retire_target(isolated_memory_db)

    repository = MemoryService(base_dir=tmp_path / "worker-wiki")._legacy_repository()
    with repository.read_snapshot() as connection:
        worker_backend_pid = int(connection.execute("SELECT pg_backend_pid()").fetchone()[0])
    assert worker_backend_pid > 0
    assert manager.session_count_for(isolated_memory_db) == 1

    worker = request.getfixturevalue("_postgres_worker")
    sibling_root = tmp_path / "sibling"
    sibling_root.mkdir()
    sibling_targets = None
    sibling_backend_pid = None

    with monkeypatch.context() as fixture_patch:
        fixture_generator = postgres_targets_fixture.__wrapped__(
            request, fixture_patch, sibling_root, worker
        )
        try:
            sibling_targets = next(fixture_generator)
            assert sibling_targets.main.database == isolated_memory_db.database
            assert sibling_targets.main.schema != isolated_memory_db.schema

            with manager.checkout(
                sibling_targets.main, resolver=lambda: sibling_targets.main
            ) as connection:
                sibling_backend_pid = int(
                    connection.execute("SELECT pg_backend_pid()").fetchone()[0]
                )
        finally:
            fixture_generator.close()

    assert sibling_targets is not None
    assert sibling_backend_pid is not None and sibling_backend_pid > 0
    assert manager.session_count_for(sibling_targets.main) == 0
    assert manager.session_count_for(isolated_memory_db) == 1

    with legacy_memory_access(
        repository,
        require_legacy_operator(),
        "recall",
        {"case": "sibling-schema-cleanup"},
    ) as operation_id:
        with repository.read_snapshot() as connection:
            retained_backend_pid = int(connection.execute("SELECT pg_backend_pid()").fetchone()[0])

    with repository.read_snapshot() as connection:
        phases = tuple(
            row[0]
            for row in connection.execute(
                "SELECT phase FROM work_memory_access_audit "
                "WHERE operation_id=%s ORDER BY phase",
                (operation_id,),
            ).fetchall()
        )

    assert retained_backend_pid == worker_backend_pid
    assert phases == ("authorized", "completed")
