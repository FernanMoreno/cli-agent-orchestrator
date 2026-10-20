"""T097 process-boundary contracts exercised against current Work services."""

import pytest

from cli_agent_orchestrator.services.work_process_supervisor import (
    WorkProcessIsolationUnavailable,
    WorkProcessSupervisor,
)


@pytest.mark.offline_database
@pytest.mark.parametrize(
    "executable", ["true", "./worker", "//usr/bin/true", "/usr/bin/../bin/true"]
)
def test_supervisor_rejects_noncanonical_executable_before_host_capability_probe(
    monkeypatch, executable
):
    supervisor = WorkProcessSupervisor()

    def capability_probe_must_not_run():
        raise WorkProcessIsolationUnavailable("capability probe reached")

    monkeypatch.setattr(supervisor, "_require_capabilities", capability_probe_must_not_run)
    with pytest.raises(ValueError, match="canonical absolute executable path"):
        supervisor.start([executable], persist_identity=lambda _identity: None)
