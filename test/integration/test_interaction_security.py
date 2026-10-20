"""Native PostgreSQL resource authority; no provider CLI or terminal I/O is mocked in.

Source metadata is persisted through the production database client. The sealed
principals stand in for already verified tokens; HTTP identity is tested by the
transport suite separately.
"""

import json
from dataclasses import asdict, is_dataclass
from test.postgres.interactions import postgres_interaction_store
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.interactions.models import (
    CreateGroupRequest,
    QuestionInput,
)
from cli_agent_orchestrator.services.interactions.service import InteractionForbidden

pytestmark = pytest.mark.postgres


def _plain(value):
    if is_dataclass(value):
        return asdict(value)
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


@pytest.fixture
def interaction_security_context(postgres_interaction_store):
    owner = auth._verified_principal("https://test.issuer", "owner", [auth.SCOPE_ADMIN], "jwt")
    observer = auth._verified_principal("https://test.issuer", "observer", [auth.SCOPE_READ], "jwt")
    database.create_terminal(
        "ac010001",
        "interaction-security",
        "worker",
        "codex",
        allowed_tools=["@cao-mcp-server"],
        session_incarnation_id="security-incarnation",
    )
    repository = postgres_interaction_store.repository
    service = postgres_interaction_store.service
    issued = service.register_source(
        terminal_id="ac010001",
        incarnation_id="security-incarnation",
        session_name="interaction-security",
        provider="codex",
        principal=owner,
    )
    group = service.create_group(
        issued.token,
        CreateGroupRequest(
            questions=[
                QuestionInput(kind="text", prompt="private-context-do-not-project", required=True)
            ]
        ),
        "security-question-operation-0001",
    )
    question = _plain(group)["questions"][0]
    return SimpleNamespace(
        owner=owner,
        observer=observer,
        repository=repository,
        service=service,
        token=issued.token,
        question_id=question["question_id"],
    )


def test_observer_metadata_never_confers_content_authority(interaction_security_context):
    context = interaction_security_context
    metadata = _plain(context.service.list_metadata(context.observer))
    serialized = json.dumps(metadata)
    assert context.question_id in serialized
    assert "private-context-do-not-project" not in serialized
    assert context.token not in serialized
    assert "prompt" not in serialized
    with pytest.raises(InteractionForbidden):
        context.service.get_detail(context.observer, context.question_id)
    detail = _plain(context.service.get_detail(context.owner, context.question_id))
    assert detail["prompt"] == "private-context-do-not-project"


def test_matching_unsealed_identity_is_not_a_principal(interaction_security_context):
    context = interaction_security_context
    impostor = SimpleNamespace(
        id=context.owner.id,
        issuer=context.owner.issuer,
        subject=context.owner.subject,
        scopes=context.owner.scopes,
        kind=context.owner.kind,
    )
    with pytest.raises(InteractionForbidden):
        context.service.get_detail(impostor, context.question_id)


def test_live_profile_revocation_blocks_existing_source_capability(interaction_security_context):
    context = interaction_security_context
    with database.SessionLocal() as connection:
        terminal = connection.get(database.TerminalModel, "ac010001")
        terminal.allowed_tools = json.dumps([])
        connection.commit()
    with pytest.raises(InteractionForbidden):
        context.service.authenticate_agent(context.token)


def test_unrelated_source_cannot_read_private_question(interaction_security_context):
    context = interaction_security_context
    database.create_terminal(
        "ac010002",
        "interaction-unrelated",
        "worker",
        "codex",
        allowed_tools=["@cao-mcp-server"],
        session_incarnation_id="unrelated-incarnation",
    )
    other = context.service.register_source(
        terminal_id="ac010002",
        incarnation_id="unrelated-incarnation",
        session_name="interaction-unrelated",
        provider="codex",
        principal=context.observer,
    )
    actor = context.service.authenticate_agent(other.token)
    with pytest.raises(InteractionForbidden):
        context.service.get_detail(actor, context.question_id)


def test_concurrent_identical_answers_commit_one_effective_receipt(interaction_security_context):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from cli_agent_orchestrator.services.interactions.models import InteractionAnswerPayload

    c = interaction_security_context
    barrier = Barrier(8)

    def answer(index):
        barrier.wait(timeout=10)
        return c.service.accept_answer(
            c.owner,
            c.question_id,
            1,
            InteractionAnswerPayload(kind="text", text="One effective answer"),
            f"concurrent-identical-answer-{index:04d}",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(answer, range(8)))
    assert len({receipt.answer_id for receipt in receipts}) == 1
    assert sum(not receipt.replayed for receipt in receipts) == 1
    with c.repository.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM interaction_answers").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM interaction_deliveries").fetchone()[0] == 1


def test_concurrent_divergent_answers_conflict_without_overwriting(interaction_security_context):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from cli_agent_orchestrator.services.interactions.models import (
        InteractionAnswerPayload,
        InteractionConflict,
    )

    c = interaction_security_context
    barrier = Barrier(2)

    def answer(index):
        barrier.wait(timeout=10)
        try:
            return c.service.accept_answer(
                c.owner,
                c.question_id,
                1,
                InteractionAnswerPayload(kind="text", text=f"Alternative {index}"),
                f"concurrent-divergent-answer-{index:04d}",
            )
        except InteractionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(answer, range(2)))
    assert sum(receipt is not None for receipt in receipts) == 1
    with c.repository.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM interaction_answers").fetchone()[0] == 1


def test_capability_token_is_never_persisted_as_plaintext(interaction_security_context):
    c = interaction_security_context
    with c.repository.read_connection() as connection:
        for table in ("interaction_sources", "interaction_events", "interaction_questions"):
            rows = connection.execute(f"SELECT * FROM {table}").fetchall()
            assert c.token not in json.dumps([tuple(row) for row in rows])


@pytest.mark.parametrize(
    "prompt",
    ["What is your password?", "¿Cuál es tu contraseña?", "What is your authentication code?"],
)
def test_credential_request_cannot_store_an_ordinary_answer(interaction_security_context, prompt):
    from cli_agent_orchestrator.services.interactions.models import (
        InteractionAnswerPayload,
        InteractionError,
    )

    c = interaction_security_context
    group = c.service.create_group(
        c.token,
        CreateGroupRequest(questions=[QuestionInput(kind="text", prompt=prompt, required=True)]),
        "credential-request-test-operation",
    )
    q = group.questions[0]
    detail = c.service.get_detail(c.owner, q["question_id"])
    assert detail["lifecycle"] == "manual_only"
    assert detail["kind"] == "credential"
    assert detail["manual_reason"]
    assert detail["can_answer"] is False
    with pytest.raises(InteractionError):
        c.service.accept_answer(
            c.owner,
            q["question_id"],
            1,
            InteractionAnswerPayload(kind="text", text="an arbitrary password"),
            "credential-answer-test-operation",
        )
    with c.repository.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM interaction_answers").fetchone()[0] == 0


def test_recognized_literal_secret_is_rejected_before_question_persistence(
    interaction_security_context,
):
    from cli_agent_orchestrator.services.interactions.models import InteractionError

    c = interaction_security_context
    synthetic_secret = "sk-" + "A" * 24
    with pytest.raises(InteractionError):
        c.service.create_group(
            c.token,
            CreateGroupRequest(
                questions=[
                    QuestionInput(kind="text", prompt=f"Use {synthetic_secret}", required=True)
                ]
            ),
            "literal-secret-test-operation",
        )
    with c.repository.read_connection() as connection:
        for table in (
            "interaction_questions",
            "interaction_question_versions",
            "interaction_events",
        ):
            assert synthetic_secret not in json.dumps(
                [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
            )
