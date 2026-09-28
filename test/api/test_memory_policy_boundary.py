"""Legacy memory is local operator compatibility, never a project ACL bypass."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from cli_agent_orchestrator.api import main
from cli_agent_orchestrator.security import auth


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,body",
    [
        (
            "/internal/memory/recall",
            {"scope": "project", "terminal_context": {"cwd": "/other-project"}},
        ),
        ("/internal/memory/context", {"terminal_context": {"cwd": "/other-project"}}),
    ],
)
async def test_authenticated_remote_cannot_self_attribute_legacy_project(monkeypatch, path, body):
    principal = auth._verified_principal("https://issuer.test", "worker", [auth.SCOPE_ADMIN], "jwt")
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[auth.get_current_principal] = lambda: principal
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: False)
    monkeypatch.setattr(main, "_require_memory_enabled", lambda: None)
    service = SimpleNamespace(
        recall=AsyncMock(return_value=[]), get_memory_context=Mock(return_value="private")
    )
    monkeypatch.setattr(main, "_get_memory_service", lambda: service)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app, client=("127.0.0.1", 1234)),
            base_url="http://127.0.0.1",
        ) as client:
            response = await client.post(path, json=body)
        assert response.status_code == 403
        service.recall.assert_not_called()
        service.get_memory_context.assert_not_called()
        assert "other-project" not in response.text
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path", [("GET", "/graph/memory"), ("POST", "/graph/memory/export")]
)
async def test_memory_graph_cannot_bypass_legacy_boundary_with_cached_projection(
    monkeypatch, method, path
):
    from cli_agent_orchestrator.graph.models import GraphView

    actor = auth._verified_principal("https://issuer.test", "worker", [auth.SCOPE_ADMIN], "jwt")
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[auth.get_current_scopes] = lambda: [auth.SCOPE_ADMIN]
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(auth, "principal_from_token", lambda token: actor)
    project = AsyncMock(return_value=GraphView(nodes=[], edges=[]))
    monkeypatch.setattr(main, "_project_graph_with_timeout", project)
    monkeypatch.setattr(main, "get_provider", lambda provider: object())
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app, client=("127.0.0.1", 1234)),
            base_url="http://127.0.0.1",
        ) as client:
            response = await client.request(
                method,
                path + "?scope=project&scope_id=other",
                headers={"Authorization": "Bearer verified"},
            )
        assert response.status_code == 403
        project.assert_not_called()
        from cli_agent_orchestrator.api.knowledge_routes import repository
        with repository().connection() as connection:
            audit = [dict(row) for row in connection.execute('SELECT * FROM work_memory_access_audit')]
        assert len(audit) == 1
        assert audit[0]['phase'] == 'denied'
        assert audit[0]['action'] == ('export' if method == 'POST' else 'graph')
        assert audit[0]['actor_id'] == actor.id
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


@pytest.mark.asyncio
@pytest.mark.parametrize('audit_fails', [False, True])
async def test_remote_store_denial_is_durable_or_unavailable(monkeypatch, tmp_path, audit_fails):
    from cli_agent_orchestrator.api import knowledge_routes
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    store = WorkRepository(tmp_path / 'denial.sqlite')
    store.initialize()
    if audit_fails:
        with store.connection() as connection:
            connection.execute("CREATE TRIGGER reject_denied BEFORE INSERT ON work_memory_access_audit BEGIN SELECT RAISE(ABORT,'PRIVATE DATABASE ERROR'); END")
    actor = auth._verified_principal('issuer', 'worker', [auth.SCOPE_ADMIN], 'jwt')
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[auth.get_current_principal] = lambda: actor
    main.app.dependency_overrides[knowledge_routes.repository] = lambda: store
    monkeypatch.setattr(auth, 'is_auth_enabled', lambda: False)
    service = SimpleNamespace(store=Mock())
    monkeypatch.setattr(main, '_get_memory_service', lambda: service)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, client=('127.0.0.1', 1234)), base_url='http://127.0.0.1') as client:
            response = await client.post('/internal/memory/store?secret=PRIVATE_QUERY', json={'content': 'PRIVATE BODY', 'key': 'PRIVATE KEY'})
        assert response.status_code == (503 if audit_fails else 403)
        assert 'PRIVATE' not in response.text
        service.store.assert_not_called()
        with store.connection() as connection:
            audit = [dict(row) for row in connection.execute('SELECT * FROM work_memory_access_audit')]
        if audit_fails:
            assert audit == []
        else:
            assert len(audit) == 1
            assert audit[0]['phase'] == 'denied'
            assert audit[0]['action'] == 'store'
            assert audit[0]['actor_id'] == actor.id
            assert 'PRIVATE' not in str(audit)
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)
