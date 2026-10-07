"""KAS resource closure pins actual bounded files, not only glob spelling."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services import launch_material


def test_kas_resource_inventory_pins_standard_and_workspace_content(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    workspace = tmp_path / "workspace"
    (skills / "one").mkdir(parents=True)
    workspace.mkdir()
    (skills / "one" / "SKILL.md").write_text("Approved shared skill")
    (workspace / "instructions.txt").write_text("Approved workspace instructions")
    monkeypatch.setattr("cli_agent_orchestrator.constants.SKILLS_DIR", skills)
    profile = SimpleNamespace(resources=["file://instructions.txt"])
    material = launch_material.freeze_kas_resources(profile, str(workspace))
    assert len(material["files"]) == 2  # overlapping standard globs deduplicate
    assert {item["content"] for item in material["files"]} == {
        "Approved shared skill",
        "Approved workspace instructions",
    }
    first = material
    (skills / "one" / "SKILL.md").write_text("Changed shared skill")
    assert launch_material.freeze_kas_resources(profile, str(workspace)) != first
    (skills / "two").mkdir()
    (skills / "two" / "SKILL.md").write_text("New skill")
    assert len(launch_material.freeze_kas_resources(profile, str(workspace))["files"]) == 3


@pytest.mark.parametrize(
    "resource", ["file://../outside.txt", "skill://${HOME}/**/SKILL.md", "https://host/resource"]
)
def test_resource_escape_or_unresolved_ref_is_refused(tmp_path, monkeypatch, resource):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "outside.txt").write_text("Unapproved outside instructions")
    monkeypatch.setattr("cli_agent_orchestrator.constants.SKILLS_DIR", tmp_path / "skills")
    with pytest.raises(ValueError):
        launch_material.freeze_kas_resources(SimpleNamespace(resources=[resource]), str(workspace))


def test_resource_symlink_escape_and_bounds_are_refused(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skills = tmp_path / "skills"
    (skills / "one").mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    (external / "SKILL.md").write_text("Outside")
    (skills / "escape").symlink_to(external, target_is_directory=True)
    monkeypatch.setattr("cli_agent_orchestrator.constants.SKILLS_DIR", skills)
    with pytest.raises(ValueError):
        launch_material.freeze_kas_resources(SimpleNamespace(resources=[]), str(workspace))
    (skills / "escape").unlink()
    (skills / "one" / "SKILL.md").write_bytes(b"a" * 65537)
    with pytest.raises(ValueError):
        launch_material.freeze_kas_resources(SimpleNamespace(resources=[]), str(workspace))
