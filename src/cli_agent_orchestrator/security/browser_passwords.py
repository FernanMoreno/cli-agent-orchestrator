"""Versioned scrypt password verification with bounded process-wide capacity."""

import hashlib
import hmac
import secrets
import threading
from functools import lru_cache

from cli_agent_orchestrator.models.browser_auth import BrowserAuthError

PARAMS = dict(n=131072, r=8, p=1, dklen=32)
_VERIFIERS = threading.BoundedSemaphore(2)
_QUEUE = threading.BoundedSemaphore(10)


def _derive(password: str, salt: bytes) -> bytes:
    if not _QUEUE.acquire(blocking=False):
        raise BrowserAuthError("auth_unavailable", 503)
    try:
        if not _VERIFIERS.acquire(timeout=5):
            raise BrowserAuthError("auth_unavailable", 503)
        try:
            return hashlib.scrypt(
                password.encode("utf-8"), salt=salt, maxmem=256 * 1024 * 1024, **PARAMS
            )
        except (ValueError, MemoryError, UnicodeError) as exc:
            raise BrowserAuthError("auth_unavailable", 503) from exc
        finally:
            _VERIFIERS.release()
    finally:
        _QUEUE.release()


def hash_password(password: str) -> dict:
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        raise ValueError("Password must have between 10 and 128 characters")
    salt = secrets.token_bytes(16)
    return dict(
        scheme="scrypt-v1",
        salt=salt.hex(),
        hash=_derive(password, salt).hex(),
        params=PARAMS.copy(),
    )


def verify_password(password: str, record: dict) -> bool:
    try:
        if record["scheme"] != "scrypt-v1" or record["params"] != PARAMS:
            return False
        salt = bytes.fromhex(record["salt"])
        expected = bytes.fromhex(record["hash"])
        if len(salt) < 16 or len(expected) != 32 or not isinstance(password, str):
            return False
        actual = _derive(password, salt)
        return hmac.compare_digest(actual, expected)
    except (KeyError, TypeError, ValueError):
        return False


@lru_cache(maxsize=1)
def dummy_password_hash() -> dict:
    return hash_password(secrets.token_urlsafe(32))
