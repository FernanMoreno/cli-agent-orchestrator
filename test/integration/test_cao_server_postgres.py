"""The managed subprocess uses its private PostgreSQL authority scopes."""

from __future__ import annotations

import stat
from test.fixtures.cao_server import CaoServer

import pytest
import requests

from cli_agent_orchestrator.clients.postgres import connect

pytestmark = pytest.mark.integration


def test_cao_server_owns_private_three_scope_postgres_targets(cao_server: CaoServer) -> None:
    assert cao_server.database_config_path.is_file()
    assert stat.S_IMODE(cao_server.database_config_path.stat().st_mode) == 0o600

    targets = (
        cao_server.database_targets.main,
        cao_server.database_targets.auth,
        cao_server.database_targets.peer,
    )
    assert len({target.identity for target in targets}) == 3
    for target in targets:
        with connect(target, readonly=True) as connection:
            identity = connection.execute("SELECT store_id FROM _cao_store_identity").fetchone()
        assert identity is not None
        assert identity["store_id"] == target.store_id

    response = requests.get(f"{cao_server.url}/sessions", timeout=10)
    response.raise_for_status()
    assert response.json() == []
