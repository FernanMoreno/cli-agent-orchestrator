"""Native case composition preserves catalog gates and closes its client on refusal."""

from pathlib import Path
from test.live_provider import codex_supervision as native
from test.live_provider.test_supervision_authority import setup
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize("case", ["ok", "catalog", "ready", "resource", "observer", "construct"])
def test_native_case_uses_one_profile_and_closes_client(tmp_path, monkeypatch, case):
    from cli_agent_orchestrator import constants
    from cli_agent_orchestrator.providers import codex
    from cli_agent_orchestrator.utils import mcp_readiness, mcp_tool_policy

    exercise, call, state, _ = setup()
    owned, methods = [], []
    monkeypatch.setattr(constants, "LOCAL_AGENT_STORE_DIR", tmp_path / "cao/agents")
    monkeypatch.setattr(
        native,
        "Path",
        lambda p: (
            SimpleNamespace(read_text=lambda: "MemAvailable: 8388608 kB")
            if str(p) == "/proc/meminfo"
            else Path(p)
        ),
    )
    monkeypatch.setattr(
        native,
        "verify_codex_runtime_snapshot",
        lambda *a, **kw: {"version": "0.164.0", "asset_count": 43},
    )
    monkeypatch.setattr(
        codex,
        "CodexProvider",
        lambda *a, **kw: SimpleNamespace(_build_codex_command=lambda: "unused"),
    )
    monkeypatch.setattr(native.catalog, "native_overrides", lambda *a: ["pinned", "app-server"])
    monkeypatch.setattr(mcp_readiness, "has_ready_signal", lambda *a: case != "ready")

    class Client:
        closed = False

        def __init__(self, *args, **kwargs):
            kwargs["owned"].append(self)
            if case == "construct":
                raise RuntimeError("constructor_failed_after_registration")

        def rpc(self, method, params):
            methods.append(method)
            assert method in {"initialize", "thread/start"}
            return {"thread": {"id": "private-thread"}} if method == "thread/start" else {}

        def send(self, message):
            assert message == {"method": "initialized"}

        def close(self):
            self.closed = True

    monkeypatch.setattr(native.catalog, "CatalogClient", Client)
    monkeypatch.setattr(
        native.catalog,
        "_catalog",
        lambda *a: (
            mcp_tool_policy.TOOL_PROFILES["supervision"] if case != "catalog" else set(),
            {"resources": ["exposed"] if case == "resource" else [], "resourceTemplates": []},
        ),
    )
    monkeypatch.setattr(
        native.catalog, "_call", lambda client, thread, tool, args: call(tool, args)
    )
    expected = lambda key: {
        "terminal_id": key,
        "state": "pending",
        "generation": "a" * 32,
        "allowed_actions": ["verify", "cancel"],
    }
    (tmp_path / "schemas/v2").mkdir(parents=True)
    (tmp_path / "schemas/v2/McpServerToolCallResponse.json").write_text(
        '{"type":"object","required":["content"],"properties":{"content":{"type":"array"},"isError":{"type":["boolean","null"]}}}'
    )

    def assert_ready():
        if case == "observer":
            raise RuntimeError("observer_not_ready")

    observations = SimpleNamespace(
        caller_id="aaaabbbb",
        child_id="ccccdddd",
        foreign_id="eeeeffff",
        inspect_expected=expected,
        observe_foreign=lambda: state,
        assert_ready=assert_ready,
        verify_native_reads=lambda: True,
    )
    args = dict(
        binary=Path("/pinned/codex"),
        runtime_manifest=Path("/private/runtime.json"),
        runtime_sha256="private",
        schema_root=tmp_path / "schemas",
        env={},
        overlay=(),
        owned=owned,
        port=1234,
        caller_id="aaaabbbb",
        child_id="ccccdddd",
        foreign_id="eeeeffff",
        observations=observations,
    )
    if case == "ok":
        result = native.run_case(tmp_path, **args)
        assert result["catalog_count"] == 11 and result["model_turns_requested"] == 0
        assert result["target_receipt"] == "fixture_seeded_postgresql"
    else:
        with pytest.raises(RuntimeError):
            native.run_case(tmp_path, **args)
    assert all(client.closed for client in owned)
    assert methods == ([] if case in {"observer", "construct"} else ["initialize", "thread/start"])
    if case != "observer":
        assert (
            "cao_tool_profile: supervision"
            in (tmp_path / "cao/agents/supervision-authority.md").read_text()
        )
