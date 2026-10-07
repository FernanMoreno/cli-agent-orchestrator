"""Acceptance proofs must come from real routed messages and owned services."""

import importlib.util
from pathlib import Path

import pytest
import requests


def load():
    spec = importlib.util.spec_from_file_location(
        "collaboration_acceptance",
        Path(__file__).resolve().parents[2] / "scripts/agent_collaboration_acceptance.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_peer_proof_requires_both_real_senders_and_delivered_state():
    module = load()
    messages = [
        {
            "sender_id": "api",
            "receiver_id": "front",
            "status": "delivered",
            "message": "CONTRACT_PROBE:nonce",
        },
        {
            "sender_id": "front",
            "receiver_id": "api",
            "status": "delivered",
            "message": "CONTRACT_REPLY:nonce",
        },
    ]
    assert module.peer_messages_verified(messages, "api", "front", "nonce")
    messages[1]["status"] = "pending"
    assert not module.peer_messages_verified(messages, "api", "front", "nonce")
    messages[1].update(status="delivered", sender_id="operator")
    assert not module.peer_messages_verified(messages, "api", "front", "nonce")


def test_duplicate_peer_proof_is_failure():
    module = load()
    messages = [
        {
            "sender_id": "api",
            "receiver_id": "front",
            "status": "delivered",
            "message": "CONTRACT_PROBE:n",
        },
        {
            "sender_id": "front",
            "receiver_id": "api",
            "status": "delivered",
            "message": "CONTRACT_REPLY:n",
        },
    ]
    with pytest.raises(RuntimeError, match="duplicate"):
        module.peer_messages_verified(messages + messages, "api", "front", "n")


def test_external_service_is_real_and_owned_cleanup_is_idempotent(tmp_path):
    module = load()
    acceptance = module.Acceptance(tmp_path)
    with acceptance:
        assert requests.get(acceptance.external_url, timeout=2).json()["nonce"] == acceptance.nonce
        assert (tmp_path / "external-mcp.py").is_file()
        acceptance.stop_external()
        acceptance.stop_external()
        with pytest.raises(requests.ConnectionError):
            requests.get(acceptance.external_url, timeout=2)


@pytest.mark.asyncio
async def test_external_mcp_process_reports_available_then_stopped_service(tmp_path):
    import json

    from fastmcp import Client

    acceptance = load().Acceptance(tmp_path)
    with acceptance:
        async with Client({"mcpServers": {"local-probe": acceptance.mcp_config}}) as client:
            result = await client.call_tool("probe_service", {})
            assert json.loads(result.content[0].text) == {
                "state": "available",
                "nonce": acceptance.nonce,
            }
            acceptance.stop_external()
            result = await client.call_tool("probe_service", {})
            assert json.loads(result.content[0].text) == {
                "state": "unavailable",
                "error": "ConnectionError",
            }
        trace = [
            json.loads(line)
            for line in (tmp_path / "external-probes.jsonl").read_text().splitlines()
        ]
        assert [entry["state"] for entry in trace] == ["available", "unavailable"]


def test_acceptance_token_has_a_signature_verified_work_identity(tmp_path, monkeypatch):
    from cli_agent_orchestrator.security import auth

    with load().Acceptance(tmp_path) as acceptance:
        for name, value in acceptance.env.items():
            monkeypatch.setenv(name, value)
        auth.get_jwks_cache().clear()
        try:
            principal = auth.principal_from_token(acceptance.env["CAO_AUTH_LOCAL_TOKEN"])
            assert auth.is_verified_principal(principal)
            assert principal.subject == "collaboration-acceptance-" + acceptance.nonce
            assert principal.kind == "jwt"
            assert {auth.SCOPE_READ, auth.SCOPE_WRITE} <= principal.scopes
        finally:
            auth.get_jwks_cache().clear()


def test_recovery_message_proof_rejects_wrong_sender_and_duplicates():
    module = load()
    message = {
        "sender_id": "front",
        "receiver_id": "api",
        "status": "pending",
        "message": "RECOVERY_PENDING:n",
    }
    assert module.recovery_message([message], "front", "api", "n", "pending") == message
    assert (
        module.recovery_message(
            [{**message, "sender_id": "operator"}], "front", "api", "n", "pending"
        )
        is None
    )
    with pytest.raises(RuntimeError, match="duplicate"):
        module.recovery_message([message, message], "front", "api", "n", "pending")


def test_restart_identity_uses_durable_generation_not_process_turn_counter():
    module = load()
    before = {
        "id": "api",
        "turn_sequence": 8,
        "turn": {"generation": "a" * 32, "state": "reconcile"},
    }
    after = {
        "id": "api",
        "turn_sequence": 0,
        "turn": {"generation": "a" * 32, "state": "reconcile"},
    }
    assert module.same_durable_turn(before, after)
    assert not module.same_durable_turn(before, {**after, "turn": {"generation": "b" * 32}})
    assert not module.same_durable_turn(before, {**after, "id": "other"})
    assert not module.same_durable_turn({"id": "api"}, {"id": "api"})
