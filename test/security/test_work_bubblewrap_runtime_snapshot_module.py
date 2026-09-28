"""The runtime snapshot service must exist before launch can rely on it."""

from __future__ import annotations

import importlib.util


def test_runtime_snapshot_module_is_importable() -> None:
    assert (
        importlib.util.find_spec("cli_agent_orchestrator.services.work_bubblewrap_runtime_snapshot")
        is not None
    )
