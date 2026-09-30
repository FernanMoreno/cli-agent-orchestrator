"""SQLite-only behavioral coverage for the durable managed-inbox bridge."""

import asyncio
import hashlib
import importlib
import importlib.util
import shutil
import sqlite3
from test.integration.test_work_dispatch import context  # noqa: F401
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.work_inbox_schema import INBOX_STORE_CONTEXT_SCHEMA
from cli_agent_orchestrator.clients.work_repository import WorkConflict
from cli_agent_orchestrator.models.work_contract import (
    ContractPermissions,
    ContractResources,
    ContractSnapshot,
    EffectiveWorkContract,
)
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    ResolvedSnapshot,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.work_admission import WorkAdmission


@pytest.fixture
def paired_context(context, monkeypatch):
    """The real SQLAlchemy inbox and WorkRepository share one temporary SQLite file."""
    engine = create_engine(
        f"sqlite:///{context.repo.path}", connect_args={"check_same_thread": False}
    )
    database.Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(constants, "DATABASE_FILE", context.repo.path)
    monkeypatch.setattr(
        database,
        "SessionLocal",
        sessionmaker(autocommit=False, autoflush=False, bind=engine),
    )
    with database.SessionLocal() as session:
        session.add(
            database.TerminalModel(
                id="receiver",
                tmux_session="managed-session",
                tmux_window="managed-window",
                provider="mock_cli",
            )
        )
        session.commit()
    return context


def _managed_module():
    spec = importlib.util.find_spec("cli_agent_orchestrator.services.work_inbox")
    assert spec is not None, "managed inbox coordinator is not installed"
    return importlib.import_module("cli_agent_orchestrator.services.work_inbox")


def _request(context, key: str):
    job, grant = context.jobs[0]
    snapshot = DelegationSnapshots(
        context.repo,
        policy=KnowledgePolicy(context.repo, job["id"], grant.id, 1),
    ).freeze(
        principal=context.actor,
        job_id=job["id"],
        contract_id=f"contract-{key}",
        binding_key=f"snapshot-{key}",
        request_hash=hashlib.sha256(key.encode()).hexdigest(),
        scope="project",
        scope_id="project",
        resolver=lambda connection, principal: ResolvedSnapshot(f"frozen context {key}"),
    )
    contract = EffectiveWorkContract(
        id=f"contract-{key}",
        operation_kind="inbox",
        provider="mock_cli",
        backend="test",
        permissions=ContractPermissions(paths=(str(context.root),)),
        resources=ContractResources(
            checkout_root=str(context.root),
            write_paths=(str(context.root / key),),
            units=1,
        ),
        snapshot=ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
    )
    return {
        "principal": context.actor,
        "job_id": job["id"],
        "idempotency_key": key,
        "request_hash": hashlib.sha256(("request-" + key).encode()).hexdigest(),
        "grant_id": grant.id,
        "expected_grant_revision": 1,
        "contract": contract,
        "lease_seconds": 300,
        "sender_id": "sender",
        "receiver_id": "receiver",
        "message": f"managed {key}",
    }


def _coordinator(context):
    module = _managed_module()
    context.service = WorkAdmission(
        context.repo,
        backends={"test": context.backend},
        delivery_adapters=module.managed_inbox_delivery_adapters(context.repo),
    )
    return module.WorkInboxCoordinator(context.service)


def _bridge_rows(repository):
    with repository.connection() as connection:
        return connection.execute(
            "SELECT inbox_id,attempt_id,generation,store_identity FROM work_inbox_bindings"
        ).fetchall()


def test_foreign_store_or_absent_binding_has_no_managed_effect(paired_context, tmp_path):
    coordinator = _coordinator(paired_context)
    receipt = coordinator.admit(**_request(paired_context, "foreign"))
    before = [tuple(row) for row in _bridge_rows(paired_context.repo)]
    store_context = database._managed_inbox_store_identity()

    with pytest.raises(WorkConflict):
        paired_context.repo.bind_inbox(
            attempt_id="missing-attempt",
            generation=1,
            inbox_id=receipt.inbox_id + 100,
            store_context=store_context,
        )
    with pytest.raises(WorkConflict):
        paired_context.repo.bind_inbox(
            attempt_id=receipt.attempt_id,
            generation=receipt.generation,
            inbox_id=receipt.inbox_id,
            store_context=type(store_context)(
                store_identity=str(tmp_path / "foreign.sqlite"),
                store_uuid=store_context.store_uuid,
            ),
        )

    assert [tuple(row) for row in _bridge_rows(paired_context.repo)] == before
    assert database.get_inbox_messages("receiver", status=None)[0].status.value == "reconcile"


def test_database_file_divergence_rejects_before_managed_reservation(
    paired_context, tmp_path, monkeypatch
):
    coordinator = _coordinator(paired_context)
    monkeypatch.setattr(constants, "DATABASE_FILE", tmp_path / "divergent.sqlite")

    with pytest.raises(WorkConflict, match="configured database"):
        coordinator.admit(**_request(paired_context, "database-file-divergence"))

    with paired_context.repo.connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_items").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM work_scheduler_requests").fetchone()[0] == 0
    assert database.get_inbox_messages("receiver", status=None) == []


def test_same_path_store_uuid_replacement_rejects_before_managed_reservation(
    paired_context, tmp_path
):
    coordinator = _coordinator(paired_context)
    original_context = database._managed_inbox_store_identity()
    replacement = tmp_path / "replacement.sqlite"

    with database.SessionLocal() as prior_sqlalchemy_connection:
        assert (
            database._managed_inbox_store_identity_for_session(prior_sqlalchemy_connection)
            == original_context
        )
        shutil.copy2(paired_context.repo.path, replacement)
        with sqlite3.connect(replacement) as connection:
            connection.execute("DROP TRIGGER work_inbox_store_context_immutable_update")
            connection.execute("UPDATE work_inbox_store_context SET store_uuid=?", (uuid4().hex,))
            connection.execute(INBOX_STORE_CONTEXT_SCHEMA[1])
        replacement.replace(paired_context.repo.path)

        with pytest.raises(WorkConflict, match="store UUID|store context"):
            coordinator.admit(**_request(paired_context, "same-path-replacement"))

    reopened = paired_context.repo.managed_inbox_store_context()
    assert reopened.store_identity == original_context.store_identity
    assert reopened.store_uuid != original_context.store_uuid
    with paired_context.repo.connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_items").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM work_scheduler_requests").fetchone()[0] == 0


def test_missing_store_context_rejects_before_managed_reservation(paired_context):
    coordinator = _coordinator(paired_context)
    request = _request(paired_context, "missing-store-context")
    with paired_context.repo.transaction() as connection:
        connection.execute("DROP TRIGGER work_inbox_store_context_immutable_delete")
        connection.execute("DELETE FROM work_inbox_store_context")

    with pytest.raises(WorkConflict, match="store context|configured database"):
        coordinator.admit(**request)

    with paired_context.repo.connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_items").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM work_scheduler_requests").fetchone()[0] == 0
    assert database.get_inbox_messages("receiver", status=None) == []


def test_store_uuid_is_stable_across_reopen_and_preserves_v15_bridge(paired_context):
    coordinator = _coordinator(paired_context)
    receipt = coordinator.admit(**_request(paired_context, "context-reopen"))
    before = [tuple(row) for row in _bridge_rows(paired_context.repo)]
    before_context = database._managed_inbox_store_identity()

    paired_context.repo.initialize()
    reopened = type(paired_context.repo)(paired_context.repo.path)
    reopened.initialize()

    assert reopened.managed_inbox_store_context(expected=before_context) == before_context
    assert database._managed_inbox_store_identity(expected=before_context) == before_context
    assert [tuple(row) for row in _bridge_rows(reopened)] == before
    assert before[0][0] == receipt.inbox_id


def test_v16_expansion_preserves_a_literal_v15_bridge(tmp_path):
    from test.clients.test_work_migrations import verified_store_at_version

    repository_module = importlib.import_module("cli_agent_orchestrator.clients.work_repository")
    store_path = tmp_path / "literal-v15.sqlite3"
    repository = verified_store_at_version(repository_module, store_path, version=15)
    store_identity = str(store_path.resolve())
    contract_json = '{"schema_version":1,"id":"contract-v15"}'

    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO work_jobs "
            "(id,project_id,principal_id,allowed_providers,grant_id,created_at) "
            "VALUES ('job-v15','project','principal-v15','[\"mock_cli\"]','grant-v15',1)"
        )
        connection.execute(
            "INSERT INTO work_principals VALUES " "('principal-v15','issuer','subject','jwt',1)"
        )
        connection.execute(
            "INSERT INTO work_grants "
            "(id,revision,job_id,principal_id,allowed_providers,permissions,expires_at,created_at) "
            "VALUES ('grant-v15',1,'job-v15','principal-v15','[\"mock_cli\"]','{}',100,1)"
        )
        connection.execute(
            "INSERT INTO work_delegation_snapshots "
            "(id,schema_version,job_id,contract_id,binding_key,request_hash,scope,scope_id,"
            "producer_principal_id,source_hash,delivered_hash,content,redacted,truncated,created_at) "
            "VALUES ('snapshot-v15',1,'job-v15','contract-v15','binding-v15',?,'project',"
            "'project','principal-v15',?,?,X'',0,0,1)",
            ("a" * 64, "b" * 64, "c" * 64),
        )
        connection.execute(
            "INSERT INTO work_items "
            "(id,job_id,operation_kind,idempotency_key,request_hash,contract_id,created_at) "
            "VALUES ('item-v15','job-v15','inbox','key-v15',?,'contract-v15',1)",
            ("d" * 64,),
        )
        connection.execute(
            "INSERT INTO work_attempts "
            "(id,work_item_id,attempt_number,generation,provider,lease_expires_at,created_at) "
            "VALUES ('attempt-v15','item-v15',1,1,'mock_cli',100,1)"
        )
        connection.execute(
            "INSERT INTO work_dispatch_bindings "
            "(attempt_id,generation,job_id,work_item_id,principal_id,grant_id,grant_revision,"
            "contract_id,contract_json,contract_hash,snapshot_id,created_at) "
            "VALUES ('attempt-v15',1,'job-v15','item-v15','principal-v15','grant-v15',1,"
            "'contract-v15',?,?, 'snapshot-v15',1)",
            (contract_json, "e" * 64),
        )
        connection.execute(
            "INSERT INTO work_inbox_store_identity VALUES (1,?,1)", (store_identity,)
        )
        connection.execute(
            "INSERT INTO work_inbox_bindings VALUES (73,'attempt-v15',1,?,1)",
            (store_identity,),
        )

    with repository.connection() as connection:
        assert connection.execute("SELECT max(version) FROM work_migrations").fetchone()[0] == 15
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        before = [tuple(row) for row in _bridge_rows(repository)]

    repository.initialize()

    assert [tuple(row) for row in _bridge_rows(repository)] == before
    assert before == [(73, "attempt-v15", 1, store_identity)]
    with repository.connection() as connection:
        context = connection.execute(
            "SELECT singleton,store_uuid,store_identity FROM work_inbox_store_context"
        ).fetchone()
        assert context[0] == 1
        assert len(context[1]) == 32
        assert context[2] == store_identity


def test_same_binding_is_idempotent_but_another_managed_row_conflicts(paired_context):
    coordinator = _coordinator(paired_context)
    request = _request(paired_context, "duplicate")
    first = coordinator.admit(**request)
    retry = coordinator.admit(**request)

    writer = getattr(database, "_create_managed_inbox_message")
    store_context = database._managed_inbox_store_identity()
    other = writer("sender", "receiver", "other managed row", store_context=store_context)
    with pytest.raises(WorkConflict):
        paired_context.repo.bind_inbox(
            attempt_id=first.attempt_id,
            generation=first.generation,
            inbox_id=other.id,
            store_context=store_context,
        )

    assert retry == first
    assert [row[0] for row in _bridge_rows(paired_context.repo)] == [first.inbox_id]
    assert other.status.value == "reconcile"


@pytest.mark.asyncio
async def test_restart_after_managed_effect_never_pastes_the_same_bridge_twice(paired_context):
    module = _managed_module()
    coordinator = _coordinator(paired_context)
    receipt = coordinator.admit(**_request(paired_context, "restart"))

    sent = await paired_context.service.dispatch_registered_next()
    assert sent["id"] == receipt.work_item_id
    restarted = WorkAdmission(
        paired_context.repo,
        backends={"test": paired_context.backend},
        delivery_adapters=module.managed_inbox_delivery_adapters(paired_context.repo),
    )

    assert await restarted.dispatch_registered_next() is None
    assert paired_context.backend.effects == [
        ("managed-session", "managed-window", "managed restart")
    ]
    assert database.get_inbox_messages("receiver", status=None)[0].status.value == "reconcile"


def test_managed_writer_preserves_legacy_pending_row_literally(paired_context):
    legacy = database.create_inbox_message("sender", "receiver", "legacy exact payload")
    coordinator = _coordinator(paired_context)
    receipt = coordinator.admit(**_request(paired_context, "legacy"))

    messages = database.get_inbox_messages("receiver", limit=10, status=None)
    assert [(item.id, item.message, item.status.value) for item in messages] == [
        (legacy.id, "legacy exact payload", "pending"),
        (receipt.inbox_id, "managed legacy", "reconcile"),
    ]
    assert [item.id for item in database.get_pending_messages("receiver", limit=10)] == [legacy.id]


def test_bridge_failure_leaves_a_retained_row_and_no_dispatchable_order(
    paired_context, monkeypatch
):
    module = _managed_module()
    coordinator = _coordinator(paired_context)
    original_writer = module._create_managed_inbox_message

    def fail_after_retention(*args, **kwargs):
        original_writer(*args, **kwargs)
        raise RuntimeError("bridge interrupted")

    monkeypatch.setattr(module, "_create_managed_inbox_message", fail_after_retention)
    with pytest.raises(RuntimeError, match="bridge interrupted"):
        coordinator.admit(**_request(paired_context, "partial"))

    with paired_context.repo.connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_inbox_bindings").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM work_scheduler_requests").fetchone()[0] == 0
        assert connection.execute("SELECT state FROM work_attempts").fetchone()[0] == "planned"
    assert database.get_inbox_messages("receiver", status=None)[0].status.value == "reconcile"


@pytest.mark.asyncio
async def test_revocation_after_bridge_blocks_the_next_managed_effect(paired_context):
    coordinator = _coordinator(paired_context)
    coordinator.admit(**_request(paired_context, "revoked"))

    paired_context.backend.preflight_hook = lambda: paired_context.control.revoke(
        paired_context.actor,
        grant_id=paired_context.jobs[0][1].id,
        expected_grant_revision=1,
        reason="managed inbox revocation",
    )

    assert await paired_context.service.dispatch_registered_next() is None
    assert paired_context.backend.effects == []
    assert database.get_inbox_messages("receiver", status=None)[0].status.value == "reconcile"
