"""Filesystem ownership contracts for restart-safe native configuration cleanup."""

import json
import os
from pathlib import Path

import pytest

from cli_agent_orchestrator.utils import opencode_ownership as ownership

pytestmark = pytest.mark.offline_database


@pytest.fixture
def allocated(tmp_path):
    base = tmp_path / "private"
    store = ownership.PrivateConfigOwnership(base, "terminal_a")
    root = store.allocate(1)
    (root / "credential").write_text("private fixture sentinel")
    return store, root


@pytest.mark.parametrize("mutation", ["bool", "float", "duplicate"])
@pytest.mark.parametrize("target", ["index", "marker"])
def test_ambiguous_documents_cannot_authorize_removal(allocated, mutation, target):
    store, root = allocated
    path = store.path if target == "index" else root / ".cao-ownership.json"
    raw = path.read_text()
    document = json.loads(raw)
    if mutation == "bool":
        document["version"] = True
        raw = json.dumps(document)
    elif mutation == "float":
        document["claim"]["inode"] = float(document["claim"]["inode"])
        raw = json.dumps(document)
    else:
        raw = raw.replace('"version": 1', '"version": 0, "version": 1')
    path.write_text(raw)
    before = store.path.read_bytes()
    assert store.cleanup() is False
    assert root.is_dir()
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("major", [1, 2])
def test_restored_exact_lookup_never_scans_other_terminals(tmp_path, monkeypatch, major):
    store = ownership.PrivateConfigOwnership(tmp_path / "private", "terminal_a")
    root = store.allocate(major)
    sibling_store = ownership.PrivateConfigOwnership(store.base, "terminal_b")
    sibling = sibling_store.allocate(major)
    restored = ownership.PrivateConfigOwnership(store.base, store.terminal_id)

    def refuse_scan(*args, **kwargs):
        pytest.fail("ownership lookup must not scan terminal directories")

    monkeypatch.setattr(Path, "glob", refuse_scan)
    monkeypatch.setattr(Path, "iterdir", refuse_scan)
    restored.bind_restored()
    assert restored.cleanup() is True
    assert not root.exists()
    assert sibling.is_dir()
    assert restored.cleanup() is True


def test_stale_instance_cannot_delete_replacement(allocated):
    original, root = allocated
    restored = ownership.PrivateConfigOwnership(original.base, original.terminal_id)
    restored.bind_restored()
    assert original.cleanup()
    replacement = original.allocate(2)
    assert restored.cleanup() is False
    assert replacement.is_dir()
    assert original.cleanup()


@pytest.mark.parametrize("target", ["index", "marker", "pool", "root"])
def test_links_never_authorize_removal(allocated, tmp_path, target):
    store, root = allocated
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep"
    sentinel.write_text("operator sentinel")
    if target in ("index", "marker"):
        path = store.path if target == "index" else root / ".cao-ownership.json"
        path.unlink()
        path.symlink_to(sentinel)
    else:
        path = root.parent if target == "pool" else root
        moved = path.with_name(path.name + "-moved")
        path.rename(moved)
        path.symlink_to(outside, target_is_directory=True)
    assert store.cleanup() is False
    assert sentinel.read_text() == "operator sentinel"


@pytest.mark.parametrize("target", ["index", "marker"])
def test_hardlinks_refuse_removal(allocated, tmp_path, target):
    store, root = allocated
    path = store.path if target == "index" else root / ".cao-ownership.json"
    os.link(path, tmp_path / "second-link")
    assert store.cleanup() is False
    assert root.is_dir()


def test_missing_registered_receipt_refuses_adoption(allocated):
    store, root = allocated
    store.path.unlink()
    restored = ownership.PrivateConfigOwnership(store.base, store.terminal_id)
    restored.bind_restored()
    assert restored.cleanup() is False
    assert store.cleanup() is False
    assert root.is_dir()


def test_fresh_unused_cleanup_does_not_create_state(tmp_path):
    store = ownership.PrivateConfigOwnership(tmp_path / "absent", "terminal_a")
    assert store.cleanup() is True
    assert not store.base.exists()


def test_remove_failure_preserves_receipt_and_can_retry(allocated, monkeypatch):
    store, root = allocated
    before = store.path.read_bytes()
    real_remove = ownership.shutil.rmtree

    def fail_remove(path):
        raise OSError("fixture removal failure")

    monkeypatch.setattr(ownership.shutil, "rmtree", fail_remove)
    assert store.cleanup() is False
    assert root.is_dir()
    retained = json.loads(store.path.read_text())
    assert retained["claim"] == json.loads(before)["claim"]
    assert retained["phase"] == "retiring"
    monkeypatch.setattr(ownership.shutil, "rmtree", real_remove)
    assert store.cleanup() is True
    assert not root.exists()


def test_clear_failure_keeps_proof_and_can_retry(allocated, monkeypatch):
    store, root = allocated
    before = store.path.read_bytes()
    real_publish = store._publish

    def fail_publish(claim, *, phase="active"):
        if claim is None:
            raise OSError("fixture publication failure")
        real_publish(claim, phase=phase)

    monkeypatch.setattr(store, "_publish", fail_publish)
    assert store.cleanup() is False
    assert not root.exists()
    retained = json.loads(store.path.read_text())
    assert retained["claim"] == json.loads(before)["claim"]
    assert retained["phase"] == "retiring"
    monkeypatch.setattr(store, "_publish", real_publish)
    assert store.cleanup() is True
    assert json.loads(store.path.read_text())["claim"] is None


def test_unbound_stale_instance_cannot_adopt_replacement(allocated):
    original, _ = allocated
    unbound = ownership.PrivateConfigOwnership(original.base, original.terminal_id)
    assert original.cleanup()
    replacement = original.allocate(2)
    assert unbound.cleanup() is False
    assert replacement.is_dir()


@pytest.mark.parametrize("restart", [False, True])
def test_partial_removal_can_retry_after_marker_is_removed(allocated, monkeypatch, restart):
    store, root = allocated
    real_remove = ownership.shutil.rmtree

    def partial_remove(path):
        (path / ".cao-ownership.json").unlink()
        raise OSError("fixture failure after marker removal")

    monkeypatch.setattr(ownership.shutil, "rmtree", partial_remove)
    assert store.cleanup() is False
    assert (root / "credential").is_file()
    monkeypatch.setattr(ownership.shutil, "rmtree", real_remove)
    if restart:
        store = ownership.PrivateConfigOwnership(store.base, store.terminal_id)
        store.bind_restored()
    assert store.cleanup() is True
    assert not root.exists()


def test_retirement_publication_failure_leaves_tree_and_proof_untouched(allocated, monkeypatch):
    store, root = allocated
    before = store.path.read_bytes()

    def refuse(claim, *, phase="active"):
        raise OSError("fixture retirement publication failure")

    monkeypatch.setattr(store, "_publish", refuse)
    assert store.cleanup() is False
    assert store.path.read_bytes() == before
    assert (root / ".cao-ownership.json").is_file()
    assert (root / "credential").is_file()


@pytest.mark.parametrize("phase", ["active", "retiring"])
def test_replaced_directory_identity_never_authorizes_removal(allocated, phase):
    store, root = allocated
    if phase == "retiring":
        store._publish(store._bound, phase="retiring")
    saved = root.with_name(root.name + "-original")
    root.rename(saved)
    root.mkdir(mode=0o700)
    (root / ".cao-ownership.json").write_bytes((saved / ".cao-ownership.json").read_bytes())
    (root / ".cao-ownership.json").chmod(0o600)
    assert store.cleanup() is False
    assert root.is_dir() and saved.is_dir()


@pytest.mark.parametrize("change", ["missing-marker", "altered-marker"])
def test_retirement_only_tolerates_missing_marker_after_durable_intent(allocated, change):
    store, root = allocated
    marker = root / ".cao-ownership.json"
    if change == "missing-marker":
        marker.unlink()
    else:
        store._publish(store._bound, phase="retiring")
        marker.write_text("{}")
    assert store.cleanup() is False
    assert root.is_dir()


def test_retirement_blocks_allocation_until_cleanup_completes(allocated):
    store, root = allocated
    store._publish(store._bound, phase="retiring")
    with pytest.raises(ownership.PrivateConfigOwnershipError):
        store.allocate(2)
    assert root.is_dir()


def test_old_receipts_without_phase_are_active_and_restorable(allocated):
    store, root = allocated
    for path in (store.path, root / ".cao-ownership.json"):
        data = json.loads(path.read_text())
        data.pop("phase")
        path.write_text(json.dumps(data))
    restored = ownership.PrivateConfigOwnership(store.base, store.terminal_id)
    restored.bind_restored()
    assert restored.cleanup() is True
    assert not root.exists()


def test_failed_allocation_retains_local_claim_for_cleanup_retry(tmp_path, monkeypatch):
    store = ownership.PrivateConfigOwnership(tmp_path / "private", "terminal_a")
    real_publish = store._publish
    real_remove = ownership.shutil.rmtree

    def published_then_failed(claim, *, phase="active"):
        real_publish(claim, phase=phase)
        raise OSError("fixture failure after durable replacement")

    def failed_remove(path):
        raise OSError("fixture rollback removal failure")

    monkeypatch.setattr(store, "_publish", published_then_failed)
    monkeypatch.setattr(ownership.shutil, "rmtree", failed_remove)
    with pytest.raises(OSError):
        store.allocate(1)
    assert store._pending is not None
    root = store.base / "opencode-v1" / store._pending["name"]
    assert root.is_dir()
    monkeypatch.setattr(store, "_publish", real_publish)
    monkeypatch.setattr(ownership.shutil, "rmtree", real_remove)
    assert store.cleanup() is True
    assert not root.exists()


def test_rebinding_stale_instance_cannot_adopt_replacement(allocated):
    store, _ = allocated
    restored = ownership.PrivateConfigOwnership(store.base, store.terminal_id)
    restored.bind_restored()
    restored.bind_restored()  # same launch is idempotent
    assert store.cleanup()
    replacement = store.allocate(2)
    with pytest.raises(ownership.PrivateConfigOwnershipError):
        restored.bind_restored()
    assert restored.cleanup() is False
    assert replacement.is_dir()


def test_failed_allocation_partial_rollback_can_restore_without_marker(tmp_path, monkeypatch):
    store = ownership.PrivateConfigOwnership(tmp_path / "private", "terminal_a")
    real_publish = store._publish
    real_remove = ownership.shutil.rmtree

    def first_publish_failed(claim, *, phase="active"):
        real_publish(claim, phase=phase)
        if phase == "active" and claim is not None:
            raise OSError("fixture failure after initial publication")

    def partial_remove(path):
        (path / ".cao-ownership.json").unlink()
        raise OSError("fixture partial rollback failure")

    monkeypatch.setattr(store, "_publish", first_publish_failed)
    monkeypatch.setattr(ownership.shutil, "rmtree", partial_remove)
    with pytest.raises(OSError):
        store.allocate(1)
    monkeypatch.setattr(ownership.shutil, "rmtree", real_remove)
    restored = ownership.PrivateConfigOwnership(store.base, store.terminal_id)
    restored.bind_restored()
    assert restored.cleanup() is True
    assert json.loads(store.path.read_text())["claim"] is None
