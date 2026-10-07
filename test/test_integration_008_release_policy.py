"""Run scheduled-release preflight with isolated local command doubles."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def policy():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    return workflow["jobs"]["preflight"]["steps"][1]["run"]


def run_policy(tmp_path, **overrides):
    commands = {
        "git": """#!/bin/bash
case "$1" in
 tag) echo "${TEST_TAG-v1.2.3}";;
 rev-list) echo "${TEST_COUNT-2}";;
 show) printf 'version = "%s"\\n' "${TEST_VERSION-1.2.4}";;
 log) printf '%s\\n' "${TEST_COMMITS-fix: repair}";;
 *) exit 90;;
esac
""",
        "gh": """#!/bin/bash
case "$1" in
 release) exit "${TEST_PRIOR_RELEASE-0}";;
 run) echo "${TEST_CI-completed:success}";;
 *) exit 91;;
esac
""",
        "python": '#!/bin/bash\ncat >/dev/null\nexit "${TEST_PYPI-0}"\n',
    }
    for name, text in commands.items():
        path = tmp_path / name
        path.write_text(text)
        path.chmod(0o700)
    output = tmp_path / "outputs"
    env = dict(
        os.environ,
        PATH=f"{tmp_path}:/usr/bin:/bin",
        GITHUB_EVENT_NAME="schedule",
        INPUT_BUMP="patch",
        GITHUB_SHA="reviewed-head",
        GITHUB_REPOSITORY="awslabs/cli-agent-orchestrator",
        GITHUB_OUTPUT=str(output),
    )
    env.update(overrides)
    result = subprocess.run(
        ["bash", "-c", policy()], env=env, capture_output=True, text=True, timeout=5
    )
    values = (
        dict(line.split("=", 1) for line in output.read_text().splitlines())
        if output.exists()
        else {}
    )
    return result, values


@pytest.mark.parametrize(
    "commits,bump",
    [
        ("fix: repair", "patch"),
        ("feat: add capability", "minor"),
        ("feat!: change contract", "major"),
        ("fix: repair\nBREAKING CHANGE: new format", "major"),
    ],
)
def test_schedule_selects_bump_after_successful_prerequisites(tmp_path, commits, bump):
    version = {"patch": "1.2.4", "minor": "1.3.0", "major": "2.0.0"}[bump]
    result, values = run_policy(tmp_path, TEST_COMMITS=commits, TEST_VERSION=version)
    assert result.returncode == 0, result.stderr
    assert values["bump"] == bump
    assert values["should_release"] == "true"


@pytest.mark.parametrize(
    "overrides",
    [
        {"TEST_CI": "missing"},
        {"TEST_CI": "in_progress:none"},
        {"TEST_PRIOR_RELEASE": "1"},
        {"TEST_PYPI": "1"},
        {"TEST_TAG": ""},
    ],
)
def test_schedule_refuses_unverified_head_or_previous_release(tmp_path, overrides):
    result, values = run_policy(tmp_path, **overrides)
    assert result.returncode != 0
    assert values.get("should_release") != "true"


def test_no_new_commits_skips_release_even_if_registry_unavailable(tmp_path):
    result, values = run_policy(tmp_path, TEST_COUNT="0", TEST_PYPI="1", TEST_CI="missing")
    assert result.returncode == 0
    assert values["should_release"] == "false"


@pytest.mark.parametrize(
    "state", ["missing", "completed:failure", "completed:skipped", "in_progress:none"]
)
def test_manual_preflight_requires_same_candidate_gates(tmp_path, state):
    result, values = run_policy(tmp_path, GITHUB_EVENT_NAME="workflow_dispatch", TEST_CI=state)
    assert result.returncode != 0
    assert values.get("should_release") != "true"


def test_manual_preflight_rejects_unprepared_version(tmp_path):
    result, values = run_policy(
        tmp_path, GITHUB_EVENT_NAME="workflow_dispatch", TEST_VERSION="1.2.3"
    )
    assert result.returncode != 0
    assert values.get("should_release") != "true"


def test_release_tags_validated_commit_without_post_gate_mutation():
    source = (ROOT / ".github/workflows/release.yml").read_text()
    assert "scripts/bump_version.py" not in source
    assert "git commit" not in source
    assert "needs.preflight.outputs.candidate_sha" in source
    assert "build_release_manifest.py" in source


def test_fork_schedule_is_dormant_and_release_is_serialized():
    source = (ROOT / ".github/workflows/release.yml").read_text()
    assert "github.repository == 'awslabs/cli-agent-orchestrator'" in source
    assert "cancel-in-progress: false" in source
    assert "INPUT_BUMP" in source


@pytest.mark.parametrize(
    "overrides", [{"TEST_TAG": ""}, {"TEST_PRIOR_RELEASE": "1"}, {"TEST_PYPI": "1"}]
)
def test_manual_rejects_unverified_previous_release(tmp_path, overrides):
    result, values = run_policy(tmp_path, GITHUB_EVENT_NAME="workflow_dispatch", **overrides)
    assert result.returncode != 0
    assert values.get("should_release") != "true"


def test_manual_accepts_exact_prepared_candidate(tmp_path):
    result, values = run_policy(tmp_path, GITHUB_EVENT_NAME="workflow_dispatch")
    assert result.returncode == 0, result.stdout + result.stderr
    assert values["candidate_sha"] == "reviewed-head"
    assert values["version"] == "1.2.4"


def test_pypi_builds_and_manifests_use_pinned_candidate():
    workflow = yaml.safe_load((ROOT / ".github/workflows/publish-to-pypi.yml").read_text())
    jobs = workflow["jobs"]
    assert "ci.yml cargo-deny.yml" in jobs["preflight"]["steps"][1]["run"]
    for name in ("build-wheels", "build-sdist", "publish-testpypi", "publish-pypi"):
        checkout = next(
            step
            for step in jobs[name]["steps"]
            if step.get("uses", "").startswith("actions/checkout@")
        )
        assert checkout["with"]["ref"] == "${{ needs.preflight.outputs.candidate_sha }}"
    for name in ("publish-testpypi", "publish-pypi"):
        steps = jobs[name]["steps"]
        manifest = next(
            i for i, step in enumerate(steps) if "build_release_manifest.py" in step.get("run", "")
        )
        publisher = next(
            i
            for i, step in enumerate(steps)
            if step.get("uses", "").startswith("pypa/gh-action-pypi-publish@")
        )
        assert manifest < publisher
        assert "provenance/release-manifest.json" in steps[manifest]["run"]


def test_manual_pypi_requires_testpypi_smoke_success():
    workflow = yaml.safe_load((ROOT / ".github/workflows/publish-to-pypi.yml").read_text())
    jobs = workflow["jobs"]
    assert "skipped" not in jobs["publish-pypi"]["if"]
    assert "needs.smoke-test.result == 'success'" in jobs["publish-pypi"]["if"]
    assert "if" not in jobs["publish-testpypi"]
    assert "if" not in jobs["smoke-test"]
