"""Tests for the durable plan-approval record (issue #583 Bolt 2, unit ``approval-store``).

Two carry the unit's load:

* ``test_a_repeated_grant_does_not_rewrite_the_original`` — write-once. If this fails, an existing approval can
  be pointed at a changed plan: the row reads as approved, the timestamp looks recent, and work nobody reviewed
  executes with a genuine approval behind it.
* ``test_an_unknown_plan_is_not_approved`` — absence means unapproved. Every fault in this unit (missing table,
  silent migration failure, database error) converges on "no row found", so if absence were ever permissive each
  one would become an authorisation bypass rather than a refused run.
"""

import importlib

import pytest

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients import database as database_client
from cli_agent_orchestrator.clients.postgres import OperationalError, connect
from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
from cli_agent_orchestrator.clients.private_plan_postgres_schema import PRIVATE_PLAN_MIGRATIONS
from cli_agent_orchestrator.clients.workflows_postgres_schema import WORKFLOW_MIGRATIONS
from cli_agent_orchestrator.services import approval_store

pytestmark = pytest.mark.postgres


@pytest.fixture()
def db_path(postgres_targets):
    """Provision a real PG store explicitly; runtime never creates tables."""
    target = postgres_targets.main
    migrate_component(target, WORKFLOW_MIGRATIONS)
    migrate_component(target, PRIVATE_PLAN_MIGRATIONS)
    return target


def _columns(target):
    with connect(target, readonly=True) as conn:
        return {
            row["name"]: row
            for row in conn.execute(
                """SELECT a.attname AS name,a.attnotnull AS required,
            EXISTS(SELECT 1 FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype='p' AND a.attnum=ANY(k.conkey)) AS primary_key
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relname='workflow_plan_approval' AND a.attnum>0 AND NOT a.attisdropped""",
                (target.schema,),
            ).fetchall()
        }


# ---------------------------------------------------------------------------
# The two load-bearing properties
# ---------------------------------------------------------------------------


def test_a_repeated_grant_does_not_rewrite_the_original(db_path):
    """WRITE-ONCE. ``INSERT OR IGNORE`` makes it a property of the statement, not of a caller's check."""
    approval_store.grant("plan-v1:abc", "stan")
    first = approval_store.get_approval("plan-v1:abc")
    assert first is not None

    approval_store.grant("plan-v1:abc", "someone-else")
    second = approval_store.get_approval("plan-v1:abc")

    assert second is not None
    assert second.approved_by == "stan", "a second grant must not change the approver"
    assert second.approved_at == first.approved_at, "a second grant must not change the timestamp"


def test_an_unknown_plan_is_not_approved(db_path):
    """Absence answers False and raises nothing — so every fault mode is fail-closed."""
    assert approval_store.is_approved("plan-v1:never-granted") is False


# ---------------------------------------------------------------------------
# The tri-state sibling (issue #696): a store FAULT is distinct from an ABSENCE
# ---------------------------------------------------------------------------


def test_approval_state_reports_absent_approved_and_is_consistent_with_is_approved(db_path):
    assert approval_store.approval_state("plan-v1:missing") == approval_store.ABSENT
    approval_store.grant("plan-v1:present", "stan")
    assert approval_store.approval_state("plan-v1:present") == approval_store.APPROVED
    # is_approved is the boolean view of the same fact.
    assert approval_store.is_approved("plan-v1:present") is True
    assert approval_store.is_approved("plan-v1:missing") is False


def test_a_store_fault_is_unknown_not_absent(db_path, monkeypatch):
    """A database error must answer UNKNOWN, not ABSENT — the whole point of #696.

    ``is_approved`` still collapses the fault to False (fail-closed), but ``approval_state`` preserves
    that CAO could not read the store, so the gate can refuse without calling it a missing approval.
    """

    def unreadable_store(*_args, **_kwargs):
        raise OperationalError("database is locked")

    monkeypatch.setattr(approval_store, "_connect", unreadable_store)

    assert approval_store.approval_state("plan-v1:anything") == approval_store.UNKNOWN
    # The boolean view stays fail-closed: a fault is not permission.
    assert approval_store.is_approved("plan-v1:anything") is False


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_the_table_exists_on_a_fresh_database_with_its_three_columns(db_path):
    """The unit's whole mitigation for a SILENT migration failure.

    ``_migrate_workflow_plan_approval`` swallows failure at debug level, so its return says nothing about the
    schema. A missing table makes every lookup answer False, refusing every run — diagnosed far from its cause.
    """
    approval_store.is_approved("anything")  # forces a connect, which runs the migrator

    cols = _columns(db_path)
    assert set(cols) == {"plan_id", "approved_at", "approved_by"}
    assert (
        cols["plan_id"]["primary_key"] is True
    ), "plan_id must be the PRIMARY KEY — one approval per plan, enforced by the DB"
    assert cols["approved_at"]["required"] is True, "approved_at is NOT NULL"
    assert cols["approved_by"]["required"] is True, "approved_by is NOT NULL"


def test_the_migration_is_idempotent(db_path):
    migrate_component(db_path, PRIVATE_PLAN_MIGRATIONS)
    migrate_component(db_path, PRIVATE_PLAN_MIGRATIONS)
    assert set(_columns(db_path)) == {"plan_id", "approved_at", "approved_by"}


def test_it_works_against_a_repointed_database_without_init_db(db_path):
    """The condition that made migrator-on-connect necessary. ``init_db`` is never called in this module."""
    assert set(_columns(db_path)) == {"plan_id", "approved_at", "approved_by"}
    approval_store.grant("plan-v1:fresh", "stan")
    assert approval_store.is_approved("plan-v1:fresh") is True


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------


def test_grant_then_is_approved(db_path):
    assert approval_store.is_approved("plan-v1:x") is False
    approval_store.grant("plan-v1:x", "stan")
    assert approval_store.is_approved("plan-v1:x") is True


def test_one_grant_does_not_approve_a_different_plan(db_path):
    """The whole re-approval mechanism: a changed plan is a different plan_id and finds no row."""
    approval_store.grant("plan-v1:aaa", "stan")
    assert approval_store.is_approved("plan-v1:bbb") is False


def test_plan_id_is_stored_verbatim_and_never_normalised(db_path):
    """A normalisation is how two distinct plans could come to share one approval."""
    for value in ("plan-v1:abc", "v2:legacy-looking", "no-scheme-at-all", "  spaced  "):
        approval_store.grant(value, "stan")
        record = approval_store.get_approval(value)
        assert record is not None
        assert record.plan_id == value


def test_get_approval_returns_the_record_and_none_when_absent(db_path):
    assert approval_store.get_approval("plan-v1:missing") is None
    approval_store.grant("plan-v1:present", "stan")
    record = approval_store.get_approval("plan-v1:present")
    assert record is not None
    assert record.approved_by == "stan"
    assert record.approved_at.endswith(
        "+00:00"
    ), "timestamps are UTC ISO, matching workflow_run.started_at"


# ---------------------------------------------------------------------------
# The surface, whose ABSENCES are the control
# ---------------------------------------------------------------------------


def test_the_module_offers_no_update_delete_or_replacing_upsert():
    """The absence IS the security control — an update path could transfer an approval to a changed plan.

    Asserted on the public surface rather than by behaviour, because the property is the absence of a
    capability. Same shape as pass 2A's leaf-module posture tests.
    """
    public = {n for n in dir(approval_store) if not n.startswith("_")}
    for forbidden in (
        "update",
        "delete",
        "revoke",
        "upsert",
        "replace",
        "set_approval",
        "update_approval",
    ):
        assert forbidden not in public, f"{forbidden} must not exist on this module"

    source = open(approval_store.__file__, encoding="utf-8").read()
    code = "".join(source.replace('"""', "\x00").split("\x00")[::2])
    assert "UPDATE workflow_plan_approval" not in code
    assert "DELETE FROM workflow_plan_approval" not in code
    assert "INSERT OR REPLACE" not in code
    assert "DO UPDATE" not in code, "a replacing upsert would defeat write-once"
    assert "ON CONFLICT (plan_id) DO NOTHING" in code


def test_database_enforces_immutable_approval(db_path):
    from cli_agent_orchestrator.clients.postgres import IntegrityError

    approval_store.grant("plan-v1:fixed", "original")
    with connect(db_path) as connection:
        for statement in (
            "UPDATE workflow_plan_approval SET approved_by='replacement'",
            "DELETE FROM workflow_plan_approval",
        ):
            with pytest.raises(IntegrityError):
                connection.execute(statement)
    assert approval_store.get_approval("plan-v1:fixed").approved_by == "original"


def test_concurrent_grants_preserve_one_original_record(db_path):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                lambda actor: approval_store.grant("plan-v1:race", actor), ["a", "b", "c", "d"]
            )
        )
    first = approval_store.get_approval("plan-v1:race")
    assert first.approved_by in {"a", "b", "c", "d"}
    approval_store.grant("plan-v1:race", "later")
    assert approval_store.get_approval("plan-v1:race") == first
