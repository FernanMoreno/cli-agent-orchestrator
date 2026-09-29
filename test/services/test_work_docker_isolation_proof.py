"""Docker isolation evidence must stay bound to one observed container."""

import pytest

from cli_agent_orchestrator.services.work_docker_isolation_proof import (
    WorkDockerIsolationExpired,
    issue_docker_runtime_isolation_proof,
)


def test_docker_runtime_proof_rechecks_exact_running_container():
    current = {
        "container_id": "c" * 64,
        "image_id": "sha256:" + "a" * 64,
        "started_at": "2026-09-29T10:00:00.000000000Z",
    }
    proof = issue_docker_runtime_isolation_proof(
        attempt_id="attempt-1",
        generation=2,
        attempt_revision=4,
        contract_hash="b" * 64,
        image_id=current["image_id"],
        container_id=current["container_id"],
        started_at=current["started_at"],
        worker_socket_identity=(11, 13),
        inspect_current=lambda: dict(current),
    )

    proof.require_current("attempt-1", 2, "b" * 64)
    current["started_at"] = "2026-09-29T10:00:01.000000000Z"
    with pytest.raises(WorkDockerIsolationExpired):
        proof.require_current("attempt-1", 2, "b" * 64)
    with pytest.raises(WorkDockerIsolationExpired):
        proof.require_current("attempt-1", 3, "b" * 64)
