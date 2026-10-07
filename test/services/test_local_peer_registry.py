"""Live process registry retains and repairs verified local identities."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import psutil
from starlette.requests import Request

from cli_agent_orchestrator.api import local_coordination_routes
from cli_agent_orchestrator.services import local_peer_registry, local_peer_service
from cli_agent_orchestrator.services.local_peer_identity import LocalProcessIdentity


def _configure_registry(monkeypatch, tmp_path):
    directory = tmp_path / "local-peers"
    monkeypatch.setattr(local_peer_registry, "LOCAL_PEER_DIR", directory)
    monkeypatch.setattr(
        local_peer_registry, "LOCAL_PEER_REGISTRY_FILE", directory / "registry.sqlite3"
    )


class _IdentityHandler(BaseHTTPRequestHandler):
    identity: dict[str, object] = {}

    def do_GET(self):
        identity = self.identity() if callable(self.identity) else self.identity
        body = json.dumps(identity).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def _identity(port: int, *, pid: int | None = None, started_at: float | None = None):
    return LocalProcessIdentity(
        instance_id=str(uuid4()),
        process_generation=str(uuid4()),
        pid=pid or os.getpid(),
        process_started_at=(
            started_at if started_at is not None else float(psutil.Process().create_time())
        ),
        display_name="registry test",
        loopback_host="127.0.0.1",
        loopback_port=port,
        public_key="a" * 43,
    )


def test_live_identity_endpoint_prevents_false_stale_prune(monkeypatch, tmp_path):
    _configure_registry(monkeypatch, tmp_path)
    handler = type(
        "IdentityHandler",
        (_IdentityHandler,),
        {"identity": {}},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        identity = _identity(server.server_port)
        monkeypatch.setattr(local_peer_service, "current_process_identity", lambda: identity)
        handler.identity = staticmethod(lambda: local_peer_service.local_identity())
        local_peer_registry.register_instance(identity)
        connection = local_peer_registry._connect()
        try:
            connection.execute(
                "UPDATE local_peer_processes SET process_started_at = process_started_at + 5 "
                "WHERE instance_id = ?",
                (identity.instance_id,),
            )
            connection.commit()
        finally:
            connection.close()
        monkeypatch.setattr(local_peer_registry, "_process_is_same_live_instance", lambda *_: False)

        records = local_peer_registry.list_instances()

        assert [record.instance_id for record in records] == [identity.instance_id]
        assert records[0].process_started_at == identity.process_started_at
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_local_identity_repairs_a_missing_registry_row(monkeypatch, tmp_path):
    _configure_registry(monkeypatch, tmp_path)
    identity = _identity(19888)
    monkeypatch.setattr(local_peer_service, "current_process_identity", lambda: identity)

    public_identity = local_peer_service.local_identity()

    assert public_identity["instance_id"] == identity.instance_id
    record = local_peer_registry.get_instance(identity.instance_id)
    assert record is not None
    assert record.process_generation == identity.process_generation


def test_identity_route_returns_registered_identity_for_loopback_request(monkeypatch, tmp_path):
    _configure_registry(monkeypatch, tmp_path)
    identity = _identity(19889)
    monkeypatch.setattr(local_peer_service, "current_process_identity", lambda: identity)
    request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/local-coordination/identity",
            "raw_path": b"/local-coordination/identity",
            "query_string": b"",
            "headers": [(b"host", b"127.0.0.1:19889")],
            "client": ("127.0.0.1", 43210),
            "server": ("127.0.0.1", 19889),
            "root_path": "",
        }
    )

    response = asyncio.run(local_coordination_routes.local_peer_identity(request))

    assert response["instance_id"] == identity.instance_id
    record = local_peer_registry.get_instance(identity.instance_id)
    assert record is not None
    assert record.process_generation == identity.process_generation
