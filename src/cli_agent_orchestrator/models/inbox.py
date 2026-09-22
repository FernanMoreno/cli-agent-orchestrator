"""Inbox message models."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class OrchestrationType(str, Enum):
    """Orchestration mode for a message delivery."""

    SEND_MESSAGE = "send_message"
    HANDOFF = "handoff"
    ASSIGN = "assign"


class MessageStatus(str, Enum):
    """Durable delivery state for an inbox message.

    ``RECONCILE`` is deliberately distinct from ``PENDING``. It means a
    terminal may already have accepted the message, but CAO lost the durable
    post-paste acknowledgement. Retrying a ``RECONCILE`` message automatically
    could execute the same task twice, so only an explicit reconciliation flow
    may resolve it.
    """

    PENDING = "pending"
    DELIVERED = "delivered"
    RECONCILE = "reconcile"
    FAILED = "failed"


class InboxMessage(BaseModel):
    """Inbox message model."""

    id: int = Field(..., description="Message ID")
    sender_id: str = Field(..., description="Sender terminal ID")
    receiver_id: str = Field(..., description="Receiver terminal ID")
    message: str = Field(..., description="Message content")
    status: MessageStatus = Field(..., description="Message status")
    created_at: datetime = Field(..., description="Creation timestamp")
