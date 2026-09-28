"""Bubblewrap restart cleanup remains a reconcile-only operation."""

import hashlib
import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.services import work_service
from cli_agent_orchestrator.services.work_service import WorkService
from test.services.test_work_process_cleanup_recovery import _reconciled_held


def _bubblewrap_identity():
    payload = {
        "version": 1,
        "kind": "bubblewrap",
        "boot_id": "test-boot",
        "monitor_pid": 4101,
        "monitor_start_time_ticks": 12001,
        "init_pid": 4102,
        "init_start_time_ticks": 12002,
        "init_parent_pid": 4101,
        "pid_namespace": [1, 41001],
        "net_namespace": [1, 41002],
        "ipc_namespace": [1, 41003],
        "monitor_argv_sha256": "a" * 64,
        "monitor_executable_sha256": "b" * 64,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {**payload, "identity_sha256": digest}


def _identity_with(identity, **updates):
    payload = {key: value for key, value in identity.items() if key != "identity_sha256"}
    payload.update(updates)
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {**payload, "identity_sha256": digest}


def _store_identity(repository, attempt_id, identity):
    with repository.connection() as connection:
        connection.execute(
            "INSERT INTO work_bubblewrap_process_identities "
            "(attempt_id,generation,protocol_version,identity_json,identity_sha256,created_at) "
            "VALUES (?,?,?,?,?,0)",
            (
                attempt_id,
                1,
                1,
                json.dumps(identity, sort_keys=True, separators=(",", ":")),
                identity["identity_sha256"],
            ),
        )


def test_restart_uses_persisted_bubblewrap_identity_only_for_cleanup(tmp_path, monkeypatch):
    repository, work, held = _reconciled_held(tmp_path)
    identity = _bubblewrap_identity()
    _store_identity(repository, held.attempt_id, identity)
    cleanup = Mock(return_value=True)
    monkeypatch.setattr(work_service, "_cleanup_bubblewrap_identity", cleanup, raising=False)
    legacy_supervisor = Mock()

    recovered = WorkService(repository).recover_process_cleanup(
        held.attempt_id, supervisor=legacy_supervisor, actor_id="owner"
    )

    assert recovered["state"] == "reconcile"
    assert recovered["attempts"][-1]["cleanup_state"] == "complete"
    cleanup.assert_called_once_with(identity)
    legacy_supervisor.reattach.assert_not_called()
    legacy_supervisor.terminate.assert_not_called()


def test_both_original_processes_gone_complete_without_signal(tmp_path, monkeypatch):
    repository, work, held = _reconciled_held(tmp_path)
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    identity = _identity_with(
        _bubblewrap_identity(),
        boot_id=boot_id,
        monitor_pid=9_000_001,
        init_pid=9_000_002,
        init_parent_pid=9_000_001,
    )
    _store_identity(repository, held.attempt_id, identity)
    monkeypatch.setattr(
        work_service.signal,
        "pidfd_send_signal",
        lambda *_: pytest.fail("a dead original process must never be signalled"),
    )
    monkeypatch.setattr(
        work_service.os,
        "pidfd_open",
        lambda *_: pytest.fail("dead original processes must not need pidfds"),
    )

    recovered = WorkService(repository).recover_process_cleanup(held.attempt_id, actor_id="owner")

    assert recovered["state"] == "reconcile"
    assert recovered["attempts"][-1]["cleanup_state"] == "complete"


def test_live_bubblewrap_pair_without_supervisor_stays_reconcile_without_pidfds(
    tmp_path, monkeypatch
):
    repository, work, held = _reconciled_held(tmp_path)
    identity = _bubblewrap_identity()
    _store_identity(repository, held.attempt_id, identity)
    expected_live = {key: identity[key] for key in work_service._BWRAP_IDENTITY_KEYS}
    monkeypatch.setattr(work_service, "_read_bubblewrap_live_identity", lambda *_: expected_live)
    monkeypatch.setattr(
        work_service, "Path", lambda path: SimpleNamespace(read_text=lambda: "test-boot")
    )
    monkeypatch.setattr(
        work_service,
        "_read_bubblewrap_proc_stat",
        lambda pid: (1, 12001 if pid == 4101 else 12002, "S"),
    )
    pidfd_opens = []
    signals = []

    def forbid_pidfd_open(pid):
        pidfd_opens.append(pid)
        raise OSError("pidfd must not be opened for observation")

    monkeypatch.setattr(work_service.os, "pidfd_open", forbid_pidfd_open)
    monkeypatch.setattr(
        work_service.signal,
        "pidfd_send_signal",
        lambda *args: signals.append(args),
    )

    recovered = WorkService(repository).recover_process_cleanup(held.attempt_id, actor_id="owner")

    assert recovered["state"] == "reconcile"
    assert recovered["attempts"][-1]["cleanup_state"] == "failed"
    assert pidfd_opens == []
    assert signals == []


@pytest.mark.parametrize(
    "changed_field",
    [
        "boot_id",
        "monitor_start_time_ticks",
        "init_start_time_ticks",
        "init_parent_pid",
        "pid_namespace",
        "net_namespace",
        "ipc_namespace",
        "monitor_argv_sha256",
        "monitor_executable_sha256",
    ],
)
def test_mismatched_live_identity_never_opens_pidfd_or_signals(monkeypatch, changed_field):
    identity = _bubblewrap_identity()
    live = {
        key: value
        for key, value in identity.items()
        if key not in {"version", "kind", "identity_sha256"}
    }
    live[changed_field] = "other" if isinstance(live[changed_field], str) else [9, 9]
    monkeypatch.setattr(work_service, "_read_bubblewrap_live_identity", lambda *_: live)
    monkeypatch.setattr(work_service, "_original_bubblewrap_pair_gone", lambda *_: False)
    monkeypatch.setattr(
        work_service.os,
        "pidfd_open",
        lambda *_: pytest.fail("pidfd must not open before complete validation"),
    )
    monkeypatch.setattr(
        work_service.signal,
        "pidfd_send_signal",
        lambda *_: pytest.fail("identity mismatch must never signal"),
    )

    assert work_service._cleanup_bubblewrap_identity(identity) is False


def test_identity_change_after_pidfd_open_never_signals(monkeypatch):
    identity = _bubblewrap_identity()
    expected = {
        key: value
        for key, value in identity.items()
        if key not in {"version", "kind", "identity_sha256"}
    }
    snapshots = [expected, {**expected, "boot_id": "rebooted"}]
    opened = {}

    def open_pidfd(pid):
        descriptor = os.open("/dev/null", os.O_RDONLY)
        opened[descriptor] = pid
        return descriptor

    monkeypatch.setattr(work_service, "_read_bubblewrap_live_identity", lambda *_: snapshots.pop(0))
    monkeypatch.setattr(work_service.os, "pidfd_open", open_pidfd)
    monkeypatch.setattr(work_service, "_bubblewrap_pidfd_target", lambda fd: opened[fd])
    monkeypatch.setattr(
        work_service.signal,
        "pidfd_send_signal",
        lambda *_: pytest.fail("post-pidfd identity mismatch must never signal"),
    )

    assert work_service._cleanup_bubblewrap_identity(identity) is False
    assert not snapshots
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_validated_pair_is_signalled_through_init_pidfd_only(monkeypatch):
    identity = _bubblewrap_identity()
    expected = {
        key: value
        for key, value in identity.items()
        if key not in {"version", "kind", "identity_sha256"}
    }
    opened = {}
    observations = []

    def open_pidfd(pid):
        descriptor = os.open("/dev/null", os.O_RDONLY)
        opened[descriptor] = pid
        return descriptor

    def exited(fd, timeout):
        observations.append((opened[fd], timeout))
        return timeout > 0

    signals = []
    monkeypatch.setattr(work_service, "_read_bubblewrap_live_identity", lambda *_: expected)
    monkeypatch.setattr(work_service.os, "pidfd_open", open_pidfd)
    monkeypatch.setattr(work_service, "_bubblewrap_pidfd_target", lambda fd: opened[fd])
    monkeypatch.setattr(work_service, "_bubblewrap_pidfd_exited", exited)
    monkeypatch.setattr(
        work_service.signal,
        "pidfd_send_signal",
        lambda fd, signum: signals.append((opened[fd], signum)),
    )

    assert work_service._cleanup_bubblewrap_identity(identity) is True
    assert signals == [(identity["init_pid"], signal.SIGKILL)]
    assert observations == [
        (identity["monitor_pid"], 0),
        (identity["init_pid"], 0),
        (identity["init_pid"], 2.0),
        (identity["monitor_pid"], 2.0),
    ]
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize("role", ["monitor", "init"])
@pytest.mark.parametrize(
    "event",
    [select.POLLERR, select.POLLNVAL, select.POLLERR | select.POLLIN],
)
def test_pidfd_poll_error_aborts_cleanup_before_signal(monkeypatch, role, event):
    identity = _bubblewrap_identity()
    expected = {key: identity[key] for key in work_service._BWRAP_IDENTITY_KEYS}
    opened = {}

    def open_pidfd(pid):
        descriptor = os.open("/dev/null", os.O_RDONLY)
        opened[descriptor] = pid
        return descriptor

    class Poller:
        def __init__(self):
            self.descriptor = None

        def register(self, descriptor, _events):
            self.descriptor = descriptor

        def poll(self, _timeout):
            pid = opened[self.descriptor]
            return [(self.descriptor, event)] if pid == identity[f"{role}_pid"] else []

    monkeypatch.setattr(work_service, "_read_bubblewrap_live_identity", lambda *_: expected)
    monkeypatch.setattr(work_service.os, "pidfd_open", open_pidfd)
    monkeypatch.setattr(work_service, "_bubblewrap_pidfd_target", lambda fd: opened[fd])
    monkeypatch.setattr(work_service.select, "poll", Poller)
    monkeypatch.setattr(
        work_service.signal,
        "pidfd_send_signal",
        lambda *_: pytest.fail("poll error must block signalling"),
    )

    assert work_service._cleanup_bubblewrap_identity(identity) is False
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_pidfd_poll_error_after_signal_does_not_confirm_cleanup(monkeypatch):
    identity = _bubblewrap_identity()
    expected = {key: identity[key] for key in work_service._BWRAP_IDENTITY_KEYS}
    opened = {}
    signals = []

    def open_pidfd(pid):
        descriptor = os.open("/dev/null", os.O_RDONLY)
        opened[descriptor] = pid
        return descriptor

    def exited(_descriptor, timeout):
        return None if timeout else False

    monkeypatch.setattr(work_service, "_read_bubblewrap_live_identity", lambda *_: expected)
    monkeypatch.setattr(work_service.os, "pidfd_open", open_pidfd)
    monkeypatch.setattr(work_service, "_bubblewrap_pidfd_target", lambda fd: opened[fd])
    monkeypatch.setattr(work_service, "_bubblewrap_pidfd_exited", exited)
    monkeypatch.setattr(
        work_service.signal,
        "pidfd_send_signal",
        lambda fd, signum: signals.append((opened[fd], signum)),
    )

    assert work_service._cleanup_bubblewrap_identity(identity) is False
    assert signals == [(identity["init_pid"], signal.SIGKILL)]
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_dead_original_monitor_can_clean_exact_surviving_init(monkeypatch):
    identity = _identity_with(_bubblewrap_identity(), boot_id="current-boot")
    opened = {}
    signals = []

    def process_stat(pid):
        if pid == identity["monitor_pid"]:
            raise FileNotFoundError(pid)
        return (1, identity["init_start_time_ticks"], "S")

    def open_pidfd(pid):
        descriptor = os.open("/dev/null", os.O_RDONLY)
        opened[descriptor] = pid
        return descriptor

    monkeypatch.setattr(
        work_service, "Path", lambda _path: SimpleNamespace(read_text=lambda: "current-boot")
    )
    monkeypatch.setattr(
        work_service,
        "_read_bubblewrap_live_identity",
        lambda *_: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr(work_service, "_read_bubblewrap_proc_stat", process_stat)
    monkeypatch.setattr(
        work_service,
        "_bubblewrap_namespace_identity",
        lambda _pid, kind: identity[f"{kind}_namespace"],
    )
    monkeypatch.setattr(work_service.os, "pidfd_open", open_pidfd)
    monkeypatch.setattr(work_service, "_bubblewrap_pidfd_target", lambda fd: opened[fd])
    monkeypatch.setattr(
        work_service,
        "_bubblewrap_pidfd_exited",
        lambda _fd, timeout: timeout > 0,
    )
    monkeypatch.setattr(
        work_service.signal,
        "pidfd_send_signal",
        lambda fd, signum: signals.append((opened[fd], signum)),
    )

    assert work_service._cleanup_bubblewrap_identity(identity) is True
    assert signals == [(identity["init_pid"], signal.SIGKILL)]
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize(
    "mismatch",
    [
        "monitor_live",
        "init_start",
        "init_namespace",
        "pidfd_target",
        "post_open_drift",
        "poll_error",
    ],
)
def test_orphan_init_cleanup_rejects_identity_or_poll_uncertainty(monkeypatch, mismatch):
    identity = _identity_with(_bubblewrap_identity(), boot_id="current-boot")
    opened = {}

    def process_stat(pid):
        if pid == identity["monitor_pid"]:
            if mismatch == "monitor_live":
                return (1, identity["monitor_start_time_ticks"], "S")
            raise FileNotFoundError(pid)
        changed = mismatch == "init_start" or (mismatch == "post_open_drift" and opened)
        return (1, identity["init_start_time_ticks"] + int(changed), "S")

    def open_pidfd(pid):
        descriptor = os.open("/dev/null", os.O_RDONLY)
        opened[descriptor] = pid
        return descriptor

    monkeypatch.setattr(
        work_service, "Path", lambda _path: SimpleNamespace(read_text=lambda: "current-boot")
    )
    monkeypatch.setattr(work_service, "_read_bubblewrap_proc_stat", process_stat)
    monkeypatch.setattr(
        work_service,
        "_bubblewrap_namespace_identity",
        lambda _pid, kind: (
            [9, 9]
            if mismatch == "init_namespace" and kind == "net"
            else identity[f"{kind}_namespace"]
        ),
    )
    monkeypatch.setattr(work_service.os, "pidfd_open", open_pidfd)
    monkeypatch.setattr(
        work_service,
        "_bubblewrap_pidfd_target",
        lambda fd: opened[fd] + int(mismatch == "pidfd_target"),
    )
    monkeypatch.setattr(
        work_service,
        "_bubblewrap_pidfd_exited",
        lambda _fd, _timeout: None if mismatch == "poll_error" else False,
    )
    monkeypatch.setattr(
        work_service.signal,
        "pidfd_send_signal",
        lambda *_: pytest.fail("unverified orphan init must not be signalled"),
    )

    assert work_service._cleanup_orphaned_bubblewrap_init(identity) is False
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize(
    "event",
    [
        select.POLLERR,
        select.POLLNVAL,
        select.POLLERR | select.POLLIN,
        select.POLLNVAL | select.POLLHUP,
    ],
)
def test_pidfd_poll_error_is_not_process_exit(monkeypatch, event):
    class Poller:
        def register(self, *_args):
            pass

        def poll(self, _timeout):
            return [(12, event)]

    monkeypatch.setattr(work_service.select, "poll", Poller)
    assert work_service._bubblewrap_pidfd_exited(12, 0) is None


def test_dead_monitor_recovery_cleans_surviving_exact_init(tmp_path, monkeypatch):
    """Handle both automatic child exit and a surviving pinned namespace init."""
    bwrap = Path("/tmp/caos-exec/T097/bubblewrap-build/bwrap")
    if not bwrap.is_file() or not hasattr(os, "pidfd_open"):
        pytest.skip("scratch Bubblewrap or pidfds unavailable")
    pid_file = tmp_path / "monitor-pid"
    controller_code = """
import pathlib, subprocess, sys, time
process = subprocess.Popen([
    sys.argv[1], '--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc',
    '--die-with-parent', '--as-pid-1', '--ro-bind', '/', '/', '--', '/bin/sleep', '30',
], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
pathlib.Path(sys.argv[2]).write_text(str(process.pid))
while True:
    time.sleep(1)
"""
    controller = subprocess.Popen(
        [sys.executable, "-c", controller_code, str(bwrap), str(pid_file)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    monitor_fd = init_fd = None
    monitor_owned = init_owned = False
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not pid_file.exists() and controller.poll() is None:
            time.sleep(0.01)
        assert pid_file.exists(), "controller did not publish Bubblewrap monitor PID"
        monitor_pid = int(pid_file.read_text())
        monitor_parent, monitor_start, monitor_state = work_service._read_bubblewrap_proc_stat(
            monitor_pid
        )
        assert monitor_parent == controller.pid and monitor_start > 0
        assert monitor_state not in {"Z", "X", "x"}
        monitor_fd = os.pidfd_open(monitor_pid)
        assert work_service._bubblewrap_pidfd_target(monitor_fd) == monitor_pid
        pinned_parent, pinned_start, pinned_state = work_service._read_bubblewrap_proc_stat(
            monitor_pid
        )
        assert pinned_parent == controller.pid and pinned_start == monitor_start
        assert pinned_state not in {"Z", "X", "x"}
        argv = Path(f"/proc/{monitor_pid}/cmdline").read_bytes()
        args = argv[:-1].split(b"\x00") if argv.endswith(b"\x00") else []
        assert args and args[0] == os.fsencode(str(bwrap))
        assert work_service._BWRAP_REQUIRED_OPTIONS.issubset(args)
        expected_exe = bwrap.stat()
        actual_exe = os.stat(f"/proc/{monitor_pid}/exe")
        assert (actual_exe.st_dev, actual_exe.st_ino) == (
            expected_exe.st_dev,
            expected_exe.st_ino,
        )
        assert controller.poll() is None
        monitor_owned = True
        if work_service._bubblewrap_pidfd_exited(monitor_fd, 0) is not False:
            pytest.skip("this host could not start scratch Bubblewrap")
        while time.monotonic() < deadline:
            children = Path(f"/proc/{monitor_pid}/task/{monitor_pid}/children").read_text().split()
            if len(children) == 1:
                break
            time.sleep(0.01)
        assert len(children) == 1
        init_fd = os.pidfd_open(int(children[0]))
        assert work_service._bubblewrap_pidfd_target(init_fd) == int(children[0])
        live = work_service._read_bubblewrap_live_identity(monitor_pid, int(children[0]))
        assert live["monitor_start_time_ticks"] == monitor_start
        assert live["monitor_executable_sha256"] == hashlib.sha256(bwrap.read_bytes()).hexdigest()
        assert work_service._bubblewrap_pidfd_target(init_fd) == live["init_pid"]
        init_owned = True
        identity = {**live, "kind": "bubblewrap", "version": 1}
        assert work_service._bubblewrap_pidfd_exited(init_fd, 0) is False

        controller.kill()
        controller.wait(timeout=3)
        assert work_service._bubblewrap_pidfd_exited(monitor_fd, 3.0) is True
        init_exited = work_service._bubblewrap_pidfd_exited(init_fd, 3.0)
        if init_exited is True:
            with monkeypatch.context() as patch:
                patch.setattr(
                    work_service.signal,
                    "pidfd_send_signal",
                    lambda *_: pytest.fail("dead Bubblewrap identity must never be signalled"),
                )
                assert work_service._cleanup_bubblewrap_identity(identity) is True
        else:
            assert init_exited is False
            assert work_service._cleanup_bubblewrap_identity(identity) is True
        assert work_service._bubblewrap_pidfd_exited(init_fd, 3.0) is True
    finally:
        if controller.poll() is None:
            controller.kill()
            controller.wait(timeout=3)
        for fd, owned in ((init_fd, init_owned), (monitor_fd, monitor_owned)):
            if owned and work_service._bubblewrap_pidfd_exited(fd, 0) is not True:
                try:
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
                except OSError:
                    pass
                work_service._bubblewrap_pidfd_exited(fd, 2.0)
        for fd in (init_fd, monitor_fd):
            if fd is not None:
                os.close(fd)
