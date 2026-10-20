"""Closed payloads for durable, attempt-authorized Work agent messages."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
)

MessageId = Annotated[StrictStr, Field(min_length=1, max_length=128)]
WorkItemId = Annotated[StrictStr, Field(min_length=1, max_length=128)]


class _WorkMessageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class WorkMessageSendRequest(_WorkMessageModel):
    operation: Literal["send"]
    receiver_work_item_id: WorkItemId
    idempotency_key: Annotated[
        StrictStr,
        Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$"),
    ]
    body: Annotated[StrictStr, Field(min_length=1)]

    @field_validator("body")
    @classmethod
    def bounded_strict_utf8_body(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Work message bodies cannot contain U+0000")
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as error:
            raise ValueError("Work message body must be strict UTF-8") from error
        if not encoded or len(encoded) > 4096:
            raise ValueError("Work message body must contain at most 4096 UTF-8 bytes")
        return value


class WorkMessageReceiveRequest(_WorkMessageModel):
    operation: Literal["receive"]
    limit: Annotated[StrictInt, Field(ge=1, le=2)] = 1


class WorkMessageAckRequest(_WorkMessageModel):
    operation: Literal["ack"]
    message_id: MessageId


class WorkMessageSendResult(_WorkMessageModel):
    message_id: MessageId
    status: Literal["sent", "replayed"]


class WorkMessageResultItem(_WorkMessageModel):
    message_id: MessageId
    sender_work_item_id: WorkItemId
    body: Annotated[StrictStr, Field(min_length=1)]
    created_at: StrictFloat

    @field_validator("body")
    @classmethod
    def bounded_strict_utf8_body(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Work message bodies cannot contain U+0000")
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as error:
            raise ValueError("Work message body must be strict UTF-8") from error
        if not encoded or len(encoded) > 4096:
            raise ValueError("Work message body must contain at most 4096 UTF-8 bytes")
        return value


class WorkMessageReceiveResult(_WorkMessageModel):
    messages: Annotated[list[WorkMessageResultItem], Field(max_length=2)]


class WorkMessageAckResult(_WorkMessageModel):
    message_id: MessageId
    status: Literal["acknowledged", "already_acknowledged"]
