"""Read-only durable discovery; no recovery, death inference or charge release."""

import multiprocessing
import os
from contextlib import contextmanager
from dataclasses import replace
from test.postgres.test_admission_metadata_credits import grant
from test.postgres.test_admission_reservations import api, domain, request, shared
from uuid import UUID, uuid4

import psutil
import pytest

pytestmark = pytest.mark.postgres
ACTOR = "principal-" + "b" * 64


def model():
    from cli_agent_orchestrator.models import admission_domains

    return admission_domains


def catalog(repository, *, limit=2):
    entries = []
    cursor = None
    for _ in range(20):
        page = repository.outstanding_resources(limit=limit, after=cursor)
        assert len(page.entries) <= limit
        entries.extend(page.entries)
        cursor = page.next_cursor
        if cursor is None:
            return entries
    pytest.fail("keyset scan did not finish")


def test_keyset_pages_keep_all_live_charges_and_exclude_released_history(shared):
    _, repository, binding, _ = shared
    storage = domain(shared, 30)
    credit = grant(shared, storage)
    live = set()
    for _ in range(5):
        operation = repository.reserve(str(uuid4()), (request(storage),))
        repository.transition(operation.operation_id, expected_revision=1, state="active")
        live.add(operation.operation_id)
    for _ in range(4):
        released = repository.reserve(str(uuid4()), (request(storage),))
        repository.transition(released.operation_id, expected_revision=1, state="released")
    before = repository.used(storage.spec.domain_id)
    for limit in (1, 2, 20):
        entries = catalog(repository, limit=limit)
        assert len(entries) == 6 and len({(x.kind, x.entry_id) for x in entries}) == 6
        assert entries[0].kind == "metadata_credit" and entries[0].entry_id == credit.credit_id
        assert {x.entry_id for x in entries if x.kind == "operation"} == live
        assert all(x.current_owner is True for x in entries)
        assert all(x.instance_id == binding.instance_id for x in entries)
        assert all(x.state == "active" for x in entries if x.kind == "operation")
    assert repository.used(storage.spec.domain_id) == before == 6
    assert repository.audit_usage() == ()


@pytest.mark.parametrize("limit", [True, 0, -1, 1001, 2.5])
def test_page_limits_rejected_before_sql(shared, monkeypatch, limit):
    _, repository, _, module = shared

    def forbidden(*args, **kwargs):
        pytest.fail("invalid page limit reached SQL")

    monkeypatch.setattr(module, "connect", forbidden)
    with pytest.raises(ValueError):
        repository.outstanding_resources(limit=limit)


def test_unsupported_cursor_rejected_before_sql(shared, monkeypatch):
    _, repository, _, module = shared

    def forbidden(*args, **kwargs):
        pytest.fail("invalid cursor reached SQL")

    monkeypatch.setattr(module, "connect", forbidden)
    with pytest.raises(ValueError):
        repository.outstanding_resources(after={"kind": "operation", "entry_id": str(uuid4())})


def test_withdrawal_does_not_hide_metadata_or_active_debt(shared):
    _, repository, binding, _ = shared
    storage = domain(shared, 4)
    credit = grant(shared, storage)
    operation = repository.reserve(str(uuid4()), (request(storage),))
    repository.transition(operation.operation_id, expected_revision=1, state="active")
    repository.provision_member(
        storage.spec.domain_id, binding, expected_revision=1, principal_id=ACTOR, active=False
    )
    entries = catalog(repository)
    assert {(x.kind, x.entry_id) for x in entries} == {
        ("metadata_credit", credit.credit_id),
        ("operation", operation.operation_id),
    }
    assert repository.used(storage.spec.domain_id) == 2
    assert repository.audit_usage() == ()


def test_changed_private_binding_cannot_label_a_stale_page_current(shared, monkeypatch):
    _, repository, binding, module = shared
    storage = domain(shared, 4)
    grant(shared, storage)
    original = module.connect

    @contextmanager
    def changed(*args, **kwargs):
        with original(*args, **kwargs) as connection:
            yield connection
        repository._binding_supplier = lambda: replace(binding, resource_view_sha256="e" * 64)

    monkeypatch.setattr(module, "connect", changed)
    with pytest.raises(module.SharedAdmissionDenied):
        repository.outstanding_resources()


def orphaned_charge(target, parent_binding, storage, writer):
    binding = replace(
        parent_binding,
        instance_id=str(uuid4()),
        process_generation=str(uuid4()),
        process_pid=os.getpid(),
        process_started_at=psutil.Process().create_time(),
    )
    repository = api().ResourceAdmissionRepository(target, binding_supplier=lambda: binding)
    repository.provision_member(
        storage.spec.domain_id, binding, expected_revision=0, principal_id=ACTOR
    )
    credit = repository.grant_metadata_credit(
        str(uuid4()), storage.spec.domain_id, binding, principal_id=ACTOR
    )
    operation = repository.reserve(str(uuid4()), (request(storage),))
    repository.transition(operation.operation_id, expected_revision=1, state="active")
    writer.send((credit.credit_id, operation.operation_id))
    writer.close()
    os._exit(0)  # Own test child dies after committing; no release/TTL callback runs.


def test_crashed_process_is_discoverable_without_releasing_its_charges(shared):
    target, repository, binding, _ = shared
    storage = domain(shared, 5)
    own_credit = grant(shared, storage)
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=orphaned_charge, args=(target, binding, storage, writer))
    process.start()
    writer.close()
    try:
        assert reader.poll(30), "owned child did not commit its inventory"
        foreign_ids = set(reader.recv())
        process.join(30)
        assert process.exitcode == 0
        entries = catalog(repository, limit=1)
        foreign = [x for x in entries if x.entry_id in foreign_ids]
        assert len(foreign) == 2 and all(x.current_owner is False for x in foreign)
        assert {x.state for x in foreign} == {"charged", "active"}
        assert next(x for x in entries if x.entry_id == own_credit.credit_id).current_owner
        assert repository.used(storage.spec.domain_id) == 3
        assert repository.audit_usage() == ()
    finally:
        reader.close()
        if process.is_alive():
            process.terminate()
            process.join(30)


def test_discovery_cursor_does_not_claim_a_cross_page_snapshot(shared):
    _, repository, _, _ = shared
    storage = domain(shared, 8)
    operations = []
    for number in (10, 30, 40):
        operation = repository.reserve(str(UUID(int=number)), (request(storage),))
        repository.transition(operation.operation_id, expected_revision=1, state="active")
        operations.append(operation.operation_id)
    first = repository.outstanding_resources(limit=1)
    assert [x.entry_id for x in first.entries] == operations[:1]
    repository.transition(operations[1], expected_revision=2, state="uncertain")
    repository.transition(operations[2], expected_revision=2, state="releasing")
    repository.transition(operations[2], expected_revision=3, state="released")
    earlier = repository.reserve(str(UUID(int=5)), (request(storage),))
    rest = repository.outstanding_resources(after=first.next_cursor, limit=20)
    assert [(x.entry_id, x.state) for x in rest.entries] == [(operations[1], "uncertain")]
    assert rest.next_cursor is None
    fresh = catalog(repository, limit=1)
    assert {x.entry_id for x in fresh} == {earlier.operation_id, *operations[:2]}
    assert repository.used(storage.spec.domain_id) == 3


def test_catalog_query_is_one_readonly_page_without_history_or_release(shared, monkeypatch):
    from dataclasses import asdict

    _, repository, _, _ = shared
    storage = domain(shared, 3)
    grant(shared, storage)
    calls = []
    original = repository._query

    def observed(connection, statement, parameters=()):
        assert connection.execute("SHOW transaction_read_only").fetchone()[0] == "on"
        calls.append(statement)
        return original(connection, statement, parameters)

    with monkeypatch.context() as patch:
        patch.setattr(repository, "_query", observed)
        page = repository.outstanding_resources(limit=1)
    assert len(calls) == 1
    query = calls[0].upper()
    assert "UNION ALL" in query and "LIMIT" in query
    assert not any(
        word in query
        for word in ["OFFSET", "_HISTORY", "UPDATE", "DELETE", "INSERT", "FOR UPDATE", "ADVISORY"]
    )
    assert set(asdict(page.entries[0])) == {
        "kind",
        "entry_id",
        "instance_id",
        "process_generation",
        "revision",
        "state",
        "current_owner",
    }
    assert repository.used(storage.spec.domain_id) == 1


def test_known_accounting_divergence_remains_inspectable_without_repair(shared):
    from psycopg import sql

    from cli_agent_orchestrator.clients.postgres import connect

    target, repository, _, _ = shared
    storage = domain(shared, 8)
    credit = grant(shared, storage)
    with connect(target.migration_target()) as owner, owner.transaction():
        owner.execute(
            sql.SQL("UPDATE {} SET committed=7 WHERE domain_id=%s").format(
                sql.Identifier(target.schema, "_cao_resource_usage")
            ),
            (storage.spec.domain_id,),
        )
    mismatch = repository.audit_usage()
    assert len(mismatch) == 1 and mismatch[0].committed == 7 and mismatch[0].allocated == 1
    page = repository.outstanding_resources()
    assert [(entry.kind, entry.entry_id) for entry in page.entries] == [
        ("metadata_credit", credit.credit_id)
    ]
    assert repository.used(storage.spec.domain_id) == 7
    assert repository.audit_usage() == mismatch


def test_zero_uuid_headers_and_released_cursor_do_not_skip_the_next_row(shared):
    _, repository, binding, _ = shared
    storage = domain(shared, 4)
    zero = str(UUID(int=0))
    credit = repository.grant_metadata_credit(
        zero, storage.spec.domain_id, binding, principal_id=ACTOR
    )
    cursor_operation = repository.reserve(zero, (request(storage),))
    remaining = repository.reserve(str(UUID(int=1)), (request(storage),))
    first = repository.outstanding_resources(limit=2)
    assert [(x.kind, x.entry_id) for x in first.entries] == [
        ("metadata_credit", credit.credit_id),
        ("operation", cursor_operation.operation_id),
    ]
    assert first.next_cursor is not None and first.next_cursor.entry_id == zero
    repository.transition(cursor_operation.operation_id, expected_revision=1, state="released")
    rest = repository.outstanding_resources(after=first.next_cursor, limit=1)
    assert [(x.kind, x.entry_id) for x in rest.entries] == [("operation", remaining.operation_id)]
    assert rest.next_cursor is None
    assert [(x.kind, x.entry_id) for x in catalog(repository, limit=1)] == [
        ("metadata_credit", zero),
        ("operation", remaining.operation_id),
    ]
    assert repository.used(storage.spec.domain_id) == 2
    assert repository.audit_usage() == ()
