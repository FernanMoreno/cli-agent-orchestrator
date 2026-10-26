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
