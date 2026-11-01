"""Two paid logical nodes and real PostgreSQL/HTTP; not distributed M2 acceptance."""

import asyncio
import threading
from dataclasses import replace
from test.conftest import mint_test_token
from test.postgres.test_admission_admin_abort import actions, pair
from test.postgres.test_admission_reservations import shared  # noqa: F401 - pair fixture dependency
from test.postgres.test_shared_production_activation import ControlledMonitor
from test.services.test_delivery_admission import observe
from test.services.test_shared_admission_runtime import principal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest

from cli_agent_orchestrator.api.admission_routes import router
from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.storage_admission import (
    StorageAdmissionUnavailable,
    physical_storage_scope,
)
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services import inbox_admission_runtime as module
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
from cli_agent_orchestrator.services.inbox_service import InboxService
from cli_agent_orchestrator.services.shared_delivery_admission import authorize_shared_delivery
from cli_agent_orchestrator.services.work_blocking import run_owned_blocking

pytestmark = pytest.mark.postgres


async def custodian(pair, rsa_keys, monkeypatch, tmp_path):
    from fastapi import FastAPI

    public_key = jwt.PyJWK.from_dict(rsa_keys[1].as_dict()).key
    jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=public_key))
    monkeypatch.setattr(auth.get_jwks_cache(), "get_client", lambda _: jwks)
    monkeypatch.setenv("CAO_ADMISSION_MODE", "shared")
    monkeypatch.setenv("CAO_ADMISSION_POLICY_FILE", str(tmp_path / "private-policy.json"))
    monkeypatch.setattr(module, "AdmissionMonitor", ControlledMonitor)
    bootstrap = ResourceBootstrap(
        repository=SimpleNamespace(resolve_target=lambda: pair.target),
        binding_supplier=lambda _: pair.binding,
        instance_supplier=lambda: pair.binding.instance_id,
    )
    value = module.runtime_from_environment(InboxService(), bootstrap_factory=lambda: bootstrap)
    assert value is not None
    await value.start()
    assert observe(value.monitor.owner, 16)
    await value.activate_shared(principal=principal())
    app = FastAPI()
    app.state.inbox_admission_runtime = value
    app.include_router(router)
    headers = {
        "Authorization": "Bearer "
        + mint_test_token(rsa_keys[0], scopes="cao:admin", subject="operator")
    }
    return value, app, headers


def data_owner(pair):
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    owner.require_shared_admission()
    assert observe(owner, 16)
    retired = [False]
    with permit.operation(clock=lambda: 10):
        coordinator = authorize_shared_delivery(
            principal=principal(),
            repository=pair.owner,
            owner=owner,
            control=permit,
            retirement=lambda: retired[0],
            clock=lambda: 10,
        )
        owner.bind_shared_admission(coordinator)
    return owner, coordinator, retired


@pytest.mark.asyncio
@pytest.mark.parametrize("active_first", [False, True])
async def test_abort_and_activation_have_one_cas_winner_and_no_unsafe_effect(
    pair, auth_enabled_env, rsa_keys, monkeypatch, tmp_path, active_first
):
    value, app, headers = await custodian(pair, rsa_keys, monkeypatch, tmp_path)
    owner, coordinator, retired = await run_owned_blocking(data_owner, pair)
    lease = owner.try_reserve("native-receiver", now=10, storage_slots=1).reservation
    assert lease is not None and lease.start()
    prepared, continue_activation = threading.Event(), threading.Event()
    body_entered, finish_body = threading.Event(), threading.Event()
    operation_ids = []
    effects = []
    original = pair.owner.reserve_for_cost

    def paused(*args, **kwargs):
        receipt = original(*args, **kwargs)
        operation_ids.append(receipt.operation_id)
        prepared.set()
        assert continue_activation.wait(30)
        return receipt

    monkeypatch.setattr(pair.owner, "reserve_for_cost", paused)
    if active_first:
        continue_activation.set()

    def run_data():
        try:
            with physical_storage_scope(lease, clock=lambda: 10):
                with connect(pair.target, readonly=True) as connection:
                    assert connection.execute("SELECT 1").fetchone()[0] == 1
                    effects.append("data")
                    body_entered.set()
                    assert finish_body.wait(30)
            return True
        except StorageAdmissionUnavailable:
            return False
        finally:
            lease.finish_after_storage()

    task = asyncio.create_task(run_owned_blocking(run_data))
    try:
        assert await run_owned_blocking(prepared.wait, 30)
        if active_first:
            assert await run_owned_blocking(body_entered.wait, 30)
        action_id = str(uuid4())
        request = {
            "version": 1,
            "action_id": action_id,
            "operation_id": operation_ids[0],
            "expected_revision": 1,
        }
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50103))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            listing = await client.post(
                "/admission/shared/debt", json={"version": 1}, headers=headers
            )
            assert listing.status_code == 200
            target = next(
                entry
                for entry in listing.json()["entries"]
                if entry["kind"] == "operation" and entry["entry_id"] == operation_ids[0]
            )
            assert target["state"] == ("active" if active_first else "preparing")
            assert target["current_owner"] is False
            assert set(target) == {
                "kind",
                "entry_id",
                "instance_id",
                "process_generation",
                "revision",
                "state",
                "current_owner",
            }
            response = await client.post(
                "/admission/shared/abort-preparing", json=request, headers=headers
            )
            if active_first:
                assert response.status_code == 409
                assert response.json() == {"detail": {"code": "resource_operation_conflict"}}
                assert (
                    await run_owned_blocking(pair.custodian.used, pair.storage.spec.domain_id) == 3
                )
                assert await run_owned_blocking(actions, pair) == []
            else:
                assert response.status_code == 200
                assert response.json() == {
                    "version": 1,
                    "action_id": action_id,
                    "operation_id": operation_ids[0],
                    "revision": 2,
                    "state": "released",
                }
                repeated = await client.post(
                    "/admission/shared/abort-preparing", json=request, headers=headers
                )
                assert repeated.status_code == 200 and repeated.json() == response.json()
                assert (
                    await run_owned_blocking(pair.custodian.used, pair.storage.spec.domain_id) == 2
                )
        continue_activation.set()
        finish_body.set()
        assert await task is active_first
        assert effects == (["data"] if active_first else [])
        owner.stop_accepting()
        await run_owned_blocking(coordinator.drain, 20)
        assert owner.snapshot(now=10).inflight == 0
        assert await run_owned_blocking(pair.custodian.used, pair.storage.spec.domain_id) == 2
        assert await run_owned_blocking(pair.custodian.audit_usage) == ()
        rows = await run_owned_blocking(actions, pair)
        assert len(rows) == (0 if active_first else 1)
    finally:
        continue_activation.set()
        finish_body.set()
        await asyncio.gather(task, return_exceptions=True)
        owner.stop_accepting()
        await run_owned_blocking(coordinator.drain, 20)
        retired[0] = True
        await value.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["debt", "abort-preparing"])
@pytest.mark.parametrize("mismatch", ["view", "repository"])
async def test_recovery_rejects_unfunded_binding_before_any_sql(
    pair, auth_enabled_env, rsa_keys, monkeypatch, tmp_path, endpoint, mismatch
):
    value, app, headers = await custodian(pair, rsa_keys, monkeypatch, tmp_path)
    try:
        with monkeypatch.context() as patch:
            if mismatch == "view":
                repository = value._recovery_repository
                patch.setattr(
                    repository,
                    "_binding_supplier",
                    lambda: replace(pair.binding, resource_view_sha256="e" * 64),
                )
            else:
                patch.setattr(value, "_recovery_repository", pair.owner)

            def forbidden(*args, **kwargs):
                pytest.fail("unfunded recovery binding reached PostgreSQL")

            patch.setattr(pair.module, "connect", forbidden)
            request = {"version": 1}
            if endpoint == "abort-preparing":
                request.update(
                    action_id=str(uuid4()), operation_id=str(uuid4()), expected_revision=1
                )
            transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50104))
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1"
            ) as client:
                response = await client.post(
                    "/admission/shared/" + endpoint, json=request, headers=headers
                )
            assert response.status_code == 503
    finally:
        await value.stop()
