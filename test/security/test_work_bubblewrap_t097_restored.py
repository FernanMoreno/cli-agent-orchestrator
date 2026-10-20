"""T097 regression for a Yama downgrade after a Work isolation proof is issued."""

from test.security.test_work_bubblewrap_isolation_proof import _proof

import pytest

from cli_agent_orchestrator.services import work_bubblewrap_isolation_proof as proof_module

pytestmark = pytest.mark.offline_database


def test_isolation_proof_expires_when_yama_sibling_policy_is_disabled(monkeypatch):
    proof, peer, _dead = _proof(monkeypatch)
    try:
        proof.require_current("attempt-1", 4, "c" * 64)

        monkeypatch.setattr(proof_module, "_require_yama_ptrace_scope", lambda: 0)
        with pytest.raises(proof_module.WorkBubblewrapIsolationExpired):
            proof.require_current("attempt-1", 4, "c" * 64)
    finally:
        peer.close()
        proof.close()
