"""Independent isolated marker concurrency/authority and tar import acceptance."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.services import memory_service, project_marker


@pytest.fixture
def private_marker(tmp_path, monkeypatch):
    engine = create_engine(
        f'sqlite:///{tmp_path / "markers.sqlite"}', connect_args={"check_same_thread": False}
    )
    database.ProjectMarkerModel.__table__.create(engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(constants, "DATABASE_FILE", tmp_path / "markers.sqlite")
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


def _native_tar(path, body, *, tags="portable", extra_manifest=None):
    import io
    import tarfile

    from cli_agent_orchestrator.services.memory_archive.legacy_format import (
        DUMP_ROW_REQUIRED_KEYS,
        compute_content_hash,
    )

    row = dict.fromkeys(DUMP_ROW_REQUIRED_KEYS, None)
    row.update(
        id=987,
        key="note",
        memory_type="reference",
        scope="global",
        scope_id=None,
        tags=tags,
        access_count=0,
        created_at="2026-10-02T00:00:00Z",
        updated_at="2026-10-02T00:00:00Z",
        file_path="/untrusted/authority.md",
        imported_from="untrusted-origin",
    )
    wiki = {"note": ("# note\n\n## 2026-10-02T00:00:00Z\n\n" + body + "\n").encode()}
    manifest = {
        "format_version": 1,
        "project_id": "untrusted-project",
        "id_kind": "override",
        "created_at": "2026-10-02T00:00:00Z",
        "exported_by": "cao",
        "scope_set": ["global"],
        "n_wiki_files": 1,
        "n_metadata_rows": 1,
        "content_hash": "",
    }
    manifest.update(extra_manifest or {})
    manifest["content_hash"] = compute_content_hash(
        manifest=manifest, dump_rows=[row], index_md=b"", wiki_files=wiki
    )
    values = {
        "manifest.json": json.dumps(manifest).encode(),
        "sqlite-dump.json": json.dumps([row]).encode(),
        "wiki/note.md": wiki["note"],
    }
    with tarfile.open(path, "w:gz") as archive:
        for name, value in values.items():
            info = tarfile.TarInfo(name)
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))


@pytest.fixture
def current_memory(tmp_path, monkeypatch):
    from cli_agent_orchestrator.services.memory_service import MemoryService
    from cli_agent_orchestrator.services.vault import binding

    engine = create_engine(f'sqlite:///{tmp_path / "memory.sqlite"}')
    database.Base.metadata.create_all(engine)
    monkeypatch.setattr(
        binding, "resolve", lambda scope, scope_id: binding.NativeBinding(scope, scope_id)
    )
    service = MemoryService(base_dir=tmp_path / "memory", db_engine=engine)
    yield service, engine
    engine.dispose()


def test_native_untrusted_archive_retains_frozen_all_scope_credential_gate(
    tmp_path, current_memory, caplog
):
    from cli_agent_orchestrator.services.memory_archive.okf import OkfArchiveBackend

    service, engine = current_memory
    path = tmp_path / "credential.tar.gz"
    credential = "AKIA" + "A" * 16  # synthetic pattern fixture, never a credential.
    _native_tar(path, "Do not import: " + credential)
    try:
        report = OkfArchiveBackend(service).import_bundle(path, "global", "skip", False)
    except ValueError:
        pass
    else:
        assert report.imported == 0 and report.rejected > 0
    assert not service.get_wiki_path("global", None, "note").exists()
    with engine.connect() as connection:
        assert connection.execute(database.MemoryMetadataModel.__table__.select()).fetchall() == []
    assert credential not in caplog.text


def test_native_tar_explicit_federated_target_preserves_current_credential_refusal(
    tmp_path, current_memory
):
    from cli_agent_orchestrator.services.memory_archive.okf import OkfArchiveBackend

    service, _ = current_memory
    path = tmp_path / "federated.tar.gz"
    _native_tar(path, "AKIA" + "A" * 16)
    try:
        report = OkfArchiveBackend(service).import_bundle(path, "federated", "skip", False)
    except ValueError:
        pass  # Native validation may refuse before the current per-topic gate.
    else:
        assert report.imported == 0 and report.rejected == 1
    assert not service.get_wiki_path("federated", None, "note").exists()


def test_native_tar_cannot_write_a_vault_bound_target(tmp_path, current_memory, monkeypatch):
    from cli_agent_orchestrator.services.memory_archive.okf import OkfArchiveBackend
    from cli_agent_orchestrator.services.vault import binding
    from cli_agent_orchestrator.services.vault.config import FolderMapping

    service, engine = current_memory
    path = tmp_path / "vault.tar.gz"
    _native_tar(path, "A bounded fact.")
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


def test_native_scope_subject_paths_and_ids_do_not_override_current_target(
    tmp_path, current_memory, monkeypatch
):
    from cli_agent_orchestrator.services.memory_archive.okf import OkfArchiveBackend

    service, engine = current_memory
    path = tmp_path / "scoped.tar.gz"
    _native_tar(path, "An explicitly selected current-project fact.")
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


@pytest.mark.parametrize(
    "change", [{"format_version": True}, {"unexpected": "authority"}, {"n_wiki_files": 0}]
)
def test_native_closed_manifest_refuses_all_writes(tmp_path, current_memory, change):
    from cli_agent_orchestrator.services.memory_archive.okf import OkfArchiveBackend

    service, engine = current_memory
    path = tmp_path / "bad-schema.tar.gz"
    _native_tar(path, "A fact.", extra_manifest=change)
    with pytest.raises(ValueError):
        OkfArchiveBackend(service).import_bundle(path, "global", "skip", False)
    with engine.connect() as connection:
        assert connection.execute(database.MemoryMetadataModel.__table__.select()).fetchall() == []


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


def test_native_tar_preserves_earlier_and_latest_facts_without_adopting_history_ids(
    tmp_path, current_memory
):
    from cli_agent_orchestrator.services.memory_archive.okf import OkfArchiveBackend

    service, engine = current_memory
    path = tmp_path / "history.tar.gz"
    _native_tar(
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


def test_native_tar_target_identity_cannot_drift_before_actual_store(
    tmp_path, current_memory, monkeypatch
):
    from cli_agent_orchestrator.services.memory_archive.okf import OkfArchiveBackend

    service, engine = current_memory
    path = tmp_path / "identity-drift.tar.gz"
    _native_tar(path, "A fact for the initially selected project.")
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
