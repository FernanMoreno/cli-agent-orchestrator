"""Untrusted compressed transport never bypasses the current memory importer."""

import io
import tarfile
from test.postgres.orm import configured_orm_engine

import frontmatter
import pytest

from cli_agent_orchestrator.services.memory_archive.tar_reader import extracted_bundle


def _current_archive(path, body):
    topic = frontmatter.dumps(
        frontmatter.Post(body, type="reference", title="note", tags="portable")
    ).encode()
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("note.md")
        member.size = len(topic)
        archive.addfile(member, io.BytesIO(topic))


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


def test_current_export_tar_roundtrip_uses_actual_store_and_preserves_content(tmp_path):
    import asyncio

    from cli_agent_orchestrator.clients.database import Base
    from cli_agent_orchestrator.services.memory_archive.okf import (
        OkfArchiveBackend,
        export_bundle_to_tar,
    )
    from cli_agent_orchestrator.services.memory_service import MemoryService

    def service(name):
        engine = configured_orm_engine()
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


def test_current_tar_import_uses_actual_current_store(tmp_path):
    from cli_agent_orchestrator.clients.database import Base
    from cli_agent_orchestrator.services.memory_archive.okf import OkfArchiveBackend
    from cli_agent_orchestrator.services.memory_service import MemoryService

    engine = configured_orm_engine()
    Base.metadata.create_all(engine)
    try:
        service = MemoryService(base_dir=tmp_path / "memory", db_engine=engine)
        archive = tmp_path / "current.tar.gz"
        _current_archive(archive, "A portable fact.")
        report = OkfArchiveBackend(service).import_bundle(archive, "global", "skip", False)
        assert report.imported == 1 and report.rejected == 0
        assert "A portable fact." in service.get_wiki_path("global", None, "note").read_text()
    finally:
        engine.dispose()
