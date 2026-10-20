"""Native vault schema, immutable migration proof and source-kind isolation.

Historical database layouts are not part of the active PostgreSQL schema contract.
Runtime PostgreSQL verification never rebuilds a caller's tables.
"""

import ast
import inspect
import textwrap
import uuid
from datetime import datetime, timezone
from pathlib import Path
from test.postgres.orm import configured_orm_engine

import pytest
from sqlalchemy import inspect as inspect_schema
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database as db_mod
from cli_agent_orchestrator.clients.database import (
    VAULT_NOTE_SCOPE_ID_SENTINEL,
    MemoryMetadataModel,
    VaultExclusionModel,
    VaultNoteModel,
)
from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch
from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope


@pytest.fixture
def isolated_db(isolated_memory_db, monkeypatch):
    engine = configured_orm_engine()
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "SessionLocal", sessionmaker(bind=engine))
    try:
        yield isolated_memory_db, engine
    finally:
        engine.dispose()


def test_fresh_native_vault_schema_and_indexes(isolated_db):
    target, engine = isolated_db
    db_mod.init_db()
    metadata = inspect_schema(engine)
    assert {"vault_note", "vault_exclusion", "vault_migration_receipt"} <= set(
        metadata.get_table_names(schema=target.schema)
    )
    columns = {
        row["name"]: row for row in metadata.get_columns("memory_metadata", schema=target.schema)
    }
    assert columns["source_kind"]["nullable"] is False
    assert columns["related_keys"]["nullable"] is True
    assert {"idx_memory_scope", "idx_memory_updated", "idx_memory_type"} <= {
        row["name"] for row in metadata.get_indexes("memory_metadata", schema=target.schema)
    }


@pytest.mark.parametrize("scope_id", [None, "project-id"])
def test_native_source_kind_uniqueness_preserves_distinct_backends(isolated_db, scope_id):
    _, engine = isolated_db

    def insert(kind):
        with sessionmaker(bind=engine)() as session:
            session.add(
                MemoryMetadataModel(
                    id=str(uuid.uuid4()),
                    key="same",
                    memory_type="project",
                    scope="global" if scope_id is None else "project",
                    scope_id=scope_id,
                    source_kind=kind,
                    file_path=f"/{kind}.md",
                    tags="",
                )
            )
            session.commit()

    insert("native")
    insert("vault")
    with pytest.raises(IntegrityError):
        insert("native")
    with pytest.raises(IntegrityError):
        insert("vault")
    with sessionmaker(bind=engine)() as session:
        assert session.query(MemoryMetadataModel).count() == 2


def test_explicit_bootstrap_preserves_max_valid_related_keys_and_metadata(isolated_db):
    target, engine = isolated_db
    content = "x" * 1023
    with sessionmaker(bind=engine)() as session:
        session.add(
            MemoryMetadataModel(
                id="survivor",
                key="survivor",
                memory_type="project",
                scope="global",
                scope_id=None,
                file_path="/survivor.md",
                tags="",
                related_keys=content,
            )
        )
        session.commit()
    bootstrap_scope(target)
    bootstrap_scope(target)
    with sessionmaker(bind=engine)() as session:
        row = session.get(MemoryMetadataModel, "survivor")
        assert row.related_keys == content and row.source_kind == "native"


def test_fresh_native_schema_rejects_related_keys_over_legacy_limit(isolated_db):
    _, engine = isolated_db
    session = sessionmaker(bind=engine)()
    try:
        session.add(
            MemoryMetadataModel(
                id="oversized-related-keys",
                key="oversized-related-keys",
                memory_type="project",
                scope="global",
                scope_id=None,
                file_path="/oversized.md",
                tags="",
                related_keys="x" * 1024,
            )
        )
        with pytest.raises(IntegrityError, match="ck_related_keys_length"):
            session.commit()
        session.rollback()
    finally:
        session.close()


@pytest.mark.parametrize("table", ["vault_note", "vault_exclusion", "vault_migration_receipt"])
def test_native_vault_tables_have_declared_nullable_provenance(isolated_db, table):
    target, engine = isolated_db
    model = db_mod.Base.metadata.tables[table]
    columns = {
        row["name"]: row for row in inspect_schema(engine).get_columns(table, schema=target.schema)
    }
    assert set(columns) == set(model.columns.keys())
    for column in model.columns:
        assert columns[column.name]["nullable"] == column.nullable


def test_runtime_rejects_damaged_vault_schema_without_repair(isolated_db):
    target, engine = isolated_db
    with connect(target.migration_target()) as owner:
        owner.execute("ALTER TABLE vault_note DROP COLUMN vault_relpath CASCADE")
    with pytest.raises(SchemaMismatch):
        db_mod.init_db()
    assert "vault_relpath" not in {
        row["name"]
        for row in inspect_schema(engine).get_columns("vault_note", schema=target.schema)
    }


def _insert(engine, *, key: str, source_kind: str) -> None:
    with sessionmaker(bind=engine)() as session:
        session.add(
            MemoryMetadataModel(
                id=str(uuid.uuid4()),
                key=key,
                memory_type="project",
                scope="project",
                scope_id="project-id",
                source_kind=source_kind,
                file_path=f"/{source_kind}-{key}.md",
                tags="",
            )
        )
        session.commit()


def test_widened_constraint_allows_backends_but_rejects_native_duplicates(isolated_db):
    target, engine = isolated_db
    db_mod.init_db()

    _insert(engine, key="shared", source_kind="native")
    _insert(engine, key="shared", source_kind="vault")

    with pytest.raises(IntegrityError):
        _insert(engine, key="shared", source_kind="native")


def test_vault_note_global_identity_uses_non_null_scope_id_sentinel(isolated_db):
    target, engine = isolated_db
    db_mod.init_db()

    with sessionmaker(bind=engine)() as session:
        session.add(
            VaultNoteModel(
                note_uid="first",
                vault_id="primary",
                scope="global",
                scope_id=VAULT_NOTE_SCOPE_ID_SENTINEL,
                cao_key="glossary",
                vault_relpath="Reference/Glossary.md",
                managed=False,
                status="indexed",
            )
        )
        session.commit()

    with pytest.raises(IntegrityError):
        with sessionmaker(bind=engine)() as session:
            session.add(
                VaultNoteModel(
                    note_uid="second",
                    vault_id="primary",
                    scope="global",
                    scope_id=VAULT_NOTE_SCOPE_ID_SENTINEL,
                    cao_key="glossary",
                    vault_relpath="Reference/Glossary-duplicate.md",
                    managed=False,
                    status="indexed",
                )
            )
            session.commit()


def _has_source_kind_equality(owner) -> bool:
    tree = ast.parse(textwrap.dedent(inspect.getsource(owner)))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not any(isinstance(op, ast.Eq) for op in node.ops):
            continue
        operands = [node.left, *node.comparators]
        if any(
            isinstance(operand, ast.Attribute) and operand.attr == "source_kind"
            for operand in operands
        ):
            return True
    return False


def test_all_key_and_scope_queries_filter_source_kind():
    from cli_agent_orchestrator.services import (
        memory_reconciliation,
        memory_relationship_service,
        memory_service,
        promotion_service,
        wiki_healer,
        wiki_lint,
    )

    # `_upsert_metadata` is a write-only INSERT .. ON CONFLICT operation on
    # this source tree, so it has no read predicate for this AST oracle. The
    # PostgreSQL regression below exercises its source-kind conflict identity.
    query_owners = (
        memory_service.MemoryService._delete_metadata,
        memory_service.MemoryService.compact,
        memory_service.MemoryService._candidate_keys_for_topic,
        memory_service.MemoryService._related_keys_lookup,
        memory_service.MemoryService._increment_access_count,
        memory_reconciliation.MemoryReconciliationService._load_rows,
        memory_reconciliation.MemoryReconciliationService._repair_metadata,
        memory_relationship_service.MemoryRelationshipService._assert_endpoint_exists,
        memory_relationship_service.MemoryRelationshipService._source_updated_map,
        memory_service.MemoryService._assert_no_cross_tier_collision,
        wiki_healer._row_exists,
        wiki_healer._delete_row,
        wiki_healer._heal_contradiction,
        wiki_healer._heal_poison,
        promotion_service.PromotionService.plan,
        wiki_lint._run_lint,
    )

    for owner in query_owners:
        assert _has_source_kind_equality(owner), owner.__qualname__

    enrich_source = inspect.getsource(memory_service.MemoryService._enrich_access_counts)
    assert "r.source_kind" in enrich_source
    assert 'getattr(m, "source_kind", "native")' in inspect.getsource(
        memory_service.MemoryService._identity
    )


@pytest.mark.parametrize("scope_id", [None, "project-id"])
def test_core_upsert_keeps_source_kind_identities_independent(isolated_db, tmp_path, scope_id):
    from cli_agent_orchestrator.services.memory_service import MemoryService

    _, engine = isolated_db
    service = MemoryService(base_dir=tmp_path / "metadata-source-kind")
    key = "shared-source-kind-" + uuid.uuid4().hex
    scope = "global" if scope_id is None else "project"
    kinds = ("native", "vault")
    first_compile = datetime(2024, 2, 3, tzinfo=timezone.utc)
    second_compile = datetime(2025, 4, 5, tzinfo=timezone.utc)
    access_time = datetime(2023, 6, 7, tzinfo=timezone.utc)

    def read_rows(session):
        return {
            row.source_kind: row
            for row in session.query(MemoryMetadataModel)
            .filter_by(key=key, scope=scope, scope_id=scope_id)
            .all()
        }

    for kind in kinds:
        source_kind_option = {} if kind == "native" else {"source_kind": kind}
        service._upsert_metadata(
            key,
            "project",
            scope,
            scope_id,
            f"/{kind}-initial.md",
            f"{kind}-initial-tags",
            f"{kind}-initial-provider",
            f"{kind}-initial-terminal",
            10,
            last_compiled_at=first_compile,
            related_keys=f"{kind}-related",
            **source_kind_option,
        )

    with sessionmaker(bind=engine)() as session:
        rows = read_rows(session)
        assert set(rows) == set(kinds)
        assert len(rows) == 2
        saved_identity = {}
        for kind, row in rows.items():
            assert row.file_path == f"/{kind}-initial.md"
            assert row.tags == f"{kind}-initial-tags"
            assert row.token_estimate == 10
            assert row.source_provider == f"{kind}-initial-provider"
            assert row.source_terminal_id == f"{kind}-initial-terminal"
            assert row.created_at is not None and row.updated_at is not None
            assert row.access_count == 0 and row.last_accessed_at is None
            assert row.last_compiled_at == first_compile
            assert row.related_keys == f"{kind}-related"
            saved_identity[kind] = (row.id, row.created_at)
            row.access_count = 20 if kind == "native" else 30
            row.last_accessed_at = access_time
        session.commit()

    for kind in kinds:
        service._upsert_metadata(
            key,
            "project",
            scope,
            scope_id,
            f"/{kind}-preserved.md",
            f"{kind}-preserved-tags",
            "ignored-provider",
            "ignored-terminal",
            11,
            preserve_provenance=True,
            source_kind=kind,
        )

    with sessionmaker(bind=engine)() as session:
        rows = read_rows(session)
        assert set(rows) == set(kinds)
        for kind, row in rows.items():
            assert (row.id, row.created_at) == saved_identity[kind]
            assert row.file_path == f"/{kind}-preserved.md"
            assert row.tags == f"{kind}-preserved-tags"
            assert row.token_estimate == 11
            assert row.source_provider == f"{kind}-initial-provider"
            assert row.source_terminal_id == f"{kind}-initial-terminal"
            assert row.last_compiled_at == first_compile
            assert row.related_keys == f"{kind}-related"
            assert row.updated_at > first_compile
            assert row.access_count == (20 if kind == "native" else 30)
            assert row.last_accessed_at == access_time

    for kind in kinds:
        service._upsert_metadata(
            key,
            "project",
            scope,
            scope_id,
            f"/{kind}-compiled.md",
            f"{kind}-compiled-tags",
            f"{kind}-latest-provider",
            f"{kind}-latest-terminal",
            12,
            last_compiled_at=second_compile,
            related_keys="",
            source_kind=kind,
        )

    with sessionmaker(bind=engine)() as session:
        rows = read_rows(session)
        assert set(rows) == set(kinds)
        for kind, row in rows.items():
            assert (row.id, row.created_at) == saved_identity[kind]
            assert row.file_path == f"/{kind}-compiled.md"
            assert row.tags == f"{kind}-compiled-tags"
            assert row.token_estimate == 12
            assert row.source_provider == f"{kind}-latest-provider"
            assert row.source_terminal_id == f"{kind}-latest-terminal"
            assert row.updated_at == second_compile
            assert row.last_compiled_at == second_compile
            assert row.related_keys == ""
            assert row.access_count == (20 if kind == "native" else 30)
            assert row.last_accessed_at == access_time

    for target_source_kind, other_source_kind in (
        ("native", "vault"),
        ("vault", "native"),
    ):
        with pytest.raises(ValueError, match=f"already exists in the {other_source_kind} store"):
            service._assert_no_cross_tier_collision(
                key,
                scope,
                scope_id,
                target_source_kind=target_source_kind,
            )


def test_binding_aware_query_helpers_default_to_native_without_call_site_changes():
    from cli_agent_orchestrator.services import (
        memory_relationship_service,
        memory_service,
    )

    helpers = (
        memory_service.MemoryService._upsert_metadata,
        memory_service.MemoryService._delete_metadata,
        memory_service.MemoryService.compact,
        memory_service.MemoryService._related_keys_lookup,
        memory_relationship_service.MemoryRelationshipService._assert_endpoint_exists,
        memory_relationship_service.MemoryRelationshipService._source_updated_map,
    )
    for helper in helpers:
        parameter = inspect.signature(helper).parameters["source_kind"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default == "native"

    for path, helper_names in {
        "src/cli_agent_orchestrator/services/memory_service.py": {
            "_upsert_metadata",
            "_delete_metadata",
            "_related_keys_lookup",
        },
        "src/cli_agent_orchestrator/services/memory_relationship_service.py": {
            "_assert_endpoint_exists",
            "_source_updated_map",
        },
    }.items():
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr not in helper_names:
                continue
            source_kind_keywords = [
                keyword for keyword in node.keywords if keyword.arg == "source_kind"
            ]
            if not source_kind_keywords:
                continue
            current = parents[node]
            while not isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                current = parents[current]
            kwonly_names = {argument.arg for argument in current.args.kwonlyargs}
            # A source-aware wrapper may forward its own keyword-only value.
            # A backend-matched consumer may instead derive the value from the
            # Memory row it is grouping. Literal or unrelated overrides would
            # weaken the native default at an existing call site. The
            # The deterministic renderer deliberately queries native and vault
            # relationship edges separately before expanding either source.
            # The vault deindex helper has already resolved a VaultBinding, so
            # its literal identifies the store it is removing from rather
            # than weakening a native-default query.
            assert len(source_kind_keywords) == 1
            source_kind_value = source_kind_keywords[0].value
            # Either injection entry point: PR #693 extracted
            # `get_memory_context(terminal_context)` out of
            # `get_memory_context_for_terminal(terminal_id)` for elastic worker
            # nodes, moving this dual-source query into the new name.
            explicit_renderer_source = (
                current.name in {"get_memory_context", "get_memory_context_for_terminal"}
                and isinstance(source_kind_value, ast.Constant)
                and source_kind_value.value in {"native", "vault"}
            )
            explicit_vault_deindex_source = (
                current.name == "_deindex_vault_memory"
                and isinstance(source_kind_value, ast.Constant)
                and source_kind_value.value in {"native", "vault"}
            )
            if explicit_renderer_source or explicit_vault_deindex_source:
                continue
            assert isinstance(source_kind_value, ast.Name), (
                "source_kind literals are allowed only for the deterministic "
                "renderer's dual-source query and VaultBinding-resolved deindexing"
            )
            source_kind_name = source_kind_value.id
            forwards_kwonly = source_kind_name == "source_kind" and "source_kind" in kwonly_names
            derives_from_memory = any(
                isinstance(assignment, (ast.Assign, ast.AnnAssign))
                and source_kind_name
                in {
                    target.id
                    for target in (
                        assignment.targets
                        if isinstance(assignment, ast.Assign)
                        else [assignment.target]
                    )
                    if isinstance(target, ast.Name)
                }
                and (
                    (
                        isinstance(assignment.value, ast.Attribute)
                        and assignment.value.attr == "source_kind"
                    )
                    or (
                        isinstance(assignment.value, ast.Call)
                        and isinstance(assignment.value.func, ast.Name)
                        and assignment.value.func.id == "getattr"
                        and len(assignment.value.args) >= 2
                        and isinstance(assignment.value.args[1], ast.Constant)
                        and assignment.value.args[1].value == "source_kind"
                        and (
                            len(assignment.value.args) < 3
                            or (
                                isinstance(assignment.value.args[2], ast.Constant)
                                and assignment.value.args[2].value == "native"
                            )
                        )
                    )
                )
                for assignment in ast.walk(current)
            )
            assert forwards_kwonly or derives_from_memory

    relationship_tree = ast.parse(
        Path("src/cli_agent_orchestrator/services/memory_relationship_service.py").read_text(
            encoding="utf-8"
        )
    )
    for node in ast.walk(relationship_tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "purge_for_key":
            continue
        assert not any(keyword.arg == "spare_origins" for keyword in node.keywords)
