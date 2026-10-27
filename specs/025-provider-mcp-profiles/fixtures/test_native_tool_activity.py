"""Actual OS controls; Python anchors here are not OpenCode acceptance."""

import dataclasses
import os
import subprocess
import sys
import time
from pathlib import Path

import native_tool_activity as activity
import pytest

pytestmark = pytest.mark.offline_database


@pytest.fixture
def held_probe():
    nonce = "a" * 32
    env = dict(os.environ, CAO_TERMINAL_ID="deadbeef", TMUX="/private/socket,1,0")
    child = subprocess.Popen(activity.command(nonce), env=env)
    try:
        deadline = time.monotonic() + 10
        while True:
            pid = activity.process_identity(child.pid)
            if pid and activity._arguments(pid.pid) == activity.command(nonce):
                break
            assert child.poll() is None and time.monotonic() < deadline
            time.sleep(0.01)
        yield child, dict(
            candidates=[pid],
            nonce=nonce,
            terminal_id="deadbeef",
            socket_path=Path("/private/socket"),
            anchor=activity.process_identity(os.getpid()),
            anchor_executable=Path(sys.executable).resolve(),
            anchor_options={},
        )
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=10)


def test_actual_pidfd_ancestry_and_bounded_death(held_probe):
    child, kwargs = held_probe
    w = activity.capture(**kwargs)
    try:
        assert w.child.pid == child.pid and not activity.exited(w)
        second = activity.capture(**kwargs)
        try:
            assert second == w
        finally:
            os.close(second.pidfd)
        child.terminate()
        child.wait(timeout=10)
        activity.require_stopped(w, deadline=time.monotonic() + 2)
        assert activity.exited(w)
    finally:
        os.close(w.pidfd)


@pytest.mark.parametrize(
    "fault",
    [
        "nonce",
        "stale_child",
        "missing",
        "terminal",
        "socket",
        "birth",
        "anchor",
        "executable",
        "option",
        "duplicate",
    ],
)
def test_unowned_or_changed_process_cannot_establish_activity(held_probe, fault):
    child, kwargs = held_probe
    if fault == "nonce":
        kwargs["nonce"] = "b" * 32
    elif fault == "stale_child":
        kwargs["candidates"] = [dataclasses.replace(kwargs["candidates"][0], birth=0)]
    elif fault == "missing":
        kwargs["candidates"] = []
    elif fault == "terminal":
        kwargs["terminal_id"] = "feedbeef"
    elif fault == "socket":
        kwargs["socket_path"] = Path("/foreign/socket")
    elif fault == "birth":
        kwargs["anchor"] = dataclasses.replace(kwargs["anchor"], birth=kwargs["anchor"].birth + 1)
    elif fault == "anchor":
        kwargs["anchor"] = kwargs["candidates"][
            0
        ]  # exact exe but anchor options below deliberately wrong
    elif fault == "executable":
        kwargs["anchor_executable"] = Path("/bin/false").resolve()
    elif fault == "option":
        kwargs["anchor_options"] = {"--model": "wrong"}
    elif fault == "duplicate":
        kwargs["candidates"] *= 2
    if fault == "anchor":
        kwargs["anchor_options"] = {"--model": "wrong"}
    if fault in ["nonce", "stale_child", "missing"]:
        assert activity.capture(**kwargs) is None
    else:
        with pytest.raises(activity.ActivityError):
            activity.capture(**kwargs)
    assert child.poll() is None


def test_live_process_cannot_be_accepted_as_stopped(held_probe):
    _, kwargs = held_probe
    w = activity.capture(**kwargs)
    try:
        with pytest.raises(activity.ActivityError):
            activity.require_stopped(w, deadline=time.monotonic() + 0.02)
    finally:
        os.close(w.pidfd)


def test_foreign_anchor_with_same_executable_is_rejected(held_probe):
    _, kwargs = held_probe
    other = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(120)"])
    try:
        kwargs["anchor"] = activity.process_identity(other.pid)
        with pytest.raises(activity.ActivityError):
            activity.capture(**kwargs)
    finally:
        other.terminate()
        other.wait(timeout=10)


def test_unreadable_owned_environment_fails_closed(held_probe, monkeypatch):
    _, kwargs = held_probe
    read = activity._read

    def blocked(pid, name):
        if name == "environ":
            raise PermissionError("controlled")
        return read(pid, name)

    monkeypatch.setattr(activity, "_read", blocked)
    with pytest.raises(PermissionError):
        activity.capture(**kwargs)


@pytest.mark.parametrize(
    "fault",
    [None, "generation", "lost", "identity", "stop_receipt", "stop_unproved", "anchor_alive"],
)
def test_cancel_requires_fresh_same_generation_and_process_proof(monkeypatch, tmp_path, fault):
    import asyncio
    from types import SimpleNamespace

    import native_paired_protocol as protocol
    import run_native_paired_worker as journey

    first = os.open("/dev/null", os.O_RDONLY)
    second = os.open("/dev/null", os.O_RDONLY)
    witness = activity.Witness(
        activity.Identity(999999, os.getuid(), 1),
        activity.Identity(999998, os.getuid(), 1),
        (),
        time.clock_gettime(time.CLOCK_BOOTTIME) + 100,
        first,
    )
    fresh = dataclasses.replace(witness, pidfd=second)
    if fault == "identity":
        fresh = dataclasses.replace(fresh, child=dataclasses.replace(fresh.child, birth=2))
    snapshots = iter([witness, None if fault == "lost" else fresh])
    events = []
    monkeypatch.setattr(journey, "native_tool_witness", lambda *_a: next(snapshots))
    turn = {"generation": "a" * 32}

    def api(*_a, **_kw):
        events.append("fresh_turn")
        return {"turn": {"generation": "b" * 32 if fault == "generation" else "a" * 32}}

    monkeypatch.setattr(journey, "api", api)
    monkeypatch.setattr(journey, "admit_memory", lambda *_a: events.append("memory"))
    monkeypatch.setattr(activity, "exited", lambda _: False)

    def stopped(*_a, **_kw):
        events.append("physical_stop")
        if fault == "stop_unproved":
            raise activity.ActivityError("not stopped")

    monkeypatch.setattr(activity, "require_stopped", stopped)
    monkeypatch.setattr(
        activity, "process_identity", lambda _: witness.anchor if fault == "anchor_alive" else None
    )

    class Client:
        async def call(self, name, body):
            events.append(name)
            return {
                "task_id": "task1",
                "state": "cancelled",
                "result": {"terminal_id": "deadbeef", "worker_stopped": fault != "stop_receipt"},
            }

    receipt = {"evidence_dir": str(tmp_path)}
    retired = set()
    try:
        call = journey.cancel_witnessed_tool(
            Client(),
            SimpleNamespace(url="http://owned"),
            "deadbeef",
            "task1",
            {},
            turn,
            "a" * 32,
            {},
            receipt,
            retired,
            time.monotonic() + (0.05 if fault == "anchor_alive" else 5),
        )
        if fault:
            with pytest.raises((protocol.HarnessError, activity.ActivityError)):
                asyncio.run(call)
            assert not receipt.get("native_tool_child_stopped_before_natural_expiry")
        else:
            assert asyncio.run(call) is True
            assert receipt["native_tool_child_stopped_before_natural_expiry"] is True
            assert events == ["fresh_turn", "memory", "cancel_local_cao_task", "physical_stop"]
        if fault in ["generation", "lost", "identity"]:
            assert "cancel_local_cao_task" not in events
        with pytest.raises(OSError):
            os.fstat(first)
    finally:
        for fd in [first, second]:
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.mark.parametrize("fault", ["status", "turn", "generation", "identity"])
def test_task_state_alone_never_authorizes_process_cancellation(monkeypatch, fault):
    import run_native_paired_worker as journey

    metadata = {"id": "deadbeef", "provider": "opencode_cli", "status": "processing"}
    turn = {
        "terminal_id": "deadbeef",
        "provider": "opencode_cli",
        "state": "pending",
        "generation": "a" * 32,
    }
    if fault == "status":
        metadata["status"] = "completed"
    elif fault == "turn":
        turn["state"] = "verified"
    elif fault == "generation":
        turn["generation"] = None
    elif fault == "identity":
        turn["terminal_id"] = "feedbeef"
    monkeypatch.setattr(
        journey,
        "owned_native_launch_identity",
        lambda *_a, **_kw: pytest.fail("identity scan should not start"),
    )
    assert journey.native_tool_witness("deadbeef", metadata, turn, "a" * 32, {}) is None


@pytest.mark.parametrize("case", ["streaming", "native_tool"])
def test_private_profile_enables_shell_only_for_explicit_tool_case(case):
    import frontmatter
    import run_native_paired_worker as journey

    from cli_agent_orchestrator.utils.opencode_permissions import cao_tools_to_opencode_permission
    from cli_agent_orchestrator.utils.opencode_v2 import _rules

    profile = frontmatter.loads(journey.worker_profile({}, activity_case=case))
    expected = ["execute_bash"] if case == "native_tool" else []
    assert profile.metadata["allowedTools"] == expected
    assert profile.metadata["mcpServers"]["cao-mcp-server"]["enabled_tools"] == []
    rules = _rules(cao_tools_to_opencode_permission(expected))
    assert [p for p in rules if p["action"] == "shell"] == [
        {"action": "shell", "resource": "*", "effect": "allow" if case == "native_tool" else "deny"}
    ]
    assert all(
        p["effect"] == "deny" for p in rules if p["action"] in ["edit", "subagent", "execute"]
    )


@pytest.mark.parametrize("case", ["streaming", "native_tool", "unknown"])
@pytest.mark.parametrize("missing", [False, True])
def test_tool_case_cannot_close_streaming_or_pass_without_its_own_proof(monkeypatch, case, missing):
    import run_native_paired_worker as journey

    proof = {
        k: True
        for k in [
            "production_pairing_verified",
            "caller_controller_created",
            "mcp_catalog_exact_twelve",
            "native_preflight_passed",
            "native_success_exact",
            "same_key_replay_exact",
            "cancelled_worker_stopped",
            "git_baseline_unchanged",
            "binary_unchanged",
            "interpreter_unchanged",
            "native_tool_child_witnessed",
            "native_tool_child_stopped_before_natural_expiry",
            "native_tool_worker_stopped",
        ]
    }
    proof.update(
        schema="caos.spec025.t168.native-paired.v1",
        mode="execute",
        activity_case=case,
        model=journey.MODEL,
        binary_sha256=journey.BINARY_SHA256,
        task_seconds=[1, 2],
        memory_admissions_mib={"initial": 6144},
    )
    monkeypatch.setattr(journey, "cleanup_safe", lambda _: True)
    if missing:
        proof.pop("native_tool_child_stopped_before_natural_expiry")
    assert journey.acceptance(proof) is (case == "native_tool" and not missing)


def test_natural_expiry_is_never_cancel_proof(held_probe, monkeypatch):
    child, kwargs = held_probe
    w = activity.capture(**kwargs)
    try:
        child.terminate()
        child.wait(timeout=10)
        monkeypatch.setattr(activity.time, "clock_gettime", lambda _: w.expires + 1)
        with pytest.raises(activity.ActivityError):
            activity.require_stopped(w, deadline=time.monotonic() + 1)
    finally:
        os.close(w.pidfd)


def test_candidate_disappearing_before_arguments_is_not_an_active_witness(held_probe, monkeypatch):
    _, kwargs = held_probe
    original = activity._arguments
    monkeypatch.setattr(
        activity, "_arguments", lambda _: (_ for _ in ()).throw(FileNotFoundError())
    )
    assert activity.capture(**kwargs) is None
    monkeypatch.setattr(activity, "_arguments", original)
    witness = activity.capture(**kwargs)
    os.close(witness.pidfd)


def test_worker_can_exit_after_its_tool_within_original_deadline(held_probe):
    _, kwargs = held_probe
    witness = activity.capture(**kwargs)
    worker = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(.15)"])
    try:
        identity = activity.process_identity(worker.pid)
        assert identity
        witness = dataclasses.replace(witness, anchor=identity)
        activity.require_worker_stopped(witness, deadline=time.monotonic() + 3)
        worker.wait(timeout=3)
    finally:
        if worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=3)
        os.close(witness.pidfd)


def test_probe_failure_code_survives_structured_exception_projection():
    import run_native_paired_worker as journey

    report = journey.exception_report(activity.ActivityError("probe_ancestry_mismatch"))
    assert report["harness_codes"] == ["probe_ancestry_mismatch"]


def test_three_native_tool_polls_use_distinct_owned_private_files(tmp_path, monkeypatch):
    import asyncio
    import json
    import stat
    from types import SimpleNamespace

    import run_native_paired_worker as journey

    from cli_agent_orchestrator.services.worktree_service import worktree_path_for

    target = SimpleNamespace(url="http://owned")
    calls = []
    tasks = {}
    receipt = {"activity_case": "native_tool", "evidence_dir": str(tmp_path)}

    class Client:
        async def call(self, name, body):
            if name == "assign_local_cao_task":
                index = int(body["operation_key"].rsplit("-", 1)[1])
                tid = "abcdef0" + str(index)
                tasks[tid] = {"task_id": tid, "terminal_id": tid}
                return tasks[tid]
            assert name == "get_local_cao_task"
            if body["task_id"] == "abcdef00":
                return {"state": "succeeded", "result": {"output": journey.EXPECTED}}
            return {"state": "running"}

    def api(server, method, route, **kwargs):
        tid = route.split("/")[2]
        if route.endswith("/working-directory"):
            return {"working_directory": worktree_path_for(str(tmp_path), tid)}
        return {
            "id": tid,
            "provider": "opencode_cli",
            "status": "processing",
            "turn": {
                "terminal_id": tid,
                "provider": "opencode_cli",
                "state": "pending",
                "generation": "a" * 32,
            },
        }

    async def cancel(*args):
        calls.append(1)
        return len(calls) == 3

    monkeypatch.setattr(journey, "api", api)
    monkeypatch.setattr(journey, "admit_memory", lambda *_: None)
    monkeypatch.setattr(journey, "worker_configuration", lambda *_a, **_kw: tmp_path)
    monkeypatch.setattr(journey, "retire_terminal", lambda *_a: None)
    monkeypatch.setattr(journey, "cancel_witnessed_tool", cancel)
    asyncio.run(
        journey._tasks(
            Client(),
            None,
            target,
            tmp_path,
            tmp_path,
            SimpleNamespace(target_instance_id="peer"),
            "caller",
            receipt,
            [],
            {},
            set(),
        )
    )
    files = sorted(tmp_path.glob("native-tool-poll-*.private.json"))
    assert len(files) == 3 and len(calls) == 3
    assert len(receipt["native_tool_observations"]) == 3
    assert all(
        json.loads(p.read_text())["turn_state"] == "pending"
        and stat.S_IMODE(p.stat().st_mode) == 0o600
        for p in files
    )
