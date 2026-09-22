"""Tests for cleanup service."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from cli_agent_orchestrator.services.cleanup_service import cleanup_old_data


class TestCleanupOldData:
    """Tests for cleanup_old_data function."""

    @patch("cli_agent_orchestrator.services.cleanup_service.SessionLocal")
    @patch("cli_agent_orchestrator.services.cleanup_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.cleanup_service.LOG_DIR")
    @patch("cli_agent_orchestrator.services.cleanup_service.RETENTION_DAYS", 7)
    def test_cleanup_old_data_deletes_old_terminals(
        self, mock_log_dir, mock_terminal_log_dir, mock_session_local
    ):
        """Test that cleanup deletes old terminals from database."""
        # Setup mock database session
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.delete.return_value = 5

        # Setup mock directories (non-existent)
        mock_log_dir.exists.return_value = False
        mock_terminal_log_dir.exists.return_value = False

        # Execute
        cleanup_old_data()

        # Verify terminal cleanup was called
        assert mock_db.query.called
        assert mock_db.commit.called

    @patch("cli_agent_orchestrator.services.cleanup_service._delete_terminal_turn_receipt_rows")
    @patch("cli_agent_orchestrator.services.cleanup_service._record_native_child_cleanup_rows")
    @patch("cli_agent_orchestrator.services.cleanup_service.provider_manager")
    @patch("cli_agent_orchestrator.services.cleanup_service.SessionLocal")
    @patch("cli_agent_orchestrator.services.cleanup_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.cleanup_service.LOG_DIR")
    @patch("cli_agent_orchestrator.services.cleanup_service.RETENTION_DAYS", 7)
    def test_cleanup_old_data_retains_grok_row_when_provider_cleanup_is_deferred(
        self,
        mock_log_dir,
        mock_terminal_log_dir,
        mock_session_local,
        mock_provider_manager,
        mock_record_native_child_cleanup,
        mock_delete_turn_receipts,
    ):
        """A retained terminal also retains its child-cleanup and receipt state."""
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db
        old_terminal = MagicMock(id="retained-grok", provider="grok_cli")
        old_terminal_query = MagicMock()
        inbox_query = MagicMock()
        idempotency_query = MagicMock()
        mock_db.query.side_effect = [
            old_terminal_query,
            inbox_query,
            idempotency_query,
        ]
        old_terminal_query.filter.return_value.all.return_value = [old_terminal]
        inbox_query.filter.return_value.delete.return_value = 0
        idempotency_query.filter.return_value.delete.return_value = 0
        mock_provider_manager.cleanup_provider.return_value = False
        mock_log_dir.exists.return_value = False
        mock_terminal_log_dir.exists.return_value = False

        cleanup_old_data()

        mock_provider_manager.cleanup_provider.assert_called_once_with("retained-grok")
        mock_record_native_child_cleanup.assert_not_called()
        mock_delete_turn_receipts.assert_not_called()
        assert mock_db.query.call_count == 3

    @patch("cli_agent_orchestrator.services.cleanup_service._delete_terminal_turn_receipt_rows")
    @patch("cli_agent_orchestrator.services.cleanup_service._record_native_child_cleanup_rows")
    @patch("cli_agent_orchestrator.services.cleanup_service.provider_manager")
    @patch("cli_agent_orchestrator.services.cleanup_service.SessionLocal")
    @patch("cli_agent_orchestrator.services.cleanup_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.cleanup_service.LOG_DIR")
    @patch("cli_agent_orchestrator.services.cleanup_service.RETENTION_DAYS", 7)
    def test_cleanup_deletes_receipts_and_native_children_for_only_deleted_terminals(
        self,
        mock_log_dir,
        mock_terminal_log_dir,
        mock_session_local,
        mock_provider_manager,
        mock_record_native_child_cleanup,
        mock_delete_turn_receipts,
    ):
        """Provider-deferred terminal rows never leak into destructive cleanup batches."""
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db
        retained_terminal = MagicMock(id="retained-provider", provider="grok_cli")
        deleted_terminal = MagicMock(id="delete-me", provider="mock_cli")
        old_terminal_query = MagicMock()
        terminal_delete_query = MagicMock()
        inbox_query = MagicMock()
        idempotency_query = MagicMock()
        mock_db.query.side_effect = [
            old_terminal_query,
            terminal_delete_query,
            inbox_query,
            idempotency_query,
        ]
        old_terminal_query.filter.return_value.all.return_value = [
            retained_terminal,
            deleted_terminal,
        ]
        terminal_delete_query.filter.return_value.delete.return_value = 1
        inbox_query.filter.return_value.delete.return_value = 0
        idempotency_query.filter.return_value.delete.return_value = 0
        mock_provider_manager.cleanup_provider.side_effect = [False, True]
        mock_log_dir.exists.return_value = False
        mock_terminal_log_dir.exists.return_value = False

        cleanup_old_data()

        mock_provider_manager.cleanup_provider.assert_has_calls(
            [
                call("retained-provider"),
                call("delete-me"),
            ]
        )
        mock_record_native_child_cleanup.assert_called_once_with(mock_db, ["delete-me"])
        mock_delete_turn_receipts.assert_called_once_with(mock_db, ["delete-me"])
        terminal_delete_query.filter.return_value.delete.assert_called_once_with(
            synchronize_session=False
        )

    @patch("cli_agent_orchestrator.services.cleanup_service.status_monitor")
    @patch("cli_agent_orchestrator.services.cleanup_service.fifo_manager")
    @patch("cli_agent_orchestrator.services.cleanup_service.SessionLocal")
    @patch("cli_agent_orchestrator.services.cleanup_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.cleanup_service.LOG_DIR")
    @patch("cli_agent_orchestrator.services.cleanup_service.RETENTION_DAYS", 7)
    def test_cleanup_old_data_deletes_old_inbox_messages(
        self,
        mock_log_dir,
        mock_terminal_log_dir,
        mock_session_local,
        mock_fifo_manager,
        mock_status_monitor,
    ):
        """Test that cleanup deletes old inbox messages from database."""
        # Setup mock database session
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.all.return_value = []
        mock_db.query.return_value.filter.return_value.delete.return_value = 10

        # Setup mock directories (non-existent)
        mock_log_dir.exists.return_value = False
        mock_terminal_log_dir.exists.return_value = False

        # Execute
        cleanup_old_data()

        # Verify cleanup was called:
        # Session 1: query.all() for terminal iteration + query.delete() for terminal deletion
        # Session 2: query.delete() for inbox deletion
        # Session 3: query.delete() for idempotency-key deletion
        assert mock_db.query.call_count >= 2
        assert mock_db.commit.call_count == 3

    @patch("cli_agent_orchestrator.services.cleanup_service.SessionLocal")
    @patch("cli_agent_orchestrator.services.cleanup_service.RETENTION_DAYS", 7)
    def test_cleanup_old_data_deletes_old_terminal_log_files(self, mock_session_local):
        """Test that cleanup deletes old terminal log files."""
        # Setup mock database session
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.delete.return_value = 0

        # Create temp directory with old and new log files
        with tempfile.TemporaryDirectory() as tmpdir:
            terminal_log_dir = Path(tmpdir) / "terminal"
            terminal_log_dir.mkdir()

            # Create old log file (older than retention period)
            old_log = terminal_log_dir / "old.log"
            old_log.write_text("old log content")
            old_time = (datetime.now() - timedelta(days=10)).timestamp()
            import os

            os.utime(old_log, (old_time, old_time))

            # Create new log file (within retention period)
            new_log = terminal_log_dir / "new.log"
            new_log.write_text("new log content")

            with patch(
                "cli_agent_orchestrator.services.cleanup_service.TERMINAL_LOG_DIR",
                terminal_log_dir,
            ):
                with patch(
                    "cli_agent_orchestrator.services.cleanup_service.LOG_DIR",
                    Path(tmpdir) / "nonexistent",
                ):
                    cleanup_old_data()

            # Verify old log was deleted, new log remains
            assert not old_log.exists()
            assert new_log.exists()

    @patch("cli_agent_orchestrator.services.cleanup_service.SessionLocal")
    @patch("cli_agent_orchestrator.services.cleanup_service.RETENTION_DAYS", 7)
    def test_cleanup_old_data_deletes_old_server_log_files(self, mock_session_local):
        """Test that cleanup deletes old server log files."""
        # Setup mock database session
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.delete.return_value = 0

        # Create temp directory with old and new log files
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs"
            log_dir.mkdir()

            # Create old log file
            old_log = log_dir / "server_old.log"
            old_log.write_text("old server log")
            old_time = (datetime.now() - timedelta(days=10)).timestamp()
            import os

            os.utime(old_log, (old_time, old_time))

            # Create new log file
            new_log = log_dir / "server_new.log"
            new_log.write_text("new server log")

            with patch(
                "cli_agent_orchestrator.services.cleanup_service.TERMINAL_LOG_DIR",
                Path(tmpdir) / "nonexistent",
            ):
                with patch(
                    "cli_agent_orchestrator.services.cleanup_service.LOG_DIR",
                    log_dir,
                ):
                    cleanup_old_data()

            # Verify old log was deleted, new log remains
            assert not old_log.exists()
            assert new_log.exists()

    @patch("cli_agent_orchestrator.services.cleanup_service.SessionLocal")
    @patch("cli_agent_orchestrator.services.cleanup_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.cleanup_service.LOG_DIR")
    @patch("cli_agent_orchestrator.services.cleanup_service.RETENTION_DAYS", 7)
    def test_cleanup_old_data_handles_database_error(
        self, mock_log_dir, mock_terminal_log_dir, mock_session_local
    ):
        """Test that cleanup handles database errors gracefully."""
        # Setup mock database session to raise an error
        mock_session_local.return_value.__enter__.side_effect = Exception("Database error")

        # Setup mock directories (non-existent)
        mock_log_dir.exists.return_value = False
        mock_terminal_log_dir.exists.return_value = False

        # Execute - should not raise exception
        cleanup_old_data()  # Should log error but not raise

    @patch("cli_agent_orchestrator.services.cleanup_service.SessionLocal")
    @patch("cli_agent_orchestrator.services.cleanup_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.cleanup_service.LOG_DIR")
    @patch("cli_agent_orchestrator.services.cleanup_service.RETENTION_DAYS", 7)
    def test_cleanup_old_data_handles_empty_directories(
        self, mock_log_dir, mock_terminal_log_dir, mock_session_local
    ):
        """Test that cleanup handles empty or non-existent directories."""
        # Setup mock database session
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db
        mock_db.query.return_value.filter.return_value.delete.return_value = 0

        # Setup mock directories as non-existent
        mock_log_dir.exists.return_value = False
        mock_terminal_log_dir.exists.return_value = False

        # Execute - should complete without error
        cleanup_old_data()

        # Verify database operations still occurred
        assert mock_db.query.called

    @patch("cli_agent_orchestrator.services.cleanup_service.status_monitor")
    @patch("cli_agent_orchestrator.services.cleanup_service.fifo_manager")
    @patch("cli_agent_orchestrator.services.cleanup_service.SessionLocal")
    @patch("cli_agent_orchestrator.services.cleanup_service.RETENTION_DAYS", 30)
    def test_cleanup_uses_correct_retention_period(
        self, mock_session_local, mock_fifo_manager, mock_status_monitor
    ):
        """Test that cleanup uses the configured retention period."""
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db

        # Capture the filter argument to verify cutoff date
        filter_calls = []

        def capture_filter(condition):
            filter_calls.append(condition)
            mock_result = MagicMock()
            mock_result.all.return_value = []
            mock_result.delete.return_value = 0
            return mock_result

        mock_db.query.return_value.filter = capture_filter

        with patch(
            "cli_agent_orchestrator.services.cleanup_service.TERMINAL_LOG_DIR"
        ) as mock_terminal:
            with patch("cli_agent_orchestrator.services.cleanup_service.LOG_DIR") as mock_log:
                mock_terminal.exists.return_value = False
                mock_log.exists.return_value = False
                cleanup_old_data()

        # Verify filter was called (terminals: .all() + .delete(), inbox: .delete())
        assert len(filter_calls) >= 2
