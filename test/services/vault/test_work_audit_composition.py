"""Vault transactions must retain the fork's verified audit boundary."""

from test.services.vault.test_boundary_refusal import _boundary_state

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients.database import Base, MemoryRelationshipModel
from cli_agent_orchestrator.services.knowledge_policy import KnowledgeAccessDenied
from cli_agent_orchestrator.services.memory_relationship_service import MemoryRelationshipService
from cli_agent_orchestrator.services.memory_service import MemoryService
from cli_agent_orchestrator.services.vault import reconcile as vault_reconcile


def test_vault_projection_has_durable_authority_before_shared_transaction(tmp_path, monkeypatch):
    state = _boundary_state(tmp_path, monkeypatch)
    (state.root / "CAO" / "healthy.md").write_text("safe projection", encoding="utf-8")
    apply_plan = vault_reconcile._apply_plan
    observed = []

    def checked_plan(db, *args, **kwargs):
        rows = (
            db.connection()
            .exec_driver_sql(
                "SELECT phase FROM work_memory_access_audit WHERE action='repair' ORDER BY id"
            )
            .fetchall()
        )
        observed.append([row[0] for row in rows])
        return apply_plan(db, *args, **kwargs)

    monkeypatch.setattr(vault_reconcile, "_apply_plan", checked_plan)
    vault_reconcile.reconcile(state.vault, apply=True)
    assert observed == [["authorized"]]
    with state.engine.connect() as connection:
        phases = connection.exec_driver_sql(
            "SELECT phase FROM work_memory_access_audit WHERE action='repair' ORDER BY id"
        ).fetchall()
    assert [row[0] for row in phases] == ["authorized", "completed"]


def test_enclosing_audit_cannot_authorize_another_database(tmp_path):
    owner_engine = create_engine(f'sqlite:///{tmp_path / "owner.db"}')
    foreign_engine = create_engine(f'sqlite:///{tmp_path / "foreign.db"}')
    Base.metadata.create_all(owner_engine)
    Base.metadata.create_all(foreign_engine)
    owner = MemoryService(base_dir=tmp_path / "native", db_engine=owner_engine)
    relationships = MemoryRelationshipService(memory_service=owner)
    Foreign = sessionmaker(bind=foreign_engine)
    with owner._legacy_operation("repair", {"operation": "vault_projection"}):
        with Foreign() as db:
            with db.begin():
                with pytest.raises(KnowledgeAccessDenied, match="another authority"):
                    relationships.replace_set(
                        "global", None, "source", "vault", "relates_to", [], db=db
                    )
    with Foreign() as db:
        assert db.query(MemoryRelationshipModel).count() == 0


@pytest.mark.parametrize(
    "method,args,kwargs",
    [
        ("clear_source", ("global", None, "source", "vault"), {}),
        ("supersede_ids", ([],), {"expect": {}}),
        ("restore_statuses", ({},), {"expect": {}}),
    ],
)
def test_new_relationship_mutations_require_enclosing_audit(tmp_path, method, args, kwargs):
    engine = create_engine(f'sqlite:///{tmp_path / "new-helper.db"}')
    Base.metadata.create_all(engine)
    owner = MemoryService(base_dir=tmp_path / "native", db_engine=engine)
    relationships = MemoryRelationshipService(memory_service=owner)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        with pytest.raises(KnowledgeAccessDenied, match="enclosing audited"):
            getattr(relationships, method)(*args, **kwargs, db=db)


def test_unreconciled_exclusion_blocks_vault_recall(tmp_path, monkeypatch):
    import asyncio
    from test.services.vault.test_vault_migration_lifecycle import _lifecycle, _migrate, _store

    from cli_agent_orchestrator.clients.database import (
        VaultExclusionModel,
        VaultMigrationReceiptModel,
    )

    state = _lifecycle(tmp_path, monkeypatch)
    _store(state, "retained native bytes")
    _migrate(state)
    with state.session() as db:
        receipt = db.query(VaultMigrationReceiptModel).one()
        db.add(
            VaultExclusionModel(
                vault_id=receipt.vault_id,
                scope=receipt.scope,
                scope_id=receipt.scope_id,
                cao_key=receipt.cao_key,
                last_known_relpath=receipt.managed_relpath,
                content_sha256=receipt.published_content_sha256,
            )
        )
        db.commit()
    assert asyncio.run(state.service.recall(scope="global", limit=100)) == []


def test_read_preflight_cannot_fall_back_from_reviewed_knowledge(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "reviewed-helper.db"}')
    Base.metadata.create_all(engine)
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy

    policy = KnowledgePolicy(WorkRepository(tmp_path / "reviewed-helper.db"), "job", "grant", 1)
    owner = MemoryService(base_dir=tmp_path / "native", db_engine=engine, knowledge_policy=policy)
    relationships = MemoryRelationshipService(memory_service=owner)
    with sessionmaker(bind=engine)() as db:
        with pytest.raises(KnowledgeAccessDenied, match="cannot fall back"):
            relationships.preflight_edge_identities({}, db=db)


def test_vault_graph_uses_its_memory_database_owner(tmp_path, monkeypatch):
    import asyncio
    from test.services.vault.test_vault_migration_lifecycle import _lifecycle, _migrate, _store

    from cli_agent_orchestrator.graph.providers.memory import MemoryGraphProvider

    state = _lifecycle(tmp_path, monkeypatch)
    _store(state, "retained native bytes")
    _migrate(state)
    provider = MemoryGraphProvider(
        memory_service=state.service,
        lint_enabled=lambda: False,
        binding_resolver=lambda *_args: state.binding,
    )
    view = asyncio.run(provider.project(scope="global"))
    assert [(node.id, node.attrs.get("is_vault")) for node in view.nodes] == [("migrated", True)]
