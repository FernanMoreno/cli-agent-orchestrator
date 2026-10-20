"""SQL operations for the PostgreSQL Work message ledger."""

from __future__ import annotations

from cli_agent_orchestrator.clients.postgres import Connection, Record
from cli_agent_orchestrator.clients.work_repository import WorkRepository


class WorkMessageRepository:
    """Perform message-table operations on the caller's verified Work transaction."""

    def __init__(self, repository: WorkRepository):
        if not isinstance(repository, WorkRepository):
            raise ValueError("verified Work repository required")
        self.repository = repository

    def _require_transaction(self, connection: Connection) -> None:
        if (
            not isinstance(connection, Connection)
            or not connection.in_transaction
            or connection.target.identity != self.repository.target.identity
        ):
            raise ValueError("message operation requires this Work transaction")

    def find_for_sender_key(
        self,
        connection: Connection,
        *,
        sender_work_item_id: str,
        idempotency_key: str,
        for_update: bool = False,
    ) -> Record | None:
        self._require_transaction(connection)
        query = "SELECT * FROM work_messages WHERE sender_work_item_id=%s AND idempotency_key=%s"
        if for_update:
            query += " FOR UPDATE"
        return connection.execute(query, (sender_work_item_id, idempotency_key)).fetchone()

    def insert(
        self,
        connection: Connection,
        *,
        message_id: str,
        job_id: str,
        sender_work_item_id: str,
        receiver_work_item_id: str,
        sender_attempt_id: str,
        sender_generation: int,
        sender_principal_id: str,
        idempotency_key: str,
        body: str,
        created_at: float,
    ) -> None:
        self._require_transaction(connection)
        connection.execute(
            "INSERT INTO work_messages "
            "(message_id,job_id,sender_work_item_id,receiver_work_item_id,"
            "sender_attempt_id,sender_generation,sender_principal_id,idempotency_key,body,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                message_id,
                job_id,
                sender_work_item_id,
                receiver_work_item_id,
                sender_attempt_id,
                sender_generation,
                sender_principal_id,
                idempotency_key,
                body,
                created_at,
            ),
        )

    def pending_for_receiver(
        self,
        connection: Connection,
        *,
        job_id: str,
        receiver_work_item_id: str,
        limit: int,
    ) -> list[Record]:
        self._require_transaction(connection)
        return connection.execute(
            "SELECT message_id,sender_work_item_id,body,created_at FROM work_messages "
            "WHERE job_id=%s AND receiver_work_item_id=%s AND acknowledged_at IS NULL "
            "ORDER BY created_at,message_id LIMIT %s",
            (job_id, receiver_work_item_id, limit),
        ).fetchall()

    def find_for_receiver(
        self,
        connection: Connection,
        *,
        job_id: str,
        receiver_work_item_id: str,
        message_id: str,
    ) -> Record | None:
        self._require_transaction(connection)
        return connection.execute(
            "SELECT * FROM work_messages WHERE message_id=%s AND job_id=%s "
            "AND receiver_work_item_id=%s FOR UPDATE",
            (message_id, job_id, receiver_work_item_id),
        ).fetchone()

    def acknowledge(
        self,
        connection: Connection,
        *,
        message_id: str,
        acknowledged_at: float,
        principal_id: str,
        attempt_id: str,
        generation: int,
    ) -> bool:
        self._require_transaction(connection)
        updated = connection.execute(
            "UPDATE work_messages SET acknowledged_at=%s,acknowledged_by_principal_id=%s,"
            "acknowledged_attempt_id=%s,acknowledged_generation=%s "
            "WHERE message_id=%s AND acknowledged_at IS NULL",
            (acknowledged_at, principal_id, attempt_id, generation, message_id),
        )
        return updated.rowcount == 1
