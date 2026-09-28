"""T093 migration v19 remains additive, verified and rollback-safe."""

import importlib
import sqlite3
from contextlib import contextmanager

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkRepository


def test_v19_launch_provision_migration_is_verified_and_rolls_back_as_one_transaction(tmp_path):
    """Removing v19 or its DDL must make the real temporary SQLite proof fail."""
    repository_module = importlib.import_module("cli_agent_orchestrator.clients.work_repository")
    path = tmp_path / "migration.sqlite3"
    repository = WorkRepository(path)
    assert repository_module.SCHEMA_VERSION >= 19, "T093 migration v19 is missing"
    repository.initialize()
    repository.verify_schema()
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version,checksum,verification_result FROM work_migrations WHERE version=19"
        ).fetchone() == (19, repository_module._CHECKSUMS[19], "verified")
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='work_launch_provisions'"
        ).fetchone() == ("work_launch_provisions",)

    failed_path = tmp_path / "migration-failure.sqlite3"
    failed = WorkRepository(failed_path)
    original = failed.connection

    @contextmanager
    def denying_v19_table():
        with original() as connection:
            connection.set_authorizer(
                lambda action, name, *args: (
                    sqlite3.SQLITE_DENY
                    if action == sqlite3.SQLITE_CREATE_TABLE and name == "work_launch_provisions"
                    else sqlite3.SQLITE_OK
                )
            )
            yield connection

    failed.connection = denying_v19_table
    with pytest.raises(sqlite3.DatabaseError):
        failed.initialize()
    with sqlite3.connect(failed_path) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='work_migrations'"
            ).fetchone()
            is None
        )
