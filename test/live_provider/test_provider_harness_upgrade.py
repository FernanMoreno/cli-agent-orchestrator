"""Offline privacy and configuration tests for the provider-harness candidate."""

from __future__ import annotations

import json
import os
from pathlib import Path
from test.live_provider import interaction_cli_journey as journey

import pytest

pytestmark = pytest.mark.offline_database


def test_provider_usage_summary_accepts_only_allowlisted_numeric_token_counters(
    tmp_path: Path,
) -> None:
    output = tmp_path / "events.jsonl"
    prompt_secret = "PROMPT_SENTINEL_MUST_NOT_ESCAPE"
    events = [
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 11,
                "cached_input_tokens": 5,
                "output_tokens": 7,
                "reasoning_output_tokens": 3,
                "api_key": "TOKEN_SENTINEL_MUST_NOT_ESCAPE",
                "prompt": prompt_secret,
            },
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": True,
                "output_tokens": -1,
                "total_tokens": "ANSWER_SENTINEL_MUST_NOT_ESCAPE",
            },
        },
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": prompt_secret},
        },
    ]
    output.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

    summary = journey._provider_event_summary(output)
    serialized = json.dumps(summary, sort_keys=True)

    assert summary["model_usage_counts"] == {
        "cached_input_tokens": 5,
        "input_tokens": 11,
        "output_tokens": 7,
        "reasoning_output_tokens": 3,
    }
    assert summary["model_usage_records"] == 1
    for marker in (
        prompt_secret,
        "TOKEN_SENTINEL_MUST_NOT_ESCAPE",
        "ANSWER_SENTINEL_MUST_NOT_ESCAPE",
    ):
        assert marker not in serialized


def test_failure_diagnostic_flags_are_fixed_boolean_categories() -> None:
    flags = journey._failure_diagnostic_flags(
        "Failed to spawn MCP server: permission denied, raw diagnostic token SHOULD_NOT_ESCAPE"
    )

    assert flags["mcp_spawn_failed"] is True
    assert flags["filesystem_write_denied"] is True
    assert all(type(value) is bool for value in flags.values())
    assert "SHOULD_NOT_ESCAPE" not in json.dumps(flags)
    assert set(flags) == set(journey._FAILURE_DIAGNOSTIC_NAMES)


def test_mcp_child_environment_is_source_explicit_and_private(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original_home = "/operator/home"
    original_codex_home = "/operator/codex-home"
    monkeypatch.setenv("HOME", original_home)
    monkeypatch.setenv("CODEX_HOME", original_codex_home)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("OPENAI_API_KEY", "TOKEN_SENTINEL_MUST_NOT_ESCAPE")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "TOKEN_SENTINEL_MUST_NOT_ESCAPE")
    run_root = tmp_path / "private-run"
    (run_root / "tmp").mkdir(parents=True, mode=0o700)
    (run_root / "cao").mkdir(mode=0o700)

    env = journey._mcp_child_environment(run_root, 43123, "terminal-safe")

    assert env["PYTHONPATH"] == str(journey._source_root() / "src")
    assert env["HOME"] == str(run_root / "mcp-home")
    assert env["HOME"] != original_home
    assert "CODEX_HOME" not in env
    assert original_codex_home not in json.dumps(env)
    assert env["CAO_HOME_DIR"] == str(run_root / "cao")
    assert env["CAO_DATABASE_CONFIG_FILE"] == str(run_root / "cao" / "database-config.json")
    assert env["CAO_API_HOST"] == "127.0.0.1"
    assert env["CAO_API_PORT"] == "43123"
    assert env["CAO_TERMINAL_ID"] == "terminal-safe"
    assert env["TMPDIR"] == str(run_root / "tmp")
    assert not any(key.endswith("_API_KEY") for key in env)
    assert "TOKEN_SENTINEL_MUST_NOT_ESCAPE" not in json.dumps(env)


def test_mcp_child_imports_this_candidate_from_a_private_home(
    tmp_path: Path,
) -> None:
    import subprocess
    import sys

    source = Path(journey._source_pythonpath()).resolve()
    run_root = tmp_path / "private-run"
    run_root.mkdir(mode=0o700)
    child_home = run_root / "mcp-home"
    env = journey._mcp_child_environment(run_root, 43123, "terminal-safe")
    program = (
        "import json, os; from pathlib import Path; "
        "import cli_agent_orchestrator; "
        "module = Path(cli_agent_orchestrator.__file__).resolve(); "
        f"source = Path({str(source)!r}).resolve(); "
        f"home = Path({str(child_home)!r}).resolve(); "
        "print(json.dumps({"
        "'candidate_source': module.samefile(source / 'cli_agent_orchestrator' / '__init__.py'), "
        "'private_home': Path.home().resolve() == home, "
        "'codex_home_absent': 'CODEX_HOME' not in os.environ"
        "}))"
    )

    result = subprocess.run(
        [sys.executable, "-B", "-c", program],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        cwd=tmp_path,
        env=env,
    )

    assert json.loads(result.stdout) == {
        "candidate_source": True,
        "private_home": True,
        "codex_home_absent": True,
    }
    assert child_home.stat().st_mode & 0o777 == 0o700


def test_mcp_source_path_fails_closed_when_candidate_source_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(journey, "_source_root", lambda: tmp_path / "missing-candidate")

    with pytest.raises(RuntimeError, match="candidate_source_unavailable"):
        journey._source_pythonpath()


def test_codex_overlay_uses_staged_auth_and_operator_packages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile_home = tmp_path / "staged-profile"
    staged_auth = profile_home / ".codex" / "auth.json"
    staged_auth.parent.mkdir(parents=True, mode=0o700)
    staged_auth.write_text('{"auth_mode":"chatgpt"}', encoding="utf-8")
    staged_auth.chmod(0o600)

    operator_home = tmp_path / "operator-home"
    codex_source = operator_home / ".codex"
    packages = codex_source / "packages"
    packages.mkdir(parents=True, mode=0o700)
    (codex_source / "auth.json").write_text(
        '{"auth_mode":"api_key","OPENAI_API_KEY":"ORIGINAL_SENTINEL"}',
        encoding="utf-8",
    )
    (codex_source / "config.toml").write_text("api_key = 'UNSTAGED_SENTINEL'\n", encoding="utf-8")

    monkeypatch.setattr(journey, "ORIGINAL_HOME", profile_home)
    monkeypatch.setenv("CAO_PRIVATE_PROFILE_HOME", str(profile_home))
    monkeypatch.setenv("HOME", str(operator_home))
    monkeypatch.delenv("CODEX_HOME", raising=False)

    run_root = tmp_path / "run"
    run_root.mkdir(mode=0o700)
    copied_identity: list[tuple[int, ...]] = []
    source, private_home = journey._prepare_codex_home_overlay(
        run_root, auth_identity=copied_identity
    )

    assert source == codex_source
    assert (private_home / "auth.json").read_text(encoding="utf-8") == '{"auth_mode":"chatgpt"}'
    assert (private_home / "auth.json").stat().st_mode & 0o777 == 0o600
    assert not (private_home / "config.toml").exists()
    assert "ORIGINAL_SENTINEL" not in (private_home / "auth.json").read_text(encoding="utf-8")
    assert {path.name for path in private_home.iterdir()} == {"auth.json", "packages"}
    assert copied_identity == [journey._source_file_identity(staged_auth, require_private=True)]


def test_codex_home_source_honors_configured_packages_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    operator_home = tmp_path / "operator-home"
    monkeypatch.setattr(journey, "ORIGINAL_HOME", tmp_path / "staged-profile")
    monkeypatch.setenv("HOME", str(operator_home))
    monkeypatch.setenv("CODEX_HOME", "~/configured-codex")

    assert journey._codex_home_source() == operator_home / "configured-codex"


def test_private_auth_copy_rejects_a_source_replaced_by_a_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "auth.json"
    original = tmp_path / "auth-original.json"
    attacker = tmp_path / "attacker.json"
    destination = tmp_path / "run" / "private" / "auth.json"
    source.write_text('{"auth_mode":"chatgpt"}', encoding="utf-8")
    source.chmod(0o600)
    attacker.write_text("PRIVATE_AUTH_SENTINEL", encoding="utf-8")
    attacker.chmod(0o600)
    real_open = os.open
    replaced = False

    def replace_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if Path(path) == source and not replaced:
            replaced = True
            source.rename(original)
            source.symlink_to(attacker)
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(journey.os, "open", replace_before_open)

    with pytest.raises(OSError):
        journey._copy_private(source, destination, require_private_source=True)

    assert replaced
    assert not destination.exists()
    assert destination.parent.stat().st_mode & 0o777 == 0o700
    assert attacker.read_text(encoding="utf-8") == "PRIVATE_AUTH_SENTINEL"


def test_private_auth_copy_requires_owner_only_source_permissions(
    tmp_path: Path,
) -> None:
    source = tmp_path / "auth.json"
    destination = tmp_path / "run" / "private" / "auth.json"
    source.write_text('{"auth_mode":"chatgpt"}', encoding="utf-8")
    source.chmod(0o644)

    assert not journey._copy_private(source, destination, require_private_source=True)
    assert not destination.exists()


def test_cleanup_actions_continue_and_private_root_is_preserved_on_failure(
    tmp_path: Path,
) -> None:
    failures: set[str] = set()
    actions: list[str] = []
    root = tmp_path / "owned-run"
    root.mkdir(mode=0o700)
    (root / "raw-private-log").write_text("private", encoding="utf-8")

    def fail() -> None:
        actions.append("first")
        raise RuntimeError("cleanup failure")

    assert not journey._cleanup_action(failures, "provider_cleanup_failed", fail)
    assert journey._cleanup_action(
        failures, "environment_restore_failed", lambda: actions.append("second")
    )
    removed = journey._remove_private_run_root(root, process_stopped=True, failures=failures)

    assert actions == ["first", "second"]
    assert not removed
    assert root.exists()
    assert "private_files_preserved" in failures
    assert failures == {"provider_cleanup_failed", "private_files_preserved"}


def test_private_root_removal_reports_actual_filesystem_result(tmp_path: Path) -> None:
    root = tmp_path / "owned-run"
    root.mkdir(mode=0o700)
    (root / "private").write_text("private", encoding="utf-8")
    failures: set[str] = set()

    removed = journey._remove_private_run_root(root, process_stopped=True, failures=failures)

    assert removed
    assert not root.exists()
    assert not failures


def test_safe_public_receipt_projects_only_allowlisted_fields() -> None:
    receipt = journey._safe_provider_receipt(
        {
            "provider": "codex",
            "result": "cli_exited_without_ack",
            "version": "codex-cli 0.0.1",
            "question_count": 0,
            "provider_event_counts": {"turn.completed": 1, "private.event": 200},
            "provider_item_type_counts": {"mcp_tool_call": 1, "private_item": 300},
            "model_usage_counts": {"input_tokens": 9, "private_counter": 400},
            "mcp_tool_names": ["cao_ask_user", "private_tool"],
            "mcp_tool_calls": [
                {
                    "server": "cao-mcp-server",
                    "tool": "cao_ask_user",
                    "status": "completed",
                    "error_present": False,
                    "result_present": True,
                    "error_code": None,
                    "arguments": "PROMPT_SENTINEL_MUST_NOT_ESCAPE",
                },
                {
                    "server": "private-server",
                    "tool": "private_tool",
                    "status": "failed",
                    "error_present": True,
                    "result_present": False,
                    "error_code": "PROMPT_SENTINEL_MUST_NOT_ESCAPE",
                },
            ],
            "failure_diagnostics": {"mcp_spawn_failed": True, "private": "secret"},
            "provider_output": "PROMPT_SENTINEL_MUST_NOT_ESCAPE",
            "auth_token": "TOKEN_SENTINEL_MUST_NOT_ESCAPE",
        }
    )
    serialized = json.dumps(receipt, sort_keys=True)

    assert receipt["provider_event_counts"] == {"turn.completed": 1}
    assert receipt["provider_item_type_counts"] == {"mcp_tool_call": 1}
    assert receipt["model_usage_counts"] == {"input_tokens": 9}
    assert receipt["mcp_tool_names"] == ["cao_ask_user"]
    assert receipt["mcp_tool_calls"] == [
        {
            "server": "cao-mcp-server",
            "tool": "cao_ask_user",
            "status": "completed",
            "error_present": False,
            "result_present": True,
            "error_code": None,
        },
        {
            "server": "other",
            "tool": "other",
            "status": "failed",
            "error_present": True,
            "result_present": False,
            "error_code": None,
        },
    ]
    assert receipt["failure_diagnostics"] == {"mcp_spawn_failed": True}
    assert all(
        marker not in serialized
        for marker in (
            "PROMPT_SENTINEL_MUST_NOT_ESCAPE",
            "TOKEN_SENTINEL_MUST_NOT_ESCAPE",
            "private.event",
            "private_item",
            "private_tool",
            "private_counter",
        )
    )


@pytest.mark.parametrize(
    ("delivery_states", "expected_delivery_states"),
    [
        (
            ["agent_acknowledged", "agent_acknowledged"],
            ["agent_acknowledged", "agent_acknowledged"],
        ),
        (["unknown", "agent_acknowledged"], ["unknown", "agent_acknowledged"]),
        (
            ["PRIVATE_DELIVERY_STATE_MUST_NOT_ESCAPE", "agent_acknowledged"],
            ["agent_acknowledged"],
        ),
    ],
    ids=("duplicate-acknowledgements", "mixed-state-order", "private-state-filtered"),
)
def test_safe_receipt_preserves_question_delivery_states_and_filters_private_values(
    delivery_states: list[str], expected_delivery_states: list[str]
) -> None:
    receipt = journey._safe_provider_receipt(
        {
            "provider": "codex",
            "question_count": 2,
            "delivery_states": delivery_states,
            "mcp_tool_names": ["cao_ask_user", "cao_ask_user"],
        }
    )
    serialized = json.dumps(receipt, sort_keys=True)

    assert receipt["delivery_states"] == expected_delivery_states
    assert receipt["mcp_tool_names"] == ["cao_ask_user"]
    assert "PRIVATE_DELIVERY_STATE_MUST_NOT_ESCAPE" not in serialized


def test_safe_receipt_keeps_only_the_approved_opencode_model_literal() -> None:
    approved = journey._safe_provider_receipt(
        {
            "provider": "opencode_cli",
            "requested_model": journey.OPENCODE_INTERACTION_MODEL,
            "result": "cli_exited_without_ack",
        }
    )
    unapproved = journey._safe_provider_receipt(
        {
            "provider": "opencode_cli",
            "requested_model": "opencode-go/unreviewed-model",
            "result": "cli_exited_without_ack",
        }
    )

    assert approved["requested_model"] == "opencode-go/longcat-2.5-preview-free"
    assert "requested_model" not in unapproved


@pytest.mark.parametrize(
    ("field", "provider", "sentinel"),
    [
        ("version", "codex", "codex-cli 0.162.1 private_answer_secret"),
        ("result", "codex", "unexpected_private_answer_secret"),
        ("phase", "codex", "provider_private_answer_secret"),
        ("error_type", "codex", "PrivateAnswerSecret"),
    ],
)
def test_safe_receipt_replaces_arbitrary_public_strings_with_fixed_labels(
    field: str, provider: str, sentinel: str
) -> None:
    result = {
        "provider": provider,
        "version": "codex-cli 0.162.1",
        "result": "cli_exited_without_ack",
        "phase": "provider_launch",
        "error_type": "RuntimeError",
    }
    result[field] = sentinel

    receipt = journey._safe_provider_receipt(result)
    serialized = json.dumps(receipt, sort_keys=True)

    assert receipt[field] == "unknown"
    assert sentinel not in serialized


@pytest.mark.parametrize(
    ("provider", "version"),
    [
        ("codex", "codex-cli 0.162.1"),
        ("claude_code", "2.1.80 (Claude Code)"),
        ("claude_code", "Claude Code 2.1.80"),
        ("opencode_cli", "opencode v2.0.18"),
    ],
)
def test_safe_receipt_preserves_only_known_provider_version_formats(
    provider: str, version: str
) -> None:
    receipt = journey._safe_provider_receipt({"provider": provider, "version": version})

    assert receipt["version"] == version


def test_safe_receipt_keeps_http_status_and_fixed_exception_categories() -> None:
    http_failure = journey._safe_provider_receipt(
        {"provider": "opencode_cli", "result": "human_answer_http_422"}
    )
    known_exception = journey._safe_provider_receipt(
        {"provider": "codex", "error_type": "RuntimeError"}
    )
    unknown_exception = journey._safe_provider_receipt(
        {"provider": "codex", "error_type": "PrivateAnswerSecret"}
    )

    assert http_failure["result"] == "human_answer_http_422"
    assert known_exception["error_type"] == "RuntimeError"
    assert unknown_exception["error_type"] == "unknown"


def test_safe_receipt_keeps_known_setup_results_and_cleanup_categories() -> None:
    receipt = journey._safe_provider_receipt(
        {
            "provider": "opencode_cli",
            "result": "mcp_server_configuration_invalid",
            "cleanup_status": "incomplete",
            "cleanup_failure_categories": [
                "database_target_close_failed",
                "claude_auth_archive_incomplete",
                "private_files_preserved",
                "private_answer_secret",
            ],
            "operator_auth_unchanged": False,
        }
    )

    assert receipt["result"] == "mcp_server_configuration_invalid"
    assert receipt["cleanup_status"] == "incomplete"
    assert receipt["cleanup_failure_categories"] == [
        "claude_auth_archive_incomplete",
        "database_target_close_failed",
        "private_files_preserved",
    ]
    assert receipt["operator_auth_unchanged"] is False


def test_safe_receipt_keeps_private_scope_attestation_without_paths() -> None:
    attestation = {
        "run_root_private": True,
        "cao_home_is_run_child": True,
        "agent_store_is_cao_child": True,
        "config_is_run_private": True,
        "bindings_active": True,
        "bindings_restored": True,
    }
    receipt = journey._safe_provider_receipt(
        {
            "provider": "opencode_cli",
            "private_scope_verified": True,
            "scope_attestation": attestation,
            "private_scope_path": "/operator/secret-run",
        }
    )

    assert receipt["private_scope_verified"] is True
    assert receipt["scope_attestation"] == attestation
    assert journey._scope_attestation_is_valid(receipt, "opencode_cli") is True
    invalid = dict(attestation)
    invalid["bindings_restored"] = False
    assert (
        journey._scope_attestation_is_valid(
            {
                "provider": "opencode_cli",
                "private_scope_verified": True,
                "scope_attestation": invalid,
            },
            "opencode_cli",
        )
        is False
    )
    assert "private_scope_path" not in receipt
    assert "/operator/secret-run" not in repr(receipt)


@pytest.mark.parametrize(
    ("cleanup_overrides", "expected_exit"),
    [
        ({}, 0),
        ({"cleanup_status": "incomplete"}, 1),
        ({"temporary_files_removed": False}, 1),
        ({"temporary_files_removed": None}, 1),
        ({"operator_auth_unchanged": False}, 1),
        ({"cleanup_status": None}, 1),
        ({"operator_auth_unchanged": None}, 1),
    ],
)
def test_codex_main_requires_cleanup_and_auth_for_success_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    cleanup_overrides: dict[str, object],
    expected_exit: int,
) -> None:
    result = {
        "provider": "codex",
        "result": "completed_and_acknowledged",
        "cleanup_status": "complete",
        "temporary_files_removed": True,
        "operator_auth_unchanged": True,
    }
    result.update(cleanup_overrides)
    monkeypatch.setattr(journey, "run", lambda *_args, **_kwargs: result.copy())
    monkeypatch.setattr(journey, "_append_sanitized_evidence", lambda _receipt: True)
    monkeypatch.setattr(journey, "ORIGINAL_HOME", tmp_path)
    monkeypatch.setattr("sys.argv", ["interaction_cli_journey", "codex"])

    assert journey.main() == expected_exit
    printed = json.loads(capsys.readouterr().out)
    assert printed["result"] == "completed_and_acknowledged"


def test_claude_main_requires_cleanup_but_has_no_auth_integrity_field(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    result = {
        "provider": "claude_code",
        "result": "completed_and_acknowledged",
        "cleanup_status": "complete",
        "temporary_files_removed": True,
    }
    monkeypatch.setattr(journey, "run", lambda *_args, **_kwargs: result.copy())
    monkeypatch.setattr(journey, "_append_sanitized_evidence", lambda _receipt: True)
    monkeypatch.setattr(journey, "ORIGINAL_HOME", tmp_path)
    monkeypatch.setattr("sys.argv", ["interaction_cli_journey", "claude_code"])

    assert journey.main() == 0
    assert json.loads(capsys.readouterr().out)["result"] == "completed_and_acknowledged"


@pytest.mark.parametrize(
    ("cleanup_overrides", "expected_exit"),
    [
        ({}, 0),
        ({"cleanup_status": "incomplete"}, 1),
        ({"temporary_files_removed": False}, 1),
        ({"temporary_files_removed": None}, 1),
        ({"operator_auth_unchanged": False}, 1),
        ({"operator_auth_unchanged": None}, 1),
        ({"private_scope_verified": False}, 1),
        ({"cleanup_status": None}, 1),
    ],
)
def test_opencode_main_requires_cleanup_and_auth_for_success_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cleanup_overrides: dict[str, object],
    expected_exit: int,
) -> None:
    from test.live_provider import opencode_interaction_journey as opencode

    result = {
        "provider": "opencode_cli",
        "result": "completed_and_acknowledged",
        "cleanup_status": "complete",
        "temporary_files_removed": True,
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
    }
    result.update(cleanup_overrides)
    monkeypatch.setattr(opencode, "run", lambda *_args, **_kwargs: result.copy())
    monkeypatch.setattr(
        "sys.argv",
        [
            "opencode_interaction_journey",
            "--run-model",
            "--model",
            "opencode-go/longcat-2.5-preview-free",
        ],
    )

    assert opencode.main() == expected_exit
    printed = json.loads(capsys.readouterr().out)
    assert printed["result"] == "completed_and_acknowledged"


@pytest.mark.parametrize(
    ("cleanup_status", "temporary_files_removed", "expected_exit"),
    [("complete", True, 0), ("incomplete", True, 1), ("complete", False, 1)],
)
def test_codex_diagnostic_success_requires_verified_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    cleanup_status: str,
    temporary_files_removed: bool,
    expected_exit: int,
) -> None:
    result = {
        "provider": "codex",
        "result": "mcp_stdio_handshake",
        "required_tools_present": True,
        "metadata_call_error": False,
        "configured_enabled_tools_are_exact": True,
        "private_capability_file_available": True,
        "cleanup_status": cleanup_status,
        "temporary_files_removed": temporary_files_removed,
        "operator_auth_unchanged": True,
    }
    monkeypatch.setattr(journey, "run", lambda *_args, **_kwargs: result.copy())
    monkeypatch.setattr(journey, "_append_sanitized_evidence", lambda _receipt: True)
    monkeypatch.setattr(journey, "ORIGINAL_HOME", tmp_path)
    monkeypatch.setattr("sys.argv", ["interaction_cli_journey", "codex", "--diagnose-mcp"])

    assert journey.main() == expected_exit
    printed = json.loads(capsys.readouterr().out)
    assert printed["result"] == "mcp_stdio_handshake"


def test_diagnostic_mcp_environment_uses_private_home_and_drops_operator_auth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    operator_values = {
        "HOME": "/operator/home",
        "CODEX_HOME": "/operator/codex",
        "OPENAI_API_KEY": "OPENAI_SENTINEL",
        "ANTHROPIC_API_KEY": "ANTHROPIC_SENTINEL",
        "CAO_AUTH_LOCAL_TOKEN": "CAO_TOKEN_SENTINEL",
        "CAO_AUTH_LOCAL_TOKEN_FILE": "/operator/token-file",
        "CAO_AUTH_JWKS_URI": "https://operator.invalid/jwks",
        "CAO_AUTH_ISSUER": "operator-issuer-sentinel",
        "CAO_AUTH_AUDIENCE": "operator-audience-sentinel",
        "AUTH0_DOMAIN": "operator-auth0-sentinel",
        "AUTH0_AUDIENCE": "operator-auth0-audience-sentinel",
    }
    for name, value in operator_values.items():
        monkeypatch.setenv(name, value)

    root = tmp_path / "private-run"
    root.mkdir(mode=0o700)
    capability = root / "cao" / "capability.json"
    env = journey._diagnostic_mcp_environment(
        root,
        43123,
        "terminal-safe",
        {"env": {"CAO_INTERACTION_CAPABILITY_FILE": str(capability)}},
    )

    assert env["HOME"] == str(root / "mcp-home")
    assert env["CAO_AUTH_MODE"] == "off"
    assert env["CAO_API_PORT"] == "43123"
    assert env["CAO_TERMINAL_ID"] == "terminal-safe"
    assert env["CAO_INTERACTION_CAPABILITY_FILE"] == str(capability)
    assert "CODEX_HOME" not in env
    blocked_operator_keys = set(operator_values) - {"HOME", "CAO_AUTH_MODE"}
    assert not (blocked_operator_keys & env.keys())
    assert env["HOME"] != operator_values["HOME"]
    assert not any(value in json.dumps(env) for value in operator_values.values())


def test_opencode_result_projects_fixed_error_and_tool_diagnostics() -> None:
    from test.live_provider import opencode_interaction_journey as opencode

    projected = opencode._opencode_event_receipt_fields(
        {
            "provider_event_counts": {"error": 1, "text": 2},
            "provider_item_type_counts": {"error": 1, "text": 2},
            "model_usage_counts": {"input_tokens": 3},
            "mcp_tool_names": ["cao_ask_user"],
            "opencode_error_name_counts": {"APIError": 1},
            "opencode_failure_category_counts": {"tool_calling_unavailable": 1},
            "unclassified_error_event_count": 1,
            "unexpected_tool_name_count": 2,
            "tool_calling_observed": True,
        }
    )
    receipt = journey._safe_provider_receipt({"provider": "opencode_cli", **projected})

    assert receipt["opencode_error_name_counts"] == {"APIError": 1}
    assert receipt["opencode_failure_category_counts"] == {"tool_calling_unavailable": 1}
    assert receipt["unclassified_error_event_count"] == 1
    assert receipt["unexpected_tool_name_count"] == 2
    assert receipt["tool_calling_observed"] is True


def test_sanitized_evidence_persists_only_allowlisted_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(journey, "ORIGINAL_HOME", tmp_path)
    result = {
        "provider": "codex",
        "result": "cli_exited_without_ack",
        "provider_event_counts": {"turn.completed": 2, "private.event": 3},
        "provider_item_type_counts": {"mcp_tool_call": 1, "private_item": 1},
        "model_usage_counts": {"input_tokens": 10, "private_counter": 999},
        "failure_diagnostics": {"mcp_spawn_failed": True, "private": "leak"},
        "mcp_tool_calls": [
            {
                "server": "cao-mcp-server",
                "tool": "cao_ask_user",
                "status": "failed",
                "error_code": "PROMPT_SECRET",
                "arguments": "ANSWER_SECRET",
            }
        ],
        "prompt": "PROMPT_SECRET",
        "answer": "ANSWER_SECRET",
        "auth_token": "TOKEN_SECRET",
    }

    assert journey._append_sanitized_evidence(result)
    evidence = tmp_path / ".cache" / "cao-live-provider" / "sanitized-evidence.jsonl"
    persisted = evidence.read_text(encoding="utf-8")
    assert evidence.stat().st_mode & 0o777 == 0o600
    assert '"provider_event_counts":{"turn.completed":2}' in persisted
    assert '"input_tokens":10' in persisted
    assert "PROMPT_SECRET" not in persisted
    assert "ANSWER_SECRET" not in persisted
    assert "TOKEN_SECRET" not in persisted
    assert "private.event" not in persisted


def test_opencode_live_run_is_closed_by_default() -> None:
    from test.live_provider import opencode_interaction_journey as opencode

    assert opencode.run(model="opencode-go/unapproved-model", run_model=False) == {
        "provider": "opencode_cli",
        "result": "model_run_not_approved",
    }


@pytest.mark.parametrize(
    ("model", "run_model", "expected_result"),
    [
        (None, True, "explicit_model_required"),
        ("", True, "explicit_model_required"),
        ("opencode-go/unapproved-model", True, "explicit_model_required"),
        (None, False, "model_run_not_approved"),
    ],
)
def test_opencode_run_rejects_unapproved_model_before_allocating_resources(
    monkeypatch: pytest.MonkeyPatch,
    model: str | None,
    run_model: bool,
    expected_result: str,
) -> None:
    from test.live_provider import opencode_interaction_journey as opencode

    def fail_if_called() -> Path:
        raise AssertionError("private_resources_allocated_before_model_validation")

    monkeypatch.setattr(journey, "_private_root", fail_if_called)
    monkeypatch.setattr(opencode.subprocess, "Popen", fail_if_called)
    monkeypatch.setattr(opencode, "_source_auth_path", fail_if_called)

    assert opencode.run(model=model, run_model=run_model) == {
        "provider": "opencode_cli",
        "result": expected_result,
    }


def test_opencode_run_cleans_private_setup_when_login_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from test.live_provider import interaction_cli_journey as journey
    from test.live_provider import opencode_interaction_journey as opencode

    root = tmp_path / "owned-run"
    root.mkdir(mode=0o700)
    home = tmp_path / "empty-operator-home"
    home.mkdir(mode=0o700)
    monkeypatch.setattr(opencode, "_private_opencode_root", lambda: root)
    monkeypatch.setattr(journey, "ORIGINAL_HOME", home)
    monkeypatch.setenv("CAO_HOME_DIR", "/operator/cao-home")
    monkeypatch.setenv("CAO_DATABASE_CONFIG_FILE", "/operator/database.json")
    monkeypatch.setenv("CAO_API_HOST", "operator-host")
    monkeypatch.setenv("CAO_AUTH_MODE", "operator-mode")

    result = opencode.run(model=journey.OPENCODE_INTERACTION_MODEL, run_model=True)

    assert result == {
        "provider": "opencode_cli",
        "result": "provider_auth_unavailable",
        "phase": "setup",
        "temporary_files_removed": True,
        "cleanup_status": "complete",
        "cleanup_failure_categories": [],
    }
    assert not root.exists()
    assert os.environ["CAO_HOME_DIR"] == "/operator/cao-home"
    assert os.environ["CAO_DATABASE_CONFIG_FILE"] == "/operator/database.json"
    assert os.environ["CAO_API_HOST"] == "operator-host"
    assert os.environ["CAO_AUTH_MODE"] == "operator-mode"


def test_opencode_run_accepts_v2_state_without_legacy_auth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from test.live_provider import interaction_cli_journey as journey
    from test.live_provider import opencode_interaction_journey as opencode
    from test.postgres import fixtures

    root = tmp_path / "owned-run"
    root.mkdir(mode=0o700)
    home = tmp_path / "operator-home"
    credential_db = home / ".local" / "share" / "opencode" / "opencode.db"
    credential_db.parent.mkdir(parents=True, mode=0o700)
    credential_db.write_bytes(b"v2-credential-state")
    credential_db.chmod(0o644)
    monkeypatch.setattr(opencode, "_private_opencode_root", lambda: root)
    monkeypatch.setattr(journey, "ORIGINAL_HOME", home)
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("CAO_HOME_DIR", "/operator/cao-home")
    monkeypatch.setenv("CAO_DATABASE_CONFIG_FILE", "/operator/database.json")
    monkeypatch.setenv("CAO_API_HOST", "operator-host")
    monkeypatch.setenv("CAO_AUTH_MODE", "operator-mode")

    def fail_target_setup(_cao_home: Path) -> None:
        raise RuntimeError("test target setup failure")

    monkeypatch.setattr(fixtures, "isolated_live_main_target", fail_target_setup)

    result = opencode.run(model=journey.OPENCODE_INTERACTION_MODEL, run_model=True)

    assert result["provider"] == "opencode_cli"
    assert result["result"] == "harness_error"
    assert result["phase"] == "setup"
    assert result["error_type"] == "RuntimeError"
    assert result["operator_auth_unchanged"] is True
    assert result["temporary_files_removed"] is True
    assert result["cleanup_status"] == "complete"
    assert result["cleanup_failure_categories"] == []
    assert not root.exists()
    assert not (home / ".local" / "share" / "opencode" / "auth.json").exists()
    assert os.environ["XDG_DATA_HOME"] == str(home / ".local" / "share")


def test_opencode_run_restores_environment_when_target_setup_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from test.live_provider import interaction_cli_journey as journey
    from test.live_provider import opencode_interaction_journey as opencode
    from test.postgres import fixtures

    root = tmp_path / "owned-run"
    root.mkdir(mode=0o700)
    home = tmp_path / "operator-home"
    auth = home / ".local" / "share" / "opencode" / "auth.json"
    auth.parent.mkdir(parents=True, mode=0o700)
    auth.write_text('{"opencode-go":{"type":"api","key":"test-only"}}', encoding="utf-8")
    auth.chmod(0o600)

    monkeypatch.setattr(opencode, "_private_opencode_root", lambda: root)
    monkeypatch.setattr(journey, "ORIGINAL_HOME", home)
    source_data_home = home / ".local" / "share"
    monkeypatch.setenv("XDG_DATA_HOME", str(source_data_home))

    def fail_target_setup(_cao_home: Path) -> None:
        raise RuntimeError("test target setup failure")

    monkeypatch.setattr(fixtures, "isolated_live_main_target", fail_target_setup)
    monkeypatch.setenv("CAO_HOME_DIR", "/operator/cao-home")
    monkeypatch.setenv("CAO_DATABASE_CONFIG_FILE", "/operator/database.json")
    monkeypatch.setenv("CAO_API_HOST", "operator-host")
    monkeypatch.setenv("CAO_AUTH_MODE", "operator-mode")

    result = opencode.run(model=journey.OPENCODE_INTERACTION_MODEL, run_model=True)

    assert result == {
        "provider": "opencode_cli",
        "result": "harness_error",
        "phase": "setup",
        "error_type": "RuntimeError",
        "temporary_files_removed": True,
        "cleanup_status": "complete",
        "cleanup_failure_categories": [],
        "operator_auth_unchanged": True,
    }
    assert not root.exists()
    assert os.environ["CAO_HOME_DIR"] == "/operator/cao-home"
    assert os.environ["CAO_DATABASE_CONFIG_FILE"] == "/operator/database.json"
    assert os.environ["CAO_API_HOST"] == "operator-host"
    assert os.environ["CAO_AUTH_MODE"] == "operator-mode"
    assert os.environ["XDG_DATA_HOME"] == str(source_data_home)


def test_opencode_model_must_be_explicit_and_never_falls_back() -> None:
    from test.live_provider import opencode_interaction_journey as opencode

    with pytest.raises(ValueError, match="explicit_provider_model_required"):
        opencode._headless_command("/usr/bin/opencode", None, "private-agent", "fixed task")
    command = opencode._headless_command(
        "/usr/bin/opencode",
        journey.OPENCODE_INTERACTION_MODEL,
        "private-agent",
        "fixed task",
    )
    assert command == [
        "/usr/bin/opencode",
        "run",
        "--standalone",
        "--model",
        journey.OPENCODE_INTERACTION_MODEL,
        "--agent",
        "private-agent",
        "--format",
        "json",
        "fixed task",
    ]


def test_opencode_auth_staging_copies_only_to_private_profile(
    tmp_path: Path,
) -> None:
    from test.live_provider import opencode_interaction_journey as opencode

    source = tmp_path / "operator" / "opencode" / "auth.json"
    source.parent.mkdir(parents=True, mode=0o700)
    source.write_text(
        '{"opencode-go":{"type":"api","key":"TOKEN_SENTINEL_MUST_NOT_ESCAPE"}}',
        encoding="utf-8",
    )
    source.chmod(0o600)
    source_before = (
        source.stat().st_ino,
        source.stat().st_size,
        source.stat().st_mtime_ns,
    )
    destination = tmp_path / "run" / "staged-auth" / "opencode" / "auth.json"

    opencode._stage_auth_file(source, destination)

    assert destination.read_bytes() == source.read_bytes()
    assert destination.stat().st_mode & 0o777 == 0o600
    assert destination.parent.stat().st_mode & 0o777 == 0o700
    assert destination.parent.parent.stat().st_mode & 0o777 == 0o700
    assert source_before == (
        source.stat().st_ino,
        source.stat().st_size,
        source.stat().st_mtime_ns,
    )


def test_opencode_credential_state_staging_is_private_and_non_mutating(tmp_path: Path) -> None:
    from test.live_provider import opencode_interaction_journey as opencode

    source = tmp_path / "operator" / "opencode" / "opencode.db"
    source.parent.mkdir(parents=True, mode=0o700)
    source.write_bytes(b"credential-state-snapshot")
    source.chmod(0o644)
    source_before = (source.read_bytes(), source.stat().st_mtime_ns)
    destination = tmp_path / "run" / "opencode" / "opencode.db"

    assert opencode._stage_private_state_file(source, destination) is True

    assert destination.read_bytes() == source_before[0]
    assert destination.stat().st_mode & 0o777 == 0o600
    assert source_before == (source.read_bytes(), source.stat().st_mtime_ns)
    assert destination.stat().st_mode & 0o777 == 0o600


def test_opencode_auth_lookup_prefers_approved_profile_over_inherited_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from test.live_provider import opencode_interaction_journey as opencode

    profile_home = tmp_path / "approved-profile"
    operator_data = tmp_path / "operator-data"
    staged_auth = profile_home / ".local" / "share" / "opencode" / "auth.json"
    inherited_auth = operator_data / "opencode" / "auth.json"
    staged_auth.parent.mkdir(mode=0o700, parents=True)
    inherited_auth.parent.mkdir(mode=0o700, parents=True)
    staged_auth.write_text("staged", encoding="utf-8")
    inherited_auth.write_text("unstaged", encoding="utf-8")
    monkeypatch.setenv("CAO_PRIVATE_PROFILE_HOME", str(profile_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(operator_data))

    selected = opencode._source_auth_path(original_home=profile_home, environment=dict(os.environ))

    assert selected == staged_auth


def test_opencode_private_git_workdir_is_a_real_local_repository(
    tmp_path: Path,
) -> None:
    import subprocess
    from test.live_provider import opencode_interaction_journey as opencode

    workdir = tmp_path / "private-run" / "work"
    workdir.parent.mkdir(mode=0o700)
    opencode._initialize_private_git_repository(workdir)

    result = subprocess.run(
        ["git", "-C", str(workdir), "rev-parse", "--is-inside-work-tree"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(workdir.parent / "private-home"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        },
    )
    assert result.stdout.strip() == "true"
    assert (workdir / ".git").is_dir()


def test_opencode_event_summary_keeps_only_known_counters_and_tool_names(
    tmp_path: Path,
) -> None:
    import json
    from test.live_provider import opencode_interaction_journey as opencode

    event_file = tmp_path / "opencode.jsonl"
    event_file.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "type": "step_finish",
                    "part": {
                        "type": "step-finish",
                        "tokens": {
                            "input": 8,
                            "output": 3,
                            "reasoning": 1,
                            "secret": 999,
                        },
                    },
                    "prompt": "PROMPT_SENTINEL_MUST_NOT_ESCAPE",
                },
                {
                    "type": "tool",
                    "part": {"type": "tool", "tool": "cao-mcp-server_cao_ask_user"},
                    "args": "ANSWER_SENTINEL_MUST_NOT_ESCAPE",
                },
                {"type": "unknown.event", "part": {"type": "private"}},
            )
        ),
        encoding="utf-8",
    )

    summary = opencode._opencode_event_summary(event_file)
    serialized = json.dumps(summary, sort_keys=True)
    assert summary["provider_event_counts"] == {"step_finish": 1, "tool": 1}
    assert summary["provider_item_type_counts"] == {"step-finish": 1, "tool": 1}
    assert summary["model_usage_counts"] == {
        "input_tokens": 8,
        "output_tokens": 3,
        "reasoning_tokens": 1,
    }
    assert summary["mcp_tool_names"] == ["cao_ask_user"]
    assert "PROMPT_SENTINEL_MUST_NOT_ESCAPE" not in serialized
    assert "ANSWER_SENTINEL_MUST_NOT_ESCAPE" not in serialized
    assert "private" not in serialized


def test_opencode_v2_writer_keeps_selected_model_and_exact_private_tool_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json
    from test.live_provider import opencode_interaction_journey as opencode
    from test.live_provider import opencode_mcp_catalog

    from cli_agent_orchestrator.providers import opencode_cli
    from cli_agent_orchestrator.utils.opencode_v2 import write_v2_configuration

    root = tmp_path / "run"
    root.mkdir(mode=0o700)
    agents = root / "agents"
    agents.mkdir(mode=0o700)
    config_dir = root / "config"
    config_dir.mkdir(mode=0o700)
    data_home = config_dir / "data"
    data_home.mkdir(mode=0o700)
    auth_source = root / "staged-auth.json"
    auth_source.write_text(
        '{"opencode-go":{"type":"api","key":"TOKEN_SENTINEL_MUST_NOT_ESCAPE"}}',
        encoding="utf-8",
    )
    auth_source.chmod(0o600)
    (data_home / "opencode").mkdir(mode=0o700)
    (data_home / "opencode" / "auth.json").symlink_to(auth_source)

    agent_name = "private-agent"
    agent_path = agents / f"{agent_name}.md"
    agent_path.write_text(opencode._agent_source(agent_name), encoding="utf-8")
    agent_path.chmod(0o600)
    mcp_environment = {
        "HOME": str(root / "private-home"),
        "PYTHONPATH": str(Path(opencode.__file__).resolve().parents[2] / "src"),
        "CAO_HOME_DIR": str(root / "cao"),
        "CAO_DATABASE_CONFIG_FILE": str(root / "cao" / "database-config.json"),
        "CAO_API_HOST": "127.0.0.1",
        "CAO_API_PORT": "43210",
        "CAO_TERMINAL_ID": "private-terminal",
        "CAO_AUTH_MODE": "off",
        "CAO_INTERACTION_CAPABILITY_FILE": str(root / "cao" / "capability.json"),
    }
    mcp_server = {
        "type": "local",
        "command": [
            "/usr/bin/python3",
            "-m",
            "cli_agent_orchestrator.mcp_server.server",
        ],
        "environment": dict(mcp_environment),
        "enabled": True,
        "codemode": False,
    }
    opencode._isolate_mcp_server(mcp_server)
    source_config = {
        "mcp": {
            "timeout": {"startup": 60_000, "catalog": 60_000, "execution": 65_000},
            "servers": {"cao-mcp-server": mcp_server},
        }
    }
    model = "opencode-go/catalog-model"
    write_v2_configuration(
        config_dir,
        source_agents=agents,
        source_config=source_config,
        selected_model=model,
        session_id="private-terminal",
        source_auth=auth_source,
    )
    document = json.loads((config_dir / "opencode.json").read_text(encoding="utf-8"))
    profile = document["agents"][agent_name]
    canonical_actions = (
        "cao-mcp-server_cao_ask_user",
        "cao-mcp-server_cao_wait_for_user_answers",
        "cao-mcp-server_cao_acknowledge_user_answers",
    )
    assert opencode_mcp_catalog.AGENT_TOOL_SCOPE == canonical_actions
    grants = [rule["action"] for rule in profile["permissions"] if rule["effect"] == "allow"]
    assert profile["permissions"][0] == {
        "action": "*",
        "resource": "*",
        "effect": "deny",
    }
    assert "TOKEN_SENTINEL_MUST_NOT_ESCAPE" not in json.dumps(document)
    saved_mcp = document["mcp"]["servers"]["cao-mcp-server"]
    assert saved_mcp["codemode"] is False
    registered_actions = tuple(
        f"cao-mcp-server_{tool}"
        for tool in (
            "cao_ask_user",
            "cao_wait_for_user_answers",
            "cao_acknowledge_user_answers",
        )
    )
    registered_actions += ("cao-mcp-server_cao_unlisted",)

    # v2.0.18 Tool.snapshot applies the last matching permission; its MCP
    # namespace preserves '-' and direct MCP definitions are used with
    # codemode=false (core/src/tool/mcp.ts and core/src/tool.ts).
    def allowed_in(candidate_profile: dict[str, Any], action: str) -> bool:
        decision = False
        for rule in candidate_profile["permissions"]:
            pattern = rule["action"]
            if pattern == "*" or pattern == action:
                decision = rule["effect"] == "allow"
        return decision

    effective_direct_actions = [
        action
        for action in registered_actions
        if not saved_mcp["codemode"] and allowed_in(profile, action)
    ]
    assert effective_direct_actions == list(canonical_actions)
    assert grants == list(canonical_actions)
    assert allowed_in(profile, "execute") is False
    assert allowed_in(profile, "cao-mcp-server_cao_unlisted") is False

    # The historical R17 profile keeps its old underscore names and therefore
    # still resolves to no canonical direct MCP definitions under v2.0.18.
    legacy_agents = root / "legacy-agents"
    legacy_agents.mkdir(mode=0o700)
    legacy_agent_path = legacy_agents / f"{agent_name}.md"
    legacy_agent_path.write_text(
        agent_path.read_text(encoding="utf-8").replace("cao-mcp-server_", "cao_mcp_server_"),
        encoding="utf-8",
    )
    legacy_config_dir = root / "legacy-config"
    write_v2_configuration(
        legacy_config_dir,
        source_agents=legacy_agents,
        source_config=source_config,
        selected_model=model,
        session_id="legacy-terminal",
        source_auth=auth_source,
    )
    legacy_document = json.loads((legacy_config_dir / "opencode.json").read_text(encoding="utf-8"))
    legacy_profile = legacy_document["agents"][agent_name]
    legacy_server = legacy_document["mcp"]["servers"]["cao-mcp-server"]
    legacy_effective_direct_actions = [
        action
        for action in registered_actions
        if not legacy_server["codemode"] and allowed_in(legacy_profile, action)
    ]
    assert legacy_effective_direct_actions == []
    assert allowed_in(legacy_profile, "execute") is False
    assert saved_mcp["command"][0] == "/usr/bin/env"
    assert saved_mcp["command"][1] == "-i"
    assert saved_mcp["command"][-3:] == [
        "/usr/bin/python3",
        "-m",
        "cli_agent_orchestrator.mcp_server.server",
    ]
    assert saved_mcp["environment"] == {}
    assert "CAO_INTERACTION_CAPABILITY_FILE=" in json.dumps(saved_mcp["command"])
    provider_env = config_dir / "provider.env"
    assert provider_env.stat().st_mode & 0o777 == 0o600
    assert "TOKEN_SENTINEL_MUST_NOT_ESCAPE" in provider_env.read_text(encoding="utf-8")

    monkeypatch.setattr(opencode_cli, "OPENCODE_CONFIG_DIR", config_dir)
    monkeypatch.setattr(opencode_cli, "OPENCODE_CONFIG_FILE", config_dir / "opencode.json")
    monkeypatch.setattr(opencode_cli, "OPENCODE_AGENTS_DIR", agents)
    adapter = opencode_cli.OpenCodeCliProvider(
        "private-terminal", "private-session", "window-0", agent_name, model=model
    )
    adapter._cli_major = 2
    adapter._v2_config_dir = config_dir
    adapter._v2_data_home = data_home
    adapter._v2_cao_mcp_server_name = "cao-mcp-server"
    adapter._validate_native_profile()
    production_command = adapter._build_launch_command()
    assert "--agent private-agent" in production_command
    assert f"--model {model}" in production_command
    assert "mini --standalone" in production_command


def test_opencode_process_environment_is_private_and_source_explicit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from test.live_provider import opencode_interaction_journey as opencode

    monkeypatch.setenv("HOME", "/operator/home")
    monkeypatch.setenv("PYTHONPATH", "/operator/source")
    monkeypatch.setenv("OPENAI_API_KEY", "TOKEN_SENTINEL_MUST_NOT_ESCAPE")
    root = tmp_path / "run"
    root.mkdir(mode=0o700)
    cao_home = root / "cao"
    cao_home.mkdir(mode=0o700)
    config_dir = root / "config"
    config_dir.mkdir(mode=0o700)
    data_home = config_dir / "data"
    data_home.mkdir(mode=0o700)
    env = opencode._private_cli_environment(
        root=root,
        cao_home=cao_home,
        config_dir=config_dir,
        data_home=data_home,
        api_port=43210,
        source_pythonpath="/absolute/candidate/src",
        path_value="/private/opencode/bin:/usr/bin",
        tmux_tmpdir=root / "tmux-tmp",
    )

    assert env["HOME"] == str(root / "private-home")
    assert env["PYTHONPATH"] == "/absolute/candidate/src"
    assert env["OPENCODE_CONFIG"] == str(config_dir / "opencode.json")
    assert env["OPENCODE_CONFIG_DIR"] == str(config_dir)
    assert env["CAO_DATABASE_CONFIG_FILE"] == str(cao_home / "database-config.json")
    assert env["CAO_API_PORT"] == "43210"
    assert "OPENAI_API_KEY" not in env
    assert "CODEX_HOME" not in env
    assert "/operator/home" not in json.dumps(env)
    assert "TOKEN_SENTINEL_MUST_NOT_ESCAPE" not in json.dumps(env)


def test_opencode_mcp_launcher_drops_provider_credentials_from_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess
    import sys
    from test.live_provider import opencode_interaction_journey as opencode

    monkeypatch.setenv("OPENAI_API_KEY", "OPENAI_SENTINEL_MUST_NOT_ESCAPE")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ANTHROPIC_SENTINEL_MUST_NOT_ESCAPE")
    monkeypatch.setenv("OPENCODE_API_KEY", "OPENCODE_SENTINEL_MUST_NOT_ESCAPE")
    capability_path = str(tmp_path / "private" / "capability.json")
    source_path = str(Path(opencode.__file__).resolve().parents[2] / "src")
    child_environment = {
        "HOME": str(tmp_path / "private-home"),
        "PYTHONPATH": source_path,
        "CAO_HOME_DIR": str(tmp_path / "cao"),
        "CAO_DATABASE_CONFIG_FILE": str(tmp_path / "cao" / "database.json"),
        "CAO_API_HOST": "127.0.0.1",
        "CAO_API_PORT": "43123",
        "CAO_TERMINAL_ID": "terminal-safe",
        "CAO_AUTH_MODE": "off",
        "CAO_INTERACTION_CAPABILITY_FILE": capability_path,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    child_program = (
        "import json, os; print(json.dumps({"
        "'provider_keys_absent': all(name not in os.environ for name in "
        "('OPENAI_API_KEY','ANTHROPIC_API_KEY','OPENCODE_API_KEY')),'source_explicit': "
        f"os.environ.get('PYTHONPATH') == {source_path!r},'capability_explicit': "
        f"os.environ.get('CAO_INTERACTION_CAPABILITY_FILE') == {capability_path!r},"
        "'terminal_explicit': os.environ.get('CAO_TERMINAL_ID') == 'terminal-safe'}))"
    )
    command = opencode._isolated_mcp_command(
        child_environment, sys.executable, ["-c", child_program]
    )
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
        env=dict(os.environ),
    )

    assert json.loads(result.stdout) == {
        "provider_keys_absent": True,
        "source_explicit": True,
        "capability_explicit": True,
        "terminal_explicit": True,
    }
    command_text = json.dumps(command)
    assert "SENTINEL_MUST_NOT_ESCAPE" not in command_text
    assert "OPENAI_API_KEY=" not in command_text
    assert "ANTHROPIC_API_KEY=" not in command_text
    assert "OPENCODE_API_KEY=" not in command_text


def test_opencode_mcp_launcher_rejects_unreviewed_environment_names() -> None:
    import sys
    from test.live_provider import opencode_interaction_journey as opencode

    with pytest.raises(ValueError, match="mcp_environment_not_allowlisted"):
        opencode._isolated_mcp_command(
            {"HOME": "/private/home", "OPENAI_API_KEY": "secret"},
            sys.executable,
            ["-m", "cli_agent_orchestrator.mcp_server.server"],
        )


@pytest.mark.offline_database
def test_opencode_event_summary_classifies_only_fixed_error_metadata_without_text(
    tmp_path: Path,
) -> None:
    from test.live_provider import opencode_interaction_journey as opencode

    output = tmp_path / "opencode-events.jsonl"
    raw_marker = "RAW_PROVIDER_DETAIL_MUST_NOT_ESCAPE"
    events = [
        {
            "type": "step_start",
            "part": {"type": "step-start"},
        },
        {
            "type": "text",
            "part": {"type": "text", "text": raw_marker},
        },
        {
            "type": "error",
            "error": {
                "name": "ProviderModelNotFoundError",
                "data": {"message": raw_marker},
            },
        },
        {
            "type": "error",
            "error": {
                "cause": {"name": "ProviderAuthError", "message": raw_marker},
            },
        },
        {
            "type": "tool",
            "part": {"type": "tool", "tool": "unapproved_private_tool"},
        },
        {
            "type": "error",
            "error": {"name": "UnknownPrivateProviderError", "message": raw_marker},
        },
    ]
    output.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

    summary = opencode._opencode_event_summary(output)
    receipt = journey._safe_provider_receipt({"provider": "opencode_cli", **summary})
    serialized = json.dumps(receipt, sort_keys=True)

    assert summary["provider_event_counts"] == {
        "error": 3,
        "step_start": 1,
        "text": 1,
        "tool": 1,
    }
    assert summary["opencode_error_name_counts"] == {
        "ProviderAuthError": 1,
        "ProviderModelNotFoundError": 1,
    }
    assert summary["opencode_failure_category_counts"] == {
        "provider_auth_rejected": 1,
        "provider_model_unavailable": 1,
        "provider_step_incomplete": 1,
    }
    assert summary["unclassified_error_event_count"] == 1
    assert summary["unexpected_tool_name_count"] == 1
    assert summary["tool_calling_observed"] is False
    assert "no_tools_available" not in summary["opencode_failure_category_counts"]
    assert raw_marker not in serialized
