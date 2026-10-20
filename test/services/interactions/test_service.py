"""Service-level authorization, answer CAS and pull-delivery tests."""

import asyncio
import json
import time
import uuid
from test.postgres.interactions import postgres_interaction_store, register_interaction_source

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.interactions.models import (
    CreateGroupRequest,
    InteractionAnswerPayload,
    QuestionInput,
)
from cli_agent_orchestrator.services.interactions.service import (
    InteractionCapacityExceeded,
    InteractionConflict,
    InteractionForbidden,
    InteractionInvalid,
    InteractionStale,
)

pytestmark = pytest.mark.postgres


def _plain(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


@pytest.fixture
def interaction_context(postgres_interaction_store):
    registered = register_interaction_source(postgres_interaction_store)
    return {
        "owner": registered.owner,
        "terminal_id": registered.terminal_id,
        "incarnation_id": registered.incarnation_id,
        "source": registered.source,
        "service": registered.service,
        "target": registered.store.target,
    }


def test_metadata_verifies_work_schema_once_per_read_context(
    monkeypatch, postgres_interaction_store
):
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    store = postgres_interaction_store
    owner = auth._verified_principal(
        "https://interaction.verify.test", "verify-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    sources = [register_interaction_source(store, owner=owner) for _ in range(2)]
    for index, source in enumerate(sources):
        store.service.create_group(
            source.token,
            CreateGroupRequest(
                questions=tuple(
                    QuestionInput(
                        kind="text",
                        prompt=f"verification question {index}/{question_index}",
                        required=True,
                    )
                    for question_index in range(3)
                ),
            ),
            f"verification-operation-{index:04d}",
        )

    verified_connections = []
    original_verify = WorkRepository._verify
    validated_sources: dict[tuple[str, str], int] = {}
    original_validate_source = store.service._validate_source_row

    def count_verification(connection, *, version=None):
        verified_connections.append(connection)
        return original_verify(connection, version=version)

    def count_source_validation(connection, source, **kwargs):
        identity = (source["terminal_id"], source["incarnation_id"])
        validated_sources[identity] = validated_sources.get(identity, 0) + 1
        return original_validate_source(connection, source, **kwargs)

    monkeypatch.setattr(WorkRepository, "_verify", staticmethod(count_verification))
    monkeypatch.setattr(store.service, "_validate_source_row", count_source_validation)
    page = store.service.list_metadata(owner, limit=100)

    assert len(page.items) == 6
    assert all("prompt" not in item and "options" not in item for item in page.items)
    assert all(
        item["group_progress"] == {"required_total": 3, "required_answered": 0}
        and item["delivery_state"] is None
        for item in page.items
    )
    # Expiry uses a verified Work transaction, then the page uses one verified
    # read snapshot. Each source's terminal and Work binding are validated once
    # in that snapshot and reused only for its metadata rows.
    assert len(verified_connections) == 2
    assert all(
        connection.target.identity == store.target.identity for connection in verified_connections
    )
    assert validated_sources == {
        (source.terminal_id, source.incarnation_id): 1 for source in sources
    }

    with store.repository.transaction() as connection:
        connection.execute(
            "UPDATE interaction_sources SET revoked_at=%s "
            "WHERE terminal_id=%s AND incarnation_id=%s",
            (time.time(), sources[0].terminal_id, sources[0].incarnation_id),
        )
    validated_sources.clear()
    revoked_page = store.service.list_metadata(owner, limit=100)
    assert len(revoked_page.items) == 3
    assert all(
        item["source"]["incarnation_id"] == sources[1].incarnation_id for item in revoked_page.items
    )
    assert validated_sources == {
        (sources[0].terminal_id, sources[0].incarnation_id): 1,
        (sources[1].terminal_id, sources[1].incarnation_id): 1,
    }


def test_required_answer_returns_through_same_group_and_ack_is_idempotent(interaction_context):
    context = interaction_context
    service = context["service"]
    owner = context["owner"]
    capability = context["source"].token
    request = CreateGroupRequest(
        wait_for_required=False,
        questions=[QuestionInput(kind="text", prompt="What should I do?", required=True)],
    )
    created = _plain(service.create_group(capability, request, "explicit-group-operation-001"))
    assert created["wait_required"] is True
    question_id = created["questions"][0]["question_id"]
    actor = service.authenticate_agent(capability)
    assert service.has_required_wait(
        context["terminal_id"], context["incarnation_id"], actor.turn_generation
    )

    pending = _plain(asyncio.run(service.wait_for_answers(capability, created["group_id"], 0)))
    assert pending["state"] == "pending"
    assert pending["answers"] == []

    accepted = service.accept_answer(
        owner,
        question_id,
        1,
        InteractionAnswerPayload(kind="text", text="continue with the README"),
        "answer-idempotency-key-0001",
    )
    assert accepted.delivery_state == "accepted"
    assert accepted.replayed is False
    assert (
        service.has_required_wait(
            context["terminal_id"], context["incarnation_id"], actor.turn_generation
        )
        is True
    )
    not_returned = asyncio.run(
        service.wait_for_required(
            context["terminal_id"],
            context["incarnation_id"],
            timeout_seconds=0,
            expected_generation=actor.turn_generation,
        )
    )
    assert not_returned.state == "waiting"
    assert service.get_detail(owner, question_id)["delivery_state"] == "accepted"

    ready = _plain(asyncio.run(service.wait_for_answers(capability, created["group_id"], 0)))
    assert ready["state"] == "ready"
    assert ready["answers"][0]["response"] == {
        "kind": "text",
        "text": "continue with the README",
        "option_ids": [],
    }
    assert service.get_detail(owner, question_id)["delivery_state"] == "returned_to_mcp"
    assert service.has_required_wait(context["terminal_id"], context["incarnation_id"]) is False
    ack = service.acknowledge_answers(
        capability,
        created["group_id"],
        [
            {
                "question_id": question_id,
                "version": 1,
                "receipt": ready["answers"][0]["receipt"],
            }
        ],
    )
    assert ack[0].state == "agent_acknowledged"
    assert (
        service.acknowledge_answers(
            capability,
            created["group_id"],
            [
                {
                    "question_id": question_id,
                    "version": 1,
                    "receipt": ready["answers"][0]["receipt"],
                }
            ],
        )[0].state
        == "agent_acknowledged"
    )


def test_accepted_required_answer_survives_human_expiry_until_source_pull(
    interaction_context, monkeypatch
):
    from cli_agent_orchestrator.services.interactions import service as service_module

    context = interaction_context
    service = context["service"]
    capability = context["source"].token
    created = _plain(
        service.create_group(
            capability,
            CreateGroupRequest(
                expires_in_seconds=1,
                questions=[
                    QuestionInput(kind="text", prompt="Choose a format.", required=True),
                    QuestionInput(kind="text", prompt="Optional note for later.", required=False),
                ],
            ),
            "accepted-before-expiry-operation-0001",
        )
    )
    question_id = created["questions"][0]["question_id"]
    accepted = service.accept_answer(
        context["owner"],
        question_id,
        1,
        InteractionAnswerPayload(kind="text", text="JSON"),
        "accepted-before-expiry-answer-0001",
    )
    assert accepted.delivery_state == "accepted"
    assert service.has_required_wait(context["terminal_id"], context["incarnation_id"])
    assert (
        service.source_status(context["terminal_id"], context["incarnation_id"]).deadline
        is not None
    )

    accepted_at = service_module._now()
    monkeypatch.setattr(service_module, "_now", lambda: accepted_at + 2)
    result = _plain(asyncio.run(service.wait_for_answers(capability, created["group_id"], 0)))
    assert result["state"] == "ready"
    assert len(result["answers"]) == 1
    assert result["answers"][0]["response"]["text"] == "JSON"
    assert service.get_detail(context["owner"], question_id)["delivery_state"] == "returned_to_mcp"
    assert not service.has_required_wait(context["terminal_id"], context["incarnation_id"])
    expired_wait = asyncio.run(
        service.wait_for_required(
            context["terminal_id"],
            context["incarnation_id"],
            timeout_seconds=0,
            expected_generation=1,
        )
    )
    assert expired_wait.state == "expired"
    with service.repository.read_connection() as connection:
        group = connection.execute(
            "SELECT lifecycle FROM interaction_groups WHERE group_id=%s", (created["group_id"],)
        ).fetchone()
        wait = connection.execute(
            "SELECT state FROM interaction_waits WHERE group_id=%s", (created["group_id"],)
        ).fetchone()
    assert group["lifecycle"] == "expired"
    assert wait["state"] == "expired"


@pytest.mark.parametrize("optional_action", ["accept", "cancel"])
def test_optional_action_after_deadline_cannot_change_expiry_or_satisfy_group(
    interaction_context, monkeypatch, optional_action
):
    from cli_agent_orchestrator.services.interactions import service as service_module

    context = interaction_context
    service = context["service"]
    group = service.create_group(
        context["source"].token,
        CreateGroupRequest(
            expires_in_seconds=1,
            questions=(
                QuestionInput(kind="text", prompt="Required", required=True),
                QuestionInput(kind="text", prompt="Optional", required=False),
            ),
        ),
        f"optional-after-deadline-{optional_action}-operation-0001",
    )
    required_id, optional_id = [item["question_id"] for item in group.questions]
    created_at = service_module._now()
    monkeypatch.setattr(service_module, "_now", lambda: created_at + 2)

    if optional_action == "accept":
        operation = lambda: service.accept_answer(
            context["owner"],
            optional_id,
            1,
            InteractionAnswerPayload(kind="text", text="late optional"),
            "late-optional-answer-idempotency-0001",
        )
    else:
        operation = lambda: service.cancel(
            context["owner"], optional_id, 1, "Optional question expired before cancellation."
        )

    with pytest.raises(InteractionStale):
        operation()

    result = _plain(
        asyncio.run(service.wait_for_answers(context["source"].token, group.group_id, 0))
    )
    assert result["state"] == "expired"
    assert result["answers"] == []
    assert not service.has_required_wait(context["terminal_id"], context["incarnation_id"])
    with service.repository.read_connection() as connection:
        questions = {
            row["question_id"]: row["lifecycle"]
            for row in connection.execute(
                "SELECT question_id,lifecycle FROM interaction_questions WHERE group_id=%s",
                (group.group_id,),
            )
        }
        group_lifecycle = connection.execute(
            "SELECT lifecycle FROM interaction_groups WHERE group_id=%s", (group.group_id,)
        ).fetchone()[0]
        wait_state = connection.execute(
            "SELECT state FROM interaction_waits WHERE group_id=%s", (group.group_id,)
        ).fetchone()[0]
        answer_count = connection.execute(
            "SELECT count(*) FROM interaction_answers WHERE question_id=%s", (optional_id,)
        ).fetchone()[0]
    assert questions == {required_id: "expired", optional_id: "expired"}
    assert group_lifecycle == "expired"
    assert wait_state == "expired"
    assert answer_count == 0


@pytest.mark.parametrize("optional_action", ["accept", "cancel"])
def test_optional_action_before_deadline_does_not_release_required_group(
    interaction_context, optional_action
):
    context = interaction_context
    service = context["service"]
    group = service.create_group(
        context["source"].token,
        CreateGroupRequest(
            questions=(
                QuestionInput(kind="text", prompt="Required", required=True),
                QuestionInput(kind="text", prompt="Optional", required=False),
            ),
        ),
        f"optional-before-deadline-{optional_action}-operation-0001",
    )
    required_id, optional_id = [item["question_id"] for item in group.questions]
    if optional_action == "accept":
        service.accept_answer(
            context["owner"],
            optional_id,
            1,
            InteractionAnswerPayload(kind="text", text="optional only"),
            "optional-answer-before-deadline-0001",
        )
    else:
        service.cancel(context["owner"], optional_id, 1, "The optional request is not needed.")

    result = _plain(
        asyncio.run(service.wait_for_answers(context["source"].token, group.group_id, 0))
    )
    assert result["state"] == "pending"
    assert result["answers"] == []
    assert service.has_required_wait(context["terminal_id"], context["incarnation_id"])
    with service.repository.read_connection() as connection:
        required_state = connection.execute(
            "SELECT lifecycle FROM interaction_questions WHERE question_id=%s", (required_id,)
        ).fetchone()[0]
        wait_state = connection.execute(
            "SELECT state FROM interaction_waits WHERE group_id=%s", (group.group_id,)
        ).fetchone()[0]
    assert required_state == "open"
    assert wait_state == "waiting"


def test_accepted_required_answers_without_source_pull_still_consume_capacity(interaction_context):
    from cli_agent_orchestrator.services.interactions import service as service_module

    context = interaction_context
    service = context["service"]
    capability = context["source"].token
    question_limit = service_module._MAX_PER_SOURCE
    group = service.create_group(
        capability,
        CreateGroupRequest(
            questions=tuple(
                QuestionInput(kind="text", prompt=f"Question {index}", required=True)
                for index in range(question_limit)
            )
        ),
        "accepted-without-pull-capacity-operation-001",
    )
    for index, question in enumerate(group.questions):
        service.accept_answer(
            context["owner"],
            question["question_id"],
            1,
            InteractionAnswerPayload(kind="text", text=f"answer {index}"),
            f"accepted-without-pull-answer-{index:04d}",
        )
    assert service.has_required_wait(context["terminal_id"], context["incarnation_id"])

    with pytest.raises(InteractionCapacityExceeded):
        service.create_group(
            capability,
            CreateGroupRequest(questions=(QuestionInput(kind="text", prompt="Overflow"),)),
            "accepted-without-pull-capacity-overflow-001",
        )

    pulled = _plain(asyncio.run(service.wait_for_answers(capability, group.group_id, 0)))
    assert pulled["state"] == "ready"
    assert len(pulled["answers"]) == question_limit
    assert not service.has_required_wait(context["terminal_id"], context["incarnation_id"])
    admitted = service.create_group(
        capability,
        CreateGroupRequest(questions=(QuestionInput(kind="text", prompt="Capacity restored"),)),
        "accepted-without-pull-capacity-restored-001",
    )
    assert admitted.lifecycle == "open"


def test_accepted_required_answers_without_source_pull_still_consume_global_capacity(
    interaction_context, monkeypatch
):
    from cli_agent_orchestrator.services.interactions import service as service_module

    context = interaction_context
    service = context["service"]
    monkeypatch.setattr(service_module, "_MAX_ACTIVE", 2)

    def make_source(label):
        terminal_id = "gcap" + uuid.uuid4().hex[:8]
        session_name = f"global-capacity-{label}-{uuid.uuid4().hex[:6]}"
        incarnation_id = f"global-capacity-inc-{uuid.uuid4().hex[:8]}"
        database.create_terminal(
            terminal_id,
            session_name,
            "worker",
            "codex",
            allowed_tools=["@cao-mcp-server"],
            session_incarnation_id=incarnation_id,
            new_session_incarnation=True,
        )
        issued = service.register_source(
            terminal_id=terminal_id,
            incarnation_id=incarnation_id,
            session_name=session_name,
            provider="codex",
            principal=context["owner"],
        )
        return terminal_id, incarnation_id, issued.token

    sources = [make_source("a"), make_source("b"), make_source("c")]
    groups = []
    for index, (_, _, capability) in enumerate(sources[:2]):
        group = service.create_group(
            capability,
            CreateGroupRequest(
                questions=(QuestionInput(kind="text", prompt=f"Global active {index}"),)
            ),
            f"global-capacity-pending-operation-{index:04d}",
        )
        groups.append(group)
        service.accept_answer(
            context["owner"],
            group.questions[0]["question_id"],
            1,
            InteractionAnswerPayload(kind="text", text=f"answer {index}"),
            f"global-capacity-answer-key-{index:04d}",
        )

    with pytest.raises(InteractionCapacityExceeded):
        service.create_group(
            sources[2][2],
            CreateGroupRequest(questions=(QuestionInput(kind="text", prompt="Global overflow"),)),
            "global-capacity-overflow-operation-001",
        )

    first_terminal, first_incarnation, first_capability = sources[0]
    pulled = _plain(asyncio.run(service.wait_for_answers(first_capability, groups[0].group_id, 0)))
    assert pulled["state"] == "ready"
    assert service.has_required_wait(first_terminal, first_incarnation) is False
    admitted = service.create_group(
        sources[2][2],
        CreateGroupRequest(
            questions=(QuestionInput(kind="text", prompt="Global capacity restored"),)
        ),
        "global-capacity-restored-operation-001",
    )
    assert admitted.lifecycle == "open"


def test_failed_capability_installation_disables_agents_but_keeps_manual_source(
    interaction_context,
):
    from cli_agent_orchestrator.providers.mcp_interactions import NativeInteractionObservation

    context = interaction_context
    service = context["service"]
    token = context["source"].token
    with pytest.raises(InteractionStale):
        service.mark_capability_unavailable(
            context["terminal_id"], context["incarnation_id"], "x" * len(token)
        )
    assert (
        service.source_status(
            context["terminal_id"], context["incarnation_id"]
        ).capability_available
        is True
    )

    assert (
        service.mark_capability_unavailable(
            context["terminal_id"], context["incarnation_id"], token
        )
        is True
    )
    assert (
        service.mark_capability_unavailable(
            context["terminal_id"], context["incarnation_id"], token
        )
        is False
    )
    with pytest.raises(InteractionForbidden):
        service.authenticate_agent(token)
    with pytest.raises(InteractionForbidden):
        service.agent_list(token)

    status = service.source_status(context["terminal_id"], context["incarnation_id"])
    assert status.state == "current"
    assert status.capability_available is False
    matrix = service.capabilities(actor=context["owner"])
    assert len(matrix) == 1
    assert matrix[0].emits_explicit is False
    assert matrix[0].required_mcp_tools == ()
    assert matrix[0].detects_native is True
    assert "capability_available" not in context["source"].source

    with connect(context["target"], readonly=True) as connection:
        window_name = connection.execute(
            "SELECT tmux_window FROM terminals WHERE id=%s", (context["terminal_id"],)
        ).fetchone()[0]
    observation = NativeInteractionObservation.manual_signal(
        screen_lines=["The provider has a manual dialog."],
        terminal_id=context["terminal_id"],
        session_name="interaction-test-" + context["terminal_id"][2:],
        window_name=window_name,
    )
    assert observation is not None
    receipt = service.observe_native(
        context["terminal_id"], context["incarnation_id"], 1, observation
    )
    assert receipt.response_capability == "manual_only"
    assert service.get_detail(context["owner"], receipt.question_id)["lifecycle"] == "manual_only"


def test_pending_capability_requires_exact_token_activation(interaction_context):
    context = interaction_context
    service = context["service"]
    issued = service.register_source(
        terminal_id=context["terminal_id"],
        incarnation_id=context["incarnation_id"],
        session_name="interaction-test-" + context["terminal_id"][2:],
        provider="codex",
        principal=context["owner"],
        capability_available=False,
    )
    token = issued.token
    status = service.source_status(context["terminal_id"], context["incarnation_id"])
    assert status.state == "current"
    assert status.capability_available is False
    assert service.capabilities(actor=context["owner"])[0].emits_explicit is False
    with pytest.raises(InteractionForbidden):
        service.authenticate_agent(token)
    with pytest.raises(InteractionStale):
        service.activate_capability(
            context["terminal_id"], context["incarnation_id"], "x" * len(token)
        )
    assert (
        service.source_status(
            context["terminal_id"], context["incarnation_id"]
        ).capability_available
        is False
    )

    assert (
        service.activate_capability(context["terminal_id"], context["incarnation_id"], token)
        is True
    )
    assert (
        service.activate_capability(context["terminal_id"], context["incarnation_id"], token)
        is False
    )
    assert service.authenticate_agent(token).terminal_id == context["terminal_id"]
    assert (
        service.source_status(
            context["terminal_id"], context["incarnation_id"]
        ).capability_available
        is True
    )


def test_failed_capability_activation_transaction_leaves_source_unavailable(
    interaction_context, monkeypatch
):
    context = interaction_context
    service = context["service"]
    issued = service.register_source(
        terminal_id=context["terminal_id"],
        incarnation_id=context["incarnation_id"],
        session_name="interaction-test-" + context["terminal_id"][2:],
        provider="codex",
        principal=context["owner"],
        capability_available=False,
    )
    append_event = service.repository.append_event

    def fail_activation_event(connection, **kwargs):
        if kwargs.get("event_type") == "interaction.source_capability_activated":
            raise RuntimeError("injected activation event failure")
        return append_event(connection, **kwargs)

    monkeypatch.setattr(service.repository, "append_event", fail_activation_event)
    with pytest.raises(RuntimeError, match="injected activation event failure"):
        service.activate_capability(context["terminal_id"], context["incarnation_id"], issued.token)
    assert (
        service.source_status(
            context["terminal_id"], context["incarnation_id"]
        ).capability_available
        is False
    )
    with pytest.raises(InteractionForbidden):
        service.authenticate_agent(issued.token)
    assert service.capabilities(actor=context["owner"])[0].emits_explicit is False

    monkeypatch.setattr(service.repository, "append_event", append_event)
    assert (
        service.activate_capability(context["terminal_id"], context["incarnation_id"], issued.token)
        is True
    )


def test_answer_is_one_effective_payload_per_version(interaction_context):
    context = interaction_context
    service = context["service"]
    group = service.create_group(
        context["source"].token,
        CreateGroupRequest(
            questions=[QuestionInput(kind="text", prompt="Question", required=True)]
        ),
        "answer-cas-operation-0001",
    )
    question_id = group.questions[0]["question_id"]
    payload = InteractionAnswerPayload(kind="text", text="same answer")
    first = service.accept_answer(
        context["owner"], question_id, 1, payload, "answer-idempotency-key-0002"
    )
    replay = service.accept_answer(
        context["owner"], question_id, 1, payload, "answer-idempotency-key-0003"
    )
    assert replay.answer_id == first.answer_id
    assert replay.replayed is True
    with pytest.raises(InteractionConflict):
        service.accept_answer(
            context["owner"],
            question_id,
            1,
            InteractionAnswerPayload(kind="text", text="different answer"),
            "answer-idempotency-key-0004",
        )


def test_numeric_answer_to_ordinary_question_remains_valid(interaction_context):
    context = interaction_context
    group = context["service"].create_group(
        context["source"].token,
        CreateGroupRequest(
            questions=(QuestionInput(kind="text", prompt="What numeric result should I record?"),)
        ),
        "numeric-answer-operation-0001",
    )
    accepted = context["service"].accept_answer(
        context["owner"],
        group.questions[0]["question_id"],
        1,
        InteractionAnswerPayload(kind="text", text="123456"),
        "numeric-answer-idempotency-0001",
    )
    assert accepted.delivery_state == "accepted"


def test_turn_advance_rejects_required_wait_and_stales_optional_question(interaction_context):
    context = interaction_context
    service = context["service"]
    capability = context["source"].token
    group = service.create_group(
        capability,
        CreateGroupRequest(
            questions=[QuestionInput(kind="text", prompt="Question", required=False)]
        ),
        "stale-turn-operation-0001",
    )
    next_generation = service.advance_turn(
        context["terminal_id"],
        context["incarnation_id"],
        reason="direct_input",
        idempotency_key="terminal-input-idempotency-001",
    )
    assert next_generation == 2
    assert (
        service.advance_turn(
            context["terminal_id"],
            context["incarnation_id"],
            reason="direct_input",
            idempotency_key="terminal-input-idempotency-001",
        )
        == 2
    )
    with pytest.raises(InteractionStale):
        service.accept_answer(
            context["owner"],
            group.questions[0]["question_id"],
            1,
            InteractionAnswerPayload(kind="text", text="late"),
            "answer-idempotency-key-0005",
        )


def test_required_interaction_blocks_turn_advance_even_when_wait_flag_is_false(interaction_context):
    context = interaction_context
    group = context["service"].create_group(
        context["source"].token,
        CreateGroupRequest(
            wait_for_required=False,
            questions=[QuestionInput(kind="text", prompt="Required question", required=True)],
        ),
        "required-edge-operation-0001",
    )
    assert group.wait_required is True
    with pytest.raises(InteractionConflict):
        context["service"].advance_turn(
            context["terminal_id"],
            context["incarnation_id"],
            reason="direct_input",
            idempotency_key="required-edge-turn-id-001",
        )


def test_cancelling_optional_member_preserves_required_group_and_wait(interaction_context):
    context = interaction_context
    service = context["service"]
    group = service.create_group(
        context["source"].token,
        CreateGroupRequest(
            questions=(
                QuestionInput(kind="text", prompt="Required", required=True),
                QuestionInput(kind="text", prompt="Optional", required=False),
            )
        ),
        "mixed-group-operation-0001",
    )
    required_id, optional_id = [item["question_id"] for item in group.questions]

    service.cancel(context["owner"], optional_id, 1, "optional request is not needed")

    with service.repository.read_connection() as connection:
        lifecycle = connection.execute(
            "SELECT lifecycle FROM interaction_groups WHERE group_id=%s", (group.group_id,)
        ).fetchone()[0]
        wait_state = connection.execute(
            "SELECT state FROM interaction_waits WHERE group_id=%s", (group.group_id,)
        ).fetchone()[0]
        required_state = connection.execute(
            "SELECT lifecycle FROM interaction_questions WHERE question_id=%s", (required_id,)
        ).fetchone()[0]
    assert lifecycle == "open"
    assert wait_state == "waiting"
    assert required_state == "open"
    assert service.has_required_wait(context["terminal_id"], context["incarnation_id"])


def test_server_derived_delegated_wait_cycle_is_marked_and_revoke_clears_it(
    interaction_context,
):
    context = interaction_context
    service = context["service"]
    owner = context["owner"]
    suffix = uuid.uuid4().hex[:8]
    left_id, right_id = "cy" + suffix + "a", "cy" + suffix + "b"
    left_inc, right_inc = "cy-inc-a-" + suffix, "cy-inc-b-" + suffix
    left_session, right_session = "cycle-left-" + suffix, "cycle-right-" + suffix
    database.create_terminal(
        left_id,
        left_session,
        "worker",
        "codex",
        allowed_tools=["@cao-mcp-server"],
        session_incarnation_id=left_inc,
        new_session_incarnation=True,
    )
    database.create_terminal(
        right_id,
        right_session,
        "worker",
        "codex",
        allowed_tools=["@cao-mcp-server"],
        session_incarnation_id=right_inc,
        new_session_incarnation=True,
    )
    service.register_source(
        terminal_id=left_id,
        incarnation_id=left_inc,
        session_name=left_session,
        provider="codex",
        principal=owner,
    )
    service.register_source(
        terminal_id=right_id,
        incarnation_id=right_inc,
        session_name=right_session,
        provider="codex",
        principal=owner,
    )
    left_assignment, right_assignment = (
        "cycle-left-assignment-" + suffix,
        "cycle-right-assignment-" + suffix,
    )
    with database.SessionLocal() as connection:
        connection.add_all(
            (
                database.AssignmentIntentModel(
                    assignment_id=left_assignment,
                    owner=owner.id,
                    parent_terminal_id=right_id,
                    parent_incarnation_id=right_inc,
                    operation_key="cycle-left-operation-" + suffix,
                    request_hash="cycle-left-request-" + suffix,
                    state="submitted",
                    result_json=json.dumps(
                        {"success": True, "terminal_id": left_id, "assignment_id": left_assignment}
                    ),
                ),
                database.AssignmentIntentModel(
                    assignment_id=right_assignment,
                    owner=owner.id,
                    parent_terminal_id=left_id,
                    parent_incarnation_id=left_inc,
                    operation_key="cycle-right-operation-" + suffix,
                    request_hash="cycle-right-request-" + suffix,
                    state="submitted",
                    result_json=json.dumps(
                        {
                            "success": True,
                            "terminal_id": right_id,
                            "assignment_id": right_assignment,
                        }
                    ),
                ),
            )
        )
        connection.commit()
    left = service.register_source(
        terminal_id=left_id,
        incarnation_id=left_inc,
        session_name=left_session,
        provider="codex",
        assignment_id=left_assignment,
    )
    right = service.register_source(
        terminal_id=right_id,
        incarnation_id=right_inc,
        session_name=right_session,
        provider="codex",
        assignment_id=right_assignment,
    )
    left_group = service.create_group(
        left.token,
        CreateGroupRequest(
            questions=(QuestionInput(kind="text", prompt="Left clarification?", required=True),)
        ),
        "cycle-left-question-operation-" + suffix,
    )
    right_group = service.create_group(
        right.token,
        CreateGroupRequest(
            questions=(QuestionInput(kind="text", prompt="Right clarification?", required=True),)
        ),
        "cycle-right-question-operation-" + suffix,
    )
    service.delegate_question(
        owner,
        left_group.questions[0]["question_id"],
        1,
        expected_assignment_id=left_assignment,
        reason="Reviewed left clarification",
    )
    service.delegate_question(
        owner,
        right_group.questions[0]["question_id"],
        1,
        expected_assignment_id=right_assignment,
        reason="Reviewed right clarification",
    )

    graph = service.wait_graph(owner)
    cycle_edges = [edge for edge in graph["edges"] if edge["cycle"]]
    assert len(cycle_edges) == 4
    assert all(edge["required"] for edge in cycle_edges)
    with service.repository.read_connection() as connection:
        states = connection.execute(
            "SELECT group_id,lifecycle FROM interaction_groups WHERE group_id IN (%s,%s) ORDER BY group_id",
            (left_group.group_id, right_group.group_id),
        ).fetchall()
        wait_states = connection.execute(
            "SELECT group_id,state FROM interaction_waits WHERE group_id IN (%s,%s) ORDER BY group_id",
            (left_group.group_id, right_group.group_id),
        ).fetchall()
        assert {row["lifecycle"] for row in states} == {"cycle_blocked"}
        assert {row["state"] for row in wait_states} == {"cycle_blocked"}
        assert connection.execute("SELECT count(*) FROM interaction_answers").fetchone()[0] == 0

    # An explicit group adds only task -> question_group. It has no reviewed
    # delegation edge back to a supervisor, so it is a graph sink even while
    # this source participates in an existing delegated cycle. The stored
    # cycle state must remain intact without needing a post-insert graph pass.
    leaf_group = service.create_group(
        left.token,
        CreateGroupRequest(
            questions=(
                QuestionInput(kind="text", prompt="Independent clarification?", required=True),
            )
        ),
        "cycle-independent-leaf-operation-" + suffix,
    )
    leaf_question_id = leaf_group.questions[0]["question_id"]
    with service.repository.read_connection() as connection:
        states = connection.execute(
            "SELECT group_id,lifecycle FROM interaction_groups "
            "WHERE group_id IN (%s,%s,%s) ORDER BY group_id",
            (left_group.group_id, right_group.group_id, leaf_group.group_id),
        ).fetchall()
        wait_states = connection.execute(
            "SELECT group_id,state FROM interaction_waits "
            "WHERE group_id IN (%s,%s,%s) ORDER BY group_id",
            (left_group.group_id, right_group.group_id, leaf_group.group_id),
        ).fetchall()
        leaf_delegations = connection.execute(
            "SELECT count(*) FROM interaction_delegations WHERE question_id=%s",
            (leaf_question_id,),
        ).fetchone()[0]
        assert {row["group_id"]: row["lifecycle"] for row in states} == {
            left_group.group_id: "cycle_blocked",
            right_group.group_id: "cycle_blocked",
            leaf_group.group_id: "open",
        }
        assert {row["group_id"]: row["state"] for row in wait_states} == {
            left_group.group_id: "cycle_blocked",
            right_group.group_id: "cycle_blocked",
            leaf_group.group_id: "waiting",
        }
        assert leaf_delegations == 0

    service.revoke_delegation(
        owner,
        left_group.questions[0]["question_id"],
        2,
        reason="Owner revoked the reviewed cycle edge.",
    )
    with service.repository.read_connection() as connection:
        states = connection.execute(
            "SELECT group_id,lifecycle FROM interaction_groups "
            "WHERE group_id IN (%s,%s) ORDER BY group_id",
            (left_group.group_id, right_group.group_id),
        ).fetchall()
        wait_states = connection.execute(
            "SELECT group_id,state FROM interaction_waits "
            "WHERE group_id IN (%s,%s) ORDER BY group_id",
            (left_group.group_id, right_group.group_id),
        ).fetchall()
        assert {row["lifecycle"] for row in states} == {"open"}
        assert {row["state"] for row in wait_states} == {"waiting"}

    service.cancel(
        owner, left_group.questions[0]["question_id"], 3, "Operator cancels the reopened group"
    )
    with service.repository.read_connection() as connection:
        assert (
            connection.execute(
                "SELECT lifecycle FROM interaction_groups WHERE group_id=%s", (left_group.group_id,)
            ).fetchone()[0]
            == "cancelled"
        )
        assert (
            connection.execute(
                "SELECT lifecycle FROM interaction_groups WHERE group_id=%s",
                (right_group.group_id,),
            ).fetchone()[0]
            == "open"
        )
        assert (
            connection.execute(
                "SELECT state FROM interaction_waits WHERE group_id=%s", (right_group.group_id,)
            ).fetchone()[0]
            == "waiting"
        )
        assert connection.execute("SELECT count(*) FROM interaction_answers").fetchone()[0] == 0


def test_native_manual_notice_is_content_free_and_exact_close_survives_turn_bump(
    interaction_context,
):
    from cli_agent_orchestrator.providers.mcp_interactions import NativeInteractionObservation

    context = interaction_context
    service = context["service"]
    with connect(context["target"], readonly=True) as connection:
        window_name = connection.execute(
            "SELECT tmux_window FROM terminals WHERE id=%s", (context["terminal_id"],)
        ).fetchone()[0]
    observation = NativeInteractionObservation.manual_signal(
        screen_lines=["A provider-owned private question is visible."],
        terminal_id=context["terminal_id"],
        session_name="interaction-test-" + context["terminal_id"][2:],
        window_name=window_name,
    )
    assert observation is not None
    receipt = service.observe_native(
        context["terminal_id"], context["incarnation_id"], 1, observation
    )
    assert receipt.response_capability == "manual_only"
    pending_native = asyncio.run(
        service.wait_for_native_manual(
            context["terminal_id"],
            context["incarnation_id"],
            timeout_seconds=0,
            expected_generation=1,
        )
    )
    assert pending_native.state == "waiting"
    assert pending_native.observation_id == receipt.observation_id
    assert service.has_required_wait(context["terminal_id"], context["incarnation_id"], 1) is False
    detail = service.get_detail(context["owner"], receipt.question_id)
    assert detail["prompt"] == "A native provider dialog requires manual action in its terminal."
    assert detail["options"] == []
    with connect(context["target"], readonly=True) as connection:
        stored_prompt = connection.execute(
            "SELECT prompt_content FROM interaction_questions WHERE question_id=%s",
            (receipt.question_id,),
        ).fetchone()[0]
        assert "private question" not in stored_prompt

    assert (
        service.advance_turn(
            context["terminal_id"],
            context["incarnation_id"],
            reason="websocket_input",
            idempotency_key="native-dialog-input-id-001",
        )
        == 2
    )
    current_group = service.create_group(
        context["source"].token,
        CreateGroupRequest(
            questions=(
                QuestionInput(kind="text", prompt="Current required question", required=True),
            )
        ),
        "native-close-preserves-current-wait-001",
    )
    assert service.has_required_wait(context["terminal_id"], context["incarnation_id"], 2) is True
    waiting_for_exact_close = asyncio.run(
        service.wait_for_native_manual(
            context["terminal_id"],
            context["incarnation_id"],
            timeout_seconds=0,
            expected_generation=1,
        )
    )
    assert waiting_for_exact_close.state == "waiting"
    assert waiting_for_exact_close.lifecycle == "stale"
    assert waiting_for_exact_close.observation_id == receipt.observation_id
    closed = service.reconcile_native_observation(
        context["terminal_id"],
        context["incarnation_id"],
        1,
        observation_id=receipt.observation_id,
        dialog_fingerprint=observation.dialog_fingerprint,
        provider_evidence_version=observation.dialog_version,
    )
    assert closed.lifecycle == "reconciled_external"
    released = asyncio.run(
        service.wait_for_native_manual(
            context["terminal_id"],
            context["incarnation_id"],
            timeout_seconds=0,
            expected_generation=1,
        )
    )
    assert released.state == "released"
    assert released.lifecycle == "reconciled_external"
    with service.repository.read_connection() as connection:
        metadata_json = connection.execute(
            "SELECT metadata_json FROM interaction_events WHERE question_id=%s "
            "AND event_type='interaction.reconciled'",
            (receipt.question_id,),
        ).fetchone()[0]
    assert json.loads(metadata_json)["outcome"] == "closed_outside_cao"
    assert released.current_generation == 2
    assert service.has_required_wait(context["terminal_id"], context["incarnation_id"], 2) is True
    with connect(context["target"], readonly=True) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM interaction_answers WHERE question_id=%s",
                (receipt.question_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT lifecycle FROM interaction_groups WHERE group_id=%s",
                (current_group.group_id,),
            ).fetchone()[0]
            == "open"
        )


def test_reopened_native_dialog_gets_new_occurrence_after_exact_close(interaction_context):
    from cli_agent_orchestrator.providers.mcp_interactions import NativeInteractionObservation

    context = interaction_context
    service = context["service"]
    with connect(context["target"], readonly=True) as connection:
        window_name = connection.execute(
            "SELECT tmux_window FROM terminals WHERE id=%s", (context["terminal_id"],)
        ).fetchone()[0]

    def observe_same_dialog():
        result = NativeInteractionObservation.manual_signal(
            screen_lines=["A provider-owned dialog is visible again."],
            terminal_id=context["terminal_id"],
            session_name="interaction-test-" + context["terminal_id"][2:],
            window_name=window_name,
        )
        assert result is not None
        return result

    first_observation = observe_same_dialog()
    first = service.observe_native(
        context["terminal_id"], context["incarnation_id"], 1, first_observation
    )
    duplicate = service.observe_native(
        context["terminal_id"], context["incarnation_id"], 1, observe_same_dialog()
    )
    assert duplicate.question_id == first.question_id
    assert duplicate.observation_id == first.observation_id
    assert duplicate.replayed is True

    closed = service.reconcile_native_observation(
        context["terminal_id"],
        context["incarnation_id"],
        1,
        observation_id=first.observation_id,
        dialog_fingerprint=first_observation.dialog_fingerprint,
        provider_evidence_version=first_observation.dialog_version,
    )
    assert closed.lifecycle == "reconciled_external"
    assert not service.has_unresolved_native_notice(
        context["terminal_id"], context["incarnation_id"]
    )

    reopened_observation = observe_same_dialog()
    reopened = service.observe_native(
        context["terminal_id"], context["incarnation_id"], 1, reopened_observation
    )
    assert reopened.question_id != first.question_id
    assert reopened.observation_id != first.observation_id
    assert reopened.lifecycle == "manual_only"
    assert service.has_unresolved_native_notice(context["terminal_id"], context["incarnation_id"])

    duplicate_reopened = service.observe_native(
        context["terminal_id"], context["incarnation_id"], 1, observe_same_dialog()
    )
    assert duplicate_reopened.question_id == reopened.question_id
    assert duplicate_reopened.observation_id == reopened.observation_id
    assert duplicate_reopened.replayed is True


def test_concurrent_native_observation_of_same_dialog_converges(interaction_context):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from cli_agent_orchestrator.providers.mcp_interactions import NativeInteractionObservation

    context = interaction_context
    service = context["service"]
    observation = NativeInteractionObservation.manual_signal(
        screen_lines=["A provider-owned dialog is visible."],
        terminal_id=context["terminal_id"],
        session_name="interaction-test-" + context["terminal_id"][2:],
        window_name="worker",
    )
    assert observation is not None
    barrier = Barrier(2)

    def observe():
        barrier.wait(timeout=10)
        return service.observe_native(
            context["terminal_id"], context["incarnation_id"], 1, observation
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = [pool.submit(observe) for _ in range(2)]
        receipts = [first.result(), second.result()]

    assert receipts[0].question_id == receipts[1].question_id
    assert receipts[0].observation_id == receipts[1].observation_id
    assert sorted(receipt.replayed for receipt in receipts) == [False, True]
    with service.repository.read_connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM interaction_questions "
                "WHERE terminal_id=%s AND incarnation_id=%s AND source_kind='native'",
                (context["terminal_id"], context["incarnation_id"]),
            ).fetchone()[0]
            == 1
        )


def test_native_notice_blocks_non_manual_turn_advance(interaction_context):
    from cli_agent_orchestrator.providers.mcp_interactions import NativeInteractionObservation

    context = interaction_context
    service = context["service"]
    with connect(context["target"], readonly=True) as connection:
        window_name = connection.execute(
            "SELECT tmux_window FROM terminals WHERE id=%s", (context["terminal_id"],)
        ).fetchone()[0]
    observation = NativeInteractionObservation.manual_signal(
        screen_lines=["A provider dialog requires manual action."],
        terminal_id=context["terminal_id"],
        session_name="interaction-test-" + context["terminal_id"][2:],
        window_name=window_name,
    )
    assert observation is not None
    receipt = service.observe_native(
        context["terminal_id"], context["incarnation_id"], 1, observation
    )

    with pytest.raises(InteractionConflict, match="provider-owned native interaction"):
        service.advance_turn(
            context["terminal_id"],
            context["incarnation_id"],
            reason="task_delivery",
            idempotency_key="native-task-delivery-turn-001",
        )
    assert (
        service.source_status(context["terminal_id"], context["incarnation_id"]).current_generation
        == 1
    )
    assert service.has_unresolved_native_notice(context["terminal_id"], context["incarnation_id"])
    with service.repository.read_connection() as connection:
        assert (
            connection.execute(
                "SELECT lifecycle FROM interaction_questions WHERE question_id=%s",
                (receipt.question_id,),
            ).fetchone()[0]
            == "manual_only"
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM interaction_answers WHERE question_id=%s",
                (receipt.question_id,),
            ).fetchone()[0]
            == 0
        )


def test_native_wait_reports_exact_close_expiry_and_unregistered(interaction_context):
    from cli_agent_orchestrator.providers.mcp_interactions import NativeInteractionObservation

    context = interaction_context
    service = context["service"]
    with connect(context["target"], readonly=True) as connection:
        window_name = connection.execute(
            "SELECT tmux_window FROM terminals WHERE id=%s", (context["terminal_id"],)
        ).fetchone()[0]
    observation = NativeInteractionObservation.manual_signal(
        screen_lines=["The native dialog is open."],
        terminal_id=context["terminal_id"],
        session_name="interaction-test-" + context["terminal_id"][2:],
        window_name=window_name,
    )
    assert observation is not None
    receipt = service.observe_native(
        context["terminal_id"], context["incarnation_id"], 1, observation
    )
    closed = service.reconcile_native_observation(
        context["terminal_id"],
        context["incarnation_id"],
        1,
        observation_id=receipt.observation_id,
        dialog_fingerprint=observation.dialog_fingerprint,
        provider_evidence_version=observation.dialog_version,
    )
    assert closed.lifecycle == "reconciled_external"
    released = asyncio.run(
        service.wait_for_native_manual(
            context["terminal_id"],
            context["incarnation_id"],
            timeout_seconds=0,
            expected_generation=1,
        )
    )
    assert released.state == "released"
    assert released.lifecycle == "reconciled_external"

    expired_observation = NativeInteractionObservation.manual_signal(
        screen_lines=["A second native dialog is open with a different viewport."],
        terminal_id=context["terminal_id"],
        session_name="interaction-test-" + context["terminal_id"][2:],
        window_name=window_name,
    )
    assert expired_observation is not None
    expired_receipt = service.observe_native(
        context["terminal_id"], context["incarnation_id"], 1, expired_observation
    )
    with service.repository.transaction() as connection:
        connection.execute(
            "UPDATE interaction_questions SET expires_at=%s WHERE question_id=%s",
            (time.time() - 1, expired_receipt.question_id),
        )
    expired = asyncio.run(
        service.wait_for_native_manual(
            context["terminal_id"],
            context["incarnation_id"],
            timeout_seconds=0,
            expected_generation=1,
        )
    )
    assert expired.state == "expired"
    assert (
        asyncio.run(
            service.wait_for_native_manual(
                "ix-unregistered-native",
                "inc-unregistered-native",
                timeout_seconds=0,
                expected_generation=1,
            )
        ).state
        == "unregistered"
    )


def test_password_question_is_manual_only_and_raw_prompt_is_not_persisted(interaction_context):
    context = interaction_context
    group = context["service"].create_group(
        context["source"].token,
        CreateGroupRequest(
            questions=(
                QuestionInput(
                    kind="choice_or_text",
                    prompt="What is your password?",
                    allow_free_text=True,
                ),
            )
        ),
        "credential-question-operation-001",
    )
    question_id = group.questions[0]["question_id"]
    detail = context["service"].get_detail(context["owner"], question_id)
    assert detail["kind"] == "credential"
    assert detail["lifecycle"] == "manual_only"
    assert detail["prompt"] == "A credential or secret request requires manual action."
    with connect(context["target"], readonly=True) as connection:
        stored = connection.execute(
            "SELECT prompt_content,options_json FROM interaction_questions WHERE question_id=%s",
            (question_id,),
        ).fetchone()
    assert "password" not in stored[0].lower()
    assert stored[1] == "[]"


def test_common_pin_and_recovery_prompts_are_manual_only(interaction_context):
    context = interaction_context
    prompts = (
        "Enter your 6-digit PIN to continue.",
        "Please type your passcode.",
        "What is your MFA code?",
        "Enter the recovery code.",
        "Please provide the security code.",
        "Introduce tu código de recuperación.",
        "Enter your verification code.",
        "Provide the OTP.",
        "Use the one-time password.",
        "Enter your login code.",
        "Use your backup code.",
        "Introduce tu contraseña de un solo uso.",
        "Introduce tu código de inicio de sesión.",
    )
    created = context["service"].create_group(
        context["source"].token,
        CreateGroupRequest(
            questions=tuple(QuestionInput(kind="text", prompt=prompt) for prompt in prompts)
        ),
        "common-credential-prompts-operation-01",
    )
    for question in created.questions:
        assert question["lifecycle"] == "manual_only"
        detail = context["service"].get_detail(context["owner"], question["question_id"])
        assert detail["kind"] == "credential"
        assert detail["prompt"] == "A credential or secret request requires manual action."


def test_operator_reasons_are_private_auditable_and_reject_known_secrets(interaction_context):
    context = interaction_context
    service = context["service"]
    owner_reason = "Operator verified ownership with the terminal maintainer."
    cancel_reason = "This request is obsolete after checking the active task."
    service.designate_content_owner(
        context["owner"],
        context["terminal_id"],
        context["incarnation_id"],
        owner_reason,
    )
    group = service.create_group(
        context["source"].token,
        CreateGroupRequest(questions=(QuestionInput(kind="text", prompt="Question to cancel"),)),
        "private-audit-question-operation-01",
    )
    question_id = group.questions[0]["question_id"]
    with service.repository.read_connection() as connection:
        event_count_before_reject = connection.execute(
            "SELECT count(*) FROM interaction_events"
        ).fetchone()[0]
    with pytest.raises(InteractionInvalid):
        service.cancel(
            context["owner"],
            question_id,
            1,
            "Cancel because this test token is sk-AbCd.",
        )
    with service.repository.read_connection() as connection:
        assert (
            connection.execute("SELECT count(*) FROM interaction_events").fetchone()[0]
            == event_count_before_reject
        )
        assert (
            connection.execute("SELECT count(*) FROM interaction_private_audit").fetchone()[0] == 1
        )

    service.cancel(context["owner"], question_id, 1, cancel_reason)
    with service.repository.read_connection() as connection:
        rows = connection.execute(
            "SELECT action,operator_principal_id,target_terminal_id,target_incarnation_id,"
            "target_question_id,reason FROM interaction_private_audit ORDER BY event_sequence"
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        (
            "content_owner_designated",
            context["owner"].id,
            context["terminal_id"],
            context["incarnation_id"],
            None,
            owner_reason,
        ),
        (
            "question_cancelled",
            context["owner"].id,
            None,
            None,
            question_id,
            cancel_reason,
        ),
    ]
    public_projection = {
        "detail": service.get_detail(context["owner"], question_id),
        "history": [
            event.model_dump(mode="json")
            for event in service.history(context["owner"], question_id)
        ],
        "metadata": service.list_metadata(context["owner"]).model_dump(mode="json"),
    }
    encoded_public = json.dumps(public_projection, default=str)
    assert owner_reason not in encoded_public
    assert cancel_reason not in encoded_public


@pytest.mark.parametrize("evidence_code", ["sk-AbCd", "123456", "verification code123456"])
def test_reconciliation_rejects_secret_evidence_codes_before_history_write(
    interaction_context, evidence_code
):
    context = interaction_context
    group = context["service"].create_group(
        context["source"].token,
        CreateGroupRequest(questions=(QuestionInput(kind="text", prompt="Question"),)),
        "private-audit-evidence-operation-01",
    )
    question_id = group.questions[0]["question_id"]
    with context["service"].repository.read_connection() as connection:
        before = connection.execute("SELECT count(*) FROM interaction_events").fetchone()[0]
    with pytest.raises(InteractionInvalid):
        context["service"].reconcile(
            context["owner"], question_id, 1, "dialog_still_open", evidence_code
        )
    with context["service"].repository.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM interaction_events").fetchone()[0] == before


@pytest.mark.parametrize(
    "prompt",
    ["Paste this test token sk-AA here.", "The password is hunter2."],
)
def test_literal_secret_prompt_is_rejected_before_interaction_rows_are_written(
    interaction_context, prompt
):
    context = interaction_context
    with context["service"].repository.read_connection() as connection:
        before = connection.execute("SELECT count(*) FROM interaction_questions").fetchone()[0]
    with pytest.raises(InteractionInvalid):
        context["service"].create_group(
            context["source"].token,
            CreateGroupRequest(questions=(QuestionInput(kind="text", prompt=prompt),)),
            "literal-secret-question-operation-001",
        )
    with context["service"].repository.read_connection() as connection:
        assert (
            connection.execute("SELECT count(*) FROM interaction_questions").fetchone()[0] == before
        )


def test_source_registration_honors_mcp_wildcard_but_not_bare_server_name(interaction_context):
    service = interaction_context["service"]
    wildcard_id = "iw" + uuid.uuid4().hex[:8]
    wildcard_session = "wildcard-session-" + uuid.uuid4().hex[:8]
    wildcard_incarnation = "wildcard-inc-" + uuid.uuid4().hex[:8]
    database.create_terminal(
        wildcard_id,
        wildcard_session,
        "worker",
        "codex",
        allowed_tools=["*"],
        session_incarnation_id=wildcard_incarnation,
        new_session_incarnation=True,
    )
    assert service.register_source(
        terminal_id=wildcard_id,
        incarnation_id=wildcard_incarnation,
        session_name=wildcard_session,
        provider="codex",
    ).token

    bare_id = "ib" + uuid.uuid4().hex[:8]
    bare_session = "bare-session-" + uuid.uuid4().hex[:8]
    bare_incarnation = "bare-inc-" + uuid.uuid4().hex[:8]
    database.create_terminal(
        bare_id,
        bare_session,
        "worker",
        "codex",
        allowed_tools=["cao-mcp-server"],
        session_incarnation_id=bare_incarnation,
        new_session_incarnation=True,
    )
    with pytest.raises(InteractionForbidden):
        service.register_source(
            terminal_id=bare_id,
            incarnation_id=bare_incarnation,
            session_name=bare_session,
            provider="codex",
        )


def test_agent_cannot_read_an_unrelated_source_and_revoked_tool_grant_fails(interaction_context):
    context = interaction_context
    observer = auth._verified_principal("https://test.issuer", "observer", [auth.SCOPE_READ], "jwt")
    group = context["service"].create_group(
        context["source"].token,
        CreateGroupRequest(questions=[QuestionInput(kind="text", prompt="private", required=True)]),
        "private-source-operation-0001",
    )
    metadata = context["service"].list_metadata(observer)
    assert metadata.items and metadata.items[0]["question_id"] == group.questions[0]["question_id"]
    other_id = "iy" + uuid.uuid4().hex[:8]
    other_session = "other-session-" + uuid.uuid4().hex[:8]
    other_inc = "other-inc-" + uuid.uuid4().hex[:8]
    database.create_terminal(
        other_id,
        other_session,
        "worker",
        "codex",
        allowed_tools=["@cao-mcp-server"],
        session_incarnation_id=other_inc,
        new_session_incarnation=True,
    )
    other = context["service"].register_source(
        terminal_id=other_id,
        incarnation_id=other_inc,
        session_name=other_session,
        provider="codex",
        principal=observer,
    )
    with pytest.raises(InteractionForbidden):
        context["service"].get_detail(other.token, group.questions[0]["question_id"])

    with database.SessionLocal() as connection:
        terminal = connection.get(database.TerminalModel, context["terminal_id"])
        terminal.allowed_tools = json.dumps([])
        connection.commit()
    with pytest.raises(InteractionForbidden):
        context["service"].authenticate_agent(context["source"].token)
