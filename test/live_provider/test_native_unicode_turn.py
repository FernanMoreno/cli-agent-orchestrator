"""Credential-free contracts for one real-provider Unicode task observation."""

from test.live_provider import native_unicode_turn as diagnostic
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "normalized",
        "missing",
        "wrong_child",
        "unsettled",
        "join_pending",
        "step_error",
        "join_error",
    ],
)
def test_unicode_result_requires_exact_text_and_same_settled_child(monkeypatch, mutation):
    expected = "CAO_UNICODE_test café, español, 日本語, e\u0301"
    child = {
        "id": "child",
        "terminal_id": "terminal",
        "provider": "codex",
        "state": "succeeded",
        "settled_at": "settled",
        "cleanup_completed_at": "cleaned",
    }
    text = expected if mutation != "normalized" else expected.replace("e\u0301", "é")
    if mutation == "missing":
        text = "unrelated"
    joined = dict(child)
    if mutation == "wrong_child":
        joined["id"] = "other"
    if mutation == "unsettled":
        joined["settled_at"] = None
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        status = 200
        if url.endswith("/openapi.json"):
            body = {
                "components": {
                    "schemas": {
                        "RunStepRequest": {
                            "type": "object",
                            "required": ["provider", "agent", "prompt"],
                        }
                    }
                }
            }
        elif url.endswith("/terminals/run-step"):
            body = {"terminal_id": "terminal", "last_message": text}
            if mutation == "step_error":
                status = 504
        else:
            assert url.endswith("/native-children/child/join")
            body = {"settled": mutation != "join_pending", "child": joined}
            if mutation == "join_error":
                status = 409
        return SimpleNamespace(status_code=status, json=lambda: body)

    monkeypatch.setattr(diagnostic.matrix, "_request", request)
    monkeypatch.setattr(diagnostic.matrix, "_receipt_for_terminal", lambda *a: dict(child))
    kwargs = dict(
        parent_id="parent",
        session_name="session",
        provider="codex",
        profile="private",
        expected=expected,
    )
    if mutation is None:
        result = diagnostic.run_unicode_turn(SimpleNamespace(url="http://owned"), **kwargs)
        assert result["expected_unicode_text_present"] is True
        assert result["same_child_join_succeeded"] is True
        assert expected not in str(result)
        assert "last_message" not in result
    else:
        with pytest.raises(AssertionError):
            diagnostic.run_unicode_turn(SimpleNamespace(url="http://owned"), **kwargs)
    body = next(c[2]["json"] for c in calls if c[1].endswith("/terminals/run-step"))
    assert body["timeout"] == 180 and body["prompt_redelivery"] is False
    assert body["teardown"] is True and body["agent"] == "private"
    assert expected in body["prompt"]
