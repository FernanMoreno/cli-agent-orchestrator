import os
import sqlite3

import pytest


def test_repository_private_versioned_and_atomic(tmp_path):
    from cli_agent_orchestrator.clients.browser_auth_repository import BrowserAuthRepository

    repo = BrowserAuthRepository(tmp_path / "private" / "auth.sqlite3", "installation")
    assert os.stat(repo.path).st_mode & 0o777 == 0o600
    assert os.stat(repo.path.parent).st_mode & 0o777 == 0o700
    with repo.transaction() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("SELECT schema_version FROM auth_metadata").fetchone()[0] == 1
    with pytest.raises(RuntimeError):
        with repo.transaction() as db:
            db.execute("UPDATE auth_metadata SET auth_epoch=9")
            raise RuntimeError("rollback")
    with repo.transaction() as db:
        assert db.execute("SELECT auth_epoch FROM auth_metadata").fetchone()[0] == 1


def test_repository_rejects_future_schema_and_wrong_installation(tmp_path):
    from cli_agent_orchestrator.clients.browser_auth_repository import BrowserAuthRepository
    from cli_agent_orchestrator.models.browser_auth import BrowserAuthError

    path = tmp_path / "auth.sqlite3"
    BrowserAuthRepository(path, "a")
    with pytest.raises(BrowserAuthError):
        BrowserAuthRepository(path, "b")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE auth_metadata SET schema_version=99")
    with pytest.raises(BrowserAuthError):
        BrowserAuthRepository(path, "a")


@pytest.mark.parametrize(
    "tamper",
    [
        "DROP TABLE browser_sessions",
        "ALTER TABLE browser_sessions DROP COLUMN access_expires_at",
        "DROP INDEX browser_sessions_account",
        "CREATE TRIGGER untrusted AFTER INSERT ON browser_accounts BEGIN UPDATE auth_metadata SET auth_epoch=1; END",
    ],
)
def test_existing_tampered_schema_is_rejected_without_repair(tmp_path, tamper):
    from cli_agent_orchestrator.clients.browser_auth_repository import BrowserAuthRepository
    from cli_agent_orchestrator.models.browser_auth import BrowserAuthError

    path = tmp_path / "auth.sqlite3"
    BrowserAuthRepository(path, "a")
    with sqlite3.connect(path) as db:
        db.executescript(tamper)
        before = db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name"
        ).fetchall()
    with pytest.raises(BrowserAuthError) as caught:
        BrowserAuthRepository(path, "a")
    assert caught.value.code == "auth_unavailable"
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name").fetchall()
            == before
        )


def test_existing_empty_database_is_not_bootstrapped(tmp_path):
    from cli_agent_orchestrator.clients.browser_auth_repository import BrowserAuthRepository
    from cli_agent_orchestrator.models.browser_auth import BrowserAuthError

    path = tmp_path / "auth.sqlite3"
    with sqlite3.connect(path):
        pass
    with pytest.raises(BrowserAuthError):
        BrowserAuthRepository(path, "a")


def test_existing_foreign_key_corruption_rejected(tmp_path):
    from cli_agent_orchestrator.clients.browser_auth_repository import BrowserAuthRepository
    from cli_agent_orchestrator.models.browser_auth import BrowserAuthError

    path = tmp_path / "auth.sqlite3"
    BrowserAuthRepository(path, "a")
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO browser_sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "session",
                "missing-account",
                "digest",
                1,
                1,
                0,
                60,
                60,
                1000,
                1000,
                1060,
                1060,
                None,
                None,
            ),
        )
    with pytest.raises(BrowserAuthError):
        BrowserAuthRepository(path, "a")
