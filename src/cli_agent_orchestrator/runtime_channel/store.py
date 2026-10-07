"""Durable once-only command claims and incarnation-fenced late results."""

from __future__ import annotations

import hashlib
import json
import math
import time

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from cli_agent_orchestrator.clients.runtime_channel_schema import (
    RemoteBase,
    RemoteOperationModel,
    RemoteRuntimeModel,
)


class RemoteIdentityConflict(ValueError):
    pass


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


class RemoteOperationStore:
    def __init__(self, engine):
        self.engine = engine
        self.runtimes = RemoteRuntimeModel.__table__
        self.operations = RemoteOperationModel.__table__

    def create_schema(self):
        """Explicit standalone-test/runtime initialization; central migration owns tables."""
        RemoteBase.metadata.create_all(self.engine)

    def activate_runtime(self, runtime_id, incarnation_id):
        if not runtime_id or not incarnation_id:
            raise ValueError("Runtime and incarnation identity required")
        with self.engine.begin() as conn:
            epoch = conn.execute(
                sqlite_insert(self.runtimes)
                .values(runtime_id=runtime_id, incarnation_id=incarnation_id, connection_epoch=1)
                .on_conflict_do_update(
                    index_elements=["runtime_id"],
                    set_={
                        "incarnation_id": incarnation_id,
                        "connection_epoch": self.runtimes.c.connection_epoch + 1,
                    },
                )
                .returning(self.runtimes.c.connection_epoch)
            ).scalar_one()
            conn.execute(
                update(self.operations)
                .where(
                    self.operations.c.runtime_id == runtime_id,
                    self.operations.c.incarnation_id != incarnation_id,
                    self.operations.c.state.in_(["prepared", "dispatching"]),
                )
                .values(state="reconcile")
            )
        return epoch

    def admit_epoch(self, runtime_id, incarnation_id, epoch):
        """Bridge adopts an authenticated central epoch, never rolling it back."""
        with self.engine.begin() as conn:
            row = (
                conn.execute(select(self.runtimes).where(self.runtimes.c.runtime_id == runtime_id))
                .mappings()
                .first()
            )
            if row and row["incarnation_id"] == incarnation_id and epoch < row["connection_epoch"]:
                raise RemoteIdentityConflict("Stale runtime connection epoch")
            conn.execute(
                sqlite_insert(self.runtimes)
                .values(
                    runtime_id=runtime_id, incarnation_id=incarnation_id, connection_epoch=epoch
                )
                .on_conflict_do_update(
                    index_elements=["runtime_id"],
                    set_={"incarnation_id": incarnation_id, "connection_epoch": epoch},
                )
            )

    def unsettled_results(self, runtime_id, incarnation_id):
        with self.engine.connect() as conn:
            rows = (
                conn.execute(
                    select(self.operations)
                    .where(
                        self.operations.c.runtime_id == runtime_id,
                        self.operations.c.incarnation_id == incarnation_id,
                        self.operations.c.state == "settled",
                    )
                    .order_by(self.operations.c.deadline.desc())
                    .limit(64)
                )
                .mappings()
                .all()
            )
            return [self._decode(row)["result"] for row in rows]

    def next_revision(self, terminal_id, incarnation_id):
        from cli_agent_orchestrator.clients.runtime_channel_schema import RemoteObservationModel

        table = RemoteObservationModel.__table__
        with self.engine.begin() as conn:
            return conn.execute(
                sqlite_insert(table)
                .values(terminal_id=terminal_id, incarnation_id=incarnation_id, revision=1)
                .on_conflict_do_update(
                    index_elements=["terminal_id", "incarnation_id"],
                    set_={"revision": table.c.revision + 1},
                )
                .returning(table.c.revision)
            ).scalar_one()

    def get_runtime(self, runtime_id):
        with self.engine.connect() as conn:
            row = (
                conn.execute(select(self.runtimes).where(self.runtimes.c.runtime_id == runtime_id))
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def request_cancellation(self, op_id):
        from cli_agent_orchestrator.clients.runtime_channel_schema import RemoteCancellationModel

        with self.engine.begin() as conn:
            conn.execute(
                sqlite_insert(RemoteCancellationModel.__table__)
                .values(op_id=op_id, requested_at=time.time())
                .on_conflict_do_nothing(index_elements=["op_id"])
            )

    def cancellation_requested(self, op_id):
        from cli_agent_orchestrator.clients.runtime_channel_schema import RemoteCancellationModel

        with self.engine.connect() as conn:
            return (
                conn.execute(
                    select(RemoteCancellationModel.op_id).where(
                        RemoteCancellationModel.op_id == op_id
                    )
                ).first()
                is not None
            )

    def prepare(
        self,
        op_id,
        runtime_id,
        incarnation_id,
        command_type,
        payload,
        *,
        terminal_id=None,
        generation=None,
        deadline=None,
    ):
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
        with self.engine.begin() as conn:
            conn.execute(
                sqlite_insert(self.operations)
                .values(
                    op_id=op_id,
                    runtime_id=runtime_id,
                    incarnation_id=incarnation_id,
                    terminal_id=terminal_id,
                    generation=generation,
                    command_type=command_type,
                    request_hash=digest,
                    request_json=request,
                    deadline=expires_at,
                    state="prepared",
                )
                .on_conflict_do_nothing(index_elements=["op_id"])
            )
            row = (
                conn.execute(select(self.operations).where(self.operations.c.op_id == op_id))
                .mappings()
                .one()
            )
            if row["request_hash"] != digest or (
                deadline is not None and row["deadline"] != expires_at
            ):
                raise RemoteIdentityConflict("Operation identity refers to different material")
            return self._decode(row)

    def claim(self, op_id, *, connection_epoch=None):
        """Persist possible delivery at the current connection before transport bytes."""
        with self.engine.begin() as conn:
            predicates = [
                self.runtimes.c.runtime_id == self.operations.c.runtime_id,
                self.runtimes.c.incarnation_id == self.operations.c.incarnation_id,
            ]
            if connection_epoch is not None:
                predicates.append(self.runtimes.c.connection_epoch == connection_epoch)
            current = select(self.runtimes.c.runtime_id).where(*predicates).exists()
            values = {"state": "dispatching"}
            if connection_epoch is not None:
                values["connection_epoch"] = connection_epoch
            result = conn.execute(
                update(self.operations)
                .where(
                    self.operations.c.op_id == op_id,
                    self.operations.c.state == "prepared",
                    self.operations.c.deadline > time.time(),
                    current,
                )
                .values(**values)
            )
            return result.rowcount == 1

    def cancel_unsent(self, op_id):
        """Only a never-claimed operation can be durably declared unsent."""
        with self.engine.begin() as conn:
            result = conn.execute(
                update(self.operations)
                .where(self.operations.c.op_id == op_id, self.operations.c.state == "prepared")
                .values(state="cancelled")
            )
            return result.rowcount == 1

    def effect_is_current(self, op_id, *, connection_epoch):
        with self.engine.connect() as conn:
            current = (
                select(self.runtimes.c.runtime_id)
                .where(
                    self.runtimes.c.runtime_id == self.operations.c.runtime_id,
                    self.runtimes.c.incarnation_id == self.operations.c.incarnation_id,
                    self.runtimes.c.connection_epoch == connection_epoch,
                )
                .exists()
            )
            return (
                conn.execute(
                    select(self.operations.c.op_id).where(
                        self.operations.c.op_id == op_id,
                        self.operations.c.state == "dispatching",
                        self.operations.c.connection_epoch == connection_epoch,
                        self.operations.c.deadline > time.time(),
                        current,
                    )
                ).first()
                is not None
            )

    def mark_unknown(self, op_id):
        with self.engine.begin() as conn:
            conn.execute(
                update(self.operations)
                .where(self.operations.c.op_id == op_id, self.operations.c.state == "dispatching")
                .values(state="reconcile")
            )

    def settle(self, op_id, runtime_id, incarnation_id, result):
        encoded = _canonical(result)
        with self.engine.begin() as conn:
            current = (
                select(self.runtimes.c.runtime_id)
                .where(
                    self.runtimes.c.runtime_id == runtime_id,
                    self.runtimes.c.incarnation_id == incarnation_id,
                )
                .exists()
            )
            changed = conn.execute(
                update(self.operations)
                .where(
                    self.operations.c.op_id == op_id,
                    self.operations.c.runtime_id == runtime_id,
                    self.operations.c.incarnation_id == incarnation_id,
                    self.operations.c.state.in_(["dispatching", "reconcile"]),
                    current,
                )
                .values(state="settled", result_json=encoded)
            )
            return changed.rowcount == 1

    def get(self, op_id):
        with self.engine.connect() as conn:
            row = (
                conn.execute(select(self.operations).where(self.operations.c.op_id == op_id))
                .mappings()
                .first()
            )
            return self._decode(row) if row else None

    @staticmethod
    def _decode(row):
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        result["result"] = (
            json.loads(result["result_json"]) if result["result_json"] is not None else None
        )
        result.pop("result_json")
        return result
