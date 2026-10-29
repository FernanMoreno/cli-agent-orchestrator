"""Negative contracts for cancellation observation; no CLI or PostgreSQL."""

from test.live_provider import native_cancel_turn as diagnostic
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.offline_database
GEN = "a" * 32


@pytest.mark.parametrize("provider", ["codex", "opencode_cli", "claude_code"])
@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "metadata_parent",
        "metadata_provider",
        "metadata_session",
        "turn_generation",
        "turn_not_active",
        "receipt_parent",
        "receipt_settled",
        "stale_accepted",
        "stale_changed_generation",
        "before_failed",
        "cancel_http",
        "cancel_generation",
        "cancel_state",
        "child_not_cancelled",
        "join_identity",
        "after_failed",
        "missing_cleanup",
        "retired_still_present",
        "parent_dead",
    ],
)
def test_cancel_observer_binds_generation_identity_witnesses_and_cleanup(
    monkeypatch, mutation, provider
):
    cancelled = deleted = False
    calls = []

    def receipt():
        return {
            "id": "child",
            "terminal_id": "terminal",
            "parent_terminal_id": ("wrong" if mutation == "receipt_parent" else "parent"),
            "provider": provider,
            "state": "cancelled" if cancelled and mutation != "child_not_cancelled" else "sent",
            "settled_at": "settled" if cancelled or mutation == "receipt_settled" else None,
            "cleanup_completed_at": (
                "cleaned" if deleted and mutation != "missing_cleanup" else None
            ),
        }

    def turn():
        return {
            "terminal_id": "terminal",
            "provider": provider,
            "generation": ("b" * 32 if mutation == "turn_generation" else GEN),
            "state": (
                "cancelled"
                if cancelled
                else ("verified" if mutation == "turn_not_active" else "pending")
            ),
            "allowed_actions": [] if cancelled else ["verify", "cancel"],
        }

    def request(method, url, **kwargs):
        nonlocal cancelled, deleted
        calls.append((method, url, kwargs))
        status = 200
        if url.endswith("/turn/cancel"):
            if kwargs["params"]["generation"] != GEN:
                status = 200 if mutation == "stale_accepted" else 409
                body = {"detail": turn()}
                if mutation == "stale_changed_generation":
                    body["detail"]["generation"] = "f" * 32
            else:
                cancelled = True
                status = 500 if mutation == "cancel_http" else 200
                body = turn()
                if mutation == "cancel_generation":
                    body["generation"] = "f" * 32
                if mutation == "cancel_state":
                    body["state"] = "pending"
        elif url.endswith("/turn"):
            body = turn()
        elif url.endswith("/join"):
            body = {"settled": True, "child": receipt()}
            if mutation == "join_identity":
                body["child"]["id"] = "wrong"
        elif method == "DELETE":
            deleted = True
            body = {}
        elif url.endswith("/parent"):
            body = {"id": "parent"}
            status = 404 if mutation == "parent_dead" else 200
        else:
            body = {
                "id": "terminal",
                "provider": "other" if mutation == "metadata_provider" else provider,
                "caller_id": "other" if mutation == "metadata_parent" else "parent",
                "session_name": "other" if mutation == "metadata_session" else "session",
            }
            status = 404 if deleted and mutation != "retired_still_present" else 200
        return SimpleNamespace(status_code=status, json=lambda: body)

    monkeypatch.setattr(diagnostic.matrix, "_request", request)
    monkeypatch.setattr(diagnostic.matrix, "_receipt_for_terminal", lambda *_: receipt())
    witnesses = []

    def before(tid, generation):
        assert tid == "terminal" and generation == GEN and not cancelled and not deleted
        witnesses.append("before")
        if mutation == "before_failed":
            raise AssertionError("active witness refused")

    def after(tid, generation):
        assert tid == "terminal" and generation == GEN and cancelled and not deleted
        assert calls[-1][1].endswith("/join")
        witnesses.append("after")
        if mutation == "after_failed":
            raise AssertionError("replacement witness refused")

    def run():
        return diagnostic.observe_cancel_turn(
            SimpleNamespace(url="http://owned"),
            parent_id="parent",
            terminal_id="terminal",
            session_name="session",
            generation=GEN,
            provider=provider,
            before_cancel=before,
            after_cancel=after,
        )

    if mutation is None:
        result = run()
        assert result["receipt_state"] == "cancelled" and witnesses == ["before", "after"]
        assert result["cleanup_preserved_verdict"] is True and deleted
    else:
        with pytest.raises(AssertionError):
            run()
    if mutation in {
        "metadata_parent",
        "metadata_provider",
        "metadata_session",
        "turn_generation",
        "turn_not_active",
        "receipt_parent",
        "receipt_settled",
        "stale_accepted",
        "stale_changed_generation",
        "before_failed",
    }:
        assert not cancelled and not deleted
    if mutation in {"after_failed", "join_identity", "child_not_cancelled"}:
        assert not deleted
