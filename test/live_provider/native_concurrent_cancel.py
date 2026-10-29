"""Shared native cancellation orchestration; adapters own configuration and wire."""

import hashlib
import inspect
import json
import os
import re
import select
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from test.e2e import test_real_provider_matrix as matrix
from test.fixtures import owned_tmux
from test.fixtures.cao_server import _pick_free_port, _start_cao_server
from test.live_provider.native_cancel_adapters import native_profile_text
from test.live_provider.native_cancel_support import save, send_turn_once, sha
from test.live_provider.native_cancel_turn import observe_cancel_turn
from test.live_provider.native_output_witness import NativeOutputWitness


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


def run_native_cancel_case(root, postgres_service, adapter):
    root = Path(root).resolve(strict=True)
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["provider"] == adapter.provider
    repo = Path(manifest["repo"])
    assert (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        == manifest["head"]
    )
    assert all(sha(repo / name) == digest for name, digest in manifest["source_sha256"].items())
    assert sha(manifest["binary"]) == manifest["binary_sha256"]
    if adapter.scoped_discovery:
        mock_bin = repo / "test/providers/fixtures/bin"
        assert {p.name for p in mock_bin.iterdir()} == {"mock_cli"}
        assert (
            sha(mock_bin / "mock_cli")
            == manifest["source_sha256"]["test/providers/fixtures/bin/mock_cli"]
        )
    home = root / "case" / "home"
    home.mkdir(mode=0o700, parents=True)
    state = {
        "memory_before_api_mib": memory(),
        "scope": "native CLI/production lifecycle; controlled HTTP, no external inference",
    }
    socket_root = owned_tmux.create_private_tmux_tmpdir()
    socket_path = socket_root / f"tmux-{os.getuid()}" / "default"
    server = None
    read_bridge = None
    discovery_fixture = None
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

    def admission(tid, before, value):
        state.setdefault("input_admissions", []).append(
            {
                "terminal_id": tid,
                "previous_sequence": before,
                "response": value,
            }
        )
        save(root / "case-result.json", state)

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
        started = time.monotonic()
        log = home / ".aws/cli-agent-orchestrator/logs/terminal" / (tid + ".log")
        witness = NativeOutputWitness(request, tid, log, offset)
        count = wait(lambda: witness.observe(marker), 5 - (time.monotonic() - started))
        state.setdefault("output_witnesses", []).append(
            {
                "terminal_id": tid,
                "start_byte": offset,
                "end_byte": witness.offset,
                "new_bytes": count,
                "semantic_marker": marker,
                "production_compositor": True,
                "marker_absent_before_boundary": True,
            }
        )
        save(root / "case-result.json", state)
        return count

    try:
        server = _start_cao_server(
            home,
            _pick_free_port(),
            postgres_service=postgres_service,
            extra_env={
                "TMUX": "",
                "TMUX_TMPDIR": str(socket_root),
                "TMPDIR": str(socket_root),
                "PATH": str(root / "bin")
                + ":"
                + (
                    str(repo / "test/providers/fixtures/bin") + ":"
                    if adapter.scoped_discovery
                    else ""
                )
                + "/usr/local/bin:/usr/bin:/bin",
                **adapter.environment,
                **(
                    {"CAO_NATIVE_READ_SOCKET": str(socket_root / "read.sock")}
                    if adapter.native_operation
                    else {}
                ),
            },
            allowed_external_credentials=adapter.credential_names,
        )
        api_process = inspect.getclosurevars(server.stop).nonlocals["process"]
        if adapter.native_operation:
            from test.live_provider.native_read_bridge import OwnedReadBridge

            read_bridge = OwnedReadBridge(socket_root / "read.sock", server.url)
            read_bridge.__enter__()
        adapter.configure(home)
        for name in (adapter.profile_prefix + "_a", adapter.profile_prefix + "_b"):
            store = home / ".aws/cli-agent-orchestrator/agent-store"
            store.mkdir(parents=True, exist_ok=True)
            (store / (name + ".md")).write_text(native_profile_text(name, adapter))
            response = matrix._request(
                "POST",
                server.url + "/agents/profiles/install",
                json={"source": name, "provider": adapter.provider},
            )
            assert response.status_code == 200
        state["memory_before_b_mib"] = memory()
        b, session = matrix._create_parent(server, adapter.provider, adapter.profile_prefix + "_b")
        sample()
        if read_bridge is not None:
            read_bridge.bind_caller(b)
            group = ["t229" if adapter.scoped_discovery else "t228", b]
            updated = request("PATCH", b, "/group", json={"group": group})
            assert updated["group"] == group and updated["allowed_tools"] == list(
                adapter.allowed_tools
            )
            state["persisted_discovery_group"] = True
            state["native_operation_mode"] = adapter.native_operation
        if adapter.expected_mcp_tools:
            from cli_agent_orchestrator.utils.mcp_readiness import (
                READY_FILE_ENV,
                READY_TOKEN_ENV,
                has_ready_signal,
            )

            config = json.loads(
                (home / ".aws/cli-agent-orchestrator/tmp" / (b + ".mcp.json")).read_text()
            )
            env = config["mcpServers"]["cao-mcp-server"]["env"]
            assert has_ready_signal(Path(env[READY_FILE_ENV]), env[READY_TOKEN_ENV])
            state["production_mcp_client_discovery_ready"] = True
            state["expected_mcp_tool_names"] = list(adapter.expected_mcp_tools)
        if adapter.scoped_discovery:
            from test.live_provider.native_discovery_fixture import OwnedDiscoveryFixture

            discovery_fixture = OwnedDiscoveryFixture(server, home, session, b, group, sample)
            state["scoped_discovery_fixture"] = discovery_fixture.setup()
            save(root / "case-result.json", state)
        if manifest["case_kind"] == "inspect":
            started_entries = entries(b)
            assert (
                len(started_entries) == 1 and (started_entries[0] / "session-start.json").is_file()
            ), "native_session_start_hook_not_observed_before_input"

        if manifest["case_kind"] in {"preflight", "inspect"}:
            send_turn_once(
                request, b, adapter.prompt_prefix + ". Complete when released.", admission
            )
            gen_b, entry_b = active(b)
            sample()
            native_records.append(json.loads((entry_b / "wrapper.json").read_text()))
            if manifest["case_kind"] == "inspect":
                wait(lambda: ledger(entry_b)["ticks"] >= 57)
            tick = ledger(entry_b)["ticks"]
            state["prefinish_output_bytes"] = output_after(
                b, 0, f"{adapter.progress_prefix}{tick:04d}"
            )
            (entry_b / "release").touch(mode=0o600)

            if manifest["case_kind"] == "inspect":
                wait(
                    lambda: (entry_b / "stop-failure.json").exists()
                    or (ledger(entry_b).get("finished") and (entry_b / "stop.json").exists())
                )
                assert not (entry_b / "stop-failure.json").exists(), "native_stop_failure_observed"
                start = json.loads((entry_b / "session-start.json").read_text())
                stop = json.loads((entry_b / "stop.json").read_text())
                assert start["schema"] == stop["schema"] == "caos.claude-hook-observation.v1"
                start, stop = start["payload"], stop["payload"]
                assert (
                    start["hook_event_name"] == "SessionStart" and stop["hook_event_name"] == "Stop"
                )
                assert start["session_id"] == stop["session_id"]
                assert stop["stop_hook_active"] is False
                markers = re.findall(
                    r"(?m)^[ \t]*(CAO-TURN-RECEIPT-[a-f0-9]{32})[ \t]*$",
                    stop["last_assistant_message"],
                )
                assert (
                    len(markers) == 1
                    and hashlib.sha256(markers[0].encode()).hexdigest()
                    == ledger(entry_b)["receipt_sha256"]
                )
                native_turn = request("POST", b, "/turn/verify", params={"generation": gen_b})
                response = matrix._request(
                    "GET",
                    server.url + "/terminals/" + b + "/output",
                    params={"mode": "last", "generation": gen_b},
                )
                meta = request("GET", b)
                target = meta["session_name"] + ":" + meta["name"]
                for label, depth in (("viewport", "0"), ("history200", "-200"), ("history", "-")):
                    capture = root / ("native-final-" + label + ".txt")
                    capture.write_text(command("capture-pane", "-p", "-t", target, "-S", depth))
                    capture.chmod(0o600)
                state["inspection"] = {
                    "scope": "native-hook schema observation only; not cancellation acceptance",
                    "same_native_session": True,
                    "own_final_receipt_in_structured_assistant_text": True,
                    "native_hook_fields": sorted(stop),
                    "assistant_text_bytes": len(stop["last_assistant_message"].encode()),
                    "backend_ticks": ledger(entry_b)["ticks"],
                    "api_turn": native_turn,
                    "api_output_status": response.status_code,
                    "api_output_body": response.json(),
                }
                matrix._delete_session(server, session)
                state["inspection_completed"] = True
                return

            def preflight_finished():
                turn = request("POST", b, "/turn/verify", params={"generation": gen_b})
                assert turn["generation"] == gen_b and turn["state"] in {"pending", "verified"}
                return turn["state"] == "verified" and turn

            state["b_final_turn"] = wait(preflight_finished)
            assert ledger(entry_b)["finished"] and ledger(entry_b)["primary_requests"] == 1
            if discovery_fixture is not None:
                discovery_fixture.assert_live()
                data = ledger(entry_b)
                assert data["discovery_expectation_sha256"] == discovery_fixture.digest
                assert data["tool_result"]["success"] is True
                from test.live_provider.native_read_bridge import validate_discovery_rows

                validate_discovery_rows(
                    data["tool_result"]["siblings"], discovery_fixture.expectation
                )
                assert len(read_bridge.response_bodies) == len(read_bridge.rows) == 2
                assert read_bridge.rows[-1]["path"] == f"/terminals/{b}/siblings"
                assert read_bridge.rows[-1]["status"] == 200
                api_body = read_bridge.response_bodies[-1]
                assert hashlib.sha256(api_body).hexdigest() == read_bridge.rows[-1]["body_sha256"]
                api_rows = json.loads(api_body)
                validate_discovery_rows(api_rows, discovery_fixture.expectation)
                assert api_rows == data["tool_result"]["siblings"]
                state["scoped_api_native_rows_equal"] = True
                state["scoped_discovery_verified"] = True
                discovery_fixture.cleanup()
                state["scoped_fixture_sessions_retired"] = True
            matrix._delete_session(server, session)
            if read_bridge is not None:
                data = ledger(entry_b)
                assert data["tool_requested"] is True and data["tool_result_requests"] == 1
                assert data["operator_client_allow"] == ["mcp__cao-mcp-server__list_siblings"]
                expected_paths = [f"/terminals/{b}"] + (
                    [f"/terminals/{b}/siblings"] if adapter.native_operation == "authorized" else []
                )
                assert [row["path"] for row in read_bridge.rows] == expected_paths
                assert all(row["status"] == 200 for row in read_bridge.rows)
                assert read_bridge.attempts and all(
                    row["admitted"] is True for row in read_bridge.attempts
                )
                state["actual_operation_verified"] = True
                state["read_bridge_requests"] = read_bridge.rows
            state["preflight_passed"] = True
            return
        assert manifest["case_kind"] == "concurrent"
        state["memory_before_a_mib"] = memory()
        a = matrix._create_native_child(
            server,
            session_name=session,
            parent_id=b,
            provider=adapter.provider,
            profile=adapter.profile_prefix + "_a",
        )
        for tid in (a, b):
            send_turn_once(
                request, tid, adapter.prompt_prefix + ". Continue until completion.", admission
            )
        gen_a, entry_a = active(a)
        gen_b, entry_b = active(b)
        assert gen_a != gen_b
        assert ledger(entry_a)["receipt_sha256"] != ledger(entry_b)["receipt_sha256"]
        assert ledger(entry_a)["network_namespace"] != ledger(entry_b)["network_namespace"]
        fifo_b = fifo(b)
        record_b = json.loads((entry_b / "wrapper.json").read_text())
        fd_b = os.pidfd_open(record_b["pid"])
        log_b = home / ".aws/cli-agent-orchestrator/logs/terminal" / (b + ".log")
        offset = log_b.stat().st_size
        before_b = ledger(entry_b)
        state["b_output_boundary"] = {"offset": offset, "ticks": before_b["ticks"]}
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
            new_bytes = output_after(b, offset, f"{adapter.progress_prefix}{fresh['ticks']:04d}")
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
                provider=adapter.provider,
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
        original_error = sys.exc_info()[1]
        if original_error is not None:
            state["first_failure_type"] = type(original_error).__name__
        # Keep the first failure and its raw capture even when initialization
        # failed before we could sample a native session.
        if manifest["case_kind"] == "inspect" or not (
            state.get("case_passed") or state.get("preflight_passed")
        ):
            for capture in (home / ".aws/cli-agent-orchestrator/logs/terminal").glob("*.log"):
                archived = root / (
                    ("observed-" if manifest["case_kind"] == "inspect" else "failed-")
                    + capture.name
                )
                shutil.copyfile(capture, archived)
                archived.chmod(0o600)
        save(root / "case-result.json", state)
        bridge_cleanup_error = None
        if read_bridge is not None:
            try:
                read_bridge.__exit__(None, None, None)
                state["read_bridge_retired"] = True
            except BaseException as error:
                bridge_cleanup_error = error
                state["read_bridge_retired"] = False
            if original_error is not None or bridge_cleanup_error is not None:
                save(
                    root / "failed-read-bridge.json",
                    {
                        "case_status": "failed",
                        "requests": read_bridge.rows,
                        "attempts": read_bridge.attempts,
                    },
                )
                state["partial_read_bridge_evidence"] = "failed-read-bridge.json"
            else:
                state["read_bridge_requests"] = [dict(row) for row in read_bridge.rows]
                state["read_bridge_attempts"] = [dict(row) for row in read_bridge.attempts]
        fixture_cleanup_error = None
        if discovery_fixture is not None:
            try:
                discovery_fixture.cleanup()
                state["scoped_fixture_sessions_retired"] = True
            except BaseException as error:
                fixture_cleanup_error = error
                state["scoped_fixture_sessions_retired"] = False
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
        if (
            state.get("case_passed")
            or state.get("preflight_passed")
            or state.get("inspection_completed")
        ):
            assert state["observed_processes_retired"] and state["wrapper_processes_retired"]
        shutil.rmtree(home)
        state["home_removed"] = not home.exists()
        save(root / "case-result.json", state)
        if bridge_cleanup_error is not None and original_error is None:
            raise bridge_cleanup_error
        if fixture_cleanup_error is not None and original_error is None:
            raise fixture_cleanup_error
