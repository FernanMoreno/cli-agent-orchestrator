import asyncio
from test.services.test_integration_008_prepared_plans import (  # noqa: F401
    context,
    plan_context,
    prepare,
)
from types import SimpleNamespace

import psycopg
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from cli_agent_orchestrator.api import main
from cli_agent_orchestrator.clients import work_repository
from cli_agent_orchestrator.services import workflow_journal, workflow_spec_service


@pytest.mark.parametrize("submit", [False, True])
def test_actual_prepared_store_fault_transport_is503_without_row(plan_context, monkeypatch, submit):
    plans, subject, directory, context = plan_context
    monkeypatch.setattr(workflow_spec_service, "WORKFLOW_SPEC_DIR", directory)
    prepared = prepare(plan_context)
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(state=SimpleNamespace(work_workflow_origins=plans.origins)),
        }
    )
    body = main.WorkflowRunRequest(
        name_or_path="wf",
        prepared_id=prepared["prepared_id"],
        expected_plan_id=prepared["plan_id"],
        run_id="fault-reviewed",
    )

    failed_connections = []

    def fail(_target, readonly=False):
        failed_connections.append(readonly)
        raise psycopg.OperationalError("PostgreSQL unavailable")

    monkeypatch.setattr(work_repository, "connect", fail)
    try:
        with pytest.raises(HTTPException) as refused:
            asyncio.run(main._start_scoped_workflow(body, request, subject, submit=submit))
        assert refused.value.status_code == 503
        assert refused.value.detail["kind"] == "private_plan_schema_unavailable"
        assert "PostgreSQL unavailable" not in str(refused.value.detail)
        assert failed_connections == [True]
    finally:
        assert workflow_journal.get_run("fault-reviewed") is None
