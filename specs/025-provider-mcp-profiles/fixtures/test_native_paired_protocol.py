"""Contracts for production CLI pairing and real MCP result admission."""

import json
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


def test_cli_pairing_receipt_does_not_export_one_use_code(monkeypatch, tmp_path):
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
        if label == "source-pair":
            text = (
                f"Par: target ({target.instance_id})\nProyecto: {tmp_path}\n"
                f"ID de emparejamiento: {challenge}\nCódigo temporal (válido 5 minutos): {code}\n"
            )
        else:
            assert stdin == f"y\n{code}\n"
            text = f"Emparejado con source para el proyecto {project_id}.\n"
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
