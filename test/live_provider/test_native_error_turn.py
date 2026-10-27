"""Credential-free contracts for managed error/receipt observations."""

from test.live_provider import native_error_turn as diagnostic
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize("case", ["auth", "throttle", "quota"])
@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "wrong_parent",
        "wrong_join",
        "retryable",
        "false_success",
        "missing_cleanup",
        "inspection_failed",
    ],
)
def test_error_observation_preserves_identity_verdict_and_explicit_cleanup(
    monkeypatch, case, mutation
):
    quota = case == "quota"
    receipt = {
        "id": "child",
        "parent_terminal_id": "parent",
        "terminal_id": "terminal",
        "provider": "codex",
        "state": "reconcile" if quota else "failed",
        "error_kind": "quota_wait" if quota else "error",
        "settled_at": "settled",
        "cleanup_completed_at": None,
    }
    calls = []
    deleted = False
    if mutation == "wrong_parent":
        receipt["parent_terminal_id"] = "other"

    def request(method, url, **kwargs):
        nonlocal deleted
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
            status = 409 if quota else 502
            body = {
                "detail": {
                    "kind": receipt["error_kind"],
                    "terminal_id": "terminal",
                    "native_child_id": "child",
                    "action": "wait_for_quota" if quota else None,
                    "delivery_may_have_occurred": True,
                    "retryable": mutation == "retryable",
                    "provider_may_resume": False,
                }
            }
            if mutation == "false_success":
                status = 200
        elif url.endswith("/join"):
            child = dict(receipt)
            if deleted and mutation != "missing_cleanup":
                child["cleanup_completed_at"] = "cleaned"
            if mutation == "wrong_join":
                child["id"] = "other"
            body = {"child": child, "settled": True}
        elif method == "DELETE":
            assert url.endswith("/terminals/terminal")
            deleted = True
            body = {"deleted": True}
        else:
            status = 404 if deleted else 200
            body = {"id": "terminal", "status": "waiting_quota" if quota else "error"}
        return SimpleNamespace(status_code=status, json=lambda: body)

    monkeypatch.setattr(diagnostic.matrix, "_request", request)
    monkeypatch.setattr(diagnostic.matrix, "_receipt_for_terminal", lambda *a: dict(receipt))
    inspections = []

    def inspect_retained(terminal_id):
        assert terminal_id == "terminal" and not deleted
        assert calls[-1][1].endswith("/join")
        inspections.append(terminal_id)
        if mutation == "inspection_failed":
            raise AssertionError("retained_observation_failed")

    if mutation is None:
        result = diagnostic.run_error_turn(
            SimpleNamespace(url="http://owned"),
            parent_id="parent",
            session_name="session",
            profile="private",
            case=case,
            before_cleanup=inspect_retained,
        )
        assert result["child_retained_before_explicit_cleanup"] is True
        assert result["cleanup_preserved_verdict"] is True
        assert result["receipt_state"] == receipt["state"]
    else:
        with pytest.raises(AssertionError):
            diagnostic.run_error_turn(
                SimpleNamespace(url="http://owned"),
                parent_id="parent",
                session_name="session",
                profile="private",
                case=case,
                before_cleanup=inspect_retained,
            )
    if mutation in {"wrong_parent", "wrong_join", "inspection_failed"}:
        assert not deleted
    assert inspections == (
        ["terminal"] if mutation in {None, "missing_cleanup", "inspection_failed"} else []
    )
