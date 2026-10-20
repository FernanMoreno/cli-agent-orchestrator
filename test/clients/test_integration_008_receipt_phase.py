"""Legacy receipt rows must fail closed at the atomic dispatch claim."""

from test.postgres.orm import configured_orm_engine
from unittest.mock import MagicMock

from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.postgres import DatabaseTarget, connect


def test_unknown_legacy_receipt_phase_cannot_claim_or_deliver_another_turn(monkeypatch):
    engine = configured_orm_engine()
    database.Base.metadata.create_all(engine)
    isolated_session = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", isolated_session)
    terminal_id = "legacy-phase"
    generation, receipt_hash = "1" * 32, "a" * 64
    try:
        with isolated_session() as session:
            session.add(
                database.TerminalModel(
                    id=terminal_id,
                    provider="codex",
                    tmux_session="cao_legacy",
                    tmux_window="legacy",
                )
            )
            session.commit()
        # Model a historical row predating the CHECK in the disposable owner's
        # schema. PostgreSQL's NOT VALID constraint still fences all new writes;
        # dispatch must independently refuse the malformed historical phase.
        target = DatabaseTarget.from_settings()
        with connect(target.migration_target()) as connection, connection.transaction():
            connection.execute(
                "ALTER TABLE terminal_turn_receipts DROP CONSTRAINT ck_terminal_turn_receipts_phase"
            )
            connection.execute(
                "INSERT INTO terminal_turn_receipts "
                "(terminal_id,provider,generation,receipt_sha256,phase,created_at,updated_at) "
                "VALUES (%s,'codex',%s,%s,'legacy-unknown',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                (terminal_id, generation, receipt_hash),
            )
            connection.execute(
                "ALTER TABLE terminal_turn_receipts ADD CONSTRAINT ck_terminal_turn_receipts_phase "
                "CHECK (phase IN ('prepared','sent','result_verified')) NOT VALID"
            )
        before = database.get_terminal_turn_receipt(terminal_id)
        delivery = MagicMock()

        claimed = database.begin_terminal_turn_receipt(terminal_id, "codex", "2" * 32, "b" * 64)
        # Model the dispatch boundary: delivery requires a successful claim.
        if claimed is not None:
            delivery()

        delivery.assert_not_called()
        assert claimed is None
        assert database.get_terminal_turn_receipt(terminal_id) == before
        assert database.get_terminal_turn_recovery(terminal_id, "2" * 32) is None
    finally:
        engine.dispose()
