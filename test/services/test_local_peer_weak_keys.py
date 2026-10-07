"""Peer admission and legacy grants reject known weak Ed25519 key encodings."""

import base64
import hashlib
import json
import time
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.services import local_peer_auth, local_peer_identity

# Published ge25519_has_small_order vectors, including the p and p+1 aliases:
# https://github.com/jedisct1/libsodium/blob/1.0.20-RELEASE/src/libsodium/crypto_core/ed25519/ref10/ed25519_ref10.c
_WEAK_Y = (
    0,
    1,
    2707385501144840649318225287225658788936804267575313519463743609750303402022,
    55188659117513257062467267217118295137698188065244968500265048394206261417927,
    2**255 - 20,
    2**255 - 19,
    2**255 - 18,
)


def _encode(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@pytest.mark.parametrize("y", _WEAK_Y)
@pytest.mark.parametrize("sign", (0, 1))
def test_pairing_rejects_small_order_and_alias_keys(y, sign):
    encoded = _encode((y | (sign << 255)).to_bytes(32, "little"))
    with pytest.raises(local_peer_auth.PairingMismatchError, match="signing key is invalid"):
        local_peer_auth._validate_public_key(encoded)


@pytest.mark.parametrize("y", (2**255 - 17, 2**255 - 1))
@pytest.mark.parametrize("sign", (0, 1))
def test_pairing_rejects_other_noncanonical_keys(y, sign):
    encoded = _encode((y | (sign << 255)).to_bytes(32, "little"))
    with pytest.raises(local_peer_auth.PairingMismatchError, match="signing key is invalid"):
        local_peer_auth._validate_public_key(encoded)


def _fields():
    return {
        "method": "GET",
        "path": "/local-coordination/sessions",
        "query": "",
        "project_id": "a" * 64,
        "instance_id": str(uuid4()),
        "process_generation": str(uuid4()),
        "timestamp": str(int(time.time())),
        "nonce": "0" * 32,
        "body_sha256": hashlib.sha256(b"").hexdigest(),
    }


@pytest.mark.parametrize("y", (0, 1))
def test_known_weak_keys_cannot_verify_forged_requests(y):
    fields = _fields()
    public = _encode(y.to_bytes(32, "little"))
    signature = _encode(bytes([1]) + bytes(63))
    # Identity-R/zero-S signatures need no private key for these public points.
    # Different nonces cover the zero-point challenge dependence as well.
    for nonce in range(64):
        fields["nonce"] = f"{nonce:032x}"
        assert not local_peer_identity.verify_peer_signature(
            public_key=public, signature=signature, **fields
        )


def test_existing_weak_key_grant_cannot_authenticate():
    fields = _fields()
    with database.SessionLocal() as session:
        session.add(
            database.LocalPeerGrantModel(
                grant_id=str(uuid4()),
                peer_instance_id=fields["instance_id"],
                project_id=fields["project_id"],
                peer_display_name="legacy weak key",
                peer_public_key=_encode(bytes([1]) + bytes(31)),
                scopes_json=json.dumps(["session:read"]),
            )
        )
        session.commit()
    with pytest.raises(local_peer_auth.LocalPeerAuthError, match="signature is invalid"):
        local_peer_auth.authenticate_peer(
            peer_instance_id=fields.pop("instance_id"),
            peer_process_generation=fields.pop("process_generation"),
            signature=_encode(bytes([1]) + bytes(63)),
            body=b"",
            required_scope="session:read",
            **fields,
        )


@pytest.mark.parametrize("sign", (0, 1))
def test_generated_keys_retain_their_original_sign_and_verify(sign):
    for _ in range(128):
        private = Ed25519PrivateKey.generate()
        public = local_peer_identity.public_key_text(private)
        raw = base64.urlsafe_b64decode(public + "=" * (-len(public) % 4))
        if raw[31] >> 7 == sign:
            break
    else:
        pytest.fail("could not generate a key with the requested public sign bit")
    assert local_peer_auth._validate_public_key(public) == public
    fields = _fields()
    signature = private.sign(local_peer_identity._signature_material(**fields))
    assert local_peer_identity.verify_peer_signature(
        public_key=public, signature=_encode(signature), **fields
    )
