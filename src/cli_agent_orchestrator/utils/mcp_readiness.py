"""Private launch-scoped signal for successful MCP client tool discovery."""

import json
from pathlib import Path

from cli_agent_orchestrator.utils.atomic_file import locked_atomic_write

READY_FILE_ENV = "CAO_MCP_READY_FILE"
READY_TOKEN_ENV = "CAO_MCP_READY_TOKEN"


def publish_ready_signal(path: Path, token: str) -> None:
    locked_atomic_write(path, json.dumps({"token": token}), mode=0o600, lock_timeout=1)


def has_ready_signal(path: Path, token: str) -> bool:
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
        return isinstance(payload, dict) and payload.get("token") == token
    except (OSError, ValueError, AttributeError):
        return False
