"""The real journey must preserve the provider's frozen native selection."""

import ast
import copy
import json
import sys
from pathlib import Path
from test.live_provider import opencode_interaction_journey as journey
from typing import Any

import pytest

from cli_agent_orchestrator.utils import opencode_v2

pytestmark = pytest.mark.offline_database


def installed_adapter(writer):
    """Exercise the actual nested function body without starting the live journey."""
    tree = ast.parse(Path(journey.__file__).read_text())
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "isolated_v2_writer"
    )
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    namespace = {
        "Any": Any,
        "Path": Path,
        "production_v2_writer": writer,
        "_write_v2_configuration_with_isolated_native_mcp": journey._write_v2_configuration_with_isolated_native_mcp,
    }
    exec(compile(module, str(journey.__file__), "exec"), namespace)
    return namespace["isolated_v2_writer"]


def configuration(tmp_path):
    return {
        "mcp": {
            "servers": {
                "cao-mcp-server": {
                    "type": "local",
                    "command": [sys.executable, "-m", "cli_agent_orchestrator.mcp_server.server"],
                    "environment": {
                        "HOME": str(tmp_path),
                        "CAO_INTERACTION_CAPABILITY_FILE": str(tmp_path / "synthetic.cap"),
                    },
                }
            }
        }
    }


@pytest.mark.parametrize("boundary", ["helper", "installed_adapter"])
@pytest.mark.parametrize(
    "snapshot", [None, ("reviewer", "---\ndescription: Reviewer\n---\nFrozen ñ, 中文.")]
)
def test_both_writer_boundaries_preserve_frozen_selection(tmp_path, boundary, snapshot):
    agents = tmp_path / "agents"
    agents.mkdir()
    path = agents / "reviewer.md"
    path.write_text("Replacement after native validation.")
    source = configuration(tmp_path)
    before = copy.deepcopy(source)
    kwargs = dict(
        source_agents=agents,
        source_config=source,
        session_id="probe1234",
        selected_model="opencode-go/exact-model",
        selected_agent_source=snapshot,
    )
    if boundary == "helper":
        written = journey._write_v2_configuration_with_isolated_native_mcp(
            opencode_v2.write_v2_configuration, tmp_path / "native", **kwargs
        )
    else:
        written = installed_adapter(opencode_v2.write_v2_configuration)(
            tmp_path / "native", **kwargs
        )
    generated = json.loads((written / "opencode.json").read_text())
    expected = "Frozen ñ, 中文." if snapshot else "Replacement after native validation."
    assert generated["agents"]["reviewer"]["system"] == expected
    assert set(generated["providers"]["opencode-go"]["models"]) == {"exact-model"}
    command = generated["mcp"]["servers"]["cao-mcp-server"]["command"]
    assert "-i" in command
    assert "CAO_INTERACTION_CAPABILITY_FILE=" + str(tmp_path / "synthetic.cap") in command
    assert source == before and path.read_text() == "Replacement after native validation."


@pytest.mark.parametrize("boundary", ["helper", "installed_adapter"])
def test_writer_snapshot_omission_keeps_legacy_call_compatible(tmp_path, boundary):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "reviewer.md").write_text("Legacy.")
    kwargs = dict(
        source_agents=agents, source_config=configuration(tmp_path), session_id="probe1234"
    )
    if boundary == "helper":
        written = journey._write_v2_configuration_with_isolated_native_mcp(
            opencode_v2.write_v2_configuration, tmp_path / "native", **kwargs
        )
    else:
        written = installed_adapter(opencode_v2.write_v2_configuration)(
            tmp_path / "native", **kwargs
        )
    assert (
        json.loads((written / "opencode.json").read_text())["agents"]["reviewer"]["system"]
        == "Legacy."
    )
