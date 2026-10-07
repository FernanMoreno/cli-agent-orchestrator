"""Real SQLite lifetime proof for schema references and pre-handoff failures."""

import sqlite3
from contextlib import closing

import pytest

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients import browser_auth_repository as browser
from cli_agent_orchestrator.clients import database, work_repository
from cli_agent_orchestrator.models.browser_auth import BrowserAuthError


@pytest.fixture
def captured_connections(monkeypatch):
    connect = sqlite3.connect
    connections = []

    def capture(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", capture)
    yield connections
    for connection in connections:
        connection.close()


def assert_closed(connections):
    assert connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")


@pytest.mark.parametrize("fail_snapshot", [False, True])
def test_browser_schema_reference_closes_on_return_and_error(
    captured_connections, monkeypatch, fail_snapshot
):
    browser._supported_schema.cache_clear()
    try:
        if fail_snapshot:

            def fail(db):
                db.execute("SELECT * FROM missing_table")

            monkeypatch.setattr(browser, "_schema_snapshot", fail)
            with pytest.raises(sqlite3.OperationalError, match="missing_table"):
                browser._supported_schema()
        else:
            snapshot = browser._supported_schema()
            assert any(row[1] == "browser_accounts" for row in snapshot)
        assert_closed(captured_connections)
    finally:
        browser._supported_schema.cache_clear()


@pytest.mark.parametrize(
    "operation", ["browser_connect", "browser_transaction", "work", "terminals"]
)
def test_corrupt_database_setup_closes_owned_connection(
    tmp_path, monkeypatch, captured_connections, operation
):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"corrupt SQLite file" * 100)
    if operation.startswith("browser"):
        repository = browser.BrowserAuthRepository.__new__(browser.BrowserAuthRepository)
        repository.path = path
        repository.busy_timeout_ms = 200
        if operation == "browser_connect":
            with pytest.raises(sqlite3.DatabaseError, match="not a database"):
                repository._connect()
        else:
            with pytest.raises(BrowserAuthError) as caught:
                with repository.transaction():
                    pytest.fail("corrupt store must fail before handoff")
            assert caught.value.code == "auth_unavailable"
            assert caught.value.status == 503
            assert isinstance(caught.value.__cause__, sqlite3.DatabaseError)
    elif operation == "work":
        with pytest.raises(sqlite3.DatabaseError, match="not a database"):
            with work_repository.WorkRepository(path).connection():
                pytest.fail("corrupt store must fail before handoff")
    else:
        monkeypatch.setattr(constants, "DATABASE_FILE", path)
        assert database._migrate_terminals_schema() is None
    assert_closed(captured_connections)


@pytest.mark.parametrize("owner", ["browser", "work"])
@pytest.mark.parametrize("fail_body", [False, True])
def test_successful_handoff_keeps_transaction_commit_and_rollback(
    tmp_path, captured_connections, owner, fail_body
):
    path = tmp_path / "private" / "store.db"
    path.parent.mkdir(mode=0o700)
    if owner == "browser":
        repository = browser.BrowserAuthRepository(path)
        statement = "UPDATE auth_metadata SET auth_epoch=9"
        query = "SELECT auth_epoch FROM auth_metadata"
        expected = 1 if fail_body else 9
    else:
        repository = work_repository.WorkRepository(path)
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE probe(value INTEGER)")
        statement = "INSERT INTO probe VALUES (9)"
        query = "SELECT count(*) FROM probe"
        expected = 0 if fail_body else 1
    # The observer and bootstrap are separately owned by this test.
    captured_connections.clear()

    def write():
        with repository.transaction() as db:
            assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            db.execute(statement)
            if fail_body:
                raise RuntimeError("body failed")

    if fail_body:
        with pytest.raises(RuntimeError, match="body failed"):
            write()
    else:
        write()
    assert_closed(captured_connections)
    with closing(sqlite3.connect(path)) as observer:
        assert observer.execute(query).fetchone()[0] == expected


@pytest.mark.parametrize("fail_later_column", [False, True])
def test_terminal_migration_closes_and_preserves_per_column_commits(
    tmp_path, monkeypatch, captured_connections, fail_later_column
):
    path = tmp_path / "terminals.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE terminals(id TEXT)")
    captured_connections.clear()
    monkeypatch.setattr(constants, "DATABASE_FILE", path)
    if fail_later_column:
        connect = sqlite3.connect

        def reject_shell_column(*args, **kwargs):
            connection = connect(*args, **kwargs)
            # SQLite itself rejects the second ALTER, after the first commit.
            alters = 0

            def authorize(action, _arg1, _arg2, _database, _source):
                nonlocal alters
                if action == sqlite3.SQLITE_ALTER_TABLE:
                    alters += 1
                    if alters == 2:
                        return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            connection.set_authorizer(authorize)
            return connection

        monkeypatch.setattr(sqlite3, "connect", reject_shell_column)
    assert database._migrate_terminals_schema() is None
    assert_closed(captured_connections)
    with closing(sqlite3.connect(path)) as observer:
        columns = {row[1] for row in observer.execute("PRAGMA table_info(terminals)")}
    assert "allowed_tools" in columns
    assert ("shell_command" in columns) is not fail_later_column
    if not fail_later_column:
        assert "session_incarnation_id" in columns
