"""Real filesystem evidence for immutable result publication and retention."""

import hashlib
import importlib
import multiprocessing
import os
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import pytest


def store_module():
    return importlib.import_module("cli_agent_orchestrator.services.step_output_store")


def test_publication_is_retrievable_before_acceptance_and_content_addressed(tmp_path):
    module = store_module()
    store = module.ImmutableResultStore(tmp_path / "results")
    content = b"validated result\x00with bytes"

    def accept(ref):
        assert store.read(ref) == content
        assert ref.content_hash == hashlib.sha256(content).hexdigest()
        assert ref.immutable_location == ref.content_hash
        assert ref.byte_length == len(content)
        assert stat.S_IMODE((tmp_path / "results" / ref.immutable_location).stat().st_mode) == 0o600
        with pytest.raises(FrozenInstanceError):
            ref.byte_length = 0
        return "accepted"

    assert store.publish(content, accept) == "accepted"


def test_failed_acceptance_leaves_recoverable_orphan(tmp_path):
    module = store_module()
    store = module.ImmutableResultStore(tmp_path / "results")
    refs = []

    def fail(ref):
        refs.append(ref)
        raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        store.publish(b"recoverable", fail)
    assert store.read(refs[0]) == b"recoverable"
    assert store.publish(b"recoverable", lambda ref: ref) == refs[0]


def test_concurrent_identical_publications_never_replace_inode(tmp_path):
    store = store_module().ImmutableResultStore(tmp_path / "results")
    ref = store.publish(b"one immutable object", lambda ref: ref)
    original = (tmp_path / "results" / ref.immutable_location).stat()
    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = list(
            pool.map(lambda _: store.publish(b"one immutable object", lambda ref: ref), range(8))
        )
    assert refs == [ref] * 8
    assert (tmp_path / "results" / ref.immutable_location).stat().st_ino == original.st_ino
    assert store.read(ref) == b"one immutable object"


def test_corrupt_existing_artifact_is_never_overwritten_or_accepted(tmp_path):
    store = store_module().ImmutableResultStore(tmp_path / "results")
    ref = store.publish(b"original", lambda ref: ref)
    path = tmp_path / "results" / ref.immutable_location
    path.write_bytes(b"tampered")
    accepted = []
    with pytest.raises(ValueError):
        store.publish(b"original", accepted.append)
    with pytest.raises(ValueError):
        store.read(ref)
    assert accepted == []
    assert path.read_bytes() == b"tampered"


def test_size_limit_rejects_before_publication(tmp_path):
    store = store_module().ImmutableResultStore(tmp_path / "results", max_bytes=3)
    accepted = []
    with pytest.raises(ValueError):
        store.publish(b"four", accepted.append)
    assert accepted == []
    assert store.read(store.publish(b"", lambda ref: ref)) == b""
    assert store.read(store.publish(b"abc", lambda ref: ref)) == b"abc"


@pytest.mark.parametrize("failure_kind", ["file", "directory"])
def test_acceptance_requires_successful_file_and_directory_fsync(
    tmp_path, monkeypatch, failure_kind
):
    store = store_module().ImmutableResultStore(tmp_path / "results")
    original_fsync = os.fsync

    def fail_selected(descriptor):
        is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        if is_directory == (failure_kind == "directory"):
            raise OSError("injected storage flush failure")
        return original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_selected)
    accepted = []
    with pytest.raises(OSError, match="injected storage flush failure"):
        store.publish(b"must be durable", accepted.append)
    assert accepted == []


@pytest.mark.parametrize("location", ["../outside", "/tmp/outside", "A" * 64, "b" * 64])
def test_artifact_reference_rejects_paths_and_hash_mismatch(tmp_path, location):
    module = store_module()
    store = module.ImmutableResultStore(tmp_path / "results")
    with pytest.raises(ValueError):
        store.read(module.ArtifactRef("a" * 64, location, 1))


def test_symlink_artifacts_and_symlink_roots_are_rejected(tmp_path):
    module = store_module()
    root = tmp_path / "results"
    store = module.ImmutableResultStore(root)
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    outside.chmod(0o600)
    content_hash = hashlib.sha256(b"outside").hexdigest()
    (root / content_hash).symlink_to(outside)
    with pytest.raises((OSError, ValueError)):
        store.publish(b"outside", lambda ref: ref)
    with pytest.raises((OSError, ValueError)):
        store.read(module.ArtifactRef(content_hash, content_hash, 7))
    assert outside.read_bytes() == b"outside"
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        module.ImmutableResultStore(alias)
    with pytest.raises((OSError, ValueError)):
        module.ImmutableResultStore(alias / "nested")


def test_orphan_cleanup_preserves_referenced_recent_and_unknown_files(tmp_path):
    store = store_module().ImmutableResultStore(tmp_path / "results")
    refs = [
        store.publish(value, lambda ref: ref) for value in (b"orphan", b"referenced", b"recent")
    ]
    root = tmp_path / "results"
    old = time.time() - 3600
    for ref in refs[:2]:
        os.utime(root / ref.immutable_location, (old, old))
    unrelated = root / "operator-note"
    unrelated.write_text("keep")
    assert store.collect_orphans(lambda: {refs[1].content_hash}, older_than=time.time() - 60) == [
        refs[0].content_hash
    ]
    assert not (root / refs[0].immutable_location).exists()
    assert store.read(refs[1]) == b"referenced"
    assert store.read(refs[2]) == b"recent"
    assert unrelated.read_text() == "keep"


def _publish_blocking(root, reference_file, published, release, errors):
    try:
        store = store_module().ImmutableResultStore(root)

        def accept(ref):
            published.set()
            if not release.wait(15):
                raise TimeoutError("test did not release acceptance")
            reference_file.write_text(ref.content_hash)
            return ref

        store.publish(b"publication in progress", accept)
    except BaseException as exc:
        errors.put(repr(exc))


def _collect_blocking(root, reference_file, started, inspected, finished, errors):
    try:
        store = store_module().ImmutableResultStore(root)

        def referenced():
            inspected.set()
            return {reference_file.read_text()} if reference_file.exists() else set()

        started.set()
        store.collect_orphans(referenced, older_than=time.time() + 60)
        finished.set()
    except BaseException as exc:
        errors.put(repr(exc))


def test_cleanup_cannot_interleave_between_publish_and_reference(tmp_path):
    module = store_module()
    root = tmp_path / "results"
    module.ImmutableResultStore(root)
    reference_file = tmp_path / "database-reference"
    ctx = multiprocessing.get_context("fork")
    published, release, started, inspected, finished = [ctx.Event() for _ in range(5)]
    errors = ctx.Queue()
    publisher = ctx.Process(
        target=_publish_blocking, args=(root, reference_file, published, release, errors)
    )
    collector = ctx.Process(
        target=_collect_blocking, args=(root, reference_file, started, inspected, finished, errors)
    )
    publisher.start()
    try:
        assert published.wait(10), "publisher did not reach acceptance"
        collector.start()
        assert started.wait(10), "collector did not start"
        assert not inspected.wait(0.2), "collector inspected references before acceptance committed"
        release.set()
        publisher.join(10)
        collector.join(10)
        assert publisher.exitcode == collector.exitcode == 0
        assert errors.empty()
        assert finished.is_set()
        content_hash = reference_file.read_text()
        assert (
            module.ImmutableResultStore(root).read(
                module.ArtifactRef(content_hash, content_hash, len(b"publication in progress"))
            )
            == b"publication in progress"
        )
    finally:
        release.set()
        for process in (publisher, collector):
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join(5)


def _crash_before_link(root):
    store = store_module().ImmutableResultStore(root)

    def crash(*args, **kwargs):
        os._exit(23)

    # Real staging bytes have been flushed when publication reaches os.link.
    os.link = crash
    store.publish(b"crash after file fsync", lambda ref: ref)


def test_cleanup_recovers_old_staging_file_after_real_publication_crash(tmp_path):
    root = tmp_path / "results"
    store = store_module().ImmutableResultStore(root)
    process = multiprocessing.get_context("fork").Process(target=_crash_before_link, args=(root,))
    process.start()
    try:
        process.join(10)
        assert process.exitcode == 23
        staging = [path for path in root.iterdir() if path.name.startswith(".publish-")]
        assert len(staging) == 1
        assert staging[0].read_bytes() == b"crash after file fsync"
        assert store.collect_orphans(lambda: set(), older_than=time.time() - 60) == []
        old = time.time() - 3600
        os.utime(staging[0], (old, old))
        assert store.collect_orphans(lambda: set(), older_than=time.time() - 60) == [
            staging[0].name
        ]
        assert not staging[0].exists()
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)


def test_cleanup_preserves_public_files_unknown_staging_names_and_symlinks(tmp_path):
    root = tmp_path / "results"
    store = store_module().ImmutableResultStore(root)
    old = time.time() - 3600
    for name, mode in (
        (".publish-" + "a" * 32, 0o644),
        ("b" * 64, 0o644),
        (".publish-unrecognized", 0o600),
    ):
        path = root / name
        path.write_bytes(b"unexpected file")
        path.chmod(mode)
        os.utime(path, (old, old))
    outside = tmp_path / "outside"
    outside.write_bytes(b"external data")
    symlink = root / (".publish-" + "c" * 32)
    symlink.symlink_to(outside)
    os.utime(symlink, (old, old), follow_symlinks=False)
    assert store.collect_orphans(lambda: set(), older_than=time.time() - 60) == []
    assert (root / ("b" * 64)).read_bytes() == b"unexpected file"
    assert symlink.is_symlink()
    assert outside.read_bytes() == b"external data"


def test_cleanup_does_not_remove_a_file_reported_as_owned_by_another_user(tmp_path, monkeypatch):
    root = tmp_path / "results"
    store = store_module().ImmutableResultStore(root)
    ref = store.publish(b"another owner's evidence", lambda ref: ref)
    old = time.time() - 3600
    os.utime(root / ref.content_hash, (old, old))
    original_stat = os.stat

    def foreign_owner(path, *args, **kwargs):
        info = original_stat(path, *args, **kwargs)
        if path == ref.content_hash:
            fields = list(info)
            fields[4] = os.geteuid() + 1
            return os.stat_result(fields)
        return info

    # An unprivileged test cannot chown a real file to another user. Substitute
    # only the observed UID; path, permissions, content and deletion remain real.
    monkeypatch.setattr(os, "stat", foreign_owner)
    assert store.collect_orphans(lambda: set(), older_than=time.time() - 60) == []
    assert store.read(ref) == b"another owner's evidence"
