"""Real legacy inbox restart with an explicitly owned tmux namespace."""

from test.e2e import test_work_lifecycle as lifecycle
from test.fixtures import owned_tmux

import pytest

pytestmark = pytest.mark.postgres


def test_queued_receipt_delivery_survives_owned_api_restart(
    tmp_path, postgres_targets, monkeypatch
):
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

    for target in (postgres_targets.main, postgres_targets.auth, postgres_targets.peer):
        bootstrap_scope(target)
    socket_root = owned_tmux.create_private_tmux_tmpdir()
    servers = []
    original_start = lifecycle._start_cao_server

    def start(*args, **kwargs):
        server = original_start(*args, **kwargs)
        servers.append(server)
        return server

    monkeypatch.setenv("TMUX", "")
    monkeypatch.setenv("TMUX_TMPDIR", str(socket_root))
    monkeypatch.setenv("TMPDIR", str(socket_root))
    monkeypatch.setattr(lifecycle, "_start_cao_server", start)
    try:
        lifecycle.test_real_mock_cli_session_and_inbox_survive_server_restart(
            tmp_path, postgres_targets
        )
    finally:
        stopped = False
        try:
            for server in servers:
                server.stop()
            stopped = bool(servers)
        finally:
            from cli_agent_orchestrator.clients import tmux

            diagnostics = []
            assert owned_tmux.cleanup_private_tmux_root(
                socket_root,
                tmp_path / "isolated-cao-home",
                tmux,
                owner_stopped=stopped,
                diagnostics=diagnostics,
            ), diagnostics
