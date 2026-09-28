"""CLI projections for the API-owned durable Work contract."""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner

from cli_agent_orchestrator.cli.commands.workflow import workflow


def _response(status_code=200, body=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body if body is not None else {}
    return response


def _work_view():
    return {
        "schema_version": 1,
        "job_id": "job-1",
        "work_item_id": "work-1",
        "attempt_id": "attempt-1",
        "job_state": "running",
        "work_state": "running",
        "attempt_state": "sent",
        "turn_state": "input_sent",
        "process_state": "unknown",
        "revision": 3,
        "result_ref": "result-1",
        "cleanup_state": "pending",
        "required_action": "await_completion",
    }


def test_work_json_forwards_authorized_api_view_unchanged():
    """Removing the read projection or changing its API DTO breaks this command."""
    body = _work_view()
    with patch(
        "cli_agent_orchestrator.cli.commands.workflow.requests.get", return_value=_response(body=body)
    ) as get:
        result = CliRunner().invoke(workflow, ["work", "work-1", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == body
    assert get.call_args.args[0].endswith("/work-items/work-1")


def test_work_human_output_keeps_state_levels_and_action_distinct():
    """Collapsing work, attempt, turn or process state loses operator context."""
    with patch(
        "cli_agent_orchestrator.cli.commands.workflow.requests.get",
        return_value=_response(body=_work_view()),
    ):
        result = CliRunner().invoke(workflow, ["work", "work-1"])

    assert result.exit_code == 0, result.output
    for line in (
        "Work item:      work-1",
        "Attempt:        attempt-1",
        "Work state:     running",
        "Attempt state:  sent",
        "Turn state:     input_sent",
        "Process state:  unknown",
        "Result:         result-1",
        "Required action: await_completion",
    ):
        assert line in result.output


def test_work_events_forwards_bounded_cursor_and_preserves_page_unchanged():
    """Changing the endpoint, cursor, page, or bearer breaks the read projection."""
    body = {
        "schema_version": 1,
        "events": [
            {
                "schema_version": 1,
                "event_id": "event-5",
                "job_id": "job-1",
                "work_item_id": "work-1",
                "attempt_id": "attempt-1",
                "sequence": 5,
                "event_type": "attempt_sent",
                "actor_id": "operator-1",
                "occurred_at": "2026-09-24T10:00:00Z",
                "metadata": {"turn": "turn-1"},
            }
        ],
        "next_cursor": 5,
        "high_water": 8,
        "gaps": [{"from_sequence": 1, "through_sequence": 4}],
    }
    with (
        patch(
            "cli_agent_orchestrator.cli.commands.workflow.requests.get",
            return_value=_response(body=body),
        ) as get,
        patch(
            "cli_agent_orchestrator.cli.commands.workflow.get_local_bearer",
            return_value="test-token",
            create=True,
        ),
    ):
        result = CliRunner().invoke(
            workflow,
            ["work-events", "job-1", "--after-sequence", "4", "--limit", "30", "--json"],
        )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == body
    assert get.call_args.args[0].endswith("/jobs/job-1/events")
    assert get.call_args.kwargs["params"] == {"after_sequence": 4, "limit": 30}
    assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer test-token"}


def test_work_events_human_output_keeps_cursor_high_water_and_gaps():
    """Dropping retention metadata makes a partial event history look complete."""
    body = {
        "schema_version": 1,
        "events": [],
        "next_cursor": 5,
        "high_water": 8,
        "gaps": [{"from_sequence": 1, "through_sequence": 4}],
    }
    with patch(
        "cli_agent_orchestrator.cli.commands.workflow.requests.get", return_value=_response(body=body)
    ):
        result = CliRunner().invoke(workflow, ["work-events", "job-1"])

    assert result.exit_code == 0, result.output
    assert "Next cursor: 5" in result.output
    assert "High-water:  8" in result.output
    assert "Gaps:" in result.output
    assert "1 through 4" in result.output


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["work-events", "job-1", "--after-sequence", "-1"], "Invalid value"),
        (["work-events", "job-1", "--limit", "0"], "Invalid value"),
        (["work-events", "job-1", "--limit", "1001"], "Invalid value"),
    ],
)
def test_work_events_rejects_invalid_cursor_or_limit_without_request(argv, expected):
    """Invalid paging never reaches the API boundary."""
    with patch("cli_agent_orchestrator.cli.commands.workflow.requests.get") as get:
        result = CliRunner().invoke(workflow, argv)

    assert result.exit_code == 2
    assert expected in result.output
    get.assert_not_called()


@pytest.mark.parametrize(
    "argv",
    [
        ["work", "a?x=1", "--json"],
        ["work-events", "a?x=1", "--json"],
    ],
)
def test_work_query_rejects_nonidentity_target_before_http_request(argv):
    """URL syntax in an ID must not retarget an authenticated API query."""
    with patch(
        "cli_agent_orchestrator.cli.commands.workflow.requests.get",
        return_value=_response(body={}),
    ) as get:
        result = CliRunner().invoke(workflow, argv)

    assert get.call_count == 0
    assert result.exit_code != 0
    assert "invalid work identifier" in result.output.lower()


@pytest.mark.parametrize("status_code", [401, 403, 404, 409, 503, 500])
def test_work_api_failure_is_bounded_and_never_reflects_response_or_token(status_code):
    """An API failure remains a bounded CLI error, never a diagnostic or credential echo."""
    secret = "test-token"
    with (
        patch(
            "cli_agent_orchestrator.cli.commands.workflow.requests.get",
            return_value=_response(status_code, {"detail": f"server saw {secret}"}),
        ),
        patch(
            "cli_agent_orchestrator.cli.commands.workflow.get_local_bearer",
            return_value=secret,
            create=True,
        ),
    ):
        result = CliRunner().invoke(workflow, ["work", "work-1"])

    assert result.exit_code != 0
    assert "work query" in result.output.lower()
    assert secret not in result.output
    assert "server saw" not in result.output


def test_work_transport_and_response_errors_are_bounded_without_token_leakage():
    """Network and malformed-response diagnostics do not leak bearer material."""
    secret = "test-token"
    scenarios = (
        requests.exceptions.ConnectionError(f"connection failed with {secret}"),
        ValueError(f"invalid response containing {secret}"),
    )
    for failure in scenarios:
        response = _response()
        if isinstance(failure, ValueError):
            response.json.side_effect = failure
            get = patch(
                "cli_agent_orchestrator.cli.commands.workflow.requests.get", return_value=response
            )
        else:
            get = patch("cli_agent_orchestrator.cli.commands.workflow.requests.get", side_effect=failure)
        with (
            get,
            patch(
                "cli_agent_orchestrator.cli.commands.workflow.get_local_bearer",
                return_value=secret,
                create=True,
            ),
        ):
            result = CliRunner().invoke(workflow, ["work", "work-1"])
        assert result.exit_code != 0
        assert "could not reach cao-server" in result.output or "invalid response" in result.output
        assert secret not in result.output
