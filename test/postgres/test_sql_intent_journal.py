"""A committed intent precedes TCP; it is not proof of backend absence."""

import importlib
import importlib.util
import os
from contextlib import contextmanager
from test.postgres.test_admission_metadata_credits import ADMIN, grant
from test.postgres.test_admission_reservations import domain, shared
from uuid import uuid4

import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
from cli_agent_orchestrator.clients.storage_admission import physical_storage_scope
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
from cli_agent_orchestrator.utils import process_sql_origin as origin

pytestmark = pytest.mark.postgres


def api():
    name = "cli_agent_orchestrator.clients.sql_intent_repository"
    assert importlib.util.find_spec(name) is not None, "missing durable SQL intent journal"
    return importlib.import_module(name)


@contextmanager
def marked(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(origin, "_STATE", origin._State(os.getpid()))
        patch.setenv(origin.RESOURCE_BINDING_FD_ENV, "123")
        yield


def prepare(shared, module, *, capacity=4):
    target, _, bound, _ = shared
    migrate_component(target, module.SQL_OPENING_MIGRATIONS)
    credit = grant(shared, domain(shared, 4))
    repository = module.SqlIntentRepository(target, binding_supplier=lambda: bound)
    journal_id = str(uuid4())
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    with permit.operation(clock=lambda: 10.0):
        with connect(target.migration_target()) as channel:
            repository.provision(
                channel,
                journal_id=journal_id,
                credit_id=credit.credit_id,
                max_records=capacity,
                principal_id=ADMIN,
            )
    return repository, journal_id


@contextmanager
def control(target):
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    with permit.operation(clock=lambda: 10.0):
        with connect(target) as connection:
            yield connection
    assert owner.snapshot(now=10.0).storage_connection_loans == 0


def test_intent_is_durable_and_duplicate_ids_do_not_confer_a_second_effect(shared, monkeypatch):
    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        target = shared[0]
        request = str(uuid4())
        with control(target) as channel:
            receipt = repository.record(
                channel, journal_id=journal, intent_id=request, path="native", destination=target
            )
            assert receipt.intent_id == request and receipt.ordinal == 1
        with control(target) as channel:
            assert repository.read(channel, journal_id=journal, intent_id=request) == receipt
            with pytest.raises(module.SqlIntentConflict):
                repository.record(
                    channel,
                    journal_id=journal,
                    intent_id=request,
                    path="native",
                    destination=target,
                )
        assert shared[1].metadata_credits_current()[0].credit_id


def test_quota_and_registration_fence_deny_before_new_driver_effects(shared, monkeypatch):
    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module, capacity=1)
        target = shared[0]
        with control(target) as channel:
            repository.record(
                channel,
                journal_id=journal,
                intent_id=str(uuid4()),
                path="native",
                destination=target,
            )
            with pytest.raises(module.SqlIntentCapacityUnavailable):
                repository.record(
                    channel,
                    journal_id=journal,
                    intent_id=str(uuid4()),
                    path="orm",
                    destination=target,
                )

            repository.fence(channel, journal_id=journal)
            repository.fence(channel, journal_id=journal)
            with pytest.raises(module.SqlIntentRegistrationSealed):
                repository.record(
                    channel,
                    journal_id=journal,
                    intent_id=str(uuid4()),
                    path="audit",
                    destination=target,
                )


def test_committed_then_lost_ack_retains_uuid_without_ever_entering_the_driver(shared, monkeypatch):
    from test.services.test_delivery_admission import create, observe, reserve

    import psycopg

    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        target = shared[0]
        owner = create()
        assert observe(owner, 2)
        lease = reserve(owner, "lost-sql-intent-ack")
        assert lease.start()
        original_transaction = psycopg.Connection.transaction
        driver = []
        with control(target) as channel:

            @contextmanager
            def lost(raw, *args, **kwargs):
                with original_transaction(raw, *args, **kwargs) as transaction:
                    yield transaction
                if raw is channel.raw:
                    raise OSError("controlled loss after the real commit")

            def forbidden(**kwargs):
                driver.append(True)
                pytest.fail("driver ran after a lost registration acknowledgement")

            try:
                with physical_storage_scope(lease, clock=lambda: 10.0):
                    with repository.record_openings(channel, journal_id=journal):
                        with monkeypatch.context() as patch:
                            patch.setattr(psycopg.Connection, "transaction", lost)
                            patch.setattr(psycopg, "connect", forbidden)
                            with pytest.raises(module.SqlIntentCommitUncertain) as failed:
                                with connect(target):
                                    pytest.fail("unacknowledged intent permitted TCP")
                assert failed.value.journal_id == journal and failed.value.intent_id is not None
                retained = repository.read(
                    channel, journal_id=journal, intent_id=failed.value.intent_id
                )
                assert retained is not None and retained.ordinal == 1
                assert not driver and origin._STATE.unconfirmed == 0
                assert origin._STATE.registration_unconfirmed == 1
            finally:
                assert lease.finish_after_storage()


def test_another_control_backend_cannot_adopt_the_original_writer(shared, monkeypatch):
    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        target = shared[0]
        with control(target) as first:
            receipt = repository.record(
                first, journal_id=journal, intent_id=str(uuid4()), path="native", destination=target
            )
            with control(target) as second:
                assert (
                    repository.read(second, journal_id=journal, intent_id=receipt.intent_id)
                    == receipt
                )
                with pytest.raises(module.SqlIntentConflict, match="original control backend"):
                    repository.record(
                        second,
                        journal_id=journal,
                        intent_id=str(uuid4()),
                        path="orm",
                        destination=target,
                    )
                # A separately paid reader/fencer gives no new-TCP permission.
                repository.fence(second, journal_id=journal)
            with pytest.raises(module.SqlIntentRegistrationSealed):
                repository.record(
                    first,
                    journal_id=journal,
                    intent_id=str(uuid4()),
                    path="audit",
                    destination=target,
                )


def test_c1_keeps_financed_completion_registration_available(shared, monkeypatch):
    from test.postgres.test_admission_generation_seal import authority

    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        credit_before = shared[1].metadata_credits_current()
        shared[1].seal_current_generation(authority(shared))
        target = shared[0]
        with control(target) as channel:
            receipt = repository.record(
                channel,
                journal_id=journal,
                intent_id=str(uuid4()),
                path="native",
                destination=target,
            )
            assert receipt.ordinal == 1
        assert shared[1].metadata_credits_current() == credit_before


@pytest.mark.parametrize("drift", ["column_update", "public_select"])
def test_effective_append_acl_drift_refuses_registration(shared, monkeypatch, drift):
    from cli_agent_orchestrator.clients.postgres.migrations import (
        SchemaMismatch,
        protect_runtime_authority_tables,
    )

    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        target = shared[0]
        relation = sql.Identifier(target.schema, "_cao_sql_opening_intents")
        with connect(target.migration_target()) as administrator:
            administrator.execute(
                sql.SQL("GRANT UPDATE(ordinal) ON {} TO {}").format(
                    relation, sql.Identifier(target.user)
                )
                if drift == "column_update"
                else sql.SQL("GRANT SELECT ON {} TO PUBLIC").format(relation)
            )
        try:
            with control(target) as channel:
                with pytest.raises(SchemaMismatch):
                    repository.record(
                        channel,
                        journal_id=journal,
                        intent_id=str(uuid4()),
                        path="native",
                        destination=target,
                    )
        finally:
            with connect(target.migration_target()) as administrator:
                protect_runtime_authority_tables(administrator, target)


def test_a_zero_quota_never_reaches_the_driver(shared, monkeypatch):
    from test.services.test_delivery_admission import create, observe, reserve

    import psycopg

    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module, capacity=0)
        target = shared[0]
        owner = create()
        assert observe(owner, 2)
        lease = reserve(owner, "zero-intent-capacity")
        assert lease.start()
        with control(target) as channel:
            try:
                with physical_storage_scope(lease, clock=lambda: 10.0):
                    with repository.record_openings(channel, journal_id=journal):
                        with monkeypatch.context() as patch:
                            patch.setattr(
                                psycopg,
                                "connect",
                                lambda **kwargs: pytest.fail("TCP ran at zero quota"),
                            )
                            with pytest.raises(module.SqlIntentCapacityUnavailable):
                                with connect(target):
                                    pytest.fail("zero quota permitted a connection")
                assert origin._STATE.unconfirmed == origin._STATE.registration_unconfirmed == 0
            finally:
                assert lease.finish_after_storage()


@pytest.mark.parametrize("path", ["native", "orm", "audit"])
def test_an_independent_backend_sees_the_committed_intent_before_tcp(
    shared, postgres_service, monkeypatch, path
):
    from test.services.test_delivery_admission import create, observe, reserve

    import psycopg

    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.services.legacy_memory_audit_repository import (
        LegacyMemoryAuditConnectionManager,
    )

    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        target = shared[0]
        owner = create()
        assert observe(owner, 3)
        lease = reserve(owner, "intent-driver")
        assert lease is not None and lease.start()
        original = psycopg.connect
        calls = []
        manager = LegacyMemoryAuditConnectionManager(max_sessions=1)
        with (
            postgres_service.connect_admin(target.database) as observer,
            control(target) as channel,
        ):

            def checked(**kwargs):
                rows = observer.execute(
                    sql.SQL("SELECT path FROM {} WHERE journal_id=%s").format(
                        sql.Identifier(target.schema, "_cao_sql_opening_intents")
                    ),
                    (journal,),
                ).fetchall()
                assert rows == [(path,)], "driver ran before another backend could see commit"
                assert not channel.in_transaction
                calls.append(True)
                return original(**kwargs)

            try:
                with physical_storage_scope(lease, clock=lambda: 10.0):
                    with repository.record_openings(channel, journal_id=journal):
                        with monkeypatch.context() as patch:
                            patch.setattr(psycopg, "connect", checked)
                            if path == "native":
                                with connect(target) as connection:
                                    assert connection.execute("SELECT 42").fetchone()[0] == 42
                            elif path == "orm":
                                raw = database._native_orm_connection()
                                try:
                                    assert raw.execute("SELECT 42").fetchone()[0] == 42
                                finally:
                                    raw.close()
                            else:
                                with manager.checkout(target, readonly=True) as connection:
                                    assert connection.execute("SELECT 42").fetchone()[0] == 42
                assert calls == [True]
            finally:
                manager.close()
                assert lease.finish_after_storage()


def test_owner_provisions_for_another_live_participant_without_adopting_its_identity(
    shared, monkeypatch
):
    import subprocess
    import sys
    from dataclasses import replace

    import psutil

    module = api()
    with marked(monkeypatch):
        target, admission, actor, _ = shared
        migrate_component(target, module.SQL_OPENING_MIGRATIONS)
        record = domain(shared, 4)
        with subprocess.Popen(
            [sys.executable, "-I", "-c", "print('ready', flush=True); input()"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        ) as child:
            assert child.stdout.readline().strip() == "ready"
            try:
                participant = replace(
                    actor,
                    instance_id=str(uuid4()),
                    process_generation=str(uuid4()),
                    process_pid=child.pid,
                    process_started_at=psutil.Process(child.pid).create_time(),
                )
                admission.provision_member(
                    record.spec.domain_id, participant, expected_revision=0, principal_id=ADMIN
                )
                credit = grant(shared, record, current=participant)
                repository = module.SqlIntentRepository(target, binding_supplier=lambda: actor)
                journal = str(uuid4())
                with control(target.migration_target()) as channel:
                    with pytest.raises(module.SqlIntentUnavailable, match="metadata root"):
                        repository.provision(
                            channel,
                            journal_id=journal,
                            credit_id=credit.credit_id,
                            max_records=2,
                            principal_id=ADMIN,
                        )
                    repository.provision(
                        channel,
                        journal_id=journal,
                        credit_id=credit.credit_id,
                        max_records=2,
                        principal_id=ADMIN,
                        participant_binding=participant,
                    )
                    repository.provision(
                        channel,
                        journal_id=journal,
                        credit_id=credit.credit_id,
                        max_records=2,
                        principal_id=ADMIN,
                        participant_binding=participant,
                    )
                    with pytest.raises(module.SqlIntentConflict):
                        repository.provision(
                            channel,
                            journal_id=journal,
                            credit_id=credit.credit_id,
                            max_records=3,
                            principal_id=ADMIN,
                            participant_binding=participant,
                        )
                assert repository._identity.current_binding() == actor
                with control(target) as channel:
                    with pytest.raises(module.SqlIntentUnavailable, match="quota is unavailable"):
                        repository.record(
                            channel,
                            journal_id=journal,
                            intent_id=str(uuid4()),
                            path="native",
                            destination=target,
                        )
            finally:
                child.communicate("finish\n", timeout=15)
                assert child.returncode == 0
