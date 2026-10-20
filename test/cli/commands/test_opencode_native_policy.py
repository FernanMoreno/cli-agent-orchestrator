"""Native profile publication must survive failed install boundaries."""

import json
from pathlib import Path
from test.cli.commands.test_install_opencode import _write_profile, install_workspace

import frontmatter
import pytest

from cli_agent_orchestrator.agent_plugins.store import InstalledPluginStore
from cli_agent_orchestrator.services import install_service
from cli_agent_orchestrator.utils import atomic_file

pytestmark = pytest.mark.offline_database


@pytest.fixture(autouse=True)
def isolated_policy_store(tmp_path, monkeypatch):
    store = InstalledPluginStore(tmp_path / "plugins", tmp_path / "plugin-data")
    monkeypatch.setattr(atomic_file, "LOCK_DIR", tmp_path / "locks")
    monkeypatch.setattr(install_service, "InstalledPluginStore", lambda: store)
    monkeypatch.setattr(install_service, "KIRO_AGENTS_DIR", tmp_path / "kiro")
    monkeypatch.setattr(install_service, "COPILOT_AGENTS_DIR", tmp_path / "copilot")
    monkeypatch.setattr(
        "cli_agent_orchestrator.agent_plugins.mcp_delivery.InstalledPluginStore", lambda: store
    )


@pytest.mark.parametrize("profile", ["interaction", "collaboration"])
def test_install_publishes_per_agent_policy_without_shared_markers(install_workspace, profile):
    from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV
    from cli_agent_orchestrator.utils.mcp_tool_policy import MCP_TOOL_PROFILE_ENV

    _write_profile(
        install_workspace["local_store"] / "test-agent.md",
        mcp_servers=(
            "  cao-mcp-server:\n    command: cao-mcp-server\n" f"    cao_tool_profile: {profile}\n"
        ),
        extra_frontmatter='allowedTools: ["@cao-mcp-server"]\n',
    )
    result = install_service.install_agent("test-agent", provider="opencode_cli")
    assert result.success, result.message
    from cli_agent_orchestrator.utils.opencode_policy import (
        parse_native_agent,
        validate_native_binding,
    )

    text = (install_workspace["agents_dir"] / "test-agent.md").read_text()
    agent, policy = parse_native_agent(text, "test-agent")
    config = json.loads(install_workspace["config_file"].read_text())
    assert policy.profiles == {"cao-mcp-server": profile}
    validate_native_binding(agent, policy, config)
    shared = config["mcp"]["cao-mcp-server"]
    assert "cao_tool_profile" not in shared
    assert MCP_TOOL_PROFILE_ENV not in shared.get("environment", {})
    assert _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV not in shared.get("environment", {})
    assert agent.content == "You are a helpful agent."


def policy_source(workspace, selection="collaboration", *, name="test-agent", body=None):
    extra = "" if selection is None else f"    cao_tool_profile: {selection}\n"
    _write_profile(
        workspace["local_store"] / f"{name}.md",
        name=name,
        mcp_servers="  cao-mcp-server:\n    command: cao-mcp-server\n" + extra,
        extra_frontmatter='allowedTools: ["@cao-mcp-server"]\n',
        **({"body": body} if body is not None else {}),
    )


@pytest.mark.parametrize("stage", ["pending", "provider", "context", "grant", "ready"])
def test_partial_install_is_previous_or_pending_and_can_be_repaired(
    install_workspace, monkeypatch, stage
):
    from cli_agent_orchestrator.utils.opencode_policy import (
        parse_native_agent,
        validate_native_binding,
    )

    policy_source(install_workspace, None)
    assert install_service.install_agent("test-agent", provider="opencode_cli").success
    agent_file = install_workspace["agents_dir"] / "test-agent.md"
    previous = agent_file.read_bytes()
    policy_source(install_workspace)
    source_file = install_workspace["local_store"] / "test-agent.md"
    previous_source = source_file.read_bytes()
    previous_config = install_workspace["config_file"].read_bytes()
    original_write = install_service.locked_atomic_write
    original_replace = atomic_file.os.replace
    calls = 0

    def fail(*args, **kwargs):
        raise OSError("injected install failure")

    def controlled_write(*args, **kwargs):
        nonlocal calls
        calls += 1
        if stage == "pending" and calls == 1:
            fail()
        return original_write(*args, **kwargs)

    def controlled_replace(source, destination):
        if Path(destination) == agent_file and calls == 2:
            fail()
        return original_replace(source, destination)

    with monkeypatch.context() as fault:
        fault.setattr(install_service, "locked_atomic_write", controlled_write)
        if stage == "ready":
            fault.setattr(atomic_file.os, "replace", controlled_replace)
        if stage == "provider":
            fault.setattr(install_service, "write_profile", fail)
        elif stage == "context":
            fault.setattr(install_service, "_write_context_file", fail)
        elif stage == "grant":
            fault.setattr(install_service, "upsert_agent_tools", fail)
        result = install_service.install_agent("test-agent", provider="opencode_cli")
    assert result.success is False
    if stage == "pending":
        assert agent_file.read_bytes() == previous
        assert source_file.read_bytes() == previous_source
        assert install_workspace["config_file"].read_bytes() == previous_config
    else:
        bare, policy = parse_native_agent(agent_file.read_text(), "test-agent")
        assert policy.state == "pending"
        with pytest.raises(ValueError):
            validate_native_binding(bare, policy, {})
    assert install_service.install_agent("test-agent", provider="opencode_cli").success
    bare, policy = parse_native_agent(agent_file.read_text(), "test-agent")
    validate_native_binding(bare, policy, json.loads(install_workspace["config_file"].read_text()))


def test_managed_to_legacy_keeps_guard_during_failure_and_after_success(
    install_workspace, monkeypatch
):
    from cli_agent_orchestrator.utils.opencode_policy import (
        parse_native_agent,
        validate_native_binding,
    )

    policy_source(install_workspace)
    assert install_service.install_agent("test-agent", provider="opencode_cli").success
    policy_source(install_workspace, None)

    def fail(*args, **kwargs):
        raise OSError("injected MCP publication failure")

    with monkeypatch.context() as fault:
        fault.setattr(install_service, "_materialize_opencode_mcp", fail)
        assert not install_service.install_agent("test-agent", provider="opencode_cli").success
    path = install_workspace["agents_dir"] / "test-agent.md"
    _, pending = parse_native_agent(path.read_text(), "test-agent")
    assert pending.state == "pending" and pending.profiles == {}
    assert install_service.install_agent("test-agent", provider="opencode_cli").success
    bare, ready = parse_native_agent(path.read_text(), "test-agent")
    assert ready.state == "ready" and ready.profiles == {}
    validate_native_binding(bare, ready, json.loads(install_workspace["config_file"].read_text()))


def test_another_agent_install_does_not_share_or_erase_policy(install_workspace):
    from cli_agent_orchestrator.utils.opencode_policy import (
        parse_native_agent,
        validate_native_binding,
    )

    for name, selection in [("first", "interaction"), ("second", "collaboration")]:
        policy_source(install_workspace, selection, name=name)
        assert install_service.install_agent(name, provider="opencode_cli").success
    config = json.loads(install_workspace["config_file"].read_text())
    for name, selection in [("first", "interaction"), ("second", "collaboration")]:
        text = (install_workspace["agents_dir"] / f"{name}.md").read_text()
        agent, policy = parse_native_agent(text, name)
        assert policy.profiles == {"cao-mcp-server": selection}
        validate_native_binding(agent, policy, config)


def test_shared_persisted_command_is_not_replaced_by_versioned_python(
    install_workspace, monkeypatch
):
    monkeypatch.setattr(
        "cli_agent_orchestrator.utils.mcp_resolution.shutil.which",
        lambda _: "/stable/launcher/cao-mcp-server",
    )
    policy_source(install_workspace)
    assert install_service.install_agent("test-agent", provider="opencode_cli").success
    config = json.loads(install_workspace["config_file"].read_text())
    assert config["mcp"]["cao-mcp-server"]["command"] == ["/stable/launcher/cao-mcp-server"]


def test_reserved_prompt_is_rejected_before_any_native_publication(install_workspace):
    from cli_agent_orchestrator.utils.opencode_policy import NATIVE_POLICY_PREFIX

    policy_source(install_workspace, body="Before\n" + NATIVE_POLICY_PREFIX + "fake")
    result = install_service.install_agent("test-agent", provider="opencode_cli")
    assert not result.success and "reserved" in result.message
    assert not install_workspace["agents_dir"].exists()
    assert not install_workspace["config_file"].exists()
    assert list(install_workspace["context_dir"].iterdir()) == []


def test_corrupt_unmanaged_native_file_can_still_be_reinstalled_as_legacy(install_workspace):
    from cli_agent_orchestrator.utils.opencode_policy import NATIVE_POLICY_PREFIX

    policy_source(install_workspace, None)
    path = install_workspace["agents_dir"] / "test-agent.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff old undecodable native file")
    result = install_service.install_agent("test-agent", provider="opencode_cli")
    assert result.success, result.message
    assert NATIVE_POLICY_PREFIX not in path.read_text()
    assert frontmatter.loads(path.read_text()).content == "You are a helpful agent."


def test_failed_plugin_refresh_leaves_pending_until_a_successful_refresh(
    install_workspace, monkeypatch
):
    from cli_agent_orchestrator.utils.opencode_policy import (
        parse_native_agent,
        validate_native_binding,
    )

    policy_source(install_workspace)
    assert install_service.install_agent("test-agent", provider="opencode_cli").success

    def fail(*args, **kwargs):
        raise OSError("injected plugin refresh failure")

    with monkeypatch.context() as fault:
        fault.setattr(install_service, "_materialize_opencode_mcp", fail)
        assert install_service.refresh_installed_agents_for_plugin_mcp() == []
    path = install_workspace["agents_dir"] / "test-agent.md"
    _, policy = parse_native_agent(path.read_text(), "test-agent")
    assert policy.state == "pending"
    assert install_service.refresh_installed_agents_for_plugin_mcp() == ["test-agent"]
    bare, policy = parse_native_agent(path.read_text(), "test-agent")
    validate_native_binding(bare, policy, json.loads(install_workspace["config_file"].read_text()))


@pytest.mark.parametrize("replacement", ["remote", "local-command", "local-environment"])
def test_changed_shared_entry_during_publication_cannot_become_ready(
    install_workspace, monkeypatch, replacement
):
    from cli_agent_orchestrator.utils import opencode_config
    from cli_agent_orchestrator.utils.opencode_policy import parse_native_agent

    policy_source(install_workspace)
    materialize = install_service._materialize_opencode_mcp

    def foreign_entry_after_materialization(*args, **kwargs):
        materialize(*args, **kwargs)
        config = opencode_config.read_config()
        original = config["mcp"]["cao-mcp-server"]
        if replacement == "remote":
            foreign = {"type": "remote", "url": "https://unrelated.invalid/mcp"}
        elif replacement == "local-command":
            foreign = {**original, "command": ["unrelated-local-server"]}
        else:
            foreign = {
                **original,
                "environment": {**original.get("environment", {}), "TAG": "foreign-owner"},
            }
        config["mcp"]["cao-mcp-server"] = foreign
        opencode_config.write_config(config)

    monkeypatch.setattr(
        install_service, "_materialize_opencode_mcp", foreign_entry_after_materialization
    )
    result = install_service.install_agent("test-agent", provider="opencode_cli")
    assert not result.success
    _, policy = parse_native_agent(
        (install_workspace["agents_dir"] / "test-agent.md").read_text(), "test-agent"
    )
    assert policy.state == "pending"


@pytest.mark.parametrize(
    "flag",
    [
        "enabled: false",
        "disabled: true",
        "enabled: 0",
        "disabled: 'false'",
        "type: remote",
        "url: https://unrelated.invalid/mcp",
        "type: []",
    ],
)
def test_explicit_policy_cannot_enable_a_disabled_or_malformed_source(install_workspace, flag):
    _write_profile(
        install_workspace["local_store"] / "test-agent.md",
        mcp_servers=(
            "  cao-mcp-server:\n    command: cao-mcp-server\n"
            f"    cao_tool_profile: interaction\n    {flag}\n"
        ),
    )
    result = install_service.install_agent("test-agent", provider="opencode_cli")
    assert not result.success
    assert not install_workspace["agents_dir"].exists()
    assert not install_workspace["config_file"].exists()


@pytest.mark.parametrize("selection", ["interaction", "collaboration"])
@pytest.mark.parametrize("enabled", ["send_message", {}, [1]])
def test_preparation_refuses_profiled_client_filter_without_mutating_input(selection, enabled):
    import copy

    servers = {
        "秘密-server": {
            "command": "cao-mcp-server",
            "cao_tool_profile": selection,
            "enabled_tools": enabled,
        }
    }
    before = copy.deepcopy(servers)
    with pytest.raises(ValueError, match="enabled_tools") as caught:
        install_service._prepare_native_profiles(servers)
    assert "秘密" not in str(caught.value)
    assert servers == before


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("enabled", ["send_message", {}, [1]])
def test_install_refuses_profiled_filter_before_changing_owned_artifacts(
    install_workspace, enabled, existing, tmp_path
):
    from click.testing import CliRunner

    from cli_agent_orchestrator.cli.commands.install import install

    if existing:
        policy_source(install_workspace)
        installed = install_service.install_agent("test-agent", provider="opencode_cli")
        assert installed.success, installed.message
    _write_profile(
        install_workspace["local_store"] / "test-agent.md",
        mcp_servers=(
            "  cao-mcp-server:\n    command: cao-mcp-server\n"
            "    cao_tool_profile: collaboration\n"
            f"    enabled_tools: {json.dumps(enabled)}\n"
        ),
        extra_frontmatter='allowedTools: ["@cao-mcp-server"]\n',
    )

    def snapshot():
        return {
            str(path.relative_to(tmp_path)): (path.stat().st_mode, path.read_bytes())
            for path in tmp_path.rglob("*")
            if path.is_file()
        }

    before = snapshot()
    result = CliRunner().invoke(install, ["test-agent", "--provider", "opencode_cli"])
    assert result.exit_code != 0
    assert "enabled_tools" in result.output
    assert "秘密" not in result.output
    assert snapshot() == before
    if not existing:
        assert not install_workspace["agents_dir"].exists()
        assert not install_workspace["config_file"].exists()
        assert list(install_workspace["context_dir"].iterdir()) == []


@pytest.mark.parametrize("explicit_null", [False, True])
def test_profiled_native_install_accepts_unspecified_client_filter(
    install_workspace, explicit_null
):
    from cli_agent_orchestrator.utils.opencode_policy import (
        parse_native_agent,
        validate_native_binding,
    )

    _write_profile(
        install_workspace["local_store"] / "test-agent.md",
        mcp_servers=(
            "  cao-mcp-server:\n    command: cao-mcp-server\n"
            "    cao_tool_profile: collaboration\n"
            + ("    enabled_tools: null\n" if explicit_null else "")
        ),
        extra_frontmatter='allowedTools: ["@cao-mcp-server"]\n',
    )
    result = install_service.install_agent("test-agent", provider="opencode_cli")
    assert result.success, result.message
    bare, policy = parse_native_agent(
        (install_workspace["agents_dir"] / "test-agent.md").read_text(), "test-agent"
    )
    assert policy.profiles == {"cao-mcp-server": "collaboration"}
    validate_native_binding(bare, policy, json.loads(install_workspace["config_file"].read_text()))


@pytest.mark.parametrize("enabled", [[], ["send_message"], None])
def test_unprofiled_native_preparation_preserves_legacy_filter_handling(enabled):
    servers = {"legacy": {"command": "other-server", "enabled_tools": enabled}}
    prepared, profiles, filters = install_service._prepare_native_profiles(servers)
    assert prepared == servers
    assert profiles == {}
    assert filters == {}
