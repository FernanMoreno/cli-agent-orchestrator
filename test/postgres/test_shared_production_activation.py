"""Production factory and real JWT HTTP activate a real PostgreSQL coordinator.

Telemetry/transport are controlled: this is a repository/control composition
scenario, not physical M1/M2/M3 acceptance.
"""

from test.conftest import mint_test_token
from test.postgres.test_admission_metadata_credits import grant
from test.postgres.test_admission_reservations import domain, shared
from test.postgres.test_shared_admission_mapping import add_domain
from test.services.test_delivery_admission import observe
from test.services.test_shared_admission_runtime import Monitor
from types import SimpleNamespace

import httpx
import jwt
import pytest
from fastapi import FastAPI

from cli_agent_orchestrator.api.admission_routes import router
from cli_agent_orchestrator.clients.storage_admission import physical_storage_scope
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services import inbox_admission_runtime as runtime_module
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.inbox_service import InboxService
from cli_agent_orchestrator.services.storage_operation_admission import StorageOperationAdmission
from cli_agent_orchestrator.services.work_admission import WorkAdmission
from cli_agent_orchestrator.services.work_blocking import run_owned_blocking

pytestmark = pytest.mark.postgres


class ControlledMonitor(Monitor):
    def __init__(self, owner, *, storage_control, **kwargs):
        self.events = []
        self.owner = owner
        self._permit = storage_control
        self.in_scope = False
        self.stopped = False


@pytest.mark.asyncio
async def test_factory_http_and_physical_data_obey_enrollment(
    shared, auth_enabled_env, rsa_keys, monkeypatch, tmp_path
):
    target, repository, binding, _ = shared
    storage = await run_owned_blocking(domain, shared, 3)
    for kind in ("host", "node"):
        await run_owned_blocking(add_domain, shared, kind, "bytes", 4096 * 1024**2)
        await run_owned_blocking(add_domain, shared, kind, "millicores", 16000)
    public_key = jwt.PyJWK.from_dict(rsa_keys[1].as_dict()).key
    jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=public_key))
    monkeypatch.setattr(auth.get_jwks_cache(), "get_client", lambda _: jwks)
    monkeypatch.setenv("CAO_ADMISSION_MODE", "shared")
    monkeypatch.setenv("CAO_ADMISSION_POLICY_FILE", str(tmp_path / "private-policy.json"))
    monkeypatch.setattr(runtime_module, "AdmissionMonitor", ControlledMonitor)
    bootstrap = ResourceBootstrap(
        repository=SimpleNamespace(resolve_target=lambda: target),
        binding_supplier=lambda _: binding,
        instance_supplier=lambda: binding.instance_id,
    )
    value = runtime_module.runtime_from_environment(
        InboxService(), bootstrap_factory=lambda: bootstrap
    )
    assert value is not None
    app = FastAPI()
    app.state.inbox_admission_runtime = value
    app.include_router(router)
    interaction_storage = StorageOperationAdmission()
    value.add_storage_participant(interaction_storage, target=target)
    work = WorkAdmission(
        WorkRepository(target), backends={}, resource_admission=value.monitor.owner
    )
    await value.start()
    assert observe(value.monitor.owner, 20)
    owner = value.monitor.owner
    assert interaction_storage._admission is owner
    assert work._resource_admission is owner
    admin = {
        "Authorization": "Bearer "
        + mint_test_token(rsa_keys[0], scopes="cao:admin", subject="operator")
    }
    reader = {
        "Authorization": "Bearer "
        + mint_test_token(rsa_keys[0], scopes="cao:read", subject="operator")
    }
    try:
        assert owner.try_reserve("receiver", now=10).reservation is None
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50102))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            forbidden = await client.post(
                "/admission/shared/activate", json={"version": 1}, headers=reader
            )
            assert forbidden.status_code == 403
            unavailable = await client.post(
                "/admission/shared/activate", json={"version": 1}, headers=admin
            )
            assert unavailable.status_code == 503
            assert not owner.shared_ready
            assert owner.try_reserve("receiver", now=10).reservation is None
            credit = await run_owned_blocking(grant, shared, storage)
            for _ in range(2):
                response = await client.post(
                    "/admission/shared/activate", json={"version": 1}, headers=admin
                )
                assert response.status_code == 200
                assert response.json() == {"version": 1, "activated": True}
                assert response.headers["cache-control"] == "no-store"
            assert owner.shared_ready and value._shared.coverage.credits == (credit,)
        lease = owner.try_reserve("receiver", now=10, storage_slots=1).reservation
        assert lease is not None and lease.start()

        def data():
            try:
                with physical_storage_scope(lease, clock=lambda: 10):
                    assert repository.used(storage.spec.domain_id) == 2
            finally:
                lease.finish_after_storage()

        await run_owned_blocking(data)
    finally:
        await value.stop()
    assert value.retired and not value._shared.worker_alive
    assert owner.snapshot(now=10).inflight == 0
    assert await run_owned_blocking(repository.used, storage.spec.domain_id) == 1
    assert await run_owned_blocking(repository.audit_usage) == ()
