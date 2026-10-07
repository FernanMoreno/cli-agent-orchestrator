"""Operator peer CLI contracts exercise real grants and project write leases."""

import json
from test.services.test_local_peer_lifecycle_contracts import (
    approve_candidate,
    candidate_payload,
    grant,
)
from test.services.test_local_peer_lifecycle_contracts import peers as peers
from uuid import uuid4

import pytest
import requests
from click.testing import CliRunner

from cli_agent_orchestrator.cli.commands.peer import peer
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.services import local_peer_registry as registry
from cli_agent_orchestrator.services import local_peer_service as service


def test_cli_list_and_pair_render_current_identity_without_granting_authority(peers):
    runner = CliRunner()
    listed = runner.invoke(peer, ["list", "--project", str(peers.project), "--json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output)["instances"][0]["instance_id"] == peers.peer.instance_id
    assert json.loads(listed.output)["grants"] == []
    paired = runner.invoke(peer, ["pair", peers.peer.instance_id, "--project", str(peers.project)])
    assert paired.exit_code == 0, paired.output
    assert "Código temporal" in paired.output
    assert peers.invitation["challenge_id"] in paired.output
    assert grant(peers) is None
    with database.SessionLocal() as session:
        row = session.get(database.LocalPeerChallengeModel, peers.invitation["challenge_id"])
        assert row.consumed_at is None


@pytest.mark.parametrize("confirmation", ["n", "y"])
def test_cli_accept_requires_confirmation_and_one_use_secret(peers, confirmation):
    payload = candidate_payload(peers)
    service.receive_pairing_invitation(payload)
    result = CliRunner().invoke(
        peer,
        ["accept", payload["challenge_id"]],
        input=confirmation + "\n" + payload["code"] + "\n",
    )
    assert payload["code"] not in result.output
    if confirmation == "n":
        assert result.exit_code == 1 and "no se creó permiso" in result.output
        assert grant(peers) is None
    else:
        assert result.exit_code == 0 and "Emparejado" in result.output
        assert grant(peers).peer_public_key == peers.peer.public_key
        assert json.loads(grant(peers).scopes_json) == ["peer:revoke", "session:read"]


def test_cli_accept_wrong_secret_and_unknown_challenge_do_not_create_grant(peers):
    payload = candidate_payload(peers)
    service.receive_pairing_invitation(payload)
    runner = CliRunner()
    wrong = runner.invoke(peer, ["accept", payload["challenge_id"]], input="y\nwrong-secret\n")
    assert wrong.exit_code == 1 and "code does not match" in wrong.output
    assert grant(peers) is None
    missing = runner.invoke(peer, ["accept", str(uuid4())])
    assert missing.exit_code == 1 and "not found" in missing.output
    assert grant(peers) is None


@pytest.mark.parametrize("remote", ["confirmed", "offline", "declined", "absent"])
def test_cli_revoke_preserves_local_denial_even_when_remote_is_offline(peers, monkeypatch, remote):
    if remote != "absent":
        approve_candidate(peers)
    if remote == "offline":

        def http(method, url, **kwargs):
            if method == "DELETE":
                raise requests.ConnectionError("offline")
            return peers.http(method, url, **kwargs)

        monkeypatch.setattr(service, "_loopback_request", http)
    result = CliRunner().invoke(
        peer,
        ["revoke", peers.peer.instance_id, "--project", str(peers.project)],
        input="n\n" if remote == "declined" else "y\n",
    )
    if remote == "declined":
        assert result.exit_code == 1 and "cancelada" in result.output
        assert grant(peers) is not None
    elif remote == "absent":
        assert result.exit_code == 0 and "No había" in result.output
    else:
        assert result.exit_code == 0, result.output
        assert grant(peers) is None
        assert grant(peers, include_revoked=True).revoked_at is not None
        expected = "ambos perfiles" if remote == "confirmed" else "no respondió"
        assert expected in result.output
        listing = CliRunner().invoke(peer, ["list", "--project", str(peers.project)])
        assert listing.exit_code == 0 and "revocado" in listing.output


def task(peers, *, state, result=None, target=None, terminal=None):
    task_id = str(uuid4())
    with database.SessionLocal() as session:
        session.add(
            database.LocalPeerTaskModel(
                task_id=task_id,
                source_instance_id=peers.current.instance_id,
                target_instance_id=target or peers.current.instance_id,
                requester_terminal_id="abcdef01",
                project_id=peers.binding["project_id"],
                operation_key=uuid4().hex,
                request_hash="a" * 64,
                use_worktree=False,
                state=state,
                terminal_id=terminal,
                result_json=json.dumps(result) if result else None,
            )
        )
        session.commit()
    return task_id


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_status_exposes_confirmed_terminal_receipt_without_rerunning_task(peers, as_json):
    task_id = task(
        peers,
        state="succeeded",
        target=peers.peer.instance_id,
        terminal="deadbeef",
        result={"state": "succeeded", "worker_stopped": True, "output": "Reviewed result"},
    )
    arguments = ["status", task_id] + (["--json"] if as_json else [])
    result = CliRunner().invoke(peer, arguments)
    assert result.exit_code == 0, result.output
    if as_json:
        receipt = json.loads(result.output)
        assert receipt["state"] == "succeeded" and receipt["result"]["output"] == "Reviewed result"
    else:
        assert "succeeded" in result.output and "Reviewed result" in result.output
        assert "Terminal: deadbeef" in result.output
    with database.SessionLocal() as session:
        assert session.get(database.LocalPeerTaskModel, task_id).state == "succeeded"
    resolved = CliRunner().invoke(peer, ["reconcile", task_id])
    assert resolved.exit_code == 0 and "ya está resuelta" in resolved.output


@pytest.mark.parametrize("confirmation", ["n", "y"])
def test_cli_reconciliation_requires_operator_confirmation_to_release_owned_lease(
    peers, confirmation
):
    task_id = task(peers, state="interrupted")
    registry.acquire_project_write_lease(
        project_id=peers.binding["project_id"],
        task_id=task_id,
        owner_instance_id=peers.current.instance_id,
    )
    result = CliRunner().invoke(peer, ["reconcile", task_id], input=confirmation + "\n")
    lease = registry.project_write_lease(peers.binding["project_id"])
    if confirmation == "n":
        assert result.exit_code == 1 and "lease sigue retenido" in result.output
        assert lease["state"] == "held"
    else:
        assert result.exit_code == 0 and "Lease liberado" in result.output
        assert lease["state"] == "released"
        with database.SessionLocal() as session:
            row = session.get(database.LocalPeerTaskModel, task_id)
            assert row.state == "reconcile"
            assert json.loads(row.result_json)["operator_confirmed_stopped"] is True


@pytest.mark.parametrize("command", ["status", "reconcile"])
def test_cli_missing_task_is_reported_as_an_operator_error(peers, command):
    result = CliRunner().invoke(peer, [command, str(uuid4())])
    assert result.exit_code == 1 and "not found" in result.output


def test_cli_unknown_pair_and_missing_project_are_errors(peers):
    runner = CliRunner()
    unknown = runner.invoke(peer, ["pair", str(uuid4()), "--project", str(peers.project)])
    assert unknown.exit_code == 1 and "not active locally" in unknown.output
    malformed = runner.invoke(peer, ["pair", peers.peer.instance_id])
    assert malformed.exit_code == 2 and "Missing option '--project'" in malformed.output
