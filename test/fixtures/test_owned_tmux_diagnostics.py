"""Cleanup diagnostics must preserve strict owned-resource decisions."""

import os
import shutil
import socket
from test.fixtures import owned_tmux
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

pytestmark = pytest.mark.offline_database


@pytest.fixture
def root(request):
    value = owned_tmux.create_private_tmux_tmpdir()
    request.addfinalizer(lambda: shutil.rmtree(value, ignore_errors=True))
    return value


def fake_tmux(verdict):
    return SimpleNamespace(
        _SERVER_ABSENT="absent",
        _SERVER_ALIVE="alive",
        _tmux_server_liveness=Mock(return_value=verdict),
    )


def test_owner_must_stop_before_any_socket_probe_even_with_diagnostics(root, tmp_path):
    module = fake_tmux("absent")
    evidence = []
    assert not owned_tmux.cleanup_private_tmux_root(
        root, tmp_path, module, owner_stopped=False, diagnostics=evidence
    )
    module._tmux_server_liveness.assert_not_called()
    assert root.is_dir()
    assert evidence[0]["reason"] == "owner_not_stopped"


def test_unknown_liveness_records_rejection_without_removing_socket_or_foreign_data(root, tmp_path):
    directory = root / f"tmux-{os.getuid()}"
    directory.mkdir(mode=0o700)
    path = directory / "default"
    sentinel = tmp_path / "foreign"
    sentinel.write_text("preserve")
    module = fake_tmux("unknown")
    evidence = []
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path))
        assert not owned_tmux.cleanup_private_tmux_root(
            root, tmp_path, module, owner_stopped=True, diagnostics=evidence
        )
        assert path.is_socket()
    assert sentinel.read_text() == "preserve"
    assert evidence[0]["reason"] == "liveness_unknown"
    assert evidence[0]["liveness"] == "unknown"


def test_known_absence_retains_successful_bool_and_no_rejected_guard(root, tmp_path):
    evidence = []
    assert owned_tmux.cleanup_private_tmux_root(
        root, tmp_path, fake_tmux("absent"), owner_stopped=True, diagnostics=evidence
    )
    assert not root.exists()
    assert evidence == []


def test_root_still_present_after_removal_is_a_recorded_refusal(root, tmp_path):
    evidence = []
    with patch.object(owned_tmux.shutil, "rmtree", return_value=None):
        assert not owned_tmux.cleanup_private_tmux_root(
            root, tmp_path, fake_tmux("absent"), owner_stopped=True, diagnostics=evidence
        )
    assert root.is_dir()
    assert evidence[0]["reason"] == "root_not_removed"
