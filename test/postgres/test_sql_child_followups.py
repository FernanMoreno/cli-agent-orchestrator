"""Follow-up plans use real parent/child identities and PostgreSQL; no new grants."""

import json
from contextlib import contextmanager
from dataclasses import replace
from test.postgres.test_child_sql_opening import pending_child
from test.postgres.test_sql_intent_journal import api, control, marked, prepare, shared
from uuid import uuid4

import pytest
from psycopg import sql

pytestmark = pytest.mark.postgres


@contextmanager
def exchange(shared, tmp_path, monkeypatch, *, capacity=30):
    module = api()
    with marked(monkeypatch):
        repository, journal = prepare(shared, module, capacity=capacity)
        with control(shared[0]) as channel:
            with pending_child(shared[0], tmp_path) as (owner, marker, denied):
                yield module, repository, journal, channel, owner, marker


def startup(repository, channel, journal, subject, target):
    return repository.record_child_opening(
        channel, journal_id=journal, intent_id=str(uuid4()), subject=subject, destination=target
    )


def follow(
    repository, channel, journal, anchor, subject, target, *, sequence=1, request=None, **kw
):
    return repository.record_child_followup(
        channel,
        journal_id=journal,
        startup_intent_id=anchor.intent_id,
        intent_id=request or str(uuid4()),
        sequence=sequence,
        subject=subject,
        destination=target,
        **kw,
    )


@pytest.mark.parametrize("count", [1, 5, 20])
def test_requests_have_shared_ordinals_and_are_visible_before_ack(
    shared, postgres_service, tmp_path, monkeypatch, count
):
    with exchange(shared, tmp_path, monkeypatch, capacity=count + 1) as (m, r, j, c, owner, marker):
        receipts = []
        with postgres_service.connect_admin(shared[0].database) as observer:

            def before(subject):
                anchor = startup(r, c, j, subject, shared[0])
                for sequence in range(1, count + 1):
                    value = follow(r, c, j, anchor, subject, shared[0], sequence=sequence)
                    assert value.ordinal == sequence + 1 and value.sequence == sequence
                    row = observer.execute(
                        sql.SQL("SELECT sequence FROM {} WHERE intent_id=%s").format(
                            sql.Identifier(shared[0].schema, "_cao_sql_child_followups")
                        ),
                        (value.intent_id,),
                    ).fetchone()
                    assert row == (sequence,) and not marker.exists()
                    receipts.append(value)
                with pytest.raises(m.SqlIntentCapacityUnavailable):
                    follow(r, c, j, anchor, subject, shared[0], sequence=count + 1)

            owner._receive_binding(timeout_s=30, before_ack=before)
            assert owner.wait(timeout_s=30).returncode == 23
        for value in receipts:
            assert r.read_child_followup(c, journal_id=j, intent_id=value.intent_id) == value
        assert r.read_child_followup(c, journal_id=j, intent_id=str(uuid4())) is None


@pytest.mark.parametrize(
    "fault",
    [
        "repeat",
        "gap",
        "bool",
        "startup_uuid",
        "local_uuid",
        "wrong_anchor",
        "wrong_target",
        "expired",
    ],
)
def test_followup_refuses_invalid_sequence_replay_destination_and_subject(
    shared, postgres_service, tmp_path, monkeypatch, fault
):
    with exchange(shared, tmp_path, monkeypatch) as (m, r, j, c, owner, marker):
        held = []

        def before(subject):
            anchor = startup(r, c, j, subject, shared[0])
            held.extend((anchor, subject))
            local = r.record(
                c, journal_id=j, intent_id=str(uuid4()), path="native", destination=shared[0]
            )
            first = follow(r, c, j, anchor, subject, shared[0])
            if fault == "expired":
                return
            kwargs = dict(sequence=2, request=str(uuid4()))
            target = shared[0]
            selected = anchor
            if fault == "repeat":
                kwargs["request"] = first.intent_id
            elif fault == "gap":
                kwargs["sequence"] = 3
            elif fault == "bool":
                kwargs["sequence"] = True
            elif fault == "startup_uuid":
                kwargs["request"] = anchor.intent_id
            elif fault == "local_uuid":
                kwargs["request"] = local.intent_id
            elif fault == "wrong_anchor":
                selected = replace(anchor, intent_id=str(uuid4()))
            elif fault == "wrong_target":
                target = replace(target, store_id=str(uuid4()))
            expected, message = {
                "repeat": (m.SqlIntentConflict, "retained SQL intent"),
                "startup_uuid": (m.SqlIntentConflict, "retained SQL intent"),
                "local_uuid": (m.SqlIntentConflict, "retained SQL intent"),
                "gap": (m.SqlIntentConflict, "consecutive"),
                "bool": (ValueError, "positive bounded integer"),
                "wrong_anchor": (m.SqlIntentUnavailable, "anchor is unavailable"),
                "wrong_target": (m.SqlIntentUnavailable, "differs from its subject"),
            }[fault]
            with postgres_service.connect_admin(shared[0].database) as observer:
                count_sql = sql.SQL("SELECT count(*) FROM {}").format(
                    sql.Identifier(shared[0].schema, "_cao_sql_child_followups")
                )
                assert observer.execute(count_sql).fetchone() == (1,)
                with pytest.raises(expected, match=message):
                    follow(r, c, j, selected, subject, target, **kwargs)
                assert observer.execute(count_sql).fetchone() == (1,)
            good = follow(r, c, j, anchor, subject, shared[0], sequence=2)
            assert good.ordinal == 4
            with pytest.raises(m.SqlIntentConflict):
                r.record(
                    c, journal_id=j, intent_id=good.intent_id, path="native", destination=shared[0]
                )
            r.fence(c, journal_id=j)
            with pytest.raises(m.SqlIntentRegistrationSealed):
                follow(r, c, j, anchor, subject, shared[0], sequence=3)

        owner._receive_binding(timeout_s=30, before_ack=before)
        assert owner.wait(timeout_s=30).returncode == 23
        if fault == "expired":
            with pytest.raises(ValueError):
                follow(r, c, j, *held, shared[0], sequence=2)


@pytest.mark.parametrize("fault", ["missing_insert", "commit_ack", "postcommit"])
def test_commit_boundaries_preserve_only_confirmed_followup_receipts(
    shared, tmp_path, monkeypatch, fault
):
    from types import SimpleNamespace

    from cli_agent_orchestrator.clients.postgres.connection import FundedSqlCommitUncertain
    from cli_agent_orchestrator.models.sql_child_subject import VerifiedChildSqlSubject

    with exchange(shared, tmp_path, monkeypatch) as (m, r, j, c, owner, marker):

        def before(subject):
            anchor = startup(r, c, j, subject, shared[0])
            request = str(uuid4())
            transaction = c.funded_transaction
            query = r._q
            original = VerifiedChildSqlSubject.check
            after = [False]

            @contextmanager
            def wrapped():
                with transaction() as current:
                    yield current
                after[0] = True
                if fault == "commit_ack":
                    raise FundedSqlCommitUncertain("controlled lost ACK")

            def check(value):
                original(value)
                if value is subject and after[0]:
                    raise ValueError("controlled postcommit subject loss")

            def no_insert(connection, statement, values=()):
                if "INSERT INTO {followups}" in statement:
                    return SimpleNamespace(fetchone=lambda: None)
                return query(connection, statement, values)

            with monkeypatch.context() as patch:
                if fault == "missing_insert":
                    patch.setattr(r, "_q", no_insert)
                else:
                    patch.setattr(c, "funded_transaction", wrapped)
                    if fault == "postcommit":
                        patch.setattr(VerifiedChildSqlSubject, "check", check)
                expected = (
                    m.SchemaMismatch
                    if fault == "missing_insert"
                    else (
                        m.SqlIntentCommitUncertain
                        if fault == "commit_ack"
                        else m.SqlFollowupAckUnavailableAfterCommit
                    )
                )
                with pytest.raises(expected) as error:
                    follow(r, c, j, anchor, subject, shared[0], request=request)
            value = r.read_child_followup(c, journal_id=j, intent_id=request)
            if fault == "missing_insert":
                assert value is None
            else:
                assert value is not None and error.value.intent_id == request
                if fault == "postcommit":
                    assert error.value.receipt == value
                with pytest.raises(m.SqlIntentConflict, match="retained SQL intent"):
                    follow(r, c, j, anchor, subject, shared[0], request=request)

        owner._receive_binding(timeout_s=30, before_ack=before)
        assert owner.wait(timeout_s=30).returncode == 23


def test_another_live_child_cannot_adopt_the_original_startup(shared, tmp_path, monkeypatch):
    with exchange(shared, tmp_path / "first", monkeypatch) as (m, r, j, c, owner, marker):

        def before(subject):
            anchor = startup(r, c, j, subject, shared[0])
            with pending_child(shared[0], tmp_path / "second") as (other, _, _):

                def wrong(second):
                    with pytest.raises(m.SqlIntentUnavailable, match="another live subject"):
                        follow(r, c, j, anchor, second, shared[0])

                other._receive_binding(timeout_s=30, before_ack=wrong)
                assert other.wait(timeout_s=30).returncode == 23
            assert follow(r, c, j, anchor, subject, shared[0]).ordinal == 2

        owner._receive_binding(timeout_s=30, before_ack=before)
        assert owner.wait(timeout_s=30).returncode == 23


def test_version2_upgrade_preserves_a_real_anchor_and_adds_append_only_followups(
    shared, postgres_service, tmp_path, monkeypatch
):
    from dataclasses import asdict
    from test.postgres.test_admission_metadata_credits import ADMIN, grant
    from test.postgres.test_admission_reservations import domain

    from psycopg.types.json import Jsonb

    from cli_agent_orchestrator.clients.admission_repository import binding_digest
    from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
    from cli_agent_orchestrator.models.sql_startup_permission import sql_startup_destination_sha256
    from cli_agent_orchestrator.utils.process_sql_origin import current_sql_application_name

    m = api()
    target = shared[0]
    bound = shared[2]
    with marked(monkeypatch):
        migrate_component(target, m.SQL_OPENING_MIGRATIONS[:2])
        credit = grant(shared, domain(shared, 4))
        journal = str(uuid4())
        anchor_id = str(uuid4())
        with postgres_service.connect_admin(target.database) as admin:
            quota = sql.Identifier(target.schema, "_cao_sql_opening_quotas")
            children = sql.Identifier(target.schema, "_cao_sql_child_openings")
            admin.execute(
                sql.SQL(
                    """INSERT INTO {}(journal_id,credit_id,instance_id,process_generation,
                binding_sha256,max_records,actor_principal_id) VALUES(%s,%s,%s,%s,%s,30,%s)"""
                ).format(quota),
                (
                    journal,
                    credit.credit_id,
                    bound.instance_id,
                    bound.process_generation,
                    binding_digest(bound),
                    ADMIN,
                ),
            )
            r = m.SqlIntentRepository(target, binding_supplier=lambda: bound)
            with pending_child(target, tmp_path) as (owner, marker, _):

                def before(subject):
                    values = dict(
                        intent_id=anchor_id,
                        journal_id=journal,
                        ordinal=1,
                        parent_binding_sha256=binding_digest(bound),
                        parent_process_pid=bound.process_pid,
                        parent_process_started_at=bound.process_started_at,
                        parent_sql_application_name=current_sql_application_name(),
                        subject_binding=Jsonb(asdict(subject.binding)),
                        subject_binding_sha256=binding_digest(subject.binding),
                        subject_instance_id=subject.binding.instance_id,
                        subject_process_generation=subject.binding.process_generation,
                        subject_process_pid=subject.binding.process_pid,
                        subject_process_started_at=subject.binding.process_started_at,
                        subject_sql_application_name=subject.sql_application_name,
                        boot_id=subject.boot_id,
                        start_time_ticks=subject.start_time_ticks,
                        pid_namespace_device=subject.pid_namespace[0],
                        pid_namespace_inode=subject.pid_namespace[1],
                        path="native",
                        destination_store_id=target.store_id,
                        destination_scope=target.scope,
                        destination_sha256=sql_startup_destination_sha256(target),
                    )
                    admin.execute(
                        sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                            children,
                            sql.SQL(",").join(map(sql.Identifier, values)),
                            sql.SQL(",").join(sql.Placeholder() for _ in values),
                        ),
                        tuple(values.values()),
                    )
                    old = admin.execute(
                        sql.SQL("SELECT * FROM {} WHERE intent_id=%s").format(children),
                        (anchor_id,),
                    ).fetchone()
                    with control(target) as channel:
                        with pytest.raises(m.SchemaMismatch):
                            r.read_child_opening(channel, journal_id=journal, intent_id=anchor_id)
                    migrate_component(target, m.SQL_OPENING_MIGRATIONS)
                    assert (
                        admin.execute(
                            sql.SQL("SELECT * FROM {} WHERE intent_id=%s").format(children),
                            (anchor_id,),
                        ).fetchone()
                        == old
                    )
                    acl = admin.execute(
                        """SELECT has_table_privilege(%s,c.oid,'SELECT'),
                        has_table_privilege(%s,c.oid,'INSERT'),has_table_privilege(%s,c.oid,'UPDATE'),
                        has_table_privilege(%s,c.oid,'DELETE'),has_table_privilege(%s,c.oid,'TRUNCATE')
                        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                        WHERE n.nspname=%s AND c.relname='_cao_sql_child_followups' """,
                        (target.user,) * 5 + (target.schema,),
                    ).fetchone()
                    assert acl == (True, True, False, False, False)
                    with control(target) as channel:
                        anchor = r.read_child_opening(
                            channel, journal_id=journal, intent_id=anchor_id
                        )
                        assert follow(r, channel, journal, anchor, subject, target).ordinal == 2

                owner._receive_binding(timeout_s=30, before_ack=before)
                assert owner.wait(timeout_s=30).returncode == 23
