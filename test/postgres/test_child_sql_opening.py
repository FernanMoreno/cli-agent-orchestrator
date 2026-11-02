"""Parent-funded registration before child ACK; not global bootstrap capacity."""

import copy
import hashlib
import json
import os
import signal
import sys
from contextlib import contextmanager
from dataclasses import asdict
from test.postgres.test_sql_intent_journal import api, control, marked, prepare, shared
from test.services.test_resource_process_supervisor import CHILD
from uuid import uuid4

import pytest
from psycopg import sql

from cli_agent_orchestrator.services.resource_process_supervisor import (
    BINDING_FD_ENV,
    ResourceProcessSupervisor,
)

pytestmark = pytest.mark.postgres


@contextmanager
def pending_child(target, tmp_path, *, generation=None, parent_journal=""):
    tmp_path.mkdir(parents=True, exist_ok=True)
    marker, denied = tmp_path / "child-first-sql", tmp_path / "child-rejected"
    target_hash = hashlib.sha256(
        json.dumps(target.identity, separators=(",", ":")).encode()
    ).hexdigest()
    source = CHILD[: CHILD.index("mode=os.environ")]
    source = source.replace("'d18bcbac-9c97-4408-baf0-24f7e2331486'", repr(target.store_id))
    source = source.replace("'a'*64", repr(target_hash))
    source = source.replace(
        "'395141ce-9910-43f6-b4c4-9fe21790309e'", repr(generation or str(uuid4()))
    )
    source += r"""
import asyncio
from types import SimpleNamespace
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.postgres import DatabaseTarget,connect
from cli_agent_orchestrator.runtime_channel import bridge
from cli_agent_orchestrator.services import admission_bootstrap
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
from cli_agent_orchestrator.services.resource_process_supervisor import ResourceProcessUnproven
from cli_agent_orchestrator.clients.sql_intent_repository import SqlIntentRepository,SqlIntentUnavailable
from uuid import uuid4
bridge.runtime_token=lambda:'synthetic-test-only'
admission_bootstrap.scoped_process_bootstrap=lambda *a,**kw:SimpleNamespace(shared_repository=lambda:SimpleNamespace(current_binding=lambda:binding))
class FirstSQL(Exception):pass
def first_sql():
    target=DatabaseTarget(**json.loads(os.environ['CHILD_SQL_TARGET']))
    owner,permit=DeliveryAdmission.with_storage_control(AdmissionPolicy())
    with permit.operation(clock=lambda:10.0):
        with connect(target) as connection:
            row=connection.execute('SELECT pg_backend_pid(),current_setting(\'application_name\')').fetchone()
            denied_quota=False
            if os.environ['CHILD_PARENT_JOURNAL']:
                try:
                    SqlIntentRepository(target,binding_supplier=lambda:binding).record(connection,journal_id=os.environ['CHILD_PARENT_JOURNAL'],intent_id=str(uuid4()),path='native',destination=target)
                except SqlIntentUnavailable:denied_quota=True
                else:raise AssertionError('parent registration granted a child quota')
            Path(os.environ['CHILD_SQL_MARKER']).write_text(json.dumps(dict(pid=row[0],label=row[1],own_quota_denied=denied_quota)))
    assert owner.snapshot(now=10.0).storage_connection_loans==0
    raise FirstSQL()
database.init_runtime_db=first_sql
try:
    asyncio.run(bridge._amain())
except FirstSQL:os._exit(23)
except ResourceProcessUnproven:
    Path(os.environ['CHILD_REJECTED_MARKER']).write_text('denied')
    os._exit(24)
os._exit(25)
"""
    values = {
        k: v
        for k, v in asdict(target).items()
        if k
        in {
            "host",
            "port",
            "database",
            "user",
            "schema",
            "store_id",
            "secret_file",
            "sslmode",
            "scope",
        }
    }
    values["secret_file"] = str(values["secret_file"])
    environment = dict(os.environ)
    environment.pop(BINDING_FD_ENV, None)
    environment.update(
        CAO_BRIDGE_SERVER_URL="http://127.0.0.1:1",
        CAO_BRIDGE_RUNTIME_ID="child-sql-test",
        CHILD_SQL_TARGET=json.dumps(values),
        CHILD_PARENT_JOURNAL=parent_journal,
        CHILD_SQL_MARKER=str(marker),
        CHILD_REJECTED_MARKER=str(denied),
    )
    owner = ResourceProcessSupervisor.launch(
        [sys.executable, "-c", source],
        expected_store_id=target.store_id,
        expected_target_identity_sha256=target_hash,
        env=environment,
    )
    try:
        yield owner, marker, denied
    finally:
        owner.send_signal(signal.SIGTERM)
        owner.wait(timeout_s=30)
        owner.close()


def require_api(repository):
    assert callable(
        getattr(repository, "record_child_opening", None)
    ), "missing durable child opening"
    assert callable(
        getattr(ResourceProcessSupervisor, "receive_binding_with_child_intent", None)
    ), "missing pre-ACK composition"


def test_independent_pg_observer_sees_registration_before_ack_and_actual_child_tcp(
    shared, postgres_service, tmp_path, monkeypatch
):
    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        require_api(repository)
        target = shared[0]
        request = str(uuid4())
        with control(target) as parent, postgres_service.connect_admin(target.database) as observer:
            first = repository.record(
                parent,
                journal_id=journal,
                intent_id=str(uuid4()),
                path="native",
                destination=target,
            )
            assert first.ordinal == 1
            with pending_child(target, tmp_path, parent_journal=journal) as (owner, marker, denied):
                original = repository.record_child_opening

                def checked(channel, **kwargs):
                    receipt = original(channel, **kwargs)
                    rows = observer.execute(
                        sql.SQL(
                            "SELECT ordinal,subject_process_pid FROM {} WHERE intent_id=%s"
                        ).format(sql.Identifier(target.schema, "_cao_sql_child_openings")),
                        (request,),
                    ).fetchall()
                    assert rows == [(2, owner._process.pid)] and not marker.exists()
                    return receipt

                with monkeypatch.context() as patch:
                    patch.setattr(repository, "record_child_opening", checked)
                    receipt = owner.receive_binding_with_child_intent(
                        channel=parent,
                        repository=repository,
                        journal_id=journal,
                        intent_id=request,
                        destination=target,
                        timeout_s=30,
                    )
                observation = owner.wait(timeout_s=30)
                assert observation.returncode == 23 and receipt.subject is observation.binding
                actual = json.loads(marker.read_text())
                assert actual["own_quota_denied"] is True
                assert actual["label"] == receipt.subject_sql_application_name
                assert receipt.parent_process_pid == os.getpid() != receipt.subject.process_pid
                assert receipt.parent_sql_application_name != actual["label"]
                assert not denied.exists()
                assert (
                    repository.read_child_opening(parent, journal_id=journal, intent_id=request)
                    == receipt
                )
            last = repository.record(
                parent,
                journal_id=journal,
                intent_id=str(uuid4()),
                path="native",
                destination=target,
            )
            assert last.ordinal == 3
        assert len(shared[1].metadata_credits_current()) == 1


def test_actual_committed_registration_with_lost_ack_never_releases_child_to_sql(
    shared, tmp_path, monkeypatch
):
    import psycopg

    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        require_api(repository)
        target = shared[0]
        request = str(uuid4())
        with control(target) as parent, pending_child(target, tmp_path) as (owner, marker, denied):
            original = psycopg.Connection.transaction

            @contextmanager
            def lost(raw, *args, **kwargs):
                with original(raw, *args, **kwargs) as transaction:
                    yield transaction
                if raw is parent.raw:
                    raise OSError("controlled loss after actual child intent commit")

            with monkeypatch.context() as patch:
                patch.setattr(psycopg.Connection, "transaction", lost)
                with pytest.raises(module.SqlIntentCommitUncertain) as caught:
                    owner.receive_binding_with_child_intent(
                        channel=parent,
                        repository=repository,
                        journal_id=journal,
                        intent_id=request,
                        destination=target,
                        timeout_s=30,
                    )
            assert caught.value.intent_id == request and owner._binding_failed
            assert owner.wait(timeout_s=30) is None and owner._wait_status == (os.CLD_EXITED, 24)
            assert not marker.exists() and denied.read_text() == "denied"
            retained = repository.read_child_opening(parent, journal_id=journal, intent_id=request)
            assert retained is not None and retained.ordinal == 1
            after = repository.record(
                parent,
                journal_id=journal,
                intent_id=str(uuid4()),
                path="native",
                destination=target,
            )
            assert after.ordinal == 2


@pytest.mark.parametrize("refusal", ["quota", "fence", "destination", "copied_channel"])
def test_quota_or_fence_prevents_ack_and_tcp(shared, tmp_path, monkeypatch, refusal):
    from dataclasses import replace

    from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable

    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module, capacity=0 if refusal == "quota" else 4)
        require_api(repository)
        target = shared[0]
        with control(target) as parent, pending_child(target, tmp_path) as (owner, marker, denied):
            if refusal == "fence":
                repository.fence(parent, journal_id=journal)
            expected = {
                "quota": module.SqlIntentCapacityUnavailable,
                "fence": module.SqlIntentRegistrationSealed,
                "destination": module.SqlIntentUnavailable,
                "copied_channel": StorageAdmissionUnavailable,
            }[refusal]
            supplied = copy.copy(parent) if refusal == "copied_channel" else parent
            destination = (
                replace(target, store_id=str(uuid4())) if refusal == "destination" else target
            )
            with pytest.raises(expected):
                owner.receive_binding_with_child_intent(
                    channel=supplied,
                    repository=repository,
                    journal_id=journal,
                    intent_id=str(uuid4()),
                    destination=destination,
                    timeout_s=30,
                )
            assert owner.wait(timeout_s=30) is None and owner._wait_status == (os.CLD_EXITED, 24)
            assert not marker.exists() and denied.read_text() == "denied"


def test_frozen_migrations_remain_exact_while_child_table_is_additive():
    from cli_agent_orchestrator.clients.admission_schema import RESOURCE_ADMISSION_MIGRATIONS
    from cli_agent_orchestrator.clients.sql_opening_schema import (
        SQL_OPENING_MIGRATIONS,
        SQL_OPENING_TABLES,
    )

    assert (
        SQL_OPENING_MIGRATIONS[0].checksum
        == "71678a014d4780947d4c093152eee9d4e57e995fcfeaa2dfdd44d9945b82f7ed"
    )
    assert len(SQL_OPENING_TABLES) == 4 and SQL_OPENING_MIGRATIONS[1].version == 2
    assert tuple(m.checksum for m in RESOURCE_ADMISSION_MIGRATIONS[:7]) == (
        "6d1c5b17a332de83c647c2498f757da432ac13b502b2cead06d4c3950f5ef49e",
        "c183ecaffafcce3a3166fea88a0aea54bfd00d5820b20e9e5594105f1a2c8f40",
        "e4881dd35a2c59659a6395d31dd342c8cd7b0b9e6946998e4419cdd04eff98af",
        "1faa34ac79c790de65d8776a8a078360b9788cdf259d359ac12e0eefe85a01c1",
        "9cb73d9762cb8c0d846bb2ecdee811744c6c72029b830d623c2452e43762f8aa",
        "ee8f728301be19bc59c6574cefb69cb4df6819f8b943629bf30f289d3d1e7208",
        "690e0f2bd29948d300cdbb04de945f61fdaa4a03be226621b4a82a3696418e2f",
    )


@pytest.mark.parametrize("drift", ["column_update", "public_select"])
def test_child_append_acl_drift_refuses_ack(shared, tmp_path, monkeypatch, drift):
    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.clients.postgres.migrations import (
        SchemaMismatch,
        protect_runtime_authority_tables,
    )

    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        target = shared[0]
        relation = sql.Identifier(target.schema, "_cao_sql_child_openings")
        with connect(target.migration_target()) as administrator:
            administrator.execute(
                sql.SQL("GRANT UPDATE(ordinal) ON {} TO {}").format(
                    relation, sql.Identifier(target.user)
                )
                if drift == "column_update"
                else sql.SQL("GRANT SELECT ON {} TO PUBLIC").format(relation)
            )
        try:
            with (
                control(target) as parent,
                pending_child(target, tmp_path) as (owner, marker, denied),
            ):
                with pytest.raises(SchemaMismatch):
                    owner.receive_binding_with_child_intent(
                        channel=parent,
                        repository=repository,
                        journal_id=journal,
                        intent_id=str(uuid4()),
                        destination=target,
                        timeout_s=30,
                    )
                assert owner.wait(timeout_s=30) is None and owner._wait_status == (
                    os.CLD_EXITED,
                    24,
                )
                assert not marker.exists() and denied.read_text() == "denied"
        finally:
            with connect(target.migration_target()) as administrator:
                protect_runtime_authority_tables(administrator, target)


def test_plain_resource_binding_cannot_impersonate_a_verified_child(shared, monkeypatch):
    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        target = shared[0]
        with control(target) as parent:
            with monkeypatch.context() as patch:
                patch.setattr(
                    parent, "execute", lambda *a, **kw: pytest.fail("unverified subject caused SQL")
                )
                with pytest.raises(ValueError, match="authenticated live subject"):
                    repository.record_child_opening(
                        parent,
                        journal_id=journal,
                        intent_id=str(uuid4()),
                        subject=shared[2],
                        destination=target,
                    )


def test_committed_child_intent_survives_child_exit_before_ack(shared, tmp_path, monkeypatch):
    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        target = shared[0]
        request = str(uuid4())
        with control(target) as parent, pending_child(target, tmp_path) as (owner, marker, denied):
            original = repository.record_child_opening

            def kill_after_commit(channel, **kwargs):
                receipt = original(channel, **kwargs)
                owner.send_signal(signal.SIGTERM)
                assert owner.wait(timeout_s=30) is None
                return receipt

            with monkeypatch.context() as patch:
                patch.setattr(repository, "record_child_opening", kill_after_commit)
                with pytest.raises(module.SqlChildAckUnavailableAfterCommit) as failed:
                    owner.receive_binding_with_child_intent(
                        channel=parent,
                        repository=repository,
                        journal_id=journal,
                        intent_id=request,
                        destination=target,
                        timeout_s=30,
                    )
            assert owner._wait_status == (os.CLD_KILLED, signal.SIGTERM)
            assert owner._binding_failed and not marker.exists() and not denied.exists()
            assert failed.value.receipt == repository.read_child_opening(
                parent, journal_id=journal, intent_id=request
            )
            assert failed.value.receipt.ordinal == 1


def test_original_child_generation_is_unique_across_parent_journals(shared, tmp_path, monkeypatch):
    from dataclasses import replace
    from test.postgres.test_admission_metadata_credits import ADMIN

    from cli_agent_orchestrator.clients.postgres import connect

    module = api()
    with marked(monkeypatch):
        first, journal = prepare(shared, module)
        target, admission, bound, _ = shared
        other = replace(bound, instance_id=str(uuid4()), process_generation=str(uuid4()))
        domain_id = admission.metadata_credits_current()[0].domain_id
        admission.provision_member(domain_id, other, expected_revision=0, principal_id=ADMIN)
        credit = admission.grant_metadata_credit(str(uuid4()), domain_id, other, principal_id=ADMIN)
        second = module.SqlIntentRepository(target, binding_supplier=lambda: other)
        other_journal = str(uuid4())
        with control(target.migration_target()) as administrator:
            second.provision(
                administrator,
                journal_id=other_journal,
                credit_id=credit.credit_id,
                max_records=4,
                principal_id=ADMIN,
            )
        generation = str(uuid4())
        with control(target) as parent:
            with pending_child(target, tmp_path / "one", generation=generation) as (
                owner,
                marker,
                denied,
            ):
                owner.receive_binding_with_child_intent(
                    channel=parent,
                    repository=first,
                    journal_id=journal,
                    intent_id=str(uuid4()),
                    destination=target,
                    timeout_s=30,
                )
                assert owner.wait(timeout_s=30).returncode == 23 and marker.exists()
            with pending_child(target, tmp_path / "two", generation=generation) as (
                owner,
                marker,
                denied,
            ):
                with pytest.raises(module.SqlIntentConflict, match="retained child startup"):
                    owner.receive_binding_with_child_intent(
                        channel=parent,
                        repository=second,
                        journal_id=other_journal,
                        intent_id=str(uuid4()),
                        destination=target,
                        timeout_s=30,
                    )
                assert owner.wait(timeout_s=30) is None and owner._wait_status == (
                    os.CLD_EXITED,
                    24,
                )
                assert not marker.exists() and denied.read_text() == "denied"
        with connect(target) as observer:
            assert (
                observer.execute(
                    sql.SQL("SELECT count(*) FROM {} WHERE journal_id=%s").format(
                        sql.Identifier(target.schema, "_cao_sql_child_openings")
                    ),
                    (other_journal,),
                ).fetchone()[0]
                == 0
            )


def test_c1_seal_does_not_cancel_already_financed_registration(shared, tmp_path, monkeypatch):
    from test.postgres.test_admission_generation_seal import authority

    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        target = shared[0]
        before = shared[1].metadata_credits_current()
        with pending_child(target, tmp_path) as (owner, marker, denied):
            shared[1].seal_current_generation(authority(shared))
            with control(target) as parent:
                receipt = owner.receive_binding_with_child_intent(
                    channel=parent,
                    repository=repository,
                    journal_id=journal,
                    intent_id=str(uuid4()),
                    destination=target,
                    timeout_s=30,
                )
                assert receipt.ordinal == 1 and owner.wait(timeout_s=30).returncode == 23
        assert shared[1].metadata_credits_current() == before


def test_corrupt_retained_subject_is_not_a_valid_descriptive_receipt(shared, tmp_path, monkeypatch):
    from psycopg.types.json import Jsonb

    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch

    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        target = shared[0]
        request = str(uuid4())
        with control(target) as parent, pending_child(target, tmp_path) as (owner, marker, denied):
            receipt = owner.receive_binding_with_child_intent(
                channel=parent,
                repository=repository,
                journal_id=journal,
                intent_id=request,
                destination=target,
                timeout_s=30,
            )
            assert owner.wait(timeout_s=30).returncode == 23
        corrupt = dict(asdict(receipt.subject), extra="not-a-binding-field")
        with connect(target.migration_target()) as administrator:
            administrator.execute(
                sql.SQL("UPDATE {} SET subject_binding=%s WHERE intent_id=%s").format(
                    sql.Identifier(target.schema, "_cao_sql_child_openings")
                ),
                (Jsonb(corrupt), request),
            )
        with control(target) as reader:
            with pytest.raises(SchemaMismatch, match="invalid subject"):
                repository.read_child_opening(reader, journal_id=journal, intent_id=request)


def test_unissued_factory_value_with_noop_validator_cannot_cause_sql(shared, monkeypatch):
    from dataclasses import replace

    from cli_agent_orchestrator.models.sql_child_subject import _issue_verified_child_sql_subject

    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        target = shared[0]
        fabricated = replace(
            shared[2],
            instance_id=str(uuid4()),
            process_generation=str(uuid4()),
            process_pid=os.getpid() + 999999,
        )
        subject = _issue_verified_child_sql_subject(
            fabricated,
            sql_application_name="caos-sql-v1-" + uuid4().hex,
            boot_id=str(uuid4()),
            start_time_ticks=1,
            pid_namespace=(1, 1),
            validate=lambda: None,
        )
        with control(target) as parent:
            with monkeypatch.context() as patch:
                patch.setattr(
                    parent, "execute", lambda *a, **kw: pytest.fail("unissued subject caused SQL")
                )
                with pytest.raises(ValueError, match="issued"):
                    repository.record_child_opening(
                        parent,
                        journal_id=journal,
                        intent_id=str(uuid4()),
                        subject=subject,
                        destination=target,
                    )


def test_confirmed_commit_followed_by_child_loss_is_not_a_commit_uncertainty(
    shared, tmp_path, monkeypatch
):
    import psycopg

    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        target = shared[0]
        request = str(uuid4())
        with control(target) as parent, pending_child(target, tmp_path) as (owner, marker, denied):
            original = psycopg.Connection.transaction

            @contextmanager
            def terminate_after_commit(raw, *args, **kwargs):
                with original(raw, *args, **kwargs) as transaction:
                    yield transaction
                if raw is parent.raw:
                    owner.send_signal(signal.SIGTERM)
                    assert owner.wait(timeout_s=30) is None

            with monkeypatch.context() as patch:
                patch.setattr(psycopg.Connection, "transaction", terminate_after_commit)
                with pytest.raises(module.SqlIntentUnavailable) as caught:
                    owner.receive_binding_with_child_intent(
                        channel=parent,
                        repository=repository,
                        journal_id=journal,
                        intent_id=request,
                        destination=target,
                        timeout_s=30,
                    )
            assert type(caught.value).__name__ == "SqlChildAckUnavailableAfterCommit"
            assert not isinstance(caught.value, module.SqlIntentCommitUncertain)
            assert caught.value.receipt == repository.read_child_opening(
                parent, journal_id=journal, intent_id=request
            )
            assert owner._wait_status == (os.CLD_KILLED, signal.SIGTERM) and not marker.exists()


def test_v1_rows_upgrade_without_rewriting_receipts_or_ordinals(shared, monkeypatch):
    from test.postgres.test_admission_metadata_credits import ADMIN, grant
    from test.postgres.test_admission_reservations import domain

    from cli_agent_orchestrator.clients.admission_repository import binding_digest
    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch, migrate_component
    from cli_agent_orchestrator.utils.process_sql_origin import current_sql_application_name

    module = api()
    with marked(monkeypatch):
        target, _, bound, _ = shared
        migrate_component(target, module.SQL_OPENING_MIGRATIONS[:1])
        credit = grant(shared, domain(shared, 4))
        journal, request = str(uuid4()), str(uuid4())
        digest = hashlib.sha256(
            json.dumps(
                (target.identity, target.user, target.sslmode), separators=(",", ":")
            ).encode()
        ).hexdigest()
        with connect(target.migration_target()) as administrator:
            administrator.execute(
                sql.SQL(
                    "INSERT INTO {}(journal_id,credit_id,instance_id,process_generation,binding_sha256,max_records,actor_principal_id) VALUES(%s,%s,%s,%s,%s,4,%s)"
                ).format(sql.Identifier(target.schema, "_cao_sql_opening_quotas")),
                (
                    journal,
                    credit.credit_id,
                    bound.instance_id,
                    bound.process_generation,
                    binding_digest(bound),
                    ADMIN,
                ),
            )
            administrator.execute(
                sql.SQL(
                    "INSERT INTO {}(intent_id,journal_id,ordinal,binding_sha256,process_pid,process_started_at,sql_application_name,path,destination_store_id,destination_scope,destination_sha256) VALUES(%s,%s,1,%s,%s,%s,%s,'native',%s,%s,%s)"
                ).format(sql.Identifier(target.schema, "_cao_sql_opening_intents")),
                (
                    request,
                    journal,
                    binding_digest(bound),
                    bound.process_pid,
                    bound.process_started_at,
                    current_sql_application_name(),
                    target.store_id,
                    target.scope,
                    digest,
                ),
            )
            before = administrator.execute(
                sql.SQL("SELECT * FROM {} WHERE intent_id=%s").format(
                    sql.Identifier(target.schema, "_cao_sql_opening_intents")
                ),
                (request,),
            ).fetchone()
        repository = module.SqlIntentRepository(target, binding_supplier=lambda: bound)
        with control(target) as channel:
            with pytest.raises(SchemaMismatch):
                repository.read(channel, journal_id=journal, intent_id=request)
        migrate_component(target, module.SQL_OPENING_MIGRATIONS)
        with connect(target) as observer:
            after = observer.execute(
                sql.SQL("SELECT * FROM {} WHERE intent_id=%s").format(
                    sql.Identifier(target.schema, "_cao_sql_opening_intents")
                ),
                (request,),
            ).fetchone()
            assert before == after
        with control(target) as channel:
            assert repository.read(channel, journal_id=journal, intent_id=request).ordinal == 1
            assert (
                repository.record(
                    channel,
                    journal_id=journal,
                    intent_id=str(uuid4()),
                    path="native",
                    destination=target,
                ).ordinal
                == 2
            )


@pytest.mark.parametrize("first_kind", ["local", "child"])
def test_intent_ids_are_unique_across_both_kinds(shared, tmp_path, monkeypatch, first_kind):
    from dataclasses import replace

    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        target = shared[0]
        request = str(uuid4())
        with control(target) as parent, pending_child(target, tmp_path) as (owner, marker, denied):
            if first_kind == "local":
                repository.record(
                    parent, journal_id=journal, intent_id=request, path="native", destination=target
                )
                with pytest.raises(module.SqlIntentConflict):
                    owner.receive_binding_with_child_intent(
                        channel=parent,
                        repository=repository,
                        journal_id=journal,
                        intent_id=request,
                        destination=target,
                        timeout_s=30,
                    )
                assert (
                    owner.wait(timeout_s=30) is None
                    and denied.read_text() == "denied"
                    and not marker.exists()
                )
                assert (
                    repository.read_child_opening(parent, journal_id=journal, intent_id=request)
                    is None
                )
            else:
                receipt = owner.receive_binding_with_child_intent(
                    channel=parent,
                    repository=repository,
                    journal_id=journal,
                    intent_id=request,
                    destination=target,
                    timeout_s=30,
                )
                assert owner.wait(timeout_s=30).returncode == 23
                with pytest.raises(module.SqlIntentConflict):
                    repository.record(
                        parent,
                        journal_id=journal,
                        intent_id=request,
                        path="native",
                        destination=target,
                    )
                assert repository.read(parent, journal_id=journal, intent_id=request) is None
                assert (
                    repository.read_child_opening(parent, journal_id=journal, intent_id=request)
                    == receipt
                )
                other = replace(
                    shared[2], instance_id=str(uuid4()), process_generation=str(uuid4())
                )
                other_parent = module.SqlIntentRepository(target, binding_supplier=lambda: other)
                with pytest.raises(module.SqlIntentUnavailable, match="quota is unavailable"):
                    other_parent.read_child_opening(parent, journal_id=journal, intent_id=request)
            assert (
                repository.record(
                    parent,
                    journal_id=journal,
                    intent_id=str(uuid4()),
                    path="native",
                    destination=target,
                ).ordinal
                == 2
            )


@pytest.mark.parametrize("constraint", ["binding", "generation", "physical", "label"])
def test_postgres_enforces_subject_uniqueness_independently_of_the_api(
    shared, tmp_path, monkeypatch, constraint
):
    import psycopg
    from psycopg.types.json import Jsonb

    from cli_agent_orchestrator.clients.postgres import connect

    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        target = shared[0]
        request = str(uuid4())
        with control(target) as parent, pending_child(target, tmp_path) as (owner, marker, denied):
            owner.receive_binding_with_child_intent(
                channel=parent,
                repository=repository,
                journal_id=journal,
                intent_id=request,
                destination=target,
                timeout_s=30,
            )
            assert owner.wait(timeout_s=30).returncode == 23
        with connect(target.migration_target()) as administrator:
            table = sql.Identifier(target.schema, "_cao_sql_child_openings")
            row = administrator.execute(
                sql.SQL("SELECT * FROM {} WHERE intent_id=%s").format(table), (request,)
            ).fetchone()
            values = {k: row[k] for k in row.keys()}
            values.update(
                intent_id=str(uuid4()),
                ordinal=2,
                subject_binding_sha256=hashlib.sha256(uuid4().bytes).hexdigest(),
                subject_instance_id=str(uuid4()),
                subject_process_generation=str(uuid4()),
                subject_sql_application_name="caos-sql-v1-" + uuid4().hex,
                subject_process_pid=row["subject_process_pid"] + 1,
                start_time_ticks=row["start_time_ticks"] + 1,
            )
            keys = {
                "binding": ["subject_binding_sha256"],
                "generation": ["subject_instance_id", "subject_process_generation"],
                "physical": [
                    "boot_id",
                    "pid_namespace_device",
                    "pid_namespace_inode",
                    "subject_process_pid",
                    "start_time_ticks",
                ],
                "label": ["subject_sql_application_name"],
            }[constraint]
            for key in keys:
                values[key] = row[key]
            values["subject_binding"] = Jsonb(values["subject_binding"])
            statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                table,
                sql.SQL(",").join(sql.Identifier(k) for k in values),
                sql.SQL(",").join(sql.Placeholder() for k in values),
            )
            with pytest.raises(psycopg.errors.UniqueViolation):
                administrator.execute(statement, tuple(values.values()))


@pytest.mark.parametrize("capacity", [1, 4])
def test_competing_paid_writers_cannot_overspend_or_adopt_one_journal(
    shared, tmp_path, monkeypatch, capacity
):
    import select
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from cli_agent_orchestrator.clients.postgres import connect

    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module, capacity=capacity)
        target = shared[0]
        barrier = threading.Barrier(2)

        def local():
            with control(target) as parent:
                barrier.wait(timeout=30)
                try:
                    receipt = repository.record(
                        parent,
                        journal_id=journal,
                        intent_id=str(uuid4()),
                        path="native",
                        destination=target,
                    )
                except (module.SqlIntentCapacityUnavailable, module.SqlIntentConflict):
                    return ("refused", "local")
                assert receipt.ordinal == 1
                return ("accepted", "local")

        def subject():
            with (
                control(target) as parent,
                pending_child(target, tmp_path) as (owner, marker, denied),
            ):
                assert select.select([owner._channel], [], [], 30)[0]
                barrier.wait(timeout=30)
                try:
                    receipt = owner.receive_binding_with_child_intent(
                        channel=parent,
                        repository=repository,
                        journal_id=journal,
                        intent_id=str(uuid4()),
                        destination=target,
                        timeout_s=30,
                    )
                except (module.SqlIntentCapacityUnavailable, module.SqlIntentConflict):
                    assert (
                        owner.wait(timeout_s=30) is None
                        and not marker.exists()
                        and denied.read_text() == "denied"
                    )
                    return ("refused", "child")
                assert receipt.ordinal == 1 and owner.wait(timeout_s=30).returncode == 23
                return ("accepted", "child")

        with ThreadPoolExecutor(max_workers=2) as executor:
            one = executor.submit(local)
            two = executor.submit(subject)
            outcomes = [one.result(timeout=90), two.result(timeout=90)]
        assert sorted(v[0] for v in outcomes) == ["accepted", "refused"]
        with connect(target) as observer:
            count = observer.execute(
                sql.SQL(
                    "SELECT (SELECT count(*) FROM {} WHERE journal_id=%s)+(SELECT count(*) FROM {} WHERE journal_id=%s)"
                ).format(
                    sql.Identifier(target.schema, "_cao_sql_opening_intents"),
                    sql.Identifier(target.schema, "_cao_sql_child_openings"),
                ),
                (journal, journal),
            ).fetchone()[0]
            assert count == 1


def test_failed_ack_send_after_commit_preserves_confirmed_public_receipt(
    shared, tmp_path, monkeypatch
):
    import socket

    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        target = shared[0]
        request = str(uuid4())
        with control(target) as parent, pending_child(target, tmp_path) as (owner, marker, denied):
            original = socket.socket.send

            def failed_send(channel, payload, *args, **kwargs):
                if channel is owner._channel and payload == b"1":
                    raise OSError("controlled failure before ACK send")
                return original(channel, payload, *args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(socket.socket, "send", failed_send)
                with pytest.raises(module.SqlChildAckUnavailableAfterCommit) as caught:
                    owner.receive_binding_with_child_intent(
                        channel=parent,
                        repository=repository,
                        journal_id=journal,
                        intent_id=request,
                        destination=target,
                        timeout_s=30,
                    )
            assert owner._binding_failed and owner.wait(timeout_s=30) is None
            assert owner._wait_status == (os.CLD_EXITED, 24) and not marker.exists()
            assert denied.read_text() == "denied"
            assert caught.value.receipt == repository.read_child_opening(
                parent, journal_id=journal, intent_id=request
            )
