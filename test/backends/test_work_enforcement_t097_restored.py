"""T097 worktree contracts restored at the current PostgreSQL-era boundaries."""

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.offline_database


def test_process_restriction_contract_carries_tool_allowlist():
    from cli_agent_orchestrator.backends.base import ProcessRestrictionContract

    contract = ProcessRestrictionContract(
        paths=(), commands=("/worker",), network=(), tools=("test.echo",)
    )

    assert contract.tools == ("test.echo",)


def test_admission_carries_work_contract_tool_allowlist_into_preflight():
    from cli_agent_orchestrator.services.work_admission import WorkAdmission

    work_contract = SimpleNamespace(
        resources=SimpleNamespace(write_paths=("/work/out",), checkout_root="/work"),
        permissions=SimpleNamespace(
            tools=("test.echo",),
            commands=("/worker",),
            network=("example.test",),
            paths=("/work",),
        ),
        executable_identities=(),
    )

    restriction = WorkAdmission._restriction(work_contract)

    assert restriction.tools == ("test.echo",)
