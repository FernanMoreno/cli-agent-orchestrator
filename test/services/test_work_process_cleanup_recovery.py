"""A reconciled process can be cleaned after restart without releasing its writer."""

import hashlib
import json
import multiprocessing
import os
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
from cli_agent_orchestrator.services import work_process_supervisor as supervisor_module
from cli_agent_orchestrator.services.work_process_supervisor import (
    WorkProcessAttempt,
    WorkProcessIdentity,
    WorkProcessIsolationUnavailable,
    WorkProcessState,
    WorkProcessSupervisor,
)
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
from cli_agent_orchestrator.services.work_scheduler import (
    SchedulerConflict,
    StoppedWriter,
    WorkScheduler,
)
from cli_agent_orchestrator.services.work_service import WorkService


def _identity():
    payload = {
        "version": 3,
        "boot_id": "cleanup-test-boot",
        "monitor_pid": 4101,
        "monitor_start_time_ticks": 12001,
        "init_pid": 4102,
        "init_start_time_ticks": 12002,
        "init_parent_pid": 4101,
        "pid_namespace": [1, 41001],
        "net_namespace": [1, 41002],
        "ipc_namespace": [1, 41003],
        "monitor_argv_prefix_sha256": "a" * 64,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return {**payload, "identity_sha256": digest}


def _proc_stat(pid, *, start, state="S"):
    fields = [state, "1", *(["0"] * 17), str(start)]
    return f"{pid} (worker) " + " ".join(fields)


@pytest.mark.parametrize(
    "boot,monitor,init,expected",
    [
        ("cleanup-test-boot", None, None, True),
        ("cleanup-test-boot", _proc_stat(4101, start=12099), None, True),
        ("cleanup-test-boot", _proc_stat(4101, start=12001, state="Z"), None, True),
        ("cleanup-test-boot", _proc_stat(4101, start=12001), None, False),
        ("cleanup-test-boot", None, _proc_stat(4102, start=12002), False),
        ("cleanup-test-boot", PermissionError("denied"), None, False),
        ("cleanup-test-boot", "malformed stat", None, False),
        (PermissionError("boot unavailable"), None, None, False),
        ("another-boot", _proc_stat(4101, start=12001), _proc_stat(4102, start=12002), True),
    ],
)
def test_v3_original_pair_termination_proof_is_observational(
    monkeypatch, boot, monitor, init, expected
):
    values = {
        "/proc/sys/kernel/random/boot_id": boot,
        "/proc/4101/stat": monitor,
        "/proc/4102/stat": init,
    }
    reads = []

    def fake_path(path):
        def read_text():
            reads.append(path)
            value = values[path]
            if value is None:
                raise FileNotFoundError(path)
            if isinstance(value, BaseException):
                raise value
            return value

        return SimpleNamespace(read_text=read_text)

    monkeypatch.setattr(supervisor_module, "Path", fake_path)
    assert WorkProcessSupervisor().original_pair_terminated(_identity()) is expected
    if boot == "another-boot":
        assert reads == ["/proc/sys/kernel/random/boot_id"]


def test_v3_original_pair_termination_proof_rejects_tampered_identity(monkeypatch):
    monkeypatch.setattr(
        supervisor_module,
        "Path",
        lambda path: (_ for _ in ()).throw(AssertionError(f"unexpected proc read: {path}")),
    )
    identity = _identity()
    identity["monitor_pid"] = 9999
    assert WorkProcessSupervisor().original_pair_terminated(identity) is False


@pytest.mark.parametrize("matching", [True, False])
def test_termination_proof_releases_only_matching_local_reattach_pidfds(monkeypatch, matching):
    monkeypatch.setattr(
        supervisor_module,
        "Path",
        lambda path: SimpleNamespace(read_text=lambda: "another-boot"),
    )
    identity = WorkProcessIdentity.from_dict(_identity())
    if not matching:
        other = _identity()
        other.pop("identity_sha256")
        other["monitor_pid"] = 5101
        other["init_parent_pid"] = 5101
        other["identity_sha256"] = hashlib.sha256(
            json.dumps(other, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        identity = WorkProcessIdentity.from_dict(other)
    supervisor = WorkProcessSupervisor()
    init_fd, init_write = os.pipe()
    monitor_fd, monitor_write = os.pipe()
    os.close(init_write)
    os.close(monitor_write)
    attempt = WorkProcessAttempt(
        command=(),
        monitor_pid=identity.monitor_pid,
        _state=WorkProcessState.UNCERTAIN,
        _monitor=None,
        _pidfd=init_fd,
        _monitor_pidfd=monitor_fd,
        _identity=identity,
    )
    supervisor._attempt = attempt
    try:
        assert supervisor.original_pair_terminated(_identity()) is True
        if matching:
            assert attempt.state is WorkProcessState.TERMINATED
            assert attempt._pidfd is None
            assert attempt._monitor_pidfd is None
            assert supervisor.is_blocked is False
            with pytest.raises(OSError):
                os.fstat(init_fd)
            with pytest.raises(OSError):
                os.fstat(monitor_fd)
        else:
            assert attempt.state is WorkProcessState.UNCERTAIN
            assert attempt._pidfd == init_fd
            assert attempt._monitor_pidfd == monitor_fd
            os.fstat(init_fd)
            os.fstat(monitor_fd)
    finally:
        for fd in (init_fd, monitor_fd):
            try:
                os.close(fd)
            except OSError:
                pass


def _reconciled_held(tmp_path):
    repository = WorkRepository(tmp_path / "cleanup.sqlite3")
    repository.initialize()
    scheduler = WorkScheduler(repository)
    scheduler.configure(capacity=1, max_queue=2, aging_seconds=10, expected_policy_revision=0)
    job = repository.create_job(
        project_id="project",
        principal_id="owner",
        allowed_providers=["mock_cli"],
        grant_id="grant",
        budget={"scheduler_units": 2},
    )
    work = repository.admit_work(
        job_id=job["id"],
        operation_kind="launch",
        idempotency_key="cleanup",
        request_hash="a" * 64,
        contract_id="contract",
        snapshot_id=None,
        provider="mock_cli",
        actor_id="owner",
        lease_seconds=60,
    )
    attempt = work["attempts"][-1]
    scheduler.enqueue(
        attempt_id=attempt["id"],
        generation=1,
        expected_attempt_revision=attempt["revision"],
        units=1,
        actor_id="owner",
    )
    held = scheduler.claim_next(actor_id="owner")
    sent = repository.transition_attempt(
        attempt_id=attempt["id"],
        generation=1,
        expected_revision=attempt["revision"],
        expected_state="planned",
        target="sent",
        actor_id="owner",
        event_id="cleanup-sent",
        evidence=TransitionEvidence(
            generation=1,
            expected_generation=1,
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        ),
    )
    reconcile = repository.transition_attempt(
        attempt_id=attempt["id"],
        generation=1,
        expected_revision=sent["attempts"][-1]["revision"],
        expected_state="sent",
        target="reconcile",
        actor_id="owner",
        event_id="cleanup-reconcile",
        evidence=TransitionEvidence(generation=1, expected_generation=1),
    )
    return repository, reconcile, held


def _reconciled_held_with_issued_proxy(tmp_path):
    from test.services.test_work_contract_binding import bind, context

    from cli_agent_orchestrator.services.work_mcp_proxy import WorkMcpProxy

    data = context(tmp_path)
    models, _, repository, actor, job, _, _, item, original = data
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE work_jobs SET budget=? WHERE id=?",
            (json.dumps({"scheduler_units": 2}), job["id"]),
        )
    contract = models.EffectiveWorkContractV2(**{**original.model_dump(), "schema_version": 2})
    binding = bind(data, contract=contract)
    attempt_id = item["attempts"][-1]["id"]
    scheduler = WorkScheduler(repository)
    scheduler.configure(capacity=1, max_queue=2, aging_seconds=10, expected_policy_revision=0)
    scheduler.enqueue(
        attempt_id=attempt_id,
        generation=1,
        expected_attempt_revision=item["attempts"][-1]["revision"],
        units=1,
        actor_id=actor.id,
    )
    held = scheduler.claim_next(actor_id=actor.id)
    sent = repository.transition_attempt(
        attempt_id=attempt_id,
        generation=1,
        expected_revision=item["attempts"][-1]["revision"],
        expected_state="planned",
        target="sent",
        actor_id=actor.id,
        event_id="cleanup-sent",
        evidence=TransitionEvidence(
            generation=1,
            expected_generation=1,
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        ),
    )
    proxy = WorkMcpProxy(
        repository,
        server_secret_factory=lambda: b"unused",
        upstream=lambda request, secret: {},
    )
    proxy.create_bound_attempt(
        attempt_id=attempt_id,
        generation=1,
        expected_attempt_revision=sent["attempts"][-1]["revision"],
        contract_hash=binding.contract_hash,
        expires_at=time.time() + 30,
    )
    proxy.close()
    reconcile = repository.transition_attempt(
        attempt_id=attempt_id,
        generation=1,
        expected_revision=sent["attempts"][-1]["revision"],
        expected_state="sent",
        target="reconcile",
        actor_id=actor.id,
        event_id="cleanup-reconcile",
        evidence=TransitionEvidence(generation=1, expected_generation=1),
    )
    return repository, reconcile, held


def _supervisor(state=WorkProcessState.TERMINATED):
    attempt = object()
    supervisor = Mock()
    supervisor.reattach.return_value = attempt
    supervisor.terminate.return_value = state
    return supervisor, attempt


def _recover(repository, work, supervisor, *, backend_reconciler=None):
    arguments = {"supervisor": supervisor, "actor_id": "owner"}
    if backend_reconciler is not None:
        arguments["backend_reconciler"] = backend_reconciler
    return WorkService(repository).recover_process_cleanup(work["attempts"][-1]["id"], **arguments)


def _replacement_args(work, held):
    return dict(
        generation=1,
        expected_revision=held.revision,
        expected_attempt_revision=work["attempts"][-1]["revision"],
        expected_work_revision=work["revision"],
        actor_id="owner",
    )


def test_missing_or_corrupt_identity_fails_without_supervisor_effect(tmp_path):
    repository, work, held = _reconciled_held(tmp_path)
    supervisor, _ = _supervisor()
    failed = _recover(repository, work, supervisor)
    assert failed["attempts"][-1]["cleanup_state"] == "failed"
    assert failed["state"] == "reconcile"
    supervisor.reattach.assert_not_called()
    supervisor.terminate.assert_not_called()
    assert WorkScheduler(repository).get_by_attempt(held.attempt_id).state == "held"

    with repository.connection() as connection:
        connection.execute(
            "INSERT INTO work_process_identities "
            "(attempt_id,generation,protocol_version,identity_json,identity_sha256,created_at) "
            "VALUES (?,?,?,?,?,0)",
            (held.attempt_id, 1, 3, "{}", "0" * 64),
        )
    failed_again = _recover(repository, failed, supervisor)
    assert failed_again["attempts"][-1]["cleanup_state"] == "failed"
    supervisor.reattach.assert_not_called()


def test_docker_reconciliation_requires_both_exact_cleanup_flags_before_proxy_recovery(
    monkeypatch, tmp_path
):
    from cli_agent_orchestrator.services import work_mcp_proxy as proxy_module

    repository, work, _ = _reconciled_held(tmp_path)
    attempt_id = work["attempts"][-1]["id"]
    events = []
    backend = Mock()
    backend.reconcile_attempt.side_effect = lambda attempt, generation: events.append(
        ("docker", attempt, generation)
    ) or {"container_removed": True, "image_removed": True}
    proxy = Mock()
    proxy.recover_incomplete_effects.side_effect = (
        lambda *args: events.append(("effects", *args)) or 0
    )
    proxy.recover_incomplete_issues.side_effect = (
        lambda *args: events.append(("issues", *args)) or 0
    )
    monkeypatch.setattr(proxy_module, "WorkMcpProxy", lambda _repository: proxy)

    complete = _recover(repository, work, None, backend_reconciler=backend)

    assert complete["attempts"][-1]["cleanup_state"] == "complete"
    backend.reconcile_attempt.assert_called_once_with(attempt_id, 1)
    assert events == [
        ("docker", attempt_id, 1),
        ("effects", attempt_id, 1),
        ("issues", attempt_id, 1),
    ]


@pytest.mark.parametrize(
    "result",
    [
        {"container_removed": True, "image_removed": False},
        {"container_removed": False, "image_removed": True},
        {"container_removed": True},
        {"container_removed": 1, "image_removed": True},
        None,
    ],
)
def test_docker_reconciliation_fails_closed_without_exact_flags(monkeypatch, tmp_path, result):
    from cli_agent_orchestrator.services import work_mcp_proxy as proxy_module

    repository, work, _ = _reconciled_held(tmp_path)
    attempt_id = work["attempts"][-1]["id"]
    backend = Mock()
    backend.reconcile_attempt.return_value = result
    proxy = Mock()
    monkeypatch.setattr(proxy_module, "WorkMcpProxy", lambda _repository: proxy)

    failed = _recover(repository, work, None, backend_reconciler=backend)

    assert failed["attempts"][-1]["cleanup_state"] == "failed"
    backend.reconcile_attempt.assert_called_once_with(attempt_id, 1)
    proxy.recover_incomplete_effects.assert_not_called()
    proxy.recover_incomplete_issues.assert_not_called()


def test_docker_cleanup_recovery_abandons_unanswered_mcp_issue_durably(tmp_path):
    from cli_agent_orchestrator.services.work_mcp_proxy import WorkMcpProxy

    repository, work, _ = _reconciled_held_with_issued_proxy(tmp_path)
    attempt_id = work["attempts"][-1]["id"]
    backend = Mock()
    backend.reconcile_attempt.return_value = {
        "container_removed": True,
        "image_removed": True,
    }

    before = WorkMcpProxy(repository)
    assert before.unresolved_issues()[0]["state"] == "issued"
    assert before.incomplete_issue_owners() == ((attempt_id, 1),)
    assert before.incomplete_effect_owners() == ()

    complete = _recover(repository, work, None, backend_reconciler=backend)

    assert complete["attempts"][-1]["cleanup_state"] == "complete"
    backend.reconcile_attempt.assert_called_once_with(attempt_id, 1)
    after = WorkMcpProxy(repository)
    assert after.unresolved_issues()[0]["state"] == "abandoned"
    assert after.incomplete_issue_owners() == ()
    assert after.incomplete_effect_owners() == ()
    with repository.read_snapshot() as connection:
        states = connection.execute(
            "SELECT state FROM work_mcp_proxy_issue_events "
            "WHERE attempt_id=? AND generation=1 ORDER BY sequence",
            (attempt_id,),
        ).fetchall()
        assert [row[0] for row in states] == ["issued", "abandoned"]
        assert (
            connection.execute(
                "SELECT count(*) FROM work_mcp_proxy_effects WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()[0]
            == 0
        )


def test_docker_reconciliation_exception_fails_closed_without_proxy_recovery(monkeypatch, tmp_path):
    from cli_agent_orchestrator.services import work_mcp_proxy as proxy_module

    repository, work, _ = _reconciled_held(tmp_path)
    backend = Mock()
    backend.reconcile_attempt.side_effect = RuntimeError("daemon unavailable")
    proxy = Mock()
    monkeypatch.setattr(proxy_module, "WorkMcpProxy", lambda _repository: proxy)

    failed = _recover(repository, work, None, backend_reconciler=backend)

    assert failed["attempts"][-1]["cleanup_state"] == "failed"
    proxy.recover_incomplete_effects.assert_not_called()
    proxy.recover_incomplete_issues.assert_not_called()


def test_verified_identity_is_reattached_and_only_termination_completes(tmp_path):
    repository, work, held = _reconciled_held(tmp_path)
    identity = _identity()
    with repository.connection() as connection:
        connection.execute(
            "INSERT INTO work_process_identities "
            "(attempt_id,generation,protocol_version,identity_json,identity_sha256,created_at) "
            "VALUES (?,?,?,?,?,0)",
            (
                held.attempt_id,
                1,
                3,
                json.dumps(identity, sort_keys=True, separators=(",", ":")),
                identity["identity_sha256"],
            ),
        )
    supervisor, attached = _supervisor()

    def terminate_without_transaction(_attempt):
        with sqlite3.connect(repository.path, timeout=0.1, isolation_level=None) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        return WorkProcessState.TERMINATED

    supervisor.terminate.side_effect = terminate_without_transaction
    backend = Mock()
    complete = _recover(repository, work, supervisor, backend_reconciler=backend)
    assert complete["attempts"][-1]["cleanup_state"] == "complete"
    backend.reconcile_attempt.assert_not_called()
    assert complete["state"] == "reconcile"
    supervisor.reattach.assert_called_once_with(identity)
    supervisor.terminate.assert_called_once_with(attached)
    assert WorkScheduler(repository).get_by_attempt(held.attempt_id).state == "held"
    events = repository.read_events(work["job_id"])["events"]
    assert [event["event_type"] for event in events[-2:]] == [
        "cleanup.pending",
        "cleanup.complete",
    ]
    repeated = _recover(repository, complete, supervisor)
    assert repeated == complete
    supervisor.reattach.assert_called_once()
    backend.reconcile_attempt.assert_not_called()


def test_concurrent_recovery_cannot_enter_cleanup_for_the_same_attempt(tmp_path):
    repository, work, held = _reconciled_held(tmp_path)
    identity = _identity()
    with repository.connection() as connection:
        connection.execute(
            "INSERT INTO work_process_identities "
            "(attempt_id,generation,protocol_version,identity_json,identity_sha256,created_at) "
            "VALUES (?,?,?,?,?,0)",
            (
                held.attempt_id,
                1,
                3,
                json.dumps(identity, sort_keys=True, separators=(",", ":")),
                identity["identity_sha256"],
            ),
        )

    entered_cleanup = threading.Event()
    finish_cleanup = threading.Event()
    first_result = []
    first_error = []
    first_supervisor, attached = _supervisor()

    def held_terminate(_attempt):
        entered_cleanup.set()
        assert finish_cleanup.wait(3), "first cleanup was not released"
        return WorkProcessState.TERMINATED

    first_supervisor.terminate.side_effect = held_terminate

    def first_recovery():
        try:
            first_result.append(_recover(repository, work, first_supervisor))
        except BaseException as error:
            first_error.append(error)

    first_thread = threading.Thread(target=first_recovery)
    first_thread.start()
    try:
        assert entered_cleanup.wait(2), "first recovery never reached cleanup"
        second_supervisor, _ = _supervisor()
        with pytest.raises(WorkConflict, match="cleanup.*(owned|locked|busy)"):
            _recover(WorkRepository(repository.path), work, second_supervisor)
        second_supervisor.reattach.assert_not_called()
        second_supervisor.terminate.assert_not_called()
        assert repository.get_attempt(held.attempt_id)["cleanup_state"] == "pending"
    finally:
        finish_cleanup.set()
        first_thread.join(timeout=3)

    assert not first_thread.is_alive()
    assert first_error == []
    assert first_result[0]["attempts"][-1]["cleanup_state"] == "complete"
    first_supervisor.terminate.assert_called_once_with(attached)


def test_process_exit_releases_cleanup_owner_for_pending_recovery(tmp_path):
    repository, work, held = _reconciled_held(tmp_path)
    identity = _identity()
    with repository.connection() as connection:
        connection.execute(
            "INSERT INTO work_process_identities "
            "(attempt_id,generation,protocol_version,identity_json,identity_sha256,created_at) "
            "VALUES (?,?,?,?,?,0)",
            (
                held.attempt_id,
                1,
                3,
                json.dumps(identity, sort_keys=True, separators=(",", ":")),
                identity["identity_sha256"],
            ),
        )

    script = """
import os, sys
sys.path.insert(0, sys.argv[4])
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services.work_service import WorkService
service = WorkService(WorkRepository(sys.argv[2]))
if sys.argv[1] == "crash":
    service.cleanup_attempt(sys.argv[3], lambda: os._exit(17), actor_id="owner")
else:
    result = service.recover_process_cleanup(sys.argv[3], actor_id="owner")
    print(result["attempts"][-1]["cleanup_state"])
"""
    source_root = Path(__file__).resolve().parents[2] / "src"

    def child(mode):
        return subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                mode,
                str(repository.path),
                held.attempt_id,
                str(source_root),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    crashed = child("crash")
    assert crashed.returncode == 17, crashed.stderr
    assert repository.get_attempt(held.attempt_id)["cleanup_state"] == "pending"
    resumed = child("resume")
    assert resumed.returncode == 0, resumed.stderr
    assert resumed.stdout.strip() == "complete"
    assert repository.get_attempt(held.attempt_id)["cleanup_state"] == "complete"
    assert WorkScheduler(repository).get_by_attempt(held.attempt_id).state == "held"


@pytest.mark.skipif(sys.platform != "linux", reason="fork context requires Linux")
def test_another_process_cannot_recover_while_cleanup_owner_is_live(tmp_path):
    repository, work, held = _reconciled_held(tmp_path)
    context = multiprocessing.get_context("fork")
    entered = context.Event()
    release = context.Event()
    reached_identity = context.Event()

    def owner_worker():
        def cleanup():
            entered.set()
            release.wait()
            return True

        WorkService(WorkRepository(repository.path)).cleanup_attempt(
            held.attempt_id, cleanup, actor_id="owner"
        )

    def contender_worker(*, bypass_lock):
        contender_repository = WorkRepository(repository.path)
        if bypass_lock:
            contender_repository.cleanup_owner_lock = lambda *_: nullcontext()

        def reached_read(*_args):
            reached_identity.set()
            raise SystemExit(31)

        contender_repository.read_bubblewrap_process_identity = reached_read
        try:
            WorkService(contender_repository).recover_process_cleanup(
                held.attempt_id, actor_id="owner"
            )
        except WorkConflict:
            raise SystemExit(0)
        raise SystemExit(32)

    owner = context.Process(target=owner_worker)
    owner.daemon = True
    owner.start()
    try:
        assert entered.wait(10), "first process never entered cleanup"
        assert repository.get_attempt(held.attempt_id)["cleanup_state"] == "pending"

        def contender(*, bypass_lock):
            process = context.Process(target=contender_worker, kwargs={"bypass_lock": bypass_lock})
            process.start()
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            return process.exitcode

        # This local negative control proves that reaching identity/cleanup
        # would be observable if the contender bypassed the owner lock.
        assert contender(bypass_lock=True) == 31
        assert reached_identity.is_set()
        reached_identity.clear()

        assert owner.is_alive(), "cleanup owner exited before the guarded contender"
        assert repository.get_attempt(held.attempt_id)["cleanup_state"] == "pending"
        with pytest.raises(WorkConflict, match="cleanup is owned"):
            with repository.cleanup_owner_lock(held.attempt_id, 1):
                pytest.fail("owner lock was released before guarded recovery")

        assert contender(bypass_lock=False) == 0
        assert not reached_identity.is_set()
        assert repository.get_attempt(held.attempt_id)["cleanup_state"] == "pending"
    finally:
        release.set()
        owner.join(timeout=10)
        if owner.is_alive():
            owner.terminate()
            owner.join(timeout=5)

    assert owner.exitcode == 0
    assert repository.get_attempt(held.attempt_id)["cleanup_state"] == "complete"


def test_cleanup_owner_lock_fails_closed_without_fcntl(tmp_path, monkeypatch):
    repository = WorkRepository(tmp_path / "work.sqlite3")
    with monkeypatch.context() as missing_fcntl:
        missing_fcntl.setitem(sys.modules, "fcntl", None)
        with pytest.raises(WorkConflict, match="cleanup owner lock is unavailable"):
            with repository.cleanup_owner_lock("attempt", 1):
                pytest.fail("cleanup entered without an OS advisory lock")


@pytest.mark.parametrize(
    "outcome", [WorkProcessState.RUNNING, WorkProcessState.UNCERTAIN, OSError("signal failed")]
)
def test_nonterminal_or_exception_remains_failed_and_reconciliable(tmp_path, outcome):
    repository, work, held = _reconciled_held(tmp_path)
    identity = _identity()
    with repository.connection() as connection:
        connection.execute(
            "INSERT INTO work_process_identities "
            "(attempt_id,generation,protocol_version,identity_json,identity_sha256,created_at) "
            "VALUES (?,?,?,?,?,0)",
            (
                held.attempt_id,
                1,
                3,
                json.dumps(identity, sort_keys=True, separators=(",", ":")),
                identity["identity_sha256"],
            ),
        )
    supervisor, _ = _supervisor(outcome)
    if isinstance(outcome, Exception):
        supervisor.terminate.side_effect = outcome
    failed = _recover(repository, work, supervisor)
    assert failed["attempts"][-1]["cleanup_state"] == "failed"
    assert failed["state"] == "reconcile"


def test_reattach_identity_mismatch_cannot_terminate_or_complete(tmp_path):
    repository, work, held = _reconciled_held(tmp_path)
    identity = _identity()
    with repository.connection() as connection:
        connection.execute(
            "INSERT INTO work_process_identities "
            "(attempt_id,generation,protocol_version,identity_json,identity_sha256,created_at) "
            "VALUES (?,?,?,?,?,0)",
            (
                held.attempt_id,
                1,
                3,
                json.dumps(identity, sort_keys=True, separators=(",", ":")),
                identity["identity_sha256"],
            ),
        )
    supervisor, _ = _supervisor()
    supervisor.reattach.side_effect = WorkProcessIsolationUnavailable("PID identity changed")
    failed = _recover(repository, work, supervisor)
    assert failed["attempts"][-1]["cleanup_state"] == "failed"
    assert failed["state"] == "reconcile"
    supervisor.reattach.assert_called_once_with(identity)
    supervisor.terminate.assert_not_called()


def test_natural_exit_after_restart_completes_without_signal_or_redelivery(tmp_path):
    repository, work, held = _reconciled_held(tmp_path)
    identity = _identity()
    with repository.connection() as connection:
        connection.execute(
            "INSERT INTO work_process_identities "
            "(attempt_id,generation,protocol_version,identity_json,identity_sha256,created_at) "
            "VALUES (?,?,?,?,?,0)",
            (
                held.attempt_id,
                1,
                3,
                json.dumps(identity, sort_keys=True, separators=(",", ":")),
                identity["identity_sha256"],
            ),
        )
    supervisor, _ = _supervisor()
    supervisor.reattach.side_effect = WorkProcessIsolationUnavailable("original processes exited")
    supervisor.original_pair_terminated.return_value = True

    complete = _recover(WorkRepository(repository.path), work, supervisor)

    assert complete["attempts"][-1]["cleanup_state"] == "complete"
    assert complete["state"] == "reconcile"
    supervisor.original_pair_terminated.assert_called_once_with(identity)
    supervisor.terminate.assert_not_called()
    assert WorkScheduler(repository).get_by_attempt(held.attempt_id).state == "held"
    assert _recover(repository, complete, supervisor) == complete
    supervisor.reattach.assert_called_once_with(identity)


@pytest.mark.parametrize(
    "boot,monitor_stat,expected_cleanup",
    [
        ("another-boot", None, "complete"),
        ("cleanup-test-boot", None, "complete"),
        ("cleanup-test-boot", _proc_stat(4101, start=12001), "failed"),
        ("cleanup-test-boot", "malformed stat", "failed"),
        (PermissionError("boot unavailable"), None, "failed"),
    ],
)
def test_restart_without_supervisor_observes_original_pair_only(
    tmp_path, monkeypatch, boot, monitor_stat, expected_cleanup
):
    repository, work, held = _reconciled_held(tmp_path)
    identity = _identity()
    with repository.connection() as connection:
        connection.execute(
            "INSERT INTO work_process_identities "
            "(attempt_id,generation,protocol_version,identity_json,identity_sha256,created_at) "
            "VALUES (?,?,?,?,?,0)",
            (
                held.attempt_id,
                1,
                3,
                json.dumps(identity, sort_keys=True, separators=(",", ":")),
                identity["identity_sha256"],
            ),
        )

    def fake_path(path):
        def read_text():
            if path == "/proc/sys/kernel/random/boot_id":
                if isinstance(boot, BaseException):
                    raise boot
                return boot
            if path == "/proc/4101/stat" and monitor_stat is not None:
                return monitor_stat
            raise FileNotFoundError(path)

        return SimpleNamespace(read_text=read_text)

    monkeypatch.setattr(supervisor_module, "Path", fake_path)
    calls = []
    monkeypatch.setattr(WorkProcessSupervisor, "reattach", lambda *args: calls.append("reattach"))
    monkeypatch.setattr(WorkProcessSupervisor, "terminate", lambda *args: calls.append("terminate"))

    recovered = WorkService(WorkRepository(repository.path)).recover_process_cleanup(
        held.attempt_id, actor_id="owner"
    )

    assert recovered["attempts"][-1]["cleanup_state"] == expected_cleanup
    assert recovered["state"] == "reconcile"
    assert WorkScheduler(repository).get_by_attempt(held.attempt_id).state == "held"
    assert calls == []


def test_pending_cleanup_resumes_after_restart(tmp_path):
    repository, work, held = _reconciled_held(tmp_path)
    identity = _identity()
    with repository.connection() as connection:
        connection.execute(
            "INSERT INTO work_process_identities "
            "(attempt_id,generation,protocol_version,identity_json,identity_sha256,created_at) "
            "VALUES (?,?,?,?,?,0)",
            (
                held.attempt_id,
                1,
                3,
                json.dumps(identity, sort_keys=True, separators=(",", ":")),
                identity["identity_sha256"],
            ),
        )
    pending = repository.record_cleanup(
        held.attempt_id,
        generation=1,
        expected_revision=work["attempts"][-1]["revision"],
        state="pending",
        actor_id="owner",
    )
    supervisor, _ = _supervisor()
    complete = _recover(WorkRepository(repository.path), pending, supervisor)
    assert complete["attempts"][-1]["cleanup_state"] == "complete"
    events = repository.read_events(work["job_id"])["events"]
    assert [event["event_type"] for event in events].count("cleanup.pending") == 1


def test_nonreconciled_writer_cannot_claim_or_touch_supervisor(tmp_path):
    repository, work, held = _reconciled_held(tmp_path)
    supervisor, _ = _supervisor()
    with repository.connection() as connection:
        connection.execute("UPDATE work_items SET state='queued' WHERE id=?", (work["id"],))
    with pytest.raises(WorkConflict):
        _recover(repository, work, supervisor)
    supervisor.reattach.assert_not_called()
    assert repository.get_attempt(held.attempt_id)["cleanup_state"] == "not_requested"


def test_replacement_release_first_blocks_cleanup_before_identity_read(tmp_path):
    repository, work, held = _reconciled_held(tmp_path)
    scheduler = WorkScheduler(
        repository,
        stop_verifier=lambda owner: StoppedWriter(
            owner.attempt_id, owner.generation, owner.revision, "exit"
        ),
    )
    released = scheduler.release_for_replacement(held.id, **_replacement_args(work, held))
    assert released["revision"] > work["revision"]
    supervisor, _ = _supervisor()
    with pytest.raises(WorkConflict):
        _recover(repository, work, supervisor)
    supervisor.reattach.assert_not_called()
    assert repository.get_attempt(held.attempt_id)["cleanup_state"] == "not_requested"


def test_cleanup_claim_first_blocks_service_repository_and_scheduler_replacement(tmp_path):
    repository, work, held = _reconciled_held(tmp_path)
    pending = repository.claim_reconciled_cleanup(
        held.attempt_id,
        generation=1,
        expected_revision=work["attempts"][-1]["revision"],
        actor_id="owner",
    )
    assert pending["attempts"][-1]["cleanup_state"] == "pending"
    calls = []
    scheduler = WorkScheduler(
        repository,
        stop_verifier=lambda owner: calls.append(owner)
        or StoppedWriter(owner.attempt_id, owner.generation, owner.revision, "exit"),
    )
    with pytest.raises(SchedulerConflict):
        WorkService(repository).retry_work(
            work["id"],
            scheduler=scheduler,
            expected_revision=pending["revision"],
            provider="mock_cli",
            evidence=TransitionEvidence(
                generation=1, expected_generation=1, reconciliation_authorized=True
            ),
            actor_id="owner",
            lease_seconds=60,
        )
    with pytest.raises(WorkConflict):
        repository.retry_work(
            work["id"],
            expected_revision=pending["revision"],
            provider="mock_cli",
            evidence=TransitionEvidence(
                generation=1,
                expected_generation=1,
                reconciliation_authorized=True,
                prior_stopped=True,
            ),
            actor_id="owner",
            lease_seconds=60,
        )
    with pytest.raises(SchedulerConflict):
        scheduler.release_for_replacement(held.id, **_replacement_args(pending, held))
    assert calls == []
    assert scheduler.get_by_attempt(held.attempt_id).state == "held"
    assert repository.get_work(work["id"])["attempts"][-1]["generation"] == 1


def test_pending_recovery_requires_held_reservation(tmp_path):
    repository, work, held = _reconciled_held(tmp_path)
    pending = repository.record_cleanup(
        held.attempt_id,
        generation=1,
        expected_revision=work["attempts"][-1]["revision"],
        state="pending",
        actor_id="owner",
    )
    with repository.connection() as connection:
        connection.execute(
            "UPDATE work_scheduler_requests SET state='released',stop_evidence_ref='external' WHERE id=?",
            (held.id,),
        )
    supervisor, _ = _supervisor()
    with pytest.raises(WorkConflict):
        _recover(repository, pending, supervisor)
    supervisor.reattach.assert_not_called()


def test_existing_pending_blocks_scheduler_before_stop_verifier(tmp_path):
    repository, work, held = _reconciled_held(tmp_path)
    pending = repository.record_cleanup(
        held.attempt_id,
        generation=1,
        expected_revision=work["attempts"][-1]["revision"],
        state="pending",
        actor_id="owner",
    )
    calls = []
    scheduler = WorkScheduler(
        repository,
        stop_verifier=lambda owner: calls.append(owner)
        or StoppedWriter(owner.attempt_id, owner.generation, owner.revision, "exit"),
    )
    with pytest.raises(SchedulerConflict):
        scheduler.release_for_replacement(held.id, **_replacement_args(pending, held))
    assert calls == []
    assert scheduler.get_by_attempt(held.attempt_id).state == "held"


def test_scheduler_rechecks_pending_after_stop_verifier(tmp_path):
    repository, work, held = _reconciled_held(tmp_path)

    def proof(owner):
        repository.claim_reconciled_cleanup(
            owner.attempt_id,
            generation=owner.generation,
            expected_revision=owner.revision,
            actor_id="owner",
        )
        return StoppedWriter(owner.attempt_id, owner.generation, owner.revision, "exit")

    scheduler = WorkScheduler(repository, stop_verifier=proof)
    with pytest.raises(SchedulerConflict):
        scheduler.release_for_replacement(held.id, **_replacement_args(work, held))
    assert scheduler.get_by_attempt(held.attempt_id).state == "held"
    assert repository.get_attempt(held.attempt_id)["cleanup_state"] == "pending"
