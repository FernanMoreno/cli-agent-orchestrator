"""Native client discovery must precede native-v1 initialization success."""

import copy
import json
import sys
from pathlib import Path
from test.providers.test_opencode_profile_initialization import launch
from test.providers.test_opencode_v1_managed_policy import prepare
from unittest.mock import AsyncMock

import pytest

from cli_agent_orchestrator.providers import opencode_cli
from cli_agent_orchestrator.utils import opencode_v1
from cli_agent_orchestrator.utils.mcp_readiness import (
    READY_FILE_ENV,
    READY_TOKEN_ENV,
    publish_ready_signal,
)

pytestmark = pytest.mark.offline_database


def native_file(tmp_path, extra=None):
    servers = {
        "cao": {
            "type": "local",
            "command": [sys.executable, "-m", "cli_agent_orchestrator.mcp_server.server"],
            "environment": {READY_FILE_ENV: "/forged", READY_TOKEN_ENV: "forged"},
        },
        "foreign": {
            "type": "local",
            "command": ["other"],
            "environment": {READY_FILE_ENV: "/forged", READY_TOKEN_ENV: "forged"},
        },
    }
    if extra:
        servers.update(extra)
    (tmp_path / "opencode.json").write_text(json.dumps({"mcp": servers}))
    return servers


def test_owned_signals_only_on_active_canonical_children(tmp_path):
    native_file(
        tmp_path,
        {
            "disabled": {
                "type": "local",
                "command": ["cao-mcp-server"],
                "enabled": False,
                "environment": {READY_FILE_ENV: "/forged", READY_TOKEN_ENV: "forged"},
            }
        },
    )
    signals = opencode_v1.prepare_v1_mcp_readiness(tmp_path)
    config = json.loads((tmp_path / "opencode.json").read_text())
    assert len(signals) == 1
    path, token = next(iter(signals.items()))
    assert path.parent == tmp_path / "mcp-ready" and not path.exists()
    assert len(token) >= 32 and token != "forged"
    assert config["mcp"]["cao"]["environment"] == {
        READY_FILE_ENV: str(path),
        READY_TOKEN_ENV: token,
    }
    for name in ["foreign", "disabled"]:
        assert config["mcp"][name]["environment"] == {}
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_bare_canonical_is_pinned_and_launch_tokens_are_distinct(tmp_path):
    tokens = []
    for name in ["a", "b"]:
        root = tmp_path / name
        root.mkdir(mode=0o700)
        native_file(root, {"cao": {"type": "local", "command": ["cao-mcp-server"]}})
        signals = opencode_v1.prepare_v1_mcp_readiness(root)
        tokens.append(next(iter(signals.values())))
        config = json.loads((root / "opencode.json").read_text())
        assert config["mcp"]["cao"]["command"] == [
            sys.executable,
            "-m",
            "cli_agent_orchestrator.mcp_server.server",
        ]
    assert tokens[0] != tokens[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("published", [None, "stale", "current"])
async def test_wait_requires_current_launch_signal(tmp_path, published):
    instance = opencode_cli.OpenCodeCliProvider("abcdef12", "private", "private", "reviewer")
    path = tmp_path / "ready.json"
    instance._v1_mcp_ready_signals = {path: "current"}
    if published:
        publish_ready_signal(path, published)
    assert await instance._wait_for_v1_cao_mcp_ready(timeout=0.01) is (published == "current")


@pytest.mark.asyncio
async def test_wait_requires_every_canonical_child(tmp_path):
    instance = opencode_cli.OpenCodeCliProvider("abcdef12", "private", "private", "reviewer")
    a, b = tmp_path / "a", tmp_path / "b"
    instance._v1_mcp_ready_signals = {a: "first", b: "second"}
    publish_ready_signal(a, "first")
    assert not await instance._wait_for_v1_cao_mcp_ready(timeout=0.01)
    publish_ready_signal(b, "second")
    assert await instance._wait_for_v1_cao_mcp_ready(timeout=0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [True, False])
async def test_production_initialize_waits_and_cleans_owned_signals(launch, monkeypatch, ready):
    instance, backend, home = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    config, path, _ = prepare(monkeypatch)
    original = copy.deepcopy(config), path.read_bytes()
    waiter = AsyncMock(return_value=ready)
    monkeypatch.setattr(instance, "_wait_for_v1_cao_mcp_ready", waiter)
    if ready:
        assert await instance.initialize()
        assert instance._v1_mcp_ready_signals
        private = instance._v1_config_dir
        instance.cleanup()
        assert not private.exists()
    else:
        with pytest.raises(TimeoutError, match="v1.*MCP"):
            await instance.initialize()
        assert instance._owned_v1_config_dir is None
    waiter.assert_awaited_once()
    backend.send_keys.assert_called_once()
    assert instance._v1_mcp_ready_signals == {}
    assert (config, path.read_bytes()) == original


@pytest.mark.asyncio
async def test_discovery_cancellation_cleans_launch(launch, monkeypatch):
    import asyncio

    instance, _, _ = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    prepare(monkeypatch)
    monkeypatch.setattr(
        instance, "_wait_for_v1_cao_mcp_ready", AsyncMock(side_effect=asyncio.CancelledError())
    )
    with pytest.raises(asyncio.CancelledError):
        await instance.initialize()
    assert instance._owned_v1_config_dir is None
    assert instance._v1_mcp_ready_signals == {}


@pytest.mark.parametrize("unsafe", ["symlink", "public"])
def test_private_signal_directory_required(tmp_path, unsafe):
    directory = tmp_path / "native"
    actual = tmp_path / "actual"
    actual.mkdir(mode=0o700)
    native_file(actual)
    if unsafe == "symlink":
        directory.symlink_to(actual, target_is_directory=True)
    else:
        directory = actual
        directory.chmod(0o755)
    before = (actual / "opencode.json").read_bytes()
    with pytest.raises(ValueError, match="private"):
        opencode_v1.prepare_v1_mcp_readiness(directory)
    assert (actual / "opencode.json").read_bytes() == before


def test_multiple_children_keep_native_fields_and_filters(tmp_path):
    servers = native_file(tmp_path)
    servers["cao"]["environment"]["CAO_INTERNAL_MCP_TOOL_PROFILE"] = "interaction"
    servers["cao"]["environment"]["CAO_INTERNAL_MCP_TOOL_FILTER"] = "[]"
    servers["second"] = copy.deepcopy(servers["cao"])
    (tmp_path / "opencode.json").write_text(json.dumps({"mcp": servers}))
    signals = opencode_v1.prepare_v1_mcp_readiness(tmp_path)
    assert len(signals) == 2 and len(set(signals.values())) == 2
    after = json.loads((tmp_path / "opencode.json").read_text())["mcp"]
    for name in ["cao", "second"]:
        assert after[name]["environment"]["CAO_INTERNAL_MCP_TOOL_PROFILE"] == "interaction"
        assert after[name]["environment"]["CAO_INTERNAL_MCP_TOOL_FILTER"] == "[]"
        assert after[name]["command"] == servers[name]["command"]
