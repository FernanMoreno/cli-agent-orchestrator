"""Native database secrets must not cross the provider subprocess boundary."""

import pytest

from cli_agent_orchestrator.clients.tmux import TmuxClient
from cli_agent_orchestrator.utils.database_environment import without_database_environment

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize(
    "key",
    ["PGPASSWORD", "PGPASSFILE", "PGSERVICE", "PGSERVICEFILE", "PGSSLKEY", "PGSSLCERT"],
)
def test_libpq_secret_and_service_configuration_is_removed(key):
    environment = {"PATH": "/usr/bin", key: "/private/database-value", "AWS_REGION": "eu-west-1"}
    assert without_database_environment(environment) == {
        "PATH": "/usr/bin",
        "AWS_REGION": "eu-west-1",
    }
    assert environment[key] == "/private/database-value"
    assert TmuxClient._is_blocked_env_key(key)
    pane_environment = {}
    TmuxClient._merge_extra_env(pane_environment, {key: "/operator-supplied-value"})
    assert key not in pane_environment
