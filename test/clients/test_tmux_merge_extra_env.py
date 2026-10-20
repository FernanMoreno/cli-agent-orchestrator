"""Tests for TmuxClient._merge_extra_env (issue #248).

Server-side defensive validation that mirrors the CLI's --env parser. CLI
already rejects bad entries up front; this layer is the safety net for
callers that bypass the CLI (cao-mcp-server, direct HTTP).
"""

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from cli_agent_orchestrator.clients.tmux import TmuxClient


def test_merge_with_none_is_noop():
    env = {"HOME": "/home/u"}
    TmuxClient._merge_extra_env(env, None)
    assert env == {"HOME": "/home/u"}


def test_merge_with_empty_dict_is_noop():
    env = {"HOME": "/home/u"}
    TmuxClient._merge_extra_env(env, {})
    assert env == {"HOME": "/home/u"}


def test_merge_adds_user_supplied_keys():
    env = {"HOME": "/home/u"}
    TmuxClient._merge_extra_env(env, {"MNEMOSYNE_DIR": "/root/mn", "X": "y"})
    assert env["MNEMOSYNE_DIR"] == "/root/mn"
    assert env["X"] == "y"


def test_merge_overrides_inherited_value():
    """An explicit --env entry wins over the inherited value on key collision."""
    env = {"AWS_REGION": "us-east-1"}
    TmuxClient._merge_extra_env(env, {"AWS_REGION": "us-west-2"})
    assert env["AWS_REGION"] == "us-west-2"


def test_merge_drops_blocked_prefix(caplog):
    env: dict[str, str] = {}
    TmuxClient._merge_extra_env(env, {"CLAUDE_SECRET": "x", "OK": "y"})
    assert "CLAUDE_SECRET" not in env
    assert env["OK"] == "y"


def test_merge_keeps_allowlisted_claude_auth_var():
    env: dict[str, str] = {}
    TmuxClient._merge_extra_env(
        env,
        {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "CLAUDE_CODE_OAUTH_TOKEN": "test-oauth-token",
        },
    )
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "test-oauth-token"


def test_merge_drops_value_at_or_above_cap():
    env: dict[str, str] = {}
    TmuxClient._merge_extra_env(env, {"BIG": "x" * 2048, "SMALL": "x" * 2047})
    assert "BIG" not in env
    assert env["SMALL"] == "x" * 2047


def test_is_blocked_env_key_classification():
    assert TmuxClient._is_blocked_env_key("CLAUDE_SESSION_ID") is True
    assert TmuxClient._is_blocked_env_key("CODEX_TOKEN") is True
    assert TmuxClient._is_blocked_env_key("__MISE_WATCH") is True
    # Allowlist matches the full key, not a prefix — only the exact entries
    # in _BLOCKED_PREFIX_ALLOWLIST are exempted from the blocked prefixes.
    assert TmuxClient._is_blocked_env_key("CLAUDE_CODE_USE_BEDROCK") is False
    assert TmuxClient._is_blocked_env_key("CLAUDE_CODE_SKIP_FOUNDRY_AUTH") is False
    assert TmuxClient._is_blocked_env_key("CLAUDE_CODE_OAUTH_TOKEN") is False
    # Unrelated keys aren't blocked.
    assert TmuxClient._is_blocked_env_key("AWS_REGION") is False
    assert TmuxClient._is_blocked_env_key("MNEMOSYNE_DIR") is False


@pytest.mark.parametrize(
    "key",
    [
        "CAO_DATABASE_CONFIG_FILE",
        "CAO_DATABASE_RUNTIME_SECRET_FILE",
        "CAO_DATABASE_MIGRATION_SECRET_FILE",
        "CAO_POSTGRES_ADMIN_SECRET_FILE",
        "CAO_MAIN_DATABASE_SECRET_FILE",
        "CAO_AUTH_DATABASE_SECRET_FILE",
        "CAO_PEER_DATABASE_SECRET_FILE",
    ],
)
def test_database_configuration_and_secret_vars_never_reach_pane_env_or_extra_env(key):
    assert TmuxClient._is_blocked_env_key(key) is True
    env: dict[str, str] = {}
    TmuxClient._merge_extra_env(env, {key: "/operator-supplied-value"})
    assert key not in env


def test_tmux_subprocess_boundary_filters_inherited_and_explicit_database_env(monkeypatch):
    from cli_agent_orchestrator.clients.tmux_transport import run_tmux

    inherited = {
        "PATH": "/usr/bin",
        "CAO_DATABASE_CONFIG_FILE": "/private/config.json",
        "CAO_POSTGRES_ADMIN_SECRET_FILE": "/private/admin.json",
    }
    monkeypatch.setenv("CAO_DATABASE_CONFIG_FILE", inherited["CAO_DATABASE_CONFIG_FILE"])
    monkeypatch.setenv(
        "CAO_POSTGRES_ADMIN_SECRET_FILE", inherited["CAO_POSTGRES_ADMIN_SECRET_FILE"]
    )
    captured: list[dict[str, str]] = []

    def run(argv, **kwargs):
        captured.append(kwargs["env"])
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with patch("cli_agent_orchestrator.clients.tmux_transport.subprocess.run", side_effect=run):
        run_tmux(["tmux", "list-sessions"])
        run_tmux(["tmux", "list-sessions"], env=inherited)

    assert len(captured) == 2
    for environment in captured:
        assert "CAO_DATABASE_CONFIG_FILE" not in environment
        assert "CAO_POSTGRES_ADMIN_SECRET_FILE" not in environment
    assert inherited["CAO_DATABASE_CONFIG_FILE"] == "/private/config.json"


@pytest.mark.skipif(shutil.which("tmux") is None, reason="requires a local tmux binary")
def test_tmux_panes_do_not_inherit_database_secrets_from_server_global_environment():
    """CAO masks DB vars from its sessions without changing tmux global env."""
    if not Path("/proc/self/environ").exists():
        pytest.skip("reads Linux process environments")

    from cli_agent_orchestrator.clients.tmux_transport import BoundedTmuxServer
    from cli_agent_orchestrator.utils.database_environment import without_database_environment

    socket_path = Path("/var/tmp") / f"cao-env-{uuid.uuid4().hex[:10]}.sock"
    session_name = f"cao-env-{uuid.uuid4().hex[:8]}"
    reserved_environment = {
        "CAO_DATABASE_CONFIG_FILE": "/private/fake-config.json",
        "CAO_DATABASE_RUNTIME_SECRET_FILE": "/private/fake-runtime.json",
        "CAO_POSTGRES_ADMIN_SECRET_FILE": "/private/fake-admin.json",
        "CAO_MAIN_DATABASE_SECRET_FILE": "/private/fake-main.json",
        "CAO_AUTH_DATABASE_SECRET_FILE": "/private/fake-auth.json",
        "CAO_PEER_DATABASE_SECRET_FILE": "/private/fake-peer.json",
        "PGPASSWORD": "private-fake-password",
        "PGPASSFILE": "/private/fake.pgpass",
        "PGSERVICE": "private-fake-service",
        "PGSERVICEFILE": "/private/fake-service.conf",
        "PGSSLKEY": "/private/fake.key",
        "PGSSLCERT": "/private/fake.crt",
    }
    seed_environment = os.environ.copy()
    seed_environment.pop("TMUX", None)
    seed_environment.update(
        {
            "CLAUDE_CODE_USE_BEDROCK": "1",
        }
    )
    seed_environment.update(reserved_environment)
    client = TmuxClient()
    client.server = BoundedTmuxServer(socket_path=str(socket_path))

    def read_pane_environment(target: str) -> dict[bytes, bytes]:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = client.server.cmd(
                "display-message",
                "-p",
                "-t",
                target,
                "#{pane_pid}\t#{pane_current_command}",
            )
            if result.returncode == 0 and result.stdout:
                pane_pid, command = result.stdout[0].strip().split("\t", 1)
                if command not in {"sleep", "tmux"}:
                    try:
                        entries = Path(f"/proc/{pane_pid}/environ").read_bytes().split(b"\0")
                    except FileNotFoundError:
                        time.sleep(0.02)
                        continue
                    return {
                        entry.split(b"=", 1)[0]: entry.split(b"=", 1)[1]
                        for entry in entries
                        if b"=" in entry
                    }
            time.sleep(0.02)
        pytest.fail("CAO pane process did not settle within the bounded test window")

    try:
        subprocess.run(
            ["tmux", "-S", str(socket_path), "new-session", "-d", "-s", "seed", "sleep 600"],
            env=seed_environment,
            capture_output=True,
            check=True,
            timeout=5,
        )
        client.create_session(
            session_name,
            "worker",
            "env-probe-root",
            working_directory=os.getcwd(),
            extra_env={
                "CAO_DATABASE_CONFIG_FILE": "/private/operator-config.json",
                "PGPASSWORD": "private-operator-password",
            },
        )
        client.reset_window(session_name, "worker", terminal_id="env-probe-root")
        client.create_window(
            session_name,
            "child-window",
            "env-probe-window",
            working_directory=os.getcwd(),
            extra_env={
                "CAO_DATABASE_CONFIG_FILE": "/private/operator-config.json",
                "PGPASSWORD": "private-operator-password",
            },
        )
        client.create_pane(
            session_name,
            "worker",
            "child-pane",
            "env-probe-pane",
            working_directory=os.getcwd(),
            extra_env={
                "CAO_DATABASE_CONFIG_FILE": "/private/operator-config.json",
                "PGPASSWORD": "private-operator-password",
            },
        )

        for target in (
            f"{session_name}:worker.0",
            f"{session_name}:child-window.0",
            f"{session_name}:worker.1",
        ):
            environment = read_pane_environment(target)
            assert all(name.encode() not in environment for name in reserved_environment)
            assert environment[b"CLAUDE_CODE_USE_BEDROCK"] == b"1"

        for name, secret in reserved_environment.items():
            global_value = client.server.cmd("show-environment", "-g", name)
            assert global_value.returncode == 0
            assert global_value.stdout == [f"{name}={secret}"]
    finally:
        subprocess.run(
            ["tmux", "-S", str(socket_path), "kill-server"],
            env=without_database_environment(os.environ),
            capture_output=True,
            check=False,
            timeout=5,
        )
