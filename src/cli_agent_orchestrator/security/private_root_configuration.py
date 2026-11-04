"""Strict private Root signer and trust configuration readers.

The child CLI and trusted parent compositor share this exact file codec. This
module performs bounded configuration reads only; it never creates keys,
starts workers, opens SQL, or grants resources.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

_MAX_TRUST_BYTES = 16 * 1024
_MAX_SIGNER_BYTES = 16 * 1024
_ROOT_KEY_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")


class RootCredentialsUnavailable(RuntimeError):
    """Fixed failure that never includes credential paths or values."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__("Root credentials are unavailable")

    def __repr__(self) -> str:
        return "RootCredentialsUnavailable()"


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _invalid_json_constant(token: str) -> NoReturn:
    raise ValueError(f"invalid JSON constant: {token}")


def read_private_root_document(
    path: Path,
    *,
    maximum_bytes: int,
    required_mode: int | None = None,
) -> Mapping[str, object]:
    """Read a bounded owner-private regular JSON file without following links.

    ``required_mode`` is used for the external signing-key document. The
    default preserves the Root CLI's existing owner-only permission rule for
    trusted-key files.
    """
    if not path.is_absolute():
        raise ValueError("configuration paths must be absolute")
    if isinstance(maximum_bytes, bool) or not isinstance(maximum_bytes, int) or maximum_bytes <= 0:
        raise ValueError("configuration byte limit is invalid")
    if required_mode is not None and (
        isinstance(required_mode, bool)
        or not isinstance(required_mode, int)
        or required_mode < 0
        or required_mode > 0o7777
    ):
        raise ValueError("configuration mode is invalid")

    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_mode & 0o077
            or (required_mode is not None and mode != required_mode)
            or before.st_size > maximum_bytes
        ):
            raise PermissionError("configuration must be a bounded private owner file")
        payload = bytearray()
        while len(payload) <= maximum_bytes:
            block = os.read(descriptor, min(4096, maximum_bytes + 1 - len(payload)))
            if not block:
                break
            payload.extend(block)
        after = os.fstat(descriptor)
        if len(payload) > maximum_bytes or (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("configuration changed while being read")
    finally:
        os.close(descriptor)

    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ValueError("configuration JSON is invalid") from error
    if type(document) is not dict or any(type(key) is not str for key in document):
        raise ValueError("configuration must be a JSON object")
    return document


def read_root_trusted_keys(path: Path) -> Mapping[str, Ed25519PublicKey]:
    """Decode the existing Root CLI v1 trust document without widening it."""
    document = read_private_root_document(path, maximum_bytes=_MAX_TRUST_BYTES)
    if (
        set(document) != {"version", "keys"}
        or type(document["version"]) is not int
        or document["version"] != 1
        or type(document["keys"]) is not dict
        or not document["keys"]
        or len(document["keys"]) > 64
    ):
        raise ValueError("trusted-key document schema is invalid")
    keys: dict[str, Ed25519PublicKey] = {}
    for key_id, encoded in document["keys"].items():
        if type(key_id) is not str or type(encoded) is not str or len(key_id) > 64:
            raise ValueError("trusted-key entry is invalid")
        try:
            raw = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError) as error:
            raise ValueError("trusted-key entry is invalid") from error
        if len(raw) != 32 or base64.b64encode(raw).decode("ascii") != encoded:
            raise ValueError("trusted-key entry is invalid")
        keys[key_id] = Ed25519PublicKey.from_public_bytes(raw)
    return keys


@dataclass(frozen=True, slots=True, repr=False)
class ExternalRootSigner:
    """Inert file-backed Root signer that revalidates its trust on each use."""

    signing_key_file: Path = field(repr=False)
    trust_file: Path = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.signing_key_file, Path)
            or not self.signing_key_file.is_absolute()
            or not isinstance(self.trust_file, Path)
            or not self.trust_file.is_absolute()
        ):
            raise RootCredentialsUnavailable() from None

    def __repr__(self) -> str:
        return "ExternalRootSigner()"

    def preflight(self) -> None:
        self._load()

    def __call__(self) -> tuple[str, Ed25519PrivateKey]:
        return self._load()

    def _load(self) -> tuple[str, Ed25519PrivateKey]:
        try:
            document = read_private_root_document(
                self.signing_key_file,
                maximum_bytes=_MAX_SIGNER_BYTES,
                required_mode=0o600,
            )
            if (
                set(document) != {"version", "key_id", "private_key_base64"}
                or type(document["version"]) is not int
                or document["version"] != 1
                or type(document["key_id"]) is not str
                or _ROOT_KEY_ID.fullmatch(document["key_id"]) is None
                or type(document["private_key_base64"]) is not str
            ):
                raise ValueError("signer document schema is invalid")
            encoded_seed = document["private_key_base64"]
            try:
                seed = base64.b64decode(encoded_seed.encode("ascii"), validate=True)
            except (UnicodeEncodeError, binascii.Error, ValueError) as error:
                raise ValueError("signer key encoding is invalid") from error
            if len(seed) != 32 or base64.b64encode(seed).decode("ascii") != encoded_seed:
                raise ValueError("signer key encoding is invalid")

            key_id = document["key_id"]
            private_key = Ed25519PrivateKey.from_private_bytes(seed)
            public_key = private_key.public_key()
            trusted_keys = read_root_trusted_keys(self.trust_file)
            trusted_key = trusted_keys.get(key_id)
            if not isinstance(trusted_key, Ed25519PublicKey):
                raise ValueError("signer key is absent from the trust document")
            derived_bytes = public_key.public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
            trusted_bytes = trusted_key.public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
            if derived_bytes != trusted_bytes:
                raise ValueError("signer key does not match the trust document")
            return key_id, private_key
        except Exception:
            raise RootCredentialsUnavailable() from None
