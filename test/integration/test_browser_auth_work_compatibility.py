"""Browser transport preserves preexisting provision, grants and authenticated receipts.

The runtime delivery observation is supplied by a test backend. This does not
claim Docker/ELF worker execution or compatibility with a live deployment.
"""

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from starlette.requests import Request

from cli_agent_orchestrator.models.work_contract import ContractSnapshot
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    ResolvedSnapshot,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
from cli_agent_orchestrator.services.work_service import DeliveryObservation, WorkService
from test.services.test_work_lineage_origin import (
    _issue_receiver_receipt,
    _managed_context,
    _managed_handoff,
)


def test_browser_identity_preserves_preexisting_work_provision_grants_and_receipt(
    tmp_path, monkeypatch
):
    from cli_agent_orchestrator.services.browser_auth import BrowserAuthService

    # These real services register a root owner, delegated child/receiver grants,
    # immutable snapshots and a managed delivery, before any browser login.
    context = _managed_context(tmp_path)
    repository, actor, job = context.repository, context.owner, context.job
    authority = WorkAuthority(repository)
    origins = context.origin_module.WorkOrigins(repository)
    child = context.admission.admit_managed_lineage(
        _managed_handoff(context, origins=origins, key="preexisting-browser-compatibility")
    )
    attempt = child["attempts"][0]
    sent = WorkService(repository).dispatch(
        child["id"],
        lambda: DeliveryObservation(),
        admission=TransitionEvidence(
            generation=attempt["generation"],
            expected_generation=attempt["generation"],
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        ),
        actor_id=actor.id,
    )
    assert sent["attempts"][0]["state"] == "sent"
    receipt = _issue_receiver_receipt(
        context, origins, child, attempt, nonce="preexisting-browser-authenticated-receipt"
    )
    receiver = WorkService(repository, origins=origins)
    acknowledged = receiver.record_task_received(receipt)
    assert acknowledged["attempts"][0]["state"] == "acknowledged"

    # Provision the existing operator with a real private launch selection,
    # using the same root grant and an independently frozen launch contract.
    snapshot = DelegationSnapshots(
        repository, policy=KnowledgePolicy(repository, job["id"], job["grant_id"], 1)
    ).freeze(
        principal=actor,
        job_id=job["id"],
        contract_id="browser-preexisting-launch",
        binding_key="browser-preexisting-launch",
        request_hash="c" * 64,
        scope="project",
        scope_id=job["project_id"],
        resolver=lambda _connection, _principal: ResolvedSnapshot("existing launch snapshot"),
    )
    launch_contract = context.child_contract.model_copy(
        update={
            "id": "browser-preexisting-launch",
            "operation_kind": "launch",
            "snapshot": ContractSnapshot(
                state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
            ),
        }
    )
    provisioning = WorkProvisioning(repository)
    provisioning.provision_launch(
        actor,
        subject=actor,
        selector="existing-selection",
        expected_revision=0,
        job_id=job["id"],
        grant_id=job["grant_id"],
        grant_revision=1,
        contract=launch_contract,
        adapter_version=1,
        lease_seconds=300,
    )
    provision_before = provisioning.resolve_launch(actor, "existing-selection")

    # Verify a real RS256 operator JWT through the production claims validator.
    # Only JWKS transport is supplied in-process; signature/issuer/audience/expiry
    # validation and Principal construction execute the production implementation.
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(
        {
            "iss": actor.issuer,
            "sub": actor.subject,
            "aud": "browser-work-compatibility",
            "scope": " ".join(actor.scopes),
            "exp": int(time.time()) + 300,
        },
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        algorithm="RS256",
    )
    monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
    monkeypatch.setenv("CAO_AUTH_JWKS_URI", "http://127.0.0.1/fixture-jwks")
    monkeypatch.setenv("CAO_AUTH_ISSUER", actor.issuer)
    monkeypatch.setenv("CAO_AUTH_AUDIENCE", "browser-work-compatibility")
    monkeypatch.setattr(
        auth.get_jwks_cache(),
        "get_client",
        lambda _uri: SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=key.public_key())
        ),
    )
    jwt_actor = auth.principal_from_token(token)
    assert jwt_actor == actor

    tables = (
        "work_principals",
        "work_jobs",
        "work_grants",
        "work_launch_provisions",
        "work_task_received_receipts",
        "work_transition_receipts",
        "work_results",
        "work_attempts",
        "work_items",
        "work_delivery_orders",
        "work_dispatch_bindings",
    )

    def durable_state():
        with repository.read_snapshot() as connection:
            return {
                table: tuple(
                    tuple(row)
                    for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
                )
                for table in tables
            }

    durable_before = durable_state()
    assert len(durable_before["work_task_received_receipts"]) == 1
    assert len(durable_before["work_transition_receipts"]) >= 2
    assert len(durable_before["work_launch_provisions"]) == 1
    assert len(durable_before["work_grants"]) == 3

    binding = {field: getattr(jwt_actor, field) for field in ("id", "issuer", "subject", "kind")}
    binding["scopes"] = sorted(jwt_actor.scopes)
    service = BrowserAuthService(tmp_path / "browser.sqlite3", binding)
    service.create_account("operator", "Fixture-password-484!")
    secret, _ = service.login("operator", "Fixture-password-484!", remember=True, peer="127.0.0.1")
    origin = "http://127.0.0.1:9999"
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/work-items",
            "server": ("127.0.0.1", 9999),
            "client": ("127.0.0.1", 1234),
            "headers": [
                (b"host", b"127.0.0.1:9999"),
                (b"origin", origin.encode()),
                (b"cookie", ("cao_browser_test=" + secret).encode()),
            ],
            "app": SimpleNamespace(
                state=SimpleNamespace(
                    browser_auth=service,
                    browser_auth_config={
                        "enabled": True,
                        "installation_id": "test",
                        "canonical_origin": origin,
                    },
                )
            ),
        }
    )
    browser_actor = auth.browser_principal(request)
    assert auth.is_verified_principal(browser_actor)
    assert browser_actor == jwt_actor
    assert provisioning.resolve_launch(browser_actor, "existing-selection") == provision_before
    assert durable_state() == durable_before

    arguments = dict(
        job_id=job["id"],
        grant_id=job["grant_id"],
        expected_grant_revision=1,
        provider="mock_cli",
        requested_permissions=Permissions(),
    )
    assert authority.authorize(browser_actor, **arguments) == authority.authorize(
        jwt_actor, **arguments
    )
    for _ in range(4):
        service.renew(secret)
        assert auth.browser_principal(request) == jwt_actor
        assert (
            provisioning.resolve_launch(auth.browser_principal(request), "existing-selection")
            == provision_before
        )
        assert receiver.record_task_received(receipt)["attempts"][0]["state"] == "acknowledged"
        assert durable_state() == durable_before

    service.logout_all(secret)
    with pytest.raises(HTTPException) as rejected:
        auth.browser_principal(request)
    assert rejected.value.status_code == 401
    assert (
        provisioning.resolve_launch(auth.principal_from_token(token), "existing-selection")
        == provision_before
    )
    assert authority.authorize(auth.principal_from_token(token), **arguments).id == job["grant_id"]
    assert receiver.record_task_received(receipt)["attempts"][0]["state"] == "acknowledged"
    assert repository.get_work(child["id"])["attempts"][0]["state"] == "acknowledged"
    assert durable_state() == durable_before
