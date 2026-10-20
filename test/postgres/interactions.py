"""Shared native PostgreSQL setup for interaction service regression tests."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import pytest


@dataclass(frozen=True)
class InteractionStore:
    target: object
    work_repository: object
    repository: object
    service: object


@pytest.fixture
def postgres_interaction_store(postgres_targets):
    """Bootstrap the full main scope and bind interactions to Work's PG store."""
    from cli_agent_orchestrator.clients.interaction_repository import InteractionRepository
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.services.interactions.service import InteractionService

    target = postgres_targets.main
    bootstrap_scope(target)
    work_repository = WorkRepository(target)
    repository = InteractionRepository(target, work_repository=work_repository)
    service = InteractionService(repository, work_repository=work_repository)
    service.initialize()
    return InteractionStore(target, work_repository, repository, service)


def register_interaction_source(
    store: InteractionStore,
    *,
    owner=None,
    provider: str = "codex",
    allowed_tools: tuple[str, ...] = ("@cao-mcp-server",),
    terminal_id: str | None = None,
    session_name: str | None = None,
    incarnation_id: str | None = None,
    execution_mode: str = "local",
):
    """Create a core terminal row and register its source on a PG-backed store."""
    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.security import auth

    suffix = uuid4().hex[:12]
    terminal_id = terminal_id or f"ix{suffix}"
    session_name = session_name or f"interaction-test-{suffix}"
    incarnation_id = incarnation_id or f"inc-{suffix}"
    owner = owner or auth._verified_principal(
        "https://interaction.postgres.test",
        f"owner-{suffix}",
        [auth.SCOPE_READ, auth.SCOPE_WRITE, auth.SCOPE_ADMIN],
        "jwt",
    )
    database.create_terminal(
        terminal_id,
        session_name,
        "worker",
        provider,
        allowed_tools=list(allowed_tools),
        session_incarnation_id=incarnation_id,
        new_session_incarnation=True,
    )
    source = store.service.register_source(
        terminal_id=terminal_id,
        incarnation_id=incarnation_id,
        session_name=session_name,
        provider=provider,
        execution_mode=execution_mode,
        principal=owner,
    )
    return SimpleNamespace(
        owner=owner,
        terminal_id=terminal_id,
        session_name=session_name,
        incarnation_id=incarnation_id,
        source=source,
        token=source.token,
        store=store,
        service=store.service,
    )
