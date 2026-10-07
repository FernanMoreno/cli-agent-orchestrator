"""CI must execute reviewed dependency/action identities, retaining fork gates."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_external_actions_have_full_commit_pins():
    unpinned = []
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        for action in re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", path.read_text(), re.M):
            if action.startswith(("./", "docker://")):
                continue
            if not re.fullmatch(r"[^@]+@[0-9a-f]{40}", action):
                unpinned.append((path.name, action))
    assert not unpinned


def test_ci_rejects_stale_lock_and_keeps_existing_acceptance():
    source = (ROOT / ".github/workflows/ci.yml").read_text()
    assert 'UV_LOCKED: "1"' in source
    assert "uv sync --locked" in source
    assert "uv sync --frozen" not in source
    assert "npm install" not in source
    assert (ROOT / ".github/workflows/t097-host-acceptance.yml").exists()
    assert (ROOT / ".github/scripts/t097-qemu-acceptance.sh").exists()
    assert (ROOT / ".github/workflows/real-provider-e2e.yml").exists()


def test_advertised_python_versions_have_required_matrix_coverage():
    import yaml

    source = (ROOT / "pyproject.toml").read_text()
    advertised = re.findall(r'Programming Language :: Python :: (3\.\d+)"', source)
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert set(advertised) <= set(workflow["jobs"]["test"]["strategy"]["matrix"]["python-version"])
    mypy = next(
        step for step in workflow["jobs"]["lint"]["steps"] if "mypy src/" in step.get("run", "")
    )
    assert not mypy.get("continue-on-error", False)
    assert "required-gates" in workflow["jobs"]
    assert "architecture" in workflow["jobs"]


def test_distributed_dependency_environments_are_reviewed():
    import yaml

    updates = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())["updates"]
    entries = {(item["package-ecosystem"], item["directory"]) for item in updates}
    for expected in [
        ("pip", "/"),
        ("pip", "/examples/fleet/panel"),
        ("cargo", "/tui"),
        ("npm", "/web"),
        ("npm", "/cao_mcp_apps"),
        ("npm", "/docusaurus"),
    ]:
        assert expected in entries


@pytest.mark.parametrize("result", ["failure", "skipped", "cancelled", "missing"])
def test_required_gate_rejects_non_success(tmp_path, result):
    import os
    import subprocess

    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["required-gates"]
    assert job["if"] == "always()"
    step = job["steps"][0]
    env = dict(os.environ, GATE_RESULTS='{"test":{"result":"' + result + '"}}')
    run = subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True)
    assert run.returncode != 0
