"""HTTP observation proves that failed lookups cannot masquerade as authority."""

import json
from test.live_provider import supervision_api_worker as worker

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline_database


def test_actual_http_observer_separates_successful_reads_and_action_attempts(tmp_path):
    tmp_path.chmod(0o700)
    app = FastAPI()

    @app.get("/terminals/aaaabbbb")
    def metadata():
        return {"id": "aaaabbbb", "caller_id": None}

    path = tmp_path / "audit.jsonl"
    worker.install_observer(app, tmp_path, path)
    with TestClient(app) as client:
        assert client.get("/terminals/aaaabbbb").status_code == 200
        assert client.get("/terminals/ccccdddd").status_code == 404
        assert client.post("/terminals/eeeeffff/turn/cancel?generation=opaque").status_code == 404
        client.get("/terminals/aaaabbbb/turn")
    assert worker.read_observations(path) == [
        {"method": "GET", "terminal_id": "aaaabbbb", "action": "metadata", "status": 200},
        {"method": "GET", "terminal_id": "ccccdddd", "action": "metadata", "status": 404},
        {"method": "POST", "terminal_id": "eeeeffff", "action": "cancel", "status": 0},
    ]
    assert "opaque" not in path.read_text() and "caller_id" not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "audit-ready.json").exists()


def test_observer_never_overwrites_existing_evidence(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "audit.jsonl"
    worker.install_observer(FastAPI(), tmp_path, path)
    with pytest.raises(FileExistsError):
        worker.install_observer(FastAPI(), tmp_path, path)


@pytest.mark.parametrize(
    "change",
    [{"status": True}, {"action": "foreign"}, {"terminal_id": "bad"}, {"body": "synthetic"}],
)
def test_invalid_observation_is_rejected(tmp_path, change):
    path = tmp_path / "audit.jsonl"
    path.write_text(
        json.dumps(
            {
                "method": "GET",
                "terminal_id": "aaaabbbb",
                "action": "metadata",
                "status": 200,
                **change,
            }
        )
        + "\n"
    )
    path.chmod(0o600)
    with pytest.raises(RuntimeError):
        worker.read_observations(path)
