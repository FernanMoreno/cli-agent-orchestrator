"""Legacy receipt rows must fail closed at the atomic dispatch claim."""

from unittest.mock import MagicMock

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database


def test_unknown_legacy_receipt_phase_cannot_claim_or_deliver_another_turn(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
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
        # Existing databases can predate the CHECK. Ignore it only to create
        # that legacy row; the repository must independently preserve its fence.
        with engine.begin() as connection:
            connection.execute(text("PRAGMA ignore_check_constraints=ON"))
            connection.execute(
                text(
                    "INSERT INTO terminal_turn_receipts "
                    "(terminal_id,provider,generation,receipt_sha256,phase,created_at,updated_at) "
                    "VALUES (:terminal_id,'codex',:generation,:receipt_hash,'legacy-unknown',"
                    "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                ),
                {
                    "terminal_id": terminal_id,
                    "generation": generation,
                    "receipt_hash": receipt_hash,
                },
            )
            connection.execute(text("PRAGMA ignore_check_constraints=OFF"))
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
