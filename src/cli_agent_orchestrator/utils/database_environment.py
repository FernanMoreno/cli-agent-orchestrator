"""Reserved server storage variables must not reach providers or pane processes."""

from collections.abc import Mapping

_DATABASE_PREFIXES = (
    "CAO_DATABASE_",
    "CAO_POSTGRES_",
    "CAO_MAIN_DATABASE_",
    "CAO_AUTH_DATABASE_",
    "CAO_PEER_DATABASE_",
)

# libpq can obtain passwords and connection secrets from inherited files even
# when CAO's own configuration variables have been removed.
_LIBPQ_SECRET_KEYS = frozenset(
    {"PGPASSWORD", "PGPASSFILE", "PGSERVICE", "PGSERVICEFILE", "PGSSLKEY", "PGSSLCERT"}
)


def is_database_env_key(key: str) -> bool:
    return key.startswith(_DATABASE_PREFIXES) or key in _LIBPQ_SECRET_KEYS


def without_database_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in environment.items() if not is_database_env_key(key)}
