"""Helpers for source capabilities and replay receipts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

REQUIRED_MCP_TOOL = "@cao-mcp-server"


def issue_capability() -> tuple[str, str]:
    token = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    return token, hashlib.sha256(token.encode("ascii")).hexdigest()


def capability_hash(token: object) -> str:
    if not isinstance(token, str) or not (32 <= len(token) <= 256):
        raise ValueError("invalid source capability")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def canonical_tool_fingerprint(value: Any) -> tuple[str, tuple[str, ...]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = None
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item or item != item.strip() for item in value
    ):
        tools: tuple[str, ...] = ()
    else:
        tools = tuple(value)
    digest = hashlib.sha256(
        json.dumps(list(tools), separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()
    return digest, tools


def answer_receipt(cap_hash: str, answer_id: str, question_id: str, version: int) -> str:
    message = f"interaction-answer/v1\0{answer_id}\0{question_id}\0{version}".encode("utf-8")
    digest = hmac.new(bytes.fromhex(cap_hash), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def receipt_hash(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def cursor_encode(created_at: float, question_id: str) -> str:
    raw = f"{created_at!r}\n{question_id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def cursor_decode(value: str) -> tuple[float, str]:
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError("invalid interaction cursor")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")
        timestamp, question_id = raw.split("\n", 1)
        return float(timestamp), question_id
    except (ValueError, UnicodeError) as error:
        raise ValueError("invalid interaction cursor") from error


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
