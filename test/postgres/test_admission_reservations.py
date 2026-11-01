"""Shared admission protocol on isolated native PostgreSQL, not physical M2 acceptance."""

import hashlib
import importlib
import json
import multiprocessing
import os
from dataclasses import asdict, replace
from uuid import uuid4

import psutil
import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.migrations import (
    ComponentMigration,
    migrate_component,
    verify_runtime_role,
)
from cli_agent_orchestrator.clients.postgres.registry import component_migrations
from cli_agent_orchestrator.models.resource_envelope import ResourceBinding

pytestmark = pytest.mark.postgres


def api():
    return importlib.import_module("cli_agent_orchestrator.clients.admission_repository")


def binding(target):
    mnt, cg = (os.stat("/proc/self/ns/" + name) for name in ("mnt", "cgroup"))
    return ResourceBinding(
        str(uuid4()),
        str(uuid4()),
        os.getpid(),
        psutil.Process().create_time(),
        target.store_id,
        hashlib.sha256(json.dumps(target.identity, separators=(",", ":")).encode()).hexdigest(),
        mnt.st_dev,
        mnt.st_ino,
        cg.st_dev,
        cg.st_ino,
        "a" * 64,
    )


@pytest.fixture
def shared(postgres_targets):
    m = api()
    schema = importlib.import_module("cli_agent_orchestrator.clients.admission_schema")
    target = postgres_targets.main
    migrate_component(target, schema.RESOURCE_ADMISSION_MIGRATIONS)
    current = binding(target)
    repository = m.ResourceAdmissionRepository(target, binding_supplier=lambda: current)
    assert repository.audit_usage() == ()
    return target, repository, current, m


def domain(shared, capacity=1):
    target, repository, current, m = shared
    spec = m.DomainSpec(
        str(uuid4()),
        str(uuid4()),
        "storage",
        "postgres_session",
        capacity,
        hashlib.sha256(uuid4().bytes).hexdigest(),
    )
    record = repository.provision_domain(
        spec, expected_revision=0, principal_id="principal-" + "b" * 64
    )
    repository.provision_member(
        spec.domain_id, current, expected_revision=0, principal_id="principal-" + "b" * 64
    )
    return record


def request(record, amount=1):
    return api().DomainRequest(
        record.spec.domain_id, record.spec.incarnation, record.revision, amount
    )


def test_registration_and_authority_acl_survive_later_generic_grants(shared):
    target, repository, current, m = shared
    schema = importlib.import_module("cli_agent_orchestrator.clients.admission_schema")
    groups = component_migrations("main")
    assert schema.RESOURCE_ADMISSION_MIGRATIONS in groups
    assert groups[-1][0].component == "import_receipts"
    for scope in ("auth", "peer"):
        assert not any(
            group[0].component == "resource_admission" for group in component_migrations(scope)
        )
    domain(shared)
    migrate_component(
        target,
        (
            ComponentMigration(
                "admission_acl_probe",
                "main",
                1,
                ("CREATE TABLE admission_acl_probe (id INTEGER)",),
                ("admission_acl_probe",),
            ),
        ),
    )
    with connect(target) as runtime:
        verify_runtime_role(runtime)
        for table in schema.AUTHORITY_TABLES:
            privileges = runtime.execute(
                "SELECT has_table_privilege(current_user,%s,'SELECT'),has_any_column_privilege(current_user,%s,'UPDATE'),has_table_privilege(current_user,%s,'TRUNCATE')",
                (target.schema + "." + table,) * 3,
            ).fetchone()
            assert tuple(privileges) == (True, False, False)
        for table in schema.LEDGER_TABLES:
            assert (
                runtime.execute(
                    "SELECT has_table_privilege(current_user,%s,'TRUNCATE')",
                    (target.schema + "." + table,),
                ).fetchone()[0]
                is False
            )


def test_multidomain_denial_is_atomic_and_unknown_member_is_not_authority(shared):
    target, repository, current, m = shared
    a, b = domain(shared, 2), domain(shared, 0)
    with pytest.raises(m.SharedCapacityUnavailable):
        repository.reserve(str(uuid4()), (request(a), request(b)))
    assert repository.used(a.spec.domain_id) == repository.used(b.spec.domain_id) == 0
    foreign = m.ResourceAdmissionRepository(
        target, binding_supplier=lambda: replace(current, process_generation=str(uuid4()))
    )
    with pytest.raises(m.SharedAdmissionDenied):
        foreign.reserve(str(uuid4()), (request(a),))
    assert repository.used(a.spec.domain_id) == 0


def test_idempotent_retry_never_double_charges_or_changes_the_request(shared):
    _, repository, _, m = shared
    a = domain(shared, 3)
    operation = str(uuid4())
    first = repository.reserve(operation, (request(a),))
    assert repository.reserve(operation, (request(a),)) == first
    assert repository.used(a.spec.domain_id) == 1
    with pytest.raises(m.SharedRevisionConflict):
        repository.reserve(operation, (request(a, 2),))
    assert repository.used(a.spec.domain_id) == 1


def test_uncertain_and_releasing_remain_charged_until_exact_release(shared):
    _, repository, _, m = shared
    a = domain(shared)
    receipt = repository.reserve(str(uuid4()), (request(a),))
    receipt = repository.transition(
        receipt.operation_id, expected_revision=receipt.revision, state="active"
    )
    receipt = repository.transition(
        receipt.operation_id, expected_revision=receipt.revision, state="uncertain"
    )
    with pytest.raises(m.SharedCapacityUnavailable):
        repository.reserve(str(uuid4()), (request(a),))
    receipt = repository.transition(
        receipt.operation_id, expected_revision=receipt.revision, state="releasing"
    )
    assert repository.used(a.spec.domain_id) == 1
    with pytest.raises(m.SharedRevisionConflict):
        repository.transition(receipt.operation_id, expected_revision=1, state="released")
    receipt = repository.transition(
        receipt.operation_id, expected_revision=receipt.revision, state="released"
    )
    assert repository.used(a.spec.domain_id) == 0
    assert repository.reserve(receipt.operation_id, (request(a),)) == receipt
    assert repository.used(a.spec.domain_id) == 0


def test_revocation_and_budget_debt_preserve_already_owned_completion(shared):
    _, repository, current, m = shared
    a = domain(shared, 2)
    first = repository.reserve(str(uuid4()), (request(a),))
    smaller = repository.provision_domain(
        replace(a.spec, capacity=0), expected_revision=1, principal_id="principal-" + "b" * 64
    )
    assert repository.used(a.spec.domain_id) == 1
    with pytest.raises(m.SharedCapacityUnavailable):
        repository.reserve(str(uuid4()), (request(smaller),))
    repository.provision_member(
        a.spec.domain_id,
        current,
        expected_revision=1,
        principal_id="principal-" + "b" * 64,
        active=False,
    )
    repository.transition(first.operation_id, expected_revision=1, state="released")
    assert repository.used(a.spec.domain_id) == 0
    with pytest.raises(m.SharedAdmissionDenied):
        repository.reserve(str(uuid4()), (request(smaller),))


def test_stale_generation_cannot_finish_someone_elses_reservation(shared):
    target, repository, current, m = shared
    a = domain(shared)
    receipt = repository.reserve(str(uuid4()), (request(a),))
    other = m.ResourceAdmissionRepository(
        target, binding_supplier=lambda: replace(current, process_generation=str(uuid4()))
    )
    with pytest.raises(m.SharedAdmissionDenied):
        other.transition(receipt.operation_id, expected_revision=1, state="released")
    assert repository.used(a.spec.domain_id) == 1
    repository.transition(receipt.operation_id, expected_revision=1, state="released")


def test_domain_incarnation_and_identity_cannot_be_silently_replaced(shared):
    _, repository, _, m = shared
    a = domain(shared)
    with pytest.raises(m.SharedRevisionConflict):
        repository.provision_domain(
            replace(a.spec, incarnation=str(uuid4())),
            expected_revision=1,
            principal_id="principal-" + "b" * 64,
        )
    stale = replace(request(a), incarnation=str(uuid4()))
    with pytest.raises(m.SharedRevisionConflict):
        repository.reserve(str(uuid4()), (stale,))
    assert repository.used(a.spec.domain_id) == 0


def _race_worker(target, requests, gate, channel):
    try:
        m = api()
        current = binding(target)
        repository = m.ResourceAdmissionRepository(target, binding_supplier=lambda: current)
        channel.send(asdict(current))
        assert gate.wait(20)
        try:
            receipt = (
                repository.reserve_for_cost(str(uuid4()), requests)
                if type(requests) is m.SharedResourceCost
                else repository.reserve(str(uuid4()), requests)
            )
            channel.send(("reserved", receipt.operation_id, os.getpid()))
        except m.SharedCapacityUnavailable:
            channel.send(("saturated", None, os.getpid()))
    except BaseException as error:
        channel.send(("error", type(error).__name__, os.getpid()))
        raise
    finally:
        channel.close()


@pytest.mark.parametrize("mapped", (False, True))
def test_two_real_processes_compete_for_one_session_without_overallocation(shared, mapped):
    target, repository, _, module = shared
    a = domain(shared)
    context = multiprocessing.get_context("spawn")
    gate = context.Event()
    processes, parents = [], []
    try:
        for _ in range(2):
            parent, child = context.Pipe()
            quantities = module.SharedResourceCost(storage_slots=1) if mapped else (request(a),)
            process = context.Process(target=_race_worker, args=(target, quantities, gate, child))
            process.start()
            child.close()
            processes.append(process)
            parents.append(parent)
        for channel in parents:
            assert channel.poll(20)
            value = channel.recv()
            assert isinstance(value, dict)
            repository.provision_member(
                a.spec.domain_id,
                ResourceBinding(**value),
                expected_revision=0,
                principal_id="principal-" + "b" * 64,
            )
        gate.set()
        results = []
        for channel in parents:
            assert channel.poll(20)
            results.append(channel.recv())
        for process in processes:
            process.join(20)
            assert process.exitcode == 0
        assert sorted(result[0] for result in results) == ["reserved", "saturated"]
        assert len({result[2] for result in results}) == 2
        assert repository.used(a.spec.domain_id) == 1
        # The winning process exiting is not evidence of release: preserve debt.
    finally:
        gate.set()
        for process in processes:
            process.join(20)
            if process.is_alive():
                process.terminate()
                process.join(20)
        for channel in parents:
            channel.close()


@pytest.mark.parametrize("mapped", (False, True))
def test_lost_acquire_and_release_acknowledgements_reconcile_exactly_once(
    shared, monkeypatch, mapped
):
    from contextlib import contextmanager

    target, repository, _, m = shared
    a = domain(shared)
    operation = str(uuid4())
    real_connect = m.connect

    def reserve():
        if mapped:
            return repository.reserve_for_cost(operation, m.SharedResourceCost(storage_slots=1))
        return repository.reserve(operation, (request(a),))

    @contextmanager
    def lost_ack(selected, *args, **kwargs):
        with real_connect(selected, *args, **kwargs) as connection:
            yield connection
        raise RuntimeError("controlled-ack-lost-after-real-commit")

    with monkeypatch.context() as patch:
        patch.setattr(m, "connect", lost_ack)
        with pytest.raises(RuntimeError, match="controlled-ack-lost"):
            reserve()
    assert repository.used(a.spec.domain_id) == 1
    receipt = reserve()
    assert receipt.revision == 1
    with monkeypatch.context() as patch:
        patch.setattr(m, "connect", lost_ack)
        with pytest.raises(RuntimeError, match="controlled-ack-lost"):
            repository.transition(operation, expected_revision=receipt.revision, state="released")
    assert repository.used(a.spec.domain_id) == 0
    released = repository.transition(
        operation, expected_revision=receipt.revision, state="released"
    )
    assert released.state == "released" and released.revision == 2
    assert repository.used(a.spec.domain_id) == 0


def test_effective_lock_order_and_hash_collision_do_not_merge_domains(shared, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    _, repository, _, m = shared
    domains = [domain(shared) for _ in range(3)]
    original = repository._lock_key
    keys = {
        domains[0].spec.domain_id: 7,
        domains[1].spec.domain_id: -3,
        domains[2].spec.domain_id: 7,
    }
    monkeypatch.setattr(
        repository, "_lock_key", lambda identity: keys.get(identity, original(identity))
    )
    recorded = []

    class Recorder:
        def execute(self, statement, parameters):
            recorded.append(parameters)

    repository._lock(
        Recorder(), m._DOMAIN_FAMILY, [value.spec.domain_id for value in reversed(domains)]
    )
    assert recorded == [(m._DOMAIN_FAMILY, -3), (m._DOMAIN_FAMILY, 7)]
    barrier = Barrier(2)

    def reserve(values):
        barrier.wait(20)
        try:
            repository.reserve(str(uuid4()), tuple(map(request, values)))
            return "reserved"
        except m.SharedCapacityUnavailable:
            return "saturated"

    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(reserve, domains)
        b = executor.submit(reserve, list(reversed(domains)))
        assert sorted((a.result(20), b.result(20))) == ["reserved", "saturated"]
    assert [repository.used(value.spec.domain_id) for value in domains] == [1, 1, 1]


def test_authority_is_read_after_real_writer_lock_not_from_a_stale_snapshot(shared, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, local

    _, repository, _, m = shared
    a = domain(shared)
    held, release, waiting = Event(), Event(), Event()
    role = local()
    original = repository._lock

    def observed(connection, family, identities):
        if family == m._DOMAIN_FAMILY and getattr(role, "value", None) == "writer":
            original(connection, family, identities)
            held.set()
            assert release.wait(20)
            return
        if family == m._DOMAIN_FAMILY and getattr(role, "value", None) == "reserve":
            waiting.set()
        return original(connection, family, identities)

    def write():
        role.value = "writer"
        return repository.provision_domain(
            replace(a.spec, capacity=0), expected_revision=1, principal_id="principal-" + "b" * 64
        )

    def reserve():
        role.value = "reserve"
        return repository.reserve(str(uuid4()), (request(a),))

    monkeypatch.setattr(repository, "_lock", observed)
    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(write)
        try:
            assert held.wait(20)
            pending = executor.submit(reserve)
            assert waiting.wait(20)
        finally:
            release.set()
        assert writer.result(20).revision == 2
        with pytest.raises(m.SharedRevisionConflict):
            pending.result(20)
    assert repository.used(a.spec.domain_id) == 0
    assert repository.audit_usage() == ()


def test_public_column_grants_cannot_forge_shared_authority(shared):
    target, repository, _, _ = shared
    a = domain(shared)
    with connect(target.migration_target()) as owner:
        owner.execute(
            sql.SQL("GRANT UPDATE (capacity) ON {} TO PUBLIC").format(
                sql.Identifier(target.schema, "_cao_resource_domains")
            )
        )
    from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch

    with pytest.raises(SchemaMismatch):
        repository.used(a.spec.domain_id)


@pytest.mark.parametrize("change", ["domain", "revoked", "membership"])
def test_unstarted_reservation_revalidates_authority_before_active(shared, change):
    _, repository, current, m = shared
    a = domain(shared, 2)
    receipt = repository.reserve(str(uuid4()), (request(a),))
    if change == "domain":
        repository.provision_domain(
            replace(a.spec, capacity=3), expected_revision=1, principal_id="principal-" + "b" * 64
        )
    else:
        repository.provision_member(
            a.spec.domain_id,
            current,
            expected_revision=1,
            principal_id="principal-" + "b" * 64,
            active=change != "revoked",
        )
    with pytest.raises((m.SharedAdmissionDenied, m.SharedRevisionConflict)):
        repository.transition(receipt.operation_id, expected_revision=1, state="active")
    assert repository.used(a.spec.domain_id) == 1
    repository.transition(receipt.operation_id, expected_revision=1, state="released")
    assert repository.used(a.spec.domain_id) == 0


def test_independent_counter_audit_detects_drift_and_never_repairs_it_silently(shared, monkeypatch):
    target, repository, _, m = shared
    a = domain(shared, 2)
    repository.reserve(str(uuid4()), (request(a),))
    assert repository.audit_usage() == ()
    with connect(target.migration_target()) as owner:
        owner.execute(
            sql.SQL("UPDATE {} SET committed=0 WHERE domain_id=%s").format(
                sql.Identifier(target.schema, "_cao_resource_usage")
            ),
            (a.spec.domain_id,),
        )
    mismatches = repository.audit_usage()
    assert len(mismatches) == 1
    assert (mismatches[0].domain_id, mismatches[0].committed, mismatches[0].allocated) == (
        a.spec.domain_id,
        0,
        1,
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            m,
            "connect",
            lambda *args, **kwargs: pytest.fail("known uncertain usage must deny before SQL"),
        )
        with pytest.raises(m.SharedAdmissionDenied):
            repository.reserve(str(uuid4()), (request(a),))
    assert repository.used(a.spec.domain_id) == 0  # Detection does not invent a repair.


@pytest.mark.parametrize(
    "kind,unit,amount",
    [
        ("host", "bytes", 64 * 1024**2),
        ("node", "millicores", 250),
        ("storage", "postgres_session", 1),
    ],
)
def test_declared_domain_units_keep_original_integer_quantities(shared, kind, unit, amount):
    target, repository, current, m = shared
    spec = m.DomainSpec(
        str(uuid4()),
        str(uuid4()),
        kind,
        unit,
        2 * amount,
        hashlib.sha256(uuid4().bytes).hexdigest(),
    )
    record = repository.provision_domain(
        spec, expected_revision=0, principal_id="principal-" + "b" * 64
    )
    repository.provision_member(
        spec.domain_id, current, expected_revision=0, principal_id="principal-" + "b" * 64
    )
    first = repository.reserve(str(uuid4()), (request(record, amount),))
    repository.reserve(str(uuid4()), (request(record, amount),))
    assert repository.used(spec.domain_id) == 2 * amount
    with connect(target, readonly=True) as observer:
        allocated = observer.execute(
            sql.SQL(
                "SELECT sum(a.amount) FROM {} a JOIN {} o USING(operation_id) WHERE a.domain_id=%s AND o.state<>'released'"
            ).format(
                sql.Identifier(target.schema, "_cao_resource_allocations"),
                sql.Identifier(target.schema, "_cao_resource_operations"),
            ),
            (spec.domain_id,),
        ).fetchone()[0]
        assert allocated == 2 * amount
    with pytest.raises(m.SharedCapacityUnavailable):
        repository.reserve(str(uuid4()), (request(record, amount),))
    repository.transition(first.operation_id, expected_revision=1, state="released")
    assert repository.used(spec.domain_id) == amount
    assert repository.audit_usage() == ()


def test_ordinary_reservation_does_not_rescan_released_history_after_initial_audit(
    shared, monkeypatch
):
    from contextlib import contextmanager

    _, repository, _, m = shared
    a = domain(shared)
    queries = []
    real_connect = m.connect

    class Observed:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, statement, *args, **kwargs):
            queries.append(
                statement.as_string() if hasattr(statement, "as_string") else str(statement)
            )
            return self.connection.execute(statement, *args, **kwargs)

    @contextmanager
    def observed_connect(*args, **kwargs):
        with real_connect(*args, **kwargs) as connection:
            yield Observed(connection)

    def measured():
        queries.clear()
        with monkeypatch.context() as patch:
            patch.setattr(m, "connect", observed_connect)
            receipt = repository.reserve(str(uuid4()), (request(a),))
        statements = list(queries)
        repository.transition(receipt.operation_id, expected_revision=1, state="released")
        return statements

    first = measured()
    for _ in range(5):
        measured()
    later = measured()
    assert len(first) == len(later)
    assert not any("sum(a.amount)" in query.lower() for query in later)
    assert repository.audit_usage() == ()


def test_audit_invalidation_before_commit_rolls_back_a_new_reservation(shared, monkeypatch):
    _, repository, _, m = shared
    a = domain(shared)
    original = repository._query

    def invalidating(connection, statement, parameters=()):
        result = original(connection, statement, parameters)
        if statement.startswith("INSERT INTO {allocations}"):
            with repository._usage_guard:
                repository._usage_uncertain = True
        return result

    with monkeypatch.context() as patch:
        patch.setattr(repository, "_query", invalidating)
        with pytest.raises(m.SharedAdmissionDenied):
            repository.reserve(str(uuid4()), (request(a),))
    assert repository.used(a.spec.domain_id) == 0
    assert repository.audit_usage() == ()


def test_audit_invalidation_after_commit_preserves_id_and_durable_charge(shared, monkeypatch):
    from contextlib import contextmanager

    _, repository, _, m = shared
    a = domain(shared)
    operation_id = str(uuid4())
    real_connect = m.connect

    @contextmanager
    def invalidate_after_commit(*args, **kwargs):
        with real_connect(*args, **kwargs) as connection:
            yield connection
        with repository._usage_guard:
            repository._usage_uncertain = True

    with monkeypatch.context() as patch:
        patch.setattr(m, "connect", invalidate_after_commit)
        with pytest.raises(m.SharedCommitUncertain):
            repository.reserve(operation_id, (request(a),))
    assert repository.used(a.spec.domain_id) == 1
    assert repository.audit_usage() == ()
    receipt = repository.reserve(operation_id, (request(a),))
    assert receipt.operation_id == operation_id and receipt.revision == 1
    assert repository.used(a.spec.domain_id) == 1
