"""Restart safety when Work process identity and proxy state are lost."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services.work_mcp_proxy import (
    WorkMcpProxy,
    WorkMcpProxyUnavailable,
)
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
from cli_agent_orchestrator.services.work_process_supervisor import (
    WorkProcessIsolationUnavailable,
    WorkProcessAttempt,
    WorkProcessIdentity,
    WorkProcessState,
    WorkProcessSupervisor,
)
from cli_agent_orchestrator.services.work_service import (
    DeliveryObservation,
    DeliveryUncertain,
    WorkService,
)


class _FakeLinuxProcessIdentity:
    """A PID/start-time pair; signal records an attempted kill without OS effects."""

    def __init__(self, pid: int, start_time_ticks: int) -> None:
        self.pid = pid
        self.start_time_ticks = start_time_ticks
        self.signals: list[int] = []

    def signal(self, signum: int) -> None:
        self.signals.append(signum)


def test_restart_recovers_process_identity_without_argv_or_redelivery(tmp_path: Path) -> None:
    """Persist supervisor identity before release, then clean it after restart."""
    probe = WorkProcessSupervisor()
    try:
        probe._require_capabilities()
    except WorkProcessIsolationUnavailable as exc:
        pytest.skip(f"host cannot run the required Work namespaces: {exc}")

    database_path = tmp_path / "work.sqlite3"
    worker_started = tmp_path / "worker-started"
    controller_ready = tmp_path / "controller-ready"
    repository = WorkRepository(database_path)
    repository.initialize()
    job = repository.create_job(
        project_id="project",
        principal_id="operator",
        allowed_providers=["mock_cli"],
        grant_id="grant",
    )
    work = repository.admit_work(
        job_id=job["id"],
        operation_kind="launch",
        idempotency_key="restart-identity",
        request_hash="c" * 64,
        contract_id="test-contract",
        snapshot_id=None,
        provider="mock_cli",
        actor_id="operator",
        lease_seconds=60,
    )
    attempt = work["attempts"][-1]
    evidence = TransitionEvidence(
        generation=attempt["generation"],
        expected_generation=attempt["generation"],
        contract_confirmed=True,
        grant_confirmed=True,
        capacity_confirmed=True,
        reservations_confirmed=True,
    )
    sensitive_argument = "argv-secret-not-for-durable-storage"
    source_root = Path(__file__).resolve().parents[2] / "src"
    controller = r"""
import sys, time
from pathlib import Path

sys.path.insert(0, sys.argv[6])
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services.work_process_supervisor import WorkProcessSupervisor

database_path = Path(sys.argv[1])
attempt_id = sys.argv[2]
generation = int(sys.argv[3])
worker_started = Path(sys.argv[4])
controller_ready = Path(sys.argv[5])
repository = WorkRepository(database_path)

worker = "from pathlib import Path; import sys,time; Path(sys.argv[2]).touch(); time.sleep(float(sys.argv[3]))"
def persist(identity):
    repository.persist_process_identity(
        attempt_id, generation, identity.to_durable_dict()
    )

WorkProcessSupervisor().start([
    sys.executable, '-I', '-c', worker,
    sys.argv[7], str(worker_started), '60',
], persist_identity=persist)
controller_ready.touch()
time.sleep(60)
"""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    runner_holder: list[subprocess.Popen[str] | None] = [None]
    send_calls: list[str] = []
    attached = None
    restarted = None
    try:

        def send_once():
            send_calls.append("initial")
            runner = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    controller,
                    str(database_path),
                    attempt["id"],
                    str(attempt["generation"]),
                    str(worker_started),
                    str(controller_ready),
                    str(source_root),
                    sensitive_argument,
                ],
                cwd=Path(__file__).resolve().parents[2],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            runner_holder[0] = runner
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not controller_ready.exists():
                if runner.poll() is not None:
                    pytest.fail(f"controller exited before startup: {runner.stderr.read()}")
                time.sleep(0.01)
            assert controller_ready.exists(), "controller did not finish gated startup"
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not worker_started.exists():
                time.sleep(0.01)
            assert worker_started.exists(), "worker did not start after identity persistence"
            return DeliveryObservation()

        dispatched = WorkService(repository).dispatch(
            work["id"], send_once, admission=evidence, actor_id="operator"
        )
        assert dispatched["attempts"][-1]["state"] == "sent"
        assert send_calls == ["initial"]
        runner = runner_holder[0]
        assert runner is not None

        # Killing the controller drops its Popen ownership; the unshare monitor
        # remains alive and the next supervisor must use PIDFDs, not wait().
        runner.kill()
        runner.wait(timeout=3)

        restarted_repository = WorkRepository(database_path)
        payload = restarted_repository.read_process_identity(attempt["id"], attempt["generation"])
        assert isinstance(payload, dict)
        serialized_identity = json.dumps(payload, sort_keys=True)
        assert payload["version"] == 3
        assert "monitor_argv_prefix_sha256" in payload
        assert "monitor_argv" not in payload
        assert "argv" not in payload
        assert sensitive_argument not in serialized_identity
        assert (
            hashlib.sha256(sensitive_argument.encode("utf-8")).hexdigest()
            not in serialized_identity
        )

        restarted = WorkProcessSupervisor()
        reattach = getattr(restarted, "reattach", None)
        assert callable(reattach), "supervisor must expose safe identity reattachment"
        attached = reattach(payload)

        assert restarted.wait(attached, timeout=0) is WorkProcessState.RUNNING
        assert restarted.terminate(attached, timeout=5) is WorkProcessState.TERMINATED
        assert attached.returncode is None

        replayed = WorkService(restarted_repository).dispatch(
            work["id"],
            lambda: send_calls.append("redelivery") or DeliveryObservation(),
            admission=evidence,
            actor_id="operator",
        )
        assert replayed["attempts"][-1]["state"] == "sent"
        assert send_calls == ["initial"]
    finally:
        runner = runner_holder[0]
        if runner is not None and runner.poll() is None:
            runner.kill()
            runner.wait(timeout=3)
        if attached is not None and attached.state is not WorkProcessState.TERMINATED:
            # A failed assertion must still leave no namespace processes behind.
            assert restarted is not None
            restarted.terminate(attached, timeout=5)
        elif runner is not None:
            try:
                cleanup_repository = WorkRepository(database_path)
                orphan_identity = cleanup_repository.read_process_identity(
                    attempt["id"], attempt["generation"]
                )
                if orphan_identity is not None:
                    cleanup = WorkProcessSupervisor()
                    orphan = cleanup.reattach(orphan_identity)
                    cleanup.terminate(orphan, timeout=5)
            except Exception:
                pass


def test_supervisor_exposes_safe_reattachment_api() -> None:
    assert callable(
        getattr(WorkProcessSupervisor, "reattach", None)
    ), "restart recovery needs an explicit identity-based reattach operation"


def test_reattach_rejects_boot_or_namespace_identity_mismatch_before_opening_pidfds(
    monkeypatch,
) -> None:
    expected = WorkProcessIdentity(
        boot_id="boot-a",
        monitor_pid=901,
        monitor_start_time_ticks=120,
        init_pid=902,
        init_start_time_ticks=121,
        init_parent_pid=901,
        pid_namespace=(1, 20),
        net_namespace=(1, 21),
        ipc_namespace=(1, 22),
        monitor_argv=("unshare",),
    )
    live = replace(expected, boot_id="boot-b", net_namespace=(1, 99))
    opened: list[int] = []
    monkeypatch.setattr(
        WorkProcessSupervisor,
        "_require_pidfd_support",
        staticmethod(lambda: None),
    )
    monkeypatch.setattr(
        WorkProcessSupervisor,
        "_read_process_identity",
        classmethod(lambda _cls, *_pids: live),
    )
    monkeypatch.setattr(os, "pidfd_open", lambda pid: opened.append(pid) or 100 + pid)

    with pytest.raises(WorkProcessIsolationUnavailable, match="does not match"):
        WorkProcessSupervisor().reattach(expected)

    assert opened == []


def test_reattach_rejects_malformed_serialized_parent_relation_before_opening_pidfds(
    monkeypatch,
) -> None:
    payload = WorkProcessIdentity(
        boot_id="boot-a",
        monitor_pid=905,
        monitor_start_time_ticks=125,
        init_pid=906,
        init_start_time_ticks=126,
        init_parent_pid=905,
        pid_namespace=(1, 25),
        net_namespace=(1, 26),
        ipc_namespace=(1, 27),
        monitor_argv=("unshare",),
    ).to_dict()
    payload["init_parent_pid"] = 999
    opened: list[int] = []
    monkeypatch.setattr(os, "pidfd_open", lambda pid: opened.append(pid) or 300 + pid)

    with pytest.raises(ValueError, match="process relationship"):
        WorkProcessSupervisor().reattach(payload)

    assert opened == []


def test_reattach_rejects_identity_change_while_pidfds_are_opened(monkeypatch) -> None:
    expected = WorkProcessIdentity(
        boot_id="boot-a",
        monitor_pid=911,
        monitor_start_time_ticks=130,
        init_pid=912,
        init_start_time_ticks=131,
        init_parent_pid=911,
        pid_namespace=(1, 30),
        net_namespace=(1, 31),
        ipc_namespace=(1, 32),
        monitor_argv=("unshare",),
    )
    snapshots = iter((expected, replace(expected, init_start_time_ticks=999)))
    opened: list[int] = []
    closed: list[int] = []
    monkeypatch.setattr(
        WorkProcessSupervisor,
        "_require_pidfd_support",
        staticmethod(lambda: None),
    )
    monkeypatch.setattr(
        WorkProcessSupervisor,
        "_read_process_identity",
        classmethod(lambda _cls, *_pids: next(snapshots)),
    )
    monkeypatch.setattr(
        os,
        "pidfd_open",
        lambda pid: opened.append(pid) or 200 + len(opened),
    )
    monkeypatch.setattr(os, "close", lambda pidfd: closed.append(pidfd))
    monkeypatch.setattr(
        WorkProcessSupervisor,
        "_pidfd_is_readable",
        staticmethod(lambda _pidfd: False),
    )

    with pytest.raises(WorkProcessIsolationUnavailable, match="changed while opening"):
        WorkProcessSupervisor().reattach(expected)

    assert opened == [expected.monitor_pid, expected.init_pid]
    assert closed == [201, 202]


def test_reattached_wait_requires_both_init_and_monitor_pidfds_to_exit(monkeypatch) -> None:
    monitor_pidfd = 41
    init_pidfd = 42
    ready = {monitor_pidfd}
    closed: list[int] = []
    attempt = WorkProcessAttempt(
        command=("fake-worker",),
        monitor_pid=501,
        _state=WorkProcessState.RUNNING,
        _monitor=None,
        _pidfd=init_pidfd,
        _monitor_pidfd=monitor_pidfd,
        _identity=WorkProcessIdentity(
            boot_id="boot-a",
            monitor_pid=501,
            monitor_start_time_ticks=120,
            init_pid=502,
            init_start_time_ticks=121,
            init_parent_pid=501,
            pid_namespace=(1, 20),
            net_namespace=(1, 21),
            ipc_namespace=(1, 22),
            monitor_argv=("unshare",),
        ),
    )
    supervisor = WorkProcessSupervisor()
    supervisor._attempt = attempt
    monkeypatch.setattr(
        WorkProcessSupervisor,
        "_pidfd_is_readable",
        staticmethod(lambda pidfd: pidfd in ready),
    )
    monkeypatch.setattr(os, "close", lambda pidfd: closed.append(pidfd))

    assert supervisor.wait(attempt, timeout=0) is WorkProcessState.RUNNING
    assert attempt._pidfd == init_pidfd
    assert attempt._monitor_pidfd == monitor_pidfd
    assert closed == []

    ready.add(init_pidfd)
    assert supervisor.wait(attempt, timeout=0) is WorkProcessState.TERMINATED
    assert closed == [init_pidfd, monitor_pidfd]


def test_restart_keeps_lost_attempt_reconciled_without_pid_or_proxy_replay(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "work.sqlite3"
    repository = WorkRepository(database_path)
    repository.initialize()
    job = repository.create_job(
        project_id="project",
        principal_id="operator",
        allowed_providers=["mock_cli"],
        grant_id="grant",
    )
    work = repository.admit_work(
        job_id=job["id"],
        operation_kind="launch",
        idempotency_key="restart-loss",
        request_hash="b" * 64,
        contract_id="test-contract",
        snapshot_id=None,
        provider="mock_cli",
        actor_id="operator",
        lease_seconds=60,
    )
    evidence = TransitionEvidence(
        generation=1,
        expected_generation=1,
        contract_confirmed=True,
        grant_confirmed=True,
        capacity_confirmed=True,
        reservations_confirmed=True,
    )

    # Model a worker whose response was lost; do not launch or kill an OS process.
    lost_process = _FakeLinuxProcessIdentity(pid=4137, start_time_ticks=120)
    original_pid = lost_process.pid
    original_start_time = lost_process.start_time_ticks
    send_calls: list[tuple[int, int]] = []

    def lost_delivery_response() -> None:
        send_calls.append((lost_process.pid, lost_process.start_time_ticks))
        raise TimeoutError("response lost after process creation")

    with pytest.raises(DeliveryUncertain):
        WorkService(repository).dispatch(
            work["id"], lost_delivery_response, admission=evidence, actor_id="operator"
        )

    attempt = repository.get_work(work["id"])["attempts"][-1]

    def interrupt_cleanup() -> bool:
        raise SystemExit("server stopped after cleanup intent")

    with pytest.raises(SystemExit, match="server stopped after cleanup intent"):
        WorkService(repository).cleanup_attempt(
            attempt["id"], interrupt_cleanup, actor_id="operator"
        )

    # Reopened DB and service model a server restart. The numeric PID is reused
    # by a different process identity; there is no durable handle to reattach.
    del lost_process
    recycled_process = _FakeLinuxProcessIdentity(
        pid=original_pid, start_time_ticks=original_start_time + 1
    )
    restarted_repository = WorkRepository(database_path)
    restarted_service = WorkService(restarted_repository)

    replayed = restarted_service.dispatch(
        work["id"],
        lambda: send_calls.append((recycled_process.pid, recycled_process.start_time_ticks)),
        admission=evidence,
        actor_id="operator",
    )
    assert replayed["state"] == "reconcile"
    assert replayed["attempts"][-1]["cleanup_state"] == "pending"
    assert send_calls == [(original_pid, original_start_time)]

    # If cleanup were blindly replayed by numeric PID, this fake identity would
    # observe a signal. Pending cleanup is intentionally left for reconciliation.
    restarted_service.cleanup_attempt(
        attempt["id"], lambda: recycled_process.signal(9) or True, actor_id="operator"
    )
    current = restarted_repository.get_work(work["id"])
    assert current["state"] == "reconcile"
    assert current["attempts"][-1]["cleanup_state"] == "pending"
    assert recycled_process.signals == []

    # A new supervisor must reject an old in-memory handle even if the recycled
    # process now has the same PID. The fake pidfd prevents any OS signal.
    stale_attempt = WorkProcessAttempt(
        command=("fake-worker",),
        monitor_pid=recycled_process.pid,
        _state=WorkProcessState.UNCERTAIN,
        _monitor=Mock(),
        _pidfd=123,
    )
    pidfd_signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        signal,
        "pidfd_send_signal",
        lambda pidfd, signum: pidfd_signals.append((pidfd, signum)),
    )
    with pytest.raises(ValueError, match="not owned by this supervisor"):
        WorkProcessSupervisor().terminate(stale_attempt)
    assert pidfd_signals == []

    # No proxy endpoint or credential was issued before restart. A new proxy
    # manager remains fail-closed before either durable issue claim or secret read.
    proxy_calls: list[str] = []
    with pytest.raises(WorkMcpProxyUnavailable):
        WorkMcpProxy().create_attempt(
            attempt_id=attempt["id"],
            generation=attempt["generation"],
            expires_at=time.time() + 60,
            claim_issue_once=lambda *_args: proxy_calls.append("claim"),
            server_secret_factory=lambda: proxy_calls.append("secret"),
        )
    assert proxy_calls == []
