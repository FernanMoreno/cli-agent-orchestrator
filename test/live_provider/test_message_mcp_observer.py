"""Offline tests for the private Spec023 production-dispatch observer."""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import os
import stat
from pathlib import Path

import pytest
from fastmcp.tools.tool import ToolResult
from message_mcp_observer import (
    OBSERVER_AUDIT_PATH_ENV,
    OBSERVER_ENABLE_ENV,
    ObserverSetupError,
    install_message_observer,
    install_message_observer_from_env,
)

pytestmark = pytest.mark.offline_database

BODY_SENTINEL = "SPEC023_OBSERVER_BODY_SENTINEL café 日本語"
RESULT_SENTINEL = "SPEC023_OBSERVER_RESULT_SENTINEL"
ERROR_SENTINEL = "SPEC023_OBSERVER_ERROR_SENTINEL"
AUTH_SENTINEL = "SPEC023_OBSERVER_AUTH_SENTINEL"
TOOL_SENTINEL = "SPEC023_OBSERVER_TOOL_SENTINEL"


class FakeServer:
    """A call surface only; its server entrypoint must never run in these tests."""

    name = "offline-fake"

    def __init__(self, call_tool):
        self.call_tool = call_tool
        self.run_called = False

    def run(self, *args, **kwargs):
        self.run_called = True
        raise AssertionError("server_entrypoint_must_not_run")


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def _audit_records(path: Path) -> list[dict[str, str]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_observer_delegates_allowed_dispatch_once_with_identical_call_and_result(
    tmp_path: Path,
) -> None:
    parent = _private_directory(tmp_path / "private")
    audit_path = parent / "observer.jsonl"
    arguments = {"message": BODY_SENTINEL, "auth": AUTH_SENTINEL}
    version = object()
    task_meta = object()
    result = object()
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def original(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    server = FakeServer(original)
    handle = install_message_observer(server, audit_path)
    try:
        returned = asyncio.run(
            server.call_tool(
                "send_message",
                arguments,
                version=version,
                task_meta=task_meta,
            )
        )
        records = _audit_records(audit_path)
    finally:
        handle.close()

    assert returned is result
    assert len(calls) == 1
    delegated_args, delegated_kwargs = calls[0]
    assert delegated_args == ("send_message", arguments)
    assert delegated_args[1] is arguments
    assert delegated_kwargs == {"version": version, "task_meta": task_meta}
    assert delegated_kwargs["version"] is version
    assert delegated_kwargs["task_meta"] is task_meta
    assert records == [{"tool": "send_message", "status": "completed", "outcome": "unknown"}]
    assert not server.run_called
    assert server.call_tool is original
    assert not handle.audit_write_failed


def test_observer_audits_ordinary_same_server_reentry(
    tmp_path: Path,
) -> None:
    parent = _private_directory(tmp_path / "private")
    audit_path = parent / "observer.jsonl"
    outer_arguments = {"message": "outer"}
    nested_arguments = {"message": "nested"}
    outer_result = object()
    nested_result = object()
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    server_holder: dict[str, FakeServer] = {}

    async def original(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            nested_returned = await server_holder["server"].call_tool(
                "send_message", nested_arguments
            )
            assert nested_returned is nested_result
            return outer_result
        return nested_result

    server = FakeServer(original)
    server_holder["server"] = server
    handle = install_message_observer(server, audit_path)
    try:
        returned = asyncio.run(server.call_tool("send_message", outer_arguments))
        records = _audit_records(audit_path)
    finally:
        handle.close()

    assert returned is outer_result
    assert calls == [
        (("send_message", outer_arguments), {}),
        (("send_message", nested_arguments), {}),
    ]
    assert records == [
        {"tool": "send_message", "status": "completed", "outcome": "unknown"},
        {"tool": "send_message", "status": "completed", "outcome": "unknown"},
    ]
    assert server.call_tool is original
    assert not server.run_called
    assert not handle.audit_write_failed


def test_observer_rejects_unknown_tool_before_delegate_with_fixed_record(
    tmp_path: Path,
) -> None:
    parent = _private_directory(tmp_path / "private")
    audit_path = parent / "observer.jsonl"
    calls: list[tuple[object, ...]] = []

    async def original(*args, **kwargs):
        calls.append(args)
        return object()

    server = FakeServer(original)
    handle = install_message_observer(server, audit_path)
    try:
        with pytest.raises(PermissionError, match="^tool_not_allowed$") as raised:
            asyncio.run(server.call_tool(TOOL_SENTINEL, {"prompt": BODY_SENTINEL}))
        records = _audit_records(audit_path)
    finally:
        handle.close()

    assert str(raised.value) == "tool_not_allowed"
    assert calls == []
    assert records == [{"tool": "other", "status": "rejected", "outcome": "unknown"}]
    assert not server.run_called


def test_observer_preserves_original_exception_and_never_logs_its_text(
    tmp_path: Path,
) -> None:
    parent = _private_directory(tmp_path / "private")
    audit_path = parent / "observer.jsonl"
    original_error = RuntimeError(ERROR_SENTINEL)

    async def original(name, arguments):
        assert name == "send_message"
        assert arguments["message"] == BODY_SENTINEL
        assert arguments["auth"] == AUTH_SENTINEL
        raise original_error

    server = FakeServer(original)
    handle = install_message_observer(server, audit_path)
    try:
        with pytest.raises(RuntimeError) as raised:
            asyncio.run(
                server.call_tool(
                    "send_message",
                    {"message": BODY_SENTINEL, "auth": AUTH_SENTINEL},
                )
            )
        audit_bytes = audit_path.read_bytes()
    finally:
        handle.close()

    assert raised.value is original_error
    assert _audit_records(audit_path) == [
        {"tool": "send_message", "status": "failed", "outcome": "unknown"}
    ]
    assert not server.run_called
    for sentinel in (BODY_SENTINEL, AUTH_SENTINEL, ERROR_SENTINEL, RESULT_SENTINEL):
        assert sentinel.encode("utf-8") not in audit_bytes


@pytest.mark.parametrize(
    ("success", "outcome"),
    [(False, "failure"), (True, "success")],
)
def test_observer_records_logical_outcome_without_persisting_returned_error(
    tmp_path: Path, success: bool, outcome: str
) -> None:
    parent = _private_directory(tmp_path / "private")
    audit_path = parent / "observer.jsonl"
    result = ToolResult(structured_content={"success": success, "error": ERROR_SENTINEL})

    async def original(*_args, **_kwargs):
        return result

    server = FakeServer(original)
    handle = install_message_observer(server, audit_path)
    try:
        returned = asyncio.run(server.call_tool("send_message", {"message": BODY_SENTINEL}))
        audit_bytes = audit_path.read_bytes()
    finally:
        handle.close()

    assert returned is result
    assert _audit_records(audit_path) == [
        {"tool": "send_message", "status": "completed", "outcome": outcome}
    ]
    assert ERROR_SENTINEL.encode("utf-8") not in audit_bytes


def test_context_guard_resets_after_original_error(tmp_path: Path) -> None:
    parent = _private_directory(tmp_path / "private")
    audit_path = parent / "observer.jsonl"
    original_error = RuntimeError(ERROR_SENTINEL)
    result = object()
    call_count = 0

    async def original(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise original_error
        return result

    server = FakeServer(original)
    handle = install_message_observer(server, audit_path)
    try:
        with pytest.raises(RuntimeError) as raised:
            asyncio.run(server.call_tool("send_message", {"message": BODY_SENTINEL}))
        returned = asyncio.run(server.call_tool("send_message", {"message": BODY_SENTINEL}))
        records = _audit_records(audit_path)
    finally:
        handle.close()

    assert raised.value is original_error
    assert returned is result
    assert call_count == 2
    assert records == [
        {"tool": "send_message", "status": "failed", "outcome": "unknown"},
        {"tool": "send_message", "status": "completed", "outcome": "unknown"},
    ]


def test_observer_audit_is_fixed_allowlisted_and_owner_only(tmp_path: Path) -> None:
    parent = _private_directory(tmp_path / "private")
    audit_path = parent / "observer.jsonl"
    result = {"success": True, "private": RESULT_SENTINEL, "body": BODY_SENTINEL}

    async def original(*args, **kwargs):
        return result

    server = FakeServer(original)
    handle = install_message_observer(server, audit_path)
    try:
        assert asyncio.run(server.call_tool("send_message", {"message": BODY_SENTINEL})) is result
        audit_bytes = audit_path.read_bytes()
    finally:
        handle.close()

    audit_stat = audit_path.stat(follow_symlinks=False)
    assert stat.S_ISREG(audit_stat.st_mode)
    assert audit_stat.st_uid == os.geteuid()
    assert stat.S_IMODE(audit_stat.st_mode) == 0o600
    assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    assert _audit_records(audit_path) == [
        {"tool": "send_message", "status": "completed", "outcome": "success"}
    ]
    assert set(_audit_records(audit_path)[0]) == {"tool", "status", "outcome"}
    for sentinel in (BODY_SENTINEL, RESULT_SENTINEL, ERROR_SENTINEL, AUTH_SENTINEL):
        assert sentinel.encode("utf-8") not in audit_bytes
    assert not server.run_called


@pytest.mark.parametrize(
    ("path_factory", "error_code"),
    [
        (lambda root: Path("relative-observer.jsonl"), "observer_audit_path_unsafe"),
        (
            lambda root: root / "missing" / "observer.jsonl",
            "observer_audit_path_unsafe",
        ),
    ],
)
def test_observer_rejects_relative_or_missing_parent_without_running_server(
    tmp_path: Path, path_factory, error_code: str
) -> None:
    private = _private_directory(tmp_path / "private")

    async def original(*args, **kwargs):
        return None

    server = FakeServer(original)
    with pytest.raises(ObserverSetupError, match=f"^{error_code}$"):
        install_message_observer(server, path_factory(private))
    assert not server.run_called
    assert not (private / "missing" / "observer.jsonl").exists()


def test_observer_rejects_non_private_parent_and_symlink_components(
    tmp_path: Path,
) -> None:
    unsafe_parent = tmp_path / "not-private"
    unsafe_parent.mkdir(mode=0o755)
    unsafe_parent.chmod(0o755)
    private = _private_directory(tmp_path / "private")
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(private, target_is_directory=True)

    async def original(*args, **kwargs):
        return None

    server_a = FakeServer(original)
    with pytest.raises(ObserverSetupError, match="^observer_audit_path_unsafe$"):
        install_message_observer(server_a, unsafe_parent / "audit.jsonl")
    assert not (unsafe_parent / "audit.jsonl").exists()
    assert not server_a.run_called

    server_b = FakeServer(original)
    with pytest.raises(ObserverSetupError, match="^observer_audit_path_unsafe$"):
        install_message_observer(server_b, linked_parent / "audit.jsonl")
    assert not (private / "audit.jsonl").exists()
    assert not server_b.run_called


def test_observer_refuses_existing_file_and_final_symlink_without_modifying_targets(
    tmp_path: Path,
) -> None:
    parent = _private_directory(tmp_path / "private")
    existing = parent / "existing.jsonl"
    existing.write_text("existing-private-content", encoding="utf-8")
    target = parent / "target.jsonl"
    target.write_text("target-private-content", encoding="utf-8")
    linked = parent / "linked.jsonl"
    linked.symlink_to(target)

    async def original(*args, **kwargs):
        return None

    for audit_path in (existing, linked):
        server = FakeServer(original)
        with pytest.raises(ObserverSetupError, match="^observer_audit_path_unsafe$"):
            install_message_observer(server, audit_path)
        assert not server.run_called
    assert existing.read_text(encoding="utf-8") == "existing-private-content"
    assert target.read_text(encoding="utf-8") == "target-private-content"
    assert linked.is_symlink()


@pytest.mark.parametrize(
    ("environment", "error_code"),
    [
        ({}, "observer_not_enabled"),
        ({OBSERVER_ENABLE_ENV: "0"}, "observer_not_enabled"),
        ({OBSERVER_ENABLE_ENV: "true"}, "observer_environment_invalid"),
        ({OBSERVER_ENABLE_ENV: "1"}, "observer_audit_path_required"),
        (
            {
                OBSERVER_ENABLE_ENV: "1",
                OBSERVER_AUDIT_PATH_ENV: "relative-audit.jsonl",
            },
            "observer_audit_path_unsafe",
        ),
    ],
)
def test_environment_gate_and_audit_path_fail_before_server_entrypoint(
    tmp_path: Path, environment: dict[str, str], error_code: str
) -> None:
    async def original(*args, **kwargs):
        return None

    server = FakeServer(original)
    with pytest.raises(ObserverSetupError, match=f"^{error_code}$"):
        install_message_observer_from_env(server, environment)

    assert not server.run_called
    assert not (tmp_path / "must-not-be-created.jsonl").exists()


def test_environment_install_requires_explicit_opt_in_and_secure_audit_parent(
    tmp_path: Path,
) -> None:
    parent = _private_directory(tmp_path / "private")
    audit_path = parent / "observer.jsonl"

    async def original(*args, **kwargs):
        return None

    server = FakeServer(original)
    handle = install_message_observer_from_env(
        server,
        {
            OBSERVER_ENABLE_ENV: "1",
            OBSERVER_AUDIT_PATH_ENV: str(audit_path),
        },
    )
    try:
        assert callable(server.call_tool)
        assert audit_path.exists()
    finally:
        handle.close()

    assert not server.run_called
    assert server.call_tool is original


def test_installed_fastmcp_request_handler_dynamically_uses_public_call_tool(
    tmp_path: Path,
) -> None:
    from fastmcp.server.mixins.mcp_operations import MCPOperationsMixin
    from fastmcp.server.server import FastMCP

    handler_tree = ast.parse(inspect.cleandoc(inspect.getsource(MCPOperationsMixin._call_tool_mcp)))
    public_method_tree = ast.parse(inspect.cleandoc(inspect.getsource(FastMCP.call_tool)))
    dynamic_dispatch = [
        node
        for node in ast.walk(handler_tree)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and isinstance(node.value.func.value, ast.Name)
        and node.value.func.value.id == "server"
        and node.value.func.attr == "call_tool"
    ]
    assert dynamic_dispatch
    assert len(dynamic_dispatch) == 1
    dispatch_call = dynamic_dispatch[0].value
    assert isinstance(dispatch_call.func.value, ast.Name)
    assert dispatch_call.func.value.id == "server"
    assert [
        argument.id if isinstance(argument, ast.Name) else None for argument in dispatch_call.args
    ] == ["key", "arguments"]
    # MCP controls the protocol key and argument map. The pinned request
    # handler creates these internal kwargs and never forwards run_middleware
    # or arbitrary request metadata to the FastMCP method.
    assert {keyword.arg for keyword in dispatch_call.keywords} == {
        "version",
        "task_meta",
    }
    middleware_reentry = [
        node
        for node in ast.walk(public_method_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
        and node.func.attr == "call_tool"
        and any(
            keyword.arg == "run_middleware"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is False
            for keyword in node.keywords
        )
    ]
    assert middleware_reentry

    class Result:
        def to_mcp_result(self):
            return wire_result

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    executed: list[tuple[object, object]] = []
    arguments = {"message": BODY_SENTINEL}
    wire_result = object()

    class FakeFastMCPDispatch(FakeServer):
        _mcp_server = object()

    server_holder: dict[str, FakeFastMCPDispatch] = {}

    async def original(*args, **kwargs):
        calls.append((args, kwargs))
        name, tool_arguments = args
        if kwargs.get("run_middleware", True):
            return await server_holder["server"].call_tool(
                name,
                tool_arguments,
                version=kwargs.get("version"),
                run_middleware=False,
                task_meta=kwargs.get("task_meta"),
            )
        executed.append((name, tool_arguments))
        return Result()

    server = FakeFastMCPDispatch(original)
    server_holder["server"] = server
    parent = _private_directory(tmp_path / "sdk-dispatch")
    audit_path = parent / "observer.jsonl"
    handle = install_message_observer(server, audit_path)
    try:
        returned = asyncio.run(MCPOperationsMixin._call_tool_mcp(server, "send_message", arguments))
        records = _audit_records(audit_path)
    finally:
        handle.close()

    assert returned is wire_result
    assert len(calls) == 2
    delegated_args, delegated_kwargs = calls[0]
    assert delegated_args == ("send_message", arguments)
    assert delegated_args[1] is arguments
    assert delegated_kwargs == {"version": None, "task_meta": None}
    nested_args, nested_kwargs = calls[1]
    assert nested_args == ("send_message", arguments)
    assert nested_args[1] is arguments
    assert nested_kwargs == {
        "version": None,
        "run_middleware": False,
        "task_meta": None,
    }
    assert executed == [("send_message", arguments)]
    assert records == [{"tool": "send_message", "status": "completed", "outcome": "unknown"}]
    assert not server.run_called


def test_observer_source_has_no_process_network_or_database_call_sites() -> None:
    source_path = Path(__file__).with_name("message_mcp_observer.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    forbidden_modules = {"subprocess", "requests", "httpx", "sqlalchemy"}
    forbidden_calls = {"Popen", "create_engine", "connect", "run"}

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported = (
                [alias.name.split(".", maxsplit=1)[0] for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module.split(".", maxsplit=1)[0] if node.module else ""]
            )
            assert not forbidden_modules.intersection(imported)
        if isinstance(node, ast.Call):
            call_name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else (node.func.id if isinstance(node.func, ast.Name) else "")
            )
            assert call_name not in forbidden_calls
