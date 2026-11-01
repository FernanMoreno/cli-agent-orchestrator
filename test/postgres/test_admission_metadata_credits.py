"""Durable metadata credits on isolated native PostgreSQL, not T013/T014 acceptance."""

import hashlib
import importlib
import multiprocessing
import os
from dataclasses import asdict, replace
from test.postgres.test_admission_reservations import api, binding, domain, request, shared
from uuid import uuid4

import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.migrations import (
    ComponentMigration,
    migrate_component,
    verify_runtime_role,
)
from cli_agent_orchestrator.models.resource_envelope import ResourceBinding

pytestmark = pytest.mark.postgres

ADMIN = "principal-" + "b" * 64
BASE_CHECKSUMS = (
    "6d1c5b17a332de83c647c2498f757da432ac13b502b2cead06d4c3950f5ef49e",
    "c183ecaffafcce3a3166fea88a0aea54bfd00d5820b20e9e5594105f1a2c8f40",
)


def grant(shared, record, credit_id=None, current=None):
    _, repository, bound, _ = shared
    return repository.grant_metadata_credit(
        credit_id or str(uuid4()), record.spec.domain_id, current or bound, principal_id=ADMIN
    )


def credit_rows(target, domain_id):
    with connect(target) as connection:
        return connection.execute(
            sql.SQL("SELECT count(*) FROM {} WHERE domain_id=%s").format(
                sql.Identifier(target.schema, "_cao_resource_metadata_credits")
            ),
            (domain_id,),
        ).fetchone()[0]


def test_applied_v1_v2_checksums_are_unchanged_and_v3_adds_two_tables():
    schema = importlib.import_module("cli_agent_orchestrator.clients.admission_schema")
    migrations = schema.RESOURCE_ADMISSION_MIGRATIONS
    assert tuple(m.checksum for m in migrations[:2]) == BASE_CHECKSUMS
    assert [m.version for m in migrations] == [1, 2, 3, 4, 5]
    assert set(migrations[2].tables) - set(migrations[0].tables) == set(
        schema.METADATA_CREDIT_TABLES
    )
    assert len(migrations[2].tables) == 9
    assert migrations[3].tables == migrations[2].tables
    assert set(migrations[4].tables) - set(migrations[3].tables) == set(schema.ADMIN_ACTION_TABLES)
    assert len(schema.AUTHORITY_TABLES + schema.LEDGER_TABLES) == 10


def test_credit_charges_capacity_once_and_blocks_new_admission(shared):
    _, repository, current, m = shared
    record = domain(shared, 1)
    credit = grant(shared, record)
    assert credit.amount == 1 and credit.revision == 1
    assert repository.used(record.spec.domain_id) == 1
    assert repository.metadata_credits_current() == (credit,)
    with pytest.raises(m.SharedCapacityUnavailable):
        repository.reserve(str(uuid4()), (request(record),))
    with pytest.raises(m.SharedCapacityUnavailable):
        repository.reserve_for_cost(str(uuid4()), m.SharedResourceCost(storage_slots=1))
    assert repository.used(record.spec.domain_id) == 1
    assert repository.audit_usage() == ()


def test_exact_retry_is_idempotent_and_changed_payload_is_rejected(shared):
    target, repository, current, m = shared
    record = domain(shared, 2)
    other = domain(shared, 2)  # Bootstrap the complete set before any credit.
    credit_id = str(uuid4())
    first = grant(shared, record, credit_id)
    assert grant(shared, record, credit_id) == first
    assert repository.used(record.spec.domain_id) == 1
    with pytest.raises(m.SharedRevisionConflict):
        grant(shared, other, credit_id)  # Same ID, different domain.
    assert repository.used(other.spec.domain_id) == 0
    assert credit_rows(target, record.spec.domain_id) == 1


def test_other_id_for_the_same_incarnation_is_denied_atomically(shared):
    target, repository, _, m = shared
    record = domain(shared, 3)
    grant(shared, record)
    with pytest.raises(m.SharedRevisionConflict):
        grant(shared, record)
    assert repository.used(record.spec.domain_id) == 1
    assert credit_rows(target, record.spec.domain_id) == 1


def test_member_revocation_retains_credit_and_counter(shared):
    _, repository, current, m = shared
    record = domain(shared, 2)
    credit = grant(shared, record)
    repository.provision_member(
        record.spec.domain_id, current, expected_revision=1, principal_id=ADMIN, active=False
    )
    assert repository.used(record.spec.domain_id) == 1
    assert repository.metadata_credits_current() == (credit,)
    assert repository.audit_usage() == ()
    with pytest.raises(m.SharedAdmissionDenied):
        repository.reserve(str(uuid4()), (request(record),))


def test_inactive_or_non_storage_authority_cannot_receive_credit(shared):
    _, repository, current, m = shared
    record = domain(shared, 1)
    repository.provision_member(
        record.spec.domain_id, current, expected_revision=1, principal_id=ADMIN, active=False
    )
    with pytest.raises(m.SharedAdmissionDenied):
        grant(shared, record)
    host = m.DomainSpec(
        str(uuid4()), str(uuid4()), "host", "bytes", 10, hashlib.sha256(uuid4().bytes).hexdigest()
    )
    repository.provision_domain(host, expected_revision=0, principal_id=ADMIN)
    repository.provision_member(host.domain_id, current, expected_revision=0, principal_id=ADMIN)
    with pytest.raises(m.SharedAdmissionDenied):
        repository.grant_metadata_credit(str(uuid4()), host.domain_id, current, principal_id=ADMIN)
    assert repository.used(record.spec.domain_id) == repository.used(host.domain_id) == 0


@pytest.mark.parametrize(
    "change",
    [
        {"resource_view_sha256": "c" * 64},
        {"process_pid": 1},
        {"process_started_at": 1.0},
    ],
)
def test_view_pid_or_birth_mismatch_is_never_adopted(shared, change):
    target, repository, current, m = shared
    record = domain(shared, 2)
    credit_id = str(uuid4())
    grant(shared, record, credit_id)
    moved = replace(current, **change)
    with pytest.raises(m.SharedRevisionConflict):
        repository.grant_metadata_credit(
            credit_id, record.spec.domain_id, moved, principal_id=ADMIN
        )
    other = m.ResourceAdmissionRepository(target, binding_supplier=lambda: moved)
    with pytest.raises(m.SharedAdmissionDenied):
        other.metadata_credits_current()  # Binding check or view mismatch; never adoption.
    assert repository.used(record.spec.domain_id) == 1


def test_member_digest_change_does_not_reassign_existing_credit(shared):
    target, repository, current, m = shared
    record = domain(shared, 2)
    credit = grant(shared, record)
    moved = replace(current, resource_view_sha256="d" * 64)
    repository.provision_member(
        record.spec.domain_id, moved, expected_revision=1, principal_id=ADMIN
    )
    with pytest.raises(m.SharedRevisionConflict):
        repository.grant_metadata_credit(
            str(uuid4()), record.spec.domain_id, moved, principal_id=ADMIN
        )
    # The credit stays bound to its original exact view, independent of membership.
    assert repository.metadata_credits_current() == (credit,)
    moved_reader = m.ResourceAdmissionRepository(target, binding_supplier=lambda: moved)
    with pytest.raises(m.SharedAdmissionDenied):
        moved_reader.metadata_credits_current()
    assert repository.used(record.spec.domain_id) == 1


def test_capacity_reduction_preserves_credit_debt(shared):
    _, repository, current, m = shared
    record = domain(shared, 1)
    grant(shared, record)
    reduced = repository.provision_domain(
        replace(record.spec, capacity=0), expected_revision=1, principal_id=ADMIN
    )
    assert repository.used(record.spec.domain_id) == 1
    assert repository.audit_usage() == ()
    with pytest.raises(m.SharedCapacityUnavailable):
        repository.reserve(str(uuid4()), (request(reduced),))


def test_audit_reports_counter_drift_including_credits(shared):
    target, repository, _, _ = shared
    record = domain(shared, 2)
    grant(shared, record)
    with connect(target.migration_target()) as owner, owner.transaction():
        owner.execute(
            sql.SQL("UPDATE {} SET committed=0 WHERE domain_id=%s").format(
                sql.Identifier(target.schema, "_cao_resource_usage")
            ),
            (record.spec.domain_id,),
        )
    mismatches = repository.audit_usage()
    assert [(v.domain_id, v.committed, v.allocated) for v in mismatches] == [
        (record.spec.domain_id, 0, 1)
    ]


def test_credit_acl_survives_later_generic_grants(shared):
    target, _, _, _ = shared
    schema = importlib.import_module("cli_agent_orchestrator.clients.admission_schema")
    migrate_component(
        target,
        (
            ComponentMigration(
                "metadata_credit_acl_probe",
                "main",
                1,
                ("CREATE TABLE metadata_credit_acl_probe (id INTEGER)",),
                ("metadata_credit_acl_probe",),
            ),
        ),
    )
    with connect(target) as runtime:
        verify_runtime_role(runtime)
        for table in schema.METADATA_CREDIT_TABLES:
            name = target.schema + "." + table
            privileges = runtime.execute(
                """SELECT has_table_privilege(current_user,%s,'SELECT'),
                has_any_column_privilege(current_user,%s,'INSERT'),
                has_any_column_privilege(current_user,%s,'UPDATE'),
                has_table_privilege(current_user,%s,'DELETE'),
                has_table_privilege(current_user,%s,'TRUNCATE'),
                has_table_privilege('public',%s,'SELECT')""",
                (name,) * 6,
            ).fetchone()
            assert tuple(privileges) == (True, False, False, False, False, False)


def _credit_worker(target, domain_id, gate, channel):
    try:
        m = api()
        current = binding(target)
        repository = m.ResourceAdmissionRepository(target, binding_supplier=lambda: current)
        channel.send(asdict(current))
        assert gate.wait(20)
        try:
            repository.grant_metadata_credit(str(uuid4()), domain_id, current, principal_id=ADMIN)
            channel.send(("credited", os.getpid()))
        except m.SharedCapacityUnavailable:
            channel.send(("saturated", os.getpid()))
    except BaseException as error:
        channel.send(("error", type(error).__name__))
        raise
    finally:
        channel.close()


def test_two_real_processes_compete_for_one_metadata_credit(shared):
    target, repository, _, _ = shared
    record = domain(shared, 1)
    context = multiprocessing.get_context("spawn")
    gate = context.Event()
    processes, parents = [], []
    try:
        for _ in range(2):
            parent, child = context.Pipe()
            process = context.Process(
                target=_credit_worker, args=(target, record.spec.domain_id, gate, child)
            )
            process.start()
            child.close()
            processes.append(process)
            parents.append(parent)
        for channel in parents:
            assert channel.poll(20)
            repository.provision_member(
                record.spec.domain_id,
                ResourceBinding(**channel.recv()),
                expected_revision=0,
                principal_id=ADMIN,
            )
        gate.set()
        results = []
        for channel in parents:
            assert channel.poll(20)
            results.append(channel.recv())
        for process in processes:
            process.join(20)
            assert process.exitcode == 0
        assert sorted(result[0] for result in results) == ["credited", "saturated"]
        assert repository.used(record.spec.domain_id) == 1
        assert credit_rows(target, record.spec.domain_id) == 1
    finally:
        gate.set()
        for process in processes:
            process.join(20)
            if process.is_alive():
                process.terminate()
                process.join(20)
        for channel in parents:
            channel.close()


def test_credit_invalidation_before_commit_rolls_back_charge_and_history(shared, monkeypatch):
    target, repository, current, module = shared
    record = domain(shared, 2)
    original = repository._change_used

    def invalidate(connection, domain_id, before, after):
        original(connection, domain_id, before, after)
        with repository._usage_guard:
            repository._usage_uncertain = True

    with monkeypatch.context() as patch:
        patch.setattr(repository, "_change_used", invalidate)
        with pytest.raises(module.SharedAdmissionDenied):
            grant(shared, record)
    assert repository.used(record.spec.domain_id) == 0
    assert credit_rows(target, record.spec.domain_id) == 0
    with connect(target, readonly=True) as observer:
        count = observer.execute(
            sql.SQL("SELECT count(*) FROM {}").format(
                sql.Identifier(target.schema, "_cao_resource_metadata_credit_history")
            )
        ).fetchone()[0]
    assert count == 0


def test_credit_invalidation_after_commit_retains_id_and_exact_retry(shared, monkeypatch):
    from contextlib import contextmanager

    _, repository, current, module = shared
    record = domain(shared, 2)
    credit_id = str(uuid4())
    original = module.connect

    @contextmanager
    def invalidate_after_commit(selected, *args, **kwargs):
        with original(selected, *args, **kwargs) as connection:
            yield connection
        if not kwargs.get("readonly", False):
            with repository._usage_guard:
                repository._usage_uncertain = True

    with monkeypatch.context() as patch:
        patch.setattr(module, "connect", invalidate_after_commit)
        with pytest.raises(module.SharedCommitUncertain):
            grant(shared, record, credit_id)
    assert repository.used(record.spec.domain_id) == 1
    assert repository.audit_usage() == ()
    receipt = grant(shared, record, credit_id)
    assert receipt.credit_id == credit_id
    assert repository.used(record.spec.domain_id) == 1


def test_actual_metadata_connection_close_precedes_local_retirement(shared, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from test.postgres.test_shared_admission_mapping import add_domain
    from test.services.test_delivery_admission import observe

    from cli_agent_orchestrator.clients.storage_admission import physical_storage_scope
    from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
    from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

    target, repository, current, module = shared
    storage = domain(shared, 3)
    for kind in ("host", "node"):
        add_domain(shared, kind, "bytes", 4096 * 1024 * 1024)
        add_domain(shared, kind, "millicores", 16000)
    grant(shared, storage)
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    assert observe(owner, 8)
    attempt = owner.try_reserve("actual-retirement", now=10, storage_slots=2)
    assert attempt.reservation is not None
    lease = attempt.reservation
    assert lease.start()
    hold = lease.hold_retirement()
    footprint = lease.footprint
    with permit.operation(clock=lambda: 10, wait=True):
        prepared = repository.reserve_for_cost(
            str(uuid4()),
            module.SharedResourceCost(
                footprint.memory_bytes, footprint.cpu_millicores, footprint.storage_slots
            ),
        )
        active = repository.transition(prepared.operation_id, expected_revision=1, state="active")
    assert repository.used(storage.spec.domain_id) == 3
    with physical_storage_scope(lease, clock=lambda: 10):
        with connect(target) as body:
            assert body.execute("SELECT 1").fetchone()[0] == 1
            assert not lease.finish_after_storage()
            assert not hold.physical_ready
        assert hold.physical_ready  # The actual psycopg raw connection closed.

    closing, can_close = threading.Event(), threading.Event()
    counter_lock = threading.Lock()
    opens = 0
    real_connect = module.connect

    @contextmanager
    def barrier_close(selected, *args, **kwargs):
        nonlocal opens
        assert not owner._process_guard._is_owned(), "SQL entered under local admission guard"
        with counter_lock:
            opens += 1
            number = opens
        with real_connect(selected, *args, **kwargs) as connection:
            yield connection
            if number == 2:
                # The inner transaction committed released, but this metadata
                # connection is still physically alive and charged separately.
                closing.set()
                assert can_close.wait(20)

    def retire():
        with permit.operation(clock=lambda: 10, retirement=hold, wait=True):
            releasing = repository.transition(
                active.operation_id, expected_revision=2, state="releasing"
            )
            return repository.transition(
                active.operation_id, expected_revision=releasing.revision, state="released"
            )

    with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=1) as executor:
        patch.setattr(module, "connect", barrier_close)
        future = executor.submit(retire)
        try:
            assert closing.wait(20)
            assert repository.used(storage.spec.domain_id) == 1  # Still the metadata credit.
            assert owner.snapshot(now=10).control_connection_loans == 1
            assert not hold.finish() and not future.done()
        finally:
            can_close.set()
        assert future.result(timeout=20).state == "released"
    assert owner.snapshot(now=10).control_connection_loans == 0
    assert hold.finish()
    assert owner.snapshot(now=10).inflight == 0
    assert repository.used(storage.spec.domain_id) == 1
    assert repository.audit_usage() == ()


def test_integer_birth_in_valid_binding_returns_canonical_credit_after_commit(shared):
    _, repository, current, _ = shared
    record = domain(shared, 2)
    captured = replace(current, process_started_at=int(current.process_started_at))
    repository.provision_member(
        record.spec.domain_id, captured, expected_revision=1, principal_id=ADMIN
    )
    credit_id = str(uuid4())
    receipt = grant(shared, record, credit_id, captured)
    assert type(receipt.process_started_at) is float
    assert receipt.process_started_at == captured.process_started_at
    assert grant(shared, record, credit_id, captured) == receipt
    assert repository.used(record.spec.domain_id) == 1
