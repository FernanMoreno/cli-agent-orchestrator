"""Closed, content-free projection of the interaction ledger into AG-UI state."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

_Lifecycle = Literal[
    "open",
    "answered",
    "expired",
    "cancelled",
    "stale",
    "manual_only",
    "reconciled_external",
]
_DeliveryState = Literal[
    "accepted",
    "prepared",
    "claimed",
    "returned_to_mcp",
    "agent_acknowledged",
    "confirmed",
    "uncertain",
    "no_effect",
    "reconciled",
]


class _ProjectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Source(_ProjectionModel):
    terminal_id: str
    incarnation_id: str
    provider: str | None
    provider_version: str | None
    execution_mode: str | None


class _Task(_ProjectionModel):
    work_item_id: str | None
    attempt_id: str | None
    session_name: str | None


class _Recipient(_ProjectionModel):
    kind: Literal["human", "supervisor", "unassigned", "manual"]
    label: str | None
    reason: str


class _GroupProgress(_ProjectionModel):
    required_total: int = Field(ge=0)
    required_answered: int = Field(ge=0)


class _AnswerReceipt(_ProjectionModel):
    question_id: str
    version: int = Field(ge=1)
    answer_id: str
    lifecycle: _Lifecycle
    delivery_state: _DeliveryState
    accepted_at: datetime


class _InteractionItem(_ProjectionModel):
    question_id: str
    version: int = Field(ge=1)
    group_id: str | None
    lifecycle: _Lifecycle
    delivery_state: _DeliveryState | None
    source: _Source
    task: _Task | None
    kind: Literal[
        "text",
        "single_choice",
        "multi_choice",
        "choice_or_text",
        "approval",
        "credential",
        "native_unknown",
    ]
    required: bool
    recipient: _Recipient
    group_progress: _GroupProgress | None
    origin: Literal["explicit", "native"]
    can_answer: Literal[False] = False
    actions: list[str] = Field(default_factory=list)
    manual_reason: str | None
    created_at: datetime
    expires_at: datetime | None
    answer_receipt: _AnswerReceipt | None


class _InteractionSnapshot(_ProjectionModel):
    available: bool
    items: list[_InteractionItem] = Field(max_length=100)
    next_cursor: str | None = Field(max_length=512)
    truncated: bool
    event_sequence: int | None = Field(default=None, ge=0)


_ITEM_FIELDS = frozenset(
    {
        "question_id",
        "version",
        "group_id",
        "lifecycle",
        "delivery_state",
        "source",
        "task",
        "kind",
        "required",
        "recipient",
        "group_progress",
        "origin",
        "manual_reason",
        "created_at",
        "expires_at",
        "answer_receipt",
    }
)
_SOURCE_FIELDS = frozenset(
    {"terminal_id", "incarnation_id", "provider", "provider_version", "execution_mode"}
)
_TASK_FIELDS = frozenset({"work_item_id", "attempt_id", "session_name"})
_RECIPIENT_FIELDS = frozenset({"kind", "label", "reason"})
_PROGRESS_FIELDS = frozenset({"required_total", "required_answered"})
_RECEIPT_FIELDS = frozenset(
    {"question_id", "version", "answer_id", "lifecycle", "delivery_state", "accepted_at"}
)


def _pick(value: object, fields: frozenset[str]) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("interaction projection contains an invalid object")
    return {key: value[key] for key in fields if key in value}


def project_interaction_snapshot(raw: Mapping[str, Any]) -> dict[str, object]:
    """Validate and allowlist one service page for shared AG-UI state.

    The service deliberately returns ordinary REST metadata internally so both
    readers share the same source validation and cursor behavior. This adapter
    narrows that shape: prompt/options/response, authority, delegation and
    private receipt tokens are never copied into the public stream.
    """
    if not isinstance(raw, Mapping):
        raise ValueError("interaction projection page is invalid")
    raw_items = raw.get("items")
    if not isinstance(raw_items, (list, tuple)):
        raise ValueError("interaction projection items are invalid")
    projected_items: list[_InteractionItem] = []
    for raw_item in raw_items:
        item = _pick(raw_item, _ITEM_FIELDS)
        if item is None:
            raise ValueError("interaction projection item is invalid")
        item["source"] = _pick(item.get("source"), _SOURCE_FIELDS)
        item["task"] = _pick(item.get("task"), _TASK_FIELDS)
        item["recipient"] = _pick(item.get("recipient"), _RECIPIENT_FIELDS)
        item["group_progress"] = _pick(item.get("group_progress"), _PROGRESS_FIELDS)
        item["answer_receipt"] = _pick(item.get("answer_receipt"), _RECEIPT_FIELDS)
        if item.get("origin") == "native" and item.get("kind") == "unknown":
            item["kind"] = "native_unknown"
        recipient = item.get("recipient")
        if isinstance(recipient, dict) and recipient.get("kind") == "agent":
            recipient["kind"] = "supervisor"
        item["can_answer"] = False
        item["actions"] = []
        try:
            projected_items.append(_InteractionItem.model_validate(item))
        except ValidationError as error:
            raise ValueError("interaction projection item is invalid") from error

    next_cursor = raw.get("next_cursor")
    event_sequence = raw.get("event_sequence")
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise ValueError("interaction projection cursor is invalid")
    if type(event_sequence) is not int or event_sequence < 0:
        raise ValueError("interaction projection high-water mark is invalid")
    result = _InteractionSnapshot(
        available=True,
        items=projected_items,
        next_cursor=next_cursor,
        truncated=next_cursor is not None,
        event_sequence=event_sequence,
    )
    return result.model_dump(mode="json", exclude_none=False)


def unavailable_interaction_snapshot() -> dict[str, object]:
    """Stable fail-closed state when the ledger projection cannot be read."""
    return _InteractionSnapshot(
        available=False,
        items=[],
        next_cursor=None,
        truncated=False,
        event_sequence=None,
    ).model_dump(mode="json", exclude_none=False)


def with_interaction_projection(
    fleet_snapshot: Mapping[str, Any], projection: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a new fleet state with the isolated interaction page attached."""
    snapshot = dict(fleet_snapshot)
    snapshot["interactions"] = project_interaction_snapshot(projection)
    return snapshot


__all__ = [
    "project_interaction_snapshot",
    "unavailable_interaction_snapshot",
    "with_interaction_projection",
]
