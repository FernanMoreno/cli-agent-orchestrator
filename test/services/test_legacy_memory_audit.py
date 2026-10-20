"""Legacy side effects require a committed, content-free audit intent."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from uuid import uuid4

import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import IntegrityError, connect
from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security.auth import Principal, _verified_principal
from cli_agent_orchestrator.services import knowledge_policy as policy


def _deny_audit_writes(repository):
    with connect(repository.target.migration_target()) as owner:
        owner.execute(
            sql.SQL("REVOKE INSERT ON work_memory_access_audit FROM {}").format(
                sql.Identifier(repository.target.user)
            )
        )


@pytest.fixture
def repository(isolated_memory_db):
    result = WorkRepository(isolated_memory_db)
    result.initialize()
    return result


@pytest.fixture
def principal():
    return _verified_principal("urn:cao:local-operator", "operator", ["admin"], "local_operator")


def test_memory_metadata_and_audit_must_use_the_same_target(isolated_memory_db, tmp_path):
    from test.postgres.fixtures import make_orm_engine

    from cli_agent_orchestrator.services.memory_service import MemoryService

    foreign = replace(
        isolated_memory_db, schema=isolated_memory_db.schema + "_foreign", store_id=str(uuid4())
    )
    bootstrap_scope(foreign)
    engine = make_orm_engine(foreign)
    try:
        service = MemoryService(base_dir=tmp_path / "wiki", db_engine=engine)
        with pytest.raises(PermissionError, match="another authority"):
            service._legacy_repository()
        assert not service.base_dir.exists()
        assert rows(WorkRepository(isolated_memory_db)) == []
        assert rows(WorkRepository(foreign)) == []
    finally:
        engine.dispose()


def rows(repository):
    with connect(repository.target) as connection:
        return [
            dict(row)
            for row in connection.execute("SELECT * FROM work_memory_access_audit ORDER BY id")
        ]


def test_committed_intent_visible_before_effect_and_survives_restart(repository, principal):
    with policy.legacy_memory_access(
        repository, principal, "store", {"path": "PRIVATE TARGET"}
    ) as operation_id:
        intent = rows(WorkRepository(repository.target))
        assert len(intent) == 1
        assert intent[0]["phase"] == "authorized"
        assert intent[0]["operation_id"] == operation_id
        assert intent[0]["actor_id"] == principal.id
        assert len(intent[0]["target_hash"]) == 64
        assert "PRIVATE TARGET" not in str(intent)
    restarted = WorkRepository(repository.target)
    restarted.verify_schema()
    assert [row["phase"] for row in rows(restarted)] == ["authorized", "completed"]


def test_legacy_audit_verifies_once_after_the_store_fence(repository, principal, monkeypatch):
    events = []
    original_verify = repository._verify
    original_lock = repository._lock_store_for_write

    def traced_verify(connection, **kwargs):
        events.append(("verify", connection.in_transaction))
        return original_verify(connection, **kwargs)

    def traced_lock(connection):
        result = original_lock(connection)
        events.append(("fence", connection.in_transaction))
        return result

    monkeypatch.setattr(repository, "_verify", traced_verify)
    monkeypatch.setattr(repository, "_lock_store_for_write", traced_lock)

    with policy.legacy_memory_access(
        repository, principal, "graph", {"scope": "global", "target": "PRIVATE TARGET"}
    ) as operation_id:
        intent = rows(repository)
        assert [row["phase"] for row in intent] == ["authorized"]
        assert intent[0]["operation_id"] == operation_id
        assert "PRIVATE TARGET" not in str(intent)

    assert events == [
        ("fence", True),
        ("verify", True),
        ("fence", True),
        ("verify", True),
    ]
    completed = rows(repository)
    assert [row["phase"] for row in completed] == ["authorized", "completed"]
    assert completed[0]["operation_id"] == operation_id


def test_legacy_audit_rejects_schema_drift_after_waiting_for_store_fence(
    repository, principal, monkeypatch
):
    lock_requested = threading.Event()
    worker = {}
    body_entered = []
    original_lock = repository._lock_store_for_write

    def capture_store_lock(connection):
        worker["pid"] = connection.execute("SELECT pg_backend_pid()").fetchone()[0]
        lock_requested.set()
        return original_lock(connection)

    monkeypatch.setattr(repository, "_lock_store_for_write", capture_store_lock)
    owner = connect(repository.target.migration_target())
    owner.begin()
    owner.execute("SELECT singleton FROM work_store_control WHERE singleton=1 FOR UPDATE")
    blocker_pid = owner.execute("SELECT pg_backend_pid()").fetchone()[0]
    changed_schema = False
    future = None
    try:

        def run_legacy_operation():
            with policy.legacy_memory_access(repository, principal, "graph", {"scope": "global"}):
                body_entered.append(True)

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="legacy-audit-fence") as pool:
            future = pool.submit(run_legacy_operation)
            assert lock_requested.wait(timeout=5), "audit writer did not reach its store fence"
            worker_pid = worker["pid"]
            deadline = time.monotonic() + 5
            blocked_by_owner = False
            with connect(repository.target) as monitor:
                while time.monotonic() < deadline:
                    state = monitor.execute(
                        "SELECT wait_event_type,pg_blocking_pids(pid) AS blockers "
                        "FROM pg_stat_activity WHERE pid=%s",
                        (worker_pid,),
                    ).fetchone()
                    if (
                        state is not None
                        and state["wait_event_type"] == "Lock"
                        and blocker_pid in state["blockers"]
                    ):
                        blocked_by_owner = True
                        break
                    time.sleep(0.01)
            assert blocked_by_owner, "audit writer was not waiting on the held store fence"

            owner.execute(
                sql.SQL("ALTER TABLE {} ADD COLUMN t053_drift_marker TEXT").format(
                    sql.Identifier(repository.target.schema, "work_memory_access_audit")
                )
            )
            owner.commit()
            changed_schema = True
            with pytest.raises(policy.LegacyMemoryAuditError) as caught:
                future.result(timeout=10)
            assert caught.value.phase == "authorized"
            assert body_entered == []
    finally:
        if owner.in_transaction:
            owner.rollback()
        owner.close()
        if changed_schema:
            with connect(repository.target.migration_target()) as cleanup:
                cleanup.execute(
                    sql.SQL("ALTER TABLE {} DROP COLUMN t053_drift_marker").format(
                        sql.Identifier(repository.target.schema, "work_memory_access_audit")
                    )
                )
        if future is not None and not future.done():
            future.result(timeout=12)


@pytest.mark.parametrize("failure_point", ["store_fence", "verification", "body"])
def test_audited_write_transaction_rolls_back_and_closes_on_failure(
    repository, failure_point, monkeypatch
):
    captured = []
    original_lock = repository._lock_store_for_write
    original_verify = repository._verify

    def capture(connection):
        captured.append(connection)

    if failure_point == "store_fence":

        def fail_at_lock(connection):
            capture(connection)
            raise RuntimeError("private fence failure")

        monkeypatch.setattr(repository, "_lock_store_for_write", fail_at_lock)
    elif failure_point == "verification":

        def fail_at_verify(connection, **kwargs):
            capture(connection)
            original_verify(connection, **kwargs)
            raise RuntimeError("private verification failure")

        monkeypatch.setattr(repository, "_verify", fail_at_verify)

    with pytest.raises(RuntimeError, match="private"):
        with repository.audited_write_transaction() as connection:
            capture(connection)
            connection.execute(
                "INSERT INTO work_memory_access_audit "
                "(operation_id,actor_id,action,target_hash,phase,occurred_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (uuid4().hex, "test-actor", "graph", "a" * 64, "authorized", 1),
            )
            raise RuntimeError("private body failure")

    assert captured
    assert all(connection.raw.closed for connection in captured)
    assert rows(repository) == []


def test_completion_commit_failure_rolls_back_and_reports_partial_outcome(
    repository, principal, monkeypatch
):
    original_transaction = repository.audited_write_transaction
    opened = []
    commit_failures = []
    transaction_count = 0
    invalid_result_id = uuid4().hex

    @contextmanager
    def fail_second_commit():
        nonlocal transaction_count
        transaction_count += 1
        current_transaction = transaction_count
        try:
            with original_transaction() as connection:
                opened.append(connection)
                yield connection
                if current_transaction == 2:
                    connection.execute("SET CONSTRAINTS ALL DEFERRED")
                    connection.execute(
                        "INSERT INTO work_results "
                        "(id,attempt_id,content_hash,immutable_location,byte_length,"
                        "validation_state,validator_id,validation_evidence,created_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            invalid_result_id,
                            "missing-attempt-for-deferred-fk",
                            "a" * 64,
                            "t053://commit-failure",
                            0,
                            "verified",
                            "t053-test",
                            "{}",
                            1.0,
                        ),
                    )
        except IntegrityError as error:
            if current_transaction == 2:
                commit_failures.append(error.sqlstate)
            raise

    monkeypatch.setattr(repository, "audited_write_transaction", fail_second_commit)

    effects = []
    with pytest.raises(policy.LegacyMemoryAuditError) as caught:
        with policy.legacy_memory_access(repository, principal, "import", {"key": "safe-id"}) as op:
            effects.append(True)

    assert effects == [True]
    assert caught.value.operation_id == op
    assert caught.value.phase == "completed"
    assert "potentially partial outcome" in str(caught.value)
    assert "23503" not in str(caught.value)
    assert "missing-attempt-for-deferred-fk" not in str(caught.value)
    assert commit_failures == ["23503"]
    assert len(opened) == 2
    assert all(connection.raw.closed for connection in opened)
    assert [row["phase"] for row in rows(repository)] == ["authorized"]
    with connect(repository.target) as connection:
        assert (
            connection.execute(
                "SELECT id FROM work_results WHERE id=%s", (invalid_result_id,)
            ).fetchone()
            is None
        )


def test_body_failure_is_audited_and_original_error_propagates(repository, principal):
    error = ValueError("PRIVATE BODY ERROR")
    with pytest.raises(ValueError) as caught:
        with policy.legacy_memory_access(repository, principal, "forget", ["private"]):
            raise error
    assert caught.value is error
    assert [row["phase"] for row in rows(repository)] == ["authorized", "failed"]
    assert "PRIVATE BODY ERROR" not in str(rows(repository))


def test_audit_insert_failure_prevents_effect(repository, principal):
    _deny_audit_writes(repository)
    reached = []
    with pytest.raises(policy.LegacyMemoryAuditError) as caught:
        with policy.legacy_memory_access(repository, principal, "store", []):
            reached.append(True)
    assert reached == []
    assert rows(repository) == []
    assert "PRIVATE ERROR" not in str(caught.value)
    assert caught.value.phase == "authorized"


@pytest.mark.parametrize("body_fails", [False, True])
def test_completion_audit_failure_reports_partial_outcome(repository, principal, body_fails):

    effects = []
    with pytest.raises(policy.LegacyMemoryAuditError) as caught:
        with policy.legacy_memory_access(repository, principal, "import", []) as operation_id:
            effects.append(True)
            _deny_audit_writes(repository)
            if body_fails:
                raise ValueError("PRIVATE BODY ERROR")
    assert effects == [True]
    assert caught.value.operation_id == operation_id
    assert caught.value.phase == ("failed" if body_fails else "completed")
    assert "partial" in str(caught.value)
    assert "PRIVATE" not in str(caught.value)
    assert [row["phase"] for row in rows(repository)] == ["authorized"]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE work_memory_access_audit SET actor_id='changed'",
        "DELETE FROM work_memory_access_audit",
    ],
)
def test_audit_is_append_only(repository, principal, statement):
    with policy.legacy_memory_access(repository, principal, "recall", []):
        pass
    with repository.connection() as connection, pytest.raises(IntegrityError):
        connection.execute(statement)
    assert len(rows(repository)) == 2


@pytest.mark.parametrize(
    "principal", [None, {}, _verified_principal("issuer", "remote", ["admin"], "jwt")]
)
def test_verified_local_principal_required(repository, principal):
    with pytest.raises(policy.KnowledgeAccessDenied):
        with policy.legacy_memory_access(repository, principal, "recall", []):
            pytest.fail("unauthorized effect")
    assert [row["phase"] for row in rows(repository)] == (
        ["denied"] if isinstance(principal, Principal) else []
    )


def test_invalid_action_and_uninitialized_store_fail_before_effect(repository, principal, tmp_path):
    with pytest.raises(ValueError):
        with policy.legacy_memory_access(repository, principal, "arbitrary", []):
            pytest.fail("invalid effect")
    empty = WorkRepository(
        replace(
            repository.target, schema=repository.target.schema + "_empty", store_id=str(uuid4())
        )
    )
    with pytest.raises(policy.LegacyMemoryAuditError):
        with policy.legacy_memory_access(empty, principal, "recall", []):
            pytest.fail("unverified effect")


def test_schema_damage_after_effect_cannot_report_success(repository, principal):
    with pytest.raises(policy.LegacyMemoryAuditError) as caught:
        with policy.legacy_memory_access(repository, principal, "export", []):
            with connect(repository.target.migration_target()) as connection:
                connection.execute(
                    "DROP TRIGGER work_memory_access_audit_immutable_delete ON work_memory_access_audit"
                )
    assert caught.value.phase == "completed"
    assert [row["phase"] for row in rows(repository)] == ["authorized"]


def test_native_bootstrap_preserves_prior_ledger_and_audit(repository, principal):
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO work_knowledge_access_audit (actor_id,authority_hash,action,target_hash,outcome,occurred_at) VALUES (%s,%s, 'read',%s, 'allowed',1)",
            ("old-actor", "a" * 64, "b" * 64),
        )
        before = [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM _cao_schema_migrations ORDER BY component,version"
            )
        ]
    bootstrap_scope(repository.target)
    with repository.connection() as connection:
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM _cao_schema_migrations ORDER BY component,version"
            )
        ] == before
        assert (
            connection.execute("SELECT actor_id FROM work_knowledge_access_audit").fetchone()[0]
            == "old-actor"
        )
    with policy.legacy_memory_access(repository, principal, "graph", []):
        pass
    assert [row["phase"] for row in rows(repository)] == ["authorized", "completed"]


def test_verified_remote_denial_committed_without_body(repository):
    actor = _verified_principal("issuer", "worker", ["admin"], "jwt")
    with pytest.raises(policy.KnowledgeAccessDenied):
        with policy.legacy_memory_access(repository, actor, "store", {"key": "PRIVATE KEY"}):
            pytest.fail("denied effect")
    audit = rows(repository)
    assert len(audit) == 1
    assert audit[0]["phase"] == "denied"
    assert audit[0]["actor_id"] == actor.id
    assert "PRIVATE KEY" not in str(audit)
    WorkRepository(repository.target).verify_schema()


def test_verified_remote_denial_audit_failure_is_explicit(repository):
    actor = _verified_principal("issuer", "worker", ["admin"], "jwt")
    _deny_audit_writes(repository)
    with pytest.raises(policy.LegacyMemoryAuditError) as caught:
        with policy.legacy_memory_access(repository, actor, "recall", []):
            pytest.fail("denied effect")
    assert caught.value.phase == "denied"
    assert "PRIVATE" not in str(caught.value)


def test_nested_audit_failure_reports_outer_partial_operation(repository, principal):
    with pytest.raises(policy.LegacyMemoryAuditError) as caught:
        with policy.legacy_memory_access(repository, principal, "forget", []) as outer:
            raise policy.LegacyMemoryAuditError("a" * 32, "authorized")
    assert caught.value.operation_id == outer
    assert caught.value.phase == "failed"
    assert "partial" in str(caught.value)
    assert [row["phase"] for row in rows(repository)] == ["authorized", "failed"]
