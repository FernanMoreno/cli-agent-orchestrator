"""Ownership witnesses follow genuine guarded SDK return, including failures."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize(
    "guarded,raises,registered",
    [(False, False, False), (True, False, True), (True, True, True), (True, False, False)],
)
def test_main_registers_before_run_and_publishes_only_after_normal_return(
    monkeypatch, guarded, raises, registered
):
    from cli_agent_orchestrator.mcp_server import server
    from cli_agent_orchestrator.utils import mcp_owner_stop

    events = []
    registration = object() if registered else None
    snapshot = {"test": "snapshot"}
    failure = RuntimeError("controlled SDK failure")

    def sdk_run():
        events.append("sdk_run")
        if raises:
            raise failure

    def guarded_run(run):
        events.append("guard_enter")
        run()
        events.append("guard_return")

    def register():
        events.append("register")
        return registration

    def publish(actual_registration, actual_snapshot):
        assert actual_registration is registration
        assert actual_snapshot is snapshot
        events.append("publish")
        return True

    monkeypatch.delenv("CAO_MCP_READY_FILE", raising=False)
    monkeypatch.delenv("CAO_MCP_READY_TOKEN", raising=False)
    monkeypatch.setattr(server, "_install_tool_profile_middleware_if_requested", lambda _mcp: None)
    monkeypatch.setattr(
        server, "_install_interaction_pull_flow_middleware_if_requested", lambda _mcp: None
    )
    monkeypatch.setattr(server.mcp, "run", sdk_run)
    monkeypatch.setattr(mcp_owner_stop, "register_owned_mcp", register)
    publisher = Mock(side_effect=publish)
    monkeypatch.setattr(mcp_owner_stop, "publish_owned_mcp_normal_return", publisher)
    guard = SimpleNamespace(run=guarded_run, state=SimpleNamespace(snapshot=lambda: snapshot))
    monkeypatch.setattr(server, "_physical_stdio_guard", guard if guarded else None)
    if raises:
        with pytest.raises(RuntimeError) as caught:
            server.main()
        assert caught.value is failure
    else:
        server.main()
    if not guarded:
        assert events == ["sdk_run"]
    elif raises:
        assert events == ["register", "guard_enter", "sdk_run"]
        publisher.assert_not_called()
    elif registered:
        assert events == ["register", "guard_enter", "sdk_run", "guard_return", "publish"]
    else:
        assert events == ["register", "guard_enter", "sdk_run", "guard_return"]
        publisher.assert_not_called()
