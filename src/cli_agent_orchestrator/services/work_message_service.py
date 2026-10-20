"""Credential-bound send, receive, and acknowledgement for Work mailboxes."""

from __future__ import annotations

import hashlib
import time
from uuid import uuid4

from pydantic import ValidationError

from cli_agent_orchestrator.clients.work_message_repository import WorkMessageRepository
from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
from cli_agent_orchestrator.models.work_message import (
    WorkMessageAckRequest,
    WorkMessageAckResult,
    WorkMessageReceiveRequest,
    WorkMessageReceiveResult,
    WorkMessageResultItem,
    WorkMessageSendRequest,
    WorkMessageSendResult,
)
from cli_agent_orchestrator.services.work_attempt_credential import (
    WorkAttemptCredentialRejected,
    WorkAttemptCredentials,
)
from cli_agent_orchestrator.services.work_authority import AuthorityDenied, WorkAuthority
from cli_agent_orchestrator.services.work_contract import WorkContracts
from cli_agent_orchestrator.services.work_origin import OriginDenied, WorkOrigins

_MESSAGE_TOOL = "cao.work.message"


class WorkMessageService:
    """Execute bounded Work mailbox operations inside one verified PG transaction."""

    def __init__(self, repository: WorkRepository, *, origins: WorkOrigins):
        if (
            not isinstance(repository, WorkRepository)
            or not isinstance(origins, WorkOrigins)
            or origins.repository is not repository
        ):
            raise ValueError("message service requires its exact Work origin owner")
        self.repository = repository
        self.origins = origins
        self.credentials = WorkAttemptCredentials(repository, origins=origins)
        self.contracts = WorkContracts(repository)
        self.messages = WorkMessageRepository(repository)
        self._clock = time.time

    @staticmethod
    def _request(
        arguments: dict,
    ) -> WorkMessageSendRequest | WorkMessageReceiveRequest | WorkMessageAckRequest:
        if type(arguments) is not dict:
            raise OriginDenied("Work message arguments are invalid")
        operation = arguments.get("operation")
        if type(operation) is not str:
            raise OriginDenied("Work message operation is unavailable")
        models: dict[
            str,
            type[WorkMessageSendRequest]
            | type[WorkMessageReceiveRequest]
            | type[WorkMessageAckRequest],
        ] = {
            "send": WorkMessageSendRequest,
            "receive": WorkMessageReceiveRequest,
            "ack": WorkMessageAckRequest,
        }
        model = models.get(operation)
        if model is None:
            raise OriginDenied("Work message operation is unavailable")
        try:
            return model.model_validate(arguments)
        except ValidationError as error:
            raise OriginDenied("Work message arguments are invalid") from error

    def execute(self, credential: bytes, arguments: dict):
        request = self._request(arguments)
        send_preflight = (
            self._preflight_send(credential, request)
            if isinstance(request, WorkMessageSendRequest)
            else None
        )
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            lane_identity = self._lock_message_lane(connection, credential)
            if send_preflight is not None and (
                send_preflight["sender_identity"]["job_id"] != lane_identity["job_id"]
                or send_preflight["sender_identity"]["work_item_id"]
                != lane_identity["work_item_id"]
                or send_preflight["sender_identity"]["grant_id"] != lane_identity["grant_id"]
                or send_preflight["sender_identity"]["grant_revision"]
                != lane_identity["grant_revision"]
            ):
                raise WorkAttemptCredentialRejected("attempt credential binding changed")
            WorkAuthority.lock_job_authority_lane(connection, lane_identity["job_id"])
            sender_context, sender_binding = self._authorize_attempt(connection, credential)
            if (
                send_preflight is not None
                and self._sender_identity(sender_context) != send_preflight["sender_identity"]
            ):
                raise WorkAttemptCredentialRejected("attempt credential binding changed")
            if isinstance(request, WorkMessageSendRequest):
                return self._send(
                    connection,
                    sender_context,
                    request,
                    credential,
                )
            if isinstance(request, WorkMessageReceiveRequest):
                self._lock_current_item(connection, sender_context)
                sender_context, sender_binding = self._authorize_attempt(connection, credential)
                return self._receive(connection, sender_context, sender_binding, request)
            if isinstance(request, WorkMessageAckRequest):
                self._lock_current_item(connection, sender_context)
                sender_context, sender_binding = self._authorize_attempt(connection, credential)
                return self._ack(connection, sender_context, sender_binding, request)
        raise OriginDenied("Work message operation is unavailable")

    def _preflight_send(self, credential: bytes, request) -> dict:
        """Authenticate the sender and inspect only the stable key read-only.

        This preflight never selects receiver lock targets. The write transaction
        repeats all authoritative checks after the store fence, message lane, and
        common job authority lane, then looks up the key before receiver liveness.
        """
        with self.repository.read_snapshot() as connection:
            sender_context, _sender_binding = self._authorize_attempt(connection, credential)
            self.messages.find_for_sender_key(
                connection,
                sender_work_item_id=sender_context.work_item_id,
                idempotency_key=request.idempotency_key,
            )
            return {"sender_identity": self._sender_identity(sender_context)}

    @staticmethod
    def _sender_identity(context) -> dict:
        return {
            "attempt_id": context.attempt_id,
            "generation": context.generation,
            "job_id": context.job_id,
            "work_item_id": context.work_item_id,
            "principal_id": context.principal_id,
            "grant_id": context.grant_id,
            "grant_revision": context.grant_revision,
            "contract_hash": context.contract_hash,
        }

    @staticmethod
    def _lock_message_lane(connection, credential: bytes):
        """Serialize mailbox writes before taking the common job authority lane."""
        if type(credential) is not bytes or len(credential) != 32:
            raise WorkAttemptCredentialRejected("attempt credential is invalid")
        digest = hashlib.sha256(credential).hexdigest()
        row = connection.execute(
            "SELECT job_id,work_item_id,grant_id,grant_revision "
            "FROM work_attempt_credentials WHERE credential_sha256=%s",
            (digest,),
        ).fetchone()
        if row is None:
            raise WorkAttemptCredentialRejected("attempt credential is unknown")
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"cao:work-message:v1:{row['job_id']}",),
        )
        return row

    def _authorize_attempt(self, connection, credential: bytes):
        context = self.credentials.authenticate_in_transaction(connection, credential)
        binding = self.contracts._revalidate_order(
            connection, context.attempt_id, generation=context.generation
        )
        if (
            binding.attempt_id != context.attempt_id
            or binding.generation != context.generation
            or binding.job_id != context.job_id
            or binding.work_item_id != context.work_item_id
            or binding.principal_id != context.principal_id
            or binding.grant_id != context.grant_id
            or binding.grant_revision != context.grant_revision
            or binding.contract_hash != context.contract_hash
        ):
            raise WorkAttemptCredentialRejected("attempt credential binding changed")
        self._require_tool(binding)
        return context, binding

    @staticmethod
    def _require_tool(binding) -> None:
        if _MESSAGE_TOOL not in binding.contract.permissions.tools:
            raise AuthorityDenied("current Work contract does not grant message access")

    def _lock_current_item(self, connection, context) -> None:
        self.repository._lock_job_for_write(connection, context.job_id)
        item = connection.execute(
            "SELECT id,job_id FROM work_items WHERE id=%s FOR UPDATE",
            (context.work_item_id,),
        ).fetchone()
        if item is None or item["job_id"] != context.job_id:
            raise WorkAttemptCredentialRejected("current Work item changed")
        latest = connection.execute(
            "SELECT id,generation FROM work_attempts WHERE work_item_id=%s "
            "ORDER BY generation DESC LIMIT 1",
            (context.work_item_id,),
        ).fetchone()
        if latest is None:
            raise WorkAttemptCredentialRejected("current Work attempt is absent")
        locked = connection.execute(
            "SELECT id,generation FROM work_attempts WHERE id=%s FOR UPDATE",
            (latest["id"],),
        ).fetchone()
        if locked is None or locked["generation"] != latest["generation"]:
            raise WorkAttemptCredentialRejected("current Work attempt changed")

    def _lock_current_items_and_attempts(
        self, connection, *, job_id: str, item_ids: tuple[str, ...]
    ):
        ordered_items = tuple(sorted(set(item_ids)))
        placeholders = ",".join("%s" for _ in ordered_items)
        items = connection.execute(
            f"SELECT id,job_id FROM work_items WHERE id IN ({placeholders}) ORDER BY id FOR UPDATE",
            ordered_items,
        ).fetchall()
        if len(items) != len(ordered_items) or any(item["job_id"] != job_id for item in items):
            raise OriginDenied("Work message target is unavailable")

        latest_attempts = []
        for item_id in ordered_items:
            latest = connection.execute(
                "SELECT id,generation FROM work_attempts WHERE work_item_id=%s "
                "ORDER BY generation DESC LIMIT 1",
                (item_id,),
            ).fetchone()
            if latest is None:
                raise OriginDenied("Work message target is unavailable")
            latest_attempts.append((latest["id"], latest["generation"], item_id))

        ordered_attempts = tuple(sorted(latest_attempts, key=lambda value: (value[0], value[1])))
        attempt_ids = tuple(value[0] for value in ordered_attempts)
        attempt_placeholders = ",".join("%s" for _ in attempt_ids)
        locked_attempts = connection.execute(
            f"SELECT id,work_item_id,generation FROM work_attempts "
            f"WHERE id IN ({attempt_placeholders}) ORDER BY id,generation FOR UPDATE",
            attempt_ids,
        ).fetchall()
        by_item = {row["work_item_id"]: row for row in locked_attempts}
        if len(by_item) != len(ordered_items):
            raise WorkConflict("current Work attempt changed before message authorization")
        for attempt_id, generation, item_id in ordered_attempts:
            current = by_item.get(item_id)
            if current is None or (current["id"], current["generation"]) != (
                attempt_id,
                generation,
            ):
                raise WorkConflict("current Work attempt changed before message authorization")
        return by_item

    def _send(self, connection, context, request, credential: bytes):
        # The write transaction has reauthenticated the sender under both lanes.
        # Resolve the stable key before checking any receiver liveness.
        existing = self.messages.find_for_sender_key(
            connection,
            sender_work_item_id=context.work_item_id,
            idempotency_key=request.idempotency_key,
        )
        if existing is not None:
            if (
                existing["receiver_work_item_id"] != request.receiver_work_item_id
                or existing["body"] != request.body
            ):
                raise WorkConflict("Work message idempotency key was reused with different data")
            return WorkMessageSendResult(message_id=existing["message_id"], status="replayed")

        if request.receiver_work_item_id == context.work_item_id:
            raise OriginDenied("Work message target is unavailable")
        receiver_identity = connection.execute(
            "SELECT job_id FROM work_items WHERE id=%s", (request.receiver_work_item_id,)
        ).fetchone()
        if receiver_identity is None or receiver_identity["job_id"] != context.job_id:
            raise OriginDenied("Work message target is unavailable")

        self.repository._lock_job_for_write(connection, context.job_id)
        current_attempts = self._lock_current_items_and_attempts(
            connection,
            job_id=context.job_id,
            item_ids=(context.work_item_id, request.receiver_work_item_id),
        )
        current_context, current_binding = self._authorize_attempt(connection, credential)
        if current_context != context:
            raise WorkAttemptCredentialRejected("sender generation changed before message send")
        receiver_attempt = current_attempts.get(request.receiver_work_item_id)
        if receiver_attempt is None:
            raise OriginDenied("Work message target is unavailable")
        receiver_binding = self.contracts._revalidate_order(
            connection,
            receiver_attempt["id"],
            generation=receiver_attempt["generation"],
        )
        self._require_tool(receiver_binding)
        if current_binding.job_id != receiver_binding.job_id:
            raise OriginDenied("Work message target is unavailable")

        message_id = uuid4().hex
        created_at = self._clock()
        self.messages.insert(
            connection,
            message_id=message_id,
            job_id=context.job_id,
            sender_work_item_id=context.work_item_id,
            receiver_work_item_id=request.receiver_work_item_id,
            sender_attempt_id=context.attempt_id,
            sender_generation=context.generation,
            sender_principal_id=context.principal_id,
            idempotency_key=request.idempotency_key,
            body=request.body,
            created_at=created_at,
        )
        self.repository._append_event(
            connection,
            job_id=context.job_id,
            work_item_id=context.work_item_id,
            attempt_id=context.attempt_id,
            actor_id=context.principal_id,
            event_type="work.message.sent",
            metadata={
                "message_id": message_id,
                "sender_work_item_id": context.work_item_id,
                "receiver_work_item_id": request.receiver_work_item_id,
            },
        )
        return WorkMessageSendResult(message_id=message_id, status="sent")

    def _receive(self, connection, context, binding, request):
        self._require_tool(binding)
        rows = self.messages.pending_for_receiver(
            connection,
            job_id=context.job_id,
            receiver_work_item_id=context.work_item_id,
            limit=request.limit,
        )
        return WorkMessageReceiveResult(
            messages=[
                WorkMessageResultItem(
                    message_id=row["message_id"],
                    sender_work_item_id=row["sender_work_item_id"],
                    body=row["body"],
                    created_at=float(row["created_at"]),
                )
                for row in rows
            ]
        )

    def _ack(self, connection, context, binding, request):
        self._require_tool(binding)
        row = self.messages.find_for_receiver(
            connection,
            job_id=context.job_id,
            receiver_work_item_id=context.work_item_id,
            message_id=request.message_id,
        )
        if row is None:
            raise OriginDenied("Work message is unavailable")
        if row["acknowledged_at"] is not None:
            return WorkMessageAckResult(message_id=row["message_id"], status="already_acknowledged")
        acknowledged_at = self._clock()
        if not self.messages.acknowledge(
            connection,
            message_id=row["message_id"],
            acknowledged_at=acknowledged_at,
            principal_id=context.principal_id,
            attempt_id=context.attempt_id,
            generation=context.generation,
        ):
            raise WorkConflict("Work message acknowledgement changed concurrently")
        self.repository._append_event(
            connection,
            job_id=context.job_id,
            work_item_id=context.work_item_id,
            attempt_id=context.attempt_id,
            actor_id=context.principal_id,
            event_type="work.message.acknowledged",
            metadata={
                "message_id": row["message_id"],
                "sender_work_item_id": row["sender_work_item_id"],
                "receiver_work_item_id": context.work_item_id,
            },
        )
        return WorkMessageAckResult(message_id=row["message_id"], status="acknowledged")
