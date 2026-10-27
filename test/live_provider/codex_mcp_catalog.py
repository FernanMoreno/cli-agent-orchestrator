"""Opt-in native Codex app-server catalog checks; never starts a model turn."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import selectors
import subprocess
import sys
import time
from pathlib import Path
from test.live_provider import interaction_cli_journey as journey

from jsonschema import validate

SERVER = "cao-mcp-server"
SCHEMAS = {
    "initialize": "v1/InitializeParams.json",
    "thread/start": "v2/ThreadStartParams.json",
    "mcpServerStatus/list": "v2/ListMcpServerStatusParams.json",
    "mcpServer/tool/call": "v2/McpServerToolCallParams.json",
    "mcpResource/read": "v2/McpResourceReadParams.json",
}


class NativeRpcError(RuntimeError):
    def __init__(self, code: int, native_message: str = ""):
        self.code = code
        self.native_message = native_message
        super().__init__("native_catalog_rpc_error")


class CatalogClient:
    """One owned native CLI session, bounded JSON lines and schema-checked requests."""

    def __init__(self, command, *, root, work, env, overlay, schema_root, owned):
        self.deadline = time.monotonic() + 75
        self.schema_root = schema_root
        self.serial = 0
        self.buffer = b""
        self.closed = False
        self.selector = selectors.DefaultSelector()
        self.log = journey._open_private_log(work / "native.stderr.log")
        try:
            self.process = subprocess.Popen(
                journey._sandbox_command(command, root, work, overlay),
                cwd=work,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self.log,
                start_new_session=True,
                bufsize=0,
            )
        except BaseException:
            self.selector.close()
            self.log.close()
            raise
        owned.append(self)
        self.selector.register(self.process.stdout, selectors.EVENT_READ)

    def send(self, message):
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        self.process.stdin.flush()

    def rpc(self, method, params):
        if method not in SCHEMAS:
            raise ValueError("catalog_only_method_required")
        validate(params, json.loads((self.schema_root / SCHEMAS[method]).read_text()))
        self.serial += 1
        request_id = self.serial
        self.send({"id": request_id, "method": method, "params": params})
        while time.monotonic() < self.deadline:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("invalid_native_rpc_frame")
                if isinstance(message.get("id"), bool):
                    raise ValueError("invalid_native_rpc_id")
                if message.get("id") == request_id and "method" not in message:
                    if "error" in message:
                        code = message["error"].get("code")
                        if type(code) is not int:
                            raise ValueError("invalid_native_rpc_error")
                        native_message = message["error"].get("message")
                        if not isinstance(native_message, str) or len(native_message) > 4096:
                            raise ValueError("invalid_native_rpc_error_message")
                        raise NativeRpcError(code, native_message)
                    if "result" not in message:
                        raise ValueError("invalid_native_rpc_result")
                    return message["result"]
                if "method" in message and "id" in message:
                    self.send(
                        {
                            "id": message["id"],
                            "error": {
                                "code": -32601,
                                "message": "Unsupported request in catalog-only client",
                            },
                        }
                    )
            remaining = self.deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                continue
            data = os.read(self.process.stdout.fileno(), 65536)
            if not data:
                raise RuntimeError("native_catalog_stdout_closed")
            self.buffer += data
            if len(self.buffer) > 4 * 1024 * 1024:
                raise ValueError("native_catalog_frame_too_large")
        raise TimeoutError("native_catalog_case_deadline")

    def close(self):
        if self.closed:
            return
        if not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        stopped = journey._terminate_owned_process_group(self.process)
        self.selector.close()
        self.log.close()
        if not stopped:
            raise RuntimeError("native_catalog_cleanup_incomplete")
        self.process.stdout.close()
        self.closed = True


def native_overrides(provider_command: str, work: Path, binary: str) -> list[str]:
    """Keep production per-session TOML; remove exec-only arguments via its existing adapter."""
    execution = journey._codex_command(provider_command, work, "", executable_override=binary)
    overrides = [execution[i + 1] for i, token in enumerate(execution[:-1]) if token == "-c"]
    return [
        binary,
        "app-server",
        "--listen",
        "stdio://",
        *[v for value in overrides for v in ("-c", value)],
        "-c",
        "features.apps=false",
        "-c",
        "features.plugins=false",
        "-c",
        "features.shell_tool=false",
    ]


def _catalog(client, thread_id):
    result = client.rpc(
        "mcpServerStatus/list",
        {"threadId": thread_id, "serverName": SERVER, "detail": "full", "limit": 20},
    )
    if result.get("nextCursor") is not None or len(result.get("data", [])) != 1:
        raise ValueError("native_catalog_server_mismatch")
    server = result["data"][0]
    if (
        server["name"] != SERVER
        or server.get("toolsError") is not None
        or server.get("runtimeStatus") != "connected"
    ):
        raise ValueError("native_catalog_not_connected")
    tools = server["tools"]
    if not isinstance(tools, dict):
        raise ValueError("native_catalog_invalid_tools")
    return {tool["name"] for tool in tools.values()}, server


def _call(client, thread_id, tool, arguments):
    from cli_agent_orchestrator.mcp_server import server

    tools = asyncio.run(server.mcp.local_provider.list_tools())
    parameters = next(t.parameters for t in tools if t.name == tool)
    validate(arguments, parameters)
    return client.rpc(
        "mcpServer/tool/call",
        {"threadId": thread_id, "server": SERVER, "tool": tool, "arguments": arguments},
    )


def _denied_call(client, thread_id, tool, arguments):
    try:
        result = _call(client, thread_id, tool, arguments)
    except NativeRpcError as exc:
        # Codex 0.164 uses the generic internal-error code for disabled tools.
        # Never interpret that code alone as an authorization refusal.
        expected = f"tool '{tool}' is disabled for MCP server '{SERVER}'"
        if exc.code != -32603 or exc.native_message != expected:
            raise
        return "native_client"
    expected = f"Tool '{tool}' is unavailable in selected CAO tool-profile mode."
    if result.get("isError") is True and any(
        block.get("type") == "text" and expected in block.get("text", "")
        for block in result.get("content", [])
        if isinstance(block, dict)
    ):
        return "server_profile"
    return None


def _case(root, label, profile, enabled, overlay, schema_root, env, binary, owned):
    from cli_agent_orchestrator.constants import LOCAL_AGENT_STORE_DIR
    from cli_agent_orchestrator.providers.codex import CodexProvider
    from cli_agent_orchestrator.utils.mcp_readiness import has_ready_signal
    from cli_agent_orchestrator.utils.mcp_tool_policy import TOOL_PROFILES

    if not LOCAL_AGENT_STORE_DIR.resolve().is_relative_to(root.resolve()):
        raise RuntimeError("fresh_private_cao_process_required")
    case = root / "cases" / label
    case.mkdir(mode=0o700, parents=True)
    work = case / "work"
    work.mkdir(mode=0o700)
    terminal = hashlib.sha256(label.encode()).hexdigest()[:8]
    child_env = journey._mcp_child_environment(case, 1, terminal)
    ready = case / "ready.json"
    nonce = secrets.token_hex(16)
    child_env.update(
        CAO_MCP_READY_FILE=str(ready), CAO_MCP_READY_TOKEN=nonce, CAO_MCP_APPS_ENABLED="0"
    )
    entry = {"command": "cao-mcp-server", "env": child_env, "cao_tool_profile": profile}
    if enabled is not None:
        entry["enabled_tools"] = enabled
    name = "catalog-" + label
    metadata = {
        "name": name,
        "description": "Native catalog-only diagnostic.",
        "provider": "codex",
        "codexProfile": "catalog-safe",
        "model": journey.CODEX_INTERACTION_MODEL,
        "mcpServers": {SERVER: entry},
    }
    LOCAL_AGENT_STORE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = LOCAL_AGENT_STORE_DIR / (name + ".md")
    path.write_text(journey._profile_frontmatter(metadata))
    path.chmod(0o600)
    provider = CodexProvider(
        terminal, "catalog-private", "worker", name, allowed_tools=["@cao-mcp-server"]
    )
    command = native_overrides(provider._build_codex_command(), work, binary)
    memory = next(
        int(l.split()[1]) // 1024
        for l in Path("/proc/meminfo").read_text().splitlines()
        if l.startswith("MemAvailable:")
    )
    if memory < 6144:
        raise RuntimeError("native_catalog_memory_admission")
    client = CatalogClient(
        command,
        root=root,
        work=work,
        env=env,
        overlay=overlay,
        schema_root=schema_root,
        owned=owned,
    )
    try:
        client.rpc(
            "initialize",
            {
                "clientInfo": {"name": "caos_catalog_acceptance", "version": "1"},
                "capabilities": {"explicitGatewayOauth": True},
            },
        )
        client.send({"method": "initialized"})
        thread = client.rpc(
            "thread/start",
            {
                "model": journey.CODEX_INTERACTION_MODEL,
                "cwd": str(work),
                "ephemeral": True,
                "approvalPolicy": "never",
                "sandbox": "read-only",
            },
        )
        thread_id = thread["thread"]["id"]
        names, status = _catalog(client, thread_id)
        expected = (
            TOOL_PROFILES[profile]
            if enabled is None
            else TOOL_PROFILES[profile].intersection(enabled)
        )
        if names != expected or not has_ready_signal(ready, nonce):
            raise ValueError("native_catalog_or_discovery_mismatch")
        if status["resources"] or status["resourceTemplates"]:
            raise ValueError("native_catalog_resource_exposed")
        ask_args = {
            "operation_key": "catalog-" + terminal + "-operation",
            "questions": [
                {"kind": "text", "prompt": "Synthetic catalog-only question", "required": True}
            ],
            "wait_for_required": False,
        }
        if "cao_ask_user" in expected:
            ask = _call(client, thread_id, "cao_ask_user", ask_args)
            code = journey._safe_mcp_result_error_code(ask)
            if ask.get("isError") is True or code != "capability_unavailable":
                raise ValueError("native_catalog_allowed_handler_mismatch")
        elif not _denied_call(client, thread_id, "cao_ask_user", ask_args):
            raise ValueError("native_catalog_empty_filter_call_not_denied")
        denied = (
            "cao_answer_interaction"
            if "cao_wait_for_user_answers" in expected
            else "cao_wait_for_user_answers"
        )
        arguments = (
            {"group_id": "2e1fc625-a391-4bfd-a62d-4dddbcbf70a3", "wait_seconds": 0}
            if denied == "cao_wait_for_user_answers"
            else {
                "question_id": "2e1fc625-a391-4bfd-a62d-4dddbcbf70a3",
                "expected_version": 1,
                "response": {"kind": "text", "text": "Synthetic forbidden answer"},
                "idempotency_key": "catalog-denied-operation-0001",
            }
        )
        denial_layer = _denied_call(client, thread_id, denied, arguments)
        if not denial_layer:
            raise ValueError("native_catalog_direct_call_not_denied")
        return (
            client,
            thread_id,
            {
                "profile": profile,
                "filter": "full" if enabled is None else "empty" if not enabled else "partial",
                "catalog_count": len(names),
                "catalog_tools": sorted(names),
                "catalog_exact": True,
                "client_discovery_confirmed": True,
                "allowed_handler_reached_without_authority": "cao_ask_user" in expected,
                "missing_capability_refused": "cao_ask_user" in expected,
                "excluded_valid_call_denied": True,
                "denial_layer": denial_layer,
                "resources_hidden": True,
                "resource_templates_hidden": True,
                "actual_operation_authorized": False,
                "memory_mib": memory,
            },
        )
    except BaseException as exc:
        failure = {
            "schema": "caos.codex-catalog-case-failure.v1",
            "profile": profile,
            "filter": "full" if enabled is None else "empty" if not enabled else "partial",
            "error_type": type(exc).__name__,
            "native_error_code": exc.code if isinstance(exc, NativeRpcError) else None,
        }
        failure_path = case / "failure.json"
        if isinstance(exc, NativeRpcError):
            failure["native_error_message"] = exc.native_message
        failure_path.write_text(json.dumps(failure, indent=2) + "\n")
        failure_path.chmod(0o600)
        client.close()
        raise


def _run_cases(root: Path, admission_only: bool, owned):
    os.environ.update(
        CAO_HOME_DIR=str(root / "cao"),
        CAO_DATABASE_CONFIG_FILE=str(root / "cao" / "database-config.json"),
    )
    from cli_agent_orchestrator.utils.mcp_tool_policy import TOOL_PROFILES

    memory = next(
        int(l.split()[1]) // 1024
        for l in Path("/proc/meminfo").read_text().splitlines()
        if l.startswith("MemAvailable:")
    )
    if memory < 6144:
        raise RuntimeError("native_catalog_memory_admission")
    overlay = journey._prepare_codex_home_overlay(root)
    (root / "codex-home/config.toml").write_text("")
    env = journey._base_cli_env(root, "codex")
    work = root / "work"
    work.mkdir(mode=0o700)
    binary = str(journey._codex_executable())
    schema_root = root / "schemas"
    generation = subprocess.run(
        journey._sandbox_command(
            [binary, "app-server", "generate-json-schema", "--out", str(schema_root)],
            root,
            work,
            overlay,
        ),
        cwd=work,
        env=env,
        capture_output=True,
        timeout=30,
    )
    if generation.returncode:
        raise RuntimeError("native_catalog_schema_generation_failed")
    cases = []
    for profile in (["interaction"] if admission_only else TOOL_PROFILES):
        for label, enabled in (
            ("full", None),
            ("empty", []),
            ("partial", ["cao_ask_user", "assign"]),
        ):
            memory = next(
                int(l.split()[1]) // 1024
                for l in Path("/proc/meminfo").read_text().splitlines()
                if l.startswith("MemAvailable:")
            )
            if memory < 6144:
                raise RuntimeError("native_catalog_memory_admission")
            progress = root / "catalog-progress.json"
            progress.write_text(
                json.dumps(
                    {
                        "schema": "caos.codex-catalog-progress.v1",
                        "completed": cases,
                        "active_profile": profile,
                        "active_filter": label,
                    },
                    indent=2,
                )
                + "\n"
            )
            progress.chmod(0o600)
            client, _, result = _case(
                root,
                profile + "-" + label,
                profile,
                enabled,
                overlay,
                schema_root,
                env,
                binary,
                owned,
            )
            try:
                cases.append(result)
            finally:
                client.close()
    isolation = None
    if not admission_only:
        first, first_thread, _ = _case(
            root,
            "isolated-interaction",
            "interaction",
            None,
            overlay,
            schema_root,
            env,
            binary,
            owned,
        )
        second = None
        try:
            second, second_thread, _ = _case(
                root,
                "isolated-coordination",
                "coordination",
                None,
                overlay,
                schema_root,
                env,
                binary,
                owned,
            )
            first_names, _ = _catalog(first, first_thread)
            second_names, _ = _catalog(second, second_thread)
            if (
                first.process.pid == second.process.pid
                or first_names != TOOL_PROFILES["interaction"]
                or second_names != TOOL_PROFILES["coordination"]
            ):
                raise ValueError("native_catalog_agent_isolation_failed")
            isolation = {
                "agents": 2,
                "catalog_counts": [len(first_names), len(second_names)],
                "first_catalog_unchanged_after_second_started": True,
                "separate_owned_processes": True,
            }
        finally:
            if second is not None:
                second.close()
            first.close()
    return {
        "schema": "caos.codex-native-catalog.v1",
        "cases": cases,
        "case_count": len(cases),
        "model_turn_started": False,
        "actual_operation_authorized": False,
        "agent_isolation": isolation,
        "scope": "Native Codex app-server per-CAO-server catalog and direct calls; apps/plugins/shell disabled for diagnostic containment; missing capability denies allowed-handler operations; no model turn or authorized backend effects",
    }


def run(root: Path, *, admission_only: bool = False):
    root = root.absolute()
    if root.is_symlink() or not root.is_dir() or root.stat().st_mode & 0o777 != 0o700:
        raise ValueError("private_catalog_root_required")
    if (root / "codex-home").exists() or (root / "cases").exists():
        raise ValueError("fresh_catalog_root_required")
    auth = journey._codex_auth_path()
    identity = journey._source_file_identity(auth, require_private=True)
    auth_sha = hashlib.sha256(auth.read_bytes()).hexdigest()
    owned = []
    result = None
    try:
        result = _run_cases(root, admission_only, owned)
        return result
    finally:
        incomplete = False
        for client in reversed(owned):
            try:
                client.close()
            except Exception:
                incomplete = True
        (root / "codex-home/auth.json").unlink(missing_ok=True)
        if (
            journey._source_file_identity(auth, require_private=True) != identity
            or hashlib.sha256(auth.read_bytes()).hexdigest() != auth_sha
        ):
            raise RuntimeError("native_catalog_operator_auth_changed")
        if incomplete:
            raise RuntimeError("native_catalog_cleanup_incomplete")
        if result is not None:
            result.update(
                owned_process_groups_closed=True,
                operator_auth_preserved=True,
                private_auth_removed=True,
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-native", action="store_true")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--admission-only", action="store_true")
    args = parser.parse_args()
    if not args.run_native or args.root is None:
        parser.error("explicit --run-native --root required")
    print(json.dumps(run(args.root, admission_only=args.admission_only), sort_keys=True))


if __name__ == "__main__":
    main()
