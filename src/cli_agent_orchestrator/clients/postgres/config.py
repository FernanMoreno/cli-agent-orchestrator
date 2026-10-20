"""Explicit private PostgreSQL configuration. Credentials never enter target reprs."""

import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID


class ConfigurationError(RuntimeError):
    """Storage configuration is missing or unsafe."""


def private_json(path: Path) -> dict:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
                raise ConfigurationError("Database configuration must be a private regular file")
            if metadata.st_uid != os.geteuid():
                raise ConfigurationError("Database configuration owner does not match runtime user")
            value = json.load(stream)
        if not isinstance(value, dict):
            raise ConfigurationError("Invalid database configuration")
        return value
    except (OSError, ValueError) as exc:
        raise ConfigurationError("Cannot read private database configuration") from exc


@dataclass(frozen=True)
class DatabaseTarget:
    host: str
    port: int
    database: str
    user: str
    schema: str
    store_id: str
    secret_file: Path = field(repr=False)
    sslmode: str = "verify-full"
    home: Path | None = field(default=None, repr=False, compare=False)
    scope: str = "main"
    migration_user: str | None = field(default=None, repr=False)
    migration_secret_file: Path | None = field(default=None, repr=False)

    def __post_init__(self):
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", self.schema):
            raise ConfigurationError("Invalid PostgreSQL namespace")
        if self.schema in {"public", "information_schema"} or self.schema.startswith("pg_"):
            raise ConfigurationError("System PostgreSQL namespaces are not application targets")
        if self.migration_user == self.user:
            raise ConfigurationError("Runtime and migration roles must be distinct")
        try:
            UUID(self.store_id)
        except (ValueError, TypeError) as exc:
            raise ConfigurationError("Invalid logical database identity") from exc
        if not 1 <= self.port <= 65535 or self.sslmode not in {
            "disable",
            "allow",
            "prefer",
            "require",
            "verify-ca",
            "verify-full",
        }:
            raise ConfigurationError("Invalid PostgreSQL connection settings")
        object.__setattr__(self, "secret_file", Path(self.secret_file))

    @property
    def identity(self) -> tuple[str, int, str, str, str]:
        return self.host, self.port, self.database, self.schema, self.store_id

    @classmethod
    def from_settings(cls, scope="main", home: Path | None = None):
        if home is None:
            from cli_agent_orchestrator.constants import CAO_HOME_DIR

            home = CAO_HOME_DIR
        home = Path(home)
        override = os.environ.get("CAO_DATABASE_CONFIG_FILE")
        path = Path(override) if override else home / "database-config.json"
        configuration = private_json(path)
        if configuration.get("version") != 1:
            raise ConfigurationError("Unsupported database configuration version")
        try:
            values = dict(configuration["targets"][scope])
            values["secret_file"] = path.parent / values["secret_file"]
            if values.get("migration_secret_file"):
                values["migration_secret_file"] = path.parent / values["migration_secret_file"]
            return cls(**values, scope=scope, home=home)
        except (KeyError, TypeError) as exc:
            raise ConfigurationError("Missing PostgreSQL target configuration") from exc

    def connection_parameters(self) -> dict:
        secret = private_json(self.secret_file)
        password = secret.get("password")
        if not isinstance(password, str) or not password:
            raise ConfigurationError("Missing PostgreSQL credential")
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": self.user,
            "password": password,
            "sslmode": self.sslmode,
            "connect_timeout": 10,
            "application_name": "caos",
        }

    def migration_target(self):
        """Use explicit bootstrap credentials; never silently promote runtime role."""
        from dataclasses import replace

        if not self.migration_user or not self.migration_secret_file:
            raise ConfigurationError("Explicit PostgreSQL migration credentials are required")
        return replace(
            self,
            user=self.migration_user,
            secret_file=self.migration_secret_file,
            migration_user=None,
            migration_secret_file=None,
        )
