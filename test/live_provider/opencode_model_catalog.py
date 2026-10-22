"""No-auth current-catalog admission for explicit live acceptance, not provider policy."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

CATALOG_URL = "https://models.opencode.ai/api.json"
CATALOG_MAX_BYTES = 16 * 1024 * 1024
CATALOG_DEADLINE_SECONDS = 10
# A separate isolated stdlib reader bounds DNS, TLS, headers and body together.
# It cannot launch descendants, import project code or access ambient auth/proxies.
# The fixed child limits mirror the constants; the parent independently enforces both.
_CATALOG_READER = """
import http.client, ssl, sys
connection = http.client.HTTPSConnection('models.opencode.ai', timeout=10, context=ssl.create_default_context())
try:
    connection.request('GET', '/api.json', headers={'Accept': 'application/json'})
    response = connection.getresponse()
    if response.status != 200:
        sys.stdout.buffer.write(str(response.status).encode() + b'\\n')
    else:
        body = response.read(16 * 1024 * 1024 + 1)
        if len(body) > 16 * 1024 * 1024:
            sys.stdout.buffer.write(b'oversized')
        else:
            sys.stdout.buffer.write(b'200\\n' + body)
except Exception:
    sys.stdout.buffer.write(b'unavailable')
finally:
    connection.close()
"""


def assess_catalog(document: Any, model: str) -> dict[str, Any]:
    """Project only fixed catalog states; never echo server payloads or model metadata."""
    result: dict[str, Any] = {"eligible": False, "reason": "catalog_invalid", "status": "unknown"}
    if (
        not isinstance(model, str)
        or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}/[A-Za-z0-9][A-Za-z0-9_.#/-]{0,119}", model
        )
        is None
    ):
        return {**result, "reason": "invalid_model"}
    provider, name = model.split("/", 1)
    if not isinstance(document, dict):
        return result
    if provider not in document:
        return {**result, "reason": "not_found"}
    provider_data = document[provider]
    if not isinstance(provider_data, dict) or not isinstance(provider_data.get("models"), dict):
        return result
    models = provider_data["models"]
    if name not in models:
        return {**result, "reason": "not_found"}
    entry = models[name]
    if not isinstance(entry, dict):
        return result
    status = entry.get("status")
    if status is not None and (
        not isinstance(status, str) or status not in {"active", "alpha", "beta", "deprecated"}
    ):
        return result
    result["status"] = status or "unspecified"
    if status == "deprecated":
        return {**result, "reason": "deprecated"}
    if entry.get("tool_call") is not True:
        return {**result, "reason": "tool_calling_unavailable"}
    return {**result, "eligible": True, "reason": "eligible"}


def check_model(model: str) -> dict[str, Any]:
    """One fixed HTTPS GET, hard child deadline, no login/config writes or model call."""
    invalid = assess_catalog({}, model)
    if invalid["reason"] == "invalid_model":
        return invalid
    metadata: dict[str, Any] = {
        "source": CATALOG_URL,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "eligible": False,
        "reason": "catalog_unavailable",
        "status": "unknown",
    }
    try:
        child = subprocess.run(
            [sys.executable, "-I", "-c", _CATALOG_READER],
            env={"PATH": os.defpath, "LANG": "C.UTF-8"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=CATALOG_DEADLINE_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return metadata
    if child.returncode != 0:
        return metadata
    output = child.stdout
    if output == b"oversized" or len(output) > CATALOG_MAX_BYTES + 4:
        return {**metadata, "reason": "catalog_oversized"}
    header, separator, body = output.partition(b"\n")
    if not separator or re.fullmatch(rb"[1-5][0-9]{2}", header) is None:
        return metadata
    metadata["http_status"] = int(header)
    if header != b"200":
        return {**metadata, "reason": "catalog_http_error"}
    metadata["catalog_sha256"] = hashlib.sha256(body).hexdigest()
    metadata["catalog_bytes"] = len(body)
    try:
        document = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        return {**metadata, "reason": "catalog_invalid"}
    return {**metadata, **assess_catalog(document, model)}
