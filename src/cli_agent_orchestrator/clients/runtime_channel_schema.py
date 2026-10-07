"""Additive remote operation journal; production migration owns registration."""

from sqlalchemy import CheckConstraint, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class RemoteBase(DeclarativeBase):
    pass


class RemoteRuntimeModel(RemoteBase):
    __tablename__ = "remote_runtime_instances"
    runtime_id: Mapped[str] = mapped_column(String, primary_key=True)
    incarnation_id: Mapped[str] = mapped_column(String, nullable=False)
    connection_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class RemoteOperationModel(RemoteBase):
    __tablename__ = "remote_operations"
    op_id: Mapped[str] = mapped_column(String, primary_key=True)
    runtime_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    incarnation_id: Mapped[str] = mapped_column(String, nullable=False)
    terminal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    generation: Mapped[str | None] = mapped_column(String, nullable=True)
    connection_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deadline: Mapped[float] = mapped_column(Float, nullable=False)
    command_type: Mapped[str] = mapped_column(String, nullable=False)
    request_hash: Mapped[str] = mapped_column(String, nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    __table_args__ = (
        CheckConstraint("state IN ('prepared','dispatching','reconcile','settled','cancelled')"),
    )


class RemotePlacementModel(RemoteBase):
    __tablename__ = "remote_terminal_placements"
    terminal_id: Mapped[str] = mapped_column(String, primary_key=True)
    runtime_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    incarnation_id: Mapped[str] = mapped_column(String, nullable=False)
    launch_op_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    session_incarnation_id: Mapped[str] = mapped_column(String, nullable=False)
    identity_json: Mapped[str] = mapped_column(Text, nullable=False)
    projection_json: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RemoteObservationModel(RemoteBase):
    __tablename__ = "remote_observation_revisions"
    terminal_id: Mapped[str] = mapped_column(String, primary_key=True)
    incarnation_id: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)


class RemoteCancellationModel(RemoteBase):
    __tablename__ = "remote_operation_cancellations"
    op_id: Mapped[str] = mapped_column(String, primary_key=True)
    requested_at: Mapped[float] = mapped_column(Float, nullable=False)
