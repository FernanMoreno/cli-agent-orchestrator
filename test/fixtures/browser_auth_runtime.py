"""Disposable authenticated runtime for Playwright; controls travel over stdin only.

No test clock, reset, or restart endpoint is added to the production HTTP API.
The child owns private SQLite files, a real local RSA JWKS issuer, and uvicorn.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import shutil
import sys
import subprocess
import uuid
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PASSWORD = "Fixture-password-484!"
USERNAME = "operator"


def main():
    import uvicorn

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
        os.environ.update(
            {
                "CAO_HOME_DIR": str(workspace / "cao"),
                "TMUX_TMPDIR": str(workspace),
                "CAO_A2A_DISABLED": "true",
                "OTEL_SDK_DISABLED": "true",
                "CAO_AGUI_ENABLED": "true",
            }
        )
        for key in ("AUTH0_DOMAIN", "AUTH0_AUDIENCE", "CAO_BROWSER_LOGIN_ROOT", "TMUX"):
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
            service = BrowserAuthService(
                root / "browser-auth.sqlite3", binding, policy, clock=lambda: controlled[0]
            )
            if not resume:
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
                    personal.restore(archive, restored)
                    root = restored
                    service = BrowserAuthService(
                        root / "browser-auth.sqlite3", binding, policy, clock=lambda: controlled[0]
                    )
                    app.state.browser_auth = service
                    listener = socket.socket()
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    listener.bind(("127.0.0.1", port))
                    listener.listen(128)
                    start()
                elif action == "lock_storage":
                    database_lock = sqlite3.connect(
                        root / "browser-auth.sqlite3", isolation_level=None
                    )
                    database_lock.execute("BEGIN IMMEDIATE")
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
            if resume:
                shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    main()
