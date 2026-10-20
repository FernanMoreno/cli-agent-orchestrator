"""Durable assignment links drive interaction dependency-cycle blocking."""

from __future__ import annotations

import json
from test.integration.test_interaction_delegation import delegation_context  # noqa: F401

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.services.interactions.models import CreateGroupRequest, QuestionInput

pytestmark = pytest.mark.postgres


def test_server_derived_reciprocal_assignment_delegations_block_a_wait_cycle(
    delegation_context,
):
    context = delegation_context
    child_group = context.service.get_detail(context.owner, context.question_id)
    child_group_id = child_group["group_id"]

    # The existing fixture gives the child a real submitted assignment from
    # the parent. Review its question so the child dependency edge targets the
    # server-derived parent identity.
    child_delegation = context.service.delegate_question(
        context.owner,
        context.question_id,
        1,
        expected_assignment_id=context.assignment_id,
        reason="Reviewed child dependency for cycle evaluation",
        expires_in_seconds=120,
    )
    assert child_delegation.supervisor_terminal_id == context.parent.source["terminal_id"]

    # Build a second durable submitted assignment in the reverse direction.
    # The interaction service must resolve the target through this row and the
    # live child source, not from a caller-supplied supervisor terminal ID.
    reverse_assignment_id = "owner-reviewed-reverse-assignment"
    with database.SessionLocal() as connection:
        connection.add(
            database.AssignmentIntentModel(
                assignment_id=reverse_assignment_id,
                owner=context.owner.id,
                parent_terminal_id="ac040002",
                parent_incarnation_id="delegation-child-inc",
                operation_key="delegation-reverse-assignment-operation",
                request_hash="reverse-assignment-test-request-hash",
                state="submitted",
                result_json=json.dumps(
                    {
                        "success": True,
                        "terminal_id": "ac040001",
                        "assignment_id": reverse_assignment_id,
                    }
                ),
            )
        )
        connection.commit()

    parent_as_child = context.service.register_source(
        terminal_id="ac040001",
        session_name="delegation-parent",
        incarnation_id="delegation-parent-inc",
        provider="codex",
        principal=context.owner,
        assignment_id=reverse_assignment_id,
    )
    parent_group = context.service.create_group(
        parent_as_child.token,
        CreateGroupRequest(
            questions=[QuestionInput(kind="text", prompt="Parent's reciprocal dependency.")]
        ),
        "reciprocal-parent-question-operation",
    )
    parent_question_id = parent_group.questions[0]["question_id"]
    parent_delegation = context.service.delegate_question(
        context.owner,
        parent_question_id,
        1,
        expected_assignment_id=reverse_assignment_id,
        reason="Reviewed parent dependency for cycle evaluation",
        expires_in_seconds=120,
    )
    assert parent_delegation.supervisor_terminal_id == context.child.source["terminal_id"]

    # Both edges are derived from actual source-incarnation and submitted
    # assignment rows, then exact owner-reviewed delegations. The ledger must
    # persist the SCC state and publish the same cycle in its safe graph DTO.
    with context.service.repository.read_connection() as connection:
        groups = connection.execute(
            "SELECT group_id,lifecycle FROM interaction_groups WHERE group_id IN (%s,%s)",
            (child_group_id, parent_group.group_id),
        ).fetchall()
        waits = connection.execute(
            "SELECT group_id,state FROM interaction_waits WHERE group_id IN (%s,%s)",
            (child_group_id, parent_group.group_id),
        ).fetchall()
    assert {row["group_id"]: row["lifecycle"] for row in groups} == {
        child_group_id: "cycle_blocked",
        parent_group.group_id: "cycle_blocked",
    }
    assert {row["group_id"]: row["state"] for row in waits} == {
        child_group_id: "cycle_blocked",
        parent_group.group_id: "cycle_blocked",
    }

    graph = context.service.wait_graph(context.owner)
    cycle_edges = [edge for edge in graph["edges"] if edge["cycle"]]
    assert cycle_edges
    assert any(
        edge["from"] == f"question_group:{child_group_id}" and edge["to"] == "task:ac040001"
        for edge in cycle_edges
    )
    assert any(
        edge["from"] == f"question_group:{parent_group.group_id}" and edge["to"] == "task:ac040002"
        for edge in cycle_edges
    )
