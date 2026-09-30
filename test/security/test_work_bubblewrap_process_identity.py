"""The parent receives restart identity before releasing a Bubblewrap worker."""

import hashlib
import json
import os
from pathlib import Path
from test.clients.test_work_bubblewrap_process_identity import _sent_attempt
from test.security.test_work_bubblewrap_composition import (
    _TRUSTED_BWRAP_SHA256,
    _WORKER_MARKER,
    _real_bubblewrap,
    _worker_fixture,
)

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services import work_bubblewrap_composition as composition


def _digest(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def test_parent_persists_exact_process_identity_before_worker_release(tmp_path):
    bwrap = _real_bubblewrap()
    executable_identity, executable = _worker_fixture()
    store = WorkRepository(tmp_path / "work.db")
    store.initialize()
    _sent_attempt(store)
    identities = []

    def persist_and_authorize(ack, process_identity):
        assert ack.sha256_digest == executable_identity.sha256_digest
        assert set(process_identity) == {
            "version",
            "kind",
            "boot_id",
            "monitor_pid",
            "monitor_start_time_ticks",
            "init_pid",
            "init_start_time_ticks",
            "init_parent_pid",
            "pid_namespace",
            "net_namespace",
            "ipc_namespace",
            "monitor_argv_sha256",
            "monitor_executable_sha256",
            "identity_sha256",
        }
        assert (
            process_identity["boot_id"]
            == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        )
        assert process_identity["monitor_pid"] > 0
        assert process_identity["init_parent_pid"] == process_identity["monitor_pid"]
        assert process_identity["init_pid"] != process_identity["monitor_pid"]
        assert process_identity["monitor_start_time_ticks"] > 0
        assert process_identity["init_start_time_ticks"] > 0
        for name in ("pid_namespace", "net_namespace", "ipc_namespace"):
            namespace = os.stat(f"/proc/{process_identity['init_pid']}/ns/{name.split('_')[0]}")
            assert process_identity[name] == [namespace.st_dev, namespace.st_ino]
        assert (
            process_identity["monitor_argv_sha256"]
            == hashlib.sha256(
                Path(f"/proc/{process_identity['monitor_pid']}/cmdline").read_bytes()
            ).hexdigest()
        )
        assert process_identity["monitor_executable_sha256"] == _TRUSTED_BWRAP_SHA256
        assert process_identity["identity_sha256"] == _digest(
            {key: value for key, value in process_identity.items() if key != "identity_sha256"}
        )
        store.persist_bubblewrap_process_identity("a", 1, process_identity)
        assert store.read_bubblewrap_process_identity("a", 1) == process_identity
        identities.append(process_identity)
        return True

    result = composition.launch_staged_static_elf(
        executable_identity,
        executable,
        persist_and_authorize=persist_and_authorize,
        bwrap_path=bwrap,
        bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
    )
    assert len(identities) == 1
    assert WorkRepository(store.path).read_bubblewrap_process_identity("a", 1) == identities[0]
    assert result.stdout == _WORKER_MARKER


def test_identity_persistence_error_withholds_worker_release():
    bwrap = _real_bubblewrap()
    executable_identity, executable = _worker_fixture()
    observed = []

    def failed_persistence(_ack, process_identity):
        observed.append(process_identity)
        raise OSError("identity persistence unavailable")

    with pytest.raises(composition.WorkBubblewrapParentGateFailed) as raised:
        composition.launch_staged_static_elf(
            executable_identity,
            executable,
            persist_and_authorize=failed_persistence,
            bwrap_path=bwrap,
            bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
        )
    assert len(observed) == 1
    assert _WORKER_MARKER not in raised.value.stdout
    assert raised.value.cleanup_confirmed is True


def test_callback_without_process_identity_never_releases_worker():
    bwrap = _real_bubblewrap()
    executable_identity, executable = _worker_fixture()
    called = []

    def legacy_callback(_ack):
        called.append(True)
        return True

    with pytest.raises(composition.WorkBubblewrapParentGateFailed) as raised:
        composition.launch_staged_static_elf(
            executable_identity,
            executable,
            persist_and_authorize=legacy_callback,
            bwrap_path=bwrap,
            bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
        )
    assert called == []
    assert _WORKER_MARKER not in raised.value.stdout
    assert raised.value.cleanup_confirmed is True
