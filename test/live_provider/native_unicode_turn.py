"""One Unicode task through production run-step; no provider CLI substitute."""

import time
from test.e2e import test_real_provider_matrix as matrix

from jsonschema import Draft202012Validator


def run_unicode_turn(server, *, parent_id, session_name, provider, profile, expected):
    """Observe exact Unicode and a matching settled native-child receipt.

    This does not inspect the internal turn CAS or attest the served model.
    The existing run-step route owns extraction, verification and teardown.
    """
    body = {
        "provider": provider,
        "agent": profile,
        "prompt": (
            "Return this exact text on its own line, preserving every Unicode codepoint: "
            + expected
            + ". Then follow the CAO completion-receipt instruction supplied with this task. "
            "Do not call tools, edit files, or delegate. Apart from that text and the "
            "required receipt, add no other text."
        ),
        "session_name": session_name,
        "caller_id": parent_id,
        "teardown": True,
        "timeout": 180.0,
        "prompt_redelivery": False,
    }
    schema_response = matrix._request("GET", server.url + "/openapi.json", timeout=30)
    assert schema_response.status_code == 200, "unicode_openapi_unavailable"
    document = schema_response.json()
    schema = dict(document["components"]["schemas"]["RunStepRequest"])
    schema["components"] = document["components"]
    Draft202012Validator(schema).validate(body)
    started = time.monotonic()
    response = matrix._request("POST", server.url + "/terminals/run-step", json=body)
    elapsed = time.monotonic() - started
    assert response.status_code == 200, f"unicode_step_http_{response.status_code}"
    assert elapsed <= 180, "unicode_step_exceeded_180_seconds"
    data = response.json()
    text = data.get("last_message", "")
    assert isinstance(text, str) and expected in text, "unicode_result_mismatch"
    terminal_id = data["terminal_id"]
    receipt = matrix._receipt_for_terminal(server, parent_id, terminal_id)
    assert receipt.get("terminal_id") == terminal_id, "unicode_receipt_terminal_mismatch"
    assert receipt.get("provider") == provider, "unicode_receipt_provider_mismatch"
    assert receipt.get("state") == "succeeded", "unicode_receipt_not_succeeded"
    assert receipt.get("settled_at"), "unicode_receipt_unsettled"
    assert receipt.get("cleanup_completed_at"), "unicode_receipt_cleanup_missing"
    joined = matrix._request("POST", server.url + "/native-children/" + receipt["id"] + "/join")
    assert joined.status_code == 200, "unicode_join_http_failure"
    payload = joined.json()
    assert payload.get("settled") is True, "unicode_join_unsettled"
    child = payload.get("child", {})
    fields = ("id", "terminal_id", "provider", "state", "settled_at", "cleanup_completed_at")
    assert all(child.get(k) == receipt.get(k) for k in fields), "unicode_join_receipt_changed"
    return {
        "schema": "caos.native-unicode-turn.v1",
        "provider": provider,
        "terminal_id": terminal_id,
        "step_http_status": 200,
        "step_seconds": round(elapsed, 3),
        "timeout_seconds": 180,
        "prompt_redelivery": False,
        "request_schema_validated": True,
        "expected_unicode_text_present": True,
        "unicode_normalization_applied": False,
        "whole_response_equality_required": False,
        "native_receipt_succeeded": True,
        "native_receipt_settled": True,
        "child_cleanup_receipted": True,
        "same_child_join_succeeded": True,
        "internal_turn_generation_cas_independently_observed": False,
    }
