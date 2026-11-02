"""Ed25519 signing and trusted-key verification for root SQL certificates."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from cli_agent_orchestrator.models.sql_root_genesis import (
    ConfirmedRootSqlOpening,
    RootSqlOpeningIntent,
    SignedRootSqlOpening,
)

_KEY_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")


class RootGenesisUnavailable(RuntimeError):
    """Fixed, non-secret failure for unavailable or invalid root genesis."""

    def __init__(self) -> None:
        super().__init__("root SQL genesis unavailable")


def _opening_bytes(record: RootSqlOpeningIntent) -> bytes:
    return json.dumps(
        record.to_packet(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def sign_root_opening(
    cap: ConfirmedRootSqlOpening,
    *,
    key_id: str,
    private_key: Ed25519PrivateKey,
) -> SignedRootSqlOpening:
    """Sign only a live post-commit capability, consuming its sole attempt."""
    if (
        type(cap) is not ConfirmedRootSqlOpening
        or type(key_id) is not str
        or _KEY_ID.fullmatch(key_id) is None
        or not isinstance(private_key, Ed25519PrivateKey)
        or type(cap.record) is not RootSqlOpeningIntent
    ):
        raise RootGenesisUnavailable()
    try:
        cap.claim_sign()
        signature = private_key.sign(_opening_bytes(cap.record))
        encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
        certificate = SignedRootSqlOpening(
            record=cap.record,
            key_id=key_id,
            signature=encoded_signature,
        )
        certificate.to_bytes()
        cap.check()
        return certificate
    except Exception:
        # The claim remains consumed on every failure after the signing attempt.
        raise RootGenesisUnavailable() from None


def verify_root_opening(
    certificate: SignedRootSqlOpening,
    *,
    trusted_keys: Mapping[str, Ed25519PublicKey],
) -> RootSqlOpeningIntent:
    """Verify against deployment-supplied keys and return descriptive data."""
    if type(certificate) is not SignedRootSqlOpening or not isinstance(trusted_keys, Mapping):
        raise RootGenesisUnavailable()
    try:
        certificate.to_bytes()  # Reject in-memory certificates over the wire bound.
        public_key = trusted_keys.get(certificate.key_id)
        if not isinstance(public_key, Ed25519PublicKey):
            raise InvalidSignature
        raw_signature = base64.b64decode(
            certificate.signature.encode("ascii") + b"==",
            altchars=b"-_",
            validate=True,
        )
        public_key.verify(raw_signature, _opening_bytes(certificate.record))
        return certificate.record
    except Exception:
        raise RootGenesisUnavailable() from None
