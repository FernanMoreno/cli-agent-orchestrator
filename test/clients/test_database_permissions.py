"""Native PostgreSQL credentials are private and unsafe stores fail closed."""

import os
from dataclasses import replace

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.postgres import ConfigurationError, connect
from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch, verify_runtime_role

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("permission", [0o644, 0o660, 0o666])
def test_runtime_rejects_readable_credential_files(postgres_core, permission):
    secret = postgres_core.secret_file
    os.chmod(secret, permission)
    try:
        with pytest.raises(ConfigurationError):
            connect(postgres_core)
    finally:
        os.chmod(secret, 0o600)


def test_runtime_rejects_schema_owner_credentials(postgres_core):
    with connect(postgres_core.migration_target()) as owner:
        with pytest.raises(SchemaMismatch):
            verify_runtime_role(owner)


def test_initialization_does_not_create_legacy_local_database_file(postgres_core, monkeypatch):
    from test.postgres.fixtures import make_orm_engine

    from cli_agent_orchestrator import constants

    engine = make_orm_engine(postgres_core)
    monkeypatch.setattr(database, "engine", engine)
    legacy_file = constants.DATABASE_FILE
    try:
        database.init_db()
        assert not legacy_file.exists()
    finally:
        engine.dispose()


def test_missing_credentials_do_not_get_created(postgres_core, tmp_path):
    absent = tmp_path / "absent.json"
    with pytest.raises(ConfigurationError):
        connect(replace(postgres_core, secret_file=absent))
    assert not absent.exists()
