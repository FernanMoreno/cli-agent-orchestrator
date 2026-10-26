"""Contracts for production CLI pairing and real MCP result admission."""

import json
from pathlib import Path
from types import SimpleNamespace

import native_paired_protocol as protocol
import pytest
from mcp import types


@pytest.mark.parametrize("structured", [True, False])
def test_only_successful_unambiguous_mcp_receipt_is_admitted(structured):
    value = {"success": True, "task_id": "private-task", "state": "running"}
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(value))],
        structuredContent={"result": value} if structured else None,
    )
    assert protocol.unwrap_coordination_result(result) == value


@pytest.mark.parametrize(
    "payload",
    [
        {"success": False},
        {"success": 1},
        {"success": True, "error": "project_busy"},
        {"success": False, "result": {"success": True}},
        {"result": {"success": True}, "extra": True},
        [],
    ],
)
def test_refused_or_ambiguous_receipt_cannot_be_success(payload):
    result = types.CallToolResult(content=[], structuredContent={"result": payload})
    with pytest.raises(protocol.HarnessError):
        protocol.unwrap_coordination_result(result)


def test_missing_or_failed_protocol_result_fails_closed():
    for value in (SimpleNamespace(), types.CallToolResult(content=[], isError=True)):
        with pytest.raises(protocol.HarnessError):
            protocol.unwrap_coordination_result(value)


@pytest.mark.parametrize("pretty_json", [False, True])
@pytest.mark.parametrize("binding", ["correct", "wrong_peer", "wrong_project"])
def test_cli_pairing_receipt_does_not_export_one_use_code(
    monkeypatch, tmp_path, pretty_json, binding
):
    source = protocol._Identity("11111111-1111-4111-8111-111111111111", "a")
    target = protocol._Identity("22222222-2222-4222-8222-222222222222", "b")
    monkeypatch.setattr(
        protocol, "_identity", lambda url: source if url == "http://127.0.0.1:19001" else target
    )
    code = "a" * 43
    challenge = "33333333-3333-4333-8333-333333333333"
    project_id = "b" * 64
    calls = []

    def command(label, env, args, raw_dir, *, stdin=None):
        calls.append((label, args, stdin))
        assert "--json" in args
        if label == "source-pair":
            payload = {
                "schema": "cao.peer.pair.v1",
                "success": True,
                "data": {
                    "challenge_id": challenge,
                    "code": code,
                    "expires_at": 2000000000.0,
                    "peer_instance_id": target.instance_id,
                    "peer_display_name": "target",
                    "project_id": project_id,
                    "canonical_root": str(tmp_path),
                    "requested_scopes": ["task:submit"],
                },
            }
        else:
            assert stdin == f"y\n{code}\n"
            payload = {
                "schema": "cao.peer.accept.v1",
                "success": True,
                "data": {
                    "peer_instance_id": source.instance_id,
                    "peer_display_name": "source",
                    "project_id": project_id,
                    "scopes": ["task:submit"],
                },
            }
        if label == "target-accept":
            if binding == "wrong_peer":
                payload["data"]["peer_instance_id"] = target.instance_id
            if binding == "wrong_project":
                payload["data"]["project_id"] = "c" * 64
        text = json.dumps(payload, indent=2 if pretty_json else None)
        return text, tmp_path / (label + ".private")

    monkeypatch.setattr(protocol, "_run_cli", command)
    source_env = {
        "HOME": str(tmp_path),
        "CAO_HOME_DIR": str(tmp_path / "source"),
        "CAO_DATABASE_CONFIG_FILE": str(tmp_path / "source.json"),
        "CAO_API_PORT": "19001",
    }
    target_env = {
        "HOME": str(tmp_path),
        "CAO_HOME_DIR": str(tmp_path / "target"),
        "CAO_DATABASE_CONFIG_FILE": str(tmp_path / "target.json"),
        "CAO_API_PORT": "19002",
    }
    if binding != "correct":
        with pytest.raises(protocol.HarnessError, match="accept_output_invalid"):
            receipt = protocol.pair_profiles(
                source_env,
                target_env,
                "http://127.0.0.1:19001",
                "http://127.0.0.1:19002",
                tmp_path,
                tmp_path,
            )
        return
    receipt = protocol.pair_profiles(
        source_env,
        target_env,
        "http://127.0.0.1:19001",
        "http://127.0.0.1:19002",
        tmp_path,
        tmp_path,
    )
    assert receipt.project_id == project_id
    assert receipt.target_instance_id == target.instance_id
    assert code not in repr(receipt)
    assert [call[0] for call in calls] == ["source-pair", "target-accept"]


def test_new_journey_stays_closed_even_with_go(monkeypatch, capsys):
    import run_native_paired_worker as journey

    monkeypatch.setattr(journey, "run", lambda *_a, **_k: pytest.fail("runtime started"))
    assert journey.RUN_ENABLED is False
    assert journey.main(["--go"]) == 2
    assert json.loads(capsys.readouterr().out) == {"status": "closed", "model_started": False}


def test_native_worker_profile_pins_model_and_denies_mcp_tools():
    import frontmatter
    import run_native_paired_worker as journey

    post = frontmatter.loads(journey.worker_profile({"CAO_API_PORT": "19002"}))
    assert post.metadata["provider"] == "opencode_cli"
    assert post.metadata["model"] == journey.MODEL
    assert post.metadata["allowedTools"] == []
    server = post.metadata["mcpServers"]["cao-mcp-server"]
    assert server["cao_tool_profile"] == "collaboration"
    assert server["enabled_tools"] == []


@pytest.mark.parametrize(
    "url", ["https://127.0.0.1:19001", "http://example.invalid:19001", "http://127.0.0.1:19002"]
)
def test_pairing_environment_cannot_redirect_to_another_endpoint(url):
    with pytest.raises(protocol.HarnessError):
        protocol._check_profile_binding({"CAO_API_PORT": "19001"}, url)


def test_shared_peer_is_written_even_when_targets_are_already_runtime_only(tmp_path):
    import dataclasses
    from test.postgres.fixtures import PostgresTargets

    import run_native_paired_worker as journey

    def target(name):
        return SimpleNamespace(
            host="127.0.0.1",
            port=15432,
            database=name,
            user=name + "_app",
            schema="private",
            store_id=name,
            secret_file=tmp_path / (name + ".json"),
            sslmode="disable",
            migration_user=None,
            migration_secret_file=None,
        )

    main, auth, private_peer, shared_peer = [
        target(name) for name in ("main", "auth", "private_peer", "shared_peer")
    ]
    original = tmp_path / "original.json"
    original.write_text(json.dumps({"peer": private_peer.database}))
    targets = PostgresTargets(main, auth, private_peer, tmp_path, original)
    adopted = dataclasses.replace(targets, peer=shared_peer)
    from test.fixtures.cao_server import _runtime_only_database_targets

    old_runtime, old_derivation = _runtime_only_database_targets(adopted, tmp_path / "legacy")
    assert old_derivation is None
    assert old_runtime.config_file == original
    assert json.loads(old_runtime.config_file.read_text())["peer"] == private_peer.database
    runtime = journey.runtime_configuration(adopted, tmp_path / "runtime")
    body = json.loads(runtime.config_file.read_text())
    assert body["targets"]["peer"]["database"] == shared_peer.database
    assert body["targets"]["main"]["database"] == main.database
    assert body["targets"]["auth"]["database"] == auth.database
    assert Path(body["targets"]["peer"]["secret_file"]).is_absolute()
    assert runtime.config_file != original
    assert json.loads(original.read_text()) == {"peer": private_peer.database}
    assert runtime.config_file.stat().st_mode & 0o777 == 0o600
    assert all("migration_user" not in value for value in body["targets"].values())


@pytest.mark.parametrize(
    "created,exited,dead,expected",
    [
        (False, False, True, True),
        (True, False, True, False),
        (True, True, True, True),
        (True, True, False, False),
    ],
)
def test_mcp_closure_distinguishes_not_created_from_failed_exit(created, exited, dead, expected):
    import run_native_paired_worker as journey

    receipt = {"mcp_client_created": created, "mcp_client_closed": exited}
    journey.record_mcp_closure(receipt, processes_dead=dead)
    assert receipt["mcp_client_closed"] is expected


@pytest.mark.parametrize(
    "text",
    [
        "Emparejado con source para el proyecto " + "b" * 64 + ".",
        '{"schema":"cao.peer.accept.v1","success":true,"success":false,"data":{}}',
        '{"schema":"cao.peer.accept.v1","success":1,"data":{}}',
        '{"schema":"cao.peer.accept.v2","success":true,"data":{}}',
        '{"schema":"cao.peer.accept.v1","success":true,"data":{},"extra":true}',
    ],
)
def test_structured_cli_rejects_prose_ambiguous_or_wrong_schema(text):
    from cli_agent_orchestrator.models.peer_cli import PeerAcceptOutput

    with pytest.raises(protocol.HarnessError, match="accept_output_invalid"):
        protocol._structured_cli_output(text, PeerAcceptOutput, "accept_output_invalid")


def test_structured_cli_requires_boolean_success_with_other_fields_valid():
    from cli_agent_orchestrator.models.peer_cli import PeerAcceptOutput

    payload = {
        "schema": "cao.peer.accept.v1",
        "success": 1,
        "data": {
            "peer_instance_id": "11111111-1111-4111-8111-111111111111",
            "peer_display_name": "source",
            "project_id": "b" * 64,
            "scopes": [],
        },
    }
    with pytest.raises(protocol.HarnessError, match="accept_output_invalid"):
        protocol._structured_cli_output(
            json.dumps(payload), PeerAcceptOutput, "accept_output_invalid"
        )


def test_private_caller_binary_is_source_bound_and_resolvable(tmp_path):
    import shutil

    import run_native_paired_worker as journey

    expected = journey.native.REPO / "test/providers/fixtures/bin/mock_cli"
    path = journey.private_executable_path(tmp_path / "bin/opencode")
    assert shutil.which("mock_cli", path=path) == str(expected)
    assert "test/providers/fixtures/bin/mock_cli" in journey.EXTRA_SOURCES


def test_native_model_selection_is_launch_argument_not_required_agent_metadata(
    tmp_path, monkeypatch
):
    import shlex

    import frontmatter
    import run_native_paired_worker as journey

    from cli_agent_orchestrator.models.opencode_agent import OpenCodeAgentConfig
    from cli_agent_orchestrator.providers.opencode_cli import OpenCodeCliProvider
    from cli_agent_orchestrator.utils import opencode_v2

    agents = tmp_path / "agents"
    agents.mkdir()
    metadata = OpenCodeAgentConfig(description="private control", permission={})
    (agents / f"{journey.WORKER}.md").write_text(
        frontmatter.dumps(frontmatter.Post("Control", **metadata.model_dump(exclude_none=True)))
    )
    native_root = tmp_path / "native"
    opencode_v2.write_v2_configuration(
        native_root,
        source_agents=agents,
        source_config={},
        session_id="deadbeef",
        selected_model=journey.MODEL,
    )
    config = json.loads((native_root / "opencode.json").read_text())
    assert "model" not in config["agents"][journey.WORKER]
    provider = OpenCodeCliProvider(
        "deadbeef", "control", "0", agent_profile=journey.WORKER, model=journey.MODEL
    )
    provider._cli_major = 2
    arguments = shlex.split(provider._build_launch_command())
    assert arguments[arguments.index("--model") + 1] == journey.MODEL
    info = native_root.stat()
    (native_root / ".cao-ownership.json").write_text(
        json.dumps(
            {
                "terminal_id": "deadbeef",
                "claim": {
                    "major": 2,
                    "device": info.st_dev,
                    "inode": info.st_ino,
                    "name": native_root.name,
                },
            }
        )
    )
    monkeypatch.setattr(journey.native, "_provider_roots", lambda _: [native_root])
    # Isolate the model-location regression from the separately checked MCP contract.
    monkeypatch.setattr(journey.native, "_native_mcp_profile_contract", lambda *_a, **_k: True)
    monkeypatch.setattr(opencode_v2, "validate_v2_mcp_configuration", lambda *_a, **_k: None)
    observed = []
    monkeypatch.setattr(
        journey, "owned_native_launch_selected", lambda tid, **kw: observed.append(tid) or True
    )
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    assert (
        journey.worker_configuration(tmp_path, "deadbeef", launch_binding={}, evidence=evidence)
        == native_root
    )
    assert observed == ["deadbeef"]


@pytest.mark.parametrize("selection", ["correct", "wrong_model", "duplicate_model", "version"])
def test_owned_native_model_gate_reads_exact_process_argv(tmp_path, selection):
    import os
    import subprocess
    import sys
    from test.live_provider import opencode_interaction_journey as processes
    from uuid import uuid4

    import run_native_paired_worker as journey

    terminal_id = uuid4().hex[:8]
    flags = ["--agent", journey.WORKER, "--model", journey.MODEL]
    if selection == "wrong_model":
        flags[-1] = "foreign/model"
    elif selection == "duplicate_model":
        flags += ["--model", journey.MODEL]
    elif selection == "version":
        flags = ["--version"]
    parent = processes._process_info(os.getpid())[0]
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; print('ready', flush=True); sys.stdin.read(1)", *flags],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env={"CAO_TERMINAL_ID": terminal_id, "PATH": "/usr/bin:/bin"},
    )
    try:
        assert child.stdout.readline() == b"ready\n"
        binding = {
            "binary_path": Path(sys.executable),
            "server_identity": parent,
            "socket_path": tmp_path / "unused-socket",
        }
        if selection in {"wrong_model", "duplicate_model"}:
            with pytest.raises(protocol.HarnessError, match="native_worker_launch_model_mismatch"):
                journey.owned_native_launch_selected(terminal_id, **binding)
        else:
            assert journey.owned_native_launch_selected(terminal_id, **binding) is (
                selection == "correct"
            )
    finally:
        child.terminate()
        child.wait(timeout=5)
        child.stdin.close()
        child.stdout.close()


@pytest.mark.parametrize("case", ["ready", "startup", "fallback", "wrong_session", "late"])
def test_native_startup_uses_typed_status_and_visible_pane(tmp_path, monkeypatch, case):
    import time

    import run_native_paired_worker as journey

    terminal = {
        "id": "deadbeef",
        "provider": "opencode_cli",
        "session_name": "private",
        "name": "0",
        "status": "idle",
    }
    observed = {**terminal}
    if case == "startup":
        observed["status"] = "unknown"
    if case == "wrong_session":
        observed["session_name"] = "foreign"
    monkeypatch.setattr(
        journey.native,
        "_read_terminal_before_deadline",
        lambda *_a, **_k: (observed, case != "late"),
    )
    calls = []

    def viewport(session, window, **kwargs):
        calls.append((session, window, kwargs))
        return "GLM-5.3-Flash · OpenCode Go" + (" · Big Pickle" if case == "fallback" else "")

    tmux = SimpleNamespace(get_history=viewport)
    server = SimpleNamespace(url="http://127.0.0.1:19002")
    if case == "wrong_session":
        with pytest.raises(protocol.HarnessError, match="terminal_binding_changed"):
            journey.native_startup_observation(
                server, terminal, tmux, deadline=time.monotonic() + 5
            )
        assert calls == []
        return
    result = journey.native_startup_observation(
        server, terminal, tmux, deadline=time.monotonic() + 5
    )
    if case == "late":
        assert result is None and calls == []
    else:
        assert calls == [("private", "0", {"strip_escapes": True, "visible_only": True})]
        assert result["ready"] is (case == "ready")


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "binary_unchanged",
        "interpreter_unchanged",
        "mcp_catalog_exact_twelve",
        "error",
        "memory",
    ],
)
def test_preflight_cannot_admit_models_without_preservation_and_catalog(fault):
    import run_native_paired_worker as journey

    proof = {
        key: True
        for key in (
            "api_processes_dead",
            "tmux_processes_dead",
            "mcp_client_closed",
            "private_provider_roots_removed",
            "worktrees_removed",
            "database_targets_closed",
            "private_state_removed",
            "operator_unchanged",
            "source_unchanged",
            "production_pairing_verified",
            "caller_controller_created",
            "native_preflight_passed",
            "mcp_catalog_exact_twelve",
            "binary_unchanged",
            "interpreter_unchanged",
        )
    }
    proof.update(
        schema="caos.spec025.t168.native-paired.v1",
        mode="preflight",
        model=journey.MODEL,
        binary_sha256=journey.BINARY_SHA256,
        memory_admissions_mib={"initial": 6144},
        cleanup_failures=[],
    )
    if fault == "error":
        proof["error_code"] = "mcp_tool_refused"
    elif fault == "memory":
        proof["memory_admissions_mib"]["initial"] = 6143
    elif fault is not None:
        proof[fault] = False
    assert journey.preflight_acceptance(proof) is (fault is None)


def test_confirmed_terminal_delete_is_not_repeated_and_keeps_other_ids(monkeypatch):
    import run_native_paired_worker as journey

    server = SimpleNamespace(url="http://127.0.0.1:19002")
    calls = []
    monkeypatch.setattr(
        journey, "api", lambda *args, **kwargs: calls.append(args) or {"success": True}
    )
    retired = set()
    journey.retire_terminal(server, "deadbeef", retired)
    journey.retire_terminal(server, "deadbeef", retired)
    assert len(calls) == 1
    journey.retire_terminal(server, "feedbeef", retired)
    assert len(calls) == 2
    assert retired == {(server.url, "deadbeef"), (server.url, "feedbeef")}


@pytest.mark.parametrize(
    "payload",
    [{"success": False}, {"success": 1}, {"success": True, "error": "deferred"}, {}, None],
)
def test_terminal_delete_requires_strict_positive_structured_receipt(payload):
    import run_native_paired_worker as journey

    retired = set()
    with pytest.raises(protocol.HarnessError, match="terminal_retirement_receipt_invalid"):
        journey.record_terminal_retirement(
            SimpleNamespace(url="http://127.0.0.1:19002"), "deadbeef", payload, retired
        )
    assert retired == set()


@pytest.mark.parametrize("fault", [None, "task", "terminal", "stop"])
def test_cancel_retirement_is_bound_to_the_stopped_worker(fault):
    import run_native_paired_worker as journey

    server = SimpleNamespace(url="http://127.0.0.1:19002")
    payload = {
        "task_id": "task-1",
        "state": "cancelled",
        "result": {"terminal_id": "deadbeef", "worker_stopped": True},
    }
    if fault == "task":
        payload["task_id"] = "foreign-task"
    elif fault == "terminal":
        payload["result"]["terminal_id"] = "feedbeef"
    elif fault == "stop":
        payload["result"]["worker_stopped"] = False
    retired = set()
    if fault is None:
        journey.record_cancelled_worker(server, "deadbeef", "task-1", payload, retired)
        assert retired == {(server.url, "deadbeef")}
    else:
        with pytest.raises(protocol.HarnessError, match="native_cancel_stop_not_proven"):
            journey.record_cancelled_worker(server, "deadbeef", "task-1", payload, retired)
        assert retired == set()


@pytest.mark.parametrize(
    "fault", [None, "ansi", "status", "turn_state", "generation", "identity", "no_model_output"]
)
def test_cancellation_reads_status_from_terminal_and_state_from_real_turn_contract(
    monkeypatch, fault
):
    import run_native_paired_worker as journey

    from cli_agent_orchestrator.services import remote_terminal_service as remote
    from cli_agent_orchestrator.services import turn_recovery_service as recovery

    monkeypatch.setattr(remote, "placement", lambda _: None)
    monkeypatch.setattr(recovery, "get_terminal_metadata", lambda _: {"provider": "opencode_cli"})
    monkeypatch.setattr(
        recovery, "get_terminal_turn_receipt", lambda _: {"generation": "a" * 32, "phase": "sent"}
    )
    monkeypatch.setattr(recovery, "get_terminal_turn_recovery", lambda *_: None)
    turn = recovery.get_turn("deadbeef")
    assert "status" not in turn and turn["state"] == "pending"
    metadata = {"id": "deadbeef", "provider": "opencode_cli", "status": "processing"}
    output = "RESULT: 468911\n1. ..."
    if fault == "ansi":
        output = "RES\x1b[31mULT: 468911\x1b[0m\n1. ..."
    elif fault == "status":
        metadata["status"] = "completed"
    elif fault == "turn_state":
        turn["state"] = "verified"
    elif fault == "generation":
        turn["generation"] = None
    elif fault == "identity":
        turn["terminal_id"] = "feedbeef"
    elif fault == "no_model_output":
        output = "Start with RESULT: plus the sum of 270584 and 198327"
    assert journey.active_native_cancellation_barrier("deadbeef", metadata, turn, output) is (
        fault in (None, "ansi")
    )


def test_cancel_poll_preserves_failed_predicate_and_private_output(tmp_path):
    import stat

    import run_native_paired_worker as journey

    terminal = "deadbeef"
    metadata = {"id": terminal, "provider": "opencode_cli", "status": "processing"}
    turn = {
        "terminal_id": terminal,
        "provider": "opencode_cli",
        "state": "pending",
        "generation": "a" * 32,
    }
    observation = journey.record_cancel_observation(
        tmp_path, terminal, metadata, turn, {"state": "running"}, "still thinking", 1.25
    )
    assert observation["schema"] == "caos.spec025.t168.cancel-observation.v1"
    assert observation["active"] is False
    assert observation["generated_canary_present"] is False
    assert observation["terminal"]["status"] == "processing"
    assert observation["turn"]["state"] == "pending"
    saved = tmp_path / "native-cancel-0000.private.json"
    assert json.loads(saved.read_text()) == observation
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    valid = journey.record_cancel_observation(
        tmp_path, terminal, metadata, turn, {"state": "running"}, "RESULT: 468911", 1.5
    )
    assert valid["active"] is True
    assert (tmp_path / "native-cancel-0001.private.json").exists()


def test_structured_exception_report_retains_nested_operation_error():
    import run_native_paired_worker as journey

    error = ExceptionGroup(
        "outer",
        [
            ExceptionGroup("inner", [protocol.HarnessError("native_model_75_second_deadline")]),
            ValueError("private detail must not escape"),
        ],
    )
    assert journey.exception_report(error) == {
        "type": "ExceptionGroup",
        "leaf_types": ["HarnessError", "ValueError"],
        "harness_codes": ["native_model_75_second_deadline"],
    }
