from unittest.mock import Mock, patch

import pytest

from cli_agent_orchestrator.utils import orchestration as o

TURN = dict(
    terminal_id="term",
    provider="codex",
    generation="a" * 32,
    state="reconcile",
    reason="receipt_missing",
    attempts=3,
    allowed_actions=["verify", "cancel"],
)


def response(body, status=200):
    result = Mock(status_code=status)
    result.json.return_value = body
    return result


@pytest.mark.parametrize("status,state", [(202, "pending"), (409, "reconcile")])
def test_result_preserves_unresolved_diagnosis(status, state):
    turn = {**TURN, "state": state}
    with patch.object(o.requests, "get", return_value=response({"detail": turn}, status)):
        result = o._result_impl("term")
    assert result["success"] is False
    assert result["state"] == state
    assert result["generation"] == TURN["generation"]
    assert result["allowed_actions"] == ["verify", "cancel"]
    assert "output" not in result


def test_status_exposes_reconcile_instead_of_processing():
    with patch.object(
        o.requests,
        "get",
        return_value=response({"id": "term", "status": "processing", "turn": TURN}),
    ):
        result = o._status_impl("term")
    assert result["status"] == "reconcile"
    assert result["turn"] == TURN


def test_receipt_cancel_uses_generation_and_never_interrupts():
    with (
        patch.object(o.requests, "get", return_value=response(TURN)),
        patch.object(
            o.requests, "post", return_value=response({**TURN, "state": "cancelled"})
        ) as post,
    ):
        result = o._cancel_impl("term")
    assert result["state"] == "cancelled"
    assert post.call_args.args[0].endswith("/terminals/term/turn/cancel")
    assert post.call_args.kwargs["params"] == {"generation": "a" * 32}


def test_verify_reads_current_generation():
    with (
        patch.object(o.requests, "get", return_value=response(TURN)),
        patch.object(
            o.requests, "post", return_value=response({**TURN, "state": "verified"})
        ) as post,
    ):
        result = o._verify_impl("term")
    assert result["success"] is True
    assert post.call_args.kwargs["params"] == {"generation": "a" * 32}


def test_inspection_failure_never_falls_back_to_interrupt():
    with (
        patch.object(o.requests, "get", side_effect=o.requests.ConnectionError()),
        patch.object(o.requests, "post") as post,
    ):
        assert o._cancel_impl("term")["success"] is False
    post.assert_not_called()


def test_recovery_conflict_keeps_new_generation():
    detail = {**TURN, "generation": "b" * 32, "reason": "stale_generation"}
    with patch.object(o.requests, "post", return_value=response({"detail": detail}, 409)):
        result = o._turn_action_impl("term", "a" * 32, "verify")
    assert result["success"] is False
    assert result["generation"] == "b" * 32
    assert result["reason"] == "stale_generation"


@pytest.mark.parametrize("waiting", ["waiting_user_answer", "waiting_quota"])
@pytest.mark.parametrize("state", ["reconcile", "cancelling", "cancelled"])
def test_status_preserves_waiting_over_recovery_state(waiting, state):
    turn = {**TURN, "state": state}
    with patch.object(
        o.requests, "get", return_value=response({"id": "term", "status": waiting, "turn": turn})
    ):
        result = o._status_impl("term")
    assert result["status"] == waiting
    assert result["turn"] == turn
