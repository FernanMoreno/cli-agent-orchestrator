"""Actual Root/PG behind operator ASGI; parent compute remains a declared fixture."""

import asyncio
import base64
import json
import os
import sys
import threading
from pathlib import Path
from test.conftest import mint_test_token
from test.postgres import fixtures as postgres_fixtures
from test.postgres.test_admission_metadata_credits import ADMIN
from test.postgres.test_admission_reservations import shared
from test.postgres.test_root_control_deployment import (
    NATIVE_ROOT_CLI,
    provision_from_operator_worker,
)
from test.postgres.test_sql_root_genesis import funded, marked
from test.services.test_admission_command_reservation import _observation, _policy
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import psutil
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI

import cli_agent_orchestrator
from cli_agent_orchestrator.clients.admission_bootstrap_repository import ResourceEnvelopeRepository
from cli_agent_orchestrator.clients.admission_bootstrap_schema import RESOURCE_ENVELOPE_MIGRATIONS
from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
from cli_agent_orchestrator.clients.sql_intent_repository import SqlIntentRepository
from cli_agent_orchestrator.clients.sql_opening_schema import SQL_OPENING_MIGRATIONS
from cli_agent_orchestrator.models.resource_envelope import EnvelopeClaims
from cli_agent_orchestrator.models.root_deployment_control import RootDeploymentStatus
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.admission_policy import ResourceAmounts
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

pytestmark = pytest.mark.postgres


def test_external_envelope_writer_privileges_preflight(shared, monkeypatch):
    """Check the actual reader/owner roles without starting a Root process."""
    target, _, binding, _ = shared
    with marked(monkeypatch):
        migrate_component(target, RESOURCE_ENVELOPE_MIGRATIONS)
        operator, permit = DeliveryAdmission.with_storage_control(_policy())
        with permit.operation(clock=lambda: 1.0):
            record = ResourceEnvelopeRepository(target_supplier=lambda: target).append(
                target,
                binding=binding,
                claims=EnvelopeClaims(4 * 1024**3, 16000, 64, True, False),
                expected_revision=0,
                principal_id=ADMIN,
            )
        assert record.binding == binding and record.active and record.revision == 1
        assert operator.snapshot(now=1.0).storage_connection_loans == 0


@pytest.mark.asyncio
async def test_http_operator_launch_provision_activate_refresh_and_close(
    shared, auth_enabled_env, rsa_keys, monkeypatch, tmp_path
):
    from cli_agent_orchestrator.api import root_deployment_routes as routes
    from cli_agent_orchestrator.services import root_deployment_runtime as module

    repository = Path(cli_agent_orchestrator.__file__).parents[2]
    assert Path.cwd() == repository == Path(postgres_fixtures.__file__).parents[2]
    assert psutil.virtual_memory().available >= 6144 * 1024**2
    target, admission, issuer_binding, _ = shared
    key = Ed25519PrivateKey.generate()
    trust = tmp_path / "root-http-trust.json"
    trust.write_text(
        json.dumps(
            {
                "version": 1,
                "keys": {
                    "test-root-http": base64.b64encode(
                        key.public_key().public_bytes(
                            serialization.Encoding.Raw, serialization.PublicFormat.Raw
                        )
                    ).decode("ascii")
                },
            }
        )
    )
    policy = tmp_path / "root-http-policy.json"
    policy.write_text(json.dumps({"version": 1, "policy": {}}))
    trust.chmod(0o600)
    policy.chmod(0o600)
    environment = dict(os.environ)
    for name in (
        "CAO_INTERNAL_RESOURCE_BINDING_FD",
        "CAO_INTERNAL_SQL_STARTUP_PERMISSION",
        "CAO_INTERNAL_SQL_BROKER_FD",
        "CAO_INTERNAL_ROOT_CONTROL_FD",
        "AUTH0_DOMAIN",
        "AUTH0_AUDIENCE",
        "CAO_AUTH_JWKS_URI",
        "CAO_AUTH_AUDIENCE",
        "CAO_AUTH_ISSUER",
    ):
        environment.pop(name, None)
    environment.update(
        CAO_INTERNAL_ROOT_SQL_GENESIS="1",
        ROOT_CONTROL_REPOSITORY=str(repository),
        PYTHONPATH=os.pathsep.join((str(repository / "src"), str(repository))),
    )
    argv = (
        sys.executable,
        "-c",
        NATIVE_ROOT_CLI,
        "--trust-file",
        str(trust),
        "--policy-file",
        str(policy),
        "--scope",
        "root/http",
        "--close-timeout",
        "5",
    )
    public = jwt.PyJWK.from_dict(rsa_keys[1].as_dict()).key
    jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=public))
    monkeypatch.setattr(auth.get_jwks_cache(), "get_client", lambda _: jwks)
    token = mint_test_token(rsa_keys[0], scopes="cao:admin", subject="root-http-operator")
    principal = auth.principal_from_token(token)
    headers = {"Authorization": "Bearer " + token}
    ledger, permit = DeliveryAdmission.with_storage_control(_policy())
    assert (
        ledger.reconfigure(
            _policy(),
            expected_generation=ledger.generation,
            budget=ResourceAmounts(64 * 1024**2, 4000, 8),
        )
        is not None
    )
    assert ledger.observe(
        _observation(at=1.0, memory=64 * 1024**2, cpu=4000, storage=8),
        now=1.0,
        generation=ledger.generation,
    )
    footprint = ResourceAmounts(1024**2, 10, 0)
    issuer = ResourceBootstrap(
        repository=ResourceEnvelopeRepository(target_supplier=lambda: target),
        binding_supplier=lambda _: issuer_binding,
        instance_supplier=lambda: issuer_binding.instance_id,
    )
    connections = []
    native_connect = module.connect

    def observe_connect(selected, *args, **kwargs):
        connections.append(
            (
                threading.current_thread(),
                selected,
                ledger._guard._is_owned(),
                ledger.snapshot(now=1.0).command_started,
            )
        )
        return native_connect(selected, *args, **kwargs)

    monkeypatch.setattr(module, "connect", observe_connect)
    runtime = module.RootDeploymentRuntime(
        module.RootDeploymentConfig(
            bootstrap=issuer,
            owner=ledger,
            control=permit,
            target=target,
            argv=argv,
            env=environment,
            key_supplier=lambda: ("test-root-http", key),
            worker_footprint=footprint,
            child_footprint=footprint,
            command_footprint=footprint,
            startup_footprint=footprint,
            refresh_footprint=footprint,
            clock=lambda: 1.0,
            timeout_s=30,
        )
    )
    app = FastAPI()
    app.include_router(routes.router)
    routes.configure_root_deployment(app, runtime)
    records = ()
    with marked(monkeypatch):
        migrate_component(target, SQL_OPENING_MIGRATIONS)
        migrate_component(target, RESOURCE_ENVELOPE_MIGRATIONS)
        primary = None
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50203)),
                base_url="http://127.0.0.1",
                headers=headers,
            ) as client:

                async def request(method, path, **kwargs):
                    # ASGITransport has no socket timeout. A test deadline must
                    # still allow the owned actor to finish physical cleanup.
                    return await asyncio.wait_for(client.request(method, path, **kwargs), 90)

                no_scope = mint_test_token(rsa_keys[0], scopes="cao:read", subject="reader")
                denied_scope = await request(
                    "POST",
                    "/admission/root/launch",
                    headers={"Authorization": "Bearer " + no_scope},
                    json={"version": 1},
                )
                assert denied_scope.status_code == 403 and not connections
                expired = mint_test_token(
                    rsa_keys[0], exp_offset=-1, iat_offset=-600, subject="expired"
                )
                denied_expiry = await request(
                    "POST",
                    "/admission/root/launch",
                    headers={"Authorization": "Bearer " + expired},
                    json={"version": 1},
                )
                assert denied_expiry.status_code == 401 and not connections
                launched = await request("POST", "/admission/root/launch", json={"version": 1})
                assert len(connections) == 1
                assert connections[0][2] is False
                assert connections[0][3] >= 1
                assert launched.status_code == 200, launched.text
                state = RootDeploymentStatus.model_validate_json(launched.content)
                assert state.phase == "awaiting_authority" and state.subject is not None
                assert runtime._deployment._activation_attempted is False
                bound = state.subject.binding
                assert bound.process_pid != os.getpid()
                assert len(connections) == 1
                assert connections[0][0] is not threading.current_thread()
                other_token = mint_test_token(
                    rsa_keys[0], scopes="cao:admin", subject="another-root-http-operator"
                )
                denied = await request(
                    "GET", "/admission/root", headers={"Authorization": "Bearer " + other_token}
                )
                assert denied.status_code == 403
                denied_activation = await request(
                    "POST",
                    "/admission/root/activate",
                    headers={"Authorization": "Bearer " + other_token},
                    json={
                        "version": 1,
                        **{
                            name: str(uuid4()) for name in ("journal_id", "intent_id", "session_id")
                        },
                    },
                )
                assert denied_activation.status_code == 403
                assert len(connections) == 1
                # External operator provisioning is explicit and separately funded.
                records, credits = provision_from_operator_worker(
                    ledger, footprint, target, issuer_binding, bound
                )
                journal, intent, session = (str(uuid4()) for _ in range(3))
                with funded(target.migration_target()) as observer:
                    assert (
                        observer.execute(
                            "SELECT count(*) FROM pg_stat_activity WHERE application_name=%s",
                            (state.subject.sql_application_name,),
                        ).fetchone()[0]
                        == 0
                    )
                operator, operator_permit = DeliveryAdmission.with_storage_control(_policy())
                with operator_permit.operation(clock=lambda: 1.0):
                    ResourceEnvelopeRepository(target_supplier=lambda: target).append(
                        target,
                        binding=bound,
                        claims=EnvelopeClaims(4 * 1024**3, 16000, 64, True, False),
                        expected_revision=0,
                        principal_id=principal.id,
                    )
                    with native_connect(target.migration_target()) as channel:
                        SqlIntentRepository(
                            target, binding_supplier=lambda: issuer_binding
                        ).provision(
                            channel,
                            journal_id=journal,
                            credit_id=credits[0].credit_id,
                            max_records=3,
                            principal_id=ADMIN,
                            participant_binding=bound,
                        )
                activated = await request(
                    "POST",
                    "/admission/root/activate",
                    json={
                        "version": 1,
                        "journal_id": journal,
                        "intent_id": intent,
                        "session_id": session,
                    },
                )
                assert activated.status_code == 200, activated.text
                assert activated.json()["phase"] == "ready"
                assert runtime._deployment._activation_attempted is True
                assert activated.json()["intent_id"] == intent
                assert len(connections) == 1
                with funded(target.migration_target()) as observer:
                    assert (
                        observer.execute(
                            "SELECT count(*) FROM pg_stat_activity WHERE application_name=%s",
                            (state.subject.sql_application_name,),
                        ).fetchone()[0]
                        == 1
                    )
                    record = SqlIntentRepository(
                        target, binding_supplier=lambda: issuer_binding
                    ).read_root_opening(observer, journal_id=journal, intent_id=intent, owner=True)
                    assert record.issuer_principal_id == principal.id
                refreshed = await request("POST", "/admission/root/refresh", json={"version": 1})
                assert refreshed.status_code == 200, refreshed.text
                assert refreshed.json()["phase"] == "ready"
                assert refreshed.json()["reason"]
                stopped = await request("POST", "/admission/root/stop", json={"version": 1})
                assert stopped.status_code == 200, stopped.text
                assert len(connections) == 1
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                await routes.close_root_deployment(app)
            except BaseException:
                if primary is None:
                    raise
                primary.add_note(
                    "Root operator cleanup was unconfirmed; retain its owner and charges. "
                    "The first failing boundary remains the primary exception."
                )
        assert ledger.snapshot(now=1.0).committed == ResourceAmounts()
        assert ledger.snapshot(now=1.0).storage_connection_loans == 0
        with funded(target.migration_target()) as channel:
            assert all(admission._used(channel, record.spec.domain_id) == 1 for record in records)
