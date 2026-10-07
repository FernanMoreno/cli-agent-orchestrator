import json
import shutil
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.services import memory_service


@pytest.fixture
def marker_store(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path/'marker.sqlite'}", connect_args={"check_same_thread": False}
    )
    database.Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr("cli_agent_orchestrator.constants.LOCK_DIR", tmp_path / "locks")
    monkeypatch.setenv("CAO_MEMORY_PROJECT_MARKER", "true")
    with (
        patch.object(memory_service, "_read_project_id_override", return_value=None),
        patch.object(memory_service, "_git_remote_identity", return_value=None),
        patch.object(memory_service, "_record_alias_safe"),
    ):
        yield
    engine.dispose()


def test_marker_non_git_rename_keeps_identity_copy_is_distinct(tmp_path, marker_store):
    before = tmp_path / "before"
    before.mkdir()
    original = memory_service.resolve_project_id(before)
    copy = tmp_path / "copy"
    shutil.copytree(before, copy)
    assert memory_service.resolve_project_id(copy) != original
    after = tmp_path / "after"
    before.rename(after)
    assert memory_service.resolve_project_id(after) == original
    with database.SessionLocal() as db:
        assert db.query(database.ProjectMarkerModel).count() == 2


def test_corrupt_and_unknown_markers_are_readonly_fallback(tmp_path, marker_store):
    import hashlib

    root = tmp_path / "root"
    root.mkdir()
    directory = root / ".cao"
    directory.mkdir()
    path = directory / "project_id"
    path.write_text("{bad")
    expected = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
    assert memory_service.resolve_project_id(root) == expected and path.read_text() == "{bad"
    hostile = json.dumps({"project_id": "a" * 12, "nonce": "b" * 32, "version": 1})
    path.write_text(hostile)
    assert memory_service.resolve_project_id(root) == expected and path.read_text() == hostile
    with database.SessionLocal() as db:
        assert db.query(database.ProjectMarkerModel).count() == 0


def test_marker_disabled_and_explicit_override_do_not_write(tmp_path, marker_store, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("CAO_MEMORY_PROJECT_MARKER", "false")
    memory_service.resolve_project_id(root)
    assert not (root / ".cao").exists()
    monkeypatch.setenv("CAO_MEMORY_PROJECT_MARKER", "true")
    with patch.object(memory_service, "_read_project_id_override", return_value="chosen"):
        assert memory_service.resolve_project_id(root) == "chosen"
    assert not (root / ".cao").exists()
