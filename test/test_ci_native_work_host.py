"""CI host policy must require real readiness and restore its single adjustment."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from scripts import ci_native_work_host as host

DENIED = (
    "rootless user/network/IPC/PID namespace probe failed before work launch: "
    "exit=1; unshare: write failed /proc/self/uid_map: Operation not permitted"
)


@pytest.fixture
def policy(tmp_path, monkeypatch):
    key = tmp_path / "apparmor-key"
    key.write_text("1\n")
    monkeypatch.setattr(host, "APPARMOR_KEY", key)
    monkeypatch.setattr(host, "hosted_ubuntu", lambda: True)
    changes = []

    def change(value):
        changes.append(value)
        key.write_text(f"{value}\n")

    monkeypatch.setattr(host, "set_policy", change)
    monkeypatch.setattr(host, "describe_host", lambda: None)
    return key, changes


def test_capable_host_never_mutates_policy(policy, monkeypatch):
    calls = []
    monkeypatch.setattr(host, "probe", lambda: calls.append(True) or (True, "ready"))
    state = {}
    host.prepare(state)
    host.restore(state)
    assert len(calls) == 2
    assert policy[1] == []


def test_canonical_denial_changes_only_one_policy_then_restores(policy, monkeypatch):
    results = iter([(False, DENIED), (True, "ready")])
    monkeypatch.setattr(host, "probe", lambda: next(results))
    state = {}
    host.prepare(state)
    assert policy[1] == [0]
    host.restore(state)
    assert policy[1] == [0, 1]
    assert policy[0].read_text().strip() == "1"


@pytest.mark.parametrize(
    "reason",
    [
        "unexpected import failure",
        DENIED.replace("exit=1", "exit=126"),
        DENIED.replace("write failed /proc/self/uid_map", "failed to execute /python"),
        DENIED + "\ntraceback",
        "rootless user/network/IPC/PID namespace capability probe timed out",
    ],
)
def test_unexpected_probe_failure_never_adjusts_policy(policy, monkeypatch, reason):
    monkeypatch.setattr(host, "probe", lambda: (False, reason))
    with pytest.raises(host.HostUnavailable):
        host.prepare({})
    assert policy[1] == []


@pytest.mark.parametrize("baseline", ["0", "2", "missing"])
def test_unapproved_policy_baseline_refuses_denial(policy, monkeypatch, baseline):
    if baseline == "missing":
        policy[0].unlink()
    else:
        policy[0].write_text(baseline)
    monkeypatch.setattr(host, "probe", lambda: (False, DENIED))
    with pytest.raises(host.HostUnavailable):
        host.prepare({})
    assert policy[1] == []


def test_non_hosted_or_root_context_never_adjusts_policy(policy, monkeypatch):
    monkeypatch.setattr(host, "hosted_ubuntu", lambda: False)
    monkeypatch.setattr(host, "probe", lambda: (False, DENIED))
    with pytest.raises(host.HostUnavailable):
        host.prepare({})
    assert policy[1] == []


@pytest.mark.parametrize(
    "invalid",
    [None, "actions", "runner", "runner_os", "system", "uid", "distribution"],
)
def test_host_context_requires_disposable_github_ubuntu_and_nonroot(monkeypatch, invalid):
    monkeypatch.setenv("GITHUB_ACTIONS", "false" if invalid == "actions" else "true")
    monkeypatch.setenv(
        "RUNNER_ENVIRONMENT", "self-hosted" if invalid == "runner" else "github-hosted"
    )
    monkeypatch.setenv("RUNNER_OS", "macOS" if invalid == "runner_os" else "Linux")
    monkeypatch.setattr(
        host.platform, "system", lambda: "Darwin" if invalid == "system" else "Linux"
    )
    monkeypatch.setattr(host.os, "geteuid", lambda: 0 if invalid == "uid" else 1000)
    monkeypatch.setattr(
        host.platform,
        "freedesktop_os_release",
        lambda: {"ID": "debian" if invalid == "distribution" else "ubuntu"},
    )
    assert host.hosted_ubuntu() is (invalid is None)


def test_root_cannot_run_tests_even_with_capable_namespaces(monkeypatch):
    monkeypatch.setattr(host.os, "geteuid", lambda: 0)
    calls = []
    monkeypatch.setattr(host, "prepare", lambda state: calls.append("prepare"))
    monkeypatch.setattr(host, "run_child", lambda argv: calls.append("tests"))
    with pytest.raises(host.HostUnavailable, match="nonroot"):
        host.run(["test/services/test_work_process_supervisor.py"])
    assert calls == []


def test_failed_postprobe_still_restores_baseline(policy, monkeypatch):
    monkeypatch.setattr(host, "probe", lambda: (False, DENIED))
    state = {}
    try:
        with pytest.raises(host.HostUnavailable):
            host.prepare(state)
    finally:
        host.restore(state)
    assert policy[1] == [0, 1]


def test_failed_sudo_attempt_still_restores_partial_mutation(policy, monkeypatch):
    def change(value):
        policy[1].append(value)
        policy[0].write_text(str(value))
        if value == 0:
            raise subprocess.CalledProcessError(1, ["sudo"])

    monkeypatch.setattr(host, "set_policy", change)
    monkeypatch.setattr(host, "probe", lambda: (False, DENIED))
    state = {}
    try:
        with pytest.raises(subprocess.CalledProcessError):
            host.prepare(state)
    finally:
        host.restore(state)
    assert policy[1] == [0, 1]


def test_restore_verifies_actual_kernel_value(policy, monkeypatch):
    policy[0].write_text("0")
    monkeypatch.setattr(host, "set_policy", lambda value: None)
    with pytest.raises(host.HostUnavailable, match="restoration"):
        host.restore({"restore": "1"})


def junit(path: Path, *, omit=None, outcome=None):
    suite = ET.Element("testsuite")
    for name in host.REQUIRED_NATIVE_TESTS:
        if name == omit:
            continue
        case = ET.SubElement(suite, "testcase", classname=host.SUPERVISOR_CLASS, name=name)
        if outcome:
            ET.SubElement(case, outcome)
    ET.ElementTree(suite).write(path)


def test_junit_requires_actual_native_success(tmp_path):
    report = tmp_path / "results.xml"
    junit(report)
    host.validate_native_results(report)


@pytest.mark.parametrize("outcome", ["skipped", "failure", "error"])
def test_junit_refuses_skipped_or_failed_supervisor_cases(tmp_path, outcome):
    report = tmp_path / "results.xml"
    junit(report, outcome=outcome)
    with pytest.raises(host.HostUnavailable):
        host.validate_native_results(report)


def test_junit_refuses_missing_native_test(tmp_path):
    report = tmp_path / "results.xml"
    junit(report, omit=next(iter(host.REQUIRED_NATIVE_TESTS)))
    with pytest.raises(host.HostUnavailable):
        host.validate_native_results(report)


def test_junit_refuses_skip_in_any_supervisor_case(tmp_path):
    report = tmp_path / "results.xml"
    junit(report)
    tree = ET.parse(report)
    extra = ET.SubElement(tree.getroot(), "testcase", classname=host.SUPERVISOR_CLASS, name="other")
    ET.SubElement(extra, "skipped")
    tree.write(report)
    with pytest.raises(host.HostUnavailable):
        host.validate_native_results(report)


@pytest.mark.parametrize("contents", [None, "broken XML", "<testsuite/>"])
def test_junit_missing_corrupt_or_empty_cannot_pass(tmp_path, contents):
    report = tmp_path / "results.xml"
    if contents is not None:
        report.write_text(contents)
    with pytest.raises((OSError, ET.ParseError, host.HostUnavailable)):
        host.validate_native_results(report)


@pytest.mark.parametrize("result", [1, KeyboardInterrupt(), RuntimeError("child error")])
def test_runner_failure_or_cancellation_restores_and_deletes_owned_state(
    tmp_path, policy, monkeypatch, result
):
    results = iter([(False, DENIED), (True, "ready")])
    monkeypatch.setattr(host, "probe", lambda: next(results))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))

    def child(argv):
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(host, "run_child", child)
    if isinstance(result, BaseException):
        with pytest.raises(type(result)):
            host.run(["test/services/test_work_process_supervisor.py"])
    else:
        assert host.run(["test/services/test_work_process_supervisor.py"]) == 1
    assert policy[1] == [0, 1]
    assert not list(tmp_path.glob("cao-native-host-*"))


def test_real_production_probe_executes_without_namespace_double():
    ready, diagnostic = host.probe()
    assert isinstance(ready, bool)
    assert diagnostic
    # Unsupported local hosts may refuse readiness; the wrapper never promotes
    # that refusal to success. CI's postprobe and native JUnit gate are mandatory.


def test_successful_test_command_restores_policy_and_deletes_report(tmp_path, policy, monkeypatch):
    results = iter([(False, DENIED), (True, "ready")])
    monkeypatch.setattr(host, "probe", lambda: next(results))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))

    def child(argv):
        assert argv[:3] == [sys.executable, "-m", "pytest"]
        report = Path(argv[-1].partition("=")[2])
        junit(report)
        return 0

    monkeypatch.setattr(host, "run_child", child)
    assert host.run(["test/services/test_work_process_supervisor.py"]) == 0
    assert policy[1] == [0, 1]
    assert not list(tmp_path.glob("cao-native-host-*"))


def test_real_child_is_drained_before_cancellation_returns(monkeypatch):
    children = []
    original = subprocess.Popen

    def launch(argv, **kwargs):
        child = original(argv, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(host.subprocess, "Popen", launch)
    previous = signal.getsignal(signal.SIGALRM)

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGALRM, interrupt)
    signal.setitimer(signal.ITIMER_REAL, 0.1)
    try:
        with pytest.raises(KeyboardInterrupt):
            host.run_child([sys.executable, "-c", "import time; time.sleep(60)"])
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    assert len(children) == 1
    assert children[0].returncode == -signal.SIGTERM
    with pytest.raises(ProcessLookupError):
        os.kill(children[0].pid, 0)


_OWNED_GROUP_REPRO = r"""
import ctypes, importlib.util, json, os, signal, subprocess, sys, threading, time
from pathlib import Path
mode, helper, marker = sys.argv[1:]
check_restore = mode.startswith("restore-")
mode = mode.removeprefix("restore-")
# Orphan reaping applies only to this disposable verifier process, never pytest
# or the user's server. It owns no children outside this reproduction.
assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
spec = importlib.util.spec_from_file_location("host", helper)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
host.TERM_GRACE_SECONDS = 0.1
host.KILL_CONFIRM_SECONDS = 1.0
descendant = (
    "import os,signal,time; from pathlib import Path; "
    "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
    "Path(" + repr(marker) + ").write_text(str(os.getpid())); time.sleep(60)"
)
leader = (
    "import subprocess,sys,time; from pathlib import Path; "
    "subprocess.Popen([sys.executable,'-c'," + repr(descendant) + "]); "
    "marker=Path(" + repr(marker) + "); "
    "\nwhile not marker.exists(): time.sleep(0.01)\n"
    + ("time.sleep(60)" if mode == "cancel" else "sys.exit(0)")
)
original = subprocess.Popen
children = []
sentinel = original([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
restored = []

class ReapedLeader:
    def __init__(self, child):
        self.child = child
        self.first = True
    def __getattr__(self, name):
        return getattr(self.child, name)
    def wait(self, timeout=None):
        result = self.child.wait(timeout=timeout)
        if self.first:
            self.first = False
            raise KeyboardInterrupt
        return result

def launch(argv, **kwargs):
    if check_restore:
        argv = [sys.executable, "-c", leader]
    child = original(argv, **kwargs)
    children.append(child)
    return ReapedLeader(child) if mode == "reaped" else child

host.subprocess.Popen = launch
if check_restore:
    host.prepare = lambda state: state.update(restore="1")
    def restore(state):
        pid = int(Path(marker).read_text())
        proc = Path(f"/proc/{pid}/stat")
        status = proc.read_text().rsplit(") ", 1)[1].split()[0] if proc.exists() else "gone"
        assert status in {"Z", "X", "x", "gone"}, "policy restored while owned child was live"
        restored.append(True)
    host.restore = restore
    host.validate_native_results = lambda report: None
def interrupt(_signum, _frame):
    raise KeyboardInterrupt
signal.signal(signal.SIGINT, interrupt)

def cancel_when_ready():
    deadline = time.monotonic() + 5
    while not Path(marker).exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    os.kill(os.getpid(), signal.SIGINT)

if mode == "cancel":
    threading.Thread(target=cancel_when_ready, daemon=True).start()
try:
    try:
        result = host.run(["selection"]) if check_restore else host.run_child([sys.executable, "-c", leader])
    except (KeyboardInterrupt, host.Cancelled):
        result = "KeyboardInterrupt"
    pid = int(Path(marker).read_text())
    proc = Path(f"/proc/{pid}/stat")
    state = proc.read_text().rsplit(") ", 1)[1].split()[0] if proc.exists() else "gone"
    print(json.dumps({"mode": mode, "result": result, "leader_status": children[0].returncode,
                      "descendant_state": state, "live": state not in {"Z", "X", "x", "gone"},
                      "restored": bool(restored), "sentinel_live": sentinel.poll() is None}))
finally:
    for child in children:
        try: os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        child.wait(timeout=2)
    try: os.killpg(sentinel.pid, signal.SIGKILL)
    except ProcessLookupError: pass
    sentinel.wait(timeout=2)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if pid == 0:
            time.sleep(0.01)
    if Path(marker).exists():
        pid = int(Path(marker).read_text())
        assert not Path(f"/proc/{pid}").exists(), "owned descendant was not reaped"
"""


@pytest.mark.parametrize("mode", ["cancel", "reaped", "normal", "restore-cancel", "restore-normal"])
def test_real_owned_group_is_drained_even_after_leader_exit(tmp_path, mode):
    helper = Path(host.__file__).resolve()
    result = subprocess.run(
        [sys.executable, "-c", _OWNED_GROUP_REPRO, mode, str(helper), str(tmp_path / "pid")],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence["live"] is False, evidence
    assert evidence["sentinel_live"] is True, evidence
    if mode.startswith("restore-"):
        assert evidence["restored"] is True, evidence


def test_unconfirmed_group_drain_cannot_restore_policy(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(host, "prepare", lambda state: state.update(restore="1"))
    monkeypatch.setattr(host, "restore", lambda state: calls.append("restore"))

    def failed_child(argv):
        raise host.GroupNotDrained("owned group still alive")

    monkeypatch.setattr(host, "run_child", failed_child)
    with pytest.raises(host.GroupNotDrained):
        host.run(["selection"])
    assert calls == []
    assert not list(tmp_path.glob("cao-native-host-*"))


def test_owned_group_that_survives_kill_fails_with_bounded_escalation(monkeypatch):
    from types import SimpleNamespace

    calls = []
    child = SimpleNamespace(pid=12345)
    monkeypatch.setattr(host, "owned_group_members", lambda pgid: [(pgid, "S")])
    monkeypatch.setattr(host.os, "killpg", lambda pgid, sig: calls.append((pgid, sig)))
    monkeypatch.setattr(host, "TERM_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(host, "KILL_CONFIRM_SECONDS", 0.01)
    started = host.time.monotonic()
    with pytest.raises(host.GroupNotDrained, match="bounded SIGKILL"):
        host.drain_owned_group(child)
    assert host.time.monotonic() - started < 1
    assert calls == [(12345, signal.SIGTERM), (12345, signal.SIGKILL)]
