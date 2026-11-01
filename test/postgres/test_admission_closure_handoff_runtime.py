"""Funded HTTP recovery races exact coordinator cleanup on real PostgreSQL."""

import asyncio
import threading
from test.postgres.test_admission_admin_abort import pair
from test.postgres.test_admission_admin_abort_runtime import custodian, data_owner
from test.postgres.test_admission_closure_handoff import witnessed_actions
from test.postgres.test_admission_reservations import shared  # noqa: F401 - pair dependency
from uuid import uuid4

import httpx
import pytest

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.storage_admission import physical_storage_scope
from cli_agent_orchestrator.services.work_blocking import run_owned_blocking

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
@pytest.mark.parametrize("custodian_first", [True, False])
async def test_foreign_http_custodian_and_original_coordinator_decrement_once(
    pair, auth_enabled_env, rsa_keys, monkeypatch, tmp_path, custodian_first
):
    value, app, headers = await custodian(pair, rsa_keys, monkeypatch, tmp_path)
    owner, coordinator, retired = await run_owned_blocking(data_owner, pair)
    lease = owner.try_reserve("native-receiver", now=10, storage_slots=1).reservation
    assert lease is not None and lease.start()
    witnessed, continue_cleanup = threading.Event(), threading.Event()
    original = pair.owner.fence_physically_closed
    receipts = []

    def paused(*args, **kwargs):
        receipt = original(*args, **kwargs)  # Transaction and physical SQL credit closed.
        receipts.append(receipt)
        witnessed.set()
        assert continue_cleanup.wait(30)
        return receipt

    monkeypatch.setattr(pair.owner, "fence_physically_closed", paused)

    def body():
        try:
            with physical_storage_scope(lease, clock=lambda: 10):
                with connect(pair.target, readonly=True) as connection:
                    assert connection.execute("SELECT 1").fetchone()[0] == 1
        finally:
            lease.finish_after_storage()

    task = asyncio.create_task(run_owned_blocking(body))
    try:
        await task
        assert await run_owned_blocking(witnessed.wait, 30)
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt.state == "releasing" and receipt.revision == 3
        assert await run_owned_blocking(pair.custodian.used, pair.storage.spec.domain_id) == 3
        snapshot = owner.snapshot(now=10)
        assert snapshot.inflight == 2  # Retained data hold plus the paused control call.
        assert snapshot.control_inflight == 1 and snapshot.control_connection_loans == 0
        assert snapshot.storage_only_inflight == 1
        if not custodian_first:
            continue_cleanup.set()
            await run_owned_blocking(coordinator.drain, 20)
        action_id = str(uuid4())
        request = dict(
            version=1,
            action_id=action_id,
            operation_id=receipt.operation_id,
            expected_revision=3,
        )
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50105))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            response = await client.post(
                "/admission/shared/release-witnessed", json=request, headers=headers
            )
            assert response.headers["cache-control"] == "no-store"
            if custodian_first:
                assert response.status_code == 200
                assert response.json() == dict(
                    version=1,
                    action_id=action_id,
                    operation_id=receipt.operation_id,
                    revision=4,
                    state="released",
                )
                repeated = await client.post(
                    "/admission/shared/release-witnessed", json=request, headers=headers
                )
                assert repeated.status_code == 200 and repeated.json() == response.json()
            else:
                assert response.status_code == 409
                assert response.json() == {"detail": {"code": "resource_operation_conflict"}}
        continue_cleanup.set()
        await run_owned_blocking(coordinator.drain, 20)
        assert owner.snapshot(now=10).inflight == 0
        assert await run_owned_blocking(pair.custodian.used, pair.storage.spec.domain_id) == 2
        assert len(await run_owned_blocking(witnessed_actions, pair)) == int(custodian_first)
        assert await run_owned_blocking(pair.custodian.audit_usage) == ()
        assert await run_owned_blocking(pair.owner.metadata_credits_current) == (
            pair.foreign_credit,
        )
    finally:
        continue_cleanup.set()
        await asyncio.gather(task, return_exceptions=True)
        owner.stop_accepting()
        await run_owned_blocking(coordinator.drain, 20)
        retired[0] = True
        await value.stop()
