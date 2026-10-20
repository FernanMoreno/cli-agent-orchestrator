"""Durable assignment submission binds owner routing for observer-only sources."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from test.postgres.interactions import postgres_interaction_store
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.interactions.service import (
    InteractionForbidden,
    InteractionStale,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def observer_assignment_context(postgres_interaction_store):
    suffix = uuid4().hex[:8]
    parent_id = f"pa{suffix}"
    session_name = f"observer-assignment-{suffix}"
    incarnation_id = f"observer-assignment-inc-{suffix}"
    owner = auth._verified_principal(
        "https://observer-assignment.test",
        f"owner-{suffix}",
        [auth.SCOPE_READ, auth.SCOPE_WRITE, auth.SCOPE_ADMIN],
        "jwt",
    )
    database.create_terminal(
        parent_id,
        session_name,
        "parent-window",
        "codex",
        allowed_tools=["@cao-mcp-server"],
        session_incarnation_id=incarnation_id,
        new_session_incarnation=True,
    )
    service = postgres_interaction_store.service
    parent_source = service.register_source(
        terminal_id=parent_id,
        incarnation_id=incarnation_id,
        session_name=session_name,
        provider="codex",
        principal=owner,
    )
    return SimpleNamespace(
        owner=owner,
        service=service,
        parent_id=parent_id,
        parent_token=parent_source.token,
        session_name=session_name,
        incarnation_id=incarnation_id,
        suffix=suffix,
        target=postgres_interaction_store.target,
    )


def _admit_assignment(context, operation_key: str | None = None) -> str:
    from cli_agent_orchestrator.services.assignment_service import AssignmentRequest, _claim

    payload = AssignmentRequest(
        operation_key=operation_key or f"observer-assignment-{context.suffix}",
        agent_profile="observer-worker",
        message="Complete the assigned task.",
    )
    parent = database.get_terminal_metadata(context.parent_id)
    admitted, result = _claim(context.parent_id, payload, context.owner.id, parent)
    assert admitted is True
    assignment_id = result.get("assignment_id")
    assert isinstance(assignment_id, str)
    return assignment_id


def _submit_assignment(context, assignment_id: str, terminal_id: str) -> None:
    from cli_agent_orchestrator.services.assignment_service import _finish

    _finish(
        assignment_id,
        {
            "assignment_id": assignment_id,
            "terminal_id": terminal_id,
            "success": True,
            "state": "submitted",
            "message": "Assignment submitted for the observer-owner integration test.",
        },
    )


def _make_parent_observer(context) -> None:
    context.service.register_observer_source(
        terminal_id=context.parent_id,
        incarnation_id=context.incarnation_id,
        session_name=context.session_name,
        provider="codex",
        principal=context.owner,
    )
    assert (
        context.service.source_status(
            context.parent_id, context.incarnation_id
        ).capability_available
        is False
    )


def _create_child_terminal(context) -> tuple[str, str]:
    terminal_id = f"ch{uuid4().hex[:8]}"
    window_name = f"worker-{context.suffix}"
    database.create_terminal(
        terminal_id,
        context.session_name,
        window_name,
        "codex",
        allowed_tools=["@cao-mcp-server"],
        session_incarnation_id=context.incarnation_id,
    )
    return terminal_id, window_name


def _create_observer_child(context, assignment_id: str) -> tuple[str, str]:
    terminal_id, window_name = _create_child_terminal(context)
    context.service.register_observer_source(
        terminal_id=terminal_id,
        incarnation_id=context.incarnation_id,
        session_name=context.session_name,
        provider="codex",
        assignment_id=assignment_id,
    )
    return terminal_id, window_name


def _record_native_manual_notice(context, terminal_id: str, window_name: str) -> str:
    fingerprint = hashlib.sha256(f"dialog-{uuid4()}".encode()).hexdigest()
    receipt = context.service.observe_native(
        terminal_id,
        context.incarnation_id,
        1,
        {
            "observation": "manual_signal",
            "source_fingerprint": fingerprint,
            "dialog_fingerprint": fingerprint,
            "dialog_version": "codex-test-evidence-v1",
            "exact_target": (terminal_id, context.session_name, window_name),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "dialog_kind": "unknown",
        },
    )
    assert receipt.lifecycle == "manual_only"
    assert receipt.response_capability == "manual_only"
    assert isinstance(receipt.question_id, str)
    return receipt.question_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parent_observer", [False, True], ids=["explicit-parent", "observer-parent"]
)
async def test_submitted_assignment_binds_observer_owner_and_routes_prior_native_notice(
    observer_assignment_context, monkeypatch, parent_observer
):
    from cli_agent_orchestrator.services import assignment_service

    context = observer_assignment_context
    if parent_observer:
        _make_parent_observer(context)
    child: dict[str, str] = {}
    question_ids: list[str] = []
    create_errors: list[str] = []

    async def create_terminal(**kwargs):
        try:
            assert kwargs["assignment_id"]
            assert kwargs["caller_id"] == context.parent_id
            terminal_id, window_name = _create_observer_child(context, kwargs["assignment_id"])
            question_id = _record_native_manual_notice(context, terminal_id, window_name)
            child.update(terminal_id=terminal_id, window_name=window_name)
            question_ids.append(question_id)

            # The assignment is only admitted until terminal creation returns.
            # The parent must not gain access from caller_id or the pending ID.
            with pytest.raises(InteractionForbidden):
                context.service.get_detail(context.owner, question_id)
            metadata = context.service.list_metadata(context.owner, {"terminal_id": terminal_id})
            assert [item["question_id"] for item in metadata.items] == [question_id]
            assert "prompt" not in metadata.items[0]
            return SimpleNamespace(id=terminal_id, session_incarnation_id=context.incarnation_id)
        except Exception as error:
            create_errors.append(f"{type(error).__name__}: {error}")
            raise

    monkeypatch.setattr(
        assignment_service.terminal_service, "ensure_terminal_is_not_work_owned", lambda *_: None
    )
    monkeypatch.setattr(assignment_service, "load_agent_profile", lambda _name: object())
    monkeypatch.setattr(assignment_service, "resolve_provider", lambda *_args, **_kwargs: "codex")
    monkeypatch.setattr(assignment_service.terminal_service, "create_terminal", create_terminal)
    monkeypatch.setattr(
        assignment_service.terminal_service,
        "bind_validated_assignment_owner",
        context.service.bind_validated_assignment_owner,
    )

    result = await assignment_service._assign_owned(
        context.parent_id,
        assignment_service.AssignmentRequest(
            operation_key=f"observer-assignment-{context.suffix}",
            agent_profile="observer-worker",
            message="Complete the assigned task.",
        ),
        owner=context.owner.id,
        registry=object(),
    )

    assert create_errors == []
    assert result["success"] is True
    assert result["state"] == "submitted"
    assert result["terminal_id"] == child["terminal_id"]
    assert len(question_ids) == 1
    status = context.service.source_status(child["terminal_id"], context.incarnation_id)
    assert status.state == "current"
    assert status.capability_available is False

    page = context.service.list_metadata(context.owner, {"terminal_id": child["terminal_id"]})
    assert [item["question_id"] for item in page.items] == question_ids
    detail = context.service.get_detail(context.owner, question_ids[0])
    assert detail["origin"] == "native"
    assert detail["lifecycle"] == "manual_only"
    assert detail["recipient"]["kind"] == "human"
    assert detail["recipient"]["reason"] == "validated_parent_content_owner"
    assert detail["can_answer"] is False
    assert detail["options"] == []
    assert context.service.capabilities(
        actor=context.owner, provider="codex", execution_mode="local"
    )

    with context.service.repository.read_connection() as connection:
        child_source = connection.execute(
            "SELECT owner_principal_id,owner_source,capability_available,assignment_id "
            "FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
            (child["terminal_id"], context.incarnation_id),
        ).fetchone()
        assert child_source["owner_principal_id"] == context.owner.id
        assert child_source["owner_source"] == "validated_parent_inheritance"
        assert child_source["capability_available"] == 0
        assert child_source["assignment_id"] == result["assignment_id"]


def test_assignment_binding_rejects_unsubmitted_forged_and_stale_relations(
    observer_assignment_context,
):
    context = observer_assignment_context

    # A pending/admitted ID is not proof of child ownership.
    pending_assignment = _admit_assignment(context, f"pending-{context.suffix}")
    pending_child, _ = _create_observer_child(context, pending_assignment)
    with pytest.raises(InteractionStale):
        context.service.bind_validated_assignment_owner(
            pending_child, context.incarnation_id, pending_assignment
        )
    with context.service.repository.read_connection() as connection:
        source = connection.execute(
            "SELECT owner_principal_id,assignment_id FROM interaction_sources WHERE terminal_id=%s",
            (pending_child,),
        ).fetchone()
        assert source["owner_principal_id"] is None
        assert source["assignment_id"] is None

    missing_id_assignment = _admit_assignment(context, f"missing-id-{context.suffix}")
    missing_id_child, _ = _create_observer_child(context, missing_id_assignment)
    from cli_agent_orchestrator.services.assignment_service import _finish

    _finish(
        missing_id_assignment,
        {
            "terminal_id": missing_id_child,
            "success": True,
            "state": "submitted",
        },
    )
    with pytest.raises(InteractionStale):
        context.service.bind_validated_assignment_owner(
            missing_id_child, context.incarnation_id, missing_id_assignment
        )

    forged_assignment = _admit_assignment(context, f"forged-{context.suffix}")
    forged_child, _ = _create_observer_child(context, forged_assignment)

    _finish(
        forged_assignment,
        {
            "assignment_id": forged_assignment,
            "terminal_id": f"other-{context.suffix}",
            "success": True,
            "state": "submitted",
        },
    )
    with pytest.raises(InteractionStale):
        context.service.bind_validated_assignment_owner(
            forged_child, context.incarnation_id, forged_assignment
        )

    stale_assignment = _admit_assignment(context, f"stale-{context.suffix}")
    stale_child, _ = _create_observer_child(context, stale_assignment)
    _submit_assignment(context, stale_assignment, stale_child)
    replacement_incarnation = f"replacement-{context.suffix}"
    with database.SessionLocal() as connection:
        terminal = connection.get(database.TerminalModel, stale_child)
        terminal.session_incarnation_id = replacement_incarnation
        connection.commit()
    with pytest.raises(InteractionStale):
        context.service.bind_validated_assignment_owner(
            stale_child, context.incarnation_id, stale_assignment
        )


def test_assignment_binding_retains_work_ownership_fence(observer_assignment_context):
    context = observer_assignment_context
    assignment_id = _admit_assignment(context, f"work-{context.suffix}")
    child, window_name = _create_observer_child(context, assignment_id)
    _record_native_manual_notice(context, child, window_name)
    _submit_assignment(context, assignment_id, child)

    now = datetime.now(timezone.utc).timestamp()
    with WorkRepository(context.target).transaction() as connection:
        connection.execute(
            "INSERT INTO work_jobs (id,project_id,principal_id,allowed_providers,grant_id,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (f"job-{context.suffix}", "project", "principal", '["codex"]', "grant", now),
        )
        connection.execute(
            "INSERT INTO work_items "
            "(id,job_id,operation_kind,idempotency_key,request_hash,contract_id,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                f"item-{context.suffix}",
                f"job-{context.suffix}",
                "launch",
                f"work-key-{context.suffix}",
                "request-hash",
                "contract",
                now,
            ),
        )
        connection.execute(
            "INSERT INTO work_attempts "
            "(id,work_item_id,attempt_number,generation,provider,terminal_id,state,lease_expires_at,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                f"attempt-{context.suffix}",
                f"item-{context.suffix}",
                1,
                1,
                "codex",
                child,
                "running",
                now + 60,
                now,
            ),
        )

    with pytest.raises(InteractionForbidden):
        context.service.bind_validated_assignment_owner(
            child, context.incarnation_id, assignment_id
        )
    with context.service.repository.read_connection() as connection:
        source = connection.execute(
            "SELECT owner_principal_id,assignment_id FROM interaction_sources WHERE terminal_id=%s",
            (child,),
        ).fetchone()
        assert source["owner_principal_id"] is None
        assert source["assignment_id"] is None


@pytest.mark.parametrize("parent_change", ["incarnation", "tools", "revoked"])
def test_submitted_assignment_rechecks_parent_source_before_owner_binding(
    observer_assignment_context, parent_change
):
    context = observer_assignment_context
    _make_parent_observer(context)
    assignment_id = _admit_assignment(context, f"parent-change-{context.suffix}-{parent_change}")
    child, window_name = _create_observer_child(context, assignment_id)
    question_id = _record_native_manual_notice(context, child, window_name)
    _submit_assignment(context, assignment_id, child)

    if parent_change == "incarnation":
        with database.SessionLocal() as connection:
            parent = connection.get(database.TerminalModel, context.parent_id)
            parent.session_incarnation_id = f"replacement-{context.suffix}"
            connection.commit()
    elif parent_change == "tools":
        with database.SessionLocal() as connection:
            parent = connection.get(database.TerminalModel, context.parent_id)
            parent.allowed_tools = json.dumps(["@cao-mcp-server", "revoked-extra-tool"])
            connection.commit()
    else:
        with context.service._transaction() as connection:
            connection.execute(
                "UPDATE interaction_sources SET revoked_at=%s WHERE terminal_id=%s AND incarnation_id=%s",
                (
                    datetime.now(timezone.utc).timestamp(),
                    context.parent_id,
                    context.incarnation_id,
                ),
            )

    with pytest.raises(InteractionStale):
        context.service.bind_validated_assignment_owner(
            child, context.incarnation_id, assignment_id
        )

    with context.service.repository.read_connection() as connection:
        source = connection.execute(
            "SELECT owner_principal_id,owner_source,assignment_id,capability_available "
            "FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
            (child, context.incarnation_id),
        ).fetchone()
        question = connection.execute(
            "SELECT version,recipient_kind,recipient_id,route_reason "
            "FROM interaction_questions WHERE question_id=%s",
            (question_id,),
        ).fetchone()
        assert tuple(source) == (None, "unowned", None, 0)
        assert tuple(question) == (1, "unassigned", None, "native_dialog_manual_action")
        assert (
            connection.execute(
                "SELECT count(*) FROM interaction_events WHERE source_terminal_id=%s "
                "AND event_type IN ('interaction.routing_updated','interaction.assignment_owner_bound')",
                (child,),
            ).fetchone()[0]
            == 0
        )


def test_assignment_owner_binding_rolls_back_all_routing_changes_on_event_failure(
    observer_assignment_context, monkeypatch
):
    context = observer_assignment_context
    assignment_id = _admit_assignment(context, f"rollback-{context.suffix}")
    child, window_name = _create_observer_child(context, assignment_id)
    question_id = _record_native_manual_notice(context, child, window_name)
    _submit_assignment(context, assignment_id, child)

    append_event = context.service.repository.append_event

    def fail_assignment_event(connection, *, event_type, **kwargs):
        if event_type == "interaction.assignment_owner_bound":
            raise RuntimeError("injected assignment owner event failure")
        return append_event(connection, event_type=event_type, **kwargs)

    monkeypatch.setattr(context.service.repository, "append_event", fail_assignment_event)
    with pytest.raises(RuntimeError, match="injected assignment owner event failure"):
        context.service.bind_validated_assignment_owner(
            child, context.incarnation_id, assignment_id
        )

    with context.service.repository.read_connection() as connection:
        source = connection.execute(
            "SELECT owner_principal_id,owner_source,assignment_id FROM interaction_sources WHERE terminal_id=%s",
            (child,),
        ).fetchone()
        question = connection.execute(
            "SELECT version,recipient_kind,recipient_id,route_reason FROM interaction_questions "
            "WHERE question_id=%s",
            (question_id,),
        ).fetchone()
        assert tuple(source) == (None, "unowned", None)
        assert tuple(question) == (1, "unassigned", None, "native_dialog_manual_action")
        assert (
            connection.execute(
                "SELECT count(*) FROM interaction_events WHERE event_type='interaction.assignment_owner_bound' "
                "AND source_terminal_id=%s",
                (child,),
            ).fetchone()[0]
            == 0
        )

    monkeypatch.setattr(context.service.repository, "append_event", append_event)
    context.service.bind_validated_assignment_owner(child, context.incarnation_id, assignment_id)
    assert context.service.get_detail(context.owner, question_id)["recipient"]["kind"] == "human"


@pytest.mark.parametrize("capability_available", [True, False])
def test_assignment_binding_preserves_explicit_source_capability_state(
    observer_assignment_context, capability_available
):
    context = observer_assignment_context
    assignment_id = _admit_assignment(context, f"explicit-{context.suffix}-{capability_available}")
    child, _ = _create_child_terminal(context)
    issued = context.service.register_source(
        terminal_id=child,
        incarnation_id=context.incarnation_id,
        session_name=context.session_name,
        provider="codex",
        assignment_id=assignment_id,
        capability_available=capability_available,
    )
    _submit_assignment(context, assignment_id, child)

    context.service.bind_validated_assignment_owner(child, context.incarnation_id, assignment_id)

    status = context.service.source_status(child, context.incarnation_id)
    assert status.capability_available is capability_available
    if capability_available:
        actor = context.service.authenticate_agent(issued.token)
        assert actor.terminal_id == child
    else:
        with pytest.raises(InteractionForbidden):
            context.service.authenticate_agent(issued.token)
