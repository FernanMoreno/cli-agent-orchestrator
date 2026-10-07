"""Registered projects compose installer mounts with launch admission."""

import importlib.util
import json
from pathlib import Path

import pytest

from cli_agent_orchestrator.utils import path_validation


def installer():
    spec = importlib.util.spec_from_file_location(
        "collaboration_installer", Path(__file__).resolve().parents[2] / "scripts/docker_install.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_workspace_readonly_manifest_and_canonical_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_OBSIDIAN_VAULT", raising=False)
    rw, ro = tmp_path / "rw", tmp_path / "ro"
    rw.mkdir()
    ro.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(ro, target_is_directory=True)
    mounts = installer().runtime_mounts([rw], [alias])
    projects = [m for m in mounts if m.get("purpose") == "project"]
    assert projects == [
        {
            "source": str(rw.resolve()),
            "target": str(rw.resolve()),
            "readonly": False,
            "purpose": "project",
        },
        {
            "source": str(ro.resolve()),
            "target": str(ro.resolve()),
            "readonly": True,
            "purpose": "project",
        },
    ]


@pytest.mark.parametrize("nested", [False, True])
def test_contradictory_workspace_modes_rejected(tmp_path, nested):
    child = tmp_path / "child"
    child.mkdir()
    with pytest.raises(ValueError, match="conflicting workspace modes"):
        installer().runtime_mounts([tmp_path], [child if nested else tmp_path])


def test_registered_directory_resolves_symlinks_and_rejects_escape(tmp_path, monkeypatch):
    project, outside = tmp_path / "project", tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    nested = project / "nested"
    nested.mkdir()
    escape = project / "escape"
    escape.symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("CAO_REGISTERED_PROJECTS", json.dumps([str(project)]))
    assert path_validation.resolve_registered_working_directory(str(nested)) == str(nested)
    for forbidden in (escape, outside):
        with pytest.raises(ValueError, match="registered project"):
            path_validation.resolve_registered_working_directory(str(forbidden))


@pytest.mark.parametrize("value", ["", "null", "{}", "[]", '["relative"]', "not json", "[42]"])
def test_invalid_registered_policy_fails_closed(tmp_path, monkeypatch, value):
    monkeypatch.setenv("CAO_REGISTERED_PROJECTS", value)
    with pytest.raises(ValueError, match="registered project"):
        path_validation.resolve_registered_working_directory(str(tmp_path))


def test_generic_working_directory_unchanged_without_policy(tmp_path, monkeypatch):
    monkeypatch.delenv("CAO_REGISTERED_PROJECTS", raising=False)
    assert path_validation.resolve_registered_working_directory(str(tmp_path)) == str(tmp_path)


def test_registered_policy_is_enforced_at_service_and_tmux_boundaries(tmp_path, monkeypatch):
    from cli_agent_orchestrator.clients.tmux import TmuxClient
    from cli_agent_orchestrator.services.terminal_service import _resolve_working_directory

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("CAO_REGISTERED_PROJECTS", json.dumps([str(project)]))
    client = TmuxClient.__new__(TmuxClient)
    for resolve in (_resolve_working_directory, client._resolve_and_validate_working_directory):
        assert resolve(str(project)) == str(project)
        with pytest.raises(ValueError, match="registered project"):
            resolve(str(tmp_path))


def test_linked_git_worktree_with_external_metadata_is_reused(tmp_path):
    import subprocess

    from cli_agent_orchestrator.services import worktree_service

    anchor, linked = tmp_path / "anchor", tmp_path / "linked"
    subprocess.run(["git", "init", "-q", str(anchor)], check=True)
    (anchor / "file.txt").write_text("base")
    subprocess.run(["git", "-C", str(anchor), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(anchor),
            "-c",
            "user.name=CAO Test",
            "-c",
            "user.email=cao-test@example.invalid",
            "commit",
            "-qm",
            "base",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(anchor), "worktree", "add", "--detach", str(linked)],
        check=True,
        capture_output=True,
    )
    assert (linked / ".git").is_file()
    assert worktree_service.find_repo_root(str(linked)) == str(linked)
    worker = Path(worktree_service.create_worktree(str(linked), "proof001"))
    assert (worker / "file.txt").read_text() == "base"
    assert worker.is_relative_to(linked)
    worktree_service.remove_worktree(str(linked), "proof001")
    assert not worker.exists()
