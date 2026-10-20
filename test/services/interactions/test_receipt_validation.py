"""Offline encoding contracts for opaque interaction answer receipts."""

from contextlib import contextmanager

import pytest
from pydantic import ValidationError

from cli_agent_orchestrator.services.interactions.models import (
    AnswerAck,
    InteractionAnswerPayload,
    InteractionInvalid,
    QuestionInput,
)
from cli_agent_orchestrator.services.interactions.service import InteractionService

QUESTION_ID = "d97001ba-d4ed-4c78-b3b9-22fa8c77631d"
GROUP_ID = "2e1fc625-a391-4bfd-a62d-4dddbcbf70a3"
NON_ASCII_RECEIPT = "r" * 42 + "ñ"
ASCII_RECEIPT = "r" * 43
pytestmark = pytest.mark.offline_database


def test_answer_ack_rejects_non_ascii_receipts_and_accepts_ascii_receipts():
    with pytest.raises(ValidationError):
        AnswerAck.model_validate(
            {"question_id": QUESTION_ID, "version": 1, "receipt": NON_ASCII_RECEIPT}
        )

    accepted = AnswerAck.model_validate(
        {"question_id": QUESTION_ID, "version": 1, "receipt": ASCII_RECEIPT}
    )
    assert accepted.receipt == ASCII_RECEIPT


def test_unicode_prompts_and_free_text_answers_remain_valid():
    question = QuestionInput(kind="text", prompt="¿Puedes revisar el café y 日本語?")
    answer = InteractionAnswerPayload(kind="free_text", text="Sí: español, café y 日本語.")

    assert "日本語" in question.prompt
    assert "español" in answer.text


def test_service_rejects_forged_non_ascii_receipt_before_transaction(monkeypatch):
    service = object.__new__(InteractionService)

    @contextmanager
    def transaction_must_not_start():
        pytest.fail("invalid receipt reached the interaction transaction")
        yield

    monkeypatch.setattr(service, "_transaction", transaction_must_not_start)
    forged_ack = AnswerAck.model_construct(
        question_id=QUESTION_ID,
        version=1,
        receipt=NON_ASCII_RECEIPT,
    )

    with pytest.raises(InteractionInvalid, match="answer receipt is malformed"):
        service.acknowledge_answers("source-capability", GROUP_ID, [forged_ack])
