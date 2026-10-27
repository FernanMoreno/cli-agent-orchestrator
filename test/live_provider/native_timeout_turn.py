"""Full-budget timeout, retained execution and explicit generation-bound stop."""

import re
import time
from test.e2e import test_real_provider_matrix as matrix

from jsonschema import Draft202012Validator


def observe_timeout_recovery(server, *, parent_id, session_name, profile, prepare_witnesses):
    body = {
        "provider": "codex",
        "agent": profile,
        "prompt": "Continue this controlled timeout diagnostic. Do not call tools.",
        "session_name": session_name,
        "caller_id": parent_id,
        "teardown": True,
        "timeout": 180.0,
        "prompt_redelivery": False,
    }
    schema_response = matrix._request("GET", server.url + "/openapi.json", timeout=30)
    assert schema_response.status_code == 200
    document = schema_response.json()
    schema = dict(document["components"]["schemas"]["RunStepRequest"])
    schema["components"] = document["components"]
    Draft202012Validator(schema).validate(body)
    started = time.monotonic()
    response = matrix._request("POST", server.url + "/terminals/run-step", json=body)
    elapsed = time.monotonic() - started
    # HTTP includes initialization; the existing client permits 60s overhead.
    assert 180 <= elapsed <= 240
    assert response.status_code == 504
    detail = response.json()["detail"]
    assert detail["kind"] == "timeout" and detail["action"] is None
    assert detail["delivery_may_have_occurred"] is True and detail["retryable"] is False
    assert detail["provider_may_resume"] is False
    diagnostics = matrix._safe_step_failure(response)["wait_diagnostics"]
    assert diagnostics["last_status"] == "processing" and diagnostics["observed_working"] is True
    assert diagnostics["prompt_redelivery_attempts"] == 0
    tid = detail["terminal_id"]
    url = server.url + "/terminals/" + tid
    metadata = matrix._request("GET", url)
    assert metadata.status_code == 200
    meta = metadata.json()
    assert meta["id"] == tid and meta["provider"] == "codex"
    assert meta["caller_id"] == parent_id and meta["session_name"] == session_name
    receipt = matrix._receipt_for_terminal(server, parent_id, tid)
    assert receipt["id"] == detail["native_child_id"]
    assert receipt["terminal_id"] == tid and receipt["parent_terminal_id"] == parent_id
    assert receipt["provider"] == "codex" and receipt["state"] == "reconcile"
    assert receipt["error_kind"] == "timeout" and receipt["settled_at"]
    assert receipt["cleanup_completed_at"] is None
    stable = (
        "id",
        "terminal_id",
        "parent_terminal_id",
        "provider",
        "state",
        "error_kind",
        "settled_at",
    )

    def joined():
        response = matrix._request(
            "POST", server.url + "/native-children/" + receipt["id"] + "/join"
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["settled"] is True  # disposition, not process stoppage
        child = payload["child"]
        assert all(child[k] == receipt[k] for k in stable)
        return child

    assert joined()["cleanup_completed_at"] is None
    response = matrix._request("GET", url + "/turn")
    assert response.status_code == 200
    turn = response.json()
    generation = turn["generation"]
    assert re.fullmatch(r"[0-9a-f]{32}", generation)

    def pending(value):
        assert value["terminal_id"] == tid and value["provider"] == "codex"
        assert value["generation"] == generation and value["state"] == "pending"
        assert set(value["allowed_actions"]) == {"verify", "cancel"}

    pending(turn)
    before, after = prepare_witnesses(tid, generation)
    response = matrix._request("POST", url + "/turn/verify", params={"generation": generation})
    assert response.status_code == 200
    pending(response.json())
    stale = ("0" if generation[0] != "0" else "1") + generation[1:]
    response = matrix._request("POST", url + "/turn/cancel", params={"generation": stale})
    assert response.status_code == 409
    pending(response.json()["detail"])
    assert joined()["cleanup_completed_at"] is None
    before(tid, generation)
    started = time.monotonic()
    response = matrix._request("POST", url + "/turn/cancel", params={"generation": generation})
    cancel_seconds = time.monotonic() - started
    assert response.status_code == 200 and cancel_seconds <= 180
    stopped = response.json()
    assert stopped["terminal_id"] == tid and stopped["provider"] == "codex"
    assert stopped["generation"] == generation and stopped["state"] == "cancelled"
    assert stopped["allowed_actions"] == []
    assert joined()["cleanup_completed_at"] is None
    after(tid, generation)
    matrix._delete_terminal(server, tid)
    assert matrix._request("GET", url).status_code == 404
    assert joined()["cleanup_completed_at"]
    assert matrix._request("GET", server.url + "/terminals/" + parent_id).status_code == 200
    return {
        "schema": "caos.managed-native-timeout.v1",
        "terminal_id": tid,
        "timeout_seconds": 180,
        "http_seconds_including_initialization": round(elapsed, 3),
        "timeout_http_status": 504,
        "receipt_state": "reconcile",
        "receipt_error_kind": "timeout",
        "turn_before_cancel": "pending",
        "turn_after_cancel": "cancelled",
        "cancel_http_status": 200,
        "cancel_seconds": round(cancel_seconds, 3),
        "stale_generation_http_status": 409,
        "verify_existing_evidence_http_status": 200,
        "prompt_redelivery": False,
        "wait_diagnostics": diagnostics,
        "timeout_disposition_and_settled_at_preserved": True,
        "cleanup_preserved_verdict": True,
        "parent_alive_after_child_delete": True,
    }
