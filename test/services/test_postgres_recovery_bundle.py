"""Real PostgreSQL capture and restore proofs for Spec 021 recovery bundles."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from psycopg import sql
from psycopg.errors import CheckViolation

from cli_agent_orchestrator.clients.postgres import DatabaseTarget, connect
from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services import recovery_bundle as recovery_bundle_module
from cli_agent_orchestrator.services.recovery_bundle import (
    OfflineRecoveryCapture,
    PostgresArchiveRunner,
    RecoveryBundleError,
    RecoveryCaptureSource,
    restore_recovery_bundle,
    verify_recovery_bundle,
)
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence

pytestmark = pytest.mark.postgres


class _OwnedDockerArchiveRunner(PostgresArchiveRunner):
    """Run the real PostgreSQL archive tools inside the fixture-owned container."""

    def __init__(self, postgres_service, tmp_path: Path):
        self.container_id = postgres_service.metadata["container_id"]
        self.tmp_path = tmp_path

    @staticmethod
    def _passfile_value(value: str) -> str:
        if any(char in value for char in ("\n", "\r", "\x00")):
            raise RuntimeError("invalid test credential field")
        return value.replace("\\", "\\\\").replace(":", "\\:")

    def _run(
        self,
        target,
        executable: str,
        archive: Path,
        *,
        dump: bool,
        snapshot_id: str | None = None,
    ) -> None:
        migration = target.migration_target()
        params = migration.connection_parameters()
        remote = f"/tmp/cao-recovery-test-{secrets.token_hex(8)}.pgpass"
        pass_line = (
            ":".join(
                self._passfile_value(value)
                for value in (
                    "127.0.0.1",
                    "5432",
                    migration.database,
                    migration.user,
                    params["password"],
                )
            )
            + "\n"
        )
        install = subprocess.run(
            [
                "docker",
                "exec",
                "--interactive",
                "--user",
                "postgres",
                self.container_id,
                "sh",
                "-c",
                f"umask 077; cat > {remote}",
            ],
            input=pass_line.encode(),
            capture_output=True,
            timeout=20,
            check=False,
        )
        if install.returncode != 0:
            subprocess.run(
                ["docker", "exec", "--user", "postgres", self.container_id, "rm", "-f", remote],
                capture_output=True,
                timeout=20,
                check=False,
            )
            raise RuntimeError("cannot provision private test passfile")
        command = [
            "docker",
            "exec",
            *(["--interactive"] if not dump else []),
            "--user",
            "postgres",
            "--env",
            f"PGPASSFILE={remote}",
            self.container_id,
            executable,
            "--no-password",
            "--host",
            "127.0.0.1",
            "--port",
            "5432",
            "--username",
            migration.user,
            "--dbname",
            migration.database,
        ]
        try:
            if dump:
                command.extend(
                    [
                        "--format=custom",
                        "--no-owner",
                        "--no-privileges",
                        "--no-comments",
                        "--schema",
                        target.schema,
                    ]
                )
                if snapshot_id is not None:
                    command.extend(["--snapshot", snapshot_id])
                with archive.open("wb") as output:
                    result = subprocess.run(
                        command,
                        stdout=output,
                        stderr=subprocess.PIPE,
                        timeout=120,
                        check=False,
                    )
                    output.flush()
                    os.fsync(output.fileno())
            else:
                command.extend(
                    ["--exit-on-error", "--single-transaction", "--no-owner", "--no-acl"]
                )
                with archive.open("rb") as input_stream:
                    result = subprocess.run(
                        command,
                        stdin=input_stream,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=120,
                        check=False,
                    )
        finally:
            subprocess.run(
                ["docker", "exec", "--user", "postgres", self.container_id, "rm", "-f", remote],
                capture_output=True,
                timeout=20,
                check=False,
            )
        if result.returncode != 0:
            raise RuntimeError(f"{executable} failed in owned test service")

    def dump(self, target, destination: Path, *, snapshot_id: str | None = None) -> None:
        self._run(target, "pg_dump", destination, dump=True, snapshot_id=snapshot_id)

    def restore(self, target, archive: Path) -> None:
        self._run(target, "pg_restore", archive, dump=False)

    def render_sql(self, archive: Path) -> bytes:
        remote = f"/tmp/cao-recovery-render-{secrets.token_hex(8)}.dump"
        install = subprocess.run(
            [
                "docker",
                "exec",
                "--interactive",
                "--user",
                "postgres",
                self.container_id,
                "sh",
                "-c",
                f"umask 077; cat > {remote}",
            ],
            input=archive.read_bytes(),
            capture_output=True,
            timeout=30,
            check=False,
        )
        if install.returncode != 0:
            raise RuntimeError("cannot stage private archive for SQL inspection")
        try:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    "--user",
                    "postgres",
                    self.container_id,
                    "pg_restore",
                    "--no-owner",
                    "--no-acl",
                    "--file=-",
                    remote,
                ],
                capture_output=True,
                timeout=120,
                check=False,
            )
        finally:
            subprocess.run(
                ["docker", "exec", "--user", "postgres", self.container_id, "rm", "-f", remote],
                capture_output=True,
                timeout=20,
                check=False,
            )
        if result.returncode != 0:
            raise RuntimeError("cannot render private archive SQL")
        return result.stdout


@pytest.fixture
def native_store(postgres_targets, tmp_path):
    target = postgres_targets.main
    bootstrap_scope(target)
    repository = WorkRepository(target, filesystem_home=postgres_targets.home)
    repository.initialize()
    for root in (
        repository.result_content_root,
        repository.delivery_content_root,
        repository.executable_content_root,
        postgres_targets.home / "memory",
    ):
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = b"#!/bin/sh\nprintf recovery\n"
    digest = hashlib.sha256(payload).hexdigest()
    object_path = repository.executable_content_root / digest
    object_path.write_bytes(payload)
    object_path.chmod(0o600)
    memory_payload = b"# Recovery memory\nThis file is part of the test fixture.\n"
    memory_file = postgres_targets.home / "memory" / "recovery-note.md"
    memory_file.write_bytes(memory_payload)
    memory_file.chmod(0o600)
    with connect(target) as connection:
        with connection.transaction():
            connection.execute(
                "INSERT INTO work_executable_contents "
                "(content_hash,immutable_location,byte_length,created_at) VALUES (%s,%s,%s,%s)",
                (digest, digest, len(payload), 1.0),
            )
            connection.execute(
                "INSERT INTO memory_metadata "
                "(id,key,memory_type,scope,scope_id,source_kind,file_path,tags,"
                "created_at,updated_at,access_count) "
                "VALUES (%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,0)",
                (
                    "memory-recovery-note",
                    "recovery-note",
                    "fact",
                    "global",
                    "native",
                    str(memory_file),
                    "",
                    datetime.now(timezone.utc),
                    datetime.now(timezone.utc),
                ),
            )
    operator = auth._verified_principal(
        "https://spec021.recovery.test",
        "recovery-operator",
        [auth.SCOPE_ADMIN],
        "jwt",
    )
    job = repository.create_job(
        project_id="recovery-project",
        principal_id=operator.id,
        allowed_providers=["mock_cli"],
        grant_id="recovery-root-grant",
    )
    WorkAuthority(repository).issue_root(
        operator,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(tools={"Read"}, paths={str(tmp_path)}),
        expires_at=datetime.now(timezone.utc).timestamp() + 3600,
    )
    work = repository.admit_work(
        job_id=job["id"],
        operation_kind="launch",
        idempotency_key="offline-recovery-attempt",
        request_hash="a" * 64,
        contract_id="recovery-contract",
        snapshot_id=None,
        provider="mock_cli",
        actor_id=operator.id,
        lease_seconds=3600,
    )
    attempt = work["attempts"][-1]
    repository.transition_attempt(
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_revision=attempt["revision"],
        expected_state=attempt["state"],
        target="sent",
        actor_id="recovery-operator",
        event_id=uuid.uuid4().hex,
        evidence=TransitionEvidence(
            generation=attempt["generation"],
            expected_generation=attempt["generation"],
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        ),
    )
    return target, repository, postgres_targets.home, digest


def _capture_context(native_store, postgres_service, tmp_path, *, runner=None, ttl_seconds=180):
    target, repository, home, digest = native_store
    authority = WorkAuthority(repository)
    operator = auth._verified_principal(
        "https://spec021.recovery.test", "postgres-recovery-admin", [auth.SCOPE_ADMIN], "jwt"
    )
    lease = authority.create_offline_cut(operator, ttl_seconds=ttl_seconds)
    source = RecoveryCaptureSource(
        database=target,
        immutable_result_root=repository.result_content_root,
        delivery_content_root=repository.delivery_content_root,
        executable_content_root=repository.executable_content_root,
        memory_root=home / "memory",
    )
    selected_runner = runner or _OwnedDockerArchiveRunner(postgres_service, tmp_path)
    capture = OfflineRecoveryCapture(authority, lease, selected_runner)
    return authority, operator, lease, source, capture, digest


def _capture(native_store, postgres_service, tmp_path):
    _authority, _operator, _lease, source, capture, digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    receipt = capture.capture(source, tmp_path / "postgres-recovery-bundle")
    assert verify_recovery_bundle(receipt) == receipt
    return receipt, source, digest


def _make_destination(postgres_service, source, tmp_path):
    from psycopg import sql

    target = source.database
    database_name = "c21restore_" + secrets.token_hex(5)
    with postgres_service.connect_admin() as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(database_name), sql.Identifier(target.migration_user)
            )
        )
        admin.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database_name), sql.Identifier(target.user)
            )
        )
    home = tmp_path / "restored-cao-home"
    home.mkdir(mode=0o700)
    return replace(target, database=database_name, home=home), database_name, home


def test_postgres_bundle_captures_and_restores_blocked_target(
    native_store, postgres_service, tmp_path
):
    receipt, source, executable_digest = _capture(native_store, postgres_service, tmp_path)
    assert receipt.bundle_path.is_dir()
    manifest = json.loads((receipt.bundle_path / "manifest.json").read_text())
    assert manifest["format_version"] == 3
    assert manifest["source"]["store_id"] == native_store[0].store_id
    assert manifest["cut"]["before"]["observed_at"] <= manifest["cut"]["after"]["observed_at"]
    assert manifest["cut"]["after"]["observed_at"] <= manifest["cut"]["promote"]["observed_at"]
    assert all(
        manifest["cut"][phase]["writer_count"] == 0
        and manifest["cut"][phase]["inventory"]
        == {
            "fingerprint": manifest["inventory"]["fingerprint"],
            "profile_version": manifest["inventory"]["profile_version"],
        }
        for phase in ("before", "after", "promote")
    )
    manifest_text = json.dumps(manifest, sort_keys=True)
    assert str(native_store[2]) not in manifest_text
    assert str(native_store[0].secret_file) not in manifest_text
    assert native_store[0].database not in manifest_text
    assert native_store[0].host not in manifest_text
    assert manifest["inventory"]["rows"]["work_executable_contents"]["count"] == 1
    assert {
        "role": "executable-content",
        "identifier": executable_digest,
        "digest": executable_digest,
        "size": len(b"#!/bin/sh\nprintf recovery\n"),
        "relative_path": None,
    } in manifest["references"]

    destination, database_name, destination_home = _make_destination(
        postgres_service, source, tmp_path
    )
    try:
        restored = restore_recovery_bundle(
            receipt,
            destination,
            filesystem_home=destination_home,
            runner=_OwnedDockerArchiveRunner(postgres_service, tmp_path),
        )
        assert restored.execution_state == "blocked_restore"
        assert restored.source_store_id == source.database.store_id
        with connect(source.database, readonly=True) as connection:
            source_work_id = connection.execute(
                "SELECT id FROM work_items WHERE idempotency_key=%s",
                ("offline-recovery-attempt",),
            ).fetchone()["id"]
            assert connection.execute(
                "SELECT state FROM work_attempts WHERE work_item_id=%s", (source_work_id,)
            ).fetchone() == ("sent",)
        object_path = (
            WorkRepository(destination, filesystem_home=destination_home).executable_content_root
            / executable_digest
        )
        assert object_path.read_bytes() == b"#!/bin/sh\nprintf recovery\n"
        restored_memory = destination_home / "memory" / "recovery-note.md"
        assert restored_memory.read_bytes() == (
            b"# Recovery memory\nThis file is part of the test fixture.\n"
        )
        with connect(destination, readonly=True) as connection:
            assert connection.execute(
                "SELECT state FROM work_attempts WHERE work_item_id=("
                "SELECT id FROM work_items WHERE idempotency_key=%s)",
                ("offline-recovery-attempt",),
            ).fetchone() == ("reconcile",)
        restored_repository = WorkRepository(destination, filesystem_home=destination_home)
        with restored_repository.read_snapshot() as connection:
            from cli_agent_orchestrator.clients.work_repository import SchemaMismatch

            with pytest.raises(SchemaMismatch, match="recovery context blocks execution"):
                restored_repository.assert_execution_allowed(connection)
    finally:
        with postgres_service.connect_admin() as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=%s AND pid<>pg_backend_pid()",
                (database_name,),
            )
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )


def test_bundle_refuses_source_target_and_tampered_object(native_store, postgres_service, tmp_path):
    receipt, _source_config, _digest = _capture(native_store, postgres_service, tmp_path)
    target, _repository, home, _ = native_store
    with pytest.raises(RecoveryBundleError):
        restore_recovery_bundle(
            receipt,
            target,
            filesystem_home=home,
            runner=_OwnedDockerArchiveRunner(postgres_service, tmp_path),
        )

    manifest = json.loads((receipt.bundle_path / "manifest.json").read_text())
    object_path = receipt.bundle_path / "objects" / manifest["dump"]["digest"]
    with object_path.open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(RecoveryBundleError):
        verify_recovery_bundle(receipt)


def test_capture_dump_failure_removes_private_staging_and_records_rejection(
    native_store, postgres_service, tmp_path
):
    class FailingDumpRunner(PostgresArchiveRunner):
        def dump(self, target, destination, *, snapshot_id):
            raise OSError("private dump failure")

        def restore(self, target, archive):
            raise AssertionError("restore must not run during capture")

    authority, _operator, lease, source, capture, _digest = _capture_context(
        native_store,
        postgres_service,
        tmp_path,
        runner=FailingDumpRunner(),
    )
    destination = tmp_path / "failed-capture"

    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        capture.capture(source, destination)

    assert not destination.exists()
    assert not tuple(tmp_path.glob(".postgres-recovery-*"))
    repository = native_store[1]
    with repository.read_snapshot() as connection:
        rows = connection.execute(
            "SELECT phase FROM work_offline_cut_rejections WHERE lease_id=%s", (lease.id,)
        ).fetchall()
    assert rows
    assert authority.verify_offline_cut(lease, source_database=source.database)


def test_capture_revalidates_durable_cut_after_authority_restart(
    native_store, postgres_service, tmp_path
):
    _authority, _operator, lease, source, _old_capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    restarted_authority = WorkAuthority(native_store[1])
    restarted_capture = OfflineRecoveryCapture(
        restarted_authority,
        lease,
        _OwnedDockerArchiveRunner(postgres_service, tmp_path),
    )
    receipt = restarted_capture.capture(source, tmp_path / "restarted-authority-capture")

    assert verify_recovery_bundle(receipt) == receipt
    assert receipt.capture_id
    assert receipt.publication_revision == lease.revision + 1


def test_capture_rejects_a_work_authority_for_another_postgres_store(
    native_store, postgres_service, tmp_path
):
    from dataclasses import replace

    target, repository, _home, _digest = native_store
    _authority, _operator, _lease, _source, capture, _unused = _capture_context(
        native_store, postgres_service, tmp_path
    )
    wrong_target = replace(
        target,
        database="wrong_cao_database",
        store_id=str(uuid.uuid4()),
        schema="wrong_cao_schema",
    )
    wrong_source = RecoveryCaptureSource(
        database=wrong_target,
        immutable_result_root=repository.result_content_root,
        delivery_content_root=repository.delivery_content_root,
        executable_content_root=repository.executable_content_root,
        memory_root=native_store[2] / "memory",
    )
    destination = tmp_path / "wrong-authority-store"

    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        capture.capture(wrong_source, destination)

    assert not destination.exists()
    assert not tuple(tmp_path.glob(".postgres-recovery-*"))


def test_capture_rejects_unregistered_database_mutation_during_dump(
    native_store, postgres_service, tmp_path
):
    target, _repository, _home, _digest = native_store
    with connect(target, readonly=True) as connection:
        original_created_at = connection.execute(
            "SELECT created_at FROM work_jobs ORDER BY id LIMIT 1"
        ).fetchone()[0]

    class MutatingDumpRunner(_OwnedDockerArchiveRunner):
        def dump(self, selected_target, destination, *, snapshot_id):
            super().dump(selected_target, destination, snapshot_id=snapshot_id)
            # Deliberately bypass the Work writer registry after the before
            # observation. The after inventory must still detect the change.
            with connect(selected_target) as connection:
                with connection.transaction():
                    connection.execute(
                        "UPDATE work_jobs SET created_at=created_at+1 WHERE id=("
                        "SELECT id FROM work_jobs ORDER BY id LIMIT 1)"
                    )

    _authority, _operator, _lease, source, capture, _unused = _capture_context(
        native_store,
        postgres_service,
        tmp_path,
        runner=MutatingDumpRunner(postgres_service, tmp_path),
    )
    destination = tmp_path / "mutation-during-dump"

    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        capture.capture(source, destination)

    with connect(target, readonly=True) as connection:
        assert (
            connection.execute("SELECT created_at FROM work_jobs ORDER BY id LIMIT 1").fetchone()[0]
            == original_created_at + 1
        )
    assert not destination.exists()
    assert not tuple(tmp_path.glob(".postgres-recovery-*"))


def test_pg_dump_uses_the_scanned_snapshot_for_transient_workflow_rows(
    native_store, postgres_service, tmp_path
):
    target, _repository, _home, _digest = native_store
    transient_run_id = "transient-capture-" + uuid.uuid4().hex
    transient_secret = "api_key = sk-1234567890abcdef1234567890abcdef"

    class TransientWorkflowWriterRunner(_OwnedDockerArchiveRunner):
        def dump(self, selected_target, destination, *, snapshot_id: str | None = None):
            with connect(selected_target) as connection:
                connection.execute(
                    "INSERT INTO workflow_run "
                    "(run_id,workflow_name,spec_snapshot,inputs_json,state,started_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        transient_run_id,
                        "unregistered-writer-proof",
                        "{}",
                        json.dumps({"credential": transient_secret}),
                        "completed",
                        "2026-10-11T00:00:00Z",
                    ),
                )
            try:
                super().dump(selected_target, destination, snapshot_id=snapshot_id)
            finally:
                with connect(selected_target) as connection:
                    connection.execute(
                        "DELETE FROM workflow_run WHERE run_id=%s", (transient_run_id,)
                    )

    _authority, _operator, _lease, source, capture, _digest = _capture_context(
        native_store,
        postgres_service,
        tmp_path,
        runner=TransientWorkflowWriterRunner(postgres_service, tmp_path),
    )
    receipt = capture.capture(source, tmp_path / "snapshot-consistent-capture")
    assert verify_recovery_bundle(receipt) == receipt

    manifest = json.loads((receipt.bundle_path / "manifest.json").read_text())
    dump_object = receipt.bundle_path / "objects" / manifest["dump"]["digest"]
    rendered = subprocess.run(
        [
            "docker",
            "exec",
            "--interactive",
            "--user",
            "postgres",
            postgres_service.metadata["container_id"],
            "pg_restore",
            "--no-password",
            "--file=-",
        ],
        input=dump_object.read_bytes(),
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert rendered.returncode == 0
    assert transient_run_id.encode() not in rendered.stdout
    assert transient_secret.encode() not in rendered.stdout
    with connect(target, readonly=True) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM workflow_run WHERE run_id=%s", (transient_run_id,)
            ).fetchone()
            is None
        )


def test_capture_does_not_publish_transient_existing_routine_body(
    native_store, postgres_service, tmp_path
):
    target, _repository, _home, _digest = native_store
    routine_name = "interaction_reject_immutable_row"
    secret_marker = "api_key = sk-1234567890abcdef1234567890abcdef"
    replacement_body = f"BEGIN RAISE EXCEPTION '{secret_marker}'; END;"

    class TransientRoutineWriterRunner(_OwnedDockerArchiveRunner):
        def dump(self, selected_target, destination, *, snapshot_id: str | None = None):
            migration_target = selected_target.migration_target()
            with connect(migration_target) as connection:
                original = connection.execute(
                    "SELECT pg_catalog.pg_get_functiondef(p.oid) "
                    "FROM pg_catalog.pg_proc p "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname=%s AND p.proname=%s",
                    (selected_target.schema, routine_name),
                ).fetchone()
                if original is None:
                    raise AssertionError("expected immutable-row trigger routine is missing")
                connection.execute(
                    sql.SQL(
                        "CREATE OR REPLACE FUNCTION {}() RETURNS trigger " "LANGUAGE plpgsql AS {}"
                    ).format(
                        sql.Identifier(selected_target.schema, routine_name),
                        sql.Literal(replacement_body),
                    )
                )
            try:
                super().dump(selected_target, destination, snapshot_id=snapshot_id)
            finally:
                with connect(migration_target) as connection:
                    connection.execute(original[0])

    runner = TransientRoutineWriterRunner(postgres_service, tmp_path)
    _authority, _operator, _lease, source, capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path, runner=runner
    )
    destination = tmp_path / "transient-routine-capture"
    try:
        receipt = capture.capture(source, destination)
    except RecoveryBundleError:
        assert not destination.exists()
        assert not tuple(tmp_path.glob(".postgres-recovery-*"))
    else:
        manifest = json.loads((receipt.bundle_path / "manifest.json").read_text())
        dump_object = receipt.bundle_path / "objects" / manifest["dump"]["digest"]
        rendered = runner.render_sql(dump_object)
        if secret_marker.encode() in rendered:
            pytest.fail("published PostgreSQL archive contains a transient routine body")
        if b"interaction history is append-only" not in rendered:
            pytest.fail("published PostgreSQL archive omitted the original routine definition")


def test_capture_rejects_routine_secret_in_exported_snapshot_after_source_reset(
    native_store, postgres_service, tmp_path, monkeypatch
):
    target, _repository, _home, _digest = native_store
    routine_name = "interaction_reject_immutable_row"
    secret_marker = "api_key = sk-1234567890abcdef1234567890abcdef"
    replacement_body = f"BEGIN RAISE EXCEPTION '{secret_marker}'; END;"
    migration_target = target.migration_target()
    with connect(migration_target, readonly=True) as reader:
        original = reader.execute(
            "SELECT pg_catalog.pg_get_functiondef(p.oid) "
            "FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname=%s AND p.proname=%s",
            (target.schema, routine_name),
        ).fetchone()
    if original is None:
        raise AssertionError("expected immutable-row trigger routine is missing")

    def write_function_definition(definition: str) -> None:
        with connect(migration_target) as writer:
            writer.execute(definition)

    def install_secret_definition() -> None:
        with connect(migration_target) as writer:
            writer.execute(
                sql.SQL(
                    "CREATE OR REPLACE FUNCTION {}() RETURNS trigger " "LANGUAGE plpgsql AS {}"
                ).format(
                    sql.Identifier(target.schema, routine_name),
                    sql.Literal(replacement_body),
                )
            )

    original_connect = recovery_bundle_module.connect
    original_inventory = recovery_bundle_module._database_inventory
    injected_before_snapshot = False
    reset_after_snapshot_export = False

    def connect_with_pre_snapshot_mutation(selected_target, readonly=False):
        nonlocal injected_before_snapshot
        if (
            not injected_before_snapshot
            and readonly
            and selected_target.identity == target.identity
        ):
            # Commit an unsafe body before the exported RR snapshot is created.
            install_secret_definition()
            injected_before_snapshot = True
        return original_connect(selected_target, readonly=readonly)

    def reset_before_inventory(*args, **kwargs):
        nonlocal reset_after_snapshot_export
        if not reset_after_snapshot_export and kwargs.get("connection") is not None:
            # This hook runs immediately after capture exports its snapshot and
            # before its first catalog/row inventory query.
            write_function_definition(original[0])
            reset_after_snapshot_export = True
        return original_inventory(*args, **kwargs)

    _authority, _operator, _lease, source, capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    monkeypatch.setattr(recovery_bundle_module, "connect", connect_with_pre_snapshot_mutation)
    monkeypatch.setattr(recovery_bundle_module, "_database_inventory", reset_before_inventory)
    destination = tmp_path / "routine-snapshot-secret-capture"
    try:
        with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
            capture.capture(source, destination)
    finally:
        if injected_before_snapshot:
            write_function_definition(original[0])

    assert injected_before_snapshot
    assert reset_after_snapshot_export
    assert not destination.exists()
    assert not tuple(tmp_path.glob(".postgres-recovery-*"))
    with connect(target, readonly=True) as reader:
        current_definition = reader.execute(
            "SELECT pg_catalog.pg_get_functiondef(p.oid) "
            "FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname=%s AND p.proname=%s",
            (target.schema, routine_name),
        ).fetchone()
    assert current_definition is not None
    assert secret_marker not in current_definition[0]


def test_capture_directory_fsync_failure_after_promotion_returns_no_receipt(
    native_store, postgres_service, tmp_path, monkeypatch
):
    authority, _operator, lease, source, capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    original_fsync_directory = recovery_bundle_module._fsync_directory

    def fail_after_parent_sync(path):
        original_fsync_directory(path)
        if path == tmp_path:
            raise OSError("injected directory fsync failure")

    monkeypatch.setattr(recovery_bundle_module, "_fsync_directory", fail_after_parent_sync)
    destination = tmp_path / "promoted-but-not-durable"

    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        capture.capture(source, destination)

    assert not destination.exists()
    assert not tuple(tmp_path.glob(".postgres-recovery-*"))
    with native_store[1].read_snapshot() as connection:
        assert connection.execute(
            "SELECT phase FROM work_offline_cut_rejections WHERE lease_id=%s",
            (lease.id,),
        ).fetchall()
    assert authority.verify_offline_cut(lease, source_database=source.database)


def test_expired_offline_cut_cannot_publish_a_capture(native_store, postgres_service, tmp_path):
    import time

    _authority, _operator, _lease, source, capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path, ttl_seconds=0.01
    )
    time.sleep(0.03)
    destination = tmp_path / "expired-cut"

    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        capture.capture(source, destination)

    assert not destination.exists()
    assert not tuple(tmp_path.glob(".postgres-recovery-*"))


def test_capture_rejects_changed_external_object_without_publishing(
    native_store, postgres_service, tmp_path
):
    _target, repository, _home, digest = native_store
    object_path = repository.executable_content_root / digest
    object_path.write_bytes(b"changed executable bytes")
    object_path.chmod(0o600)
    _authority, _operator, _lease, source, capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    destination = tmp_path / "changed-external"

    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        capture.capture(source, destination)

    assert not destination.exists()
    assert not tuple(tmp_path.glob(".postgres-recovery-*"))


def test_capture_rejects_executable_inode_replaced_after_inventory(
    native_store, postgres_service, tmp_path
):
    _target, repository, _home, digest = native_store
    object_path = repository.executable_content_root / digest
    outside = tmp_path / "replacement-executable"
    outside.write_bytes(b"replacement executable")
    outside.chmod(0o600)

    class ReplacingDumpRunner(_OwnedDockerArchiveRunner):
        def dump(self, target, destination, *, snapshot_id):
            super().dump(target, destination, snapshot_id=snapshot_id)
            object_path.unlink()
            object_path.symlink_to(outside)

    _authority, _operator, _lease, source, capture, _unused = _capture_context(
        native_store,
        postgres_service,
        tmp_path,
        runner=ReplacingDumpRunner(postgres_service, tmp_path),
    )
    destination = tmp_path / "replaced-executable"

    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        capture.capture(source, destination)

    assert not destination.exists()
    assert not tuple(tmp_path.glob(".postgres-recovery-*"))


def test_capture_requires_roots_for_every_referenced_external_object(
    native_store, postgres_service, tmp_path
):
    _authority, _operator, _lease, source, capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    source_without_executable_root = replace(source, executable_content_root=None)
    destination = tmp_path / "missing-executable-root"

    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        capture.capture(source_without_executable_root, destination)

    assert not destination.exists()
    assert not tuple(tmp_path.glob(".postgres-recovery-*"))


@pytest.mark.parametrize("damage", ["missing-file", "symlink", "wrong-size"])
def test_capture_rejects_broken_executable_references_before_publication(
    native_store, postgres_service, tmp_path, damage
):
    target, repository, _home, digest = native_store
    object_path = repository.executable_content_root / digest
    if damage == "missing-file":
        object_path.unlink()
    elif damage == "symlink":
        outside = tmp_path / "outside-executable"
        outside.write_bytes(b"#!/bin/sh\nexit 1\n")
        outside.chmod(0o600)
        object_path.unlink()
        object_path.symlink_to(outside)
    else:
        payload = b"#!/bin/sh\nprintf wrong-size\n"
        invalid_digest = hashlib.sha256(payload).hexdigest()
        invalid_path = repository.executable_content_root / invalid_digest
        invalid_path.write_bytes(payload)
        invalid_path.chmod(0o600)
        with connect(target) as connection:
            with connection.transaction():
                connection.execute(
                    "INSERT INTO work_executable_contents "
                    "(content_hash,immutable_location,byte_length,created_at) "
                    "VALUES (%s,%s,%s,%s)",
                    (invalid_digest, invalid_digest, len(payload) + 1, 2.0),
                )

    _authority, _operator, _lease, source, capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    destination = tmp_path / f"broken-executable-{damage}"
    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        capture.capture(source, destination)
    assert not destination.exists()
    assert not tuple(tmp_path.glob(".postgres-recovery-*"))


def test_executable_catalog_rejects_invalid_location_before_capture(
    native_store, postgres_service, tmp_path
):
    target, _repository, _home, digest = native_store
    invalid_digest = hashlib.sha256(b"invalid executable location").hexdigest()
    with connect(target) as connection:
        with connection.transaction():
            with pytest.raises(CheckViolation):
                with connection.transaction():
                    connection.execute(
                        "INSERT INTO work_executable_contents "
                        "(content_hash,immutable_location,byte_length,created_at) "
                        "VALUES (%s,%s,%s,%s)",
                        (invalid_digest, "0" * 64, 1, 2.0),
                    )
            with pytest.raises(CheckViolation, match="executable content catalog is immutable"):
                with connection.transaction():
                    connection.execute(
                        "UPDATE work_executable_contents SET immutable_location=%s "
                        "WHERE content_hash=%s",
                        ("0" * 64, digest),
                    )
        row = connection.execute(
            "SELECT immutable_location FROM work_executable_contents WHERE content_hash=%s",
            (digest,),
        ).fetchone()
    assert row is not None and row["immutable_location"] == digest
    receipt, _source, _content_digest = _capture(native_store, postgres_service, tmp_path)
    assert verify_recovery_bundle(receipt) == receipt


def test_capture_rejects_executable_root_with_symlinked_ancestor(
    native_store, postgres_service, tmp_path
):
    target, _repository, home, _digest = native_store
    real_root = tmp_path / "real-parent" / "executables"
    real_root.mkdir(mode=0o700, parents=True)
    source_object = native_store[1].executable_content_root / native_store[3]
    object_copy = real_root / native_store[3]
    object_copy.write_bytes(source_object.read_bytes())
    object_copy.chmod(0o600)
    link = tmp_path / "linked-parent"
    link.symlink_to(real_root.parent, target_is_directory=True)
    unsafe_root = link / "executables"
    source = RecoveryCaptureSource(
        database=target,
        immutable_result_root=native_store[1].result_content_root,
        delivery_content_root=native_store[1].delivery_content_root,
        executable_content_root=unsafe_root,
        memory_root=home / "memory",
    )
    _authority, _operator, _lease, _ordinary_source, capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    destination = tmp_path / "symlink-root-ancestor"

    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        capture.capture(source, destination)

    assert not destination.exists()
    assert not tuple(tmp_path.glob(".postgres-recovery-*"))


def test_capture_rejects_oversized_executable_before_reading_payload(
    native_store, postgres_service, tmp_path, monkeypatch
):
    _target, repository, _home, digest = native_store
    object_path = repository.executable_content_root / digest
    with object_path.open("wb") as stream:
        stream.truncate(recovery_bundle_module._MAX_EXECUTABLE_BYTES + 1)
    object_path.chmod(0o600)
    expected_identity = object_path.stat().st_ino

    bytes_read_from_executable = 0
    original_read = recovery_bundle_module.os.read

    def count_executable_read(descriptor, size):
        nonlocal bytes_read_from_executable
        result = original_read(descriptor, size)
        if os.fstat(descriptor).st_ino == expected_identity:
            bytes_read_from_executable += len(result)
        return result

    monkeypatch.setattr(recovery_bundle_module.os, "read", count_executable_read)
    _authority, _operator, _lease, source, capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    destination = tmp_path / "oversized-executable"

    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        capture.capture(source, destination)

    assert bytes_read_from_executable == 0
    assert not destination.exists()
    assert not tuple(tmp_path.glob(".postgres-recovery-*"))


@pytest.mark.parametrize(
    "value",
    [
        "api_key = sk-1234567890abcdef1234567890abcdef",
        "unsafe\x01control",
    ],
)
def test_capture_rejects_database_row_secrets_and_control_text(
    native_store, postgres_service, tmp_path, value
):
    target, _repository, _home, _digest = native_store
    with connect(target) as connection:
        with connection.transaction():
            connection.execute(
                "UPDATE memory_metadata SET tags=%s WHERE id=%s",
                (value, "memory-recovery-note"),
            )

    _authority, _operator, _lease, source, capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    destination = tmp_path / "unsafe-database-row"
    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        capture.capture(source, destination)
    assert not destination.exists()
    assert not tuple(tmp_path.glob(".postgres-recovery-*"))


def test_postgres_archive_contains_no_deleted_secret_row_version(
    native_store, postgres_service, tmp_path
):
    target, _repository, _home, _digest = native_store
    deleted_secret = "api_key = sk-1234567890abcdef1234567890abcdef"
    with connect(target) as connection:
        with connection.transaction():
            connection.execute(
                "UPDATE memory_metadata SET tags=%s WHERE id=%s",
                (deleted_secret, "memory-recovery-note"),
            )
            connection.execute(
                "UPDATE memory_metadata SET tags=%s WHERE id=%s",
                ("safe current value", "memory-recovery-note"),
            )

    receipt, _source, _digest = _capture(native_store, postgres_service, tmp_path)
    manifest = json.loads((receipt.bundle_path / "manifest.json").read_text())
    dump_object = receipt.bundle_path / "objects" / manifest["dump"]["digest"]
    container_id = postgres_service.metadata["container_id"]
    rendered = subprocess.run(
        [
            "docker",
            "exec",
            "--interactive",
            "--user",
            "postgres",
            container_id,
            "pg_restore",
            "--no-password",
            "--file=-",
        ],
        input=dump_object.read_bytes(),
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert rendered.returncode == 0
    assert deleted_secret.encode() not in rendered.stdout


def test_capture_preserves_every_declared_work_content_family(
    native_store, postgres_service, tmp_path
):
    target, repository, _home, executable_digest = native_store
    orphan_payload = b"unreferenced orphan must not enter the archive\n"
    orphan_digest = hashlib.sha256(orphan_payload).hexdigest()
    orphan_path = repository.result_content_root / orphan_digest
    orphan_path.write_bytes(orphan_payload)
    orphan_path.chmod(0o600)
    with repository.read_snapshot() as connection:
        attempt = connection.execute(
            "SELECT a.id,a.generation,a.revision,a.work_item_id,i.job_id "
            "FROM work_attempts a JOIN work_items i ON i.id=a.work_item_id LIMIT 1"
        ).fetchone()
    assert attempt is not None

    families = {
        "result-content": (repository.result_content_root, b"result text\n"),
        "worktree-evidence": (repository.result_content_root, b"worktree evidence\n"),
        "delivery-content": (repository.delivery_content_root, b"delivery text\n"),
    }
    metadata = {}
    for role, (root, payload) in families.items():
        digest = hashlib.sha256(payload).hexdigest()
        path = root / digest
        path.write_bytes(payload)
        path.chmod(0o600)
        metadata[role] = (digest, payload)

    with connect(target) as connection:
        with connection.transaction():
            principal_id = connection.execute(
                "SELECT principal_id FROM work_grants WHERE id=%s",
                ("recovery-root-grant",),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO work_delegation_snapshots "
                "(id,schema_version,job_id,contract_id,binding_key,request_hash,scope,scope_id,"
                "producer_principal_id,source_hash,delivered_hash,content,redacted,truncated,created_at) "
                "VALUES (%s,1,%s,%s,%s,%s,'project',%s,%s,%s,%s,%s,0,0,%s)",
                (
                    "recovery-snapshot",
                    attempt["job_id"],
                    "recovery-contract",
                    "recovery-binding",
                    "a" * 64,
                    "recovery-project",
                    principal_id,
                    "b" * 64,
                    "c" * 64,
                    b"frozen recovery content",
                    1.0,
                ),
            )
            contract_json = '{"schema_version":1,"id":"recovery-contract"}'
            contract_hash = "e" * 64
            connection.execute(
                "INSERT INTO work_dispatch_bindings "
                "(attempt_id,generation,job_id,work_item_id,principal_id,grant_id,grant_revision,"
                "contract_id,contract_json,contract_hash,snapshot_id,created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s)",
                (
                    attempt["id"],
                    attempt["generation"],
                    attempt["job_id"],
                    attempt["work_item_id"],
                    principal_id,
                    "recovery-root-grant",
                    "recovery-contract",
                    contract_json,
                    contract_hash,
                    "recovery-snapshot",
                    1.0,
                ),
            )
            connection.execute(
                "INSERT INTO work_delivery_orders "
                "(attempt_id,generation,operation_kind,schema_version,adapter_version,contract_hash,"
                "payload_json,request_hash,delivery_hash) VALUES (%s,%s,'launch',1,1,%s,%s,%s,%s)",
                (attempt["id"], attempt["generation"], contract_hash, "{}", "a" * 64, "d" * 64),
            )
            result_digest, result_payload = metadata["result-content"]
            connection.execute(
                "INSERT INTO work_results "
                "(id,attempt_id,content_hash,immutable_location,byte_length,validation_state,"
                "validator_id,validation_evidence,created_at) VALUES (%s,%s,%s,%s,%s,'verified',%s,%s,%s)",
                (
                    "recovery-result",
                    attempt["id"],
                    result_digest,
                    result_digest,
                    len(result_payload),
                    "recovery-test",
                    "{}",
                    1.0,
                ),
            )
            evidence_digest, evidence_payload = metadata["worktree-evidence"]
            connection.execute(
                "INSERT INTO work_worktree_evidence "
                "(id,job_id,work_item_id,attempt_id,generation,attempt_revision,content_hash,"
                "immutable_location,byte_length,base_head,stop_evidence_ref,grant_id,"
                "grant_revision,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    "recovery-worktree-evidence",
                    attempt["job_id"],
                    attempt["work_item_id"],
                    attempt["id"],
                    attempt["generation"],
                    attempt["revision"],
                    evidence_digest,
                    evidence_digest,
                    len(evidence_payload),
                    "base-head",
                    "stop-proof",
                    "recovery-root-grant",
                    1,
                    1.0,
                ),
            )
            delivery_digest, delivery_payload = metadata["delivery-content"]
            connection.execute(
                "INSERT INTO work_delivery_content_refs "
                "(attempt_id,generation,owner_principal_id,contract_hash,snapshot_id,content_ref,"
                "content_hash,byte_length) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    attempt["id"],
                    attempt["generation"],
                    principal_id,
                    contract_hash,
                    "recovery-snapshot",
                    delivery_digest,
                    delivery_digest,
                    len(delivery_payload),
                ),
            )

    receipt, _source, _digest = _capture(native_store, postgres_service, tmp_path)
    manifest = json.loads((receipt.bundle_path / "manifest.json").read_text())
    observed = {
        reference["role"]: (reference["digest"], reference["size"])
        for reference in manifest["references"]
    }
    for role, (digest, payload) in metadata.items():
        assert observed[role] == (digest, len(payload))
        assert (receipt.bundle_path / "objects" / digest).read_bytes() == payload
    assert orphan_digest not in observed.values()
    assert orphan_digest not in {item["digest"] for item in manifest["objects"]}
    assert observed["executable-content"] == (
        executable_digest,
        len(b"#!/bin/sh\nprintf recovery\n"),
    )


@pytest.mark.parametrize("role", ["result-content", "worktree-evidence", "delivery-content"])
def test_capture_rejects_secret_in_referenced_work_content(
    native_store, postgres_service, tmp_path, role
):
    _target, repository, _home, _executable_digest = native_store
    payload = b"api_key = sk-1234567890abcdef1234567890abcdef\n"
    digest = hashlib.sha256(payload).hexdigest()
    root = (
        repository.delivery_content_root
        if role == "delivery-content"
        else repository.result_content_root
    )
    path = root / digest
    path.write_bytes(payload)
    path.chmod(0o600)
    with repository.read_snapshot() as connection:
        attempt = connection.execute(
            "SELECT a.id,a.generation,a.revision,a.work_item_id,i.job_id "
            "FROM work_attempts a JOIN work_items i ON i.id=a.work_item_id LIMIT 1"
        ).fetchone()
    with connect(repository.target) as connection:
        with connection.transaction():
            if role == "result-content":
                connection.execute(
                    "INSERT INTO work_results "
                    "(id,attempt_id,content_hash,immutable_location,byte_length,validation_state,"
                    "validator_id,validation_evidence,created_at) VALUES (%s,%s,%s,%s,%s,'verified',%s,%s,%s)",
                    (
                        "secret-result",
                        attempt["id"],
                        digest,
                        digest,
                        len(payload),
                        "test",
                        "{}",
                        1.0,
                    ),
                )
            elif role == "worktree-evidence":
                connection.execute(
                    "INSERT INTO work_worktree_evidence "
                    "(id,job_id,work_item_id,attempt_id,generation,attempt_revision,content_hash,"
                    "immutable_location,byte_length,base_head,stop_evidence_ref,grant_id,"
                    "grant_revision,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        "secret-worktree-evidence",
                        attempt["job_id"],
                        attempt["work_item_id"],
                        attempt["id"],
                        attempt["generation"],
                        attempt["revision"],
                        digest,
                        digest,
                        len(payload),
                        "base-head",
                        "stop-proof",
                        "recovery-root-grant",
                        1,
                        1.0,
                    ),
                )
            else:
                _insert_delivery_content_reference(
                    connection,
                    attempt,
                    digest=digest,
                    byte_length=len(payload),
                )

    _authority, _operator, _lease, source, capture, _content_digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    destination = tmp_path / f"secret-{role}"
    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        capture.capture(source, destination)
    assert not destination.exists()


def _insert_delivery_content_reference(connection, attempt, *, digest, byte_length):
    principal_id = connection.execute(
        "SELECT principal_id FROM work_grants WHERE id=%s",
        ("recovery-root-grant",),
    ).fetchone()[0]
    snapshot_id = "recovery-delivery-snapshot"
    contract_id = "recovery-delivery-contract"
    contract_hash = "f" * 64
    connection.execute(
        "INSERT INTO work_delegation_snapshots "
        "(id,schema_version,job_id,contract_id,binding_key,request_hash,scope,scope_id,"
        "producer_principal_id,source_hash,delivered_hash,content,redacted,truncated,created_at) "
        "VALUES (%s,1,%s,%s,%s,%s,'project',%s,%s,%s,%s,%s,0,0,1.0)",
        (
            snapshot_id,
            attempt["job_id"],
            contract_id,
            "recovery-delivery-binding",
            "a" * 64,
            "recovery-project",
            principal_id,
            "b" * 64,
            "c" * 64,
            b"reviewed recovery context",
        ),
    )
    connection.execute(
        "INSERT INTO work_dispatch_bindings "
        "(attempt_id,generation,job_id,work_item_id,principal_id,grant_id,grant_revision,"
        "contract_id,contract_json,contract_hash,snapshot_id,created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,1.0)",
        (
            attempt["id"],
            attempt["generation"],
            attempt["job_id"],
            attempt["work_item_id"],
            principal_id,
            "recovery-root-grant",
            contract_id,
            '{"schema_version":1,"id":"recovery-delivery-contract"}',
            contract_hash,
            snapshot_id,
        ),
    )
    connection.execute(
        "INSERT INTO work_delivery_orders "
        "(attempt_id,generation,operation_kind,schema_version,adapter_version,contract_hash,"
        "payload_json,request_hash,delivery_hash) VALUES (%s,%s,'launch',1,1,%s,%s,%s,%s)",
        (attempt["id"], attempt["generation"], contract_hash, "{}", "d" * 64, "e" * 64),
    )
    connection.execute(
        "INSERT INTO work_delivery_content_refs "
        "(attempt_id,generation,owner_principal_id,contract_hash,snapshot_id,content_ref,"
        "content_hash,byte_length) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            attempt["id"],
            attempt["generation"],
            principal_id,
            contract_hash,
            snapshot_id,
            digest,
            digest,
            byte_length,
        ),
    )


def _insert_delegation_snapshot(repository, content: bytes) -> None:
    with repository.read_snapshot() as connection:
        job = connection.execute("SELECT id FROM work_jobs LIMIT 1").fetchone()
        grant = connection.execute(
            "SELECT principal_id FROM work_grants WHERE id=%s",
            ("recovery-root-grant",),
        ).fetchone()
    assert job is not None
    assert grant is not None
    with connect(repository.target) as connection:
        with connection.transaction():
            connection.execute(
                "INSERT INTO work_delegation_snapshots "
                "(id,schema_version,job_id,contract_id,binding_key,request_hash,scope,scope_id,"
                "producer_principal_id,source_hash,delivered_hash,content,redacted,truncated,created_at) "
                "VALUES (%s,1,%s,%s,%s,%s,'project',%s,%s,%s,%s,%s,0,0,1.0)",
                (
                    "recovery-delegation-snapshot",
                    job["id"],
                    "recovery-snapshot-contract",
                    "recovery-snapshot-binding",
                    hashlib.sha256(b"request").hexdigest(),
                    "recovery-project",
                    grant["principal_id"],
                    hashlib.sha256(content).hexdigest(),
                    hashlib.sha256(content).hexdigest(),
                    content,
                ),
            )


def test_capture_restores_exact_reviewed_delegation_snapshot_bytes(
    native_store, postgres_service, tmp_path
):
    _target, repository, _home, _digest = native_store
    content = "Delegated context — preserves UTF-8 and newlines.\nSecond line.".encode("utf-8")
    _insert_delegation_snapshot(repository, content)
    receipt, source, _digest = _capture(native_store, postgres_service, tmp_path)
    destination, database_name, destination_home = _make_destination(
        postgres_service, source, tmp_path
    )
    try:
        restore_recovery_bundle(
            receipt,
            destination,
            filesystem_home=destination_home,
            runner=_OwnedDockerArchiveRunner(postgres_service, tmp_path),
        )
        with connect(destination, readonly=True) as connection:
            row = connection.execute(
                "SELECT content FROM work_delegation_snapshots WHERE id=%s",
                ("recovery-delegation-snapshot",),
            ).fetchone()
        assert row is not None and row["content"] == content
    finally:
        with postgres_service.connect_admin() as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=%s AND pid<>pg_backend_pid()",
                (database_name,),
            )
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )


@pytest.mark.parametrize(
    "content",
    [
        b"api_key = sk-1234567890abcdef1234567890abcdef\n",
        b"control\x01byte",
        b"not utf8: \xff",
    ],
    ids=["secret", "control-byte", "non-utf8"],
)
def test_capture_rejects_unsafe_delegation_snapshot_bytes(
    native_store, postgres_service, tmp_path, content
):
    _target, repository, _home, _digest = native_store
    _insert_delegation_snapshot(repository, content)
    _authority, _operator, _lease, source, capture, _content_digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    destination = tmp_path / "unsafe-delegation-snapshot"
    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        capture.capture(source, destination)
    assert not destination.exists()


@pytest.mark.parametrize("kind", ["view", "trigger"])
def test_capture_rejects_unreviewed_executable_schema_objects(
    native_store, postgres_service, tmp_path, kind
):
    target, _repository, _home, _digest = native_store
    _authority, _operator, _lease, source, capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    with postgres_service.connect_admin(target.database) as admin:
        if kind == "view":
            admin.execute(
                sql.SQL("CREATE VIEW {}.unreviewed_recovery_view AS SELECT 1 AS value").format(
                    sql.Identifier(target.schema)
                )
            )
        else:
            admin.execute(
                sql.SQL(
                    "CREATE FUNCTION {}.unreviewed_recovery_trigger() RETURNS trigger "
                    "LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END'"
                ).format(sql.Identifier(target.schema))
            )
            admin.execute(
                sql.SQL(
                    "CREATE TRIGGER unreviewed_recovery_trigger AFTER INSERT ON {}.memory_metadata "
                    "FOR EACH ROW EXECUTE FUNCTION {}.unreviewed_recovery_trigger()"
                ).format(sql.Identifier(target.schema), sql.Identifier(target.schema))
            )
    try:
        with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
            capture.capture(source, tmp_path / f"unreviewed-{kind}")
        assert not (tmp_path / f"unreviewed-{kind}").exists()
    finally:
        with postgres_service.connect_admin(target.database) as admin:
            if kind == "view":
                admin.execute(
                    sql.SQL("DROP VIEW IF EXISTS {}.unreviewed_recovery_view").format(
                        sql.Identifier(target.schema)
                    )
                )
            else:
                admin.execute(
                    sql.SQL(
                        "DROP TRIGGER IF EXISTS unreviewed_recovery_trigger ON {}.memory_metadata"
                    ).format(sql.Identifier(target.schema))
                )
                admin.execute(
                    sql.SQL("DROP FUNCTION IF EXISTS {}.unreviewed_recovery_trigger()").format(
                        sql.Identifier(target.schema)
                    )
                )


def test_capture_rejects_unreviewed_standalone_secret_routine(
    native_store, postgres_service, tmp_path
):
    """Schema dump routines must be inventoried and screened before publication."""
    target, _repository, _home, _digest = native_store
    _authority, _operator, _lease, source, capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    routine = sql.Identifier(target.schema, "unreviewed_backup_secret")
    synthetic_secret = "api_key = sk-1234567890abcdef1234567890abcdef"
    with postgres_service.connect_admin(target.database) as admin:
        admin.execute(
            sql.SQL(
                "CREATE FUNCTION {}() RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT {} $$"
            ).format(routine, sql.Literal(synthetic_secret))
        )

    destination = tmp_path / "standalone-secret-routine"
    try:
        with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
            capture.capture(source, destination)
        assert not destination.exists()
        assert not list(tmp_path.glob(".postgres-recovery-*"))
    finally:
        with postgres_service.connect_admin(target.database) as admin:
            admin.execute(sql.SQL("DROP FUNCTION IF EXISTS {}()").format(routine))


def test_capture_excludes_schema_comments_from_pg_dump(native_store, postgres_service, tmp_path):
    """Schema comments are outside the portable database contract and may contain secrets."""
    target, _repository, _home, _digest = native_store
    marker = "api_key = sk-1234567890abcdef1234567890abcdef"
    table = sql.Identifier(target.schema, "work_jobs")
    runner = _OwnedDockerArchiveRunner(postgres_service, tmp_path)
    with connect(target.migration_target()) as admin:
        admin.execute(sql.SQL("COMMENT ON TABLE {} IS {}").format(table, sql.Literal(marker)))
    try:
        _authority, _operator, _lease, source, capture, _digest = _capture_context(
            native_store, postgres_service, tmp_path, runner=runner
        )
        receipt = capture.capture(source, tmp_path / "comment-free-bundle")
        manifest = json.loads((receipt.bundle_path / "manifest.json").read_text())
        dump_path = receipt.bundle_path / "objects" / manifest["dump"]["digest"]
        rendered_sql = runner.render_sql(dump_path)
        marker_present = marker.encode() in rendered_sql
        assert marker_present is False, "recovery archive should omit schema comments"
    finally:
        with connect(target.migration_target()) as admin:
            admin.execute(sql.SQL("COMMENT ON TABLE {} IS NULL").format(table))


def test_capture_rejects_unreviewed_standalone_secret_enum(
    native_store, postgres_service, tmp_path
):
    """Custom types are schema-wide pg_dump objects outside reviewed table DDL."""
    target, _repository, _home, _digest = native_store
    enum_type = sql.Identifier(target.schema, "unreviewed_backup_secret")
    marker = "api_key = sk-1234567890abcdef1234567890abcdef"
    with connect(target.migration_target()) as admin:
        admin.execute(sql.SQL("CREATE TYPE {} AS ENUM ({})").format(enum_type, sql.Literal(marker)))
    _authority, _operator, _lease, source, capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    destination = tmp_path / "enum-secret-bundle"
    try:
        with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
            capture.capture(source, destination)
        assert not destination.exists()
        assert not list(tmp_path.glob(".postgres-recovery-*"))
    finally:
        with connect(target.migration_target()) as admin:
            admin.execute(sql.SQL("DROP TYPE IF EXISTS {}").format(enum_type))


def test_capture_rejects_unreviewed_extended_statistics(native_store, postgres_service, tmp_path):
    """Auxiliary catalog objects must be closed even when attached to a reviewed table."""
    target, _repository, _home, _digest = native_store
    statistics = sql.Identifier(target.schema, "unreviewed_backup_stats")
    table = sql.Identifier(target.schema, "work_jobs")
    with connect(target.migration_target()) as admin:
        admin.execute(
            sql.SQL("CREATE STATISTICS {} ON project_id, created_at FROM {}").format(
                statistics, table
            )
        )
    _authority, _operator, _lease, source, capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    destination = tmp_path / "statistics-bundle"
    try:
        with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
            capture.capture(source, destination)
        assert not destination.exists()
        assert not list(tmp_path.glob(".postgres-recovery-*"))
    finally:
        with connect(target.migration_target()) as admin:
            admin.execute(sql.SQL("DROP STATISTICS IF EXISTS {}").format(statistics))


@pytest.mark.parametrize("object_kind", ["operator", "collation", "shelltype"])
def test_capture_rejects_unreviewed_schema_auxiliary_objects(
    native_store, postgres_service, tmp_path, object_kind
):
    """Schema-scoped pg_dump objects need an explicit owner in the recovery catalog."""
    target, _repository, _home, _digest = native_store
    schema_name = sql.Identifier(target.schema)
    collation_name = sql.Identifier(target.schema, "unreviewed_backup_collation")
    shell_type_name = sql.Identifier(target.schema, "unreviewed_backup_shell")
    operator_name = sql.SQL("{}.##").format(schema_name)
    if object_kind == "shelltype":
        with postgres_service.connect_admin(target.database) as superuser:
            superuser.execute(sql.SQL("CREATE TYPE {}").format(shell_type_name))
        with connect(target, readonly=True) as reader:
            shell_state = reader.execute(
                """SELECT t.typisdefined FROM pg_catalog.pg_type t
                JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace
                WHERE n.nspname=%s AND t.typname=%s""",
                (target.schema, "unreviewed_backup_shell"),
            ).fetchone()
        assert shell_state == (False,)
    else:
        with connect(target.migration_target()) as admin:
            if object_kind == "operator":
                admin.execute(
                    sql.SQL(
                        "CREATE OPERATOR {} (FUNCTION = pg_catalog.int4eq, "
                        "LEFTARG = integer, RIGHTARG = integer)"
                    ).format(operator_name)
                )
            else:
                admin.execute(
                    sql.SQL("CREATE COLLATION {} (provider = libc, locale = 'C')").format(
                        collation_name
                    )
                )
    _authority, _operator, _lease, source, capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    destination = tmp_path / f"{object_kind}-bundle"
    try:
        with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
            capture.capture(source, destination)
        assert not destination.exists()
        assert not list(tmp_path.glob(".postgres-recovery-*"))
    finally:
        if object_kind == "shelltype":
            with postgres_service.connect_admin(target.database) as superuser:
                superuser.execute(sql.SQL("DROP TYPE IF EXISTS {}").format(shell_type_name))
        else:
            with connect(target.migration_target()) as admin:
                if object_kind == "operator":
                    admin.execute(
                        sql.SQL("DROP OPERATOR IF EXISTS {} (integer, integer)").format(
                            operator_name
                        )
                    )
                else:
                    admin.execute(sql.SQL("DROP COLLATION IF EXISTS {}").format(collation_name))


def test_capture_rejects_symlink_and_secret_memory_objects(
    native_store, postgres_service, tmp_path
):
    _target, repository, home, digest = native_store
    memory_file = home / "memory" / "recovery-note.md"
    outside = tmp_path / "outside-object"
    outside.write_bytes(b"outside")
    outside.chmod(0o600)
    object_path = repository.executable_content_root / digest
    object_path.unlink()
    object_path.symlink_to(outside)
    _authority, _operator, _lease, source, capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        capture.capture(source, tmp_path / "symlink-object")
    assert not (tmp_path / "symlink-object").exists()

    object_path.unlink()
    object_path.write_bytes(b"#!/bin/sh\nprintf recovery\n")
    object_path.chmod(0o600)
    memory_file.write_text("password: clearly-not-a-real-secret-value\n", encoding="utf-8")
    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        capture.capture(source, tmp_path / "secret-memory")
    assert not (tmp_path / "secret-memory").exists()


def test_revoke_during_capture_fences_publication_and_cleans_staging(
    native_store, postgres_service, tmp_path
):
    entered_dump = threading.Event()
    resume_dump = threading.Event()

    class PausedDumpRunner(_OwnedDockerArchiveRunner):
        def dump(self, target, destination, *, snapshot_id):
            super().dump(target, destination, snapshot_id=snapshot_id)
            entered_dump.set()
            if not resume_dump.wait(timeout=30):
                raise TimeoutError("test did not release dump")

    authority, operator, lease, source, capture, _digest = _capture_context(
        native_store,
        postgres_service,
        tmp_path,
        runner=PausedDumpRunner(postgres_service, tmp_path),
    )
    destination = tmp_path / "revoked-during-capture"
    failures = []

    def run_capture():
        try:
            capture.capture(source, destination)
        except Exception as error:  # captured from the worker to assert after joining
            failures.append(error)

    worker = threading.Thread(target=run_capture)
    worker.start()
    assert entered_dump.wait(timeout=30)
    authority.revoke_offline_cut(operator, lease)
    resume_dump.set()
    worker.join(timeout=30)

    assert not worker.is_alive()
    assert len(failures) == 1 and isinstance(failures[0], RecoveryBundleError)
    assert not destination.exists()
    assert not tuple(tmp_path.glob(".postgres-recovery-*"))


def test_revocation_waits_for_publication_and_cannot_revoke_published_cut(
    native_store, postgres_service, tmp_path, monkeypatch
):
    authority, operator, lease, source, capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    original_write_manifest = recovery_bundle_module._write_manifest
    publish_entered = threading.Event()
    revoke_started = threading.Event()
    revoke_errors = []

    def gated_write_manifest(directory, manifest):
        result = original_write_manifest(directory, manifest)
        publish_entered.set()
        assert revoke_started.wait(timeout=10)
        return result

    def revoke():
        assert publish_entered.wait(timeout=30)
        revoke_started.set()
        try:
            authority.revoke_offline_cut(operator, lease)
        except Exception as error:
            revoke_errors.append(error)

    monkeypatch.setattr(recovery_bundle_module, "_write_manifest", gated_write_manifest)
    revoker = threading.Thread(target=revoke)
    revoker.start()
    destination = tmp_path / "publication-race"
    receipt = capture.capture(source, destination)
    revoker.join(timeout=30)

    assert not revoker.is_alive()
    assert verify_recovery_bundle(receipt) == receipt
    assert len(revoke_errors) == 1
    assert not tuple(tmp_path.glob(".postgres-recovery-*"))


@pytest.mark.parametrize("damage", ["missing_object", "extra_object", "private_mode"])
def test_verifier_rejects_incomplete_or_nonprivate_bundle_members(
    native_store, postgres_service, tmp_path, damage
):
    receipt, _source, _digest = _capture(native_store, postgres_service, tmp_path)
    manifest = json.loads((receipt.bundle_path / "manifest.json").read_text())
    first_object = receipt.bundle_path / "objects" / manifest["objects"][0]["digest"]
    if damage == "missing_object":
        first_object.unlink()
    elif damage == "extra_object":
        extra = receipt.bundle_path / "objects" / ("0" * 64)
        extra.write_bytes(b"extra")
        extra.chmod(0o600)
    else:
        first_object.chmod(0o644)

    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        verify_recovery_bundle(receipt)


def test_verifier_rejects_recomputed_manifest_with_duplicate_objects(
    native_store, postgres_service, tmp_path
):
    receipt, _source, _digest = _capture(native_store, postgres_service, tmp_path)
    manifest_path = receipt.bundle_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["objects"].append(dict(manifest["objects"][0]))
    payload = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    manifest_path.write_bytes(payload)
    manifest_path.chmod(0o600)
    forged_receipt = replace(
        receipt,
        manifest_digest=hashlib.sha256(payload).hexdigest(),
        manifest_size=len(payload),
    )

    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        verify_recovery_bundle(forged_receipt)


@pytest.mark.parametrize(
    "tamper",
    [
        "lease_fence",
        "before_inventory",
        "missing_promote",
        "unobserved_phase",
        "phase_timestamp",
        "capture_id",
    ],
)
def test_verifier_rejects_recomputed_cut_receipt_tampering(
    native_store, postgres_service, tmp_path, tamper
):
    receipt, _source, _digest = _capture(native_store, postgres_service, tmp_path)
    manifest_path = receipt.bundle_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if tamper == "lease_fence":
        manifest["cut"]["lease"]["fence"] += 1
    elif tamper == "before_inventory":
        manifest["cut"]["before"]["inventory"]["fingerprint"] = "0" * 64
    elif tamper == "missing_promote":
        manifest["cut"].pop("promote")
    elif tamper == "unobserved_phase":
        manifest["cut"]["unobserved"] = dict(manifest["cut"]["after"])
    elif tamper == "phase_timestamp":
        before = manifest["cut"]["before"]["observed_at"]
        after = manifest["cut"]["after"]["observed_at"]
        manifest["cut"]["before"]["observed_at"] = before + (after - before) / 2
    else:
        manifest["capture_id"] = "0" * 32
    payload = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    manifest_path.write_bytes(payload)
    manifest_path.chmod(0o600)
    forged_receipt = replace(
        receipt,
        manifest_digest=hashlib.sha256(payload).hexdigest(),
        manifest_size=len(payload),
    )

    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        verify_recovery_bundle(forged_receipt, source_target=native_store[0])


def test_verifier_rejects_receipt_when_durable_observation_contradicts_manifest(
    native_store, postgres_service, tmp_path
):
    receipt, source, _digest = _capture(native_store, postgres_service, tmp_path)
    target = source.database
    with connect(target.migration_target()) as connection:
        with connection.transaction():
            stored = connection.execute(
                "SELECT observation FROM work_offline_cut_observations "
                "WHERE lease_id=%s AND capture_id=%s AND phase='after'",
                (receipt.lease_id, receipt.capture_id),
            ).fetchone()
            assert stored is not None
            observation = json.loads(stored["observation"])
            observation["observed_at"] += 1.0
            connection.execute(
                "UPDATE work_offline_cut_observations SET observation=%s "
                "WHERE lease_id=%s AND capture_id=%s AND phase='after'",
                (
                    json.dumps(observation, sort_keys=True, separators=(",", ":")),
                    receipt.lease_id,
                    receipt.capture_id,
                ),
            )

    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        verify_recovery_bundle(receipt, source_target=target)


def test_bundle_verification_requires_the_exact_source_target(
    native_store, postgres_service, tmp_path, monkeypatch
):
    receipt, source, _digest = _capture(native_store, postgres_service, tmp_path)
    target = source.database
    wrong_target = replace(target, store_id=str(uuid.uuid4()))
    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        verify_recovery_bundle(receipt, source_target=wrong_target)

    def missing_configuration(cls, scope="main", home=None):
        raise RuntimeError("private source configuration unavailable")

    monkeypatch.setattr(DatabaseTarget, "from_settings", classmethod(missing_configuration))
    assert verify_recovery_bundle(receipt, source_target=target) == receipt
    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        verify_recovery_bundle(receipt)


def test_verifier_rejects_a_copied_bundle_at_an_unpublished_path(
    native_store, postgres_service, tmp_path
):
    receipt, source, _digest = _capture(native_store, postgres_service, tmp_path)
    copied_path = tmp_path / "copied-recovery-bundle"
    shutil.copytree(receipt.bundle_path, copied_path)
    copied_receipt = replace(receipt, bundle_path=copied_path)
    copied_manifest = (copied_path / "manifest.json").read_bytes()
    assert (hashlib.sha256(copied_manifest).hexdigest(), len(copied_manifest)) == (
        receipt.manifest_digest,
        receipt.manifest_size,
    )

    # The copied bytes and manifest digest remain valid. Publication is bound
    # to the path recorded by the source authority, so this is not a valid
    # restore receipt until an explicit republication protocol exists.
    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        verify_recovery_bundle(copied_receipt, source_target=source.database)


def test_restore_refuses_wrong_store_and_preserves_existing_schema(
    native_store, postgres_service, tmp_path
):
    receipt, source, _digest = _capture(native_store, postgres_service, tmp_path)
    target, database_name, home = _make_destination(postgres_service, source, tmp_path)
    wrong_store = replace(target, store_id=str(uuid.uuid4()), schema=target.schema + "_wrong")
    runner = _OwnedDockerArchiveRunner(postgres_service, tmp_path)
    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        restore_recovery_bundle(receipt, wrong_store, filesystem_home=home, runner=runner)

    with postgres_service.connect_admin(target.database) as admin:
        admin.execute(
            sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                sql.Identifier(target.schema), sql.Identifier(target.migration_user)
            )
        )
        admin.execute(
            sql.SQL("CREATE TABLE {}.sentinel (value integer)").format(
                sql.Identifier(target.schema)
            )
        )
    try:
        with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
            restore_recovery_bundle(receipt, target, filesystem_home=home, runner=runner)
        with postgres_service.connect_admin(target.database) as admin:
            assert admin.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname=%s", (target.schema,)
            ).fetchone() == (1,)
            assert admin.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name='sentinel'",
                (target.schema,),
            ).fetchone() == (1,)
    finally:
        with postgres_service.connect_admin(target.database) as admin:
            admin.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(target.schema))
            )
        with postgres_service.connect_admin() as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=%s AND pid<>pg_backend_pid()",
                (database_name,),
            )
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )


def test_restore_failure_inside_atomic_pg_restore_leaves_no_schema_or_staging(
    native_store, postgres_service, tmp_path
):
    receipt, source, _digest = _capture(native_store, postgres_service, tmp_path)
    target, database_name, home = _make_destination(postgres_service, source, tmp_path)

    class AtomicRestoreFailureRunner(_OwnedDockerArchiveRunner):
        def restore(self, destination, archive):
            # Mirror pg_restore --single-transaction failure: any schema created
            # during this operation is rolled back before the exception escapes.
            with postgres_service.connect_admin(destination.database) as admin:
                with admin.transaction():
                    admin.execute(
                        sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                            sql.Identifier(destination.schema),
                            sql.Identifier(destination.migration_user),
                        )
                    )
                    raise OSError("injected failure before atomic restore commit")

    try:
        with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
            restore_recovery_bundle(
                receipt,
                target,
                filesystem_home=home,
                runner=AtomicRestoreFailureRunner(postgres_service, tmp_path),
            )
        with postgres_service.connect_admin(database_name) as admin:
            assert (
                admin.execute(
                    "SELECT 1 FROM pg_namespace WHERE nspname=%s", (target.schema,)
                ).fetchone()
                is None
            )
        assert not tuple(home.glob(".recovery-objects-*"))
        assert not WorkRepository(target, filesystem_home=home).result_content_root.exists()
        assert not WorkRepository(target, filesystem_home=home).executable_content_root.exists()
    finally:
        with postgres_service.connect_admin() as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=%s AND pid<>pg_backend_pid()",
                (database_name,),
            )
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )


def test_restore_does_not_drop_schema_created_after_absence_preflight(
    native_store, postgres_service, tmp_path
):
    receipt, source, _digest = _capture(native_store, postgres_service, tmp_path)
    target, database_name, home = _make_destination(postgres_service, source, tmp_path)

    class CompetingSchemaRunner(_OwnedDockerArchiveRunner):
        def restore(self, destination, archive):
            with postgres_service.connect_admin(destination.database) as admin:
                admin.execute(
                    sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                        sql.Identifier(destination.schema),
                        sql.Identifier(destination.migration_user),
                    )
                )
                admin.execute(
                    sql.SQL("CREATE TABLE {}.sentinel (value text)").format(
                        sql.Identifier(destination.schema)
                    )
                )
            # The archive runner encounters the competing schema and fails.
            super().restore(destination, archive)

    try:
        with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
            restore_recovery_bundle(
                receipt,
                target,
                filesystem_home=home,
                runner=CompetingSchemaRunner(postgres_service, tmp_path),
            )
        with postgres_service.connect_admin(database_name) as admin:
            assert admin.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname=%s", (target.schema,)
            ).fetchone() == (1,)
            assert admin.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema=%s AND table_name='sentinel'",
                (target.schema,),
            ).fetchone() == (1,)
    finally:
        with postgres_service.connect_admin(database_name) as admin:
            admin.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(target.schema))
            )
        with postgres_service.connect_admin() as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=%s AND pid<>pg_backend_pid()",
                (database_name,),
            )
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )


@pytest.mark.parametrize("kind", ["trigger", "view"])
def test_restore_rejects_unreviewed_relation_and_drops_only_its_schema(
    native_store, postgres_service, tmp_path, kind
):
    receipt, source, _digest = _capture(native_store, postgres_service, tmp_path)
    target, database_name, home = _make_destination(postgres_service, source, tmp_path)

    class RestoreWithUnreviewedView(_OwnedDockerArchiveRunner):
        def restore(self, destination, archive):
            super().restore(destination, archive)
            with postgres_service.connect_admin(destination.database) as admin:
                if kind == "view":
                    admin.execute(
                        sql.SQL(
                            "CREATE VIEW {}.unreviewed_restore_view AS SELECT 1 AS value"
                        ).format(sql.Identifier(destination.schema))
                    )
                else:
                    admin.execute(
                        sql.SQL(
                            "CREATE FUNCTION {}.unreviewed_restore_trigger() RETURNS trigger "
                            "LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END'"
                        ).format(sql.Identifier(destination.schema))
                    )
                    admin.execute(
                        sql.SQL(
                            "CREATE TRIGGER unreviewed_restore_trigger AFTER INSERT ON "
                            "{}.memory_metadata FOR EACH ROW EXECUTE FUNCTION {}.unreviewed_restore_trigger()"
                        ).format(
                            sql.Identifier(destination.schema), sql.Identifier(destination.schema)
                        )
                    )

    try:
        with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
            restore_recovery_bundle(
                receipt,
                target,
                filesystem_home=home,
                runner=RestoreWithUnreviewedView(postgres_service, tmp_path),
            )
        with postgres_service.connect_admin(database_name) as admin:
            assert (
                admin.execute(
                    "SELECT 1 FROM pg_namespace WHERE nspname=%s", (target.schema,)
                ).fetchone()
                is None
            )
        assert not tuple(home.glob(".recovery-objects-*"))
    finally:
        with postgres_service.connect_admin() as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=%s AND pid<>pg_backend_pid()",
                (database_name,),
            )
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )


def test_restore_validation_failure_after_object_promotion_cleans_owned_artifacts(
    native_store, postgres_service, tmp_path, monkeypatch
):
    receipt, source, _digest = _capture(native_store, postgres_service, tmp_path)
    target, database_name, home = _make_destination(postgres_service, source, tmp_path)

    def reject_restore(*_args, **_kwargs):
        raise RuntimeError("injected restore authority failure")

    monkeypatch.setattr(WorkRepository, "block_imported_restore", reject_restore)
    repository = WorkRepository(target, filesystem_home=home)
    roots = (repository.executable_content_root, home / "memory")
    try:
        with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
            restore_recovery_bundle(
                receipt,
                target,
                filesystem_home=home,
                runner=_OwnedDockerArchiveRunner(postgres_service, tmp_path),
            )
        with postgres_service.connect_admin(database_name) as admin:
            assert (
                admin.execute(
                    "SELECT 1 FROM pg_namespace WHERE nspname=%s", (target.schema,)
                ).fetchone()
                is None
            )
        assert all(not path.exists() for path in roots)
        assert not tuple(home.glob(".recovery-objects-*"))
    finally:
        with postgres_service.connect_admin() as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=%s AND pid<>pg_backend_pid()",
                (database_name,),
            )
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )
