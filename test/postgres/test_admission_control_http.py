"""Real HTTP/JWKS/PostgreSQL control, without inventing hierarchy authority."""

import os
import socket
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from test.conftest import mint_test_token
from test.fixtures.cao_server import _JWKSServer
from test.postgres.test_admission_bootstrap import resource_target
from uuid import uuid4

import httpx
import psutil
import pytest
import uvicorn
from fastapi import FastAPI
from psycopg import sql

from cli_agent_orchestrator.api.admission_routes import router
from cli_agent_orchestrator.clients.admission_bootstrap_repository import ResourceEnvelopeRepository
from cli_agent_orchestrator.clients.admission_bootstrap_schema import RESOURCE_ENVELOPE_TABLE
from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.models.resource_envelope import EnvelopeClaims
from cli_agent_orchestrator.services.admission_bootstrap import (
    ResourceBootstrap,
    capture_server_binding,
)
from cli_agent_orchestrator.services.admission_monitor import AdmissionMonitor
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.admission_postgres import PostgresPressureSampler
from cli_agent_orchestrator.services.admission_settings import AdmissionPolicyLoader
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
from cli_agent_orchestrator.services.inbox_admission_runtime import InboxAdmissionRuntime
from cli_agent_orchestrator.services.inbox_service import InboxService
from cli_agent_orchestrator.services.local_peer_identity import LocalProcessIdentity

pytestmark = pytest.mark.postgres


def test_real_http_admin_registration_cas_revocation_and_runtime_acl(
    resource_target, auth_enabled_env, rsa_keys, monkeypatch
):
    target = resource_target
    identity = LocalProcessIdentity(
        str(uuid4()),
        str(uuid4()),
        os.getpid(),
        psutil.Process().create_time(),
        "owned-resource-control-test",
        "127.0.0.1",
        0,
        "test-only-public-identity",
    )
    repository = ResourceEnvelopeRepository(target_supplier=lambda: target)
    bootstrap = ResourceBootstrap(
        repository=repository,
        binding_supplier=lambda selected: capture_server_binding(
            selected, identity_supplier=lambda: identity
        ),
        instance_supplier=lambda: identity.instance_id,
    )
    owner = DeliveryAdmission(AdmissionPolicy())
    monitor = AdmissionMonitor(
        owner,
        policy_loader=AdmissionPolicyLoader(source=lambda: {"version": 1, "policy": {}}),
        bootstrap=bootstrap,
        postgres=PostgresPressureSampler(target_supplier=lambda: target),
        scope_id="owned-native-http-control",
        local_storage_capacity=lambda: 4,
    )
    value = InboxAdmissionRuntime(InboxService(), monitor=monitor, bootstrap=bootstrap)

    @asynccontextmanager
    async def lifespan(app):
        await value.start()
        try:
            yield
        finally:
            await value.stop()

    app = FastAPI(lifespan=lifespan)
    app.state.inbox_admission_runtime = value
    app.include_router(router)
    jwks = _JWKSServer(rsa_keys[1])
    listener = socket.socket()
    thread = None
    server = None
    try:
        jwks.start()
        monkeypatch.setenv("CAO_AUTH_JWKS_URI", jwks.url)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        deadline = time.monotonic() + 30
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "owned HTTP server did not start"
        admin = {
            "Authorization": "Bearer " + mint_test_token(rsa_keys[0], subject="native-operator")
        }
        reader = {
            "Authorization": "Bearer "
            + mint_test_token(rsa_keys[0], scopes="cao:read", subject="native-reader")
        }
        # Coverage is deliberately unproven. Registration must not turn it into
        # permission to admit input; neither this test nor /proc proves ancestors.
        claims = EnvelopeClaims(1024**3, 1000, 4, False, False)
        path = "/admission/resource-envelope"
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=30, trust_env=False
        ) as client:
            assert client.get(path).status_code == 401
            assert client.get(path, headers=reader).status_code == 403
            assert client.get(path, headers=admin).status_code == 404
            assert client.get("/admission/diagnostics").status_code == 401
            assert client.get("/admission/diagnostics", headers=reader).status_code == 403
            diagnostic = client.get("/admission/diagnostics", headers=admin)
            assert diagnostic.status_code == 200
            assert diagnostic.json()["scope"] == "process_local"
            assert diagnostic.json()["distributed_observed"] is False
            assert diagnostic.json()["capacity"] is None
            assert diagnostic.headers["cache-control"] == "no-store"
            created = client.post(
                path, headers=admin, json={"expected_revision": 0, "claims": asdict(claims)}
            )
            assert created.status_code == 200 and created.json() == {"revision": 1, "active": True}
            assert created.headers["cache-control"] == "no-store"
            current = client.get(path, headers=admin)
            assert current.status_code == 200
            assert current.json() == {"revision": 1, "active": True, "claims": asdict(claims)}
            replay = client.post(
                path, headers=admin, json={"expected_revision": 0, "claims": asdict(claims)}
            )
            assert replay.status_code == 409
            assert bootstrap.load_current().reason == "hierarchy_unverified"
            assert owner.snapshot(now=time.monotonic()).available_slots == 0
            revoked = client.post(path + "/revoke", headers=admin, json={"expected_revision": 1})
            assert revoked.status_code == 200 and revoked.json() == {"revision": 2, "active": False}
            assert bootstrap.load_current().reason == "authority_revoked"
            assert owner.snapshot(now=time.monotonic()).available_slots == 0
        with connect(target, readonly=True) as runtime:
            assert runtime.read_only
            rows = runtime.execute(
                sql.SQL(
                    "SELECT revision,active,actor_principal_id,process_pid,process_started_at "
                    "FROM {} WHERE instance_id=%s ORDER BY revision"
                ).format(sql.Identifier(RESOURCE_ENVELOPE_TABLE)),
                (identity.instance_id,),
            ).fetchall()
            assert len(rows) == 2
            assert [(row[0], row[1]) for row in rows] == [(1, True), (2, False)]
            assert rows[0][2] == rows[1][2] and rows[0][2].startswith("principal-")
            assert all(
                row[3] == identity.pid and row[4] == identity.process_started_at for row in rows
            )
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=60)
            assert not thread.is_alive(), "owned HTTP worker did not physically retire"
        listener.close()
        jwks.stop()
    assert value.retired and monitor.retired
