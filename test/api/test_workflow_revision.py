"""Conditional workflow editing uses exact source revisions, never last-writer-wins."""

import hashlib
import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import DatabaseTarget, connect
from cli_agent_orchestrator.services import workflow_spec_service as service
from cli_agent_orchestrator.utils import atomic_file

SOURCE = """name: revision
description: original
mode: sequential
steps:
  - id: first
    provider: claude_code
    agent: developer
    prompt: original
"""


@pytest.fixture
def workflow(tmp_path, monkeypatch, isolated_memory_db):
    directory = tmp_path / "workflows"
    directory.mkdir()
    monkeypatch.setattr(service, "WORKFLOW_SPEC_DIR", directory)
    monkeypatch.setattr(atomic_file, "LOCK_DIR", tmp_path / "locks")
    path = directory / "revision.yaml"
    path.write_text(SOURCE)
    return path


def test_source_revision_and_update_preserve_legacy_get(client, workflow):
    path = workflow
    legacy = client.get("/workflows/revision").json()
    response = client.get("/workflows/revision/source")
    assert response.status_code == 200
    source = response.json()
    assert source["content"] == SOURCE
    assert source["source_hash"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert client.get("/workflows/revision").json() == legacy
    updated = SOURCE.replace("original", "changed")
    response = client.put(
        "/workflows/revision",
        json={
            "expected_source_hash": source["source_hash"],
            "content": updated,
        },
    )
    assert response.status_code == 200
    assert path.read_text() == updated
    assert response.json()["source_hash"] == hashlib.sha256(updated.encode()).hexdigest()
    assert client.get("/workflows/revision").json()["description"] == "changed"


def test_stale_editor_cannot_overwrite_winner(client, workflow):
    path = workflow
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    first = client.put(
        "/workflows/revision",
        json={
            "expected_source_hash": original_hash,
            "content": SOURCE.replace("original", "winner"),
        },
    )
    assert first.status_code == 200
    second = client.put(
        "/workflows/revision",
        json={
            "expected_source_hash": original_hash,
            "content": SOURCE.replace("original", "loser"),
        },
    )
    assert second.status_code == 409
    assert "winner" in path.read_text()
    with connect(DatabaseTarget.from_settings(scope="main")) as connection:
        assert (
            connection.execute("SELECT description FROM workflow_index").fetchone()[0] == "winner"
        )


def test_concurrent_editors_share_one_compare_and_publish_boundary(workflow):
    path = workflow
    assert hasattr(service, "update_workflow"), "conditional authoring service missing"
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    barrier = threading.Barrier(2)

    def edit(description):
        barrier.wait(timeout=5)
        try:
            service.update_workflow("revision", SOURCE.replace("original", description), expected)
            return description
        except service.WorkflowRevisionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(edit, ["first", "second"]))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert path.read_text() == SOURCE.replace("original", winners[0])
    with connect(DatabaseTarget.from_settings(scope="main")) as connection:
        assert (
            connection.execute("SELECT description FROM workflow_index").fetchone()[0] == winners[0]
        )


@pytest.mark.parametrize(
    "content", ["not: a workflow", SOURCE.replace("name: revision", "name: other")]
)
def test_invalid_content_preserves_file_and_index(client, workflow, content):
    path = workflow
    service.rebuild_index_from_files()
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    response = client.put(
        "/workflows/revision",
        json={
            "expected_source_hash": expected,
            "content": content,
        },
    )
    assert response.status_code == 400
    assert path.read_text() == SOURCE
    with connect(DatabaseTarget.from_settings(scope="main")) as connection:
        assert connection.execute("SELECT name,description FROM workflow_index").fetchall() == [
            ("revision", "original")
        ]


def test_missing_revision_is_rejected_without_writing(client, workflow):
    path = workflow
    response = client.put("/workflows/revision", json={"content": SOURCE})
    assert response.status_code == 422
    assert path.read_text() == SOURCE


def test_index_failure_preserves_canonical_publication_for_rebuild(workflow):
    path = workflow
    assert hasattr(service, "update_workflow"), "conditional authoring service missing"
    service.rebuild_index_from_files()
    target = DatabaseTarget.from_settings(scope="main")
    with connect(target.migration_target()) as connection:
        connection.execute(
            sql.SQL(
                "CREATE FUNCTION {}.fail_changed_workflow_index() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "IF NEW.description = 'changed' THEN "
                "RAISE EXCEPTION 'index unavailable' USING ERRCODE = 'XX000'; "
                "END IF; RETURN NEW; END $$"
            ).format(sql.Identifier(target.schema))
        )
        connection.execute(
            sql.SQL(
                "CREATE TRIGGER fail_changed BEFORE INSERT ON {}.workflow_index "
                "FOR EACH ROW EXECUTE FUNCTION {}.fail_changed_workflow_index()"
            ).format(sql.Identifier(target.schema), sql.Identifier(target.schema))
        )
    try:
        changed = SOURCE.replace("original", "changed")
        result = service.update_workflow(
            "revision", changed, hashlib.sha256(path.read_bytes()).hexdigest()
        )
        assert result["source_hash"] == hashlib.sha256(changed.encode()).hexdigest()
        assert path.read_text() == changed
        with connect(target) as connection:
            assert (
                connection.execute("SELECT description FROM workflow_index").fetchone()[0]
                == "original"
            )
    finally:
        with connect(target.migration_target()) as connection:
            connection.execute(
                sql.SQL("DROP TRIGGER IF EXISTS fail_changed ON {}.workflow_index").format(
                    sql.Identifier(target.schema)
                )
            )
            connection.execute(
                sql.SQL("DROP FUNCTION IF EXISTS {}.fail_changed_workflow_index()").format(
                    sql.Identifier(target.schema)
                )
            )
    service.rebuild_index_from_files()
    with connect(target) as connection:
        assert (
            connection.execute("SELECT description FROM workflow_index").fetchone()[0] == "changed"
        )


def test_source_hash_preserves_crlf_bytes(client, workflow):
    path = workflow
    raw = SOURCE.replace("\n", "\r\n").encode()
    path.write_bytes(raw)
    response = client.get("/workflows/revision/source")
    assert response.status_code == 200
    assert response.json()["source_hash"] == hashlib.sha256(raw).hexdigest()
    assert response.json()["content"].encode() == raw


def _edit_in_process(content, expected, barrier, results):
    barrier.wait(timeout=10)
    try:
        service.update_workflow("revision", content, expected)
        results.put("updated")
    except service.WorkflowRevisionConflict:
        results.put("conflict")


def test_separate_processes_cannot_both_accept_the_same_source(workflow):
    path = workflow
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    context = multiprocessing.get_context("fork")
    barrier, results = context.Barrier(2), context.Queue()
    processes = [
        context.Process(
            target=_edit_in_process,
            args=(
                SOURCE.replace("original", name),
                expected,
                barrier,
                results,
            ),
        )
        for name in ("first", "second")
    ]
    try:
        for process in processes:
            process.start()
        assert sorted(results.get(timeout=15) for _ in processes) == ["conflict", "updated"]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        results.close()


def test_script_update_is_validated_without_execution(client, workflow):
    yaml_path = workflow
    path = yaml_path.with_suffix(".py")
    yaml_path.unlink()
    path.write_text("INPUTS = {}\n")
    original = client.get("/workflows/revision/source")
    assert original.status_code == 200
    content = "INPUTS = {}\nraise RuntimeError('must not execute while editing')\n"
    response = client.put(
        "/workflows/revision",
        json={
            "expected_source_hash": original.json()["source_hash"],
            "content": content,
        },
    )
    assert response.status_code == 200
    assert path.read_text() == content
    invalid = client.put(
        "/workflows/revision",
        json={
            "expected_source_hash": response.json()["source_hash"],
            "content": "def invalid(:",
        },
    )
    assert invalid.status_code == 400
    assert path.read_text() == content


def test_escaping_symlink_cannot_read_or_replace_outside_source(client, workflow):
    path = workflow
    outside = path.parent.parent / "outside.yaml"
    outside.write_text(SOURCE)
    path.unlink()
    path.symlink_to(outside)
    assert client.get("/workflows/revision/source").status_code == 404
    response = client.put(
        "/workflows/revision",
        json={
            "expected_source_hash": hashlib.sha256(outside.read_bytes()).hexdigest(),
            "content": SOURCE.replace("original", "changed"),
        },
    )
    assert response.status_code == 404
    assert outside.read_text() == SOURCE


def test_read_scope_cannot_publish_edits(client, workflow, monkeypatch):
    from cli_agent_orchestrator.api.main import app
    from cli_agent_orchestrator.security.auth import get_current_scopes

    path = workflow
    monkeypatch.setenv("CAO_AUTH_JWKS_URI", "https://auth.invalid/jwks")
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_scopes] = lambda: ["cao:read"]
    try:
        assert client.get("/workflows/revision/source").status_code == 200
        response = client.put(
            "/workflows/revision",
            json={
                "expected_source_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
                "content": SOURCE.replace("original", "changed"),
            },
        )
        assert response.status_code == 403
        assert path.read_text() == SOURCE
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
