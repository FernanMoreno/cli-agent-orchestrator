"""No-model real API/PostgreSQL/tmux preflight for scoped mock peer setup."""

import os
from pathlib import Path
from test.e2e import test_real_provider_matrix as matrix
from test.fixtures import owned_tmux
from test.fixtures.cao_server import _pick_free_port, _start_cao_server
from test.live_provider.native_discovery_fixture import (
    PROFILE,
    OwnedDiscoveryFixture,
    write_mock_profile,
)
from test.live_provider.native_read_bridge import validate_discovery_rows

import pytest

pytestmark = pytest.mark.postgres


def test_owned_mock_api_fixture_has_real_peer_and_decoys(postgres_service, tmp_path):
    repo = Path(__file__).resolve().parents[2]
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    socket_root = owned_tmux.create_private_tmux_tmpdir()
    server = None
    fixture = None
    session = None
    try:
        server = _start_cao_server(
            home,
            _pick_free_port(),
            postgres_service=postgres_service,
            extra_env={
                "TMUX": "",
                "TMUX_TMPDIR": str(socket_root),
                "TMPDIR": str(socket_root),
                "PATH": str(repo / "test/providers/fixtures/bin") + ":/usr/local/bin:/usr/bin:/bin",
            },
        )
        write_mock_profile(home)
        caller, session = matrix._create_parent(server, "mock_cli", PROFILE)
        group = ["t229", caller]
        response = matrix._request(
            "PATCH", server.url + f"/terminals/{caller}/group", json={"group": group}
        )
        assert response.status_code == 200 and response.json()["group"] == group
        fixture = OwnedDiscoveryFixture(server, home, session, caller, group, lambda: None)
        evidence = fixture.setup()
        assert evidence["expected_count"] == 1 and evidence["decoy_count"] == 2
        response = matrix._request("GET", server.url + f"/terminals/{caller}/siblings")
        assert response.status_code == 200
        validate_discovery_rows(response.json(), fixture.expectation)
        fixture.assert_live()
        fixture.cleanup()
    finally:
        try:
            try:
                if fixture is not None:
                    fixture.cleanup()
            finally:
                if server is not None and session is not None:
                    matrix._delete_session(server, session)
        finally:
            try:
                if server is not None:
                    server.stop()
            finally:
                from cli_agent_orchestrator.clients import tmux

                assert owned_tmux.cleanup_private_tmux_root(
                    socket_root, home, tmux, owner_stopped=True
                )
