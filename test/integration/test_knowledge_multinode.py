"""Two local HTTP processes coordinate through one temporary SQLite authority.

This checks process and store boundaries on one host. It does not simulate HA,
consensus, replicated storage, or a network partition between replicated nodes.
"""

from __future__ import annotations

import multiprocessing
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import requests

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority


@pytest.fixture
def inherited_auth_environment(monkeypatch):
    """Model an auth-enabled parent process inherited by both local test nodes."""
    monkeypatch.setenv("AUTH0_DOMAIN", "example.invalid")
    monkeypatch.setenv("CAO_AUTH_JWKS_URI", "https://example.invalid/jwks")


def _clear_local_auth_environment() -> None:
    """Keep both the parent setup and forked HTTP nodes independent of inherited IdP config."""
    for name in ("AUTH0_DOMAIN", "CAO_AUTH_JWKS_URI"):
        os.environ.pop(name, None)


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _serve_knowledge_http(database_path: str, port: int) -> None:
    """Serve the production knowledge router in its own local process."""
    _clear_local_auth_environment()

    import uvicorn
    from fastapi import FastAPI

    from cli_agent_orchestrator.api import knowledge_routes

    app = FastAPI()
    app.include_router(knowledge_routes.router)
    app.dependency_overrides[knowledge_routes.repository] = lambda: WorkRepository(database_path)
    uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            log_config=None,
        )
    ).run()


def _start_node(database_path: Path) -> tuple[multiprocessing.Process, str]:
    port = _free_port()
    process = multiprocessing.get_context("fork").Process(
        target=_serve_knowledge_http,
        args=(str(database_path), port),
        name=f"knowledge-http-{port}",
    )
    process.start()
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.exitcode is not None:
            process.join(timeout=0)
            raise RuntimeError(f"knowledge HTTP process exited with {process.exitcode}")
        try:
            response = requests.get(f"{url}/openapi.json", timeout=0.5)
            if response.status_code == 200:
                return process, url
        except requests.RequestException:
            time.sleep(0.05)
    _stop_node(process)
    raise RuntimeError("knowledge HTTP process did not become ready within 15 seconds")


def _stop_node(process: multiprocessing.Process) -> None:
    if process.is_alive():
        process.terminate()
    process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)


def _selectors(job_id: str) -> dict[str, str | int]:
    return {"job_id": job_id, "grant_id": "grant", "grant_revision": 1}


def _proposal(expected_version: int, content: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "scope": "project",
        "scope_id": "project",
        "expected_version": expected_version,
        "content": content,
        "confidence": 0.8,
        "fresh_until": time.time() + 600,
    }


def _post_proposal(url: str, selectors: dict[str, str | int], body: dict[str, object]):
    return requests.post(
        f"{url}/v1/knowledge/records/record/revisions",
        params=selectors,
        json=body,
        timeout=(2, 12),
    )


def _stored_revisions(repository: WorkRepository) -> tuple[int, list[tuple[int, str]]]:
    with repository.read_snapshot() as connection:
        head = connection.execute(
            "SELECT version FROM work_knowledge_records WHERE id='record'"
        ).fetchone()
        revisions = connection.execute(
            "SELECT revision,content FROM work_knowledge_revisions "
            "WHERE record_id='record' ORDER BY revision"
        ).fetchall()
    return head[0], [(row[0], row[1]) for row in revisions]


def test_two_processes_share_sqlite_authority_across_conflict_and_reconnect(
    tmp_path, inherited_auth_environment, monkeypatch
):
    monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
    monkeypatch.delenv("CAO_AUTH_JWKS_URI", raising=False)
    assert not auth.is_auth_enabled()
    database_path = tmp_path / "authority" / "work.sqlite3"
    database_path.parent.mkdir()
    repository = WorkRepository(database_path)
    repository.initialize()
    principal = auth.local_operator_principal()
    job = repository.create_job(
        project_id="project",
        principal_id=principal.id,
        allowed_providers=["local-test"],
        grant_id="grant",
    )
    WorkAuthority(repository).issue_root(
        principal,
        job_id=job["id"],
        providers={"local-test"},
        permissions=Permissions(tools={"knowledge.read", "knowledge.propose"}),
        expires_at=time.time() + 600,
    )
    selectors = _selectors(job["id"])

    nodes: list[multiprocessing.Process] = []
    try:
        node_a, url_a = _start_node(database_path)
        nodes.append(node_a)
        node_b, url_b = _start_node(database_path)
        nodes.append(node_b)
        assert node_a.pid != node_b.pid

        start_together = threading.Barrier(2)

        def submit(url: str, content: str):
            start_together.wait(timeout=5)
            return _post_proposal(url, selectors, _proposal(0, content))

        with ThreadPoolExecutor(max_workers=2) as pool:
            proposals = [
                pool.submit(submit, url_a, "proposal from process A"),
                pool.submit(submit, url_b, "proposal from process B"),
            ]
            responses = [proposal.result(timeout=15) for proposal in proposals]

        assert sorted(response.status_code for response in responses) == [201, 409]
        assert _stored_revisions(repository)[0] == 1

        _stop_node(node_b)
        assert not node_b.is_alive()

        advanced = _post_proposal(
            url_a, selectors, _proposal(1, "durable while process B is unavailable")
        )
        assert advanced.status_code == 201, advanced.text

        with pytest.raises(requests.ConnectionError):
            _post_proposal(url_b, selectors, _proposal(1, "unreachable process cannot commit"))

        winner_content = next(
            response.json()["content"] for response in responses if response.status_code == 201
        )
        assert _stored_revisions(repository) == (
            2,
            [(1, winner_content), (2, "durable while process B is unavailable")],
        )

        reconnected_b, reconnected_url = _start_node(database_path)
        nodes.append(reconnected_b)
        stale = _post_proposal(
            reconnected_url, selectors, _proposal(1, "unreachable process cannot commit")
        )
        assert stale.status_code == 409, stale.text
        current = requests.get(
            f"{reconnected_url}/v1/knowledge/records/record",
            params=selectors,
            timeout=(2, 12),
        )
        assert current.status_code == 200, current.text
        assert current.json()["record_version"] == 2
        assert current.json()["content"] == "durable while process B is unavailable"
    finally:
        for process in nodes:
            _stop_node(process)
