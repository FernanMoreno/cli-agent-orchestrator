"""The v24 recovery context is a fail-closed execution boundary."""

import hashlib
import sqlite3
import time

import pytest

from test.clients.test_work_migrations import repository_module
from test.fixtures.work_store import work_store_paths  # noqa: F401


def _v23_store(module, path):
    """Build an actual verified v23 ledger without invoking the v24 migrator."""
    store = module.WorkRepository(path)
    with store.transaction() as connection:
        for version in range(1, 24):
            for statement in module._MIGRATIONS[version]:
                connection.execute(statement)
            if version == 16:
                store._initialize_inbox_store_context(connection)
            connection.execute(
                "INSERT INTO work_migrations VALUES (?,?,?,?)",
                (version, module._CHECKSUMS[version], time.time(), "verified"),
            )
        module.WorkRepository._verify(connection, version=23)
    return store


def test_v23_store_without_v24_context_is_not_execution_eligible(work_store_paths):
    """The current writer must not certify an otherwise valid v23 store for execution."""
    module = repository_module()
    store = _v23_store(module, work_store_paths.database)

    with pytest.raises(module.SchemaMismatch):
        store.verify_schema()


def test_restore_blocked_context_is_not_execution_eligible(work_store_paths):
    module = repository_module()
    store = module.WorkRepository(work_store_paths.database)
    store.initialize()

    with store.connection() as connection:
        assert store.assert_execution_allowed(connection).execution_state == "normal"

    with store.transaction() as connection:
        store._block_recovery_for_restore(
            connection,
            installation_uuid="f" * 32,
            bundle_digest="a" * 64,
            restore_receipt="b" * 64,
        )

    with store.connection() as connection:
        with pytest.raises(module.SchemaMismatch, match="blocks execution"):
            store.assert_execution_allowed(connection)


def _copy_sqlite(source, staging):
    """Make the path-distinct SQLite copy that a restore path receives."""
    with (
        sqlite3.connect(source) as source_connection,
        sqlite3.connect(staging) as staging_connection,
    ):
        source_connection.backup(staging_connection)


def _admit_restore_work(store, job, key):
    return store.admit_work(
        job_id=job["id"],
        operation_kind="launch",
        idempotency_key=key,
        request_hash=hashlib.sha256(key.encode()).hexdigest(),
        contract_id=f"contract-{key}",
        snapshot_id="snapshot",
        provider="mock_cli",
        actor_id="restore-operator",
    )


def _transition_restore_attempt(module, store, work, target, *, event_id, **evidence):
    attempt = work["attempts"][-1]
    return store.transition_attempt(
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_revision=attempt["revision"],
        expected_state=attempt["state"],
        target=target,
        actor_id="restore-operator",
        event_id=event_id,
        evidence=module.TransitionEvidence(
            generation=attempt["generation"],
            expected_generation=attempt["generation"],
            **evidence,
        ),
    )


def _portable_restore_source(module, path):
    """Create every state whose historical evidence must survive a blocked copy."""
    source = module.WorkRepository(path)
    source.initialize()
    job = source.create_job(
        project_id="restore-project",
        principal_id="restore-operator",
        allowed_providers=["mock_cli"],
        grant_id="restore-grant",
    )
    planned = _admit_restore_work(source, job, "planned")
    sent = _transition_restore_attempt(
        module,
        source,
        _admit_restore_work(source, job, "sent"),
        "sent",
        event_id="restore-sent",
        contract_confirmed=True,
        grant_confirmed=True,
        capacity_confirmed=True,
        reservations_confirmed=True,
    )
    acknowledged = _transition_restore_attempt(
        module,
        source,
        _admit_restore_work(source, job, "acknowledged"),
        "sent",
        event_id="restore-ack-sent",
        contract_confirmed=True,
        grant_confirmed=True,
        capacity_confirmed=True,
        reservations_confirmed=True,
    )
    acknowledged = _transition_restore_attempt(
        module,
        source,
        acknowledged,
        "acknowledged",
        event_id="restore-acknowledged",
        task_received=True,
    )
    running = _transition_restore_attempt(
        module,
        source,
        _admit_restore_work(source, job, "running"),
        "sent",
        event_id="restore-running-sent",
        contract_confirmed=True,
        grant_confirmed=True,
        capacity_confirmed=True,
        reservations_confirmed=True,
    )
    running = _transition_restore_attempt(
        module,
        source,
        running,
        "running",
        event_id="restore-running",
        task_received=True,
        execution_started=True,
    )
    reconcile = _transition_restore_attempt(
        module,
        source,
        _admit_restore_work(source, job, "reconcile"),
        "sent",
        event_id="restore-reconcile-sent",
        contract_confirmed=True,
        grant_confirmed=True,
        capacity_confirmed=True,
        reservations_confirmed=True,
    )
    reconcile = _transition_restore_attempt(
        module, source, reconcile, "reconcile", event_id="restore-reconcile"
    )
    cancelled = _transition_restore_attempt(
        module,
        source,
        _admit_restore_work(source, job, "cancelled"),
        "cancelled",
        event_id="restore-cancelled",
    )
    with source.connection() as connection:
        context = module._stored_inbox_context(connection)
    return (
        source,
        job,
        {
            "planned": planned,
            "sent": sent,
            "acknowledged": acknowledged,
            "running": running,
            "reconcile": reconcile,
            "cancelled": cancelled,
        },
        context,
    )


def _provenance_rows(path):
    with sqlite3.connect(path) as connection:
        return {
            "context": connection.execute(
                "SELECT store_identity,store_uuid FROM work_inbox_store_context"
            ).fetchall(),
            "bridge": connection.execute(
                "SELECT singleton,store_identity,opened_at FROM work_inbox_store_identity"
            ).fetchall(),
            "bindings": connection.execute(
                "SELECT inbox_id,attempt_id,generation,store_identity,created_at "
                "FROM work_inbox_bindings ORDER BY inbox_id"
            ).fetchall(),
        }


def test_portable_copy_is_blocked_without_rewriting_inbox_provenance_or_replaying_effects(
    work_store_paths, monkeypatch
):
    """A distinct-path v26 copy remains inspectable provenance, never a live inbox."""
    module = repository_module()
    source, job, original, source_context = _portable_restore_source(
        module, work_store_paths.database
    )
    source_before = work_store_paths.database.read_bytes()
    source_provenance = _provenance_rows(work_store_paths.database)
    source_events = source.read_events(job["id"])["events"]
    staging_path = work_store_paths.database.with_name("portable-staging.sqlite3")
    _copy_sqlite(work_store_paths.database, staging_path)
    staging = module.WorkRepository(staging_path)

    # RED boundary: ordinary verification deliberately rejects the staging
    # pathname before a recovery context can be changed.
    with staging.connection() as connection:
        with pytest.raises(module.SchemaMismatch, match="names another file"):
            staging._verify(connection)

    context = staging._block_portable_restore_copy(
        source_inbox_context=source_context,
        installation_uuid="f" * 32,
        bundle_digest="a" * 64,
        restore_receipt="b" * 64,
    )

    assert context.execution_state == "blocked_restore"
    assert context.installation_uuid == "f" * 32
    assert context.source_installation_uuid != context.installation_uuid
    assert context.bundle_digest == "a" * 64
    assert context.restore_receipt == "b" * 64
    assert work_store_paths.database.read_bytes() == source_before
    assert _provenance_rows(staging_path) == source_provenance
    assert _provenance_rows(work_store_paths.database) == source_provenance

    expected_attempt_states = {
        "planned": "planned",
        "sent": "reconcile",
        "acknowledged": "reconcile",
        "running": "reconcile",
        "reconcile": "reconcile",
        "cancelled": "cancelled",
    }
    for label, before in original.items():
        after = staging.get_work(before["id"])
        assert after["attempts"][-1]["id"] == before["attempts"][-1]["id"]
        assert after["attempts"][-1]["generation"] == before["attempts"][-1]["generation"]
        assert after["attempts"][-1]["state"] == expected_attempt_states[label]
    staged_events = staging.read_events(job["id"])["events"]
    assert staged_events[: len(source_events)] == source_events

    # The physical-path guard remains a fail-closed barrier.  Sensitivity to
    # the historical identity then proves the durable blocked context itself
    # is what denies the hypothetical same-identity reader.
    with staging.connection() as connection:
        with pytest.raises(module.SchemaMismatch, match="names another file"):
            staging.assert_execution_allowed(connection)
    monkeypatch.setattr(
        module, "_connection_store_identity", lambda _connection: source_context.store_identity
    )
    with staging.connection() as connection:
        with pytest.raises(module.SchemaMismatch, match="blocks execution"):
            staging.assert_execution_allowed(connection)


def test_portable_blocked_staging_publishes_to_a_distinct_third_path(work_store_paths):
    """Local publication moves only the already-blocked staging SQLite copy."""
    module = repository_module()
    _source, _job, _original, source_context = _portable_restore_source(
        module, work_store_paths.database
    )
    source_before = work_store_paths.database.read_bytes()
    source_provenance = _provenance_rows(work_store_paths.database)
    with sqlite3.connect(work_store_paths.database) as connection:
        source_recovery = connection.execute(
            "SELECT installation_uuid FROM work_recovery_context"
        ).fetchone()[0]
    staging_path = work_store_paths.database.with_name("portable-third-staging.sqlite3")
    destination_path = work_store_paths.database.with_name("portable-destination.sqlite3")
    _copy_sqlite(work_store_paths.database, staging_path)
    assert not destination_path.exists()

    module.WorkRepository(staging_path)._block_portable_restore_copy(
        source_inbox_context=source_context,
        installation_uuid="f" * 32,
        bundle_digest="a" * 64,
        restore_receipt="b" * 64,
    )
    staging_path.replace(destination_path)

    assert work_store_paths.database.read_bytes() == source_before
    assert not staging_path.exists()
    assert destination_path.exists()
    assert _provenance_rows(destination_path) == source_provenance
    with sqlite3.connect(destination_path) as connection:
        assert connection.execute(
            "SELECT execution_state,installation_uuid,source_installation_uuid,bundle_digest,restore_receipt "
            "FROM work_recovery_context"
        ).fetchone() == (
            "blocked_restore",
            "f" * 32,
            source_recovery,
            "a" * 64,
            "b" * 64,
        )

    destination = module.WorkRepository(destination_path)
    with destination.connection() as connection:
        with pytest.raises(module.SchemaMismatch) as rejected:
            destination.assert_execution_allowed(connection)
    assert "names another file" in str(rejected.value) or "blocks execution" in str(rejected.value)


@pytest.mark.parametrize(
    "damage", ["ddl", "ledger", "foreign_key", "non_normal", "identity", "uuid", "bridge"]
)
def test_portable_restore_copy_rejects_invalid_or_contradictory_provenance(
    work_store_paths, damage
):
    module = repository_module()
    source, _job, original, source_context = _portable_restore_source(
        module, work_store_paths.database
    )
    staging_path = work_store_paths.database.with_name(f"portable-{damage}.sqlite3")
    if damage == "non_normal":
        with source.transaction() as connection:
            source._block_recovery_for_restore(
                connection,
                installation_uuid="e" * 32,
                bundle_digest="a" * 64,
                restore_receipt="b" * 64,
            )
    _copy_sqlite(work_store_paths.database, staging_path)

    with sqlite3.connect(staging_path) as connection:
        if damage == "ddl":
            connection.execute("DROP TRIGGER work_recovery_context_transition")
        elif damage == "ledger":
            connection.execute("UPDATE work_migrations SET checksum='tampered' WHERE version=26")
        elif damage == "foreign_key":
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute(
                "UPDATE work_attempts SET work_item_id='missing-work' WHERE id=?",
                (original["planned"]["attempts"][-1]["id"],),
            )
        elif damage in {"identity", "uuid"}:
            trigger = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='work_inbox_store_context_immutable_update'"
            ).fetchone()[0]
            connection.execute("DROP TRIGGER work_inbox_store_context_immutable_update")
            if damage == "identity":
                connection.execute(
                    "UPDATE work_inbox_store_context SET store_identity=?",
                    (str(staging_path.with_name("forged-source.sqlite3").resolve()),),
                )
            else:
                connection.execute("UPDATE work_inbox_store_context SET store_uuid=?", ("c" * 32,))
            connection.execute(trigger)
        elif damage == "bridge":
            connection.execute(
                "INSERT INTO work_inbox_store_identity VALUES (1,?,?)",
                (str(staging_path.with_name("contradictory.sqlite3").resolve()), time.time()),
            )
    damaged_before = staging_path.read_bytes()

    staging = module.WorkRepository(staging_path)
    with pytest.raises(module.SchemaMismatch):
        staging._block_portable_restore_copy(
            source_inbox_context=source_context,
            installation_uuid="f" * 32,
            bundle_digest="a" * 64,
            restore_receipt="b" * 64,
        )
    assert staging_path.read_bytes() == damaged_before


def test_portable_restore_copy_rejects_the_live_source_store(work_store_paths):
    module = repository_module()
    source, _job, _original, source_context = _portable_restore_source(
        module, work_store_paths.database
    )
    source_before = work_store_paths.database.read_bytes()

    with pytest.raises(module.SchemaMismatch, match="path-distinct copied store"):
        source._block_portable_restore_copy(
            source_inbox_context=source_context,
            installation_uuid="f" * 32,
            bundle_digest="a" * 64,
            restore_receipt="b" * 64,
        )
    assert work_store_paths.database.read_bytes() == source_before


def test_portable_restore_copy_rolls_back_context_and_attempts_after_reconciliation_fault(
    work_store_paths, monkeypatch
):
    module = repository_module()
    _source, _job, _original, source_context = _portable_restore_source(
        module, work_store_paths.database
    )
    staging_path = work_store_paths.database.with_name("portable-rollback.sqlite3")
    _copy_sqlite(work_store_paths.database, staging_path)
    staging = module.WorkRepository(staging_path)
    with sqlite3.connect(staging_path) as connection:
        before_context = connection.execute("SELECT * FROM work_recovery_context").fetchall()
        before_attempts = connection.execute(
            "SELECT id,state,revision,generation FROM work_attempts ORDER BY id"
        ).fetchall()
        before_events = connection.execute(
            "SELECT event_id,job_id,work_item_id,attempt_id,sequence,event_type,actor_id,metadata "
            "FROM work_events ORDER BY sequence"
        ).fetchall()
        before_receipts = connection.execute(
            "SELECT event_id,request_hash,work_item_id FROM work_transition_receipts ORDER BY event_id"
        ).fetchall()

    original_transition = staging._transition_attempt
    calls = 0

    def fail_after_first_reconciliation(*args, **kwargs):
        nonlocal calls
        result = original_transition(*args, **kwargs)
        calls += 1
        if calls == 1:
            raise RuntimeError("injected portable reconciliation fault")
        return result

    monkeypatch.setattr(staging, "_transition_attempt", fail_after_first_reconciliation)
    with pytest.raises(RuntimeError, match="injected portable reconciliation fault"):
        staging._block_portable_restore_copy(
            source_inbox_context=source_context,
            installation_uuid="f" * 32,
            bundle_digest="a" * 64,
            restore_receipt="b" * 64,
        )
    with sqlite3.connect(staging_path) as connection:
        assert (
            connection.execute("SELECT * FROM work_recovery_context").fetchall() == before_context
        )
        assert (
            connection.execute(
                "SELECT id,state,revision,generation FROM work_attempts ORDER BY id"
            ).fetchall()
            == before_attempts
        )
        assert (
            connection.execute(
                "SELECT event_id,job_id,work_item_id,attempt_id,sequence,event_type,actor_id,metadata "
                "FROM work_events ORDER BY sequence"
            ).fetchall()
            == before_events
        )
        assert (
            connection.execute(
                "SELECT event_id,request_hash,work_item_id FROM work_transition_receipts ORDER BY event_id"
            ).fetchall()
            == before_receipts
        )


@pytest.mark.parametrize("damage", ["absent", "duplicate", "unknown_state", "unknown_version"])
def test_execution_guard_fails_closed_for_invalid_recovery_context(work_store_paths, damage):
    module = repository_module()
    store = module.WorkRepository(work_store_paths.database)
    store.initialize()

    with sqlite3.connect(work_store_paths.database) as connection:
        connection.execute("DROP TRIGGER work_recovery_context_transition")
        connection.execute("DROP TRIGGER work_recovery_context_immutable_delete")
        if damage == "absent":
            connection.execute("DELETE FROM work_recovery_context")
        elif damage == "duplicate":
            connection.execute("DROP TABLE work_recovery_context")
            connection.execute(
                "CREATE TABLE work_recovery_context "
                "(singleton INTEGER, context_version INTEGER, execution_state TEXT, "
                "installation_uuid TEXT, source_installation_uuid TEXT, "
                "bundle_digest TEXT, restore_receipt TEXT, created_at REAL)"
            )
            connection.executemany(
                "INSERT INTO work_recovery_context VALUES (?,?,?,?,?,?,?,?)",
                [
                    (1, 1, "normal", "a" * 32, None, None, None, 1),
                    (2, 1, "normal", "b" * 32, None, None, None, 1),
                ],
            )
        else:
            connection.execute("DROP TABLE work_recovery_context")
            connection.execute(
                "CREATE TABLE work_recovery_context "
                "(singleton INTEGER, context_version INTEGER, execution_state TEXT, "
                "installation_uuid TEXT, source_installation_uuid TEXT, "
                "bundle_digest TEXT, restore_receipt TEXT, created_at REAL)"
            )
            connection.execute(
                "INSERT INTO work_recovery_context VALUES (?,?,?,?,?,?,?,?)",
                (
                    1,
                    2 if damage == "unknown_version" else 1,
                    "normal" if damage == "unknown_version" else "unknown",
                    "a" * 32,
                    None,
                    None,
                    None,
                    1,
                ),
            )

    with store.connection() as connection:
        with pytest.raises(module.SchemaMismatch):
            store.assert_execution_allowed(connection)


def test_durable_registered_writer_blocks_cut_and_live_cut_blocks_new_writer(work_store_paths):
    """The lease fences durable effect registrations, not merely in-memory attempts."""
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.work_authority import WorkAuthority

    module = repository_module()
    store = module.WorkRepository(work_store_paths.database)
    store.initialize()
    operator = auth._verified_principal("https://cut.test", "operator", [auth.SCOPE_ADMIN], "jwt")
    job = store.create_job(
        project_id="cut-project",
        principal_id=operator.id,
        allowed_providers=["mock_cli"],
        grant_id="root",
    )
    work = store.admit_work(
        job_id=job["id"],
        operation_kind="launch",
        idempotency_key="writer",
        request_hash="a" * 64,
        contract_id="contract",
        snapshot_id="snapshot",
        provider="mock_cli",
        actor_id=operator.id,
    )
    writer_id = work["attempts"][0]["id"]
    with store.transaction() as connection:
        store._register_writer_effect(connection, writer_id=writer_id)
    with pytest.raises(module.WorkConflict, match="writer is active"):
        WorkAuthority(store).create_offline_cut(operator, ttl_seconds=60)
    with store.transaction() as connection:
        store._release_writer_effect(connection, writer_id=writer_id)
    lease = WorkAuthority(store).create_offline_cut(operator, ttl_seconds=60)
    with store.transaction() as connection:
        with pytest.raises(module.WorkConflict, match="fenced"):
            store._register_writer_effect(connection, writer_id=writer_id)
    # The denied effect never joins the registry or mutates the lease. A
    # restarted owner must still read the durable live lease, not local state.
    assert (
        WorkAuthority(module.WorkRepository(work_store_paths.database)).verify_offline_cut(
            lease, source_database=work_store_paths.database
        )
        == lease
    )


def test_offline_cut_rejects_forged_nonprincipal_and_local_fallback_operators(work_store_paths):
    """Only a canonical authenticated Principal can create a durable cut."""
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.work_authority import AuthorityDenied, WorkAuthority

    module = repository_module()
    store = module.WorkRepository(work_store_paths.database)
    store.initialize()
    authority = WorkAuthority(store)

    with pytest.raises(AuthorityDenied):
        authority.create_offline_cut(object(), ttl_seconds=60)

    forged = object.__new__(auth.Principal)
    for field, value in (
        ("id", "forged-principal"),
        ("issuer", "https://cut.test"),
        ("subject", "operator"),
        ("scopes", frozenset({auth.SCOPE_ADMIN})),
        ("kind", "jwt"),
    ):
        object.__setattr__(forged, field, value)
    with pytest.raises(AuthorityDenied):
        authority.create_offline_cut(forged, ttl_seconds=60)

    with pytest.raises(AuthorityDenied):
        authority.create_offline_cut(auth.local_operator_principal(), ttl_seconds=60)
