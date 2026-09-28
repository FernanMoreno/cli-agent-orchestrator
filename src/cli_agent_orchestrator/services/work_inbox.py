"""Server-owned staged admission and protected delivery for managed inbox rows."""

import json
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, field_validator

from cli_agent_orchestrator.clients.database import (
    _create_managed_inbox_message,
    _managed_inbox_store_identity,
    _managed_inbox_target,
)
from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope
from cli_agent_orchestrator.services.inbox_service import InboxService
from cli_agent_orchestrator.services.work_delivery import DeliveryAdapter
from cli_agent_orchestrator.services.work_service import DeliveryObservation


class ManagedInboxPayload(BaseModel):
    """The immutable delivery order refers only to a bridge-selected row."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    inbox_id: int

    @field_validator("inbox_id")
    @classmethod
    def positive_inbox_id(cls, value):
        if type(value) is not int or value <= 0:
            raise ValueError("managed inbox id must be positive")
        return value


@dataclass(frozen=True)
class ManagedInboxReceipt:
    work_item_id: str
    attempt_id: str
    generation: int
    inbox_id: int


def managed_inbox_delivery_adapters(
    repository: WorkRepository, *, delivery_service: InboxService | None = None
):
    """Create the explicit v1 server adapter; it never reaches a provider directly."""
    if not isinstance(repository, WorkRepository):
        raise ValueError("verified work repository required")
    service = delivery_service or InboxService()
    try:
        store_context = _managed_inbox_store_identity()
    except RuntimeError as error:
        raise WorkConflict("managed inbox store context is unavailable") from error
    if repository.managed_inbox_store_context(expected=store_context) != store_context:
        raise WorkConflict("managed inbox store context is contradictory")

    async def send(binding, payload, snapshot, port):
        try:
            identity = _managed_inbox_store_identity(expected=store_context)
        except RuntimeError as error:
            raise WorkConflict("managed inbox store context changed before effect") from error
        repository.get_inbox_binding(
            attempt_id=binding.attempt_id,
            generation=binding.generation,
            inbox_id=payload.inbox_id,
            store_context=identity,
        )
        # dispatch_registered_next already revalidates binding, authority, contract
        # and frozen snapshot. ``port.send_keys`` repeats its fresh guard immediately
        # before the only external effect.
        service.deliver_managed(payload.inbox_id, port, store_context=identity)
        # A paste has no correlatable receipt. Preserve sent/RECONCILE rather than
        # claiming receipt or enabling a restart replay.
        return DeliveryObservation()

    return {("inbox", 1): DeliveryAdapter(ManagedInboxPayload, send)}


class WorkInboxCoordinator:
    """The sole server composition of retained row, bridge, order, and queue."""

    def __init__(self, admission):
        repository = getattr(admission, "repository", None)
        if not isinstance(repository, WorkRepository):
            raise ValueError("server work admission required")
        self.admission = admission
        self.repository = repository
        try:
            self._store_context = _managed_inbox_store_identity()
        except RuntimeError as error:
            raise WorkConflict("managed inbox store context is unavailable") from error
        if self.repository.managed_inbox_store_context(expected=self._store_context) != self._store_context:
            raise WorkConflict("managed inbox store context is contradictory")

    def _validate_store_context(self):
        """Pin both real connections to the UUID captured for this server context."""
        try:
            context = _managed_inbox_store_identity(expected=self._store_context)
        except RuntimeError as error:
            raise WorkConflict(str(error)) from error
        if self.repository.managed_inbox_store_context(expected=context) != context:
            raise WorkConflict("managed inbox store context is contradictory")
        return context

    def admit(
        self,
        *,
        principal,
        job_id,
        idempotency_key,
        request_hash,
        grant_id,
        expected_grant_revision,
        contract,
        sender_id,
        receiver_id,
        message,
        lease_seconds=60,
        parent_work_item_id=None,
    ) -> ManagedInboxReceipt:
        if getattr(contract, "operation_kind", None) != "inbox":
            raise ValueError("managed inbox requires an inbox contract")
        if ("inbox", 1) not in self.admission.deliveries.adapters:
            raise WorkConflict("managed inbox adapter is not registered")
        if any(not isinstance(value, str) or not value for value in (sender_id, receiver_id, message)):
            raise ValueError("managed inbox values must be non-empty strings")
        store_context = self._validate_store_context()
        reserved, created = self.admission.reserve_managed_inbox(
            principal=principal,
            job_id=job_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            grant_id=grant_id,
            expected_grant_revision=expected_grant_revision,
            contract=contract,
            managed_store_context=store_context,
            lease_seconds=lease_seconds,
            parent_work_item_id=parent_work_item_id,
        )
        attempt = reserved["attempts"][0]
        bridge = self.repository.find_inbox_binding(
            attempt_id=attempt["id"],
            generation=attempt["generation"],
            store_context=store_context,
        )
        if bridge is None:
            if not created:
                raise WorkConflict("retained managed inbox reservation requires reconciliation")
            inbox = _create_managed_inbox_message(
                sender_id, receiver_id, message, store_context=store_context
            )
            bridge = self.repository.bind_inbox(
                attempt_id=attempt["id"],
                generation=attempt["generation"],
                inbox_id=inbox.id,
                store_context=store_context,
            )
        else:
            target = _managed_inbox_target(
                bridge["inbox_id"], store_context=store_context
            )
            if (
                target.message.sender_id,
                target.message.receiver_id,
                target.message.message,
            ) != (sender_id, receiver_id, message):
                raise WorkConflict("managed inbox replay differs from the retained row")
        envelope = WorkDeliveryEnvelope(
            operation_kind="inbox",
            adapter_version=1,
            payload_json=json.dumps({"inbox_id": bridge["inbox_id"]}, separators=(",", ":")),
        )
        self.admission.enable_managed_inbox(
            attempt_id=attempt["id"],
            generation=attempt["generation"],
            delivery=envelope,
            managed_store_context=self._validate_store_context(),
        )
        return ManagedInboxReceipt(
            work_item_id=reserved["id"],
            attempt_id=attempt["id"],
            generation=attempt["generation"],
            inbox_id=bridge["inbox_id"],
        )
