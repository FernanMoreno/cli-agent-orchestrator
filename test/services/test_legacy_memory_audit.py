"""Legacy side effects require a committed, content-free audit intent."""

import sqlite3

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security.auth import Principal, _verified_principal
from cli_agent_orchestrator.services import knowledge_policy as policy


@pytest.fixture
def repository(tmp_path):
    result = WorkRepository(tmp_path / "work.sqlite")
    result.initialize()
    return result


@pytest.fixture
def principal():
    return _verified_principal("urn:cao:local-operator", "operator", ["admin"], "local_operator")


def rows(repository):
    with repository.connection() as connection:
        return [
            dict(row)
            for row in connection.execute("SELECT * FROM work_memory_access_audit ORDER BY id")
        ]


def test_committed_intent_visible_before_effect_and_survives_restart(repository, principal):
    with policy.legacy_memory_access(
        repository, principal, "store", {"path": "PRIVATE TARGET"}
    ) as operation_id:
        intent = rows(WorkRepository(repository.path))
        assert len(intent) == 1
        assert intent[0]["phase"] == "authorized"
        assert intent[0]["operation_id"] == operation_id
        assert intent[0]["actor_id"] == principal.id
        assert len(intent[0]["target_hash"]) == 64
        assert "PRIVATE TARGET" not in str(intent)
    restarted = WorkRepository(repository.path)
    restarted.verify_schema()
    assert [row["phase"] for row in rows(restarted)] == ["authorized", "completed"]


def test_body_failure_is_audited_and_original_error_propagates(repository, principal):
    error = ValueError("PRIVATE BODY ERROR")
    with pytest.raises(ValueError) as caught:
        with policy.legacy_memory_access(repository, principal, "forget", ["private"]):
            raise error
    assert caught.value is error
    assert [row["phase"] for row in rows(repository)] == ["authorized", "failed"]
    assert "PRIVATE BODY ERROR" not in str(rows(repository))


def test_audit_insert_failure_prevents_effect(repository, principal):
    with repository.connection() as connection:
        connection.execute(
            "CREATE TRIGGER reject_audit BEFORE INSERT ON work_memory_access_audit BEGIN SELECT RAISE(ABORT,'PRIVATE ERROR'); END"
        )
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
    with repository.connection() as connection:
        connection.execute(
            "CREATE TRIGGER reject_finish BEFORE INSERT ON work_memory_access_audit WHEN NEW.phase != 'authorized' BEGIN SELECT RAISE(ABORT,'PRIVATE ERROR'); END"
        )
    effects = []
    with pytest.raises(policy.LegacyMemoryAuditError) as caught:
        with policy.legacy_memory_access(repository, principal, "import", []) as operation_id:
            effects.append(True)
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
    with repository.connection() as connection, pytest.raises(sqlite3.IntegrityError):
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
    empty = WorkRepository(tmp_path / "empty.sqlite")
    with pytest.raises(policy.LegacyMemoryAuditError):
        with policy.legacy_memory_access(empty, principal, "recall", []):
            pytest.fail("unverified effect")


def test_schema_damage_after_effect_cannot_report_success(repository, principal):
    with pytest.raises(policy.LegacyMemoryAuditError) as caught:
        with policy.legacy_memory_access(repository, principal, "export", []):
            with repository.connection() as connection:
                connection.execute("DROP TRIGGER work_memory_access_audit_immutable_delete")
    assert caught.value.phase == "completed"
    assert [row["phase"] for row in rows(repository)] == ["authorized"]


def test_v12_upgrade_preserves_prior_ledger_and_audit(tmp_path, principal):
    from cli_agent_orchestrator.clients.work_repository import _CHECKSUMS, _MIGRATIONS

    repository = WorkRepository(tmp_path / "upgrade.sqlite")
    with repository.transaction() as connection:
        for version in range(1, 13):
            for statement in _MIGRATIONS[version]:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO work_migrations VALUES (?,?,?,?)",
                (version, _CHECKSUMS[version], 1.0, "verified"),
            )
        connection.execute(
            "INSERT INTO work_knowledge_access_audit (actor_id,authority_hash,action,target_hash,outcome,occurred_at) VALUES (?,?, 'read',?, 'allowed',1)",
            ("old-actor", "a" * 64, "b" * 64),
        )
        before = [tuple(row) for row in connection.execute("SELECT * FROM work_migrations")]
    repository.initialize()
    with repository.connection() as connection:
        assert [
            tuple(row)
            for row in connection.execute("SELECT * FROM work_migrations WHERE version<=12")
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
    WorkRepository(repository.path).verify_schema()


def test_verified_remote_denial_audit_failure_is_explicit(repository):
    actor = _verified_principal("issuer", "worker", ["admin"], "jwt")
    with repository.connection() as connection:
        connection.execute(
            "CREATE TRIGGER reject_denial BEFORE INSERT ON work_memory_access_audit BEGIN SELECT RAISE(ABORT,'PRIVATE'); END"
        )
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
