"""Typed domain values for the interaction ledger.

The input models intentionally omit actor, source, recipient, and authority
fields. Those values are resolved from sealed Principals and source records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class InteractionError(Exception):
    """Base error translated by API/MCP adapters into stable public statuses."""


class InteractionForbidden(InteractionError, PermissionError):
    """The actor is valid but lacks current resource authority."""


class InteractionNotFound(InteractionError, LookupError):
    """The requested opaque interaction identity does not exist."""


class InteractionConflict(InteractionError, ValueError):
    """The idempotency key, answer, or compare-and-swap identity conflicts."""


class InteractionStale(InteractionError, ValueError):
    """The source, question version, turn, or Work attempt is no longer current."""

    current_version: int | None = None


class InteractionInvalid(InteractionError, ValueError):
    """The typed question or answer is malformed or unsupported."""


class InteractionCapacityExceeded(InteractionError):
    """Admission was rejected because a configured durable limit is full."""


class _InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)


class _OutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)


QuestionKind = Literal["text", "single_choice", "multi_choice", "choice_or_text", "approval"]


class InteractionOption(_InputModel):
    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=1024)
    available: bool = True


class QuestionInput(_InputModel):
    client_key: str | None = Field(default=None, min_length=1, max_length=128)
    kind: QuestionKind
    prompt: str = Field(min_length=1, max_length=16384)
    required: bool = True
    allow_free_text: bool = False
    options: tuple[InteractionOption, ...] = Field(default_factory=tuple, max_length=32)

    @model_validator(mode="after")
    def validate_question(self):
        if len(self.prompt.encode("utf-8")) > 16_384:
            raise ValueError("prompt exceeds 16384 UTF-8 bytes")
        ids = [option.id for option in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError("option ids must be unique")
        if any(len(option.label.encode("utf-8")) > 1024 for option in self.options):
            raise ValueError("option label exceeds 1024 UTF-8 bytes")
        if self.kind == "text" and self.options:
            raise ValueError("text questions cannot declare options")
        if self.kind == "single_choice" and self.allow_free_text:
            raise ValueError("single_choice does not allow free text")
        if self.allow_free_text and self.kind not in {"text", "choice_or_text"}:
            raise ValueError("free text is valid only for text or choice_or_text")
        if self.kind in {"single_choice", "multi_choice", "approval"}:
            if not self.options:
                raise ValueError("choice questions require declared options")
            if not any(option.available for option in self.options):
                raise ValueError("choice questions require an available option")
        if self.kind == "choice_or_text" and not self.allow_free_text:
            if not self.options or not any(option.available for option in self.options):
                raise ValueError("choice_or_text requires an available option or free text")
        if self.kind == "approval" and not self.required:
            raise ValueError("approval questions are always required")
        return self


class CreateGroupRequest(_InputModel):
    questions: tuple[QuestionInput, ...] = Field(min_length=1, max_length=20)
    expires_in_seconds: int = Field(default=1800, ge=1, le=86400)
    wait_for_required: bool = True


class InteractionAnswerPayload(_InputModel):
    kind: Literal["text", "options", "free_text"]
    text: str | None = Field(default=None, max_length=4096)
    option_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=32)

    @model_validator(mode="after")
    def validate_shape(self):
        if self.text is not None and len(self.text.encode("utf-8")) > 4096:
            raise ValueError("answer exceeds 4096 UTF-8 bytes")
        if len(self.option_ids) != len(set(self.option_ids)):
            raise ValueError("option ids must be unique")
        if self.kind == "options" and (not self.option_ids or self.text is not None):
            raise ValueError("options answer requires option ids only")
        if self.kind in {"text", "free_text"} and (self.text is None or self.option_ids):
            raise ValueError("text answer requires text only")
        return self


class AnswerAck(_InputModel):
    question_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    receipt: str = Field(min_length=16, max_length=256)

    @field_validator("receipt")
    @classmethod
    def _receipt_ascii(cls, value: str) -> str:
        if not value.isascii():
            raise ValueError("answer receipt must contain only ASCII characters")
        return value


@dataclass(frozen=True)
class WorkSourceBinding:
    """Exact durable Work order identity copied from a server-owned binding."""

    job_id: str
    work_item_id: str
    attempt_id: str
    attempt_generation: int
    contract_hash: str
    grant_id: str
    grant_revision: int
    principal_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "work_item_id": self.work_item_id,
            "attempt_id": self.attempt_id,
            "attempt_generation": self.attempt_generation,
            "contract_hash": self.contract_hash,
            "grant_id": self.grant_id,
            "grant_revision": self.grant_revision,
            "principal_id": self.principal_id,
        }


@dataclass(frozen=True)
class AgentActor:
    terminal_id: str
    incarnation_id: str
    source_id: str
    turn_generation: int
    _seal: object
    _cap_hash: str


class IssuedCapability(_OutputModel):
    token: str
    source: dict[str, object]


class InteractionPage(_OutputModel):
    items: tuple[dict[str, object], ...] = ()
    next_cursor: str | None = None


class CreatedGroup(_OutputModel):
    group_id: str
    lifecycle: str
    wait_required: bool
    questions: tuple[dict[str, object], ...] = ()
    # Internal transport status; omitted from the strict response body.
    replayed: bool = Field(default=False, exclude=True)


class AnswerReceipt(_OutputModel):
    question_id: str
    version: int
    answer_id: str
    lifecycle: str
    delivery_state: str
    accepted_at: datetime
    replayed: bool = False


class ReturnedAnswer(_OutputModel):
    question_id: str
    version: int
    receipt: str
    response: InteractionAnswerPayload


class WaitResult(_OutputModel):
    group_id: str
    state: Literal["pending", "ready", "expired", "cancelled", "stale", "cycle_blocked"]
    answers: tuple[ReturnedAnswer, ...] = ()
    next_event_sequence: int = 0
    next_poll_after_ms: int = 1000

    @model_validator(mode="after")
    def pending_has_no_answer_body(self):
        if self.state != "ready" and self.answers:
            raise ValueError("non-ready interaction waits must not contain answer bodies")
        return self


class SourceStatus(_OutputModel):
    state: Literal["current", "stale_turn", "replaced", "inactive", "unregistered"]
    terminal_id: str
    incarnation_id: str | None
    current_generation: int | None
    capability_available: bool = True
    deadline: datetime | None = None


class DelegationReceipt(_OutputModel):
    delegation_id: str
    question_id: str
    question_version: int
    supervisor_terminal_id: str
    supervisor_incarnation_id: str
    assignment_id: str
    expires_at: datetime
    revoked_at: datetime | None = None
    replayed: bool = False


class ObservationReceipt(_OutputModel):
    observation_id: str
    question_id: str | None
    lifecycle: str
    response_capability: str
    manual_reason: str | None
    turn_generation: int | None = None
    replayed: bool = False


class NativeObservationPin(_OutputModel):
    """Validated runtime-only identity for recovering a native observation."""

    terminal_id: str
    incarnation_id: str
    question_id: str
    observation_id: str
    turn_generation: int
    current_generation: int
    lifecycle: str
    dialog_fingerprint: str
    provider_evidence_version: str
    exact_target: tuple[str, str, str]


class WaitStatus(_OutputModel):
    state: Literal["waiting", "released", "expired", "cancelled", "stale", "cycle_blocked"]
    group_ids: tuple[str, ...] = ()
    deadline: datetime | None = None


class NativeWaitStatus(_OutputModel):
    """Runtime-only status for waiting on one provider-owned manual dialog."""

    state: Literal["waiting", "released", "expired", "cancelled", "stale", "unregistered"]
    lifecycle: str | None = None
    question_id: str | None = None
    observation_id: str | None = None
    current_generation: int | None = None
    deadline: datetime | None = None


class DeliveryClaim(_OutputModel):
    delivery_id: str
    answer_id: str
    claim_nonce: str
    state: str


class DeliveryReceipt(_OutputModel):
    delivery_id: str
    answer_id: str
    state: str
    question_id: str
    version: int
    updated_at: datetime


class HistoryEvent(_OutputModel):
    sequence: int
    event_type: str
    actor_kind: str
    actor_label: str | None
    occurred_at: datetime
    safe_metadata: dict[str, object]


class ProviderCapability(_OutputModel):
    provider: str
    provider_version: str | None
    execution_mode: str
    emits_explicit: bool
    detects_native: bool
    responds_native: tuple[str, ...]
    manual_reason: str | None
    required_mcp_tools: tuple[str, ...] = ()
