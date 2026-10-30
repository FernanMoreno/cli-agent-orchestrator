"""Import and disabled-path guarantees for the legacy memory audit adapter."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline_database


def test_memory_service_import_binds_audit_repository_without_opening_database():
    from cli_agent_orchestrator.services import memory_service

    script = r"""
import os
import sys
from pathlib import Path

home_state = ("HOME" in os.environ, os.environ.get("HOME"))
codex_home_state = ("CODEX_HOME" in os.environ, os.environ.get("CODEX_HOME"))
private_home = Path(sys.argv[1])
expected_source = Path(sys.argv[2]).resolve()
Path.home = classmethod(lambda cls: private_home)
original_expanduser = os.path.expanduser

def private_expanduser(value):
    if isinstance(value, bytes):
        if value == b"~":
            return os.fsencode(str(private_home))
        if value.startswith(b"~/"):
            return os.fsencode(str(private_home)) + value[1:]
        return original_expanduser(value)
    if value == "~":
        return str(private_home)
    if value.startswith("~/"):
        return str(private_home) + value[1:]
    return original_expanduser(value)

os.path.expanduser = private_expanduser
assert Path.home() == private_home
assert os.path.expanduser("~") == str(private_home)
assert os.path.expanduser("~/config") == str(private_home / "config")

from cli_agent_orchestrator.clients.postgres import DatabaseTarget
from cli_agent_orchestrator.clients.postgres import config as postgres_config
import psycopg

calls = []

def forbidden(*args, **kwargs):
    calls.append((args, kwargs))
    raise AssertionError("database configuration or connection accessed during import")

DatabaseTarget.from_settings = classmethod(forbidden)
postgres_config.private_json = forbidden
psycopg.connect = forbidden

import cli_agent_orchestrator.services.memory_service as memory_service

module_path = Path(memory_service.__file__).resolve()
assert expected_source == module_path or expected_source in module_path.parents
assert "cli_agent_orchestrator.services.legacy_memory_audit_repository" in sys.modules
assert "cli_agent_orchestrator.services.memory_scoring" in sys.modules
assert "cli_agent_orchestrator.services.memory_append" in sys.modules
from cli_agent_orchestrator.services.legacy_memory_audit_repository import (
    _LegacyMemoryAuditWorkRepository,
    _legacy_memory_audit_manager,
)

repository_path = Path(
    sys.modules["cli_agent_orchestrator.services.legacy_memory_audit_repository"].__file__
).resolve()
assert expected_source == repository_path or expected_source in repository_path.parents
assert memory_service._LegacyMemoryAuditWorkRepository is _LegacyMemoryAuditWorkRepository
manager = _legacy_memory_audit_manager()
assert manager.managed_session_count == 0
assert manager._entries == {}
assert manager._opening == set()
assert manager._latest_profiles == {}
assert calls == []
assert ("HOME" in os.environ, os.environ.get("HOME")) == home_state
assert ("CODEX_HOME" in os.environ, os.environ.get("CODEX_HOME")) == codex_home_state
"""
    source_root = Path(memory_service.__file__).resolve().parents[2]
    child_environment = os.environ.copy()
    existing_pythonpath = child_environment.get("PYTHONPATH")
    child_pythonpath = [str(source_root)]
    if existing_pythonpath:
        child_pythonpath.append(existing_pythonpath)
    child_environment["PYTHONPATH"] = os.pathsep.join(child_pythonpath)
    result = subprocess.run(
        [sys.executable, "-c", script, str(Path.home()), str(source_root)],
        cwd=Path.cwd(),
        env=child_environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_disabled_store_does_not_resolve_audit_database(monkeypatch, tmp_path):
    import psycopg

    from cli_agent_orchestrator.clients.postgres import DatabaseTarget
    from cli_agent_orchestrator.clients.postgres import config as postgres_config
    from cli_agent_orchestrator.services import memory_service

    def forbidden(*args, **kwargs):
        pytest.fail("disabled memory store accessed the audit database")

    monkeypatch.setattr(memory_service, "_is_memory_enabled", lambda: False)
    monkeypatch.setattr(memory_service.MemoryService, "_legacy_repository", forbidden)
    monkeypatch.setattr(memory_service.MemoryService, "_get_db_session", forbidden)
    monkeypatch.setattr(DatabaseTarget, "from_settings", classmethod(forbidden))
    monkeypatch.setattr(postgres_config, "private_json", forbidden)
    monkeypatch.setattr(psycopg, "connect", forbidden)

    service = memory_service.MemoryService(base_dir=tmp_path / "wiki")
    with pytest.raises(memory_service.MemoryDisabledError):
        asyncio.run(
            service.store(
                content="disabled write",
                scope="global",
                memory_type="reference",
                key="disabled-write",
            )
        )

    assert service._legacy_audit_repository is None
    assert not service.base_dir.exists()
