"""Bridge registration is real JWT/HTTP/PG control, never hierarchy proof."""

import importlib
import time
from dataclasses import asdict
from test.conftest import mint_test_token
from test.fixtures.cao_server import _JWKSServer
from test.postgres.test_admission_bootstrap import resource_target
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from cli_agent_orchestrator.clients.admission_bootstrap_repository import ResourceEnvelopeRepository
from cli_agent_orchestrator.models.resource_envelope import EnvelopeClaims
from cli_agent_orchestrator.runtime_channel.bridge import Bridge
from cli_agent_orchestrator.services import admission_bootstrap
from cli_agent_orchestrator.services.admission_monitor import AdmissionMonitor
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.admission_postgres import PostgresPressureSampler
from cli_agent_orchestrator.services.admission_settings import AdmissionPolicyLoader
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
from cli_agent_orchestrator.services.inbox_admission_runtime import InboxAdmissionRuntime

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_bridge_real_http_jwt_registration_keeps_incomplete_coverage_closed(
    resource_target, auth_enabled_env, rsa_keys, monkeypatch
):
    participant_api = importlib.import_module("cli_agent_orchestrator.runtime_channel.admission")
    server_api = importlib.import_module("cli_agent_orchestrator.api.admission_server")
    monkeypatch.setattr(admission_bootstrap, "get_persistent_instance_id", lambda: profile)
    profile = str(uuid4())
    bootstrap = admission_bootstrap.scoped_process_bootstrap(
        "bridge-resource/native-http",
        repository=ResourceEnvelopeRepository(target_supplier=lambda: resource_target),
    )
    owner = DeliveryAdmission(AdmissionPolicy())
    bridge = Bridge(
        "ws://isolated", "native-http", "channel-token-is-not-an-admin", store=SimpleNamespace()
    )
    port = participant_api.BridgeAdmissionParticipant(bridge)
    monitor = AdmissionMonitor(
        owner,
        policy_loader=AdmissionPolicyLoader(source=lambda: {"version": 1, "policy": {}}),
        bootstrap=bootstrap,
        postgres=PostgresPressureSampler(target_supplier=lambda: resource_target),
        scope_id="owned-bridge-http",
        local_storage_capacity=lambda: 4,
    )
    value = InboxAdmissionRuntime(port, monitor=monitor, bootstrap=bootstrap)
    server = server_api.AdmissionControlServer(value)
    jwks = _JWKSServer(rsa_keys[1])
    try:
        jwks.start()
        monkeypatch.setenv("CAO_AUTH_JWKS_URI", jwks.url)
        await value.start()
        await server.start()
        assert bridge._resource_admission is owner
        assert bridge._resource_clock is value.clock
        admin = {"Authorization": "Bearer " + mint_test_token(rsa_keys[0], subject="bridge-admin")}
        reader = {
            "Authorization": "Bearer "
            + mint_test_token(rsa_keys[0], scopes="cao:read", subject="bridge-reader")
        }
        claims = EnvelopeClaims(1024**3, 1000, 4, False, False)
        path = "/admission/resource-envelope"
        async with httpx.AsyncClient(base_url=server.url, trust_env=False) as client:
            assert (await client.get(path)).status_code == 401
            assert (await client.get(path, headers=reader)).status_code == 403
            assert (
                await client.get(path, headers={"Authorization": "Bearer " + bridge._token})
            ).status_code == 401
            assert (await client.get(path, headers=admin)).status_code == 404
            response = await client.post(
                path, headers=admin, json={"expected_revision": 0, "claims": asdict(claims)}
            )
            assert response.status_code == 200 and response.json() == {
                "revision": 1,
                "active": True,
            }
            assert response.headers["cache-control"] == "no-store"
            assert (await client.get(path, headers=admin)).json()["claims"] == asdict(claims)
            assert (
                await client.post(
                    path, headers=admin, json={"expected_revision": 0, "claims": asdict(claims)}
                )
            ).status_code == 409
            assert bootstrap.load_current().reason == "hierarchy_unverified"
            assert owner.snapshot(now=time.monotonic()).available_slots == 0
            response = await client.post(
                path + "/revoke", headers=admin, json={"expected_revision": 1}
            )
            assert response.status_code == 200 and response.json() == {
                "revision": 2,
                "active": False,
            }
            assert bootstrap.load_current().reason == "authority_revoked"
            assert owner.snapshot(now=time.monotonic()).available_slots == 0
    finally:
        value.close_admission()
        await server.stop()
        await value.stop()
        jwks.stop()
    assert value.retired and server.task.done()
