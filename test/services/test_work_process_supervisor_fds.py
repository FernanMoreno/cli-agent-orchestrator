"""Real descriptor-inheritance evidence for the current process supervisor."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cli_agent_orchestrator.services.work_process_supervisor import (
    WorkProcessIsolationUnavailable,
    WorkProcessStartUncertain,
    WorkProcessState,
    WorkProcessSupervisor,
)


_FD_PROBE = r"""
import json, os, sys, time

fd = int(sys.argv[1])
expected_dev = int(sys.argv[2])
expected_ino = int(sys.argv[3])
expected_target = sys.argv[4]
fd_path = f"/proc/self/fd/{fd}"
try:
    target = os.readlink(fd_path)
    current = os.stat(fd_path)
    report = {
        "same_object": (
            current.st_dev == expected_dev
            and current.st_ino == expected_ino
            and target == expected_target
        ),
        "target": target,
    }
except OSError:
    report = {"same_object": False, "target": None}

encoded = json.dumps(report, sort_keys=True)
print(encoded)
if len(sys.argv) > 5:
    with open(sys.argv[5], "w", encoding="utf-8") as result:
        result.write(encoded)
"""


def _discard_identity(_identity: object) -> None:
    """This descriptor-isolation test does not exercise restart persistence."""


@pytest.mark.parametrize("mode", ["inner-wait-failure", "parent-timeout"])
def test_worker_standard_streams_are_detached_and_cleanup_survives_controller_failure(
    tmp_path: Path,
    mode: str,
) -> None:
    """Worker stdio stays private and persisted identity enables failure cleanup."""
    if not sys.platform.startswith("linux"):
        pytest.skip("WorkProcessSupervisor standard-stream isolation requires Linux")

    probe = WorkProcessSupervisor()
    try:
        probe._require_capabilities()
    except WorkProcessIsolationUnavailable as exc:
        pytest.skip(f"host cannot run the required Work namespaces: {exc}")

    report_path = tmp_path / "standard-stream-report.json"
    identity_path = tmp_path / "process-identity.json"
    cleanup_path = tmp_path / "cleanup-proof.json"
    source_root = Path(__file__).resolve().parents[2] / "src"
    stdin_canary = b"CAO_CONTROLLER_STDIN_CANARY_7f9d"
    stdout_canary = "CAO_WORKER_STDOUT_CANARY_a813"
    stderr_canary = "CAO_WORKER_STDERR_CANARY_b042"
    controller = r"""
import json, os, sys, time
from pathlib import Path

sys.path.insert(0, sys.argv[5])
from cli_agent_orchestrator.services.work_process_supervisor import (
    WorkProcessState,
    WorkProcessSupervisor,
)

identity_path, report, cleanup_path = map(Path, sys.argv[1:4])
mode = sys.argv[5]

def atomic_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as output:
        json.dump(value, output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)

def persist(identity):
    atomic_json(identity_path, identity.to_dict())

worker = (
    "import json,os,sys,time; "
    "from pathlib import Path; "
    "assert Path(sys.argv[1]).exists(); "
    "stdin=os.read(0,4096).decode('ascii'); "
    "os.write(1,b'CAO_WORKER_STDOUT_CANARY_a813'); "
    "os.write(2,b'CAO_WORKER_STDERR_CANARY_b042'); "
    "open(sys.argv[2],'w',encoding='utf-8').write(json.dumps({'stdin':stdin})); "
    "time.sleep(60)"
)
supervisor = WorkProcessSupervisor()
attempt = None
try:
    attempt = supervisor.start(
        [sys.executable, '-c', worker, str(identity_path), str(report)],
        persist_identity=persist,
    )
    if mode == 'inner-wait-failure':
        deadline = time.monotonic() + 5
        while not report.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not report.exists():
            raise RuntimeError('worker did not write its report before injected failure')
        real_wait = supervisor.wait
        first_call = [True]
        def fail_first_wait(target, *, timeout=None):
            if first_call[0]:
                first_call[0] = False
                raise RuntimeError('injected inner wait failure')
            return real_wait(target, timeout=timeout)
        supervisor.wait = fail_first_wait
    supervisor.wait(attempt, timeout=60)
finally:
    active = attempt or supervisor.attempt
    if active is not None:
        state = supervisor.terminate(active, timeout=5)
        if state is not WorkProcessState.TERMINATED:
            raise RuntimeError('controller could not confirm worker cleanup')
        atomic_json(cleanup_path, {'state': 'TERMINATED', 'owner': 'controller'})
"""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    runner = subprocess.Popen(
        [
            sys.executable,
            "-c",
            controller,
            str(identity_path),
            str(report_path),
            str(cleanup_path),
            str(source_root),
            mode,
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert runner.stdin is not None
    runner.stdin.write(stdin_canary)
    runner.stdin.close()
    runner.stdin = None

    controller_stdout = b""
    controller_stderr = b""
    timeout_observed = False
    cleanup_confirmed = False
    cleanup_error: str | None = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not report_path.exists():
            if runner.poll() is not None:
                controller_stdout, controller_stderr = runner.communicate(timeout=3)
                pytest.fail(
                    "controller exited before worker report was written: "
                    + controller_stderr.decode("utf-8", errors="replace")
                )
            time.sleep(0.01)
        assert identity_path.exists(), "identity was not durably persisted before startup"
        assert report_path.exists(), "worker did not report its standard-stream observations"

        if mode == "parent-timeout":
            with pytest.raises(subprocess.TimeoutExpired):
                runner.wait(timeout=0.1)
            timeout_observed = True
            runner.kill()
            controller_stdout, controller_stderr = runner.communicate(timeout=5)
        else:
            controller_stdout, controller_stderr = runner.communicate(timeout=10)
            assert runner.returncode != 0, "injected wait failure did not reach the controller"
            assert b"injected inner wait failure" in controller_stderr
    finally:
        if runner.poll() is None:
            runner.kill()
            try:
                controller_stdout, controller_stderr = runner.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                cleanup_error = "controller did not exit after kill"

        if identity_path.exists():
            try:
                identity = json.loads(identity_path.read_text(encoding="utf-8"))
                recovery = WorkProcessSupervisor()
                try:
                    orphan = recovery.reattach(identity)
                except (ValueError, WorkProcessIsolationUnavailable):
                    proof = json.loads(cleanup_path.read_text(encoding="utf-8"))
                    cleanup_confirmed = proof.get("state") == "TERMINATED"
                else:
                    state = recovery.terminate(orphan, timeout=5)
                    cleanup_confirmed = state is WorkProcessState.TERMINATED
                    if cleanup_confirmed:
                        temporary = cleanup_path.with_name(cleanup_path.name + ".parent.tmp")
                        with temporary.open("w", encoding="utf-8") as output:
                            json.dump({"state": "TERMINATED", "owner": "parent"}, output)
                            output.flush()
                            os.fsync(output.fileno())
                        os.replace(temporary, cleanup_path)
                        directory_fd = os.open(cleanup_path.parent, os.O_RDONLY)
                        try:
                            os.fsync(directory_fd)
                        finally:
                            os.close(directory_fd)
            except (OSError, ValueError, WorkProcessIsolationUnavailable) as exc:
                cleanup_error = f"could not confirm process cleanup: {exc}"
        else:
            cleanup_error = "controller did not persist a reattachment identity"

    assert cleanup_error is None, cleanup_error
    assert cleanup_confirmed, "neither controller nor parent confirmed worker cleanup"
    if mode == "parent-timeout":
        assert timeout_observed, "controller did not reach the expected parent timeout"

    worker_report = json.loads(report_path.read_text(encoding="utf-8"))
    combined_controller_output = controller_stdout + controller_stderr
    detached = {
        "stdin": worker_report["stdin"] == "",
        "stdout": stdout_canary.encode() not in combined_controller_output,
        "stderr": stderr_canary.encode() not in combined_controller_output,
    }
    assert all(detached.values()), f"controller standard streams reached worker: {detached}"


def test_supervisor_does_not_inherit_an_unrelated_inheritable_fd(
    tmp_path: Path,
) -> None:
    """Prove the same inheritable FD reaches a control child but not this worker."""
    if not sys.platform.startswith("linux"):
        pytest.skip("WorkProcessSupervisor FD isolation requires Linux")
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        pytest.skip("Python/Linux runtime lacks pidfd_open or pidfd_send_signal")
    if not Path("/proc/self/fd").is_dir() or not Path("/proc/self/status").is_file():
        pytest.skip("procfs is unavailable at /proc/self/fd or /proc/self/status")

    unshare = shutil.which("unshare")
    if unshare is None:
        pytest.skip("executable unshare is unavailable")

    owned_fds: list[int] = []
    if hasattr(os, "memfd_create"):
        try:
            descriptor = os.memfd_create("cao-work-supervisor-fd-probe", flags=0)
            owned_fds.append(descriptor)
        except OSError:
            descriptor, writer_fd = os.pipe()
            owned_fds.extend((descriptor, writer_fd))
    else:
        descriptor, writer_fd = os.pipe()
        owned_fds.extend((descriptor, writer_fd))

    supervisor = WorkProcessSupervisor(unshare_path=unshare)
    attempt = None
    try:
        os.set_inheritable(descriptor, True)
        parent_fd_path = Path(f"/proc/self/fd/{descriptor}")
        parent_stat = os.stat(parent_fd_path)
        parent_target = os.readlink(parent_fd_path)

        # Verify the fixture itself: this exact parent descriptor is open,
        # explicitly inheritable, and has a stable procfs identity.
        assert os.get_inheritable(descriptor)
        assert os.stat(parent_fd_path).st_dev == parent_stat.st_dev
        assert os.stat(parent_fd_path).st_ino == parent_stat.st_ino
        assert os.readlink(parent_fd_path) == parent_target

        probe_args = [
            sys.executable,
            "-c",
            _FD_PROBE,
            str(descriptor),
            str(parent_stat.st_dev),
            str(parent_stat.st_ino),
            parent_target,
        ]

        # Control: this same descriptor is visible when inheritance is allowed.
        control = subprocess.run(
            probe_args,
            close_fds=False,
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(control.stdout)["same_object"] is True

        report_path = tmp_path / "worker-fd-report.json"
        try:
            attempt = supervisor.start(
                [*probe_args, str(report_path)],
                persist_identity=_discard_identity,
            )
        except WorkProcessStartUncertain as exc:
            attempt = exc.attempt
            raise
        except WorkProcessIsolationUnavailable as exc:
            pytest.skip(f"unshare PID-namespace capability is unavailable: {exc}")

        assert supervisor.wait(attempt, timeout=5.0) is WorkProcessState.TERMINATED
        worker_report = json.loads(report_path.read_text(encoding="utf-8"))
        assert worker_report["same_object"] is False
    finally:
        try:
            if attempt is not None and attempt.state is not WorkProcessState.TERMINATED:
                supervisor.terminate(attempt, timeout=5.0)
        finally:
            for fd in owned_fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
