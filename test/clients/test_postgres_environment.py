"""Storage configuration is server-owned and never forwarded to agents."""

from cli_agent_orchestrator.utils.database_environment import (
    is_database_env_key,
    without_database_environment,
)


def test_reserved_database_environment_is_removed():
    source = {
        "PATH": "/bin",
        "CAO_TERMINAL_ID": "t",
        "CAO_DATABASE_CONFIG_FILE": "/private/config",
        "CAO_POSTGRES_PASSWORD": "private",
        "CAO_AUTH_DATABASE_SECRET_FILE": "/private/auth",
    }
    result = without_database_environment(source)
    assert result == {"PATH": "/bin", "CAO_TERMINAL_ID": "t"}
    assert source["CAO_POSTGRES_PASSWORD"] == "private"
    assert is_database_env_key("CAO_DATABASE_CONFIG_FILE")
