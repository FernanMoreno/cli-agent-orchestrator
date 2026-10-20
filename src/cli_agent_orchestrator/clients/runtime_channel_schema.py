"""Additive remote operation journal; production migration owns registration."""

from sqlalchemy import BigInteger, CheckConstraint, Float, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class RemoteBase(DeclarativeBase):
    pass


class RemoteRuntimeModel(RemoteBase):
    __tablename__ = "remote_runtime_instances"
    runtime_id: Mapped[str] = mapped_column(String, primary_key=True)
    incarnation_id: Mapped[str] = mapped_column(String, nullable=False)
    connection_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)


class RemoteOperationModel(RemoteBase):
    __tablename__ = "remote_operations"
    op_id: Mapped[str] = mapped_column(String, primary_key=True)
    runtime_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    incarnation_id: Mapped[str] = mapped_column(String, nullable=False)
    terminal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    generation: Mapped[str | None] = mapped_column(String, nullable=True)
    connection_epoch: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
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
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class RemoteObservationModel(RemoteBase):
    __tablename__ = "remote_observation_revisions"
    terminal_id: Mapped[str] = mapped_column(String, primary_key=True)
    incarnation_id: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)


class RemoteCancellationModel(RemoteBase):
    __tablename__ = "remote_operation_cancellations"
    op_id: Mapped[str] = mapped_column(String, primary_key=True)
    requested_at: Mapped[float] = mapped_column(Float, nullable=False)


def runtime_channel_migrations():
    """Return the PostgreSQL DDL component for durable runtime-channel state.

    The central bootstrap owns applying this descriptor. Runtime constructors
    verify the installed component and never create tables.
    """
    from sqlalchemy.dialects.postgresql import dialect
    from sqlalchemy.schema import CreateIndex, CreateTable

    from cli_agent_orchestrator.clients.postgres.migrations import ComponentMigration

    tables = tuple(sorted(RemoteBase.metadata.tables.values(), key=lambda table: table.name))
    statements = [str(CreateTable(table).compile(dialect=dialect())) for table in tables]
    statements.extend(
        str(CreateIndex(index).compile(dialect=dialect()))
        for table in tables
        for index in sorted(table.indexes, key=lambda item: item.name or "")
    )
    names = tuple(table.name for table in tables)
    # Preserve the exact v1 declarations and checksum so installed v1 stores
    # can be verified before widening these columns.
    v1_statements = tuple(statement.replace(" BIGINT", " INTEGER") for statement in statements)
    v1 = ComponentMigration("runtime_channel", "main", 1, v1_statements, names)
    v2_statements = (
        "ALTER TABLE remote_runtime_instances ALTER COLUMN connection_epoch TYPE BIGINT",
        "ALTER TABLE remote_operations ALTER COLUMN connection_epoch TYPE BIGINT",
        "ALTER TABLE remote_terminal_placements ALTER COLUMN revision TYPE BIGINT",
        "ALTER TABLE remote_observation_revisions ALTER COLUMN revision TYPE BIGINT",
    )
    v2 = ComponentMigration("runtime_channel", "main", 2, v2_statements, names)
    return v1, v2
