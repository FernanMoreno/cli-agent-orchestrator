"""Store operations close native handles on success and preserve failure policies."""

import pytest

from cli_agent_orchestrator.clients.postgres import Connection, OperationalError
from cli_agent_orchestrator.services import (
    approval_store,
    workflow_journal,
    workflow_service,
    workflow_spec_service,
)


@pytest.fixture
def stores(tmp_path):
    workflow_journal.insert_run("generation-run", "workflow", "{}", "{}", "running", "now")
    scan = tmp_path / "specs"
    scan.mkdir()
    return {
        "grant": lambda: approval_store.grant("approval", "local"),
        "approval_state": lambda: approval_store.approval_state("missing"),
        "get_approval": lambda: approval_store.get_approval("missing"),
        "list_workflows": lambda: workflow_spec_service.list_workflows(str(scan)),
        "generation": lambda: workflow_service.update_run_generation("generation-run", "2"),
    }


@pytest.mark.parametrize(
    "operation_name", ["grant", "approval_state", "get_approval", "list_workflows", "generation"]
)
@pytest.mark.parametrize("fail_query", [False, True])
def test_store_connections_close_on_success_and_error(
    stores, monkeypatch, operation_name, fail_query
):
    allocated = []
    original_init = Connection.__init__
    original_execute = Connection.execute

    def track(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        allocated.append(self)

    def execute(self, statement, params=(), *, prepare: bool | None = None):
        if fail_query:
            raise OperationalError("injected store query failure")
        if prepare is None:
            return original_execute(self, statement, params)
        return original_execute(self, statement, params, prepare=prepare)

    monkeypatch.setattr(Connection, "__init__", track)
    monkeypatch.setattr(Connection, "execute", execute)
    if fail_query and operation_name not in {"approval_state", "get_approval"}:
        with pytest.raises(OperationalError, match="injected store query failure"):
            stores[operation_name]()
    else:
        result = stores[operation_name]()
        if operation_name == "approval_state":
            assert result == (approval_store.UNKNOWN if fail_query else approval_store.ABSENT)
        if operation_name == "get_approval":
            assert result is None
        if operation_name == "list_workflows":
            assert result == []
    assert allocated
    assert all(connection.raw.closed for connection in allocated)
