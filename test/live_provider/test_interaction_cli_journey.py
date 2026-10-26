"""Offline safety checks for the bounded live interaction harness."""

from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path
from test.live_provider import interaction_cli_journey as journey
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.providers import claude_code as claude_code_module
from cli_agent_orchestrator.providers.claude_code import ClaudeCodeProvider
from cli_agent_orchestrator.providers.codex import CodexProvider
from cli_agent_orchestrator.utils import agent_profiles

pytestmark = pytest.mark.offline_database


def _write_subscription_codex_home(
    home: Path, *, auth_data: dict[str, object]
) -> tuple[bytes, bytes]:
    home.mkdir(mode=0o700)
    (home / "packages").mkdir(mode=0o700)
    auth_path = home / "auth.json"
    auth_path.write_text(json.dumps(auth_data), encoding="utf-8")
    auth_path.chmod(0o600)
    config_path = home / "config.toml"
    config_path.write_text(
        'model = "operator-default-model"\n'
        "[profiles.interaction-safe]\n"
        'model = "operator-profile-model"\n',
        encoding="utf-8",
    )
    config_path.chmod(0o600)
    return auth_path.read_bytes(), config_path.read_bytes()


def test_codex_acceptance_profile_pins_model_without_editing_operator_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = tmp_path / "agent-store"
    monkeypatch.setattr(constants, "LOCAL_AGENT_STORE_DIR", store)
    monkeypatch.setattr(agent_profiles, "LOCAL_AGENT_STORE_DIR", store)

    operator_codex_home = tmp_path / "operator-codex"
    original_auth, original_config = _write_subscription_codex_home(
        operator_codex_home,
        auth_data={"auth_mode": "chatgpt"},
    )
    monkeypatch.setenv("CODEX_HOME", str(operator_codex_home))

    private_original_home = tmp_path / "profile-home"
    staged_codex_home = private_original_home / ".codex"
    staged_codex_home.mkdir(mode=0o700, parents=True)
    staged_auth = staged_codex_home / "auth.json"
    staged_auth.write_bytes(original_auth)
    staged_auth.chmod(0o600)
    staged_config = staged_codex_home / "config.toml"
    staged_config.write_bytes(original_config)
    staged_config.chmod(0o600)
    monkeypatch.setattr(journey, "ORIGINAL_HOME", private_original_home)
    monkeypatch.setenv("CAO_PRIVATE_PROFILE_HOME", str(private_original_home))

    run_root = tmp_path / "isolated-run"
    run_root.mkdir(mode=0o700)
    profile_name = "live-interaction-model-test"
    journey._install_profile(run_root, profile_name, "codex", 12345, "terminal-test")

    profile = agent_profiles.load_agent_profile(profile_name)
    assert profile.model == journey.CODEX_INTERACTION_MODEL == "gpt-6-luna"

    provider = CodexProvider(
        "terminal-test",
        "session-test",
        "window-0",
        profile_name,
        allowed_tools=["@cao-mcp-server"],
    )
    provider_command = provider._build_codex_command()
    provider_args = shlex.split(provider_command)
    model_index = provider_args.index("--model")
    assert provider_args[model_index + 1] == "gpt-6-luna"

    command = journey._codex_command(
        provider_command,
        run_root,
        "bounded acceptance prompt",
        executable_override="/fixture/codex",
    )
    command_args = command
    assert command_args[0:2] == ["/fixture/codex", "exec"]
    assert command_args[command_args.index("--model") + 1] == "gpt-6-luna"
    assert "--profile" not in command_args
    assert command_args[command_args.index("--sandbox") + 1] == "read-only"
    assert command_args[command_args.index("--disable") + 1] == "shell_tool"
    assert 'approval_policy="never"' in command_args
    approval_overrides = [
        command_args[index + 1]
        for index, value in enumerate(command_args[:-1])
        if value == "-c"
        and command_args[index + 1].startswith("mcp_servers.")
        and ".approval_mode=" in command_args[index + 1]
    ]
    assert approval_overrides == [
        f'mcp_servers.cao-mcp-server.tools.{tool}.approval_mode="approve"'
        for tool in journey.INTERACTION_TOOLS
    ]
    assert not any("default_tools_approval_mode" in value for value in command_args)
    assert not any("mcp_servers.other" in value for value in command_args)
    assert "--dangerously-bypass-approvals-and-sandbox" not in command_args
    assert 'web_search="disabled"' in command_args
    assert journey._agent_tool_scope_exact("codex", profile, command_args, run_root) is True

    widened_command = [
        *command_args[:-1],
        "-c",
        'mcp_servers.cao-mcp-server.enabled_tools=["cao_ask_user"]',
        command_args[-1],
    ]
    assert journey._agent_tool_scope_exact("codex", profile, widened_command, run_root) is False
    substituted_server = list(command_args)
    server_command_index = next(
        index
        for index, token in enumerate(substituted_server[:-1])
        if token == "-c"
        and substituted_server[index + 1].startswith("mcp_servers.cao-mcp-server.command=")
    )
    substituted_server[server_command_index + 1] = (
        'mcp_servers.cao-mcp-server.command="/fixture/unapproved-mcp"'
    )
    assert journey._agent_tool_scope_exact("codex", profile, substituted_server, run_root) is False

    uncontained_command = list(command_args)
    disable_index = uncontained_command.index("--disable")
    del uncontained_command[disable_index : disable_index + 2]
    assert journey._agent_tool_scope_exact("codex", profile, uncontained_command, run_root) is False
    assert journey._agent_tool_scope_exact("opencode_cli", profile, command_args, run_root) is False
    safe_scope = journey._safe_provider_receipt(
        {"provider": "codex", "agent_tool_scope_exact": True}
    )
    assert safe_scope["agent_tool_scope_exact"] is True
    assert "agent_tool_scope_exact" not in journey._safe_provider_receipt(
        {"provider": "codex", "agent_tool_scope_exact": "verified"}
    )

    source, private_home = journey._prepare_codex_home_overlay(run_root)
    assert source == operator_codex_home.resolve()
    assert (private_home / "auth.json").read_bytes() == original_auth
    assert (private_home / "config.toml").read_bytes() == original_config
    assert (private_home / "auth.json").stat().st_mode & 0o777 == 0o600
    assert (private_home / "config.toml").stat().st_mode & 0o777 == 0o600
    assert (operator_codex_home / "auth.json").read_bytes() == original_auth
    assert (operator_codex_home / "config.toml").read_bytes() == original_config


def test_codex_interaction_approval_overrides_reject_unallowlisted_tools() -> None:
    overrides = journey._codex_interaction_approval_overrides

    assert overrides("other-server", journey.INTERACTION_TOOLS) == []
    with pytest.raises(ValueError, match="interaction tool allowlist"):
        overrides("cao-mcp-server", (*journey.INTERACTION_TOOLS, "delete_workspace"))


def test_opencode_profile_stays_native_for_agui_restart() -> None:
    from test.live_provider import opencode_interaction_journey as native_journey

    assert journey._profile_model("opencode_cli", agui_restart=False) is None
    assert journey._profile_model("opencode_cli", agui_restart=True) is None
    assert journey.OPENCODE_INTERACTION_MODEL == "opencode-go/longcat-2.5-preview-free"
    assert native_journey._validate_model_id(journey.OPENCODE_INTERACTION_MODEL) == (
        journey.OPENCODE_INTERACTION_MODEL
    )
    with pytest.raises(ValueError, match="unsupported_provider"):
        journey._profile_model("unapproved_provider", agui_restart=False)


def test_opencode_prompt_and_profile_use_the_same_three_exposed_tools() -> None:
    from test.live_provider import opencode_interaction_journey as native_journey

    prompt = native_journey._opencode_prompt("operation-test")
    agent_source = native_journey._agent_source("agent-test")

    assert "seven CAO interaction MCP tools" not in prompt
    assert "seven CAO interaction MCP tools" not in agent_source
    for tool_name in native_journey.OPENCODE_INTERACTION_TOOL_NAMES:
        assert tool_name in prompt
        assert tool_name in agent_source
        assert prompt.count(tool_name) == 2
    assert "cao-mcp-server_cao-mcp-server_" not in prompt
    for short_name in journey.INTERACTION_TOOLS[3:]:
        assert short_name not in prompt


def test_opencode_agui_restart_dispatches_to_native_journey(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    expected = {"provider": "opencode_cli", "result": "native_candidate"}

    def run_native() -> dict[str, str]:
        calls.append("native")
        return expected

    monkeypatch.setattr(journey, "_run_opencode_agui_restart", run_native, raising=False)
    result = journey.run("opencode_cli", agui_restart=True)

    assert calls == ["native"]
    assert result is expected


def test_opencode_without_agui_opt_in_stays_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        journey,
        "_run_opencode_agui_restart",
        lambda: calls.append("native"),
        raising=False,
    )

    result = journey.run("opencode_cli", agui_restart=False)

    assert result == {"provider": "opencode_cli", "result": "unsupported_provider"}
    assert calls == []


def test_opencode_success_requires_native_state_all_snapshots_cleanup_and_harness_exit() -> None:
    complete = {
        "provider": "opencode_cli",
        "requested_model": journey.OPENCODE_INTERACTION_MODEL,
        "result": "completed_and_acknowledged",
        "question_count": 2,
        "human_answers_posted": 2,
        "delivery_states": ["agent_acknowledged", "agent_acknowledged"],
        "agui_restart_enabled": True,
        "api_worker_restarts": 1,
        "agui_snapshot_count": 3,
        "agui_identity_stable": True,
        "agui_projection_redacted": True,
        "agui_final_acknowledged": True,
        "agui_ledger_identity_stable": True,
        "agui_observation_complete": True,
        "agui_exact_ack_observed": True,
        "native_provider_processes_stable": True,
        "database_target_stable": True,
        "native_provider_final_state": "IDLE",
        "native_provider_status_before_deadline": True,
        "native_provider_state_source": "tmux_live_pane_via_production_provider",
        "native_provider_exit_status": None,
        "native_provider_exit_status_observed": False,
        "native_mcp_ready": True,
        "operator_auth_unchanged": True,
        "private_scope_verified": True,
        "scope_attestation": {
            "run_root_private": True,
            "cao_home_is_run_child": True,
            "agent_store_is_cao_child": True,
            "config_is_run_private": True,
            "bindings_active": True,
            "bindings_restored": True,
        },
        "cleanup_status": "complete",
        "temporary_files_removed": True,
    }

    assert journey._provider_journey_succeeded(complete, require_auth_unchanged=True)
    assert not journey._provider_journey_succeeded(
        {
            "provider": "opencode_cli",
            "result": "completed_and_acknowledged",
            "exit_code": 0,
        },
        require_auth_unchanged=True,
    )
    for state in ("PROCESSING", "ERROR", "UNKNOWN"):
        incomplete = {**complete, "native_provider_final_state": state}
        assert not journey._provider_journey_succeeded(incomplete, require_auth_unchanged=True)
    expired_status = {**complete, "native_provider_status_before_deadline": False}
    assert not journey._provider_journey_succeeded(expired_status, require_auth_unchanged=True)
    cleanup_failed = {**complete, "cleanup_status": "incomplete"}
    assert not journey._provider_journey_succeeded(cleanup_failed, require_auth_unchanged=True)


def test_opencode_profile_install_keeps_model_selection_with_native_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import yaml

    store = tmp_path / "agent-store"
    monkeypatch.setattr(constants, "LOCAL_AGENT_STORE_DIR", store)
    monkeypatch.setattr(agent_profiles, "LOCAL_AGENT_STORE_DIR", store)
    monkeypatch.setattr(
        journey,
        "_mcp_child_environment",
        lambda _root, port, _terminal: {"CAO_API_PORT": str(port)},
    )
    run_root = tmp_path / "isolated-run"
    run_root.mkdir(mode=0o700)

    journey._install_profile(
        run_root, "opencode-profile-test", "opencode_cli", 12345, "terminal-test"
    )

    profile_text = (store / "opencode-profile-test.md").read_text(encoding="utf-8")
    metadata = yaml.safe_load(profile_text.split("---\n", 2)[1])
    loaded_profile = agent_profiles.load_agent_profile("opencode-profile-test")
    assert metadata["provider"] == "opencode_cli"
    assert "model" not in metadata
    assert set(metadata["mcpServers"]) == {"cao-mcp-server"}
    assert loaded_profile.provider == "opencode_cli"
    assert loaded_profile.model is None


def test_claude_actual_profile_and_command_prove_exact_mcp_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = tmp_path / "agent-store"
    monkeypatch.setattr(constants, "LOCAL_AGENT_STORE_DIR", store)
    monkeypatch.setattr(agent_profiles, "LOCAL_AGENT_STORE_DIR", store)
    monkeypatch.setattr(
        journey,
        "_mcp_child_environment",
        lambda _root, port, _terminal: {"CAO_API_PORT": str(port)},
    )
    run_root = tmp_path / "isolated-run"
    run_root.mkdir(mode=0o700)
    cao_home = run_root / "cao"
    cao_home.mkdir(mode=0o700)
    monkeypatch.setattr(claude_code_module, "CAO_HOME_DIR", cao_home)

    journey._install_profile(run_root, "claude-scope-test", "claude_code", 12345, "terminal-test")
    profile = agent_profiles.load_agent_profile("claude-scope-test")
    provider = ClaudeCodeProvider(
        "terminal-test",
        "session-test",
        "window-0",
        "claude-scope-test",
        allowed_tools=["@cao-mcp-server"],
    )
    provider_command = provider._build_claude_command(profile)
    command = journey._claude_command(provider_command, "bounded acceptance prompt")

    assert journey._agent_tool_scope_exact("claude_code", profile, command, run_root) is True

    expanded_command = list(command)
    allowed_index = expanded_command.index("--allowedTools")
    expanded_command.insert(allowed_index + 1, "mcp__cao-mcp-server__delete_workspace")
    assert (
        journey._agent_tool_scope_exact("claude_code", profile, expanded_command, run_root) is False
    )

    enabled_builtin = list(command)
    tools_index = enabled_builtin.index("--tools")
    enabled_builtin[tools_index + 1] = "Read"
    assert (
        journey._agent_tool_scope_exact("claude_code", profile, enabled_builtin, run_root) is False
    )

    non_strict_command = list(command)
    del non_strict_command[non_strict_command.index("--strict-mcp-config")]
    assert (
        journey._agent_tool_scope_exact("claude_code", profile, non_strict_command, run_root)
        is False
    )

    mcp_config_path = Path(command[command.index("--mcp-config") + 1])
    config = json.loads(mcp_config_path.read_text(encoding="utf-8"))
    config["mcpServers"]["unapproved-server"] = {
        "type": "stdio",
        "command": "unapproved",
        "args": [],
    }
    mcp_config_path.write_text(json.dumps(config), encoding="utf-8")
    assert journey._agent_tool_scope_exact("claude_code", profile, command, run_root) is False


def test_agent_message_failure_diagnostics_emit_only_fixed_flags(tmp_path: Path) -> None:
    marker = "PRIVATE_MESSAGE_BODY_MARKER"
    output = tmp_path / "provider-output.jsonl"
    output.write_text(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": (
                        "Tools are unavailable because the capability is missing. "
                        "I cannot assist. The prompt does not match the request. " + marker
                    ),
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    diagnostics = journey._agent_message_failure_diagnostics(output)
    safe = journey._safe_provider_receipt(
        {
            "provider": "codex",
            "result": "cli_exited_without_ack",
            "agent_message_diagnostics": diagnostics,
        }
    )

    assert diagnostics == {
        "scan_complete": True,
        "agent_message_seen": True,
        "reports_tools_unavailable": True,
        "reports_capability_missing": True,
        "reports_refusal": True,
        "reports_prompt_mismatch": True,
    }
    encoded = json.dumps(safe, sort_keys=True)
    assert marker not in encoded
    assert "agent_message_diagnostics" in safe
    assert "text" not in safe["agent_message_diagnostics"]


def test_claude_rate_limit_event_is_classified_without_provider_text() -> None:
    output = '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected"}}'

    assert journey._failure_category(output, returncode=1, timed_out=False) == (
        "provider_or_model_rejected"
    )
    assert journey._sanitized_failure_signals(output) == ["provider_rate_limited"]
    safe = journey._safe_provider_receipt(
        {
            "provider": "claude_code",
            "result": "provider_or_model_rejected",
            "failure_diagnostics": journey._failure_diagnostic_flags(output),
        }
    )
    assert safe["result"] == "provider_or_model_rejected"
    assert safe["failure_diagnostics"]["provider_rate_limited"] is True
    assert all(
        value is False
        for key, value in safe["failure_diagnostics"].items()
        if key != "provider_rate_limited"
    )
    assert "rate_limit_info" not in repr(safe)


def test_agent_message_classifier_does_not_treat_non_agent_items_as_causes(
    tmp_path: Path,
) -> None:
    output = tmp_path / "provider-output.jsonl"
    output.write_text(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "user_message", "text": "tools are unavailable"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    diagnostics = journey._agent_message_failure_diagnostics(output)

    assert diagnostics == {
        "scan_complete": True,
        "agent_message_seen": False,
        "reports_tools_unavailable": False,
        "reports_capability_missing": False,
        "reports_refusal": False,
        "reports_prompt_mismatch": False,
    }


def test_agent_message_classifier_marks_deep_json_incomplete_without_leaking(
    tmp_path: Path,
) -> None:
    marker = "PRIVATE_DEEP_JSON_MARKER"
    nested_item = (
        b"[" * 10000
        + json.dumps({"type": "agent_message", "text": marker}).encode("utf-8")
        + b"]" * 10000
    )
    output = tmp_path / "deep-provider-output.jsonl"
    output.write_bytes(b'{"type":"item.completed","item":' + nested_item + b"}\n")

    diagnostics = journey._agent_message_failure_diagnostics(output)
    safe = journey._safe_provider_receipt(
        {
            "provider": "codex",
            "result": "cli_exited_without_ack",
            "agent_message_diagnostics": diagnostics,
        }
    )

    assert diagnostics == {
        "scan_complete": False,
        "agent_message_seen": False,
        "reports_tools_unavailable": False,
        "reports_capability_missing": False,
        "reports_refusal": False,
        "reports_prompt_mismatch": False,
    }
    assert marker not in json.dumps(safe, sort_keys=True)


@pytest.mark.parametrize("provider", ["codex", "claude_code"])
def test_provider_child_environment_never_contains_api_key_variables(
    provider: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    private_original_home = tmp_path / "original-home"
    monkeypatch.setattr(journey, "ORIGINAL_HOME", private_original_home)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "EXAMPLE_API_KEY"):
        monkeypatch.setenv(key, "synthetic-test-key")

    if provider == "claude_code":
        credentials = private_original_home / ".claude" / ".credentials.json"
        credentials.parent.mkdir(parents=True, mode=0o700)
        credentials.write_text('{"synthetic":"subscription credential"}', encoding="utf-8")
        credentials.chmod(0o600)

    run_root = tmp_path / "provider-run"
    run_root.mkdir(mode=0o700)
    child_environment = journey._base_cli_env(run_root, provider)

    assert not any(key.endswith("_API_KEY") for key in child_environment)
    assert "OPENAI_API_KEY" not in child_environment
    assert "ANTHROPIC_API_KEY" not in child_environment
    if provider == "claude_code":
        assert (run_root / "claude-home" / ".credentials.json").is_file()


@pytest.mark.parametrize(
    "auth_data",
    [
        {"auth_mode": "apikey", "OPENAI_API_KEY": "synthetic-test-key"},
        {"auth_mode": "chatgpt", "OPENAI_API_KEY": "synthetic-test-key"},
        {"auth_mode": "missing"},
    ],
)
def test_codex_private_overlay_rejects_non_subscription_or_metered_auth(
    auth_data: dict[str, object], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    operator_codex_home = tmp_path / "operator-codex"
    _write_subscription_codex_home(operator_codex_home, auth_data=auth_data)
    monkeypatch.setenv("CODEX_HOME", str(operator_codex_home))
    run_root = tmp_path / "isolated-run"
    run_root.mkdir(mode=0o700)

    with pytest.raises(RuntimeError, match="codex_subscription_auth_unavailable"):
        journey._prepare_codex_home_overlay(run_root)

    assert not (run_root / "codex-home").exists()
    assert not (run_root / "codex-auth-staging.json").exists()


def test_provider_event_summary_distinguishes_tool_calls_without_exposing_payloads(
    tmp_path: Path,
) -> None:
    output = tmp_path / "provider.jsonl"
    private_marker = "DO_NOT_PERSIST_TEST_PROMPT_OR_ANSWER"
    events = [
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "The tool cao_ask_user is available."},
        },
        {
            "type": "item.started",
            "item": {
                "id": "call-safe-test",
                "type": "mcp_tool_call",
                "server": "cao-mcp-server",
                "tool": "cao_ask_user",
                "arguments": {"prompt": private_marker},
                "status": "in_progress",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "call-safe-test",
                "type": "mcp_tool_call",
                "server": "cao-mcp-server",
                "tool": "cao_ask_user",
                "arguments": {"prompt": private_marker},
                "status": "failed",
                "error": {"message": private_marker},
                "result": {
                    "content": [{"type": "text", "text": private_marker}],
                    "structured_content": {"error": {"code": "forbidden"}},
                },
            },
        },
        {
            "type": "item.completed",
            "item": {"type": "tool_search", "query": private_marker},
        },
        {"type": "item.completed", "item": {"type": "mcp_tool_call", "tool": "other"}},
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "server": "cao-mcp-server",
                "tool": "cao_ask_user",
                "status": "failed",
                "error": {"message": "Invalid params: " + private_marker},
            },
        },
    ]
    output.write_text(
        "\n".join(json.dumps(event) for event in events) + "\nnot-json\n",
        encoding="utf-8",
    )

    summary = journey._provider_event_summary(output)

    assert summary["provider_event_counts"] == {"item.completed": 5, "item.started": 1}
    assert summary["provider_item_type_counts"] == {
        "agent_message": 1,
        "mcp_tool_call": 4,
        "tool_search": 1,
    }
    assert summary["mcp_tool_names"] == ["cao_ask_user"]
    assert summary["mcp_tool_calls"] == [
        {
            "server": "cao-mcp-server",
            "tool": "cao_ask_user",
            "status": "failed",
            "error_present": True,
            "result_present": True,
            "error_code": "forbidden",
            "mcp_result_shape": "call_tool_result",
            "mcp_is_error": "absent",
            "mcp_result_success": "unknown",
            "mcp_content_block_types": ["text"],
            "mcp_structured_content_present": True,
            "mcp_result_error_code": "forbidden",
            "mcp_unavailable_cause": None,
        },
        {
            "server": "cao-mcp-server",
            "tool": "cao_ask_user",
            "status": "failed",
            "error_present": True,
            "result_present": False,
            "error_code": "protocol_invalid_params",
        },
        {
            "server": "other",
            "tool": "other",
            "status": "unknown",
            "error_present": False,
            "result_present": False,
            "error_code": None,
        },
    ]
    assert private_marker not in json.dumps(summary)


@pytest.mark.parametrize(
    (
        "result_id",
        "is_error",
        "include_error_flag",
        "expected_status",
        "expected_complete",
    ),
    [
        ("opaque-tool-id", False, True, "completed", True),
        ("opaque-tool-id", True, True, "failed", False),
        ("opaque-tool-id", None, False, "completed", True),
        ("opaque-tool-id", "invalid", True, "unknown", False),
        ("different-id", False, True, "in_progress", False),
    ],
)
def test_claude_stream_json_tool_calls_require_matching_successful_result(
    tmp_path: Path,
    result_id: str,
    is_error: object,
    include_error_flag: bool,
    expected_status: str,
    expected_complete: bool,
) -> None:
    output = tmp_path / "claude-provider.jsonl"
    private_marker = "PRIVATE_CLAUDE_TOOL_PAYLOAD"
    result_block = {
        "type": "tool_result",
        "tool_use_id": result_id,
        "content": [{"type": "text", "text": private_marker}],
    }
    if include_error_flag:
        result_block["is_error"] = is_error
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "opaque-tool-id",
                        "name": "mcp__cao-mcp-server__cao_ask_user",
                        "input": {"prompt": private_marker},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {"content": [result_block]},
        },
    ]
    output.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )

    summary = journey._provider_event_summary(output)

    assert summary["mcp_tool_calls"] == [
        {
            "server": "cao-mcp-server",
            "tool": "cao_ask_user",
            "status": expected_status,
            "error_present": (is_error is True and result_id == "opaque-tool-id"),
            "result_present": result_id == "opaque-tool-id",
            "error_code": None,
        }
    ]
    assert summary["provider_event_counts"] == {"assistant": 1, "user": 1}
    assert summary["provider_item_type_counts"] == {
        "tool_result": 1,
        "tool_use": 1,
    }
    assert journey._provider_tool_completed(output, "cao_ask_user") is expected_complete
    assert private_marker not in json.dumps(summary)


@pytest.mark.parametrize(
    "tool_name",
    ["mcp__other-server__unapproved_tool", "Bash"],
)
def test_claude_stream_json_records_unapproved_tools_as_other(
    tmp_path: Path, tool_name: str
) -> None:
    output = tmp_path / "claude-provider.jsonl"
    private_marker = "PRIVATE_UNAPPROVED_TOOL_INPUT"
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "opaque-tool-id",
                        "name": tool_name,
                        "input": {"value": private_marker},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "opaque-tool-id",
                        "content": [],
                    }
                ]
            },
        },
    ]
    output.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )

    summary = journey._provider_event_summary(output)

    assert summary["mcp_tool_calls"] == [
        {
            "server": "other",
            "tool": "other",
            "status": "completed",
            "error_present": False,
            "result_present": True,
            "error_code": None,
        }
    ]
    assert journey._provider_tool_completed(output, "cao_ask_user") is False
    assert private_marker not in json.dumps(summary)


def test_ack_identity_diagnostics_compare_receipts_without_emitting_private_values() -> None:
    private_values = (
        "PRIVATE_GROUP_ID",
        "PRIVATE_QUESTION_ID_A",
        "PRIVATE_QUESTION_ID_B",
        "PRIVATE_RECEIPT_A",
        "PRIVATE_RECEIPT_B",
    )
    returned_answers = [
        {"question_id": private_values[1], "version": 2, "receipt": private_values[3]},
        {"question_id": private_values[2], "version": 4, "receipt": private_values[4]},
    ]
    matching = journey._ack_identity_match_diagnostics(
        created_group_id=private_values[0],
        waited_group_id=private_values[0],
        waited_answers=returned_answers,
        ack_group_id=private_values[0],
        acknowledgements=returned_answers,
    )
    mismatching = journey._ack_identity_match_diagnostics(
        created_group_id=private_values[0],
        waited_group_id=private_values[0],
        waited_answers=returned_answers,
        ack_group_id="ANOTHER_PRIVATE_GROUP",
        acknowledgements=[
            {"question_id": private_values[2], "version": 3, "receipt": "WRONG_RECEIPT"},
            {"question_id": private_values[1], "version": 2, "receipt": private_values[3]},
        ],
    )

    assert matching == {
        "comparison_available": True,
        "ack_group_matches_created_group": True,
        "ack_group_matches_wait_group": True,
        "ack_count_matches_returned_answers": True,
        "ack_question_ids_match_returned_answers": True,
        "ack_versions_match_returned_answers": True,
        "ack_receipts_match_returned_answers": True,
    }
    assert mismatching == {
        "comparison_available": True,
        "ack_group_matches_created_group": False,
        "ack_group_matches_wait_group": False,
        "ack_count_matches_returned_answers": True,
        "ack_question_ids_match_returned_answers": True,
        "ack_versions_match_returned_answers": False,
        "ack_receipts_match_returned_answers": False,
    }
    encoded_diagnostics = json.dumps({"matching": matching, "mismatching": mismatching})
    assert all(
        value not in encoded_diagnostics for value in (*private_values, "ANOTHER_PRIVATE_GROUP")
    )


@pytest.mark.parametrize("ack_group_matches", [True, False])
def test_service_identity_probe_tracks_real_boundary_without_retaining_values(
    ack_group_matches: bool,
) -> None:
    class Service:
        def create_group(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(group_id="PRIVATE_CREATED_GROUP")

        async def wait_for_answers(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                group_id="PRIVATE_CREATED_GROUP",
                state="ready",
                answers=(
                    SimpleNamespace(
                        question_id="PRIVATE_QUESTION",
                        version=6,
                        receipt="PRIVATE_OPAQUE_RECEIPT",
                    ),
                ),
            )

        def acknowledge_answers(self, *_args: object, **_kwargs: object) -> tuple[str, ...]:
            return ("acknowledged",)

    service = Service()
    safe_diagnostics = journey._instrument_ack_identity_service(service)
    service.create_group(None, None)
    ready = asyncio.run(service.wait_for_answers(None, "PRIVATE_CREATED_GROUP"))
    acknowledgements = [
        {
            "question_id": ready.answers[0].question_id,
            "version": ready.answers[0].version,
            "receipt": ready.answers[0].receipt,
        }
    ]
    ack_group_id = "PRIVATE_CREATED_GROUP" if ack_group_matches else "PRIVATE_WRONG_GROUP"
    service.acknowledge_answers(None, ack_group_id, acknowledgements)

    diagnostics = safe_diagnostics()
    assert diagnostics["create_group_seen"] is True
    assert diagnostics["wait_call_count"] == diagnostics["ready_wait_count"] == 1
    assert diagnostics["ack_call_count"] == 1
    check = diagnostics["ack_checks"][0]
    assert check["comparison_available"] is True
    assert check["ack_group_matches_created_group"] is ack_group_matches
    assert check["ack_group_matches_wait_group"] is ack_group_matches
    assert check["ack_count_matches_returned_answers"] is True
    assert check["ack_question_ids_match_returned_answers"] is True
    assert check["ack_versions_match_returned_answers"] is True
    assert check["ack_receipts_match_returned_answers"] is True
    encoded_diagnostics = json.dumps(diagnostics)
    assert "PRIVATE_" not in encoded_diagnostics


def test_tool_error_summary_accepts_only_fixed_error_codes() -> None:
    private_marker = "PRIVATE_TOOL_ERROR_DETAIL"

    assert journey._safe_tool_error_code({"error": {"code": "forbidden"}}) == "forbidden"
    assert journey._safe_tool_error_code({"error": {"code": -32602}}) == ("protocol_invalid_params")
    assert journey._safe_tool_error_code({"message": private_marker}) is None
    assert private_marker not in str(journey._safe_tool_error_code({"message": private_marker}))
    assert journey._safe_tool_error_code("Invalid params: " + private_marker) == (
        "protocol_invalid_params"
    )
    assert (
        journey._safe_tool_error_code({"message": "Invalid params: " + private_marker})
        == "protocol_invalid_params"
    )
    assert journey._safe_tool_error_code("connection closed: " + private_marker) == (
        "mcp_connection_closed"
    )


def test_unclassified_error_diagnostic_keeps_only_private_mcp_error_fields(
    tmp_path: Path,
) -> None:
    private_marker = "PRIVATE_UNCLASSIFIED_MCP_ERROR"
    output = tmp_path / "provider.jsonl"
    output.write_text(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "id": "private-call-id",
                    "server": "cao-mcp-server",
                    "tool": "cao_ask_user",
                    "status": "failed",
                    "arguments": {"prompt": "PRIVATE_PROMPT"},
                    "error": {"message": "provider-specific failure: " + private_marker},
                    "result": {"content": [{"text": "PRIVATE_RESULT"}]},
                },
            }
        ),
        encoding="utf-8",
    )
    diagnostic_dir = tmp_path / "private-diagnostics"

    diagnostic_path = journey._preserve_unclassified_mcp_errors(
        output, diagnostic_dir=diagnostic_dir
    )

    assert diagnostic_path is not None
    assert diagnostic_dir.stat().st_mode & 0o777 == 0o700
    assert diagnostic_path.stat().st_mode & 0o777 == 0o600
    saved = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert saved == {
        "format": "cao-private-mcp-error-diagnostic-v1",
        "calls": [
            {
                "event_type": "item.completed",
                "tool": "cao_ask_user",
                "status": "failed",
                "error": {"message": "provider-specific failure: " + private_marker},
            }
        ],
    }
    assert "PRIVATE_PROMPT" not in json.dumps(saved)
    assert "PRIVATE_RESULT" not in json.dumps(saved)
    assert "private-call-id" not in json.dumps(saved)


def test_classified_mcp_errors_do_not_create_private_diagnostic_files(
    tmp_path: Path,
) -> None:
    output = tmp_path / "provider.jsonl"
    output.write_text(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "server": "cao-mcp-server",
                    "tool": "cao_ask_user",
                    "error": {"message": "Invalid params: private"},
                },
            }
        ),
        encoding="utf-8",
    )
    diagnostic_dir = tmp_path / "private-diagnostics"

    assert journey._preserve_unclassified_mcp_errors(output, diagnostic_dir=diagnostic_dir) is None
    assert not diagnostic_dir.exists()


def test_claude_config_dir_sources_private_login_from_explicit_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private_original_home = tmp_path / "original-home"
    wrong_source = private_original_home / ".claude" / ".credentials.json"
    wrong_source.parent.mkdir(parents=True, mode=0o700)
    wrong_source.write_text("fixture-origin-default", encoding="utf-8")
    wrong_source.chmod(0o600)

    explicit_source_dir = tmp_path / "windows-mounted-claude-config"
    explicit_source_dir.mkdir(mode=0o700)
    explicit_source = explicit_source_dir / ".credentials.json"
    explicit_source.write_text("fixture-origin-explicit", encoding="utf-8")
    explicit_source.chmod(0o600)
    explicit_bytes = explicit_source.read_bytes()

    monkeypatch.setattr(journey, "ORIGINAL_HOME", private_original_home)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(explicit_source_dir))
    run_root = tmp_path / "provider-run"
    run_root.mkdir(mode=0o700)

    child_environment = journey._base_cli_env(run_root, "claude_code")
    copied = run_root / "claude-home" / ".credentials.json"

    assert child_environment["CLAUDE_CONFIG_DIR"] == str(run_root / "claude-home")
    assert copied.read_bytes() == explicit_bytes
    assert copied.stat().st_mode & 0o777 == 0o600
    assert explicit_source.read_bytes() == explicit_bytes
    assert wrong_source.read_text(encoding="utf-8") == "fixture-origin-default"


def test_codex_mcp_result_diagnostic_uses_wire_fields_without_payload(
    tmp_path: Path,
) -> None:
    from mcp.types import CallToolResult, TextContent

    output = tmp_path / "codex-mcp-result.jsonl"
    private_marker = "PRIVATE_MCP_RESULT_BODY"
    wire_result = CallToolResult(
        content=[TextContent(type="text", text="Opaque detail " + private_marker)],
        structuredContent={"error": {"code": "forbidden"}},
        isError=True,
    ).model_dump(by_alias=True, exclude_none=True)
    assert wire_result["isError"] is True
    assert "structuredContent" in wire_result
    event = {
        "type": "item.completed",
        "item": {
            "id": "opaque-call-id",
            "type": "mcp_tool_call",
            "server": "cao-mcp-server",
            "tool": "cao_wait_for_user_answers",
            "arguments": {"prompt": private_marker},
            "status": "completed",
            "result": wire_result,
        },
    }
    output.write_text(json.dumps(event) + "\n", encoding="utf-8")

    summary = journey._provider_event_summary(output)
    call = summary["mcp_tool_calls"][0]
    assert call["status"] == "completed"
    assert call["error_present"] is True
    assert journey._provider_tool_completed(output, "cao_wait_for_user_answers") is False
    assert call["mcp_result_shape"] == "call_tool_result"
    assert call["mcp_is_error"] == "true"
    assert call["mcp_result_success"] == "false"
    assert call["mcp_content_block_types"] == ["text"]
    assert call["mcp_structured_content_present"] is True
    assert call["mcp_result_error_code"] == "forbidden"

    safe = journey._safe_provider_receipt({"mcp_tool_calls": [call]})
    safe_call = safe["mcp_tool_calls"][0]
    assert safe_call["error_present"] is True
    assert safe_call["mcp_is_error"] == "true"
    assert safe_call["mcp_result_success"] == "false"
    assert safe_call["mcp_result_error_code"] == "forbidden"
    assert private_marker not in json.dumps(summary)
    assert private_marker not in json.dumps(safe)


@pytest.mark.parametrize("wire", ["structuredContent", "structured_content", "text"])
@pytest.mark.parametrize("cause", ["http_error", "connection_error", "unexpected_error"])
def test_unavailable_cause_survives_wire_event_and_receipt(tmp_path, wire, cause):
    import requests

    from cli_agent_orchestrator.mcp_server import server

    response = requests.Response()
    response.status_code = 503
    errors = {
        "http_error": requests.HTTPError(response=response),
        "connection_error": requests.ConnectionError("PRIVATE_ERROR"),
        "unexpected_error": RuntimeError("PRIVATE_ERROR"),
    }
    failure = server._interaction_tool_error(errors[cause]).model_dump(mode="json")
    result = (
        {"content": [{"type": "text", "text": json.dumps(failure)}]}
        if wire == "text"
        else {wire: failure}
    )
    output = tmp_path / "event.jsonl"
    output.write_text(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "private-id",
                    "type": "mcp_tool_call",
                    "server": "cao-mcp-server",
                    "tool": "cao_wait_for_user_answers",
                    "status": "completed",
                    "result": result,
                },
            }
        )
        + "\n"
    )
    summary = journey._provider_event_summary(output)
    safe = journey._safe_provider_receipt(summary)
    assert safe["mcp_tool_calls"][0]["mcp_unavailable_cause"] == cause
    assert safe["mcp_tool_calls"][0]["mcp_result_error_code"] == "unavailable"
    assert "PRIVATE_ERROR" not in json.dumps(summary)
    assert failure["error"]["detail"] not in json.dumps(safe)


@pytest.mark.parametrize(
    "detail", ["PRIVATE_VALUE", None, [], "The CAO interaction API is unreachable. PRIVATE_VALUE"]
)
def test_unavailable_cause_does_not_echo_unknown_detail(detail):
    result = {"structuredContent": {"error": {"code": "unavailable", "detail": detail}}}
    diagnostics = journey._safe_mcp_result_diagnostics(result)
    assert diagnostics["mcp_unavailable_cause"] is None
    assert "PRIVATE_VALUE" not in json.dumps(diagnostics)
    safe = journey._safe_provider_receipt(
        {"mcp_tool_calls": [{"mcp_unavailable_cause": ["PRIVATE_VALUE"]}]}
    )
    assert safe["mcp_tool_calls"][0]["mcp_unavailable_cause"] is None


def test_unavailable_cause_rejects_conflicting_or_unbounded_results():
    unreachable = {
        "error": {"code": "unavailable", "detail": "The CAO interaction API is unreachable."}
    }
    http_error = {
        "error": {
            "code": "unavailable",
            "detail": "The interaction service could not complete the request.",
        }
    }
    for value in (
        {
            "structuredContent": unreachable,
            "content": [{"type": "text", "text": json.dumps(http_error)}],
        },
        [unreachable] + [{}] * 15 + [http_error],
        {"content": [unreachable] + [{}] * 15 + [http_error]},
        {"error": {"code": "forbidden", "detail": "The CAO interaction API is unreachable."}},
    ):
        assert journey._safe_mcp_result_diagnostics(value)["mcp_unavailable_cause"] is None


def test_codex_mcp_result_false_and_absent_flags_preserve_success(
    tmp_path: Path,
) -> None:
    from mcp.types import CallToolResult, TextContent

    private_marker = "PRIVATE_ERROR_TEXT_MUST_NOT_SET_PROTOCOL_ERROR"
    error_text = TextContent(type="text", text="internal server error detail " + private_marker)
    cases = (
        (
            "false",
            CallToolResult(content=[error_text], isError=False).model_dump(
                by_alias=True, exclude_none=True
            ),
            "false",
        ),
        (
            "absent",
            CallToolResult.model_validate({"content": [error_text]}).model_dump(
                by_alias=True, exclude_none=True, exclude_unset=True
            ),
            "absent",
        ),
    )
    for name, wire_result, expected_state in cases:
        output = tmp_path / f"codex-mcp-{name}-result.jsonl"
        assert ("isError" in wire_result) is (name == "false")
        event = {
            "type": "item.completed",
            "item": {
                "id": f"opaque-{name}",
                "type": "mcp_tool_call",
                "server": "cao-mcp-server",
                "tool": "cao_wait_for_user_answers",
                "status": "completed",
                "result": wire_result,
            },
        }
        output.write_text(json.dumps(event) + "\n", encoding="utf-8")

        call = journey._provider_event_summary(output)["mcp_tool_calls"][0]
        assert call["status"] == "completed"
        assert call["mcp_is_error"] == expected_state
        assert call["mcp_result_error_code"] == "mcp_server_error"
        assert call["error_present"] is False
        assert journey._provider_tool_completed(output, "cao_wait_for_user_answers") is True
        safe = journey._safe_provider_receipt({"mcp_tool_calls": [call]})
        assert safe["mcp_tool_calls"][0]["error_present"] is False
        assert private_marker not in json.dumps(call)
        assert private_marker not in json.dumps(safe)


def test_codex_mcp_snake_case_and_invalid_error_flags_fail_closed(
    tmp_path: Path,
) -> None:
    cases = (
        ("snake-true", {"is_error": True}, "true"),
        ("contradictory", {"isError": True, "is_error": False}, "invalid"),
        ("invalid-type", {"isError": "true"}, "invalid"),
    )
    for name, flag_fields, expected_state in cases:
        output = tmp_path / f"codex-mcp-{name}.jsonl"
        event = {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "server": "cao-mcp-server",
                "tool": "cao_wait_for_user_answers",
                "status": "completed",
                "result": {"content": [], **flag_fields},
            },
        }
        output.write_text(json.dumps(event) + "\n", encoding="utf-8")

        call = journey._provider_event_summary(output)["mcp_tool_calls"][0]
        assert call["status"] == "completed"
        assert call["mcp_is_error"] == expected_state
        assert call["error_present"] is True
        assert journey._provider_tool_completed(output, "cao_wait_for_user_answers") is False
        safe = journey._safe_provider_receipt({"mcp_tool_calls": [call]})
        assert safe["mcp_tool_calls"][0]["error_present"] is True


def test_codex_mcp_result_diagnostic_keeps_invalid_flags_as_enums(
    tmp_path: Path,
) -> None:
    output = tmp_path / "codex-invalid-mcp-result.jsonl"
    private_marker = "PRIVATE_UNKNOWN_RESULT_FIELD"
    event = {
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call",
            "server": "cao-mcp-server",
            "tool": "cao_wait_for_user_answers",
            "status": "completed",
            "result": {
                "content": [{"type": private_marker, "text": private_marker}],
                "isError": "true",
                "structuredContent": private_marker,
                "unapproved": {"body": private_marker},
            },
        },
    }
    output.write_text(json.dumps(event) + "\n", encoding="utf-8")

    call = journey._provider_event_summary(output)["mcp_tool_calls"][0]
    assert call["mcp_result_shape"] == "call_tool_result"
    assert call["mcp_is_error"] == "invalid"
    assert call["mcp_result_success"] == "unknown"
    assert call["mcp_content_block_types"] == ["other"]
    assert call["mcp_structured_content_present"] is True
    assert call["mcp_result_error_code"] is None
    assert private_marker not in json.dumps(call)
