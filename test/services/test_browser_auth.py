from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Barrier
import pytest

BINDING = dict(
    id="existing-operator",
    issuer="local-issuer",
    subject="existing-subject",
    kind="jwt",
    scopes=["sessions:read"],
)
PASSWORD = "a sufficiently long password"


@pytest.fixture
def service(tmp_path):
    from cli_agent_orchestrator.services.browser_auth import BrowserAuthService

    now = [1000.0]
    policy = dict(
        installation_id="test",
        temporal_idle_seconds=20,
        temporal_absolute_seconds=60,
        remembered_idle_seconds=100,
        remembered_absolute_seconds=200,
        access_seconds=10,
    )
    instance = BrowserAuthService(tmp_path / "auth.sqlite3", BINDING, policy, clock=lambda: now[0])
    instance.create_account("Owner", PASSWORD)
    return instance, now, policy


def error(code, callback):
    from cli_agent_orchestrator.models.browser_auth import BrowserAuthError

    with pytest.raises(BrowserAuthError) as caught:
        callback()
    assert caught.value.code == code
    return caught.value


def test_login_preserves_identity_and_persists_without_secrets(service):
    from cli_agent_orchestrator.services.browser_auth import BrowserAuthService

    auth, now, policy = service
    secret, dto = auth.login("OWNER", PASSWORD, True, "127.0.0.1")
    assert dto["username"] == "owner"
    assert dto["remembered"] is True
    assert dto["absolute_expires_at"] == 1200
    assert auth.identity(secret) == BINDING
    restarted = BrowserAuthService(auth.repository.path, BINDING, policy, clock=lambda: now[0])
    assert restarted.session(secret)["session_id"] == dto["session_id"]
    with auth.repository.transaction() as db:
        rows = repr([tuple(r) for r in db.execute("SELECT * FROM browser_sessions")])
        events = repr([tuple(r) for r in db.execute("SELECT * FROM browser_auth_events")])
    assert secret not in rows + events
    assert PASSWORD not in rows + events
    error("account_exists", lambda: auth.create_account("different", PASSWORD))


def test_lease_renew_does_not_extend_idle_and_touch_is_explicit(service):
    auth, now, _ = service
    secret, _ = auth.login("owner", PASSWORD)
    now[0] = 1011
    error("access_renewal_required", lambda: auth.identity(secret))
    assert auth.session(secret)["idle_expires_at"] == 1020
    assert auth.renew(secret)["access_expires_at"] == 1020
    now[0] = 1015
    auth.identity(secret, touch=True)
    assert auth.session(secret)["idle_expires_at"] == 1035
    now[0] = 1035
    error("session_expired", lambda: auth.renew(secret))


def test_logout_is_independent_and_logout_all_wins_over_renew(service):
    auth, _, _ = service
    first, _ = auth.login("owner", PASSWORD)
    second, _ = auth.login("owner", PASSWORD)
    auth.logout(first)
    auth.logout(first)
    error("session_revoked", lambda: auth.renew(first))
    auth.identity(second)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: auth.renew(second), range(8)))
    auth.logout_all(second)
    error("session_revoked", lambda: auth.identity(second))


def test_password_change_reset_disable_invalidate(service):
    auth, _, _ = service
    secret, _ = auth.login("owner", PASSWORD)
    new = "the replacement long password"
    error("credentials_rejected", lambda: auth.change_password(secret, "wrong", new))
    auth.change_password(secret, PASSWORD, new)
    error("session_revoked", lambda: auth.session(secret))
    error("credentials_rejected", lambda: auth.login("owner", PASSWORD))
    second, _ = auth.login("owner", new)
    auth.reset_password(PASSWORD)
    error("session_revoked", lambda: auth.session(second))
    third, _ = auth.login("owner", PASSWORD)
    auth.invalidate_all()
    error("session_revoked", lambda: auth.session(third))
    fourth, _ = auth.login("owner", PASSWORD)
    auth.disable()
    error("session_revoked", lambda: auth.session(fourth))
    error("credentials_rejected", lambda: auth.login("owner", PASSWORD))


def test_rate_limit_by_account_and_peer_with_explicit_recovery(service):
    auth, now, _ = service
    for i in range(10):
        error("credentials_rejected", lambda: auth.login("absent", PASSWORD, peer="same-peer"))
    exc = error("login_throttled", lambda: auth.login("owner", PASSWORD, peer="same-peer"))
    assert exc.status == 429 and exc.retry_after == 60
    auth.reset_password(PASSWORD)
    auth.login("owner", PASSWORD, peer="same-peer")
    now[0] = 1400
    auth.login("owner", PASSWORD, peer="same-peer")


def test_clock_rollback_and_policy_tightening_fail_closed(service):
    from cli_agent_orchestrator.services.browser_auth import BrowserAuthService

    auth, now, policy = service
    secret, _ = auth.login("owner", PASSWORD, True)
    now[0] = 1010
    auth.renew(secret)
    now[0] = 1009
    error("clock_untrusted", lambda: auth.session(secret))
    now[0] = 1010
    tightened = BrowserAuthService(
        auth.repository.path,
        BINDING,
        dict(policy, remembered_absolute_seconds=10, remembered_idle_seconds=10),
        clock=lambda: now[0],
    )
    error("session_expired", lambda: tightened.session(secret))


def test_binding_change_and_database_lock_fail_closed(service):
    from cli_agent_orchestrator.services.browser_auth import BrowserAuthService

    auth, now, policy = service
    secret, _ = auth.login("owner", PASSWORD)
    changed = BrowserAuthService(
        auth.repository.path, dict(BINDING, subject="other"), policy, clock=lambda: now[0]
    )
    error("auth_unavailable", lambda: changed.session(secret))
    with sqlite3.connect(auth.repository.path) as db:
        db.execute("BEGIN IMMEDIATE")
        error("auth_unavailable", lambda: auth.identity(secret))


@pytest.mark.parametrize(
    "policy",
    [dict(access_seconds=0), dict(temporal_idle_seconds=999999), dict(access_seconds=999999)],
)
def test_invalid_policy_rejected(tmp_path, policy):
    from cli_agent_orchestrator.services.browser_auth import BrowserAuthService

    with pytest.raises(ValueError):
        BrowserAuthService(tmp_path / "auth.sqlite3", BINDING, policy)


def test_local_pending_account_verification_creates_no_session(service):
    auth, _, _ = service
    assert auth.account_matches("OWNER", PASSWORD)
    assert not auth.account_matches("other", PASSWORD)
    assert not auth.account_matches("owner", "wrong")
    with auth.repository.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM browser_sessions").fetchone()[0] == 0


def test_concurrent_failures_cannot_bypass_threshold(service, monkeypatch):
    from cli_agent_orchestrator.services import browser_auth

    auth, _, _ = service
    # Exercise concurrent limiter transactions independently of scrypt's bounded
    # worker queue, whose safe overload response is auth_unavailable.
    monkeypatch.setattr(browser_auth, "verify_password", lambda *_: False)
    # Pin storage availability so this test measures the atomic quota rather
    # than the repository's separately tested fail-fast overload policy.
    auth.repository.busy_timeout_ms = 5000
    start = Barrier(12)

    def attempt(_):
        start.wait(timeout=10)
        try:
            auth.login("owner", "wrong", peer="attacker")
        except Exception as exc:
            return exc.code
        return "unexpected success"

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(attempt, range(12)))
    assert outcomes.count("credentials_rejected") == 10
    assert outcomes.count("login_throttled") == 2
    with auth.repository.transaction() as db:
        rows = db.execute(
            "SELECT failures,pending_count,blocked_until FROM browser_login_limits"
        ).fetchall()
        assert rows
        assert all(
            row["failures"] == 10 and row["pending_count"] == 0 and row["blocked_until"] == 1060
            for row in rows
        )
    error("login_throttled", lambda: auth.login("owner", PASSWORD, peer="different"))


def test_block_expires_without_waiting_entire_window(service):
    auth, now, _ = service
    for _ in range(10):
        error("credentials_rejected", lambda: auth.login("owner", "wrong"))
    now[0] += 60
    auth.login("owner", PASSWORD)


def test_forward_jump_cannot_resurrect_session_after_clock_returns(service):
    auth, now, _ = service
    secret, _ = auth.login("owner", PASSWORD)
    now[0] = 2000
    error("session_expired", lambda: auth.session(secret))
    now[0] = 1001
    error("clock_untrusted", lambda: auth.session(secret))


def test_oversized_password_never_matches_fallback_verification_text(service):
    auth, _, _ = service
    auth.reset_password("invalid password input")
    error("credentials_rejected", lambda: auth.login("owner", "x" * 129))
    secret, _ = auth.login("owner", "invalid password input")
    error("credentials_rejected", lambda: auth.change_password(secret, "x" * 129, PASSWORD))


def test_login_rechecks_limit_after_password_verification(service, monkeypatch):
    from cli_agent_orchestrator.services import browser_auth

    auth, _, _ = service
    real_verify = browser_auth.verify_password

    def verify_then_block(password, record):
        valid = real_verify(password, record)
        with auth.repository.transaction() as db:
            db.execute("UPDATE browser_login_limits SET blocked_until=1060,failures=10")
        return valid

    monkeypatch.setattr(browser_auth, "verify_password", verify_then_block)
    exc = error("login_throttled", lambda: auth.login("owner", PASSWORD))
    assert exc.retry_after == 60
    with auth.repository.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM browser_sessions").fetchone()[0] == 0


@pytest.mark.parametrize("operation", ["login", "change_password"])
def test_verifier_failure_releases_capacity_without_password_failure(
    service, monkeypatch, operation
):
    from cli_agent_orchestrator.services import browser_auth
    from cli_agent_orchestrator.models.browser_auth import BrowserAuthError

    auth, _, _ = service
    secret, _ = auth.login("owner", PASSWORD)

    def fail_verification(*_):
        raise BrowserAuthError("auth_unavailable", 503)

    monkeypatch.setattr(browser_auth, "verify_password", fail_verification)
    callback = lambda: (
        auth.login("owner", PASSWORD)
        if operation == "login"
        else auth.change_password(secret, PASSWORD, PASSWORD)
    )
    error("auth_unavailable", callback)
    with auth.repository.transaction() as db:
        assert all(
            row["pending_count"] == 0 and row["failures"] == 0
            for row in db.execute("SELECT * FROM browser_login_limits")
        )


def test_password_hash_failure_releases_capacity(service, monkeypatch):
    from cli_agent_orchestrator.services import browser_auth
    from cli_agent_orchestrator.models.browser_auth import BrowserAuthError

    auth, _, _ = service
    secret, _ = auth.login("owner", PASSWORD)

    def fail_hash(*_):
        raise BrowserAuthError("auth_unavailable", 503)

    monkeypatch.setattr(browser_auth, "hash_password", fail_hash)
    error("auth_unavailable", lambda: auth.change_password(secret, PASSWORD, PASSWORD))
    with auth.repository.transaction() as db:
        assert all(
            row["pending_count"] == 0 and row["failures"] == 0
            for row in db.execute("SELECT * FROM browser_login_limits")
        )
    auth.identity(secret)


@pytest.mark.parametrize("new_password", ["short", "123456789", "x" * 129, None])
def test_invalid_new_password_rejected_before_reservation(service, new_password):
    auth, _, _ = service
    secret, _ = auth.login("owner", PASSWORD)
    exc = error(
        "invalid_auth_request", lambda: auth.change_password(secret, PASSWORD, new_password)
    )
    assert exc.status == 422
    with auth.repository.transaction() as db:
        assert all(
            row["pending_count"] == 0 and row["failures"] == 0
            for row in db.execute("SELECT * FROM browser_login_limits")
        )


def test_failed_old_verifier_does_not_release_new_window_capacity(service, monkeypatch):
    from cli_agent_orchestrator.services import browser_auth
    from cli_agent_orchestrator.models.browser_auth import BrowserAuthError

    auth, now, _ = service

    def replace_window_then_fail(*_):
        now[0] = 1301
        with auth.repository.transaction() as db:
            db.execute(
                "UPDATE browser_login_limits SET window_started_at=1301,failures=4,pending_count=1"
            )
        raise BrowserAuthError("auth_unavailable", 503)

    monkeypatch.setattr(browser_auth, "verify_password", replace_window_then_fail)
    error("auth_unavailable", lambda: auth.login("owner", PASSWORD))
    with auth.repository.transaction() as db:
        assert all(
            row["pending_count"] == 1 and row["failures"] == 4
            for row in db.execute("SELECT * FROM browser_login_limits")
        )


def test_ten_character_password_supports_creation_login_change_and_recovery(tmp_path):
    from cli_agent_orchestrator.services.browser_auth import BrowserAuthService

    auth = BrowserAuthService(tmp_path / "auth.sqlite3", BINDING)
    auth.create_account("owner", "1234567890")
    secret, _ = auth.login("owner", "1234567890")
    auth.change_password(secret, "1234567890", "abcdefghij")
    error("session_revoked", lambda: auth.session(secret))
    renewed, _ = auth.login("owner", "abcdefghij")
    auth.reset_password("ABCDEFGHIJ")
    error("session_revoked", lambda: auth.session(renewed))
    assert auth.login("owner", "ABCDEFGHIJ")[1]["username"] == "owner"
