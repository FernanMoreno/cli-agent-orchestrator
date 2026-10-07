"""Workflow source clients preserve exact revisions and local authentication."""

import json
from unittest.mock import Mock, patch

from click.testing import CliRunner

from cli_agent_orchestrator.cli.commands.workflow import workflow


def response(payload):
    return Mock(status_code=200, ok=True, json=Mock(return_value=payload))


def test_create_from_stdin_sends_authenticated_exact_source():
    source = "INPUTS = {}\n# exact\n"
    with (
        patch(
            "cli_agent_orchestrator.cli.commands.workflow.requests.request",
            return_value=response({"name": "new"}),
        ) as request,
        patch(
            "cli_agent_orchestrator.cli.commands.workflow._work_query_headers",
            return_value={"Authorization": "Bearer local"},
        ),
    ):
        result = CliRunner().invoke(workflow, ["create", "new", "--source", "-"], input=source)
    assert result.exit_code == 0, result.output
    assert request.call_args.kwargs["json"] == {"name": "new", "content": source}
    assert request.call_args.kwargs["headers"] == {"Authorization": "Bearer local"}


def test_get_source_returns_exact_revision_and_auth():
    dto = {"name": "new", "content": "INPUTS = {}\n", "source_hash": "a" * 64}
    with (
        patch(
            "cli_agent_orchestrator.cli.commands.workflow.requests.get", return_value=response(dto)
        ) as request,
        patch(
            "cli_agent_orchestrator.cli.commands.workflow._work_query_headers",
            return_value={"Authorization": "Bearer local"},
        ),
    ):
        result = CliRunner().invoke(workflow, ["get", "new", "--source", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == dto
    assert request.call_args.args[0].endswith("/workflows/new/source")
    assert request.call_args.kwargs["headers"] == {"Authorization": "Bearer local"}


def test_validate_source_from_stdin_has_no_local_execution():
    source = "INPUTS = {}\nraise RuntimeError('never execute')\n"
    with patch(
        "cli_agent_orchestrator.cli.commands.workflow.requests.post",
        return_value=response({"status": "pass"}),
    ) as request:
        result = CliRunner().invoke(
            workflow, ["validate", "--source", "-", "--name", "new", "--json"], input=source
        )
    assert result.exit_code == 0, result.output
    assert request.call_args.kwargs["json"] == {"name": "new", "content": source}
