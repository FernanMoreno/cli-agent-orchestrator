"""Real SQLite pairing lifecycle: approval, identity pinning and revocation."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import asdict, replace
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cli_agent_orchestrator.api import local_coordination_routes as routes
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.services import local_peer_auth as auth
from cli_agent_orchestrator.services import local_peer_identity as identity
from cli_agent_orchestrator.services import local_peer_registry as registry
from cli_agent_orchestrator.services import local_peer_service as service


def response(payload, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    result.url = "http://127.0.0.1:19890/local-coordination"
    return result


@pytest.fixture
def peers(monkeypatch, tmp_path):
    home = tmp_path / "identity"
    monkeypatch.setattr(identity, "CAO_HOME_DIR", home)
    monkeypatch.setattr(identity, "LOCAL_PEER_INSTANCE_ID_FILE", home / "instance-id")
    monkeypatch.setattr(identity, "LOCAL_PEER_PRIVATE_KEY_FILE", home / "private-key")
    monkeypatch.setattr(identity, "SERVER_HOST", "127.0.0.1")
    monkeypatch.setattr(identity, "SERVER_PORT", 19889)
    monkeypatch.setattr(identity, "ALLOWED_HOSTS", ["127.0.0.1"])
    monkeypatch.setattr(registry, "LOCAL_PEER_DIR", tmp_path / "registry")
    monkeypatch.setattr(
        registry, "LOCAL_PEER_REGISTRY_FILE", tmp_path / "registry" / "peers.sqlite3"
    )
    identity.current_process_identity.cache_clear()
    identity.peer_private_key.cache_clear()
    current = identity.current_process_identity()
    private = Ed25519PrivateKey.generate()
    peer = replace(
        current,
        instance_id=str(uuid4()),
        process_generation=str(uuid4()),
        loopback_port=19890,
        display_name="other profile",
        public_key=identity.public_key_text(private),
    )
    registry.register_instance(current)
    registry.register_instance(peer)
    project = tmp_path / "project"
    project.mkdir()
    binding = service.project_binding(project)
    context = SimpleNamespace(
        current=current,
        peer=peer,
        private=private,
        project=project,
        binding=binding,
        calls=[],
        invitation=None,
    )

    def peer_http(method, url, **kwargs):
        context.calls.append((method, url, kwargs))
        path = urlsplit(url).path
        if path == "/local-coordination/identity":
            return response(
                asdict(
                    context.current
                    if urlsplit(url).port == context.current.loopback_port
                    else context.peer
                )
            )
        if path == "/local-coordination/projects/verify":
            return response({**binding, "matches": True})
        if path == "/local-coordination/pairings":
            context.invitation = kwargs["json"]
            return response(
                {
                    "challenge_id": context.invitation["challenge_id"],
                    "project_id": binding["project_id"],
                }
            )
        if path.endswith("/accept"):
            return response({"accepted": True, "project_id": binding["project_id"]})
        if path == "/local-coordination/sessions":
            return response(
                {
                    "project_id": binding["project_id"],
                    "state": "empty",
                    "sessions": [],
                    "disappeared_count": 0,
                }
            )
        if method == "DELETE":
            return response({"revoked": True})
        raise AssertionError((method, url, kwargs))

    monkeypatch.setattr(service, "_loopback_request", peer_http)
    context.http = peer_http
    yield context
    identity.current_process_identity.cache_clear()
    identity.peer_private_key.cache_clear()


def candidate_payload(peers, **updates):
    payload = {
        "challenge_id": str(uuid4()),
        "code": auth.new_pairing_code(),
        "expires_at": time.time() + 300,
        "current_instance_id": peers.current.instance_id,
        "initiator_instance_id": peers.peer.instance_id,
        "initiator_process_generation": peers.peer.process_generation,
        "initiator_display_name": peers.peer.display_name,
        "initiator_public_key": peers.peer.public_key,
        "initiator_loopback_port": peers.peer.loopback_port,
        "candidate_instance_id": peers.current.instance_id,
        "candidate_process_generation": peers.current.process_generation,
        "candidate_display_name": peers.current.display_name,
        "candidate_public_key": peers.current.public_key,
        **peers.binding,
        "requested_scopes": ["session:read", "peer:revoke"],
    }
    payload.update(updates)
    return payload


def approve_candidate(peers):
    payload = candidate_payload(peers)
    service.receive_pairing_invitation(payload)
    service.accept_pairing(payload["challenge_id"], payload["code"])
    return payload


def initiator(peers):
    receipt = service.initiate_pairing(
        peers.peer.instance_id, str(peers.project), scopes={"session:read", "peer:revoke"}
    )
    arguments = {
        "challenge_id": receipt["challenge_id"],
        "code": receipt["code"],
        "candidate_instance_id": peers.peer.instance_id,
        "candidate_process_generation": peers.peer.process_generation,
        "candidate_display_name": peers.peer.display_name,
        "candidate_public_key": peers.peer.public_key,
        "project_id": peers.binding["project_id"],
    }
    return receipt, arguments


def grant(peers, include_revoked=False):
    return auth.get_peer_grant(
        peer_instance_id=peers.peer.instance_id,
        project_id=peers.binding["project_id"],
        include_revoked=include_revoked,
    )


def test_initiator_requires_approval_pins_key_and_cannot_replay_after_revocation(peers):
    receipt, arguments = initiator(peers)
    assert grant(peers) is None
    with database.SessionLocal() as session:
        challenge = session.get(database.LocalPeerChallengeModel, receipt["challenge_id"])
        assert challenge.code_hash == auth.hash_secret(receipt["code"])
        assert challenge.consumed_at is None
        assert session.get(
            database.LocalPeerProjectModel, peers.binding["project_id"]
        ).canonical_root == str(peers.project)
    auth.accept_at_initiator(**arguments)
    pinned = grant(peers)
    assert pinned.peer_public_key == peers.peer.public_key
    assert json.loads(pinned.scopes_json) == ["peer:revoke", "session:read"]
    auth.accept_at_initiator(**arguments)  # Safe retry preserves the same grant.
    assert grant(peers).grant_id == pinned.grant_id
    assert auth.revoke_peer_grant(
        peer_instance_id=peers.peer.instance_id, project_id=peers.binding["project_id"]
    )
    with pytest.raises(auth.PairingMismatchError, match="already used"):
        auth.accept_at_initiator(**arguments)
    assert grant(peers) is None
    assert auth.list_peer_grants(project_id=peers.binding["project_id"]) == []
    assert auth.list_peer_grants(include_revoked=True)[0]["revoked_at"] is not None


@pytest.mark.parametrize(
    "changed",
    [
        "code",
        "candidate_instance_id",
        "candidate_process_generation",
        "candidate_public_key",
        "project_id",
        "expired",
        "missing",
    ],
)
def test_initiator_denies_changed_or_expired_approval_without_grant(peers, changed):
    receipt, arguments = initiator(peers)
    if changed == "expired":
        with database.SessionLocal() as session:
            session.get(database.LocalPeerChallengeModel, receipt["challenge_id"]).expires_at = (
                time.time() - 1
            )
            session.commit()
    elif changed == "missing":
        arguments["challenge_id"] = str(uuid4())
    elif changed == "candidate_public_key":
        arguments[changed] = identity.public_key_text(Ed25519PrivateKey.generate())
    else:
        arguments[changed] = "different"
    with pytest.raises(auth.LocalPeerAuthError):
        auth.accept_at_initiator(**arguments)
    assert grant(peers) is None


def test_candidate_challenge_is_idempotent_but_approval_is_not_reusable_after_revoke(peers):
    payload = candidate_payload(peers)
    first = service.receive_pairing_invitation(payload)
    assert service.receive_pairing_invitation(payload) == first
    assert grant(peers) is None
    assert "code" not in first and "code_hash" not in first
    assert (
        auth.get_candidate_challenge(payload["challenge_id"], payload["code"])["consumed_at"]
        is None
    )
    result = service.accept_pairing(payload["challenge_id"], payload["code"])
    assert result["peer_instance_id"] == peers.peer.instance_id
    assert result["scopes"] == ["peer:revoke", "session:read"]
    auth.finish_candidate_pairing(payload["challenge_id"])
    with pytest.raises(auth.PairingMismatchError, match="already used"):
        auth.get_candidate_challenge(payload["challenge_id"], payload["code"])
    auth.revoke_peer_grant(
        peer_instance_id=peers.peer.instance_id, project_id=peers.binding["project_id"]
    )
    with pytest.raises(auth.PairingMismatchError, match="already used"):
        auth.finish_candidate_pairing(payload["challenge_id"])
    fresh = approve_candidate(peers)
    assert fresh["challenge_id"] != payload["challenge_id"]
    assert grant(peers).revoked_at is None


@pytest.mark.parametrize(
    "change",
    [
        {"requested_scopes": ["admin:anything"]},
        {"requested_scopes": []},
        {"code": "short"},
        {"initiator_public_key": "abcde"},
        {"candidate_public_key": "short"},
        {"candidate_instance_id": str(uuid4())},
        {"expires_at": 1},
    ],
)
def test_malformed_candidate_offer_never_authorizes(peers, change):
    payload = candidate_payload(peers, **change)
    with pytest.raises(auth.LocalPeerAuthError):
        auth.receive_candidate_challenge(payload)
    assert grant(peers) is None
    with database.SessionLocal() as session:
        assert session.get(database.LocalPeerChallengeModel, payload["challenge_id"]) is None


@pytest.mark.parametrize(
    "field", ["code", "initiator_process_generation", "project_id", "requested_scopes"]
)
def test_candidate_offer_collision_does_not_replace_original_authority(peers, field):
    payload = candidate_payload(peers)
    original = auth.receive_candidate_challenge(payload)
    changed = dict(payload)
    changed[field] = ["task:submit"] if field == "requested_scopes" else "c" * 32
    with pytest.raises(auth.PairingMismatchError, match="conflicts"):
        auth.receive_candidate_challenge(changed)
    assert auth.get_candidate_challenge(payload["challenge_id"], payload["code"]) == original
    assert grant(peers) is None


@pytest.mark.parametrize("operation", ["get", "finish"])
def test_expired_candidate_cannot_be_approved(peers, operation):
    payload = candidate_payload(peers)
    auth.receive_candidate_challenge(payload)
    with database.SessionLocal() as session:
        session.get(database.LocalPeerChallengeModel, payload["challenge_id"]).expires_at = (
            time.time() - 1
        )
        session.commit()
    with pytest.raises(auth.PairingExpiredError):
        if operation == "get":
            auth.get_candidate_challenge(payload["challenge_id"], payload["code"])
        else:
            auth.finish_candidate_pairing(payload["challenge_id"])
    assert grant(peers) is None


@pytest.mark.parametrize(
    "stage", ["identity", "project", "invitation", "wrong_project", "wrong_receipt"]
)
def test_initiation_transport_uncertainty_never_creates_a_grant(peers, monkeypatch, stage):
    def transport(method, url, **kwargs):
        path = urlsplit(url).path
        if stage == "identity" and path.endswith("/identity"):
            raise requests.ConnectionError("offline")
        if stage == "project" and path.endswith("/projects/verify"):
            return response({}, 503)
        if stage == "wrong_project" and path.endswith("/projects/verify"):
            return response({"matches": False})
        if stage == "invitation" and method == "POST":
            raise requests.Timeout("uncertain delivery")
        if stage == "wrong_receipt" and method == "POST":
            return response({"challenge_id": "other", "project_id": peers.binding["project_id"]})
        return peers.http(method, url, **kwargs)

    monkeypatch.setattr(service, "_loopback_request", transport)
    with pytest.raises((service.LocalPeerUnavailable, auth.PairingMismatchError)):
        service.initiate_pairing(peers.peer.instance_id, str(peers.project))
    assert grant(peers) is None


@pytest.mark.parametrize(
    "change", ["candidate_generation", "initiator_key", "candidate_key", "project"]
)
def test_received_offer_is_bound_to_live_profiles_and_project(peers, change):
    payload = candidate_payload(peers)
    fields = {
        "candidate_generation": "candidate_process_generation",
        "initiator_key": "initiator_public_key",
        "candidate_key": "candidate_public_key",
        "project": "project_id",
    }
    payload[fields[change]] = "changed"
    with pytest.raises(auth.PairingMismatchError):
        service.receive_pairing_invitation(payload)
    assert grant(peers) is None


@pytest.mark.parametrize(
    "receipt", [{"accepted": False}, {"accepted": True, "project_id": "other"}]
)
def test_remote_acceptance_must_match_before_candidate_grants(peers, monkeypatch, receipt):
    payload = candidate_payload(peers)
    service.receive_pairing_invitation(payload)

    def transport(method, url, **kwargs):
        return response(receipt) if url.endswith("/accept") else peers.http(method, url, **kwargs)

    monkeypatch.setattr(service, "_loopback_request", transport)
    with pytest.raises(auth.PairingMismatchError, match="invalid receipt"):
        service.accept_pairing(payload["challenge_id"], payload["code"])
    assert grant(peers) is None
    assert (
        auth.get_candidate_challenge(payload["challenge_id"], payload["code"])["consumed_at"]
        is None
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"project_id": "other"},
        {"state": "corrupt", "sessions": []},
        {"state": "empty", "sessions": {}},
    ],
)
def test_peer_snapshot_rejects_wrong_project_and_malformed_state(peers, monkeypatch, payload):
    approve_candidate(peers)

    def transport(method, url, **kwargs):
        if urlsplit(url).path == "/local-coordination/sessions":
            return response({"project_id": peers.binding["project_id"], **payload})
        return peers.http(method, url, **kwargs)

    monkeypatch.setattr(service, "_loopback_request", transport)
    result = service.sessions_from_peer(peers.peer.instance_id, peers.binding["project_id"])
    assert result["state"] == "unavailable" and result["sessions"] == []


def test_peer_snapshots_and_revocation_keep_authority_failures_distinct_from_offline(
    peers, monkeypatch
):
    with pytest.raises(auth.LocalPeerAuthError, match="not paired"):
        service.sessions_from_peer(peers.peer.instance_id, peers.binding["project_id"])
    approve_candidate(peers)
    assert (
        service.sessions_from_peer(peers.peer.instance_id, peers.binding["project_id"])["state"]
        == "empty"
    )
    listed = service.list_local_peers(project_id=peers.binding["project_id"])
    assert listed[0]["online"] and listed[0]["identity_matches_grant"]

    def transport(method, url, **kwargs):
        if method == "DELETE":
            assert grant(peers, include_revoked=True).revoked_at is not None
            raise requests.Timeout("remote acknowledgement unknown")
        return peers.http(method, url, **kwargs)

    monkeypatch.setattr(service, "_loopback_request", transport)
    result = service.revoke_peer(peers.peer.instance_id, peers.binding["project_id"])
    assert result == {"revoked": True, "local_revoked": True, "remote_revoked": False}
    with pytest.raises(auth.LocalPeerAuthError, match="revoked"):
        service.sessions_from_peer(peers.peer.instance_id, peers.binding["project_id"])
    assert not auth.revoke_peer_grant(
        peer_instance_id=peers.peer.instance_id, project_id=peers.binding["project_id"]
    )
    assert service.revoke_peer(str(uuid4()), peers.binding["project_id"]) == {
        "revoked": False,
        "remote_revoked": False,
    }


@pytest.mark.parametrize("state", ["rotated_key", "offline"])
def test_peer_listing_never_reports_unpinned_or_unreachable_profile_online(
    peers, monkeypatch, state
):
    approve_candidate(peers)
    if state == "rotated_key":
        peers.peer = replace(
            peers.peer, public_key=identity.public_key_text(Ed25519PrivateKey.generate())
        )
    else:
        monkeypatch.setattr(
            service,
            "_loopback_request",
            lambda *args, **kwargs: (_ for _ in ()).throw(requests.ConnectionError("offline")),
        )
    listed = service.list_local_peers(project_id=peers.binding["project_id"])
    assert listed[0]["online"] is False
    assert listed[0]["identity_matches_grant"] is False
    assert listed[0]["loopback_url"] is None
    if state == "rotated_key":
        with pytest.raises(auth.LocalPeerAuthError, match="signing identity changed"):
            service.sessions_from_peer(peers.peer.instance_id, peers.binding["project_id"])
    else:
        assert (
            service.sessions_from_peer(peers.peer.instance_id, peers.binding["project_id"])["state"]
            == "unavailable"
        )


def test_project_paths_are_relative_and_cannot_follow_symlinks_outside_authority(peers, tmp_path):
    child = peers.project / "child"
    child.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (peers.project / "escape").symlink_to(outside, target_is_directory=True)
    assert service.resolve_project_path(str(peers.project), None) == str(peers.project)
    assert service.resolve_project_path(str(peers.project), "child") == str(child)
    for value in (str(outside), "../outside", "escape"):
        with pytest.raises(service.LocalPeerError):
            service.resolve_project_path(str(peers.project), value)


def test_http_bootstrap_and_signed_session_route_enforce_real_peer_grants(peers, monkeypatch):
    application = FastAPI()
    application.include_router(routes.router)
    from cli_agent_orchestrator.services import session_service

    monkeypatch.setattr(
        session_service, "get_backend", lambda: SimpleNamespace(list_sessions=lambda: [])
    )
    approve_candidate(peers)
    path = "/local-coordination/sessions"
    query = "project_id=" + peers.binding["project_id"]
    timestamp, nonce = str(int(time.time())), uuid4().hex
    digest = hashlib.sha256(b"").hexdigest()
    signature = peers.private.sign(
        identity._signature_material(
            "GET",
            path,
            query,
            peers.binding["project_id"],
            peers.peer.instance_id,
            peers.peer.process_generation,
            timestamp,
            nonce,
            digest,
        )
    )
    headers = {
        "X-CAO-Peer-Instance": peers.peer.instance_id,
        "X-CAO-Peer-Generation": peers.peer.process_generation,
        "X-CAO-Peer-Timestamp": timestamp,
        "X-CAO-Peer-Nonce": nonce,
        "X-CAO-Peer-Body-SHA256": digest,
        "X-CAO-Peer-Signature": base64.urlsafe_b64encode(signature).decode().rstrip("="),
    }
    with TestClient(
        application, base_url="http://127.0.0.1:19889", client=("127.0.0.1", 50000)
    ) as client:
        assert (
            client.get("/local-coordination/identity").json()["instance_id"]
            == peers.current.instance_id
        )
        assert (
            client.get("/local-coordination/instances").json()[0]["instance_id"]
            == peers.peer.instance_id
        )
        verified = client.get(
            "/local-coordination/projects/verify",
            params={
                "project_path": str(peers.project),
                "expected_project_id": peers.binding["project_id"],
            },
        )
        assert verified.json()["matches"] is True
        assert (
            client.get(
                "/local-coordination/projects/verify",
                params={
                    "project_path": "/nonexistent-scope-test-project",
                    "expected_project_id": peers.binding["project_id"],
                },
            ).status_code
            == 400
        )
        result = client.get(path + "?" + query, headers=headers)
        assert result.status_code == 200 and result.json()["state"] == "empty"
        assert (
            client.get(path + "?" + query, headers=headers).status_code == 403
        )  # Actual nonce replay.


@pytest.mark.parametrize(
    "case,expected",
    [("valid", 201), ("expired", 410), ("generation", 409), ("project", 409), ("actions", 409)],
)
def test_http_pairing_offers_validate_live_identity_before_storage(peers, case, expected):
    payload = candidate_payload(peers)
    payload.pop("current_instance_id")
    if case == "expired":
        payload["expires_at"] = 1
    elif case == "generation":
        payload["initiator_process_generation"] = str(uuid4())
    elif case == "project":
        payload["project_id"] = "b" * 64
    elif case == "actions":
        payload["requested_scopes"] = ["admin:all"]
    application = FastAPI()
    application.include_router(routes.router)
    with TestClient(
        application, base_url="http://127.0.0.1:19889", client=("127.0.0.1", 50000)
    ) as client:
        result = client.post("/local-coordination/pairings", json=payload)
    assert result.status_code == expected, result.text
    assert grant(peers) is None
    with database.SessionLocal() as session:
        stored = session.get(database.LocalPeerChallengeModel, payload["challenge_id"])
        assert (stored is not None) == (case == "valid")
        if stored is not None:
            assert stored.consumed_at is None


@pytest.mark.parametrize(
    "case,expected",
    [("valid", 200), ("expired", 410), ("code", 409), ("generation", 409), ("display_name", 409)],
)
def test_http_acceptance_is_bound_to_challenge_and_current_candidate(peers, case, expected):
    receipt, arguments = initiator(peers)
    arguments.pop("challenge_id")
    if case == "expired":
        with database.SessionLocal() as session:
            session.get(database.LocalPeerChallengeModel, receipt["challenge_id"]).expires_at = 1
            session.commit()
    elif case == "code":
        arguments["code"] = "c" * 32
    elif case == "generation":
        arguments["candidate_process_generation"] = str(uuid4())
    elif case == "display_name":
        arguments["candidate_display_name"] = "impersonated profile"
    application = FastAPI()
    application.include_router(routes.router)
    with TestClient(
        application, base_url="http://127.0.0.1:19889", client=("127.0.0.1", 50000)
    ) as client:
        result = client.post(
            f"/local-coordination/pairings/{receipt['challenge_id']}/accept", json=arguments
        )
    assert result.status_code == expected, result.text
    assert (grant(peers) is not None) == (case == "valid")


def peer_headers(peers, method, path, query):
    timestamp, nonce, digest = str(int(time.time())), uuid4().hex, hashlib.sha256(b"").hexdigest()
    signature = peers.private.sign(
        identity._signature_material(
            method,
            path,
            query,
            peers.binding["project_id"],
            peers.peer.instance_id,
            peers.peer.process_generation,
            timestamp,
            nonce,
            digest,
        )
    )
    return {
        "X-CAO-Peer-Instance": peers.peer.instance_id,
        "X-CAO-Peer-Generation": peers.peer.process_generation,
        "X-CAO-Peer-Timestamp": timestamp,
        "X-CAO-Peer-Nonce": nonce,
        "X-CAO-Peer-Body-SHA256": digest,
        "X-CAO-Peer-Signature": base64.urlsafe_b64encode(signature).decode().rstrip("="),
    }


def test_signed_http_revocation_blocks_subsequent_session_read(peers):
    approve_candidate(peers)
    application = FastAPI()
    application.include_router(routes.router)
    query = "project_id=" + peers.binding["project_id"]
    path = "/local-coordination/peers/" + peers.peer.instance_id
    with TestClient(
        application, base_url="http://127.0.0.1:19889", client=("127.0.0.1", 50000)
    ) as client:
        result = client.delete(
            path + "?" + query, headers=peer_headers(peers, "DELETE", path, query)
        )
        assert result.status_code == 200 and result.json()["revoked"] is True
        session_path = "/local-coordination/sessions"
        denied = client.get(
            session_path + "?" + query, headers=peer_headers(peers, "GET", session_path, query)
        )
        assert denied.status_code == 403 and denied.json()["detail"]["kind"] == "scope_denied"
    assert grant(peers) is None


def test_signed_http_task_status_is_scoped_to_peer_project_and_requester(peers):
    payload = candidate_payload(peers, requested_scopes=["task:status"])
    service.receive_pairing_invitation(payload)
    service.accept_pairing(payload["challenge_id"], payload["code"])
    task_id = str(uuid4())
    with database.SessionLocal() as session:
        session.add(
            database.LocalPeerTaskModel(
                task_id=task_id,
                source_instance_id=peers.peer.instance_id,
                target_instance_id=peers.current.instance_id,
                requester_terminal_id="abcdef01",
                project_id=peers.binding["project_id"],
                operation_key=uuid4().hex,
                request_hash="a" * 64,
                use_worktree=False,
                state="accepted",
            )
        )
        session.commit()
    application = FastAPI()
    application.include_router(routes.router)
    path = "/local-coordination/tasks/" + task_id
    query = "project_id=" + peers.binding["project_id"] + "&requester_terminal_id=abcdef01"
    with TestClient(
        application, base_url="http://127.0.0.1:19889", client=("127.0.0.1", 50000)
    ) as client:
        result = client.get(path + "?" + query, headers=peer_headers(peers, "GET", path, query))
        assert result.status_code == 200 and result.json()["task_id"] == task_id
        other_query = query.replace("abcdef01", "abcdef02")
        denied = client.get(
            path + "?" + other_query, headers=peer_headers(peers, "GET", path, other_query)
        )
        assert denied.status_code == 404 and denied.json()["detail"]["kind"] == "task_not_found"
        session_path = "/local-coordination/sessions"
        session_query = "project_id=" + peers.binding["project_id"]
        denied = client.get(
            session_path + "?" + session_query,
            headers=peer_headers(peers, "GET", session_path, session_query),
        )
        assert denied.status_code == 403
