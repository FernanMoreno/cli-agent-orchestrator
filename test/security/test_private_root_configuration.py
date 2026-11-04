"""Offline tests for trusted Root signer and trust-file loading."""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cli_agent_orchestrator.security import private_root_configuration as root_credentials

pytestmark = pytest.mark.offline_database


def _raw_private_key(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


def _raw_public_key(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _write_signer(
    path: Path,
    key: Ed25519PrivateKey,
    *,
    key_id: str = "root-test",
    seed: bytes | None = None,
    mode: int = 0o600,
) -> None:
    encoded = base64.b64encode(_raw_private_key(key) if seed is None else seed).decode("ascii")
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "key_id": key_id,
                "private_key_base64": encoded,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    path.chmod(mode)


def _write_trust(
    path: Path,
    keys: dict[str, bytes],
    *,
    mode: int = 0o600,
) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "keys": {
                    key_id: base64.b64encode(public_key).decode("ascii")
                    for key_id, public_key in keys.items()
                },
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    path.chmod(mode)


def test_constructor_is_inert_repr_hides_paths_and_each_call_rereads_files(tmp_path):
    signer_path = tmp_path / "root-signer.json"
    trust_path = tmp_path / "root-trust.json"
    key = Ed25519PrivateKey.generate()
    _write_signer(signer_path, key)
    _write_trust(trust_path, {"root-test": _raw_public_key(key)})

    with patch.object(
        root_credentials.os, "open", side_effect=AssertionError("I/O in constructor")
    ):
        signer = root_credentials.ExternalRootSigner(signer_path, trust_path)

    assert repr(signer) == "ExternalRootSigner()"
    assert str(signer_path) not in repr(signer)
    assert str(trust_path) not in repr(signer)
    with patch.object(root_credentials.os, "open", wraps=os.open) as open_file:
        assert signer.preflight() is None
        key_id, loaded = signer()
    assert open_file.call_count == 4
    assert key_id == "root-test"
    assert _raw_public_key(loaded) == _raw_public_key(key)


def test_signer_is_not_cached_and_rejects_a_changed_untrusted_key(tmp_path):
    signer_path = tmp_path / "root-signer.json"
    trust_path = tmp_path / "root-trust.json"
    key = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    _write_signer(signer_path, key)
    _write_trust(trust_path, {"root-test": _raw_public_key(key)})
    signer = root_credentials.ExternalRootSigner(signer_path, trust_path)
    signer.preflight()

    _write_signer(signer_path, other)
    with pytest.raises(root_credentials.RootCredentialsUnavailable):
        signer()


def test_signer_key_and_trust_public_key_must_match(tmp_path):
    signer_path = tmp_path / "root-signer.json"
    trust_path = tmp_path / "root-trust.json"
    key = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    _write_signer(signer_path, key)
    _write_trust(trust_path, {"root-test": _raw_public_key(other)})

    signer = root_credentials.ExternalRootSigner(signer_path, trust_path)
    with pytest.raises(root_credentials.RootCredentialsUnavailable):
        signer.preflight()


def test_signer_requires_an_exact_owner_private_file_mode(tmp_path):
    signer_path = tmp_path / "root-signer.json"
    trust_path = tmp_path / "root-trust.json"
    key = Ed25519PrivateKey.generate()
    _write_signer(signer_path, key, mode=0o400)
    _write_trust(trust_path, {"root-test": _raw_public_key(key)})

    with pytest.raises(root_credentials.RootCredentialsUnavailable):
        root_credentials.ExternalRootSigner(signer_path, trust_path).preflight()


def test_signer_schema_rejects_duplicate_boolean_extra_and_invalid_key_id(tmp_path):
    signer_path = tmp_path / "root-signer.json"
    trust_path = tmp_path / "root-trust.json"
    key = Ed25519PrivateKey.generate()
    seed = base64.b64encode(_raw_private_key(key)).decode("ascii")
    _write_trust(trust_path, {"root-test": _raw_public_key(key)})
    invalid_documents = (
        '{"version":1,"key_id":"root-test","key_id":"root-test",'
        f'"private_key_base64":"{seed}"}}',
        json.dumps({"version": True, "key_id": "root-test", "private_key_base64": seed}),
        json.dumps(
            {
                "version": 1,
                "key_id": "root-test",
                "private_key_base64": seed,
                "extra": "ignored fields are forbidden",
            }
        ),
        json.dumps({"version": 1, "key_id": "root key", "private_key_base64": seed}),
    )
    signer = root_credentials.ExternalRootSigner(signer_path, trust_path)
    for document in invalid_documents:
        signer_path.write_text(document, encoding="utf-8")
        signer_path.chmod(0o600)
        with pytest.raises(root_credentials.RootCredentialsUnavailable):
            signer()


def test_signer_rejects_wrong_seed_length_and_noncanonical_base64(tmp_path):
    signer_path = tmp_path / "root-signer.json"
    trust_path = tmp_path / "root-trust.json"
    key = Ed25519PrivateKey.generate()
    _write_trust(trust_path, {"root-test": _raw_public_key(key)})
    signer = root_credentials.ExternalRootSigner(signer_path, trust_path)

    _write_signer(signer_path, key, seed=_raw_private_key(key)[:-1])
    with pytest.raises(root_credentials.RootCredentialsUnavailable):
        signer()

    seed = _raw_private_key(key)
    encoded = base64.b64encode(seed).decode("ascii")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    final_value = alphabet.index(encoded[-2])
    noncanonical = (
        encoded[:-2]
        + alphabet[(final_value & 0b110000) | ((final_value + 1) & 0b001111)]
        + encoded[-1]
    )
    assert base64.b64decode(noncanonical, validate=True) == seed
    signer_path.write_text(
        json.dumps(
            {
                "version": 1,
                "key_id": "root-test",
                "private_key_base64": noncanonical,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    signer_path.chmod(0o600)
    with pytest.raises(root_credentials.RootCredentialsUnavailable):
        signer()


def test_signer_error_does_not_disclose_paths_seeds_or_inner_causes(tmp_path):
    signer_path = tmp_path / "private-secret-root-signer.json"
    trust_path = tmp_path / "private-secret-root-trust.json"
    key = Ed25519PrivateKey.generate()
    _write_signer(signer_path, key)
    _write_trust(trust_path, {"root-test": _raw_public_key(Ed25519PrivateKey.generate())})
    signer = root_credentials.ExternalRootSigner(signer_path, trust_path)

    with pytest.raises(root_credentials.RootCredentialsUnavailable) as failure:
        signer.preflight()

    assert str(failure.value) == "Root credentials are unavailable"
    assert repr(failure.value) == "RootCredentialsUnavailable()"
    assert failure.value.__cause__ is None
    assert failure.value.__suppress_context__ is True
    assert str(signer_path) not in str(failure.value)
    assert str(trust_path) not in repr(failure.value)


def test_shared_private_reader_rejects_fifo_symlink_permissions_and_size(tmp_path):
    fifo = tmp_path / "root-trust.pipe"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(PermissionError):
        root_credentials.read_private_root_document(fifo, maximum_bytes=16 * 1024)

    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "root-trust-link.json"
    link.symlink_to(target)
    with pytest.raises(OSError):
        root_credentials.read_private_root_document(link, maximum_bytes=16 * 1024)

    target.chmod(0o644)
    with pytest.raises(PermissionError):
        root_credentials.read_private_root_document(target, maximum_bytes=16 * 1024)

    target.chmod(0o600)
    target.write_text("x" * 20, encoding="utf-8")
    target.chmod(0o600)
    with pytest.raises(PermissionError):
        root_credentials.read_private_root_document(target, maximum_bytes=16)


def test_shared_private_reader_rejects_relative_paths_and_detects_midread_change(
    tmp_path,
):
    with pytest.raises(ValueError):
        root_credentials.read_private_root_document(Path("relative.json"), maximum_bytes=1024)

    path = tmp_path / "changing.json"
    path.write_text('{"version":1}', encoding="utf-8")
    path.chmod(0o600)
    original_read = os.read
    changed = False

    def change_during_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        payload = original_read(descriptor, size)
        if payload and not changed:
            changed = True
            path.write_text('{"version":2}', encoding="utf-8")
            path.chmod(0o600)
        return payload

    with patch.object(root_credentials.os, "read", side_effect=change_during_read):
        with pytest.raises(ValueError):
            root_credentials.read_private_root_document(path, maximum_bytes=1024)
    assert changed


def test_trust_reader_preserves_strict_existing_json_codec(tmp_path):
    key = Ed25519PrivateKey.generate()
    trust_path = tmp_path / "root-trust.json"
    _write_trust(trust_path, {"root-test": _raw_public_key(key)})
    loaded = root_credentials.read_root_trusted_keys(trust_path)
    assert set(loaded) == {"root-test"}
    assert loaded["root-test"].public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    ) == _raw_public_key(key)

    valid_key = base64.b64encode(_raw_public_key(key)).decode("ascii")
    invalid_documents = (
        '{"version":1,"keys":{"root-test":"' + valid_key + '"},"version":1}',
        json.dumps({"version": True, "keys": {"root-test": valid_key}}),
        json.dumps({"version": 1, "keys": {"root-test": valid_key}, "extra": 0}),
    )
    for document in invalid_documents:
        trust_path.write_text(document, encoding="utf-8")
        trust_path.chmod(0o600)
        with pytest.raises(ValueError):
            root_credentials.read_root_trusted_keys(trust_path)


def test_cli_keeps_private_reader_and_trust_loader_aliases(tmp_path):
    from cli_agent_orchestrator.runtime_channel import root_control

    key = Ed25519PrivateKey.generate()
    trust_path = tmp_path / "root-trust.json"
    _write_trust(trust_path, {"root-test": _raw_public_key(key)})

    assert root_control._read_private_document(
        trust_path,
        maximum_bytes=16 * 1024,
    ) == root_credentials.read_private_root_document(
        trust_path,
        maximum_bytes=16 * 1024,
    )
    assert set(root_control._read_trusted_keys(trust_path)) == {"root-test"}
