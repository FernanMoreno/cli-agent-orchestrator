"""Personal deployment persists authority and SQLite state without demo cleanup."""

import importlib.util
import json
import os
import sqlite3
from pathlib import Path

import jwt
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/personal_deployment.py"


def load_module():
    spec = importlib.util.spec_from_file_location("personal_deployment", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_browser_link_is_private_and_contains_short_lived_fragment(deployment):
    from urllib.parse import parse_qs, urlsplit

    module, root = deployment
    link = module.write_browser_link(root)
    assert link.stat().st_mode & 0o077 == 0
    url = urlsplit(link.read_text().strip())
    assert url.netloc == "127.0.0.1:19889"
    assert not url.query
    token = parse_qs(url.fragment)["cao_token"][0]
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == 3600


@pytest.fixture
def deployment(tmp_path):
    module = load_module()
    root = tmp_path / "personal"
    module.initialize(root, server_port=19889, jwks_port=19890, image_id="sha256:" + "a" * 64)
    return module, root


def test_initialization_is_private_and_idempotent(deployment):
    module, root = deployment
    before = (root / "issuer.pem").read_bytes()
    module.initialize(root, server_port=19889, jwks_port=19890, image_id="sha256:" + "a" * 64)
    assert (root / "issuer.pem").read_bytes() == before
    assert root.stat().st_mode & 0o077 == 0
    assert (root / "issuer.pem").stat().st_mode & 0o077 == 0
    assert module.environment(root)["CAO_HOME_DIR"] == str(root / "cao")
    assert module.environment(root)["CAO_WORK_DOCKER_LOCAL"] == "1"


def test_token_is_signed_and_bound_to_persistent_issuer(deployment):
    module, root = deployment
    first = module.mint_token(root, lifetime=3600)
    key = jwt.PyJWK.from_dict(module.jwks(root)["keys"][0]).key
    config = json.loads((root / "deployment.json").read_text())
    claims = jwt.decode(
        first, key, algorithms=["RS256"], issuer=config["issuer"], audience=config["audience"]
    )
    assert claims["scope"] == "cao:read cao:write cao:admin"
    assert claims["exp"] - claims["iat"] == 3600
    with pytest.raises(ValueError):
        module.mint_token(root, lifetime=0)


def test_insecure_private_key_and_non_loopback_config_fail_closed(deployment):
    module, root = deployment
    (root / "issuer.pem").chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        module.environment(root)
    (root / "issuer.pem").chmod(0o600)
    p = root / "deployment.json"
    value = json.loads(p.read_text())
    value["host"] = "0.0.0.0"
    p.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="loopback"):
        module.environment(root)


def test_backup_includes_wal_and_restore_preserves_state(deployment, tmp_path):
    module, root = deployment
    database = root / "cao" / "database.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE proof(value TEXT)")
    connection.execute("INSERT INTO proof VALUES ('durable')")
    connection.commit()
    destination = tmp_path / "backup"
    module.backup(root, destination)
    restored = tmp_path / "restored"
    module.restore(destination, restored)
    with sqlite3.connect(restored / "cao" / "database.db") as reader:
        assert reader.execute("SELECT value FROM proof").fetchone() == ("durable",)
        reader.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert module.jwks(restored) == module.jwks(root)
    with pytest.raises(FileExistsError):
        module.restore(destination, root)
    connection.close()


def test_backup_refuses_running_service_and_nested_destination(deployment, tmp_path):
    import fcntl

    module, root = deployment
    with (root / "service.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="stop"):
            module.backup(root, tmp_path / "busy-backup")
    with pytest.raises(ValueError):
        module.backup(root, root / "backup")


def test_service_is_loopback_with_restart_and_existing_credentials(deployment):
    module, root = deployment
    unit = module.service_unit(root)
    assert "Restart=on-failure" in unit
    assert "RuntimeMaxSec" not in unit
    assert "personal_deployment.py" in unit
    assert "HOME=" not in unit
    env = module.environment(root)
    assert env["CAO_API_HOST"] == "127.0.0.1"
    assert "HOME" not in env


def test_explicit_personal_scheduler_setup_survives_restart(tmp_path):
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.services.work_scheduler import WorkScheduler

    module = load_module()
    repository = WorkRepository(tmp_path / "work.sqlite3")
    repository.initialize()
    module.configure_personal_scheduler(repository)
    assert WorkScheduler(repository).claim_next(actor_id="personal-operator") is None
    WorkScheduler(repository).configure(
        capacity=2, max_queue=9, aging_seconds=10, expected_policy_revision=1
    )
    module.configure_personal_scheduler(repository)
    with repository.transaction() as connection:
        row = connection.execute(
            "SELECT revision,capacity,max_queue FROM work_scheduler_policy"
        ).fetchone()
        assert tuple(row) == (2, 2, 9)


def test_expected_server_shutdown_is_not_a_failure(deployment, monkeypatch):
    """systemd SIGTERM should stop cleanly; unexpected child exits remain failures."""
    import signal
    from unittest.mock import MagicMock

    module, root = deployment
    handlers = {}
    monkeypatch.setattr(module.os, "environ", os.environ.copy())

    def install(sig, callback):
        previous = handlers.get(sig, signal.SIG_DFL)
        handlers[sig] = callback
        return previous

    process = MagicMock()
    process.poll.return_value = None

    def wait():
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        process.poll.return_value = -signal.SIGTERM
        return -signal.SIGTERM

    process.wait.side_effect = wait
    monkeypatch.setattr(module.signal, "signal", install)
    monkeypatch.setattr(module, "ThreadingHTTPServer", MagicMock())
    monkeypatch.setattr(module.threading, "Thread", MagicMock())
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr("cli_agent_orchestrator.clients.database.init_db", lambda: None)
    monkeypatch.setattr(module, "configure_personal_scheduler", lambda repository: None)
    module.serve(root)
    process.terminate.assert_called_once()


def test_account_create_verifies_local_identity_and_clean_link(deployment, monkeypatch):
    module, root = deployment
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: "correct horse battery staple")
    module.account_create(root, username="operator")
    config = json.loads((root / "deployment.json").read_text())
    policy = config["browser_login"]
    assert policy["enabled"] is True
    assert policy["binding"]["kind"] == "jwt"
    assert policy["binding"]["issuer"] == config["issuer"]
    assert policy["binding"]["subject"] == config["subject"]
    assert policy["binding"]["scopes"] == ["cao:read", "cao:write", "cao:admin"]
    assert module.environment(root)["CAO_BROWSER_LOGIN_ROOT"] == str(root)
    assert module.write_browser_link(root).read_text() == "http://127.0.0.1:19889/\n"
    assert (root / "browser-auth.sqlite3").stat().st_mode & 0o077 == 0
    assert "correct horse" not in (root / "deployment.json").read_text()


def test_password_confirmation_failure_leaves_login_disabled(deployment, monkeypatch):
    module, root = deployment
    prompts = iter(["correct horse battery staple", "different password"])
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: next(prompts))
    with pytest.raises(ValueError, match="match"):
        module.account_create(root, username="operator")
    assert (
        not json.loads((root / "deployment.json").read_text())
        .get("browser_login", {})
        .get("enabled")
    )
    assert not (root / "browser-auth.sqlite3").exists()


def test_browser_login_config_tampering_fails_closed(deployment, monkeypatch):
    module, root = deployment
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: "correct horse battery staple")
    module.account_create(root, username="operator")
    path = root / "deployment.json"
    config = json.loads(path.read_text())
    config["subject"] = "other-owner"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="binding"):
        module.environment(root)


def test_restore_client_paths_are_final_before_publish(deployment, tmp_path, monkeypatch):
    module, root = deployment
    destination = tmp_path / "restored"
    original = module.os.rename
    observed = []

    def publish(source, target):
        observed.append(Path(source).joinpath("client.env").read_text())
        original(source, target)

    monkeypatch.setattr(module.os, "rename", publish)
    module.restore(root, destination)
    assert len(observed) == 1
    assert str(destination / "cao") in observed[0]
    assert ".tmp/cao" not in observed[0]


def test_reset_disable_and_restore_revoke_without_identity_change(
    deployment, tmp_path, monkeypatch
):
    module, root = deployment
    password = "correct horse battery staple"
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: password)
    module.account_create(root, username="operator")
    service = module.browser_auth_service(root)
    secret, _ = service.login("operator", password, True, "127.0.0.1")
    original = json.loads((root / "deployment.json").read_text())["browser_login"]["binding"]
    saved = tmp_path / "backup"
    module.backup(root, saved)
    restored = tmp_path / "restored"
    module.restore(saved, restored)
    restored_service = module.browser_auth_service(restored)
    from cli_agent_orchestrator.models.browser_auth import BrowserAuthError

    with pytest.raises(BrowserAuthError):
        restored_service.session(secret)
    password = "replacement horse battery staple"
    module.account_reset(root)
    with pytest.raises(BrowserAuthError):
        service.session(secret)
    assert (
        json.loads((root / "deployment.json").read_text())["browser_login"]["binding"] == original
    )
    module.account_disable(root)
    assert "#cao_token=" in module.write_browser_link(root).read_text()
    assert module.environment(root)["CAO_AUTH_JWKS_URI"]
    assert module.jwks(root) == module.jwks(restored)


def test_missing_browser_database_is_not_recreated_on_startup(deployment, monkeypatch):
    module, root = deployment
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: "correct horse battery staple")
    module.account_create(root, username="operator")
    (root / "browser-auth.sqlite3").unlink()
    with pytest.raises(ValueError, match="private"):
        module.environment(root)
    assert not (root / "browser-auth.sqlite3").exists()


def test_account_reset_remains_available_with_service_lock(deployment, monkeypatch):
    module, root = deployment
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: "correct horse battery staple")
    module.account_create(root, username="operator")
    with module._lock(root):
        module.account_reset(root)
    assert module.browser_auth_service(root)


def test_interrupted_create_is_retryable_without_account_overwrite(deployment, monkeypatch):
    module, root = deployment
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: "correct horse battery staple")
    write = module._write

    def fail_enabled(path, content):
        if path.name == "deployment.json" and json.loads(content).get("browser_login", {}).get(
            "enabled"
        ):
            raise OSError("injected configuration failure")
        write(path, content)

    monkeypatch.setattr(module, "_write", fail_enabled)
    with pytest.raises(OSError, match="injected"):
        module.account_create(root, username="operator")
    assert json.loads((root / "deployment.json").read_text())["browser_login"]["enabled"] is False
    monkeypatch.setattr(module, "_write", write)
    module.account_create(root, username="operator")
    assert json.loads((root / "deployment.json").read_text())["browser_login"]["enabled"] is True


def test_backup_serializes_with_local_account_updates(deployment, tmp_path):
    module, root = deployment
    with module._lock(root, name="account.lock"):
        with pytest.raises(RuntimeError, match="account operation"):
            module.backup(root, tmp_path / "account-update-backup")


def test_local_token_publication_is_private_and_keeps_identity(deployment):
    module, root = deployment
    expires = module.publish_local_token(root, lifetime=60)
    path = root / "mcp-bearer.jwt"
    assert path.stat().st_mode & 0o777 == 0o600
    key = jwt.PyJWK.from_dict(module.jwks(root)["keys"][0]).key
    config = json.loads((root / "deployment.json").read_text())
    claims = jwt.decode(
        path.read_text(),
        key,
        algorithms=["RS256"],
        issuer=config["issuer"],
        audience=config["audience"],
    )
    assert expires == claims["exp"]
    assert claims["sub"] == config["subject"]
    assert claims["scope"] == " ".join(config["scopes"])
    assert claims["exp"] - claims["iat"] == 60
    assert module.environment(root)["CAO_AUTH_LOCAL_TOKEN_FILE"] == str(path)


def test_local_token_publication_failure_preserves_previous_file(deployment, monkeypatch):
    module, root = deployment
    module.publish_local_token(root)
    path = root / "mcp-bearer.jwt"
    before = path.read_bytes()
    monkeypatch.setattr(
        module.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("publication failed"))
    )
    with pytest.raises(OSError):
        module.publish_local_token(root)
    assert path.read_bytes() == before
    assert not list(root.glob("mcp-bearer.jwt.*.tmp"))


def test_renewal_loop_updates_credential_without_restarting_any_process(deployment, monkeypatch):
    module, root = deployment
    calls = []
    monkeypatch.setattr(
        module, "publish_local_token", lambda root, **kwargs: calls.append(kwargs) or 1000
    )

    class Stop:
        def is_set(self):
            return False

        def wait(self, seconds):
            assert 0 < seconds <= 30
            return True

    module.rotate_local_tokens(root, Stop(), expires_at=0, lifetime=60, clock=lambda: 100)
    assert calls == [{"lifetime": 60}]


def test_renewal_loop_retries_failure_without_leaking_token(deployment, monkeypatch, capsys):
    module, root = deployment
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise OSError("secret-token-must-not-appear")

    monkeypatch.setattr(module, "publish_local_token", fail)

    class Stop:
        def is_set(self):
            return False

        def wait(self, seconds):
            assert seconds == 5
            return True

    module.rotate_local_tokens(root, Stop(), expires_at=0, lifetime=60, clock=lambda: 100)
    assert calls == [True]
    assert "secret-token-must-not-appear" not in capsys.readouterr().err


@pytest.mark.parametrize("ttl", [0, 59, 86401, True, "60"])
def test_local_token_ttl_rejects_invalid_values(deployment, ttl):
    module, root = deployment
    with pytest.raises(ValueError):
        module.publish_local_token(root, lifetime=ttl)
