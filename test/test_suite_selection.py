"""Regression tests for the named contributor and CI Python suites."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _collect(path: Path, *args: str) -> str:
    env = os.environ.copy()
    # Inspect the project's suite defaults, independent of the parent runner.
    env.pop("PYTEST_ADDOPTS", None)
    # Keep explicit pytest_cov loading below deterministic even when the
    # parent process normally auto-discovers third-party pytest plugins.
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            # The isolated backend runner disables plugin auto-discovery;
            # load the project's declared coverage plugin explicitly so
            # pyproject options and this test's --no-cov override are valid.
            "-p",
            "pytest_cov.plugin",
            str(path),
            "-c",
            str(REPO_ROOT / "pyproject.toml"),
            "--collect-only",
            "-q",
            "--no-cov",
            *args,
        ],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_contributor_and_ci_suites_keep_their_intentional_difference(tmp_path: Path) -> None:
    regression = tmp_path / "test_selection.py"
    regression.write_text(
        "import pytest\n\n"
        "def test_unit():\n"
        "    pass\n\n"
        "@pytest.mark.integration\n"
        "def test_integration():\n"
        "    pass\n\n"
        "@pytest.mark.e2e\n"
        "def test_e2e_outside_e2e_directory():\n"
        "    pass\n",
        encoding="utf-8",
    )

    contributor = _collect(regression)
    ci = _collect(regression, "-m", "not e2e")
    integration = _collect(regression, "-m", "integration")

    assert "test_unit" in contributor
    assert "test_integration" not in contributor
    assert "test_e2e_outside_e2e_directory" not in contributor
    assert "test_unit" in ci
    assert "test_integration" in ci
    assert "test_e2e_outside_e2e_directory" not in ci
    assert "test_unit" not in integration
    assert "test_integration" in integration
    assert "test_e2e_outside_e2e_directory" not in integration
