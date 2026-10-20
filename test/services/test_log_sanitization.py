"""The log sanitizer stays independent of compiler imports and side effects."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline_database

_PACKAGE_ROOT = Path(__file__).parents[2] / "src"


def _subprocess_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(_PACKAGE_ROOT), environment.get("PYTHONPATH", "")))
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


@pytest.mark.parametrize(
    ("value", "max_len", "expected"),
    [
        ("\x1b[31mred\x1b[0m", 200, "red"),
        ("a\r\nb", 200, r"a\r\nb"),
        ("a\u0085\u2028\u2029b", 200, r"a\n\n\nb"),
        ("a\ud800b\udfffc", 200, "abc"),
        ("\x00a\tb\x1fc", 200, "a\tbc"),
        ("abcdef", 4, "abc…"),
    ],
)
def test_leaf_sanitizer_preserves_log_contract(value, max_len, expected):
    from cli_agent_orchestrator.services.log_sanitization import _sanitize_for_log

    assert _sanitize_for_log(value, max_len=max_len) == expected


def test_wiki_compiler_keeps_the_same_callable_alias():
    from cli_agent_orchestrator.services import log_sanitization, wiki_compiler

    assert wiki_compiler._sanitize_for_log is log_sanitization._sanitize_for_log


def test_importing_audit_log_does_not_import_wiki_compiler(tmp_path):
    script = """
import importlib
import sys

audit_log = importlib.import_module("cli_agent_orchestrator.services.audit_log")
assert "cli_agent_orchestrator.services.wiki_compiler" not in sys.modules
leaf = importlib.import_module("cli_agent_orchestrator.services.log_sanitization")
assert audit_log._sanitize_for_log is leaf._sanitize_for_log
"""
    environment = _subprocess_environment()
    environment["CAO_HOME_DIR"] = str(tmp_path / "isolated-cao-home")

    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr


def test_leaf_import_is_pure_and_does_not_load_runtime_modules(tmp_path):
    script = """
import importlib
import sys
from pathlib import Path

leaf = importlib.import_module("cli_agent_orchestrator.services.log_sanitization")
assert leaf._sanitize_for_log("a\\n\\u2028b") == r"a\\n\\nb"
for module in (
    "cli_agent_orchestrator.constants",
    "cli_agent_orchestrator.services.audit_log",
    "cli_agent_orchestrator.services.wiki_compiler",
):
    assert module not in sys.modules, module
assert list(Path.cwd().iterdir()) == []
"""
    environment = _subprocess_environment()

    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
