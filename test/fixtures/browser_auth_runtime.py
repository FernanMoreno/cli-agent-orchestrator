"""Disposable PostgreSQL-backed authenticated runtime for Playwright.

Controls travel over stdin only; no test clock, reset, or restart endpoint is
added to the production HTTP API. The child owns isolated PostgreSQL scopes, a
real local RSA JWKS issuer, and uvicorn.
"""

from __future__ import annotations

import atexit
import contextlib
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PASSWORD = "Fixture-password-484!"
USERNAME = "operator"


def _use_private_path_home(private_home: Path) -> None:
    """Keep this test process account paths private without setting HOME."""
    os.environ.pop("HOME", None)
    account_home = Path.home().resolve()
    resolved_private_home = private_home.resolve()
    if resolved_private_home == account_home:
        raise RuntimeError("private browser fixture home must differ from account home")
    Path.home = classmethod(lambda _cls: resolved_private_home)
    if Path.home().resolve() != resolved_private_home or "HOME" in os.environ:
        raise RuntimeError("private browser fixture home was not applied safely")


def _write_private(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _postgres_service():
    """Find the same uniquely owned test service used by pytest's PG fixture."""
    from test.postgres.fixtures import (
        OWNER_LABEL,
        OWNER_VALUE,
        PostgresService,
        _container_metadata,
        _docker,
        _inspect,
        _private_json,
    )

    metadata_file = os.environ.get("CAO_TEST_POSTGRES_METADATA_FILE")
    if metadata_file:
        supplied = _private_json(Path(metadata_file))
        inspected = _inspect(str(supplied.get("container_id", "")))
        metadata = _container_metadata(inspected["Id"])
        if supplied.get("run_id") != metadata["run_id"]:
            raise RuntimeError("PostgreSQL acceptance service identity changed")
        for key, supplied_key in (
            ("host", "published_host"),
            ("port", "published_port"),
            ("password_file", "password_file"),
        ):
            if supplied.get(supplied_key) != metadata[key]:
                raise RuntimeError("PostgreSQL acceptance service metadata changed")
    else:
        containers = _docker(
            ["docker", "ps", "--filter", f"label={OWNER_LABEL}={OWNER_VALUE}", "--quiet"]
        ).splitlines()
        if len(containers) != 1:
            raise RuntimeError("Exactly one owned PostgreSQL test service is required")
        metadata = _container_metadata(containers[0])
    password = Path(metadata["password_file"]).read_text(encoding="utf-8").strip()
    return PostgresService(metadata, password)


def _owned_resources_path(workspace: Path) -> Path:
    return workspace / "browser-auth-postgres-resources.json"


def _remember_target_resources(workspace: Path, config_path: Path) -> None:
    """Track only targets published under this fixture's private workspace."""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    resources_path = _owned_resources_path(workspace)
    resources = (
        json.loads(resources_path.read_text())
        if resources_path.exists()
        else {
            "databases": [],
            "roles": [],
        }
    )
    for target in config.get("targets", {}).values():
        database = target.get("database")
        if isinstance(database, str) and database not in resources["databases"]:
            resources["databases"].append(database)
        for role in (target.get("user"), target.get("migration_user")):
            if isinstance(role, str) and role not in resources["roles"]:
                resources["roles"].append(role)
    temporary = resources_path.with_name(resources_path.name + ".tmp")
    _write_private(temporary, json.dumps(resources, sort_keys=True).encode())
    os.replace(temporary, resources_path)


def _cleanup_postgres(workspace: Path) -> None:
    resources_path = _owned_resources_path(workspace)
    if not resources_path.exists():
        return
    resources = json.loads(resources_path.read_text(encoding="utf-8"))
    if not resources.get("databases") and not resources.get("roles"):
        resources_path.unlink(missing_ok=True)
        return
    from psycopg import sql

    service = _postgres_service()
    for database in resources["databases"]:
        with service.connect_admin() as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()",
                (database,),
            )
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))
    with service.connect_admin() as admin:
        for role in reversed(resources["roles"]):
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
    resources_path.unlink(missing_ok=True)


def _create_postgres_targets(
    personal,
    root: Path,
    workspace: Path,
    *,
    bootstrap: bool,
    prepare_schema: bool = True,
    schema_names: dict[str, str] | None = None,
):
    """Create isolated main/auth/peer targets; optionally install source schemas."""
    import secrets

    from psycopg import sql

    from cli_agent_orchestrator.clients.postgres import DatabaseTarget
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

    service = _postgres_service()
    suffix = uuid.uuid4().hex[:12]
    prefix = "caobrowser" + suffix
    home = root / "cao"
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    values = {}
    created_databases = []
    created_roles = []
    try:
        with service.connect_admin() as admin:
            for scope in ("main", "auth", "peer"):
                database = f"{prefix}_{scope}"
                owner = f"{prefix}_{scope}_owner"
                runtime = f"{prefix}_{scope}_app"
                owner_password = secrets.token_urlsafe(32)
                runtime_password = secrets.token_urlsafe(32)
                admin.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(owner), sql.Literal(owner_password)
                    )
                )
                created_roles.append(owner)
                admin.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(runtime), sql.Literal(runtime_password)
                    )
                )
                created_roles.append(runtime)
                admin.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(database), sql.Identifier(owner)
                    )
                )
                created_databases.append(database)
                admin.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(database), sql.Identifier(runtime)
                    )
                )
                schema = (schema_names or {}).get(scope, f"s_{suffix}_{scope}")
                owner_secret = f"{scope}-migration.json"
                runtime_secret = f"{scope}-runtime.json"
                _write_private(
                    home / owner_secret, json.dumps({"password": owner_password}).encode()
                )
                _write_private(
                    home / runtime_secret, json.dumps({"password": runtime_password}).encode()
                )
                values[scope] = {
                    "host": service.metadata["host"],
                    "port": service.metadata["port"],
                    "database": database,
                    "user": runtime,
                    "schema": schema,
                    "store_id": str(uuid.uuid4()),
                    "secret_file": runtime_secret,
                    "sslmode": "disable",
                    "migration_user": owner,
                    "migration_secret_file": owner_secret,
                }
        if prepare_schema:
            for scope, target in values.items():
                with service.connect_admin(target["database"]) as admin:
                    admin.execute(
                        sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                            sql.Identifier(target["schema"]),
                            sql.Identifier(target["migration_user"]),
                        )
                    )
                    admin.execute(
                        sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(
                            sql.Identifier(target["schema"])
                        )
                    )
                    admin.execute(
                        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                            sql.Identifier(target["schema"]), sql.Identifier(target["user"])
                        )
                    )
        personal.write_database_config(root, values)
        config_path = home / "database-config.json"
        _remember_target_resources(workspace, config_path)
        old_config = os.environ.get("CAO_DATABASE_CONFIG_FILE")
        os.environ["CAO_DATABASE_CONFIG_FILE"] = str(config_path)
        try:
            targets = {
                scope: DatabaseTarget.from_settings(scope=scope, home=home)
                for scope in ("main", "auth", "peer")
            }
        finally:
            if old_config is None:
                os.environ.pop("CAO_DATABASE_CONFIG_FILE", None)
            else:
                os.environ["CAO_DATABASE_CONFIG_FILE"] = old_config
        if bootstrap:
            for target in targets.values():
                bootstrap_scope(target)
        return targets, config_path
    except BaseException:
        if created_databases or created_roles:
            resources_path = _owned_resources_path(workspace)
            resources = {
                "databases": created_databases,
                "roles": created_roles,
            }
            _write_private(
                resources_path.with_name(resources_path.name + ".tmp"),
                json.dumps(resources, sort_keys=True).encode(),
            )
            os.replace(resources_path.with_name(resources_path.name + ".tmp"), resources_path)
            _cleanup_postgres(workspace)
        raise


def _provision_postgres(personal, root: Path, workspace: Path):
    """Create isolated source stores and explicitly install their schemas."""
    targets, config_path = _create_postgres_targets(personal, root, workspace, bootstrap=True)
    os.environ["CAO_DATABASE_CONFIG_FILE"] = str(config_path)
    os.environ["CAO_HOME_DIR"] = str(root / "cao")
    return targets


def main():
    resume = json.loads(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] == "--resume" else None
    setup_mode = "--setup" in sys.argv or bool(resume and resume.get("setup"))
    directory = (
        contextlib.nullcontext(resume["workspace"])
        if resume
        else tempfile.TemporaryDirectory(prefix="cao-browser-e2e-")
    )
    with directory as temporary:
        workspace = Path(temporary)
        os.chmod(workspace, 0o700)
        private_home = workspace / "home"
        private_home.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(private_home, 0o700)
        _use_private_path_home(private_home)
        import uvicorn

        os.environ.update(
            {
                "TMUX_TMPDIR": str(workspace),
                "CAO_A2A_DISABLED": "true",
                "OTEL_SDK_DISABLED": "true",
                "CAO_AGUI_ENABLED": "true",
            }
        )
        for key in (
            "AUTH0_DOMAIN",
            "AUTH0_AUDIENCE",
            "CAO_BROWSER_LOGIN_ROOT",
            "CAO_HOME_DIR",
            "CAO_DATABASE_CONFIG_FILE",
            "TMUX",
        ):
            os.environ.pop(key, None)
        spec = importlib.util.spec_from_file_location(
            "personal_e2e", Path(__file__).resolve().parents[2] / "scripts/personal_deployment.py"
        )
        personal = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(personal)
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", resume["port"] if resume else 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        root = Path(resume["root"]) if resume else workspace / "personal"
        # Real personal key/config format, without provisioning Docker or providers.
        personal.initialize(
            root,
            server_port=port,
            jwks_port=port + 1 if port < 65535 else port - 1,
            image_id="sha256:" + "a" * 64,
        )
        database_home = root / "cao"
        os.environ["CAO_HOME_DIR"] = str(database_home)
        os.environ["CAO_DATABASE_CONFIG_FILE"] = str(database_home / "database-config.json")
        from cli_agent_orchestrator import constants

        # Keep this fixture from reading or writing the developer's user-wide
        # local-peer directory. Production keeps the same stable host ID under
        # that user-owned location.
        constants.LOCAL_PEER_DIR = workspace / "local-peers"
        if resume:
            _remember_target_resources(workspace, database_home / "database-config.json")
        else:
            _provision_postgres(personal, root, workspace)
        cleanup_postgres = partial(_cleanup_postgres, workspace)
        atexit.register(cleanup_postgres)
        jwks_document = json.dumps(personal.jwks(root)).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/.well-known/jwks.json":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(jwks_document)))
                self.end_headers()
                self.wfile.write(jwks_document)

            def log_message(self, *_):
                pass

        issuer = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=issuer.serve_forever, daemon=True).start()
        config = json.loads((root / "deployment.json").read_text())
        os.environ.update(
            {
                "CAO_AUTH_JWKS_URI": f"http://127.0.0.1:{issuer.server_port}/.well-known/jwks.json",
                "CAO_AUTH_ISSUER": config["issuer"],
                "CAO_AUTH_AUDIENCE": config["audience"],
            }
        )
        import jwt

        from cli_agent_orchestrator.clients.postgres import DatabaseTarget, connect
        from cli_agent_orchestrator.security.auth import FULL_SCOPE_SET, principal_from_token
        from cli_agent_orchestrator.services.browser_auth import BrowserAuthService

        token = jwt.encode(
            {
                "iss": config["issuer"],
                "sub": config["subject"],
                "aud": config["audience"],
                "scope": " ".join(FULL_SCOPE_SET),
                "exp": int(time.time()) + 86400,
            },
            (root / "issuer.pem").read_bytes(),
            algorithm="RS256",
            headers={"kid": config["kid"]},
        )
        actor = principal_from_token(token)
        binding = {key: getattr(actor, key) for key in ("id", "issuer", "subject", "kind")}
        binding["scopes"] = sorted(actor.scopes)
        controlled = [resume["clock"] if resume else time.time()]
        policy = {
            "enabled": True,
            "installation_id": "acceptance",
            "binding": binding,
            "canonical_origin": origin,
            "transport_policy": "loopback_http",
        }
        if setup_mode:
            os.environ["CAO_BROWSER_LOGIN_ROOT"] = str(root)
            from cli_agent_orchestrator.api.main import app

            service = None
        else:
            config["scopes"] = binding["scopes"]
            config["browser_login"] = policy
            personal._write(root / "deployment.json", json.dumps(config).encode())
            target = DatabaseTarget.from_settings(scope="auth", home=root / "cao")
            service = BrowserAuthService(target, binding, policy, clock=lambda: controlled[0])
            if not resume:
                service.initialize_store()
                service.create_account(USERNAME, PASSWORD)
            else:
                service.validate_binding()
            from cli_agent_orchestrator.api.main import app

            app.state.browser_auth = service
            app.state.browser_auth_config = {
                "enabled": True,
                "installation_id": "acceptance",
                "canonical_origin": origin,
                "transport_policy": "loopback_http",
                "loopback_http": True,
            }
        database_lock = None
        server = None
        thread = None

        def start():
            nonlocal server, thread
            server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
            thread = threading.Thread(
                target=server.run, kwargs={"sockets": [listener]}, daemon=True
            )
            thread.start()
            deadline = time.monotonic() + 40
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("authenticated browser runtime did not start")
                time.sleep(0.05)

        def stop():
            if server:
                server.should_exit = True
            if thread:
                thread.join(timeout=15)
                if thread.is_alive():
                    raise RuntimeError("browser runtime did not stop")
            with contextlib.suppress(OSError):
                listener.close()

        terminal_session = "browser-e2e-" + uuid.uuid4().hex[:12]
        terminal_id = "browser-fixture-" + uuid.uuid4().hex[:12]
        try:
            start()
            # A real disposable PTY target, with no provider credentials or paid calls.
            subprocess.run(
                [
                    "tmux",
                    "new-session",
                    "-d",
                    "-s",
                    terminal_session,
                    "-n",
                    "browser-fixture",
                    "/bin/cat",
                ],
                check=True,
            )
            from cli_agent_orchestrator.clients.database import create_terminal

            create_terminal(terminal_id, terminal_session, "browser-fixture", "mock_cli")
            print(
                json.dumps(
                    {
                        "ready": True,
                        "origin": origin,
                        "terminal_id": terminal_id,
                        **({"bootstrap_token": token} if setup_mode else {}),
                    }
                ),
                flush=True,
            )
            if resume:
                print(json.dumps({"ack": "restart"}), flush=True)
            for raw in sys.stdin:
                command = json.loads(raw)
                action = command["action"]
                if setup_mode:
                    service = getattr(app.state, "browser_auth", None)
                if action == "advance":
                    controlled[0] += command["seconds"]
                elif action == "reset":
                    service.reset_password(PASSWORD)
                elif action == "restart":
                    # Replace the Python interpreter after closing the complete server,
                    # preserving only private on-disk state and the internal test clock.
                    stop()
                    issuer.shutdown()
                    issuer.server_close()
                    subprocess.run(
                        ["tmux", "kill-session", "-t", terminal_session],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    checkpoint = json.dumps(
                        {
                            "workspace": str(workspace),
                            "root": str(root),
                            "port": port,
                            "clock": controlled[0],
                            "setup": setup_mode,
                        }
                    )
                    os.execv(
                        sys.executable,
                        [sys.executable, str(Path(__file__).resolve()), "--resume", checkpoint],
                    )
                elif action == "restore":
                    stop()
                    archive = workspace / ("archive-" + uuid.uuid4().hex)
                    restored = workspace / ("restored-" + uuid.uuid4().hex)
                    personal.backup(root, archive)
                    source_targets = json.loads(
                        (root / "cao" / "database-config.json").read_text()
                    )["targets"]
                    destination_target_root = workspace / ("restore-target-" + uuid.uuid4().hex)
                    _, destination_config = _create_postgres_targets(
                        personal,
                        destination_target_root,
                        workspace,
                        bootstrap=False,
                        prepare_schema=False,
                        schema_names={
                            scope: source_targets[scope]["schema"]
                            for scope in ("main", "auth", "peer")
                        },
                    )
                    destination_targets = json.loads(destination_config.read_text())["targets"]
                    for scope in ("main", "auth", "peer"):
                        source = source_targets[scope]
                        destination = destination_targets[scope]
                        if (
                            source["database"],
                            source["schema"],
                        ) == (destination["database"], destination["schema"]):
                            raise RuntimeError(
                                "PostgreSQL restore target must be physically distinct"
                            )
                    personal.restore(archive, restored, database_config=destination_config)
                    root = restored
                    os.environ.update(personal.environment(root))
                    # This fixture injects a controlled clock directly into the
                    # app state. The production lifespan hook would rebuild an
                    # auth service with wall time if this deployment-root
                    # setting remained present, breaking future-clock tests.
                    os.environ.pop("CAO_BROWSER_LOGIN_ROOT", None)
                    constants.CAO_HOME_DIR = root / "cao"
                    from cli_agent_orchestrator.clients import database

                    database.engine.dispose()
                    restored_config = personal._config(root)
                    _remember_target_resources(workspace, root / "cao" / "database-config.json")
                    policy = restored_config["browser_login"]
                    target = DatabaseTarget.from_settings(scope="auth", home=root / "cao")
                    service = BrowserAuthService(
                        target, binding, policy, clock=lambda: controlled[0]
                    )
                    app.state.browser_auth = service
                    app.state.browser_auth_config = policy
                    listener = socket.socket()
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    listener.bind(("127.0.0.1", port))
                    listener.listen(128)
                    start()
                elif action == "lock_storage":
                    target = DatabaseTarget.from_settings(scope="auth", home=root / "cao")
                    database_lock = connect(target)
                    database_lock.begin()
                    database_lock.execute(
                        "SELECT singleton_id FROM auth_metadata WHERE singleton_id=1 FOR UPDATE"
                    )
                elif action == "unlock_storage":
                    database_lock.rollback()
                    database_lock.close()
                    database_lock = None
                elif action == "terminal_alive":
                    subprocess.run(["tmux", "has-session", "-t", terminal_session], check=True)
                elif action == "stop":
                    break
                else:
                    raise ValueError("unknown private fixture command")
                print(json.dumps({"ack": action}), flush=True)
        finally:
            if database_lock is not None:
                database_lock.rollback()
                database_lock.close()
            subprocess.run(
                ["tmux", "kill-session", "-t", terminal_session],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            stop()
            issuer.shutdown()
            issuer.server_close()
            with contextlib.suppress(OSError):
                listener.close()
            try:
                cleanup_postgres()
            finally:
                atexit.unregister(cleanup_postgres)
            if resume:
                shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    main()
