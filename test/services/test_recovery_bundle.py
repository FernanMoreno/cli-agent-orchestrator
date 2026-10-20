"""Unit boundaries for the PostgreSQL recovery API and bounded file reader."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from cli_agent_orchestrator.clients.postgres import DatabaseTarget
from cli_agent_orchestrator.services.recovery_bundle import (
    OfflineRecoveryCapture,
    RecoveryBundleError,
    RecoveryCaptureSource,
)


def _target(tmp_path: Path, *, scope: str = "main") -> DatabaseTarget:
    return DatabaseTarget(
        host="127.0.0.1",
        port=5432,
        database="cao_test",
        user="cao_runtime",
        schema="cao_main",
        store_id=str(uuid.uuid4()),
        secret_file=tmp_path / "runtime-secret.json",
        sslmode="disable",
        home=tmp_path,
        scope=scope,
    )


@pytest.mark.offline_database
def test_recovery_capture_requires_an_explicit_main_postgres_target(tmp_path):
    target = _target(tmp_path)
    source = RecoveryCaptureSource(database=target)

    assert source.database is target
    assert source.database.scope == "main"

    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        RecoveryCaptureSource(database=tmp_path / "database-path")

    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        RecoveryCaptureSource(database=_target(tmp_path, scope="auth"))


@pytest.mark.offline_database
def test_recovery_capture_requires_the_work_authority_and_durable_cut(tmp_path):
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.services.work_authority import OfflineCutLease, WorkAuthority

    target = _target(tmp_path)
    authority = WorkAuthority(WorkRepository(target))
    lease = OfflineCutLease(
        id="a" * 32,
        store_identity="/private/store",
        operator_principal_id="operator",
        owner="work",
        scope="registered-work-writers",
        epoch=1,
        fence=1,
        expires_at=100.0,
        revision=1,
    )

    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        OfflineRecoveryCapture(object(), lease, object())

    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        OfflineRecoveryCapture(authority, object(), object())


@pytest.mark.offline_database
def test_hash_reader_rejects_a_file_that_grows_during_bounded_read(tmp_path, monkeypatch):
    from cli_agent_orchestrator.services import recovery_bundle as module

    payload = tmp_path / "growing-object"
    payload.write_bytes(b"four")
    descriptor = os.open(payload, os.O_RDWR)
    before = os.fstat(descriptor)
    original_read = os.read
    appended = False

    def grow_after_first_read(fd: int, size: int) -> bytes:
        nonlocal appended
        block = original_read(fd, size)
        if not appended:
            with payload.open("ab") as writer:
                writer.write(b"12345678")
            appended = True
        return block

    monkeypatch.setattr(module.os, "read", grow_after_first_read)
    try:
        with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
            module._hash_open_file(
                descriptor,
                before=before,
                scan_text=False,
                maximum_size=8,
            )
    finally:
        os.close(descriptor)

    assert appended
