"""Structured operator CLI output preserves confirmation and one-use authority."""

import json

import pytest
from click.testing import CliRunner

from cli_agent_orchestrator.cli.commands import peer

SOURCE = "11111111-1111-4111-8111-111111111111"
TARGET = "22222222-2222-4222-8222-222222222222"
CHALLENGE = "33333333-3333-4333-8333-333333333333"
PROJECT = "b" * 64
CODE = "a" * 43


@pytest.fixture
def pairing(monkeypatch, tmp_path):
    challenge = {
        "challenge_id": CHALLENGE,
        "code": CODE,
        "expires_at": 2000000000.0,
        "peer_instance_id": TARGET,
        "peer_display_name": "destino",
        "project_id": PROJECT,
        "canonical_root": str(tmp_path),
        "requested_scopes": ["task:submit"],
    }
    candidate = {
        "initiator_display_name": "origen",
        "initiator_instance_id": SOURCE,
        "canonical_root": str(tmp_path),
        "requested_scopes": ["task:submit"],
    }
    accepted = {
        "peer_instance_id": SOURCE,
        "peer_display_name": "origen",
        "project_id": PROJECT,
        "scopes": ["task:submit"],
    }
    calls = []
    monkeypatch.setattr(peer.local_peer_service, "initiate_pairing", lambda *_: challenge)
    monkeypatch.setattr(peer.local_peer_auth, "get_candidate_challenge", lambda _: candidate)

    def accept(challenge_id, code):
        calls.append((challenge_id, code))
        return accepted

    monkeypatch.setattr(peer.local_peer_service, "accept_pairing", accept)
    return tmp_path, challenge, accepted, calls


def test_pair_json_is_one_versioned_document(pairing):
    path, challenge, _, _ = pairing
    result = CliRunner().invoke(peer.peer, ["pair", TARGET, "--project", str(path), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "schema": "cao.peer.pair.v1",
        "success": True,
        "data": challenge,
    }
    assert "Código temporal" not in result.stdout


@pytest.mark.parametrize("confirmation", ["y", "n"])
def test_accept_json_keeps_review_on_stderr_and_requires_consent(pairing, confirmation):
    _, _, accepted, calls = pairing
    result = CliRunner().invoke(
        peer.peer, ["accept", CHALLENGE, "--json"], input=confirmation + "\n" + CODE + "\n"
    )
    body = json.loads(result.stdout)
    assert CODE not in result.output
    assert "Acciones solicitadas" in result.stderr
    if confirmation == "y":
        assert result.exit_code == 0, result.output
        assert body == {"schema": "cao.peer.accept.v1", "success": True, "data": accepted}
        assert calls == [(CHALLENGE, CODE)]
    else:
        assert result.exit_code == 1
        assert body["schema"] == "cao.peer.error.v1"
        assert body["success"] is False
        assert body["error"]["code"] == "pairing_declined"
        assert calls == []


def test_json_failure_is_nonzero_and_does_not_serialize_exception_secrets(pairing, monkeypatch):
    path, _, _, _ = pairing

    def fail(*_):
        raise peer.local_peer_service.LocalPeerError("private-marker-do-not-export")

    monkeypatch.setattr(peer.local_peer_service, "initiate_pairing", fail)
    result = CliRunner().invoke(peer.peer, ["pair", TARGET, "--project", str(path), "--json"])
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["schema"] == "cao.peer.error.v1" and body["success"] is False
    assert body["error"]["code"] == "pairing_failed"
    assert "private-marker-do-not-export" not in result.output


def test_json_accept_eof_does_not_call_authorization(pairing):
    _, _, _, calls = pairing
    result = CliRunner().invoke(peer.peer, ["accept", CHALLENGE, "--json"], input="")
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["success"] is False and body["error"]["code"] == "pairing_aborted"
    assert calls == []


def test_pair_human_default_is_preserved(pairing):
    path, _, _, _ = pairing
    result = CliRunner().invoke(peer.peer, ["pair", TARGET, "--project", str(path)])
    assert result.exit_code == 0
    assert "Código temporal (válido 5 minutos)" in result.stdout


def test_invalid_service_output_becomes_generic_json_error(pairing, monkeypatch):
    path, challenge, _, _ = pairing
    invalid = {**challenge, "code": "private-marker-do-not-export"}
    monkeypatch.setattr(peer.local_peer_service, "initiate_pairing", lambda *_: invalid)
    result = CliRunner().invoke(peer.peer, ["pair", TARGET, "--project", str(path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "pairing_result_invalid"
    assert invalid["code"] not in result.output
