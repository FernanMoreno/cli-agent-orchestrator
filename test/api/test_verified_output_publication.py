"""LAST must observe a durable result settled after its unavailable read."""

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.providers.base import OutputExtractionError, TurnResultUnavailableError
from cli_agent_orchestrator.services import terminal_service as terminal
from cli_agent_orchestrator.services import turn_recovery_service as recovery

KEY = "abcd1234"
GENERATION = "1" * 32
RECEIPT_HASH = "2" * 64


def claim():
    assert db.engine.dialect.name == "postgresql"
    db.create_terminal(KEY, "publication-race", "worker", "mock_cli", "reviewer")
    assert db.begin_terminal_turn_receipt(KEY, "mock_cli", GENERATION, RECEIPT_HASH)
    assert db.mark_terminal_turn_receipt_sent(KEY, GENERATION, RECEIPT_HASH)


def settle(result):
    return db.settle_terminal_turn_receipt_result(
        KEY,
        GENERATION,
        RECEIPT_HASH,
        hashlib.sha256(b"private receipt-bearing result").hexdigest(),
        result_text=result,
    )


def request_after_settlement(client, monkeypatch, result):
    claim()
    entered, release = threading.Event(), threading.Event()
    original = terminal.get_output
    calls = []
    provider = Mock(side_effect=AssertionError("durable result must not restore a provider"))
    backend = Mock(side_effect=AssertionError("durable result must not need native history"))
    monkeypatch.setattr(terminal.provider_manager, "get_provider", provider)
    monkeypatch.setattr(terminal, "get_backend", backend)
    monkeypatch.setattr(terminal.status_monitor, "get_buffer", lambda _key: "cached observation")

    def read(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            assert recovery.get_turn(KEY)["state"] == "pending"
            entered.set()
            assert release.wait(10)
            raise TurnResultUnavailableError("output was not verified at the first read")
        return original(*args, **kwargs)

    monkeypatch.setattr(terminal, "get_output", read)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.get, f"/terminals/{KEY}/output?mode=last")
        try:
            assert entered.wait(10)
            assert settle(result)
            release.set()
            response = pending.result(timeout=15)
        finally:
            release.set()
    provider.assert_not_called()
    backend.assert_not_called()
    return response, calls


@pytest.mark.parametrize("result", ["retained public result", ""])
def test_last_returns_same_generation_result_settled_between_api_reads(client, monkeypatch, result):
    response, calls = request_after_settlement(client, monkeypatch, result)
    assert response.status_code == 200, response.text
    assert response.json() == {"output": result, "mode": "last"}
    assert calls == [
        ((KEY, terminal.OutputMode.LAST), {}),
        ((KEY, terminal.OutputMode.LAST), {"expected_generation": GENERATION}),
    ]
    assert recovery.get_turn(KEY)["state"] == "verified"


def test_verified_without_retained_text_still_fails_closed(client, monkeypatch):
    response, calls = request_after_settlement(client, monkeypatch, None)
    assert response.status_code == 409
    assert response.json()["detail"]["state"] == "verified"
    assert len(calls) <= 2
    assert db.get_terminal_turn_recovery(KEY)["result_text"] is None


@pytest.mark.parametrize("replacement_state", ["pending", "verified"])
def test_last_does_not_return_another_generation_after_verified_observation(
    client, monkeypatch, replacement_state
):
    actual = recovery.get_turn
    observed = []
    new_generation, new_hash = "3" * 32, "4" * 64

    def inspect(key):
        turn = actual(key)
        if turn["state"] == "verified" and not observed:
            observed.append(turn["generation"])
            assert db.begin_terminal_turn_receipt(KEY, "mock_cli", new_generation, new_hash)
            if replacement_state == "verified":
                assert db.settle_terminal_turn_receipt_result(
                    KEY,
                    new_generation,
                    new_hash,
                    hashlib.sha256(b"new private result").hexdigest(),
                    result_text="another generation's result",
                )
        return turn

    monkeypatch.setattr(recovery, "get_turn", inspect)
    response, calls = request_after_settlement(client, monkeypatch, "old result")
    assert response.status_code == (202 if replacement_state == "pending" else 409), response.text
    assert response.json()["detail"]["generation"] == new_generation
    assert response.json()["detail"]["state"] == replacement_state
    assert observed == [GENERATION]
    assert len(calls) == 2
    assert calls[-1][1] == {"expected_generation": GENERATION}


@pytest.mark.parametrize("generation", [None, "old", "g" * 32, "1" * 31])
def test_verified_without_valid_generation_never_uses_unbound_retry(
    client, monkeypatch, generation
):
    read = Mock(side_effect=TurnResultUnavailableError("no proof"))
    monkeypatch.setattr(terminal, "get_output", read)
    monkeypatch.setattr(
        recovery,
        "get_turn",
        lambda _key: {"terminal_id": KEY, "generation": generation, "state": "verified"},
    )
    response = client.get(f"/terminals/{KEY}/output?mode=last")
    assert response.status_code == 409
    read.assert_called_once_with(KEY, terminal.OutputMode.LAST)


def test_full_is_not_replaced_by_a_retained_last_result(client, monkeypatch):
    claim()
    assert settle("retained last result")
    read = Mock(side_effect=TurnResultUnavailableError("full unavailable"))
    monkeypatch.setattr(terminal, "get_output", read)
    response = client.get(f"/terminals/{KEY}/output?mode=full")
    assert response.status_code == 409
    read.assert_called_once_with(KEY, terminal.OutputMode.FULL)


@pytest.mark.parametrize(
    "failure,code",
    [
        (OutputExtractionError("reader failed"), 500),
        (ValueError("terminal gone"), 404),
        (RuntimeError("read failed"), 500),
    ],
)
def test_generation_bound_read_preserves_typed_read_errors(client, monkeypatch, failure, code):
    read = Mock(side_effect=[TurnResultUnavailableError("first read unavailable"), failure])
    monkeypatch.setattr(terminal, "get_output", read)
    monkeypatch.setattr(
        recovery,
        "get_turn",
        lambda _key: {"terminal_id": KEY, "generation": GENERATION, "state": "verified"},
    )
    response = client.get(f"/terminals/{KEY}/output?mode=last")
    assert response.status_code == code
    assert read.call_count == 2
    assert read.call_args.kwargs == {"expected_generation": GENERATION}
