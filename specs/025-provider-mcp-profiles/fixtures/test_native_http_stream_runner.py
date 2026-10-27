"""Installed HTTP observer/runner composition; no provider/model or actual API."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import run_native_paired_worker as runner

pytestmark = pytest.mark.offline_database


def test_prepare_commits_loader_and_real_worktree_inherits_it(tmp_path):
    from test.live_provider.opencode_interaction_journey import _initialize_private_git_repository

    project = tmp_path / "project"
    _initialize_private_git_repository(project)
    before = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project).strip()
    result = runner.prepare_http_observer(project, tmp_path / "runtime", "a" * 32)
    loader = project / result.project_loader_path
    after = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project).strip()
    assert after != before
    assert hashlib.sha256(loader.read_bytes()).hexdigest() == result.project_loader_sha256
    assert loader.stat().st_mode & 0o777 == 0o644
    assert (
        subprocess.check_output(["git", "show", "HEAD:" + result.project_loader_path], cwd=project)
        == loader.read_bytes()
    )
    worktree = tmp_path / "worker"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree), "HEAD"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    try:
        assert (worktree / result.project_loader_path).read_bytes() == loader.read_bytes()
        expected = json.loads(result.expectation_path.read_text())
        assert expected["phase_prompts"] == [
            runner.task_message(i, "native_http_stream") for i in range(2)
        ]
        assert expected["receipt_suffix"].count("{{CAO_TERMINAL_ID}}") == 1
        assert all(p.endswith(expected["last_instruction"]) for p in expected["phase_prompts"])
    finally:
        subprocess.run(
            ["git", "worktree", "remove", str(worktree)],
            cwd=project,
            check=True,
            capture_output=True,
        )


def test_http_case_requires_its_own_proof_and_preserves_other_cases(monkeypatch):
    monkeypatch.setattr(runner, "cleanup_safe", lambda _: True)
    flags = [
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
        "http_stream_growth_witnessed",
        "http_stream_final_fence_verified",
        "http_stream_worker_stopped",
    ]
    receipt = dict.fromkeys(flags, True)
    receipt.update(
        schema="caos.spec025.t168.native-paired.v1",
        mode="execute",
        activity_case="native_http_stream",
        model=runner.MODEL,
        binary_sha256=runner.BINARY_SHA256,
        task_seconds=[1, 2],
        memory_admissions_mib={"initial": 6144},
    )
    assert runner.acceptance(receipt) is True
    for flag in flags[-3:]:
        assert runner.acceptance({k: v for k, v in receipt.items() if k != flag}) is False
    for case in ["streaming", "native_tool", "unknown"]:
        assert runner.acceptance(dict(receipt, activity_case=case)) is False


@pytest.mark.parametrize("physical_stop", [False, True])
def test_retirement_and_http_flags_follow_physical_proof(monkeypatch, tmp_path, physical_stop):
    import asyncio
    from types import SimpleNamespace

    retired = set()
    receipt = {
        "evidence_dir": str(tmp_path),
        "http_stream_runtime": str(tmp_path / "runtime"),
        "http_stream_nonce": "a" * 32,
    }
    target = SimpleNamespace(url="http://owned.invalid")

    class Client:
        async def call(self, name, arguments):
            assert name == "cancel_local_cao_task" and arguments == {"task_id": "task"}
            return {
                "task_id": "task",
                "state": "cancelled",
                "result": {"worker_stopped": True, "terminal_id": "0123abcd"},
            }

    async def controller(**kwargs):
        assert await kwargs["cancel"]() is True
        assert retired == set()
        assert "http_stream_worker_stopped" not in receipt
        if not physical_stop:
            raise runner.http_controller.ControllerError("http_stream_native_process_not_stopped")
        snap = runner.http_activity.Snapshot(
            "0123abcd", "a" * 32, "b" * 64, (1, 2), 1, 2, 10, 2, 100, 2
        )
        return runner.http_controller.CancellationProof(
            snap, snap, snap, runner.http_controller.ProcessIdentity(42, os.getuid(), 1)
        )

    monkeypatch.setattr(runner.http_controller, "cancel_if_growing_http_text", controller)
    monkeypatch.setattr(runner, "admit_memory", lambda *args: None)
    monkeypatch.setattr(runner, "private_json", lambda *args: None)
    operation = runner.cancel_witnessed_http(
        Client(),
        target,
        "0123abcd",
        "task",
        {},
        receipt,
        retired,
        __import__("time").monotonic() + 5,
    )
    if physical_stop:
        assert asyncio.run(operation) is True
        assert retired == {(target.url, "0123abcd")}
        assert receipt["http_stream_worker_stopped"] is True
    else:
        with pytest.raises(
            runner.protocol.HarnessError, match="http_stream_native_process_not_stopped"
        ):
            asyncio.run(operation)
        assert retired == set()
        assert "http_stream_worker_stopped" not in receipt


def test_installed_worktree_node_writer_strict_reader_controller_and_pidfd_compose(
    monkeypatch, tmp_path
):
    import asyncio
    import select
    import shutil
    import time
    from test.live_provider import opencode_http_text_stream_observer as observer
    from test.live_provider import opencode_interaction_journey as journey
    from types import SimpleNamespace

    from cli_agent_orchestrator.providers.base import BaseProvider

    project = tmp_path / "project"
    journey._initialize_private_git_repository(project)
    nonce, terminal, generation = "a" * 32, "0123abcd", "b" * 32
    token = "CAO-TURN-RECEIPT-" + "c" * 32
    digest = hashlib.sha256(token.encode()).hexdigest()
    installed = runner.prepare_http_observer(project, tmp_path / "runtime", nonce)
    worker = tmp_path / "worker"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(worker), "HEAD"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    worker.chmod(0o700)
    source = tmp_path / "source-prompt.txt"
    source.write_text(
        BaseProvider._format_turn_receipt_input(
            SimpleNamespace(terminal_id=terminal),
            runner.task_message(1, "native_http_stream"),
            token,
        )
    )
    source.chmod(0o600)
    node = shutil.which("node")
    assert node is not None
    script = Path(__file__).parent / "t187/control/installed_http_stream.mjs"
    process = subprocess.Popen(
        [node, str(script), str(worker / installed.project_loader_path), str(source)],
        cwd=worker,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "CAO_TERMINAL_ID": terminal},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )
    receipt = {
        "evidence_dir": str(tmp_path),
        "http_stream_runtime": str(installed.private_root),
        "http_stream_nonce": nonce,
    }
    retired = set()
    target = SimpleNamespace(
        url="http://synthetic-api.invalid", database_targets=SimpleNamespace(main=object())
    )
    metadata = {
        "id": terminal,
        "provider": "opencode_cli",
        "status": "processing",
        "turn": {
            "terminal_id": terminal,
            "provider": "opencode_cli",
            "state": "pending",
            "generation": generation,
        },
    }

    def read_message():
        assert select.select([process.stdout], [], [], 4)[0]
        raw = process.stdout.readline(1025)
        assert raw.endswith(b"\n") and len(raw) <= 1024
        return json.loads(raw)

    def send(operation):
        process.stdin.write((json.dumps({"schema": 1, "operation": operation}) + "\n").encode())

    def owner(*args, **kwargs):
        identity = runner.http_controller.process_identity(process.pid)
        if identity is None:
            return None
        assert (Path("/proc") / str(process.pid) / "exe").resolve() == Path(node).resolve()
        return journey._process_info(identity.pid)[0]

    class Client:
        async def call(self, name, arguments):
            assert name == "cancel_local_cao_task" and arguments == {"task_id": "task"}
            assert retired == set()
            send("finish")
            process.stdin.close()
            return {
                "task_id": "task",
                "state": "cancelled",
                "result": {"worker_stopped": True, "terminal_id": terminal},
            }

    # Explicit synthetic API/SQL/ownership boundaries; actual installed files,
    # strict read_live, capture/controller, kernel pidfd and Node lifecycle.
    monkeypatch.setattr(runner, "api", lambda *args, **kwargs: metadata)
    monkeypatch.setattr(runner, "owned_native_launch_identity", owner)
    monkeypatch.setattr(runner, "admit_memory", lambda *args: None)
    monkeypatch.setattr(runner.http_activity, "_active_receipt", lambda *args: (generation, digest))
    monkeypatch.setattr(journey, "_owned_process_snapshot_for_cleanup", lambda *args: {owner()})
    directory = installed.ledger_root / terminal
    try:
        assert read_message() == {"schema": 1, "state": "ready"}
        send("first")
        stop = time.monotonic() + 4
        while time.monotonic() < stop:
            if observer.read_live(directory, nonce, digest)["active"]:
                break
            time.sleep(0.01)
        else:
            pytest.fail("installed first TEXT did not become active")

        async def run():
            async def publish():
                await asyncio.sleep(0.1)
                send("second")

            producer = asyncio.create_task(publish())
            try:
                assert (
                    await runner.cancel_witnessed_http(
                        Client(),
                        target,
                        terminal,
                        "task",
                        {"server_identity": object(), "socket_path": tmp_path / "socket"},
                        receipt,
                        retired,
                        time.monotonic() + 10,
                    )
                    is True
                )
                await producer
            finally:
                if not producer.done():
                    producer.cancel()
                    try:
                        await producer
                    except asyncio.CancelledError:
                        pass

        asyncio.run(run())
        assert process.wait(timeout=2) == 0
        assert read_message() == {
            "schema": 1,
            "state": "finished",
            "original_exact": True,
            "original_identity": True,
            "disposed": 2,
        }
        proof = json.loads((tmp_path / "native-http-activity.private.json").read_text())
        assert proof["second"]["text_bytes"] > proof["first"]["text_bytes"] > 0
        assert proof["second"]["sequence"] > proof["first"]["sequence"]
        assert proof["final"]["receipt_sha256"] == digest
        assert retired == {(target.url, terminal)}
        assert observer.read_live(directory, nonce, digest)["active"] is False
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in directory.glob("*.json"))
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=2)
        subprocess.run(
            ["git", "worktree", "remove", str(worker)], cwd=project, check=True, capture_output=True
        )


def test_tasks_http_branch_uses_expected_prompts_without_legacy_output_poll(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace

    from cli_agent_orchestrator.services.worktree_service import worktree_path_for

    messages, cancelled = {}, []
    receipt = {"activity_case": "native_http_stream", "evidence_dir": str(tmp_path)}

    class Client:
        async def call(self, name, body):
            if name == "assign_local_cao_task":
                index = int(body["operation_key"].rsplit("-", 1)[1])
                messages.setdefault(index, body["message"])
                assert messages[index] == body["message"]
                tid = "abcdef0" + str(index)
                return {"task_id": tid, "terminal_id": tid}
            assert name == "get_local_cao_task"
            return (
                {"state": "succeeded", "result": {"output": runner.EXPECTED}}
                if body["task_id"] == "abcdef00"
                else {"state": "running"}
            )

    def api(server, method, route, **kwargs):
        assert not route.endswith("/output")
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
        cancelled.append(args[2])
        return True

    monkeypatch.setattr(runner, "api", api)
    monkeypatch.setattr(runner, "admit_memory", lambda *_: None)
    monkeypatch.setattr(runner, "worker_configuration", lambda *_a, **_kw: tmp_path)
    monkeypatch.setattr(runner, "retire_terminal", lambda *_a: None)
    monkeypatch.setattr(runner, "cancel_witnessed_http", cancel)
    asyncio.run(
        runner._tasks(
            Client(),
            None,
            SimpleNamespace(url="http://owned"),
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
    assert messages == {i: runner.task_message(i, "native_http_stream") for i in range(2)}
    assert cancelled == ["abcdef01"]
    assert receipt["native_success_exact"] is True and receipt["same_key_replay_exact"] is True


def test_http_profile_adds_no_native_or_mcp_permission():
    import frontmatter

    profile = frontmatter.loads(runner.worker_profile({}, activity_case="native_http_stream"))
    assert profile.metadata["allowedTools"] == []
    assert profile.metadata["mcpServers"]["cao-mcp-server"]["enabled_tools"] == []


def test_loader_git_commands_ignore_operator_git_environment(monkeypatch, tmp_path):
    from test.live_provider.opencode_interaction_journey import _initialize_private_git_repository

    project, foreign = tmp_path / "project", tmp_path / "foreign"
    _initialize_private_git_repository(project)
    _initialize_private_git_repository(foreign)
    foreign_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=foreign)
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(foreign))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "foreign-index"))
    result = runner.prepare_http_observer(project, tmp_path / "runtime", "a" * 32)
    for name in ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"]:
        monkeypatch.delenv(name)
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=foreign) == foreign_head
    assert (
        subprocess.check_output(["git", "show", "HEAD:" + result.project_loader_path], cwd=project)
        == result.project_loader_bytes
    )
    assert not (tmp_path / "foreign-index").exists()
