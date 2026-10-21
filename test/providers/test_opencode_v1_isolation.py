"""V1 cannot mutate or inherit the operator's configuration and state."""

import asyncio
import copy
import json
import shlex
from pathlib import Path
from test.providers.test_opencode_profile_initialization import launch
from unittest.mock import AsyncMock

import pytest

from cli_agent_orchestrator.providers import opencode_cli
from cli_agent_orchestrator.utils import opencode_config

pytestmark = pytest.mark.offline_database


@pytest.mark.asyncio
async def test_v1_launch_uses_private_state_and_preserves_source(launch, monkeypatch):
    instance, backend, _ = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    source = {"mcp": {}, "agent": {"reviewer": {"tools": {"cao*": True}}}}
    before = copy.deepcopy(source)
    monkeypatch.setattr(opencode_config, "read_config", lambda: source)
    source_data = Path(__import__("os").environ["XDG_DATA_HOME"])
    (source_data / "opencode").mkdir(parents=True)
    auth = source_data / "opencode/auth.json"
    auth.write_text(json.dumps({"synthetic": {"type": "api", "key": "synthetic-private-key"}}))
    original = auth.read_bytes()
    for key in ("OPENCODE_CONFIG_CONTENT", "OPENCODE_PERMISSION", "OPENCODE_AUTH_CONTENT"):
        monkeypatch.setenv(key, "synthetic-inherited-override")
    instance._model = "synthetic/exact-model"
    assert await instance.initialize() is True
    assert instance._v1_config_dir is not None
    private = instance._v1_config_dir
    generated = json.loads((private / "opencode.json").read_text())
    assert generated["agent"]["reviewer"]["prompt"] == "Review."
    assert "agents" not in generated
    assert generated["model"] == "synthetic/exact-model"
    command = backend.send_keys.call_args.args[2]
    words = shlex.split(command)
    assert "/usr/bin/env" in words
    for field in (
        "HOME",
        "OPENCODE_TEST_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "TMPDIR",
        "OPENCODE_DB",
    ):
        value = next(w.split("=", 1)[1] for w in words if w.startswith(field + "="))
        assert Path(value).is_relative_to(private)
    assert "OPENCODE_DISABLE_PROJECT_CONFIG=1" in words
    for key in ("OPENCODE_CONFIG_CONTENT", "OPENCODE_PERMISSION", "OPENCODE_AUTH_CONTENT"):
        assert ["-u", key] == words[words.index(key) - 1 : words.index(key) + 1]
    assert "synthetic-inherited-override" not in command
    assert "synthetic-private-key" not in command
    assert (private / "data/opencode/auth.json").read_bytes() == original
    assert auth.read_bytes() == original and source == before
    assert private.stat().st_mode & 0o077 == 0
    assert (private / "opencode.json").stat().st_mode & 0o077 == 0
    assert (private / "data/opencode/auth.json").stat().st_mode & 0o077 == 0
    instance.cleanup()
    assert not private.exists()
    assert auth.read_bytes() == original


@pytest.mark.asyncio
async def test_v1_failed_readiness_cleans_only_owned_private_root(launch, monkeypatch):
    instance, _, cao_home = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    monkeypatch.setattr(opencode_config, "read_config", lambda: {"mcp": {}, "agent": {}})
    monkeypatch.setattr(instance, "_wait_for_initial_ready", AsyncMock(return_value=False))
    sibling = cao_home / "opencode-v1/terminal-unrelated"
    sibling.mkdir(parents=True)
    (sibling / "keep").write_bytes(b"unrelated")
    with pytest.raises(TimeoutError):
        await instance.initialize()
    assert instance._owned_v1_config_dir is None
    assert list(sibling.parent.iterdir()) == [sibling]
    assert (sibling / "keep").read_bytes() == b"unrelated"


@pytest.mark.asyncio
async def test_v1_multiagent_launches_keep_configuration_state_and_cleanup_separate(
    launch, monkeypatch
):
    reviewer, _, _ = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    source = {"mcp": {}, "agent": {}}
    monkeypatch.setattr(opencode_config, "read_config", lambda: source)
    (opencode_cli.OPENCODE_AGENTS_DIR / "worker.md").write_text(
        "---\ndescription: Worker\n---\nImplement.\n"
    )
    worker = opencode_cli.OpenCodeCliProvider("test5678", "test", "worker", "worker")
    monkeypatch.setattr(worker, "_wait_for_initial_ready", AsyncMock(return_value=True))
    try:
        assert await asyncio.gather(reviewer.initialize(), worker.initialize()) == [True, True]
        first, second = reviewer._v1_config_dir, worker._v1_config_dir
        assert first is not None and second is not None and first != second
        first_config = json.loads((first / "opencode.json").read_text())
        second_config = json.loads((second / "opencode.json").read_text())
        assert set(first_config["agent"]) == {"reviewer"}
        assert first_config["agent"]["reviewer"]["prompt"] == "Review."
        assert set(second_config["agent"]) == {"worker"}
        assert second_config["agent"]["worker"]["prompt"] == "Implement."
        state = second / "data/opencode/worker-state"
        state.write_bytes(b"worker-owned-state")
        reviewer.cleanup()
        assert not first.exists()
        assert state.read_bytes() == b"worker-owned-state"
        assert (second / "opencode.json").is_file()
        assert source == {"mcp": {}, "agent": {}}
    finally:
        worker.cleanup()


def test_native_v1_writer_freezes_agent_and_preserves_native_permissions(tmp_path):
    from cli_agent_orchestrator.utils.opencode_v1 import write_v1_configuration

    source = {"mcp": {}, "tools": {"cao*": False}, "agent": {"reviewer": {"tools": {"cao*": True}}}}
    before = copy.deepcopy(source)
    text = "---\ndescription: Reviewer\npermission:\n  bash:\n    '*': deny\n    'git *': allow\n---\nFrozen ñ, 中文."
    result = write_v1_configuration(
        tmp_path,
        source_config=source,
        selected_agent_source=("reviewer", text),
        selected_model="synthetic/model",
        session_id="test1234",
    )
    generated = json.loads((result / "opencode.json").read_text())
    agent = generated["agent"]["reviewer"]
    assert agent["prompt"] == "Frozen ñ, 中文."
    assert agent["permission"]["bash"] == {"*": "deny", "git *": "allow"}
    assert agent["permission"]["cao*"] == "allow"
    assert "permissions" not in agent
    assert generated["autoupdate"] is False
    assert generated["share"] == "disabled"
    assert source == before


@pytest.mark.parametrize(
    "source,metadata,global_permission,agent_permission",
    [
        ({}, "tools:\n  bash: false\n", {}, {"bash": "deny"}),
        ({"tools": {"write": False}}, "", {"edit": "deny"}, {}),
        ({"tools": {"patch": False}}, "", {"edit": "deny"}, {}),
        (
            {"tools": {"bash": True}, "permission": {"bash": "deny"}},
            "",
            {"bash": "deny"},
            {},
        ),
        (
            {},
            "tools:\n  write: false\npermission:\n  edit: ask\n",
            {},
            {"edit": "ask"},
        ),
    ],
)
def test_v1_native_tool_rules_preserve_scope_aliases_and_permission_precedence(
    tmp_path, source, metadata, global_permission, agent_permission
):
    from cli_agent_orchestrator.utils.opencode_v1 import write_v1_configuration

    write_v1_configuration(
        tmp_path,
        source_config=source,
        selected_agent_source=(
            "reviewer",
            "---\ndescription: Reviewer\n" + metadata + "---\nReview.",
        ),
        session_id="test1234",
    )
    generated = json.loads((tmp_path / "opencode.json").read_text())
    assert generated.get("permission", {}) == global_permission
    assert generated["agent"]["reviewer"]["permission"] == agent_permission
    assert "tools" not in generated and "tools" not in generated["agent"]["reviewer"]


@pytest.mark.parametrize("name", ["../escape", "", "/absolute"])
def test_v1_writer_rejects_unbounded_agent_identity_before_writing(tmp_path, name):
    from cli_agent_orchestrator.utils.opencode_v1 import write_v1_configuration

    with pytest.raises(ValueError):
        write_v1_configuration(
            tmp_path,
            source_config={},
            selected_agent_source=(name, "Review."),
            session_id="test1234",
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "auth", [{}, {"synthetic": {"type": "api", "key": "synthetic-inline-key"}}]
)
async def test_v1_snapshots_inline_auth_before_clearing_inherited_override(
    launch, monkeypatch, auth
):
    instance, backend, _ = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    monkeypatch.setattr(opencode_config, "read_config", lambda: {"mcp": {}, "agent": {}})
    monkeypatch.setenv("OPENCODE_AUTH_CONTENT", json.dumps(auth))
    assert await instance.initialize() is True
    assert json.loads((instance._v1_config_dir / "data/opencode/auth.json").read_text()) == auth
    assert "synthetic-inline-key" not in backend.send_keys.call_args.args[2]


@pytest.mark.parametrize(
    "extra",
    [
        {"cao_tool_profile": "collaboration"},
        {"enabled_tools": []},
        {"codemode": True},
        {"environment": {"KEY": 1}},
        {"timeout": 0},
        {"type": "remote", "url": "http://mcp.invalid", "command": ["server"]},
    ],
)
def test_v1_writer_refuses_unsupported_or_malformed_mcp_before_emission(tmp_path, extra):
    from cli_agent_orchestrator.utils.opencode_v1 import write_v1_configuration

    source = {"mcp": {"cao": {"type": "local", "command": ["server"], **extra}}}
    with pytest.raises(ValueError):
        write_v1_configuration(
            tmp_path,
            source_config=source,
            selected_agent_source=("reviewer", "Review."),
            session_id="test1234",
        )
    assert not list(tmp_path.iterdir())


def test_v1_literal_servers_name_is_not_mistaken_for_wrapper(tmp_path):
    from cli_agent_orchestrator.utils.opencode_v1 import write_v1_configuration

    source = {"mcp": {"servers": {"type": "local", "command": ["server"]}}}
    write_v1_configuration(
        tmp_path,
        source_config=source,
        selected_agent_source=("reviewer", "Review."),
        session_id="test1234",
    )
    assert json.loads((tmp_path / "opencode.json").read_text())["mcp"] == source["mcp"] | {
        "servers": source["mcp"]["servers"] | {"enabled": True}
    }


def test_v1_writer_refuses_loss_of_v2_global_timeout(tmp_path):
    from cli_agent_orchestrator.utils.opencode_v1 import write_v1_configuration

    with pytest.raises(ValueError):
        write_v1_configuration(
            tmp_path,
            source_config={"mcp": {"servers": {}, "timeout": {"execution": 5}}},
            selected_agent_source=("reviewer", "Review."),
            session_id="test1234",
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "source",
    [
        {"mcp": []},
        {"agent": []},
        {"mode": {"unselected": {"prompt": "Unselected legacy agent"}}},
        {"permission": {"*": "invalid"}},
        {"agent": {"reviewer": {"mode": "invalid"}}},
        {
            "mcp": {
                "cao.one": {"type": "local", "command": ["server"]},
                "cao/one": {"type": "local", "command": ["server"]},
            }
        },
    ],
)
def test_v1_malformed_configuration_and_collisions_fail_before_writing(tmp_path, source):
    from cli_agent_orchestrator.utils.opencode_v1 import write_v1_configuration

    with pytest.raises(ValueError):
        write_v1_configuration(
            tmp_path,
            source_config=source,
            selected_agent_source=("reviewer", "Review."),
            session_id="test1234",
        )
    assert not list(tmp_path.iterdir())
