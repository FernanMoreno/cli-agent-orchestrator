"""Opt-in actual OpenCode CLI concurrency, with synthetic isolated HTTP streams."""

import inspect
import json
import os
import select
import shutil
import stat
import subprocess
import time
from pathlib import Path
from test.e2e import test_real_provider_matrix as matrix
from test.fixtures import owned_tmux
from test.fixtures.cao_server import _pick_free_port, _start_cao_server
from test.live_provider.native_cancel_turn import observe_cancel_turn
from test.live_provider.opencode_cancel_gateway import MODEL, PRIMARY_INSTRUCTION, save, sha

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("CAO_T220_ROOT"), reason="requires a fresh source-pinned T220 descriptor"
)


def memory():
    value = next(
        int(s.split()[1]) // 1024
        for s in Path("/proc/meminfo").read_text().splitlines()
        if s.startswith("MemAvailable:")
    )
    assert value >= 6144, "native_memory_floor"
    return value


def wait(check, seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError("native_observation_deadline")


def test_native_cancel_a_while_b_continues(postgres_service):
    root = Path(os.environ["CAO_T220_ROOT"]).resolve(strict=True)
    manifest = json.loads((root / "manifest.json").read_text())
    repo = Path(manifest["repo"])
    assert (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        == manifest["head"]
    )
    assert all(sha(repo / name) == digest for name, digest in manifest["source_sha256"].items())
    assert sha(manifest["binary"]) == manifest["binary_sha256"]
    home = root / "case" / "home"
    home.mkdir(mode=0o700, parents=True)
    state = {
        "memory_before_api_mib": memory(),
        "scope": "native CLI/production lifecycle; controlled HTTP, no external inference",
    }
    socket_root = owned_tmux.create_private_tmux_tmpdir()
    socket_path = socket_root / f"tmux-{os.getuid()}" / "default"
    server = None
    processes = {}
    sessions = set()
    native_records = []
    api_process = None
    session = None
    state["tmux_root"] = str(socket_root)
    save(root / "case-result.json", state)

    def command(*args):
        return subprocess.check_output(
            ["tmux", "-S", str(socket_path), *args], text=True, timeout=5
        ).strip()

    def sample():
        # Session identities are proven owned by this private tmux socket.
        for line in command("list-panes", "-a", "-F", "#{pane_pid}").splitlines():
            pid = int(line)
            assert os.getsid(pid) == pid
            sessions.add(pid)
        for proc in Path("/proc").iterdir():
            if not proc.name.isdecimal():
                continue
            try:
                if proc.stat().st_uid == os.getuid() and os.getsid(int(proc.name)) in sessions:
                    identity = owned_tmux._read_process_identity(int(proc.name))
                    if identity is None:
                        # A disappearing process is absence; an unreadable live
                        # identity must never be reported as retired.
                        proc.stat()
                        raise AssertionError("owned_process_identity_unreadable")
                    processes[int(proc.name)] = identity
            except (FileNotFoundError, ProcessLookupError):
                pass

    def retired(pid, expected):
        try:
            current = Path("/proc") / str(pid)
            uid = current.stat().st_uid
            fields = (current / "stat").read_text().rsplit(")", 1)[1].split()
            assert len(fields) > 19
            return (uid, fields[19]) != expected
        except (FileNotFoundError, ProcessLookupError):
            return True

    def entries(tid):
        return sorted((root / "observations" / tid).glob("attempt-*"))

    def ledger(entry):
        path = entry / "backend.json"
        if not path.exists():
            return None
        value = json.loads(path.read_text())
        assert not value["errors"]
        assert value["loopback_only"] and value["operator_home_masked"]
        assert value["binary_sha256"] == manifest["binary_sha256"]
        assert value["tty"] == [220, 50]
        return value

    def request(method, tid, suffix="", **kwargs):
        response = matrix._request(method, server.url + "/terminals/" + tid + suffix, **kwargs)
        assert response.status_code == 200
        return response.json()

    def active(tid):
        def ready():
            rows = entries(tid)
            if len(rows) != 1:
                return False
            data = ledger(rows[0])
            turn = request("GET", tid, "/turn")
            if (
                data
                and data["primary_requests"] == 1
                and data["ticks"] > 1
                and turn["state"] == "pending"
                and request("GET", tid)["status"] == "processing"
            ):
                return turn["generation"], rows[0]
            return False

        return wait(ready)

    def fifo(tid):
        info = (home / ".aws/cli-agent-orchestrator/fifos" / (tid + ".fifo")).stat()
        assert stat.S_ISFIFO(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
        return [info.st_dev, info.st_ino]

    def output_after(tid, offset, marker):
        def found():
            value = request("GET", tid, "/output/range", params={"offset": offset, "length": 65536})
            assert value["offset"] == offset and value["terminal_id"] == tid
            return marker in value["data"] and len(value["data"].encode())

        return wait(found, 5)

    try:
        server = _start_cao_server(
            home,
            _pick_free_port(),
            postgres_service=postgres_service,
            extra_env={
                "TMUX": "",
                "TMUX_TMPDIR": str(socket_root),
                "TMPDIR": str(socket_root),
                "PATH": str(root / "bin") + ":/usr/local/bin:/usr/bin:/bin",
                "OPENCODE_API_KEY": "synthetic-local-control",
            },
            allowed_external_credentials=frozenset({"OPENCODE_API_KEY"}),
        )
        api_process = inspect.getclosurevars(server.stop).nonlocals["process"]
        source_config = home / ".aws/opencode/opencode.json"
        source_config.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        source_config.write_text(
            json.dumps(
                {
                    "tools": {"*": False},
                    "mcp": {"servers": {}},
                    "providers": {
                        "opencode-go": {
                            "env": ["OPENCODE_API_KEY"],
                            "package": "@opencode/ai/providers/openai-compatible",
                            "settings": {"baseURL": "http://127.0.0.1:43871/v1"},
                            "models": {
                                "glm-5.3-flash": {"limit": {"context": 196608, "output": 32768}}
                            },
                        }
                    },
                }
            )
        )
        for name in ("t220_a", "t220_b"):
            store = home / ".aws/cli-agent-orchestrator/agent-store"
            store.mkdir(parents=True, exist_ok=True)
            (store / (name + ".md")).write_text(
                f"---\nname: {name}\ndescription: Native concurrent cancellation control\nprovider: opencode_cli\nmodel: {MODEL}\nrole: developer\nallowedTools: []\n---\n{PRIMARY_INSTRUCTION}: Follow the user request.\n"
            )
            response = matrix._request(
                "POST",
                server.url + "/agents/profiles/install",
                json={"source": name, "provider": "opencode_cli"},
            )
            assert response.status_code == 200
        state["memory_before_b_mib"] = memory()
        b, session = matrix._create_parent(server, "opencode_cli", "t220_b")
        if manifest["case_kind"] == "preflight":
            value = request(
                "POST",
                b,
                "/input",
                params={"message": "T220 controlled native turn. Complete when released."},
            )
            assert value["success"] and value["turn_sequence"] == 1
            gen_b, entry_b = active(b)
            sample()
            native_records.append(json.loads((entry_b / "wrapper.json").read_text()))
            tick = ledger(entry_b)["ticks"]
            state["prefinish_output_bytes"] = output_after(b, 0, f"T220-progress-{tick:04d}")
            (entry_b / "release").touch(mode=0o600)

            def preflight_finished():
                turn = request("POST", b, "/turn/verify", params={"generation": gen_b})
                assert turn["generation"] == gen_b and turn["state"] in {"pending", "verified"}
                return turn["state"] == "verified" and turn

            state["b_final_turn"] = wait(preflight_finished)
            assert ledger(entry_b)["finished"] and ledger(entry_b)["primary_requests"] == 1
            matrix._delete_session(server, session)
            state["preflight_passed"] = True
            return
        assert manifest["case_kind"] == "concurrent"
        state["memory_before_a_mib"] = memory()
        a = matrix._create_native_child(
            server, session_name=session, parent_id=b, provider="opencode_cli", profile="t220_a"
        )
        for tid in (a, b):
            value = request(
                "POST",
                tid,
                "/input",
                params={"message": "T220 controlled native turn. Continue until completion."},
            )
            assert value["success"] and value["turn_sequence"] == 1
        gen_a, entry_a = active(a)
        gen_b, entry_b = active(b)
        assert gen_a != gen_b
        assert ledger(entry_a)["receipt_sha256"] != ledger(entry_b)["receipt_sha256"]
        assert ledger(entry_a)["network_namespace"] != ledger(entry_b)["network_namespace"]
        before_b = ledger(entry_b)
        fifo_b = fifo(b)
        record_b = json.loads((entry_b / "wrapper.json").read_text())
        fd_b = os.pidfd_open(record_b["pid"])
        log_b = home / ".aws/cli-agent-orchestrator/logs/terminal" / (b + ".log")
        offset = log_b.stat().st_size
        sample()
        state["memory_before_cancel_mib"] = memory()
        native_records.extend(
            json.loads((e / "wrapper.json").read_text()) for tid in (a, b) for e in entries(tid)
        )
        old_a = native_records[0]
        fd_a = os.pidfd_open(old_a["pid"])

        def before_cancel(tid, gen):
            assert tid == a and gen == gen_a
            assert not select.select([fd_a, fd_b], [], [], 0)[0]
            assert ledger(entry_a)["primary_requests"] == ledger(entry_b)["primary_requests"] == 1

        def after_cancel(tid, gen):
            assert tid == a and gen == gen_a
            assert select.select([fd_a], [], [], 5)[0], "old_a_still_alive"
            assert len(entries(a)) == 2
            replacement = next(e for e in entries(a) if e != entry_a)
            data = ledger(replacement)
            assert data and data["requests"] == data["primary_requests"] == 0
            assert request("GET", a)["status"] == "idle"
            assert not select.select([fd_b], [], [], 0)[0]
            assert entries(b) == [entry_b] and fifo(b) == fifo_b
            turn = request("POST", b, "/turn/verify", params={"generation": gen_b})
            assert turn["state"] == "pending" and turn["generation"] == gen_b
            fresh = wait(
                lambda: ledger(entry_b)["ticks"] > before_b["ticks"] + 1 and ledger(entry_b)
            )
            new_bytes = output_after(b, offset, f"T220-progress-{fresh['ticks']:04d}")
            state.update(
                b_same_generation=True,
                b_same_fifo=True,
                b_process_alive=True,
                b_fresh_output_bytes=new_bytes,
                a_replacement_requests=0,
                a_original_retired=True,
            )
            sample()
            native_records.append(json.loads((replacement / "wrapper.json").read_text()))

        try:
            state["cancellation"] = observe_cancel_turn(
                server,
                parent_id=b,
                terminal_id=a,
                session_name=session,
                generation=gen_a,
                provider="opencode_cli",
                before_cancel=before_cancel,
                after_cancel=after_cancel,
            )
        finally:
            os.close(fd_a)
            os.close(fd_b)
        (entry_b / "release").touch(mode=0o600)

        def completed():
            turn = request("POST", b, "/turn/verify", params={"generation": gen_b})
            assert turn["generation"] == gen_b
            assert turn["state"] in {"pending", "verified"}
            return turn["state"] == "verified" and turn

        state["b_final_turn"] = wait(completed)
        assert ledger(entry_b)["finished"] and ledger(entry_b)["primary_requests"] == 1
        matrix._delete_session(server, session)
        state["case_passed"] = True
    finally:
        # Keep the first failure and its raw capture even when initialization
        # failed before we could sample a native session.
        if not (state.get("case_passed") or state.get("preflight_passed")):
            for capture in (home / ".aws/cli-agent-orchestrator/logs/terminal").glob("*.log"):
                archived = root / ("failed-" + capture.name)
                shutil.copyfile(capture, archived)
                archived.chmod(0o600)
        save(root / "case-result.json", state)
        if server is not None:
            try:
                if session is not None:
                    matrix._delete_session(server, session)
            finally:
                server.stop()
        from cli_agent_orchestrator.clients import tmux as tmux_client

        state["tmux_cleanup"] = owned_tmux.cleanup_private_tmux_root(
            socket_root,
            home,
            tmux_client,
            owner_stopped=api_process is None or api_process.poll() is not None,
        )
        assert state["tmux_cleanup"]
        native_records = [
            json.loads(path.read_text())
            for path in (root / "observations").glob("*/*/wrapper.json")
        ]
        for record in native_records:
            fd = None
            if not retired(record["pid"], (record["uid"], record["birth"])):
                fd = os.pidfd_open(record["pid"])
            if fd is not None:
                try:
                    assert select.select([fd], [], [], 5)[0], "owned_wrapper_still_alive"
                finally:
                    os.close(fd)
        state["observed_processes_retired"] = bool(processes) and all(
            retired(pid, identity) for pid, identity in processes.items()
        )
        state["wrapper_processes_retired"] = bool(native_records) and all(
            retired(record["pid"], (record["uid"], record["birth"])) for record in native_records
        )
        assert state["wrapper_processes_retired"] or not native_records
        if state.get("case_passed") or state.get("preflight_passed"):
            assert state["observed_processes_retired"] and state["wrapper_processes_retired"]
        shutil.rmtree(home)
        state["home_removed"] = not home.exists()
        save(root / "case-result.json", state)
