"""Persistent browser authentication independent of JWT and durable agent work."""

import hashlib
import json
import math
import re
import secrets
import time
from contextlib import contextmanager
from dataclasses import fields

from cli_agent_orchestrator.clients.browser_auth_repository import BrowserAuthRepository
from cli_agent_orchestrator.models.browser_auth import BrowserAuthError, BrowserSessionPolicy
from cli_agent_orchestrator.security.browser_passwords import (
    dummy_password_hash,
    hash_password,
    verify_password,
)


class BrowserAuthService:
    def __init__(self, path, binding: dict, policy: dict | None = None, clock=time.time):
        config = dict(policy or {})
        limits = dict(config.get("limits", {}))
        limits.update(
            {f.name: config[f.name] for f in fields(BrowserSessionPolicy) if f.name in config}
        )
        self.policy = BrowserSessionPolicy(**limits)
        self.binding = dict(binding)
        self.binding["scopes"] = list(binding.get("scopes", []))
        if (
            self.binding.get("kind") != "jwt"
            or any(
                not isinstance(self.binding.get(k), str) or not self.binding[k]
                for k in ("id", "issuer", "subject")
            )
            or not isinstance(binding.get("scopes"), list)
            or any(not isinstance(scope, str) for scope in self.binding["scopes"])
        ):
            raise ValueError("Browser identity binding must be a verified JWT operator")
        self.clock = clock
        self.enabled = config.get("enabled", True)
        self.repository = BrowserAuthRepository(path, config.get("installation_id", "local"))
        # Verify scrypt capacity before enabling login, including unknown-account verification.
        self._dummy = dummy_password_hash()

    @staticmethod
    def _digest(value):
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _username(username):
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", username):
            raise ValueError(
                "Username must have 1-64 ASCII letters, digits, dots, underscores or hyphens"
            )
        return username.lower()

    def _now(self, db, local=False):
        now = float(self.clock())
        metadata = self.repository._metadata(db)
        if not math.isfinite(now) or now < 0:
            raise BrowserAuthError("clock_untrusted", 503)
        if now < metadata["time_high_water"] and not local:
            raise BrowserAuthError("clock_untrusted", 503)
        db.execute("UPDATE auth_metadata SET time_high_water=MAX(time_high_water,?)", (now,))
        return now, metadata

    @staticmethod
    def _account(db):
        return db.execute("SELECT * FROM browser_accounts LIMIT 1").fetchone()

    @staticmethod
    def _record(account):
        return dict(
            scheme=account["password_scheme"],
            salt=account["password_salt"],
            hash=account["password_hash"],
            params=json.loads(account["password_params"]),
        )

    def _check_binding(self, account):
        expected = (
            self.binding["id"],
            self.binding["issuer"],
            self.binding["subject"],
            self.binding["kind"],
            sorted(self.binding["scopes"]),
        )
        actual = (
            account["principal_id"],
            account["issuer"],
            account["subject"],
            account["principal_kind"],
            sorted(json.loads(account["scopes_json"])),
        )
        if actual != expected:
            raise BrowserAuthError("auth_unavailable", 503)

    def validate_account(self):
        with self.repository.transaction() as db:
            self.repository._metadata(db)
            account = self._account(db)
            if not self.enabled or account is None or not account["enabled"]:
                raise BrowserAuthError("auth_unavailable", 503)
            self._check_binding(account)

    validate_binding = validate_account

    @staticmethod
    def _audit(db, now, kind, outcome="ok", account=None, session=None, peer="local"):
        db.execute(
            "INSERT INTO browser_auth_events (occurred_at,event_kind,outcome_code,account_id,session_id,peer_category,auth_method) VALUES (?,?,?,?,?,?,?)",
            (
                now,
                kind,
                outcome,
                account,
                session,
                "local" if peer == "local" else "socket",
                "local_password",
            ),
        )
        db.execute("DELETE FROM browser_auth_events WHERE occurred_at < ?", (now - 90 * 86400,))

    def create_account(self, username, password):
        normalized = self._username(username)
        record = hash_password(password)
        with self.repository.transaction() as db:
            now, _ = self._now(db, local=True)
            if self._account(db) is not None:
                raise BrowserAuthError("account_exists", 409)
            db.execute(
                "INSERT INTO browser_accounts VALUES (?,?,?,?,?,?,1,1,?,?,?,?,?,?,?)",
                (
                    secrets.token_hex(16),
                    normalized,
                    record["scheme"],
                    record["salt"],
                    record["hash"],
                    json.dumps(record["params"]),
                    self.binding["id"],
                    self.binding["issuer"],
                    self.binding["subject"],
                    self.binding["kind"],
                    json.dumps(self.binding["scopes"]),
                    now,
                    now,
                ),
            )
            self._audit(db, now, "account_create")

    def _buckets(self, username, peer):
        return [
            (self._digest("account:" + username), "account"),
            (self._digest("peer:" + str(peer)), "peer"),
        ]

    @staticmethod
    def _purge(db, now):
        db.execute("DELETE FROM browser_login_limits WHERE updated_at < ?", (now - 600,))
        db.execute(
            "DELETE FROM browser_sessions WHERE COALESCE(revoked_at,absolute_expires_at) < ?",
            (now - 7 * 86400,),
        )

    def _reserve(self, db, now, buckets):
        self._purge(db, now)
        for key, kind in buckets:
            row = db.execute(
                "SELECT * FROM browser_login_limits WHERE bucket_digest=?", (key,)
            ).fetchone()
            if row and row["blocked_until"] > now:
                raise BrowserAuthError(
                    "login_throttled", 429, max(1, math.ceil(row["blocked_until"] - now))
                )
            expired = row and (
                now - row["window_started_at"] >= 300
                or (row["blocked_until"] and row["blocked_until"] <= now)
            )
            if row and not expired and row["failures"] + row["pending_count"] >= 10:
                raise BrowserAuthError("login_throttled", 429, 1)
        if db.execute("SELECT COUNT(*) FROM browser_login_limits").fetchone()[0] >= 4096:
            raise BrowserAuthError("auth_unavailable", 503)
        for key, kind in buckets:
            row = db.execute(
                "SELECT * FROM browser_login_limits WHERE bucket_digest=?", (key,)
            ).fetchone()
            reset = (
                not row
                or now - row["window_started_at"] >= 300
                or (row["blocked_until"] and row["blocked_until"] <= now)
            )
            if reset:
                db.execute(
                    "INSERT OR REPLACE INTO browser_login_limits VALUES (?,?,?,0,0,?,1)",
                    (key, kind, now, now),
                )
            else:
                db.execute(
                    "UPDATE browser_login_limits SET pending_count=pending_count+1,updated_at=? WHERE bucket_digest=?",
                    (now, key),
                )

        return [
            (
                key,
                db.execute(
                    "SELECT window_started_at FROM browser_login_limits WHERE bucket_digest=?",
                    (key,),
                ).fetchone()[0],
            )
            for key, _ in buckets
        ]

    @contextmanager
    def _reserved_attempt(self, reservations):
        settled = [False]
        try:
            yield settled
        finally:
            if not settled[0]:
                # Infrastructure/validation failures do not count as rejected
                # passwords. An old verifier cannot consume a newer window's
                # reservations after the original window has expired/reset.
                with self.repository.transaction() as db:
                    for key, started_at in reservations:
                        db.execute(
                            "UPDATE browser_login_limits SET pending_count=MAX(0,pending_count-1) WHERE bucket_digest=? AND window_started_at=?",
                            (key, started_at),
                        )

    @staticmethod
    def _finish_attempt(db, now, reservations, valid):
        for key, started_at in reservations:
            db.execute(
                "UPDATE browser_login_limits SET pending_count=MAX(0,pending_count-1),failures=failures+?,updated_at=? WHERE bucket_digest=? AND window_started_at=?",
                (0 if valid else 1, now, key, started_at),
            )
            if not valid:
                db.execute(
                    "UPDATE browser_login_limits SET blocked_until=? WHERE bucket_digest=? AND window_started_at=? AND failures>=10 AND blocked_until<=?",
                    (now + 60, key, started_at, now),
                )

    def account_matches(self, username, password):
        """Local recovery check; never creates a session or exposes account material."""
        try:
            normalized = self._username(username)
        except ValueError:
            normalized = ""
        with self.repository.transaction() as db:
            account = self._account(db)
            if account is not None:
                self._check_binding(account)
            snapshot = dict(account) if account else None
        valid = verify_password(password, self._record(snapshot) if snapshot else self._dummy)
        with self.repository.transaction() as db:
            current = self._account(db)
            return bool(
                valid
                and snapshot
                and current
                and normalized == current["username_normalized"]
                and current["account_version"] == snapshot["account_version"]
            )

    def login(self, username, password, remember=False, peer="local"):
        try:
            normalized = self._username(username)
        except ValueError:
            normalized = str(username).lower()[:128]
        buckets = self._buckets(normalized, peer)
        with self.repository.transaction() as db:
            now, metadata = self._now(db)
            reservations = self._reserve(db, now, buckets)
            account = self._account(db)
            if account is not None:
                self._check_binding(account)
            snapshot = dict(account) if account is not None else None
            epoch = metadata["auth_epoch"]
        with self._reserved_attempt(reservations) as settled:
            candidate = (
                password
                if isinstance(password, str) and len(password) <= 128
                else "invalid password input"
            )
            valid = verify_password(
                candidate,
                (
                    self._record(snapshot)
                    if snapshot and snapshot["username_normalized"] == normalized
                    else self._dummy
                ),
            )
            valid = valid and isinstance(password, str) and 10 <= len(password) <= 128
            secret = secrets.token_urlsafe(32)
            rejected = False
            throttled = None
            with self.repository.transaction() as db:
                now, metadata = self._now(db)
                account = self._account(db)
                if account is not None:
                    self._check_binding(account)
                blocked_until = max(
                    (
                        row[0]
                        for key, _ in buckets
                        if (
                            row := db.execute(
                                "SELECT blocked_until FROM browser_login_limits WHERE bucket_digest=?",
                                (key,),
                            ).fetchone()
                        )
                    ),
                    default=0,
                )
                if valid and blocked_until > now:
                    self._finish_attempt(db, now, reservations, True)
                    self._audit(db, now, "login_failure", "login_throttled", peer=peer)
                    rejected = True
                    throttled = BrowserAuthError(
                        "login_throttled", 429, max(1, math.ceil(blocked_until - now))
                    )
                elif (
                    not valid
                    or not self.enabled
                    or snapshot is None
                    or account is None
                    or not account["enabled"]
                    or account["username_normalized"] != normalized
                    or account["account_version"] != snapshot["account_version"]
                    or metadata["auth_epoch"] != epoch
                ):
                    self._finish_attempt(db, now, reservations, False)
                    self._audit(db, now, "login_failure", "credentials_rejected", peer=peer)
                    rejected = True
                else:
                    self._finish_attempt(db, now, reservations, True)
                    idle = (
                        self.policy.remembered_idle_seconds
                        if remember
                        else self.policy.temporal_idle_seconds
                    )
                    absolute = (
                        self.policy.remembered_absolute_seconds
                        if remember
                        else self.policy.temporal_absolute_seconds
                    )
                    session_id = secrets.token_hex(16)
                    db.execute(
                        "INSERT INTO browser_sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)",
                        (
                            session_id,
                            account["id"],
                            self._digest(secret),
                            metadata["auth_epoch"],
                            account["account_version"],
                            int(bool(remember)),
                            idle,
                            absolute,
                            now,
                            now,
                            now + absolute,
                            now + min(self.policy.access_seconds, idle, absolute),
                        ),
                    )
                    self._audit(
                        db,
                        now,
                        "login_success",
                        account=account["id"],
                        session=session_id,
                        peer=peer,
                    )
                    session = db.execute(
                        "SELECT * FROM browser_sessions WHERE id=?", (session_id,)
                    ).fetchone()
                    result = self._dto(session, account, now)
            settled[0] = True
            if rejected:
                raise throttled or BrowserAuthError("credentials_rejected")
            return secret, result

    def _load(self, db, secret, require_access=False, touch=False):
        now, metadata = self._now(db)
        if not isinstance(secret, str) or not secret or len(secret) > 256:
            raise BrowserAuthError("session_required")
        session = db.execute(
            "SELECT * FROM browser_sessions WHERE secret_digest=?", (self._digest(secret),)
        ).fetchone()
        if session is None:
            raise BrowserAuthError("session_required")
        account = self._account(db)
        if account is None:
            raise BrowserAuthError("session_revoked")
        self._check_binding(account)
        if (
            not self.enabled
            or not account["enabled"]
            or session["revoked_at"] is not None
            or session["auth_epoch"] != metadata["auth_epoch"]
            or session["account_version"] != account["account_version"]
        ):
            raise BrowserAuthError("session_revoked")
        idle_deadline, absolute_deadline = self._deadlines(session)
        if now >= min(idle_deadline, absolute_deadline):
            raise BrowserAuthError("session_expired")
        if require_access and now >= session["access_expires_at"]:
            raise BrowserAuthError("access_renewal_required")
        if touch:
            db.execute(
                "UPDATE browser_sessions SET last_activity_at=? WHERE id=?", (now, session["id"])
            )
            session = db.execute(
                "SELECT * FROM browser_sessions WHERE id=?", (session["id"],)
            ).fetchone()
        return session, account, now

    def _deadlines(self, session):
        remembered = session["remembered"]
        idle = (
            self.policy.remembered_idle_seconds if remembered else self.policy.temporal_idle_seconds
        )
        absolute = (
            self.policy.remembered_absolute_seconds
            if remembered
            else self.policy.temporal_absolute_seconds
        )
        return (
            session["last_activity_at"] + min(idle, session["idle_seconds"]),
            min(session["absolute_expires_at"], session["created_at"] + absolute),
        )

    def _dto(self, session, account, now):
        idle, absolute = self._deadlines(session)
        return dict(
            session_id=session["id"],
            username=account["username_normalized"],
            remembered=bool(session["remembered"]),
            access_expires_at=min(session["access_expires_at"], idle, absolute),
            idle_expires_at=idle,
            absolute_expires_at=absolute,
            server_time=now,
            session_revision=f"{session['auth_epoch']}:{session['account_version']}",
        )

    def session(self, secret, require_access=False, touch=False):
        with self.repository.transaction() as db:
            session, account, now = self._load(db, secret, require_access, touch)
            return self._dto(session, account, now)

    def identity(self, secret, require_access=True, touch=False):
        self.session(secret, require_access, touch)
        return dict(self.binding, scopes=list(self.binding["scopes"]))

    def renew(self, secret):
        try:
            with self.repository.transaction() as db:
                session, account, now = self._load(db, secret)
                idle, absolute = self._deadlines(session)
                db.execute(
                    "UPDATE browser_sessions SET access_expires_at=? WHERE id=?",
                    (min(now + self.policy.access_seconds, idle, absolute), session["id"]),
                )
                updated = db.execute(
                    "SELECT * FROM browser_sessions WHERE id=?", (session["id"],)
                ).fetchone()
                return self._dto(updated, account, now)
        except BrowserAuthError as exc:
            if exc.status != 503:
                with self.repository.transaction() as db:
                    now, _ = self._now(db)
                    self._audit(db, now, "renew_failure", exc.code)
            raise

    def logout(self, secret):
        if not isinstance(secret, str) or not secret or len(secret) > 256:
            return
        with self.repository.transaction() as db:
            now, _ = self._now(db)
            session = db.execute(
                "SELECT * FROM browser_sessions WHERE secret_digest=?", (self._digest(secret),)
            ).fetchone()
            if session is not None and session["revoked_at"] is None:
                db.execute(
                    "UPDATE browser_sessions SET revoked_at=?,revoke_reason='logout' WHERE id=?",
                    (now, session["id"]),
                )
                self._audit(db, now, "logout", account=session["account_id"], session=session["id"])

    def _revoke(self, db, now, reason):
        db.execute("UPDATE auth_metadata SET auth_epoch=auth_epoch+1")
        db.execute(
            "UPDATE browser_sessions SET revoked_at=COALESCE(revoked_at,?),revoke_reason=COALESCE(revoke_reason,?)",
            (now, reason),
        )

    def logout_all(self, secret):
        with self.repository.transaction() as db:
            _, account, now = self._load(db, secret)
            self._revoke(db, now, "revoke_all")
            self._audit(db, now, "revoke_all", account=account["id"])

    def _replace_password(self, db, account, record, now, reason):
        self._check_binding(account)
        db.execute(
            "UPDATE browser_accounts SET password_scheme=?,password_salt=?,password_hash=?,password_params=?,account_version=account_version+1,password_changed_at=?,enabled=1 WHERE id=?",
            (
                record["scheme"],
                record["salt"],
                record["hash"],
                json.dumps(record["params"]),
                now,
                account["id"],
            ),
        )
        self._revoke(db, now, reason)
        self._audit(db, now, reason, account=account["id"])

    def change_password(self, secret, current, new, peer="local"):
        if not isinstance(new, str) or not 10 <= len(new) <= 128:
            raise BrowserAuthError("invalid_auth_request", 422)
        with self.repository.transaction() as db:
            session, account, now = self._load(db, secret, require_access=True)
            snapshot = dict(account)
            buckets = self._buckets(account["username_normalized"], peer)
            reservations = self._reserve(db, now, buckets)
        with self._reserved_attempt(reservations) as settled:
            valid = verify_password(
                (
                    current
                    if isinstance(current, str) and len(current) <= 128
                    else "invalid password input"
                ),
                self._record(snapshot),
            )
            valid = valid and isinstance(current, str) and 10 <= len(current) <= 128
            if not valid:
                with self.repository.transaction() as db:
                    now, _ = self._now(db)
                    self._finish_attempt(db, now, reservations, False)
                    self._audit(
                        db,
                        now,
                        "password_change",
                        "credentials_rejected",
                        account=snapshot["id"],
                        peer=peer,
                    )
                settled[0] = True
                raise BrowserAuthError("credentials_rejected")
            record = hash_password(new)
            with self.repository.transaction() as db:
                _, account, now = self._load(db, secret, require_access=True)
                if account["account_version"] != snapshot["account_version"]:
                    raise BrowserAuthError("session_revoked")
                self._finish_attempt(db, now, reservations, True)
                self._replace_password(db, account, record, now, "password_change")
            settled[0] = True

    def reset_password(self, new):
        record = hash_password(new)
        with self.repository.transaction() as db:
            now, _ = self._now(db, local=True)
            account = self._account(db)
            if account is None:
                raise BrowserAuthError("account_required", 409)
            self._replace_password(db, account, record, now, "local_recovery")
            db.execute("DELETE FROM browser_login_limits")

    def disable(self):
        with self.repository.transaction() as db:
            now, _ = self._now(db, local=True)
            db.execute("UPDATE browser_accounts SET enabled=0,account_version=account_version+1")
            self._revoke(db, now, "local_disable")
            self._audit(db, now, "local_disable")

    def invalidate_all(self, reason="restore_invalidate"):
        # Caller-provided strings never enter audit or revocation records.
        reason = (
            "restore_invalidate" if reason in ("restore", "restore_invalidate") else "revoke_all"
        )
        with self.repository.transaction() as db:
            now, _ = self._now(db, local=True)
            self._revoke(db, now, reason)
            self._audit(db, now, reason)
