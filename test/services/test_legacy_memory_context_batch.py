from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import psycopg
import pytest
from psycopg.pq import TransactionStatus

from cli_agent_orchestrator.clients import work_repository as work_module
from cli_agent_orchestrator.clients.postgres import Connection
from cli_agent_orchestrator.clients.postgres.migrations import (
    SchemaMismatch as PostgresSchemaMismatch,
)
from cli_agent_orchestrator.services import legacy_memory_audit_repository as audit_module
from cli_agent_orchestrator.services.legacy_memory_audit_repository import (
    _CONTEXT_BATCH_FALLBACK,
    _CONTEXT_BATCH_LOCK_SQL,
    _CONTEXT_BATCH_SAVEPOINT,
    _CONTEXT_BATCH_SQL,
    PooledAuditLeaseExpired,
    _LeaseState,
    _LegacyMemoryAuditWorkRepository,
    _PooledAuditConnection,
)

pytestmark = pytest.mark.offline_database

IDENTITY = "12345678-1234-5678-1234-567812345678"
STORE_UUID = UUID(IDENTITY).hex
INSTALLATION_UUID = "0123456789abcdef0123456789abcdef"
TEST_SAVEPOINT = f"{_CONTEXT_BATCH_SAVEPOINT}_0123456789abcdef"


def inbox_row(**overrides):
    row = {"singleton": 1, "store_identity": IDENTITY, "store_uuid": STORE_UUID}
    row.update(overrides)
    return row


def control_row(**overrides):
    row = {"singleton": 1, "store_identity": IDENTITY, "store_uuid": STORE_UUID}
    row.update(overrides)
    return row


def recovery_row(**overrides):
    row = {
        "singleton": 1,
        "context_version": 1,
        "execution_state": "normal",
        "installation_uuid": INSTALLATION_UUID,
        "source_installation_uuid": None,
        "bundle_digest": None,
        "restore_receipt": None,
        "created_at": 123.5,
    }
    row.update(overrides)
    return row


def batch_rows(inbox=None, control=None, recovery=None):
    inbox = inbox if inbox is not None else inbox_row()
    control = control if control is not None else control_row()
    recovery = recovery if recovery is not None else recovery_row()
    common = {
        "context_version": None,
        "execution_state": None,
        "installation_uuid": None,
        "source_installation_uuid": None,
        "bundle_digest": None,
        "restore_receipt": None,
        "created_at": None,
    }
    return [
        {"context_kind": "inbox", **common, **inbox},
        {"context_kind": "control", **common, **control},
        {
            "context_kind": "recovery",
            "singleton": recovery["singleton"],
            "store_identity": None,
            "store_uuid": None,
            **recovery,
        },
    ]


class Cursor:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)


class NativeStub(Connection):
    def __init__(self, responses=None, fail_query=None):
        self.target = SimpleNamespace(store_id=IDENTITY)
        self.queries = []
        self.responses = responses or {}
        self.fail_query = fail_query

    def execute(self, statement, params=(), *, prepare=None):
        self.queries.append(statement)
        if statement == self.fail_query:
            raise psycopg.errors.UndefinedTable("private test relation absent")
        return Cursor(self.responses.get(statement, ()))


class PooledStub(_PooledAuditConnection):
    def __init__(
        self,
        rows=None,
        fallback=False,
        responses=None,
        fail_query=None,
        in_transaction=True,
    ):
        self.target = SimpleNamespace(store_id=IDENTITY)
        self.queries = []
        self.rows = rows if rows is not None else batch_rows()
        self.fallback = fallback
        self.responses = responses or {}
        self.fail_query = fail_query
        self.transaction_state = in_transaction

    @property
    def in_transaction(self):
        return self.transaction_state

    def _batch_verify_work_contexts(self):
        self.queries.append("BATCH")
        return _CONTEXT_BATCH_FALLBACK if self.fallback else self.rows

    def execute(self, statement, params=(), *, prepare=None):
        self.queries.append(statement)
        if statement == self.fail_query:
            raise psycopg.errors.UndefinedTable("private test relation absent")
        return Cursor(self.responses.get(statement, ()))


class FakePipeline:
    def __init__(self, raw):
        self.raw = raw

    def __enter__(self):
        self.raw.active_pipeline = True
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.raw.active_pipeline = False
        self.raw.round_trips += 1
        if exc is not None:
            return False
        if self.raw.fail_at is not None:
            if self.raw.fail_at != f"SAVEPOINT {TEST_SAVEPOINT}":
                self.raw.savepoints.add(TEST_SAVEPOINT)
            self.raw.aborted = True
            raise self.raw.pipeline_error
        self.raw.savepoints.discard(TEST_SAVEPOINT)
        return False


class RawPipelineStub:
    def __init__(self, fail_at=None, fail_recovery=False):
        self.info = SimpleNamespace(transaction_status=TransactionStatus.INTRANS)
        self.active_pipeline = False
        self.round_trips = 0
        self.fail_at = fail_at
        self.fail_recovery = fail_recovery
        self.pipeline_error = psycopg.errors.InsufficientPrivilege("private test query denied")
        self.cleanup_error = psycopg.OperationalError("private test cleanup failed")
        self.queued = []
        self.direct = []
        self.aborted = False
        self.savepoints = {_CONTEXT_BATCH_SAVEPOINT}
        self.full_rollback_called = False

    def pipeline(self):
        return FakePipeline(self)

    def execute(self, statement):
        if self.active_pipeline:
            self.queued.append(statement)
            if statement == _CONTEXT_BATCH_SQL:
                return Cursor(batch_rows())
            return Cursor()
        self.direct.append(statement)
        if self.fail_recovery in (True, "rollback") and statement.startswith(
            "ROLLBACK TO SAVEPOINT"
        ):
            raise self.cleanup_error
        if self.fail_recovery == "release" and statement.startswith("RELEASE SAVEPOINT"):
            raise self.cleanup_error
        if statement.startswith("ROLLBACK TO SAVEPOINT"):
            savepoint = statement.removeprefix("ROLLBACK TO SAVEPOINT ")
            if savepoint not in self.savepoints:
                raise psycopg.errors.InvalidSavepointSpecification(
                    "private savepoint does not exist"
                )
            self.aborted = False
        elif statement.startswith("RELEASE SAVEPOINT"):
            savepoint = statement.removeprefix("RELEASE SAVEPOINT ")
            if savepoint not in self.savepoints:
                raise psycopg.errors.InvalidSavepointSpecification(
                    "private savepoint does not exist"
                )
            self.savepoints.remove(savepoint)
        return Cursor()

    def rollback(self):
        self.full_rollback_called = True


class PooledRawStub(_PooledAuditConnection):
    """Exercise the real lease pipeline method with fake sequential cursors."""

    def __init__(self, raw, responses=None, fail_query=None):
        self._pooled_raw = raw
        self._lease_state = _LeaseState()
        self._raw_view = object()
        self.target = SimpleNamespace(store_id=IDENTITY)
        self.responses = responses or {}
        self.fail_query = fail_query
        self.queries = []

    @property
    def in_transaction(self):
        return self._lease_state.call(
            lambda: self._pooled_raw.info.transaction_status != TransactionStatus.IDLE
        )

    def execute(self, statement, params=(), *, prepare=None):
        self.queries.append(statement)
        if statement == self.fail_query:
            raise psycopg.errors.UndefinedTable("private test relation absent")
        return Cursor(self.responses.get(statement, ()))


class TailBatchTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.savepoint = patch.object(
            audit_module,
            "_new_context_batch_savepoint",
            return_value=TEST_SAVEPOINT,
            create=True,
        )
        self.savepoint.start()
        self.component = patch.object(
            work_module,
            "verify_component",
            side_effect=lambda *args: self.events.append("component"),
        )
        self.role = patch.object(
            work_module,
            "verify_runtime_role",
            side_effect=lambda *args: self.events.append("role"),
        )
        self.component.start()
        self.role.start()
        self.addCleanup(self.savepoint.stop)
        self.addCleanup(self.component.stop)
        self.addCleanup(self.role.stop)

    def test_savepoint_factory_uses_distinct_fixed_width_safe_identifiers(self):
        self.savepoint.stop()
        try:
            factory = getattr(audit_module, "_new_context_batch_savepoint", None)
            self.assertIsNotNone(factory, "per-call savepoint factory is required")
            with patch.object(
                audit_module.secrets,
                "token_hex",
                side_effect=("0" * 16, "f" * 16),
            ):
                names = (
                    factory(),
                    factory(),
                )
        finally:
            self.savepoint.start()

        self.assertEqual(
            names,
            (
                f"{_CONTEXT_BATCH_SAVEPOINT}_{'0' * 16}",
                f"{_CONTEXT_BATCH_SAVEPOINT}_{'f' * 16}",
            ),
        )
        self.assertNotEqual(names[0], names[1])
        self.assertTrue(all(name.isascii() and name.isidentifier() for name in names))

    def test_successful_private_batch_uses_one_sync_and_returns_only_rows(self):
        pooled = object.__new__(_PooledAuditConnection)
        raw = RawPipelineStub()
        pooled._pooled_raw = raw
        pooled._lease_state = _LeaseState()
        pooled._raw_view = object()
        pooled.target = SimpleNamespace(store_id=IDENTITY)

        rows = pooled._batch_verify_work_contexts()

        self.assertEqual(raw.round_trips, 1)
        self.assertEqual(raw.queued[0], f"SAVEPOINT {TEST_SAVEPOINT}")
        self.assertEqual(raw.queued[1], _CONTEXT_BATCH_LOCK_SQL)
        self.assertEqual(raw.queued[2], _CONTEXT_BATCH_SQL)
        self.assertEqual(raw.queued[3], f"RELEASE SAVEPOINT {TEST_SAVEPOINT}")
        self.assertEqual(len(rows), 3)
        self.assertFalse(pooled._lease_state.tainted)
        self.assertIsNot(rows, raw)

    def test_batch_statement_has_three_explicitly_typed_context_sources(self):
        self.assertEqual(_CONTEXT_BATCH_SQL.count("UNION ALL"), 2)
        self.assertLess(
            _CONTEXT_BATCH_SQL.index("FROM work_inbox_store_context"),
            _CONTEXT_BATCH_SQL.index("FROM work_store_control"),
        )
        self.assertLess(
            _CONTEXT_BATCH_SQL.index("FROM work_store_control"),
            _CONTEXT_BATCH_SQL.index("FROM work_recovery_context"),
        )
        for cast in (
            "singleton::bigint",
            "store_identity::text",
            "store_uuid::text",
            "context_version::bigint",
            "created_at::double precision",
            "NULL::double precision",
        ):
            self.assertIn(cast, _CONTEXT_BATCH_SQL)

    def test_server_error_rolls_back_savepoint_before_ordered_fallback(self):
        pooled = object.__new__(_PooledAuditConnection)
        raw = RawPipelineStub(fail_at=_CONTEXT_BATCH_LOCK_SQL)
        pooled._pooled_raw = raw
        pooled._lease_state = _LeaseState()
        pooled._raw_view = object()
        pooled.target = SimpleNamespace(store_id=IDENTITY)

        result = pooled._batch_verify_work_contexts()

        self.assertIs(result, _CONTEXT_BATCH_FALLBACK)
        self.assertEqual(raw.round_trips, 1)
        self.assertEqual(
            raw.direct,
            [
                f"ROLLBACK TO SAVEPOINT {TEST_SAVEPOINT}",
                f"RELEASE SAVEPOINT {TEST_SAVEPOINT}",
            ],
        )
        self.assertFalse(pooled._lease_state.tainted)

    def test_server_errors_at_batch_and_release_recover_to_savepoint(self):
        for failed_statement in (
            _CONTEXT_BATCH_LOCK_SQL,
            _CONTEXT_BATCH_SQL,
            f"RELEASE SAVEPOINT {TEST_SAVEPOINT}",
        ):
            with self.subTest(failed_statement=failed_statement):
                pooled = object.__new__(_PooledAuditConnection)
                raw = RawPipelineStub(fail_at=failed_statement)
                pooled._pooled_raw = raw
                pooled._lease_state = _LeaseState()
                pooled._raw_view = object()
                pooled.target = SimpleNamespace(store_id=IDENTITY)

                result = pooled._batch_verify_work_contexts()

                self.assertIs(result, _CONTEXT_BATCH_FALLBACK)
                self.assertEqual(raw.round_trips, 1)
                self.assertEqual(
                    raw.direct,
                    [
                        f"ROLLBACK TO SAVEPOINT {TEST_SAVEPOINT}",
                        f"RELEASE SAVEPOINT {TEST_SAVEPOINT}",
                    ],
                )
                self.assertFalse(raw.full_rollback_called)
                self.assertFalse(pooled._lease_state.tainted)

    def test_pipeline_savepoint_failure_cannot_rollback_outer_transaction(self):
        pooled = object.__new__(_PooledAuditConnection)
        raw = RawPipelineStub(fail_at=f"SAVEPOINT {TEST_SAVEPOINT}")
        pooled._pooled_raw = raw
        pooled._lease_state = _LeaseState()
        pooled._raw_view = object()
        pooled.target = SimpleNamespace(store_id=IDENTITY)

        with self.assertRaises(psycopg.errors.InsufficientPrivilege) as raised:
            pooled._batch_verify_work_contexts()

        self.assertIs(raised.exception, raw.pipeline_error)
        self.assertIsInstance(
            raised.exception.__cause__, psycopg.errors.InvalidSavepointSpecification
        )
        self.assertEqual(raw.direct, [f"ROLLBACK TO SAVEPOINT {TEST_SAVEPOINT}"])
        # A caller's older savepoint with the former fixed name is untouched.
        self.assertEqual(raw.savepoints, {_CONTEXT_BATCH_SAVEPOINT})
        self.assertFalse(raw.full_rollback_called)
        self.assertTrue(pooled._lease_state.tainted)

    def test_real_adapter_fallback_surfaces_inbox_before_later_select_error(self):
        inbox_query = "SELECT singleton,store_identity,store_uuid FROM work_inbox_store_context"
        control_query = "SELECT singleton,store_identity,store_uuid FROM work_store_control"
        for failed_statement in (_CONTEXT_BATCH_LOCK_SQL, _CONTEXT_BATCH_SQL):
            with self.subTest(failed_statement=failed_statement):
                raw = RawPipelineStub(fail_at=failed_statement)
                pooled = PooledRawStub(
                    raw,
                    responses={inbox_query: [inbox_row(store_identity="wrong")]},
                    fail_query=control_query,
                )

                with self.assertRaisesRegex(work_module.SchemaMismatch, "managed inbox context"):
                    _LegacyMemoryAuditWorkRepository._verify(pooled)

                self.assertEqual(
                    raw.direct,
                    [
                        f"ROLLBACK TO SAVEPOINT {TEST_SAVEPOINT}",
                        f"RELEASE SAVEPOINT {TEST_SAVEPOINT}",
                    ],
                )
                self.assertEqual(pooled.queries, [inbox_query])
                self.assertFalse(pooled._lease_state.tainted)

    def test_idle_physical_connection_uses_sequential_fallback(self):
        inbox_query = "SELECT singleton,store_identity,store_uuid FROM work_inbox_store_context"
        control_query = "SELECT singleton,store_identity,store_uuid FROM work_store_control"
        recovery_query = (
            "SELECT singleton,context_version,execution_state,installation_uuid,"
            "source_installation_uuid,bundle_digest,restore_receipt,created_at "
            "FROM work_recovery_context"
        )
        responses = {
            inbox_query: [inbox_row()],
            control_query: [control_row()],
            recovery_query: [
                (
                    1,
                    1,
                    "normal",
                    INSTALLATION_UUID,
                    None,
                    None,
                    None,
                    123.5,
                )
            ],
        }
        raw = RawPipelineStub()
        raw.info.transaction_status = TransactionStatus.IDLE
        pooled = PooledRawStub(raw, responses=responses)

        _LegacyMemoryAuditWorkRepository._verify(pooled)

        self.assertEqual(raw.queued, [])
        self.assertEqual(raw.round_trips, 0)
        self.assertEqual(pooled.queries, [inbox_query, control_query, recovery_query])

    def test_fetch_failure_after_pipeline_sync_taints_the_lease(self):
        class BrokenRows(Cursor):
            def fetchall(self):
                raise psycopg.OperationalError("private fetch failed")

        class BrokenFetchPipelineStub(RawPipelineStub):
            def execute(self, statement):
                if self.active_pipeline:
                    self.queued.append(statement)
                    if statement == _CONTEXT_BATCH_SQL:
                        return BrokenRows(batch_rows())
                    return Cursor()
                self.direct.append(statement)
                return Cursor()

        pooled = object.__new__(_PooledAuditConnection)
        raw = BrokenFetchPipelineStub()
        pooled._pooled_raw = raw
        pooled._lease_state = _LeaseState()
        pooled._raw_view = object()
        pooled.target = SimpleNamespace(store_id=IDENTITY)

        with self.assertRaisesRegex(psycopg.OperationalError, "private fetch failed"):
            pooled._batch_verify_work_contexts()
        self.assertTrue(pooled._lease_state.tainted)

    def test_expired_or_forked_lease_never_starts_pipeline(self):
        for invalidation in ("expired", "forked"):
            with self.subTest(invalidation=invalidation):
                pooled = object.__new__(_PooledAuditConnection)
                raw = RawPipelineStub()
                state = _LeaseState()
                if invalidation == "expired":
                    state.expire()
                else:
                    state.pid += 1
                pooled._pooled_raw = raw
                pooled._lease_state = state
                pooled._raw_view = object()
                pooled.target = SimpleNamespace(store_id=IDENTITY)

                with self.assertRaises(PooledAuditLeaseExpired):
                    pooled._batch_verify_work_contexts()

                self.assertEqual(raw.queued, [])
                self.assertEqual(raw.round_trips, 0)

    def test_savepoint_recovery_failure_preserves_pipeline_error_and_taints(self):
        for failed_cleanup in ("rollback", "release"):
            with self.subTest(failed_cleanup=failed_cleanup):
                pooled = object.__new__(_PooledAuditConnection)
                raw = RawPipelineStub(
                    fail_at=_CONTEXT_BATCH_LOCK_SQL,
                    fail_recovery=failed_cleanup,
                )
                pooled._pooled_raw = raw
                pooled._lease_state = _LeaseState()
                pooled._raw_view = object()
                pooled.target = SimpleNamespace(store_id=IDENTITY)

                with self.assertRaises(psycopg.errors.InsufficientPrivilege) as raised:
                    pooled._batch_verify_work_contexts()

                self.assertIs(raised.exception, raw.pipeline_error)
                self.assertIs(raised.exception.__cause__, raw.cleanup_error)
                self.assertEqual(str(raised.exception), "private test query denied")
                self.assertTrue(pooled._lease_state.tainted)

    def test_batch_validates_inbox_then_control_then_recovery(self):
        pooled = PooledStub(
            rows=batch_rows(
                inbox=inbox_row(store_identity="wrong"),
                control=control_row(store_identity="also-wrong"),
                recovery=recovery_row(context_version=77),
            )
        )
        with self.assertRaisesRegex(work_module.SchemaMismatch, "managed inbox context"):
            _LegacyMemoryAuditWorkRepository._verify(pooled)
        self.assertEqual(self.events, ["component", "role"])
        self.assertEqual(pooled.queries, ["BATCH"])

    def test_batch_checks_contexts_in_contract_order_not_result_order(self):
        rows = batch_rows(
            inbox=inbox_row(store_identity="wrong"),
            control=control_row(store_identity="also-wrong"),
        )
        pooled = PooledStub(rows=list(reversed(rows)))

        with self.assertRaisesRegex(work_module.SchemaMismatch, "managed inbox context"):
            _LegacyMemoryAuditWorkRepository._verify(pooled)

        self.assertEqual(pooled.queries, ["BATCH"])

    def test_batch_preserves_control_and_recovery_failures(self):
        pooled = PooledStub(rows=batch_rows(control=control_row(store_uuid="bad")))
        with self.assertRaisesRegex(work_module.SchemaMismatch, "Work store control identity"):
            _LegacyMemoryAuditWorkRepository._verify(pooled)

        pooled = PooledStub(rows=batch_rows(recovery=recovery_row(context_version=77)))
        with self.assertRaisesRegex(work_module.SchemaMismatch, "recovery context"):
            _LegacyMemoryAuditWorkRepository._verify(pooled)

    def test_batch_accepts_valid_normal_and_blocked_restore_contexts(self):
        _LegacyMemoryAuditWorkRepository._verify(PooledStub())
        blocked = recovery_row(
            execution_state="blocked_restore",
            source_installation_uuid="fedcba9876543210fedcba9876543210",
            bundle_digest="a" * 64,
            restore_receipt="b" * 64,
        )
        _LegacyMemoryAuditWorkRepository._verify(PooledStub(rows=batch_rows(recovery=blocked)))

    def test_speculative_failure_falls_back_and_earlier_inbox_error_wins(self):
        inbox_query = "SELECT singleton,store_identity,store_uuid FROM work_inbox_store_context"
        control_query = "SELECT singleton,store_identity,store_uuid FROM work_store_control"
        pooled = PooledStub(
            fallback=True,
            responses={inbox_query: [inbox_row(store_identity="wrong")]},
            fail_query=control_query,
        )
        with self.assertRaisesRegex(work_module.SchemaMismatch, "managed inbox context"):
            _LegacyMemoryAuditWorkRepository._verify(pooled)
        self.assertEqual(pooled.queries, ["BATCH", inbox_query])

    def test_private_nontransaction_and_generic_connections_keep_three_ordered_reads(
        self,
    ):
        inbox_query = "SELECT singleton,store_identity,store_uuid FROM work_inbox_store_context"
        control_query = "SELECT singleton,store_identity,store_uuid FROM work_store_control"
        recovery_query = (
            "SELECT singleton,context_version,execution_state,installation_uuid,"
            "source_installation_uuid,bundle_digest,restore_receipt,created_at "
            "FROM work_recovery_context"
        )
        responses = {
            inbox_query: [inbox_row()],
            control_query: [control_row()],
            recovery_query: [
                (
                    1,
                    1,
                    "normal",
                    INSTALLATION_UUID,
                    None,
                    None,
                    None,
                    123.5,
                )
            ],
        }
        native = NativeStub(responses=responses)
        _LegacyMemoryAuditWorkRepository._verify(native)
        self.assertEqual(native.queries, [inbox_query, control_query, recovery_query])

        pooled = PooledStub(fallback=True, responses=responses, in_transaction=False)
        _LegacyMemoryAuditWorkRepository._verify(pooled)
        self.assertEqual(pooled.queries, [inbox_query, control_query, recovery_query])
        self.assertEqual(self.events, ["component", "role", "component", "role"])

    def test_common_checks_precede_batch_and_map_component_errors(self):
        pooled = PooledStub()
        self.component.stop()
        with patch.object(
            work_module,
            "verify_component",
            side_effect=PostgresSchemaMismatch("catalog mismatch"),
        ):
            with self.assertRaisesRegex(work_module.SchemaMismatch, "catalog mismatch"):
                _LegacyMemoryAuditWorkRepository._verify(pooled)
        self.assertEqual(self.events, [])
        self.assertEqual(pooled.queries, [])


if __name__ == "__main__":
    unittest.main()
