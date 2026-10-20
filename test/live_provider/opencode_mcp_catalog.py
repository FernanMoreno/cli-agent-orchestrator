"""Opt-in, no-model OpenCode v2 MCP catalog startup check.

This starts the installed OpenCode CLI with CAO's production v2 config writer,
production launch command builder, and bundled CAO MCP stdio server. It does not
configure a model or submit a task. It verifies only that the CLI completes the
MCP catalog handshake; the configured local agent scope is reported separately
and this is not a Work authorization test.

Run with:

    CAO_OPENCODE_TEST_DATABASE_CONFIG_FILE=/path/to/disposable/database-config.json \
        uv run python test/live_provider/opencode_mcp_catalog.py

The process is bounded to 80 seconds. Temporary config, capability, screen, and
startup logs live in a private temporary directory and are removed at exit.
The dedicated PostgreSQL config must point to a disposable, already migrated
test store; this probe writes one terminal and interaction source there. The
current HOME is inherited unchanged, while XDG and OpenCode config/data paths
are redirected into the temporary directory. Provider credentials are not
copied or inherited into the child environment.

The native timeout shape is based on OpenCode v2.0.18's primary schema:
https://github.com/anomalyco/opencode/blob/v2.0.18/packages/schema/src/mcp.ts
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import tty
from pathlib import Path
from typing import Any

STARTUP_LIMIT_SECONDS = 80.0
NATIVE_MCP_TIMEOUT = {"startup": 60_000, "catalog": 60_000, "execution": 65_000}
AGENT_TOOL_SCOPE = (
    "cao-mcp-server_cao_ask_user",
    "cao-mcp-server_cao_wait_for_user_answers",
    "cao-mcp-server_cao_acknowledge_user_answers",
)


def _temporary_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="cao-opencode-catalog-"))
    root.chmod(0o700)
    return root


def _version_text(value: str) -> str:
    return next((line.strip()[:100] for line in value.splitlines() if line.strip()), "unknown")


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.wait(timeout=3)


def _read_log(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def run() -> dict[str, Any]:
    root = _temporary_root()
    original_home = Path.home()
    global_config = original_home / ".config/opencode/opencode.json"
    try:
        before_global_config = global_config.read_bytes() if global_config.is_file() else None
    except OSError:
        before_global_config = None

    process: subprocess.Popen[bytes] | None = None
    master_fd: int | None = None
    reader_thread: threading.Thread | None = None
    provider = None
    output_file = None
    result: dict[str, Any] = {
        "provider": "opencode_cli",
        "version": "unknown",
        "result": "setup_error",
        "mcp_connected": False,
        "server_catalog_tool_count": 0,
        "normalization_diagnostics": 0,
        "model_configured": False,
        "model_requests": 0,
        "task_submitted": False,
        "agent_tool_scope": list(AGENT_TOOL_SCOPE),
        "agent_tool_scope_exact": True,
        "work_grant_used": False,
        "home_unchanged": str(Path.home()) == str(original_home),
        "global_config_modified": False,
    }

    try:
        database_config_file = os.environ.get("CAO_OPENCODE_TEST_DATABASE_CONFIG_FILE", "").strip()
        if not database_config_file:
            result.update(result="postgres_test_database_required")
            return result
        database_config_path = Path(database_config_file).expanduser().resolve(strict=True)
        os.environ["CAO_DATABASE_CONFIG_FILE"] = str(database_config_path)

        # Use a fresh CAO home before importing modules whose path constants are
        # bound at import time. No operator profile or default database is read.
        cao_home = root / "cao-home"
        workdir = root / "work"
        cao_home.mkdir(mode=0o700)
        workdir.mkdir(mode=0o700)
        os.environ.update(
            {
                "CAO_HOME_DIR": str(cao_home),
                "CAO_API_HOST": "127.0.0.1",
                "CAO_AUTH_MODE": "off",
                "PYTHONPATH": str(Path.cwd() / "src"),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )

        from cli_agent_orchestrator.clients import database
        from cli_agent_orchestrator.clients.interaction_repository import InteractionRepository
        from cli_agent_orchestrator.clients.postgres import DatabaseTarget
        from cli_agent_orchestrator.providers.mcp_interactions import (
            CAO_INTERACTION_CAPABILITY_FILE,
            overlay_interaction_capability_file,
        )
        from cli_agent_orchestrator.providers.opencode_cli import (
            OpenCodeCliProvider,
            detect_opencode_major,
            resolve_opencode_runtime,
        )
        from cli_agent_orchestrator.services import terminal_service
        from cli_agent_orchestrator.services.interactions.capability import (
            canonical_tool_fingerprint,
        )
        from cli_agent_orchestrator.services.interactions.service import InteractionService
        from cli_agent_orchestrator.utils.opencode_v2 import write_v2_configuration

        terminal_id = "catalog-" + os.urandom(6).hex()
        incarnation_id = "catalog-inc-" + os.urandom(8).hex()
        session_name = "cao-" + os.urandom(6).hex()
        profile_name = "interaction-catalog"
        allowed_tools = ["@cao-mcp-server"]

        database_target = DatabaseTarget.from_settings(home=cao_home)
        database.init_db()
        database.create_terminal(
            terminal_id,
            session_name,
            "window-0",
            "opencode_cli",
            agent_profile=profile_name,
            allowed_tools=allowed_tools,
            working_directory=str(workdir),
            session_incarnation_id=incarnation_id,
            new_session_incarnation=True,
        )
        interaction_service = InteractionService(InteractionRepository(database_target))
        interaction_service.initialize()

        provider = OpenCodeCliProvider(
            terminal_id,
            session_name,
            "window-0",
            profile_name,
            allowed_tools=allowed_tools,
        )
        executable, runtime_environment = resolve_opencode_runtime()
        provider._launch_runtime = (executable, runtime_environment)
        provider._cli_major = detect_opencode_major(provider._launch_runtime)
        if provider._cli_major != 2:
            result.update(result="unsupported_cli_version", version="not_v2")
            return result

        version_command = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
            env=runtime_environment,
        )
        result["version"] = _version_text(version_command.stdout or version_command.stderr)

        issued = terminal_service._install_interaction_source_capability(
            provider,
            interaction_service,
            terminal_id=terminal_id,
            incarnation_id=incarnation_id,
            session_name=session_name,
            provider="opencode_cli",
            provider_version=result["version"],
            execution_mode="local",
            profile_revision="no-model-catalog-probe",
            allowed_tool_fingerprint=canonical_tool_fingerprint(allowed_tools)[0],
            principal=None,
        )
        if not issued or not provider.interaction_capability_file:
            result.update(result="capability_setup_failed")
            return result

        api_socket = socket.socket()
        try:
            api_socket.bind(("127.0.0.1", 0))
            api_port = int(api_socket.getsockname()[1])
        finally:
            api_socket.close()

        source_environment = {
            "CAO_HOME_DIR": str(cao_home),
            "CAO_DATABASE_CONFIG_FILE": str(database_config_path),
            "CAO_API_HOST": "127.0.0.1",
            "CAO_API_PORT": str(api_port),
            "CAO_TERMINAL_ID": terminal_id,
        }
        source_server = {
            "type": "local",
            "command": [sys.executable, "-m", "cli_agent_orchestrator.mcp_server.server"],
            "environment": source_environment,
            "disabled": False,
            "codemode": False,
            "timeout": NATIVE_MCP_TIMEOUT,
        }
        servers = overlay_interaction_capability_file(
            {"cao-mcp-server": source_server}, provider.interaction_capability_file
        )
        if CAO_INTERACTION_CAPABILITY_FILE not in servers["cao-mcp-server"].get("environment", {}):
            result.update(result="capability_overlay_failed")
            return result

        agents_dir = root / "agents"
        agents_dir.mkdir(mode=0o700)
        permission_lines = "".join(f"  {tool}: true\n" for tool in AGENT_TOOL_SCOPE)
        agent_text = (
            "---\n"
            "description: isolated no-model MCP catalog probe\n"
            "mode: primary\n"
            "permission:\n"
            '  "*": false\n'
            f"{permission_lines}"
            "---\n"
            "Catalog startup probe only. No model task is submitted.\n"
        )
        agent_path = agents_dir / f"{profile_name}.md"
        agent_path.write_text(agent_text, encoding="utf-8")
        agent_path.chmod(0o600)

        native_source_config = {
            "mcp": {"timeout": NATIVE_MCP_TIMEOUT, "servers": servers},
        }
        config_dir = root / "opencode-v2"
        config_dir.mkdir(mode=0o700)
        write_v2_configuration(
            config_dir,
            source_agents=agents_dir,
            source_config=native_source_config,
            session_id=terminal_id,
            selected_model=None,
        )
        config_path = config_dir / "opencode.json"
        generated_config = json.loads(config_path.read_text(encoding="utf-8"))
        generated_server = generated_config["mcp"]["servers"]["cao-mcp-server"]
        if generated_server.get("timeout") != NATIVE_MCP_TIMEOUT:
            result.update(result="native_timeout_config_mismatch")
            return result
        generated_scope = tuple(
            rule["action"]
            for rule in generated_config["agents"][profile_name]["permissions"]
            if rule["effect"] == "allow"
        )
        if set(generated_scope) != set(AGENT_TOOL_SCOPE):
            result.update(result="agent_tool_scope_mismatch")
            return result

        data_home = config_dir / "data"
        (data_home / "opencode").mkdir(mode=0o700, parents=True)
        startup_log = data_home / "mcp-startup.log"
        startup_log.touch(mode=0o600)
        provider._owned_v2_config_dir = config_dir
        provider._v2_config_dir = config_dir
        provider._v2_data_home = data_home
        provider._v2_cao_mcp_server_name = "cao-mcp-server"

        private_xdg_config = root / "xdg-config"
        private_xdg_data = root / "xdg-data"
        private_xdg_cache = root / "xdg-cache"
        private_xdg_state = root / "xdg-state"
        private_tmp = root / "tmp"
        for path in (
            private_xdg_config,
            private_xdg_data,
            private_xdg_cache,
            private_xdg_state,
            private_tmp,
        ):
            path.mkdir(mode=0o700)

        # An allowlist avoids forwarding any provider or cloud credentials.
        child_environment = {
            "HOME": str(original_home),
            "PATH": runtime_environment["PATH"],
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "TERM": "xterm-256color",
            "PYTHONPATH": str(Path.cwd() / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "XDG_CONFIG_HOME": str(private_xdg_config),
            "XDG_DATA_HOME": str(private_xdg_data),
            "XDG_CACHE_HOME": str(private_xdg_cache),
            "XDG_STATE_HOME": str(private_xdg_state),
            "TMPDIR": str(private_tmp),
            "CAO_HOME_DIR": str(cao_home),
            "CAO_DATABASE_CONFIG_FILE": str(database_config_path),
            "CAO_API_HOST": "127.0.0.1",
            "CAO_API_PORT": str(api_port),
            "CAO_AUTH_MODE": "off",
        }

        provider._v2_config_dir = config_dir
        command = provider._build_launch_command()
        master_fd, slave_fd = pty.openpty()
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        tty.setraw(slave_fd)
        output_path = root / "screen.bin"
        output_file = output_path.open("wb")
        process = subprocess.Popen(
            ["/bin/bash", "-c", command],
            cwd=workdir,
            env=child_environment,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave_fd)

        def drain_screen() -> None:
            assert master_fd is not None and output_file is not None
            while True:
                try:
                    chunk = os.read(master_fd, 8192)
                except OSError:
                    break
                if not chunk:
                    break
                output_file.write(chunk)
                output_file.flush()

        reader_thread = threading.Thread(target=drain_screen, daemon=True)
        reader_thread.start()

        deadline = time.monotonic() + STARTUP_LIMIT_SECONDS
        log_contents = ""
        connected = re.compile(
            r'message="mcp connected"\s+server=cao-mcp-server(?:\s+tools=(\d+))?'
        )
        while time.monotonic() < deadline and process.poll() is None:
            log_contents = _read_log(startup_log)
            match = connected.search(log_contents)
            if match:
                result["mcp_connected"] = True
                result["server_catalog_tool_count"] = int(match.group(1) or 0)
                break
            time.sleep(0.1)

        result["normalization_diagnostics"] = log_contents.count(
            "configuration normalization diagnostic"
        )
        result["result"] = (
            "mcp_catalog_connected"
            if result["mcp_connected"]
            else (
                "cli_exited_without_mcp"
                if process.poll() is not None
                else "mcp_catalog_startup_timeout"
            )
        )
        result["native_timeout_shape"] = "v2_positive_integer_milliseconds"
        result["private_config_mode"] = oct(config_dir.stat().st_mode & 0o777)
        result["private_capability_mode"] = oct(
            Path(provider.interaction_capability_file).stat().st_mode & 0o777
        )
    except FileNotFoundError:
        result.update(result="opencode_cli_unavailable")
    except Exception as exc:
        # Only emit the exception type; provider and MCP output can include
        # operator-specific paths or configuration and is not suitable here.
        result.update(result="setup_error", error_type=type(exc).__name__)
    finally:
        if process is not None:
            _terminate(process)
        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError:
                pass
        if reader_thread is not None:
            reader_thread.join(timeout=2)
        if output_file is not None:
            output_file.close()
        if provider is not None:
            provider.cleanup()
        shutil.rmtree(root, ignore_errors=True)
        try:
            after_global_config = global_config.read_bytes() if global_config.is_file() else None
            result["global_config_modified"] = after_global_config != before_global_config
        except OSError:
            result["global_config_modified"] = True

    return result


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
