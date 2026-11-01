"""A foreign custodian releases only the original owner's durable physical fence."""

from dataclasses import replace
from test.postgres.test_admission_admin_abort import ACTOR, abort, actions, pair, prepare
from test.postgres.test_admission_reservations import (  # noqa: F401 - pair dependency
    request,
    shared,
)
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch

pytestmark = pytest.mark.postgres


def active(pair):
    row = prepare(pair)
    return pair.owner.transition(row.operation_id, expected_revision=1, state="active")


def fence(pair, operation, *, original_binding=None):
    return pair.owner.fence_physically_closed(
        operation.operation_id,
        expected_revision=operation.revision,
        original_binding=original_binding or pair.foreign,
    )


def finalize(pair, operation, *, action_id=None, expected_revision=None):
    return pair.custodian.admin_release_witnessed(
        operation.operation_id,
        action_id=action_id or str(uuid4()),
        expected_revision=operation.revision if expected_revision is None else expected_revision,
        principal_id=ACTOR,
        expected_binding=pair.binding,
    )


def witnessed_actions(pair):
    with connect(pair.target, readonly=True) as connection:
        return connection.execute(
            sql.SQL("SELECT * FROM {} ORDER BY action_id").format(
                sql.Identifier(pair.target.schema, "_cao_resource_witness_releases")
            )
        ).fetchall()


def test_foreign_witness_release_is_atomic_audited_idempotent_and_keeps_metadata(pair):
    operation = active(pair)
    closed = fence(pair, operation)
    assert closed.state == "releasing" and closed.revision == 3
    assert fence(pair, operation) == closed
    assert pair.custodian.used(pair.storage.spec.domain_id) == 3
    action_id = str(uuid4())
    receipt = finalize(pair, closed, action_id=action_id)
    assert (receipt.action_id, receipt.operation_id, receipt.revision, receipt.state) == (
        action_id,
        operation.operation_id,
        4,
        "released",
    )
    assert finalize(pair, closed, action_id=action_id) == receipt
    row = pair.owner.read_operation(operation.operation_id)
    assert row.state == "released" and row.revision == 4
    assert (
        pair.owner.transition(operation.operation_id, expected_revision=3, state="released") == row
    )
    assert pair.custodian.used(pair.storage.spec.domain_id) == 2
    audit = witnessed_actions(pair)
    assert len(audit) == 1 and str(audit[0]["action_id"]) == action_id
    assert audit[0]["from_state"] == "releasing" and audit[0]["from_revision"] == 3
    assert audit[0]["to_state"] == "released" and audit[0]["to_revision"] == 4
    assert len(audit[0]["allocations_json"]) == 5
    assert {item["unit"] for item in audit[0]["allocations_json"]} == {
        "bytes",
        "millicores",
        "postgres_session",
    }
    assert pair.owner.metadata_credits_current() == (pair.foreign_credit,)
    assert pair.custodian.metadata_credits_current() == (pair.own_credit,)
    assert pair.custodian.audit_usage() == ()


@pytest.mark.parametrize("state", ["preparing", "active", "uncertain", "releasing"])
def test_unproven_states_are_not_foreign_closure_witnesses(pair, state):
    operation = prepare(pair)
    if state != "preparing":
        operation = pair.owner.transition(
            operation.operation_id, expected_revision=1, state="active"
        )
    if state in {"uncertain", "releasing"}:
        operation = pair.owner.transition(operation.operation_id, expected_revision=2, state=state)
    with pytest.raises(pair.module.SharedRevisionConflict):
        finalize(pair, operation, expected_revision=max(3, operation.revision))
    assert pair.custodian.used(pair.storage.spec.domain_id) == 3
    assert pair.owner.read_operation(operation.operation_id) == operation
    assert witnessed_actions(pair) == []


def test_original_physical_closure_survives_view_and_membership_change(pair, monkeypatch):
    operation = active(pair)
    pair.custodian.provision_member(
        pair.storage.spec.domain_id,
        pair.foreign,
        expected_revision=1,
        principal_id=ACTOR,
        active=False,
    )
    monkeypatch.setattr(
        pair.owner,
        "_binding_supplier",
        lambda: replace(pair.foreign, resource_view_sha256="e" * 64),
    )
    closed = fence(pair, operation, original_binding=pair.foreign)
    assert closed.state == "releasing" and closed.revision == 3
    assert finalize(pair, closed).state == "released"
    assert pair.custodian.used(pair.storage.spec.domain_id) == 2


def test_witness_cannot_be_relabelled_uncertain_and_owner_cleanup_still_converges(pair):
    closed = fence(pair, active(pair))
    with pytest.raises(pair.module.SharedRevisionConflict):
        pair.owner.transition(closed.operation_id, expected_revision=3, state="uncertain")
    assert pair.owner.read_operation(closed.operation_id) == closed
    assert pair.custodian.used(pair.storage.spec.domain_id) == 3
    row = pair.owner.transition(closed.operation_id, expected_revision=3, state="released")
    assert row.state == "released" and row.revision == 4
    assert pair.custodian.used(pair.storage.spec.domain_id) == 2


def test_applied_v1_to_v5_checksums_are_frozen_and_v6_is_additive():
    from cli_agent_orchestrator.clients.admission_schema import RESOURCE_ADMISSION_MIGRATIONS

    expected = {
        1: "6d1c5b17a332de83c647c2498f757da432ac13b502b2cead06d4c3950f5ef49e",
        2: "c183ecaffafcce3a3166fea88a0aea54bfd00d5820b20e9e5594105f1a2c8f40",
        3: "e4881dd35a2c59659a6395d31dd342c8cd7b0b9e6946998e4419cdd04eff98af",
        4: "1faa34ac79c790de65d8776a8a078360b9788cdf259d359ac12e0eefe85a01c1",
        5: "9cb73d9762cb8c0d846bb2ecdee811744c6c72029b830d623c2452e43762f8aa",
    }
    assert {m.version: m.checksum for m in RESOURCE_ADMISSION_MIGRATIONS[:5]} == expected
    assert [m.version for m in RESOURCE_ADMISSION_MIGRATIONS] == [1, 2, 3, 4, 5, 6, 7]
    assert len(RESOURCE_ADMISSION_MIGRATIONS[4].tables) == 10
    assert len(RESOURCE_ADMISSION_MIGRATIONS[5].tables) == 11
    assert (
        RESOURCE_ADMISSION_MIGRATIONS[5].checksum
        == "ee8f728301be19bc59c6574cefb69cb4df6819f8b943629bf30f289d3d1e7208"
    )
    assert len(RESOURCE_ADMISSION_MIGRATIONS[6].tables) == 12
    assert set(RESOURCE_ADMISSION_MIGRATIONS[6].tables) - set(
        RESOURCE_ADMISSION_MIGRATIONS[5].tables
    ) == {"_cao_resource_generation_seals"}


def test_observed_counter_underflow_blocks_new_admission_before_sql_without_repair(
    pair, monkeypatch
):
    operation = prepare(pair)
    assert pair.custodian.audit_usage() == ()
    with connect(pair.target.migration_target()) as writer:
        writer.execute(
            sql.SQL("UPDATE {} SET committed=0 WHERE domain_id=%s").format(
                sql.Identifier(pair.target.schema, "_cao_resource_usage")
            ),
            (pair.storage.spec.domain_id,),
        )
    with pytest.raises(SchemaMismatch):
        abort(pair, operation)
    assert pair.custodian.used(pair.storage.spec.domain_id) == 0
    assert pair.owner.read_operation(operation.operation_id).state == "preparing"
    assert actions(pair) == []

    def forbidden(*args, **kwargs):
        pytest.fail("known counter corruption admitted a fresh SQL attempt")

    with monkeypatch.context() as patch:
        patch.setattr(pair.module, "connect", forbidden)
        with pytest.raises(pair.module.SharedAdmissionDenied):
            pair.custodian.reserve(str(uuid4()), (request(pair.storage),))


@pytest.mark.parametrize("fault", ["usage", "binding"])
def test_witness_release_rolls_back_every_counter_and_audit_on_precommit_invalidation(
    pair, monkeypatch, fault
):
    operation = fence(pair, active(pair))
    action_id = str(uuid4())
    original = pair.custodian._change_used
    supplier = pair.custodian._binding_supplier
    injected = []

    def invalidate(*args):
        original(*args)
        if not injected:
            injected.append(True)
            if fault == "usage":
                with pair.custodian._usage_guard:
                    pair.custodian._usage_uncertain = True
            else:
                pair.custodian._binding_supplier = lambda: replace(
                    pair.binding, resource_view_sha256="e" * 64
                )

    with monkeypatch.context() as patch:
        patch.setattr(pair.custodian, "_change_used", invalidate)
        try:
            with pytest.raises(pair.module.SharedAdmissionDenied):
                finalize(pair, operation, action_id=action_id)
        finally:
            pair.custodian._binding_supplier = supplier
    assert injected == [True]
    row = pair.owner.read_operation(operation.operation_id)
    assert row.state == "releasing" and row.revision == 3
    assert witnessed_actions(pair) == []
    assert pair.custodian.audit_usage() == ()
    assert pair.custodian.used(pair.storage.spec.domain_id) == 3
    assert finalize(pair, operation, action_id=action_id).state == "released"
    assert pair.custodian.used(pair.storage.spec.domain_id) == 2
    assert len(witnessed_actions(pair)) == 1


@pytest.mark.parametrize("fault", ["ack", "binding", "interface", "storage_close"])
def test_witness_release_committed_without_ack_preserves_action_identity_and_replay(
    pair, monkeypatch, fault
):
    from contextlib import contextmanager

    operation = fence(pair, active(pair))
    action_id = str(uuid4())
    original = pair.module.connect
    supplier = pair.custodian._binding_supplier
    injected = []

    @contextmanager
    def lose_ack(target, *args, **kwargs):
        with original(target, *args, **kwargs) as connection:
            yield connection
        if target.user != pair.target.user and not injected:
            injected.append(True)
            if fault == "ack":
                raise OSError("synthetic postcommit transport loss")
            if fault == "interface":
                raise psycopg.InterfaceError("synthetic postcommit interface loss")
            if fault == "storage_close":
                from cli_agent_orchestrator.clients.storage_admission import (
                    StorageAdmissionUnavailable,
                )

                raise StorageAdmissionUnavailable()
            pair.custodian._binding_supplier = lambda: replace(
                pair.binding, resource_view_sha256="e" * 64
            )

    with monkeypatch.context() as patch:
        patch.setattr(pair.module, "connect", lose_ack)
        try:
            with pytest.raises(pair.module.SharedActionCommitUncertain) as caught:
                finalize(pair, operation, action_id=action_id)
            assert caught.value.action_id == action_id
            assert caught.value.operation_id == operation.operation_id
        finally:
            pair.custodian._binding_supplier = supplier
    assert injected == [True]
    row = pair.owner.read_operation(operation.operation_id)
    assert row.state == "released" and row.revision == 4
    assert pair.custodian.used(pair.storage.spec.domain_id) == 2
    assert len(witnessed_actions(pair)) == 1
    assert finalize(pair, operation, action_id=action_id).state == "released"
    assert pair.custodian.used(pair.storage.spec.domain_id) == 2
    assert len(witnessed_actions(pair)) == 1
    assert pair.custodian.audit_usage() == ()


@pytest.mark.parametrize("first_kind", ["preparing", "witnessed"])
def test_action_identifier_cannot_cross_release_kinds(pair, first_kind):
    first = prepare(pair)
    second = fence(pair, active(pair))
    action_id = str(uuid4())
    if first_kind == "preparing":
        abort(pair, first, action_id=action_id)
        with pytest.raises(pair.module.SharedRevisionConflict):
            finalize(pair, second, action_id=action_id)
        assert len(actions(pair)) == 1 and witnessed_actions(pair) == []
        assert pair.owner.read_operation(second.operation_id).state == "releasing"
    else:
        finalize(pair, second, action_id=action_id)
        with pytest.raises(pair.module.SharedRevisionConflict):
            abort(pair, first, action_id=action_id)
        assert actions(pair) == [] and len(witnessed_actions(pair)) == 1
        assert pair.owner.read_operation(first.operation_id).state == "preparing"
    assert pair.custodian.used(pair.storage.spec.domain_id) == 3
    assert pair.custodian.audit_usage() == ()


@pytest.mark.parametrize("revision", [True, 0, 1, 2, 2**63 - 1, "3"])
def test_invalid_witness_revision_is_refused_before_any_sql(pair, monkeypatch, revision):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid closure revision reached SQL")

    monkeypatch.setattr(pair.module, "connect", forbidden)
    with pytest.raises(ValueError):
        pair.custodian.admin_release_witnessed(
            str(uuid4()),
            action_id=str(uuid4()),
            expected_revision=revision,
            principal_id=ACTOR,
            expected_binding=pair.binding,
        )


def test_original_fence_lost_ack_is_reconciled_without_second_marker(pair, monkeypatch):
    from contextlib import contextmanager

    operation = active(pair)
    original = pair.module.connect
    injected = []

    @contextmanager
    def lost(*args, **kwargs):
        with original(*args, **kwargs) as connection:
            yield connection
        if not injected:
            injected.append(True)
            raise OSError("synthetic owned fence acknowledgement loss")

    with monkeypatch.context() as patch:
        patch.setattr(pair.module, "connect", lost)
        with pytest.raises(OSError):
            fence(pair, operation)
    assert injected == [True]
    closed = fence(pair, operation)
    assert closed.state == "releasing" and closed.revision == 3
    assert pair.custodian.used(pair.storage.spec.domain_id) == 3
    assert witnessed_actions(pair) == []
    finalize(pair, closed)
    assert fence(pair, operation).state == "released"
    assert pair.custodian.used(pair.storage.spec.domain_id) == 2
    assert pair.custodian.audit_usage() == ()


def test_rebound_members_cannot_admit_new_view_using_retained_old_metadata(pair, monkeypatch):
    moved = replace(pair.foreign, resource_view_sha256="d" * 64)
    for domain in pair.domains:
        pair.custodian.provision_member(
            domain.domain_id, moved, expected_revision=1, principal_id=ACTOR
        )
    monkeypatch.setattr(pair.owner, "_binding_supplier", lambda: moved)
    with pytest.raises(pair.module.SharedAdmissionDenied):
        pair.owner.reserve_for_cost(
            str(uuid4()), pair.module.SharedResourceCost(1024**2, 100, 1), require_metadata=True
        )
    with pytest.raises(pair.module.SharedRevisionConflict):
        pair.custodian.grant_metadata_credit(
            str(uuid4()), pair.storage.spec.domain_id, moved, principal_id=ACTOR
        )
    assert pair.custodian.used(pair.storage.spec.domain_id) == 2
    assert pair.custodian.audit_usage() == ()


def test_v5_upgrade_keeps_every_historical_state_unmarked_and_old_reader_closed(postgres_targets):
    from cli_agent_orchestrator.clients.admission_schema import RESOURCE_ADMISSION_MIGRATIONS
    from cli_agent_orchestrator.clients.postgres.migrations import (
        migrate_component,
        verify_component,
    )

    target = postgres_targets.main
    previous = RESOURCE_ADMISSION_MIGRATIONS[:5]
    migrate_component(target, previous)
    states = ("preparing", "active", "uncertain", "releasing", "released")
    with connect(target) as writer:
        for revision, state in enumerate(states, 1):
            writer.execute(
                sql.SQL("""INSERT INTO {} (operation_id,instance_id,process_generation,
                    process_pid,process_started_at,binding_sha256,request_sha256,state,revision)
                    VALUES (%s,%s,%s,1,1,%s,%s,%s,%s)""").format(
                    sql.Identifier(target.schema, "_cao_resource_operations")
                ),
                (str(uuid4()), str(uuid4()), str(uuid4()), "a" * 64, "b" * 64, state, revision),
            )
        verify_component(writer, previous)
        with pytest.raises(SchemaMismatch):
            verify_component(writer, RESOURCE_ADMISSION_MIGRATIONS)
    previous6 = RESOURCE_ADMISSION_MIGRATIONS[:6]
    migrate_component(target, previous6)
    with connect(target, readonly=True) as reader:
        verify_component(reader, previous6)
        old_rows = reader.execute(
            sql.SQL(
                "SELECT state,revision,closure_witness_revision FROM {} ORDER BY revision"
            ).format(sql.Identifier(target.schema, "_cao_resource_operations"))
        ).fetchall()
        assert old_rows == [(state, revision, None) for revision, state in enumerate(states, 1)]
        with pytest.raises(SchemaMismatch):
            verify_component(reader, previous)
        with pytest.raises(SchemaMismatch):
            verify_component(reader, RESOURCE_ADMISSION_MIGRATIONS)
    migrate_component(target, RESOURCE_ADMISSION_MIGRATIONS)
    with connect(target, readonly=True) as reader:
        rows = reader.execute(
            sql.SQL(
                "SELECT state,revision,closure_witness_revision FROM {} ORDER BY revision"
            ).format(sql.Identifier(target.schema, "_cao_resource_operations"))
        ).fetchall()
        assert rows == [(state, revision, None) for revision, state in enumerate(states, 1)]
        verify_component(reader, RESOURCE_ADMISSION_MIGRATIONS)
        with pytest.raises(SchemaMismatch):
            verify_component(reader, previous)

        with pytest.raises(SchemaMismatch):
            verify_component(reader, previous6)


def test_witness_audit_remains_owner_written_after_generic_later_grants(pair):
    from cli_agent_orchestrator.clients.postgres.migrations import (
        ComponentMigration,
        migrate_component,
    )

    migrate_component(
        pair.target,
        (
            ComponentMigration(
                "closure_grant_probe",
                "main",
                1,
                ("CREATE TABLE closure_grant_probe (id INTEGER)",),
                ("closure_grant_probe",),
            ),
        ),
    )
    assert witnessed_actions(pair) == []
    for statement in (
        "INSERT INTO {} DEFAULT VALUES",
        "UPDATE {} SET from_revision=3 WHERE false",
        "DELETE FROM {} WHERE false",
    ):
        with connect(pair.target) as runtime:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                runtime.execute(
                    sql.SQL(statement).format(
                        sql.Identifier(pair.target.schema, "_cao_resource_witness_releases")
                    )
                )
