"""Service boundary tests for the bounded AG-UI interaction projection."""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from test.postgres.interactions import postgres_interaction_store
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.interactions.models import (
    CreateGroupRequest,
    InteractionAnswerPayload,
    QuestionInput,
)

pytestmark = pytest.mark.postgres


def test_interaction_cursor_roundtrips_exact_float_and_accepts_legacy_precision():
    from cli_agent_orchestrator.services.interactions.capability import (
        cursor_decode,
        cursor_encode,
    )

    question_id = str(uuid.uuid4())
    for timestamp in (1791678542.0859067, 1791678511.6422534):
        assert cursor_decode(cursor_encode(timestamp, question_id)) == (
            timestamp,
            question_id,
        )

        legacy = (
            base64.urlsafe_b64encode(f"{timestamp:.6f}\n{question_id}".encode("utf-8"))
            .rstrip(b"=")
            .decode("ascii")
        )
        assert cursor_decode(legacy) == (float(f"{timestamp:.6f}"), question_id)


@pytest.fixture
def projection_boundary_context(postgres_interaction_store):
    suffix = uuid.uuid4().hex[:10]
    owner = auth._verified_principal(
        "https://agui.boundaries.test",
        "projection-boundary-owner-" + suffix,
        [auth.SCOPE_ADMIN],
        "jwt",
    )
    return SimpleNamespace(
        owner=owner,
        service=postgres_interaction_store.service,
        target=postgres_interaction_store.target,
    )


def _register_terminal_source(context, *, observer: bool = False):
    suffix = uuid.uuid4().hex[:12]
    terminal_id = "ag" + suffix
    session_name = "agui-boundary-" + suffix
    incarnation_id = "agui-boundary-inc-" + suffix
    database.create_terminal(
        terminal_id,
        session_name,
        "worker",
        "codex",
        allowed_tools=["@cao-mcp-server"],
        session_incarnation_id=incarnation_id,
        new_session_incarnation=True,
    )
    if observer:
        source = context.service.register_observer_source(
            terminal_id=terminal_id,
            incarnation_id=incarnation_id,
            session_name=session_name,
            provider="codex",
            principal=context.owner,
        )
    else:
        source = context.service.register_source(
            terminal_id=terminal_id,
            incarnation_id=incarnation_id,
            session_name=session_name,
            provider="codex",
            principal=context.owner,
        )
    return SimpleNamespace(
        terminal_id=terminal_id,
        session_name=session_name,
        incarnation_id=incarnation_id,
        source=source,
    )


def _create_questions(context, source, *, prefix: str, count: int) -> list[str]:
    request = CreateGroupRequest(
        questions=tuple(
            QuestionInput(
                kind="text",
                prompt=f"{prefix} safe metadata question {index:03d}?",
                required=True,
            )
            for index in range(count)
        )
    )
    created = context.service.create_group(
        source.source.token,
        request,
        f"agui-boundary-{prefix}-operation",
    )
    return [str(question["question_id"]) for question in created.questions]


@pytest.mark.integration
@pytest.mark.parametrize(
    "cohort_timestamp",
    [1791678542.0859067, 1791678511.6422534],
    ids=["datetime-rounds-up", "datetime-rounds-down"],
)
def test_service_projection_pages_101_items_without_duplicates(
    projection_boundary_context, cohort_timestamp
):
    from cli_agent_orchestrator.services.agui.interaction_projection import (
        project_interaction_snapshot,
    )

    context = projection_boundary_context
    retired_source = _register_terminal_source(context)
    retired_question_id = _create_questions(context, retired_source, prefix="retired", count=1)[0]
    context.service.cancel(
        context.owner,
        retired_question_id,
        1,
        "Release capacity for the pagination boundary case.",
    )

    question_ids = [retired_question_id]
    for source_index in range(5):
        source = _register_terminal_source(context)
        question_ids.extend(
            _create_questions(
                context,
                source,
                prefix=f"batch-{source_index:02d}",
                count=20,
            )
        )

    with context.service.repository.transaction() as connection:
        connection.executemany(
            "UPDATE interaction_questions SET created_at=%s WHERE question_id=%s",
            [(cohort_timestamp, question_id) for question_id in question_ids],
        )

    first_raw = context.service.projection_snapshot(limit=100)
    first = project_interaction_snapshot(first_raw)
    assert len(first["items"]) == 100
    assert first["truncated"] is True
    assert isinstance(first["next_cursor"], str)

    second_raw = context.service.projection_snapshot(cursor=str(first["next_cursor"]), limit=100)
    second = project_interaction_snapshot(second_raw)
    from cli_agent_orchestrator.services.interactions.capability import cursor_decode

    cursor_timestamp, cursor_question_id = cursor_decode(str(first["next_cursor"]))
    with context.service.repository.read_connection() as connection:
        raw_rows = connection.execute(
            "SELECT question_id,created_at FROM interaction_questions "
            "WHERE question_id IN (" + ",".join("%s" for _ in question_ids) + ") "
            "ORDER BY created_at DESC,question_id DESC",
            question_ids,
        ).fetchall()
    boundary_row = raw_rows[99]
    assert cursor_timestamp == boundary_row["created_at"] == cohort_timestamp
    assert cursor_question_id == boundary_row["question_id"]
    first_page_ids = {item["question_id"] for item in first["items"]}
    second_page_ids = {item["question_id"] for item in second["items"]}
    assert len(second["items"]) == 1
    assert second["truncated"] is False
    assert second["next_cursor"] is None

    page_ids = [str(item["question_id"]) for page in (first, second) for item in page["items"]]
    assert len(page_ids) == 101
    assert len(set(page_ids)) == 101
    assert set(page_ids) == set(question_ids)

    metadata_first = context.service.list_metadata(context.owner, limit=100)
    metadata_second = context.service.list_metadata(
        context.owner, cursor=metadata_first.next_cursor, limit=100
    )
    assert len(metadata_first.items) == 100
    assert len(metadata_second.items) == 1
    assert metadata_first.next_cursor is not None
    assert metadata_second.next_cursor is None
    assert {
        str(item["question_id"]) for item in (*metadata_first.items, *metadata_second.items)
    } == set(question_ids)


@pytest.mark.integration
def test_agent_list_cursor_uses_exact_visible_raw_timestamp(projection_boundary_context):
    context = projection_boundary_context
    source = _register_terminal_source(context)
    question_ids: list[str] = []
    for batch_index, count in enumerate((20, 20, 20, 20, 20, 1)):
        batch = _create_questions(
            context,
            source,
            prefix=f"agent-page-{batch_index:02d}",
            count=count,
        )
        question_ids.extend(batch)
        context.service.cancel(
            context.owner,
            batch[0],
            1,
            "Release capacity for the pagination boundary case.",
        )

    cohort_timestamp = 1791678542.0859067
    with context.service.repository.transaction() as connection:
        connection.executemany(
            "UPDATE interaction_questions SET created_at=%s WHERE question_id=%s",
            [(cohort_timestamp, question_id) for question_id in question_ids],
        )

    first = context.service.agent_list(source.source.token, limit=100)
    assert len(first.items) == 100
    assert first.next_cursor is not None
    second = context.service.agent_list(source.source.token, cursor=first.next_cursor, limit=100)
    assert len(second.items) == 1
    assert second.next_cursor is None

    from cli_agent_orchestrator.services.interactions.capability import cursor_decode

    timestamp, question_id = cursor_decode(first.next_cursor)
    with context.service.repository.read_connection() as connection:
        boundary = connection.execute(
            "SELECT question_id,created_at FROM interaction_questions "
            "WHERE question_id IN (" + ",".join("%s" for _ in question_ids) + ") "
            "ORDER BY created_at DESC,question_id DESC LIMIT 1 OFFSET 99",
            question_ids,
        ).fetchone()
    assert boundary is not None
    assert timestamp == boundary["created_at"] == cohort_timestamp
    assert question_id == boundary["question_id"]
    page_ids = [str(item["question_id"]) for item in (*first.items, *second.items)]
    assert len(page_ids) == 101
    assert len(set(page_ids)) == 101
    assert set(page_ids) == set(question_ids)


@pytest.mark.integration
def test_projection_skips_missing_or_invalid_sources_and_keeps_capabilityless_observer_visible(
    projection_boundary_context,
):
    from cli_agent_orchestrator.providers.mcp_interactions import (
        NativeInteractionObservation,
    )
    from cli_agent_orchestrator.services.agui.interaction_projection import (
        project_interaction_snapshot,
    )

    context = projection_boundary_context
    invalid_source = _register_terminal_source(context)
    invalid_question_id = _create_questions(
        context, invalid_source, prefix="invalid-profile", count=1
    )[0]
    observer = _register_terminal_source(context, observer=True)
    status = context.service.source_status(observer.terminal_id, observer.incarnation_id)
    assert status.state == "current"
    assert status.capability_available is False
    observation = NativeInteractionObservation.manual_signal(
        screen_lines=["Synthetic provider dialog; contents remain private."],
        terminal_id=observer.terminal_id,
        session_name=observer.session_name,
        window_name="worker",
    )
    assert observation is not None
    receipt = context.service.observe_native(
        observer.terminal_id,
        observer.incarnation_id,
        1,
        observation,
    )

    unregistered_suffix = uuid.uuid4().hex[:12]
    unregistered_terminal_id = "ag" + unregistered_suffix
    unregistered_incarnation_id = "agui-boundary-inc-" + unregistered_suffix
    database.create_terminal(
        unregistered_terminal_id,
        "agui-unregistered-" + unregistered_suffix,
        "worker",
        "codex",
        allowed_tools=["@cao-mcp-server"],
        session_incarnation_id=unregistered_incarnation_id,
        new_session_incarnation=True,
    )
    assert (
        context.service.source_status(unregistered_terminal_id, unregistered_incarnation_id).state
        == "unregistered"
    )

    with database.SessionLocal() as session:
        terminal = session.get(database.TerminalModel, invalid_source.terminal_id)
        assert terminal is not None
        terminal.allowed_tools = json.dumps(["ordinary-tool"])
        session.commit()

    projection = project_interaction_snapshot(context.service.projection_snapshot())
    visible_ids = {str(item["question_id"]) for item in projection["items"]}
    assert receipt.question_id in visible_ids
    assert invalid_question_id not in visible_ids
    assert not any(
        item["source"]["terminal_id"] == unregistered_terminal_id for item in projection["items"]
    )
    observer_item = next(
        item for item in projection["items"] if item["question_id"] == receipt.question_id
    )
    assert observer_item["origin"] == "native"
    assert observer_item["kind"] == "native_unknown"
    assert observer_item["can_answer"] is False
    assert observer_item["actions"] == []
    encoded = json.dumps(projection)
    assert "Synthetic provider dialog; contents remain private." not in encoded


def test_projection_adapter_fails_closed_on_invalid_item_shape():
    from cli_agent_orchestrator.services.agui.interaction_projection import (
        project_interaction_snapshot,
        unavailable_interaction_snapshot,
    )

    invalid_page = {
        "items": [{"question_id": "shape-invalid-question"}],
        "next_cursor": None,
        "event_sequence": 0,
    }
    with pytest.raises(ValueError, match="interaction projection item is invalid"):
        project_interaction_snapshot(invalid_page)

    unavailable = unavailable_interaction_snapshot()
    assert unavailable["available"] is False
    assert unavailable["items"] == []
    assert unavailable["next_cursor"] is None
    assert unavailable["truncated"] is False


@pytest.mark.integration
def test_service_projection_keeps_answer_receipt_and_event_high_water_together(
    projection_boundary_context,
):
    from cli_agent_orchestrator.services.agui.interaction_projection import (
        project_interaction_snapshot,
    )

    context = projection_boundary_context
    source = _register_terminal_source(context)
    created = context.service.create_group(
        source.source.token,
        CreateGroupRequest(
            questions=(
                QuestionInput(
                    kind="text",
                    prompt="Synthetic question for receipt snapshot consistency?",
                    required=True,
                ),
            )
        ),
        "agui-receipt-snapshot-operation-001",
    )
    question_id = str(created.questions[0]["question_id"])
    accepted = context.service.accept_answer(
        context.owner,
        question_id,
        1,
        InteractionAnswerPayload(kind="text", text="synthetic safe answer"),
        "agui-receipt-snapshot-answer-001",
    )
    returned = asyncio.run(
        context.service.wait_for_answers(source.source.token, created.group_id, 0)
    )
    assert returned.state == "ready"
    assert returned.answers[0].response.text == "synthetic safe answer"

    projection = project_interaction_snapshot(context.service.projection_snapshot())
    item = next(item for item in projection["items"] if item["question_id"] == question_id)
    assert item["delivery_state"] == "returned_to_mcp"
    assert item["answer_receipt"]["answer_id"] == accepted.answer_id
    assert item["answer_receipt"]["delivery_state"] == "returned_to_mcp"
    with context.service.repository.read_connection() as connection:
        latest_event_sequence = int(
            connection.execute(
                "SELECT coalesce(max(sequence),0) FROM interaction_events"
            ).fetchone()[0]
        )
        question_event_sequence = int(
            connection.execute(
                "SELECT coalesce(max(sequence),0) FROM interaction_events WHERE question_id=%s",
                (question_id,),
            ).fetchone()[0]
        )
    assert projection["event_sequence"] == latest_event_sequence
    assert projection["event_sequence"] >= question_event_sequence
