"""Offline contracts for the default-closed native v2 full-surface case."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

RUNNER_PATH = Path(__file__).with_name("run_native_v2_acceptance.py")
SPEC = importlib.util.spec_from_file_location("native_v2_acceptance_candidate", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def test_public_gate_is_closed_without_reading_execution_manifest(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [str(RUNNER_PATH)])
    monkeypatch.setattr(
        runner,
        "_load_run_manifest",
        lambda *args, **kwargs: pytest.fail("closed gate reached manifest or runtime"),
    )

    assert runner.RUN_ENABLED is False
    assert runner.main() == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "explicit_go_required"


def test_offline_self_check_executes_existing_output_range_controls():
    result = runner._self_check()
    assert result["success"] is True
    assert result["checks"]["private_output_range_controls"] is True


def test_go_without_exact_source_and_review_bindings_stays_closed(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [str(RUNNER_PATH), "--go"])
    monkeypatch.setattr(
        runner,
        "_load_run_manifest",
        lambda *args, **kwargs: pytest.fail("incomplete gate reached manifest or runtime"),
    )

    assert runner.main() == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == (
        "exact_model_source_manifest_and_review_required"
    )


def test_public_candidate_all_execution_arguments_stay_closed(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(RUNNER_PATH),
            "--go",
            "--model",
            runner.MODEL,
            "--expected-head",
            "a" * 40,
            "--manifest",
            "/tmp/manifest-that-must-not-be-read.json",
            "--manifest-sha256",
            "b" * 64,
            "--review-digest",
            "c" * 64,
        ],
    )
    monkeypatch.setattr(
        runner,
        "_load_run_manifest",
        lambda *args, **kwargs: pytest.fail("closed public candidate read the manifest"),
    )

    assert runner.RUN_ENABLED is False
    assert runner.main() == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == ("private_execution_copy_required")


def test_run_rejects_closed_candidate_before_manifest_or_runtime(monkeypatch):
    monkeypatch.setattr(runner, "_load_run_manifest", lambda *a, **k: pytest.fail())
    monkeypatch.setattr(
        runner,
        "_private_execution_copy_contract",
        lambda: pytest.fail("closed candidate inspected execution copy"),
    )
    with pytest.raises(runner.GateError, match="private_execution_copy_required"):
        runner._run(
            runner.MODEL,
            go=True,
            expected_head="a" * 40,
            manifest_path=Path("/tmp/unused"),
            manifest_sha256="b" * 64,
            review_digest="c" * 64,
        )


def test_private_execution_derivation_changes_only_the_single_closed_flag(tmp_path, monkeypatch):
    closed = RUNNER_PATH.read_bytes()
    executed = runner._execution_source_bytes(closed)
    assert runner._execution_source_matches(closed, executed)
    assert executed.count(b"RUN_ENABLED = True") == 1
    assert closed.count(b"RUN_ENABLED = False") == 1
    assert not runner._execution_source_matches(closed, executed + b"\n# extra")
    assert not runner._execution_source_matches(
        closed.replace(b"RUN_ENABLED = False", b"RUN_ENABLED = True", 1), executed
    )

    destination = tmp_path / "private-runner.py"
    hashes = runner._write_private_execution_copy(destination)
    assert destination.stat().st_mode & 0o777 == 0o600
    assert destination.read_bytes() == executed
    monkeypatch.setattr(runner, "__file__", str(destination))
    assert runner._private_execution_copy_contract()
    destination.write_bytes(executed + b"\n# changed")
    destination.chmod(0o600)
    assert not runner._private_execution_copy_contract()
    assert hashes == {
        "candidate_sha256": hashlib.sha256(closed).hexdigest(),
        "execution_candidate_sha256": hashlib.sha256(executed).hexdigest(),
    }


def test_private_execution_derivation_cli_only_writes_and_never_runs(tmp_path, monkeypatch, capsys):
    destination = tmp_path / "runner-copy.py"
    monkeypatch.setattr(
        sys,
        "argv",
        [str(RUNNER_PATH), "--derive-execution-copy", str(destination)],
    )
    assert runner.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["success"] is True
    assert destination.is_file()
    assert destination.read_bytes() == runner._execution_source_bytes(RUNNER_PATH.read_bytes())


def test_manifest_private_reader_rejects_symlinks_and_hardlinks(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b'{"schema":"test"}')
    manifest.chmod(0o600)
    assert runner._read_owned_private_file(manifest, maximum=1024) == b'{"schema":"test"}'

    hardlink = tmp_path / "manifest-hardlink.json"
    hardlink.hardlink_to(manifest)
    with pytest.raises(runner.GateError, match="private_file_identity_invalid"):
        runner._read_owned_private_file(manifest, maximum=1024)

    symlink = tmp_path / "manifest-symlink.json"
    symlink.symlink_to(manifest)
    with pytest.raises(runner.GateError):
        runner._read_owned_private_file(symlink, maximum=1024)


def test_profile_fixture_has_separate_unfiltered_full_eight_surface():
    case = json.loads(
        (RUNNER_PATH.parent / "native-v2-collaboration-acceptance-profiles.json").read_text(
            encoding="utf-8"
        )
    )
    sender_allowed, sender_filter, _ = runner._profile_contract("sender")
    receiver_allowed, receiver_filter, _ = runner._profile_contract("receiver")

    assert case["schema"] == "caos.native-v2-collaboration-acceptance-profiles.v1"
    assert case["execution_artifact"]["repository_root_environment"] == (
        runner.REPOSITORY_ROOT_ENV
        + " points the private copy back to the source checkout; its exact path and HEAD are manifest-bound."
    )
    assert (
        "canonical resolved interpreter path"
        in case["execution_artifact"]["python_interpreter_binding"]
    )
    assert case["required_checks"]["private_execution_copy_exact_single_flag_derivation"] is True
    assert (
        case["required_checks"][
            "private_credential_snapshot_never_unlinked_while_owned_writers_uncertain"
        ]
        is True
    )
    assert (
        case["required_checks"]["sigterm_uses_safe_cancellation_checkpoints_and_cleanup_finishes"]
        is True
    )
    assert (
        case["required_checks"]["python_venv_launch_path_and_canonical_interpreter_hash_bound"]
        is True
    )
    assert "acceptance cannot pass" in case["evidence_handling"]["credential_snapshot_cleanup"]
    assert case["authentication_scope"].startswith("Only this selected OpenCode Go model lane")
    assert (
        "no cross-provider, OAuth, or global authentication claim" in case["authentication_scope"]
    )
    assert sender_allowed == case["profiles"]["sender"]["allowed_tools"] == ["@cao-mcp-server"]
    assert sender_filter is None
    assert case["profiles"]["sender"]["enabled_mcp_tools"] is None
    assert case["profiles"]["sender"]["prompt"] == runner._profile_contract("sender")[2]
    assert receiver_allowed == case["profiles"]["receiver"]["allowed_tools"] == []
    assert receiver_filter == case["profiles"]["receiver"]["enabled_mcp_tools"] == []
    assert case["profiles"]["receiver"]["prompt"] == runner._profile_contract("receiver")[2]
    assert case["catalog"] == list(runner.SENDER_TOOLS)
    assert case["required_checks"]["sender_calls_exact"] == list(runner.SENDER_CALLS)
    assert case["required_checks"]["sender_calls_from_canonical_cao_mcp_server"] is True
    assert case["required_checks"]["production_api_worker_real_lifespan"] is True
    assert case["required_checks"]["production_api_worker_invocation_exact"] is True
    assert case["required_checks"]["production_runtime_capability_issuance"] is True
    assert (
        case["required_checks"][
            "each_current_working_directory_equals_its_distinct_private_role_workspace"
        ]
        is True
    )
    assert (
        case["required_checks"][
            "each_private_role_workspace_is_a_run_owned_project_with_its_own_nonce_pinned_offer_observer"
        ]
        is True
    )
    assert (
        case["required_checks"][
            "sender_model_request_full_eight_cao_mcp_tools_no_execute_or_unexpected_tools"
        ]
        is True
    )
    assert case["required_checks"]["receiver_model_request_zero_cao_mcp_tools"] is True
    assert (
        case["required_checks"]["roles_only_source_approved_optional_builtins_on_model_requests"]
        is True
    )
    assert (
        case["required_checks"][
            "native_builtin_offer_names_within_source_approved_optional_contract"
        ]
        is True
    )
    assert case["required_checks"]["receiver_has_no_cao_mcp_tools"] is True
    assert case["native_model_request_observation"][
        "source_approved_optional_native_builtins"
    ] == list(runner.OPTIONAL_OPENCODE_BUILTIN_TOOL_NAMES)
    assert case["native_model_request_observation"]["builtin_execution_observation"].startswith(
        "not_observed;"
    )
    assert (
        case["required_checks"][
            "offer_observer_records_names_only_and_no_raw_request_body_or_headers"
        ]
        is True
    )
    assert (
        case["required_checks"][
            "installed_sender_and_receiver_prompts_exactly_match_profile_fixture"
        ]
        is True
    )
    assert case["required_checks"]["receipt_redacted_only_private_raw_evidence_separate"] is True
    assert (
        "selected-model primary requests only" in case["native_model_request_observation"]["scope"]
    )


def test_observer_requires_full_unfiltered_catalog_but_only_three_expected_calls():
    observations = {
        "sender": {
            "observer_status": "observed",
            "mcp_observer_shutdown_observed": True,
            "mcp_catalog_observation_status": "observed",
            "mcp_catalog_observation_count": 1,
            "mcp_catalog_tool_names": list(runner.SENDER_TOOLS),
            "mcp_catalog_unexpected_tool_count": 0,
            "mcp_tool_observation_status": "observed",
            "mcp_tool_calls": [
                {
                    "server": "cao-mcp-server",
                    "tool": name,
                    "status": "completed",
                    "error_present": False,
                    "result_present": True,
                }
                for name in runner.SENDER_CALLS
            ],
            "mcp_tool_unexpected_call_count": 0,
        },
        "receiver": {
            "observer_status": "observed",
            "mcp_observer_shutdown_observed": True,
            "mcp_catalog_observation_status": "unobserved",
            "mcp_catalog_observation_count": 0,
            "mcp_catalog_tool_names": [],
            "mcp_catalog_unexpected_tool_count": 0,
            "mcp_tool_observation_status": "unobserved",
            "mcp_tool_calls": [],
            "mcp_tool_unexpected_call_count": 0,
        },
    }

    assert runner._observer_contract(observations)
    subset = json.loads(json.dumps(observations))
    subset["sender"]["mcp_catalog_tool_names"] = ["list_siblings", "send_message"]
    assert not runner._observer_contract(subset)
    extra_call = json.loads(json.dumps(observations))
    extra_call["sender"]["mcp_tool_calls"].append(
        {"tool": "cao_ask_user", "status": "completed", "error_present": False}
    )
    assert not runner._observer_contract(extra_call)
    wrong_child = json.loads(json.dumps(observations))
    wrong_child["sender"]["mcp_tool_calls"][0]["server"] = "other-mcp-server"
    assert not runner._observer_contract(wrong_child)
    missing_catalog_event = json.loads(json.dumps(observations))
    missing_catalog_event["sender"]["mcp_catalog_observation_status"] = "unobserved"
    missing_catalog_event["sender"]["mcp_catalog_observation_count"] = 0
    assert not runner._observer_contract(missing_catalog_event)
    receiver_tool = json.loads(json.dumps(observations))
    receiver_tool["receiver"]["mcp_tool_calls"] = [
        {
            "server": "cao-mcp-server",
            "tool": "list_siblings",
            "status": "completed",
            "error_present": False,
            "result_present": True,
        }
    ]
    assert not runner._observer_contract(receiver_tool)


def _http_offer_summary(offers):
    records = [
        {
            "model_match": True,
            "kind": "primary",
            "status": "observed" if offer else "valid_empty",
            "tool_count": len(offer),
            "name_counts": {name: int(name in offer) for name in runner.HTTP_OFFER_TOOL_NAMES},
            "other_name_count": 0,
        }
        for offer in offers
    ]
    return {
        "observer_status": "observed",
        "tool_offer_status": "observed",
        "nonce": "1" * 32,
        "hook_disposed": True,
        "trace_complete": True,
        "overflow": False,
        "request_count": len(records),
        "durable_record_count": len(records),
        "target_primary_request_count": len(records),
        "valid_target_primary_request_count": len(records),
        "tool_name_counts": {
            name: sum(record["name_counts"][name] for record in records)
            for name in runner.HTTP_OFFER_TOOL_NAMES
        },
        "other_tool_name_count": 0,
        "requests": records,
    }


def test_outbound_model_requests_prove_per_role_tool_offers():
    sender = _http_offer_summary(
        [
            {*runner.MCP_HTTP_OFFER_TOOL_NAMES, "skill"},
            {*runner.MCP_HTTP_OFFER_TOOL_NAMES, "todowrite"},
        ]
    )
    receiver = _http_offer_summary([{"skill"}, {"todowrite"}])

    assert runner._http_tool_offer_contract(
        sender,
        expected_nonce="1" * 32,
        expected_mcp_tool_names=runner.MCP_HTTP_OFFER_TOOL_NAMES,
    )
    assert runner._http_tool_offer_contract(
        receiver,
        expected_nonce="1" * 32,
        expected_mcp_tool_names=(),
    )

    missing_tool = json.loads(json.dumps(sender))
    missing_tool["requests"][0]["name_counts"][runner.MCP_HTTP_OFFER_TOOL_NAMES[0]] = 0
    assert not runner._http_tool_offer_contract(
        missing_tool,
        expected_nonce="1" * 32,
        expected_mcp_tool_names=runner.MCP_HTTP_OFFER_TOOL_NAMES,
    )
    execute_exposed = json.loads(json.dumps(sender))
    execute_exposed["requests"][0]["tool_count"] += 1
    execute_exposed["requests"][0]["other_name_count"] = 1
    execute_exposed["other_tool_name_count"] = 1
    assert not runner._http_tool_offer_contract(
        execute_exposed,
        expected_nonce="1" * 32,
        expected_mcp_tool_names=runner.MCP_HTTP_OFFER_TOOL_NAMES,
    )
    receiver_with_mcp = _http_offer_summary(
        [{"skill", "cao-mcp-server_send_message"}, {"todowrite"}]
    )
    assert not runner._http_tool_offer_contract(
        receiver_with_mcp,
        expected_nonce="1" * 32,
        expected_mcp_tool_names=(),
    )
    unknown_tool = json.loads(json.dumps(receiver))
    unknown_tool["requests"][0]["tool_count"] += 1
    unknown_tool["requests"][0]["other_name_count"] = 1
    unknown_tool["other_tool_name_count"] = 1
    assert not runner._http_tool_offer_contract(
        unknown_tool,
        expected_nonce="1" * 32,
        expected_mcp_tool_names=(),
    )
    inconsistent_aggregate = json.loads(json.dumps(sender))
    inconsistent_aggregate["tool_name_counts"][runner.MCP_HTTP_OFFER_TOOL_NAMES[0]] = 0
    assert not runner._http_tool_offer_contract(
        inconsistent_aggregate,
        expected_nonce="1" * 32,
        expected_mcp_tool_names=runner.MCP_HTTP_OFFER_TOOL_NAMES,
    )
    incomplete_target = json.loads(json.dumps(receiver))
    incomplete_target["valid_target_primary_request_count"] -= 1
    assert not runner._http_tool_offer_contract(
        incomplete_target,
        expected_nonce="1" * 32,
        expected_mcp_tool_names=(),
    )


def test_derived_offer_plugin_is_model_nonce_and_profile_bound():
    source = (runner.REPO / "test/live_provider/opencode_http_tool_offer_observer.js").read_bytes()
    sender = runner._http_tool_offer_plugin_bytes(
        source,
        nonce="a" * 32,
        tool_names=runner.HTTP_OFFER_TOOL_NAMES,
    ).decode("utf-8")
    assert 'const MODEL_ID = "glm-5.3-flash"' in sender
    assert 'nonce = "' + "a" * 32 + '"' in sender
    assert all(f'"{name}"' in sender for name in runner.HTTP_OFFER_TOOL_NAMES)
    assert "longcat-2.5-preview-free" not in sender

    receiver = runner._http_tool_offer_plugin_bytes(
        source,
        nonce="b" * 32,
        tool_names=runner.HTTP_OFFER_TOOL_NAMES,
    ).decode("utf-8")
    assert all(f'"{name}"' in receiver for name in runner.HTTP_OFFER_TOOL_NAMES)
    assert 'nonce = "' + "b" * 32 + '"' in receiver
    with pytest.raises(runner.GateError):
        runner._http_tool_offer_plugin_bytes(
            source,
            nonce="invalid",
            tool_names=runner.HTTP_OFFER_TOOL_NAMES,
        )


def test_open_code_mcp_empty_operation_scope_keeps_only_source_hard_allowed_builtins():
    from cli_agent_orchestrator.utils.opencode_permissions import (
        ALL_OPENCODE_TOOLS,
        cao_tools_to_opencode_permission,
    )

    receiver_permissions = cao_tools_to_opencode_permission([])
    sender_permissions = cao_tools_to_opencode_permission(["@cao-mcp-server"])
    optional = set(runner.OPTIONAL_OPENCODE_BUILTIN_TOOL_NAMES)

    assert optional == {"skill", "todowrite"}
    for permissions in (receiver_permissions, sender_permissions):
        assert all(permissions[name] == "allow" for name in optional)
        assert all(permissions[name] == "deny" for name in set(ALL_OPENCODE_TOOLS) - optional)


def test_runtime_capability_is_bound_to_exact_current_sender_source():
    from cli_agent_orchestrator.services.interactions.capability import (
        canonical_tool_fingerprint,
        capability_hash,
    )

    token = "opaque-test-capability-string-long-enough"
    terminal = {
        "id": "a1b2c3d4",
        "session_incarnation_id": "1" * 32,
        "session_name": "cao-t104v2-0123456789abcdef",
        "agent_profile": "t104-v2-sender",
        "allowed_tools": ["@cao-mcp-server"],
    }
    fingerprint, _ = canonical_tool_fingerprint(terminal["allowed_tools"])
    actor = SimpleNamespace(
        terminal_id=terminal["id"],
        incarnation_id=terminal["session_incarnation_id"],
    )
    status = SimpleNamespace(state="current", capability_available=True)
    source = {
        "terminal_id": terminal["id"],
        "incarnation_id": terminal["session_incarnation_id"],
        "revoked_at": None,
        "capability_available": True,
        "capability_hash": capability_hash(token),
        "allowed_tool_fingerprint": fingerprint,
        "provider": "opencode_cli",
        "execution_mode": "local",
        "session_name": terminal["session_name"],
        "profile_revision": terminal["agent_profile"],
        "owner_principal_id": None,
        "owner_source": "unowned",
    }

    assert runner._capability_source_binding_matches(
        actor, status, source, terminal, token, capability_hash, canonical_tool_fingerprint
    )
    mismatched_source = {**source, "incarnation_id": "2" * 32}
    assert not runner._capability_source_binding_matches(
        actor,
        status,
        mismatched_source,
        terminal,
        token,
        capability_hash,
        canonical_tool_fingerprint,
    )
    mismatched_actor = SimpleNamespace(
        terminal_id="ffffffff", incarnation_id=terminal["session_incarnation_id"]
    )
    assert not runner._capability_source_binding_matches(
        mismatched_actor,
        status,
        source,
        terminal,
        token,
        capability_hash,
        canonical_tool_fingerprint,
    )
    stale = SimpleNamespace(state="stale_turn", capability_available=True)
    assert not runner._capability_source_binding_matches(
        actor, stale, source, terminal, token, capability_hash, canonical_tool_fingerprint
    )
    wrong_owner = {**source, "owner_principal_id": "unexpected"}
    assert not runner._capability_source_binding_matches(
        actor, status, wrong_owner, terminal, token, capability_hash, canonical_tool_fingerprint
    )


def test_private_native_config_binds_sender_capability_and_receiver_empty_filter():
    from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV
    from cli_agent_orchestrator.utils.mcp_tool_policy import (
        MCP_TOOL_FILTER_ENV,
        MCP_TOOL_PROFILE_ENV,
    )

    full_server_grant = [{"action": "cao-mcp-server_*", "resource": "*", "effect": "allow"}]

    sender_environment = {
        MCP_TOOL_PROFILE_ENV: "collaboration",
        "CAO_INTERACTION_CAPABILITY_FILE": "/private/sender.cap",
    }
    server = {"codemode": False}
    assert runner._native_mcp_profile_contract(
        "sender",
        server,
        sender_environment,
        full_server_grant,
        capability_path=Path("/private/sender.cap"),
        filter_env=MCP_TOOL_FILTER_ENV,
        profile_env=MCP_TOOL_PROFILE_ENV,
        legacy_marker=_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV,
    )
    receiver_environment = {
        MCP_TOOL_PROFILE_ENV: "collaboration",
        MCP_TOOL_FILTER_ENV: "[]",
    }
    assert runner._native_mcp_profile_contract(
        "receiver",
        server,
        receiver_environment,
        [],
        capability_path=None,
        filter_env=MCP_TOOL_FILTER_ENV,
        profile_env=MCP_TOOL_PROFILE_ENV,
        legacy_marker=_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV,
    )
    missing_direct_tools = [
        {"action": "cao-mcp-server_cao_ask_user", "resource": "*", "effect": "allow"}
    ]
    assert not runner._native_mcp_profile_contract(
        "sender",
        server,
        sender_environment,
        missing_direct_tools,
        capability_path=Path("/private/sender.cap"),
        filter_env=MCP_TOOL_FILTER_ENV,
        profile_env=MCP_TOOL_PROFILE_ENV,
        legacy_marker=_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV,
    )
    explicit_execute_grant = full_server_grant + [
        {"action": "cao-mcp-server_execute", "resource": "*", "effect": "allow"}
    ]
    assert not runner._native_mcp_profile_contract(
        "sender",
        server,
        sender_environment,
        explicit_execute_grant,
        capability_path=Path("/private/sender.cap"),
        filter_env=MCP_TOOL_FILTER_ENV,
        profile_env=MCP_TOOL_PROFILE_ENV,
        legacy_marker=_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV,
    )
    legacy_restricted = {
        **sender_environment,
        _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV: "1",
    }
    assert not runner._native_mcp_profile_contract(
        "sender",
        server,
        legacy_restricted,
        full_server_grant,
        capability_path=Path("/private/sender.cap"),
        filter_env=MCP_TOOL_FILTER_ENV,
        profile_env=MCP_TOOL_PROFILE_ENV,
        legacy_marker=_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV,
    )
    code_mode = {"codemode": True}
    assert not runner._native_mcp_profile_contract(
        "sender",
        code_mode,
        sender_environment,
        full_server_grant,
        capability_path=Path("/private/sender.cap"),
        filter_env=MCP_TOOL_FILTER_ENV,
        profile_env=MCP_TOOL_PROFILE_ENV,
        legacy_marker=_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV,
    )


def _write_private_provider_root(
    cao_home: Path,
    *,
    role: str,
    environment: dict[str, str],
    permissions: list[dict[str, str]],
    command: str | list[str] | None = None,
):
    terminal_id = "a1b2c3d4" if role == "sender" else "e5f6a7b8"
    root_parent = cao_home / "opencode-v2"
    root_parent.mkdir(mode=0o700, parents=True)
    root_parent.chmod(0o700)
    root = root_parent / f"terminal-{terminal_id}"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    root_stat = root.lstat()
    ownership = {
        "terminal_id": terminal_id,
        "claim": {
            "major": 2,
            "name": root.name,
            "device": root_stat.st_dev,
            "inode": root_stat.st_ino,
        },
    }
    marker = root / ".cao-ownership.json"
    marker.write_text(json.dumps(ownership), encoding="utf-8")
    marker.chmod(0o600)
    server = {
        "command": command
        or [
            sys.executable,
            "-m",
            "cli_agent_orchestrator.mcp_server.server",
        ],
        "codemode": False,
        "environment": environment,
    }
    config = {
        "mcp": {"servers": {"cao-mcp-server": server}},
        "agents": {f"t104-v2-{role}": {"permissions": permissions}},
    }
    config_path = root / "opencode.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    config_path.chmod(0o600)
    return terminal_id, server, config_path


def test_private_v2_root_requires_current_owned_canonical_full_profile(tmp_path):
    from cli_agent_orchestrator.utils.mcp_tool_policy import (
        MCP_TOOL_FILTER_ENV,
        MCP_TOOL_PROFILE_ENV,
    )

    cao_home = tmp_path / "cao"
    cao_home.mkdir(mode=0o700)
    cao_home.chmod(0o700)
    sender_capability = cao_home / "tmp/interaction-capabilities/sender.cap"
    sender_environment = {
        MCP_TOOL_PROFILE_ENV: "collaboration",
        "CAO_INTERACTION_CAPABILITY_FILE": str(sender_capability),
    }
    sender_id, _, _ = _write_private_provider_root(
        cao_home,
        role="sender",
        environment=sender_environment,
        permissions=[{"action": "cao-mcp-server_*", "resource": "*", "effect": "allow"}],
        command=[
            sys.executable,
            "-m",
            "cli_agent_orchestrator.mcp_server.server",
        ],
    )
    sender_terminal = {
        "id": sender_id,
        "agent_profile": "t104-v2-sender",
    }
    assert runner._provider_config_contract(
        cao_home,
        sender_terminal,
        role="sender",
        capability_path=sender_capability,
    )

    sender_root = next((cao_home / "opencode-v2").glob("terminal-*"))
    sender_config_path = sender_root / "opencode.json"
    sender_config = json.loads(sender_config_path.read_text(encoding="utf-8"))
    sender_config["mcp"]["servers"]["cao-mcp-server"]["command"] = "/tmp/fake-mcp"
    sender_config_path.write_text(json.dumps(sender_config), encoding="utf-8")
    sender_config_path.chmod(0o600)
    assert not runner._provider_config_contract(
        cao_home,
        sender_terminal,
        role="sender",
        capability_path=sender_capability,
    )

    receiver_cap_home = tmp_path / "receiver-cao"
    receiver_cap_home.mkdir(mode=0o700)
    receiver_cap_home.chmod(0o700)
    receiver_environment = {
        MCP_TOOL_PROFILE_ENV: "collaboration",
        MCP_TOOL_FILTER_ENV: "[]",
    }
    receiver_id, _, _ = _write_private_provider_root(
        receiver_cap_home,
        role="receiver",
        environment=receiver_environment,
        permissions=[],
        command=[
            sys.executable,
            "-m",
            "cli_agent_orchestrator.mcp_server.server",
        ],
    )
    assert runner._provider_config_contract(
        receiver_cap_home,
        {"id": receiver_id, "agent_profile": "t104-v2-receiver"},
        role="receiver",
        capability_path=None,
    )

    receiver_root = next((receiver_cap_home / "opencode-v2").glob("terminal-*"))
    receiver_config_path = receiver_root / "opencode.json"
    receiver_config = json.loads(receiver_config_path.read_text(encoding="utf-8"))
    receiver_config["mcp"]["servers"]["cao-mcp-server"]["environment"][
        "CAO_INTERACTION_CAPABILITY_FILE"
    ] = str(sender_capability)
    receiver_config_path.write_text(json.dumps(receiver_config), encoding="utf-8")
    receiver_config_path.chmod(0o600)
    assert not runner._provider_config_contract(
        receiver_cap_home,
        {"id": receiver_id, "agent_profile": "t104-v2-receiver"},
        role="receiver",
        capability_path=None,
    )


def test_current_manifest_contract_binds_source_binary_python_postgres_and_review(
    tmp_path,
):
    source_hashes = runner._source_fingerprints()
    source_fingerprint = hashlib.sha256(
        json.dumps(source_hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=runner.REPO, text=True).strip()
    digest = runner._sha256
    closed_source = RUNNER_PATH.read_bytes()
    executed_source = runner._execution_source_bytes(closed_source)
    closed_digest = hashlib.sha256(closed_source).hexdigest()
    executed_digest = hashlib.sha256(executed_source).hexdigest()
    run_root = Path(tempfile.mkdtemp(prefix="t104-manifest-test-", dir="/tmp"))
    run_root.chmod(0o700)
    expected = "a" * 64
    document = {
        "schema": runner.RUN_MANIFEST_SCHEMA,
        "run_enabled": True,
        "expected_source_head": head,
        "review_digest": expected,
        "repository_root": str(runner.REPO.resolve()),
        "candidate_sha256": closed_digest,
        "execution_candidate_sha256": executed_digest,
        "model": runner.MODEL,
        "source_paths": list(runner.SOURCE_PATHS),
        "source_sha256s": source_hashes,
        "source_file_count": len(source_hashes),
        "source_fingerprint_sha256": source_fingerprint,
        "postgres_autostart": 0,
        "owner_uid": __import__("os").getuid(),
        "operator_auth_snapshot_sha256": "b" * 64,
        "profile_fixture_sha256": digest(
            RUNNER_PATH.parent / "native-v2-collaboration-acceptance-profiles.json"
        ),
        "python_path": str(Path(sys.executable)),
        "python_resolved_path": str(Path(sys.executable).resolve(strict=True)),
        "python_sha256": digest(Path(sys.executable).resolve(strict=True)),
        "binary_path": str(Path(sys.executable).resolve()),
        "binary_sha256": digest(Path(sys.executable).resolve()),
        "postgres_fixture_sha256": digest(runner.REPO / "test/postgres/fixtures.py"),
        "postgres_guard_derivation_sha256": "c" * 64,
        "postgres_container_id": "d" * 64,
        "postgres_image_id": "sha256:" + "e" * 64,
        "run_root": str(run_root),
    }

    assert runner._manifest_document_valid(
        document,
        expected_head=head,
        review_digest=expected,
        runner_sha256=executed_digest,
    )
    assert not runner._manifest_document_valid(
        document,
        expected_head=head,
        review_digest="f" * 64,
        runner_sha256=executed_digest,
    )
    changed_source = {**document, "source_sha256s": {**source_hashes}}
    first_path = next(iter(changed_source["source_sha256s"]))
    changed_source["source_sha256s"][first_path] = "0" * 64
    assert not runner._manifest_document_valid(
        changed_source,
        expected_head=head,
        review_digest=expected,
        runner_sha256=executed_digest,
    )
    wrong_execution_copy = {**document, "execution_candidate_sha256": "f" * 64}
    assert not runner._manifest_document_valid(
        wrong_execution_copy,
        expected_head=head,
        review_digest=expected,
        runner_sha256=executed_digest,
    )
    wrong_closed_candidate = {**document, "candidate_sha256": "e" * 64}
    assert not runner._manifest_document_valid(
        wrong_closed_candidate,
        expected_head=head,
        review_digest=expected,
        runner_sha256=executed_digest,
    )
    resolved_launch_path = str(Path(sys.executable).resolve(strict=True))
    if resolved_launch_path != str(Path(sys.executable)):
        collapsed_venv_alias = {**document, "python_path": resolved_launch_path}
        assert not runner._manifest_document_valid(
            collapsed_venv_alias,
            expected_head=head,
            review_digest=expected,
            runner_sha256=executed_digest,
        )
    wrong_resolved_python = {
        **document,
        "python_resolved_path": str(Path("/tmp/not-the-pinned-python")),
    }
    assert not runner._manifest_document_valid(
        wrong_resolved_python,
        expected_head=head,
        review_digest=expected,
        runner_sha256=executed_digest,
    )

    alias = tmp_path / "repo-alias"
    alias.symlink_to(runner.REPO, target_is_directory=True)
    aliased_root = {**document, "run_root": str(alias / "specs")}
    assert not runner._manifest_document_valid(
        aliased_root,
        expected_head=head,
        review_digest=expected,
        runner_sha256=executed_digest,
    )
    run_root.rmdir()


def test_runtime_source_map_is_complete_and_exact():
    assert runner._runtime_source_modules_match(runner.RUN_SOURCE_RELATIVE_PATHS)
    incomplete = dict(runner.RUN_SOURCE_RELATIVE_PATHS)
    incomplete.pop("cli_agent_orchestrator.api.main")
    assert not runner._runtime_source_modules_match(incomplete)
    mismatched = dict(runner.RUN_SOURCE_RELATIVE_PATHS)
    mismatched["cli_agent_orchestrator.services.interactions.service"] = "src/other/service.py"
    assert not runner._runtime_source_modules_match(mismatched)


def test_current_production_api_lifespan_and_issuance_sources_are_bound():
    from test.fixtures.cao_server import _start_cao_server

    from cli_agent_orchestrator.api import main as api_main
    from cli_agent_orchestrator.providers import opencode_cli
    from cli_agent_orchestrator.services import terminal_service
    from cli_agent_orchestrator.services.interactions import service as interaction_service_module

    assert runner._lifespan_context_contains(
        api_main.app.router.lifespan_context, api_main.lifespan
    )
    assert runner._production_api_capability_issuance_contract(
        api_main,
        terminal_service,
        interaction_service_module,
        opencode_cli,
        _start_cao_server,
    ) == {
        "api_lifespan_and_interaction_service": True,
        "capability_issuance_path": True,
        "model_launch_source_binding": True,
        "production_api_worker": True,
    }


def test_api_worker_invocation_is_exact_real_production_entrypoint():
    python_path = Path(sys.executable)
    process = SimpleNamespace(
        args=[
            sys.executable,
            "-m",
            "cli_agent_orchestrator.api.main",
            "--host",
            "127.0.0.1",
            "--port",
            "45678",
        ]
    )
    server = SimpleNamespace(port=45678)

    assert runner._api_worker_invocation_matches(process, server, python_path)
    resolved_executable = Path(sys.executable).resolve(strict=True)
    if resolved_executable != python_path:
        wrong_venv_alias = SimpleNamespace(args=list(process.args))
        wrong_venv_alias.args[0] = str(resolved_executable)
        assert not runner._api_worker_invocation_matches(wrong_venv_alias, server, python_path)
    disabled_lifespan = SimpleNamespace(args=process.args + ["--lifespan", "off"])
    assert not runner._api_worker_invocation_matches(disabled_lifespan, server, python_path)
    wrong_module = SimpleNamespace(args=list(process.args))
    wrong_module.args[2] = "test.fixtures.cao_server"
    assert not runner._api_worker_invocation_matches(wrong_module, server, python_path)


def test_python_child_uses_the_venv_launch_path_and_canonical_hash_target(
    monkeypatch,
):
    launch_path = Path(sys.executable)
    resolved_path = launch_path.resolve(strict=True)
    assert launch_path.is_absolute()
    monkeypatch.setattr(runner, "PINNED_PYTHON", launch_path)
    monkeypatch.setattr(runner, "PINNED_PYTHON_RESOLVED", resolved_path)
    assert runner._runtime_path_valid()
    assert runner._python_manifest_identity_matches(
        {
            "python_path": str(launch_path),
            "python_resolved_path": str(resolved_path),
        }
    )

    probe = "import sys; print(sys.prefix); print(sys.base_prefix)"
    launch_result = subprocess.run(
        [str(launch_path), "-I", "-c", probe],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    launch_prefix, launch_base_prefix = launch_result.stdout.splitlines()
    if launch_path != resolved_path:
        base_result = subprocess.run(
            [str(resolved_path), "-I", "-c", probe],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        base_prefix, base_base_prefix = base_result.stdout.splitlines()
        assert launch_prefix != launch_base_prefix
        assert Path(launch_prefix) == Path(sys.prefix)
        assert base_prefix == base_base_prefix


def test_lifecycle_teardown_guards_require_exact_booleans_and_dead_identities():
    assert runner._postgres_teardown_allowed(
        api_processes_dead=True,
        capture_threads_dead=True,
        tmux_processes_dead=True,
    )
    for bad in (1, 0, None, False):
        assert not runner._postgres_teardown_allowed(
            api_processes_dead=bad,
            capture_threads_dead=True,
            tmux_processes_dead=True,
        )
        assert not runner._postgres_teardown_allowed(
            api_processes_dead=True,
            capture_threads_dead=bad,
            tmux_processes_dead=True,
        )
        assert not runner._postgres_teardown_allowed(
            api_processes_dead=True,
            capture_threads_dead=True,
            tmux_processes_dead=bad,
        )

    class Journey:
        def __init__(self, value):
            self.value = value

        def _process_identity_alive(self, _identity):
            if isinstance(self.value, BaseException):
                raise self.value
            return self.value

    records = [
        {"role": "api_a", "identity": object()},
        {"role": "api_b", "identity": object()},
    ]
    attempted = {"api_a", "api_b"}
    assert runner._api_launches_confirmed_dead(
        records, attempted_roles=attempted, journey=Journey(False)
    )
    for alive in (True, 0, 1, None, RuntimeError("unknown")):
        assert not runner._api_launches_confirmed_dead(
            records, attempted_roles=attempted, journey=Journey(alive)
        )
    assert not runner._api_launches_confirmed_dead(
        records[:1], attempted_roles=attempted, journey=Journey(False)
    )

    class Capture:
        def __init__(self, alive):
            self._thread = SimpleNamespace(is_alive=lambda: alive)
            self._failed = False

    assert runner._capture_threads_confirmed_dead([Capture(False)])
    assert runner._capture_stopped_cleanly(Capture(False))
    for alive in (True, 0, 1, None):
        assert not runner._capture_threads_confirmed_dead([Capture(alive)])
        assert not runner._capture_stopped_cleanly(Capture(alive))

    safe = {key: True for key in runner._REQUIRED_TRUE_RECEIPT_FIELDS}
    safe.update(
        {
            "api_a_launch_attempted": True,
            "api_b_launch_attempted": True,
            "api_a_cleanup": True,
            "api_b_cleanup": True,
            "postgres_cleanup_deferred": False,
            "credentials_cleanup_deferred": False,
            "native_provider_roots_immediately_after_delete": 0,
            "capability_files_immediately_after_delete": 0,
        }
    )
    assert runner._private_root_cleanup_safe(safe)
    deferred_credentials = {**safe, "credentials_cleanup_deferred": True}
    assert not runner._private_root_cleanup_safe(deferred_credentials)
    invalid_attempt_flag = {**safe, "api_a_launch_attempted": 1}
    assert not runner._private_root_cleanup_safe(invalid_attempt_flag)
    invalid_cleanup_flag = {**safe, "api_a_cleanup": 1}
    assert not runner._private_root_cleanup_safe(invalid_cleanup_flag)
    for field in (
        "api_cleanup",
        "api_processes_confirmed_dead_before_postgres_teardown",
        "agui_capture_threads_confirmed_dead_before_postgres_teardown",
        "tmux_processes_confirmed_dead_before_postgres_teardown",
        "tmux_cleanup",
        "postgres_cleanup",
    ):
        unknown = {**safe, field: 1}
        assert not runner._private_root_cleanup_safe(unknown)


def test_private_output_range_controls_cover_all_eight_existing_clock_checks():
    assert runner._output_range_controls() == 8


def test_sigterm_routes_through_cleanup_before_child_process_exit():
    script = f"""
import importlib.util, json, os, signal, subprocess, sys
path = {str(RUNNER_PATH)!r}
spec = importlib.util.spec_from_file_location("sigterm_runner", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
ordering = []
previous = signal.getsignal(signal.SIGTERM)
child = None
receipt = {{"actual_cancel_observed": False}}
reached_checkpoint = False
try:
    with module._scoped_sigterm_handler():
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            reached_checkpoint = True
            assert module._SIGTERM_PENDING is True
            module._cancellation_checkpoint()
        finally:
            module._SIGTERM_CLEANUP_ACTIVE = True
            ordering.append("capture_stop")
            child.terminate()
            child.wait(timeout=3)
            ordering.append("api_stopped")
            module._cancellation_checkpoint()
            ordering.extend(["tmux_cleanup", "postgres_teardown", "private_root_cleanup"])
            receipt["actual_cancel_observed"] = module._SIGTERM_PENDING is True
except module._ControlledSigterm:
    ordering.append("safe_point_cancel_caught")
assert child is not None and child.poll() is not None
assert reached_checkpoint
assert receipt["actual_cancel_observed"] is True
assert receipt["actual_cancel_observed"] is not False
assert signal.getsignal(signal.SIGTERM) is previous
ordering.append("handler_restored")
assert ordering == ["capture_stop", "api_stopped", "tmux_cleanup", "postgres_teardown", "private_root_cleanup", "safe_point_cancel_caught", "handler_restored"]
print(json.dumps(ordering))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=runner.REPO,
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "capture_stop",
        "api_stopped",
        "tmux_cleanup",
        "postgres_teardown",
        "private_root_cleanup",
        "safe_point_cancel_caught",
        "handler_restored",
    ]


def test_run_marks_signal_received_at_cleanup_boundary_before_receipt_commit():
    script = f"""
import importlib.util, json, os, signal, sys
path = {str(RUNNER_PATH)!r}
spec = importlib.util.spec_from_file_location("sigterm_commit_runner", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
module.RUN_ENABLED = True
module._private_execution_copy_contract = lambda: True
module.RECEIPT = None
module._write_private = lambda path, content: None
module._acceptance_contract = lambda receipt: receipt.get("actual_cancel_observed") is False
def fake_run_enabled(*args, **kwargs):
    os.kill(os.getpid(), signal.SIGTERM)
    return {{"actual_cancel_observed": False, "cleanup_failures": [], "error_code": None}}
module._run_enabled = fake_run_enabled
previous = signal.getsignal(signal.SIGTERM)
receipt = module._run(
    module.MODEL,
    go=True,
    expected_head="a" * 40,
    manifest_path=None,
    manifest_sha256="b" * 64,
    review_digest="c" * 64,
)
assert signal.getsignal(signal.SIGTERM) is previous
assert receipt["actual_cancel_observed"] is True
assert receipt["success"] is False
assert "sigterm_requested" in receipt["cleanup_failures"]
print(json.dumps({{"success": receipt["success"], "cancel": receipt["actual_cancel_observed"]}}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=runner.REPO,
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    output_lines = result.stdout.splitlines()
    assert json.loads(output_lines[0])["success"] is False
    assert json.loads(output_lines[1]) == {"success": False, "cancel": True}


def test_credential_snapshot_observation_includes_wal_shm_and_unknown_errors(tmp_path, monkeypatch):
    root = tmp_path / "private-run"
    database_directory = root / "opencode-data" / "opencode"
    database_directory.mkdir(parents=True)
    for sidecar in ("opencode.db-wal", "opencode.db-shm"):
        path = database_directory / sidecar
        path.write_bytes(b"private")
        assert runner._credential_snapshot_status(root, snapshot_created=True) == "retained"
        path.unlink()
    assert runner._credential_snapshot_status(root, snapshot_created=True) == "removed"

    denied_path = database_directory / "opencode.db-wal"
    original_lstat = Path.lstat

    def denied_lstat(path):
        if path == denied_path:
            raise PermissionError("not readable")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", denied_lstat)
    assert runner._credential_snapshot_status(root, snapshot_created=True) == "unknown"


def test_scoped_sigterm_handler_rejects_non_main_thread():
    import threading

    errors = []

    def enter_handler():
        try:
            with runner._scoped_sigterm_handler():
                raise AssertionError("worker thread entered process signal scope")
        except runner.GateError as error:
            errors.append(error.code)

    worker = threading.Thread(target=enter_handler)
    worker.start()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert errors == ["execution_requires_main_thread"]


def test_current_working_directory_must_match_the_exact_private_role_workspace(tmp_path):
    expected = tmp_path / "owned-workdir"
    expected.mkdir(mode=0o700)
    other = tmp_path / "other-workdir"
    other.mkdir(mode=0o700)

    assert runner._working_directory_matches(str(expected), expected)
    assert not runner._working_directory_matches(str(other), expected)
    assert not runner._working_directory_matches("relative/path", expected)
    assert not runner._working_directory_matches(str(tmp_path / "missing"), expected)


def test_working_directory_request_uses_remaining_deadline_and_rejects_late_arrival(
    monkeypatch,
):
    clock = [10.0]
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"working_directory": "/private/role"}

    class Requests:
        def __init__(self, arrival):
            self.arrival = arrival
            self.calls = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            clock[0] = self.arrival
            return Response()

    timely = Requests(13.9)
    assert runner._api_json(
        timely,
        "GET",
        "http://127.0.0.1/working-directory",
        expected=200,
        deadline=14.0,
    ) == {"working_directory": "/private/role"}
    assert timely.calls[0][2]["timeout"] == (4.0, 4.0)

    clock[0] = 10.0
    late = Requests(14.01)
    with pytest.raises(runner.GateError, match="api_response_after_deadline"):
        runner._api_json(
            late,
            "GET",
            "http://127.0.0.1/working-directory",
            expected=200,
            deadline=14.0,
        )
    assert late.calls[0][2]["timeout"] == (4.0, 4.0)

    clock[0] = 15.0
    expired = Requests(15.0)
    with pytest.raises(runner.GateError, match="api_request_deadline_expired"):
        runner._api_json(
            expired,
            "GET",
            "http://127.0.0.1/working-directory",
            expected=200,
            deadline=14.0,
        )
    assert expired.calls == []


def test_acceptance_requires_exact_receiver_acknowledgement(monkeypatch):
    manifest = {
        "profile_fixture_sha256": "1" * 64,
        "operator_auth_snapshot_sha256": "2" * 64,
    }
    for name, value in (
        ("EXPECTED_HEAD", "3" * 40),
        ("RUN_MANIFEST_SHA256", "4" * 64),
        ("SOURCE_FINGERPRINT_SHA256", "5" * 64),
        ("RUNNER_SHA256_AT_ADMISSION", "6" * 64),
        ("REVIEW_DIGEST", "c" * 64),
        ("RUN_MANIFEST", manifest),
        ("PINNED_OPENCODE_SHA256", "7" * 64),
        ("PINNED_POSTGRES_ID", "8" * 64),
        ("PINNED_POSTGRES_IMAGE", "sha256:" + "9" * 64),
        ("PINNED_POSTGRES_FIXTURE_SHA256", "a" * 64),
        ("PINNED_POSTGRES_GUARD_DERIVATION_SHA256", "b" * 64),
    ):
        monkeypatch.setattr(runner, name, value)
    monkeypatch.setattr(runner, "_api_process_identities_valid", lambda _: True)
    monkeypatch.setattr(runner, "_restart_snapshots_unchanged", lambda _: True)

    receipt = {field: True for field in runner._REQUIRED_TRUE_RECEIPT_FIELDS}
    receipt.update(
        {
            "schema": runner.RECEIPT_SCHEMA,
            "model": runner.MODEL,
            "auth_lane": runner.MODEL,
            "cao_auth_mode": "off",
            "source_head_expected": runner.EXPECTED_HEAD,
            "source_head": runner.EXPECTED_HEAD,
            "run_manifest_sha256": runner.RUN_MANIFEST_SHA256,
            "review_digest": runner.REVIEW_DIGEST,
            "source_fingerprint_sha256_expected": runner.SOURCE_FINGERPRINT_SHA256,
            "runner_sha256_expected": runner.RUNNER_SHA256_AT_ADMISSION,
            "runner_sha256": runner.RUNNER_SHA256_AT_ADMISSION,
            "source_fingerprint_sha256": runner.SOURCE_FINGERPRINT_SHA256,
            "profile_fixture_sha256_expected": manifest["profile_fixture_sha256"],
            "operator_auth_snapshot_sha256": manifest["operator_auth_snapshot_sha256"],
            "sender_capability_source_owner_kind": "unowned",
            "native_surface": (
                "full unfiltered collaboration profile; human capability issued and source-bound"
            ),
            "cross_model_or_oauth_claimed": False,
            "receiver_native_builtin_execution_observation": "not_observed",
            "native_model_offer_scope": "selected_model_primary_requests_only",
            "receipt_redacted_only": True,
            "private_raw_evidence_present": True,
            "actual_cancel_observed": False,
            "credentials_cleanup_deferred": False,
            "retained_private_state": {
                "status": "removed",
                "credential_snapshot": "removed",
            },
            "binary_sha256_expected": runner.PINNED_OPENCODE_SHA256,
            "postgres_container_id_expected": runner.PINNED_POSTGRES_ID,
            "postgres_image_id_expected": runner.PINNED_POSTGRES_IMAGE,
            "postgres_container_id": runner.PINNED_POSTGRES_ID,
            "postgres_image_id": runner.PINNED_POSTGRES_IMAGE,
            "postgres_fixture_cleanup_guard_source_sha256": (runner.PINNED_POSTGRES_FIXTURE_SHA256),
            "postgres_fixture_cleanup_guard_derived_sha256": (
                runner.PINNED_POSTGRES_GUARD_DERIVATION_SHA256
            ),
            "postgres_cleanup_deferred": False,
            "claude_used": False,
            "session_delete_http_status": 200,
            "native_provider_roots_immediately_after_delete": 0,
            "capability_files_immediately_after_delete": 0,
            "cleanup_failures": [],
            "observer_seal_seconds": 1.0,
            "memory_admissions_mib": {
                key: runner.MEMORY_FLOOR_MIB for key in runner._SUCCESS_MEMORY_ADMISSIONS
            },
            "receiver_model_phase_seconds": 1.0,
            "sender_model_phase_seconds": 1.0,
            "phases": [
                {
                    "name": name,
                    "one_exact_pg_api_message": True,
                    "agui_redacted_one_notification": True,
                    "receiver_turn_completed_once": True,
                    "receiver_ack_exact": True,
                    "durable_turn_completed": True,
                    "last_receipt_unicode_verified": True,
                    "agui_capture_stopped_cleanly": True,
                    "sender_model_within_75_seconds": True,
                    "receiver_model_within_75_seconds": True,
                    "sender_model_seconds": 1.0,
                    "receiver_model_seconds": 1.0,
                }
                for name, _ in runner.PHASES
            ],
        }
    )
    assert runner._acceptance_contract(receipt)
    cancelled_receipt = {**receipt, "actual_cancel_observed": True}
    assert not runner._acceptance_contract(cancelled_receipt)
    deferred_credentials = {**receipt, "credentials_cleanup_deferred": True}
    assert not runner._acceptance_contract(deferred_credentials)
    unrestricted_retention = {
        **receipt,
        "retained_private_state": {
            "status": "retained",
            "credential_snapshot": "retained",
            "path": "/operator/private/path",
        },
    }
    assert not runner._acceptance_contract(unrestricted_retention)
    receipt["phases"][0]["receiver_ack_exact"] = False
    assert not runner._acceptance_contract(receipt)
