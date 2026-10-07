#!/usr/bin/env python3
"""Supervise the existing personal API/issuer and its container-local proxy."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def proxy_config(server_port: int) -> str:
    if type(server_port) is not int or not 1024 <= server_port <= 65535:
        raise ValueError("invalid personal API port")
    return f"""pid /tmp/cao-nginx/nginx.pid;
error_log /dev/null crit;
worker_processes 1;
events {{ worker_connections 1024; }}
http {{
  access_log off;
  client_body_temp_path /tmp/cao-nginx/body;
  proxy_temp_path /tmp/cao-nginx/proxy;
  fastcgi_temp_path /tmp/cao-nginx/fastcgi;
  uwsgi_temp_path /tmp/cao-nginx/uwsgi;
  scgi_temp_path /tmp/cao-nginx/scgi;
  map $http_upgrade $connection_upgrade {{ default upgrade; '' close; }}
  server {{
    listen 8080;
    client_max_body_size 32m;
    location / {{
      proxy_pass http://127.0.0.1:{server_port};
      proxy_http_version 1.1;
      proxy_set_header Host $http_host;
      proxy_set_header Forwarded "";
      proxy_set_header X-Forwarded-For "";
      proxy_set_header X-Real-IP "";
      proxy_set_header X-Forwarded-Proto "";
      proxy_set_header X-Forwarded-Host "";
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection $connection_upgrade;
      proxy_buffering off;
      proxy_request_buffering off;
      proxy_read_timeout 3600s;
      proxy_send_timeout 3600s;
    }}
  }}
}}
"""


def healthcheck(root: Path) -> None:
    config = json.loads((root / "deployment.json").read_text())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for path in ("/", "/health", "/auth/config"):
        request = urllib.request.Request(
            "http://127.0.0.1:8080" + path,
            headers={"Host": f"127.0.0.1:{config['server_port']}"},
        )
        with opener.open(request, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError("application not healthy")
            if path == "/" and b"<html" not in response.read().lower():
                raise RuntimeError("bundled frontend is missing")


def check_mcp_integrations() -> None:
    """Exercise configured host-dependent MCP executables without logging credentials."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10 uses the declared runtime dependency.
        import tomli as tomllib

    config = Path.home() / ".codex/config.toml"
    if not config.exists():
        return
    servers = tomllib.loads(config.read_text()).get("mcp_servers", {})
    for server in servers.values():
        arguments = server.get("args", [])
        if server.get("command") == "cmd.exe":
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "cao-installation-check", "version": "1"},
                },
            }
            result = subprocess.run(
                ["cmd.exe", *arguments],
                input=(json.dumps(request) + "\n").encode(),
                capture_output=True,
                timeout=25,
                check=False,
            )
            replies = [
                json.loads(line)
                for line in result.stdout.decode().splitlines()
                if line.startswith("{")
            ]
            if not any(reply.get("id") == 1 and "result" in reply for reply in replies):
                raise RuntimeError("Windows MCP initialization failed")
        if "--executable-path" in arguments and any("playwright" in str(a) for a in arguments):
            browser = arguments[arguments.index("--executable-path") + 1]
            env = {**os.environ, **server.get("env", {})}
            result = subprocess.run(
                [browser, "--headless", "--no-sandbox", "--dump-dom", "about:blank"],
                env=env,
                capture_output=True,
                timeout=25,
                check=True,
            )
            if b"<html" not in result.stdout:
                raise RuntimeError("configured Playwright browser failed")


def serve(root: Path) -> None:
    config = json.loads((root / "deployment.json").read_text())
    runtime = Path("/tmp/cao-nginx")
    runtime.mkdir(mode=0o700, exist_ok=True)
    nginx_config = runtime / "nginx.conf"
    nginx_config.write_text(proxy_config(config["server_port"]))
    children = []
    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    original = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        children.append(
            subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name("personal_deployment.py")),
                    "--root",
                    str(root),
                    "serve",
                ]
            )
        )
        children.append(subprocess.Popen(["nginx", "-c", str(nginx_config), "-g", "daemon off;"]))
        # The personal issuer renews the MCP credential without stopping agents.
        while not stopping:
            if any(child.poll() is not None for child in children):
                raise RuntimeError("a required application process exited")
            time.sleep(0.2)
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=25)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for sig, handler in original.items():
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("serve", "healthcheck"), default="serve")
    parser.add_argument(
        "--root", type=Path, default=Path(os.environ.get("CAO_PERSONAL_ROOT", "/data"))
    )
    args = parser.parse_args()
    # _config validates the sealed browser binding and imports constants early.
    # Select the durable CAO state before personal_deployment imports those modules.
    os.environ["CAO_HOME_DIR"] = str(args.root / "cao")
    (healthcheck if args.command == "healthcheck" else serve)(args.root)


if __name__ == "__main__":
    main()
