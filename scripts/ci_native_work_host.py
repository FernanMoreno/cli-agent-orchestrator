"""Prepare one disposable CI host, run pytest unprivileged, restore its policy.

Usage: uv run python scripts/ci_native_work_host.py -- <unchanged pytest arguments>
This is validation infrastructure. Production isolation remains fail closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from xml.etree import ElementTree as ET

APPARMOR_KEY = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
POLICY_NAME = "kernel.apparmor_restrict_unprivileged_userns"
TERM_GRACE_SECONDS = 10.0
KILL_CONFIRM_SECONDS = 5.0
GROUP_POLL_SECONDS = 0.05
SUPERVISOR_CLASS = "test.services.test_work_process_supervisor"
REQUIRED_NATIVE_TESTS = frozenset(
    {
        "test_terminate_waits_for_setsid_double_fork_descendants_to_disappear",
        "test_uncertain_cleanup_blocks_start_until_explicit_reconciliation",
        "test_wait_reconciles_uncertain_attempt_after_natural_exit",
        "test_start_keeps_command_gated_when_unshare_omits_network_and_ipc_namespaces",
        "test_worker_does_not_inherit_server_environment",
        "test_restart_preserves_uncertain_work_and_pending_cleanup_without_redelivery",
    }
)
_PROBE_CODE = """
import json
from cli_agent_orchestrator.services.work_process_supervisor import (
    WorkProcessSupervisor, WorkProcessIsolationUnavailable,
)
try:
    executable = WorkProcessSupervisor()._require_capabilities()
except WorkProcessIsolationUnavailable as exc:
    print(json.dumps({"ready": False, "diagnostic": str(exc)}))
else:
    print(json.dumps({"ready": True, "diagnostic": executable}))
"""
_DENIAL_PREFIX = "rootless user/network/IPC/PID namespace probe failed before work launch: exit=1; "
_DENIALS = frozenset(
    _DENIAL_PREFIX + f"unshare: {operation}: {reason}"
    for operation in (
        "unshare failed",
        "write failed /proc/self/uid_map",
        "write failed /proc/self/gid_map",
    )
    for reason in ("Operation not permitted", "Permission denied")
)


class HostUnavailable(RuntimeError):
    """The validation host has not proved the required native capability."""


class GroupNotDrained(HostUnavailable):
    """Live owned processes could not be ruled out before policy restoration."""


class Cancelled(BaseException):
    def __init__(self, signum: int):
        self.signum = signum


def log(message: str) -> None:
    print(f"ci-native-work-host: {message}", flush=True)


def probe() -> tuple[bool, str]:
    """Call the real production probe in isolated Python, never an argv substitute."""
    result = subprocess.run(
        [sys.executable, "-I", "-c", _PROBE_CODE],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode:
        return False, f"probe Python exited {result.returncode}: {result.stderr.strip()}"
    try:
        data = json.loads(result.stdout)
        if type(data["ready"]) is not bool or not isinstance(data["diagnostic"], str):
            raise ValueError("unexpected production probe result")
        return data["ready"], data["diagnostic"]
    except (ValueError, KeyError, TypeError) as exc:
        raise HostUnavailable("invalid production probe output") from exc


def hosted_ubuntu() -> bool:
    return (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("RUNNER_OS") == "Linux"
        and platform.system() == "Linux"
        and os.geteuid() != 0
        and platform.freedesktop_os_release().get("ID") == "ubuntu"
    )


def describe_host() -> None:
    log(f"kernel={platform.release()} uid={os.geteuid()} platform={platform.system()}")
    log(
        f"runner={os.environ.get('RUNNER_ENVIRONMENT', 'unset')} "
        f"image={os.environ.get('ImageOS', 'unset')}/{os.environ.get('ImageVersion', 'unset')}"
    )
    if platform.system() == "Linux":
        release = platform.freedesktop_os_release()
        log(f"os={release.get('ID', 'unset')} version={release.get('VERSION_ID', 'unset')}")
    unshare = shutil.which("unshare")
    if unshare:
        executable = Path(unshare).resolve()
        digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        result = subprocess.run(
            [str(executable), "--version"], capture_output=True, text=True, timeout=5, check=True
        )
        log(f"unshare={executable} sha256={digest} version={result.stdout.strip()}")
    source = (
        Path(__file__).resolve().parents[1]
        / "src/cli_agent_orchestrator/services/work_process_supervisor.py"
    )
    log(f"supervisor_sha256={hashlib.sha256(source.read_bytes()).hexdigest()}")
    for key in (
        APPARMOR_KEY,
        Path("/proc/sys/kernel/unprivileged_userns_clone"),
        Path("/proc/sys/user/max_user_namespaces"),
        Path("/proc/sys/kernel/yama/ptrace_scope"),
    ):
        log(f"{key}={key.read_text().strip() if key.exists() else 'absent'}")


def set_policy(value: int) -> None:
    subprocess.run(
        ["sudo", "-n", "sysctl", "-w", f"{POLICY_NAME}={value}"],
        stdin=subprocess.DEVNULL,
        timeout=10,
        check=True,
    )


def prepare(state: dict[str, str]) -> None:
    describe_host()
    ready, diagnostic = probe()
    log(f"initial_probe={'PASS' if ready else 'FAIL'} diagnostic={diagnostic}")
    if not ready:
        if diagnostic not in _DENIALS or not hosted_ubuntu():
            raise HostUnavailable("namespace denial is not eligible for CI host preparation")
        if not APPARMOR_KEY.exists() or APPARMOR_KEY.read_text().strip() != "1":
            raise HostUnavailable("AppArmor userns baseline is not the recognized value 1")
        # Save restoration authority before sudo, including partial/failed writes.
        state["restore"] = "1"
        set_policy(0)
        if APPARMOR_KEY.read_text().strip() != "0":
            raise HostUnavailable("AppArmor userns adjustment was not observed")
        log("observed AppArmor userns policy 1 -> 0 on disposable hosted Ubuntu")
    ready, diagnostic = probe()
    log(f"final_probe={'PASS' if ready else 'FAIL'} diagnostic={diagnostic}")
    if not ready:
        raise HostUnavailable("required real production namespace postprobe failed")


def restore(state: dict[str, str]) -> None:
    if "restore" not in state:
        return
    set_policy(int(state["restore"]))
    if APPARMOR_KEY.read_text().strip() != state["restore"]:
        raise HostUnavailable("AppArmor userns restoration was not observed")
    log("AppArmor userns baseline restored and verified")


def validate_native_results(report: Path) -> None:
    cases = [
        case
        for case in ET.parse(report).iter("testcase")
        if case.get("classname") == SUPERVISOR_CLASS
    ]
    seen = {case.get("name") for case in cases}
    missing = REQUIRED_NATIVE_TESTS - seen
    bad = [
        case.get("name")
        for case in cases
        if any(case.find(outcome) is not None for outcome in ("skipped", "failure", "error"))
    ]
    if missing or bad:
        raise HostUnavailable(f"native supervisor evidence missing={sorted(missing)} bad={bad}")
    log(f"native supervisor evidence: {len(cases)} passed, zero skipped; required six present")


def owned_group_members(pgid: int) -> list[tuple[int, str]]:
    """Read only proc stat identity/state; retain only our new-session group.

    start_new_session gives the launched child PGID=SID=PID. A surviving group
    keeps its group identity after the leader exits. Never signal individual
    discovered PIDs, other groups, or detached sessions. Zombies cannot execute
    test work; their parent owns reaping (the wrapper reaps its direct child).
    """
    members = []
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(") ", 1)[1].split()
            except (FileNotFoundError, ProcessLookupError):
                continue
            if int(fields[2]) != pgid:
                continue
            if int(fields[3]) != pgid:
                raise GroupNotDrained("owned process group no longer has its original session")
            members.append((int(entry.name), fields[0]))
    except (OSError, ValueError, IndexError) as exc:
        raise GroupNotDrained("cannot verify owned process-group state through procfs") from exc
    return members


def drain_owned_group(child: subprocess.Popen) -> None:
    pgid = child.pid

    def live_members() -> list[int]:
        return [pid for pid, state in owned_group_members(pgid) if state not in {"Z", "X", "x"}]

    def signal_group(signum: int) -> None:
        # Validate the owned session before signalling its group, even if the
        # leader has already exited. No broad process-tree signalling is used.
        if owned_group_members(pgid):
            try:
                os.killpg(pgid, signum)
            except ProcessLookupError:
                pass
            except OSError as exc:
                raise GroupNotDrained("could not signal the owned process group") from exc

    def wait_group(deadline: float) -> bool:
        while True:
            if not live_members():
                # A second observation catches children forked during the first
                # procfs snapshot; the interval is bounded and never busy-spins.
                time.sleep(GROUP_POLL_SECONDS)
                if not live_members():
                    return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(GROUP_POLL_SECONDS, remaining))

    signal_group(signal.SIGTERM)
    if not wait_group(time.monotonic() + TERM_GRACE_SECONDS):
        signal_group(signal.SIGKILL)
        if not wait_group(time.monotonic() + KILL_CONFIRM_SECONDS):
            raise GroupNotDrained(f"owned process group {pgid} remains live after bounded SIGKILL")
    try:
        child.wait(timeout=KILL_CONFIRM_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise GroupNotDrained("owned process-group leader could not be reaped") from exc


def run_child(argv: list[str]) -> int:
    child = subprocess.Popen(argv, start_new_session=True)
    try:
        return child.wait()
    finally:
        # Leader completion is insufficient on either return or exception.
        # Drain the owned group before the caller can restore host policy.
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            for sig in previous:
                signal.signal(sig, signal.SIG_IGN)
            drain_owned_group(child)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def run(pytest_args: list[str]) -> int:
    if os.geteuid() == 0:
        raise HostUnavailable("CI validation tests must run as a nonroot user")
    # Prevent callers from replacing the private report consumed by this gate.
    if not pytest_args or any(arg.startswith(("--junitxml", "--junit-xml")) for arg in pytest_args):
        raise HostUnavailable("provide pytest selection without a separate JUnit report")
    parent = os.environ.get("RUNNER_TEMP")
    owned = Path(tempfile.mkdtemp(prefix="cao-native-host-", dir=parent))
    report = owned / "native-results.xml"
    state: dict[str, str] = {}
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def cancel(signum, _frame):
        raise Cancelled(signum)

    try:
        for sig in previous:
            signal.signal(sig, cancel)
        prepare(state)
        result = run_child([sys.executable, "-m", "pytest", *pytest_args, f"--junitxml={report}"])
        if result:
            return result
        validate_native_results(report)
        return 0
    except GroupNotDrained:
        state["drain_failed"] = "1"
        raise
    finally:
        # Ordinary cancellation is handled above. SIGKILL cannot execute finally;
        # the disposable hosted VM is the final cleanup boundary in that case.
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        try:
            if "drain_failed" in state:
                log(
                    "FAIL: owned group drain unconfirmed; policy restoration deferred to VM disposal"
                )
            else:
                restore(state)
        finally:
            try:
                shutil.rmtree(owned)
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)


def main() -> int:
    arguments = sys.argv[1:]
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    try:
        return run(arguments)
    except Cancelled as exc:
        log(f"cancelled by signal {exc.signum}; owned test process drained")
        return 128 + exc.signum
    except (HostUnavailable, OSError, subprocess.SubprocessError, ET.ParseError) as exc:
        log(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
