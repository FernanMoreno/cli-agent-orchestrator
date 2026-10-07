"""Credential-free acceptance with two real CAO servers and real mock_cli workers."""

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import requests

pytestmark = pytest.mark.integration

WRAPPER = r"""
import asyncio
import os
from pathlib import Path
from cli_agent_orchestrator import constants
constants.LOCAL_PEER_DIR = Path(os.environ["TEST_PEER_REGISTRY"])
constants.LOCAL_PEER_REGISTRY_FILE = constants.LOCAL_PEER_DIR / "registry.sqlite3"
from cli_agent_orchestrator.api.main import app
from cli_agent_orchestrator.services import local_peer_service, local_peer_auth
from cli_agent_orchestrator.clients import database
@app.post("/test/pair")
async def pair(body: dict):
    return await asyncio.to_thread(local_peer_service.initiate_pairing, body["peer"], body["project"])
@app.post("/test/accept")
async def accept(body: dict):
    return await asyncio.to_thread(local_peer_service.accept_pairing, body["challenge_id"], body["code"])
@app.post("/test/requester")
async def requester(body: dict):
    database.create_terminal("deadbeef", "cao-test-requester", "0", "mock_cli", working_directory=body["project"])
    return {"ok": True}
@app.post("/test/revoke")
async def revoke(body: dict):
    return await asyncio.to_thread(local_peer_service.revoke_peer, body["peer"], body["project_id"])
app.router.routes[:] = app.router.routes[-4:] + app.router.routes[:-4]
import uvicorn
uvicorn.run(app, host="127.0.0.1", port=constants.SERVER_PORT, log_level="warning")
"""


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait(predicate, seconds=90):
    end = time.monotonic() + seconds
    last = None
    while time.monotonic() < end:
        try:
            last = predicate()
            if last:
                return last
        except requests.RequestException:
            pass
        time.sleep(0.2)
    raise AssertionError(f"local acceptance deadline expired; last={last!r}")


def test_two_real_local_profiles_terminal_receipts_and_authorization(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    shared_file = project / "shared.txt"
    shared_file.write_text("local project acceptance\n")
    project_hash_before = hashlib.sha256(shared_file.read_bytes()).hexdigest()
    socket_dir = Path(tempfile.mkdtemp(prefix="cao15-tmux-"))
    registry = tmp_path / "registry"
    processes = []
    logs = []
    urls = []
    env = os.environ.copy()
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    for key in ("AUTH0_DOMAIN", "AUTH0_AUDIENCE", "CAO_AUTH_JWKS_URI"):
        env.pop(key, None)
    env.update(
        CAO_A2A_DISABLED="true",
        CAO_WORK_LAUNCH_MODE="legacy",
        CAO_WORKFLOW_CONTINUATION_DRIVER_DISABLED="true",
        CAO_LOCAL_LISTENER_DISABLED="true",
        OTEL_SDK_DISABLED="true",
        TMUX_TMPDIR=str(socket_dir),
        TEST_PEER_REGISTRY=str(registry),
        PYTHONUNBUFFERED="1",
    )
    try:
        for index in range(2):
            home = tmp_path / f"profile-{index}"
            store = home / "agent-store"
            store.mkdir(parents=True)
            (store / "local-mock.md").write_text(
                "---\nname: local-mock\ndescription: local acceptance\nprovider: mock_cli\n---\nEcho the requested task.\n"
            )
            child_env = dict(
                env,
                CAO_HOME_DIR=str(home),
                CAO_API_PORT=str(_port()),
                CAO_API_HOST="127.0.0.1",
                CAO_INSTANCE_NAME=f"local-acceptance-{index}",
            )
            log = (tmp_path / f"server-{index}.log").open("w")
            logs.append(log)
            process = subprocess.Popen(
                [sys.executable, "-c", WRAPPER], env=child_env, stdout=log, stderr=subprocess.STDOUT
            )
            processes.append(process)
            urls.append(f'http://127.0.0.1:{child_env["CAO_API_PORT"]}')
        for url, process in zip(urls, processes):

            def ready():
                assert process.poll() is None, f"server stopped; inspect {tmp_path}"
                response = requests.get(url + "/health", timeout=2)
                return response.status_code == 200

            _wait(ready, 180)
        identities = [
            requests.get(url + "/local-coordination/identity", timeout=5).json() for url in urls
        ]
        assert identities[0]["instance_id"] != identities[1]["instance_id"]
        challenge = requests.post(
            urls[0] + "/test/pair",
            json={"peer": identities[1]["instance_id"], "project": str(project)},
            timeout=30,
        )
        assert challenge.status_code == 200, challenge.text
        challenge = challenge.json()
        response = requests.post(
            urls[1] + "/test/accept",
            json={"challenge_id": challenge["challenge_id"], "code": challenge["code"]},
            timeout=30,
        )
        assert response.status_code == 200, response.text
        assert (
            requests.post(
                urls[0] + "/test/requester", json={"project": str(project)}, timeout=5
            ).status_code
            == 200
        )
        task_url = urls[0] + "/local-coordination/agent/tasks"
        sessions_url = (
            urls[0] + f'/local-coordination/agent/peers/{identities[1]["instance_id"]}/sessions'
        )
        session_params = {"requester_terminal_id": "deadbeef", "project_path": str(project)}
        empty = requests.get(sessions_url, params=session_params, timeout=15)
        assert empty.status_code == 200 and empty.json()["state"] == "empty", empty.text
        receipts = []
        for index, (message, expected) in enumerate(
            [("hello", "succeeded"), ("__mock_error__", "failed"), ("__mock_sleep_30", "cancelled")]
        ):
            body = {
                "requester_terminal_id": "deadbeef",
                "peer_instance_id": identities[1]["instance_id"],
                "project_path": str(project),
                "operation_key": f"acceptance-{index}",
                "agent_profile": "local-mock",
                "message": message,
            }
            response = requests.post(task_url, json=body, timeout=45)
            assert response.status_code == 202, response.text
            receipt = response.json()
            task_id = receipt["task_id"]
            replay = requests.post(task_url, json=body, timeout=45)
            assert replay.status_code == 202, replay.text
            assert replay.json()["task_id"] == task_id
            assert replay.json()["terminal_id"] == receipt["terminal_id"]
            if expected == "cancelled":

                def writing():
                    response = requests.get(
                        urls[1] + f'/terminals/{receipt["terminal_id"]}/output',
                        params={"mode": "full"},
                        timeout=10,
                    )
                    return response.status_code == 200 and "__mock_sleep_30" in response.json().get(
                        "output", ""
                    )

                _wait(writing, 30)
                blocked = requests.post(
                    task_url, json={**body, "operation_key": "acceptance-contended"}, timeout=45
                )
                assert (
                    blocked.status_code == 202 and blocked.json().get("error") == "project_busy"
                ), blocked.text
                with sqlite3.connect(registry / "registry.sqlite3") as connection:
                    lease = connection.execute(
                        "SELECT task_id,state FROM local_project_write_leases WHERE project_id=?",
                        (challenge["project_id"],),
                    ).fetchone()
                assert lease[0] == task_id and lease[1] != "released"
                response = requests.post(
                    task_url + f"/{task_id}/cancel",
                    json={"requester_terminal_id": "deadbeef"},
                    timeout=30,
                )
                assert response.status_code == 200, response.text

            def finished():
                response = requests.get(
                    task_url + f"/{task_id}",
                    params={"requester_terminal_id": "deadbeef"},
                    timeout=10,
                )
                assert response.status_code == 200, response.text
                receipt = response.json()
                return receipt if receipt["state"] in {"succeeded", "failed", "cancelled"} else None

            receipt = _wait(finished, 90)
            assert receipt["state"] == receipt["result"]["state"] == expected, receipt
            assert finished() == receipt
            receipts.append(receipt)
            snapshot = requests.get(sessions_url, params=session_params, timeout=15)
            assert (
                snapshot.status_code == 200 and snapshot.json()["state"] == "available"
            ), snapshot.text
        with sqlite3.connect(registry / "registry.sqlite3") as connection:
            assert (
                connection.execute(
                    "SELECT state FROM local_project_write_leases WHERE project_id=?",
                    (challenge["project_id"],),
                ).fetchone()[0]
                == "released"
            )
        response = requests.post(
            urls[0] + "/test/revoke",
            json={"peer": identities[1]["instance_id"], "project_id": challenge["project_id"]},
            timeout=30,
        )
        assert response.status_code == 200 and response.json()["revoked"]
        denied = requests.post(
            task_url, json={**body, "operation_key": "acceptance-denied"}, timeout=15
        )
        assert denied.status_code == 403, denied.text
        # A second explicit pairing restores the grant before exercising outage.
        second = requests.post(
            urls[0] + "/test/pair",
            json={"peer": identities[1]["instance_id"], "project": str(project)},
            timeout=30,
        )
        assert second.status_code == 200, second.text
        second = second.json()
        accepted = requests.post(
            urls[1] + "/test/accept",
            json={"challenge_id": second["challenge_id"], "code": second["code"]},
            timeout=30,
        )
        assert accepted.status_code == 200, accepted.text
        processes[1].terminate()
        processes[1].wait(timeout=30)
        unavailable = requests.get(sessions_url, params=session_params, timeout=15)
        assert (
            unavailable.status_code == 200 and unavailable.json()["state"] == "unavailable"
        ), unavailable.text
        offline_task = requests.post(
            task_url, json={**body, "operation_key": "acceptance-offline"}, timeout=15
        )
        assert (
            offline_task.status_code == 503
            and offline_task.json()["detail"]["kind"] == "peer_unavailable"
        ), offline_task.text
        with sqlite3.connect(tmp_path / "profile-0/db/cli-agent-orchestrator.db") as connection:
            offline_task_id = connection.execute(
                "SELECT task_id FROM local_peer_tasks WHERE operation_key='acceptance-offline'"
            ).fetchone()[0]
        offline_status = requests.get(
            task_url + f"/{offline_task_id}",
            params={"requester_terminal_id": "deadbeef"},
            timeout=15,
        )
        assert (
            offline_status.status_code == 503
            and offline_status.json()["detail"]["kind"] == "peer_unavailable"
        ), offline_status.text
        # A cached terminal result remains authoritative even after peer exit.
        final = requests.get(
            task_url + f'/{receipts[-1]["task_id"]}',
            params={"requester_terminal_id": "deadbeef"},
            timeout=10,
        )
        assert final.status_code == 200 and final.json() == receipts[-1]
        (tmp_path / "acceptance-receipts.json").write_text(
            json.dumps(
                {
                    "identities": identities,
                    "receipts": receipts,
                    "session_states": [
                        empty.json()["state"],
                        snapshot.json()["state"],
                        unavailable.json()["state"],
                    ],
                    "cancellation_lease_states": [lease[1], "released"],
                    "revoked_submission_http_status": denied.status_code,
                    "offline_task_http_status": offline_status.status_code,
                    "project_file_sha256_before": project_hash_before,
                    "project_file_sha256_after": hashlib.sha256(
                        shared_file.read_bytes()
                    ).hexdigest(),
                },
                indent=2,
            )
        )
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        subprocess.run(["tmux", "kill-server"], env=env, capture_output=True, timeout=10)
        shutil.rmtree(socket_dir)
        for log in logs:
            log.close()
