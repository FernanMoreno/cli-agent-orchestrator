"""Workflow event streaming uses the same local credential as its REST reads."""

from unittest.mock import patch

from cli_agent_orchestrator.cli.commands import workflow


def test_event_stream_preserves_auth_and_resume_headers():
    with (
        patch.object(workflow, "get_local_bearer", return_value="synthetic-local-token"),
        patch.object(workflow.requests, "get") as get,
    ):
        workflow._open_events_stream("test-run", 7)
    headers = get.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer synthetic-local-token"
    assert headers["Accept"] == "text/event-stream"
    assert headers["Last-Event-ID"] == "7"
    assert get.call_args.kwargs["params"] == {"after_seq": 7}
