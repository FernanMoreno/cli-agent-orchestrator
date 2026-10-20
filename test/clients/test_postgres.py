"""PostgreSQL target parsing and private configuration contracts."""

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from cli_agent_orchestrator.clients.postgres import ConfigurationError, DatabaseTarget, Record


def test_record_retains_names_and_positions():
    row = Record(("id", "payload"), (3, b"data"))
    assert row[0] == row["id"] == 3
    assert dict(row) == {"id": 3, "payload": b"data"}


@pytest.mark.offline_database
def test_missing_configuration_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("CAO_DATABASE_CONFIG_FILE", raising=False)
    with pytest.raises(ConfigurationError):
        DatabaseTarget.from_settings(home=tmp_path)


@pytest.mark.offline_database
def test_private_configuration_redacts_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("CAO_DATABASE_CONFIG_FILE", raising=False)
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"password": "do-not-display"}))
    secret.chmod(0o600)
    config = tmp_path / "database-config.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "targets": {
                    "main": {
                        "host": "127.0.0.1",
                        "port": 5432,
                        "database": "cao",
                        "user": "cao",
                        "schema": "main",
                        "store_id": str(uuid4()),
                        "secret_file": str(secret),
                    }
                },
            }
        )
    )
    config.chmod(0o600)
    target = DatabaseTarget.from_settings(home=tmp_path)
    assert "do-not-display" not in repr(target)
    assert target.schema == "main"
    assert target.connection_parameters()["password"] == "do-not-display"


def test_world_readable_secret_is_rejected(tmp_path):
    secret = tmp_path / "secret.json"
    secret.write_text('{"password":"private"}')
    secret.chmod(0o644)
    with pytest.raises(ConfigurationError):
        DatabaseTarget(
            host="localhost",
            port=5432,
            database="cao",
            user="cao",
            schema="main",
            store_id=str(uuid4()),
            secret_file=secret,
        ).connection_parameters()
