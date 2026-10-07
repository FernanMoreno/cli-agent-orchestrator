"""Signed local-peer requests require pinned keys, exact scopes and fresh nonces."""

import base64
import hashlib
import json
import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.services import local_peer_auth
from cli_agent_orchestrator.services.local_peer_identity import _signature_material


def _keypair():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return private, base64.urlsafe_b64encode(public).decode("ascii").rstrip("=")


def _signed_fields(
    private, *, peer_id, process_generation, project_id, method, path, query="", body=b""
):
    timestamp = str(int(time.time()))
    nonce = uuid4().hex
    body_sha256 = hashlib.sha256(body).hexdigest()
    signature = private.sign(
        _signature_material(
            method,
            path,
            query,
            project_id,
            peer_id,
            process_generation,
            timestamp,
            nonce,
            body_sha256,
        )
    )
    return {
        "peer_instance_id": peer_id,
        "peer_process_generation": process_generation,
        "project_id": project_id,
        "signature": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        "method": method,
        "path": path,
        "query": query,
        "timestamp": timestamp,
        "nonce": nonce,
        "body_sha256": body_sha256,
        "body": body,
    }


def _grant(public, *, peer_id, project_id, scopes, revoked_at=None):
    with database.SessionLocal() as session:
        session.add(
            database.LocalPeerGrantModel(
                grant_id=str(uuid4()),
                peer_instance_id=peer_id,
                project_id=project_id,
                peer_display_name="test-peer",
                peer_public_key=public,
                scopes_json=json.dumps(sorted(scopes)),
                revoked_at=revoked_at,
            )
        )
        session.commit()


def test_signed_peer_request_accepts_once_and_rejects_nonce_replay():
    private, public = _keypair()
    peer_id = str(uuid4())
    generation = str(uuid4())
    project_id = "a" * 64
    request = _signed_fields(
        private,
        peer_id=peer_id,
        process_generation=generation,
        project_id=project_id,
        method="POST",
        path="/local-coordination/tasks",
        body=b'{"message":"review"}',
    )
    _grant(
        public,
        peer_id=peer_id,
        project_id=project_id,
        scopes={"task:submit"},
    )

    authenticated = local_peer_auth.authenticate_peer(**request, required_scope="task:submit")
    assert authenticated["peer_instance_id"] == peer_id

    with pytest.raises(local_peer_auth.LocalPeerAuthError, match="already been used"):
        local_peer_auth.authenticate_peer(**request, required_scope="task:submit")


def test_signed_peer_request_rejects_a_modified_body():
    private, public = _keypair()
    peer_id = str(uuid4())
    project_id = "b" * 64
    request = _signed_fields(
        private,
        peer_id=peer_id,
        process_generation=str(uuid4()),
        project_id=project_id,
        method="POST",
        path="/local-coordination/tasks",
        body=b'{"message":"review"}',
    )
    request["body"] = b'{"message":"different"}'
    _grant(
        public,
        peer_id=peer_id,
        project_id=project_id,
        scopes={"task:submit"},
    )

    with pytest.raises(local_peer_auth.LocalPeerAuthError, match="body signature"):
        local_peer_auth.authenticate_peer(**request, required_scope="task:submit")


def test_revoked_peer_can_read_only_its_own_retained_task():
    private, public = _keypair()
    peer_id = str(uuid4())
    project_id = "c" * 64
    task_id = str(uuid4())
    requester_terminal_id = "deadbeef"
    _grant(
        public,
        peer_id=peer_id,
        project_id=project_id,
        scopes={"task:status"},
        revoked_at=datetime.now(timezone.utc),
    )
    with database.SessionLocal() as session:
        session.add(
            database.LocalPeerTaskModel(
                task_id=task_id,
                source_instance_id=peer_id,
                target_instance_id=str(uuid4()),
                requester_terminal_id=requester_terminal_id,
                project_id=project_id,
                operation_key="peer-status-key",
                request_hash="d" * 64,
                use_worktree=False,
                state="running",
            )
        )
        session.commit()

    query = f"project_id={project_id}&requester_terminal_id={requester_terminal_id}"
    valid_request = _signed_fields(
        private,
        peer_id=peer_id,
        process_generation=str(uuid4()),
        project_id=project_id,
        method="GET",
        path=f"/local-coordination/tasks/{task_id}",
        query=query,
    )
    receipt = local_peer_auth.authenticate_peer(
        **valid_request,
        required_scope="task:status",
        task_id=task_id,
        requester_terminal_id=requester_terminal_id,
    )
    assert receipt["revoked_at"] is not None

    other_terminal_id = "cafebabe"
    wrong_request = _signed_fields(
        private,
        peer_id=peer_id,
        process_generation=str(uuid4()),
        project_id=project_id,
        method="GET",
        path=f"/local-coordination/tasks/{task_id}",
        query=f"project_id={project_id}&requester_terminal_id={other_terminal_id}",
    )
    with pytest.raises(local_peer_auth.LocalPeerAuthError, match="revoked"):
        local_peer_auth.authenticate_peer(
            **wrong_request,
            required_scope="task:status",
            task_id=task_id,
            requester_terminal_id=other_terminal_id,
        )


@pytest.mark.parametrize("mismatch", ["project", "action"])
def test_peer_capability_is_bound_to_exact_project_and_action(mismatch):
    private, public = _keypair()
    peer_id, generation = str(uuid4()), str(uuid4())
    project_id = "a" * 64
    _grant(public, peer_id=peer_id, project_id=project_id, scopes={"task:submit"})
    request = _signed_fields(
        private,
        peer_id=peer_id,
        process_generation=generation,
        project_id="b" * 64 if mismatch == "project" else project_id,
        method="GET",
        path="/local-coordination/sessions",
        body=b"",
    )
    with pytest.raises(local_peer_auth.LocalPeerAuthError):
        local_peer_auth.authenticate_peer(
            **request, required_scope="session:read" if mismatch == "action" else "task:submit"
        )
