"""Memory entrypoints retain the reviewed store's authority and audit boundary."""

import time

import pytest

from cli_agent_orchestrator.services.memory_service import MemoryService
from cli_agent_orchestrator.services.knowledge_revisions import KnowledgeRevisions
from test.services.test_knowledge_policy import policy_context  # noqa: F401


def proposal(service, actor):
    return service.propose_revision(
        actor,
        scope="project",
        scope_id="project",
        record_id="memory",
        expected_version=0,
        content="historical fact",
        confidence=0.5,
        fresh_until=time.time() + 600,
    )


def audit_rows(repository):
    with repository.connection() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT action,outcome,actor_id FROM work_knowledge_access_audit ORDER BY id"
            )
        ]


def test_memory_service_preserves_proposal_without_promoting_to_instructions(policy_context):
    _, repository, actor, _, _, _, policy = policy_context()
    service = MemoryService(knowledge_policy=policy)
    revision = proposal(service, actor)
    assert revision.decision == "proposed"
    assert service.instructions(actor, scope="project", scope_id="project") == ()
    assert service.read_revision(actor, "memory").content == "historical fact"
    assert [(row["action"], row["outcome"]) for row in audit_rows(repository)] == [
        ("propose", "allowed"),
        ("instructions", "allowed"),
        ("read", "allowed"),
    ]


def test_reviewed_access_without_policy_never_falls_back_to_wiki(tmp_path):
    service = MemoryService(base_dir=tmp_path)
    with pytest.raises(PermissionError):
        service.instructions(None, scope="project", scope_id="project")
    assert not list(tmp_path.iterdir())


def test_reviewed_repository_audits_reads_without_advancing_record_version(policy_context):
    _, repository, actor, _, _, _, policy = policy_context()
    service = KnowledgeRevisions(repository, authorize=policy)
    revision = proposal(service, actor)
    assert service.read_revision(actor, "memory").record_version == revision.record_version
    assert [row["action"] for row in audit_rows(repository)] == ["propose", "read"]


def test_revoked_memory_read_is_denied_and_audited_without_content(policy_context):
    _, repository, actor, _, grant, authority, policy = policy_context()
    service = MemoryService(knowledge_policy=policy)
    proposal(service, actor)
    authority.revoke(actor, grant_id=grant.id, expected_grant_revision=1, reason="revoked")
    with pytest.raises(PermissionError):
        service.read_revision(actor, "memory")
    rows = audit_rows(repository)
    assert rows[-1] == {"action": "read", "outcome": "denied", "actor_id": actor.id}
    assert "historical fact" not in str(rows)


def test_failed_audit_cannot_publish_or_return_content(policy_context):
    _, repository, actor, _, _, _, policy = policy_context()
    service = MemoryService(knowledge_policy=policy)
    proposal(service, actor)
    with repository.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER reject_audit BEFORE INSERT ON work_knowledge_access_audit "
            "BEGIN SELECT RAISE(ABORT,'audit unavailable'); END"
        )
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        service.read_revision(actor, "memory")
    with pytest.raises(sqlite3.IntegrityError):
        service.propose_revision(
            actor,
            scope="project",
            scope_id="project",
            record_id="second",
            expected_version=0,
            content="not published",
            confidence=0.5,
            fresh_until=time.time() + 600,
        )
    with repository.connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_knowledge_records").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_managed_facade_cannot_drop_to_legacy_store(policy_context, tmp_path):
    _, _, _, _, _, _, policy = policy_context()
    service = MemoryService(base_dir=tmp_path / "wiki", knowledge_policy=policy)
    with pytest.raises(PermissionError):
        await service.store("must not bypass", scope="global", key="bypass")
    assert not service.base_dir.exists()


@pytest.mark.asyncio
async def test_authenticated_process_cannot_use_implicit_local_legacy_identity(
    tmp_path, monkeypatch
):
    from cli_agent_orchestrator.security import auth

    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    service = MemoryService(base_dir=tmp_path / "wiki")
    with pytest.raises(PermissionError):
        await service.recall(scope="global")
    assert not service.base_dir.exists()


@pytest.mark.asyncio
async def test_local_legacy_store_redacts_project_secret_before_writing(
    tmp_path, isolated_memory_db
):
    service = MemoryService(base_dir=tmp_path / "wiki")
    secret = "sk-" + "a" * 48
    memory = await service.store(
        "api_key=" + secret,
        scope="project",
        key="redacted",
        terminal_context={"cwd": str(tmp_path)},
    )
    assert secret not in memory.content
    assert secret not in __import__("pathlib").Path(memory.file_path).read_text()


@pytest.mark.asyncio
async def test_existing_legacy_secret_is_redacted_on_recall_and_context(
    tmp_path, isolated_memory_db
):
    from pathlib import Path

    service = MemoryService(base_dir=tmp_path / "wiki")
    ctx = {"cwd": str(tmp_path)}
    memory = await service.store("safe-marker", scope="project", key="old", terminal_context=ctx)
    secret = "sk-" + "b" * 48
    path = Path(memory.file_path)
    path.write_text(path.read_text().replace("safe-marker", "api_key=" + secret))
    recalled = await service.recall(scope="project", terminal_context=ctx, search_mode="metadata")
    assert recalled
    assert secret not in recalled[0].content
    block = service.get_memory_context(ctx, budget_chars=10000)
    assert block and secret not in block
    assert "[REDACTED" in block


def test_curated_context_uses_same_redaction_policy(tmp_path, monkeypatch):
    from cli_agent_orchestrator.services import terminal_service
    from cli_agent_orchestrator.models.terminal import TerminalStatus

    service = MemoryService(base_dir=tmp_path)
    monkeypatch.setattr(
        service, "_get_terminal_context", lambda terminal_id: {"session_name": "session"}
    )
    monkeypatch.setattr(
        service, "_find_context_manager_terminal", lambda session: {"id": "curator"}
    )
    monkeypatch.setattr(
        terminal_service.provider_manager, "get_provider", lambda terminal_id: object()
    )
    monkeypatch.setattr(
        terminal_service.status_monitor, "get_status", lambda terminal_id: TerminalStatus.IDLE
    )
    monkeypatch.setattr(terminal_service, "send_input", lambda *args: None)
    secret = "sk-" + "c" * 48
    monkeypatch.setattr(
        terminal_service,
        "get_output",
        lambda terminal_id: "<cao-memory>api_key=" + secret + "</cao-memory>",
    )
    result = service.get_curated_memory_context("worker", "task")
    assert result and secret not in result


def test_access_audit_distinguishes_requested_record(policy_context):
    _, repository, actor, _, _, _, policy = policy_context()
    service = MemoryService(knowledge_policy=policy)
    proposal(service, actor)
    service.propose_revision(
        actor,
        scope="project",
        scope_id="project",
        record_id="another",
        expected_version=0,
        content="different",
        confidence=0.5,
        fresh_until=time.time() + 600,
    )
    service.read_revision(actor, "memory")
    service.read_revision(actor, "another")
    with repository.connection() as connection:
        rows = connection.execute(
            "SELECT target_hash FROM work_knowledge_access_audit WHERE action='read'"
        ).fetchall()
    assert len(rows) == 2 and rows[0][0] != rows[1][0]


@pytest.mark.asyncio
async def test_legacy_operations_use_durable_audit_in_metadata_database(
    tmp_path, isolated_memory_db
):
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    service = MemoryService(base_dir=tmp_path / "wiki", db_engine=isolated_memory_db)
    await service.store("local fact", scope="global", key="fact")
    assert await service.recall(scope="global", search_mode="metadata")
    service.get_memory_context({}, budget_chars=2000)
    repository = WorkRepository(isolated_memory_db.url.database)
    with repository.connection() as connection:
        rows = connection.execute("SELECT action,phase FROM work_memory_access_audit").fetchall()
    completed = {row["action"] for row in rows if row["phase"] == "completed"}
    assert {"store", "recall", "context"} <= completed
    assert "local fact" not in repr([tuple(row) for row in rows])


@pytest.mark.asyncio
async def test_legacy_audit_failure_prevents_file_write(tmp_path, isolated_memory_db):
    import sqlite3
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    repository = WorkRepository(isolated_memory_db.url.database)
    repository.initialize()
    with repository.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER reject_legacy_audit BEFORE INSERT ON work_memory_access_audit "
            "BEGIN SELECT RAISE(ABORT,'audit unavailable'); END"
        )
    service = MemoryService(base_dir=tmp_path / "wiki", db_engine=isolated_memory_db)
    with pytest.raises((sqlite3.Error, RuntimeError)):
        await service.store("must not persist", scope="global", key="blocked")
    assert not service.base_dir.exists()


@pytest.mark.asyncio
async def test_graph_cache_hits_still_pass_legacy_policy_and_audit(
    tmp_path, isolated_memory_db, monkeypatch
):
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.graph.providers.memory import MemoryGraphProvider, _CACHE
    from cli_agent_orchestrator.security import auth

    service = MemoryService(base_dir=tmp_path / "wiki", db_engine=isolated_memory_db)
    provider = MemoryGraphProvider(memory_service=service, lint_enabled=lambda: False)
    _CACHE.clear()
    try:
        await provider.project(scope="global")
        await provider.project(scope="global")
        repository = WorkRepository(isolated_memory_db.url.database)
        with repository.connection() as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM work_memory_access_audit WHERE action='graph' AND phase='completed'"
                ).fetchone()[0]
                == 2
            )
        monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
        with pytest.raises(PermissionError):
            await provider.project(scope="global")
    finally:
        _CACHE.clear()


def test_direct_archive_backend_cannot_bypass_legacy_policy(
    tmp_path, isolated_memory_db, monkeypatch
):
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.memory_archive.okf import OkfArchiveBackend

    service = MemoryService(base_dir=tmp_path / "wiki", db_engine=isolated_memory_db)
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    with pytest.raises(PermissionError):
        OkfArchiveBackend(service).export_bundle("global", None, tmp_path / "out", False, False)
    assert not (tmp_path / "out").exists()


@pytest.mark.asyncio
async def test_relationship_owner_audits_and_redacts_metadata(
    tmp_path, isolated_memory_db, monkeypatch
):
    from sqlalchemy.orm import sessionmaker
    from cli_agent_orchestrator.services import memory_relationship_service as relationships
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    monkeypatch.setattr(relationships, "SessionLocal", sessionmaker(bind=isolated_memory_db))
    memory = MemoryService(base_dir=tmp_path / "wiki", db_engine=isolated_memory_db)
    for key in ("first", "second"):
        await memory.store("safe fact", scope="global", key=key)
    service = relationships.MemoryRelationshipService()
    secret = "sk-" + "d" * 48
    relation = service.create(
        "global",
        None,
        "first",
        "second",
        "relates_to",
        "human",
        attributes={"summary": "api_key=" + secret},
    )
    assert secret not in repr(relation.attributes)
    assert service.get(relation.id).attributes == relation.attributes
    repository = WorkRepository(isolated_memory_db.url.database)
    with repository.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_memory_access_audit WHERE action='relationships' AND phase='completed'"
            ).fetchone()[0]
            >= 2
        )


@pytest.mark.asyncio
async def test_memory_graph_redacts_cached_historical_metadata(
    tmp_path, isolated_memory_db, monkeypatch
):
    from unittest.mock import AsyncMock
    from cli_agent_orchestrator.graph.models import GraphView, Node
    from cli_agent_orchestrator.graph.providers.memory import MemoryGraphProvider, _CACHE

    service = MemoryService(base_dir=tmp_path / "wiki", db_engine=isolated_memory_db)
    provider = MemoryGraphProvider(memory_service=service, lint_enabled=lambda: False)
    secret = "sk-" + "e" * 48
    historical = GraphView(
        nodes=[Node(id="safe", kind="topic", label="safe", attrs={"summary": "api_key=" + secret})],
        edges=[],
    )
    monkeypatch.setattr(provider, "_build", AsyncMock(return_value=historical))
    _CACHE.clear()
    try:
        for _ in range(2):
            result = await provider.project(scope="global")
            assert secret not in result.model_dump_json()
        assert (
            secret in historical.model_dump_json()
        ), "projection must not mutate the cached source"
    finally:
        _CACHE.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["recall", "graph", "forget"])
async def test_nested_audit_failure_never_degrades_to_success(
    tmp_path, isolated_memory_db, operation
):
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.services.knowledge_policy import LegacyMemoryAuditError
    from cli_agent_orchestrator.graph.providers.memory import MemoryGraphProvider, _CACHE

    service = MemoryService(base_dir=tmp_path / "wiki", db_engine=isolated_memory_db)
    await service.store("safe fact", scope="global", key="fact")
    repository = WorkRepository(isolated_memory_db.url.database)
    with repository.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER reject_relationship_audit BEFORE INSERT ON work_memory_access_audit "
            "WHEN NEW.action='relationships' BEGIN SELECT RAISE(ABORT,'audit unavailable'); END"
        )
    _CACHE.clear()
    try:
        with pytest.raises(LegacyMemoryAuditError):
            if operation == "recall":
                await service.recall(scope="global", search_mode="metadata", include_related=True)
            elif operation == "forget":
                await service.forget("fact", scope="global")
            else:
                await MemoryGraphProvider(
                    memory_service=service, lint_enabled=lambda: False
                ).project(scope="global")
    finally:
        _CACHE.clear()


@pytest.mark.asyncio
async def test_compaction_redacts_inputs_and_output_and_audits(
    tmp_path, isolated_memory_db, monkeypatch
):
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from cli_agent_orchestrator.services import wiki_compiler
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    service = MemoryService(base_dir=tmp_path / "wiki", db_engine=isolated_memory_db)
    memory = await service.store("historical-marker", scope="global", key="fact")
    path = Path(memory.file_path)
    secret = "sk-" + "f" * 48
    raw = path.read_text().replace("historical-marker", "api_key=" + secret)
    path.write_text(raw)
    compile_mock = AsyncMock(
        return_value=SimpleNamespace(used_llm=True, compiled_content=raw, elapsed_ms=1)
    )
    monkeypatch.setattr(wiki_compiler, "compile", compile_mock)
    monkeypatch.setattr(
        wiki_compiler, "find_related", AsyncMock(side_effect=RuntimeError("mock unavailable"))
    )
    result = await service._run_background_compile(
        scope="global",
        scope_id=None,
        key="fact",
        new_entry="api_key=" + secret,
        pre_append_content=raw,
        expected_content=raw,
        provider_hint=None,
    )
    assert result == "applied"
    assert secret not in repr(compile_mock.call_args)
    assert secret not in path.read_text()
    with WorkRepository(isolated_memory_db.url.database).connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_memory_access_audit WHERE action='compact' AND phase='completed'"
            ).fetchone()[0]
            == 1
        )


def test_archive_redacts_historical_tags_before_export(tmp_path, isolated_memory_db):
    import asyncio
    from pathlib import Path
    from cli_agent_orchestrator.services.memory_archive.okf import OkfArchiveBackend

    service = MemoryService(base_dir=tmp_path / "wiki", db_engine=isolated_memory_db)
    memory = asyncio.run(
        service.store("safe fact", scope="global", key="fact", tags="historical-marker")
    )
    secret = "AKIA" + "ABCDEFGHIJKLMNOP"
    path = Path(memory.file_path)
    path.write_text(path.read_text().replace("historical-marker", secret))
    destination = tmp_path / "export"
    OkfArchiveBackend(service).export_bundle("global", None, destination, False, False)
    assert secret not in "\n".join(file.read_text() for file in destination.glob("*.md"))


@pytest.mark.asyncio
@pytest.mark.parametrize("shared", ["neither", "base", "database"])
async def test_memory_graph_cache_isolates_persistent_owners(tmp_path, shared):
    from sqlalchemy import create_engine
    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.graph.providers.memory import MemoryGraphProvider, _CACHE
    from cli_agent_orchestrator.services.memory_relationship_service import (
        MemoryRelationshipService,
    )

    first_engine = create_engine(f"sqlite:///{tmp_path / 'first.db'}")
    second_engine = (
        first_engine
        if shared == "database"
        else create_engine(f"sqlite:///{tmp_path / 'second.db'}")
    )
    for engine in {first_engine, second_engine}:
        database.Base.metadata.create_all(engine)
    first_base = tmp_path / "first"
    second_base = first_base if shared == "base" else tmp_path / "second"
    first = MemoryService(base_dir=first_base, db_engine=first_engine)
    second = MemoryService(base_dir=second_base, db_engine=second_engine)
    for service in (first, second):
        for key in ("one", "two"):
            await service.store("safe fact", scope="global", key=key)
    if shared == "base":
        MemoryRelationshipService(memory_service=first).create(
            "global", None, "one", "two", "relates_to", "human"
        )
    else:
        await first.store("first owner only", scope="global", key="private")
    _CACHE.clear()
    try:
        first_view = await MemoryGraphProvider(first, lint_enabled=lambda: False).project()
        second_view = await MemoryGraphProvider(second, lint_enabled=lambda: False).project()
        assert first_view.meta["cached"] is False
        assert second_view.meta["cached"] is False
        if shared == "base":
            assert first_view.edges
            assert second_view.edges == []
        else:
            assert "private" in {node.id for node in first_view.nodes}
            assert "private" not in {node.id for node in second_view.nodes}
        # A new facade over the same persistent owner must reuse its entry.
        same_owner = MemoryService(base_dir=second_base / ".", db_engine=second_engine)
        reused = await MemoryGraphProvider(same_owner, lint_enabled=lambda: False).project()
        assert reused.meta["cached"] is True
        assert reused.nodes == second_view.nodes
        assert reused.edges == second_view.edges
    finally:
        _CACHE.clear()
        for engine in {first_engine, second_engine}:
            engine.dispose()
