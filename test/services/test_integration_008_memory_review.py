"""Independent isolated marker concurrency/authority and tar import acceptance."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from test.postgres.orm import configured_orm_engine

import frontmatter
import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.postgres import DatabaseTarget
from cli_agent_orchestrator.services import memory_service, project_marker


@pytest.fixture
def private_marker(tmp_path, monkeypatch):
    engine = configured_orm_engine()
    target = DatabaseTarget.from_settings()
    assert inspect(engine).has_table(
        database.ProjectMarkerModel.__tablename__, schema=target.schema
    )
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(constants, "LOCK_DIR", tmp_path / "locks")
    monkeypatch.setenv("CAO_MEMORY_PROJECT_MARKER", "true")
    monkeypatch.setattr(memory_service, "_read_project_id_override", lambda: None)
    monkeypatch.setattr(memory_service, "_git_remote_identity", lambda _: None)
    monkeypatch.setattr(memory_service, "_record_alias_safe", lambda *args: None)
    yield engine
    engine.dispose()


def test_marker_concurrent_resolution_publishes_one_private_identity(tmp_path, private_marker):
    root = tmp_path / "project"
    root.mkdir()
    with ThreadPoolExecutor(max_workers=8) as pool:
        identities = list(pool.map(memory_service.resolve_project_id, [root] * 16))
    assert len(set(identities)) == 1
    marker = root / ".cao" / "project_id"
    payload = json.loads(marker.read_text())
    assert payload["project_id"] == identities[0]
    assert marker.stat().st_mode & 0o777 == 0o600
    with database.SessionLocal() as db:
        assert db.query(database.ProjectMarkerModel).count() == 1


def test_marker_directory_swap_cannot_publish_outside_project(
    tmp_path, private_marker, monkeypatch
):
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    original = project_marker._read

    def swapped(directory_fd):
        directory = root / ".cao"
        directory.rmdir()
        directory.symlink_to(outside, target_is_directory=True)
        return original(directory_fd)

    monkeypatch.setattr(project_marker, "_read", swapped)
    assert project_marker.resolve(root) is None
    assert list(outside.iterdir()) == []
    with database.SessionLocal() as db:
        assert db.query(database.ProjectMarkerModel).count() == 0


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_marker_static_symlink_cannot_read_or_publish_external_identity(
    tmp_path, private_marker, kind
):
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    existing = outside / "project_id"
    existing.write_text("untouched")
    if kind == "directory":
        (root / ".cao").symlink_to(outside, target_is_directory=True)
    else:
        (root / ".cao").mkdir()
        (root / ".cao" / "project_id").symlink_to(existing)
    assert project_marker.resolve(root) is None
    assert existing.read_text() == "untouched"
    assert sorted(path.name for path in outside.iterdir()) == ["project_id"]


def test_git_identity_precedes_marker_without_filesystem_effect(
    tmp_path, private_marker, monkeypatch
):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(
        memory_service, "_git_remote_identity", lambda _: "https://example.test/org/repo.git"
    )
    assert memory_service.resolve_project_id(root) == memory_service._normalize_git_remote(
        "https://example.test/org/repo.git"
    )
    assert not (root / ".cao").exists()


def _current_tar(path, body, *, tags="portable", metadata=None):
    import io
    import tarfile

    frontmatter_values = {
        "type": "reference",
        "title": "note",
        "tags": tags,
        **(metadata or {}),
    }
    topic = frontmatter.dumps(frontmatter.Post(body, **frontmatter_values)).encode()
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("note.md")
        info.size = len(topic)
        archive.addfile(info, io.BytesIO(topic))


@pytest.fixture
def current_memory(tmp_path, monkeypatch):
    from cli_agent_orchestrator.services.memory_service import MemoryService
    from cli_agent_orchestrator.services.vault import binding

    engine = configured_orm_engine()
    database.Base.metadata.create_all(engine)
    monkeypatch.setattr(
        binding, "resolve", lambda scope, scope_id: binding.NativeBinding(scope, scope_id)
    )
    service = MemoryService(base_dir=tmp_path / "memory", db_engine=engine)
    yield service, engine
    engine.dispose()


def test_current_tar_federated_target_refuses_credentials(tmp_path, current_memory):
    from cli_agent_orchestrator.services.memory_archive.okf import OkfArchiveBackend

    service, _ = current_memory
    path = tmp_path / "federated.tar.gz"
    _current_tar(path, "Do not import: " + "AKIA" + "A" * 16)
    report = OkfArchiveBackend(service).import_bundle(path, "federated", "skip", False)
    assert report.imported == 0 and report.rejected == 1
    assert not service.get_wiki_path("federated", None, "note").exists()


def test_current_tar_cannot_write_a_vault_bound_target(tmp_path, current_memory, monkeypatch):
    from cli_agent_orchestrator.services.memory_archive.okf import OkfArchiveBackend
    from cli_agent_orchestrator.services.vault import binding
    from cli_agent_orchestrator.services.vault.config import FolderMapping

    service, engine = current_memory
    path = tmp_path / "vault.tar.gz"
    _current_tar(path, "A bounded fact.")
    monkeypatch.setattr(
        binding,
        "resolve",
        lambda scope, scope_id: binding.VaultBinding(
            scope,
            scope_id,
            "private",
            str(tmp_path / "vault"),
            FolderMapping(folder="shared", scope="global"),
        ),
    )
    report = OkfArchiveBackend(service).import_bundle(path, "global", "skip", False)
    assert report.imported == 0 and report.rejected == 1
    with engine.connect() as connection:
        assert connection.execute(database.MemoryMetadataModel.__table__.select()).fetchall() == []
    assert not (tmp_path / "vault").exists()


def test_untrusted_frontmatter_does_not_override_current_target_identity(
    tmp_path, current_memory, monkeypatch
):
    from cli_agent_orchestrator.services.memory_archive.okf import OkfArchiveBackend

    service, engine = current_memory
    path = tmp_path / "scoped.tar.gz"
    _current_tar(
        path,
        "An explicitly selected current-project fact.",
        metadata={
            "id": "987",
            "scope": "global",
            "scope_id": "untrusted-project",
            "file_path": "/untrusted/authority.md",
            "imported_from": "untrusted-origin",
        },
    )
    cwd = tmp_path / "current-project"
    cwd.mkdir()
    monkeypatch.setattr(
        memory_service, "_read_project_id_override", lambda: "selected-current-project"
    )
    monkeypatch.setattr(memory_service, "_record_alias_safe", lambda *args: None)
    report = OkfArchiveBackend(service).import_bundle(
        path, "project", "skip", False, {"cwd": str(cwd)}
    )
    assert report.imported == 1 and report.target_scope_id == "selected-current-project"
    with engine.connect() as connection:
        rows = connection.execute(database.MemoryMetadataModel.__table__.select()).mappings().all()
    assert len(rows) == 1 and rows[0]["scope"] == "project"
    assert rows[0]["scope_id"] == "selected-current-project"
    assert str(rows[0]["id"]) != "987"
    assert rows[0]["file_path"] != "/untrusted/authority.md"
    assert "untrusted-origin" not in str(rows[0])


def test_tar_budget_includes_undeclared_pax_metadata_before_materialization(tmp_path, monkeypatch):
    import io
    import tarfile

    from cli_agent_orchestrator.services.memory_archive import tar_reader

    path = tmp_path / "pax.tar.gz"
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        item = tarfile.TarInfo("note.md")
        item.size = 5
        item.pax_headers = {"comment": "x" * 16000}
        archive.addfile(item, io.BytesIO(b"exact"))
    original = tar_reader._BoundedReader
    # Exercise the real gzip -> streaming tar path with a small test budget,
    # including metadata tarfile consumes before yielding a declared member.
    monkeypatch.setattr(
        tar_reader, "_BoundedReader", lambda stream, maximum: original(stream, 8192)
    )
    with pytest.raises(ValueError, match="decompression limit"):
        with tar_reader.extracted_bundle(path):
            pytest.fail("PAX metadata escaped the decompression budget")


def test_current_tar_preserves_body_and_escapes_structural_markers(tmp_path, current_memory):
    from cli_agent_orchestrator.services.memory_archive.okf import OkfArchiveBackend

    service, engine = current_memory
    path = tmp_path / "history.tar.gz"
    _current_tar(
        path,
        "Earlier durable fact.\n\n## 2026-10-02T01:00:00Z\n"
        "<!-- id: untrusted-history-id | scope: agent | type: reference | tags: archive -->\n\n"
        "Latest durable fact.",
    )
    report = OkfArchiveBackend(service).import_bundle(path, "global", "skip", False)
    assert report.imported == 1 and report.rejected == 0
    text = service.get_wiki_path("global", None, "note").read_text()
    assert "Earlier durable fact." in text and "Latest durable fact." in text
    assert "<!-- id: untrusted-history-id" not in text
    with engine.connect() as connection:
        row = connection.execute(database.MemoryMetadataModel.__table__.select()).mappings().one()
    assert row["scope"] == "global" and str(row["id"]) != "untrusted-history-id"


def test_current_tar_target_identity_cannot_drift_before_actual_store(
    tmp_path, current_memory, monkeypatch
):
    from cli_agent_orchestrator.services.memory_archive.okf import OkfArchiveBackend

    service, engine = current_memory
    path = tmp_path / "identity-drift.tar.gz"
    _current_tar(path, "A fact for the initially selected project.")
    cwd = tmp_path / "project"
    cwd.mkdir()
    identity = {"value": "initial-project"}
    monkeypatch.setattr(memory_service, "_read_project_id_override", lambda: identity["value"])
    monkeypatch.setattr(memory_service, "_record_alias_safe", lambda *args: None)
    real_store = service.store

    async def change_before_effect(*args, **kwargs):
        identity["value"] = "intervening-project"
        return await real_store(*args, **kwargs)

    monkeypatch.setattr(service, "store", change_before_effect)
    try:
        report = OkfArchiveBackend(service).import_bundle(
            path, "project", "skip", False, {"cwd": str(cwd)}
        )
    except (ValueError, PermissionError):
        report = None  # Refusing a changed target is also safe.
    with engine.connect() as connection:
        rows = connection.execute(database.MemoryMetadataModel.__table__.select()).mappings().all()
    assert all(row["scope_id"] == "initial-project" for row in rows)
    if report is not None and report.imported:
        assert report.target_scope_id == "initial-project"
    assert not service.get_wiki_path("project", "intervening-project", "note").exists()
