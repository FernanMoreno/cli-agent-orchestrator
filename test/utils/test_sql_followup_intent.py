"""Follow-up receipts describe requests, never physical SQL authority."""

import importlib
from dataclasses import FrozenInstanceError, replace
from uuid import uuid4

import pytest

pytestmark = pytest.mark.offline_database


def receipt():
    module = importlib.import_module("cli_agent_orchestrator.models.sql_opening_intent")
    cls = getattr(module, "ChildSqlFollowupIntent", None)
    assert cls is not None, "missing durable follow-up request contract"
    return cls(
        intent_id=str(uuid4()),
        journal_id=str(uuid4()),
        ordinal=2,
        startup_intent_id=str(uuid4()),
        sequence=1,
        path="native",
        destination_scope="main",
        destination_sha256="a" * 64,
        subject_binding_sha256="b" * 64,
        subject_sql_application_name="caos-sql-v1-" + uuid4().hex,
    )


def test_followup_has_an_independent_request_and_original_startup_anchor():
    value = receipt()
    assert value.intent_id != value.startup_intent_id and value.sequence == 1
    with pytest.raises(FrozenInstanceError):
        value.sequence = 2
    assert not any(hasattr(value, name) for name in ("claim", "grant", "finish", "release"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("destination_scope", ""),
        ("destination_scope", "x" * 129),
        ("sequence", True),
        ("sequence", 0),
        ("sequence", 2**63),
        ("ordinal", False),
        ("ordinal", -1),
        ("intent_id", "invalid"),
        ("startup_intent_id", "invalid"),
        ("path", "sql"),
        ("destination_sha256", "A" * 64),
        ("subject_sql_application_name", "caos"),
    ],
)
def test_ambiguous_followup_receipts_are_rejected(field, value):
    with pytest.raises(ValueError):
        replace(receipt(), **{field: value})


def test_followup_registration_is_an_explicit_repository_operation():
    from cli_agent_orchestrator.clients.sql_intent_repository import SqlIntentRepository

    assert callable(getattr(SqlIntentRepository, "record_child_followup", None))
    assert callable(getattr(SqlIntentRepository, "read_child_followup", None))


def test_followup_migration_preserves_prior_checksums_and_table_memberships():
    from cli_agent_orchestrator.clients.sql_opening_schema import SQL_OPENING_MIGRATIONS

    assert tuple(m.checksum for m in SQL_OPENING_MIGRATIONS[:2]) == (
        "71678a014d4780947d4c093152eee9d4e57e995fcfeaa2dfdd44d9945b82f7ed",
        "c6e27af1094d494a3884e753e72210ea4dd22758d41db3c2fdccfbb56d65e2ad",
    )
    assert all("_cao_sql_child_followups" not in m.tables for m in SQL_OPENING_MIGRATIONS[:2])
    assert "_cao_sql_child_followups" in SQL_OPENING_MIGRATIONS[2].tables
