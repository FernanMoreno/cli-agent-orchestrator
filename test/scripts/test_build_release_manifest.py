"""Offline artifact provenance: hashes and resolved inventories attest local bytes."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module():
    spec = importlib.util.spec_from_file_location(
        "release_manifest", ROOT / "scripts/build_release_manifest.py"
    )
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def fixture_root(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="example"\nversion="1.2.3"\n')
    (tmp_path / "uv.lock").write_text('version=1\n[[package]]\nname="requests"\nversion="2.32.4"\n')
    web = tmp_path / "web"
    web.mkdir()
    (web / "package-lock.json").write_text(
        json.dumps({"packages": {"node_modules/widget": {"version": "2.0.0"}}})
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "example-1.2.3.tar.gz").write_bytes(b"artifact bytes")
    return tmp_path, dist


def test_manifest_records_version_revision_hashes_and_inventory(tmp_path):
    root, dist = fixture_root(tmp_path)
    manifest = module().build_manifest(root, dist, "a" * 40)
    assert manifest["version"] == "1.2.3"
    assert manifest["revision"] == "a" * 40
    artifact = manifest["artifacts"][0]
    assert artifact["sha256"] == hashlib.sha256(b"artifact bytes").hexdigest()
    assert artifact["size_bytes"] == len(b"artifact bytes")
    assert any(
        item["name"] == "requests" and item["version"] == "2.32.4"
        for item in manifest["dependencies"]
    )
    assert any(
        item["name"] == "widget" and item["version"] == "2.0.0" for item in manifest["dependencies"]
    )
    (dist / "example-1.2.3.tar.gz").write_bytes(b"tampered")
    assert (
        module().build_manifest(root, dist, "a" * 40)["artifacts"][0]["sha256"]
        != artifact["sha256"]
    )


def test_manifest_is_deterministic_and_excludes_own_output(tmp_path):
    root, dist = fixture_root(tmp_path)
    first = module().build_manifest(root, dist, "a" * 40)
    (dist / "release-manifest.json").write_text(json.dumps(first))
    assert module().build_manifest(root, dist, "a" * 40) == first


@pytest.mark.parametrize("revision", ["", "main", "a" * 39])
def test_manifest_rejects_unverifiable_revision(tmp_path, revision):
    root, dist = fixture_root(tmp_path)
    with pytest.raises(ValueError):
        module().build_manifest(root, dist, revision)


def test_manifest_rejects_empty_artifact_set(tmp_path):
    root, dist = fixture_root(tmp_path)
    for artifact in dist.iterdir():
        artifact.unlink()
    with pytest.raises(ValueError, match="artifact"):
        module().build_manifest(root, dist, "a" * 40)


def test_manifest_does_not_hash_external_symlink(tmp_path):
    root, dist = fixture_root(tmp_path)
    (dist / "external.whl").symlink_to(root / "uv.lock")
    with pytest.raises(ValueError, match="symlink"):
        module().build_manifest(root, dist, "a" * 40)


@pytest.mark.parametrize("changed", ["pyproject.toml", "uv.lock"])
def test_cli_refuses_dirty_candidate_metadata_or_lock(monkeypatch, tmp_path, changed):
    root, dist = fixture_root(tmp_path)
    loaded = module()
    original = {
        name: (root / name).read_bytes()
        for name in ("pyproject.toml", "uv.lock", "web/package-lock.json")
    }

    def git_output(command, **kwargs):
        if "rev-parse" in command:
            return "a" * 40 + "\n"
        if "ls-files" in command:
            return b"pyproject.toml\0uv.lock\0web/package-lock.json\0"
        if "show" in command:
            return original[command[-1].split(":", 1)[1]]
        raise AssertionError(command)

    monkeypatch.setattr(loaded.subprocess, "check_output", git_output)
    monkeypatch.setattr(
        "sys.argv",
        [
            "manifest",
            "--root",
            str(root),
            "--dist",
            str(dist),
            "--output",
            str(root / "manifest.json"),
        ],
    )
    (root / changed).write_bytes(original[changed] + b"\n# dirty\n")
    with pytest.raises(ValueError, match="candidate"):
        loaded.main()
    assert not (root / "manifest.json").exists()
