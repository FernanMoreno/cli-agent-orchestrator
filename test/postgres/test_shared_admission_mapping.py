"""Complete private membership mapping; not physical Inbox/Work acceptance."""

import hashlib
from dataclasses import replace
from test.postgres.test_admission_reservations import domain, shared
from uuid import uuid4

import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import connect

pytestmark = pytest.mark.postgres


def quantities(module, memory=0, cpu=0, storage=1):
    return module.SharedResourceCost(memory, cpu, storage)


def add_domain(shared, kind, unit, capacity):
    _, repository, current, module = shared
    spec = module.DomainSpec(
        str(uuid4()), str(uuid4()), kind, unit, capacity, hashlib.sha256(uuid4().bytes).hexdigest()
    )
    repository.provision_domain(spec, expected_revision=0, principal_id="principal-" + "b" * 64)
    repository.provision_member(
        spec.domain_id, current, expected_revision=0, principal_id="principal-" + "b" * 64
    )
    return spec


def test_server_maps_all_units_and_preserves_original_charges(shared):
    target, repository, _, module = shared
    memory = add_domain(shared, "host", "bytes", 8192)
    node_memory = add_domain(shared, "node", "bytes", 8192)
    cpu = add_domain(shared, "node", "millicores", 1000)
    host_cpu = add_domain(shared, "host", "millicores", 1000)
    storage = domain(shared, 5).spec
    cost = quantities(module, 2048, 250, 2)
    op = str(uuid4())
    prepared = repository.reserve_for_cost(op, cost)
    assert repository.reserve_for_cost(op, cost) == prepared
    active = repository.transition(op, expected_revision=prepared.revision, state="active")
    specs = (memory, node_memory, cpu, host_cpu, storage)
    assert [repository.used(s.domain_id) for s in specs] == [2048, 2048, 250, 250, 2]
    with connect(target, readonly=True) as observer:
        row = observer.execute(
            sql.SQL("SELECT membership_sha256,cost_sha256 FROM {} WHERE operation_id=%s").format(
                sql.Identifier(target.schema, "_cao_resource_operations")
            ),
            (op,),
        ).fetchone()
        assert all(isinstance(v, str) and len(v) == 64 for v in row)
    with pytest.raises(module.SharedRevisionConflict):
        repository.reserve_for_cost(op, quantities(module, 1024, 250, 2))
    releasing = repository.transition(op, expected_revision=active.revision, state="releasing")
    repository.transition(op, expected_revision=releasing.revision, state="released")
    assert [repository.used(s.domain_id) for s in specs] == [0, 0, 0, 0, 0]


def test_new_membership_before_active_invalidates_complete_mapping(shared):
    _, repository, _, module = shared
    first = domain(shared, 2)
    prepared = repository.reserve_for_cost(str(uuid4()), quantities(module))
    second = domain(shared, 2)
    with pytest.raises(module.SharedRevisionConflict):
        repository.transition(prepared.operation_id, expected_revision=1, state="active")
    assert repository.used(first.spec.domain_id) == 1
    assert repository.used(second.spec.domain_id) == 0
    # Same ID acknowledges original charge even when the map has changed.
    assert repository.reserve_for_cost(prepared.operation_id, quantities(module)) == prepared
    repository.transition(prepared.operation_id, expected_revision=1, state="released")
    assert repository.used(first.spec.domain_id) == 0


def test_zero_cost_membership_still_fences_activation(shared):
    _, repository, _, module = shared
    storage = domain(shared, 2)
    prepared = repository.reserve_for_cost(str(uuid4()), quantities(module))
    add_domain(shared, "host", "bytes", 100)
    with pytest.raises(module.SharedRevisionConflict):
        repository.transition(prepared.operation_id, expected_revision=1, state="active")
    assert repository.used(storage.spec.domain_id) == 1


@pytest.mark.parametrize("memory,cpu,storage", [(1, 0, 1), (0, 1, 1), (0, 0, 0)])
def test_uncovered_or_empty_cost_cannot_admit(shared, memory, cpu, storage):
    _, repository, _, module = shared
    known = domain(shared, 3)
    with pytest.raises((module.SharedAdmissionDenied, ValueError)):
        repository.reserve_for_cost(str(uuid4()), quantities(module, memory, cpu, storage))
    assert repository.used(known.spec.domain_id) == 0


def test_withdrawn_or_changed_membership_cannot_be_omitted(shared):
    _, repository, current, module = shared
    first, second = domain(shared, 3), domain(shared, 3)
    repository.provision_member(
        second.spec.domain_id,
        current,
        expected_revision=1,
        principal_id="principal-" + "b" * 64,
        active=False,
    )
    with pytest.raises(module.SharedAdmissionDenied):
        repository.reserve_for_cost(str(uuid4()), quantities(module))
    assert repository.used(first.spec.domain_id) == 0
    repository.provision_member(
        second.spec.domain_id,
        replace(current, resource_view_sha256="d" * 64),
        expected_revision=2,
        principal_id="principal-" + "b" * 64,
    )
    with pytest.raises(module.SharedAdmissionDenied):
        repository.reserve_for_cost(str(uuid4()), quantities(module))
    assert repository.used(first.spec.domain_id) == 0


def test_raw_and_complete_mapping_cannot_reinterpret_each_other(shared):
    from test.postgres.test_admission_reservations import request

    _, repository, _, module = shared
    record = domain(shared, 4)
    raw = repository.reserve(str(uuid4()), (request(record),))
    with pytest.raises(module.SharedRevisionConflict):
        repository.reserve_for_cost(raw.operation_id, quantities(module))
    mapped = repository.reserve_for_cost(str(uuid4()), quantities(module))
    with pytest.raises(module.SharedRevisionConflict):
        repository.reserve(mapped.operation_id, (request(record),))
    assert repository.used(record.spec.domain_id) == 2


def test_v2_preserves_existing_header_as_raw(postgres_targets):
    from test.postgres.test_admission_reservations import api, binding

    from cli_agent_orchestrator.clients.admission_schema import RESOURCE_ADMISSION_MIGRATIONS
    from cli_agent_orchestrator.clients.postgres.migrations import migrate_component

    target = postgres_targets.main
    migrate_component(target, RESOURCE_ADMISSION_MIGRATIONS[:1])
    current = binding(target)
    operation = str(uuid4())
    with connect(target) as runtime:
        runtime.execute(
            sql.SQL("""INSERT INTO {} (operation_id,instance_id,process_generation,
                process_pid,process_started_at,binding_sha256,request_sha256,state,revision)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'preparing',1)""").format(
                sql.Identifier(target.schema, "_cao_resource_operations")
            ),
            (
                operation,
                current.instance_id,
                current.process_generation,
                current.process_pid,
                current.process_started_at,
                api().binding_digest(current),
                "a" * 64,
            ),
        )
    migrate_component(target, RESOURCE_ADMISSION_MIGRATIONS)
    with connect(target, readonly=True) as runtime:
        row = runtime.execute(
            sql.SQL(
                "SELECT state,revision,membership_sha256,cost_sha256 FROM {} WHERE operation_id=%s"
            ).format(sql.Identifier(target.schema, "_cao_resource_operations")),
            (operation,),
        ).fetchone()
    assert tuple(row) == ("preparing", 1, None, None)


def test_membership_writer_waits_for_complete_reservation_transaction(shared, monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    target, repository, current, module = shared
    first = domain(shared, 4)
    writer = module.ResourceAdmissionRepository(target, binding_supplier=lambda: current)
    held, continue_reserve, writer_entered = (threading.Event() for _ in range(3))
    backend = []
    original_basis = repository._membership_basis
    original_writer_lock = writer._lock_membership

    def held_basis(connection, selected):
        result = original_basis(connection, selected)
        held.set()
        if not continue_reserve.wait(15):
            raise RuntimeError("test reservation barrier did not retire")
        return result

    def writer_lock(connection, selected):
        backend.append(connection.execute("SELECT pg_backend_pid()").fetchone()[0])
        writer_entered.set()
        original_writer_lock(connection, selected)

    monkeypatch.setattr(repository, "_membership_basis", held_basis)
    monkeypatch.setattr(writer, "_lock_membership", writer_lock)
    with ThreadPoolExecutor(max_workers=2) as executor:
        prepare = executor.submit(repository.reserve_for_cost, str(uuid4()), quantities(module))
        try:
            assert held.wait(10)
            change = executor.submit(
                writer.provision_member,
                first.spec.domain_id,
                current,
                expected_revision=1,
                principal_id="principal-" + "b" * 64,
            )
            assert writer_entered.wait(10)
            deadline = time.monotonic() + 10
            with connect(target, readonly=True) as observer:
                while True:
                    blockers = observer.execute(
                        "SELECT pg_blocking_pids(%s)", (backend[0],)
                    ).fetchone()[0]
                    if blockers:
                        break
                    assert time.monotonic() < deadline, "actual membership writer never waited"
                    continue_reserve.wait(0.01)
            assert not change.done()
        finally:
            continue_reserve.set()
        prepared = prepare.result(timeout=10)
        assert change.result(timeout=10) == 2
    monkeypatch.setattr(repository, "_membership_basis", original_basis)
    with pytest.raises(module.SharedRevisionConflict):
        repository.transition(prepared.operation_id, expected_revision=1, state="active")
    assert repository.used(first.spec.domain_id) == 1


@pytest.mark.parametrize("values", [(True, 0, 1), (-1, 0, 1), (0, 0, 0), (0, 0, 2**63)])
def test_invalid_cost_is_rejected_before_repository(values):
    from cli_agent_orchestrator.models.admission_domains import SharedResourceCost

    with pytest.raises(ValueError):
        SharedResourceCost(*values)


@pytest.mark.parametrize(
    "kind,unit",
    [("host", "bytes"), ("node", "bytes"), ("host", "millicores"), ("node", "millicores")],
)
def test_memory_and_cpu_need_both_host_and_node_budgets(shared, kind, unit):
    _, repository, _, module = shared
    record = add_domain(shared, kind, unit, 100)
    cost = quantities(module, memory=int(unit == "bytes"), cpu=int(unit == "millicores"), storage=0)
    with pytest.raises(module.SharedAdmissionDenied):
        repository.reserve_for_cost(str(uuid4()), cost)
    assert repository.used(record.domain_id) == 0
