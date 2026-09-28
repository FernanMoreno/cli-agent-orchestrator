"""HTTP knowledge authority uses real SQLite, durable grants and ASGI authentication."""

import hashlib
import asyncio
import importlib
import importlib.util
import time
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.step_output_store import ImmutableResultStore
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority


@pytest.fixture
def authority(tmp_path, monkeypatch):
    name = "cli_agent_orchestrator.api.knowledge_routes"
    routes = importlib.import_module(name) if importlib.util.find_spec(name) else None
    monkeypatch.delenv("CAO_AUTH_JWKS_URI", raising=False)
    monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
    actor = auth.local_operator_principal()
    repository = WorkRepository(tmp_path / "knowledge.db")
    repository.initialize()
    job = repository.create_job(
        project_id="project", principal_id=actor.id, allowed_providers=["codex"], grant_id="root"
    )
    control = WorkAuthority(repository)
    grant = control.issue_root(
        actor,
        job_id=job["id"],
        providers={"codex"},
        permissions=Permissions(
            tools={"knowledge.read", "knowledge.propose", "knowledge.review", "knowledge.tombstone"}
        ),
        expires_at=time.time() + 300,
    )
    work = repository.admit_work(
        job_id=job["id"],
        operation_kind="test",
        idempotency_key="one",
        request_hash=hashlib.sha256(b"request").hexdigest(),
        contract_id="contract",
        snapshot_id=None,
        provider="codex",
        actor_id=actor.id,
    )
    evidence = ImmutableResultStore(tmp_path / "artifacts").publish(
        b"reviewed",
        lambda ref: repository.register_result(
            attempt_id=work["attempts"][0]["id"],
            generation=1,
            content_hash=ref.content_hash,
            immutable_location=ref.immutable_location,
            byte_length=ref.byte_length,
            validator_id="test",
            validation_evidence={"verified": True},
            actor_id=actor.id,
        ),
    )
    app = FastAPI()
    if routes is not None:
        app.include_router(routes.router)
        app.dependency_overrides[routes.repository] = lambda: repository
    selectors = {"job_id": job["id"], "grant_id": grant.id, "grant_revision": 1}
    body = {
        "schema_version": 1,
        "scope": "project",
        "scope_id": "project",
        "expected_version": 0,
        "content": "safe knowledge",
        "evidence_refs": [evidence["id"]],
        "confidence": 0.8,
        "fresh_until": time.time() + 300,
    }
    return app, repository, actor, control, grant, selectors, body, evidence


def client(context, *, peer="127.0.0.1"):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=context[0], client=(peer, 1234)),
        base_url="http://127.0.0.1",
    )


@pytest.mark.asyncio
async def test_http_proposal_review_history_and_tombstone_are_authoritative(authority):
    _, _, actor, _, _, selectors, body, evidence = authority
    async with client(authority) as http:
        response = await http.post(
            "/v1/knowledge/records/record/revisions", params=selectors, json=body
        )
        assert response.status_code == 201, response.text
        assert response.json()["producer_principal_id"] == actor.id
        assert response.json()["decision"] == "proposed"
        context = {**selectors, "scope": "project", "scope_id": "project"}
        assert (await http.get("/v1/knowledge/instructions", params=context)).json() == []
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
        assert len((await http.get("/v1/knowledge/instructions", params=context)).json()) == 1
        historical = await http.get("/v1/knowledge/records/record/revisions/1", params=selectors)
        assert historical.json()["decision"] == "approved"
        retired = await http.post(
            "/v1/knowledge/records/record/revisions/1/tombstone",
            params=selectors,
            json={"schema_version": 1, "expected_version": 2},
        )
        assert retired.status_code == 200 and retired.json()["content"] is None
        assert (await http.get("/v1/knowledge/instructions", params=context)).json() == []


@pytest.mark.asyncio
async def test_stale_version_is_409_and_revocation_denies_reads_and_writes(authority):
    _, _, actor, control, grant, selectors, body, _ = authority
    async with client(authority) as http:
        assert (
            await http.post("/v1/knowledge/records/record/revisions", params=selectors, json=body)
        ).status_code == 201
        stale = await http.post(
            "/v1/knowledge/records/record/revisions", params=selectors, json=body
        )
        assert (
            stale.status_code == 409
            and stale.json()["detail"]["code"] == "knowledge_revision_conflict"
        )
        control.revoke(actor, grant_id=grant.id, expected_grant_revision=1, reason="test")
        assert (await http.get("/v1/knowledge/records/record", params=selectors)).status_code == 403
        assert (
            await http.post("/v1/knowledge/records/other/revisions", params=selectors, json=body)
        ).status_code == 403


@pytest.mark.asyncio
async def test_http_review_rejects_a_stale_expected_version(authority):
    """A review must CAS against the version produced by its proposal."""
    _, _, _, _, _, selectors, body, evidence = authority
    async with client(authority) as http:
        created = await http.post(
            "/v1/knowledge/records/record/revisions", params=selectors, json=body
        )
        assert created.status_code == 201, created.text
        stale_review = await http.post(
            "/v1/knowledge/records/record/revisions/1/review",
            params=selectors,
            json={
                "schema_version": 1,
                "expected_version": 0,
                "decision": "approved",
                "examined_refs": [evidence["id"]],
            },
        )
    assert stale_review.status_code == 409
    assert stale_review.json()["detail"]["code"] == "knowledge_revision_conflict"


@pytest.mark.asyncio
async def test_http_authority_contract_rejects_stale_cas_hides_tombstone_and_revoked_cursor(
    authority,
):
    """Would fail if CAS, tombstone redaction, or cursor reauthorization regressed."""
    _, _, actor, control, grant, selectors, body, evidence = authority
    recovery = {
        **selectors,
        "schema_version": 1,
        "scope": "project",
        "scope_id": "project",
        "limit": 1,
    }
    async with client(authority) as http:
        created = await http.post(
            "/v1/knowledge/records/a/revisions", params=selectors, json=body
        )
        assert created.status_code == 201, created.text
        stale_review = await http.post(
            "/v1/knowledge/records/a/revisions/1/review",
            params=selectors,
            json={
                "schema_version": 1,
                "expected_version": 0,
                "decision": "approved",
                "examined_refs": [evidence["id"]],
            },
        )
        assert stale_review.status_code == 409
        assert stale_review.json()["detail"]["code"] == "knowledge_revision_conflict"
        approved = await http.post(
            "/v1/knowledge/records/a/revisions/1/review",
            params=selectors,
            json={
                "schema_version": 1,
                "expected_version": 1,
                "decision": "approved",
                "examined_refs": [evidence["id"]],
            },
        )
        assert approved.status_code == 200, approved.text
        tombstoned = await http.post(
            "/v1/knowledge/records/a/revisions/1/tombstone",
            params=selectors,
            json={"schema_version": 1, "expected_version": 2},
        )
        assert tombstoned.status_code == 200, tombstoned.text
        assert tombstoned.json()["content"] is None
        later = await http.post(
            "/v1/knowledge/records/b/revisions",
            params=selectors,
            json={**body, "content": "later knowledge"},
        )
        assert later.status_code == 201, later.text
        first_page = await http.get("/v1/knowledge/recovery", params=recovery)
        assert first_page.status_code == 200, first_page.text
        page = first_page.json()
        assert [(row["record_id"], row["content"]) for row in page["revisions"]] == [
            ("a", None)
        ]
        cursor = page["next_cursor"]
        assert isinstance(cursor, str) and cursor.startswith("kcr1_")
        control.revoke(actor, grant_id=grant.id, expected_grant_revision=1, reason="test")
        continuation = await http.get(
            "/v1/knowledge/recovery", params={**recovery, "cursor": cursor}
        )
    assert continuation.status_code == 403
    assert continuation.json()["detail"]["code"] == "knowledge_forbidden"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"schema_version": "1"},
        {"schema_version": 2},
        {"producer_principal_id": "spoofed"},
        {"caller_id": "spoofed"},
        {"scope": "AKIAIOSFODNN7EXAMPLE"},
        {"content": {"secret": "AKIAIOSFODNN7EXAMPLE"}},
    ],
)
async def test_closed_dto_rejects_spoofing_and_never_echoes_invalid_secrets(authority, change):
    body = {**authority[6], **change}
    async with client(authority) as http:
        response = await http.post(
            "/v1/knowledge/records/record/revisions", params=authority[5], json=body
        )
    assert response.status_code == 422
    assert "AKIAIOSFODNN7EXAMPLE" not in response.text
    with authority[1].connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_knowledge_records").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_nonloopback_cannot_claim_local_operator(authority):
    async with client(authority, peer="203.0.113.9") as http:
        response = await http.post(
            "/v1/knowledge/records/record/revisions",
            params=authority[5],
            json=authority[6],
            headers={"X-Forwarded-For": "127.0.0.1"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_configured_auth_missing_bearer_does_not_become_local_operator(
    authority, monkeypatch
):
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    async with client(authority) as http:
        response = await http.post(
            "/v1/knowledge/records/record/revisions", params=authority[5], json=authority[6]
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_wrong_job_or_scope_is_denied_by_real_grant_policy(authority):
    async with client(authority) as http:
        response = await http.post(
            "/v1/knowledge/records/record/revisions",
            params={**authority[5], "job_id": "wrong"},
            json=authority[6],
        )
        assert response.status_code == 403
        response = await http.post(
            "/v1/knowledge/records/record/revisions",
            params=authority[5],
            json={**authority[6], "scope_id": "other"},
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_commit_failure_returns_503_and_rolls_back_without_echo(authority):
    with authority[1].transaction() as connection:
        connection.execute(
            "CREATE TRIGGER refuse_knowledge BEFORE INSERT ON work_knowledge_events BEGIN SELECT RAISE(ABORT,'AKIAIOSFODNN7EXAMPLE'); END"
        )
    async with client(authority) as http:
        response = await http.post(
            "/v1/knowledge/records/record/revisions", params=authority[5], json=authority[6]
        )
    assert response.status_code == 503 and "AKIAIOSFODNN7EXAMPLE" not in response.text
    with authority[1].connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_knowledge_records").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_secret_content_is_redacted_before_http_or_durable_response(authority):
    async with client(authority) as http:
        response = await http.post(
            "/v1/knowledge/records/record/revisions",
            params=authority[5],
            json={**authority[6], "content": "AKIAIOSFODNN7EXAMPLE"},
        )
    assert response.status_code == 201 and response.json()["redacted"]
    assert "AKIAIOSFODNN7EXAMPLE" not in response.text
    with authority[1].connection() as connection:
        assert "AKIAIOSFODNN7EXAMPLE" not in "\n".join(connection.iterdump())


@pytest.mark.asyncio
async def test_signed_jwt_identity_is_checked_against_the_selected_grant(authority, monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setenv("AUTH0_DOMAIN", "issuer.example")
    monkeypatch.setenv("CAO_AUTH_AUDIENCE", "cao")
    monkeypatch.delenv("CAO_AUTH_ISSUER", raising=False)
    signing_client = SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private.public_key())
    )
    monkeypatch.setattr(auth.get_jwks_cache(), "get_client", lambda uri: signing_client)
    claims = {
        "iss": "https://issuer.example/",
        "sub": "owner",
        "aud": "cao",
        "exp": time.time() + 300,
        "scope": "cao:admin",
    }
    token = jwt.encode(claims, private, algorithm="RS256")
    actor = auth.principal_from_token(token)
    repository, control = authority[1], authority[3]
    job = repository.create_job(
        project_id="project",
        principal_id=actor.id,
        allowed_providers=["codex"],
        grant_id="jwt-root",
    )
    grant = control.issue_root(
        actor,
        job_id=job["id"],
        providers={"codex"},
        permissions=Permissions(tools={"knowledge.read", "knowledge.propose"}),
        expires_at=time.time() + 300,
    )
    selectors = {"job_id": job["id"], "grant_id": grant.id, "grant_revision": 1}
    async with client(authority, peer="203.0.113.9") as http:
        response = await http.post(
            "/v1/knowledge/records/jwt-record/revisions",
            params=selectors,
            json=authority[6],
            headers={"Authorization": "Bearer " + token, "caller_id": "spoofed"},
        )
        assert response.status_code == 201, response.text
        assert response.json()["producer_principal_id"] == actor.id
        other = jwt.encode({**claims, "sub": "other"}, private, algorithm="RS256")
        denied = await http.get(
            "/v1/knowledge/records/jwt-record",
            params=selectors,
            headers={"Authorization": "Bearer " + other},
        )
        assert denied.status_code == 403
        expired = jwt.encode({**claims, "exp": 1}, private, algorithm="RS256")
        assert (
            await http.get(
                "/v1/knowledge/records/jwt-record",
                params=selectors,
                headers={"Authorization": "Bearer " + expired},
            )
        ).status_code == 401


@pytest.mark.asyncio
async def test_http_concurrent_cas_has_one_commit_and_one_conflict(authority):
    async with client(authority) as http:
        responses = await asyncio.gather(
            *(
                http.post(
                    "/v1/knowledge/records/record/revisions",
                    params=authority[5],
                    json={**authority[6], "content": value},
                )
                for value in ("first", "second")
            )
        )
    assert sorted(response.status_code for response in responses) == [201, 409]
    with authority[1].connection() as connection:
        assert (
            connection.execute("SELECT count(*) FROM work_knowledge_revisions").fetchone()[0] == 1
        )


@pytest.mark.asyncio
async def test_malformed_json_and_oversized_stream_are_bounded_and_sanitized(authority):
    async with client(authority) as http:
        malformed = await http.post(
            "/v1/knowledge/records/record/revisions",
            params=authority[5],
            content=b'{"content":"AKIAIOSFODNN7EXAMPLE",',
            headers={"Content-Type": "application/json"},
        )
        assert malformed.status_code == 422 and "AKIAIOSFODNN7EXAMPLE" not in malformed.text

        async def oversized():
            for _ in range(18):
                yield b"x" * 65536

        response = await http.post(
            "/v1/knowledge/records/record/revisions",
            params=authority[5],
            content=oversized(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413


@pytest.mark.asyncio
async def test_sanitization_is_router_local_not_a_global_validation_change(authority):
    @authority[0].get("/legacy-validation")
    def legacy(count: int):
        return count

    async with client(authority) as http:
        new = await http.get(
            "/v1/knowledge/instructions",
            params={**authority[5], "scope": "AKIAIOSFODNN7EXAMPLE", "scope_id": "project"},
        )
        assert new.status_code == 422 and "AKIAIOSFODNN7EXAMPLE" not in new.text
        unchanged = await http.get("/legacy-validation", params={"count": "legacy-invalid"})
        assert unchanged.status_code == 422 and isinstance(unchanged.json()["detail"], list)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["expected_version", "grant_revision", "revision"])
async def test_sqlite_integer_bounds_are_rejected_at_http_boundary(authority, field):
    selectors, body = dict(authority[5]), dict(authority[6])
    if field == "expected_version":
        body[field] = 2**63
    if field == "grant_revision":
        selectors[field] = 2**63
    async with client(authority) as http:
        if field == "revision":
            assert (
                await http.post(
                    "/v1/knowledge/records/record/revisions", params=selectors, json=body
                )
            ).status_code == 201
            response = await http.get(
                f"/v1/knowledge/records/record/revisions/{2**63}", params=selectors
            )
        else:
            response = await http.post(
                "/v1/knowledge/records/record/revisions", params=selectors, json=body
            )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_real_application_includes_the_authoritative_router(authority, monkeypatch):
    from cli_agent_orchestrator.api import knowledge_routes
    from cli_agent_orchestrator.api.main import app

    monkeypatch.setitem(app.dependency_overrides, knowledge_routes.repository, lambda: authority[1])
    async with client((app, *authority[1:])) as http:
        response = await http.post(
            "/v1/knowledge/records/record/revisions", params=authority[5], json=authority[6]
        )
        assert response.status_code == 201, response.text
        read = await http.get("/v1/knowledge/records/record", params=authority[5])
        assert read.status_code == 200 and read.json()["decision"] == "proposed"
