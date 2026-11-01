"""Async ownership carries a joined handoff, never a physical SQL permission."""

import threading
from test.services.test_delivery_admission import create, observe, reserve

import pytest

from cli_agent_orchestrator.clients import storage_admission as module
from cli_agent_orchestrator.services.work_blocking import run_owned_blocking

pytestmark = pytest.mark.offline_database


def started():
    owner = create()
    assert observe(owner, 4)
    lease = reserve(owner, "logical")
    assert lease.start()
    return owner, lease


@pytest.mark.asyncio
async def test_logical_scope_cannot_open_sql_on_async_waiter():
    owner, lease = started()
    try:
        with module.logical_storage_scope(lease, clock=lambda: 10):
            with pytest.raises(module.StorageAdmissionUnavailable):
                module.acquire_storage_connection()
            assert owner.snapshot(now=10).storage_connection_loans == 0
    finally:
        lease.finish_after_storage()


@pytest.mark.asyncio
async def test_logical_scope_only_owned_worker_can_borrow_exact_lease():
    owner, lease = started()
    loop_thread = threading.get_ident()

    def physical():
        assert threading.get_ident() != loop_thread
        assert module._current_storage_owner() is lease
        loan = module.acquire_storage_connection()
        assert loan is not None
        assert owner.snapshot(now=10).storage_connection_loans == 1
        assert loan.finish()
        return "owned"

    try:
        with module.logical_storage_scope(lease, clock=lambda: 10):
            assert await run_owned_blocking(physical) == "owned"
        assert not module.storage_admission_failed()
        assert owner.snapshot(now=10).storage_connection_loans == 0
    finally:
        lease.finish_after_storage()


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_owner", [False, True])
async def test_logical_scope_cannot_be_promoted_in_place_to_physical(foreign_owner):
    owner, lease = started()
    other_owner, other = started() if foreign_owner else (owner, lease)
    try:
        with module.logical_storage_scope(lease, clock=lambda: 10):
            with pytest.raises(module.StorageAdmissionUnavailable):
                with module.physical_storage_scope(other, clock=lambda: 10):
                    pytest.fail("async caller promoted itself to a SQL worker")
            assert module.storage_admission_failed()
        assert owner.snapshot(now=10).storage_connection_loans == 0
        assert other_owner.snapshot(now=10).storage_connection_loans == 0
    finally:
        lease.finish_after_storage()
        if foreign_owner:
            other.finish_after_storage()
