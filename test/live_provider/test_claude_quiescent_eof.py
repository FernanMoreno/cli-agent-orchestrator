"""Explicit diagnostic EOF preserves native mode and the original close budget."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import message_api_restart as contract
import message_api_restart_live as live
import message_mcp_shutdown as shutdown
import pytest

pytestmark = pytest.mark.offline_database
FLAG = "CAO_SPEC023_CLAUDE_REQUEST_QUIESCENT_EOF"


@pytest.mark.parametrize("value,expected", [("0", False), ("1", True)])
def test_explicit_mode_parser(value, expected):
    parser = getattr(contract, "parse_claude_eof_gate", None)
    assert callable(parser), "explicit Claude shutdown mode is absent"
    assert parser({FLAG: value}) is expected
    assert parser({}) is False


@pytest.mark.parametrize("value", [True, 1, None, "true", "", " 1"])
def test_explicit_mode_parser_rejects_ambiguous_values(value):
    parser = getattr(contract, "parse_claude_eof_gate", None)
    assert callable(parser)
    with pytest.raises(ValueError):
        parser({FLAG: value})


def config_args(tmp_path):
    tmp_path.chmod(0o700)
    return {
        "python_executable": Path(sys.executable),
        "source_root": Path(__file__).resolve().parents[2],
        "candidate_test_dir": Path(__file__).parent,
        "audit_path": tmp_path / "audit.jsonl",
    }


def test_eof_config_is_explicit_and_keeps_exact_profile_filter(tmp_path):
    args = config_args(tmp_path)
    default = contract.build_message_mcp_server_config(provider="claude_code", **args)
    assert "CAO_SPEC023_MCP_GRACEFUL_EOF" not in default["env"]
    explicit = contract.build_message_mcp_server_config(
        provider="claude_code", claude_quiescent_eof=True, **args
    )
    assert explicit["cao_tool_profile"] == default["cao_tool_profile"] == "collaboration"
    assert explicit["enabled_tools"] == default["enabled_tools"] == ["send_message"]
    assert explicit["args"] == default["args"]
    assert explicit["env"] == {**default["env"], "CAO_SPEC023_MCP_GRACEFUL_EOF": "1"}
    assert contract.message_mcp_config_ready(
        "claude_code", explicit, claude_quiescent_eof=True, **args
    )
    assert not contract.message_mcp_config_ready("claude_code", explicit, **args)
    assert not contract.message_mcp_config_ready(
        "claude_code", default, claude_quiescent_eof=True, **args
    )
    for bad in ("0", True, "true"):
        explicit["env"]["CAO_SPEC023_MCP_GRACEFUL_EOF"] = bad
        assert not contract.message_mcp_config_ready(
            "claude_code", explicit, claude_quiescent_eof=True, **args
        )


@pytest.mark.parametrize("value", [1, "1", None])
def test_builder_rejects_non_boolean_mode(tmp_path, value):
    with pytest.raises(RuntimeError):
        contract.build_message_mcp_server_config(
            provider="claude_code", claude_quiescent_eof=value, **config_args(tmp_path)
        )


def setup_close(tmp_path, monkeypatch, *, enabled=True, records=2):
    tmp_path.chmod(0o700)
    audit = tmp_path / "audit.jsonl"
    row = {"tool": "send_message", "status": "completed", "outcome": "success"}
    audit.write_text((json.dumps(row) + "\n") * records)
    audit.chmod(0o600)
    state = SimpleNamespace(
        sender_id="a1b2c3d4",
        audit_path=audit,
        claude_quiescent_eof=enabled,
        shutdown_mode="not_observed",
        claude_exit_diagnostic=None,
    )
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(live, "time", SimpleNamespace(monotonic=lambda: clock.now))
    calls = []

    def eof(path, *, deadline):
        calls.append(("eof", deadline))
        clock.now += 1.0
        return True

    def api(*args, timeout, **kwargs):
        calls.append(("exit", timeout.total))
        clock.now += 0.5
        return 200, {}

    def poll(current, *, timeout):
        calls.append(("seal", timeout))
        clock.now += 0.1
        return True

    monkeypatch.setattr(shutdown, "request_shutdown", eof)
    monkeypatch.setattr(live, "_api_request", api)
    monkeypatch.setattr(live, "_wait_for_audit_completion", poll)
    monkeypatch.setattr(live, "_audit_completion_present", lambda _: True)
    return state, clock, calls


def test_eof_exit_and_seal_share_original_six_seconds(tmp_path, monkeypatch):
    state, clock, calls = setup_close(tmp_path, monkeypatch)
    assert live._graceful_claude_sender_exit_and_wait_for_seal(state, "http://local")
    assert calls == [("eof", 106.0), ("exit", 5.0), ("seal", 4.5)]
    assert state.shutdown_mode == "harness_requested_eof"
    assert state.claude_eof_outcome == "normal_eof_verified"
    assert live._graceful_claude_sender_exit_and_wait_for_seal(state, "http://local")
    assert sum(kind == "eof" for kind, _ in calls) == 1


@pytest.mark.parametrize("records", [0, 1, 3])
def test_eof_refuses_incomplete_history_before_external_effect(tmp_path, monkeypatch, records):
    state, _, calls = setup_close(tmp_path, monkeypatch, records=records)
    assert not live._graceful_claude_sender_exit_and_wait_for_seal(state, "http://local")
    assert calls == []
    assert state.claude_eof_outcome == "history_invalid"


@pytest.mark.parametrize("late", [False, True])
def test_failed_or_late_eof_never_falls_back_to_native_exit(tmp_path, monkeypatch, late):
    state, clock, calls = setup_close(tmp_path, monkeypatch)

    def eof(path, *, deadline):
        clock.now += 7 if late else 1
        return late

    monkeypatch.setattr(shutdown, "request_shutdown", eof)
    assert not live._graceful_claude_sender_exit_and_wait_for_seal(state, "http://local")
    assert calls == [] and state.shutdown_mode == "not_observed"
    assert state.claude_eof_outcome == ("deadline_exhausted" if late else "eof_request_failed")


def test_default_native_close_does_not_request_eof(tmp_path, monkeypatch):
    state, _, calls = setup_close(tmp_path, monkeypatch, enabled=False)
    assert live._graceful_claude_sender_exit_and_wait_for_seal(state, "http://local")
    assert [kind for kind, _ in calls] == ["exit", "seal"]
    assert state.shutdown_mode == "not_observed"


def test_receipt_never_promotes_explicit_eof_to_native_automatic_shutdown():
    projected = contract.project_safe_receipt(
        {
            "provider": "claude_code",
            "result": "pass",
            "shutdown_mode": "harness_requested_eof",
            "native_automatic_shutdown_accepted": True,
        }
    )
    assert projected.get("shutdown_mode") == "harness_requested_eof"
    assert projected.get("native_automatic_shutdown_accepted") is False


@pytest.mark.parametrize("outcome", ["bad", True, None, {}, []])
def test_receipt_closes_eof_diagnostic_enums_and_flag_types(outcome):
    projected = contract.project_safe_receipt(
        {
            "provider": "claude_code",
            "claude_quiescent_eof_requested": "1",
            "claude_eof_outcome": outcome,
        }
    )
    assert projected["claude_quiescent_eof_requested"] is False
    assert projected["claude_eof_outcome"] == "not_observed"


@pytest.mark.parametrize("provider", ["codex", "opencode_cli"])
def test_claude_option_cannot_change_another_provider(tmp_path, provider):
    with pytest.raises(RuntimeError):
        contract.build_message_mcp_server_config(
            provider=provider, claude_quiescent_eof=True, **config_args(tmp_path)
        )


def test_bad_live_gate_is_rejected_before_setup_or_credentials(tmp_path, monkeypatch):
    from unittest.mock import Mock

    for provider, key in contract.PROVIDER_GATE_ENV.items():
        monkeypatch.setenv(key, "1" if provider == "claude_code" else "0")
    monkeypatch.setenv(FLAG, "not-a-mode")
    monkeypatch.setenv(contract.SOURCE_ROOT_ENV, str(Path(__file__).resolve().parents[2]))
    monkeypatch.setenv(live.EVIDENCE_ROOT_ENV, str(tmp_path))
    setup = Mock(side_effect=AssertionError("setup must not run"))
    monkeypatch.setattr(live, "_verify_selected_setup", setup)
    with pytest.raises(pytest.fail.Exception, match="spec023_preflight_failed"):
        live.run_selected_message_acceptance(SimpleNamespace())
    setup.assert_not_called()


@pytest.mark.parametrize("bad", ["failure", "unknown"])
def test_two_non_success_calls_do_not_authorize_eof(tmp_path, monkeypatch, bad):
    state, _, calls = setup_close(tmp_path, monkeypatch)
    row = {"tool": "send_message", "status": "completed", "outcome": bad}
    state.audit_path.write_text((json.dumps(row) + "\n") * 2)
    assert not live._graceful_claude_sender_exit_and_wait_for_seal(state, "http://local")
    assert calls == []


def test_missing_normal_seal_after_eof_does_not_request_cli_exit(tmp_path, monkeypatch):
    state, _, calls = setup_close(tmp_path, monkeypatch)
    monkeypatch.setattr(live, "_audit_completion_present", lambda _: False)
    assert not live._graceful_claude_sender_exit_and_wait_for_seal(state, "http://local")
    assert [kind for kind, _ in calls] == ["eof"]
    assert state.shutdown_mode == "not_observed"
    assert state.claude_eof_outcome == "normal_seal_missing"


def test_late_seal_after_eof_and_exit_stays_failed(tmp_path, monkeypatch):
    state, clock, _ = setup_close(tmp_path, monkeypatch)

    def late_poll(*args, **kwargs):
        clock.now = 107.0
        return True

    monkeypatch.setattr(live, "_wait_for_audit_completion", late_poll)
    assert not live._graceful_claude_sender_exit_and_wait_for_seal(state, "http://local")


def test_eof_control_exception_does_not_fall_back(tmp_path, monkeypatch):
    state, _, calls = setup_close(tmp_path, monkeypatch)

    def broken(*args, **kwargs):
        raise OSError("private transport unavailable")

    monkeypatch.setattr(shutdown, "request_shutdown", broken)
    assert not live._graceful_claude_sender_exit_and_wait_for_seal(state, "http://local")
    assert calls == []
    assert state.claude_eof_outcome == "eof_control_error"
