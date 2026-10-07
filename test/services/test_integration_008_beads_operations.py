"""Durable external writes never replay an uncertain create."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.clients.beads import BeadsError, Task
from cli_agent_orchestrator.services import beads_service as service


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'metadata.sqlite'}", connect_args={"check_same_thread": False}
    )
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr("cli_agent_orchestrator.constants.LOCK_DIR", tmp_path / "locks")
    root = tmp_path / "workspace"
    root.mkdir()
    info = root.stat()
    yield service.BeadsWorkspace("repo", root, "legacy", (info.st_dev, info.st_ino), "r" * 64)
    engine.dispose()


class Adapter:
    def __init__(self):
        self.calls = 0

    def add(self, title, description="", priority=2, **kwargs):
        self.calls += 1
        return Task("task-1", title, description, priority, labels=kwargs.get("labels", []))


def test_concurrent_create_exact_replay_and_changed_key_conflict(workspace):
    adapter = Adapter()
    payload = {"title": "one task", "description": "exact", "priority": 2}
    with patch.object(service, "client_for", return_value=(workspace, adapter)):

        def mutate(_):
            return service.metadata_operation(
                "repo", owner="u", operation_key="create-key-1", action="create", values=payload
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(mutate, range(4)))
        assert all(row == rows[0] for row in rows) and adapter.calls == 1
        with pytest.raises(service.BeadsConflict):
            service.metadata_operation(
                "repo",
                owner="u",
                operation_key="create-key-1",
                action="create",
                values={**payload, "title": "changed"},
            )


def test_uncertain_create_is_retained_never_automatically_repeated(workspace):
    adapter = Adapter()

    def unknown(*args, **kwargs):
        adapter.calls += 1
        raise BeadsError("beads_timeout", uncertain=True)

    adapter.add = unknown
    with patch.object(service, "client_for", return_value=(workspace, adapter)):
        args = dict(
            owner="u", operation_key="create-key-2", action="create", values={"title": "one"}
        )
        first = service.metadata_operation("repo", **args)
        second = service.metadata_operation("repo", **args)
    assert first == second and first["state"] == "uncertain" and adapter.calls == 1


def test_stale_metadata_hash_refuses_before_process_write(workspace):
    adapter = Adapter()
    adapter.get = lambda _key: Task("task-1", "actual")
    with patch.object(service, "client_for", return_value=(workspace, adapter)):
        with pytest.raises(service.BeadsConflict):
            service.metadata_operation(
                "repo",
                owner="u",
                operation_key="update-key-1",
                action="update",
                task_id="task-1",
                expected_hash="0" * 64,
                values={"title": "change"},
            )
    assert adapter.calls == 0


def test_multiline_preview_no_external_writes_and_draft_identity():
    result = service.decompose_preview("- first\n2. second")
    assert [row["title"] for row in result["tasks"]] == ["first", "second"]
    assert len(result["draft_hash"]) == 64
    with pytest.raises(ValueError):
        service.decompose_preview("x\n" * 33)


def test_invalid_metadata_is_refused_before_claim_or_process(workspace):
    adapter = Adapter()
    with patch.object(service, "client_for", return_value=(workspace, adapter)):
        for values in (
            {"title": "ok", "priority": True},
            {"title": "ok", "labels": ["a,b"]},
            {"title": ""},
        ):
            with pytest.raises(ValueError):
                service.metadata_operation(
                    "repo", owner="u", operation_key="invalid-key-1", action="create", values=values
                )
    assert adapter.calls == 0
    with db.SessionLocal() as session:
        assert session.query(db.BeadsOperationModel).count() == 0


def test_epic_bulk_resume_does_not_duplicate_after_partial_response(workspace):
    adapter = Adapter()
    created = []

    def add(title, description="", priority=2, **kwargs):
        adapter.calls += 1
        if title == "second":
            raise BeadsError("beads_timeout", uncertain=True)
        task = Task(
            "task-" + str(adapter.calls),
            title,
            description,
            priority,
            labels=kwargs.get("labels", []),
        )
        created.append(task)
        return task

    adapter.add = add
    adapter.get = lambda key: next((task for task in created if task.id == key), None)
    draft = service.decompose_preview("first\nsecond")
    args = dict(
        owner="u",
        operation_key="epic-key-1",
        tasks=draft["tasks"],
        draft_hash=draft["draft_hash"],
        epic={"title": "epic"},
    )
    with patch.object(service, "client_for", return_value=(workspace, adapter)):
        first = service.bulk_create("repo", **args)
        again = service.bulk_create("repo", **args)
        assert first == again and first["state"] == "partial"
        assert adapter.calls == 3
        with pytest.raises(service.BeadsConflict):
            service.bulk_create("repo", **{**args, "epic": {"title": "different"}})
        assert adapter.calls == 3
