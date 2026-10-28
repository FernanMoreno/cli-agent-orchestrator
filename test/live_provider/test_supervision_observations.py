"""Real HTTP middleware files supply the native proof counters and frozen expectations."""

import copy
from test.live_provider import supervision_observations as observation
from test.live_provider.supervision_api_worker import install_observer
from test.live_provider.supervision_authority import AuthorityProbeError

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline_database
CALLER, CHILD, FOREIGN = "aaaabbbb", "ccccdddd", "eeeeffff"


@pytest.fixture
def context(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    app = FastAPI()
    state = {"foreign_parent": None, "foreign_status": 200}

    @app.get("/terminals/{key}")
    def metadata(key):
        if key == FOREIGN and state["foreign_status"] != 200:
            return JSONResponse({}, status_code=state["foreign_status"])
        return {
            "id": key,
            "caller_id": (
                CALLER if key == CHILD else state["foreign_parent"] if key == FOREIGN else None
            ),
            "status": "processing",
            "allowed_tools": ["@cao-mcp-server"],
        }

    @app.get("/terminals/{key}/turn")
    def turn(key):
        return {
            "terminal_id": key,
            "generation": "a" * 32,
            "state": "pending",
            "allowed_actions": ["cancel"],
        }

    audit = tmp_path / "audit.jsonl"
    install_observer(app, tmp_path, audit)
    with TestClient(app) as client:

        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def get(self, url, timeout):
                assert self.trust_env is False and timeout == 10
                return client.get(url)

        monkeypatch.setattr(observation.requests, "Session", Session)
        client.post("/terminals/12345678/turn/cancel")

        def make():
            return observation.ApiObservations(
                "http://127.0.0.1:1234",
                tmp_path,
                audit,
                CALLER,
                CHILD,
                FOREIGN,
                lambda key: {"generation": "a" * 32, "phase": "sent"},
            )

        yield make, client, state, audit


def native_gets(client, *, child=True, foreign=True):
    for _ in range(4):
        client.get("/terminals/" + CALLER)
    if child:
        client.get("/terminals/" + CHILD)
    if foreign:
        client.get("/terminals/" + FOREIGN)


def test_actual_file_counters_and_frozen_expectations(context):
    make, client, _, _ = context
    obs = make()
    before = obs.observe_foreign()
    expected = obs.inspect_expected(CHILD)
    native_gets(client)
    after = obs.observe_foreign()
    assert after["target_reads"] == before["target_reads"] + 1
    assert before["action_requests"] == after["action_requests"] == 1
    assert obs.verify_native_reads() is True
    expected["state"] = "corrupted"
    assert obs.inspect_expected(CHILD)["state"] == "pending"


@pytest.mark.parametrize(
    "kind", ["foreign404", "foreign401", "missing_child", "extra_foreign", "post"]
)
def test_failed_lookup_or_action_never_counts_as_authorization(context, kind):
    make, client, state, _ = context
    obs = make()
    if kind.startswith("foreign"):
        state["foreign_status"] = int(kind[-3:])
    native_gets(client, child=kind != "missing_child")
    if kind == "extra_foreign":
        client.get("/terminals/" + FOREIGN)
    if kind == "post":
        client.post("/terminals/" + FOREIGN + "/turn/cancel")
    with pytest.raises(AuthorityProbeError):
        obs.verify_native_reads()
    if kind.startswith("foreign"):
        with pytest.raises(AuthorityProbeError):
            obs.observe_foreign()


def test_foreign_owned_by_caller_is_not_a_negative_control(context):
    make, _, state, _ = context
    state["foreign_parent"] = CALLER
    with pytest.raises(AuthorityProbeError, match="actual_foreign_target_invalid"):
        make()
