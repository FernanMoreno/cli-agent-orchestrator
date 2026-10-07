#!/usr/bin/env python3
"""Private, persistent localhost CAO service; host providers plus Docker Work."""

from __future__ import annotations

import argparse
import base64
import fcntl
import getpass
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _private(path: Path, *, directory=False):
    if path.is_symlink() or not path.exists():
        raise ValueError(f"private {'directory' if directory else 'file'} required: {path.name}")
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError(f"private owner-only permissions required: {path.name}")
    if directory != path.is_dir():
        raise ValueError(f"invalid private path: {path.name}")


def _write(path: Path, content: bytes):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as writer:
            writer.write(content)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _config(root: Path):
    _private(root, directory=True)
    for name in ("deployment.json", "issuer.pem"):
        _private(root / name)
    _private(root / "cao", directory=True)
    config = json.loads((root / "deployment.json").read_text())
    if config.get("host") != "127.0.0.1":
        raise ValueError("personal deployment must bind to loopback")
    for field in ("server_port", "jwks_port"):
        if type(config.get(field)) is not int or not 1024 <= config[field] <= 65535:
            raise ValueError("invalid deployment port")
    if config["server_port"] == config["jwks_port"]:
        raise ValueError("server and issuer ports must differ")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", config.get("image_id", "")):
        raise ValueError("immutable Docker image ID required")
    policy = config.get("browser_login")
    if policy is not None:
        if not isinstance(policy, dict) or type(policy.get("enabled")) is not bool:
            raise ValueError("invalid browser login configuration")
        if (
            policy.get("canonical_origin") != f"http://127.0.0.1:{config['server_port']}"
            or policy.get("transport_policy") != "loopback_http"
        ):
            raise ValueError("invalid browser login origin or transport")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", policy.get("installation_id", "")):
            raise ValueError("invalid browser login installation ID")
        if policy.get("binding") != _local_binding(root, config):
            raise ValueError("browser login binding differs from configured operator")
    return config


def _local_binding(root, config):
    """Verify with the existing local public key; no running HTTP issuer required."""
    from cli_agent_orchestrator.security.auth import _scopes_from_claims, _verified_principal

    scopes = config.get("scopes", ["cao:read", "cao:write", "cao:admin"])
    if (
        not isinstance(scopes, list)
        or not scopes
        or any(scope not in ("cao:read", "cao:write", "cao:admin") for scope in scopes)
    ):
        raise ValueError("invalid configured operator scopes")
    key = serialization.load_pem_private_key((root / "issuer.pem").read_bytes(), password=None)
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": config["issuer"],
            "sub": config["subject"],
            "aud": config["audience"],
            "exp": now + 60,
            "scope": " ".join(scopes),
        },
        key,
        algorithm="RS256",
    )
    claims = jwt.decode(
        token,
        key.public_key(),
        algorithms=["RS256"],
        issuer=config["issuer"],
        audience=config["audience"],
        options={"require": ["iss", "sub", "aud", "exp"]},
    )
    principal = _verified_principal(
        claims["iss"], claims["sub"], _scopes_from_claims(claims), "jwt"
    )
    return {
        "id": principal.id,
        "issuer": principal.issuer,
        "subject": principal.subject,
        "kind": principal.kind,
        "scopes": scopes,
    }


def browser_auth_service(root: Path, *, validate=True):
    from cli_agent_orchestrator.services.browser_auth import BrowserAuthService

    config = _config(root)
    policy = config.get("browser_login")
    if policy is None:
        raise ValueError("browser login account has not been configured")
    path = root / "browser-auth.sqlite3"
    if validate:
        _private(path)
    service = BrowserAuthService(path, policy["binding"], policy)
    if validate:
        service.validate_binding()
    return service


def _password():
    password = getpass.getpass("New password: ")
    if password != getpass.getpass("Confirm password: "):
        raise ValueError("passwords do not match")
    return password


def account_create(root: Path, *, username: str):
    config = _config(root)
    password = _password()
    with _lock(root), _lock(root, name="account.lock"):
        config = _config(root)
        if config.get("browser_login", {}).get("enabled"):
            raise ValueError("browser login account is already configured; use account-reset")
        config["scopes"] = config.get("scopes", ["cao:read", "cao:write", "cao:admin"])
        policy = {
            "enabled": False,
            "provisioning_pending": True,
            "installation_id": uuid.uuid4().hex,
            "canonical_origin": f"http://127.0.0.1:{config['server_port']}",
            "transport_policy": "loopback_http",
            "binding": _local_binding(root, config),
            "limits": {
                "access_seconds": 3600,
                "remembered_idle_seconds": 604800,
                "remembered_absolute_seconds": 2592000,
                "temporal_idle_seconds": 28800,
                "temporal_absolute_seconds": 86400,
            },
        }
        from cli_agent_orchestrator.services.browser_auth import BrowserAuthService

        policy = config.get("browser_login", policy)
        config["browser_login"] = policy
        _write(root / "deployment.json", json.dumps(config, indent=2).encode())
        service = BrowserAuthService(root / "browser-auth.sqlite3", policy["binding"], policy)
        from cli_agent_orchestrator.models.browser_auth import BrowserAuthError

        try:
            service.create_account(username, password)
        except BrowserAuthError as error:
            if (
                error.code != "account_exists"
                or not policy.get("provisioning_pending")
                or not service.account_matches(username, password)
            ):
                raise
        policy.pop("provisioning_pending", None)
        policy["enabled"] = True
        config["browser_login"] = policy
        _write(root / "deployment.json", json.dumps(config, indent=2).encode())
    renew_client(root)


def account_reset(root: Path):
    _config(root)
    password = _password()
    with _lock(root, name="account.lock"):
        service = browser_auth_service(root, validate=False)
        service.reset_password(password)
        config = _config(root)
        config["browser_login"].pop("provisioning_pending", None)
        config["browser_login"]["enabled"] = True
        _write(root / "deployment.json", json.dumps(config, indent=2).encode())
        renew_client(root)


def account_disable(root: Path):
    with _lock(root, name="account.lock"):
        browser_auth_service(root).disable()
        config = _config(root)
        config["browser_login"]["enabled"] = False
        _write(root / "deployment.json", json.dumps(config, indent=2).encode())
        renew_client(root)


def initialize(root: Path, *, server_port=9889, jwks_port=9890, image_id: str):
    if root.exists():
        existing = _config(root)
        if (existing["server_port"], existing["jwks_port"], existing["image_id"]) != (
            server_port,
            jwks_port,
            image_id,
        ):
            raise ValueError("existing deployment differs; do not overwrite live configuration")
        return
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("immutable Docker image ID required")
    if (
        any(type(port) is not int or not 1024 <= port <= 65535 for port in (server_port, jwks_port))
        or server_port == jwks_port
    ):
        raise ValueError("distinct unprivileged ports required")
    root.mkdir(parents=True, mode=0o700)
    (root / "cao").mkdir(mode=0o700)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _write(
        root / "issuer.pem",
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    _write(
        root / "deployment.json",
        json.dumps(
            {
                "host": "127.0.0.1",
                "server_port": server_port,
                "jwks_port": jwks_port,
                "image_id": image_id,
                "issuer": "urn:cao:personal:" + uuid.uuid4().hex,
                "audience": "cao-personal",
                "subject": "personal-operator",
                "scopes": ["cao:read", "cao:write", "cao:admin"],
                "kid": uuid.uuid4().hex,
            },
            indent=2,
        ).encode(),
    )
    renew_client(root)


def _key(root):
    _config(root)
    return serialization.load_pem_private_key((root / "issuer.pem").read_bytes(), password=None)


def jwks(root: Path):
    config = _config(root)
    numbers = _key(root).public_key().public_numbers()

    def encode(n):
        return (
            base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big"))
            .rstrip(b"=")
            .decode()
        )

    return {
        "keys": [
            {
                "kty": "RSA",
                "alg": "RS256",
                "use": "sig",
                "kid": config["kid"],
                "n": encode(numbers.n),
                "e": encode(numbers.e),
            }
        ]
    }


def mint_token(root: Path, *, lifetime=3600):
    if type(lifetime) is not int or not 60 <= lifetime <= 86400:
        raise ValueError("token lifetime must be 60..86400 seconds")
    config = _config(root)
    now = int(time.time())
    return jwt.encode(
        {
            "iss": config["issuer"],
            "aud": config["audience"],
            "sub": config["subject"],
            "iat": now,
            "exp": now + lifetime,
            "scope": " ".join(config.get("scopes", ["cao:read", "cao:write", "cao:admin"])),
        },
        _key(root),
        algorithm="RS256",
        headers={"kid": config["kid"], "typ": "JWT"},
    )


def publish_local_token(root: Path, *, lifetime=3600):
    """Atomically publish a renewable operator bearer under the private root."""
    token = mint_token(root, lifetime=lifetime)
    path = root / "mcp-bearer.jwt"
    if path.exists() or path.is_symlink():
        _private(path)
    _write(path, token.encode("ascii"))
    return jwt.decode(token, options={"verify_signature": False})["exp"]


def rotate_local_tokens(root: Path, stopping, expires_at: int, *, lifetime=3600, clock=time.time):
    """Renew before expiry; publication failure neither replays work nor restarts agents."""
    margin = min(300, lifetime // 2)
    while not stopping.is_set():
        if expires_at - clock() <= margin:
            try:
                expires_at = publish_local_token(root, lifetime=lifetime)
            except (OSError, ValueError):
                print("Local MCP credential renewal failed; retrying publication", file=sys.stderr)
                if stopping.wait(5):
                    break
                continue
        if stopping.wait(min(30, max(0.2, expires_at - clock() - margin))):
            break


def environment(root: Path):
    root = root.absolute()
    config = _config(root)
    if config.get("browser_login", {}).get("enabled"):
        browser_auth_service(root)
    env = {
        "CAO_BROWSER_LOGIN_ROOT": str(root),
        "CAO_HOME_DIR": str(root / "cao"),
        "CAO_API_HOST": "127.0.0.1",
        "CAO_API_PORT": str(config["server_port"]),
        "CAO_TERMINAL_BACKEND": "tmux",
        "TMUX_TMPDIR": str(root.with_name(root.name + "-sockets")),
        "CAO_AUTH_JWKS_URI": f"http://127.0.0.1:{config['jwks_port']}/jwks.json",
        "CAO_AUTH_ISSUER": config["issuer"],
        "CAO_AUTH_AUDIENCE": config["audience"],
        "CAO_AUTH_LOCAL_TOKEN": mint_token(root),
        "CAO_WORK_DOCKER_LOCAL": "1",
        "CAO_WORK_DOCKER_IMAGE_ID": config["image_id"],
        "CAO_ENABLE_PUBLIC_WORK_INGRESS": "true",
    }

    credential = root / "mcp-bearer.jwt"
    if credential.exists() or credential.is_symlink():
        _private(credential)
        env["CAO_AUTH_LOCAL_TOKEN_FILE"] = str(credential)
    return env


def renew_client(root: Path, *, final_root: Path | None = None):
    env = environment(root)
    if final_root is not None:
        for name in ("CAO_HOME_DIR", "CAO_BROWSER_LOGIN_ROOT", "TMUX_TMPDIR"):
            env[name] = env[name].replace(str(root.absolute()), str(final_root.absolute()))
    _write(
        root / "client.env",
        ("\n".join("export " + k + "=" + shlex.quote(v) for k, v in env.items()) + "\n").encode(),
    )


def write_browser_link(root: Path) -> Path:
    """Write an operator-only access link; fragments are not sent to HTTP servers."""
    config = _config(root)
    link = root / "web-link.txt"
    if config.get("browser_login", {}).get("enabled"):
        browser_auth_service(root)
        url = f"http://127.0.0.1:{config['server_port']}/\n"
    else:
        url = f"http://127.0.0.1:{config['server_port']}/#cao_token={mint_token(root)}\n"
    _write(link, url.encode())
    return link


def open_browser(root: Path):
    link = write_browser_link(root)
    url = link.read_text().strip()
    powershell = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    if powershell.is_file():
        subprocess.run(
            [
                str(powershell),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$url = [Console]::In.ReadToEnd().Trim(); Start-Process $url",
            ],
            input=url,
            text=True,
            check=True,
        )
    else:
        import webbrowser

        if not webbrowser.open(url):
            raise RuntimeError(f"open the private access link in {link}")


@contextmanager
def _lock(root: Path, *, name="service.lock"):
    _private(root, directory=True)
    path = root / name
    if path.is_symlink():
        raise ValueError("private service lock required")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                "stop personal service before backup or duplicate startup"
                if name == "service.lock"
                else "another account operation is in progress"
            ) from error
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _copy_state(source: Path, destination: Path):
    destination.mkdir(mode=0o700)
    for entry in source.iterdir():
        if entry.name.endswith(("-wal", "-shm")) or entry.name in ("service.lock", "account.lock"):
            continue
        if entry.is_symlink():
            raise ValueError("backup refuses symbolic links")
        target = destination / entry.name
        if entry.is_dir():
            _copy_state(entry, target)
        elif entry.is_file():
            if entry.suffix in (".db", ".sqlite", ".sqlite3"):
                with (
                    sqlite3.connect(entry.as_uri() + "?mode=ro", uri=True) as reader,
                    sqlite3.connect(target) as writer,
                ):
                    reader.backup(writer)
                    if writer.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                        raise RuntimeError("SQLite backup integrity failed")
                target.chmod(0o600)
            else:
                _write(target, entry.read_bytes())
        else:
            raise ValueError("backup refuses sockets/devices")


def backup(root: Path, destination: Path):
    root = root.absolute()
    destination = destination.absolute()
    _config(root)
    if destination.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(
        destination.resolve()
    ):
        raise ValueError("backup must be outside deployment")
    if destination.exists():
        raise FileExistsError(destination)
    with _lock(root), _lock(root, name="account.lock"):
        staging = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            _copy_state(root, staging)
            _config(staging)
            os.rename(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def restore(source: Path, destination: Path):
    _config(source)
    if destination.exists():
        raise FileExistsError(destination)
    source, destination = source.absolute(), destination.absolute()
    if destination.resolve().is_relative_to(source.resolve()) or source.resolve().is_relative_to(
        destination.resolve()
    ):
        raise ValueError("restore must be outside source deployment")
    with _lock(source), _lock(source, name="account.lock"):
        staging = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            _copy_state(source, staging)
            config = _config(staging)
            if "browser_login" in config:
                browser_auth_service(staging, validate=False).invalidate_all("restore")
            renew_client(staging, final_root=destination)
            os.rename(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def service_unit(root: Path):
    _config(root)

    # systemd command words use double quotes, not shell quoting; escape specifiers.
    def word(value):
        if any(char in str(value) for char in ("\n", "\r", "\x00")):
            raise ValueError("invalid systemd command value")
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'

    command = " ".join(
        map(word, (sys.executable, Path(__file__).absolute(), "--root", root.absolute(), "serve"))
    )
    return (
        "[Unit]\nDescription=CAO personal localhost service\nAfter=default.target\n\n[Service]\nType=simple\nExecStart="
        + command
        + "\nEnvironment="
        + word("PATH=" + str(Path(sys.executable).parent) + ":" + os.environ.get("PATH", ""))
        + "\nRestart=on-failure\nRestartSec=5\nKillMode=control-group\nTimeoutStopSec=30\nUMask=0077\n\n[Install]\nWantedBy=default.target\n"
    )


def configure_personal_scheduler(repository):
    """Explicit one-worker policy; preserve operator changes across restart."""
    from cli_agent_orchestrator.services.work_scheduler import WorkScheduler

    repository.verify_schema()
    with repository.read_snapshot() as connection:
        configured = connection.execute(
            "SELECT revision FROM work_scheduler_policy WHERE singleton=1"
        ).fetchone()
    if configured is None:
        WorkScheduler(repository).configure(
            capacity=1, max_queue=8, aging_seconds=10, expected_policy_revision=0
        )


def provision(root: Path, *, worker: Path, checkout: Path, selection: str, project_id: str):
    """Publish a bounded static launch contract for the signature-verified operator."""
    env = environment(root)
    os.environ.update({key: value for key, value in env.items() if key.startswith("CAO_")})
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.constants import DATABASE_FILE
    from cli_agent_orchestrator.security.auth import principal_from_token

    principal = principal_from_token(env["CAO_AUTH_LOCAL_TOKEN"])
    path = Path(__file__).with_name("local_work_docker_demo.py")
    spec = importlib.util.spec_from_file_location("personal_static_provision", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.provision_local_static_work(
        WorkRepository(DATABASE_FILE),
        principal,
        selection=selection,
        project_id=project_id,
        checkout_root=checkout,
        worker_bytes=worker.read_bytes(),
    )


def serve(root: Path):
    config = _config(root)
    environment(root)  # Validate browser storage/binding before opening any listener.
    with _lock(root):
        lifetime = config.get("mcp_token_lifetime_seconds", 3600)
        expires_at = publish_local_token(root, lifetime=lifetime)
        renewal_stop = threading.Event()
        renewal_thread = None
        payload = json.dumps(jwks(root)).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/jwks.json":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        issuer = ThreadingHTTPServer(("127.0.0.1", config["jwks_port"]), Handler)
        issuer.daemon_threads = True
        thread = threading.Thread(target=issuer.serve_forever, daemon=True)
        thread.start()
        env = os.environ.copy()
        # Explicit personal settings override ambient auth and remote Docker config.
        env.update(environment(root))
        env.pop("AUTH0_DOMAIN", None)
        env["CAO_AUTH_LOCAL_TOKEN"] = mint_token(root, lifetime=86400)
        env["DOCKER_HOST"] = "unix:///var/run/docker.sock"
        for name in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
            env.pop(name, None)
        env["PATH"] = str(Path(sys.executable).parent) + ":" + env.get("PATH", "")
        runtime = root.with_name(root.name + "-sockets")
        runtime.mkdir(mode=0o700, exist_ok=True)
        _private(runtime, directory=True)
        env["TMUX_TMPDIR"] = str(runtime)
        # This process imports CAO only after the durable home is selected.
        os.environ.update({k: v for k, v in env.items() if k.startswith("CAO_")})
        from cli_agent_orchestrator.clients.database import init_db
        from cli_agent_orchestrator.clients.work_repository import WorkRepository
        from cli_agent_orchestrator.constants import DATABASE_FILE

        init_db()
        configure_personal_scheduler(WorkRepository(DATABASE_FILE))
        renew_client(root)
        process = None
        stopping = False

        def stop(_signum, _frame):
            nonlocal stopping
            stopping = True
            if process is not None and process.poll() is None:
                process.terminate()

        original = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            process = subprocess.Popen(
                [
                    str(Path(sys.executable).parent / "cao-server"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(config["server_port"]),
                    "--terminal",
                    "tmux",
                ],
                env=env,
            )
            renewal_thread = threading.Thread(
                target=rotate_local_tokens, args=(root, renewal_stop, expires_at),
                kwargs={"lifetime": lifetime}, name="cao-local-token-renewal", daemon=True,
            )
            renewal_thread.start()
            code = process.wait()
            if code and not stopping:
                raise RuntimeError(f"personal server exited with code {code}")
        finally:
            renewal_stop.set()
            if renewal_thread is not None:
                renewal_thread.join(timeout=2)
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            issuer.shutdown()
            issuer.server_close()
            thread.join(timeout=2)
            for sig, handler in original.items():
                signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / ".local/share/cao-personal")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--image-id", required=True)
    init.add_argument("--port", type=int, default=9889)
    init.add_argument("--jwks-port", type=int, default=9890)
    sub.add_parser("serve")
    sub.add_parser("renew")
    sub.add_parser("web")
    sub.add_parser("unit")
    sub.add_parser("account-create").add_argument("--username", required=True)
    sub.add_parser("account-reset")
    sub.add_parser("account-disable")
    static = sub.add_parser("provision")
    static.add_argument("--worker", type=Path, required=True)
    static.add_argument("--checkout", type=Path, required=True)
    static.add_argument("--selection", default="personal-docker")
    static.add_argument("--project", default="personal-docker")
    for name in ("backup", "restore"):
        sub.add_parser(name).add_argument("path", type=Path)
    args = parser.parse_args()
    root = args.root.absolute()
    if args.command == "init":
        initialize(root, server_port=args.port, jwks_port=args.jwks_port, image_id=args.image_id)
    elif args.command == "account-create":
        account_create(root, username=args.username)
    elif args.command == "account-reset":
        account_reset(root)
    elif args.command == "account-disable":
        account_disable(root)
    elif args.command == "serve":
        serve(root)
    elif args.command == "renew":
        renew_client(root)
    elif args.command == "web":
        open_browser(root)
    elif args.command == "unit":
        print(service_unit(root), end="")
    elif args.command == "provision":
        reference = provision(
            root,
            worker=args.worker,
            checkout=args.checkout,
            selection=args.selection,
            project_id=args.project,
        )
        print(f"provisioned {reference.selector} revision {reference.revision}")
    elif args.command == "backup":
        backup(root, args.path)
    elif args.command == "restore":
        restore(args.path, root)


if __name__ == "__main__":
    main()
