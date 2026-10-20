"""Owner-reviewed delegation over durable assignment and source identities."""

import asyncio
import json
from test.postgres.interactions import postgres_interaction_store
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.interactions.models import (
    CreateGroupRequest,
    InteractionAnswerPayload,
    InteractionConflict,
    InteractionForbidden,
    InteractionStale,
    QuestionInput,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def delegation_context(postgres_interaction_store):
    owner = auth._verified_principal("https://delegation.test", "owner", [auth.SCOPE_ADMIN], "jwt")
    service = postgres_interaction_store.service
    sources = {}
    for role, terminal_id in (("parent", "ac040001"), ("child", "ac040002")):
        database.create_terminal(
            terminal_id,
            f"delegation-{role}",
            "worker",
            "codex",
            allowed_tools=["@cao-mcp-server"],
            session_incarnation_id=f"delegation-{role}-inc",
            new_session_incarnation=True,
        )
    sources["parent"] = service.register_source(
        terminal_id="ac040001",
        session_name="delegation-parent",
        incarnation_id="delegation-parent-inc",
        provider="codex",
        principal=owner,
    )
    assignment_id = "owner-reviewed-assignment"
    with database.SessionLocal() as connection:
        connection.add(
            database.AssignmentIntentModel(
                assignment_id=assignment_id,
                owner=owner.id,
                parent_terminal_id="ac040001",
                parent_incarnation_id="delegation-parent-inc",
                operation_key="delegation-assignment-operation",
                request_hash="test-request-hash",
                state="submitted",
                result_json=json.dumps(
                    {
                        "success": True,
                        "terminal_id": "ac040002",
                        "assignment_id": assignment_id,
                    }
                ),
            )
        )
        connection.commit()
    sources["child"] = service.register_source(
        terminal_id="ac040002",
        session_name="delegation-child",
        incarnation_id="delegation-child-inc",
        provider="codex",
        assignment_id=assignment_id,
    )
    group = service.create_group(
        sources["child"].token,
        CreateGroupRequest(
            questions=[QuestionInput(kind="text", prompt="Which file format?", required=True)]
        ),
        "owner-reviewed-question-operation",
    )
    return SimpleNamespace(
        service=service,
        owner=owner,
        parent=sources["parent"],
        child=sources["child"],
        assignment_id=assignment_id,
        group_id=group.group_id,
        question_id=group.questions[0]["question_id"],
    )


def _delegate(context):
    context.service.delegate_question(
        context.owner,
        context.question_id,
        1,
        expected_assignment_id=context.assignment_id,
        reason="Reviewed clarification about the known file format",
        expires_in_seconds=60,
    )
    return context.service.get_detail(context.owner, context.question_id)


def test_assignment_parent_has_no_content_authority_before_owner_review(delegation_context):
    c = delegation_context
    with pytest.raises(InteractionForbidden):
        c.service.agent_get(c.parent.token, c.question_id)
    detail = c.service.get_detail(c.owner, c.question_id)
    assert detail["delegation_candidate"]["assignment_id"] == c.assignment_id
    assert detail["recipient"]["kind"] == "human"
    assert detail["can_answer"] is True
    reviewed = _delegate(c)
    assert reviewed["version"] == 2
    assert reviewed["recipient"]["kind"] == "agent"
    assert c.service.agent_get(c.parent.token, c.question_id)["prompt"] == "Which file format?"
    receipt = c.service.agent_answer(
        c.parent.token,
        c.question_id,
        2,
        InteractionAnswerPayload(kind="text", text="JSON"),
        "delegation-answer-operation-001",
    )
    assert receipt.version == 2
    with c.service.repository.read_connection() as connection:
        answer = connection.execute(
            "SELECT actor_kind FROM interaction_answers WHERE answer_id=%s", (receipt.answer_id,)
        ).fetchone()
        assert answer["actor_kind"] == "agent"


def test_revocation_invalidates_exact_version_and_does_not_resurrect_authority(delegation_context):
    c = delegation_context
    _delegate(c)
    reason = "Owner reviewed the scope and will answer directly."
    c.service.revoke_delegation(c.owner, c.question_id, 2, reason=reason)
    detail = c.service.get_detail(c.owner, c.question_id)
    assert detail["version"] == 3
    assert detail["recipient"]["kind"] == "human"
    assert detail["can_answer"] is True
    with pytest.raises(InteractionForbidden):
        c.service.agent_get(c.parent.token, c.question_id)
    with pytest.raises((InteractionForbidden, InteractionStale)):
        c.service.agent_answer(
            c.parent.token,
            c.question_id,
            2,
            InteractionAnswerPayload(kind="text", text="Late"),
            "delegation-late-answer-operation",
        )
    with c.service.repository.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM interaction_answers").fetchone()[0] == 0
        audit = connection.execute(
            "SELECT action,operator_principal_id,target_question_id,reason "
            "FROM interaction_private_audit WHERE action='delegation_revoked'"
        ).fetchone()
        assert tuple(audit) == ("delegation_revoked", c.owner.id, c.question_id, reason)
        versions = connection.execute(
            "SELECT version FROM interaction_question_versions WHERE question_id=%s ORDER BY version",
            (c.question_id,),
        ).fetchall()
        assert [row[0] for row in versions] == [1, 2, 3]
    public_history = [
        event.model_dump(mode="json") for event in c.service.history(c.owner, c.question_id)
    ]
    assert reason not in json.dumps(public_history)


def test_accepted_delegated_answer_cannot_be_revoked_and_remains_pullable(delegation_context):
    c = delegation_context
    _delegate(c)
    accepted = c.service.agent_answer(
        c.parent.token,
        c.question_id,
        2,
        InteractionAnswerPayload(kind="text", text="JSON"),
        "delegated-answer-before-revoke-0001",
    )
    assert accepted.delivery_state == "accepted"
    with pytest.raises(InteractionConflict, match="after its answer is accepted"):
        c.service.revoke_delegation(
            c.owner,
            c.question_id,
            2,
            reason="Keep the accepted answer bound to its reviewed version.",
        )

    detail = c.service.get_detail(c.owner, c.question_id)
    assert detail["version"] == 2
    assert detail["delivery_state"] == "accepted"
    pulled = asyncio.run(c.service.wait_for_answers(c.child.token, c.group_id, 0))
    assert pulled.state == "ready"
    assert pulled.answers[0].response.text == "JSON"
    assert c.service.get_detail(c.owner, c.question_id)["delivery_state"] == "returned_to_mcp"
    with c.service.repository.read_connection() as connection:
        versions = connection.execute(
            "SELECT version FROM interaction_question_versions WHERE question_id=%s ORDER BY version",
            (c.question_id,),
        ).fetchall()
        delegation = connection.execute(
            "SELECT revoked_at FROM interaction_delegations WHERE question_id=%s AND question_version=2",
            (c.question_id,),
        ).fetchone()
    assert [row[0] for row in versions] == [1, 2]
    assert delegation["revoked_at"] is None


def test_delegation_deadline_returns_a_new_answerable_human_version(
    delegation_context, monkeypatch
):
    from cli_agent_orchestrator.services.interactions import service as service_module

    c = delegation_context
    _delegate(c)
    now = service_module._now()
    monkeypatch.setattr(service_module, "_now", lambda: now + 61)
    c.service.list_metadata(c.owner)
    detail = c.service.get_detail(c.owner, c.question_id)
    assert detail["version"] == 3
    assert detail["recipient"]["kind"] == "human"
    assert detail["can_answer"] is True
    with pytest.raises(InteractionForbidden):
        c.service.agent_get(c.parent.token, c.question_id)
    history = c.service.history(c.owner, c.question_id)
    assert any(event.event_type == "interaction.escalated" for event in history)
