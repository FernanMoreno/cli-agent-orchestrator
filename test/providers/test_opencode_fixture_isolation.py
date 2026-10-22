"""Offline defaults must not consume the parent's synthetic operator state."""

import json
import os
from pathlib import Path
from test.providers.test_opencode_profile_initialization import launch  # noqa: F401
from unittest.mock import AsyncMock

import pytest

from cli_agent_orchestrator.providers import opencode_cli
from cli_agent_orchestrator.utils import opencode_config

pytestmark = [pytest.mark.offline_database, pytest.mark.asyncio]


@pytest.mark.parametrize("inherited", ["inline_auth", "managed_config", "v2_xdg", "v2_db"])
async def test_launch_does_not_consume_inherited_state(tmp_path, monkeypatch, request, inherited):
    operator = tmp_path / "synthetic-operator"
    operator.mkdir()
    config = operator / "opencode.json"
    config.write_text(json.dumps({"model": "synthetic/operator-model"}))
    data = operator / "data/opencode"
    data.mkdir(parents=True)
    (data / "opencode.db").write_bytes(b"synthetic parent XDG database")
    (data / "auth.json").write_text('{"synthetic":"parent-file-only"}')
    database = operator / "override.db"
    database.write_bytes(b"synthetic parent override database")
    before = {path: path.read_bytes() for path in operator.rglob("*") if path.is_file()}
    parent_home, parent_path = os.environ.get("HOME"), os.environ.get("PATH")
    monkeypatch.setenv("XDG_DATA_HOME", str(operator / "data"))
    monkeypatch.setenv("OPENCODE_DB", str(database))
    if inherited == "v2_xdg":
        monkeypatch.delenv("OPENCODE_DB")
    monkeypatch.setenv("OPENCODE_AUTH_CONTENT", '{"synthetic":"parent-inline-only"}')
    monkeypatch.setattr(opencode_config, "OPENCODE_CONFIG_FILE", config)
    if inherited != "managed_config":
        monkeypatch.setattr(opencode_config, "read_config", lambda: {})
    original_open = Path.open
    original_os_open = os.open

    def reject_operator_path(path):
        if isinstance(path, (str, bytes, os.PathLike)):
            if Path(os.fsdecode(path)).absolute().is_relative_to(operator):
                raise AssertionError("offline fixture accessed inherited operator state")

    def reject_operator_read(path, *args, **kwargs):
        reject_operator_path(path)
        return original_open(path, *args, **kwargs)

    def reject_operator_fd(path, *args, **kwargs):
        reject_operator_path(path)
        return original_os_open(path, *args, **kwargs)

    with monkeypatch.context() as guard:
        guard.setattr(Path, "open", reject_operator_read)
        guard.setattr(os, "open", reject_operator_fd)
        instance, _, _ = request.getfixturevalue("launch")
        major = 2 if inherited.startswith("v2_") else 1
        monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: major)
        monkeypatch.setattr(instance, "_wait_for_v1_cao_mcp_ready", AsyncMock(return_value=True))
        assert await instance.initialize()
        private = (
            instance._v2_data_home / "opencode"
            if major == 2
            else instance._v1_config_dir / "data/opencode"
        )
        assert not (private / "auth.json").exists()
        assert not (private / "opencode.db").exists()
        assert os.environ.get("HOME") == parent_home
        assert os.environ.get("PATH") == parent_path
    assert {path: path.read_bytes() for path in before} == before
