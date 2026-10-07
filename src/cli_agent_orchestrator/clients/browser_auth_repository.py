"""Private SQLite authority for browser sessions; no dependency on Work state."""

import os
import sqlite3
from contextlib import closing, contextmanager
from functools import lru_cache
from pathlib import Path

from cli_agent_orchestrator.models.browser_auth import BrowserAuthError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_metadata (
 singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1), schema_version INTEGER NOT NULL,
 installation_id TEXT NOT NULL, auth_epoch INTEGER NOT NULL, time_high_water REAL NOT NULL);
CREATE TABLE IF NOT EXISTS browser_accounts (
 id TEXT PRIMARY KEY, username_normalized TEXT UNIQUE NOT NULL,
 password_scheme TEXT NOT NULL, password_salt TEXT NOT NULL, password_hash TEXT NOT NULL,
 password_params TEXT NOT NULL, account_version INTEGER NOT NULL, enabled INTEGER NOT NULL,
 principal_id TEXT NOT NULL, issuer TEXT NOT NULL, subject TEXT NOT NULL, principal_kind TEXT NOT NULL,
 scopes_json TEXT NOT NULL, created_at REAL NOT NULL, password_changed_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS browser_sessions (
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES browser_accounts(id),
 secret_digest TEXT UNIQUE NOT NULL, auth_epoch INTEGER NOT NULL, account_version INTEGER NOT NULL,
 remembered INTEGER NOT NULL, idle_seconds INTEGER NOT NULL, absolute_seconds INTEGER NOT NULL,
 created_at REAL NOT NULL, last_activity_at REAL NOT NULL, absolute_expires_at REAL NOT NULL,
 access_expires_at REAL NOT NULL, revoked_at REAL, revoke_reason TEXT);
CREATE INDEX IF NOT EXISTS browser_sessions_account ON browser_sessions(account_id);
CREATE TABLE IF NOT EXISTS browser_login_limits (
 bucket_digest TEXT PRIMARY KEY, bucket_kind TEXT NOT NULL, window_started_at REAL NOT NULL,
 failures INTEGER NOT NULL, blocked_until REAL NOT NULL, updated_at REAL NOT NULL, pending_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS browser_auth_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at REAL NOT NULL,
 event_kind TEXT NOT NULL, outcome_code TEXT NOT NULL, account_id TEXT, session_id TEXT,
 peer_category TEXT NOT NULL, auth_method TEXT NOT NULL);
"""


def _schema_snapshot(db):
    """Capture tables, constraints, indexes and triggers, excluding SQLite internals."""
    return tuple(
        (row[0], row[1], row[2], " ".join(row[3].split()) if row[3] else None)
        for row in db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
        )
    )


@lru_cache(maxsize=1)
def _supported_schema():
    with closing(sqlite3.connect(":memory:")) as reference, reference:
        reference.executescript(_SCHEMA)
        return _schema_snapshot(reference)


class BrowserAuthRepository:
    def __init__(self, path, installation_id: str = "local", busy_timeout_ms: int = 200):
        self.path = Path(path)
        self.installation_id = installation_id
        self.busy_timeout_ms = busy_timeout_ms
        try:
            existed = self.path.exists()
            if self.path.parent.is_symlink() or self.path.is_symlink():
                raise BrowserAuthError("auth_unavailable", 503)
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if (
                self.path.parent.stat().st_uid != os.geteuid()
                or self.path.parent.stat().st_mode & 0o077
            ):
                raise BrowserAuthError("auth_unavailable", 503)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                if os.fstat(fd).st_uid != os.geteuid():
                    raise BrowserAuthError("auth_unavailable", 503)
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
            with self.transaction() as db:
                if _schema_snapshot(db):
                    self._validate_schema(db)
                    self._metadata(db)
                elif existed:
                    # An existing empty or interrupted store has no supported
                    # authority state. Never silently bootstrap or repair it.
                    raise BrowserAuthError("auth_unavailable", 503)
                else:
                    # Create schema and initial metadata atomically under the
                    # same lock used by all subsequent authority operations.
                    for statement in _SCHEMA.split(";"):
                        if statement.strip():
                            db.execute(statement)
                    db.execute("INSERT INTO auth_metadata VALUES (1,1,?,1,0)", (installation_id,))
                    self._validate_schema(db)
                    self._metadata(db)
        except (OSError, sqlite3.Error) as exc:
            raise BrowserAuthError("auth_unavailable", 503) from exc

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            return db
        except BaseException:
            db.close()
            raise

    @staticmethod
    def _validate_schema(db):
        if _schema_snapshot(db) != _supported_schema():
            raise BrowserAuthError("auth_unavailable", 503)
        if [row[0] for row in db.execute("PRAGMA quick_check")] != ["ok"]:
            raise BrowserAuthError("auth_unavailable", 503)
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise BrowserAuthError("auth_unavailable", 503)

    def _metadata(self, db):
        row = db.execute("SELECT * FROM auth_metadata WHERE singleton_id=1").fetchone()
        if (
            row is None
            or row["schema_version"] != 1
            or row["installation_id"] != self.installation_id
        ):
            raise BrowserAuthError("auth_unavailable", 503)
        return row

    @contextmanager
    def transaction(self):
        db = None
        try:
            db = self._connect()
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except sqlite3.Error as exc:
            if db is not None:
                db.rollback()
            raise BrowserAuthError("auth_unavailable", 503) from exc
        except BrowserAuthError as exc:
            # Denied authentication rolls back all effects, but cannot erase an
            # observed forward clock jump and thereby resurrect old sessions.
            high_water = None
            if db is not None:
                if exc.status in (401, 429):
                    row = db.execute(
                        "SELECT time_high_water FROM auth_metadata WHERE singleton_id=1"
                    ).fetchone()
                    high_water = row[0] if row else None
                db.rollback()
                if high_water is not None:
                    try:
                        db.execute("BEGIN IMMEDIATE")
                        db.execute(
                            "UPDATE auth_metadata SET time_high_water=MAX(time_high_water,?)",
                            (high_water,),
                        )
                        db.commit()
                    except sqlite3.Error as storage_error:
                        db.rollback()
                        raise BrowserAuthError("auth_unavailable", 503) from storage_error
            raise
        except BaseException:
            if db is not None:
                db.rollback()
            raise
        finally:
            if db is not None:
                db.close()
