"""MCP work launch is an authenticated intent-only client of the HTTP seam."""

from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
from fastapi.testclient import TestClient

from cli_agent_orchestrator.api import main as api_main
from cli_agent_orchestrator.clients.work_repository import WorkConflict
from cli_agent_orchestrator.mcp_server import server, utils
from cli_agent_orchestrator.services.work_launch_gateway import build_durable_launch_gateway
from test.services.test_work_launch_runtime import (
    ProtectedFakeBackend,
    durable_counts,
    provision,
    trusted_setup,
)


def _tool_function(name):
    tool = getattr(server, name, None)
    assert tool is not None, f"missing MCP tool {name}"
    return getattr(tool, "fn", tool)


def _launch_args(**overrides):
    args = {
        "selection": "opaque",
        "agent_profile": "developer",
        "session_name": "cao-durable-launch",
        "message": "review this change",
        "allowed_tools": ["tool.read"],
    }
    args.update(overrides)
    return args


@pytest.fixture
def launch_transport(trusted_setup, monkeypatch):
    repository, principal, _, _, _, _ = trusted_setup
    provision(trusted_setup)
    backend = ProtectedFakeBackend()
    gateway = build_durable_launch_gateway(repository, backends={"test": backend})
    previous_gateway = getattr(api_main.app.state, "durable_launch_gateway", None)
    api_main.app.state.durable_launch_gateway = gateway
    monkeypatch.setattr(api_main, "principal_from_token", lambda token: principal)
    monkeypatch.setattr(utils, "get_local_bearer", lambda: "mcp-machine-token")

    api_client = TestClient(api_main.app, base_url="http://localhost")
    calls = []

    def local_post(url, *, json, headers=None, timeout=None):
        calls.append(
            {
                "path": urlsplit(url).path,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        assert timeout is not None and timeout > 0
        api_response = api_client.post(urlsplit(url).path, json=json, headers=headers)
        response = requests.Response()
        response.status_code = api_response.status_code
        response.headers.update(api_response.headers)
        response.url = url
        response._content = api_response.content
        return response

    monkeypatch.setattr(utils.requests, "post", local_post)
    try:
        yield SimpleNamespace(
            client=api_client,
            requests=calls,
            repository=repository,
            principal=principal,
            backend=backend,
        )
    finally:
        api_client.close()
        if previous_gateway is None:
            if hasattr(api_main.app.state, "durable_launch_gateway"):
                del api_main.app.state.durable_launch_gateway
        else:
            api_main.app.state.durable_launch_gateway = previous_gateway


@pytest.mark.asyncio
async def test_mcp_work_launch_registers_and_admits_only_through_http_gateway(launch_transport):
    tools = await server.mcp.list_tools()
    assert "work_launch" in {tool.name for tool in tools}

    result = await _tool_function("work_launch")(**_launch_args())

    assert result["ok"] is True
    assert result["receipt"]["generation"] == 1
    assert result["receipt"]["state"] == "queued"
    assert result["receipt"]["work_item_id"]
    assert result["receipt"]["attempt_id"]
    assert len(launch_transport.requests) == 1
    sent = launch_transport.requests[0]
    assert sent["path"] == "/work-launches"
    assert sent["json"] == _launch_args()
    assert sent["headers"] == {"Authorization": "Bearer mcp-machine-token"}
    assert durable_counts(launch_transport.repository) == (1, 1, 1, 1, 1)
    assert launch_transport.backend.effects == []
    with launch_transport.repository.connection() as connection:
        assert (
            connection.execute("SELECT count(*) FROM work_task_received_receipts").fetchone()[0]
            == 0
        )


@pytest.mark.asyncio
async def test_mcp_work_launch_foreign_selector_is_structured_and_has_no_effects(launch_transport):
    result = await _tool_function("work_launch")(**_launch_args(selection="someone-elses-selector"))

    assert result == {
        "ok": False,
        "status_code": 503,
        "error": {
            "code": "launch_context_unavailable",
            "message": "Trusted launch context is unavailable.",
            "retryable": False,
            "required_action": "provision_launch_context",
        },
    }
    assert durable_counts(launch_transport.repository) == (0, 0, 0, 0, 0)
    assert launch_transport.backend.preflights == []
    assert launch_transport.backend.effects == []


@pytest.mark.asyncio
async def test_mcp_work_launch_conflict_keeps_the_server_owned_recovery_action(
    launch_transport, monkeypatch
):
    """The full runtime→gateway→HTTP→MCP path cannot request a caller key."""
    gateway = api_main.app.state.durable_launch_gateway
    runtime = gateway._launch_runtime_provider._runtime

    def conflict(*args, **kwargs):
        raise WorkConflict("private durable conflict detail")

    monkeypatch.setattr(runtime._admission, "admit", conflict)

    result = await _tool_function("work_launch")(**_launch_args())

    assert result == {
        "ok": False,
        "status_code": 409,
        "error": {
            "code": "launch_idempotency_conflict",
            "message": "Launch operation conflicts with an existing server-owned identity.",
            "retryable": False,
            "required_action": "inspect_existing_launch",
        },
    }
    assert "private durable conflict detail" not in str(result)
    assert durable_counts(launch_transport.repository) == (0, 0, 0, 0, 0)
    assert launch_transport.backend.effects == []


def test_mcp_work_launch_sanitizes_unknown_upstream_error_detail():
    """An arbitrary HTTP error body is never an MCP launch error contract."""
    response = requests.Response()
    response.status_code = 500
    response._content = (
        b'{"detail":{"code":"private_failure","message":"token=secret",'
        b'"retryable":true,"required_action":"retry_everything"}}'
    )

    result = server._work_launch_http_error(requests.HTTPError(response=response))

    assert result == {
        "ok": False,
        "status_code": 500,
        "error": {
            "code": "launch_response_invalid",
            "message": "cao-server returned an invalid launch response.",
            "retryable": False,
            "required_action": "inspect_server_configuration",
        },
    }
    assert "token=secret" not in str(result)


@pytest.mark.asyncio
async def test_mcp_work_launch_treats_helper_value_error_as_nonretryable_response_failure(
    monkeypatch,
):
    """Only requests transport exceptions may invite retrying the same intent."""
    monkeypatch.setattr(
        utils,
        "post_body_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("private parse detail")),
    )

    result = await _tool_function("work_launch")(**_launch_args())

    assert result == {
        "ok": False,
        "status_code": None,
        "error": {
            "code": "launch_response_invalid",
            "message": "cao-server returned an invalid launch response.",
            "retryable": False,
            "required_action": "inspect_server_configuration",
        },
    }
    assert "private parse detail" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    (
        requests.exceptions.InvalidURL,
        requests.exceptions.InvalidSchema,
        requests.exceptions.MissingSchema,
    ),
)
async def test_mcp_work_launch_treats_invalid_http_configuration_as_nonretryable(
    monkeypatch, error_type
):
    """A configured endpoint error cannot truthfully ask callers to retry transport."""

    def invalid_configuration(*args, **kwargs):
        raise error_type("private configured endpoint detail")

    monkeypatch.setattr(utils, "post_body_json", invalid_configuration)

    result = await _tool_function("work_launch")(**_launch_args())

    assert result == {
        "ok": False,
        "status_code": None,
        "error": {
            "code": "launch_internal_error",
            "message": "Unable to process the launch request.",
            "retryable": False,
            "required_action": "inspect_server_configuration",
        },
    }
    assert "private configured endpoint detail" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type", (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
)
async def test_mcp_work_launch_keeps_connection_and_timeout_failures_retryable(
    monkeypatch, error_type
):
    """Reachable transport outages retain the R2 same-intent retry contract."""

    def transient_transport(*args, **kwargs):
        raise error_type("private transient detail")

    monkeypatch.setattr(utils, "post_body_json", transient_transport)

    result = await _tool_function("work_launch")(**_launch_args())

    assert result == {
        "ok": False,
        "status_code": None,
        "error": {
            "code": "launch_transport_unavailable",
            "message": "Could not reach cao-server to admit the launch.",
            "retryable": True,
            "required_action": "retry_same_intent",
        },
    }
    assert "private transient detail" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity", "expected_status", "expected_code"),
    [("absent", 401, "launch_identity_required"), ("false", 403, "launch_authority_denied")],
)
async def test_mcp_work_launch_rejects_absent_or_false_identity_before_effects(
    launch_transport, monkeypatch, identity, expected_status, expected_code
):
    if identity == "absent":
        monkeypatch.setattr(utils, "get_local_bearer", lambda: None)
    else:
        monkeypatch.setattr(api_main, "principal_from_token", lambda token: object())

    result = await _tool_function("work_launch")(**_launch_args())

    assert result["ok"] is False
    assert result["status_code"] == expected_status
    assert result["error"]["code"] == expected_code
    assert durable_counts(launch_transport.repository) == (0, 0, 0, 0, 0)
    assert launch_transport.backend.effects == []


@pytest.mark.asyncio
async def test_mcp_work_launch_rejects_ack_and_authority_fields_before_http(launch_transport):
    launch = _tool_function("work_launch")

    with pytest.raises(TypeError):
        await launch(**_launch_args(task_received=True, receiver_id="forged"))
    with pytest.raises(TypeError):
        await launch(**_launch_args(caller_id="forged", child_id="forged", continuation="forged"))

    assert launch_transport.requests == []
    assert durable_counts(launch_transport.repository) == (0, 0, 0, 0, 0)
