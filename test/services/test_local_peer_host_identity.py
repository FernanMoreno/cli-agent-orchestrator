"""The user-wide peer host fence is private, stable filesystem identity."""

import os
import stat
from uuid import UUID

import pytest

from cli_agent_orchestrator.services.local_peers.host_identity import (
    HostIdentityError,
    current_host_identity,
)


def test_host_identity_is_stable_private_and_bound_to_os_user(tmp_path):
    directory = tmp_path / "local-peers"

    first = current_host_identity(directory)
    second = current_host_identity(directory)

    assert UUID(first.host_id)
    assert second == first
    assert first.owner_uid == os.geteuid()
    assert stat.S_IMODE((directory / "host-id").stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_host_identity_rejects_replaced_or_public_file(tmp_path):
    directory = tmp_path / "local-peers"
    identity = current_host_identity(directory)
    path = directory / "host-id"
    path.chmod(0o644)

    with pytest.raises(HostIdentityError):
        current_host_identity(directory)

    path.chmod(0o600)
    path.write_text("not-a-uuid\n")
    with pytest.raises(HostIdentityError):
        current_host_identity(directory)
    assert identity.host_id != "not-a-uuid"


def test_host_identity_rejects_symlinked_identity_file(tmp_path):
    directory = tmp_path / "local-peers"
    directory.mkdir(mode=0o700)
    other = tmp_path / "other"
    other.write_text("00000000-0000-0000-0000-000000000000\n")
    (directory / "host-id").symlink_to(other)

    with pytest.raises(HostIdentityError):
        current_host_identity(directory)
