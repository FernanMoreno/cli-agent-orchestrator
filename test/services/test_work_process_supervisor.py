"""Real Linux process-tree proofs for the Work attempt supervisor."""

from __future__ import annotations

import errno
import hashlib
import json
import multiprocessing
import os
import select
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services import work_process_supervisor
from cli_agent_orchestrator.services.work_process_supervisor import (
    WorkProcessAttempt,
    WorkProcessIdentity,
    WorkProcessIsolationUnavailable,
    WorkProcessStartUncertain,
    WorkProcessState,
    WorkProcessSupervisor,
    WorkProcessSupervisorBlocked,
)
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
from cli_agent_orchestrator.services.work_service import (
    DeliveryUncertain,
    WorkService,
)


def _require_native_work_namespaces(supervisor: WorkProcessSupervisor) -> str:
    """Preflight native proofs without hiding unrelated launcher regressions."""
    try:
        return supervisor._require_capabilities()
    except WorkProcessIsolationUnavailable as exc:
        message = str(exc)
        missing_capability = message in {
            "Linux PID, network, and IPC namespaces are required to contain Work",
            "this Python/Linux host lacks pidfd_open or pidfd_send_signal",
            "this host lacks the close-on-exec startup gate",
            "an executable util-linux unshare command is required for PID namespaces",
        }
        diagnostic = message.partition("; ")[2]
        denied_namespace = message.startswith(
            "rootless user/network/IPC/PID namespace probe failed before work launch: exit=1; "
        ) and diagnostic in {
            "unshare: unshare failed: Operation not permitted",
            "unshare: unshare failed: Permission denied",
            "unshare: write failed /proc/self/uid_map: Operation not permitted",
            "unshare: write failed /proc/self/uid_map: Permission denied",
            "unshare: write failed /proc/self/gid_map: Operation not permitted",
            "unshare: write failed /proc/self/gid_map: Permission denied",
        }
        if not (missing_capability or denied_namespace):
            raise
        pytest.skip(f"host cannot run the required Work namespaces: {exc}")


@pytest.fixture
def native_work_namespaces() -> str:
    return _require_native_work_namespaces(WorkProcessSupervisor())


@pytest.fixture
def namespace_probe_prerequisites() -> None:
    """Omit only executable-double probes whose host cannot reach subprocess.run."""
    if not work_process_supervisor.sys.platform.startswith("linux"):
        pytest.skip("executable namespace guard probes require Linux")
    try:
        WorkProcessSupervisor._require_pidfd_support()
    except WorkProcessIsolationUnavailable as exc:
        pytest.skip(f"executable namespace guard probes unavailable: {exc}")
    if not hasattr(os, "pipe2"):
        pytest.skip("executable namespace guard probes require pipe2")


@pytest.mark.parametrize(
    "diagnostic",
    [
        "unshare: write failed /proc/self/uid_map: Operation not permitted",
        "unshare: unshare failed: Permission denied",
    ],
)
def test_native_namespace_guard_skips_denied_real_probe(
    tmp_path, diagnostic, namespace_probe_prerequisites
):
    arguments = tmp_path / "probe-arguments.json"
    unshare = tmp_path / "unshare"
    unshare.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        f"Path({str(arguments)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        f"sys.stderr.write({diagnostic!r} + '\\n')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    unshare.chmod(0o755)

    with pytest.raises(pytest.skip.Exception, match="host cannot run the required Work namespaces"):
        _require_native_work_namespaces(WorkProcessSupervisor(unshare_path=str(unshare)))

    command = json.loads(arguments.read_text(encoding="utf-8"))
    assert command[:8] == [
        "--user",
        "--map-root-user",
        "--net",
        "--ipc",
        "--pid",
        "--fork",
        "--kill-child=SIGKILL",
        "--",
    ]


@pytest.mark.parametrize(
    ("diagnostic", "returncode"),
    [
        ("synthetic bootstrap regression", 1),
        ("unshare: unexpected runtime failure", 1),
        ("unshare: failed to execute /broken/python: Permission denied", 126),
        ("unshare: failed to execute /broken/python: Permission denied", 1),
        ("unshare: write failed /proc/self/uid_map: Operation not permitted", 126),
    ],
)
def test_native_namespace_guard_does_not_skip_unexpected_probe_failure(
    tmp_path, diagnostic, returncode, namespace_probe_prerequisites
):
    unshare = tmp_path / "unshare"
    unshare.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"sys.stderr.write({diagnostic!r} + '\\n')\n"
        f"sys.exit({returncode})\n",
        encoding="utf-8",
    )
    unshare.chmod(0o755)

    with pytest.raises(WorkProcessIsolationUnavailable, match=diagnostic):
        try:
            _require_native_work_namespaces(WorkProcessSupervisor(unshare_path=str(unshare)))
        except pytest.skip.Exception as exc:
            pytest.fail(f"unexpected probe failure was skipped: {exc}")


def test_native_namespace_guard_does_not_skip_unexpected_runtime_error(monkeypatch):
    supervisor = WorkProcessSupervisor()

    def broken_probe():
        raise RuntimeError("unexpected probe regression")

    monkeypatch.setattr(supervisor, "_require_capabilities", broken_probe)
    with pytest.raises(RuntimeError, match="unexpected probe regression"):
        _require_native_work_namespaces(supervisor)


def _wait_for_json(path: Path, timeout: float = 5.0) -> dict[str, int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.01)
    pytest.fail(f"process tree did not publish {path}")


def _pidfd_exited(pidfd: int) -> bool:
    poller = select.poll()
    poller.register(pidfd, select.POLLIN)
    return bool(poller.poll(0))


@pytest.mark.parametrize("event", [select.POLLERR, select.POLLNVAL, select.POLLERR | select.POLLIN])
def test_reattached_wait_never_confirms_exit_from_pidfd_poll_error(monkeypatch, event):
    monitor_fd = os.open("/dev/null", os.O_RDONLY)
    init_fd = os.open("/dev/null", os.O_RDONLY)
    attempt = WorkProcessAttempt(
        command=("/bin/true",),
        monitor_pid=4101,
        _state=WorkProcessState.RUNNING,
        _monitor=None,
        _monitor_pidfd=monitor_fd,
        _pidfd=init_fd,
    )
    supervisor = WorkProcessSupervisor()
    supervisor._attempt = attempt

    class Poller:
        def __init__(self):
            self.descriptor = None

        def register(self, descriptor, _events):
            self.descriptor = descriptor

        def poll(self, _timeout):
            return [(self.descriptor, event)]

    monkeypatch.setattr(work_process_supervisor.select, "poll", Poller)
    try:
        assert supervisor.wait(attempt, timeout=0) is WorkProcessState.UNCERTAIN
        assert attempt._monitor_pidfd == monitor_fd and attempt._pidfd == init_fd
    finally:
        for descriptor in (monitor_fd, init_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass


def test_seccomp_ack_poll_error_never_reads_ack_or_releases_command(tmp_path, monkeypatch):
    supervisor = WorkProcessSupervisor()
    try:
        supervisor._require_capabilities()
    except WorkProcessIsolationUnavailable as exc:
        pytest.skip(f"Work process isolation unavailable: {exc}")

    real_poll = select.poll
    real_read = os.read
    ack_fds = set()
    ack_error_seen = []
    ack_reads = []

    class ErrorAckPoller:
        def __init__(self):
            self.real = real_poll()
            self.ack_fd = None

        def register(self, descriptor, events):
            self.real.register(descriptor, events)
            if os.readlink(f"/proc/self/fd/{descriptor}").startswith("pipe:"):
                self.ack_fd = descriptor
                ack_fds.add(descriptor)

        def poll(self, timeout):
            events = self.real.poll(timeout)
            if self.ack_fd is not None and events:
                ack_error_seen.append(True)
                return [(self.ack_fd, select.POLLERR | select.POLLIN)]
            return events

    def observed_read(descriptor, size):
        if descriptor in ack_fds:
            ack_reads.append(descriptor)
        return real_read(descriptor, size)

    monkeypatch.setattr(work_process_supervisor.select, "poll", ErrorAckPoller)
    monkeypatch.setattr(work_process_supervisor.os, "read", observed_read)
    marker = tmp_path / "command-released"
    error = None
    try:
        try:
            supervisor.start(_touch_command(marker), persist_identity=lambda _identity: None)
        except (WorkProcessIsolationUnavailable, WorkProcessStartUncertain) as exc:
            error = exc
        assert ack_error_seen
        assert ack_reads == []
        assert error is not None
        assert supervisor.attempt is not None
        assert supervisor.attempt._gate_released is False
        assert not marker.exists()
    finally:
        attempt = supervisor.attempt
        if attempt is not None and attempt.state is not WorkProcessState.TERMINATED:
            supervisor.terminate(attempt, timeout=5.0)


def test_abort_after_pidfd_identity_mismatch_never_signals_unverified_init(monkeypatch):
    monitor_fd = os.open("/dev/null", os.O_RDONLY)
    init_fd = os.open("/dev/null", os.O_RDONLY)
    attempt = WorkProcessAttempt(
        command=("/bin/true",),
        monitor_pid=4101,
        _state=WorkProcessState.STARTING,
        _monitor=None,
        _monitor_pidfd=monitor_fd,
        _pidfd=init_fd,
        _identity=None,
    )
    supervisor = WorkProcessSupervisor(cleanup_timeout=0)
    supervisor._attempt = attempt
    signals = []
    monkeypatch.setattr(
        work_process_supervisor.signal,
        "pidfd_send_signal",
        lambda fd, signum: signals.append((fd, signum)),
    )
    try:
        with pytest.raises(WorkProcessStartUncertain, match="unverified"):
            supervisor._abort_start(attempt, "process identity changed while opening pidfds")
        assert signals == []
        assert attempt.state is WorkProcessState.UNCERTAIN
    finally:
        for descriptor in (monitor_fd, init_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass


@pytest.mark.skipif(sys.platform != "linux", reason="fork context requires Linux")
def test_abort_start_does_not_deadlock_with_reentrant_waiter() -> None:
    """A callback can expose the attempt while start still owns the supervisor lock."""

    def exercise(result: multiprocessing.Queue) -> None:
        supervisor = WorkProcessSupervisor(cleanup_timeout=0)
        attempt = WorkProcessAttempt(
            command=("/bin/true",),
            monitor_pid=4101,
            _state=WorkProcessState.STARTING,
            _monitor=None,
            _identity=WorkProcessIdentity(
                boot_id="boot",
                monitor_pid=4101,
                monitor_start_time_ticks=1,
                init_pid=4102,
                init_start_time_ticks=1,
                init_parent_pid=4101,
                pid_namespace=(1, 1),
                net_namespace=(1, 2),
                ipc_namespace=(1, 3),
                monitor_argv=("unshare",),
            ),
        )
        supervisor._attempt = attempt
        outcomes: list[WorkProcessState] = []
        with supervisor._lock:
            waiter = threading.Thread(
                target=lambda: outcomes.append(supervisor.wait(attempt, timeout=0)), daemon=True
            )
            waiter.start()
            deadline = time.monotonic() + 1.0
            while attempt._wait_lock.acquire(blocking=False):
                attempt._wait_lock.release()
                if time.monotonic() >= deadline:
                    result.put("waiter did not acquire wait lock")
                    return
                time.sleep(0.001)
            try:
                supervisor._abort_start(attempt, "callback failed")
            except WorkProcessStartUncertain:
                pass
            else:
                result.put("abort unexpectedly succeeded")
                return
        waiter.join(timeout=1.0)
        result.put((attempt.state, outcomes, waiter.is_alive()))

    context = multiprocessing.get_context("fork")
    result = context.Queue()
    process = context.Process(target=exercise, args=(result,))
    process.start()
    process.join(timeout=2.0)
    if process.is_alive():
        process.terminate()
        process.join(timeout=1.0)
    assert process.exitcode == 0, "start and wait deadlocked on reversed lock acquisition"
    assert result.get(timeout=1.0) == (
        WorkProcessState.UNCERTAIN,
        [WorkProcessState.UNCERTAIN],
        False,
    )


def test_wait_keeps_unverified_uncertain_pidfds_without_exit_proof():
    monitor_fd = os.open("/dev/null", os.O_RDONLY)
    init_fd = os.open("/dev/null", os.O_RDONLY)
    attempt = WorkProcessAttempt(
        command=("/bin/true",),
        monitor_pid=4101,
        _state=WorkProcessState.UNCERTAIN,
        _monitor=None,
        _monitor_pidfd=monitor_fd,
        _pidfd=init_fd,
        _identity=None,
        reason="unverified pidfd ownership blocks cleanup",
    )
    supervisor = WorkProcessSupervisor()
    supervisor._attempt = attempt
    try:
        assert supervisor.wait(attempt, timeout=0) is WorkProcessState.UNCERTAIN
        assert attempt._monitor_pidfd == monitor_fd and attempt._pidfd == init_fd
        os.fstat(monitor_fd)
        os.fstat(init_fd)
    finally:
        for descriptor in (monitor_fd, init_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass


def test_terminate_keeps_unverified_uncertain_pidfds_without_signal(monkeypatch):
    monitor_fd = os.open("/dev/null", os.O_RDONLY)
    init_fd = os.open("/dev/null", os.O_RDONLY)
    attempt = WorkProcessAttempt(
        command=("/bin/true",),
        monitor_pid=4101,
        _state=WorkProcessState.UNCERTAIN,
        _monitor=None,
        _monitor_pidfd=monitor_fd,
        _pidfd=init_fd,
        _identity=None,
        reason="unverified pidfd ownership blocks cleanup",
    )
    supervisor = WorkProcessSupervisor()
    supervisor._attempt = attempt
    signals = []
    monkeypatch.setattr(
        work_process_supervisor.signal,
        "pidfd_send_signal",
        lambda fd, signum: signals.append((fd, signum)),
    )
    try:
        assert supervisor.terminate(attempt, timeout=0) is WorkProcessState.UNCERTAIN
        assert signals == []
        assert attempt._monitor_pidfd == monitor_fd and attempt._pidfd == init_fd
        os.fstat(monitor_fd)
        os.fstat(init_fd)
    finally:
        for descriptor in (monitor_fd, init_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _pidfd_target_pid(pidfd: int) -> int:
    """Read the live PID pinned by a pidfd, failing if its target is gone."""
    try:
        fields = Path(f"/proc/self/fdinfo/{pidfd}").read_text().splitlines()
        pid_fields = [line.split(":", 1)[1].strip() for line in fields if line.startswith("Pid:")]
        assert (
            len(pid_fields) == 1 and pid_fields[0] != "-1"
        ), f"pidfd {pidfd} does not identify a live process"
        return int(pid_fields[0])
    except (OSError, ValueError, IndexError) as exc:
        raise AssertionError(f"could not validate pidfd {pidfd}") from exc


def _assert_process_identity_exited(pid: int, start_time_ticks: int) -> None:
    """Verify the exact proc identity is gone or is only a dead zombie."""
    try:
        _parent, current_start, state = WorkProcessSupervisor._read_proc_stat(pid)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise AssertionError(f"could not verify process {pid} exit through procfs") from exc
    assert current_start != start_time_ticks or state in {
        "Z",
        "X",
        "x",
    }, f"process {pid} with the expected start time is still active"


def _wait_pidfds_exited(pidfds: list[int], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(_pidfd_exited(pidfd) for pidfd in pidfds):
            return True
        time.sleep(0.01)
    return all(_pidfd_exited(pidfd) for pidfd in pidfds)


def _touch_command(path: Path) -> list[str]:
    code = "from pathlib import Path; import sys; Path(sys.argv[1]).touch()"
    return [sys.executable, "-c", code, str(path)]


def _discard_identity(_identity: object) -> None:
    """These lifecycle tests do not exercise restart persistence."""


def _cleanup_started_attempt(
    supervisor: WorkProcessSupervisor,
    attempt: WorkProcessAttempt | None,
    *,
    timeout: float = 5.0,
    termination_proof: WorkProcessAttempt | None = None,
) -> None:
    """Resolve even a start() attempt raised before its return value was assigned."""
    owned_attempt = attempt or supervisor.attempt
    if owned_attempt is None or owned_attempt.state is WorkProcessState.TERMINATED:
        return
    if owned_attempt._pidfd is None or owned_attempt._monitor_pidfd is None:
        # The invalid-monitor test can fail before the supervisor opens pidfds.
        # Recover both exact handles from the still-running monitor; never count
        # killing only the monitor as proof that namespace PID 1 is gone.
        new_monitor_pidfd: int | None = None
        new_init_pidfd: int | None = None
        try:
            if owned_attempt._monitor_pidfd is not None:
                if not _pidfd_exited(owned_attempt._monitor_pidfd):
                    assert (
                        _pidfd_target_pid(owned_attempt._monitor_pidfd) == owned_attempt.monitor_pid
                    ), "monitor pidfd does not match the attempt monitor PID"
            else:
                new_monitor_pidfd = os.pidfd_open(owned_attempt.monitor_pid)
            children_path = Path(
                f"/proc/{owned_attempt.monitor_pid}/task/" f"{owned_attempt.monitor_pid}/children"
            )
            try:
                children = [int(pid) for pid in children_path.read_text().split()]
            except (FileNotFoundError, ProcessLookupError):
                children = []
            except (OSError, ValueError) as exc:
                raise AssertionError(
                    "could not inspect monitor children to identify namespace PID 1"
                ) from exc
            namespace_inits = [
                pid for pid in children if WorkProcessSupervisor._is_namespace_init(pid)
            ]
            if not namespace_inits and not children:
                # An empty child list alone is not proof that PID 1 is gone;
                # it may have been reparented. Close the partial handle only
                # after a separate reattach attempt proved this exact identity.
                proof_identity = (
                    termination_proof._identity if termination_proof is not None else None
                )
                assert termination_proof is not None and (
                    termination_proof.state is WorkProcessState.TERMINATED
                ), (
                    "no live namespace PID 1 child and no terminated reattach proof; "
                    "retaining partial pidfds and UNCERTAIN state"
                )
                assert (
                    proof_identity is not None
                ), "terminated reattach attempt has no process identity"
                assert (
                    proof_identity.monitor_pid == owned_attempt.monitor_pid
                ), "reattach proof identifies a different monitor"
                assert (
                    owned_attempt._identity is None or owned_attempt._identity == proof_identity
                ), "reattach proof differs from the original attempt identity"
                assert (
                    proof_identity.boot_id
                    == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                ), "reattach proof belongs to another kernel boot"
                monitor_pidfd = owned_attempt._monitor_pidfd or new_monitor_pidfd
                assert monitor_pidfd is not None and _pidfd_exited(
                    monitor_pidfd
                ), "reattach proof did not establish monitor pidfd exit"
                if owned_attempt._pidfd is not None:
                    assert _pidfd_exited(
                        owned_attempt._pidfd
                    ), "reattach proof did not establish namespace PID 1 pidfd exit"
                if owned_attempt._monitor is not None:
                    returncode = owned_attempt._monitor.poll()
                    assert (
                        returncode is not None
                    ), "monitor Popen handle has not reaped the exited monitor"
                    owned_attempt.returncode = returncode
                _assert_process_identity_exited(
                    proof_identity.init_pid,
                    proof_identity.init_start_time_ticks,
                )
                _assert_process_identity_exited(
                    proof_identity.monitor_pid,
                    proof_identity.monitor_start_time_ticks,
                )
                if owned_attempt._pidfd is not None:
                    os.close(owned_attempt._pidfd)
                    owned_attempt._pidfd = None
                if owned_attempt._monitor_pidfd is not None:
                    os.close(owned_attempt._monitor_pidfd)
                    owned_attempt._monitor_pidfd = None
                raise AssertionError(
                    "reattach proof confirms exit; partial pidfds were closed, "
                    "but the original attempt stays UNCERTAIN"
                )
            assert len(namespace_inits) == 1, (
                "cleanup requires exactly one validated namespace PID 1 child; "
                f"observed children={children}, namespace_inits={namespace_inits}"
            )
            init_pid = namespace_inits[0]
            if owned_attempt._identity is not None and init_pid != owned_attempt._identity.init_pid:
                raise AssertionError("namespace PID 1 differs from persisted attempt identity")
            if owned_attempt._pidfd is not None:
                if not _pidfd_exited(owned_attempt._pidfd):
                    assert (
                        _pidfd_target_pid(owned_attempt._pidfd) == init_pid
                    ), "namespace init pidfd does not match the validated child"
            else:
                new_init_pidfd = os.pidfd_open(init_pid)
            children_after = [int(pid) for pid in children_path.read_text().split()]
            assert children_after == [init_pid] and WorkProcessSupervisor._is_namespace_init(
                init_pid
            ), "namespace PID 1 changed while its pidfd was being opened"
            if new_init_pidfd is not None:
                assert (
                    _pidfd_target_pid(new_init_pidfd) == init_pid
                ), "new namespace init pidfd does not match the validated child"
            if new_monitor_pidfd is not None:
                assert (
                    _pidfd_target_pid(new_monitor_pidfd) == owned_attempt.monitor_pid
                ), "new monitor pidfd does not match the attempt monitor PID"

            if owned_attempt._monitor_pidfd is None:
                assert new_monitor_pidfd is not None
                owned_attempt._monitor_pidfd = new_monitor_pidfd
                new_monitor_pidfd = None
            if owned_attempt._pidfd is None:
                assert new_init_pidfd is not None
                owned_attempt._pidfd = new_init_pidfd
                new_init_pidfd = None
        finally:
            if new_monitor_pidfd is not None:
                os.close(new_monitor_pidfd)
            if new_init_pidfd is not None:
                os.close(new_init_pidfd)
    state = supervisor.terminate(owned_attempt, timeout=timeout)
    if state is not WorkProcessState.TERMINATED:
        state = supervisor.wait(owned_attempt, timeout=timeout)
    assert state is WorkProcessState.TERMINATED, (
        f"test cleanup left attempt {owned_attempt.monitor_pid} {state.value}: "
        f"{owned_attempt.reason}"
    )


def _cleanup_partial_attempt_with_reattach_fallback(
    supervisor: WorkProcessSupervisor,
    recovery: WorkProcessSupervisor,
    attempt: WorkProcessAttempt | None,
    identity: WorkProcessIdentity | None,
    reattached: WorkProcessAttempt | None,
) -> WorkProcessAttempt | None:
    """Use reattach, then original cleanup; retain any still-live reattach pidfds."""
    owned_attempt = attempt or supervisor.attempt
    owned_identity = identity or (owned_attempt._identity if owned_attempt is not None else None)
    if (
        owned_attempt is not None
        and owned_attempt.state is WorkProcessState.TERMINATED
        and reattached is None
    ):
        return reattached

    try:
        try:
            if owned_identity is not None and reattached is None:
                reattached = recovery.reattach(owned_identity)
            if reattached is not None and reattached.state is not WorkProcessState.TERMINATED:
                state = recovery.terminate(reattached, timeout=2.0)
                if state is not WorkProcessState.TERMINATED:
                    state = recovery.wait(reattached, timeout=2.0)
                assert state is WorkProcessState.TERMINATED, (
                    f"reattach cleanup left {reattached.monitor_pid} {state.value}: "
                    f"{reattached.reason}"
                )
        except BaseException as recovery_error:
            try:
                _cleanup_started_attempt(supervisor, owned_attempt, timeout=2.0)
            except BaseException as fallback_error:
                raise AssertionError(
                    "reattach cleanup failed and original-attempt cleanup remains unproven"
                ) from fallback_error
            if reattached is None:
                raise recovery_error
            try:
                state = recovery.wait(reattached, timeout=2.0)
                assert state is WorkProcessState.TERMINATED, (
                    f"original fallback ran, but reattach remains {state.value}: "
                    f"{reattached.reason}"
                )
            except BaseException as unresolved_error:
                raise AssertionError(
                    "original-attempt fallback ran but reattach still cannot prove termination"
                ) from unresolved_error
            return reattached

        if owned_attempt is not None and owned_attempt.state is not WorkProcessState.TERMINATED:
            if owned_attempt._pidfd is not None or owned_attempt._monitor_pidfd is not None:
                if reattached is not None and reattached.state is WorkProcessState.TERMINATED:
                    try:
                        _cleanup_started_attempt(
                            supervisor,
                            owned_attempt,
                            timeout=2.0,
                            termination_proof=reattached,
                        )
                    except AssertionError as exc:
                        assert "reattach proof confirms exit; partial pidfds were closed" in str(
                            exc
                        )
                        assert owned_attempt.state is WorkProcessState.UNCERTAIN
                        assert owned_attempt._pidfd is None and owned_attempt._monitor_pidfd is None
                else:
                    _cleanup_started_attempt(supervisor, owned_attempt, timeout=2.0)
        return reattached
    finally:
        try:
            if reattached is not None and reattached.state is not WorkProcessState.TERMINATED:
                try:
                    _cleanup_started_attempt(recovery, reattached, timeout=2.0)
                except BaseException as final_cleanup_error:
                    raise AssertionError(
                        "reattach cleanup remains UNCERTAIN; retaining its pidfds on "
                        "recovery.attempt for reattachment"
                    ) from final_cleanup_error
        finally:
            if (
                reattached is not None
                and reattached.state is WorkProcessState.TERMINATED
                and owned_attempt is not None
                and owned_attempt.state is WorkProcessState.UNCERTAIN
                and (owned_attempt._pidfd is not None or owned_attempt._monitor_pidfd is not None)
            ):
                try:
                    _cleanup_started_attempt(
                        supervisor,
                        owned_attempt,
                        timeout=2.0,
                        termination_proof=reattached,
                    )
                except AssertionError as exc:
                    assert "reattach proof confirms exit; partial pidfds were closed" in str(exc)
                    assert owned_attempt._pidfd is None and owned_attempt._monitor_pidfd is None
            if owned_attempt is not None and owned_attempt.state is WorkProcessState.TERMINATED:
                for descriptor_name in ("_pidfd", "_monitor_pidfd"):
                    descriptor = getattr(owned_attempt, descriptor_name)
                    if descriptor is not None:
                        os.close(descriptor)
                        setattr(owned_attempt, descriptor_name, None)
            if reattached is not None and reattached.state is WorkProcessState.TERMINATED:
                for descriptor_name in ("_pidfd", "_monitor_pidfd"):
                    descriptor = getattr(reattached, descriptor_name)
                    if descriptor is not None:
                        os.close(descriptor)
                        setattr(reattached, descriptor_name, None)


def test_terminate_waits_for_setsid_double_fork_descendants_to_disappear(
    tmp_path: Path,
    native_work_namespaces: str,
) -> None:
    """A detached grandchild must be gone before the attempt reports TERMINATED."""
    marker = tmp_path / "tree.json"
    code = r"""
import json, os, signal, sys, time

marker = sys.argv[1]
def host_pid():
    with open('/proc/self/status', encoding='utf-8') as status:
        for line in status:
            if line.startswith('NSpid:'):
                return int(line.split()[1])
    raise RuntimeError('procfs did not report NSpid')

root_pid = host_pid()
first_child = os.fork()
if first_child == 0:
    os.setsid()
    session_leader_pid = host_pid()
    grandchild = os.fork()
    if grandchild:
        os._exit(0)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    with open(marker, 'w', encoding='utf-8') as output:
        json.dump(
            {
                'init': root_pid,
                'session_leader': session_leader_pid,
                'grandchild': host_pid(),
            },
            output,
        )
    while True:
        time.sleep(0.05)

os.waitpid(first_child, 0)
while not os.path.exists(marker):
    time.sleep(0.01)
while True:
    time.sleep(0.05)
    """
    supervisor = WorkProcessSupervisor()
    attempt = None
    pidfds: dict[str, int] = {}
    try:
        attempt = supervisor.start(
            [sys.executable, "-c", code, str(marker)],
            persist_identity=_discard_identity,
        )
        pids = _wait_for_json(marker)
        pidfds = {name: os.pidfd_open(pids[name]) for name in ("init", "grandchild")}
        assert os.getsid(pids["grandchild"]) == pids["session_leader"]
        assert os.getsid(pids["grandchild"]) != os.getsid(attempt.monitor_pid)
        assert supervisor.wait(attempt, timeout=0.02) is WorkProcessState.RUNNING

        assert supervisor.terminate(attempt, timeout=5.0) is WorkProcessState.TERMINATED
        assert _wait_pidfds_exited(
            list(pidfds.values())
        ), "a recorded namespace process remained alive"
        assert all(not Path(f"/proc/{pid}").exists() for pid in pids.values())
    finally:
        try:
            _cleanup_started_attempt(supervisor, attempt)
            if not _wait_pidfds_exited(list(pidfds.values()), timeout=0):
                for fd in pidfds.values():
                    try:
                        signal.pidfd_send_signal(fd, signal.SIGKILL)
                    except OSError:
                        pass
                _wait_pidfds_exited(list(pidfds.values()))
        finally:
            for fd in pidfds.values():
                os.close(fd)


def test_uncertain_cleanup_blocks_start_until_explicit_reconciliation(
    tmp_path: Path,
    native_work_namespaces: str,
) -> None:
    supervisor = WorkProcessSupervisor()
    attempt = None
    marker = tmp_path / "must-not-run"
    try:
        attempt = supervisor.start(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            persist_identity=_discard_identity,
        )
        assert (
            supervisor.uncertain(attempt, "cleanup acknowledgement was lost")
            is WorkProcessState.UNCERTAIN
        )
        assert supervisor.is_blocked
        with pytest.raises(WorkProcessSupervisorBlocked):
            supervisor.start(_touch_command(marker), persist_identity=_discard_identity)
        assert not marker.exists()

        assert supervisor.terminate(attempt, timeout=5.0) is WorkProcessState.TERMINATED
        assert not supervisor.is_blocked
    finally:
        _cleanup_started_attempt(supervisor, attempt)


def test_wait_reconciles_uncertain_attempt_after_natural_exit(
    tmp_path: Path,
    native_work_namespaces: str,
) -> None:
    """A later monitor/pidfd observation can resolve uncertainty without a signal."""
    allow_exit = tmp_path / "allow-exit"
    result = tmp_path / "natural-result.txt"
    code = """
from pathlib import Path
import sys, time

allow_exit = Path(sys.argv[1])
result = Path(sys.argv[2])
while not allow_exit.exists():
    time.sleep(0.01)
result.write_text("natural exit", encoding="utf-8")
    """
    supervisor = WorkProcessSupervisor()
    attempt = None
    try:
        attempt = supervisor.start(
            [sys.executable, "-c", code, str(allow_exit), str(result)],
            persist_identity=_discard_identity,
        )
        assert (
            supervisor.uncertain(attempt, "cleanup acknowledgement was lost")
            is WorkProcessState.UNCERTAIN
        )
        assert supervisor.is_blocked

        allow_exit.touch()
        assert supervisor.wait(attempt, timeout=5.0) is WorkProcessState.TERMINATED
        assert attempt.returncode == 0
        assert result.read_text(encoding="utf-8") == "natural exit"
        assert not supervisor.is_blocked
    finally:
        _cleanup_started_attempt(supervisor, attempt)


def test_missing_unshare_rejects_before_the_command_has_any_effect(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-run"
    supervisor = WorkProcessSupervisor(unshare_path="/missing/unshare")

    with pytest.raises(WorkProcessIsolationUnavailable, match="unshare"):
        supervisor.start(_touch_command(marker), persist_identity=_discard_identity)

    assert not marker.exists()


def test_pid_namespace_capability_probe_does_not_inherit_environment(
    tmp_path: Path, monkeypatch
) -> None:
    unshare = tmp_path / "unshare"
    unshare.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    unshare.chmod(0o755)
    probe_options: dict[str, object] = {}
    secret_name = "CAO_T097_CANARY_SECRET"
    monkeypatch.setenv(secret_name, "server-secret-must-not-reach-probe")

    def successful_probe(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        probe_options.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(work_process_supervisor.subprocess, "run", successful_probe)
    supervisor = WorkProcessSupervisor(unshare_path=str(unshare), capability_timeout=2.5)

    assert supervisor._require_capabilities() == str(unshare)
    assert probe_options.get("env") == {}
    assert secret_name not in probe_options.get("env", {})
    assert probe_options["stdin"] is subprocess.DEVNULL
    assert probe_options["timeout"] == 2.5


def test_pid_namespace_capability_probe_requires_network_and_ipc_namespaces(
    tmp_path: Path, monkeypatch
) -> None:
    unshare = tmp_path / "unshare"
    unshare.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    unshare.chmod(0o755)
    probe_command: list[str] = []

    def successful_probe(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        probe_command.extend(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(work_process_supervisor.subprocess, "run", successful_probe)
    supervisor = WorkProcessSupervisor(unshare_path=str(unshare))

    assert supervisor._require_capabilities() == str(unshare)
    assert "--net" in probe_command
    assert "--ipc" in probe_command


def test_worker_uses_private_network_and_ipc_namespaces_and_cannot_reach_host_loopback(
    tmp_path: Path,
) -> None:
    report = tmp_path / "namespace-report.json"
    code = r"""
import json, os, socket, sys
from pathlib import Path

def namespace_identity(kind):
    value = os.stat(f"/proc/self/ns/{kind}")
    return f"{value.st_dev}:{value.st_ino}"

probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
probe.settimeout(1.0)
try:
    probe.connect(("127.0.0.1", int(sys.argv[2])))
except OSError:
    loopback = "blocked"
else:
    loopback = "connected"
finally:
    probe.close()

Path(sys.argv[1]).write_text(json.dumps({
    "net": namespace_identity("net"),
    "ipc": namespace_identity("ipc"),
    "loopback": loopback,
}), encoding="utf-8")
"""
    supervisor = WorkProcessSupervisor()
    try:
        supervisor._require_capabilities()
    except WorkProcessIsolationUnavailable as exc:
        pytest.skip(f"host cannot run the required Work namespaces: {exc}")

    host_net = os.stat("/proc/self/ns/net")
    host_ipc = os.stat("/proc/self/ns/ipc")
    host_net_identity = f"{host_net.st_dev}:{host_net.st_ino}"
    host_ipc_identity = f"{host_ipc.st_dev}:{host_ipc.st_ino}"

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.1)
        port = listener.getsockname()[1]
        attempt = None
        try:
            attempt = supervisor.start(
                [
                    sys.executable,
                    "-c",
                    code,
                    str(report),
                    str(port),
                ],
                persist_identity=_discard_identity,
            )
            assert supervisor.wait(attempt, timeout=5.0) is WorkProcessState.TERMINATED
            result = json.loads(report.read_text(encoding="utf-8"))
            assert result["loopback"] == "blocked"
            assert result["net"] != host_net_identity
            assert result["ipc"] != host_ipc_identity
            with pytest.raises(socket.timeout):
                listener.accept()
        finally:
            _cleanup_started_attempt(supervisor, attempt)


def test_worker_seccomp_blocks_memfd_but_allows_fork_exec_and_subprocess(
    tmp_path: Path,
) -> None:
    supervisor = WorkProcessSupervisor()
    try:
        supervisor._require_capabilities()
    except WorkProcessIsolationUnavailable as exc:
        pytest.skip(f"host cannot run the required Work namespaces: {exc}")

    baseline_code = r"""
import ctypes, json
libc = ctypes.CDLL(None, use_errno=True)
ctypes.set_errno(0)
result = libc.syscall(ctypes.c_long(319), ctypes.c_void_p(0), ctypes.c_ulong(0))
print(json.dumps({"result": result, "errno": ctypes.get_errno()}))
"""
    baseline = subprocess.run(
        [sys.executable, "-I", "-c", baseline_code],
        env={},
        capture_output=True,
        text=True,
        check=False,
    )
    if baseline.returncode != 0:
        pytest.skip(f"could not establish memfd_create baseline: {baseline.stderr}")
    baseline_result = json.loads(baseline.stdout)
    if baseline_result != {"result": -1, "errno": errno.EFAULT}:
        pytest.skip(
            "inherited seccomp policy prevents a clean memfd_create baseline "
            f"(return={baseline_result['result']}, errno={baseline_result['errno']})"
        )

    report = tmp_path / "worker-seccomp-report.json"
    identity_path = tmp_path / "worker-identity.json"
    descendant_report = tmp_path / "descendant-seccomp-report.json"
    code = r"""
import ctypes, json, os, subprocess, sys
from pathlib import Path

identity = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
libc = ctypes.CDLL(None, use_errno=True)
ctypes.set_errno(0)
memfd_result = libc.syscall(ctypes.c_long(319), ctypes.c_void_p(0), ctypes.c_ulong(0))
memfd_errno = ctypes.get_errno()

descendant_code = (
    "import ctypes,json,sys; from pathlib import Path; "
    "libc=ctypes.CDLL(None,use_errno=True); ctypes.set_errno(0); "
    "result=libc.syscall(ctypes.c_long(319),ctypes.c_void_p(0),ctypes.c_ulong(0)); "
    "Path(sys.argv[1]).write_text(json.dumps({'result':result,'errno':ctypes.get_errno()}))"
)
pid = os.fork()
if pid == 0:
    os.execve(
        sys.executable,
        [sys.executable, "-c", descendant_code, sys.argv[3]],
        os.environ.copy(),
    )
_, status = os.waitpid(pid, 0)
fork_exec_ok = os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
descendant = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))

child = subprocess.run(
    [sys.executable, "-c", "print('subprocess-exec-ok')"],
    capture_output=True,
    text=True,
    check=False,
    preexec_fn=lambda: None,
)
Path(sys.argv[1]).write_text(json.dumps({
    "memfd_result": memfd_result,
    "memfd_errno": memfd_errno,
    "identity": identity,
    "descendant": descendant,
    "fork_exec_ok": fork_exec_ok,
    "subprocess_returncode": child.returncode,
    "subprocess_stdout": child.stdout,
}), encoding="utf-8")
"""

    def persist_identity(identity: object) -> None:
        payload = identity.to_dict()  # type: ignore[attr-defined]
        temp_path = identity_path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as output:
            json.dump(payload, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, identity_path)
        dir_fd = os.open(identity_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    attempt = None
    try:
        attempt = supervisor.start(
            [
                sys.executable,
                "-c",
                code,
                str(report),
                str(identity_path),
                str(descendant_report),
            ],
            persist_identity=persist_identity,
        )
        assert supervisor.wait(attempt, timeout=10.0) is WorkProcessState.TERMINATED
        observed = json.loads(report.read_text(encoding="utf-8"))
        persisted_identity = json.loads(identity_path.read_text(encoding="utf-8"))
        assert observed == {
            "memfd_result": -1,
            "memfd_errno": errno.EPERM,
            "identity": persisted_identity,
            "descendant": {"result": -1, "errno": errno.EPERM},
            "fork_exec_ok": True,
            "subprocess_returncode": 0,
            "subprocess_stdout": "subprocess-exec-ok\n",
        }
    finally:
        _cleanup_started_attempt(supervisor, attempt)


def test_seccomp_install_failure_is_after_durable_identity_and_never_releases_command(
    tmp_path: Path, monkeypatch
) -> None:
    """An installer failure cannot run work or lose the monitor's reattach identity."""
    supervisor = WorkProcessSupervisor(cleanup_timeout=0.0)
    try:
        supervisor._require_capabilities()
    except WorkProcessIsolationUnavailable as exc:
        pytest.skip(f"host cannot run the required Work namespaces: {exc}")

    identity_path = tmp_path / "identity.json"
    marker = tmp_path / "command-must-not-run"
    # Model an import/installer error in the isolated bootstrap. The parent
    # must have persisted identity before requesting installation; the child
    # reports failure and waits for cleanup instead of receiving RELEASE.
    failing_bootstrap = r"""
import os, sys, time
gate_fd = int(sys.argv[1])
ack_fd = int(sys.argv[2])
worker_argv = sys.argv[3:]
if os.read(gate_fd, 1) != b"I":
    os._exit(73)
def report_failure():
    try:
        os.write(ack_fd, b"E")
    except OSError:
        pass
    while True:
        time.sleep(60)
if not os.path.exists(worker_argv[-2]):
    report_failure()
try:
    import __cao_test_missing_seccomp_installer__
except ImportError:
    report_failure()
try:
    os.write(ack_fd, b"S")
except OSError:
    report_failure()
os._exit(75)
"""
    monkeypatch.setattr(work_process_supervisor, "_BOOTSTRAP", failing_bootstrap)
    persisted: dict[str, object] = {}

    def persist_identity(identity: object) -> None:
        payload = identity.to_dict()  # type: ignore[attr-defined]
        temp_path = identity_path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as output:
            json.dump(payload, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, identity_path)
        dir_fd = os.open(identity_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        persisted.update(payload)

    worker = [
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; Path(sys.argv[2]).touch()",
        str(identity_path),
        str(marker),
    ]
    attempt = None
    recovery = None
    reattached = None
    cleanup_signal_attempts = []
    try:
        with pytest.raises(WorkProcessStartUncertain) as start_error:
            with monkeypatch.context() as no_kill:
                no_kill.setattr(
                    work_process_supervisor.signal,
                    "pidfd_send_signal",
                    lambda fd, signum: cleanup_signal_attempts.append((fd, signum)),
                )
                attempt = supervisor.start(worker, persist_identity=persist_identity)

        attempt = start_error.value.attempt
        assert attempt._identity is not None
        assert cleanup_signal_attempts == [(attempt._pidfd, signal.SIGKILL)]
        stored = json.loads(identity_path.read_text(encoding="utf-8"))
        assert stored == persisted == attempt._identity.to_dict()
        recovery = WorkProcessSupervisor(cleanup_timeout=2.0)
        # The failure left an uncertain but identifiable process. A fresh
        # supervisor can reattach to it and confirm cleanup by killing it.
        reattached = recovery.reattach(stored)
        assert recovery.terminate(reattached, timeout=2.0) is WorkProcessState.TERMINATED

        assert identity_path.exists()
        assert not marker.exists()
    finally:
        try:
            if recovery is not None:
                _cleanup_started_attempt(recovery, reattached, timeout=2.0)
        finally:
            _cleanup_started_attempt(supervisor, attempt, timeout=2.0)


def test_invalid_isolation_after_matching_pidfd_snapshots_keeps_cleanup_identity(
    tmp_path: Path, monkeypatch
) -> None:
    supervisor = WorkProcessSupervisor(cleanup_timeout=2.0)
    try:
        supervisor._require_capabilities()
    except WorkProcessIsolationUnavailable as exc:
        pytest.skip(f"host cannot run the required Work namespaces: {exc}")
    marker = tmp_path / "command-must-not-run"
    monkeypatch.setattr(
        supervisor,
        "_verify_process_isolation",
        lambda _identity: (False, "injected isolation failure"),
    )
    try:
        with pytest.raises(WorkProcessIsolationUnavailable, match="injected isolation failure"):
            supervisor.start(_touch_command(marker), persist_identity=_discard_identity)
        assert supervisor.attempt is not None
        assert supervisor.attempt._identity is not None
        assert not marker.exists()
    finally:
        attempt = supervisor.attempt
        if attempt is not None and attempt._monitor is not None:
            try:
                attempt._monitor.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                attempt._monitor.kill()
                attempt._monitor.wait(timeout=2.0)
        if attempt is not None:
            for descriptor in (attempt._pidfd, attempt._monitor_pidfd):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass


def test_partial_pidfd_start_cleanup_recovers_and_closes_both_handles(monkeypatch) -> None:
    """Cleanup must pin PID 1 if startup fails between the two pidfd opens."""
    supervisor = WorkProcessSupervisor(cleanup_timeout=0.0)
    try:
        supervisor._require_capabilities()
    except WorkProcessIsolationUnavailable as exc:
        pytest.skip(f"host cannot run the required Work namespaces: {exc}")

    # Keep the namespace init alive after _abort_start closes the gate so the
    # helper can deterministically recover the missing pidfd.
    monkeypatch.setattr(
        work_process_supervisor,
        "_BOOTSTRAP",
        r"""import os, sys, time
gate_fd = int(sys.argv[1])
while True:
    try:
        token = os.read(gate_fd, 1)
    except OSError:
        token = b""
    if token:
        os._exit(75)
    time.sleep(0.05)
""",
    )

    real_pidfd_open = os.pidfd_open

    def fail_namespace_init_pidfd(pid: int, flags: int = 0) -> int:
        if WorkProcessSupervisor._is_namespace_init(pid):
            raise OSError(errno.EMFILE, "injected PID 1 pidfd exhaustion")
        return real_pidfd_open(pid, flags)

    attempt = None
    try:
        with pytest.raises(WorkProcessStartUncertain) as start_error:
            with monkeypatch.context() as partial_pidfds:
                partial_pidfds.setattr(
                    work_process_supervisor.os,
                    "pidfd_open",
                    fail_namespace_init_pidfd,
                )
                supervisor.start(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    persist_identity=_discard_identity,
                )

        attempt = start_error.value.attempt
        assert attempt._monitor_pidfd is not None
        assert attempt._pidfd is None
        _cleanup_started_attempt(supervisor, attempt, timeout=2.0)
        assert attempt.state is WorkProcessState.TERMINATED
        assert attempt._monitor_pidfd is None
        assert attempt._pidfd is None
    finally:
        _cleanup_started_attempt(supervisor, attempt, timeout=2.0)


def test_partial_pidfd_after_reattach_closes_local_fd_without_claiming_terminated(
    monkeypatch,
) -> None:
    """Reattach proves exit; the original partial attempt stays UNCERTAIN."""
    supervisor = WorkProcessSupervisor(cleanup_timeout=0.0)
    try:
        supervisor._require_capabilities()
    except WorkProcessIsolationUnavailable as exc:
        pytest.skip(f"host cannot run the required Work namespaces: {exc}")

    monkeypatch.setattr(
        work_process_supervisor,
        "_BOOTSTRAP",
        r"""import os, sys, time
gate_fd = int(sys.argv[1])
while True:
    try:
        token = os.read(gate_fd, 1)
    except OSError:
        token = b""
    if token:
        os._exit(75)
    time.sleep(0.05)
""",
    )

    identities: list[WorkProcessIdentity] = []
    original_read_identity = supervisor._read_process_identity

    def capture_identity(
        monitor_pid: int, init_pid: int, *, strict_monitor_argv: bool = True
    ) -> WorkProcessIdentity:
        identity = original_read_identity(
            monitor_pid, init_pid, strict_monitor_argv=strict_monitor_argv
        )
        identities.append(identity)
        return identity

    monkeypatch.setattr(supervisor, "_read_process_identity", capture_identity)
    real_pidfd_open = os.pidfd_open

    def fail_namespace_init_pidfd(pid: int, flags: int = 0) -> int:
        if WorkProcessSupervisor._is_namespace_init(pid):
            raise OSError(errno.EMFILE, "injected PID 1 pidfd exhaustion")
        return real_pidfd_open(pid, flags)

    recovery = WorkProcessSupervisor(cleanup_timeout=2.0)
    attempt = None
    reattached = None
    known_identity: WorkProcessIdentity | None = None
    try:
        with pytest.raises(WorkProcessStartUncertain) as start_error:
            with monkeypatch.context() as partial_pidfds:
                partial_pidfds.setattr(
                    work_process_supervisor.os,
                    "pidfd_open",
                    fail_namespace_init_pidfd,
                )
                supervisor.start(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    persist_identity=_discard_identity,
                )

        attempt = start_error.value.attempt
        assert attempt._monitor_pidfd is not None
        assert attempt._pidfd is None
        known_identity = identities[0]
        assert len(identities) == 2
        assert identities[1] == known_identity
        assert attempt._identity == known_identity
        reattached = recovery.reattach(known_identity)
        assert recovery.terminate(reattached, timeout=2.0) is WorkProcessState.TERMINATED

        # The fresh supervisor proved termination for the exact saved process
        # identity. The original supervisor lacks PID 1's pidfd, so it must
        # release its partial monitor pidfd without upgrading UNCERTAIN.
        with pytest.raises(
            AssertionError,
            match="reattach proof confirms exit; partial pidfds were closed",
        ):
            _cleanup_started_attempt(
                supervisor,
                attempt,
                timeout=2.0,
                termination_proof=reattached,
            )
        assert attempt._monitor_pidfd is None
        assert attempt._pidfd is None
        assert attempt.state is WorkProcessState.UNCERTAIN
        assert reattached.state is WorkProcessState.TERMINATED
    finally:
        reattached = _cleanup_partial_attempt_with_reattach_fallback(
            supervisor,
            recovery,
            attempt,
            known_identity,
            reattached,
        )


def test_partial_pidfd_cleanup_falls_back_when_reattach_fails(monkeypatch) -> None:
    """A failed recovery reattach still cleans through the original attempt."""
    supervisor = WorkProcessSupervisor(cleanup_timeout=0.0)
    try:
        supervisor._require_capabilities()
    except WorkProcessIsolationUnavailable as exc:
        pytest.skip(f"host cannot run the required Work namespaces: {exc}")

    monkeypatch.setattr(
        work_process_supervisor,
        "_BOOTSTRAP",
        r"""import os, sys, time
gate_fd = int(sys.argv[1])
while True:
    try:
        token = os.read(gate_fd, 1)
    except OSError:
        token = b""
    if token:
        os._exit(75)
    time.sleep(0.05)
""",
    )

    identity_snapshots: list[WorkProcessIdentity] = []
    original_read_identity = supervisor._read_process_identity

    def capture_identity(
        monitor_pid: int, init_pid: int, *, strict_monitor_argv: bool = True
    ) -> WorkProcessIdentity:
        identity = original_read_identity(
            monitor_pid, init_pid, strict_monitor_argv=strict_monitor_argv
        )
        identity_snapshots.append(identity)
        return identity

    monkeypatch.setattr(supervisor, "_read_process_identity", capture_identity)
    real_pidfd_open = os.pidfd_open

    def fail_namespace_init_pidfd(pid: int, flags: int = 0) -> int:
        if WorkProcessSupervisor._is_namespace_init(pid):
            raise OSError(errno.EMFILE, "injected PID 1 pidfd exhaustion")
        return real_pidfd_open(pid, flags)

    recovery = WorkProcessSupervisor(cleanup_timeout=2.0)

    def fail_reattach(_identity: WorkProcessIdentity) -> WorkProcessAttempt:
        raise WorkProcessIsolationUnavailable("injected reattach failure")

    monkeypatch.setattr(recovery, "reattach", fail_reattach)
    attempt = None
    known_identity: WorkProcessIdentity | None = None
    try:
        with pytest.raises(WorkProcessStartUncertain) as start_error:
            with monkeypatch.context() as partial_pidfds:
                partial_pidfds.setattr(
                    work_process_supervisor.os,
                    "pidfd_open",
                    fail_namespace_init_pidfd,
                )
                supervisor.start(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    persist_identity=_discard_identity,
                )

        # Keep ``attempt`` unset as a caller's ``start()`` assignment would be
        # when the call raises; the cleanup helper must recover supervisor.attempt.
        owned_attempt = start_error.value.attempt
        assert supervisor.attempt is owned_attempt
        assert owned_attempt._monitor_pidfd is not None and owned_attempt._pidfd is None
        known_identity = identity_snapshots[0]
        assert len(identity_snapshots) == 2
        assert identity_snapshots[1] == known_identity
        assert owned_attempt._identity == known_identity
        with pytest.raises(WorkProcessIsolationUnavailable, match="injected reattach failure"):
            _cleanup_partial_attempt_with_reattach_fallback(
                supervisor,
                recovery,
                attempt,
                known_identity,
                None,
            )
        assert owned_attempt.state is WorkProcessState.TERMINATED
        assert owned_attempt._monitor_pidfd is None and owned_attempt._pidfd is None
    finally:
        _cleanup_partial_attempt_with_reattach_fallback(
            supervisor,
            recovery,
            attempt,
            known_identity,
            None,
        )


def test_partial_pidfd_fallback_reconciles_reattach_after_uncertain_terminate(
    monkeypatch,
) -> None:
    """Original cleanup fallback must also resolve reattach pidfds afterward."""
    supervisor = WorkProcessSupervisor(cleanup_timeout=0.0)
    try:
        supervisor._require_capabilities()
    except WorkProcessIsolationUnavailable as exc:
        pytest.skip(f"host cannot run the required Work namespaces: {exc}")

    monkeypatch.setattr(
        work_process_supervisor,
        "_BOOTSTRAP",
        r"""import os, sys, time
gate_fd = int(sys.argv[1])
while True:
    try:
        token = os.read(gate_fd, 1)
    except OSError:
        token = b""
    if token:
        os._exit(75)
    time.sleep(0.05)
""",
    )

    identities: list[WorkProcessIdentity] = []
    original_read_identity = supervisor._read_process_identity

    def capture_identity(
        monitor_pid: int, init_pid: int, *, strict_monitor_argv: bool = True
    ) -> WorkProcessIdentity:
        identity = original_read_identity(
            monitor_pid, init_pid, strict_monitor_argv=strict_monitor_argv
        )
        identities.append(identity)
        return identity

    monkeypatch.setattr(supervisor, "_read_process_identity", capture_identity)
    real_pidfd_open = os.pidfd_open

    def fail_namespace_init_pidfd(pid: int, flags: int = 0) -> int:
        if WorkProcessSupervisor._is_namespace_init(pid):
            raise OSError(errno.EMFILE, "injected PID 1 pidfd exhaustion")
        return real_pidfd_open(pid, flags)

    recovery = WorkProcessSupervisor(cleanup_timeout=2.0)
    attempt = None
    reattached = None
    identity: WorkProcessIdentity | None = None
    try:
        with pytest.raises(WorkProcessStartUncertain) as start_error:
            with monkeypatch.context() as partial_pidfds:
                partial_pidfds.setattr(
                    work_process_supervisor.os,
                    "pidfd_open",
                    fail_namespace_init_pidfd,
                )
                supervisor.start(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    persist_identity=_discard_identity,
                )

        attempt = start_error.value.attempt
        assert attempt._monitor_pidfd is not None and attempt._pidfd is None
        identity = identities[0]
        assert len(identities) == 2
        assert identities[1] == identity
        assert attempt._identity == identity
        reattached = recovery.reattach(identity)

        real_wait = recovery.wait
        wait_calls = 0

        def report_uncertain_once(
            recovery_attempt: WorkProcessAttempt,
            *,
            timeout: float | None = None,
        ) -> WorkProcessState:
            nonlocal wait_calls
            wait_calls += 1
            if wait_calls == 1:
                return WorkProcessState.UNCERTAIN
            return real_wait(recovery_attempt, timeout=timeout)

        monkeypatch.setattr(
            recovery,
            "terminate",
            lambda _attempt, *, timeout=None: WorkProcessState.UNCERTAIN,
        )
        monkeypatch.setattr(recovery, "wait", report_uncertain_once)

        resolved = _cleanup_partial_attempt_with_reattach_fallback(
            supervisor,
            recovery,
            attempt,
            identity,
            reattached,
        )
        assert resolved is reattached
        assert wait_calls == 2
        assert attempt.state is WorkProcessState.TERMINATED
        assert reattached.state is WorkProcessState.TERMINATED
        assert attempt._pidfd is None and attempt._monitor_pidfd is None
        assert reattached._pidfd is None and reattached._monitor_pidfd is None
    finally:
        _cleanup_partial_attempt_with_reattach_fallback(
            supervisor,
            recovery,
            attempt,
            identity,
            reattached,
        )


def test_partial_pidfd_finally_closes_original_after_reattach_proof(monkeypatch) -> None:
    """Finally must close the original partial fd after late reattach proof."""
    supervisor = WorkProcessSupervisor(cleanup_timeout=0.0)
    try:
        supervisor._require_capabilities()
    except WorkProcessIsolationUnavailable as exc:
        pytest.skip(f"host cannot run the required Work namespaces: {exc}")

    monkeypatch.setattr(
        work_process_supervisor,
        "_BOOTSTRAP",
        r"""import os, sys, time
gate_fd = int(sys.argv[1])
while True:
    try:
        token = os.read(gate_fd, 1)
    except OSError:
        token = b""
    if token:
        os._exit(75)
    time.sleep(0.05)
""",
    )

    identities: list[WorkProcessIdentity] = []
    original_read_identity = supervisor._read_process_identity

    def capture_identity(
        monitor_pid: int, init_pid: int, *, strict_monitor_argv: bool = True
    ) -> WorkProcessIdentity:
        identity = original_read_identity(
            monitor_pid, init_pid, strict_monitor_argv=strict_monitor_argv
        )
        identities.append(identity)
        return identity

    monkeypatch.setattr(supervisor, "_read_process_identity", capture_identity)
    real_pidfd_open = os.pidfd_open

    def fail_namespace_init_pidfd(pid: int, flags: int = 0) -> int:
        if WorkProcessSupervisor._is_namespace_init(pid):
            raise OSError(errno.EMFILE, "injected PID 1 pidfd exhaustion")
        return real_pidfd_open(pid, flags)

    recovery = WorkProcessSupervisor(cleanup_timeout=2.0)
    attempt = None
    reattached = None
    identity: WorkProcessIdentity | None = None
    try:
        with pytest.raises(WorkProcessStartUncertain) as start_error:
            with monkeypatch.context() as partial_pidfds:
                partial_pidfds.setattr(
                    work_process_supervisor.os,
                    "pidfd_open",
                    fail_namespace_init_pidfd,
                )
                supervisor.start(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    persist_identity=_discard_identity,
                )

        attempt = start_error.value.attempt
        assert attempt._monitor_pidfd is not None and attempt._pidfd is None
        identity = identities[0]
        assert len(identities) == 2
        assert identities[1] == identity
        assert attempt._identity == identity
        reattached = recovery.reattach(identity)

        # Exit both pinned processes without asking recovery to reconcile yet.
        # The first recovery wait below is forced UNCERTAIN, so the original
        # helper first sees an empty child list without proof and must retain
        # its partial pidfd. The finally path then proves termination via the
        # reattached handle and closes that stale original pidfd.
        assert reattached._pidfd is not None and reattached._monitor_pidfd is not None
        signal.pidfd_send_signal(reattached._pidfd, signal.SIGKILL)
        try:
            signal.pidfd_send_signal(reattached._monitor_pidfd, signal.SIGKILL)
        except OSError as exc:
            if exc.errno != errno.ESRCH:
                raise
            assert _pidfd_exited(
                reattached._monitor_pidfd
            ), "monitor returned ESRCH before its pidfd became readable"
        assert _wait_pidfds_exited([reattached._pidfd, reattached._monitor_pidfd], timeout=2.0)

        real_wait = recovery.wait
        wait_calls = 0

        def report_uncertain_once(
            recovery_attempt: WorkProcessAttempt,
            *,
            timeout: float | None = None,
        ) -> WorkProcessState:
            nonlocal wait_calls
            wait_calls += 1
            if wait_calls == 1:
                return WorkProcessState.UNCERTAIN
            return real_wait(recovery_attempt, timeout=timeout)

        monkeypatch.setattr(
            recovery,
            "terminate",
            lambda _attempt, *, timeout=None: WorkProcessState.UNCERTAIN,
        )
        monkeypatch.setattr(recovery, "wait", report_uncertain_once)
        with pytest.raises(
            AssertionError,
            match="original-attempt cleanup remains unproven",
        ):
            _cleanup_partial_attempt_with_reattach_fallback(
                supervisor,
                recovery,
                attempt,
                identity,
                reattached,
            )

        assert wait_calls == 2
        assert reattached.state is WorkProcessState.TERMINATED
        assert reattached._pidfd is None and reattached._monitor_pidfd is None
        assert attempt.state is WorkProcessState.UNCERTAIN
        assert attempt._pidfd is None and attempt._monitor_pidfd is None
    finally:
        _cleanup_partial_attempt_with_reattach_fallback(
            supervisor,
            recovery,
            attempt,
            identity,
            reattached,
        )


def test_reattach_recovers_command_after_two_bootstrap_fds() -> None:
    supervisor = WorkProcessSupervisor()
    try:
        supervisor._require_capabilities()
    except WorkProcessIsolationUnavailable as exc:
        pytest.skip(f"host cannot run the required Work namespaces: {exc}")

    command = [sys.executable, "-c", "import time; time.sleep(20)"]
    persisted: dict[str, object] = {}
    attempt = None
    recovery = WorkProcessSupervisor()
    reattached = None
    try:
        attempt = supervisor.start(
            command,
            persist_identity=lambda identity: persisted.update(identity.to_dict()),
        )
        assert persisted["version"] == 2
        reattached = recovery.reattach(persisted)
        assert reattached.command == tuple(command)
        assert recovery.terminate(reattached, timeout=5.0) is WorkProcessState.TERMINATED
        assert supervisor.wait(attempt, timeout=5.0) is WorkProcessState.TERMINATED
    finally:
        try:
            _cleanup_started_attempt(recovery, reattached)
        finally:
            _cleanup_started_attempt(supervisor, attempt)


def test_durable_identity_v3_hashes_supervisor_prefix_and_reattaches_from_live_process() -> None:
    supervisor = WorkProcessSupervisor()
    try:
        supervisor._require_capabilities()
    except WorkProcessIsolationUnavailable as exc:
        pytest.skip(f"host cannot run the required Work namespaces: {exc}")

    command = [sys.executable, "-c", "import time; time.sleep(20)"]
    attempt = None
    recovery = WorkProcessSupervisor()
    reattached = None
    try:
        attempt = supervisor.start(command, persist_identity=lambda _identity: None)
        assert attempt._identity is not None
        legacy = attempt._identity.to_dict()
        assert legacy["version"] == 2
        assert legacy["monitor_argv"] == list(attempt._identity.monitor_argv)

        durable = attempt._identity.to_durable_dict()
        prefix_end = attempt._identity.monitor_argv.index("--") + 7
        canonical_prefix = json.dumps(
            list(attempt._identity.monitor_argv[:prefix_end]),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        assert durable["version"] == 3
        assert "monitor_argv" not in durable
        assert durable["monitor_argv_prefix_sha256"] == hashlib.sha256(canonical_prefix).hexdigest()
        identity_payload = {
            key: value for key, value in durable.items() if key != "identity_sha256"
        }
        canonical_identity = json.dumps(
            identity_payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        assert durable["identity_sha256"] == hashlib.sha256(canonical_identity).hexdigest()
        assert "import time; time.sleep(20)" not in repr(durable)
        parsed = WorkProcessIdentity.from_dict(durable)
        assert parsed.monitor_argv == ()
        assert parsed.to_dict() == durable

        invalid_digest_format = dict(durable)
        invalid_digest_format["monitor_argv_prefix_sha256"] = "A" * 64
        with pytest.raises(ValueError, match="supervisor-prefix digest is invalid"):
            WorkProcessIdentity.from_dict(invalid_digest_format)

        corrupted_envelope = dict(durable)
        corrupted_envelope["monitor_argv_prefix_sha256"] = "f" * 64
        with pytest.raises(ValueError, match="digest does not match its fields"):
            WorkProcessIdentity.from_dict(corrupted_envelope)

        malformed = dict(durable)
        malformed["monitor_argv_prefix_sha256"] = "0" * 64
        malformed_identity_payload = {
            key: value for key, value in malformed.items() if key != "identity_sha256"
        }
        malformed["identity_sha256"] = hashlib.sha256(
            json.dumps(
                malformed_identity_payload,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        with pytest.raises(WorkProcessIsolationUnavailable, match="does not match the live"):
            recovery.reattach(malformed)

        changed_boot_id = dict(durable)
        changed_boot_id["boot_id"] = f"{durable['boot_id']}\x00suffix"
        changed_boot_payload = {
            key: value for key, value in changed_boot_id.items() if key != "identity_sha256"
        }
        changed_boot_id["identity_sha256"] = hashlib.sha256(
            json.dumps(
                changed_boot_payload,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        with pytest.raises(ValueError, match="boot ID is invalid"):
            WorkProcessIdentity.from_dict(changed_boot_id)

        reattached = recovery.reattach(durable)
        assert reattached.command == tuple(command)
        assert recovery.terminate(reattached, timeout=5.0) is WorkProcessState.TERMINATED
        assert supervisor.wait(attempt, timeout=5.0) is WorkProcessState.TERMINATED
    finally:
        try:
            _cleanup_started_attempt(recovery, reattached)
        finally:
            _cleanup_started_attempt(supervisor, attempt)


def test_reattach_rejects_v1_identity_with_typed_t097_cleanup_gate() -> None:
    legacy_identity = {
        "version": 1,
        "boot_id": "legacy-boot-id",
        "monitor_pid": 2_147_483_647,
        "monitor_start_time_ticks": 10,
        "init_pid": 2_147_483_646,
        "init_start_time_ticks": 11,
        "init_parent_pid": 2_147_483_647,
        "pid_namespace": [1, 2],
        "net_namespace": [1, 3],
        "ipc_namespace": [1, 4],
        "monitor_argv": ["unshare", "--", "python", "-I", "-c", "legacy-bootstrap"],
    }

    with pytest.raises(
        WorkProcessIsolationUnavailable,
        match="identity protocol v1.*cleanup-only.*T097",
    ) as error:
        WorkProcessSupervisor().reattach(legacy_identity)

    assert type(error.value).__name__ == "WorkProcessIdentityProtocolIncompatible"


def test_start_keeps_command_gated_when_unshare_omits_network_and_ipc_namespaces(
    tmp_path: Path,
    monkeypatch,
    native_work_namespaces: str,
) -> None:
    real_unshare = native_work_namespaces

    wrapper = tmp_path / "unshare-without-network-ipc"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        f"unshare = {real_unshare!r}\n"
        "args = [arg for arg in sys.argv[1:] if arg not in ('--net', '--ipc')]\n"
        "os.execv(unshare, [sys.argv[0], *args])\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    marker = tmp_path / "command-must-not-run"
    supervisor = WorkProcessSupervisor(
        unshare_path=str(wrapper), startup_timeout=1.0, cleanup_timeout=1.0
    )
    monkeypatch.setattr(supervisor, "_require_capabilities", lambda: str(wrapper))
    attempt = None
    try:
        with pytest.raises(WorkProcessIsolationUnavailable, match="monitor argv"):
            attempt = supervisor.start(_touch_command(marker), persist_identity=_discard_identity)

        assert not marker.exists()
    finally:
        _cleanup_started_attempt(supervisor, attempt)


def test_worker_does_not_inherit_server_environment(
    tmp_path: Path,
    monkeypatch,
    native_work_namespaces: str,
) -> None:
    key = "CAO_T097_CANARY_SECRET"  # gitleaks:allow
    monkeypatch.setenv(key, "server-secret-must-not-reach-worker")
    report = tmp_path / "worker-environment.txt"
    code = (
        "import os, pathlib, sys; "
        "pathlib.Path(sys.argv[1]).write_text("
        "os.environ.get('CAO_T097_CANARY_SECRET', '<missing>'))"
    )
    supervisor = WorkProcessSupervisor()
    attempt = None
    try:
        attempt = supervisor.start(
            [sys.executable, "-c", code, str(report)],
            persist_identity=_discard_identity,
        )
        assert supervisor.wait(attempt, timeout=5.0) is WorkProcessState.TERMINATED
        assert report.read_text(encoding="utf-8") == "<missing>"
    finally:
        _cleanup_started_attempt(supervisor, attempt)


def test_worker_cannot_receive_an_unchecked_environment_mapping(tmp_path: Path) -> None:
    marker = tmp_path / "unchecked-environment-must-not-run"
    supervisor = WorkProcessSupervisor()
    attempt = None
    try:
        with pytest.raises(TypeError):
            attempt = supervisor.start(
                _touch_command(marker),
                persist_identity=_discard_identity,
                env={"CAO_T097_CANARY_SECRET": "caller-secret"},
            )
    finally:
        _cleanup_started_attempt(supervisor, attempt)

    assert not marker.exists()


def test_restart_preserves_uncertain_work_and_pending_cleanup_without_redelivery(
    tmp_path: Path,
    native_work_namespaces: str,
) -> None:
    """A restarted service cannot replay or claim cleanup without a reattach handle."""
    repository_path = tmp_path / "work.sqlite3"
    repository = WorkRepository(repository_path)
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
        idempotency_key="attempt",
        request_hash="a" * 64,
        contract_id="test-contract",
        snapshot_id=None,
        provider="mock_cli",
        actor_id="operator",
        lease_seconds=60,
    )
    admission = TransitionEvidence(
        generation=1,
        expected_generation=1,
        contract_confirmed=True,
        grant_confirmed=True,
        capacity_confirmed=True,
        reservations_confirmed=True,
    )
    supervisor = WorkProcessSupervisor()
    process_attempt = None
    send_calls: list[str] = []
    cleanup_calls: list[str] = []

    def uncertain_send() -> None:
        nonlocal process_attempt
        send_calls.append("sent")
        process_attempt = supervisor.start(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            persist_identity=_discard_identity,
        )
        raise TimeoutError("simulated server loss after the process started")

    try:
        with pytest.raises(DeliveryUncertain):
            WorkService(repository).dispatch(
                work["id"], uncertain_send, admission=admission, actor_id="operator"
            )

        attempt_id = repository.get_work(work["id"])["attempts"][-1]["id"]

        def interrupted_cleanup() -> bool:
            cleanup_calls.append("first-process")
            raise SystemExit("simulate death after pending was committed")

        with pytest.raises(SystemExit, match="pending was committed"):
            WorkService(repository).cleanup_attempt(
                attempt_id, interrupted_cleanup, actor_id="operator"
            )
        assert repository.get_attempt(attempt_id)["cleanup_state"] == "pending"

        restarted_repository = WorkRepository(repository_path)
        restarted_service = WorkService(restarted_repository)
        replayed = restarted_service.dispatch(
            work["id"], uncertain_send, admission=admission, actor_id="operator"
        )
        assert replayed["attempts"][-1]["state"] == "reconcile"
        assert send_calls == ["sent"]

        restarted_service.cleanup_attempt(
            attempt_id,
            lambda: cleanup_calls.append("must-not-blindly-replay") or True,
            actor_id="operator",
        )
        assert cleanup_calls == ["first-process"]
        assert restarted_repository.get_attempt(attempt_id)["cleanup_state"] == "pending"
        assert process_attempt is not None
        assert supervisor.wait(process_attempt, timeout=0) is WorkProcessState.RUNNING
    finally:
        _cleanup_started_attempt(supervisor, process_attempt)
