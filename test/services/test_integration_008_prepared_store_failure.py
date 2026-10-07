import asyncio
import sqlite3
from test.services.test_integration_008_prepared_plans import (  # noqa: F401
    context,
    plan_context,
    prepare,
)
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from cli_agent_orchestrator.api import main
from cli_agent_orchestrator.services import approval_store, workflow_journal


@pytest.mark.parametrize("submit", [False, True])
def test_actual_prepared_store_fault_transport_is503_without_row(plan_context, monkeypatch, submit):
    plans, subject, directory, context = plan_context
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

    def fail():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(approval_store, "_connect", fail)
    try:
        with pytest.raises(HTTPException) as refused:
            asyncio.run(main._start_scoped_workflow(body, request, subject, submit=submit))
        assert refused.value.status_code == 503
        assert refused.value.detail["kind"] == "plan_approval_unavailable"
        assert "database is locked" not in str(refused.value.detail)
    finally:
        assert workflow_journal.get_run("fault-reviewed") is None
