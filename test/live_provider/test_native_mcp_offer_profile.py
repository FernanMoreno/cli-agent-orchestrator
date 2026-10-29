"""A positive native MCP offer is a catalogue gate, not operation authority."""

import json
import shlex
from test.live_provider import native_cancel_adapters as adapters
from unittest.mock import MagicMock

import pytest
import yaml

from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.providers import claude_code
from cli_agent_orchestrator.utils.mcp_tool_policy import MCP_TOOL_FILTER_ENV, MCP_TOOL_PROFILE_ENV

pytestmark = pytest.mark.offline_database


def test_mcp_adapter_uses_real_production_server_and_only_selected_catalogue(tmp_path, monkeypatch):
    adapter = adapters.CLAUDE_MCP_OFFER
    text = adapters.native_profile_text("native-mcp-offer", adapter)
    profile = AgentProfile(**yaml.safe_load(text.split("---", 2)[1]))
    assert profile.allowedTools == ["@cao-mcp-server"]
    server = profile.mcpServers["cao-mcp-server"]
    assert server["command"] == "cao-mcp-server"
    assert server["cao_tool_profile"] == "collaboration"
    assert server["enabled_tools"] == ["list_siblings"]
    monkeypatch.setattr(claude_code, "CAO_HOME_DIR", tmp_path)
    provider = claude_code.ClaudeCodeProvider(
        "1234abcd", "s", "w", agent_profile=profile.name, allowed_tools=profile.allowedTools
    )
    args = shlex.split(provider._build_claude_command(profile))
    assert args[args.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in args
    value = json.loads(__import__("pathlib").Path(args[args.index("--mcp-config") + 1]).read_text())
    config = value["mcpServers"]["cao-mcp-server"]
    assert config["alwaysLoad"] is True
    assert config["env"][MCP_TOOL_PROFILE_ENV] == "collaboration"
    assert json.loads(config["env"][MCP_TOOL_FILTER_ENV]) == ["list_siblings"]
    assert adapter.expected_mcp_tools == ("mcp__cao-mcp-server__list_siblings",)


def test_existing_adapters_keep_original_empty_native_grants():
    for adapter in (adapters.CLAUDE, adapters.OPENCODE, adapters.CLAUDE_INSPECT):
        profile = AgentProfile(
            **yaml.safe_load(adapters.native_profile_text("existing", adapter).split("---", 2)[1])
        )
        assert profile.allowedTools == []
        assert adapter.expected_mcp_tools == ()


@pytest.mark.parametrize(
    "profile_case,kind",
    [("unknown", "preflight"), ("mcp_offer", "concurrent"), ("mcp_offer", "inspect")],
)
def test_manifest_cannot_silently_widen_or_change_the_mcp_gate(profile_case, kind):
    with pytest.raises(AssertionError):
        adapters.claude_adapter_for_manifest({"profile_case": profile_case, "case_kind": kind})


def test_actual_launch_config_and_wrapper_preserve_provider_timeouts(tmp_path, monkeypatch):
    from test.live_provider import claude_cancel_gateway as gateway

    adapter = adapters.CLAUDE_MCP_OFFER
    profile = AgentProfile(
        **yaml.safe_load(adapters.native_profile_text("mcp", adapter).split("---", 2)[1])
    )
    monkeypatch.setattr(claude_code, "CAO_HOME_DIR", tmp_path)
    provider = claude_code.ClaudeCodeProvider(
        "1234abcd", "s", "w", agent_profile="mcp", allowed_tools=profile.allowedTools
    )
    values = shlex.split(provider._build_claude_command(profile))
    gateway.validate_native_launch(values, adapter.expected_mcp_tools)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"case_kind": "preflight", "profile_case": "mcp_offer"})
    )
    monkeypatch.setenv("MCP_TIMEOUT", "60123")
    monkeypatch.setenv("MCP_CONNECT_TIMEOUT_MS", "60456")
    captured = {}

    def isolated(root, args, *, prepare_environment, validate_args, **kwargs):
        validate_args(args)
        captured.update(prepare_environment({"HOME": str(tmp_path)}))

    monkeypatch.setattr(gateway, "isolated_wrapper", isolated)
    gateway.wrapper(tmp_path, values)
    assert captured["MCP_TIMEOUT"] == "60123"
    assert captured["MCP_CONNECT_TIMEOUT_MS"] == "60456"
    assert json.loads(captured["CAO_NATIVE_EXPECTED_MCP_TOOLS"]) == list(adapter.expected_mcp_tools)


@pytest.mark.parametrize("outcome", ["pass", "failure", "timeout"])
def test_import_probe_uses_actual_config_environment_and_records_failure(
    tmp_path, monkeypatch, outcome
):
    import subprocess
    from test.live_provider import claude_cancel_gateway as gateway
    from types import SimpleNamespace

    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "cao-mcp-server": {
                        "command": "/exact/configured/python",
                        "env": {"FILTER": "configured"},
                    }
                }
            }
        )
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PYTHONPATH", "configured-namespace-repo")
    recorded = []

    def invoke(args, **kwargs):
        assert args == [
            "/exact/configured/python",
            "-c",
            "import cli_agent_orchestrator.mcp_server.server",
        ]
        assert kwargs["env"]["FILTER"] == "configured"
        assert kwargs["env"]["PYTHONPATH"] == "configured-namespace-repo"
        assert kwargs["cwd"] == str(tmp_path) and kwargs["timeout"] == 30
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(args, 30)
        return SimpleNamespace(
            returncode=0 if outcome == "pass" else 1, stdout=b"", stderr=b"diagnostic"
        )

    monkeypatch.setattr(gateway.subprocess, "run", invoke)
    args = ["--mcp-config", str(config)]
    if outcome == "pass":
        gateway.probe_mcp_import(args, tmp_path, lambda **value: recorded.append(value))
        assert recorded[-1]["production_mcp_masked_import_passed"] is True
    else:
        with pytest.raises((subprocess.TimeoutExpired, AssertionError)):
            gateway.probe_mcp_import(args, tmp_path, lambda **value: recorded.append(value))
        assert recorded[-1]["errors"]
    assert recorded[0] == {"production_mcp_import_started": True}
    if outcome != "timeout":
        assert (tmp_path / "mcp-import-probe.json").stat().st_mode & 0o777 == 0o600
