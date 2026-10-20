"""Durable PostgreSQL command claims and incarnation-fenced late results."""

from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any

from cli_agent_orchestrator.clients.postgres import DatabaseTarget, connect
from cli_agent_orchestrator.clients.postgres.migrations import (
    verify_component,
    verify_runtime_role,
)
from cli_agent_orchestrator.clients.runtime_channel_schema import runtime_channel_migrations


class RemoteIdentityConflict(ValueError):
    """A runtime operation ID was reused with conflicting identity or evidence."""


def _canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


class RemoteOperationStore:
    """Journal remote effects in the configured main PostgreSQL target.

    The constructor verifies a previously provisioned component. It never
    creates tables or upgrades the configured store.
    """

    def __init__(self, target: DatabaseTarget | None = None):
        if target is None:
            from cli_agent_orchestrator.clients.database import main_target

            target = main_target()
        if not isinstance(target, DatabaseTarget):
            raise TypeError("RemoteOperationStore requires a PostgreSQL DatabaseTarget")
        self.target = target
        with connect(target, readonly=True) as connection:
            verify_runtime_role(connection)
            verify_component(connection, runtime_channel_migrations())

    def activate_runtime(self, runtime_id: str, incarnation_id: str) -> int:
        if not runtime_id or not incarnation_id:
            raise ValueError("Runtime and incarnation identity required")
        with connect(self.target) as connection, connection.transaction():
            row = connection.execute(
                """INSERT INTO remote_runtime_instances(runtime_id,incarnation_id,connection_epoch)
                   VALUES (%s,%s,1)
                   ON CONFLICT(runtime_id) DO UPDATE SET
                     incarnation_id=EXCLUDED.incarnation_id,
                     connection_epoch=remote_runtime_instances.connection_epoch+1
                   RETURNING connection_epoch""",
                (runtime_id, incarnation_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("Runtime activation did not return an epoch")
            epoch = int(row[0])
            connection.execute(
                """UPDATE remote_operations SET state='reconcile'
                   WHERE runtime_id=%s AND incarnation_id<>%s
                     AND state IN ('prepared','dispatching')""",
                (runtime_id, incarnation_id),
            )
        return epoch

    def admit_epoch(self, runtime_id: str, incarnation_id: str, epoch: int) -> None:
        """Adopt an authenticated central epoch without rolling the same incarnation back."""
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
            raise ValueError("A positive runtime connection epoch is required")
        with connect(self.target) as connection, connection.transaction():
            row = connection.execute(
                """INSERT INTO remote_runtime_instances(runtime_id,incarnation_id,connection_epoch)
                   VALUES (%s,%s,%s)
                   ON CONFLICT(runtime_id) DO UPDATE SET
                     incarnation_id=EXCLUDED.incarnation_id,
                     connection_epoch=EXCLUDED.connection_epoch
                   WHERE remote_runtime_instances.incarnation_id<>EXCLUDED.incarnation_id
                      OR EXCLUDED.connection_epoch>=remote_runtime_instances.connection_epoch
                   RETURNING incarnation_id,connection_epoch""",
                (runtime_id, incarnation_id, epoch),
            ).fetchone()
            if row is None:
                raise RemoteIdentityConflict("Stale runtime connection epoch")
            connection.execute(
                """UPDATE remote_operations SET state='reconcile'
                   WHERE runtime_id=%s AND incarnation_id<>%s
                     AND state IN ('prepared','dispatching')""",
                (runtime_id, incarnation_id),
            )

    def unsettled_results(self, runtime_id: str, incarnation_id: str) -> list[Any]:
        with connect(self.target, readonly=True) as connection:
            rows = connection.execute(
                """SELECT result_json FROM remote_operations
                   WHERE runtime_id=%s AND incarnation_id=%s AND state='settled'
                   ORDER BY deadline DESC LIMIT 64""",
                (runtime_id, incarnation_id),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def next_revision(self, terminal_id: str, incarnation_id: str) -> int:
        with connect(self.target) as connection, connection.transaction():
            row = connection.execute(
                """INSERT INTO remote_observation_revisions(terminal_id,incarnation_id,revision)
                   VALUES (%s,%s,1)
                   ON CONFLICT(terminal_id,incarnation_id) DO UPDATE SET
                     revision=remote_observation_revisions.revision+1
                   RETURNING revision""",
                (terminal_id, incarnation_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("Observation revision allocation failed")
            return int(row[0])

    def get_runtime(self, runtime_id: str) -> dict[str, Any] | None:
        with connect(self.target, readonly=True) as connection:
            row = connection.execute(
                """SELECT runtime_id,incarnation_id,connection_epoch
                   FROM remote_runtime_instances WHERE runtime_id=%s""",
                (runtime_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "runtime_id": row["runtime_id"],
            "incarnation_id": row["incarnation_id"],
            "connection_epoch": row["connection_epoch"],
        }

    def request_cancellation(self, op_id: str) -> None:
        with connect(self.target) as connection, connection.transaction():
            connection.execute(
                """INSERT INTO remote_operation_cancellations(op_id,requested_at)
                   VALUES (%s,%s) ON CONFLICT(op_id) DO NOTHING""",
                (op_id, time.time()),
            )

    def cancellation_requested(self, op_id: str) -> bool:
        with connect(self.target, readonly=True) as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM remote_operation_cancellations WHERE op_id=%s", (op_id,)
                ).fetchone()
                is not None
            )

    def prepare(
        self,
        op_id: str,
        runtime_id: str,
        incarnation_id: str,
        command_type: str,
        payload: dict[str, Any],
        *,
        terminal_id: str | None = None,
        generation: str | None = None,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        if deadline is not None and (
            isinstance(deadline, bool)
            or not isinstance(deadline, (float, int))
            or not math.isfinite(deadline)
        ):
            raise ValueError("Finite remote operation deadline required")
        expires_at = float(deadline) if deadline is not None else time.time() + 60
        request = _canonical(
            {
                "runtime_id": runtime_id,
                "incarnation_id": incarnation_id,
                "type": command_type,
                "payload": payload,
                "terminal_id": terminal_id,
                "generation": generation,
            }
        )
        digest = hashlib.sha256(request.encode()).hexdigest()
        with connect(self.target) as connection, connection.transaction():
            connection.execute(
                """INSERT INTO remote_operations(
                       op_id,runtime_id,incarnation_id,terminal_id,generation,command_type,
                       request_hash,request_json,deadline,state)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'prepared')
                   ON CONFLICT(op_id) DO NOTHING""",
                (
                    op_id,
                    runtime_id,
                    incarnation_id,
                    terminal_id,
                    generation,
                    command_type,
                    digest,
                    request,
                    expires_at,
                ),
            )
            row = connection.execute(
                """SELECT op_id,runtime_id,incarnation_id,terminal_id,generation,
                          connection_epoch,deadline,command_type,request_hash,request_json,
                          state,result_json
                   FROM remote_operations WHERE op_id=%s""",
                (op_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Prepared operation was not found after insertion")
            if row["request_hash"] != digest or (
                deadline is not None and row["deadline"] != expires_at
            ):
                raise RemoteIdentityConflict("Operation identity refers to different material")
            return self._decode(row)

    def claim(self, op_id: str, *, connection_epoch: int | None = None) -> bool:
        with connect(self.target) as connection, connection.transaction():
            if connection_epoch is None:
                row = connection.execute(
                    """UPDATE remote_operations AS operation SET state='dispatching'
                       WHERE operation.op_id=%s AND operation.state='prepared'
                         AND operation.deadline>%s
                         AND EXISTS (
                           SELECT 1 FROM remote_runtime_instances AS runtime
                           WHERE runtime.runtime_id=operation.runtime_id
                             AND runtime.incarnation_id=operation.incarnation_id
                         ) RETURNING operation.op_id""",
                    (op_id, time.time()),
                ).fetchone()
            else:
                row = connection.execute(
                    """UPDATE remote_operations AS operation SET
                         state='dispatching',connection_epoch=%s
                       WHERE operation.op_id=%s AND operation.state='prepared'
                         AND operation.deadline>%s
                         AND EXISTS (
                           SELECT 1 FROM remote_runtime_instances AS runtime
                           WHERE runtime.runtime_id=operation.runtime_id
                             AND runtime.incarnation_id=operation.incarnation_id
                             AND runtime.connection_epoch=%s
                         ) RETURNING operation.op_id""",
                    (connection_epoch, op_id, time.time(), connection_epoch),
                ).fetchone()
        return row is not None

    def cancel_unsent(self, op_id: str) -> bool:
        with connect(self.target) as connection, connection.transaction():
            row = connection.execute(
                """UPDATE remote_operations SET state='cancelled'
                   WHERE op_id=%s AND state='prepared' RETURNING op_id""",
                (op_id,),
            ).fetchone()
        return row is not None

    def effect_is_current(self, op_id: str, *, connection_epoch: int) -> bool:
        with connect(self.target, readonly=True) as connection:
            return (
                connection.execute(
                    """SELECT 1 FROM remote_operations AS operation
                   JOIN remote_runtime_instances AS runtime
                     ON runtime.runtime_id=operation.runtime_id
                    AND runtime.incarnation_id=operation.incarnation_id
                   WHERE operation.op_id=%s AND operation.state='dispatching'
                     AND operation.connection_epoch=%s AND runtime.connection_epoch=%s
                     AND operation.deadline>%s""",
                    (op_id, connection_epoch, connection_epoch, time.time()),
                ).fetchone()
                is not None
            )

    def mark_unknown(self, op_id: str) -> None:
        with connect(self.target) as connection, connection.transaction():
            connection.execute(
                """UPDATE remote_operations SET state='reconcile'
                   WHERE op_id=%s AND state='dispatching'""",
                (op_id,),
            )

    def settle(
        self,
        op_id: str,
        runtime_id: str,
        incarnation_id: str,
        result: dict[str, Any],
    ) -> bool:
        encoded = _canonical(result)
        with connect(self.target) as connection, connection.transaction():
            row = connection.execute(
                """UPDATE remote_operations AS operation
                   SET state='settled',result_json=%s
                   WHERE operation.op_id=%s AND operation.runtime_id=%s
                     AND operation.incarnation_id=%s
                     AND operation.state IN ('dispatching','reconcile')
                     AND EXISTS (
                       SELECT 1 FROM remote_runtime_instances AS runtime
                       WHERE runtime.runtime_id=%s AND runtime.incarnation_id=%s
                     ) RETURNING operation.op_id""",
                (
                    encoded,
                    op_id,
                    runtime_id,
                    incarnation_id,
                    runtime_id,
                    incarnation_id,
                ),
            ).fetchone()
        return row is not None

    def get(self, op_id: str) -> dict[str, Any] | None:
        with connect(self.target, readonly=True) as connection:
            row = connection.execute(
                """SELECT op_id,runtime_id,incarnation_id,terminal_id,generation,
                          connection_epoch,deadline,command_type,request_hash,request_json,
                          state,result_json FROM remote_operations WHERE op_id=%s""",
                (op_id,),
            ).fetchone()
        return self._decode(row) if row is not None else None

    @staticmethod
    def _decode(row) -> dict[str, Any]:
        result = {name: row[name] for name in row.keys()}
        result["request"] = json.loads(result.pop("request_json"))
        encoded_result = result.pop("result_json")
        result["result"] = json.loads(encoded_result) if encoded_result is not None else None
        return result
