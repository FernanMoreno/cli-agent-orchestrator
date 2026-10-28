"""Canonical MCP process, real SDK seal and exact production pre-stop utility."""

import json
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path
from test.live_provider.message_api_restart import audit_completion_seal_valid
from test.mcp_server.test_physical_eof_sdk import Peer

import pytest

from cli_agent_orchestrator.utils.mcp_owner_stop import (
    OWNER_DIR_ENV,
    SIGNAL_STOP_ENV,
    PrestopOutcome,
    prestop_owned_mcp,
    reconcile_owned_mcp,
)

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize("separate_child_home", [False, True])
def test_production_owner_stops_canonical_mcp_before_parent_closes_input(
    tmp_path, separate_child_home
):
    home = tmp_path / "api-home"
    home.mkdir(mode=0o700)
    (home / "tmp").mkdir(mode=0o700)
    child_home = tmp_path / "mcp-home" if separate_child_home else home
    if separate_child_home:
        child_home.mkdir(mode=0o700)
    terminal_id = "owned-sdk"
    repo = Path(__file__).resolve().parents[2]
    audit_path = tmp_path / "mcp-audit.jsonl"
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": str(child_home),
        "CAO_HOME_DIR": str(child_home),
        "CAO_TERMINAL_ID": terminal_id,
        "CAO_MCP_READY_FILE": str(home / "tmp" / "sdk.ready"),
        "CAO_MCP_READY_TOKEN": "owned-sdk-control",
        OWNER_DIR_ENV: str(home / "tmp" / f"{terminal_id}.mcp-owners"),
        SIGNAL_STOP_ENV: "1",
        "CAO_INTERNAL_MCP_TOOL_PROFILE": "interaction",
        "CAO_SPEC023_MCP_OBSERVER": "1",
        "CAO_SPEC023_SOURCE_ROOT": str(repo),
        "CAO_SPEC023_MCP_AUDIT_PATH": str(audit_path),
        "PYTHONPATH": os.pathsep.join([str(repo / "test/live_provider"), str(repo / "src")]),
        "PYTHONDONTWRITEBYTECODE": "1",
        "FASTMCP_LOG_LEVEL": "ERROR",
    }
    args = ["-m", "cli_agent_orchestrator.mcp_server.server"]
    config = home / "tmp" / f"{terminal_id}.mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "cao-mcp-server": {"command": sys.executable, "args": args, "env": env}
                }
            }
        )
    )
    config.chmod(0o600)
    peer = object.__new__(Peer)
    peer.log = (tmp_path / "stderr.log").open("wb")
    peer.process = subprocess.Popen(
        [sys.executable, *args],
        cwd=repo,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=peer.log,
        start_new_session=True,
        bufsize=0,
    )
    peer.selector = selectors.DefaultSelector()
    peer.selector.register(peer.process.stdout, selectors.EVENT_READ)
    peer.buffer = b""
    try:
        peer.initialize()
        assert peer.process.stdin.closed is False
        assert list(Path(env[OWNER_DIR_ENV]).glob("*.json"))
        started = time.monotonic()
        deadline = started + 6.0
        result = prestop_owned_mcp(terminal_id, deadline, home=home)
        assert result.outcome is PrestopOutcome.RETIRED_NORMAL
        assert (
            result.identity_count
            == result.signaled_count
            == result.retired_count
            == result.normal_count
            == 1
        )
        assert result.unknown_count == result.still_live_count == 0
        assert peer.process.stdin.closed is False
        # This direct child wait establishes actual process0, independently of
        # the normal SDK witness and pidfd retirement observed by the utility.
        assert peer.process.wait(timeout=max(0.001, deadline - time.monotonic())) == 0
        assert time.monotonic() < deadline
        assert audit_completion_seal_valid(audit_path)
        assert audit_path.read_bytes() == b""  # Catalog only; no tool dispatched.
        assert reconcile_owned_mcp(terminal_id, home=home) is True
        assert not list(Path(env[OWNER_DIR_ENV]).iterdir())
    finally:
        peer.close()
