"""Opt-in real-process capacity ramp; no model CLI or inference is launched."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from test.fixtures import mock_capacity as capacity
from test.fixtures import owned_tmux
from test.fixtures.cao_server import _pick_free_port, _start_cao_server

import pytest
import requests

pytestmark = pytest.mark.postgres
REPO = Path(__file__).resolve().parents[2]
SLEEP_SECONDS = 10


def request(server, method, path, *, timeout=10.0, **kwargs):
    response = requests.request(method, server.url + path, timeout=timeout, **kwargs)
    assert response.status_code in (200, 201, 204), (path, response.status_code)
    return response.json() if response.content else None


def wait_ready(server, terminal):
    start = time.monotonic()
    while time.monotonic() - start < 25:
        state = request(server, "GET", f"/terminals/{terminal}", timeout=3)["status"]
        assert state != "error", "terminal initialization failed"
        if state in {"idle", "completed"}:
            return
        time.sleep(0.1)
    raise AssertionError("terminal readiness exceeded original 25-second deadline")


def wait_echo(server, terminal, expected):
    start = time.monotonic()
    while time.monotonic() - start < 25:
        result = request(
            server, "GET", f"/terminals/{terminal}/output", params={"mode": "last"}, timeout=3
        )
        if result["output"].strip() == expected:
            return time.monotonic()
        time.sleep(0.1)
    raise AssertionError("mock echo exceeded original 25-second deadline")


def tmux_panes(socket, names):
    result = subprocess.run(
        ["tmux", "-S", str(socket), "list-panes", "-a", "-F", "#{session_name}\t#{pane_pid}"],
        capture_output=True,
        text=True,
        timeout=3,
        check=True,
    )
    rows = [line.split("\t") for line in result.stdout.splitlines()]
    selected_rows = [(name, int(pid)) for name, pid in rows if name in names]
    assert len(selected_rows) == len(names), "expected exactly one pane per owned session"
    selected = dict(selected_rows)
    assert len(selected) == len(names) and len(set(selected.values())) == len(names)
    return selected


def retire_processes(observed):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not any(capacity.same_live(p) for p in observed.values()):
            return
        time.sleep(0.05)
    raise AssertionError("owned processes remain after session deletion")


def run_rung(workers, home, postgres_service, record):
    home.mkdir(mode=0o700)
    socket_root = owned_tmux.create_private_tmux_tmpdir()
    socket = socket_root / f"tmux-{os.getuid()}" / "default"
    server = sampler = None
    sessions = []
    owned = {}
    api = None
    server_stopped = False
    record.update(
        workers=workers,
        agent_process_target=workers + 1,
        created=0,
        ready=0,
        peak_processing=0,
        delivered=0,
        echoed=0,
        retired=0,
        cleanup_confirmed=False,
        passed=False,
    )
    try:
        server = _start_cao_server(
            home,
            _pick_free_port(),
            postgres_service=postgres_service,
            extra_env={
                "CAO_WORK_LAUNCH_MODE": "legacy",
                "TMUX": "",
                "TMUX_TMPDIR": str(socket_root),
                "TMPDIR": str(socket_root),
                "PATH": str(REPO / "test/providers/fixtures/bin") + os.pathsep + os.environ["PATH"],
            },
        )
        api = capacity.owned_api(home, server.port)
        sampler = capacity.Sampler(api).start()
        agents = []
        startup_seconds = []
        for i in range(workers + 1):
            start = time.monotonic()
            name = f"capacity-{workers}-{i}-{uuid.uuid4().hex[:8]}"
            row = request(
                server,
                "POST",
                "/sessions",
                params={"provider": "mock_cli", "agent_profile": "developer", "session_name": name},
                timeout=30,
            )
            sessions.append(row["session_name"])
            agents.append(row["id"])
            record["created"] += 1
            wait_ready(server, row["id"])
            record["ready"] += 1
            startup_seconds.append(time.monotonic() - start)
            pid = tmux_panes(socket, [row["session_name"]])[row["session_name"]]
            pane = capacity.process(pid)
            assert pane is not None
            sampler.roots.append(pane)
            for p in capacity.tree(pane).values():
                owned[p.identity] = p
        record["startup_seconds"] = startup_seconds
        panes = tmux_panes(socket, sessions)
        mocks = {
            agent: capacity.mock_process(
                capacity.process(panes[name]), REPO / "test/providers/fixtures/bin/mock_cli"
            )
            for agent, name in zip(agents, sessions)
        }
        assert len({p.identity for p in mocks.values()}) == workers + 1
        record["agent_processes"] = {
            key: capacity.evidence_process(value) for key, value in mocks.items()
        }
        sender, *receivers = agents
        submitted = {}

        def post(terminal, message):
            submitted[terminal] = time.monotonic()
            return request(
                server,
                "POST",
                f"/terminals/{terminal}/inbox/messages",
                params={"sender_id": sender, "message": message},
            )

        sleep_message = f"__mock_sleep_{SLEEP_SECONDS}"
        with ThreadPoolExecutor(max_workers=workers) as pool:
            posts = {terminal: pool.submit(post, terminal, sleep_message) for terminal in receivers}
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                samples = {}
                for terminal in receivers:
                    child = capacity.sleeping_child(mocks[terminal], SLEEP_SECONDS)
                    if child is not None:
                        owned[child.identity] = child
                        samples[terminal] = child
                observed = capacity.confirmed_overlap(samples)
                record["peak_processing"] = max(record["peak_processing"], observed)
                if observed == workers:
                    record["overlap_monotonic"] = time.monotonic()
                    record["overlap_processes"] = {
                        key: capacity.evidence_process(value) for key, value in samples.items()
                    }
                    break
                if all(f.done() for f in posts.values()):
                    for f in posts.values():
                        f.result()
                time.sleep(0.05)
            assert (
                record["peak_processing"] == workers
            ), "requested real processing overlap not observed"
            messages = {
                terminal: future.result()["message_id"] for terminal, future in posts.items()
            }
            echoes = {
                terminal: pool.submit(wait_echo, server, terminal, f"slept {SLEEP_SECONDS}s")
                for terminal in receivers
            }
            record["load_submit_to_echo_seconds"] = [
                future.result() - submitted[terminal] for terminal, future in echoes.items()
            ]
            payloads = {
                terminal: f"capacity-{workers}-{terminal}-{uuid.uuid4().hex}"
                for terminal in receivers
            }
            followups = {
                terminal: pool.submit(post, terminal, payloads[terminal]) for terminal in receivers
            }
            followup_ids = {
                terminal: future.result()["message_id"] for terminal, future in followups.items()
            }
            echoes = {
                terminal: pool.submit(wait_echo, server, terminal, payloads[terminal])
                for terminal in receivers
            }
            record["message_submit_to_echo_seconds"] = [
                future.result() - submitted[terminal] for terminal, future in echoes.items()
            ]
        for terminal in receivers:
            rows = request(server, "GET", f"/terminals/{terminal}/inbox/messages")
            assert len(rows) == 2
            assert {row["id"] for row in rows} == {messages[terminal], followup_ids[terminal]}
            assert {row["message"] for row in rows} == {sleep_message, payloads[terminal]}
            assert all(row["sender_id"] == sender and row["status"] == "delivered" for row in rows)
            record["delivered"] += len(rows)
            # Rendered pane history avoids FIFO-reader lag while retaining exact echo counts.
            full = subprocess.run(
                [
                    "tmux",
                    "-S",
                    str(socket),
                    "capture-pane",
                    "-p",
                    "-S",
                    "-",
                    "-t",
                    panes_target(sessions[agents.index(terminal)]),
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
            ).stdout
            assert full.count(f"> MOCK: slept {SLEEP_SECONDS}s") == 1
            assert full.count("> MOCK: " + payloads[terminal]) == 1
            record["echoed"] += 2
        record["workload_correct"] = True
    finally:
        failures = []
        if sampler is not None:
            try:
                record["memory"] = sampler.stop()
            except BaseException as error:
                failures.append(type(error).__name__)
        for name in reversed(sessions):
            try:
                request(server, "DELETE", f"/sessions/{name}", timeout=5)
                record["retired"] += 1
            except BaseException as error:
                failures.append(type(error).__name__)
        try:
            retire_processes(owned)
        except BaseException as error:
            failures.append(type(error).__name__)
        if server is not None:
            try:
                server.stop()
                server_stopped = True
                assert api is None or not capacity.same_live(api)
            except BaseException as error:
                failures.append(type(error).__name__)
        from cli_agent_orchestrator.clients import tmux

        diagnostics = []
        removed = owned_tmux.cleanup_private_tmux_root(
            socket_root, home, tmux, owner_stopped=server_stopped, diagnostics=diagnostics
        )
        record["cleanup"] = {
            "server_stopped": server_stopped,
            "tmux_root_removed": removed,
            "rejections": diagnostics,
            "failures": failures,
        }
        record["cleanup_confirmed"] = (
            removed and server_stopped and not failures and record["retired"] == len(sessions)
        )
        record["post_cleanup_available_mib"] = capacity.available_mib()
        record["passed"] = record.get("workload_correct", False) and record["cleanup_confirmed"]
        assert record["cleanup_confirmed"], record["cleanup"]


def panes_target(session):
    return session + ":"


@pytest.mark.skipif(
    os.environ.get("CAO_RUN_MOCK_CAPACITY") != "1",
    reason="explicit real-process capacity opt-in required",
)
def test_real_mock_cli_capacity_ramp(tmp_path, postgres_service):
    maximum = int(os.environ.get("CAO_MOCK_CAPACITY_MAX_WORKERS", "20"))
    assert maximum in (20, 32)
    records = []
    previous = None
    for workers in (2, 4, 8, 12, 20, 32):
        if workers > maximum:
            break
        capacity.admit_rung(workers, previous, capacity.available_mib())
        # A ready file alone does not prove identity; inspect exact service on every rung.
        from test.postgres.fixtures import _container_metadata

        assert (
            _container_metadata(postgres_service.metadata["container_id"])["container_id"]
            == postgres_service.metadata["container_id"]
        )
        record = {"available_before_mib": capacity.available_mib()}
        records.append(record)
        start = time.monotonic()
        try:
            run_rung(workers, tmp_path / f"rung-{workers}", postgres_service, record)
        finally:
            record["total_seconds"] = time.monotonic() - start
            descriptor = os.open(
                tmp_path / f"capacity-{workers}.private.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(descriptor, "w") as stream:
                json.dump(record, stream, indent=2)
        previous = record
    assert len(records) == (6 if maximum == 32 else 5)
    assert all(r["passed"] for r in records)
