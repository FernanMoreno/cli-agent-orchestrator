"""Shared per-user PostgreSQL registry for live CAO processes and write leases."""

from __future__ import annotations

import ipaddress
import json
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import psutil

from cli_agent_orchestrator.clients.postgres import (
    ConfigurationError,
    DatabaseError,
    DatabaseTarget,
    IntegrityError,
    Record,
    connect,
)
from cli_agent_orchestrator.clients.postgres.migrations import (
    ComponentMigration,
    SchemaMismatch,
    verify_component,
    verify_runtime_role,
)
from cli_agent_orchestrator.constants import LOCAL_PEER_DIR
from cli_agent_orchestrator.services.local_peer_identity import (
    LocalProcessIdentity,
    current_process_identity,
)
from cli_agent_orchestrator.services.local_peers.host_identity import (
    HostIdentity,
    HostIdentityError,
    current_host_identity,
)


class ProfileAlreadyRunningError(RuntimeError):
    """A different live server already owns this CAO profile identity."""


class ProjectWriteLeaseBusy(RuntimeError):
    """Another peer task currently owns the project's write lease."""


class LocalPeerRegistryUnavailable(RuntimeError):
    """The user-wide peer registry cannot safely serve this process."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


_TABLES = ("local_peer_processes", "local_project_write_leases")

LOCAL_PEER_MIGRATIONS = (
    ComponentMigration(
        component="local_peer_registry",
        scope="peer",
        version=1,
        statements=(
            """CREATE TABLE local_peer_processes (
                host_id TEXT NOT NULL,
                owner_uid BIGINT NOT NULL CHECK (owner_uid >= 0),
                instance_id TEXT NOT NULL,
                process_generation TEXT NOT NULL,
                pid BIGINT NOT NULL CHECK (pid > 0),
                process_started_at DOUBLE PRECISION NOT NULL,
                display_name TEXT NOT NULL,
                loopback_host TEXT NOT NULL CHECK (loopback_host IN ('127.0.0.1', '::1')),
                loopback_port INTEGER NOT NULL CHECK (loopback_port BETWEEN 1 AND 65535),
                started_at DOUBLE PRECISION NOT NULL,
                last_seen DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (host_id, owner_uid, instance_id),
                UNIQUE (host_id, owner_uid, process_generation)
            )""",
            """CREATE INDEX local_peer_processes_display
                ON local_peer_processes(host_id, owner_uid, display_name, instance_id)""",
            """CREATE TABLE local_project_write_leases (
                host_id TEXT NOT NULL,
                owner_uid BIGINT NOT NULL CHECK (owner_uid >= 0),
                project_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                owner_instance_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('held', 'interrupted', 'reconcile', 'released')),
                heartbeat_at DOUBLE PRECISION NOT NULL,
                updated_at DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (host_id, owner_uid, project_id)
            )""",
            """CREATE INDEX local_project_write_leases_owner
                ON local_project_write_leases(host_id, owner_uid, owner_instance_id)""",
        ),
        tables=_TABLES,
    ),
)


@dataclass(frozen=True)
class LocalPeerRecord:
    instance_id: str
    process_generation: str
    pid: int
    process_started_at: float
    display_name: str
    loopback_host: str
    loopback_port: int
    started_at: float
    last_seen: float

    @property
    def base_url(self) -> str:
        host = f"[{self.loopback_host}]" if ":" in self.loopback_host else self.loopback_host
        return f"http://{host}:{self.loopback_port}"


def _database_target() -> DatabaseTarget:
    target = DatabaseTarget.from_settings(scope="peer")
    if not isinstance(target, DatabaseTarget):
        raise LocalPeerRegistryUnavailable("Local peer registry is unavailable")
    return target


@lru_cache(maxsize=8)
def _verify_target(target: DatabaseTarget) -> None:
    try:
        with connect(target, readonly=True) as connection:
            verify_runtime_role(connection)
            verify_component(connection, LOCAL_PEER_MIGRATIONS)
    except (ConfigurationError, DatabaseError, SchemaMismatch) as exc:
        raise LocalPeerRegistryUnavailable("Local peer registry is unavailable") from exc


def _host_scope() -> HostIdentity:
    try:
        return current_host_identity(LOCAL_PEER_DIR)
    except HostIdentityError as exc:
        raise LocalPeerRegistryUnavailable("Local peer registry is unavailable") from exc


def _connect():
    try:
        target = _database_target()
        _verify_target(target)
        return connect(target)
    except (ConfigurationError, DatabaseError, SchemaMismatch) as exc:
        raise LocalPeerRegistryUnavailable("Local peer registry is unavailable") from exc


def _process_is_same_live_instance(pid: int, expected_started_at: float) -> bool:
    try:
        actual_started_at = float(psutil.Process(pid).create_time())
    except (psutil.Error, OSError, ValueError):
        return False
    return abs(actual_started_at - expected_started_at) < 1.0


def _identity_endpoint_confirms(row: Record) -> bool:
    """Keep a registry row when its recorded CAO process still proves its identity."""
    host = str(row["loopback_host"])
    try:
        address = ipaddress.ip_address(host)
        port = int(str(row["loopback_port"]))
        if not address.is_loopback or not 1 <= port <= 65535:
            return False
    except (ValueError, TypeError):
        return False

    url_host = f"[{host}]" if address.version == 6 else host
    request = Request(f"http://{url_host}:{port}/local-coordination/identity")
    try:
        opener = build_opener(ProxyHandler({}), _RejectRedirects())
        with opener.open(request, timeout=2.0) as response:
            payload = response.read(16_385)
        if len(payload) > 16_384:
            return False
        identity = json.loads(payload)
    except (OSError, ValueError, TypeError):
        return False

    if not isinstance(identity, dict):
        return False
    reported_instance_id = identity.get("instance_id")
    reported_generation = identity.get("process_generation")
    reported_pid = identity.get("pid")
    if (
        not isinstance(reported_instance_id, str)
        or not isinstance(reported_generation, str)
        or type(reported_pid) is not int
    ):
        return False
    expected_instance_id = row["instance_id"]
    expected_generation = row["process_generation"]
    expected_pid = row["pid"]
    if (
        not isinstance(expected_instance_id, str)
        or not isinstance(expected_generation, str)
        or type(expected_pid) is not int
    ):
        return False
    return (
        reported_instance_id == expected_instance_id
        and reported_generation == expected_generation
        and reported_pid == expected_pid
    )


def _stale_process_rows(connection, host: HostIdentity) -> list[dict[str, object]]:
    """Inspect only the current host/user scope; remote PIDs are never compared."""
    rows = connection.execute(
        "SELECT * FROM local_peer_processes WHERE host_id=%s AND owner_uid=%s",
        (host.host_id, host.owner_uid),
    ).fetchall()
    stale = []
    for row in rows:
        if _process_is_same_live_instance(row["pid"], row["process_started_at"]):
            continue
        if not _identity_endpoint_confirms(row):
            stale.append(dict(row))
    return stale


def _prune_stale_processes(connection, stale_rows: list[dict[str, object]]) -> None:
    """Delete only exact stale current-host/user snapshots checked before the lock."""
    connection.executemany(
        """DELETE FROM local_peer_processes
           WHERE host_id=%s AND owner_uid=%s AND instance_id=%s AND process_generation=%s
             AND pid=%s AND process_started_at=%s AND last_seen=%s""",
        (
            (
                row["host_id"],
                row["owner_uid"],
                row["instance_id"],
                row["process_generation"],
                row["pid"],
                row["process_started_at"],
                row["last_seen"],
            )
            for row in stale_rows
        ),
    )


def register_instance(
    identity: LocalProcessIdentity | None = None, *, prune_stale: bool = True
) -> LocalPeerRecord:
    """Publish this server; reject duplicate live processes for one profile."""
    identity = identity or current_process_identity()
    host = _host_scope()
    now = time.time()
    connection = _connect()
    try:
        stale_rows = _stale_process_rows(connection, host) if prune_stale else []
        connection.begin()
        _prune_stale_processes(connection, stale_rows)
        cursor = connection.execute(
            """INSERT INTO local_peer_processes
               (host_id,owner_uid,instance_id,process_generation,pid,process_started_at,
                display_name,loopback_host,loopback_port,started_at,last_seen)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(host_id,owner_uid,instance_id) DO UPDATE SET
                   pid=EXCLUDED.pid,process_started_at=EXCLUDED.process_started_at,
                   display_name=EXCLUDED.display_name,loopback_host=EXCLUDED.loopback_host,
                   loopback_port=EXCLUDED.loopback_port,last_seen=EXCLUDED.last_seen
               WHERE local_peer_processes.process_generation=EXCLUDED.process_generation
               RETURNING instance_id,process_generation,pid,process_started_at,display_name,
                         loopback_host,loopback_port,started_at,last_seen""",
            (
                host.host_id,
                host.owner_uid,
                identity.instance_id,
                identity.process_generation,
                identity.pid,
                identity.process_started_at,
                identity.display_name,
                identity.loopback_host,
                identity.loopback_port,
                now,
                now,
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise ProfileAlreadyRunningError(
                "This CAO profile already has a live server; use a different CAO_HOME_DIR."
            )
        connection.commit()
        return LocalPeerRecord(**dict(row))
    except IntegrityError as exc:
        if connection.in_transaction:
            connection.rollback()
        raise ProfileAlreadyRunningError(
            "This CAO profile already has a live server; use a different CAO_HOME_DIR."
        ) from exc
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def unregister_instance(identity: LocalProcessIdentity | None = None) -> None:
    """Remove only the current host/user row owned by this process generation."""
    identity = identity or current_process_identity()
    host = _host_scope()
    connection = _connect()
    try:
        connection.execute(
            """DELETE FROM local_peer_processes
               WHERE host_id=%s AND owner_uid=%s AND instance_id=%s AND process_generation=%s""",
            (host.host_id, host.owner_uid, identity.instance_id, identity.process_generation),
        )
    finally:
        connection.close()


def list_instances() -> list[LocalPeerRecord]:
    """Return same-host/user candidates whose PID and start fence still match."""
    host = _host_scope()
    connection = _connect()
    try:
        stale_rows = _stale_process_rows(connection, host)
        connection.begin()
        _prune_stale_processes(connection, stale_rows)
        connection.commit()
        rows = connection.execute(
            """SELECT instance_id,process_generation,pid,process_started_at,display_name,
                      loopback_host,loopback_port,started_at,last_seen
               FROM local_peer_processes WHERE host_id=%s AND owner_uid=%s
               ORDER BY display_name,instance_id""",
            (host.host_id, host.owner_uid),
        ).fetchall()
        return [LocalPeerRecord(**dict(row)) for row in rows]
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def get_instance(instance_id: str) -> LocalPeerRecord | None:
    return next((item for item in list_instances() if item.instance_id == instance_id), None)


def acquire_project_write_lease(*, project_id: str, task_id: str, owner_instance_id: str) -> None:
    """Acquire a single-host/user-wide lease atomically across CAO profiles."""
    if not project_id or not task_id or not owner_instance_id:
        raise ValueError("project, task and owner identities are required")
    host = _host_scope()
    connection = _connect()
    now = time.time()
    try:
        connection.begin()
        inserted = connection.execute(
            """INSERT INTO local_project_write_leases
               (host_id,owner_uid,project_id,task_id,owner_instance_id,state,heartbeat_at,updated_at)
               VALUES (%s,%s,%s,%s,%s,'held',%s,%s)
               ON CONFLICT(host_id,owner_uid,project_id) DO NOTHING""",
            (host.host_id, host.owner_uid, project_id, task_id, owner_instance_id, now, now),
        )
        if inserted.rowcount == 1:
            connection.commit()
            return
        row = connection.execute(
            """SELECT task_id,owner_instance_id,state FROM local_project_write_leases
               WHERE host_id=%s AND owner_uid=%s AND project_id=%s FOR UPDATE""",
            (host.host_id, host.owner_uid, project_id),
        ).fetchone()
        if row is None:
            raise LocalPeerRegistryUnavailable("Project lease changed during acquisition")
        if row["state"] != "released":
            if row["task_id"] == task_id and row["owner_instance_id"] == owner_instance_id:
                connection.execute(
                    """UPDATE local_project_write_leases SET heartbeat_at=%s,updated_at=%s
                       WHERE host_id=%s AND owner_uid=%s AND project_id=%s""",
                    (now, now, host.host_id, host.owner_uid, project_id),
                )
                connection.commit()
                return
            raise ProjectWriteLeaseBusy("another local CAO task holds this project's write lease")
        connection.execute(
            """UPDATE local_project_write_leases
               SET task_id=%s,owner_instance_id=%s,state='held',heartbeat_at=%s,updated_at=%s
               WHERE host_id=%s AND owner_uid=%s AND project_id=%s""",
            (task_id, owner_instance_id, now, now, host.host_id, host.owner_uid, project_id),
        )
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def update_project_write_lease(
    *, project_id: str, task_id: str, owner_instance_id: str, state: str
) -> bool:
    """Set a lease state only when the exact task still owns it."""
    if state not in {"held", "interrupted", "reconcile", "released"}:
        raise ValueError("invalid project write lease state")
    host = _host_scope()
    connection = _connect()
    now = time.time()
    try:
        cursor = connection.execute(
            """UPDATE local_project_write_leases SET state=%s,heartbeat_at=%s,updated_at=%s
               WHERE host_id=%s AND owner_uid=%s AND project_id=%s AND task_id=%s
                 AND owner_instance_id=%s AND (state!='released' OR %s='released')""",
            (
                state,
                now,
                now,
                host.host_id,
                host.owner_uid,
                project_id,
                task_id,
                owner_instance_id,
                state,
            ),
        )
        changed_rows: object = cursor.rowcount
        if isinstance(changed_rows, bool) or not isinstance(changed_rows, int):
            return False
        return changed_rows == 1
    finally:
        connection.close()


def project_write_lease(project_id: str) -> dict[str, object] | None:
    host = _host_scope()
    connection = _connect()
    try:
        row = connection.execute(
            """SELECT project_id,task_id,owner_instance_id,state,heartbeat_at,updated_at
               FROM local_project_write_leases
               WHERE host_id=%s AND owner_uid=%s AND project_id=%s""",
            (host.host_id, host.owner_uid, project_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def list_project_write_leases(*, owner_instance_id: str) -> list[dict[str, object]]:
    """List current host/user leases owned by one profile for startup recovery."""
    if not owner_instance_id:
        return []
    host = _host_scope()
    connection = _connect()
    try:
        rows = connection.execute(
            """SELECT project_id,task_id,owner_instance_id,state,heartbeat_at,updated_at
               FROM local_project_write_leases
               WHERE host_id=%s AND owner_uid=%s AND owner_instance_id=%s""",
            (host.host_id, host.owner_uid, owner_instance_id),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
