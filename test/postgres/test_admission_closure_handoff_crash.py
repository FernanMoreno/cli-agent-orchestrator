"""Owned process exits around a real coordinator fence; no PID/TTL authority."""

import multiprocessing
import os
import threading
from dataclasses import replace
from test.postgres.test_admission_admin_abort import ACTOR, pair
from test.postgres.test_admission_admin_abort_runtime import custodian, data_owner
from test.postgres.test_admission_closure_handoff import witnessed_actions
from test.postgres.test_admission_reservations import shared  # noqa: F401 - pair dependency
from types import SimpleNamespace
from uuid import uuid4

import httpx
import psutil
import pytest

from cli_agent_orchestrator.clients.admission_repository import ResourceAdmissionRepository
from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.storage_admission import physical_storage_scope
from cli_agent_orchestrator.services.work_blocking import run_owned_blocking

pytestmark = pytest.mark.postgres


def exit_around_coordinator_fence(target, parent_binding, domains, writer, after_fence):
    """Only this test-created process is exited; every data SQL handle is closed."""
    try:
        binding = replace(
            parent_binding,
            instance_id=str(uuid4()),
            process_generation=str(uuid4()),
            process_pid=os.getpid(),
            process_started_at=psutil.Process().create_time(),
        )
        repository = ResourceAdmissionRepository(target, binding_supplier=lambda: binding)
        for domain in domains:
            repository.provision_member(
                domain.domain_id, binding, expected_revision=0, principal_id=ACTOR
            )
        storage = next(domain for domain in domains if domain.kind == "storage")
        credit = repository.grant_metadata_credit(
            str(uuid4()), storage.domain_id, binding, principal_id=ACTOR
        )
        owner, coordinator, _ = data_owner(SimpleNamespace(owner=repository, target=target))
        original = repository.fence_physically_closed

        def exit_here(operation_id, **kwargs):
            if after_fence:
                row = original(operation_id, **kwargs)
            else:
                row = repository.read_operation(operation_id)
            writer.send((credit.credit_id, row, binding))
            writer.close()
            os._exit(0)  # Real coordinator cleanup thread exits its own whole process.

        repository.fence_physically_closed = exit_here
        lease = owner.try_reserve("child-receiver", now=10, storage_slots=1).reservation
        assert lease is not None and lease.start()
        try:
            with physical_storage_scope(lease, clock=lambda: 10):
                with connect(target, readonly=True) as connection:
                    assert connection.execute("SELECT 1").fetchone()[0] == 1
        finally:
            lease.finish_after_storage()
        threading.Event().wait(45)
        raise AssertionError("owned coordinator did not reach physical handoff")
    except BaseException:
        writer.close()
        os._exit(1)


@pytest.mark.asyncio
@pytest.mark.parametrize("after_fence", [False, True])
async def test_real_owner_exit_only_releases_when_durable_physical_fence_exists(
    pair, auth_enabled_env, rsa_keys, monkeypatch, tmp_path, after_fence
):
    value, app, headers = await custodian(pair, rsa_keys, monkeypatch, tmp_path)
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(
        target=exit_around_coordinator_fence,
        args=(pair.target, pair.binding, pair.domains, writer, after_fence),
    )
    process.start()
    writer.close()
    try:
        assert await run_owned_blocking(reader.poll, 30), "owned child did not report its handoff"
        credit_id, row, binding = reader.recv()
        await run_owned_blocking(process.join, 30)
        assert process.exitcode == 0 and binding.process_pid == process.pid
        assert row.state == ("releasing" if after_fence else "active")
        assert row.revision == (3 if after_fence else 2)
        assert await run_owned_blocking(pair.custodian.used, pair.storage.spec.domain_id) == 4
        request = dict(
            version=1,
            action_id=str(uuid4()),
            operation_id=row.operation_id,
            expected_revision=3,
        )
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50106))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            response = await client.post(
                "/admission/shared/release-witnessed", json=request, headers=headers
            )
            if after_fence:
                assert response.status_code == 200 and response.json()["revision"] == 4
                repeated = await client.post(
                    "/admission/shared/release-witnessed", json=request, headers=headers
                )
                assert repeated.status_code == 200 and repeated.json() == response.json()
            else:
                assert response.status_code == 409
                assert response.json() == {"detail": {"code": "resource_closure_unproven"}}
            listing = await client.post(
                "/admission/shared/debt", json={"version": 1}, headers=headers
            )
            assert listing.status_code == 200
            entries = listing.json()["entries"]
            child_credit = next(item for item in entries if item["entry_id"] == credit_id)
            assert child_credit["state"] == "charged" and child_credit["current_owner"] is False
            remaining = [item for item in entries if item["entry_id"] == row.operation_id]
            assert len(remaining) == int(not after_fence)
        assert await run_owned_blocking(pair.custodian.used, pair.storage.spec.domain_id) == (
            3 if after_fence else 4
        )
        assert len(await run_owned_blocking(witnessed_actions, pair)) == int(after_fence)
        assert await run_owned_blocking(pair.custodian.audit_usage) == ()
    finally:
        reader.close()
        if process.is_alive():
            process.terminate()  # Only the exact child created above, never other sessions.
            await run_owned_blocking(process.join, 30)
        await value.stop()
