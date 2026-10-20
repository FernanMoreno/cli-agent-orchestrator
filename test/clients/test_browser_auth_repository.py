"""Browser-auth storage uses its isolated native PostgreSQL target."""

import pytest

pytestmark = pytest.mark.postgres


@pytest.fixture
def auth_repository(postgres_targets):
    from cli_agent_orchestrator.clients.browser_auth_repository import (
        BROWSER_AUTH_MIGRATIONS,
        BrowserAuthRepository,
    )
    from cli_agent_orchestrator.clients.postgres.migrations import migrate_component

    target = postgres_targets["auth"]
    migrate_component(target, BROWSER_AUTH_MIGRATIONS)
    repository = BrowserAuthRepository(target, "installation")
    repository.initialize_store()
    return repository


def test_browser_auth_requires_explicit_migrated_postgresql(postgres_targets):
    from cli_agent_orchestrator.clients.browser_auth_repository import BrowserAuthRepository
    from cli_agent_orchestrator.models.browser_auth import BrowserAuthError

    with pytest.raises(BrowserAuthError) as caught:
        BrowserAuthRepository(postgres_targets["auth"], "installation")

    assert caught.value.code == "auth_unavailable"


def test_repository_is_native_transactional_and_bound_to_installation(auth_repository):
    from cli_agent_orchestrator.clients.browser_auth_repository import BrowserAuthRepository
    from cli_agent_orchestrator.models.browser_auth import BrowserAuthError

    repository = auth_repository
    with pytest.raises(RuntimeError, match="rollback"):
        with repository.transaction() as db:
            db.execute("UPDATE auth_metadata SET auth_epoch=9 WHERE singleton_id=1")
            raise RuntimeError("rollback")

    with repository.transaction() as db:
        assert db.execute("SELECT schema_version FROM auth_metadata").fetchone()[0] == 1
        assert db.execute("SELECT auth_epoch FROM auth_metadata").fetchone()[0] == 1

    with pytest.raises(BrowserAuthError) as caught:
        BrowserAuthRepository(repository.target, "another-installation")
    assert caught.value.code == "auth_unavailable"


def test_repository_closes_native_connection_after_success_and_failure(
    auth_repository, monkeypatch
):
    opened = []
    connect = auth_repository._connect

    def capture():
        connection = connect()
        opened.append(connection)
        return connection

    monkeypatch.setattr(auth_repository, "_connect", capture)
    with auth_repository.transaction():
        pass
    assert opened[-1].raw.closed

    with pytest.raises(RuntimeError, match="transaction failed"):
        with auth_repository.transaction():
            raise RuntimeError("transaction failed")
    assert opened[-1].raw.closed


def test_auth_metadata_initialization_is_idempotent_and_never_resets_state(auth_repository):
    with auth_repository.transaction() as db:
        db.execute("UPDATE auth_metadata SET auth_epoch=2 WHERE singleton_id=1")

    auth_repository.initialize_store()

    with auth_repository.transaction() as db:
        assert db.execute("SELECT auth_epoch FROM auth_metadata").fetchone()[0] == 2


def test_auth_epochs_and_rate_limit_counters_keep_64_bit_values(auth_repository):
    large = 1 << 40
    with auth_repository.transaction() as db:
        db.execute(
            "UPDATE auth_metadata SET auth_epoch=%s WHERE singleton_id=1",
            (large,),
        )
        db.execute(
            """INSERT INTO browser_login_limits(
                   bucket_digest,bucket_kind,window_started_at,failures,blocked_until,
                   updated_at,pending_count
               ) VALUES (%s,%s,0,%s,0,0,%s)""",
            ("large-counter-test", "peer", large, large),
        )
        assert (
            db.execute("SELECT auth_epoch FROM auth_metadata WHERE singleton_id=1").fetchone()[0]
            == large
        )
        assert db.execute(
            "SELECT failures,pending_count FROM browser_login_limits WHERE bucket_digest=%s",
            ("large-counter-test",),
        ).fetchone() == (large, large)


def test_catalog_tampering_is_rejected_without_repair(auth_repository):
    from cli_agent_orchestrator.clients.browser_auth_repository import BrowserAuthRepository
    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.models.browser_auth import BrowserAuthError

    with connect(auth_repository.target.migration_target()) as db:
        db.execute("DROP INDEX browser_sessions_account")

    with pytest.raises(BrowserAuthError) as caught:
        BrowserAuthRepository(auth_repository.target, "installation")
    assert caught.value.code == "auth_unavailable"

    with connect(auth_repository.target.migration_target()) as db:
        row = db.execute(
            "SELECT 1 FROM pg_indexes WHERE schemaname=%s AND indexname=%s",
            (auth_repository.target.schema, "browser_sessions_account"),
        ).fetchone()
    assert row is None
