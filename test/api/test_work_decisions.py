"""Durable decision routes use the authenticated principal, never request authority."""

from __future__ import annotations

import time
from test.services.test_work_decisions import EFFECT, EVIDENCE, _decision_context

import httpx
import pytest
from fastapi import FastAPI

from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_authority import WorkAuthority
from cli_agent_orchestrator.services.work_decisions import WorkDecisions


@pytest.fixture
def decision_api(tmp_path):
    """A real temporary work store behind just the public route boundary."""
    from cli_agent_orchestrator.api import work_routes

    context = _decision_context(tmp_path)
    app = FastAPI()
    app.include_router(work_routes.router)
    app.dependency_overrides[work_routes.repository] = lambda: context.repository
    app.dependency_overrides[auth.get_current_principal] = lambda: context.actor
    return app, context


def _body(context, **changes):
    attempt = context.original
    body = {
        "attempt_id": attempt.attempt_id,
        "generation": attempt.generation,
        "idempotency_key": "api-decision-v1",
        "evidence_refs": list(EVIDENCE),
        "action": "resume",
        "reason": "operator reviewed durable evidence",
        "authorized_effects": [EFFECT],
    }
    body.update(changes)
    return body


def _decision_count(context):
    with context.repository.read_snapshot() as connection:
        return connection.execute("SELECT count(*) FROM work_human_decisions").fetchone()[0]


@pytest.mark.asyncio
async def test_spoofed_or_malformed_api_decision_never_writes(decision_api):
    app, context = decision_api
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://127.0.0.1",
    ) as client:
        spoofed = await client.post(
            f"/work-items/{context.original.work_item_id}/decisions",
            json=_body(context, actor_id="forged-principal"),
        )
        malformed = await client.post(
            f"/work-items/{context.original.work_item_id}/decisions",
            json=_body(context, generation=True),
        )

    assert spoofed.status_code == malformed.status_code == 422
    assert _decision_count(context) == 0


@pytest.mark.asyncio
async def test_decision_mutations_require_write_scope(decision_api, monkeypatch):
    app, context = decision_api
    monkeypatch.setenv("CAO_AUTH_JWKS_URI", "https://idp.example/jwks")
    app.dependency_overrides[auth.get_current_scopes] = lambda: [auth.SCOPE_READ]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://127.0.0.1",
    ) as client:
        denied = await client.post(
            f"/work-items/{context.original.work_item_id}/decisions", json=_body(context)
        )
        assert denied.status_code == 403
        assert _decision_count(context) == 0

        app.dependency_overrides[auth.get_current_scopes] = lambda: [auth.SCOPE_WRITE]
        created = await client.post(
            f"/work-items/{context.original.work_item_id}/decisions", json=_body(context)
        )
        assert created.status_code == 200, created.text

        app.dependency_overrides[auth.get_current_scopes] = lambda: [auth.SCOPE_READ]
        revoke_denied = await client.post(
            f"/work-decisions/{created.json()['id']}/revoke",
            json={"reason": "operator withdrew approval"},
        )

    assert revoke_denied.status_code == 403
    assert WorkDecisions(context.repository).get(created.json()["id"]).revoked_at is None


@pytest.mark.asyncio
async def test_api_decision_is_idempotent_and_divergence_has_no_second_effect(decision_api):
    app, context = decision_api
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://127.0.0.1",
    ) as client:
        first = await client.post(
            f"/work-items/{context.original.work_item_id}/decisions", json=_body(context)
        )
        replay = await client.post(
            f"/work-items/{context.original.work_item_id}/decisions", json=_body(context)
        )
        divergent = await client.post(
            f"/work-items/{context.original.work_item_id}/decisions",
            json=_body(context, reason="different evidence"),
        )

    assert first.status_code == replay.status_code == 200
    assert first.json()["id"] == replay.json()["id"]
    assert first.json()["actor_id"] == context.actor.id
    assert divergent.status_code == 409
    assert _decision_count(context) == 1


@pytest.mark.asyncio
async def test_wrong_effective_principal_or_stale_binding_never_writes(decision_api):
    app, context = decision_api
    outsider = auth._verified_principal("https://issuer.test", "outsider", [], "jwt")
    app.dependency_overrides[auth.get_current_principal] = lambda: outsider
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://127.0.0.1",
    ) as client:
        wrong_principal = await client.post(
            f"/work-items/{context.original.work_item_id}/decisions", json=_body(context)
        )
        app.dependency_overrides[auth.get_current_principal] = lambda: context.actor
        stale = await client.post("/work-items/not-this-work/decisions", json=_body(context))

    assert wrong_principal.status_code == 403
    assert stale.status_code == 409
    assert _decision_count(context) == 0


@pytest.mark.asyncio
async def test_original_actor_can_revoke_after_live_grant_expires(decision_api):
    app, context = decision_api
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://127.0.0.1",
    ) as client:
        created = await client.post(
            f"/work-items/{context.original.work_item_id}/decisions", json=_body(context)
        )
        assert created.status_code == 200, created.text
        WorkAuthority(context.repository).revoke(
            context.actor,
            grant_id=context.grant.id,
            expected_grant_revision=context.grant.revision,
            reason="future authority withdrawn",
        )
        revoked = await client.post(
            f"/work-decisions/{created.json()['id']}/revoke",
            json={"reason": "operator withdrew historical approval"},
        )

    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["decision_id"] == created.json()["id"]
