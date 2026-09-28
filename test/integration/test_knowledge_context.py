"""One authority across service, actual API and MCP's HTTP transport boundary."""

import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest
import requests

from cli_agent_orchestrator.api.main import app
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.mcp_server import knowledge_tools, utils
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services import knowledge_revisions
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.memory_service import MemoryService
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority


@pytest.mark.asyncio
async def test_same_approved_context_and_revocation_across_service_http_mcp(tmp_path, monkeypatch):
    monkeypatch.delenv("CAO_AUTH_JWKS_URI", raising=False)
    monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
    repository = WorkRepository(tmp_path / "knowledge.sqlite3")
    repository.initialize()
    monkeypatch.setattr("cli_agent_orchestrator.constants.DATABASE_FILE", repository.path)
    actor = auth.local_operator_principal()
    job = repository.create_job(
        project_id="project",
        principal_id=actor.id,
        allowed_providers=["mock_cli"],
        grant_id="root",
    )
    authority = WorkAuthority(repository)
    grant = authority.issue_root(
        actor,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"knowledge.read", "knowledge.propose", "knowledge.review", "knowledge.tombstone"}
        ),
        expires_at=time.time() + 300,
    )
    work = repository.admit_work(
        job_id=job["id"],
        operation_kind="test",
        idempotency_key="test",
        request_hash="a" * 64,
        contract_id="contract",
        snapshot_id=None,
        provider="mock_cli",
        actor_id=actor.id,
    )
    from cli_agent_orchestrator.services.step_output_store import ImmutableResultStore

    evidence = ImmutableResultStore(tmp_path / "artifacts").publish(
        b"reviewer evidence",
        lambda ref: repository.register_result(
            attempt_id=work["attempts"][0]["id"],
            generation=1,
            content_hash=ref.content_hash,
            immutable_location=ref.immutable_location,
            byte_length=ref.byte_length,
            validator_id="test",
            validation_evidence={"valid": True},
            actor_id=actor.id,
        ),
    )
    selectors = {"job_id": job["id"], "grant_id": grant.id, "grant_revision": 1}

    def client():
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
            base_url="http://127.0.0.1",
        )

    # Replace only network transport: MCP still crosses actual HTTP routing/auth,
    # policy, SQLite and serialization. No model/CLI or operator database involved.
    def get_json(path, **params):
        async def request():
            async with client() as http:
                response = await http.get(path, params=params)
            if response.status_code >= 400:
                error_response = requests.Response()
                error_response.status_code = response.status_code
                raise requests.HTTPError(response=error_response)
            return response.json()

        return asyncio.run(request())

    monkeypatch.setattr(utils, "get_json", get_json)
    service = MemoryService(knowledge_policy=KnowledgePolicy(repository, job["id"], grant.id, 1))
    arguments = ("project", "project", job["id"], grant.id, 1)
    async with client() as http:
        body = {
            "schema_version": 1,
            "expected_version": 0,
            "scope": "project",
            "scope_id": "project",
            "content": "Reviewed context",
            "confidence": 0.8,
            "fresh_until": time.time() + 300,
            "evidence_refs": [evidence["id"]],
        }
        assert (
            await http.post("/v1/knowledge/records/record/revisions", params=selectors, json=body)
        ).status_code == 201
        assert (await knowledge_tools.knowledge_instructions(*arguments))["instructions"] == []
        approved = await http.post(
            "/v1/knowledge/records/record/revisions/1/review",
            params=selectors,
            json={
                "schema_version": 1,
                "expected_version": 1,
                "decision": "approved",
                "examined_refs": [evidence["id"]],
            },
        )
        assert approved.status_code == 200, approved.text
        expected = [
            item.model_dump(mode="json")
            for item in service.instructions(
                actor,
                scope="project",
                scope_id="project",
            )
        ]
        result = await knowledge_tools.knowledge_instructions(*arguments)
        assert result == {"ok": True, "instructions": expected}
        assert len(expected) == 1 and expected[0]["content"] == "Reviewed context"

        # Advance only knowledge freshness, not JWT/grant/process clocks.
        with monkeypatch.context() as expired:
            expired.setattr(
                knowledge_revisions, "time", SimpleNamespace(time=lambda: body["fresh_until"] + 1)
            )
            assert (await knowledge_tools.knowledge_instructions(*arguments))["instructions"] == []
            assert service.instructions(actor, scope="project", scope_id="project") == ()

        # A new head cannot inherit the old approval, including after reopening.
        body.update(expected_version=2, content="Unreviewed replacement")
        assert (
            await http.post("/v1/knowledge/records/record/revisions", params=selectors, json=body)
        ).status_code == 201
        assert (await knowledge_tools.knowledge_instructions(*arguments))["instructions"] == []
        retired = await http.post(
            "/v1/knowledge/records/record/revisions/2/tombstone",
            params=selectors,
            json={"schema_version": 1, "expected_version": 3},
        )
        assert retired.status_code == 200, retired.text
        withdrawn = await knowledge_tools.knowledge_read("record", job["id"], grant.id, 1)
        assert withdrawn["knowledge"]["content"] is None
        assert withdrawn["knowledge"]["tombstone"] is True
        authority.revoke(actor, grant_id=grant.id, expected_grant_revision=1, reason="test")
        with pytest.raises(PermissionError):
            service.instructions(actor, scope="project", scope_id="project")
        assert (await http.get("/v1/knowledge/records/record", params=selectors)).status_code == 403
        assert (await knowledge_tools.knowledge_read("record", job["id"], grant.id, 1))[
            "code"
        ] == "knowledge_forbidden"

    with repository.connection() as connection:
        rows = connection.execute(
            "SELECT action,outcome FROM work_knowledge_access_audit ORDER BY id"
        ).fetchall()
    assert ("read", "allowed") in [tuple(row) for row in rows]
    assert [tuple(row) for row in rows[-3:]] == [
        ("instructions", "denied"),
        ("read", "denied"),
        ("read", "denied"),
    ]
