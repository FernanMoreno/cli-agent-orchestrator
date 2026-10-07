"""Shared per-user registry for live CAO processes and write leases."""

from __future__ import annotations

import ipaddress
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import psutil

from cli_agent_orchestrator.constants import LOCAL_PEER_DIR, LOCAL_PEER_REGISTRY_FILE
from cli_agent_orchestrator.services.local_peer_identity import (
    LocalProcessIdentity,
    current_process_identity,
)


class ProfileAlreadyRunningError(RuntimeError):
    """A different live server already owns this CAO profile identity."""


class ProjectWriteLeaseBusy(RuntimeError):
    """Another peer task currently owns the project's write lease."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


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


def _ensure_registry_parent() -> None:
    LOCAL_PEER_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(LOCAL_PEER_DIR, 0o700)
    except OSError:
        pass


def _connect() -> sqlite3.Connection:
    _ensure_registry_parent()
    connection = sqlite3.connect(str(LOCAL_PEER_REGISTRY_FILE), timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        os.chmod(LOCAL_PEER_REGISTRY_FILE, 0o600)
    except OSError:
        pass
    return connection


def _initialize(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS local_peer_processes (
            instance_id TEXT PRIMARY KEY,
            process_generation TEXT NOT NULL UNIQUE,
            pid INTEGER NOT NULL,
            process_started_at REAL NOT NULL,
            display_name TEXT NOT NULL,
            loopback_host TEXT NOT NULL CHECK(loopback_host IN ('127.0.0.1', '::1')),
            loopback_port INTEGER NOT NULL CHECK(loopback_port BETWEEN 1 AND 65535),
            started_at REAL NOT NULL,
            last_seen REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS local_project_write_leases (
            project_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            owner_instance_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('held', 'interrupted', 'reconcile', 'released')),
            heartbeat_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        """)
    try:
        os.chmod(LOCAL_PEER_REGISTRY_FILE, 0o600)
    except OSError:
        pass


def _process_is_same_live_instance(pid: int, expected_started_at: float) -> bool:
    try:
        actual_started_at = float(psutil.Process(pid).create_time())
    except (psutil.Error, OSError, ValueError):
        return False
    return abs(actual_started_at - expected_started_at) < 1.0


def _identity_endpoint_confirms(row: sqlite3.Row | dict[str, object]) -> bool:
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
        # This probe runs before the registry write transaction begins. The
        # identity endpoint can therefore repair a missing row without waiting
        # on a SQLite lock held by the process checking it.
        opener = build_opener(ProxyHandler({}), _RejectRedirects())
        with opener.open(request, timeout=2.0) as response:
            payload = response.read(16_385)
        if len(payload) > 16_384:
            return False
        identity = json.loads(payload)
    except (OSError, ValueError, TypeError):
        return False

    if not isinstance(identity, dict) or type(identity.get("pid")) is not int:
        return False
    return (
        identity.get("instance_id") == row["instance_id"]
        and identity.get("process_generation") == row["process_generation"]
        and identity["pid"] == row["pid"]
    )


def _stale_process_rows(connection: sqlite3.Connection) -> list[dict[str, object]]:
    """Find rows disproved by both the PID start fence and the live identity API."""
    rows = connection.execute("SELECT * FROM local_peer_processes").fetchall()
    stale = []
    for row in rows:
        if _process_is_same_live_instance(row["pid"], row["process_started_at"]):
            continue
        if not _identity_endpoint_confirms(row):
            stale.append(dict(row))
    return stale


def _prune_stale_processes(
    connection: sqlite3.Connection, stale_rows: list[dict[str, object]]
) -> None:
    """Delete only the exact stale snapshot checked before the write transaction."""
    connection.executemany(
        """DELETE FROM local_peer_processes
           WHERE instance_id = ? AND process_generation = ? AND pid = ?
             AND process_started_at = ? AND last_seen = ?""",
        (
            (
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
    now = time.time()
    connection = _connect()
    try:
        _initialize(connection)
        stale_rows = _stale_process_rows(connection) if prune_stale else []
        connection.execute("BEGIN IMMEDIATE")
        _prune_stale_processes(connection, stale_rows)
        existing = connection.execute(
            "SELECT * FROM local_peer_processes WHERE instance_id = ?",
            (identity.instance_id,),
        ).fetchone()
        if existing is not None:
            if existing["process_generation"] != identity.process_generation:
                raise ProfileAlreadyRunningError(
                    "This CAO profile already has a live server; use a different CAO_HOME_DIR."
                )
            connection.execute(
                """UPDATE local_peer_processes
                   SET pid = ?, process_started_at = ?, display_name = ?,
                       loopback_host = ?, loopback_port = ?, last_seen = ?
                   WHERE instance_id = ? AND process_generation = ?""",
                (
                    identity.pid,
                    identity.process_started_at,
                    identity.display_name,
                    identity.loopback_host,
                    identity.loopback_port,
                    now,
                    identity.instance_id,
                    identity.process_generation,
                ),
            )
        else:
            connection.execute(
                """INSERT INTO local_peer_processes
                   (instance_id, process_generation, pid, process_started_at, display_name,
                    loopback_host, loopback_port, started_at, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
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
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return LocalPeerRecord(
        instance_id=identity.instance_id,
        process_generation=identity.process_generation,
        pid=identity.pid,
        process_started_at=identity.process_started_at,
        display_name=identity.display_name,
        loopback_host=identity.loopback_host,
        loopback_port=identity.loopback_port,
        started_at=now,
        last_seen=now,
    )


def unregister_instance(identity: LocalProcessIdentity | None = None) -> None:
    """Remove only the registry row owned by this exact process generation."""
    identity = identity or current_process_identity()
    if not LOCAL_PEER_REGISTRY_FILE.exists():
        return
    connection = _connect()
    try:
        _initialize(connection)
        connection.execute(
            "DELETE FROM local_peer_processes WHERE instance_id = ? AND process_generation = ?",
            (identity.instance_id, identity.process_generation),
        )
        connection.commit()
    finally:
        connection.close()


def list_instances() -> list[LocalPeerRecord]:
    """Return candidates whose PID and process start fence still match."""
    if not LOCAL_PEER_REGISTRY_FILE.exists():
        return []
    connection = _connect()
    try:
        _initialize(connection)
        stale_rows = _stale_process_rows(connection)
        connection.execute("BEGIN IMMEDIATE")
        _prune_stale_processes(connection, stale_rows)
        connection.commit()
        rows = connection.execute(
            "SELECT * FROM local_peer_processes ORDER BY display_name, instance_id"
        ).fetchall()
        return [LocalPeerRecord(**dict(row)) for row in rows]
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_instance(instance_id: str) -> LocalPeerRecord | None:
    return next((item for item in list_instances() if item.instance_id == instance_id), None)


def acquire_project_write_lease(*, project_id: str, task_id: str, owner_instance_id: str) -> None:
    """Acquire a single-user-wide lease atomically across independent profiles."""
    if not project_id or not task_id or not owner_instance_id:
        raise ValueError("project, task and owner identities are required")
    connection = _connect()
    now = time.time()
    try:
        _initialize(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT task_id, owner_instance_id, state FROM local_project_write_leases "
            "WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if row is not None and row["state"] != "released":
            if row["task_id"] == task_id and row["owner_instance_id"] == owner_instance_id:
                connection.execute(
                    "UPDATE local_project_write_leases SET heartbeat_at = ?, updated_at = ? "
                    "WHERE project_id = ?",
                    (now, now, project_id),
                )
                connection.commit()
                return
            raise ProjectWriteLeaseBusy("another local CAO task holds this project's write lease")
        connection.execute(
            """INSERT INTO local_project_write_leases
               (project_id, task_id, owner_instance_id, state, heartbeat_at, updated_at)
               VALUES (?, ?, ?, 'held', ?, ?)
               ON CONFLICT(project_id) DO UPDATE SET
                   task_id = excluded.task_id,
                   owner_instance_id = excluded.owner_instance_id,
                   state = 'held',
                   heartbeat_at = excluded.heartbeat_at,
                   updated_at = excluded.updated_at""",
            (project_id, task_id, owner_instance_id, now, now),
        )
        connection.commit()
    except BaseException:
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
    if not LOCAL_PEER_REGISTRY_FILE.exists():
        return False
    connection = _connect()
    now = time.time()
    try:
        _initialize(connection)
        cursor = connection.execute(
            """UPDATE local_project_write_leases SET state = ?, heartbeat_at = ?, updated_at = ?
               WHERE project_id = ? AND task_id = ? AND owner_instance_id = ?
                 AND (state != 'released' OR ? = 'released')""",
            (state, now, now, project_id, task_id, owner_instance_id, state),
        )
        connection.commit()
        return cursor.rowcount == 1
    finally:
        connection.close()


def project_write_lease(project_id: str) -> dict[str, object] | None:
    if not LOCAL_PEER_REGISTRY_FILE.exists():
        return None
    connection = _connect()
    try:
        _initialize(connection)
        row = connection.execute(
            "SELECT * FROM local_project_write_leases WHERE project_id = ?", (project_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def list_project_write_leases(*, owner_instance_id: str) -> list[dict[str, object]]:
    """List write leases owned by one profile for startup recovery."""
    if not owner_instance_id or not LOCAL_PEER_REGISTRY_FILE.exists():
        return []
    connection = _connect()
    try:
        _initialize(connection)
        rows = connection.execute(
            "SELECT * FROM local_project_write_leases WHERE owner_instance_id = ?",
            (owner_instance_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
