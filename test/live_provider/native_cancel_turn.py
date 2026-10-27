"""Bound cancellation observation; physical process evidence belongs to its caller."""

import re
import time
from test.e2e import test_real_provider_matrix as matrix


def observe_cancel_turn(
    server, *, parent_id, terminal_id, session_name, generation, before_cancel, after_cancel
):
    assert re.fullmatch(r"[0-9a-f]{32}", generation)
    url = server.url + "/terminals/" + terminal_id
    metadata = matrix._request("GET", url)
    assert metadata.status_code == 200
    metadata = metadata.json()
    assert metadata["id"] == terminal_id and metadata["provider"] == "codex"
    assert metadata["caller_id"] == parent_id and metadata["session_name"] == session_name

    def active(turn):
        assert turn["terminal_id"] == terminal_id and turn["provider"] == "codex"
        assert turn["generation"] == generation and turn["state"] == "pending"
        assert "cancel" in turn["allowed_actions"]

    response = matrix._request("GET", url + "/turn")
    assert response.status_code == 200
    active(response.json())
    receipt = matrix._receipt_for_terminal(server, parent_id, terminal_id)
    assert receipt["terminal_id"] == terminal_id and receipt["parent_terminal_id"] == parent_id
    assert receipt["provider"] == "codex" and receipt["state"] in {
        "planned",
        "acknowledged",
        "sent",
        "running",
    }
    assert receipt["settled_at"] is None and receipt["cleanup_completed_at"] is None

    # A valid but foreign generation exercises ownership, not query validation.
    stale = ("0" if generation[0] != "0" else "1") + generation[1:]
    response = matrix._request("POST", url + "/turn/cancel", params={"generation": stale})
    assert response.status_code == 409
    active(response.json()["detail"])
    refused = matrix._receipt_for_terminal(server, parent_id, terminal_id)
    assert all(
        refused[k] == receipt[k]
        for k in (
            "id",
            "terminal_id",
            "parent_terminal_id",
            "provider",
            "state",
            "settled_at",
            "cleanup_completed_at",
        )
    )
    before_cancel(terminal_id, generation)
    started = time.monotonic()
    response = matrix._request("POST", url + "/turn/cancel", params={"generation": generation})
    elapsed = time.monotonic() - started
    assert response.status_code == 200 and elapsed <= 180
    turn = response.json()
    assert turn["terminal_id"] == terminal_id and turn["provider"] == "codex"
    assert turn["generation"] == generation and turn["state"] == "cancelled"
    assert turn["allowed_actions"] == []
    cancelled = matrix._receipt_for_terminal(server, parent_id, terminal_id)
    stable = ("id", "terminal_id", "parent_terminal_id", "provider")
    assert all(cancelled[k] == receipt[k] for k in stable)
    assert cancelled["state"] == "cancelled" and cancelled["settled_at"]
    assert cancelled["cleanup_completed_at"] is None

    def joined():
        response = matrix._request(
            "POST", server.url + "/native-children/" + receipt["id"] + "/join"
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["settled"] is True
        child = payload["child"]
        assert all(child[k] == cancelled[k] for k in (*stable, "state", "settled_at"))
        return child

    assert joined()["cleanup_completed_at"] is None
    after_cancel(terminal_id, generation)
    matrix._delete_terminal(server, terminal_id)
    assert matrix._request("GET", url).status_code == 404
    assert joined()["cleanup_completed_at"]
    assert matrix._request("GET", server.url + "/terminals/" + parent_id).status_code == 200
    return {
        "schema": "caos.managed-native-cancel.v1",
        "terminal_id": terminal_id,
        "cancel_http_status": 200,
        "cancel_seconds": round(elapsed, 3),
        "stale_generation_http_status": 409,
        "same_generation_cancelled": True,
        "receipt_state": "cancelled",
        "same_child_join_verified": True,
        "cleanup_preserved_verdict": True,
        "parent_alive_after_child_delete": True,
        "before_and_after_owned_witnesses_returned": True,
    }
