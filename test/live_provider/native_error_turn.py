"""Observe controlled provider errors through the production managed task API."""

import time
from test.e2e import test_real_provider_matrix as matrix

from jsonschema import Draft202012Validator


def run_error_turn(server, *, parent_id, session_name, profile, case):
    """Inspect a retained error child before explicitly cleaning its terminal."""
    assert case in {"auth", "throttle", "quota"}
    quota = case == "quota"
    expected_http = 409 if quota else 502
    expected_kind = "quota_wait" if quota else "error"
    expected_state = "reconcile" if quota else "failed"
    body = {
        "provider": "codex",
        "agent": profile,
        "prompt": "Reply briefly to this controlled provider-error diagnostic. Do not use tools.",
        "session_name": session_name,
        "caller_id": parent_id,
        "teardown": True,
        "timeout": 180.0,
        "prompt_redelivery": False,
    }
    response = matrix._request("GET", server.url + "/openapi.json", timeout=30)
    assert response.status_code == 200
    document = response.json()
    schema = dict(document["components"]["schemas"]["RunStepRequest"])
    schema["components"] = document["components"]
    Draft202012Validator(schema).validate(body)
    started = time.monotonic()
    response = matrix._request("POST", server.url + "/terminals/run-step", json=body)
    elapsed = time.monotonic() - started
    assert elapsed <= 180, "managed_error_step_exceeded_180_seconds"
    assert response.status_code == expected_http, "managed_error_step_http_mismatch"
    detail = response.json()["detail"]
    assert detail["kind"] == expected_kind
    assert detail["delivery_may_have_occurred"] is True and detail["retryable"] is False
    assert detail["action"] == ("wait_for_quota" if quota else None)
    assert type(detail["provider_may_resume"]) is bool
    if not quota:
        assert detail["provider_may_resume"] is False
    assert "wait_diagnostics" not in detail
    terminal_id = detail["terminal_id"]
    assert isinstance(terminal_id, str) and terminal_id
    receipt = matrix._receipt_for_terminal(server, parent_id, terminal_id)
    assert receipt["id"] == detail["native_child_id"]
    assert receipt["parent_terminal_id"] == parent_id and receipt["terminal_id"] == terminal_id
    assert receipt["provider"] == "codex"
    assert receipt["state"] == expected_state and receipt["error_kind"] == expected_kind
    assert receipt["settled_at"] and receipt["cleanup_completed_at"] is None
    assert matrix._request("GET", server.url + "/terminals/" + terminal_id).status_code == 200
    stable = (
        "id",
        "parent_terminal_id",
        "terminal_id",
        "provider",
        "state",
        "error_kind",
        "settled_at",
    )

    def joined_child():
        result = matrix._request("POST", server.url + "/native-children/" + receipt["id"] + "/join")
        assert result.status_code == 200
        payload = result.json()
        # Reconcile is a durable join verdict, not successful task completion.
        assert payload["settled"] is True
        child = payload["child"]
        assert all(child.get(k) == receipt.get(k) for k in stable)
        return child

    assert joined_child()["cleanup_completed_at"] is None
    matrix._delete_terminal(server, terminal_id)
    assert matrix._request("GET", server.url + "/terminals/" + terminal_id).status_code == 404
    assert joined_child()["cleanup_completed_at"]
    return {
        "schema": "caos.managed-native-error.v1",
        "case": case,
        "terminal_id": terminal_id,
        "http_status": expected_http,
        "kind": expected_kind,
        "step_seconds": round(elapsed, 3),
        "timeout_seconds": 180,
        "prompt_redelivery": False,
        "request_schema_validated": True,
        "delivery_may_have_occurred": True,
        "retryable": False,
        "provider_may_resume": detail["provider_may_resume"],
        "receipt_state": expected_state,
        "same_child_join_verified": True,
        "child_retained_before_explicit_cleanup": True,
        "cleanup_preserved_verdict": True,
    }
