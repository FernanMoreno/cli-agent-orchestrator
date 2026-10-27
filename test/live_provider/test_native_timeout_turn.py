"""Timeout is not success/stoppage; retained receipt identity must survive recovery."""

from test.live_provider import native_timeout_turn as diagnostic
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.offline_database
GEN = "a" * 32


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "early_timeout",
        "false_success",
        "wrong_kind",
        "retryable",
        "not_delivered",
        "not_working",
        "redelivery",
        "private_diagnostics",
        "wrong_parent",
        "wrong_child_id",
        "succeeded_receipt",
        "wrong_receipt_error",
        "unsettled",
        "wrong_turn_state",
        "verify_generation_changed",
        "stale_accepted",
        "stale_generation_changed",
        "before_failed",
        "cancel_generation_changed",
        "cancel_not_stopped",
        "receipt_changed_after_cancel",
        "settled_changed_after_cancel",
        "after_failed",
        "missing_cleanup",
        "parent_dead",
    ],
)
def test_timeout_recovery_preserves_original_disposition(monkeypatch, mutation):
    cancelled = deleted = False
    calls = []
    witnesses = []

    def receipt():
        return {
            "id": "child",
            "terminal_id": "terminal",
            "parent_terminal_id": "wrong" if mutation == "wrong_parent" else "parent",
            "provider": "codex",
            "state": (
                "succeeded"
                if mutation == "succeeded_receipt"
                or (cancelled and mutation == "receipt_changed_after_cancel")
                else "reconcile"
            ),
            "error_kind": "error" if mutation == "wrong_receipt_error" else "timeout",
            "settled_at": (
                None
                if mutation == "unsettled"
                else (
                    "changed"
                    if cancelled and mutation == "settled_changed_after_cancel"
                    else "settled"
                )
            ),
            "cleanup_completed_at": (
                "cleaned" if deleted and mutation != "missing_cleanup" else None
            ),
        }

    def turn():
        return {
            "terminal_id": "terminal",
            "provider": "codex",
            "generation": GEN,
            "state": (
                "cancelled"
                if cancelled
                else ("reconcile" if mutation == "wrong_turn_state" else "pending")
            ),
            "allowed_actions": [] if cancelled else ["verify", "cancel"],
        }

    def request(method, url, **kwargs):
        nonlocal cancelled, deleted
        calls.append((method, url))
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
            assert kwargs["json"]["timeout"] == 180 and kwargs["json"]["teardown"] is True
            assert kwargs["json"]["prompt_redelivery"] is False
            status = 200 if mutation == "false_success" else 504
            body = {
                "detail": {
                    "kind": "error" if mutation == "wrong_kind" else "timeout",
                    "action": None,
                    "delivery_may_have_occurred": mutation != "not_delivered",
                    "retryable": mutation == "retryable",
                    "provider_may_resume": False,
                    "terminal_id": "terminal",
                    "native_child_id": "wrong" if mutation == "wrong_child_id" else "child",
                    "wait_diagnostics": {
                        "phase": "completion",
                        "last_status": "processing",
                        "observed_working": mutation != "not_working",
                        "receipt_probe_errors": 0,
                        "receipt_candidate_seen": False,
                        "stable_receipt_observations": 0,
                        "idle_extraction_attempted": False,
                        "receipt_probe_calls": 0,
                        "prompt_redelivery_attempts": 1 if mutation == "redelivery" else 0,
                        "delivery_verified": False,
                    },
                }
            }
            if mutation == "private_diagnostics":
                body["detail"]["wait_diagnostics"]["raw_text"] = "must not escape"
        elif url.endswith("/turn/verify"):
            body = turn()
            if mutation == "verify_generation_changed":
                body["generation"] = "b" * 32
        elif url.endswith("/turn/cancel"):
            if kwargs["params"]["generation"] != GEN:
                status = 200 if mutation == "stale_accepted" else 409
                body = {"detail": turn()}
                if mutation == "stale_generation_changed":
                    body["detail"]["generation"] = "b" * 32
            else:
                cancelled = True
                body = turn()
                if mutation == "cancel_generation_changed":
                    body["generation"] = "b" * 32
                if mutation == "cancel_not_stopped":
                    body["state"] = "pending"
        elif url.endswith("/turn"):
            body = turn()
        elif url.endswith("/join"):
            body = {"child": receipt(), "settled": True}
        elif method == "DELETE":
            deleted = True
            body = {}
        elif url.endswith("/parent"):
            status = 404 if mutation == "parent_dead" else 200
            body = {}
        else:
            status = 404 if deleted else 200
            body = {
                "id": "terminal",
                "provider": "codex",
                "caller_id": "parent",
                "session_name": "session",
            }
        return SimpleNamespace(status_code=status, json=lambda: body)

    monkeypatch.setattr(diagnostic.matrix, "_request", request)
    monkeypatch.setattr(diagnostic.matrix, "_receipt_for_terminal", lambda *_: receipt())
    times = iter([0, 70 if mutation == "early_timeout" else 200, 201, 227])
    monkeypatch.setattr(diagnostic.time, "monotonic", lambda: next(times, 227))

    def prepare(tid, generation):
        assert tid == "terminal" and generation == GEN and not cancelled and not deleted

        def before(*_):
            witnesses.append("before")
            assert not cancelled and not deleted
            if mutation == "before_failed":
                raise AssertionError("active witness refused")

        def after(*_):
            witnesses.append("after")
            assert cancelled and not deleted
            if mutation == "after_failed":
                raise AssertionError("replacement witness refused")

        return before, after

    def run():
        return diagnostic.observe_timeout_recovery(
            SimpleNamespace(url="http://owned"),
            parent_id="parent",
            session_name="session",
            profile="private",
            prepare_witnesses=prepare,
        )

    if mutation is None:
        result = run()
        assert result["timeout_disposition_and_settled_at_preserved"] and deleted
        assert result["turn_after_cancel"] == "cancelled" and result["receipt_state"] == "reconcile"
        assert witnesses == ["before", "after"]
    else:
        with pytest.raises((AssertionError, KeyError)):
            run()
    assert sum(url.endswith("/terminals/run-step") for _, url in calls) == 1
    if mutation in {
        "before_failed",
        "stale_accepted",
        "stale_generation_changed",
        "verify_generation_changed",
    }:
        assert not cancelled and not deleted
    if mutation in {"receipt_changed_after_cancel", "settled_changed_after_cancel", "after_failed"}:
        assert not deleted
