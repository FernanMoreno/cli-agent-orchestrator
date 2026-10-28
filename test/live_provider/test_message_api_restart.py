"""Offline contract tests for the private Spec023 acceptance candidate."""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
from datetime import datetime, timezone
from pathlib import Path

import message_api_restart
import message_api_restart_live
import message_mcp_observer
import pytest
from message_api_restart import (
    INBOX_STATUSES,
    POST_RESTART_MESSAGE,
    PRE_RESTART_MESSAGE,
    PROVIDER_GATE_ENV,
    SOURCE_ROOT_ENV,
    build_message_mcp_server_config,
    classify_native_create_error_body,
    classify_native_create_error_response,
    compare_inbox_row,
    delete_exact_terminals,
    is_source_bound_mcp_target,
    message_mcp_config_ready,
    parse_provider_gates,
    phase_trigger,
    project_safe_receipt,
    read_dispatch_audit,
    require_selected_provider_ready,
    validate_agui_notification,
    validate_dispatch_history,
    validate_restart_handoff,
)
from message_api_restart_live import (
    AcceptanceFailure,
    AguiCapture,
    RunState,
    _accumulate_event_evidence,
    _audit_failure_present,
    _await_phase,
    _cleanup,
    _phase_failure_kind,
    _record_failure_stage,
    _valid_row,
)

from cli_agent_orchestrator.models.inbox import InboxMessage, MessageStatus
from cli_agent_orchestrator.providers.claude_code import ClaudeCodeProvider
from cli_agent_orchestrator.providers.codex import CodexProvider
from cli_agent_orchestrator.providers.opencode_cli import OpenCodeCliProvider

pytestmark = pytest.mark.offline_database


EXPECTED_BODY = "Escribe una nota privada: café, español y 日本語. SPEC023_BODY_SENTINEL"

_REQUIRED_GENERIC_MESSAGE_SOURCE_BINDINGS = frozenset(
    {
        "src/cli_agent_orchestrator/mcp_server/server.py",
        "src/cli_agent_orchestrator/mcp_server/physical_eof.py",
        "src/cli_agent_orchestrator/utils/mcp_owner_stop.py",
        "src/cli_agent_orchestrator/services/session_service.py",
        "src/cli_agent_orchestrator/services/cleanup_service.py",
        "src/cli_agent_orchestrator/mcp_server/interaction_pull_flow_only.py",
        "src/cli_agent_orchestrator/mcp_server/tool_profiles.py",
        "src/cli_agent_orchestrator/utils/orchestration.py",
        "src/cli_agent_orchestrator/api/main.py",
        "src/cli_agent_orchestrator/clients/database.py",
        "src/cli_agent_orchestrator/models/inbox.py",
        "src/cli_agent_orchestrator/models/terminal.py",
        "src/cli_agent_orchestrator/services/inbox_service.py",
        "src/cli_agent_orchestrator/services/terminal_service.py",
        "src/cli_agent_orchestrator/services/status_monitor.py",
        "src/cli_agent_orchestrator/services/event_log_service.py",
        "src/cli_agent_orchestrator/services/agui_stream.py",
        "src/cli_agent_orchestrator/plugins/__init__.py",
        "src/cli_agent_orchestrator/plugins/base.py",
        "src/cli_agent_orchestrator/plugins/events.py",
        "src/cli_agent_orchestrator/plugins/registry.py",
        "src/cli_agent_orchestrator/plugins/builtin/event_log_publisher.py",
        "src/cli_agent_orchestrator/backends/registry.py",
        "src/cli_agent_orchestrator/backends/tmux_backend.py",
        "src/cli_agent_orchestrator/providers/base.py",
        "src/cli_agent_orchestrator/providers/manager.py",
        "src/cli_agent_orchestrator/providers/codex.py",
        "src/cli_agent_orchestrator/providers/claude_code.py",
        "src/cli_agent_orchestrator/services/turn_recovery_service.py",
        "src/cli_agent_orchestrator/utils/tool_mapping.py",
        "src/cli_agent_orchestrator/utils/mcp_tool_policy.py",
        "src/cli_agent_orchestrator/utils/mcp_readiness.py",
        "src/cli_agent_orchestrator/providers/mcp_interactions.py",
        "src/cli_agent_orchestrator/providers/opencode_cli.py",
        "src/cli_agent_orchestrator/services/install_service.py",
        "src/cli_agent_orchestrator/utils/mcp_resolution.py",
        "src/cli_agent_orchestrator/utils/opencode_config.py",
        "src/cli_agent_orchestrator/utils/opencode_permissions.py",
        "src/cli_agent_orchestrator/utils/opencode_v2.py",
    }
)


def _source_root_from_test_file() -> Path:
    configured_root = os.environ.get(SOURCE_ROOT_ENV)
    if configured_root is None:
        source_root = Path(__file__).resolve(strict=True).parents[2]
    else:
        if not configured_root.strip():
            raise AssertionError(f"{SOURCE_ROOT_ENV} must not be empty")
        source_root = Path(configured_root).resolve(strict=True)
    if not source_root.is_dir():
        raise AssertionError("Spec023 source root must be a directory")
    return source_root


def _same_source_file(actual: Path, expected: Path) -> bool:
    try:
        return actual.samefile(expected)
    except OSError:
        return False


def _private_tmux_test_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="s023test-", dir="/tmp"))
    root.chmod(0o700)
    return root


ERROR_SENTINEL = "SPEC023_ERROR_SENTINEL"
AUTH_SENTINEL = "SPEC023_AUTH_SENTINEL"


def test_source_root_defaults_to_test_checkout_and_honors_candidate_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_checkout = Path(__file__).resolve(strict=True).parents[2]
    monkeypatch.delenv(SOURCE_ROOT_ENV, raising=False)
    assert _source_root_from_test_file() == test_checkout

    monkeypatch.setenv(SOURCE_ROOT_ENV, str(test_checkout))
    assert _source_root_from_test_file() == test_checkout


def test_inbox_row_comparison_checks_existing_immutable_fields_and_body_privately() -> None:
    expected = {
        "id": 41,
        "sender_id": "sender-a",
        "receiver_id": "receiver-b",
        "message": EXPECTED_BODY,
        "created_at": "2026-10-15T10:00:00+00:00",
    }
    row = {
        **expected,
        "status": "delivered",
    }

    assert compare_inbox_row(expected, row)
    assert compare_inbox_row(expected, {**expected, "status": "pending"})
    assert not compare_inbox_row(expected, {**row, "message": "truncated"})
    assert not compare_inbox_row(expected, {**row, "id": 42})
    assert not compare_inbox_row(expected, {**row, "status": "invented"})
    assert INBOX_STATUSES == {"pending", "delivered", "reconcile", "failed"}
    assert "version" not in expected


def test_inbox_helpers_accept_native_positive_integer_id_and_reject_invalid_ids() -> None:
    model = InboxMessage(
        id=41,
        sender_id="sender-a",
        receiver_id="receiver-b",
        message=EXPECTED_BODY,
        status=MessageStatus.DELIVERED,
        created_at=datetime(2026, 10, 15, tzinfo=timezone.utc),
    )
    row = model.model_dump(mode="json")
    expected = {key: value for key, value in row.items() if key != "status"}

    assert type(row["id"]) is int
    assert compare_inbox_row(expected, row)
    assert _valid_row(row, "sender-a", "receiver-b", EXPECTED_BODY)
    assert compare_inbox_row(expected, {**row, "id": 42}) is False

    for invalid_id in (True, "41", 0, -1):
        assert not compare_inbox_row({**expected, "id": invalid_id}, row)
        assert not compare_inbox_row(expected, {**row, "id": invalid_id})
        assert not _valid_row({**row, "id": invalid_id}, "sender-a", "receiver-b", EXPECTED_BODY)


def test_agui_notification_requires_redacted_send_message_metadata() -> None:
    event = {
        "type": "TEXT_MESSAGE_CONTENT",
        "message_id": "receiver-b",
        "role": "assistant",
        "delta": "",
        "metadata": {
            "sender": "sender-a",
            "receiver": "receiver-b",
            "orchestration_type": "send_message",
        },
    }

    assert validate_agui_notification(event, sender_id="sender-a", receiver_id="receiver-b")
    assert not validate_agui_notification(
        {**event, "delta": EXPECTED_BODY},
        sender_id="sender-a",
        receiver_id="receiver-b",
    )
    assert not validate_agui_notification(
        {**event, "metadata": {**event["metadata"], "orchestration_type": "handoff"}},
        sender_id="sender-a",
        receiver_id="receiver-b",
    )
    assert not validate_agui_notification(
        {
            **event,
            "metadata": {**event["metadata"], "unmodeled": "unexpected_field"},
        },
        sender_id="sender-a",
        receiver_id="receiver-b",
    )
    assert not validate_agui_notification(
        {
            **event,
            "metadata": {**event["metadata"], "unmodeled": PRE_RESTART_MESSAGE},
        },
        sender_id="sender-a",
        receiver_id="receiver-b",
    )
    assert not validate_agui_notification(
        {**event, "unmodeled": {"nested": POST_RESTART_MESSAGE}},
        sender_id="sender-a",
        receiver_id="receiver-b",
    )


@pytest.mark.parametrize(
    "message",
    [PRE_RESTART_MESSAGE, PRE_RESTART_MESSAGE[:-1] + "。"],
    ids=["exact-fixture-body", "ideographic-full-stop-near-match"],
)
def test_expected_message_matches_source_send_message_injection_with_fake_http(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    source_root = _source_root_from_test_file()
    source_path = source_root / "src/cli_agent_orchestrator/utils/orchestration.py"
    source_entry = str(source_root / "src")
    if source_entry not in sys.path:
        sys.path.insert(0, source_entry)
    orchestration = importlib.import_module("cli_agent_orchestrator.utils.orchestration")
    assert _same_source_file(Path(orchestration.__file__), source_path)

    sender_id = "a1b2c3d4"
    receiver_id = "e5f60718"
    captured: dict[str, object] = {}

    class Response:
        status_code = 201

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"id": "private-test-row"}

    def fake_post(url: str, **kwargs: object) -> Response:
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return Response()

    monkeypatch.setattr(orchestration, "ENABLE_SENDER_ID_INJECTION", True)
    monkeypatch.setattr(orchestration, "_current_terminal_id", lambda: sender_id)
    monkeypatch.setattr(orchestration, "_callback_route", lambda: (None, None))
    monkeypatch.setattr(orchestration, "_auth_headers", lambda *_args: {})
    monkeypatch.setattr(orchestration, "elastic_worker_gateway_headers", lambda: {})
    monkeypatch.setattr(orchestration, "_mcp_timeout", lambda: 1.0)
    monkeypatch.setattr(orchestration.requests, "post", fake_post)

    result = orchestration._send_message_impl(receiver_id, message)
    expected = message_api_restart.expected_persisted_message(PRE_RESTART_MESSAGE, sender_id)
    suffix = expected[len(PRE_RESTART_MESSAGE) :]

    assert result == {"id": "private-test-row"}
    assert isinstance(captured.get("params"), dict)
    assert captured["params"]["sender_id"] == sender_id
    assert captured["params"]["message"] == message + suffix
    assert captured["params"]["message"] != message
    if message == PRE_RESTART_MESSAGE:
        assert captured["params"]["message"] == expected
    else:
        # A transport preserves even an incorrect near-match. The acceptance
        # fixture must reject it rather than silently repair punctuation.
        assert captured["params"]["message"] != expected
        assert message[:-1] == PRE_RESTART_MESSAGE[:-1]
        assert ord(message[-1]) == 0x3002
        assert ord(PRE_RESTART_MESSAGE[-1]) == 0x002E


def test_expected_persisted_message_is_the_exact_source_suffix() -> None:
    sender_id = "a1b2c3d4"
    assert message_api_restart.expected_persisted_message(PRE_RESTART_MESSAGE, sender_id) == (
        f"{PRE_RESTART_MESSAGE}\n\n[Message from terminal {sender_id}. "
        "Use send_message MCP tool for any follow-up work.]"
    )
    assert message_api_restart.expected_persisted_message(
        POST_RESTART_MESSAGE, sender_id
    ).startswith(POST_RESTART_MESSAGE)
    with pytest.raises(ValueError, match="^invalid_persisted_message$"):
        message_api_restart.expected_persisted_message("other", sender_id)
    with pytest.raises(ValueError, match="^invalid_persisted_message$"):
        message_api_restart.expected_persisted_message(PRE_RESTART_MESSAGE, "BAD-ID")


def test_sender_prompt_accepts_real_receipt_envelopes_for_all_supported_providers() -> None:
    receiver_id = "spec023-receiver"
    source_root = _source_root_from_test_file()
    adapters = (
        ("codex", CodexProvider, "src/cli_agent_orchestrator/providers/codex.py"),
        (
            "claude_code",
            ClaudeCodeProvider,
            "src/cli_agent_orchestrator/providers/claude_code.py",
        ),
        (
            "opencode_cli",
            OpenCodeCliProvider,
            "src/cli_agent_orchestrator/providers/opencode_cli.py",
        ),
    )
    generic_input_clause = (
        "one of these triggers followed only by the CAO-generated turn-receipt envelope"
    )
    codex_input_clause = (
        "one of these triggers followed only by the CAO-generated Codex turn-receipt envelope"
    )
    reply_receipt_clause = "reply with `sent` on its own line followed by the exact CAO receipt"
    missing_prompt_contract = False
    prompts = []

    for provider_name, provider_type, provider_module_path in adapters:
        provider_module = sys.modules.get(provider_type.__module__)
        expected_module = source_root / provider_module_path
        if (
            provider_module is None
            or not isinstance(getattr(provider_module, "__file__", None), str)
            or not _same_source_file(Path(provider_module.__file__), expected_module)
        ):
            pytest.fail("receipt_provider_source_origin_mismatch", pytrace=False)
        prompt = message_api_restart_live._build_sender_prompt(provider_name, receiver_id)
        prompts.append(prompt)
        has_receipt_input_clause = generic_input_clause in prompt or (
            provider_name == "codex" and codex_input_clause in prompt
        )
        has_final_receipt_instruction = "on the final visible line" in prompt or (
            provider_name == "codex" and "required by the Codex envelope" in prompt
        )
        missing_prompt_contract = missing_prompt_contract or not (
            "the entire user message is exactly one of these phase triggers" in prompt
            and "exactly one of these triggers" in prompt
            and has_receipt_input_clause
            and "any other text or second trigger is invalid" in prompt
            and reply_receipt_clause in prompt
            and has_final_receipt_instruction
            and "reply only with `sent`" not in prompt
        )
        if (
            prompt.count("call send_message exactly once") != 2
            or prompt.count("Never repeat a call.") != 1
            or prompt.count(receiver_id) != 1
            or json.dumps(PRE_RESTART_MESSAGE, ensure_ascii=False) not in prompt
            or json.dumps(POST_RESTART_MESSAGE, ensure_ascii=False) not in prompt
        ):
            pytest.fail("receipt_provider_prompt_scope_changed", pytrace=False)

        for phase in ("before_restart", "after_restart"):
            trigger = phase_trigger(phase)
            if prompt.count(trigger) != 1:
                pytest.fail("receipt_provider_trigger_missing_or_duplicated", pytrace=False)
            provider = provider_type(
                terminal_id="spec023-sender",
                session_name="cao-spec023-test",
                window_name="0",
            )
            prepared = provider.prepare_input(trigger)
            receipt = provider._pending_turn_receipt
            if (
                provider.requires_turn_receipt is not True
                or not isinstance(receipt, str)
                or not receipt
            ):
                pytest.fail("receipt_provider_prepare_input_missing_receipt", pytrace=False)
            if (
                prepared != provider._format_turn_receipt_input(trigger, receipt)
                or not prepared.startswith(trigger + chr(10) * 2)
                or prepared.count(trigger) != 1
                or "one final line containing exactly this receipt" not in prepared
            ):
                pytest.fail("receipt_provider_prepare_input_envelope_mismatch", pytrace=False)
            if provider._result_has_active_receipt("sent"):
                pytest.fail("receipt_provider_guard_accepted_bare_result", pytrace=False)
            if not provider._result_has_active_receipt("sent" + chr(10) + receipt):
                pytest.fail("receipt_provider_guard_rejected_active_receipt", pytrace=False)

    if missing_prompt_contract or len(set(prompts)) != 1:
        pytest.fail("receipt_provider_prompt_contract_missing", pytrace=False)


def test_owned_session_name_uses_the_source_prefix_and_fixed_run_token() -> None:
    source_root = _source_root_from_test_file()
    source_entry = str(source_root / "src")
    if source_entry not in sys.path:
        sys.path.insert(0, source_entry)
    constants = importlib.import_module("cli_agent_orchestrator.constants")
    assert _same_source_file(
        Path(constants.__file__), source_root / "src/cli_agent_orchestrator/constants.py"
    )
    assert constants.SESSION_PREFIX == "cao-"
    assert (
        message_api_restart.owned_session_name(constants.SESSION_PREFIX, "a1b2c3d4e5")
        == "cao-spec023-a1b2c3d4e5"
    )
    with pytest.raises(ValueError, match="^invalid_session_name$"):
        message_api_restart.owned_session_name("", "a1b2c3d4e5")
    with pytest.raises(ValueError, match="^invalid_session_name$"):
        message_api_restart.owned_session_name(constants.SESSION_PREFIX, "bad")


def test_live_source_modules_are_resolved_from_the_explicit_checkout() -> None:
    source_root = _source_root_from_test_file()
    modules = message_api_restart_live._source_modules(source_root)
    assert len(modules) == 6
    relative_paths = (
        "src/cli_agent_orchestrator/constants.py",
        "src/cli_agent_orchestrator/clients/tmux.py",
        "src/cli_agent_orchestrator/clients/postgres/config.py",
        "test/fixtures/cao_server.py",
        "test/postgres/fixtures.py",
        "test/e2e/test_real_provider_matrix.py",
    )
    assert all(
        _same_source_file(Path(module.__file__), source_root / relative_path)
        for module, relative_path in zip(modules, relative_paths, strict=True)
    )
    postgres_config = modules[2]
    assert postgres_config.DatabaseTarget.__module__ == (
        "cli_agent_orchestrator.clients.postgres.config"
    )
    owned_tmux_module = importlib.import_module("test.fixtures.owned_tmux")
    assert owned_tmux_module is message_api_restart_live.owned_tmux
    assert "test/fixtures/owned_tmux.py" in message_api_restart_live._SOURCE_BINDING_FILES
    assert _same_source_file(
        Path(owned_tmux_module.__file__), source_root / "test/fixtures/owned_tmux.py"
    )
    process_identity = message_api_restart_live._read_process_identity(os.getpid())
    assert process_identity is not None
    assert process_identity[0] == os.geteuid()
    assert process_identity[1].isdecimal()


def test_source_bindings_cover_generic_message_persistence_delivery_and_agui_owners() -> None:
    assert _REQUIRED_GENERIC_MESSAGE_SOURCE_BINDINGS <= set(
        message_api_restart_live._SOURCE_BINDING_FILES
    )


@pytest.mark.parametrize("explicit", [False, True])
def test_native_lane_interpreter_binding_is_selected_at_actual_module_import(explicit):
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env.pop("CAO_SPEC023_PYTHON", None)
    if explicit:
        env["CAO_SPEC023_PYTHON"] = sys.executable
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo / "test/live_provider"), str(repo), str(repo / "src")]
    )
    result = subprocess.run(
        [sys.executable, "-c", "import message_api_restart_live as m; print(m.PINNED_PYTHON)"],
        env=env,
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, "native lane import failed"
    expected = (
        sys.executable if explicit else "/home/felni/.cache/caos-spec016-validation-venv/bin/python"
    )
    assert result.stdout.strip() == expected


@pytest.mark.parametrize(
    ("binding_area", "relative_path"),
    [
        (
            "source",
            "src/cli_agent_orchestrator/clients/postgres/migrations.py",
        ),
        *[
            ("source", relative_path)
            for relative_path in sorted(_REQUIRED_GENERIC_MESSAGE_SOURCE_BINDINGS)
        ],
        ("candidate", "test_message_api_restart.py"),
    ],
)
@pytest.mark.parametrize("damage", ["missing", "mutated", "symlink"])
def test_added_source_bindings_fail_before_auth_or_spawn(
    binding_area: str,
    relative_path: str,
    damage: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    candidate_dir = tmp_path / "candidate"
    candidate_checkout = Path(__file__).parent

    source_relatives = set(message_api_restart_live._SOURCE_BINDING_FILES)
    source_relatives.add("src/cli_agent_orchestrator/clients/postgres/migrations.py")
    for relative in source_relatives:
        target = source_root / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(b"offline source binding")

    candidate_relatives = set(message_api_restart_live._hash_candidate_bindings(candidate_checkout))
    candidate_relatives.add("test_message_api_restart.py")
    for relative in candidate_relatives:
        target = candidate_dir / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(b"offline candidate binding")

    root = source_root if binding_area == "source" else candidate_dir
    target = root / relative_path
    if damage == "missing":
        target.unlink()
    elif damage == "symlink":
        target.unlink()
        link_target = tmp_path / "symlink-target"
        link_target.write_bytes(b"regular target")
        target.symlink_to(link_target)
    else:
        assert damage == "mutated"

    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    evidence_root.chmod(0o700)
    monkeypatch.setattr(
        message_api_restart_live,
        "__file__",
        str(candidate_dir / "message_api_restart_live.py"),
    )
    monkeypatch.setattr(message_api_restart_live, "PINNED_PYTHON", sys.executable)
    monkeypatch.setattr(
        message_api_restart_live.shutil,
        "which",
        lambda _binary: "/offline/fake-provider",
    )
    monkeypatch.setattr(
        message_api_restart_live,
        "_source_modules",
        lambda _source_root: (
            types.SimpleNamespace(SESSION_PREFIX="cao-"),
            types.SimpleNamespace(),
            types.SimpleNamespace(DatabaseTarget=object),
            types.SimpleNamespace(),
            types.SimpleNamespace(),
            types.SimpleNamespace(),
        ),
    )

    for gate in PROVIDER_GATE_ENV.values():
        monkeypatch.delenv(gate, raising=False)
    monkeypatch.setenv(PROVIDER_GATE_ENV["codex"], "1")
    monkeypatch.setenv(message_api_restart_live.LIVE_ENABLE_ENV, "1")
    monkeypatch.setenv(message_api_restart_live.POSTGRES_AUTOSTART_ENV, "0")
    monkeypatch.setenv(
        message_api_restart_live.POSTGRES_METADATA_ENV,
        str(tmp_path / "postgres-metadata.json"),
    )
    monkeypatch.setenv(
        "CAO_REAL_PROVIDER_E2E_PROVIDERS",
        json.dumps({"codex": {"binary": "fake-provider", "exclude_reason": None}}),
    )
    monkeypatch.setenv(SOURCE_ROOT_ENV, str(source_root))
    monkeypatch.setenv(message_api_restart_live.EVIDENCE_ROOT_ENV, str(evidence_root))

    if damage == "mutated":
        hash_name = (
            "_hash_source_bindings" if binding_area == "source" else "_hash_candidate_bindings"
        )
        real_hash = getattr(message_api_restart_live, hash_name)
        calls = 0

        def mutate_after_first_hash(root_path: Path) -> dict[str, str]:
            nonlocal calls
            bindings = real_hash(root_path)
            calls += 1
            if calls == 1:
                target.write_bytes(target.read_bytes() + b"\nmutated-after-snapshot\n")
            return bindings

        monkeypatch.setattr(message_api_restart_live, hash_name, mutate_after_first_hash)

    side_effects: list[str] = []

    def forbidden(name: str):
        def record_and_fail(*_args: object, **_kwargs: object) -> None:
            side_effects.append(name)
            raise AssertionError(f"unexpected side effect: {name}")

        return record_and_fail

    for name in (
        "_provider_auth_helpers",
        "_create_private_tmux_tmpdir",
        "_pick_free_port",
        "_start_server",
        "_api_process",
    ):
        monkeypatch.setattr(message_api_restart_live, name, forbidden(name))

    if damage in {"missing", "symlink"}:
        with pytest.raises(pytest.fail.Exception, match="spec023_preflight_failed"):
            message_api_restart_live.run_selected_message_acceptance(object())
    else:
        receipt = message_api_restart_live.run_selected_message_acceptance(object())
        assert receipt["failure_stage"] == "preflight"
        assert receipt["cleanup"] == "pass"
    assert side_effects == []


def test_matrix_source_import_suspends_live_collection_and_restores_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str | None] = []
    sentinel = object()
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    real_import = message_api_restart_live.importlib.import_module

    def import_module(name: str):
        if name == "test.e2e.test_real_provider_matrix":
            observed.append(os.environ.get("CAO_RUN_LIVE_PROVIDER_TESTS"))
            return sentinel
        return real_import(name)

    monkeypatch.setattr(message_api_restart_live.importlib, "import_module", import_module)

    assert (
        message_api_restart_live._import_source_module("test.e2e.test_real_provider_matrix")
        is sentinel
    )
    assert observed == ["0"]
    assert os.environ["CAO_RUN_LIVE_PROVIDER_TESTS"] == "1"


def test_live_provider_auth_helpers_come_from_source_bound_cao_server() -> None:
    source_root = _source_root_from_test_file()
    modules = message_api_restart_live._source_modules(source_root)
    cao_server_module = modules[3]
    matrix_module = modules[5]

    assert cao_server_module.__name__ == "test.fixtures.cao_server"
    assert matrix_module.__name__ == "test.e2e.test_real_provider_matrix"
    assert cao_server_module._authorize_live_provider.__module__ == ("test.fixtures.cao_server")
    assert cao_server_module._stage_live_provider_auth.__module__ == ("test.fixtures.cao_server")
    assert not hasattr(matrix_module, "_authorize_live_provider")
    assert not hasattr(matrix_module, "_stage_live_provider_auth")

    resolver = getattr(message_api_restart_live, "_provider_auth_helpers", None)
    assert callable(resolver)
    authorize, stage = resolver(cao_server_module)
    assert authorize is cao_server_module._authorize_live_provider
    assert stage is cao_server_module._stage_live_provider_auth
    with pytest.raises(AcceptanceFailure, match="^preflight$"):
        resolver(matrix_module)


def test_live_entrypoint_routes_provider_auth_through_bound_server_fixture() -> None:
    source_path = Path(message_api_restart_live.__file__)
    module_tree = ast.parse(source_path.read_text(encoding="utf-8"))
    entrypoint = next(
        node
        for node in module_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_selected_message_acceptance"
    )
    resolver_calls = [
        node
        for node in ast.walk(entrypoint)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_provider_auth_helpers"
    ]

    assert len(resolver_calls) == 1
    assert len(resolver_calls[0].args) == 1
    assert isinstance(resolver_calls[0].args[0], ast.Name)
    assert resolver_calls[0].args[0].id == "server_module"
    assert any(
        isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Store)
        and node.id == "server_module"
        for node in ast.walk(entrypoint)
    )


def test_postgres_fingerprint_accepts_real_database_target_identity() -> None:
    source_root = _source_root_from_test_file()
    source_entry = str(source_root / "src")
    if source_entry not in sys.path:
        sys.path.insert(0, source_entry)
    postgres_config = importlib.import_module("cli_agent_orchestrator.clients.postgres.config")
    assert _same_source_file(
        Path(postgres_config.__file__),
        source_root / "src/cli_agent_orchestrator/clients/postgres/config.py",
    )
    target_type = postgres_config.DatabaseTarget

    def target(scope: str, database: str, store_id: str) -> object:
        return target_type(
            host="127.0.0.1",
            port=5432,
            database=database,
            user=f"cao_{scope}",
            schema=f"schema_{scope}",
            store_id=store_id,
            secret_file=Path("/private/never-opened"),
            sslmode="disable",
            scope=scope,
            migration_user=f"cao_{scope}_migration",
        )

    targets = types.SimpleNamespace(
        main=target("main", "db_main", "11111111-1111-4111-8111-111111111111"),
        auth=target("auth", "db_auth", "22222222-2222-4222-8222-222222222222"),
        peer=target("peer", "db_peer", "33333333-3333-4333-8333-333333333333"),
    )
    fingerprint = message_api_restart_live._target_fingerprint(
        targets, database_target_type=target_type
    )
    assert isinstance(fingerprint, str) and len(fingerprint) == 64
    assert all(
        private_value not in fingerprint
        for private_value in ("127.0.0.1", "db_main", "schema_main", "cao_main")
    )
    with pytest.raises(message_api_restart_live.AcceptanceFailure):
        message_api_restart_live._target_fingerprint(
            types.SimpleNamespace(
                main=types.SimpleNamespace(identity=targets.main.identity),
                auth=targets.auth,
                peer=targets.peer,
            ),
            database_target_type=target_type,
        )


def test_complete_seal_requires_a_private_positive_marker(tmp_path: Path) -> None:
    audit_path = tmp_path / "dispatch.jsonl"
    seal_path = audit_path.with_name(audit_path.name + ".complete")
    assert not message_api_restart.audit_completion_seal_valid(audit_path)

    seal_path.write_bytes(b"SPEC023_AUDIT_COMPLETE\n")
    seal_path.chmod(0o644)
    assert not message_api_restart.audit_completion_seal_valid(audit_path)

    seal_path.chmod(0o600)
    assert message_api_restart.audit_completion_seal_valid(audit_path)
    seal_path.write_bytes(b"SPEC023_AUDIT_COMPLETE\nextra")
    assert not message_api_restart.audit_completion_seal_valid(audit_path)


def test_source_bound_run_writes_complete_only_after_normal_return_and_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_path = tmp_path / "dispatch.jsonl"
    seal_path = audit_path.with_name(audit_path.name + ".complete")
    monkeypatch.setenv("CAO_SPEC023_MCP_OBSERVER", "0")
    monkeypatch.setenv("CAO_SPEC023_MCP_AUDIT_PATH", str(audit_path))
    sitecustomize_spec = importlib.util.spec_from_file_location(
        "spec023_test_sitecustomize", Path(__file__).with_name("sitecustomize.py")
    )
    assert sitecustomize_spec is not None and sitecustomize_spec.loader is not None
    sitecustomize = importlib.util.module_from_spec(sitecustomize_spec)
    sitecustomize_spec.loader.exec_module(sitecustomize)

    class Observer:
        audit_write_failed = False
        closed = False

        def close(self) -> None:
            self.closed = True

    observer = Observer()
    result = object()
    assert not seal_path.exists()
    assert sitecustomize._finish_source_bound_run(result, observer) is result
    assert observer.closed
    assert message_api_restart.audit_completion_seal_valid(audit_path)
    assert seal_path.stat().st_mode & 0o777 == 0o600


def test_server_exception_after_lost_rejection_cannot_pass_two_audit_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = _source_root_from_test_file()
    source_server = source_root / "src/cli_agent_orchestrator/mcp_server/server.py"
    audit_path = tmp_path / "dispatch.jsonl"
    complete_path = audit_path.with_name(audit_path.name + ".complete")
    writes = 0
    original_write = os.write

    def fail_rejection_write(fd: int, payload: bytes | memoryview) -> int:
        nonlocal writes
        writes += 1
        if writes == 1:
            raise OSError("private-test-write-failure")
        return original_write(fd, payload)

    monkeypatch.setattr(message_mcp_observer.os, "write", fail_rejection_write)
    expected_error = RuntimeError("private-test-server-failure")
    results = [object(), object()]
    calls = 0

    class FakeFastMCP:
        def run(self) -> object:
            with pytest.raises(PermissionError, match="^tool_not_allowed$"):
                asyncio.run(self.call_tool("other", {}))
            first = asyncio.run(self.call_tool("send_message", {"message": "one"}))
            second = asyncio.run(self.call_tool("send_message", {"message": "two"}))
            assert first is results[0]
            assert second is results[1]
            raise expected_error

        async def call_tool(self, name: str, arguments: dict[str, str]) -> object:
            nonlocal calls
            calls += 1
            return results[calls - 1]

    server = FakeFastMCP()
    fake_fastmcp = types.ModuleType("fastmcp")
    fake_fastmcp.FastMCP = FakeFastMCP
    fake_main = types.ModuleType("__main__")
    fake_main.__spec__ = types.SimpleNamespace(name="cli_agent_orchestrator.mcp_server.server")
    fake_main.__file__ = str(source_server)
    fake_main.mcp = server
    monkeypatch.setitem(sys.modules, "fastmcp", fake_fastmcp)
    monkeypatch.setitem(sys.modules, "__main__", fake_main)
    monkeypatch.setenv("CAO_SPEC023_MCP_OBSERVER", "1")
    monkeypatch.setenv("CAO_SPEC023_MCP_AUDIT_PATH", str(audit_path))
    monkeypatch.setenv(SOURCE_ROOT_ENV, str(source_root))
    sitecustomize_spec = importlib.util.spec_from_file_location(
        "spec023_exception_sitecustomize", Path(__file__).with_name("sitecustomize.py")
    )
    assert sitecustomize_spec is not None and sitecustomize_spec.loader is not None
    sitecustomize = importlib.util.module_from_spec(sitecustomize_spec)
    sitecustomize_spec.loader.exec_module(sitecustomize)

    with pytest.raises(RuntimeError) as raised:
        server.run()
    assert raised.value is expected_error
    assert writes == 3
    assert read_dispatch_audit(audit_path) == [
        {"tool": "send_message", "status": "completed", "outcome": "unknown"},
        {"tool": "send_message", "status": "completed", "outcome": "unknown"},
    ]
    assert not complete_path.exists()
    assert not message_api_restart.audit_completion_seal_valid(audit_path)


def test_provider_gates_are_default_off_and_selected_provider_setup_fails_closed() -> None:
    assert parse_provider_gates({}) == ()
    assert set(PROVIDER_GATE_ENV.values()) == {
        "CAO_RUN_MESSAGE_API_RESTART_CODEX",
        "CAO_RUN_MESSAGE_API_RESTART_CLAUDE_CODE",
        "CAO_RUN_MESSAGE_API_RESTART_OPENCODE",
    }
    assert parse_provider_gates({PROVIDER_GATE_ENV["codex"]: "1"}) == ("codex",)
    assert parse_provider_gates({PROVIDER_GATE_ENV["claude_code"]: "1"}) == ("claude_code",)
    assert parse_provider_gates({PROVIDER_GATE_ENV["opencode_cli"]: "1"}) == ("opencode_cli",)
    with pytest.raises(ValueError, match="invalid_provider_gate"):
        parse_provider_gates({PROVIDER_GATE_ENV["codex"]: "true"})
    with pytest.raises(ValueError, match="invalid_provider_gate"):
        parse_provider_gates({PROVIDER_GATE_ENV["codex"]: None})
    with pytest.raises(ValueError, match="one_provider_per_run"):
        parse_provider_gates(
            {PROVIDER_GATE_ENV["codex"]: "1", PROVIDER_GATE_ENV["claude_code"]: "1"}
        )
    with pytest.raises(RuntimeError, match="selected_provider_unavailable"):
        require_selected_provider_ready(
            "codex",
            {"binary": True, "auth": False, "source": True, "tools": True},
        )


def test_mcp_profile_config_pins_snapshot_python_and_observer_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAO_ENABLE_SENDER_ID_INJECTION", "false")
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(mode=0o700)
    audit_dir.chmod(0o700)
    source_root = tmp_path / "source"
    server_source = source_root / "src/cli_agent_orchestrator/mcp_server/server.py"
    server_source.parent.mkdir(parents=True)
    package_dir = source_root / "src/cli_agent_orchestrator"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text("\n", encoding="utf-8")
    (server_source.parent / "__init__.py").write_text("\n", encoding="utf-8")
    server_source.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(__file__).with_name('runtime-state.txt').write_text(\n"
        "    'true' if sys.dont_write_bytecode else 'false', encoding='ascii'\n"
        ")\n",
        encoding="utf-8",
    )
    candidate_test_dir = tmp_path / "candidate/test/live_provider"
    candidate_test_dir.mkdir(parents=True)
    (candidate_test_dir / "sitecustomize.py").write_text("# observer fixture\n", encoding="utf-8")
    config = build_message_mcp_server_config(
        provider="codex",
        python_executable=Path(sys.executable),
        source_root=source_root,
        candidate_test_dir=candidate_test_dir,
        audit_path=audit_dir / "sender.jsonl",
    )

    child_home = tmp_path / "child-home"
    child_home.mkdir(mode=0o700)
    child_environment = {
        "PATH": os.defpath,
        "HOME": str(child_home),
        "PYTHONNOUSERSITE": "1",
        **config["env"],
    }
    child_result = subprocess.run(
        [config["command"], *config["args"]],
        env=child_environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
        check=False,
        close_fds=True,
    )
    assert child_result.returncode == 0, "fake source-bound MCP import failed"
    runtime_state = (server_source.parent / "runtime-state.txt").read_text(encoding="ascii")
    generated_bytecode = tuple(source_root.rglob("*.pyc")) + tuple(
        candidate_test_dir.rglob("*.pyc")
    )
    assert runtime_state == "true" and not generated_bytecode, (
        "source-bound MCP child bytecode guard failed; "
        f"dont_write_bytecode={runtime_state}, pyc_count={len(generated_bytecode)}"
    )

    assert config["command"] == str(Path(sys.executable))
    assert config["args"] == ["-B", "-m", "cli_agent_orchestrator.mcp_server.server"]
    assert config["enabled_tools"] == ["send_message"]
    assert config["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert config["env"]["CAO_ENABLE_SENDER_ID_INJECTION"] == "true"
    assert config["env"]["FASTMCP_LOG_LEVEL"] == "WARNING"
    assert config["env"]["CAO_SPEC023_MCP_OBSERVER"] == "1"
    assert config["env"]["CAO_SPEC023_MCP_AUDIT_PATH"] == str(audit_dir / "sender.jsonl")
    assert config["env"]["PYTHONPATH"].split(os.pathsep) == [
        str(candidate_test_dir.resolve()),
        str(source_root.resolve() / "src"),
        str(source_root.resolve()),
    ]
    assert all("AUTH" not in name and "TOKEN" not in name for name in config["env"])
    python_alias = tmp_path / "pinned-python-link"
    python_alias.symlink_to(Path(sys.executable))
    alias_config = build_message_mcp_server_config(
        provider="codex",
        python_executable=python_alias,
        source_root=source_root,
        candidate_test_dir=candidate_test_dir,
        audit_path=audit_dir / "alias-sender.jsonl",
    )
    assert alias_config["command"] == str(python_alias)
    assert message_mcp_config_ready(
        "codex",
        config,
        python_executable=Path(sys.executable),
        source_root=source_root,
        candidate_test_dir=candidate_test_dir,
        audit_path=audit_dir / "sender.jsonl",
    )
    assert not message_mcp_config_ready(
        "codex",
        {
            **config,
            "env": {**config["env"], "ANTHROPIC_API_KEY": "SPEC023_AUTH_SENTINEL"},
        },
        python_executable=Path(sys.executable),
        source_root=source_root,
        candidate_test_dir=candidate_test_dir,
        audit_path=audit_dir / "sender.jsonl",
    )
    assert not message_mcp_config_ready(
        "codex",
        {**config, "args": ["-m", "cli_agent_orchestrator.mcp_server.server"]},
        python_executable=Path(sys.executable),
        source_root=source_root,
        candidate_test_dir=candidate_test_dir,
        audit_path=audit_dir / "sender.jsonl",
    )
    assert not message_mcp_config_ready(
        "codex",
        {
            **config,
            "env": {
                key: value
                for key, value in config["env"].items()
                if key != "PYTHONDONTWRITEBYTECODE"
            },
        },
        python_executable=Path(sys.executable),
        source_root=source_root,
        candidate_test_dir=candidate_test_dir,
        audit_path=audit_dir / "sender.jsonl",
    )
    assert not message_mcp_config_ready(
        "codex",
        {
            **config,
            "env": {
                **config["env"],
                "CAO_ENABLE_SENDER_ID_INJECTION": "false",
            },
        },
        python_executable=Path(sys.executable),
        source_root=source_root,
        candidate_test_dir=candidate_test_dir,
        audit_path=audit_dir / "sender.jsonl",
    )
    claude_config = build_message_mcp_server_config(
        provider="claude_code",
        python_executable=Path(sys.executable),
        source_root=source_root,
        candidate_test_dir=candidate_test_dir,
        audit_path=audit_dir / "claude-sender.jsonl",
    )
    assert claude_config["enabled_tools"] == ["send_message"]
    assert claude_config["cao_tool_profile"] == "collaboration"
    assert claude_config["args"] == ["-m", "cli_agent_orchestrator.mcp_server.server"]
    assert claude_config["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert message_mcp_config_ready(
        "claude_code",
        claude_config,
        python_executable=Path(sys.executable),
        source_root=source_root,
        candidate_test_dir=candidate_test_dir,
        audit_path=audit_dir / "claude-sender.jsonl",
    )
    opencode_config = build_message_mcp_server_config(
        provider="opencode_cli",
        python_executable=Path(sys.executable),
        source_root=source_root,
        candidate_test_dir=candidate_test_dir,
        audit_path=audit_dir / "opencode-sender.jsonl",
    )
    assert "enabled_tools" not in opencode_config
    assert message_mcp_config_ready(
        "opencode_cli",
        opencode_config,
        python_executable=Path(sys.executable),
        source_root=source_root,
        candidate_test_dir=candidate_test_dir,
        audit_path=audit_dir / "opencode-sender.jsonl",
    )
    assert phase_trigger("before_restart") == "SPEC023_SEND_BEFORE_RESTART"
    assert phase_trigger("after_restart") == "SPEC023_SEND_AFTER_RESTART"
    with pytest.raises(ValueError, match="invalid_phase"):
        phase_trigger("other")


def test_mcp_site_hook_requires_the_source_root_singleton(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    expected = source_root / "src/cli_agent_orchestrator/mcp_server/server.py"
    expected.parent.mkdir(parents=True)
    expected.write_text("# source fixture\n", encoding="utf-8")
    other = tmp_path / "installed/cli_agent_orchestrator/mcp_server/server.py"
    other.parent.mkdir(parents=True)
    other.write_text("# unrelated installed module\n", encoding="utf-8")
    server = object()

    assert is_source_bound_mcp_target(
        module_spec_name="cli_agent_orchestrator.mcp_server.server",
        module_server=server,
        server=server,
        module_file=expected,
        source_root=source_root,
    )
    assert not is_source_bound_mcp_target(
        module_spec_name="cli_agent_orchestrator.mcp_server.server",
        module_server=server,
        server=server,
        module_file=other,
        source_root=source_root,
    )
    assert not is_source_bound_mcp_target(
        module_spec_name="wrong.module",
        module_server=server,
        server=server,
        module_file=expected,
        source_root=source_root,
    )
    assert not is_source_bound_mcp_target(
        module_spec_name="cli_agent_orchestrator.mcp_server.server",
        module_server=object(),
        server=server,
        module_file=expected,
        source_root=source_root,
    )


def test_api_replacement_preserves_configuration_and_both_original_native_terminals() -> None:
    api_a = {
        "source_root": "/fixed/source",
        "home": "/private/home",
        "port": 23145,
        "database_identity": "isolated-target-set-a",
        "process_id": 120,
        "healthy": True,
    }
    api_b = {**api_a, "process_id": 121}
    terminals_a = {"sender-a": "idle", "receiver-b": "completed"}
    terminals_b = {"sender-a": "idle", "receiver-b": "completed"}

    assert validate_restart_handoff(api_a, api_b, terminals_a, terminals_b)
    assert not validate_restart_handoff(
        api_a,
        {**api_b, "database_identity": "different-target-set"},
        terminals_a,
        terminals_b,
    )
    assert not validate_restart_handoff(api_a, api_b, terminals_a, {"sender-a": "idle"})
    assert not validate_restart_handoff(
        api_a, api_b, terminals_a, {"sender-a": "idle", "receiver-b": "missing"}
    )


def test_agui_event_seen_before_delivery_is_retained_for_phase_acceptance() -> None:
    phase_events: list[bool] = []

    assert _accumulate_event_evidence(phase_events, [True])
    assert _accumulate_event_evidence(phase_events, [])
    assert phase_events == [True]
    assert not _accumulate_event_evidence(phase_events, [True])


def test_agui_capture_treats_close_interruption_as_expected_shutdown(monkeypatch) -> None:
    blocked = threading.Event()
    released = threading.Event()
    close_called = threading.Event()
    unhandled: list[str] = []

    class ClosingResponse:
        status_code = 200

        def iter_lines(self, *, decode_unicode: bool):
            assert decode_unicode is True
            yield ": connected"
            blocked.set()
            if not released.wait(timeout=5.0):
                raise AssertionError("response_close_did_not_release_stream")
            raise AttributeError("'NoneType' object has no attribute 'read'")

        def close(self) -> None:
            close_called.set()
            released.set()

    response = ClosingResponse()
    monkeypatch.setattr(message_api_restart_live.requests, "get", lambda *_a, **_k: response)
    monkeypatch.setattr(
        threading,
        "excepthook",
        lambda args: unhandled.append(type(args.exc_value).__name__),
    )
    capture = AguiCapture("http://127.0.0.1:1", "sender", "receiver")

    capture.start(timeout=1.0)
    assert blocked.wait(timeout=1.0)
    capture.stop()

    assert close_called.is_set()
    assert released.is_set()
    assert not capture._thread.is_alive()
    assert not capture._failed
    assert unhandled == []


def test_agui_capture_keeps_unexpected_attribute_error_as_stream_failure(monkeypatch) -> None:
    unhandled: list[str] = []

    class BrokenResponse:
        status_code = 200

        def iter_lines(self, *, decode_unicode: bool):
            assert decode_unicode is True
            raise AttributeError("unexpected stream failure")
            yield ""

        def close(self) -> None:
            pass

    response = BrokenResponse()
    monkeypatch.setattr(message_api_restart_live.requests, "get", lambda *_a, **_k: response)
    monkeypatch.setattr(
        threading,
        "excepthook",
        lambda args: unhandled.append(type(args.exc_value).__name__),
    )
    capture = AguiCapture("http://127.0.0.1:1", "sender", "receiver")

    capture._read()

    assert capture._ready.is_set()
    assert capture._failed
    assert unhandled == []


def test_agui_capture_keeps_non_iterator_attribute_error_after_stop_as_failure(
    monkeypatch,
) -> None:
    class BrokenResponse:
        status_code = 200

        def iter_lines(self, *, decode_unicode: bool):
            assert decode_unicode is True
            raise AttributeError("unexpected setup failure")

        def close(self) -> None:
            pass

    response = BrokenResponse()
    monkeypatch.setattr(message_api_restart_live.requests, "get", lambda *_a, **_k: response)
    capture = AguiCapture("http://127.0.0.1:1", "sender", "receiver")
    capture._stop.set()

    capture._read()

    assert capture._failed


def test_safe_receipt_drops_body_auth_paths_and_raw_diagnostics() -> None:
    receipt = project_safe_receipt(
        {
            "provider": "codex",
            "result": "pass",
            "mcp_calls": [
                {"tool": "send_message", "status": "completed", "outcome": "success"},
                {"tool": "send_message", "status": "completed", "outcome": ERROR_SENTINEL},
            ],
            "body": EXPECTED_BODY,
            "body_sha256": "SPEC023_BODY_HASH_SENTINEL",
            "auth": AUTH_SENTINEL,
            "auth_path": "/private/auth-cache/SPEC023_PATH_SENTINEL",
            "provider_output": ERROR_SENTINEL,
            "exception": RuntimeError(ERROR_SENTINEL),
        }
    )
    serialized = json.dumps(receipt, sort_keys=True)

    assert receipt == {
        "provider": "codex",
        "result": "pass",
        "shutdown_mode": "not_observed",
        "native_automatic_shutdown_accepted": False,
        "mcp_calls": [
            {"tool": "send_message", "status": "completed", "outcome": "success"},
            {"tool": "send_message", "status": "completed", "outcome": "unknown"},
        ],
        "first_row_identity_matched": False,
        "events_redacted": False,
        "terminals_preserved": False,
        "row_count": 0,
        "event_count": 0,
        "api_replacements": 0,
        "cleanup": "not_attempted",
        "failure_stage": "none",
        "failure_point": "none",
        "failure_kind": "none",
        "failure_http_status": 0,
        "last_terminal_status": "unknown",
        "failure_elapsed_ms": 0,
        "failure_detail": "none",
    }
    assert all(
        marker not in serialized
        for marker in (
            EXPECTED_BODY,
            "SPEC023_BODY_HASH_SENTINEL",
            AUTH_SENTINEL,
            "SPEC023_PATH_SENTINEL",
            ERROR_SENTINEL,
        )
    )


def test_safe_receipt_allowlists_native_start_diagnostics_without_raw_output() -> None:
    receipt = project_safe_receipt(
        {
            "provider": "codex",
            "result": "failed",
            "failure_stage": "native_terminals",
            "failure_point": "receiver_create",
            "failure_kind": "http_status",
            "failure_http_status": 500,
            "last_terminal_status": ERROR_SENTINEL,
            "failure_elapsed_ms": 120_153,
            "provider_output": ERROR_SENTINEL,
            "response_body": AUTH_SENTINEL,
            "failure_detail": "provider_init_timeout",
        }
    )
    serialized = json.dumps(receipt, sort_keys=True)

    assert receipt["failure_stage"] == "native_terminals"
    assert receipt["failure_point"] == "receiver_create"
    assert receipt["failure_kind"] == "http_status"
    assert receipt["failure_http_status"] == 500
    assert receipt["last_terminal_status"] == "unknown"
    assert receipt["failure_elapsed_ms"] == 120_153
    assert receipt["failure_detail"] == "provider_init_timeout"
    assert AUTH_SENTINEL not in serialized
    assert ERROR_SENTINEL not in serialized


@pytest.mark.parametrize(
    ("status", "failure_kind"),
    [
        ("error", "provider_error"),
        ("waiting_quota", "quota_wait"),
        ("waiting_user_answer", "interaction_wait"),
        ("reconcile", "reconcile_required"),
        ("missing", "terminal_missing"),
    ],
)
def test_phase_status_diagnostics_are_fixed_codes(status: str, failure_kind: str) -> None:
    assert _phase_failure_kind(status) == failure_kind
    assert _phase_failure_kind("processing") is None
    assert _phase_failure_kind("unknown") is None


def test_phase_wait_classifies_a_settled_turn_without_tool_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A ready terminal with no evidence must fail fast with a fixed code.

    This keeps the live lane from spending its full 180-second phase budget on
    a provider/model that returned to its composer without invoking MCP.
    """
    state = RunState(
        provider="opencode_cli",
        run_root=tmp_path,
        workspace=tmp_path / "workspace",
        home=tmp_path / "home",
        source_root=tmp_path,
        audit_path=tmp_path / "audit.jsonl",
    )
    state.sender_id = "a" * 8
    state.receiver_id = "b" * 8
    state.active_server = types.SimpleNamespace(url="http://phase.test")
    state.expected_turn_sequence = 1
    clock = [100.0]

    monkeypatch.setattr(message_api_restart_live.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        message_api_restart_live.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    monkeypatch.setattr(message_api_restart_live, "_audit_failure_present", lambda _state: False)
    monkeypatch.setattr(message_api_restart_live, "read_dispatch_audit", lambda _path: [])
    monkeypatch.setattr(message_api_restart_live, "_get_rows", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        message_api_restart_live,
        "_terminal_turn_observation",
        lambda *args, **kwargs: message_api_restart_live._PhaseTurnObservation(
            "idle", 1, 1, "c" * 32, True
        ),
    )

    class _Capture:
        def take_until(self, _deadline: float) -> list[bool]:
            return []

    with pytest.raises(AcceptanceFailure, match="^phase_a$"):
        _await_phase(
            state,
            capture=_Capture(),
            expected_bodies=(PRE_RESTART_MESSAGE,),
            phase_body=PRE_RESTART_MESSAGE,
            expected_call_count=1,
            phase="before_restart",
        )

    assert state.failure_stage == "none"
    assert state.failure_point == "phase_wait"
    assert state.failure_kind == "no_tool_call"
    assert state.last_terminal_status == "idle"


def test_native_session_failure_response_is_reduced_to_fixed_safe_codes() -> None:
    assert (
        classify_native_create_error_body(
            {"detail": "Failed to create session: Codex initialization timed out after 120 seconds"}
        )
        == "provider_init_timeout"
    )
    assert (
        classify_native_create_error_body(
            {
                "detail": "Failed to create terminal: Codex initialization timed out after 120 seconds"
            }
        )
        == "provider_init_timeout"
    )
    assert (
        classify_native_create_error_body(
            {"detail": "Failed to create session: Shell initialization timed out after 120s"}
        )
        == "shell_timeout"
    )
    assert (
        classify_native_create_error_body(
            {"detail": "Failed to create terminal: Shell initialization timed out after 120s"}
        )
        == "shell_timeout"
    )
    raw_detail = f"Failed to create session: {AUTH_SENTINEL}"
    classified = classify_native_create_error_body({"detail": raw_detail})
    receipt = project_safe_receipt(
        {
            "provider": "codex",
            "result": "failed",
            "failure_detail": classified,
            "response_body": {"detail": raw_detail},
        }
    )
    assert classified == "unclassified"
    assert receipt["failure_detail"] == "unclassified"
    assert AUTH_SENTINEL not in json.dumps(receipt, sort_keys=True)


def test_native_create_http_response_diagnostic_handles_bad_json_without_retaining_body() -> None:
    class FakeResponse:
        def __init__(self, body: object = None, error: Exception | None = None) -> None:
            self.body = body
            self.error = error

        def json(self) -> object:
            if self.error is not None:
                raise self.error
            return self.body

    good = FakeResponse(
        {"detail": "Failed to create terminal: OpenCode CLI initialization timed out after 120s"}
    )
    malformed = FakeResponse(error=ValueError(AUTH_SENTINEL))
    unknown_shape = FakeResponse({"detail": [AUTH_SENTINEL]})

    assert classify_native_create_error_response(good) == "provider_init_timeout"
    assert classify_native_create_error_response(malformed) == "unclassified"
    assert classify_native_create_error_response(unknown_shape) == "unclassified"
    assert classify_native_create_error_response(None) == "unclassified"

    receipt = project_safe_receipt(
        {
            "provider": "codex",
            "result": "failed",
            "failure_detail": classify_native_create_error_response(malformed),
            "response_body": AUTH_SENTINEL,
        }
    )
    assert AUTH_SENTINEL not in json.dumps(receipt, sort_keys=True)


def test_dispatch_audit_requires_exactly_two_real_allowed_successes_and_no_extra_fields(
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "observer.jsonl"
    audit_path.write_text(
        '{"tool":"send_message","status":"completed","outcome":"success"}\n'
        '{"tool":"send_message","status":"completed","outcome":"success"}\n',
        encoding="utf-8",
    )
    audit_path.chmod(0o600)

    records = read_dispatch_audit(audit_path)
    assert validate_dispatch_history(records, expected_calls=2)
    assert not validate_dispatch_history(records[:1], expected_calls=2)
    assert not validate_dispatch_history(
        [{"tool": "other", "status": "rejected", "outcome": "unknown"}, *records[1:]],
        expected_calls=2,
    )
    assert not validate_dispatch_history(
        [{"tool": "send_message", "status": "failed", "outcome": "unknown"}, *records[1:]],
        expected_calls=2,
    )
    assert not validate_dispatch_history(
        [{**records[0], "outcome": "failure"}, *records[1:]], expected_calls=2
    )
    assert not validate_dispatch_history(
        [{**records[0], "outcome": "unknown"}, *records[1:]], expected_calls=2
    )
    assert not validate_dispatch_history(
        [{**records[0], "body": EXPECTED_BODY}, *records[1:]], expected_calls=2
    )


def test_audit_reader_rejects_symlink_and_non_private_modes(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        '{"tool":"send_message","status":"completed","outcome":"success"}\n',
        encoding="utf-8",
    )
    audit.chmod(0o644)
    with pytest.raises(RuntimeError, match="audit_file_not_private"):
        read_dispatch_audit(audit)

    private = tmp_path / "private.jsonl"
    private.write_text(
        '{"tool":"send_message","status":"completed","outcome":"success"}\n',
        encoding="utf-8",
    )
    private.chmod(0o600)
    link = tmp_path / "link.jsonl"
    link.symlink_to(private)
    with pytest.raises(RuntimeError, match="audit_file_not_private"):
        read_dispatch_audit(link)


def test_cleanup_deletes_only_the_two_owned_native_terminal_ids() -> None:
    deleted: list[str] = []

    class Response:
        status_code = 200

    def delete(url: str) -> Response:
        deleted.append(url.rsplit("/", 1)[-1])
        return Response()

    delete_exact_terminals(
        "http://127.0.0.1:23331",
        ("a1b2c3d4", "e5f60718"),
        request_fn=delete,
    )
    assert deleted == ["a1b2c3d4", "e5f60718"]

    deleted.clear()
    with pytest.raises(RuntimeError, match="cleanup_failed"):
        delete_exact_terminals(
            "http://127.0.0.1:23331",
            ("a1b2c3d4", "a1b2c3d4"),
            request_fn=delete,
        )
    assert deleted == []

    class FailedResponse:
        status_code = 409

    with pytest.raises(RuntimeError, match="cleanup_failed"):
        delete_exact_terminals(
            "http://127.0.0.1:23331",
            ("a1b2c3d4",),
            request_fn=lambda _url: FailedResponse(),
        )


def test_audit_write_failure_marker_catches_missing_rejection_plus_two_successes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    audit_path = private / "dispatch.jsonl"
    marker_path = audit_path.with_name(audit_path.name + ".failed")
    writes = 0
    original_write = os.write

    def fail_first_write(fd: int, payload: bytes | memoryview) -> int:
        nonlocal writes
        writes += 1
        if writes == 1:
            raise OSError("private-test-write-failure")
        return original_write(fd, payload)

    monkeypatch.setattr(message_mcp_observer.os, "write", fail_first_write)
    results = [object(), object()]
    calls = 0

    async def original(name: str, arguments: dict[str, str]) -> object:
        nonlocal calls
        calls += 1
        return results[calls - 1]

    class Server:
        pass

    server = Server()
    server.call_tool = original
    handle = message_mcp_observer.install_message_observer(server, audit_path)
    with pytest.raises(PermissionError, match="^tool_not_allowed$"):
        asyncio.run(server.call_tool("unapproved", {"message": EXPECTED_BODY}))
    first_result = asyncio.run(server.call_tool("send_message", {"message": EXPECTED_BODY}))
    second_result = asyncio.run(server.call_tool("send_message", {"message": EXPECTED_BODY}))
    assert first_result is results[0]
    assert second_result is results[1]
    assert read_dispatch_audit(audit_path) == [
        {"tool": "send_message", "status": "completed", "outcome": "unknown"},
        {"tool": "send_message", "status": "completed", "outcome": "unknown"},
    ]

    monkeypatch.setenv("CAO_SPEC023_MCP_OBSERVER", "0")
    sitecustomize_spec = importlib.util.spec_from_file_location(
        "spec023_test_sitecustomize", Path(__file__).with_name("sitecustomize.py")
    )
    assert sitecustomize_spec is not None and sitecustomize_spec.loader is not None
    sitecustomize = importlib.util.module_from_spec(sitecustomize_spec)
    sitecustomize_spec.loader.exec_module(sitecustomize)
    monkeypatch.setenv("CAO_SPEC023_MCP_AUDIT_PATH", str(audit_path))
    with pytest.raises(SystemExit) as raised:
        sitecustomize._finish_source_bound_run(object(), handle)
    assert raised.value.code == 86
    assert handle.audit_write_failed
    assert marker_path.read_bytes() == b"SPEC023_AUDIT_WRITE_FAILED\n"
    assert marker_path.stat().st_mode & 0o777 == 0o600
    state = RunState(
        provider="codex",
        run_root=tmp_path,
        workspace=private,
        home=private,
        source_root=private,
        audit_path=audit_path,
    )
    assert _audit_failure_present(state)


def test_cleanup_transport_failure_still_stops_server_and_exits_targets_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class ActiveServer:
        url = "http://127.0.0.1:24567"

        def stop(self) -> None:
            calls.append("stop")

    class TargetContext:
        def __exit__(self, exc_type, exc_value, traceback) -> None:
            calls.append("exit")

    class ExitedProcess:
        def poll(self) -> int:
            return 0

    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    server = ActiveServer()
    state = RunState(
        provider="codex",
        run_root=tmp_path,
        workspace=workspace,
        home=tmp_path / "home",
        source_root=tmp_path / "source",
        audit_path=tmp_path / "dispatch.jsonl",
    )
    state.active_server = server
    state.servers.append(server)
    state.targets = object()
    state.target_context = TargetContext()
    state.sender_id = "a1b2c3d4"
    state.receiver_id = "e5f60718"
    state.failure_stage = "phase_b"
    deleted: list[str] = []

    def transport_failure(_url: str, terminal_id: str) -> bool:
        deleted.append(terminal_id)
        return False

    monkeypatch.setattr("message_api_restart_live._delete_one", transport_failure)
    monkeypatch.setattr("message_api_restart_live._api_process", lambda _server: ExitedProcess())
    assert not _cleanup(state, server_module=object())
    _record_failure_stage(state, state.failure_stage)

    assert deleted == ["a1b2c3d4", "e5f60718"]
    assert calls == ["stop", "exit"]
    assert state.target_context is None
    assert state.cleanup == "failed"
    assert state.failure_stage == "phase_b"
    assert workspace.is_dir()
    safe_receipt = project_safe_receipt(
        {
            "provider": "codex",
            "result": "failed",
            "cleanup": state.cleanup,
            "failure_stage": state.failure_stage,
        }
    )
    assert safe_receipt["failure_stage"] == "phase_b"


def test_api_process_that_survives_stop_preserves_database_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class LiveProcess:
        pid = 4242

        def poll(self) -> None:
            return None

        def wait(self, timeout: float) -> None:
            return None

    class ActiveServer:
        url = "http://127.0.0.1:24568"

        def stop(self) -> None:
            return None

    class TargetContext:
        exits = 0

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            self.exits += 1

    process = LiveProcess()
    server = ActiveServer()
    context = TargetContext()
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    state = RunState(
        provider="codex",
        run_root=tmp_path,
        workspace=workspace,
        home=tmp_path / "home",
        source_root=tmp_path / "source",
        audit_path=tmp_path / "dispatch.jsonl",
    )
    state.servers.append(server)
    state.active_server = server
    state.target_context = context
    monkeypatch.setattr("message_api_restart_live._api_process", lambda _server: process)
    monkeypatch.setattr("message_api_restart_live._api_request", lambda *_a, **_k: (599, None))
    monkeypatch.setattr("message_api_restart_live.os.getpgid", lambda pid: pid)
    monkeypatch.setattr("message_api_restart_live.os.killpg", lambda *_args: None)

    assert not _cleanup(state, server_module=object())
    assert context.exits == 0
    assert state.target_context is None
    assert state.cleanup == "failed"
    assert workspace.is_dir()
    assert message_api_restart_live._PRESERVED_TARGET_CONTEXTS[-1] is context
    message_api_restart_live._PRESERVED_TARGET_CONTEXTS.remove(context)


def test_unproven_private_tmux_exit_preserves_targets_and_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ExitedProcess:
        def poll(self) -> int:
            return 0

    class ActiveServer:
        url = "http://127.0.0.1:24569"

        def stop(self) -> None:
            return None

    class TargetContext:
        exits = 0

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            self.exits += 1

    class UnknownTmux:
        _SERVER_ABSENT = "absent"
        _SERVER_ALIVE = "alive"
        _SERVER_UNKNOWN = "unknown"

        @staticmethod
        def _tmux_server_liveness(_socket: str) -> str:
            return "unknown"

    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    home = workspace / "home"
    home.mkdir(mode=0o700)
    tmux_root = _private_tmux_test_root()
    state = RunState(
        provider="codex",
        run_root=tmp_path,
        workspace=workspace,
        home=home,
        source_root=tmp_path,
        audit_path=tmp_path / "dispatch.jsonl",
        tmux_tmpdir=tmux_root,
    )
    server = ActiveServer()
    context = TargetContext()
    state.servers.append(server)
    state.active_server = server
    state.target_context = context
    monkeypatch.setattr("message_api_restart_live._api_process", lambda _server: ExitedProcess())

    assert not _cleanup(state, server_module=object(), tmux_module=UnknownTmux)
    assert context.exits == 0
    assert state.target_context is None
    assert state.cleanup == "failed"
    assert workspace.is_dir()
    assert tmux_root.is_dir()
    assert message_api_restart_live._PRESERVED_TARGET_CONTEXTS[-1] is context
    message_api_restart_live._PRESERVED_TARGET_CONTEXTS.remove(context)
    shutil.rmtree(tmux_root)


def test_api_request_and_inbox_read_preserve_the_current_failure_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_request(*_args: object, **_kwargs: object) -> object:
        raise message_api_restart_live.requests.ConnectionError("private-test-transport-failure")

    monkeypatch.setattr(message_api_restart_live.requests, "request", failed_request)
    with pytest.raises(message_api_restart_live.AcceptanceFailure) as request_error:
        message_api_restart_live._api_request(
            "GET", "http://127.0.0.1/private", failure_stage="phase_b"
        )
    assert request_error.value.stage == "phase_b"

    monkeypatch.setattr(
        message_api_restart_live,
        "_api_request",
        lambda *_args, **_kwargs: (503, None),
    )
    with pytest.raises(message_api_restart_live.AcceptanceFailure) as read_error:
        message_api_restart_live._get_rows(
            "http://127.0.0.1", "a1b2c3d4", failure_stage="api_b_verify"
        )
    assert read_error.value.stage == "api_b_verify"


def test_server_a_and_b_receive_the_same_private_tmux_environment(
    tmp_path: Path,
) -> None:
    captured: list[dict[str, str]] = []
    root = tmp_path / "s023-private"
    root.mkdir(mode=0o700)
    home = tmp_path / "home"
    home.mkdir(mode=0o700)

    class ServerModule:
        @staticmethod
        def _start_cao_server(
            _home: Path, _port: int, *, extra_env: dict[str, str], **_kwargs: object
        ) -> object:
            captured.append(dict(extra_env))
            return types.SimpleNamespace(stop=lambda: None)

    state = RunState(
        provider="codex",
        run_root=tmp_path,
        workspace=tmp_path,
        home=home,
        source_root=tmp_path,
        audit_path=tmp_path / "dispatch.jsonl",
        tmux_tmpdir=root,
    )
    message_api_restart_live._start_server(state, ServerModule, 23101)
    message_api_restart_live._start_server(state, ServerModule, 23101)

    assert len(captured) == 2
    assert captured[0] == captured[1]
    assert captured[0]["TMUX"] == ""
    assert captured[0]["TMUX_TMPDIR"] == str(root)
    assert captured[0]["TMPDIR"] == str(root)
    assert captured[0]["CAO_ENABLE_SENDER_ID_INJECTION"] == "true"


def test_created_private_tmux_root_is_short_and_owner_only() -> None:
    root = message_api_restart_live._create_private_tmux_tmpdir()
    try:
        socket_path = root / f"tmux-{os.getuid()}" / "default"
        assert root.parent == Path("/tmp")
        assert root.stat().st_mode & 0o777 == 0o700
        assert len(os.fsencode(socket_path)) < 100
    finally:
        shutil.rmtree(root)


def test_private_tmux_root_is_removed_only_after_owned_empty_server_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket as socket_module
    import subprocess

    root = _private_tmux_test_root()
    socket_dir = root / f"tmux-{os.getuid()}"
    socket_dir.mkdir(mode=0o700, parents=True)
    root.chmod(0o700)
    socket_dir.chmod(0o700)
    socket_path = socket_dir / "default"
    unix_socket = socket_module.socket(socket_module.AF_UNIX)
    unix_socket.bind(str(socket_path))
    unix_socket.close()
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    alive = True
    exited = False
    commands: list[tuple[str, ...]] = []

    class TmuxModule:
        _SERVER_ALIVE = "alive"
        _SERVER_ABSENT = "absent"
        _SERVER_UNKNOWN = "unknown"

        @staticmethod
        def _tmux_server_liveness(_socket: str) -> str:
            return "alive" if alive else "absent"

    def runner(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal alive, exited
        command = tuple(args[3:])
        commands.append(command)
        if command == ("display-message", "-p", "#{pid}"):
            return subprocess.CompletedProcess(args, 0, "4243\n", "")
        if command == ("list-sessions", "-F", "#{session_id}"):
            return subprocess.CompletedProcess(args, 0, "", "")
        if command == ("kill-server",):
            alive = False
            exited = True
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 1, "", "")

    monkeypatch.setattr(
        "message_api_restart_live.owned_tmux.shutil.which", lambda _name: "/usr/bin/tmux"
    )
    monkeypatch.setattr(
        "message_api_restart_live.owned_tmux._read_process_identity",
        lambda pid: (os.geteuid(), "12345") if pid == 4243 else None,
    )
    monkeypatch.setattr(
        "message_api_restart_live.owned_tmux.os.pidfd_open",
        lambda _pid, _flags: os.open(os.devnull, os.O_RDONLY),
    )
    monkeypatch.setattr("message_api_restart_live.owned_tmux._pidfd_exited", lambda _pidfd: exited)
    state = RunState(
        provider="codex",
        run_root=tmp_path,
        workspace=tmp_path,
        home=home,
        source_root=tmp_path,
        audit_path=tmp_path / "dispatch.jsonl",
        tmux_tmpdir=root,
    )
    state.sender_id = "a1b2c3d4"
    state.receiver_id = "e5f60718"
    state.deleted_ids.update({state.sender_id, state.receiver_id})

    assert message_api_restart_live._cleanup_private_tmux_root(
        state, TmuxModule, command_runner=runner
    )
    assert commands == [
        ("display-message", "-p", "#{pid}"),
        ("list-sessions", "-F", "#{session_id}"),
        ("kill-server",),
    ]
    assert exited
    assert not root.exists()
    assert not message_api_restart_live._cleanup_private_tmux_root(
        state, TmuxModule, command_runner=runner
    )


def test_private_tmux_cleanup_fails_closed_for_sessions_or_live_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket as socket_module
    import subprocess

    root = _private_tmux_test_root()
    socket_dir = root / f"tmux-{os.getuid()}"
    socket_dir.mkdir(mode=0o700, parents=True)
    root.chmod(0o700)
    socket_dir.chmod(0o700)
    socket_path = socket_dir / "default"
    unix_socket = socket_module.socket(socket_module.AF_UNIX)
    unix_socket.bind(str(socket_path))
    unix_socket.close()
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    calls: list[tuple[str, ...]] = []

    class TmuxModule:
        _SERVER_ALIVE = "alive"
        _SERVER_ABSENT = "absent"
        _SERVER_UNKNOWN = "unknown"

        @staticmethod
        def _tmux_server_liveness(_socket: str) -> str:
            return "alive"

    def runner(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        command = tuple(args[3:])
        calls.append(command)
        if command == ("display-message", "-p", "#{pid}"):
            return subprocess.CompletedProcess(args, 0, "4243\n", "")
        return subprocess.CompletedProcess(args, 0, "session-0\n", "")

    monkeypatch.setattr(
        "message_api_restart_live.owned_tmux.shutil.which", lambda _name: "/usr/bin/tmux"
    )
    monkeypatch.setattr(
        "message_api_restart_live.owned_tmux._read_process_identity",
        lambda _pid: (os.geteuid(), "12345"),
    )
    monkeypatch.setattr(
        "message_api_restart_live.owned_tmux.os.pidfd_open",
        lambda _pid, _flags: os.open(os.devnull, os.O_RDONLY),
    )
    monkeypatch.setattr("message_api_restart_live.owned_tmux._pidfd_exited", lambda _fd: False)
    state = RunState(
        provider="codex",
        run_root=tmp_path,
        workspace=tmp_path,
        home=home,
        source_root=tmp_path,
        audit_path=tmp_path / "dispatch.jsonl",
        tmux_tmpdir=root,
    )
    state.sender_id = "a1b2c3d4"
    state.deleted_ids.add(state.sender_id)

    assert not message_api_restart_live._cleanup_private_tmux_root(
        state,
        TmuxModule,
        command_runner=runner,
        api_processes_exited=False,
    )
    assert calls == []
    assert not message_api_restart_live._cleanup_private_tmux_root(
        state, TmuxModule, command_runner=runner
    )
    assert calls == [
        ("display-message", "-p", "#{pid}"),
        ("list-sessions", "-F", "#{session_id}"),
    ]
    assert root.exists()
    shutil.rmtree(root)


def test_private_tmux_cleanup_rejects_pid_reuse_before_listing_or_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket as socket_module
    import subprocess

    root = _private_tmux_test_root()
    socket_dir = root / f"tmux-{os.getuid()}"
    socket_dir.mkdir(mode=0o700, parents=True)
    socket_dir.chmod(0o700)
    socket_path = socket_dir / "default"
    unix_socket = socket_module.socket(socket_module.AF_UNIX)
    unix_socket.bind(str(socket_path))
    unix_socket.close()
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    commands: list[tuple[str, ...]] = []
    identities = iter(((os.geteuid(), "12345"), (os.geteuid(), "54321")))

    class TmuxModule:
        _SERVER_ALIVE = "alive"
        _SERVER_ABSENT = "absent"
        _SERVER_UNKNOWN = "unknown"

        @staticmethod
        def _tmux_server_liveness(_socket: str) -> str:
            return "alive"

    def runner(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        command = tuple(args[3:])
        commands.append(command)
        return subprocess.CompletedProcess(args, 0, "4243\n", "")

    monkeypatch.setattr(
        "message_api_restart_live.owned_tmux.shutil.which", lambda _name: "/usr/bin/tmux"
    )
    monkeypatch.setattr(
        "message_api_restart_live.owned_tmux._read_process_identity",
        lambda _pid: next(identities),
    )
    monkeypatch.setattr(
        "message_api_restart_live.owned_tmux.os.pidfd_open",
        lambda _pid, _flags: os.open(os.devnull, os.O_RDONLY),
    )
    state = RunState(
        provider="codex",
        run_root=tmp_path,
        workspace=tmp_path,
        home=home,
        source_root=tmp_path,
        audit_path=tmp_path / "dispatch.jsonl",
        tmux_tmpdir=root,
    )
    state.sender_id = "a1b2c3d4"
    state.deleted_ids.add(state.sender_id)

    assert not message_api_restart_live._cleanup_private_tmux_root(
        state, TmuxModule, command_runner=runner
    )
    assert commands == [("display-message", "-p", "#{pid}")]
    assert root.exists()
    shutil.rmtree(root)


def test_private_tmux_cleanup_rejects_socket_replacement_before_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket as socket_module
    import subprocess

    root = _private_tmux_test_root()
    socket_dir = root / f"tmux-{os.getuid()}"
    socket_dir.mkdir(mode=0o700, parents=True)
    socket_dir.chmod(0o700)
    socket_path = socket_dir / "default"
    unix_socket = socket_module.socket(socket_module.AF_UNIX)
    unix_socket.bind(str(socket_path))
    unix_socket.close()
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    calls: list[tuple[str, ...]] = []

    class TmuxModule:
        _SERVER_ALIVE = "alive"
        _SERVER_ABSENT = "absent"
        _SERVER_UNKNOWN = "unknown"

        @staticmethod
        def _tmux_server_liveness(_socket: str) -> str:
            return "alive"

    def runner(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        command = tuple(args[3:])
        calls.append(command)
        if command == ("display-message", "-p", "#{pid}"):
            return subprocess.CompletedProcess(args, 0, "4243\n", "")
        if command == ("list-sessions", "-F", "#{session_id}"):
            socket_path.unlink()
            replacement = socket_module.socket(socket_module.AF_UNIX)
            replacement.bind(str(socket_path))
            replacement.close()
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(
        "message_api_restart_live.owned_tmux.shutil.which", lambda _name: "/usr/bin/tmux"
    )
    monkeypatch.setattr(
        "message_api_restart_live.owned_tmux._read_process_identity",
        lambda _pid: (os.geteuid(), "12345"),
    )
    monkeypatch.setattr(
        "message_api_restart_live.owned_tmux.os.pidfd_open",
        lambda _pid, _flags: os.open(os.devnull, os.O_RDONLY),
    )
    monkeypatch.setattr("message_api_restart_live.owned_tmux._pidfd_exited", lambda _fd: False)
    state = RunState(
        provider="codex",
        run_root=tmp_path,
        workspace=tmp_path,
        home=home,
        source_root=tmp_path,
        audit_path=tmp_path / "dispatch.jsonl",
        tmux_tmpdir=root,
    )
    state.sender_id = "a1b2c3d4"
    state.deleted_ids.add(state.sender_id)

    assert not message_api_restart_live._cleanup_private_tmux_root(
        state, TmuxModule, command_runner=runner
    )
    assert calls == [
        ("display-message", "-p", "#{pid}"),
        ("list-sessions", "-F", "#{session_id}"),
    ]
    assert root.exists()
    shutil.rmtree(root)


@pytest.mark.parametrize("socket_kind", ("regular_file", "foreign_owner"))
def test_private_tmux_cleanup_preserves_untrusted_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, socket_kind: str
) -> None:
    import socket as socket_module

    root = _private_tmux_test_root()
    socket_dir = root / f"tmux-{os.getuid()}"
    socket_dir.mkdir(mode=0o700, parents=True)
    socket_dir.chmod(0o700)
    socket_path = socket_dir / "default"
    unix_socket = None
    if socket_kind == "regular_file":
        socket_path.write_text("untrusted socket path", encoding="utf-8")
    else:
        unix_socket = socket_module.socket(socket_module.AF_UNIX)
        unix_socket.bind(str(socket_path))

    home = tmp_path / "home"
    home.mkdir(mode=0o700)

    class TmuxModule:
        _SERVER_ALIVE = "alive"
        _SERVER_ABSENT = "absent"
        _SERVER_UNKNOWN = "unknown"

        @staticmethod
        def _tmux_server_liveness(_socket: str) -> str:
            return "alive"

    def unexpected_command(*_args: object, **_kwargs: object) -> object:
        pytest.fail("tmux command ran before socket ownership was established")

    if socket_kind == "foreign_owner":
        real_euid = os.geteuid()
        calls = 0

        def changing_euid() -> int:
            nonlocal calls
            calls += 1
            return real_euid if calls == 1 else real_euid + 1

        monkeypatch.setattr("message_api_restart_live.owned_tmux.os.geteuid", changing_euid)

    try:
        assert not message_api_restart_live.owned_tmux.cleanup_private_tmux_root(
            root,
            home,
            TmuxModule,
            owner_stopped=True,
            command_runner=unexpected_command,
        )
        assert root.exists()
    finally:
        if unix_socket is not None:
            unix_socket.close()
        shutil.rmtree(root)


def test_offline_tests_and_observer_have_no_process_or_network_start_path() -> None:
    forbidden_imports = {"httpx", "requests", "socket", "sqlalchemy", "subprocess"}
    forbidden_database_calls = {"create_engine", "create_async_engine"}
    for module in (message_api_restart, message_mcp_observer):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots = {alias.name.split(".", 1)[0] for alias in node.names}
                assert not imported_roots.intersection(forbidden_imports)
            elif isinstance(node, ast.ImportFrom):
                imported_root = (node.module or "").split(".", 1)[0]
                assert imported_root not in forbidden_imports
            elif isinstance(node, ast.Call):
                target = node.func
                names: list[str] = []
                while isinstance(target, ast.Attribute):
                    names.append(target.attr)
                    target = target.value
                if isinstance(target, ast.Name):
                    names.append(target.id)
                call_path = tuple(reversed(names))
                assert not call_path or call_path[0] not in forbidden_imports
                assert not call_path or call_path[-1] not in forbidden_database_calls


class _OfflineOpenCodeRuntimeReached(RuntimeError):
    pass


def _exercise_native_profile_order_offline(
    live_module: object,
    *,
    source_root: Path,
    case_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_name: str,
    install_status: int = 200,
) -> tuple[list[str], dict[str, object]]:
    """Run the real acceptance function with fake routes and the real profile gate."""
    from types import SimpleNamespace

    import requests

    from cli_agent_orchestrator.providers import opencode_cli
    from cli_agent_orchestrator.utils.opencode_config import to_opencode_agent_id

    evidence_root = case_root / "evidence"
    native_agents = case_root / "native-agents"
    evidence_root.mkdir(mode=0o700, parents=True)
    case_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    native_agents.mkdir(mode=0o700, parents=True, exist_ok=True)
    native_agents.chmod(0o700)
    sequence: list[str] = []
    profile_paths: dict[str, Path] = {}
    patched_live_names = (
        "_admit_codex_runtime",
        "_verify_selected_setup",
        "_hash_source_bindings",
        "_hash_candidate_bindings",
        "_create_private_tmux_tmpdir",
        "_pick_free_port",
        "_provider_auth_helpers",
        "_staged_auth_is_private",
        "build_message_mcp_server_config",
        "message_mcp_config_ready",
        "_target_fingerprint",
        "_start_server",
        "_api_process",
        "read_dispatch_audit",
        "_wait_ready",
        "_cleanup",
        "_write_profile",
        "_api_request",
    )
    original_live_values = {name: getattr(live_module, name) for name in patched_live_names}
    original_provider_values = {
        "OPENCODE_AGENTS_DIR": opencode_cli.OPENCODE_AGENTS_DIR,
        "resolve_opencode_runtime": opencode_cli.resolve_opencode_runtime,
        "detect_opencode_major": opencode_cli.detect_opencode_major,
    }
    original_post = requests.post
    original_which = live_module.shutil.which
    environment_keys = (
        *live_module.PROVIDER_GATE_ENV.values(),
        live_module.SOURCE_ROOT_ENV,
        live_module.EVIDENCE_ROOT_ENV,
    )
    original_environment = {key: os.environ.get(key) for key in environment_keys}

    class Response:
        def __init__(self, status_code: int, body: dict[str, str]):
            self.status_code = status_code
            self._body = body

        def json(self) -> dict[str, str]:
            return dict(self._body)

        def close(self) -> None:
            return None

    class TargetContext:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *_args: object) -> bool:
            return False

    class Request:
        def getfixturevalue(self, name: str) -> object:
            assert name == "postgres_service"
            return object()

    with monkeypatch.context() as patch:
        # These synthetic routes test profile-install ordering; complete runtime
        # admission and its before-credentials refusal are covered separately.
        patch.setattr(live_module, "_admit_codex_runtime", lambda *_args: None)
        patch.setattr(opencode_cli, "OPENCODE_AGENTS_DIR", native_agents)
        patch.setattr(
            opencode_cli,
            "resolve_opencode_runtime",
            lambda: (_ for _ in ()).throw(_OfflineOpenCodeRuntimeReached()),
        )
        patch.setattr(
            opencode_cli,
            "detect_opencode_major",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("CLI detection must not run")
            ),
        )
        for gate in live_module.PROVIDER_GATE_ENV.values():
            patch.delenv(gate, raising=False)
        patch.setenv(live_module.PROVIDER_GATE_ENV[provider_name], "1")
        patch.setenv(live_module.SOURCE_ROOT_ENV, str(source_root))
        patch.setenv(live_module.EVIDENCE_ROOT_ENV, str(evidence_root))

        source_files = {"offline": "source-pin"}
        candidate_files = {"offline": "candidate-pin"}
        constants = SimpleNamespace(SESSION_PREFIX="cao-")
        postgres_config = SimpleNamespace(DatabaseTarget=object)
        postgres = SimpleNamespace(isolated_live_targets=lambda *_args, **_kwargs: TargetContext())
        server_module = SimpleNamespace()
        patch.setattr(
            live_module,
            "_verify_selected_setup",
            lambda *_args, **_kwargs: (
                source_files,
                candidate_files,
                constants,
                SimpleNamespace(),
                postgres_config,
                server_module,
                postgres,
                SimpleNamespace(),
            ),
        )
        patch.setattr(live_module, "_hash_source_bindings", lambda *_args: source_files)
        patch.setattr(live_module, "_hash_candidate_bindings", lambda *_args: candidate_files)
        patch.setattr(live_module, "_create_private_tmux_tmpdir", lambda: case_root / "tmux")
        patch.setattr(live_module, "_pick_free_port", lambda: 41000)
        authorization = SimpleNamespace(binary="offline-cli", model="offline-model")
        patch.setattr(
            live_module,
            "_provider_auth_helpers",
            lambda *_args, **_kwargs: (
                lambda *_a, **_k: authorization,
                lambda *_a, **_k: None,
            ),
        )
        patch.setattr(live_module, "_staged_auth_is_private", lambda *_args: True)
        patch.setattr(live_module, "build_message_mcp_server_config", lambda **_k: {})
        patch.setattr(live_module, "message_mcp_config_ready", lambda *_a, **_k: True)
        patch.setattr(live_module.shutil, "which", lambda *_a, **_k: "/offline/fake")
        patch.setattr(live_module, "_target_fingerprint", lambda *_a, **_k: {"target": "offline"})
        server = SimpleNamespace(url="http://offline.invalid")
        patch.setattr(live_module, "_start_server", lambda *_a, **_k: server)
        patch.setattr(
            live_module,
            "_api_process",
            lambda *_a, **_k: SimpleNamespace(pid=9001, poll=lambda: None),
        )
        patch.setattr(live_module, "read_dispatch_audit", lambda *_a, **_k: [])

        ready_calls = 0

        def stop_at_native_boundary(*_args: object, **_kwargs: object) -> str:
            nonlocal ready_calls
            ready_calls += 1
            if provider_name == "opencode_cli" and ready_calls == 1:
                return "idle"
            raise live_module.AcceptanceFailure("native_terminals")

        patch.setattr(live_module, "_wait_ready", stop_at_native_boundary)

        def cleanup(state: object, **_kwargs: object) -> bool:
            state.cleanup = "pass"
            return True

        patch.setattr(live_module, "_cleanup", cleanup)
        original_write_profile = live_module._write_profile

        def tracked_write_profile(home: Path, text: str, name: str) -> Path:
            profile_path = original_write_profile(home, text, name)
            profile_paths[name] = profile_path
            return profile_path

        patch.setattr(live_module, "_write_profile", tracked_write_profile)

        def fake_api_request(
            method: str, url: str, **kwargs: object
        ) -> tuple[int, dict[str, object]]:
            assert provider_name == "opencode_cli"
            assert method == "POST" and url.endswith("/agents/profiles/install")
            name = kwargs["json"]["source"]
            sequence.append(
                "install_receiver" if name.startswith("spec023-receiver-") else "install_sender"
            )
            if install_status != 200:
                sequence.append("install_rejected")
                return install_status, {}
            native_path = native_agents / f"{to_opencode_agent_id(name)}.md"
            native_path.write_text(
                profile_paths[name].read_text(encoding="utf-8"), encoding="utf-8"
            )
            return 200, {}

        patch.setattr(live_module, "_api_request", fake_api_request)

        def initialize_native_profile(name: str) -> bool:
            adapter = opencode_cli.OpenCodeCliProvider(
                "00000001", "offline-session", "offline-window", agent_profile=name
            )
            try:
                asyncio.run(adapter.initialize())
            except ValueError:
                sequence.append("provider_missing_native_profile")
                return False
            except _OfflineOpenCodeRuntimeReached:
                sequence.append("provider_native_profile_validated")
                return True
            raise AssertionError("unexpected provider initialization result")

        def fake_post(url: str, *, params: dict[str, str], **_kwargs: object) -> Response:
            if url.endswith("/sessions"):
                sequence.append("create_receiver")
                if provider_name == "opencode_cli" and not initialize_native_profile(
                    params["agent_profile"]
                ):
                    return Response(500, {})
                return Response(
                    201,
                    {"id": "00000001", "session_name": params["session_name"]},
                )
            if "/terminals" in url:
                sequence.append("create_sender")
                if provider_name == "opencode_cli" and not initialize_native_profile(
                    params["agent_profile"]
                ):
                    return Response(500, {})
                return Response(201, {"id": "00000002"})
            raise AssertionError("unexpected fake route")

        patch.setattr(requests, "post", fake_post)
        receipt = live_module.run_selected_message_acceptance(Request())
        assert receipt["result"] == "failed"
        assert receipt["failure_stage"] == "native_terminals"

    assert {name: getattr(live_module, name) for name in patched_live_names} == original_live_values
    assert {
        "OPENCODE_AGENTS_DIR": opencode_cli.OPENCODE_AGENTS_DIR,
        "resolve_opencode_runtime": opencode_cli.resolve_opencode_runtime,
        "detect_opencode_major": opencode_cli.detect_opencode_major,
    } == original_provider_values
    assert requests.post is original_post
    assert live_module.shutil.which is original_which
    assert {key: os.environ.get(key) for key in environment_keys} == original_environment
    return sequence, receipt


def test_opencode_native_profiles_install_before_each_terminal_create(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_root = _source_root_from_test_file()
    provider_module = importlib.import_module("cli_agent_orchestrator.providers.opencode_cli")
    assert _same_source_file(
        Path(message_api_restart_live.__file__),
        Path(__file__).parent / "message_api_restart_live.py",
    )
    assert _same_source_file(
        Path(message_api_restart.__file__),
        source_root / "test/live_provider/message_api_restart.py",
    )
    assert _same_source_file(
        Path(provider_module.__file__),
        source_root / "src/cli_agent_orchestrator/providers/opencode_cli.py",
    )

    sequence, receipt = _exercise_native_profile_order_offline(
        message_api_restart_live,
        source_root=source_root,
        case_root=tmp_path / "success",
        monkeypatch=monkeypatch,
        provider_name="opencode_cli",
    )
    assert sequence == [
        "install_receiver",
        "create_receiver",
        "provider_native_profile_validated",
        "install_sender",
        "create_sender",
        "provider_native_profile_validated",
    ]
    assert receipt["failure_stage"] == "native_terminals"


def test_opencode_install_rejection_prevents_receiver_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_root = _source_root_from_test_file()
    sequence, receipt = _exercise_native_profile_order_offline(
        message_api_restart_live,
        source_root=source_root,
        case_root=tmp_path / "rejected",
        monkeypatch=monkeypatch,
        provider_name="opencode_cli",
        install_status=503,
    )
    assert sequence == ["install_receiver", "install_rejected"]
    assert receipt["failure_stage"] == "native_terminals"


def test_non_opencode_lane_does_not_install_native_profiles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_root = _source_root_from_test_file()
    sequence, receipt = _exercise_native_profile_order_offline(
        message_api_restart_live,
        source_root=source_root,
        case_root=tmp_path / "codex",
        monkeypatch=monkeypatch,
        provider_name="codex",
    )
    assert sequence == ["create_receiver"]
    assert receipt["failure_stage"] == "native_terminals"


@pytest.fixture
def claude_message_scope(tmp_path):
    root = _source_root_from_test_file()
    audit = tmp_path / "audit"
    audit.mkdir(mode=0o700)
    kwargs = dict(
        python_executable=Path(sys.executable),
        source_root=root,
        candidate_test_dir=root / "test/live_provider",
        audit_path=audit / "calls.jsonl",
    )
    return build_message_mcp_server_config(provider="claude_code", **kwargs), kwargs


@pytest.mark.parametrize(
    "field,value",
    [
        ("cao_tool_profile", None),
        ("cao_tool_profile", "coordination"),
        ("enabled_tools", None),
        ("enabled_tools", []),
        ("enabled_tools", ["list_siblings"]),
        ("enabled_tools", ["send_message", "send_message"]),
        ("enabled_tools", False),
    ],
)
def test_claude_message_config_requires_exact_profile_and_filter(
    claude_message_scope, field, value
):
    config, kwargs = claude_message_scope
    expected = {**config, "cao_tool_profile": "collaboration", "enabled_tools": ["send_message"]}
    assert message_mcp_config_ready("claude_code", expected, **kwargs)
    bad = dict(expected)
    if value is None:
        del bad[field]
    else:
        bad[field] = value
    assert not message_mcp_config_ready("claude_code", bad, **kwargs)


@pytest.mark.asyncio
async def test_claude_message_config_reaches_actual_builder_and_server_filter(
    claude_message_scope, monkeypatch, tmp_path
):
    import shlex

    from fastmcp import Client, FastMCP
    from fastmcp.exceptions import ToolError

    from cli_agent_orchestrator.mcp_server import server
    from cli_agent_orchestrator.models.agent_profile import AgentProfile
    from cli_agent_orchestrator.providers import claude_code
    from cli_agent_orchestrator.utils.mcp_tool_policy import (
        MCP_TOOL_FILTER_ENV,
        MCP_TOOL_PROFILE_ENV,
    )

    config, _ = claude_message_scope
    assert config.get("cao_tool_profile") == "collaboration"
    assert config.get("enabled_tools") == ["send_message"]
    profile = AgentProfile(
        name="message-scope",
        description="test",
        allowedTools=["@cao-mcp-server"],
        mcpServers={"cao-mcp-server": config},
    )
    with monkeypatch.context() as patch:
        patch.setattr(claude_code, "CAO_HOME_DIR", tmp_path)
        patch.setattr(claude_code, "load_agent_profile", lambda _: profile)
        provider = claude_code.ClaudeCodeProvider("test1234", "session", "window", "message-scope")
        args = shlex.split(provider._build_claude_command())
        entry = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())["mcpServers"][
            "cao-mcp-server"
        ]
        assert "cao_tool_profile" not in entry and "enabled_tools" not in entry
        assert entry["env"][MCP_TOOL_PROFILE_ENV] == "collaboration"
        assert json.loads(entry["env"][MCP_TOOL_FILTER_ENV]) == ["send_message"]
        patch.setenv(MCP_TOOL_PROFILE_ENV, entry["env"][MCP_TOOL_PROFILE_ENV])
        patch.setenv(MCP_TOOL_FILTER_ENV, entry["env"][MCP_TOOL_FILTER_ENV])
        app = FastMCP("message-scope")
        invoked = []

        @app.tool()
        async def send_message() -> str:
            invoked.append("send_message")
            return "sent"

        @app.tool()
        async def list_siblings() -> str:
            invoked.append("list_siblings")
            return "not allowed"

        server._install_tool_profile_middleware_if_requested(app)
        async with Client(app) as client:
            assert {tool.name for tool in await client.list_tools()} == {"send_message"}
            assert (await client.call_tool("send_message")).data == "sent"
            with pytest.raises(ToolError):
                await client.call_tool("list_siblings")
        assert invoked == ["send_message"]
