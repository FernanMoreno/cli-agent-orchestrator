"""Offline evidence contracts for one owned, completed local task query."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import threading
from pathlib import Path
from test.live_provider import opencode_mcp_observer as observer_module

import pytest
from mcp import types

pytestmark = pytest.mark.offline_database

NONCE = "a" * 32
CALLER = "a1b2c3d4"
TASK_ID = "11111111-1111-4111-8111-111111111111"
TOOL = "get_local_cao_task"
PRIVATE_OUTPUT = "PRIVATE_TASK_OUTPUT_MUST_NOT_ESCAPE"
OTHER_TASK_ID = "22222222-2222-4222-8222-222222222222"
_DEFAULT = object()


def test_frozen_observer_import_matches_the_native_fixture_loader():
    name = "_task_observer_native_file_loader_probe"
    assert name not in sys.modules
    spec = importlib.util.spec_from_file_location(name, observer_module.__file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Existing native readers execute a frozen module without registering it
    # in sys.modules. Keep that established loader protocol compatible.
    spec.loader.exec_module(module)
    assert module._SafeTaskResult().success is None
    assert name not in sys.modules


def test_expanded_native_catalog_does_not_enable_task_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(
        observer_module, "INTERACTION_TOOLS", observer_module.INTERACTION_TOOLS | {TOOL}
    )
    path = tmp_path / "default-protocol.jsonl"
    observer = observer_module.ProtocolObserver(path, NONCE)
    try:
        call_id, tool = observer.start_call(TOOL, {"task_id": TASK_ID})
        assert tool == TOOL
        assert observer._task_binding_loaded is False and observer._task_probes == {}
        observer.finish_call(call_id, status="completed", error_present=False, result_present=True)
    finally:
        observer.close()
    summary = observer_module.read_protocol_observations(path, expected_nonce=NONCE)
    assert summary["observer_status"] == "observed"
    assert "safe_result_success" not in summary["mcp_tool_calls"][0]
    assert TASK_ID not in path.read_text()


@pytest.mark.parametrize(
    "invalid", ["missing_path", "missing_caller", "wrong_caller", "unselected"]
)
def test_environment_requires_explicit_task_binding_and_protected_caller(tmp_path, invalid):
    from fastmcp import FastMCP

    server = FastMCP("task-binding-environment")
    environment = {
        "CAO_OPENCODE_MCP_OBSERVER": "1",
        "CAO_OPENCODE_MCP_OBSERVER_PATH": str(tmp_path / "protocol.jsonl"),
        "CAO_OPENCODE_MCP_OBSERVER_NONCE": NONCE,
        "CAO_OPENCODE_MCP_SAFE_RESULT_TOOL": TOOL,
        "CAO_OPENCODE_MCP_SAFE_TASK_BINDING_PATH": str(tmp_path / "task-binding.json"),
        "CAO_TERMINAL_ID": CALLER,
    }
    if invalid == "missing_path":
        environment.pop("CAO_OPENCODE_MCP_SAFE_TASK_BINDING_PATH")
    elif invalid == "missing_caller":
        environment.pop("CAO_TERMINAL_ID")
    elif invalid == "wrong_caller":
        environment["CAO_TERMINAL_ID"] = PRIVATE_OUTPUT
    else:
        environment.pop("CAO_OPENCODE_MCP_SAFE_RESULT_TOOL")
    with pytest.raises(RuntimeError, match="mcp_observer_task_binding_invalid"):
        observer_module.install_protocol_observer_from_env(server, environment)
    assert not (tmp_path / "protocol.jsonl").exists()


def test_environment_installation_does_not_require_task_issuance_during_boot(tmp_path):
    from fastmcp import FastMCP

    server = FastMCP("task-binding-late-issuance")
    path = tmp_path / "protocol.jsonl"
    binding = tmp_path / "task-binding.json"
    observer = observer_module.install_protocol_observer_from_env(
        server,
        {
            "CAO_OPENCODE_MCP_OBSERVER": "1",
            "CAO_OPENCODE_MCP_OBSERVER_PATH": str(path),
            "CAO_OPENCODE_MCP_OBSERVER_NONCE": NONCE,
            "CAO_OPENCODE_MCP_SAFE_RESULT_TOOL": TOOL,
            "CAO_OPENCODE_MCP_SAFE_TASK_BINDING_PATH": str(binding),
            "CAO_TERMINAL_ID": CALLER,
        },
    )
    try:
        assert not binding.exists() and observer._task_binding_loaded is False
    finally:
        observer.close()
    assert _read(path)["mcp_tool_calls"] == []


def _issue_binding(path: Path, **overrides: object) -> None:
    document = {
        "nonce": NONCE,
        "task_id": TASK_ID,
        "requester_terminal_id": CALLER,
        "state": "succeeded",
        **overrides,
    }
    temporary = path.with_suffix(".pending")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(document, stream)
    temporary.replace(path)


def _task_receipt(**overrides: object) -> dict[str, object]:
    return {
        "task_id": TASK_ID,
        "requester_terminal_id": CALLER,
        "state": "succeeded",
        "result": {"state": "succeeded", "output": PRIVATE_OUTPUT},
        "operation_key": "PRIVATE_OPERATION_KEY",
        **overrides,
    }


def _produced_task_receipt():
    from cli_agent_orchestrator.clients.database import LocalPeerTaskModel
    from cli_agent_orchestrator.services.local_peer_service import _task_dict

    # Exercise the real ORM/HTTP producer shape without acquiring a database.
    row = LocalPeerTaskModel(
        task_id=TASK_ID,
        source_instance_id="33333333-3333-4333-8333-333333333333",
        target_instance_id="44444444-4444-4444-8444-444444444444",
        requester_terminal_id=CALLER,
        project_id="fixture-project",
        operation_key="PRIVATE_OPERATION_KEY",
        state="succeeded",
        result_json=json.dumps({"state": "succeeded", "output": PRIVATE_OUTPUT}),
    )
    return _task_dict(row)


def _production_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, enabled=True, transport_error=False
):
    from cli_agent_orchestrator.mcp_server import server as production_server

    low_level = production_server.mcp._mcp_server
    list_handler = low_level.request_handlers[types.ListToolsRequest]
    call_handler = low_level.request_handlers[types.CallToolRequest]
    path = tmp_path / "task-protocol.jsonl"
    binding = tmp_path / "task-binding.json"
    observed_requests = []

    def transport(route, **query):
        observed_requests.append((route, query))
        if transport_error:
            import requests

            raise requests.HTTPError(PRIVATE_OUTPUT)
        return _produced_task_receipt()

    monkeypatch.setattr(
        production_server, "_local_peer_caller_context", lambda: (CALLER, str(tmp_path), None)
    )
    monkeypatch.setattr(production_server.mcp_utils, "local_get_json", transport)
    options = (
        {
            "expected_safe_result_tool": TOOL,
            "expected_task_binding_path": binding,
            "expected_task_requester_id": CALLER,
        }
        if enabled
        else {}
    )
    observer = observer_module.install_protocol_observer(low_level, path, NONCE, **options)
    # The binding is issued after the MCP child/observer starts and before the
    # model requests the tool. Initialization must not wait for this file.
    _issue_binding(binding)

    async def dispatch():
        return await low_level.request_handlers[types.CallToolRequest](
            types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(name=TOOL, arguments={"task_id": TASK_ID}),
            )
        )

    try:
        result = asyncio.run(dispatch())
    finally:
        observer.close()
        low_level.request_handlers[types.ListToolsRequest] = list_handler
        low_level.request_handlers[types.CallToolRequest] = call_handler
    summary = observer_module.read_protocol_observations(
        path, expected_nonce=NONCE, **({"expected_safe_result_tool": TOOL} if enabled else {})
    )
    assert observed_requests == [
        (f"/local-coordination/agent/tasks/{TASK_ID}", {"requester_terminal_id": CALLER})
    ]
    assert isinstance(result.root, types.CallToolResult) and result.root.isError is False
    return path, summary


def test_registered_task_query_correlates_argument_result_and_late_binding(tmp_path, monkeypatch):
    path, summary = _production_query(tmp_path, monkeypatch)
    assert summary["observer_status"] == "observed"
    assert summary["mcp_tool_calls"] == [
        {
            "server": "cao-mcp-server",
            "tool": TOOL,
            "status": "completed",
            "error_present": False,
            "result_present": True,
            "safe_result_success": True,
            "safe_result_task_binding_observed": True,
            "safe_result_task_argument_matches": True,
            "safe_result_task_id_matches": True,
            "safe_result_task_requester_matches": True,
            "safe_result_task_state": "succeeded",
            "safe_result_task_state_matches": True,
            "safe_result_task_result_state": "succeeded",
            "safe_result_task_result_state_matches": True,
        }
    ]
    exported = path.read_text() + json.dumps(summary)
    for private in (TASK_ID, CALLER, PRIVATE_OUTPUT, "PRIVATE_OPERATION_KEY"):
        assert private not in exported


def test_task_result_capture_stays_off_by_default(tmp_path, monkeypatch):
    path, summary = _production_query(tmp_path, monkeypatch, enabled=False)
    assert summary["observer_status"] == "observed"
    assert summary["mcp_tool_calls"][0]["tool"] == "other"
    assert "safe_result_success" not in summary["mcp_tool_calls"][0]
    assert "safe_task_result_observed" not in path.read_text()
    assert PRIVATE_OUTPUT not in path.read_text()


def test_handled_http_failure_is_not_mistaken_for_a_successful_task_read(tmp_path, monkeypatch):
    path, summary = _production_query(tmp_path, monkeypatch, transport_error=True)
    call = summary["mcp_tool_calls"][0]
    assert call["status"] == "completed" and call["error_present"] is False
    assert call["safe_result_task_argument_matches"] is True
    assert call["safe_result_success"] is False
    assert call["safe_result_task_id_matches"] is None
    _assert_private(path, summary)


def _new_observer(tmp_path):
    path = tmp_path / "task-protocol.jsonl"
    binding = tmp_path / "task-binding.json"
    observer = observer_module.ProtocolObserver(
        path,
        NONCE,
        expected_safe_result_tool=TOOL,
        expected_task_binding_path=binding,
        expected_task_requester_id=CALLER,
    )
    return observer, path, binding


def _payload(document=None, *, text=_DEFAULT, structured=False, is_error=False):
    if document is None:
        document = {"success": True, **_task_receipt()}
    content = (
        []
        if text is None
        else [
            types.TextContent(type="text", text=json.dumps(document) if text is _DEFAULT else text)
        ]
    )
    return types.CallToolResult(
        content=content,
        structuredContent=document if structured else None,
        isError=is_error,
    )


def _finish(observer, payload, arguments=_DEFAULT):
    if arguments is _DEFAULT:
        arguments = {"task_id": TASK_ID}
    call_id, tool = observer.start_call(TOOL, arguments)
    assert tool == TOOL
    observer.record_safe_result(call_id, payload)
    observer.finish_call(
        call_id, status="completed", error_present=False, result_present=payload is not None
    )


def _read(path):
    return observer_module.read_protocol_observations(
        path, expected_nonce=NONCE, expected_safe_result_tool=TOOL
    )


def _assert_private(path, summary):
    exported = path.read_text() + json.dumps(summary)
    for value in (TASK_ID, OTHER_TASK_ID, CALLER, PRIVATE_OUTPUT, "PRIVATE_OPERATION_KEY"):
        assert value not in exported


@pytest.mark.parametrize(
    ("overrides", "field", "expected"),
    [
        ({"task_id": OTHER_TASK_ID}, "id_matches", False),
        ({"task_id": 1}, "id_matches", None),
        ({"task_id": None}, "id_matches", None),
        ({"requester_terminal_id": "b1b2c3d4"}, "requester_matches", False),
        ({"requester_terminal_id": True}, "requester_matches", None),
        ({"requester_terminal_id": None}, "requester_matches", None),
        ({"state": "failed"}, "state_matches", False),
        ({"state": "cancelled"}, "state_matches", False),
        ({"state": "reconcile"}, "state_matches", False),
        ({"state": PRIVATE_OUTPUT}, "state_matches", False),
        ({"state": True}, "state_matches", None),
        ({"state": None}, "state_matches", None),
        ({"result": {"state": "failed"}}, "result_state_matches", False),
        ({"result": {"state": "cancelled"}}, "result_state_matches", False),
        ({"result": {"state": "reconcile"}}, "result_state_matches", False),
        ({"result": {"state": PRIVATE_OUTPUT}}, "result_state_matches", False),
        ({"result": {"state": 1}}, "result_state_matches", None),
        ({"result": None}, "result_state_matches", None),
    ],
)
def test_task_evidence_requires_exact_owner_identity_and_coherent_states(
    tmp_path, overrides, field, expected
):
    observer, path, binding = _new_observer(tmp_path)
    _issue_binding(binding)
    try:
        _finish(observer, _payload({"success": True, **_task_receipt(**overrides)}))
    finally:
        observer.close()
    summary = _read(path)
    assert summary["observer_status"] == "observed"
    assert summary["mcp_tool_calls"][0]["safe_result_task_" + field] is expected
    _assert_private(path, summary)


@pytest.mark.parametrize(
    "arguments",
    [
        None,
        {},
        {"task_id": OTHER_TASK_ID},
        {"task_id": True},
        {"task_id": TASK_ID, "extra": PRIVATE_OUTPUT},
        {"private": PRIVATE_OUTPUT},
    ],
)
def test_returning_expected_task_does_not_credit_a_different_request(tmp_path, arguments):
    observer, path, binding = _new_observer(tmp_path)
    _issue_binding(binding)
    try:
        _finish(observer, _payload(), arguments)
    finally:
        observer.close()
    summary = _read(path)
    call = summary["mcp_tool_calls"][0]
    assert call["safe_result_task_id_matches"] is True
    assert call["safe_result_task_argument_matches"] is False
    _assert_private(path, summary)


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "nonce",
        "owner",
        "uuid",
        "state",
        "extra",
        "mode",
        "link",
        "hardlink",
        "directory",
        "fifo",
        "oversized",
        "utf8",
        "duplicate",
    ],
)
def test_unissued_or_unsafe_metadata_never_establishes_task_evidence(tmp_path, kind):
    observer, path, binding = _new_observer(tmp_path)
    if kind != "missing":
        overrides = {
            "nonce": {"nonce": "b" * 32},
            "owner": {"requester_terminal_id": "b1b2c3d4"},
            "uuid": {"task_id": "invalid"},
            "state": {"state": "failed"},
            "extra": {"unknown": PRIVATE_OUTPUT},
        }.get(kind, {})
        _issue_binding(binding, **overrides)
    if kind == "mode":
        binding.chmod(0o644)
    elif kind == "link":
        issued = binding.with_name("issued.json")
        binding.rename(issued)
        binding.symlink_to(issued)
    elif kind == "hardlink":
        os.link(binding, binding.with_name("second-link.json"))
    elif kind in {"directory", "fifo"}:
        binding.unlink()
        if kind == "directory":
            binding.mkdir(mode=0o700)
        else:
            os.mkfifo(binding, 0o600)
    elif kind == "oversized":
        binding.write_bytes(b"x" * 1025)
    elif kind == "utf8":
        binding.write_bytes(b"\xff")
    elif kind == "duplicate":
        binding.write_text('{"nonce":"' + NONCE + '","nonce":"' + NONCE + '"}')
    try:
        _finish(observer, _payload())
    finally:
        observer.close()
    summary = _read(path)
    assert summary["observer_status"] == "observed"
    call = summary["mcp_tool_calls"][0]
    assert call["safe_result_task_binding_observed"] is False
    assert call["safe_result_task_argument_matches"] is None
    assert call["safe_result_success"] is None
    _assert_private(path, summary)


@pytest.mark.parametrize(
    "kind",
    [
        "error",
        "success_false",
        "success_integer",
        "non_json",
        "duplicate",
        "oversized",
        "utf8_oversized",
        "surrogate",
        "conflicting",
        "extra_block",
        "nested_wrapper",
    ],
)
def test_ambiguous_or_failed_sdk_results_cannot_credit_a_task(tmp_path, kind):
    observer, path, binding = _new_observer(tmp_path)
    _issue_binding(binding)
    document = {"success": True, **_task_receipt()}
    if kind == "error":
        payload = _payload(document, is_error=True)
    elif kind == "success_false":
        payload = _payload({"success": False, "error": PRIVATE_OUTPUT})
    elif kind == "success_integer":
        payload = _payload({**document, "success": 1})
    elif kind == "non_json":
        payload = _payload(text=PRIVATE_OUTPUT)
    elif kind == "duplicate":
        payload = _payload(text='{"success":true,"success":true}')
    elif kind == "oversized":
        payload = _payload(text=PRIVATE_OUTPUT * 5000)
    elif kind == "utf8_oversized":
        text = json.dumps({**document, "unexported": "界" * 1500}, ensure_ascii=False)
        assert len(text) < 4096 < len(text.encode())
        payload = _payload(text=text)
    elif kind == "surrogate":
        payload = _payload(text="\ud800")
    elif kind == "conflicting":
        payload = _payload(
            document, structured=True, text=json.dumps({**document, "task_id": OTHER_TASK_ID})
        )
    elif kind == "extra_block":
        payload = _payload(document, structured=True)
        payload.content.append(types.TextContent(type="text", text=PRIVATE_OUTPUT))
    else:
        payload = _payload({"result": {"result": document}})
    try:
        _finish(observer, payload)
    finally:
        observer.close()
    summary = _read(path)
    assert summary["mcp_tool_calls"][0]["safe_result_success"] is (
        False if kind == "success_false" else None
    )
    _assert_private(path, summary)


@pytest.mark.parametrize("wrapped", [False, True])
def test_structured_and_text_sdk_forms_agree_on_safe_task_fields(tmp_path, wrapped):
    observer, path, binding = _new_observer(tmp_path)
    _issue_binding(binding)
    document = {"success": True, **_task_receipt()}
    if wrapped:
        document = {"result": document}
    try:
        _finish(observer, _payload(document, structured=True))
    finally:
        observer.close()
    summary = _read(path)
    call = summary["mcp_tool_calls"][0]
    assert call["safe_result_success"] is True
    assert call["safe_result_task_result_state_matches"] is True
    _assert_private(path, summary)


@pytest.mark.parametrize("mode", ["changed", "initially_missing"])
def test_binding_is_frozen_once_and_never_reacquired_on_retry(tmp_path, mode):
    observer, path, binding = _new_observer(tmp_path)
    if mode == "changed":
        _issue_binding(binding)
    try:
        _finish(observer, _payload())
        _issue_binding(binding, task_id=OTHER_TASK_ID)
        _finish(
            observer,
            _payload({"success": True, **_task_receipt(task_id=OTHER_TASK_ID)}),
            {"task_id": OTHER_TASK_ID},
        )
    finally:
        observer.close()
    summary = _read(path)
    assert len(summary["mcp_tool_calls"]) == 2
    second = summary["mcp_tool_calls"][1]
    assert second["safe_result_task_argument_matches"] is (False if mode == "changed" else None)
    assert second["safe_result_task_id_matches"] is (False if mode == "changed" else None)
    _assert_private(path, summary)


def test_interleaved_calls_keep_their_own_argument_comparisons(tmp_path):
    observer, path, binding = _new_observer(tmp_path)
    _issue_binding(binding)
    try:
        first, _ = observer.start_call(TOOL, {"task_id": TASK_ID})
        second, _ = observer.start_call(TOOL, {"task_id": OTHER_TASK_ID})
        for call_id in (second, first):
            observer.record_safe_result(call_id, _payload())
            observer.finish_call(
                call_id, status="completed", error_present=False, result_present=True
            )
    finally:
        observer.close()
    summary = _read(path)
    assert [call["safe_result_task_argument_matches"] for call in summary["mcp_tool_calls"]] == [
        True,
        False,
    ]
    _assert_private(path, summary)


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "duplicate",
        "after_finish",
        "wrong_call",
        "bool_call",
        "wrong_tool",
        "extra_field",
        "bool_as_int",
        "unbound_positive",
        "state_contradiction",
    ],
)
def test_reader_rejects_uncorrelated_or_forged_task_evidence(tmp_path, change):
    observer, path, binding = _new_observer(tmp_path)
    _issue_binding(binding)
    try:
        _finish(observer, _payload())
    finally:
        observer.close()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    index = next(i for i, row in enumerate(rows) if row["event"] == "safe_task_result_observed")
    row = rows[index]
    if change == "missing":
        rows.pop(index)
    elif change == "duplicate":
        rows.insert(index, dict(row))
    elif change == "after_finish":
        rows[index], rows[index + 1] = rows[index + 1], rows[index]
    elif change == "wrong_call":
        row["call_id"] = 2
    elif change == "bool_call":
        row["call_id"] = True
    elif change == "wrong_tool":
        next(item for item in rows if item["event"] == "tool_call_started")["tool"] = "other"
    elif change == "extra_field":
        row["output"] = PRIVATE_OUTPUT
    elif change == "bool_as_int":
        row["argument_matches"] = 1
    elif change == "unbound_positive":
        row["binding_observed"] = False
    else:
        row["state_matches"] = False
    for sequence, item in enumerate(rows, 1):
        item["sequence"] = sequence
    path.write_text("".join(json.dumps(item) + "\n" for item in rows))
    summary = _read(path)
    assert summary["observer_status"] == "incomplete"
    assert PRIVATE_OUTPUT not in json.dumps(summary)


def test_pending_task_evidence_is_bounded_and_late_load_cannot_revive_closed_observer(
    tmp_path, monkeypatch
):
    observer, _path, binding = _new_observer(tmp_path)
    _issue_binding(binding)
    try:
        for _ in range(300):
            observer.start_call(TOOL, {"task_id": TASK_ID})
        assert len(observer._task_probes) <= observer_module._MAX_EVENTS
    finally:
        observer.close()
    assert observer._task_probes == {}

    other = tmp_path / "race"
    other.mkdir(mode=0o700)
    observer, _path, _binding = _new_observer(other)
    entered, release = threading.Event(), threading.Event()

    def delayed(*_args):
        entered.set()
        assert release.wait(2)
        return observer_module._TaskBinding(TASK_ID, CALLER, "succeeded")

    monkeypatch.setattr(observer_module, "_load_task_binding", delayed)
    worker = threading.Thread(target=observer.start_call, args=(TOOL, {"task_id": TASK_ID}))
    try:
        worker.start()
        assert entered.wait(2)
        observer.close()
    finally:
        release.set()
        worker.join(timeout=2)
    assert not worker.is_alive()
    assert observer._task_probes == {} and observer._task_binding is None
