"""Owner-issued SQL root records on the pinned PostgreSQL fixture."""

import os
from contextlib import contextmanager
from dataclasses import replace
from test.postgres.test_admission_metadata_credits import ADMIN, grant
from test.postgres.test_admission_reservations import domain, shared
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
from cli_agent_orchestrator.utils import process_sql_origin as origin

pytestmark = pytest.mark.postgres


@contextmanager
def marked(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(origin, "_STATE", origin._State(os.getpid()))
        patch.setenv(origin.RESOURCE_BINDING_FD_ENV, "123")
        yield


@contextmanager
def funded(target):
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    with permit.operation(clock=lambda: 10.0):
        with connect(target) as channel:
            yield channel
    assert owner.snapshot(now=10.0).storage_connection_loans == 0


def api():
    import importlib
    import importlib.util

    name = "cli_agent_orchestrator.clients.sql_intent_repository"
    assert importlib.util.find_spec(name) is not None, "missing SQL root journal"
    return importlib.import_module(name)


def prepare(shared, module, *, capacity=4):
    target, _, bound, _ = shared
    migrate_component(target, module.SQL_OPENING_MIGRATIONS)
    # Establish the whole storage set before granting any amount-one credits.
    records = (domain(shared, 4), domain(shared, 4))
    credits = tuple(grant(shared, record) for record in records)
    repository = module.SqlIntentRepository(target, binding_supplier=lambda: bound)
    journal_id = str(uuid4())
    with funded(target.migration_target()) as channel:
        repository.provision(
            channel,
            journal_id=journal_id,
            credit_id=credits[0].credit_id,
            max_records=capacity,
            principal_id=ADMIN,
        )
    return repository, journal_id, credits


def opening_request(bound, target):
    return dict(
        session_id=str(uuid4()),
        subject=bound,
        subject_sql_application_name="caos-sql-v1-" + uuid4().hex,
        boot_id=str(uuid4()),
        start_time_ticks=1,
        pid_namespace=(1, 2),
        destination=target,
        principal_id=ADMIN,
    )


def test_root_commit_links_complete_credit_set_and_signing_capability_is_one_use(
    shared, postgres_service, monkeypatch
):
    from cli_agent_orchestrator.models.sql_root_genesis import (
        ConfirmedRootSqlOpening,
        RootSqlOpeningIntent,
    )
    from cli_agent_orchestrator.services.sql_root_genesis import (
        RootGenesisUnavailable,
        sign_root_opening,
    )

    target, admission, bound, _ = shared
    module = api()
    with marked(monkeypatch):
        repository, journal, credits = prepare(shared, module)
        request = str(uuid4())
        values = opening_request(bound, target)
        with postgres_service.connect_admin(target.database) as observer:
            with funded(target.migration_target()) as channel:
                issue = module._issue_confirmed_root_sql_opening

                def independently_observed(record, *, validate):
                    root_row = observer.execute(
                        sql.SQL(
                            "SELECT ordinal,credit_id,session_id,path,destination_scope "
                            "FROM {} WHERE journal_id=%s AND intent_id=%s"
                        ).format(sql.Identifier(target.schema, module.SQL_ROOT_OPENINGS)),
                        (journal, request),
                    ).fetchone()
                    links = observer.execute(
                        sql.SQL(
                            "SELECT credit_id FROM {} WHERE root_intent_id=%s "
                            "AND journal_id=%s ORDER BY credit_id"
                        ).format(sql.Identifier(target.schema, module.SQL_ROOT_OPENING_CREDITS)),
                        (request, journal),
                    ).fetchall()
                    assert root_row is not None
                    assert (
                        root_row[0],
                        str(root_row[1]),
                        str(root_row[2]),
                        root_row[3],
                        root_row[4],
                    ) == (1, record.credit_id, values["session_id"], "native", "main")
                    assert tuple(str(row[0]) for row in links) == tuple(
                        sorted(credit.credit_id for credit in credits)
                    )
                    assert (
                        observer.execute(
                            sql.SQL("SELECT count(*) FROM {} WHERE journal_id=%s").format(
                                sql.Identifier(target.schema, module.SQL_OPENING_CHANNELS)
                            ),
                            (journal,),
                        ).fetchone()[0]
                        == 0
                    )
                    return issue(record, validate=validate)

                with monkeypatch.context() as patch:
                    patch.setattr(
                        module, "_issue_confirmed_root_sql_opening", independently_observed
                    )
                    capability = repository.record_root_opening(
                        channel,
                        journal_id=journal,
                        intent_id=request,
                        **values,
                    )
                assert isinstance(capability, ConfirmedRootSqlOpening)
                capability.check()
                signed = sign_root_opening(
                    capability,
                    key_id="ephemeral-test",
                    private_key=Ed25519PrivateKey.generate(),
                )
                assert signed.record.intent_id == request
                with pytest.raises(RootGenesisUnavailable):
                    sign_root_opening(
                        capability,
                        key_id="ephemeral-test",
                        private_key=Ed25519PrivateKey.generate(),
                    )
                owner_read = repository.read_root_opening(
                    channel, journal_id=journal, intent_id=request, owner=True
                )
                assert type(owner_read) is RootSqlOpeningIntent
                assert owner_read.credit_ids == tuple(sorted(c.credit_id for c in credits))

        with funded(target) as runtime:
            runtime_read = repository.read_root_opening(
                runtime, journal_id=journal, intent_id=request
            )
            assert runtime_read == owner_read
            for table in (module.SQL_ROOT_OPENINGS, module.SQL_ROOT_OPENING_CREDITS):
                privileges = runtime.execute(
                    """SELECT has_table_privilege(current_user,%s,'SELECT'),
                    has_table_privilege(current_user,%s,'INSERT'),
                    has_table_privilege(current_user,%s,'UPDATE'),
                    has_table_privilege(current_user,%s,'DELETE')""",
                    (target.schema + "." + table,) * 4,
                ).fetchone()
                assert tuple(privileges) == (True, False, False, False)
            with pytest.raises(RootGenesisUnavailable):
                sign_root_opening(
                    runtime_read,
                    key_id="ephemeral-test",
                    private_key=Ed25519PrivateKey.generate(),
                )

        assert admission.used(credits[0].domain_id) == 1
        assert admission.used(credits[1].domain_id) == 1


def test_external_funded_issuer_uses_subject_quota_and_owner_readback(shared, monkeypatch):
    target, admission, issuer_binding, _ = shared
    module = api()
    with marked(monkeypatch):
        migrate_component(target, module.SQL_OPENING_MIGRATIONS)
        records = (domain(shared, 4), domain(shared, 4))
        subject = replace(
            issuer_binding,
            instance_id=str(uuid4()),
            process_generation=str(uuid4()),
        )
        for record in records:
            admission.provision_member(
                record.spec.domain_id,
                subject,
                expected_revision=0,
                principal_id=ADMIN,
            )
        credits = tuple(grant(shared, record, current=subject) for record in records)
        repository = module.SqlIntentRepository(target, binding_supplier=lambda: issuer_binding)
        journal = str(uuid4())
        with funded(target.migration_target()) as issuer_channel:
            repository.provision(
                issuer_channel,
                journal_id=journal,
                credit_id=credits[0].credit_id,
                max_records=4,
                principal_id=ADMIN,
                participant_binding=subject,
            )
            intent_id = str(uuid4())
            capability = repository.record_root_opening(
                issuer_channel,
                journal_id=journal,
                intent_id=intent_id,
                **opening_request(subject, target),
            )
            assert capability.record.subject == subject
            capability.check()  # The capability remains bound to the original issuer.
            owner_read = repository.read_root_opening(
                issuer_channel,
                journal_id=journal,
                intent_id=intent_id,
                owner=True,
            )
            assert owner_read == capability.record
        with funded(target) as runtime_channel:
            with pytest.raises(module.SqlIntentUnavailable):
                repository.read_root_opening(
                    runtime_channel,
                    journal_id=journal,
                    intent_id=intent_id,
                )
        subject_reader = module.SqlIntentRepository(target, binding_supplier=lambda: subject)
        with funded(target) as runtime_channel:
            assert (
                subject_reader.read_root_opening(
                    runtime_channel,
                    journal_id=journal,
                    intent_id=intent_id,
                )
                == owner_read
            )


@pytest.mark.parametrize("denial", ["quota", "fence", "cross_uuid", "binding"])
def test_root_opening_denials_do_not_issue_a_capability(shared, monkeypatch, denial):
    from cli_agent_orchestrator.clients.admission_repository import (
        SharedAdmissionDenied,
    )

    target, _, bound, _ = shared
    module = api()
    with marked(monkeypatch):
        repository, journal, _ = prepare(shared, module, capacity=0 if denial == "quota" else 4)
        request = str(uuid4())
        values = opening_request(bound, target)
        if denial == "binding":
            values["subject"] = replace(bound, resource_view_sha256="c" * 64)
        with funded(target) as runtime:
            if denial == "fence":
                repository.fence(runtime, journal_id=journal)
            if denial == "cross_uuid":
                repository.record(
                    runtime,
                    journal_id=journal,
                    intent_id=request,
                    path="native",
                    destination=target,
                )

        with funded(target.migration_target()) as owner_channel:
            expected_error = {
                "quota": module.SqlIntentCapacityUnavailable,
                "fence": module.SqlIntentRegistrationSealed,
                "cross_uuid": module.SqlIntentConflict,
                "binding": SharedAdmissionDenied,
            }[denial]
            with pytest.raises(
                expected_error,
                match="retained SQL intent" if denial == "cross_uuid" else None,
            ):
                repository.record_root_opening(
                    owner_channel,
                    journal_id=journal,
                    intent_id=request,
                    **values,
                )


def test_invalid_issuer_principal_is_rejected_before_using_the_channel(shared, monkeypatch):
    target, _, bound, _ = shared
    module = api()
    with marked(monkeypatch):
        repository, journal, _ = prepare(shared, module)
        values = opening_request(bound, target)
        values["principal_id"] = "not-a-verified-principal"
        with funded(target.migration_target()) as channel:
            with monkeypatch.context() as patch:
                patch.setattr(
                    channel,
                    "execute",
                    lambda *args, **kwargs: pytest.fail("invalid principal reached SQL"),
                )
                with pytest.raises(ValueError):
                    repository.record_root_opening(
                        channel,
                        journal_id=journal,
                        intent_id=str(uuid4()),
                        **values,
                    )


@pytest.mark.parametrize("component", [0, 1])
def test_zero_pid_namespace_component_is_rejected_before_using_the_channel(
    shared, monkeypatch, component
):
    target, _, bound, _ = shared
    module = api()
    with marked(monkeypatch):
        repository, journal, _ = prepare(shared, module)
        values = opening_request(bound, target)
        namespace = list(values["pid_namespace"])
        namespace[component] = 0
        values["pid_namespace"] = tuple(namespace)
        with funded(target.migration_target()) as channel:
            with monkeypatch.context() as patch:
                patch.setattr(
                    channel,
                    "execute",
                    lambda *args, **kwargs: pytest.fail("invalid PID namespace reached SQL"),
                )
                with pytest.raises(ValueError):
                    repository.record_root_opening(
                        channel,
                        journal_id=journal,
                        intent_id=str(uuid4()),
                        **values,
                    )


def test_lost_commit_ack_retains_root_rows_but_never_issues_the_capability(
    shared, postgres_service, monkeypatch
):
    import psycopg

    target, _, bound, _ = shared
    module = api()
    with marked(monkeypatch):
        repository, journal, _ = prepare(shared, module)
        request = str(uuid4())
        values = opening_request(bound, target)
        with (
            postgres_service.connect_admin(target.database) as observer,
            funded(target.migration_target()) as channel,
        ):
            original_transaction = psycopg.Connection.transaction

            @contextmanager
            def lose_ack(raw, *args, **kwargs):
                with original_transaction(raw, *args, **kwargs) as transaction:
                    yield transaction
                if raw is channel.raw:
                    raise OSError("controlled loss after root commit")

            with monkeypatch.context() as patch:
                patch.setattr(psycopg.Connection, "transaction", lose_ack)
                patch.setattr(
                    module,
                    "_issue_confirmed_root_sql_opening",
                    lambda *args, **kwargs: pytest.fail("lost ACK issued a signing capability"),
                )
                with pytest.raises(module.SqlIntentCommitUncertain) as failure:
                    repository.record_root_opening(
                        channel,
                        journal_id=journal,
                        intent_id=request,
                        **values,
                    )
            assert (failure.value.journal_id, failure.value.intent_id) == (journal, request)
            assert observer.execute(
                sql.SQL("SELECT ordinal FROM {} WHERE journal_id=%s AND intent_id=%s").format(
                    sql.Identifier(target.schema, module.SQL_ROOT_OPENINGS)
                ),
                (journal, request),
            ).fetchone() == (1,)
            assert (
                observer.execute(
                    sql.SQL("SELECT count(*) FROM {} WHERE root_intent_id=%s").format(
                        sql.Identifier(target.schema, module.SQL_ROOT_OPENING_CREDITS)
                    ),
                    (request,),
                ).fetchone()[0]
                == 2
            )
