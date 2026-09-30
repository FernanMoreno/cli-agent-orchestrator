"""Attempt binding and process lifetime checks for runtime isolation proofs."""

import os
import socket

import pytest

from cli_agent_orchestrator.services import work_bubblewrap_isolation_proof as proof_module


class _Snapshot:
    digest = "d" * 64

    def validate(self):
        return None


class _Repository:
    def read_bubblewrap_process_identity(self, _attempt_id, _generation):
        return self.identity


def _proof(monkeypatch):
    monitor_fd = os.open("/dev/null", os.O_RDONLY)
    init_fd = os.open("/dev/null", os.O_RDONLY)
    worker, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    identity = {
        "kind": "bubblewrap",
        "monitor_pid": 100,
        "init_pid": 200,
        "init_parent_pid": 100,
        "monitor_start_time_ticks": 1,
        "init_start_time_ticks": 2,
        "pid_namespace": [42, 43],
        "identity_sha256": "a" * 64,
    }
    repository = _Repository()
    repository.identity = identity
    monkeypatch.setattr(proof_module, "_namespace_inode", lambda pid, _ns: 20 if pid == 200 else 10)
    monkeypatch.setattr(
        proof_module, "_process_start_time_ticks", lambda pid: 2 if pid == 200 else 1
    )
    monkeypatch.setattr(proof_module, "_namespace_identity", lambda _pid, _ns: (42, 43))
    monkeypatch.setattr(proof_module, "_require_yama_ptrace_scope", lambda: 1)
    dead = set()

    def require_live(descriptor, _pid):
        if descriptor in dead:
            raise proof_module.WorkBubblewrapIsolationExpired("dead")

    monkeypatch.setattr(
        proof_module.WorkBubblewrapRuntimeIsolationProof,
        "_require_pidfd",
        staticmethod(require_live),
    )
    proof = proof_module._issue_runtime_isolation_proof(
        repository=repository,
        snapshot=_Snapshot(),
        attempt_id="attempt-1",
        generation=4,
        attempt_revision=9,
        contract_hash="c" * 64,
        process_identity=identity,
        monitor_pidfd=monitor_fd,
        init_pidfd=init_fd,
        worker_socket_fd=worker.fileno(),
    )
    os.close(monitor_fd)
    os.close(init_fd)
    return proof, peer, dead


def test_isolation_proof_binds_attempt_generation_contract_and_socket(monkeypatch):
    proof, peer, _dead = _proof(monkeypatch)
    try:
        proof.require_current("attempt-1", 4, "c" * 64)
        assert proof.worker_socket_identity is not None
        for attempt_id, generation, contract_hash in (
            ("other-attempt", 4, "c" * 64),
            ("attempt-1", 5, "c" * 64),
            ("attempt-1", 4, "e" * 64),
        ):
            with pytest.raises(proof_module.WorkBubblewrapIsolationExpired):
                proof.require_current(attempt_id, generation, contract_hash)
    finally:
        peer.close()
        proof.close()


def test_isolation_proof_rejects_exited_pidfd_and_changed_snapshot(monkeypatch):
    proof, peer, dead = _proof(monkeypatch)
    try:
        dead.add(proof._init_pidfd)
        with pytest.raises(proof_module.WorkBubblewrapIsolationExpired):
            proof.require_current("attempt-1", 4, "c" * 64)
        dead.clear()
        proof._snapshot.digest = "f" * 64
        with pytest.raises(proof_module.WorkBubblewrapIsolationExpired):
            proof.require_current("attempt-1", 4, "c" * 64)
    finally:
        peer.close()
        proof.close()


@pytest.mark.parametrize("changed", ["starttime", "namespace"])
def test_isolation_proof_rejects_reused_pid_or_changed_namespace(monkeypatch, changed):
    proof, peer, _dead = _proof(monkeypatch)
    try:
        if changed == "starttime":
            monkeypatch.setattr(proof_module, "_process_start_time_ticks", lambda _pid: 999)
        else:
            monkeypatch.setattr(proof_module, "_namespace_identity", lambda _pid, _ns: (42, 99))
        with pytest.raises(proof_module.WorkBubblewrapIsolationExpired):
            proof.require_current("attempt-1", 4, "c" * 64)
    finally:
        peer.close()
        proof.close()
