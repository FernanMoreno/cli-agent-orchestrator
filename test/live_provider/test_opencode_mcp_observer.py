"""Offline contract tests for the private OpenCode MCP protocol observer."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from test.live_provider import opencode_mcp_observer
from types import SimpleNamespace

import pytest


@pytest.mark.offline_database
def test_observer_records_protocol_catalog_and_calls_without_payloads(tmp_path: Path) -> None:
    from fastmcp import FastMCP
    from mcp import types

    server = FastMCP("observer-contract")
    tool_calls = {
        "cao_ask_user": 0,
        "cao_wait_for_user_answers": 0,
        "cao_acknowledge_user_answers": 0,
        "private_unexpected_tool": 0,
    }

    @server.tool()
    async def cao_ask_user(value: str) -> dict[str, bool]:
        tool_calls["cao_ask_user"] += 1
        return {"ok": bool(value)}

    @server.tool()
    async def cao_wait_for_user_answers(value: str) -> dict[str, bool]:
        tool_calls["cao_wait_for_user_answers"] += 1
        raise RuntimeError("RAW_ERROR_SENTINEL_MUST_NOT_ESCAPE")

    @server.tool()
    async def cao_acknowledge_user_answers(value: str) -> dict[str, bool]:
        tool_calls["cao_acknowledge_user_answers"] += 1
        return {"ok": bool(value)}

    @server.tool()
    async def private_unexpected_tool(value: str) -> dict[str, bool]:
        tool_calls["private_unexpected_tool"] += 1
        return {"ok": bool(value)}

    path = tmp_path / "mcp-observations.jsonl"
    nonce = "c" * 32
    observer = opencode_mcp_observer.install_protocol_observer(server._mcp_server, path, nonce)
    handlers = server._mcp_server.request_handlers

    async def dispatch() -> None:
        catalog = await handlers[types.ListToolsRequest](
            types.ListToolsRequest(method="tools/list")
        )
        assert isinstance(catalog.root, types.ListToolsResult)
        results = []
        for name in (
            "cao_ask_user",
            "cao_wait_for_user_answers",
            "cao_acknowledge_user_answers",
            "private_unexpected_tool",
        ):
            request = types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(
                    name=name,
                    arguments={"value": "RAW_ARGUMENT_SENTINEL_MUST_NOT_ESCAPE"},
                ),
            )
            result = await handlers[types.CallToolRequest](request)
            results.append(result)
            assert isinstance(result.root, types.CallToolResult)
        assert len(results) == 4
        assert results[1].root.isError is True

    try:
        asyncio.run(dispatch())
    finally:
        observer.close()

    assert path.stat().st_mode & 0o777 == 0o600
    sidecar = path.read_text(encoding="utf-8")
    assert "RAW_ARGUMENT_SENTINEL_MUST_NOT_ESCAPE" not in sidecar
    assert "RAW_ERROR_SENTINEL_MUST_NOT_ESCAPE" not in sidecar
    summary = opencode_mcp_observer.read_protocol_observations(path, expected_nonce=nonce)
    serialized = json.dumps(summary, sort_keys=True)

    assert summary["observer_status"] == "observed"
    assert summary["mcp_catalog_observation_status"] == "observed"
    assert summary["mcp_catalog_tool_names"] == [
        "cao_acknowledge_user_answers",
        "cao_ask_user",
        "cao_wait_for_user_answers",
    ]
    assert summary["mcp_catalog_unexpected_tool_count"] == 1
    assert tool_calls == {name: 1 for name in tool_calls}
    assert summary["mcp_tool_observation_status"] == "observed"
    assert summary["observed_interaction_tools"] == [
        "cao_acknowledge_user_answers",
        "cao_ask_user",
        "cao_wait_for_user_answers",
    ]
    assert [call["status"] for call in summary["mcp_tool_calls"]] == [
        "completed",
        "failed",
        "completed",
        "completed",
    ]
    assert summary["mcp_tool_calls"][-1]["tool"] == "other"
    assert "RAW_ARGUMENT_SENTINEL_MUST_NOT_ESCAPE" not in serialized
    assert "RAW_ERROR_SENTINEL_MUST_NOT_ESCAPE" not in serialized
    assert nonce not in serialized

    from test.live_provider import interaction_cli_journey as common
    from test.live_provider import opencode_interaction_journey as opencode

    receipt_fields = opencode._mcp_observer_receipt_fields(
        observation_path=path,
        expected_nonce=nonce,
        agent_tool_scope_exact=True,
    )
    assert receipt_fields["agent_tool_scope_exact"] is True
    assert receipt_fields["observed_interaction_tools"] == summary["observed_interaction_tools"]
    assert receipt_fields["mcp_tool_calls"] == summary["mcp_tool_calls"]

    inner_receipt = common._safe_provider_receipt({"provider": "opencode_cli", **receipt_fields})
    assert inner_receipt["agent_tool_scope_exact"] is True
    assert inner_receipt["mcp_observer_status"] == "observed"
    assert inner_receipt["mcp_catalog_observation_status"] == "observed"
    assert inner_receipt["mcp_tool_observation_status"] == "observed"
    assert inner_receipt["observed_interaction_tools"] == summary["observed_interaction_tools"]
    safe_inner_calls = [
        {key: call[key] for key in summary_call}
        for call, summary_call in zip(
            inner_receipt["mcp_tool_calls"],
            summary["mcp_tool_calls"],
            strict=True,
        )
    ]
    assert safe_inner_calls == summary["mcp_tool_calls"]
    assert all(call["error_code"] is None for call in inner_receipt["mcp_tool_calls"])
    assert "RAW_ARGUMENT_SENTINEL_MUST_NOT_ESCAPE" not in json.dumps(inner_receipt)
    assert "RAW_ERROR_SENTINEL_MUST_NOT_ESCAPE" not in json.dumps(inner_receipt)


@pytest.mark.offline_database
def test_error_result_is_failed_and_forwarded_unchanged(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from mcp import types

    catalog_result = types.ServerResult(root=types.ListToolsResult(tools=[]))
    error_result = types.ServerResult(
        root=types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text="RAW_RESULT_SENTINEL_MUST_NOT_ESCAPE",
                )
            ],
            isError=True,
        )
    )
    dispatch_counts = {"list": 0, "call": 0}

    async def list_handler(_request):
        dispatch_counts["list"] += 1
        return catalog_result

    async def call_handler(_request):
        dispatch_counts["call"] += 1
        return error_result

    server = SimpleNamespace(
        request_handlers={
            types.ListToolsRequest: list_handler,
            types.CallToolRequest: call_handler,
        }
    )
    path = tmp_path / "error-result.jsonl"
    observer = opencode_mcp_observer.install_protocol_observer(server, path, "1" * 32)

    async def dispatch():
        catalog = await server.request_handlers[types.ListToolsRequest](
            types.ListToolsRequest(method="tools/list")
        )
        request = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(
                name="cao_ask_user",
                arguments={"private": "RAW_ARGUMENT_SENTINEL_MUST_NOT_ESCAPE"},
            ),
        )
        result = await server.request_handlers[types.CallToolRequest](request)
        assert catalog is catalog_result
        assert result is error_result

    try:
        asyncio.run(dispatch())
    finally:
        observer.close()

    summary = opencode_mcp_observer.read_protocol_observations(path, expected_nonce="1" * 32)
    assert dispatch_counts == {"list": 1, "call": 1}
    assert summary["observer_status"] == "observed"
    assert summary["mcp_tool_calls"] == [
        {
            "server": "cao-mcp-server",
            "tool": "cao_ask_user",
            "status": "failed",
            "error_present": True,
            "result_present": True,
        }
    ]
    sidecar = path.read_text(encoding="utf-8")
    assert "RAW_RESULT_SENTINEL_MUST_NOT_ESCAPE" not in sidecar
    assert "RAW_ARGUMENT_SENTINEL_MUST_NOT_ESCAPE" not in sidecar


@pytest.mark.offline_database
def test_internal_list_refresh_is_suppressed_and_context_resets_after_error(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    from mcp import types

    catalog_result = types.ServerResult(root=types.ListToolsResult(tools=[]))
    list_calls = 0
    gate = asyncio.Event()
    server = SimpleNamespace(request_handlers={})

    async def list_handler(_request):
        nonlocal list_calls
        list_calls += 1
        return catalog_result

    async def call_handler(request):
        nested = await server.request_handlers[types.ListToolsRequest](
            types.ListToolsRequest(method="tools/list")
        )
        assert nested is catalog_result
        gate.set()
        await asyncio.sleep(0)
        raise RuntimeError("PRIVATE_FIXED_ERROR_SENTINEL")

    server.request_handlers.update(
        {
            types.ListToolsRequest: list_handler,
            types.CallToolRequest: call_handler,
        }
    )
    path = tmp_path / "context-observations.jsonl"
    observer = opencode_mcp_observer.install_protocol_observer(server, path, "2" * 32)
    handlers = server.request_handlers

    async def dispatch():
        async def independent_catalog_task():
            await gate.wait()
            return await handlers[types.ListToolsRequest](
                types.ListToolsRequest(method="tools/list")
            )

        independent = asyncio.create_task(independent_catalog_task())
        request = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(
                name="cao_ask_user",
                arguments={},
            ),
        )
        try:
            await handlers[types.CallToolRequest](request)
        except RuntimeError:
            pass
        else:
            raise AssertionError("call_handler_error_not_forwarded")
        await independent
        await handlers[types.ListToolsRequest](types.ListToolsRequest(method="tools/list"))

    try:
        asyncio.run(dispatch())
    finally:
        observer.close()

    summary = opencode_mcp_observer.read_protocol_observations(path, expected_nonce="2" * 32)
    assert list_calls == 3
    assert summary["mcp_catalog_observation_count"] == 2
    assert summary["mcp_tool_calls"] == [
        {
            "server": "cao-mcp-server",
            "tool": "cao_ask_user",
            "status": "failed",
            "error_present": True,
            "result_present": False,
        }
    ]
    assert "PRIVATE_FIXED_ERROR_SENTINEL" not in path.read_text(encoding="utf-8")


@pytest.mark.offline_database
def test_missing_or_wrong_nonce_observations_are_unobserved_not_successful(
    tmp_path: Path,
) -> None:
    missing = opencode_mcp_observer.read_protocol_observations(
        tmp_path / "missing.jsonl",
        expected_nonce="d" * 32,
    )
    assert missing["observer_status"] == "unobserved"
    assert missing["mcp_catalog_observation_status"] == "unobserved"
    assert missing["mcp_tool_observation_status"] == "unobserved"
    assert missing["mcp_tool_calls"] == []
    assert missing["observed_interaction_tools"] == []

    wrong = tmp_path / "wrong-nonce.jsonl"
    wrong.write_text(
        json.dumps(
            {
                "version": 1,
                "nonce": "e" * 32,
                "sequence": 1,
                "event": "observer_ready",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    wrong.chmod(0o600)
    wrong_summary = opencode_mcp_observer.read_protocol_observations(wrong, expected_nonce="d" * 32)
    assert wrong_summary["observer_status"] == "unobserved"
    assert wrong_summary["mcp_tool_calls"] == []


@pytest.mark.offline_database
def test_inner_receipt_does_not_infer_calls_from_scope_or_mcp_ready(tmp_path: Path) -> None:
    from test.live_provider import interaction_cli_journey as common
    from test.live_provider import opencode_interaction_journey as opencode

    raw = {
        "provider": "opencode_cli",
        "native_mcp_ready": True,
        **opencode._mcp_observer_receipt_fields(
            observation_path=tmp_path / "missing.jsonl",
            expected_nonce="f" * 32,
            agent_tool_scope_exact=True,
        ),
    }
    receipt = common._safe_provider_receipt(raw)

    assert receipt["agent_tool_scope_exact"] is True
    assert receipt["mcp_observer_status"] == "unobserved"
    assert receipt["mcp_catalog_observation_status"] == "unobserved"
    assert receipt["mcp_tool_observation_status"] == "unobserved"
    assert receipt["observed_interaction_tools"] == []
    assert receipt["mcp_tool_calls"] == []


@pytest.mark.offline_database
def test_unclosed_sidecar_is_incomplete_not_successful(tmp_path: Path) -> None:
    path = tmp_path / "unfinished.jsonl"
    observer = opencode_mcp_observer.ProtocolObserver(path, "b" * 32)
    observer.start_call("cao_ask_user")
    try:
        summary = opencode_mcp_observer.read_protocol_observations(path, expected_nonce="b" * 32)
    finally:
        observer.close()

    assert summary["observer_status"] == "incomplete"
    assert summary["mcp_tool_observation_status"] == "incomplete"
    assert summary["mcp_observer_shutdown_observed"] is False
    assert summary["mcp_tool_calls"] == [
        {
            "server": "cao-mcp-server",
            "tool": "cao_ask_user",
            "status": "in_progress",
            "error_present": False,
            "result_present": False,
        }
    ]


@pytest.mark.offline_database
def test_native_mcp_observer_binding_is_optional_and_paired(tmp_path: Path) -> None:
    from test.live_provider import opencode_interaction_journey as journey

    defaults = journey._native_mcp_server(
        cao_home=tmp_path / "cao",
        mcp_home=tmp_path / "mcp-home",
        mcp_xdg_data_home=tmp_path / "mcp-xdg",
        tmp_dir=tmp_path / "tmp",
        api_port=50123,
        terminal_id="oc123456789abc",
    )
    environment = defaults["environment"]
    assert "CAO_OPENCODE_MCP_OBSERVER" not in environment
    assert "CAO_OPENCODE_MCP_OBSERVER_PATH" not in environment
    assert "CAO_OPENCODE_MCP_OBSERVER_NONCE" not in environment

    path = tmp_path / "observations.jsonl"
    nonce = "a" * 32
    configured = journey._native_mcp_server(
        cao_home=tmp_path / "cao",
        mcp_home=tmp_path / "mcp-home",
        mcp_xdg_data_home=tmp_path / "mcp-xdg",
        tmp_dir=tmp_path / "tmp",
        api_port=50123,
        terminal_id="oc123456789abc",
        install_restart_barrier=True,
        protocol_observation_path=path,
        protocol_observation_nonce=nonce,
    )
    configured_environment = configured["environment"]
    assert configured_environment["CAO_OPENCODE_MCP_OBSERVER"] == "1"
    assert configured_environment["CAO_OPENCODE_MCP_OBSERVER_PATH"] == str(path)
    assert configured_environment["CAO_OPENCODE_MCP_OBSERVER_NONCE"] == nonce
    assert configured_environment["CAO_OPENCODE_MCP_SOURCE_ROOT"] == str(
        Path(journey.__file__).resolve().parents[2]
    )
    assert str(Path(journey.__file__).resolve().parent) in configured_environment["PYTHONPATH"]
    assert set(configured_environment) <= journey._ISOLATED_MCP_ENV_NAMES
    with pytest.raises(ValueError, match="mcp_observer_source_bound_hook_required"):
        journey._native_mcp_server(
            cao_home=tmp_path / "cao",
            mcp_home=tmp_path / "mcp-home",
            mcp_xdg_data_home=tmp_path / "mcp-xdg",
            tmp_dir=tmp_path / "tmp",
            api_port=50123,
            terminal_id="oc123456789abc",
            protocol_observation_path=path,
            protocol_observation_nonce=nonce,
        )
    with pytest.raises(ValueError, match="mcp_observer_binding_incomplete"):
        journey._native_mcp_server(
            cao_home=tmp_path / "cao",
            mcp_home=tmp_path / "mcp-home",
            mcp_xdg_data_home=tmp_path / "mcp-xdg",
            tmp_dir=tmp_path / "tmp",
            api_port=50123,
            terminal_id="oc123456789abc",
            protocol_observation_path=path,
        )


@pytest.mark.offline_database
@pytest.mark.parametrize("failure", ["fsync", "close", "both"])
def test_observer_io_failure_never_claims_complete_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    parent = tmp_path / f"private-{failure}"
    parent.mkdir(mode=0o700)
    path = parent / "dispatch.jsonl"
    observer = opencode_mcp_observer.ProtocolObserver(path, "e" * 32)
    observer.start_call("cao_ask_user")
    descriptor = observer._descriptor
    real_fsync = os.fsync
    real_close = os.close

    def failing_fsync(file_descriptor: int) -> None:
        if file_descriptor == descriptor:
            raise OSError("private-fsync-failure")
        real_fsync(file_descriptor)

    def failing_close(file_descriptor: int) -> None:
        if file_descriptor == descriptor:
            raise OSError("private-close-failure")
        real_close(file_descriptor)

    with monkeypatch.context() as patch:
        if failure in {"fsync", "both"}:
            patch.setattr(opencode_mcp_observer.os, "fsync", failing_fsync)
        if failure in {"close", "both"}:
            patch.setattr(opencode_mcp_observer.os, "close", failing_close)
        observer.close()

    if failure in {"close", "both"}:
        try:
            real_close(descriptor)
        except OSError:
            pass

    summary = opencode_mcp_observer.read_protocol_observations(path, expected_nonce="e" * 32)
    assert summary["observer_status"] == "incomplete"
    assert summary["mcp_observer_shutdown_observed"] is False
    assert not path.with_name(path.name + ".complete").exists()


def _load_opencode_sitecustomize(
    monkeypatch: pytest.MonkeyPatch,
    *,
    server: object,
    source_root: Path,
    observation_path: Path | None,
    nonce: str | None,
) -> None:
    sitecustomize_path = source_root / "test/live_provider/sitecustomize.py"
    monkeypatch.syspath_prepend(str(sitecustomize_path.parent))
    monkeypatch.setenv("CAO_SPEC023_MCP_OBSERVER", "0")
    monkeypatch.delenv("CAO_SPEC023_SOURCE_ROOT", raising=False)
    monkeypatch.setenv("CAO_OPENCODE_MCP_OBSERVER", "1")
    monkeypatch.setenv("CAO_OPENCODE_MCP_SOURCE_ROOT", str(source_root))
    if observation_path is None:
        monkeypatch.delenv("CAO_OPENCODE_MCP_OBSERVER_PATH", raising=False)
    else:
        monkeypatch.setenv("CAO_OPENCODE_MCP_OBSERVER_PATH", str(observation_path))
    if nonce is None:
        monkeypatch.delenv("CAO_OPENCODE_MCP_OBSERVER_NONCE", raising=False)
    else:
        monkeypatch.setenv("CAO_OPENCODE_MCP_OBSERVER_NONCE", nonce)
    monkeypatch.delenv("CAO_MCP_RESTART_BARRIER_DIR", raising=False)
    source_server = source_root / "src/cli_agent_orchestrator/mcp_server/server.py"
    monkeypatch.setitem(
        sys.modules,
        "__main__",
        SimpleNamespace(
            __spec__=SimpleNamespace(name="cli_agent_orchestrator.mcp_server.server"),
            __file__=str(source_server),
            mcp=server,
        ),
    )
    spec = importlib.util.spec_from_file_location("r2_opencode_sitecustomize", sitecustomize_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


@pytest.mark.offline_database
def test_source_bound_sitecustomize_observes_real_fastmcp_handlers_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastmcp import FastMCP
    from mcp import types

    source_root = Path(__file__).resolve().parents[2]
    private_root = tmp_path / "private-mcp"
    private_root.mkdir(mode=0o700)
    path = private_root / "dispatch.jsonl"
    nonce = "f" * 32
    server = FastMCP("source-bound-observer-contract")
    calls = {"run": 0, "tool": 0}

    @server.tool()
    async def cao_ask_user(value: str) -> dict[str, bool]:
        calls["tool"] += 1
        return {"ok": bool(value)}

    result_marker = object()
    original_run = FastMCP.run

    def fake_run(instance: object, *args: object, **kwargs: object) -> object:
        calls["run"] += 1
        assert instance is server

        async def dispatch() -> None:
            handlers = server._mcp_server.request_handlers
            catalog = await handlers[types.ListToolsRequest](
                types.ListToolsRequest(method="tools/list")
            )
            assert isinstance(catalog.root, types.ListToolsResult)
            result = await handlers[types.CallToolRequest](
                types.CallToolRequest(
                    method="tools/call",
                    params=types.CallToolRequestParams(
                        name="cao_ask_user", arguments={"value": "private-test-value"}
                    ),
                )
            )
            assert isinstance(result.root, types.CallToolResult)
            assert result.root.isError is False

        asyncio.run(dispatch())
        return result_marker

    monkeypatch.setattr(FastMCP, "run", fake_run)
    _load_opencode_sitecustomize(
        monkeypatch,
        server=server,
        source_root=source_root,
        observation_path=path,
        nonce=nonce,
    )
    try:
        assert server.run() is result_marker
    finally:
        FastMCP.run = original_run

    summary = opencode_mcp_observer.read_protocol_observations(path, expected_nonce=nonce)
    assert calls == {"run": 1, "tool": 1}
    assert summary["observer_status"] == "observed"
    assert summary["mcp_catalog_tool_names"] == ["cao_ask_user"]
    assert [call["tool"] for call in summary["mcp_tool_calls"]] == ["cao_ask_user"]


@pytest.mark.offline_database
def test_source_bound_sitecustomize_run_exception_keeps_trace_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastmcp import FastMCP
    from mcp import types

    source_root = Path(__file__).resolve().parents[2]
    private_root = tmp_path / "private-mcp-run-error"
    private_root.mkdir(mode=0o700)
    path = private_root / "dispatch.jsonl"
    nonce = "a" * 32
    server = FastMCP("source-bound-observer-run-error")
    calls = {"run": 0, "tool": 0}
    run_error = RuntimeError("private-run-failure")

    @server.tool()
    async def cao_ask_user(value: str) -> dict[str, bool]:
        calls["tool"] += 1
        return {"ok": bool(value)}

    original_run = FastMCP.run

    def fake_run(instance: object, *args: object, **kwargs: object) -> object:
        calls["run"] += 1
        assert instance is server

        async def dispatch() -> None:
            handlers = server._mcp_server.request_handlers
            catalog = await handlers[types.ListToolsRequest](
                types.ListToolsRequest(method="tools/list")
            )
            assert isinstance(catalog.root, types.ListToolsResult)
            result = await handlers[types.CallToolRequest](
                types.CallToolRequest(
                    method="tools/call",
                    params=types.CallToolRequestParams(
                        name="cao_ask_user", arguments={"value": "private-test-value"}
                    ),
                )
            )
            assert isinstance(result.root, types.CallToolResult)
            assert result.root.isError is False

        asyncio.run(dispatch())
        raise run_error

    monkeypatch.setattr(FastMCP, "run", fake_run)
    _load_opencode_sitecustomize(
        monkeypatch,
        server=server,
        source_root=source_root,
        observation_path=path,
        nonce=nonce,
    )
    try:
        with pytest.raises(RuntimeError) as caught:
            server.run()
    finally:
        FastMCP.run = original_run

    summary = opencode_mcp_observer.read_protocol_observations(path, expected_nonce=nonce)
    assert caught.value is run_error
    assert calls == {"run": 1, "tool": 1}
    assert summary["observer_status"] == "incomplete"
    assert summary["mcp_catalog_tool_names"] == ["cao_ask_user"]
    assert summary["mcp_tool_observation_status"] == "incomplete"
    assert summary["mcp_tool_calls"][0]["status"] == "completed"
    assert summary["mcp_observer_shutdown_observed"] is False
    assert not path.with_name(path.name + ".complete").exists()


@pytest.mark.offline_database
@pytest.mark.parametrize("missing", ["path", "nonce"])
def test_source_bound_sitecustomize_rejects_incomplete_observer_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    from fastmcp import FastMCP

    source_root = Path(__file__).resolve().parents[2]
    server = FastMCP("source-bound-observer-refusal")
    original_run = FastMCP.run
    calls = 0

    def fake_run(instance: object, *args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        return None

    path = tmp_path / "missing-binding" / "dispatch.jsonl"
    path.parent.mkdir(mode=0o700)
    monkeypatch.setattr(FastMCP, "run", fake_run)
    _load_opencode_sitecustomize(
        monkeypatch,
        server=server,
        source_root=source_root,
        observation_path=None if missing == "path" else path,
        nonce=None if missing == "nonce" else "9" * 32,
    )
    try:
        with pytest.raises(SystemExit):
            server.run()
    finally:
        FastMCP.run = original_run
    assert calls == 0
    assert not path.exists()


@pytest.mark.offline_database
def test_source_bound_sitecustomize_rejects_server_outside_source_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastmcp import FastMCP

    source_root = Path(__file__).resolve().parents[2]
    server = FastMCP("source-bound-observer-wrong-origin")
    original_run = FastMCP.run
    calls = 0

    def fake_run(instance: object, *args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        return None

    private_root = tmp_path / "wrong-source"
    private_root.mkdir(mode=0o700)
    wrong_server_file = private_root / "server.py"
    wrong_server_file.write_text("# not the bound CAOS source\n", encoding="utf-8")
    path = private_root / "dispatch.jsonl"
    monkeypatch.setattr(FastMCP, "run", fake_run)
    _load_opencode_sitecustomize(
        monkeypatch,
        server=server,
        source_root=source_root,
        observation_path=path,
        nonce="b" * 32,
    )
    main_module = sys.modules["__main__"]
    monkeypatch.setattr(main_module, "__file__", str(wrong_server_file))
    try:
        with pytest.raises(SystemExit):
            server.run()
    finally:
        FastMCP.run = original_run

    assert calls == 0
    assert not path.exists()
