"""Elastic worker provisioning and acknowledged completion tests."""

import asyncio
import time
from test.services.test_integration_008_elastic_compat import elastic_context, store  # noqa:F401
from unittest.mock import AsyncMock, Mock, patch

from cli_agent_orchestrator.mcp_server import server
from cli_agent_orchestrator.models.inbox import OrchestrationType
from cli_agent_orchestrator.services import terminal_service
from cli_agent_orchestrator.utils import orchestration


def test_assign_elastic_provisions_then_assigns(monkeypatch):
    response = Mock(status_code=202, ok=True)
    response.json.return_value = {
        "assignment_id": "elastic-1",
        "operation_key": "stable-key-1",
        "success": True,
        "worker_id": "deadbeef",
        "elastic": True,
        "state": "submitted",
    }
    with (
        patch.object(server, "_current_terminal_id", return_value="abc12345"),
        patch.object(server.requests, "post", return_value=response) as post,
        patch.object(server, "_assign_impl") as direct,
    ):
        result = asyncio.run(
            server.assign_elastic("developer", "Implement it", operation_key="stable-key-1")
        )
    assert result["success"] and result["assignment_id"] == "elastic-1"
    assert post.call_args.args[0].endswith("/terminals/abc12345/elastic-assignments")
    assert post.call_args.kwargs["json"] == {
        "operation_key": "stable-key-1",
        "agent_profile": "developer",
        "message": "Implement it",
    }
    direct.assert_not_called()


def test_assign_elastic_deferred_failure_reports_terminal_ended(monkeypatch):
    """Exercise the real elastic placement and deferred-session failure path."""
    monkeypatch.setenv("CAO_ELASTIC_BROKER_URL", "http://broker:9890")
    monkeypatch.setenv("CAO_ELASTIC_BROKER_TOKEN", "broker-token")
    monkeypatch.setenv(server.ADVERTISED_URL_ENV, "http://cao-supervisor:9889")
    monkeypatch.setenv("CAO_ELASTIC_CALLBACK_URL", "http://broker:9890")

    lease = Mock(status_code=200)
    lease.raise_for_status.return_value = None
    lease.json.return_value = {
        "worker_id": "deadbeef",
        "target_host": "cao-worker-deadbeef.ns.svc.cluster.local",
        "working_directory": "/home/cao/workspace/workers/deadbeef",
        "session_name": "cao-worker-deadbeef",
        "release_token": "release-token",
    }
    session = Mock(status_code=200)
    session.json.return_value = {
        "id": "def67890",
        "session_name": "cao-worker-deadbeef",
    }
    ok = Mock(status_code=200)
    ok.raise_for_status.return_value = None
    posts = []

    def post(url, *args, **kwargs):
        posts.append((url, kwargs))
        if url == "http://broker:9890/workers":
            return lease
        if url.endswith("/sessions"):
            return session
        return ok

    async def inline_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    async def exercise():
        # The fresh assignment endpoint has already durably placed this worker.
        # Exercise its owned deferred initialization failure and callback boundary.
        monkeypatch.setenv("CAO_ELASTIC_WORKER_ID", "deadbeef")
        monkeypatch.setenv("CAO_ELASTIC_RELEASE_TOKEN", "release-token")
        provider = AsyncMock()
        provider.initialize.side_effect = RuntimeError("provider startup failed")
        before_tasks = set(terminal_service._deferred_init_tasks)
        terminal_service._schedule_deferred_init(
            provider,
            "def67890",
            "Implement it",
            OrchestrationType.ASSIGN,
            None,
        )
        (task,) = set(terminal_service._deferred_init_tasks) - before_tasks
        await task

    with (
        patch.object(server, "_current_terminal_id", return_value="abc12345"),
        # _assign_impl/_send_message_impl moved to utils/orchestration, so they
        # resolve the caller through THAT module's name. server.py still has its
        # own imported reference, so both need patching.
        patch.object(orchestration, "_current_terminal_id", return_value="abc12345"),
        patch.object(server.requests, "get", return_value=Mock(status_code=200)),
        patch.object(server.requests, "post", side_effect=post),
        patch.object(terminal_service.asyncio, "to_thread", inline_to_thread),
        patch.object(
            terminal_service,
            "get_terminal_metadata",
            return_value={"caller_id": None, "tmux_session": "cao-worker-deadbeef"},
        ),
        patch.object(
            terminal_service,
            "get_session_env",
            return_value={
                "CAO_CALLBACK_URL": "http://broker:9890",
                "CAO_CALLBACK_TERMINAL_ID": "abc12345",
            },
        ),
        patch.object(terminal_service, "delete_terminal") as delete,
    ):
        asyncio.run(exercise())

    urls = [url for url, _ in posts]
    callback = next(
        kwargs for url, kwargs in posts if url.endswith("/terminals/abc12345/inbox/messages")
    )
    assert callback["headers"] == {
        "X-CAO-Worker-ID": "deadbeef",
        "X-CAO-Release-Token": "release-token",
    }
    assert "http://broker:9890/workers/deadbeef/terminal-ended" in urls
    terminal_ended = next(
        kwargs for url, kwargs in posts if url.endswith("/workers/deadbeef/terminal-ended")
    )
    assert terminal_ended["headers"] == {"X-CAO-Release-Token": "release-token"}
    delete.assert_called_once_with("def67890", registry=None)


def _lease_response():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "worker_id": "deadbeef",
        "target_host": "cao-worker-deadbeef.ns.svc.cluster.local",
        "working_directory": "/home/cao/workspace/workers/deadbeef",
        "session_name": "cao-worker-deadbeef",
        "release_token": "release-token",
    }
    return response


def test_assign_elastic_omits_provider_so_the_broker_default_wins(monkeypatch):
    response = Mock(status_code=202, ok=True)
    response.json.return_value = {
        "assignment_id": "elastic-1",
        "success": True,
        "state": "submitted",
    }
    with (
        patch.object(server, "_current_terminal_id", return_value="abc12345"),
        patch.object(server.requests, "post", return_value=response) as post,
    ):
        asyncio.run(
            server.assign_elastic("developer", "Implement it", operation_key="default-provider-key")
        )
    assert "provider" not in post.call_args.kwargs["json"]


def test_assign_elastic_forwards_an_explicit_provider(monkeypatch):
    response = Mock(status_code=202, ok=True)
    response.json.return_value = {
        "assignment_id": "elastic-1",
        "success": True,
        "state": "submitted",
    }
    with (
        patch.object(server, "_current_terminal_id", return_value="abc12345"),
        patch.object(server.requests, "post", return_value=response) as post,
    ):
        asyncio.run(
            server.assign_elastic(
                "developer",
                "Implement it",
                provider="claude_code",
                operation_key="explicit-provider-key",
            )
        )
    assert post.call_args.kwargs["json"]["provider"] == "claude_code"


def test_assign_elastic_warns_the_worker_not_to_speak_first(monkeypatch, elastic_context):
    from cli_agent_orchestrator.services import elastic_assignment_service as service

    _, remote = elastic_context
    payload = service.ElasticAssignmentRequest(
        operation_key="warning-key-1", agent_profile="developer", message="Implement it"
    )
    result = asyncio.run(service.assign("1234abcd", payload, owner="operator"))
    assert result["success"]
    sent = remote.call_args.kwargs["worker_message"]
    assert "finish all tools before final prose" in sent and "complete_assignment" in sent


def test_assign_elastic_releases_when_assignment_fails(monkeypatch, elastic_context):
    from cli_agent_orchestrator.services import elastic_assignment_service as service

    _, remote = elastic_context
    remote.return_value = {"success": False, "state": "refused"}
    with patch.object(service.requests, "delete", return_value=Mock(status_code=200)) as delete:
        payload = service.ElasticAssignmentRequest(
            operation_key="refused-key-1", agent_profile="developer", message="Implement it"
        )
        result = asyncio.run(service.assign("1234abcd", payload, owner="operator"))
    assert result["worker_released"] and delete.call_count == 1
    assert delete.call_args.args[0].endswith("/workers/worker-1")


def test_complete_assignment_releases_only_after_delivery(monkeypatch):
    monkeypatch.setenv("CAO_ELASTIC_WORKER_ID", "deadbeef")
    monkeypatch.setenv("CAO_ELASTIC_BROKER_URL", "http://broker:9890")
    monkeypatch.setenv("CAO_ELASTIC_RELEASE_TOKEN", "release-token")
    response = Mock()
    response.raise_for_status.return_value = None
    with (
        patch.object(server, "_send_message_impl", return_value={"success": True}),
        patch.object(server.requests, "post", return_value=response) as post,
    ):
        result = asyncio.run(server.complete_assignment("Done"))

    assert result["success"] is True
    assert result["release_scheduled"] is True
    assert post.call_args.args[0].endswith("/workers/deadbeef/complete")
    assert post.call_args.kwargs["headers"]["X-CAO-Release-Token"] == "release-token"


def test_complete_assignment_keeps_worker_when_delivery_fails(monkeypatch):
    monkeypatch.setenv("CAO_ELASTIC_WORKER_ID", "deadbeef")
    monkeypatch.setenv("CAO_ELASTIC_BROKER_URL", "http://broker:9890")
    monkeypatch.setenv("CAO_ELASTIC_RELEASE_TOKEN", "release-token")
    with (
        patch.object(server, "_send_message_impl", return_value={"success": False}),
        patch.object(server.requests, "post") as post,
    ):
        result = asyncio.run(server.complete_assignment("Done"))

    assert result["success"] is False
    post.assert_not_called()


# --- readiness: the caller waits on the Service, not on pod readiness --------
#
# The broker used to hold POST /workers open until the worker pod reported Ready,
# which cost the caller the worker's whole boot AND still handed back an address
# that was not yet routable (a Ready pod is not yet a Service with a published
# endpoint). The wait moved here, onto the address about to be used.


def test_wait_remote_ready_returns_on_the_first_healthy_answer():
    with patch.object(server.requests, "get", return_value=Mock(status_code=200)) as get:
        orchestration._wait_remote_ready("http://worker:9889", 5.0)

    assert get.call_count == 1
    assert get.call_args.args[0] == "http://worker:9889/health"


def test_wait_remote_ready_polls_through_a_converging_service():
    """A refused connection while endpoints propagate is expected, not fatal."""
    responses = [
        server.requests.RequestException("Connection refused"),
        Mock(status_code=503),
        Mock(status_code=200),
    ]
    with (
        patch.object(server.requests, "get", side_effect=responses) as get,
        patch.object(server.time, "sleep") as sleep,
    ):
        orchestration._wait_remote_ready("http://worker:9889", 5.0)

    assert get.call_count == 3
    # Sub-second polling: the gap being waited out is a second or two, so a 5s
    # backoff would spend the whole wait not looking.
    assert all(call.args[0] <= 0.5 for call in sleep.call_args_list)


def test_wait_remote_ready_raises_something_diagnosable_on_timeout():
    with (
        patch.object(
            server.requests, "get", side_effect=server.requests.RequestException("no route")
        ),
        patch.object(server.time, "sleep"),
    ):
        try:
            orchestration._wait_remote_ready("http://worker:9889", 0.0)
            raise AssertionError("expected a ValueError")
        except ValueError as exc:
            message = str(exc)

    assert "http://worker:9889" in message
    assert "no route" in message
    assert "NetworkPolicy" in message


def _remote_session_response():
    response = Mock(status_code=200)
    response.json.return_value = {
        "assignment_id": "fresh-1",
        "state": "submitted",
        "terminal_id": "def67890",
        "success": True,
    }
    return response


def test_assign_remote_does_not_wait_by_default(monkeypatch):
    """Plain `assign` to a long-running node must stay byte-identical.

    A target_host naming a static pod is either up or genuinely broken, so a
    caller that did not just create it should still fail in seconds.
    """
    monkeypatch.setenv(server.ADVERTISED_URL_ENV, "http://cao-supervisor:9889")
    with (
        patch.object(server.requests, "post", return_value=_remote_session_response()),
        patch.object(orchestration, "_wait_remote_ready") as wait,
    ):
        result = orchestration._assign_remote(
            agent_profile="developer",
            worker_message="Implement it",
            current_terminal_id="abc12345",
            target_host="cao-worker-0",
            working_directory=None,
            engine=None,
            model=None,
            use_worktree=False,
            operation_key="remote-readiness-key",
        )

    assert result["success"] is True
    wait.assert_not_called()


def test_assign_remote_waits_before_it_posts_the_task(monkeypatch):
    """Order matters: POST /sessions is not idempotent, so it gets one shot."""
    monkeypatch.setenv(server.ADVERTISED_URL_ENV, "http://cao-supervisor:9889")
    calls = []
    with (
        patch.object(
            server.requests,
            "post",
            side_effect=lambda *a, **k: (calls.append("post"), _remote_session_response())[1],
        ),
        patch.object(
            orchestration, "_wait_remote_ready", side_effect=lambda *a: calls.append("wait")
        ) as wait,
    ):
        orchestration._assign_remote(
            agent_profile="developer",
            worker_message="Implement it",
            current_terminal_id="abc12345",
            target_host="cao-worker-deadbeef.ns.svc.cluster.local",
            working_directory=None,
            engine=None,
            model=None,
            use_worktree=False,
            operation_key="remote-readiness-key",
            ready_wait_seconds=30.0,
        )

    assert calls == ["wait", "post"]
    assert wait.call_args.args == ("http://cao-worker-deadbeef.ns.svc.cluster.local:9889", 30.0)


def test_assign_elastic_asks_the_assignment_to_wait_for_its_new_worker(
    monkeypatch, elastic_context
):
    from cli_agent_orchestrator.services import elastic_assignment_service as service

    monkeypatch.delenv("CAO_ELASTIC_WORKER_READY_WAIT", raising=False)
    _, remote = elastic_context
    payload = service.ElasticAssignmentRequest(
        operation_key="wait-key-1", agent_profile="developer", message="Implement it"
    )
    assert asyncio.run(service.assign("1234abcd", payload, owner="operator"))["success"]
    assert remote.call_args.kwargs["ready_wait_seconds"] == 120.0


def test_elastic_ready_wait_is_tunable_and_survives_a_bad_value(monkeypatch):
    monkeypatch.setenv("CAO_ELASTIC_WORKER_READY_WAIT", "7.5")
    assert server._elastic_ready_wait() == 7.5
    monkeypatch.setenv("CAO_ELASTIC_WORKER_READY_WAIT", "-3")
    assert server._elastic_ready_wait() == 0.0
    monkeypatch.setenv("CAO_ELASTIC_WORKER_READY_WAIT", "soon")
    assert server._elastic_ready_wait() == 120.0


def test_assign_elastic_calls_overlap_instead_of_serialising(monkeypatch):
    def slow_post(*args, **kwargs):
        time.sleep(0.2)
        response = Mock(status_code=202, ok=True)
        response.json.return_value = {
            "assignment_id": kwargs["json"]["operation_key"],
            "success": True,
            "state": "submitted",
        }
        return response

    async def three():
        return await asyncio.gather(
            *(
                server.assign_elastic("developer", f"Task {i}", operation_key=f"fanout-key-{i}")
                for i in range(3)
            )
        )

    with (
        patch.object(server, "_current_terminal_id", return_value="abc12345"),
        patch.object(server.requests, "post", side_effect=slow_post),
    ):
        started = time.monotonic()
        results = asyncio.run(three())
        elapsed = time.monotonic() - started
    assert all(row["success"] for row in results) and elapsed < 0.6
