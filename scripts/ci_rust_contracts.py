#!/usr/bin/env python3
"""Run the Rust live endpoint contracts against a private native PostgreSQL cluster."""

from __future__ import annotations

import os
import re
import secrets
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

POSTGRES_MAJOR = 18
_PG_START_TIMEOUT_SECONDS = 60
_PG_STOP_TIMEOUT_SECONDS = 30


def _private_directory(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_mode & 0o077
        or metadata.st_uid != os.geteuid()
    ):
        raise RuntimeError("Rust CI PostgreSQL paths must be owned private directories")


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class PrivatePostgresCluster:
    """An owner-only PostgreSQL cluster with loopback TCP and bounded lifecycle."""

    def __init__(
        self,
        root: Path,
        *,
        pg_bin_dir: Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        port_picker: Callable[[], int] | None = None,
    ) -> None:
        self.root = Path(root)
        self.pg_bin_dir = Path(pg_bin_dir).resolve() if pg_bin_dir is not None else None
        self._runner = runner or subprocess.run
        self._port_picker = port_picker or _free_loopback_port
        self.data_dir = self.root / "data"
        self.socket_dir = self.root / "socket"
        self.log_path = self.root / "postgres.log"
        self.port: int | None = None
        self.username: str | None = None
        self.password: str | None = None
        self.started = False
        self._start_attempted = False
        self._initdb: Path | None = None
        self._pg_ctl: Path | None = None

    def _binary(self, name: str) -> Path:
        if self.pg_bin_dir is None:
            located = shutil.which(name)
            candidate = Path(located) if located is not None else None
        else:
            candidate = self.pg_bin_dir / name
        if candidate is None:
            raise RuntimeError(f"PostgreSQL {POSTGRES_MAJOR} executable {name} was not found")
        try:
            resolved = candidate.resolve(strict=True)
            metadata = resolved.stat()
        except OSError as exc:
            raise RuntimeError(
                f"PostgreSQL {POSTGRES_MAJOR} executable {name} is unavailable"
            ) from exc
        if not stat.S_ISREG(metadata.st_mode) or not os.access(resolved, os.X_OK):
            raise RuntimeError(f"PostgreSQL {POSTGRES_MAJOR} executable {name} is not executable")
        return resolved

    def _run(self, args: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        try:
            return self._runner(
                [str(argument) for argument in args],
                capture_output=True,
                check=False,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("The private PostgreSQL process could not be controlled") from exc

    @staticmethod
    def _require_success(result: subprocess.CompletedProcess[str], operation: str) -> None:
        if result.returncode == 0:
            return
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            detail = detail[-1600:]
            raise RuntimeError(f"Private PostgreSQL {operation} failed: {detail}")
        raise RuntimeError(
            f"Private PostgreSQL {operation} failed with exit code {result.returncode}"
        )

    def start(self) -> None:
        _private_directory(self.root)
        if os.geteuid() == 0:
            raise RuntimeError("PostgreSQL initdb must run as the non-root CI user")

        self._initdb = self._binary("initdb")
        self._pg_ctl = self._binary("pg_ctl")
        if self._initdb.parent != self._pg_ctl.parent:
            raise RuntimeError("PostgreSQL initdb and pg_ctl must come from one installation")
        version = self._run([str(self._initdb), "--version"], timeout=10)
        self._require_success(version, "version check")
        match = re.search(r"PostgreSQL\)\s+(\d+)", version.stdout)
        if match is None or int(match.group(1)) != POSTGRES_MAJOR:
            raise RuntimeError(
                f"Rust CI requires PostgreSQL {POSTGRES_MAJOR}; initdb reported "
                f"{version.stdout.strip() or '<no version>'}"
            )
        pg_ctl_version = self._run([str(self._pg_ctl), "--version"], timeout=10)
        self._require_success(pg_ctl_version, "control version check")
        pg_ctl_match = re.search(r"PostgreSQL\)\s+(\d+)", pg_ctl_version.stdout)
        if pg_ctl_match is None or int(pg_ctl_match.group(1)) != POSTGRES_MAJOR:
            raise RuntimeError(
                f"Rust CI requires PostgreSQL {POSTGRES_MAJOR}; pg_ctl reported "
                f"{pg_ctl_version.stdout.strip() or '<no version>'}"
            )

        self.port = self._port_picker()
        self.username = f"cao_ci_{secrets.token_hex(8)}"
        self.password = secrets.token_urlsafe(32)
        self.data_dir.mkdir(mode=0o700)
        os.chmod(self.data_dir, 0o700)
        _private_directory(self.data_dir)
        self.socket_dir.mkdir(mode=0o700)
        os.chmod(self.socket_dir, 0o700)
        _private_directory(self.socket_dir)

        password_file = self.root / "bootstrap-password"
        descriptor = os.open(
            password_file,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(self.password + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            if password_file.stat().st_mode & 0o077:
                raise RuntimeError("Private PostgreSQL bootstrap password permissions are unsafe")
            initdb = self._run(
                [
                    str(self._initdb),
                    "--pgdata",
                    str(self.data_dir),
                    "--username",
                    self.username,
                    "--pwfile",
                    str(password_file),
                    "--encoding=UTF8",
                    "--locale=C",
                    "--auth-local=trust",
                    "--auth-host=scram-sha-256",
                ],
                timeout=120,
            )
            self._require_success(initdb, "initialization")
        finally:
            password_file.unlink(missing_ok=True)

        assert self.port is not None
        self._start_attempted = True
        start = self._run(
            [
                str(self._pg_ctl),
                "--pgdata",
                str(self.data_dir),
                "--log",
                str(self.log_path),
                "--options",
                "-h 127.0.0.1 "
                f"-p {self.port} "
                "-c max_connections=256 "
                "-c max_locks_per_transaction=256 "
                f"-c unix_socket_directories={shlex.quote(str(self.socket_dir))}",
                "--wait",
                "--timeout",
                str(_PG_START_TIMEOUT_SECONDS),
                "start",
            ],
            timeout=_PG_START_TIMEOUT_SECONDS + 10,
        )
        if start.returncode != 0:
            status = self._run(
                [str(self._pg_ctl), "--pgdata", str(self.data_dir), "status"], timeout=10
            )
            self.started = status.returncode == 0
            self._require_success(start, "startup")
        self.started = True

    def stop(self) -> None:
        if self._pg_ctl is None or not self._start_attempted:
            return
        _private_directory(self.root)
        _private_directory(self.data_dir)
        status = self._run(
            [str(self._pg_ctl), "--pgdata", str(self.data_dir), "status"], timeout=10
        )
        if status.returncode == 3:
            self.started = False
            return
        self._require_success(status, "status check")
        stop = self._run(
            [
                str(self._pg_ctl),
                "--pgdata",
                str(self.data_dir),
                "--wait",
                "--timeout",
                str(_PG_STOP_TIMEOUT_SECONDS),
                "--mode",
                "fast",
                "stop",
            ],
            timeout=_PG_STOP_TIMEOUT_SECONDS + 10,
        )
        self._require_success(stop, "shutdown")
        self.started = False


def _private_run_root() -> Path:
    base = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()).resolve(strict=True)
    if not base.is_dir():
        raise RuntimeError("RUNNER_TEMP must be an existing directory")
    root = Path(tempfile.mkdtemp(prefix="cao-rust-contract-", dir=base))
    os.chmod(root, 0o700)
    _private_directory(root)
    return root


def _remove_private_run_root(root: Path) -> None:
    _private_directory(root)
    shutil.rmtree(root)


def _ensure_project_root_on_path() -> Path:
    project_root = Path(__file__).resolve().parents[1]
    project_root_text = str(project_root)
    if project_root_text not in sys.path:
        sys.path.insert(0, project_root_text)
    return project_root


def _run_live_rust_tests(root: Path, service: Any, command: Sequence[str]) -> int:
    project_root = _ensure_project_root_on_path()

    from test.fixtures.cao_server import _pick_free_port, _start_cao_server
    from test.postgres.fixtures import isolated_live_targets

    targets_home = root / "targets"
    with isolated_live_targets(targets_home, service=service) as targets:
        server_home = root / "server-home"
        server_home.mkdir(mode=0o700)
        os.chmod(server_home, 0o700)
        _private_directory(server_home)
        server = _start_cao_server(
            server_home,
            _pick_free_port(),
            extra_env={"CAO_AUTH_LOCAL_TOKEN": "", "CAO_AUTH_LOCAL_TOKEN_FILE": ""},
            database_targets=targets,
            postgres_service=service,
        )
        try:
            environment = os.environ.copy()
            environment.pop("CAO_DATABASE_CONFIG_FILE", None)
            environment.pop("CAO_AUTH_LOCAL_TOKEN", None)
            environment.pop("CAO_AUTH_LOCAL_TOKEN_FILE", None)
            environment["CAO_API_HOST"] = "127.0.0.1"
            environment["CAO_API_PORT"] = str(server.port)
            return subprocess.run(
                list(command),
                cwd=project_root / "tui",
                env=environment,
                check=False,
            ).returncode
        finally:
            server.stop()


def _valid_cargo_command(arguments: Sequence[str]) -> bool:
    return list(arguments) == ["--", "cargo", "test", "--locked"]


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not _valid_cargo_command(arguments):
        print(
            "Usage: uv run python scripts/ci_rust_contracts.py -- cargo test --locked",
            file=sys.stderr,
        )
        return 2

    # Make repository test fixtures importable before creating the database
    # cluster so a packaging/path error cannot leave PostgreSQL running.
    _ensure_project_root_on_path()
    root = _private_run_root()
    cluster = PrivatePostgresCluster(root)
    try:
        cluster.start()
        assert cluster.port is not None and cluster.username is not None
        assert cluster.password is not None

        from test.postgres.fixtures import PostgresService

        service = PostgresService(
            metadata={
                "host": "127.0.0.1",
                "port": cluster.port,
                "user": cluster.username,
            },
            password=cluster.password,
        )
        print(f"PostgreSQL {POSTGRES_MAJOR} ready on 127.0.0.1:{cluster.port}", flush=True)
        return _run_live_rust_tests(root, service, arguments[1:])
    finally:
        cluster.stop()
        _remove_private_run_root(root)


if __name__ == "__main__":
    raise SystemExit(main())
