"""Production CLI pairing and real stdio helpers for the isolated native journey."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Mapping
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import requests
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from cli_agent_orchestrator.utils.mcp_tool_policy import MCP_TOOL_PROFILE_ENV, TOOL_PROFILES

COORDINATION_TOOLS = TOOL_PROFILES["coordination"]


class HarnessError(RuntimeError):
    """Fixed-code error; diagnostic paths point only to private raw captures."""

    def __init__(self, code: str, diagnostics: tuple[str, ...] = ()) -> None:
        self.code = code
        self.diagnostics = diagnostics
        super().__init__(code)


@dataclass(frozen=True)
class PairingReceipt:
    source_instance_id: str
    target_instance_id: str
    project_id: str
    diagnostics: tuple[str, ...]


@dataclass(frozen=True)
class _Identity:
    instance_id: str
    process_generation: str


def _check_profile_binding(env: Mapping[str, str], base_url: str) -> None:
    try:
        url = urlsplit(base_url)
        port = url.port
        configured_port = int(env.get("CAO_API_PORT", "9889"))
    except (TypeError, ValueError):
        raise HarnessError("profile_endpoint_mismatch") from None
    host = url.hostname
    configured_host = env.get("CAO_API_HOST", "127.0.0.1").lower()
    if (
        url.scheme != "http"
        or host not in {"127.0.0.1", "localhost"}
        or port is None
        or configured_port != port
        or configured_host not in {host, "0.0.0.0"}
    ):
        raise HarnessError("profile_endpoint_mismatch")


def _identity(base_url: str) -> _Identity:
    """Read the public identity of the actual private API process."""
    endpoint = base_url.rstrip("/") + "/local-coordination/identity"
    try:
        response = requests.get(endpoint, timeout=(1.5, 3.0), allow_redirects=False)
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError):
        raise HarnessError("peer_identity_unavailable") from None
    if not isinstance(body, dict):
        raise HarnessError("peer_identity_invalid")
    instance_id, generation = body.get("instance_id"), body.get("process_generation")
    try:
        valid = (
            isinstance(instance_id, str)
            and str(UUID(instance_id)) == instance_id
            and isinstance(generation, str)
            and str(UUID(generation)) == generation
            and isinstance(body.get("loopback_port"), int)
            and not isinstance(body.get("loopback_port"), bool)
            and body["loopback_port"] == urlsplit(base_url).port
            and isinstance(body.get("public_key"), str)
            and len(body["public_key"]) >= 40
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise HarnessError("peer_identity_invalid")
    return _Identity(instance_id, generation)


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _private_log(raw_dir: str | Path, label: str, returncode, stdout, stderr) -> Path:
    """Store raw CLI output, including the one-use code, owner-only."""
    try:
        directory = Path(raw_dir)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = directory.lstat()
        if directory.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise HarnessError("private_diagnostics_directory_invalid")
        path = directory / f"{label}-{uuid4().hex}.log"
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(
                f"returncode: {returncode}\nstdout:\n{_text(stdout)}\nstderr:\n{_text(stderr)}\n"
            )
        os.chmod(path, 0o600)
        return path
    except OSError:
        raise HarnessError("private_diagnostics_unavailable") from None


def _run_cli(label, env, args, raw_dir, *, stdin=None):
    try:
        result = subprocess.run(
            [sys.executable, "-m", "cli_agent_orchestrator.cli.main", *args],
            env=dict(env),
            input=stdin,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        path = _private_log(raw_dir, label, None, error.stdout, error.stderr)
        raise HarnessError(f"{label}_timeout", (str(path),)) from None
    except OSError:
        path = _private_log(raw_dir, label, None, "", "CLI process could not start")
        raise HarnessError(f"{label}_failed", (str(path),)) from None
    path = _private_log(raw_dir, label, result.returncode, result.stdout, result.stderr)
    if result.returncode != 0:
        raise HarnessError(f"{label}_failed", (str(path),))
    return result.stdout, path


def _one(pattern: str, text: str) -> str | None:
    found = re.findall(pattern, text, flags=re.MULTILINE)
    return found[0] if len(found) == 1 else None


def pair_profiles(
    source_env: Mapping[str, str],
    target_env: Mapping[str, str],
    source_url: str,
    target_url: str,
    project: str | Path,
    raw_dir: str | Path,
) -> PairingReceipt:
    """Pair running profiles through `cao peer pair` and `cao peer accept`.

    Successful CLI calls are the registry-binding evidence: source pairing calls
    `current_server_identity()`, and target acceptance revalidates its registered
    generation and the initiator against the live peer registry. The helper
    returns no challenge ID, code, CLI stdout, or stderr.
    """
    _check_profile_binding(source_env, source_url)
    _check_profile_binding(target_env, target_url)
    if (
        not source_env.get("HOME")
        or source_env.get("HOME") != target_env.get("HOME")
        or not source_env.get("CAO_HOME_DIR")
        or not target_env.get("CAO_HOME_DIR")
        or source_env.get("CAO_HOME_DIR") == target_env.get("CAO_HOME_DIR")
        or not source_env.get("CAO_DATABASE_CONFIG_FILE")
        or not target_env.get("CAO_DATABASE_CONFIG_FILE")
        or source_env.get("CAO_DATABASE_CONFIG_FILE") == target_env.get("CAO_DATABASE_CONFIG_FILE")
    ):
        raise HarnessError("profile_store_binding_invalid")
    try:
        project_path = str(Path(project).expanduser().resolve(strict=True))
    except (OSError, RuntimeError, TypeError):
        raise HarnessError("project_path_invalid") from None
    if not Path(project_path).is_dir():
        raise HarnessError("project_path_invalid")

    source_before, target_before = _identity(source_url), _identity(target_url)
    if source_before.instance_id == target_before.instance_id:
        raise HarnessError("peer_identity_not_distinct")

    pair_stdout, pair_log = _run_cli(
        "source-pair",
        source_env,
        ["peer", "pair", target_before.instance_id, "--project", project_path],
        raw_dir,
    )
    challenge_id = _one(
        r"^ID de emparejamiento: ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
        pair_stdout,
    )
    pairing_code = _one(r"^Código temporal \(válido 5 minutos\): ([A-Za-z0-9_-]{43})$", pair_stdout)
    selected_peer = _one(r"^Par: .+ \(([0-9a-f-]{36})\)$", pair_stdout)
    canonical_project = _one(r"^Proyecto: (.+)$", pair_stdout)
    if (
        challenge_id is None
        or pairing_code is None
        or selected_peer != target_before.instance_id
        or canonical_project != project_path
    ):
        raise HarnessError("pair_output_invalid", (str(pair_log),))

    accept_stdout, accept_log = _run_cli(
        "target-accept",
        target_env,
        ["peer", "accept", challenge_id],
        raw_dir,
        stdin=f"y\n{pairing_code}\n",
    )
    project_id = _one(r"^Emparejado con .+ para el proyecto ([0-9a-f]{64})\.$", accept_stdout)
    if project_id is None:
        raise HarnessError("accept_output_invalid", (str(pair_log), str(accept_log)))

    if _identity(source_url) != source_before or _identity(target_url) != target_before:
        raise HarnessError("peer_identity_changed", (str(pair_log), str(accept_log)))
    return PairingReceipt(
        source_instance_id=source_before.instance_id,
        target_instance_id=target_before.instance_id,
        project_id=project_id,
        diagnostics=(str(pair_log), str(accept_log)),
    )


def unwrap_coordination_result(result: Any) -> dict[str, Any]:
    """Accept only a JSON receipt with `success is True` and no `error` field."""
    if getattr(result, "isError", None) is not False:
        raise HarnessError("mcp_tool_error")
    payload = getattr(result, "structuredContent", None)
    if payload is None:
        text_blocks = [
            item.text
            for item in getattr(result, "content", ())
            if getattr(item, "type", None) == "text"
            and isinstance(getattr(item, "text", None), str)
        ]
        if len(text_blocks) != 1:
            raise HarnessError("mcp_result_invalid")
        try:
            payload = json.loads(text_blocks[0])
        except (TypeError, ValueError):
            raise HarnessError("mcp_result_invalid") from None
    if isinstance(payload, dict) and set(payload) == {"result"}:
        payload = payload["result"]
    if not isinstance(payload, dict):
        raise HarnessError("mcp_result_invalid")
    if payload.get("success") is not True or "error" in payload:
        raise HarnessError("mcp_tool_refused")
    return payload


@dataclass
class CoordinationClient:
    _session: ClientSession

    async def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if name not in COORDINATION_TOOLS:
            raise HarnessError("tool_not_in_coordination_profile")
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(name, dict(arguments)), timeout=75
            )
        except asyncio.TimeoutError:
            raise HarnessError("mcp_tool_timeout") from None
        except Exception:
            raise HarnessError("mcp_tool_call_failed") from None
        return unwrap_coordination_result(result)


@asynccontextmanager
async def open_coordination_client(
    env: Mapping[str, str], caller_id: str
) -> AsyncIterator[CoordinationClient]:
    """Start the production MCP module as a real stdio child and verify its catalog."""
    if not isinstance(caller_id, str) or re.fullmatch(r"[a-f0-9]{8}", caller_id) is None:
        raise HarnessError("caller_identity_invalid")
    child_env = dict(env)
    child_env[MCP_TOOL_PROFILE_ENV] = "coordination"
    child_env["CAO_TERMINAL_ID"] = caller_id
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from cli_agent_orchestrator.mcp_server.server import main; main()"],
        env=child_env,
    )
    entered = False
    try:
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                try:
                    await asyncio.wait_for(session.initialize(), timeout=90)
                    tools = await asyncio.wait_for(session.list_tools(), timeout=75)
                except asyncio.TimeoutError:
                    raise HarnessError("mcp_startup_timeout") from None
                except Exception:
                    raise HarnessError("mcp_startup_failed") from None
                if (
                    len(COORDINATION_TOOLS) != 12
                    or len(tools.tools) != 12
                    or {tool.name for tool in tools.tools} != COORDINATION_TOOLS
                ):
                    raise HarnessError("mcp_catalog_mismatch")
                entered = True
                yield CoordinationClient(session)
    except HarnessError:
        raise
    except Exception:
        if entered:
            raise
        raise HarnessError("mcp_stdio_unavailable") from None
