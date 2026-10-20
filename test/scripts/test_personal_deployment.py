"""Personal deployment persists authority and PostgreSQL state without cleanup."""

import importlib.util
import json
import os
from pathlib import Path

import jwt
import pytest
from psycopg import sql

pytestmark = pytest.mark.postgres

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/personal_deployment.py"


def load_module():
    spec = importlib.util.spec_from_file_location("personal_deployment", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.offline_database
def test_backup_copy_omits_stale_database_client_passfiles(tmp_path):
    module = load_module()
    source = tmp_path / "source"
    cao = source / "cao"
    stale = cao / "pg-client-interrupted"
    stale.mkdir(parents=True, mode=0o700)
    (stale / "pgpass").write_text("secret:must:not:be:copied", encoding="utf-8")
    (stale / "pgpass").chmod(0o600)
    (cao / "durable-marker").write_text("keep", encoding="utf-8")
    destination = tmp_path / "backup"

    module._copy_state(source, destination)

    assert not (destination / "cao" / stale.name).exists()
    assert (destination / "cao" / "durable-marker").read_text() == "keep"


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
def deployment(tmp_path, postgres_targets):
    module = load_module()
    root = tmp_path / "personal"
    module.initialize(root, server_port=19889, jwks_port=19890, image_id="sha256:" + "a" * 64)
    module.write_database_config(root, _target_configuration(postgres_targets))
    module.bootstrap_database(root)
    module._test_postgres_targets = postgres_targets
    return module, root


def _target_configuration(targets):
    result = {}
    for scope in ("main", "auth", "peer"):
        target = getattr(targets, scope)
        result[scope] = {
            "host": target.host,
            "port": target.port,
            "database": target.database,
            "user": target.user,
            "schema": target.schema,
            "store_id": target.store_id,
            "secret_file": str(target.secret_file),
            "sslmode": target.sslmode,
            "migration_user": target.migration_user,
            "migration_secret_file": str(target.migration_secret_file),
        }
    return result


def _install_real_archive_adapter(module, postgres_service, tmp_path, monkeypatch):
    """Route the deployment helper through pg_dump/pg_restore in its owned PG container."""
    from test.services.test_postgres_recovery_bundle import _OwnedDockerArchiveRunner

    runner = _OwnedDockerArchiveRunner(postgres_service, tmp_path)

    def run(_root, target, executable, _arguments, *, output=None, input_path=None):
        if executable == "pg_dump":
            archive = tmp_path / f"personal-{target.scope}-{os.urandom(8).hex()}.dump"
            runner.dump(target, archive)
            assert output is not None
            output.write(archive.read_bytes())
            archive.unlink()
            return None
        if executable == "pg_restore":
            assert input_path is not None
            runner.restore(target, input_path)
            return None
        raise AssertionError(f"unexpected PostgreSQL executable: {executable}")

    monkeypatch.setattr(module, "_run_database_client", run)


def _create_restore_targets(module, targets, postgres_service, tmp_path):
    """Prepare physically distinct databases and a private external v1 config."""
    config = _target_configuration(targets)
    databases = []
    suffix = os.urandom(5).hex()
    with postgres_service.connect_admin() as admin:
        for scope in ("main", "auth", "peer"):
            target = getattr(targets, scope)
            database = f"pr_{suffix}_{scope}"
            admin.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(database), sql.Identifier(target.migration_user)
                )
            )
            admin.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database), sql.Identifier(target.user)
                )
            )
            config[scope]["database"] = database
            databases.append(database)
    path = tmp_path / "restore-database-config.json"
    path.write_text(json.dumps({"version": 1, "targets": config}, sort_keys=True))
    path.chmod(0o600)
    return path, tuple(databases)


def _drop_databases(postgres_service, databases):
    with postgres_service.connect_admin() as admin:
        for database in databases:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=%s AND pid<>pg_backend_pid()",
                (database,),
            )
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))


def test_initialization_is_private_and_idempotent(deployment):
    module, root = deployment
    before = (root / "issuer.pem").read_bytes()
    module.initialize(root, server_port=19889, jwks_port=19890, image_id="sha256:" + "a" * 64)
    assert (root / "issuer.pem").read_bytes() == before
    assert root.stat().st_mode & 0o077 == 0
    assert (root / "issuer.pem").stat().st_mode & 0o077 == 0
    assert module.environment(root)["CAO_HOME_DIR"] == str(root / "cao")
    assert module.environment(root)["CAO_WORK_DOCKER_LOCAL"] == "1"


def test_verify_database_accepts_full_work_component_history(deployment):
    """The staged v1 prefix is not treated as the installed Work history."""
    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.clients.postgres.registry import component_migrations

    module, root = deployment
    target = module.database_target(root, "main")
    work_histories = [
        history for history in component_migrations("main") if history[0].component == "work"
    ]
    complete = max(work_histories, key=len)
    assert len(work_histories) > 1
    with connect(target, readonly=True) as connection:
        installed = connection.execute(
            "SELECT max(version) FROM _cao_schema_migrations "
            "WHERE scope='main' AND component='work'"
        ).fetchone()[0]
    assert installed == complete[-1].version
    module.verify_database(root)


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


def test_backup_and_restore_roundtrip_all_postgresql_scopes(
    deployment, postgres_service, tmp_path, monkeypatch
):
    module, root = deployment
    targets = module._test_postgres_targets
    _install_real_archive_adapter(module, postgres_service, tmp_path, monkeypatch)
    # Add durable data in each scope so the archive check proves content, not just schema.
    from cli_agent_orchestrator.clients.postgres import connect

    with connect(targets.main) as connection:
        with connection.transaction():
            connection.execute("UPDATE work_scheduler_policy SET capacity=3 WHERE singleton=1")
    with connect(targets.auth) as connection:
        with connection.transaction():
            connection.execute(
                "INSERT INTO auth_metadata "
                "(singleton_id,schema_version,installation_id,auth_epoch,time_high_water) "
                "VALUES (1,1,'personal-proof',1,0) ON CONFLICT (singleton_id) DO UPDATE "
                "SET installation_id=EXCLUDED.installation_id"
            )
    with connect(targets.peer) as connection:
        with connection.transaction():
            connection.execute(
                "INSERT INTO local_project_write_leases "
                "(host_id,owner_uid,project_id,task_id,owner_instance_id,state,heartbeat_at,updated_at) "
                "VALUES ('proof-host',0,'proof-project','proof-task','proof-owner','interrupted',1,1)"
            )

    backup_path = tmp_path / "backup"
    module.backup(root, backup_path)
    manifest = json.loads((backup_path / "backup-manifest.json").read_text())
    assert set(manifest["scopes"]) == {"main", "auth", "peer"}
    assert {scope for item in manifest["dumps"] for scope in item["scopes"]} == {
        "main",
        "auth",
        "peer",
    }
    assert not (backup_path / "cao" / "database-config.json").exists()
    assert not (backup_path / "cao" / "postgres").exists()

    destination_config, databases = _create_restore_targets(
        module, targets, postgres_service, tmp_path
    )
    restored = tmp_path / "restored"
    try:
        module.restore(backup_path, restored, database_config=destination_config)
        module.verify_database(restored)
        restored_targets = {
            scope: module.database_target(restored, scope) for scope in ("main", "auth", "peer")
        }
        with connect(restored_targets["main"]) as connection:
            assert connection.execute(
                "SELECT capacity FROM work_scheduler_policy WHERE singleton=1"
            ).fetchone() == (3,)
            assert connection.execute(
                "SELECT execution_state FROM work_recovery_context WHERE singleton=1"
            ).fetchone() == ("blocked_restore",)
        with connect(restored_targets["auth"]) as connection:
            assert connection.execute(
                "SELECT installation_id FROM auth_metadata WHERE singleton_id=1"
            ).fetchone() == ("personal-proof",)
        with connect(restored_targets["peer"]) as connection:
            assert connection.execute(
                "SELECT task_id FROM local_project_write_leases WHERE project_id='proof-project'"
            ).fetchone() == ("proof-task",)
        assert module.jwks(restored) == module.jwks(root)
    finally:
        _drop_databases(postgres_service, databases)
    with pytest.raises(FileExistsError):
        module.restore(backup_path, root, database_config=destination_config)


def test_restore_failure_after_one_committed_scope_removes_only_attempt_schemas(
    deployment, postgres_service, tmp_path, monkeypatch
):
    module, root = deployment
    _install_real_archive_adapter(module, postgres_service, tmp_path, monkeypatch)
    backup_path = tmp_path / "backup-before-failure"
    module.backup(root, backup_path)
    destination_config, databases = _create_restore_targets(
        module, module._test_postgres_targets, postgres_service, tmp_path
    )
    destination_targets = {
        scope: module.database_target(root, scope, destination_config)
        for scope in ("main", "auth", "peer")
    }
    from hashlib import sha256

    backup_digests = {
        path.relative_to(backup_path): sha256(path.read_bytes()).hexdigest()
        for path in backup_path.rglob("*")
        if path.is_file()
    }
    original_runner = module._run_database_client
    restore_calls = []
    second_group_saw_main = []

    def fail_second_scope(selected_root, target, executable, arguments, **kwargs):
        if executable == "pg_restore":
            restore_calls.append(target.scope)
            if len(restore_calls) == 2:
                second_group_saw_main.append(
                    module._target_schema_exists(destination_targets["main"])
                )
                raise RuntimeError("injected second-scope restore failure")
        return original_runner(selected_root, target, executable, arguments, **kwargs)

    monkeypatch.setattr(module, "_run_database_client", fail_second_scope)
    restored = tmp_path / "unpublished-restore"
    try:
        with pytest.raises(RuntimeError, match="second-scope restore failure"):
            module.restore(backup_path, restored, database_config=destination_config)

        assert len(restore_calls) == 2
        assert second_group_saw_main == [True]
        assert not restored.exists()
        assert not any(
            module._target_schema_exists(target) for target in destination_targets.values()
        )
        assert backup_digests == {
            path.relative_to(backup_path): sha256(path.read_bytes()).hexdigest()
            for path in backup_path.rglob("*")
            if path.is_file()
        }
    finally:
        _drop_databases(postgres_service, databases)


def test_restore_preflights_existing_schema_without_touching_it(
    deployment, postgres_service, tmp_path, monkeypatch
):
    from cli_agent_orchestrator.clients.postgres import connect

    module, root = deployment
    _install_real_archive_adapter(module, postgres_service, tmp_path, monkeypatch)
    backup_path = tmp_path / "backup-before-preflight"
    module.backup(root, backup_path)
    destination_config, databases = _create_restore_targets(
        module, module._test_postgres_targets, postgres_service, tmp_path
    )
    targets = {
        scope: module.database_target(root, scope, destination_config)
        for scope in ("main", "auth", "peer")
    }
    existing = targets["peer"]
    with connect(existing.migration_target()) as connection:
        with connection.transaction():
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(existing.schema)))
    before = module._restore_schema_receipt(existing)
    original_runner = module._run_database_client
    restored_groups = []

    def track_restore(*args, **kwargs):
        restored_groups.append(args[2])
        return original_runner(*args, **kwargs)

    monkeypatch.setattr(module, "_run_database_client", track_restore)
    restored = tmp_path / "preflight-restore"
    try:
        with pytest.raises(ValueError, match="must be absent and empty"):
            module.restore(backup_path, restored, database_config=destination_config)

        assert restored_groups == []
        assert not restored.exists()
        assert module._restore_schema_receipt(existing) == before
        assert not module._target_schema_exists(targets["main"])
        assert not module._target_schema_exists(targets["auth"])
    finally:
        _drop_databases(postgres_service, databases)


@pytest.mark.parametrize("drift", ["schema_oid", "owner"])
def test_restore_compensation_refuses_replaced_or_reowned_schema(
    deployment, postgres_service, drift
):
    module, root = deployment
    target = module.database_target(root, "peer")
    original = module._restore_schema_receipt(target)
    assert original is not None

    with postgres_service.connect_admin(target.database) as admin:
        if drift == "schema_oid":
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(target.schema)))
            admin.execute(
                sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                    sql.Identifier(target.schema), sql.Identifier(target.migration_user)
                )
            )
        else:
            admin.execute(
                sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(
                    sql.Identifier(target.schema), sql.Identifier(target.user)
                )
            )

    replacement = module._restore_schema_receipt(target)
    assert replacement is not None
    if drift == "schema_oid":
        assert replacement["schema_oid"] != original["schema_oid"]
    else:
        assert replacement["owner_oid"] != original["owner_oid"]

    with pytest.raises(RuntimeError, match="schema identity changed"):
        module._remove_restored_schema_if_unchanged(target, original)

    assert module._restore_schema_receipt(target) == replacement


def test_database_client_can_dump_external_postgres_on_host_loopback(deployment, tmp_path):
    """The real PostgreSQL image must reach an externally published loopback target."""
    module, root = deployment
    target = module.database_target(root, "main")
    assert target.host == "127.0.0.1"

    archive_path = tmp_path / "external-loopback.dump"
    with archive_path.open("wb") as archive:
        module._run_database_client(
            root,
            target,
            "pg_dump",
            ["--format=custom", "--schema-only", "--schema", target.schema],
            output=archive,
        )

    assert archive_path.read_bytes().startswith(b"PGDMP")


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


def test_explicit_personal_scheduler_setup_survives_restart(deployment):
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.services.work_scheduler import WorkScheduler

    module, root = deployment
    repository = WorkRepository(module._test_postgres_targets.main)
    repository.initialize()
    with repository.transaction() as connection:
        connection.execute("DELETE FROM work_scheduler_policy WHERE singleton=1")

    # The normal-install operation fills a missing personal default without
    # changing the imported receipt during the earlier import-only phase.
    module.configure_scheduler(root)
    with repository.read_snapshot() as connection:
        row = connection.execute(
            "SELECT revision,capacity,max_queue FROM work_scheduler_policy WHERE singleton=1"
        ).fetchone()
        assert tuple(row) == (1, 1, 8)

    assert WorkScheduler(repository).claim_next(actor_id="personal-operator") is None
    WorkScheduler(repository).configure(
        capacity=2, max_queue=9, aging_seconds=10, expected_policy_revision=1
    )
    module.configure_scheduler(root)
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
    assert (root / "cao" / "database-config.json").stat().st_mode & 0o077 == 0
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
    from cli_agent_orchestrator.clients.postgres import connect

    target = module.database_target(root, "auth")
    with connect(target, readonly=True) as connection:
        assert connection.execute("SELECT count(*) FROM browser_accounts").fetchone() == (0,)


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


def test_restore_client_paths_are_final_before_publish(
    deployment, postgres_service, tmp_path, monkeypatch
):
    module, root = deployment
    targets = module._test_postgres_targets
    _install_real_archive_adapter(module, postgres_service, tmp_path, monkeypatch)
    backup = tmp_path / "restore-path-backup"
    module.backup(root, backup)
    destination_config, databases = _create_restore_targets(
        module, targets, postgres_service, tmp_path
    )
    destination = tmp_path / "restored"
    original = module.os.rename
    observed = []

    def publish(source, target):
        observed.append(Path(source).joinpath("client.env").read_text())
        original(source, target)

    monkeypatch.setattr(module.os, "rename", publish)
    try:
        module.restore(backup, destination, database_config=destination_config)
        assert len(observed) == 1
        assert str(destination / "cao") in observed[0]
        assert ".tmp/cao" not in observed[0]
    finally:
        _drop_databases(postgres_service, databases)


def test_reset_disable_and_restore_revoke_without_identity_change(
    deployment, postgres_service, tmp_path, monkeypatch
):
    module, root = deployment
    targets = module._test_postgres_targets
    _install_real_archive_adapter(module, postgres_service, tmp_path, monkeypatch)
    password = "correct horse battery staple"
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: password)
    module.account_create(root, username="operator")
    service = module.browser_auth_service(root)
    secret, _ = service.login("operator", password, True, "127.0.0.1")
    original = json.loads((root / "deployment.json").read_text())["browser_login"]["binding"]
    saved = tmp_path / "backup"
    module.backup(root, saved)
    destination_config, databases = _create_restore_targets(
        module, targets, postgres_service, tmp_path
    )
    restored = tmp_path / "restored"
    try:
        module.restore(saved, restored, database_config=destination_config)
        restored_service = module.browser_auth_service(restored)
        from cli_agent_orchestrator.models.browser_auth import BrowserAuthError

        with pytest.raises(BrowserAuthError):
            restored_service.session(secret)
        module.account_reset(root)
        with pytest.raises(BrowserAuthError):
            service.session(secret)
        assert (
            json.loads((root / "deployment.json").read_text())["browser_login"]["binding"]
            == original
        )
        module.account_disable(root)
        assert "#cao_token=" in module.write_browser_link(root).read_text()
        assert module.environment(root)["CAO_AUTH_JWKS_URI"]
        assert module.jwks(root) == module.jwks(restored)
    finally:
        _drop_databases(postgres_service, databases)


def test_missing_auth_schema_is_not_recreated_by_runtime_verification(deployment, postgres_service):
    from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch

    module, root = deployment
    target = module._test_postgres_targets.auth
    with postgres_service.connect_admin(target.database) as admin:
        admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(target.schema)))
    with pytest.raises(SchemaMismatch):
        module.verify_database(root)
    with postgres_service.connect_admin(target.database) as admin:
        assert (
            admin.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname=%s", (target.schema,)
            ).fetchone()
            is None
        )


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
