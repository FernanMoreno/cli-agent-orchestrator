"""Native PostgreSQL lock races, not physical multi-node acceptance."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from test.postgres.test_admission_generation_seal import authority
from test.postgres.test_admission_metadata_credits import grant
from test.postgres.test_admission_reservations import domain, request, shared
from uuid import uuid4

import pytest

from cli_agent_orchestrator.clients.postgres import connect

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("producer", ["raw_reserve", "first_activation"])
@pytest.mark.parametrize("seal_first", [True, False], ids=["seal-first", "producer-first"])
def test_generation_seal_serializes_with_new_producers(shared, producer, seal_first, monkeypatch):
    target, seal_repository, binding, module = shared
    producer_repository = module.ResourceAdmissionRepository(
        target, binding_supplier=lambda: binding
    )
    storage = domain(shared, capacity=4)
    credit = grant(shared, storage)

    # Both repository instances must enter the race with a verified accounting basis.
    assert seal_repository.audit_usage() == ()
    assert producer_repository.audit_usage() == ()

    operation_id = str(uuid4())
    if producer == "first_activation":
        preparing = producer_repository.reserve_for_cost(
            operation_id,
            module.SharedResourceCost(storage_slots=1),
            require_metadata=True,
        )
        assert (preparing.state, preparing.revision) == ("preparing", 1)

    terminal_authority = authority(shared)

    def produce():
        if producer == "raw_reserve":
            return producer_repository.reserve(operation_id, (request(storage),))
        return producer_repository.transition(operation_id, expected_revision=1, state="active")

    seal_call = lambda: seal_repository.seal_current_generation(terminal_authority)
    if seal_first:
        holder_repository, holder_call = seal_repository, seal_call
        contender_repository, contender_call = producer_repository, produce
    else:
        holder_repository, holder_call = producer_repository, produce
        contender_repository, contender_call = seal_repository, seal_call

    holder_entered = threading.Event()
    contender_entered = threading.Event()
    release_holder = threading.Event()
    holder_pid = []
    contender_pid = []
    original_holder_lock = holder_repository._lock_membership
    original_contender_lock = contender_repository._lock_membership

    def hold_after_membership_lock(connection, selected):
        assert selected == binding
        result = original_holder_lock(connection, selected)
        holder_pid.append(connection.execute("SELECT pg_catalog.pg_backend_pid()").fetchone()[0])
        holder_entered.set()
        if not release_holder.wait(30):
            raise AssertionError("first transaction did not receive its release signal")
        return result

    def observe_before_membership_wait(connection, selected):
        assert selected == binding
        contender_pid.append(connection.execute("SELECT pg_catalog.pg_backend_pid()").fetchone()[0])
        contender_entered.set()
        return original_contender_lock(connection, selected)

    monkeypatch.setattr(holder_repository, "_lock_membership", hold_after_membership_lock)
    monkeypatch.setattr(contender_repository, "_lock_membership", observe_before_membership_wait)

    holder_future = None
    contender_future = None
    with ThreadPoolExecutor(max_workers=2) as executor:
        try:
            holder_future = executor.submit(holder_call)
            assert holder_entered.wait(30), "first transaction never held the membership lock"
            contender_future = executor.submit(contender_call)
            assert contender_entered.wait(
                30
            ), "second transaction never reached the membership lock"

            deadline = time.monotonic() + 30
            with connect(target, readonly=True) as observer:
                while True:
                    blocked_by_holder = observer.execute(
                        """SELECT EXISTS (
                            SELECT 1 FROM pg_catalog.pg_locks AS waiting
                            JOIN pg_catalog.pg_locks AS held
                              ON held.locktype=waiting.locktype
                             AND held.database IS NOT DISTINCT FROM waiting.database
                             AND held.classid IS NOT DISTINCT FROM waiting.classid
                             AND held.objid IS NOT DISTINCT FROM waiting.objid
                             AND held.objsubid IS NOT DISTINCT FROM waiting.objsubid
                            WHERE waiting.pid=%s AND waiting.locktype='advisory'
                              AND NOT waiting.granted AND held.pid=%s AND held.granted
                        )""",
                        (contender_pid[0], holder_pid[0]),
                    ).fetchone()[0]
                    if blocked_by_holder:
                        break
                    assert (
                        time.monotonic() < deadline
                    ), "second transaction did not wait on the holder's advisory lock"
                    # This is polling backoff only; PostgreSQL's lock table is the ordering proof.
                    release_holder.wait(0.01)
            assert not contender_future.done()
        finally:
            release_holder.set()
            for future in (holder_future, contender_future):
                if future is not None:
                    try:
                        future.result(timeout=30)
                    except BaseException:
                        pass

        assert holder_future is not None and contender_future is not None
        if seal_first:
            seal = holder_future.result(timeout=30)
            assert seal.process_generation == binding.process_generation
            with pytest.raises(module.SharedAdmissionDenied):
                contender_future.result(timeout=30)
        else:
            committed = holder_future.result(timeout=30)
            assert committed.operation_id == operation_id
            assert contender_future.result(timeout=30).process_generation == (
                binding.process_generation
            )

    current = seal_repository.read_operation(operation_id)
    if producer == "raw_reserve" and seal_first:
        assert current is None
    else:
        assert current is not None
        if producer == "raw_reserve":
            assert (current.state, current.revision) == ("preparing", 1)
        elif seal_first:
            assert (current.state, current.revision) == ("preparing", 1)
        else:
            assert (current.state, current.revision) == ("active", 2)

        # A producer that committed before the seal, or an existing preparing
        # operation fenced by it, may still be cleaned up by its original owner.
        if current.state == "preparing":
            seal_repository.transition(
                operation_id, expected_revision=current.revision, state="released"
            )
        elif current.state == "active":
            releasing = seal_repository.transition(
                operation_id, expected_revision=current.revision, state="releasing"
            )
            seal_repository.transition(
                operation_id, expected_revision=releasing.revision, state="released"
            )

    assert seal_repository.metadata_credits_current() == (credit,)
    assert seal_repository.used(storage.spec.domain_id) == 1
    assert seal_repository.audit_usage() == ()
