"""Untrusted compressed transport never bypasses the current memory importer."""

import io
import tarfile

import pytest

from cli_agent_orchestrator.services.memory_archive.tar_reader import extracted_bundle


@pytest.mark.parametrize("kind", ["traversal", "symlink", "duplicate", "oversize"])
def test_unsafe_tar_refuses_before_returning_any_import_tree(tmp_path, kind):
    path = tmp_path / "bad.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("../outside.md" if kind == "traversal" else "note.md")
        if kind == "symlink":
            member.type = tarfile.SYMTYPE
            member.linkname = "/etc/passwd"
        if kind == "oversize":
            member.size = 4 * 1024 * 1024 + 1
        archive.addfile(member, io.BytesIO(b"0" * member.size))
        if kind == "duplicate":
            archive.addfile(member)
    with pytest.raises(ValueError):
        with extracted_bundle(path):
            pytest.fail("unsafe archive was yielded")
    assert not (tmp_path / "outside.md").exists()


def test_transport_extracts_exact_regular_bytes_to_private_disposable_root(tmp_path):
    source = tmp_path / "good.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        member = tarfile.TarInfo("topic.md")
        member.size = 5
        archive.addfile(member, io.BytesIO(b"exact"))
    with extracted_bundle(source) as root:
        assert root.stat().st_mode & 0o777 == 0o700
        assert (root / "topic.md").read_bytes() == b"exact"
        remembered = root
    assert not remembered.exists()


def _native_archive(path, *, tamper=False, scope="global"):
    import json

    from cli_agent_orchestrator.services.memory_archive.legacy_format import (
        DUMP_ROW_REQUIRED_KEYS,
        compute_content_hash,
    )

    row = dict.fromkeys(DUMP_ROW_REQUIRED_KEYS, None)
    row.update(
        id=1,
        key="note",
        memory_type="reference",
        scope=scope,
        scope_id=None,
        tags="portable",
        access_count=0,
        created_at="2026-10-02T00:00:00Z",
        updated_at="2026-10-02T00:00:00Z",
    )
    wiki = {"note": b"# note\n\n## 2026-10-02T00:00:00Z\n\nA portable fact.\n"}
    manifest = {
        "format_version": 1,
        "project_id": "project",
        "id_kind": "override",
        "created_at": "2026-10-02T00:00:00Z",
        "exported_by": "cao",
        "scope_set": [scope],
        "n_wiki_files": 1,
        "n_metadata_rows": 1,
        "content_hash": "sha256:" + "0" * 64,
    }
    manifest["content_hash"] = compute_content_hash(
        manifest=manifest, dump_rows=[row], index_md=b"", wiki_files=wiki
    )
    if tamper:
        wiki["note"] += b"changed"
    members = {
        "manifest.json": json.dumps(manifest).encode(),
        "sqlite-dump.json": json.dumps([row]).encode(),
        "wiki/note.md": wiki["note"],
    }
    with tarfile.open(path, "w:gz") as archive:
        for name, value in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))


def test_native_v1_validated_and_converted_without_sqlite_authority(tmp_path):
    from cli_agent_orchestrator.services.memory_archive.legacy_reader import as_current_bundle

    path = tmp_path / "native.tar.gz"
    _native_archive(path)
    with extracted_bundle(path) as original, as_current_bundle(original) as converted:
        text = (converted / "note.md").read_text()
        assert "A portable fact." in text and "type: reference" in text
        assert not (converted / "sqlite-dump.json").exists()


@pytest.mark.parametrize("tamper,scope", [(True, "global"), (False, "agent"), (False, "session")])
def test_native_v1_hash_and_private_scope_invalid_before_materialization(tmp_path, tamper, scope):
    from cli_agent_orchestrator.services.memory_archive.legacy_reader import as_current_bundle

    path = tmp_path / "native.tar.gz"
    _native_archive(path, tamper=tamper, scope=scope)
    with pytest.raises(ValueError):
        with extracted_bundle(path) as original, as_current_bundle(original):
            pytest.fail("invalid native material was yielded")


def test_current_export_tar_roundtrip_uses_actual_store_and_preserves_content(tmp_path):
    import asyncio

    from sqlalchemy import create_engine

    from cli_agent_orchestrator.clients.database import Base
    from cli_agent_orchestrator.services.memory_archive.okf import (
        OkfArchiveBackend,
        export_bundle_to_tar,
    )
    from cli_agent_orchestrator.services.memory_service import MemoryService

    def service(name):
        engine = create_engine(f"sqlite:///{tmp_path/(name+'.sqlite')}")
        Base.metadata.create_all(engine)
        return MemoryService(base_dir=tmp_path / name, db_engine=engine), engine

    source, first_engine = service("source")
    target, second_engine = service("target")
    try:
        asyncio.run(
            source.store(
                "Portable exact fact.", key="note", scope="global", memory_type="reference"
            )
        )
        archive = tmp_path / "roundtrip.tar.gz"
        export_bundle_to_tar(
            OkfArchiveBackend(source), "global", None, archive, include_history=False, redact=False
        )
        report = OkfArchiveBackend(target).import_bundle(archive, "global", "skip", False)
        assert report.imported == 1 and report.rejected == 0
        assert "Portable exact fact." in target.get_wiki_path("global", None, "note").read_text()
    finally:
        first_engine.dispose()
        second_engine.dispose()


def test_native_v1_import_uses_actual_current_store_and_target_identity(tmp_path):
    from sqlalchemy import create_engine

    from cli_agent_orchestrator.clients.database import Base
    from cli_agent_orchestrator.services.memory_archive.okf import OkfArchiveBackend
    from cli_agent_orchestrator.services.memory_service import MemoryService

    engine = create_engine(f"sqlite:///{tmp_path/'native.sqlite'}")
    Base.metadata.create_all(engine)
    try:
        service = MemoryService(base_dir=tmp_path / "memory", db_engine=engine)
        archive = tmp_path / "native.tar.gz"
        _native_archive(archive)
        report = OkfArchiveBackend(service).import_bundle(archive, "global", "skip", False)
        assert report.imported == 1 and report.rejected == 0
        assert "A portable fact." in service.get_wiki_path("global", None, "note").read_text()
    finally:
        engine.dispose()
