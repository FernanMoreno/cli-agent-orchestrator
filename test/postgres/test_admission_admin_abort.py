"""Audited CAS abort applies only before the shared data effect can start."""

from dataclasses import replace
from test.postgres.test_admission_metadata_credits import grant
from test.postgres.test_admission_reservations import api, domain, request, shared
from test.postgres.test_shared_admission_mapping import add_domain
from test.services.test_shared_admission_runtime import principal
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.migrations import ComponentMigration, migrate_component
from cli_agent_orchestrator.models.admission_domains import SharedResourceCost
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.admission_domains import ResourceDomainAuthority

pytestmark = pytest.mark.postgres
ACTOR = "principal-" + "b" * 64


@pytest.fixture
def pair(shared):
    target, custodian, binding, module = shared
    storage = domain(shared, 8)
    domains = [storage.spec]
    for kind in ("host", "node"):
        domains.append(add_domain(shared, kind, "bytes", 4096 * 1024**2))
        domains.append(add_domain(shared, kind, "millicores", 16000))
    assert all(type(spec) is module.DomainSpec for spec in domains)
    own_credit = grant(shared, storage)
    foreign = replace(binding, instance_id=str(uuid4()), process_generation=str(uuid4()))
    for record in domains:
        custodian.provision_member(
            record.domain_id, foreign, expected_revision=0, principal_id=ACTOR
        )
    owner = module.ResourceAdmissionRepository(target, binding_supplier=lambda: foreign)
    foreign_credit = owner.grant_metadata_credit(
        str(uuid4()), storage.spec.domain_id, foreign, principal_id=ACTOR
    )
    return SimpleNamespace(
        target=target,
        custodian=custodian,
        owner=owner,
        binding=binding,
        foreign=foreign,
        storage=storage,
        domains=domains,
        own_credit=own_credit,
        foreign_credit=foreign_credit,
        module=module,
    )


def prepare(pair, *, raw=False):
    operation_id = str(uuid4())
    if raw:
        return pair.owner.reserve(operation_id, (request(pair.storage),))
    return pair.owner.reserve_for_cost(
        operation_id, SharedResourceCost(1024**2, 100, 1), require_metadata=True
    )


def abort(pair, operation, *, action_id=None, actor=None, expected_revision=1):
    return ResourceDomainAuthority(pair.custodian).abort_preparing(
        principal=actor if actor is not None else principal(),
        action_id=action_id or str(uuid4()),
        operation_id=operation.operation_id,
        expected_revision=expected_revision,
    )


def actions(pair):
    with connect(pair.target, readonly=True) as reader:
        return reader.execute(
            sql.SQL("SELECT * FROM {} ORDER BY action_id").format(
                sql.Identifier(pair.target.schema, "_cao_resource_admin_actions")
            )
        ).fetchall()


def test_foreign_preparing_abort_is_atomic_audited_and_replayable(pair):
    operation = prepare(pair)
    action_id = str(uuid4())
    assert pair.custodian.used(pair.storage.spec.domain_id) == 3
    receipt = abort(pair, operation, action_id=action_id)
    assert (receipt.action_id, receipt.operation_id, receipt.revision, receipt.state) == (
        action_id,
        operation.operation_id,
        2,
        "released",
    )
    row = pair.owner.read_operation(operation.operation_id)
    assert row.state == "released" and row.revision == 2
    assert abort(pair, operation, action_id=action_id) == receipt
    assert (
        pair.owner.transition(operation.operation_id, expected_revision=1, state="released") == row
    )
    assert pair.custodian.used(pair.storage.spec.domain_id) == 2
    audit = actions(pair)
    assert len(audit) == 1
    assert str(audit[0]["action_id"]) == action_id
    assert audit[0]["actor_principal_id"] == ResourceBootstrap._administrator(principal())
    assert audit[0]["from_state"] == "preparing" and audit[0]["to_state"] == "released"
    assert audit[0]["from_revision"] == 1 and audit[0]["to_revision"] == 2
    assert len(audit[0]["allocations_json"]) == 5
    assert {item["unit"] for item in audit[0]["allocations_json"]} == {
        "bytes",
        "millicores",
        "postgres_session",
    }
    assert pair.custodian.audit_usage() == ()
    assert pair.owner.metadata_credits_current() == (pair.foreign_credit,)
    assert pair.custodian.metadata_credits_current() == (pair.own_credit,)


@pytest.mark.parametrize("state", ["active", "uncertain", "releasing", "released"])
def test_started_or_owner_released_operation_is_never_admin_aborted(pair, state):
    operation = prepare(pair)
    if state == "released":
        pair.owner.transition(operation.operation_id, expected_revision=1, state="released")
    else:
        pair.owner.transition(operation.operation_id, expected_revision=1, state="active")
        if state != "active":
            pair.owner.transition(operation.operation_id, expected_revision=2, state=state)
    before = pair.custodian.used(pair.storage.spec.domain_id)
    with pytest.raises(pair.module.SharedRevisionConflict):
        abort(pair, operation)
    assert actions(pair) == []
    assert pair.custodian.used(pair.storage.spec.domain_id) == before
    assert pair.owner.read_operation(operation.operation_id).state == state


def test_legacy_raw_preparing_contract_is_not_assumed_safe_to_abort(pair):
    operation = prepare(pair, raw=True)
    with pytest.raises(pair.module.SharedAdmissionDenied):
        abort(pair, operation)
    assert pair.owner.read_operation(operation.operation_id).state == "preparing"
    assert pair.custodian.used(pair.storage.spec.domain_id) == 3
    assert actions(pair) == []


def test_action_identity_cannot_be_reused_for_another_operation(pair):
    first = prepare(pair)
    action_id = str(uuid4())
    abort(pair, first, action_id=action_id)
    second = prepare(pair)
    with pytest.raises(pair.module.SharedRevisionConflict):
        abort(pair, second, action_id=action_id)
    with pytest.raises(pair.module.SharedRevisionConflict):
        abort(pair, first, action_id=str(uuid4()))
    assert pair.owner.read_operation(second.operation_id).state == "preparing"
    assert len(actions(pair)) == 1 and pair.custodian.used(pair.storage.spec.domain_id) == 3


@pytest.mark.parametrize("revision", [True, 0, 2])
def test_unsupported_revision_is_denied_before_sql(pair, monkeypatch, revision):
    operation = prepare(pair)

    def forbidden(*args, **kwargs):
        pytest.fail("invalid revision reached SQL")

    monkeypatch.setattr(pair.module, "connect", forbidden)
    with pytest.raises(ValueError):
        abort(pair, operation, expected_revision=revision)


def test_v1_to_v4_checksums_are_preserved_and_v5_is_additive():
    from cli_agent_orchestrator.clients.admission_schema import RESOURCE_ADMISSION_MIGRATIONS

    expected = {
        1: "6d1c5b17a332de83c647c2498f757da432ac13b502b2cead06d4c3950f5ef49e",
        2: "c183ecaffafcce3a3166fea88a0aea54bfd00d5820b20e9e5594105f1a2c8f40",
        3: "e4881dd35a2c59659a6395d31dd342c8cd7b0b9e6946998e4419cdd04eff98af",
        4: "1faa34ac79c790de65d8776a8a078360b9788cdf259d359ac12e0eefe85a01c1",
    }
    assert {m.version: m.checksum for m in RESOURCE_ADMISSION_MIGRATIONS[:4]} == expected
    assert [m.version for m in RESOURCE_ADMISSION_MIGRATIONS] == [1, 2, 3, 4, 5]


def test_audit_authority_stays_readonly_after_later_generic_grants(pair):
    migrate_component(
        pair.target,
        (
            ComponentMigration(
                "admin_action_grant_probe",
                "main",
                1,
                ("CREATE TABLE admin_action_grant_probe (id INTEGER)",),
                ("admin_action_grant_probe",),
            ),
        ),
    )
    assert actions(pair) == []
    for statement in [
        "INSERT INTO {} DEFAULT VALUES",
        "UPDATE {} SET from_revision=1 WHERE false",
        "DELETE FROM {} WHERE false",
    ]:
        with connect(pair.target) as runtime:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                runtime.execute(
                    sql.SQL(statement).format(
                        sql.Identifier(pair.target.schema, "_cao_resource_admin_actions")
                    )
                )


def test_owner_writer_role_uses_the_same_paid_control_credit(pair, monkeypatch):
    from test.services.test_delivery_admission import observe

    from cli_agent_orchestrator.clients.storage_admission import _current_storage_owner
    from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
    from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
    from cli_agent_orchestrator.services.shared_delivery_admission import authorize_shared_delivery

    operation = prepare(pair)
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    owner.require_shared_admission()
    assert observe(owner, 16)
    retired = [False]
    with permit.operation(clock=lambda: 10):
        coordinator = authorize_shared_delivery(
            principal=principal(),
            repository=pair.custodian,
            owner=owner,
            control=permit,
            retirement=lambda: retired[0],
            clock=lambda: 10,
        )
        owner.bind_shared_admission(coordinator)
    original = pair.module.connect
    roles = []

    def paid(target, *args, **kwargs):
        scope = _current_storage_owner()
        assert scope is owner._control_lease and not scope._storage_connections
        connection = original(target, *args, **kwargs)
        assert len(scope._storage_connections) == 1
        roles.append(target.user != pair.target.user)
        return connection

    try:
        with monkeypatch.context() as patch:
            patch.setattr(pair.module, "connect", paid)
            with permit.operation(clock=lambda: 10):
                result = abort(pair, operation)
                assert result.state == "released"
                assert not owner._control_lease._storage_connections
        assert roles == [False, True]
        assert owner.snapshot(now=10).inflight == 0
        assert pair.custodian.used(pair.storage.spec.domain_id) == 2
    finally:
        owner.stop_accepting()
        coordinator.drain(20)
        retired[0] = True


def test_v4_upgrade_and_mixed_version_readers_fail_closed(postgres_targets):
    from cli_agent_orchestrator.clients.admission_schema import RESOURCE_ADMISSION_MIGRATIONS
    from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch, verify_component

    target = postgres_targets.main
    previous = RESOURCE_ADMISSION_MIGRATIONS[:4]
    migrate_component(target, previous)
    with connect(target, readonly=True) as reader:
        verify_component(reader, previous)
        with pytest.raises(SchemaMismatch):
            verify_component(reader, RESOURCE_ADMISSION_MIGRATIONS)
    migrate_component(target, RESOURCE_ADMISSION_MIGRATIONS)
    with connect(target, readonly=True) as reader:
        verify_component(reader, RESOURCE_ADMISSION_MIGRATIONS)
        with pytest.raises(SchemaMismatch):
            verify_component(reader, previous)


def test_replay_actor_cannot_claim_another_principals_action(pair):
    from cli_agent_orchestrator.security.auth import _verified_principal

    operation = prepare(pair)
    action_id = str(uuid4())
    abort(pair, operation, action_id=action_id)
    another = _verified_principal("urn:caos:test", "another-operator", ["cao:admin"], "jwt")
    with pytest.raises(pair.module.SharedRevisionConflict):
        abort(pair, operation, action_id=action_id, actor=another)
    assert len(actions(pair)) == 1
    assert pair.custodian.used(pair.storage.spec.domain_id) == 2


@pytest.mark.parametrize("fault", ["usage", "binding"])
def test_abort_rolls_back_every_counter_and_audit_on_precommit_invalidation(
    pair, monkeypatch, fault
):
    operation = prepare(pair)
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
                abort(pair, operation, action_id=action_id)
        finally:
            pair.custodian._binding_supplier = supplier
    assert injected == [True]
    row = pair.owner.read_operation(operation.operation_id)
    assert row.state == "preparing" and row.revision == 1
    assert actions(pair) == []
    assert pair.custodian.audit_usage() == ()
    assert pair.custodian.used(pair.storage.spec.domain_id) == 3
    assert abort(pair, operation, action_id=action_id).state == "released"
    assert pair.custodian.used(pair.storage.spec.domain_id) == 2
    assert len(actions(pair)) == 1


@pytest.mark.parametrize("fault", ["ack", "binding", "interface", "storage_close"])
def test_abort_committed_without_ack_preserves_action_identity_and_replay(pair, monkeypatch, fault):
    from contextlib import contextmanager

    operation = prepare(pair)
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
                abort(pair, operation, action_id=action_id)
            assert caught.value.action_id == action_id
            assert caught.value.operation_id == operation.operation_id
        finally:
            pair.custodian._binding_supplier = supplier
    assert injected == [True]
    row = pair.owner.read_operation(operation.operation_id)
    assert row.state == "released" and row.revision == 2
    assert pair.custodian.used(pair.storage.spec.domain_id) == 2
    assert len(actions(pair)) == 1
    assert abort(pair, operation, action_id=action_id).state == "released"
    assert pair.custodian.used(pair.storage.spec.domain_id) == 2
    assert len(actions(pair)) == 1
    assert pair.custodian.audit_usage() == ()
