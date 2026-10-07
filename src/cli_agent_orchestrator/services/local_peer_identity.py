"""Persistent CAO profile identity and per-process identity."""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import os
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

import psutil
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from cli_agent_orchestrator.constants import (
    ALLOWED_HOSTS,
    CAO_HOME_DIR,
    LOCAL_PEER_DISPLAY_NAME,
    LOCAL_PEER_INSTANCE_ID_FILE,
    LOCAL_PEER_PRIVATE_KEY_FILE,
    SERVER_HOST,
    SERVER_PORT,
)


class LocalPeerUnavailableError(RuntimeError):
    """The current server address cannot be represented as a loopback peer."""


@dataclass(frozen=True)
class LocalProcessIdentity:
    """Public coordinates for one running profile process."""

    instance_id: str
    process_generation: str
    pid: int
    process_started_at: float
    display_name: str
    loopback_host: str
    loopback_port: int
    public_key: str


@lru_cache(maxsize=1)
def peer_private_key() -> Ed25519PrivateKey:
    """Load or create the profile's private signing key with owner-only access."""
    CAO_HOME_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(CAO_HOME_DIR, 0o700)
    except OSError:
        pass
    path = LOCAL_PEER_PRIVATE_KEY_FILE
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raw = Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raw = path.read_bytes()
        else:
            with os.fdopen(descriptor, "wb") as key_file:
                key_file.write(raw)
                key_file.flush()
                os.fsync(key_file.fileno())
    if len(raw) != 32:
        raise RuntimeError(f"Invalid local CAO peer signing key: {path}")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_key_text(private_key: Ed25519PrivateKey | None = None) -> str:  # gitleaks:allow
    key = private_key or peer_private_key()
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


# Masked Y encodings from libsodium's ge25519_has_small_order reference:
# https://github.com/jedisct1/libsodium/blob/1.0.20-RELEASE/src/libsodium/crypto_core/ed25519/ref10/ed25519_ref10.c
_ED25519_FIELD_P = 2**255 - 19
_ED25519_SMALL_ORDER_Y = frozenset(
    (
        0,
        1,
        2707385501144840649318225287225658788936804267575313519463743609750303402022,
        55188659117513257062467267217118295137698188065244968500265048394206261417927,
        _ED25519_FIELD_P - 1,
        _ED25519_FIELD_P,
        _ED25519_FIELD_P + 1,
    )
)


def peer_public_key_bytes(value: str) -> bytes:
    """Decode a peer key, rejecting noncanonical and small-order encodings.

    This is an encoding guard, not full subgroup validation. Cryptography
    remains responsible for verifying signatures with the original key bytes.
    """
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if len(raw) != 32:
        raise ValueError("peer signing key is invalid")
    y = int.from_bytes(raw, "little") & (2**255 - 1)
    if y >= _ED25519_FIELD_P or y in _ED25519_SMALL_ORDER_Y:
        raise ValueError("peer signing key is invalid")
    return raw


def _signature_material(
    method: str,
    path: str,
    query: str,
    project_id: str,
    instance_id: str,
    process_generation: str,
    timestamp: str,
    nonce: str,
    body_sha256: str,
) -> bytes:
    return "\n".join(
        (
            method.upper(),
            path,
            query,
            project_id,
            instance_id,
            process_generation,
            timestamp,
            nonce,
            body_sha256,
        )
    ).encode("utf-8")


def signed_peer_headers(
    *,
    method: str,
    path: str,
    project_id: str,
    body: bytes = b"",
    query: str = "",
    identity: LocalProcessIdentity | None = None,
) -> dict[str, str]:
    identity = identity or current_process_identity()
    timestamp = str(int(time.time()))
    nonce = uuid4().hex
    body_sha256 = hashlib.sha256(body).hexdigest()
    signature = peer_private_key().sign(
        _signature_material(
            method,
            path,
            query,
            project_id,
            identity.instance_id,
            identity.process_generation,
            timestamp,
            nonce,
            body_sha256,
        )
    )
    return {
        "X-CAO-Peer-Instance": identity.instance_id,
        "X-CAO-Peer-Generation": identity.process_generation,
        "X-CAO-Peer-Timestamp": timestamp,
        "X-CAO-Peer-Nonce": nonce,
        "X-CAO-Peer-Body-SHA256": body_sha256,
        "X-CAO-Peer-Signature": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
    }


def verify_peer_signature(
    *,
    public_key: str,
    method: str,
    path: str,
    query: str,
    project_id: str,
    instance_id: str,
    process_generation: str,
    timestamp: str,
    nonce: str,
    body_sha256: str,
    signature: str,
) -> bool:
    try:
        issued_at = int(timestamp)
        if abs(time.time() - issued_at) > 300 or re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
            return False
        public_raw = peer_public_key_bytes(public_key)
        signature_raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        Ed25519PublicKey.from_public_bytes(public_raw).verify(
            signature_raw,
            _signature_material(
                method,
                path,
                query,
                project_id,
                instance_id,
                process_generation,
                timestamp,
                nonce,
                body_sha256,
            ),
        )
        return True
    except (binascii.Error, ValueError, InvalidSignature):
        return False


def _loopback_host_for_server() -> str:
    configured = SERVER_HOST.strip().lower()
    if configured in {"localhost", "0.0.0.0"}:
        candidate = "127.0.0.1"
    else:
        try:
            address = ipaddress.ip_address(configured)
        except ValueError as error:
            raise LocalPeerUnavailableError(
                "CAO peer coordination requires a loopback-capable API host"
            ) from error
        if address.is_unspecified:
            candidate = "::1" if address.version == 6 else "127.0.0.1"
        elif address.is_loopback:
            candidate = str(address)
        else:
            raise LocalPeerUnavailableError(
                "CAO peer coordination requires a loopback-capable API host"
            )

    # The initial same-runtime implementation publishes IPv4 loopback only;
    # this is the address permitted by the default Host policy and the shared
    # registry schema. Never advertise a host a peer or this server rejects.
    if candidate != "127.0.0.1":
        raise LocalPeerUnavailableError(
            "CAO peer discovery currently requires CAO_API_HOST=127.0.0.1, localhost, or 0.0.0.0"
        )
    if candidate not in ALLOWED_HOSTS and "*" not in ALLOWED_HOSTS:
        raise LocalPeerUnavailableError(
            f"CAO peer discovery is disabled because {candidate} is not in CAO_ALLOWED_HOSTS"
        )
    return candidate


def _read_instance_id(path: Path) -> str:
    value = path.read_text(encoding="ascii").strip()
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as error:
        raise RuntimeError(f"Invalid local CAO identity file: {path}") from error


def get_persistent_instance_id() -> str:
    """Read or atomically create the UUID belonging to this CAO profile."""
    CAO_HOME_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(CAO_HOME_DIR, 0o700)
    except OSError:
        pass

    path = LOCAL_PEER_INSTANCE_ID_FILE
    try:
        return _read_instance_id(path)
    except FileNotFoundError:
        value = str(uuid4())
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return _read_instance_id(path)
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as identity_file:
                identity_file.write(value + "\n")
                identity_file.flush()
                os.fsync(identity_file.fileno())
        except BaseException:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return value


@lru_cache(maxsize=1)
def current_process_identity() -> LocalProcessIdentity:
    """Return stable coordinates for this server process until it exits."""
    pid = os.getpid()
    try:
        process_started_at = float(psutil.Process(pid).create_time())
    except (psutil.Error, OSError, ValueError) as error:
        raise RuntimeError("Cannot determine current CAO process start time") from error

    display_name = " ".join(LOCAL_PEER_DISPLAY_NAME.split())[:80] or "CAO"
    return LocalProcessIdentity(
        instance_id=get_persistent_instance_id(),
        process_generation=str(uuid4()),
        pid=pid,
        process_started_at=process_started_at,
        display_name=display_name,
        loopback_host=_loopback_host_for_server(),
        loopback_port=SERVER_PORT,
        public_key=public_key_text(),
    )
