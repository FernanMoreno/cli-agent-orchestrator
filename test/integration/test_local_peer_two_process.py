"""Credential-free acceptance with two real CAO servers and real mock_cli workers."""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import requests

pytestmark = [pytest.mark.integration, pytest.mark.postgres]

WRAPPER = r"""
import asyncio
import os
from pathlib import Path
from cli_agent_orchestrator import constants
constants.LOCAL_PEER_DIR = Path(os.environ["TEST_PEER_REGISTRY"])
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


def _runtime_target_config(target, *, secret_file: str | None = None) -> dict[str, object]:
    return {
        "host": target.host,
        "port": target.port,
        "database": target.database,
        "user": target.user,
        "schema": target.schema,
        "store_id": target.store_id,
        "secret_file": secret_file or target.secret_file.name,
        "sslmode": target.sslmode,
    }


@pytest.fixture
def two_local_peer_profiles(tmp_path, postgres_service):
    """Two profile stores with one deliberately shared, user-wide peer store."""
    from contextlib import ExitStack
    from test.postgres.fixtures import isolated_live_targets

    stack = ExitStack()
    try:
        homes = [tmp_path / "profile-0", tmp_path / "profile-1"]
        profiles = [
            stack.enter_context(isolated_live_targets(home, service=postgres_service))
            for home in homes
        ]

        # Local pairing challenges and grants belong to each profile's main
        # store. Only the peer registry is shared across profiles on this OS
        # user, matching the installed database-config contract.
        shared_peer_secret = homes[1] / "peer-shared-runtime.json"
        shutil.copyfile(profiles[0].peer.secret_file, shared_peer_secret)
        os.chmod(shared_peer_secret, 0o600)
        second_config = {
            "version": 1,
            "targets": {
                "main": _runtime_target_config(profiles[1].main),
                "auth": _runtime_target_config(profiles[1].auth),
                "peer": _runtime_target_config(
                    profiles[0].peer, secret_file=shared_peer_secret.name
                ),
            },
        }
        profiles[1].config_file.write_text(json.dumps(second_config, indent=2))
        os.chmod(profiles[1].config_file, 0o600)
        yield profiles
    finally:
        stack.close()


def test_two_real_local_profiles_terminal_receipts_and_authorization(
    tmp_path, two_local_peer_profiles
):
    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.services.local_peers.host_identity import current_host_identity

    # The child servers use separate profile stores and the same user-wide
    # peer registry. They still own separate profile homes, tmux targets and
    # agent processes.
    profile_targets = two_local_peer_profiles
    peer_target = profile_targets[0].peer
    initiator_target = profile_targets[0].main
    project = tmp_path / "project"
    project.mkdir()
    shared_file = project / "shared.txt"
    shared_file.write_text("local project acceptance\n")
    project_hash_before = hashlib.sha256(shared_file.read_bytes()).hexdigest()
    socket_dir = Path(tempfile.mkdtemp(prefix="cao15-tmux-"))
    registry = tmp_path / "registry"
    host = current_host_identity(registry)
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
            home = profile_targets[index].home
            store = home / "agent-store"
            store.mkdir(parents=True)
            (store / "local-mock.md").write_text(
                "---\nname: local-mock\ndescription: local acceptance\nprovider: mock_cli\n---\nEcho the requested task.\n"
            )
            child_env = dict(
                env,
                CAO_HOME_DIR=str(home),
                CAO_DATABASE_CONFIG_FILE=str(profile_targets[index].config_file),
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
                contended_id = blocked.json()["task_id"]
                missing_status = requests.get(
                    task_url + f"/{contended_id}",
                    params={"requester_terminal_id": "deadbeef"},
                    timeout=15,
                )
                missing_cancel = requests.post(
                    task_url + f"/{contended_id}/cancel",
                    json={"requester_terminal_id": "deadbeef"},
                    timeout=15,
                )
                # Capture both public outcomes before either safety assertion.
                observations = {
                    name: {"http_status": value.status_code, "body": value.json()}
                    for name, value in (("status", missing_status), ("cancel", missing_cancel))
                }
                (tmp_path / "busy-missing-observations.json").write_text(
                    json.dumps(observations, indent=2)
                )
                with connect(profile_targets[1].main) as connection:
                    assert (
                        connection.execute(
                            "SELECT COUNT(*) FROM local_peer_tasks WHERE task_id=%s",
                            (contended_id,),
                        ).fetchone()[0]
                        == 0
                    )
                assert missing_status.status_code == 404, observations
                assert missing_status.json() == {"detail": {"kind": "task_not_found"}}
                assert missing_cancel.status_code == 404, observations
                assert missing_cancel.json() == {"detail": {"kind": "task_not_found"}}
                with connect(initiator_target) as connection:
                    assert connection.execute(
                        "SELECT state,terminal_id,result_json FROM local_peer_tasks WHERE task_id=%s",
                        (contended_id,),
                    ).fetchone() == ("accepted", None, None)
                with connect(peer_target) as connection:
                    lease = connection.execute(
                        """SELECT task_id,state FROM local_project_write_leases
                           WHERE host_id=%s AND owner_uid=%s AND project_id=%s""",
                        (host.host_id, host.owner_uid, challenge["project_id"]),
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
            if expected == "cancelled":
                # A missing observation must not cache absence or consume the key.
                retried = requests.post(
                    task_url,
                    json={**body, "operation_key": "acceptance-contended"},
                    timeout=45,
                )
                assert retried.status_code == 202, retried.text
                retry_receipt = retried.json()
                assert retry_receipt["task_id"] == contended_id
                assert retry_receipt["terminal_id"] not in (None, receipt["terminal_id"])
                resumed = requests.get(
                    task_url + f"/{contended_id}",
                    params={"requester_terminal_id": "deadbeef"},
                    timeout=15,
                )
                assert resumed.status_code == 200, resumed.text

                def retry_writing():
                    response = requests.get(
                        urls[1] + f'/terminals/{retry_receipt["terminal_id"]}/output',
                        params={"mode": "full"},
                        timeout=10,
                    )
                    return response.status_code == 200 and "__mock_sleep_30" in response.json().get(
                        "output", ""
                    )

                # Match the incumbent cancellation scenario: wait for actual
                # worker input, not only the asynchronous assignment response.
                _wait(retry_writing, 30)
                stopped = requests.post(
                    task_url + f"/{contended_id}/cancel",
                    json={"requester_terminal_id": "deadbeef"},
                    timeout=30,
                )
                assert stopped.status_code == 200, stopped.text

                retry_observations = []

                def retry_finished():
                    response = requests.get(
                        task_url + f"/{contended_id}",
                        params={"requester_terminal_id": "deadbeef"},
                        timeout=10,
                    )
                    assert response.status_code == 200, response.text
                    value = response.json()
                    retry_observations.append(value)
                    (tmp_path / "retry-cancel-observations.json").write_text(
                        json.dumps(retry_observations, indent=2)
                    )
                    return value if value["state"] in {"succeeded", "failed", "cancelled"} else None

                retried_final = _wait(retry_finished, 90)
                assert retried_final["state"] == "cancelled"
                assert retried_final["result"]["state"] == "cancelled"
                assert retried_final["result"]["worker_stopped"] is True
            snapshot = requests.get(sessions_url, params=session_params, timeout=15)
            assert (
                snapshot.status_code == 200 and snapshot.json()["state"] == "available"
            ), snapshot.text
        with connect(peer_target) as connection:
            assert (
                connection.execute(
                    """SELECT state FROM local_project_write_leases
                       WHERE host_id=%s AND owner_uid=%s AND project_id=%s""",
                    (host.host_id, host.owner_uid, challenge["project_id"]),
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
        with connect(initiator_target) as connection:
            offline_task_id = connection.execute(
                "SELECT task_id FROM local_peer_tasks WHERE operation_key=%s",
                ("acceptance-offline",),
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
