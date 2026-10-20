"""Native admission and delegated-cycle lifecycle boundary regressions."""

from __future__ import annotations

import asyncio
import json
from test.integration.test_interaction_delegation import delegation_context  # noqa: F401
from test.integration.test_interaction_work_authority import (  # noqa: F401
    _bind_authorized_interaction_work,
    _bind_work,
    _register,
    interaction_work_context,
)
from test.postgres.interactions import postgres_interaction_store, register_interaction_source
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.interactions.models import (
    CreateGroupRequest,
    InteractionForbidden,
    QuestionInput,
)
from cli_agent_orchestrator.services.interactions.service import (
    InteractionCapacityExceeded,
)

pytestmark = pytest.mark.postgres


def _make_reciprocal_cycle(context, *, delegation_expires_in: int = 120):
    """Create two real assigned sources whose owner-reviewed waits form an SCC."""
    service = context.service
    suffix = uuid4().hex[:10]

    child_delegation = service.delegate_question(
        context.owner,
        context.question_id,
        1,
        expected_assignment_id=context.assignment_id,
        reason="Reviewed child dependency for lifecycle test",
        expires_in_seconds=delegation_expires_in,
    )
    assert child_delegation.supervisor_terminal_id == context.parent.source["terminal_id"]

    reverse_assignment_id = f"cycle-reverse-assignment-{suffix}"
    with database.SessionLocal() as connection:
        connection.add(
            database.AssignmentIntentModel(
                assignment_id=reverse_assignment_id,
                owner=context.owner.id,
                parent_terminal_id=context.child.source["terminal_id"],
                parent_incarnation_id=context.child.source["incarnation_id"],
                operation_key=f"cycle-reverse-operation-{suffix}",
                request_hash=f"cycle-reverse-request-{suffix}",
                state="submitted",
                result_json=json.dumps(
                    {
                        "success": True,
                        "terminal_id": context.parent.source["terminal_id"],
                        "assignment_id": reverse_assignment_id,
                    }
                ),
            )
        )
        connection.commit()

    with service.repository.read_connection() as connection:
        parent = connection.execute(
            "SELECT session_name FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
            (context.parent.source["terminal_id"], context.parent.source["incarnation_id"]),
        ).fetchone()
    parent_as_child = service.register_source(
        terminal_id=context.parent.source["terminal_id"],
        incarnation_id=context.parent.source["incarnation_id"],
        session_name=parent["session_name"],
        provider="codex",
        principal=context.owner,
        assignment_id=reverse_assignment_id,
    )
    parent_group = service.create_group(
        parent_as_child.token,
        CreateGroupRequest(
            questions=[
                QuestionInput(kind="text", prompt="Parent reciprocal dependency?", required=True)
            ]
        ),
        f"cycle-parent-group-operation-{suffix}",
    )
    parent_question_id = parent_group.questions[0]["question_id"]
    parent_delegation = service.delegate_question(
        context.owner,
        parent_question_id,
        1,
        expected_assignment_id=reverse_assignment_id,
        reason="Reviewed parent dependency for lifecycle test",
        expires_in_seconds=delegation_expires_in,
    )
    assert parent_delegation.supervisor_terminal_id == context.child.source["terminal_id"]
    return SimpleNamespace(
        service=service,
        owner=context.owner,
        child=context.child,
        parent=context.parent,
        parent_as_child=parent_as_child,
        child_group_id=context.group_id,
        child_question_id=context.question_id,
        parent_group_id=parent_group.group_id,
        parent_question_id=parent_question_id,
    )


def _cycle_edges(graph):
    return [edge for edge in graph["edges"] if edge["cycle"]]


def test_delegation_expiry_clears_only_the_old_cycle_edges(delegation_context, monkeypatch):
    from cli_agent_orchestrator.services.interactions import service as service_module

    frozen_now = service_module._now()
    controlled_now = [frozen_now]
    monkeypatch.setattr(service_module, "_now", lambda: controlled_now[0])

    cycle = _make_reciprocal_cycle(delegation_context, delegation_expires_in=1)
    assert _cycle_edges(cycle.service.wait_graph(cycle.owner))

    controlled_now[0] = frozen_now + 2
    cycle.service.list_metadata(cycle.owner)

    with cycle.service.repository.read_connection() as connection:
        groups = connection.execute(
            "SELECT group_id,lifecycle FROM interaction_groups WHERE group_id IN (%s,%s)",
            (cycle.child_group_id, cycle.parent_group_id),
        ).fetchall()
        waits = connection.execute(
            "SELECT group_id,state FROM interaction_waits WHERE group_id IN (%s,%s)",
            (cycle.child_group_id, cycle.parent_group_id),
        ).fetchall()
        questions = connection.execute(
            "SELECT question_id,version,human_required,recipient_kind FROM interaction_questions "
            "WHERE question_id IN (%s,%s)",
            (cycle.child_question_id, cycle.parent_question_id),
        ).fetchall()
        old_delegations = connection.execute(
            "SELECT question_id,question_version,revoked_at FROM interaction_delegations "
            "WHERE question_id IN (%s,%s)",
            (cycle.child_question_id, cycle.parent_question_id),
        ).fetchall()

    assert {row["group_id"]: row["lifecycle"] for row in groups} == {
        cycle.child_group_id: "open",
        cycle.parent_group_id: "open",
    }
    assert {row["group_id"]: row["state"] for row in waits} == {
        cycle.child_group_id: "waiting",
        cycle.parent_group_id: "waiting",
    }
    assert {
        row["question_id"]: (row["version"], row["human_required"], row["recipient_kind"])
        for row in questions
    } == {
        cycle.child_question_id: (3, True, "human"),
        cycle.parent_question_id: (3, True, "human"),
    }
    # Old append-only grants remain historical; only the exact current question
    # version can carry read/answer authority or a wait-graph edge.
    assert len(old_delegations) == 2
    assert all(row["question_version"] == 2 for row in old_delegations)
    assert _cycle_edges(cycle.service.wait_graph(cycle.owner)) == []
    with pytest.raises(InteractionForbidden):
        cycle.service.agent_get(cycle.parent_as_child.token, cycle.child_question_id)


def test_replacing_a_supervisor_source_stales_its_wait_and_reopens_the_other_side(
    delegation_context,
):
    cycle = _make_reciprocal_cycle(delegation_context)
    assert _cycle_edges(cycle.service.wait_graph(cycle.owner))

    replacement_incarnation = f"replacement-{uuid4().hex}"
    parent_terminal_id = cycle.parent.source["terminal_id"]
    with database.SessionLocal() as connection:
        terminal = connection.get(database.TerminalModel, parent_terminal_id)
        assert terminal is not None
        parent_session_name = terminal.tmux_session
        terminal.session_incarnation_id = replacement_incarnation
        incarnation = connection.get(database.SessionIncarnationModel, parent_session_name)
        assert incarnation is not None
        incarnation.incarnation_id = replacement_incarnation
        connection.commit()
    replacement = cycle.service.register_source(
        terminal_id=parent_terminal_id,
        incarnation_id=replacement_incarnation,
        session_name=parent_session_name,
        provider="codex",
        principal=cycle.owner,
    )

    with cycle.service.repository.read_connection() as connection:
        groups = connection.execute(
            "SELECT group_id,lifecycle FROM interaction_groups WHERE group_id IN (%s,%s)",
            (cycle.child_group_id, cycle.parent_group_id),
        ).fetchall()
        waits = connection.execute(
            "SELECT group_id,state FROM interaction_waits WHERE group_id IN (%s,%s)",
            (cycle.child_group_id, cycle.parent_group_id),
        ).fetchall()

    assert {row["group_id"]: row["lifecycle"] for row in groups} == {
        cycle.child_group_id: "open",
        cycle.parent_group_id: "stale",
    }
    assert {row["group_id"]: row["state"] for row in waits} == {
        cycle.child_group_id: "waiting",
        cycle.parent_group_id: "stale",
    }
    assert _cycle_edges(cycle.service.wait_graph(cycle.owner)) == []
    assert (
        cycle.service.source_status(parent_terminal_id, cycle.parent.source["incarnation_id"]).state
        == "inactive"
    )
    assert (
        cycle.service.source_status(parent_terminal_id, replacement_incarnation).state == "current"
    )
    pending = asyncio.run(
        cycle.service.wait_for_required(
            cycle.child.source["terminal_id"],
            cycle.child.source["incarnation_id"],
            timeout_seconds=0,
            expected_generation=1,
        )
    )
    assert pending.state == "waiting"
    detail = cycle.service.get_detail(cycle.owner, cycle.child_question_id)
    assert detail["delegation_candidate"] is None
    with pytest.raises(InteractionForbidden):
        cycle.service.agent_get(cycle.parent_as_child.token, cycle.child_question_id)
    with pytest.raises(InteractionForbidden):
        cycle.service.agent_get(replacement.token, cycle.child_question_id)


def test_profile_drift_removes_cycle_authority_without_releasing_required_wait(
    delegation_context,
):
    cycle = _make_reciprocal_cycle(delegation_context)
    assert _cycle_edges(cycle.service.wait_graph(cycle.owner))

    with database.SessionLocal() as connection:
        terminal = connection.get(database.TerminalModel, cycle.parent.source["terminal_id"])
        assert terminal is not None
        terminal.allowed_tools = "[]"
        connection.commit()

    assert (
        cycle.service.source_status(
            cycle.parent.source["terminal_id"], cycle.parent.source["incarnation_id"]
        ).state
        == "inactive"
    )
    stale = asyncio.run(
        cycle.service.wait_for_required(
            cycle.parent.source["terminal_id"],
            cycle.parent.source["incarnation_id"],
            timeout_seconds=0,
            expected_generation=1,
        )
    )
    assert stale.state == "stale"

    graph = cycle.service.wait_graph(cycle.owner)
    assert _cycle_edges(graph) == []
    with cycle.service.repository.read_connection() as connection:
        groups = connection.execute(
            "SELECT group_id,lifecycle FROM interaction_groups WHERE group_id IN (%s,%s)",
            (cycle.child_group_id, cycle.parent_group_id),
        ).fetchall()
        waits = connection.execute(
            "SELECT group_id,state FROM interaction_waits WHERE group_id IN (%s,%s)",
            (cycle.child_group_id, cycle.parent_group_id),
        ).fetchall()

    # The invalid source's dependency remains blocked for reconciliation. The
    # still-current child wait becomes open/waiting but cannot be released by
    # the stale supervisor delegation.
    assert {row["group_id"]: row["lifecycle"] for row in groups} == {
        cycle.child_group_id: "open",
        cycle.parent_group_id: "cycle_blocked",
    }
    assert {row["group_id"]: row["state"] for row in waits} == {
        cycle.child_group_id: "waiting",
        cycle.parent_group_id: "cycle_blocked",
    }
    assert cycle.service.has_required_wait(
        cycle.child.source["terminal_id"], cycle.child.source["incarnation_id"]
    )


def test_mixed_required_optional_questions_share_the_100_active_capacity(
    postgres_interaction_store,
):
    owner = auth._verified_principal(
        "https://interaction.capacity.test", "mixed-capacity-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    service = postgres_interaction_store.service
    sources = [
        register_interaction_source(postgres_interaction_store, owner=owner) for _ in range(6)
    ]
    requests = []
    groups = []
    for source_index, source in enumerate(sources[:5]):
        request = CreateGroupRequest(
            wait_for_required=False,
            questions=tuple(
                QuestionInput(
                    kind="text",
                    prompt=f"Mixed capacity {source_index}/{question_index}",
                    required=question_index % 2 == 0,
                )
                for question_index in range(20)
            ),
        )
        operation_key = f"mixed-capacity-operation-{source_index:04d}"
        group = service.create_group(source.token, request, operation_key)
        assert group.wait_required is True
        requests.append((source, request, operation_key))
        groups.append(group)

    with service.repository.read_connection() as connection:
        counts = connection.execute(
            "SELECT count(*) AS total,count(*) FILTER (WHERE required IS TRUE) AS required "
            "FROM interaction_questions WHERE lifecycle='open' AND expires_at>%s",
            (service_module_now(),),
        ).fetchone()
        event_rows = connection.execute(
            "SELECT event_type FROM interaction_events WHERE group_id=%s ORDER BY sequence",
            (groups[0].group_id,),
        ).fetchall()
    assert counts["total"] == 100
    assert counts["required"] == 50
    assert [row["event_type"] for row in event_rows] == [
        *("interaction.question_created" for _ in range(20)),
        "interaction.group_created",
    ]

    first_source, first_request, first_key = requests[0]
    replay = service.create_group(first_source.token, first_request, first_key)
    assert replay.replayed is True
    assert replay.group_id == groups[0].group_id
    with pytest.raises(InteractionCapacityExceeded):
        service.create_group(
            sources[5].token,
            CreateGroupRequest(questions=[QuestionInput(kind="text", prompt="Beyond 100")]),
            "mixed-capacity-overflow-operation",
        )


@pytest.mark.asyncio
async def test_metadata_bulk_rejects_unbound_source_with_historical_work_attempt(
    interaction_work_context,
):
    context = interaction_work_context
    source = _register(context)
    group = context.interactions.create_group(
        source.token,
        CreateGroupRequest(
            questions=[QuestionInput(kind="text", prompt="Historical Work binding", required=True)]
        ),
        "metadata-historical-work-question-operation",
    )
    work = await _bind_work(context)
    attempt_id = work["attempts"][0]["id"]
    with context.repo.transaction() as connection:
        connection.execute("UPDATE work_attempts SET state='finished' WHERE id=%s", (attempt_id,))

    with context.interactions.repository.read_connection() as connection:
        source_row = connection.execute(
            "SELECT attempt_id,execution_mode FROM interaction_sources WHERE terminal_id=%s",
            (context.terminal_id,),
        ).fetchone()
        attempts = connection.execute(
            "SELECT id,state FROM work_attempts WHERE terminal_id=%s", (context.terminal_id,)
        ).fetchall()
    assert tuple(source_row) == (None, "local")
    assert [(row["id"], row["state"]) for row in attempts] == [(attempt_id, "finished")]

    metadata = context.interactions.list_metadata(
        context.actor, {"terminal_id": context.terminal_id}
    )
    assert group.questions[0]["question_id"] not in {item["question_id"] for item in metadata.items}


def test_metadata_bulk_rejects_managed_source_without_attempt_binding(interaction_work_context):
    context = interaction_work_context
    source = _register(context)
    group = context.interactions.create_group(
        source.token,
        CreateGroupRequest(
            questions=[QuestionInput(kind="text", prompt="Missing managed binding", required=True)]
        ),
        "metadata-managed-unbound-question-operation",
    )
    with context.interactions.repository.transaction() as connection:
        connection.execute(
            "UPDATE interaction_sources SET execution_mode='work_managed',attempt_id=NULL,"
            "attempt_generation=NULL WHERE terminal_id=%s AND incarnation_id=%s",
            (context.terminal_id, context.incarnation_id),
        )

    metadata = context.interactions.list_metadata(
        context.actor, {"terminal_id": context.terminal_id}
    )
    assert group.questions[0]["question_id"] not in {item["question_id"] for item in metadata.items}


@pytest.mark.asyncio
async def test_metadata_bulk_revalidates_bound_work_after_grant_revocation(
    interaction_work_context, monkeypatch
):
    context = interaction_work_context
    source, grant = await _bind_authorized_interaction_work(context)
    group = context.interactions.create_group(
        source.token,
        CreateGroupRequest(
            questions=[QuestionInput(kind="text", prompt="Revoked bound Work", required=True)]
        ),
        "metadata-revoked-bound-work-question-operation",
    )
    question_id = group.questions[0]["question_id"]
    from cli_agent_orchestrator.services.work_contract import WorkContracts

    revalidated = []
    real_revalidate = WorkContracts._revalidate_order

    def observe_revalidation(self, connection, *args, **kwargs):
        assert connection.in_transaction
        revalidated.append(True)
        return real_revalidate(self, connection, *args, **kwargs)

    monkeypatch.setattr(WorkContracts, "_revalidate_order", observe_revalidation)
    context.control.revoke(
        context.actor,
        grant_id=grant.id,
        expected_grant_revision=grant.revision,
        reason="Owner revoked the source before metadata projection",
    )

    metadata = context.interactions.list_metadata(
        context.actor, {"terminal_id": context.terminal_id}
    )
    assert revalidated
    assert question_id not in {item["question_id"] for item in metadata.items}


def service_module_now() -> float:
    from cli_agent_orchestrator.services.interactions import service as service_module

    return service_module._now()
