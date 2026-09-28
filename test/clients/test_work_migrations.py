"""Additive work schema must verify before accepting new durable operations."""

import importlib
import importlib.util
import sqlite3
import time
from contextlib import contextmanager

import pytest

from test.fixtures.work_store import work_store_paths  # noqa: F401


def repository_module():
    name = "cli_agent_orchestrator.clients.work_repository"
    assert importlib.util.find_spec(name) is not None, "verified work repository is missing"
    return importlib.import_module(name)


def verified_store_at_version(module, path, version):
    """Build an actual ledger for an older writer without invoking newer migrations."""
    store = module.WorkRepository(path)
    with store.transaction() as connection:
        for migration_version in range(1, version + 1):
            for statement in module._MIGRATIONS[migration_version]:
                connection.execute(statement)
            if migration_version == 16:
                store._initialize_inbox_store_context(connection)
            if migration_version == 24:
                store._initialize_recovery_context(connection)
            connection.execute(
                "INSERT INTO work_migrations VALUES (?,?,?,?)",
                (
                    migration_version,
                    module._CHECKSUMS[migration_version],
                    time.time(),
                    "verified",
                ),
            )
        module.WorkRepository._verify(connection, version=version)
    return store


def test_empty_store_is_verified_and_initialization_is_idempotent(work_store_paths):
    module = repository_module()
    store = module.WorkRepository(work_store_paths.database)
    store.initialize()
    store.initialize()
    store.verify_schema()
    with sqlite3.connect(work_store_paths.database) as connection:
        rows = connection.execute(
            "SELECT version, checksum, verification_result FROM work_migrations"
        ).fetchall()
    assert len(rows) == module.SCHEMA_VERSION
    assert rows[0][0] == 1 and len(rows[0][1]) == 64 and rows[0][2] == "verified"
    assert rows[1][0] == 2 and len(rows[1][1]) == 64 and rows[1][2] == "verified"


def test_v25_to_current_adds_exact_cut_process_identity_and_v2_evidence_schema(work_store_paths):
    """Every later additive store remains ledger-exact."""
    module = repository_module()
    store = verified_store_at_version(module, work_store_paths.database, version=25)
    with store.connection() as connection:
        ledger = connection.execute(
            "SELECT version,checksum,verification_result FROM work_migrations ORDER BY version"
        ).fetchall()
        assert [tuple(row) for row in ledger] == [
            (version, module._CHECKSUMS[version], "verified") for version in range(1, 26)
        ]
        assert module._schema_objects(connection) == module._EXPECTED_SCHEMAS[25]

    store.initialize()

    with store.connection() as connection:
        ledger = connection.execute(
            "SELECT version,checksum,verification_result FROM work_migrations ORDER BY version"
        ).fetchall()
        assert [tuple(row) for row in ledger] == [
            (version, module._CHECKSUMS[version], "verified")
            for version in range(1, module.SCHEMA_VERSION + 1)
        ]
        assert module._schema_objects(connection) == module._EXPECTED_SCHEMAS[module.SCHEMA_VERSION]
        for name in (
            "work_offline_cuts",
            "work_offline_cut_rejections",
            "work_registered_writers",
            "work_process_identities",
            "work_dispatch_v2_evidence",
        ):
            assert name in module._schema_objects(connection)
        with pytest.raises(module.SchemaMismatch):
            module.WorkRepository._verify(connection, version=25)
        with pytest.raises(module.SchemaMismatch):
            module.WorkRepository._verify(connection, version=26)


def test_v26_to_v27_adds_process_identity_store_without_changing_attempts(work_store_paths):
    module = repository_module()
    store = verified_store_at_version(module, work_store_paths.database, version=26)
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO work_jobs "
            "(id,project_id,principal_id,allowed_providers,grant_id,created_at) "
            "VALUES ('legacy-job','project','owner','[\"mock_cli\"]','grant',1)"
        )
        connection.execute(
            "INSERT INTO work_items "
            "(id,job_id,operation_kind,idempotency_key,request_hash,contract_id,created_at) "
            "VALUES ('legacy-work','legacy-job','launch','legacy-key',?, 'contract',1)",
            ("b" * 64,),
        )
        connection.execute(
            "INSERT INTO work_attempts "
            "(id,work_item_id,attempt_number,generation,provider,lease_expires_at,created_at) "
            "VALUES ('legacy-attempt','legacy-work',1,1,'mock_cli',999,1)"
        )
        attempts_before = [
            tuple(row)
            for row in connection.execute("SELECT * FROM work_attempts ORDER BY id").fetchall()
        ]

    assert module.SCHEMA_VERSION >= 27
    store.initialize()

    with store.connection() as connection:
        module.WorkRepository._verify(connection)
        ledger = connection.execute(
            "SELECT version,checksum,verification_result FROM work_migrations ORDER BY version"
        ).fetchall()
        assert [tuple(row) for row in ledger] == [
            (version, module._CHECKSUMS[version], "verified")
            for version in range(1, module.SCHEMA_VERSION + 1)
        ]
        assert module._schema_objects(connection) == module._EXPECTED_SCHEMAS[module.SCHEMA_VERSION]
        assert [
            tuple(row)
            for row in connection.execute("SELECT * FROM work_attempts ORDER BY id").fetchall()
        ] == attempts_before
        assert connection.execute("SELECT count(*) FROM work_process_identities").fetchone()[0] == 0


def test_v27_to_v28_preserves_attempts_dispatch_schema_and_repeated_startup(work_store_paths):
    from cli_agent_orchestrator.models.work_contract import (
        ContractPermissions,
        ContractResources,
        ContractSnapshot,
        EffectiveWorkContract,
    )

    module = repository_module()
    store = verified_store_at_version(module, work_store_paths.database, version=27)
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO work_jobs "
            "(id,project_id,principal_id,allowed_providers,grant_id,created_at) "
            "VALUES ('v27-job','project','owner','[\"mock_cli\"]','grant',1)"
        )
        connection.execute(
            "INSERT INTO work_items "
            "(id,job_id,operation_kind,idempotency_key,request_hash,contract_id,created_at) "
            "VALUES ('v27-work','v27-job','launch','legacy-key',?, 'contract',1)",
            ("b" * 64,),
        )
        connection.execute(
            "INSERT INTO work_attempts "
            "(id,work_item_id,attempt_number,generation,provider,lease_expires_at,created_at) "
            "VALUES ('v27-attempt','v27-work',1,1,'mock_cli',999,1)"
        )
        connection.execute(
            "INSERT INTO work_principals VALUES ('owner','issuer','subject','jwt',1)"
        )
        connection.execute(
            "INSERT INTO work_grants "
            "(id,revision,job_id,principal_id,allowed_providers,permissions,expires_at,created_at) "
            "VALUES ('grant',1,'v27-job','owner','[\"mock_cli\"]','{}',999,1)"
        )
        connection.execute(
            "INSERT INTO work_delegation_snapshots "
            "(id,schema_version,job_id,contract_id,binding_key,request_hash,scope,scope_id,"
            "producer_principal_id,source_hash,delivered_hash,content,redacted,truncated,created_at) "
            "VALUES ('v27-snapshot',1,'v27-job','contract','key',?,'project','project',"
            "'owner',?,?,?,0,0,1)",
            ("a" * 64, "b" * 64, "c" * 64, b""),
        )
        contract = EffectiveWorkContract(
            id="contract",
            operation_kind="launch",
            provider="mock_cli",
            backend="test",
            permissions=ContractPermissions(),
            resources=ContractResources(checkout_root="/workspace", units=1),
            snapshot=ContractSnapshot(state="present", id="v27-snapshot", delivered_hash="c" * 64),
        )
        connection.execute(
            "INSERT INTO work_dispatch_bindings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "v27-attempt",
                1,
                "v27-job",
                "v27-work",
                "owner",
                "grant",
                1,
                "contract",
                contract.canonical_json(),
                contract.canonical_hash(),
                "v27-snapshot",
                1,
            ),
        )
        dispatch_before = tuple(
            connection.execute(
                "SELECT * FROM work_dispatch_bindings WHERE attempt_id='v27-attempt'"
            ).fetchone()
        )
        attempts_before = [
            tuple(row) for row in connection.execute("SELECT * FROM work_attempts ORDER BY id")
        ]
        prior_dispatch_objects = {
            name: sql
            for name, sql in module._schema_objects(connection).items()
            if name.startswith("work_dispatch_bindings")
        }
        prior_ledger = [
            tuple(row)
            for row in connection.execute(
                "SELECT version,checksum,verification_result FROM work_migrations ORDER BY version"
            )
        ]
    store.initialize()
    store.initialize()
    with store.connection() as connection:
        module.WorkRepository._verify(connection)
        assert connection.execute("SELECT id FROM work_attempts").fetchone()[0] == "v27-attempt"
        assert (
            tuple(
                connection.execute(
                    "SELECT * FROM work_dispatch_bindings WHERE attempt_id='v27-attempt'"
                ).fetchone()
            )
            == dispatch_before
        )
        assert {
            name: sql
            for name, sql in module._schema_objects(connection).items()
            if name.startswith("work_dispatch_bindings")
        } == prior_dispatch_objects
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE work_dispatch_bindings SET contract_hash=? WHERE attempt_id='v27-attempt'",
                ("f" * 64,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM work_dispatch_bindings WHERE attempt_id='v27-attempt'")
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT version,checksum,verification_result FROM work_migrations ORDER BY version"
            )
        ] == prior_ledger + [
            (version, module._CHECKSUMS[version], "verified")
            for version in range(28, module.SCHEMA_VERSION + 1)
        ]
        assert "work_dispatch_v2_evidence" in module._schema_objects(connection)
        assert [
            tuple(row)
            for row in connection.execute("SELECT * FROM work_attempts ORDER BY id").fetchall()
        ] == attempts_before


def test_v28_to_v29_preserves_populated_v1_v2_bindings_and_fks(work_store_paths):
    from cli_agent_orchestrator.models.work_contract import (
        ContractPermissions,
        ContractResources,
        ContractSnapshot,
        EffectiveWorkContract,
        EffectiveWorkContractV2,
    )
    from cli_agent_orchestrator.services.work_contract import WorkContracts

    module = repository_module()
    assert module.SCHEMA_VERSION >= 29
    store = verified_store_at_version(module, work_store_paths.database, version=28)
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO work_jobs "
            "(id,project_id,principal_id,allowed_providers,grant_id,created_at) "
            "VALUES ('job','project','owner','[\"mock_cli\"]','grant',1)"
        )
        connection.execute(
            "INSERT INTO work_principals VALUES ('owner','issuer','subject','jwt',1)"
        )
        connection.execute(
            "INSERT INTO work_grants "
            "(id,revision,job_id,principal_id,allowed_providers,permissions,expires_at,created_at) "
            "VALUES ('grant',1,'job','owner','[\"mock_cli\"]','{}',999,1)"
        )
        for version in (1, 2):
            suffix = str(version)
            connection.execute(
                "INSERT INTO work_items "
                "(id,job_id,operation_kind,idempotency_key,request_hash,contract_id,created_at) "
                "VALUES (?, 'job','launch',?, ?, ?,1)",
                (f"work-{suffix}", f"key-{suffix}", "a" * 64, f"contract-{suffix}"),
            )
            connection.execute(
                "INSERT INTO work_attempts "
                "(id,work_item_id,attempt_number,generation,provider,lease_expires_at,created_at) "
                "VALUES (?,?,1,1,'mock_cli',999,1)",
                (f"attempt-{suffix}", f"work-{suffix}"),
            )
            connection.execute(
                "INSERT INTO work_delegation_snapshots "
                "(id,schema_version,job_id,contract_id,binding_key,request_hash,scope,scope_id,"
                "producer_principal_id,source_hash,delivered_hash,content,redacted,"
                "truncated,created_at) "
                "VALUES (?,1,'job',?,'key',?,'project','project','owner',?,?,?,0,0,1)",
                (f"snapshot-{suffix}", f"contract-{suffix}", "a" * 64, "b" * 64, "c" * 64, b""),
            )
            fields = dict(
                id=f"contract-{suffix}",
                operation_kind="launch",
                provider="mock_cli",
                backend="test",
                permissions=ContractPermissions(),
                resources=ContractResources(checkout_root="/workspace", units=1),
                snapshot=ContractSnapshot(
                    state="present", id=f"snapshot-{suffix}", delivered_hash="c" * 64
                ),
            )
            contract = (
                EffectiveWorkContract(**fields)
                if version == 1
                else EffectiveWorkContractV2(**fields)
            )
            connection.execute(
                "INSERT INTO work_dispatch_bindings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"attempt-{suffix}",
                    1,
                    "job",
                    f"work-{suffix}",
                    "owner",
                    "grant",
                    1,
                    f"contract-{suffix}",
                    WorkContracts._projection(contract),
                    contract.canonical_hash(),
                    f"snapshot-{suffix}",
                    1,
                ),
            )
            if version == 2:
                connection.execute(
                    "INSERT INTO work_dispatch_v2_evidence VALUES (?,?,?,?)",
                    ("attempt-2", 1, contract.canonical_json(), contract.canonical_hash()),
                )
        preserved = {
            table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in (
                "work_jobs",
                "work_items",
                "work_attempts",
                "work_dispatch_bindings",
                "work_dispatch_v2_evidence",
                "work_delegation_snapshots",
            )
        }
    store.initialize()
    store.initialize()
    with store.connection() as connection:
        module.WorkRepository._verify(connection)
        for table, rows in preserved.items():
            restored = [
                tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")
            ]
            assert restored == rows
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert tuple(
            connection.execute(
                "SELECT version,checksum FROM work_migrations WHERE version=29"
            ).fetchone()
        ) == (29, module._CHECKSUMS[29])
        assert "work_executable_contents" in module._schema_objects(connection)
        digest = "a" * 64
        for invalid in (
            ("A" * 64, "A" * 64, 1),
            (digest, digest, 0),
            (digest, digest, 8 * 1024 * 1024 + 1),
            (digest, "b" * 64, 1),
            (b"c" * 64, b"c" * 64, 1),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO work_executable_contents VALUES (?,?,?,?)", (*invalid, 1.0)
                )
        connection.execute(
            "INSERT INTO work_executable_contents VALUES (?,?,?,?)",
            (digest, digest, 1, 1.0),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE work_executable_contents SET byte_length=2 WHERE content_hash=?",
                (digest,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM work_executable_contents WHERE content_hash=?", (digest,)
            )
        for suffix in ("1", "2"):
            row = connection.execute(
                "SELECT * FROM work_dispatch_bindings WHERE attempt_id=?", (f"attempt-{suffix}",)
            ).fetchone()
            assert WorkContracts._stored_contract(connection, row).id == f"contract-{suffix}"


def test_legacy_rows_and_schema_are_unchanged(work_store_paths):
    with sqlite3.connect(work_store_paths.database) as connection:
        connection.execute(
            "CREATE TABLE terminal_turn_receipts (terminal_id TEXT PRIMARY KEY, phase TEXT)"
        )
        connection.execute("INSERT INTO terminal_turn_receipts VALUES ('legacy-terminal', 'sent')")
        before = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='terminal_turn_receipts'"
        ).fetchone()
    store = repository_module().WorkRepository(work_store_paths.database)
    store.initialize()
    with sqlite3.connect(work_store_paths.database) as connection:
        assert connection.execute("SELECT * FROM terminal_turn_receipts").fetchall() == [
            ("legacy-terminal", "sent")
        ]
        assert (
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='terminal_turn_receipts'"
            ).fetchone()
            == before
        )
        assert connection.execute("SELECT count(*) FROM work_items").fetchone()[0] == 0


def test_partial_or_unknown_schema_fails_closed_without_marking_applied(work_store_paths):
    module = repository_module()
    with sqlite3.connect(work_store_paths.database) as connection:
        connection.execute("CREATE TABLE work_items (id TEXT)")
    with pytest.raises(module.SchemaMismatch):
        module.WorkRepository(work_store_paths.database).initialize()
    with sqlite3.connect(work_store_paths.database) as connection:
        assert (
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='work_migrations'"
            ).fetchone()
            is None
        )


@pytest.mark.parametrize("damage", ["checksum", "future", "column", "index", "foreign_key"])
def test_verified_ledger_does_not_hide_schema_damage(work_store_paths, damage):
    module = repository_module()
    store = module.WorkRepository(work_store_paths.database)
    store.initialize()
    with sqlite3.connect(work_store_paths.database) as connection:
        if damage == "checksum":
            connection.execute("UPDATE work_migrations SET checksum='tampered'")
        elif damage == "future":
            connection.execute(
                "UPDATE work_migrations SET version=999 WHERE version=(SELECT max(version) FROM work_migrations)"
            )
        elif damage == "column":
            connection.execute("ALTER TABLE work_jobs RENAME COLUMN state TO broken_state")
        elif damage == "index":
            connection.execute("DROP INDEX work_items_idempotency")
        else:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute(
                "INSERT INTO work_attempts (id,work_item_id,attempt_number,generation,provider,state,lease_expires_at,created_at) VALUES ('bad','missing',1,1,'mock_cli','planned',123,100)"
            )
    with pytest.raises(module.SchemaMismatch):
        store.initialize()


def test_read_only_legacy_reader_can_read_after_additive_migration(work_store_paths):
    with sqlite3.connect(work_store_paths.database) as connection:
        connection.execute("CREATE TABLE native_children (id TEXT PRIMARY KEY, state TEXT)")
        connection.execute("INSERT INTO native_children VALUES ('old-child', 'acknowledged')")
    repository_module().WorkRepository(work_store_paths.database).initialize()
    with sqlite3.connect(f"file:{work_store_paths.database}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT id,state FROM native_children").fetchall() == [
            ("old-child", "acknowledged")
        ]


def test_database_initialization_runs_verified_work_migration(work_store_paths, monkeypatch):
    from cli_agent_orchestrator.clients import database

    monkeypatch.setattr("cli_agent_orchestrator.constants.DATABASE_FILE", work_store_paths.database)
    # Isolate this new hook, without invoking legacy migrators against operator state.
    for name in vars(database):
        if name.startswith("_migrate_") or name == "_restrict_db_file_permissions":
            monkeypatch.setattr(database, name, lambda: None)
    monkeypatch.setattr(database.Base.metadata, "create_all", lambda **kwargs: None)
    database.init_db()
    assert work_store_paths.database.exists(), "init_db did not initialize durable work"
    repository_module().WorkRepository(work_store_paths.database).verify_schema()


def test_reservations_and_step_contracts_are_verified_migrations(work_store_paths):
    module = repository_module()
    store = module.WorkRepository(work_store_paths.database)
    store.initialize()
    with sqlite3.connect(work_store_paths.database) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert "work_reservation_sets" in names
        assert "work_path_reservations" in names
        assert "work_step_contracts" in names
        assert connection.execute(
            "SELECT version FROM work_migrations ORDER BY version"
        ).fetchall() == [(version,) for version in range(1, module.SCHEMA_VERSION + 1)]
        connection.execute("DROP TRIGGER work_step_contracts_immutable_update")
    with pytest.raises(module.SchemaMismatch):
        store.verify_schema()


def test_recovery_context_v24_is_additive_verified_and_rolls_back(work_store_paths, monkeypatch):
    """v24 adds recovery context without changing the v23 lineage migration history."""
    module = repository_module()
    assert module.SCHEMA_VERSION >= 25, "T094 v25 task receipt migration is missing"
    store = verified_store_at_version(module, work_store_paths.database, version=23)
    with sqlite3.connect(work_store_paths.database) as connection:
        connection.execute(
            "INSERT INTO work_jobs "
            "(id,project_id,principal_id,allowed_providers,grant_id,created_at) "
            "VALUES ('v23-job','project','owner','[]','grant',1)"
        )
        before_ledger = connection.execute(
            "SELECT * FROM work_migrations WHERE version<=23 ORDER BY version"
        ).fetchall()
        before_jobs = connection.execute("SELECT * FROM work_jobs").fetchall()
    store.initialize()
    store.verify_schema()
    with store.connection() as connection:
        # An older v24 verifier sees both the extra DDL and ledger entry as a
        # mixed version, so it must not read/execute this newer store.
        with pytest.raises(module.SchemaMismatch):
            module.WorkRepository._verify(connection, version=24)
    with sqlite3.connect(work_store_paths.database) as connection:
        assert (
            connection.execute(
                "SELECT * FROM work_migrations WHERE version<=23 ORDER BY version"
            ).fetchall()
            == before_ledger
        )
        assert connection.execute("SELECT * FROM work_jobs").fetchall() == before_jobs
        assert connection.execute(
            "SELECT version FROM work_migrations WHERE version=20"
        ).fetchone() == (20,)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='work_origin_authorizations'"
        ).fetchone() == ("work_origin_authorizations",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='work_launch_origin_bindings'"
        ).fetchone() == ("work_launch_origin_bindings",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='work_lineage_integrity'"
        ).fetchone() == ("work_lineage_integrity",)
        context = connection.execute(
            "SELECT context_version,execution_state,installation_uuid,"
            "source_installation_uuid,bundle_digest,restore_receipt "
            "FROM work_recovery_context"
        ).fetchall()
        assert len(context) == 1
        assert context[0][0] == 1
        assert context[0][1] == "normal"
        assert len(context[0][2]) == 32
        assert context[0][3:] == (None, None, None)
        assert connection.execute(
            "SELECT version FROM work_migrations WHERE version=24"
        ).fetchone() == (24,)
        connection.execute("DROP TRIGGER work_lineage_integrity_immutable_update")
        connection.execute("DROP TRIGGER work_origin_subjects_immutable_update")
    with pytest.raises(module.SchemaMismatch):
        store.verify_schema()

    failed = verified_store_at_version(
        module, work_store_paths.artifacts / "v24-failure.sqlite3", version=23
    )
    original = failed.connection

    @contextmanager
    def deny_recovery_context():
        with original() as connection:
            connection.set_authorizer(
                lambda action, name, *args: (
                    sqlite3.SQLITE_DENY
                    if action == sqlite3.SQLITE_CREATE_TABLE and name == "work_recovery_context"
                    else sqlite3.SQLITE_OK
                )
            )
            yield connection

    monkeypatch.setattr(failed, "connection", deny_recovery_context)
    with pytest.raises(sqlite3.DatabaseError):
        failed.initialize()
    with sqlite3.connect(failed.path) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='work_recovery_context'"
            ).fetchone()
            is None
        )
        assert connection.execute("SELECT max(version) FROM work_migrations").fetchone() == (23,)


def test_v26_offline_cut_ddl_denial_rolls_back_without_ledger_entry(work_store_paths, monkeypatch):
    """The complete v26 cut fence is atomic when SQLite rejects one of its DDL statements."""
    module = repository_module()
    store = verified_store_at_version(module, work_store_paths.database, version=25)
    original = store.connection

    @contextmanager
    def deny_offline_cut_schema():
        with original() as connection:
            connection.set_authorizer(
                lambda action, name, *args: (
                    sqlite3.SQLITE_DENY
                    if action == sqlite3.SQLITE_CREATE_TABLE and name == "work_offline_cuts"
                    else sqlite3.SQLITE_OK
                )
            )
            yield connection

    monkeypatch.setattr(store, "connection", deny_offline_cut_schema)
    with pytest.raises(sqlite3.DatabaseError):
        store.initialize()
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT max(version) FROM work_migrations").fetchone() == (25,)
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='work_offline_cuts'"
            ).fetchone()
            is None
        )


def test_snapshot_migration_preserves_all_v8_ledger_entries(work_store_paths):
    module = repository_module()
    with sqlite3.connect(work_store_paths.database) as connection:
        for version in range(1, 9):
            for statement in module._MIGRATIONS[version]:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO work_migrations VALUES (?,?,?,?)",
                (version, module._CHECKSUMS[version], float(version), "verified"),
            )
        before = connection.execute("SELECT * FROM work_migrations ORDER BY version").fetchall()
    store = module.WorkRepository(work_store_paths.database)
    store.initialize()
    with sqlite3.connect(work_store_paths.database) as connection:
        assert (
            connection.execute(
                "SELECT * FROM work_migrations WHERE version<=8 ORDER BY version"
            ).fetchall()
            == before
        )
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert "work_delegation_snapshots" in names
        assert "work_snapshot_sources" in names
        assert connection.execute(
            "SELECT verification_result FROM work_migrations WHERE version=9"
        ).fetchone() == ("verified",)
        connection.execute("DROP TRIGGER work_delegation_snapshots_immutable_update")
    with pytest.raises(module.SchemaMismatch):
        store.verify_schema()


def test_delivery_migration_preserves_existing_v10_bindings(work_store_paths):
    module = importlib.import_module("cli_agent_orchestrator.clients.work_repository")
    path = work_store_paths.database
    with sqlite3.connect(path) as connection:
        for version in range(1, 11):
            for statement in module._MIGRATIONS[version]:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO work_migrations VALUES (?,?,?,?)",
                (version, module._CHECKSUMS[version], 1.0, "verified"),
            )
    repository = module.WorkRepository(path)
    repository.initialize()
    repository.initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='work_delivery_orders'"
        ).fetchone() == ("work_delivery_orders",)
        assert connection.execute("SELECT count(*) FROM work_dispatch_bindings").fetchone()[0] == 0
        connection.execute("DROP TRIGGER work_delivery_orders_immutable_update")
    with pytest.raises(module.SchemaMismatch):
        repository.verify_schema()


def test_delivery_content_migration_preserves_v13_plaintext_history(work_store_paths):
    """Expand-only v14 keeps a valid v13 delivery row as non-rewritable history."""
    module = repository_module()
    path = work_store_paths.database
    digest = "a" * 64
    with sqlite3.connect(path) as connection:
        for version in range(1, 14):
            for statement in module._MIGRATIONS[version]:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO work_migrations VALUES (?,?,?,?)",
                (version, module._CHECKSUMS[version], float(version), "verified"),
            )
        connection.execute(
            "INSERT INTO work_principals VALUES ('principal','issuer','subject','jwt',1)"
        )
        connection.execute(
            "INSERT INTO work_jobs (id,project_id,principal_id,allowed_providers,grant_id,created_at) "
            "VALUES ('job','project','principal','[\"mock_cli\"]','grant',1)"
        )
        connection.execute(
            "INSERT INTO work_grants "
            "(id,revision,job_id,principal_id,allowed_providers,permissions,expires_at,created_at) "
            "VALUES ('grant',1,'job','principal','[\"mock_cli\"]','{}',100,1)"
        )
        connection.execute(
            "INSERT INTO work_delegation_snapshots VALUES "
            "('snapshot',1,'job','contract','binding',?,'project','project','principal',?,?,X'78',0,0,1)",
            (digest, digest, digest),
        )
        connection.execute(
            "INSERT INTO work_items "
            "(id,job_id,operation_kind,idempotency_key,request_hash,contract_id,snapshot_id,created_at) "
            "VALUES ('work','job','launch','legacy',?,'contract','snapshot',1)",
            (digest,),
        )
        connection.execute(
            "INSERT INTO work_attempts "
            "(id,work_item_id,attempt_number,generation,provider,lease_expires_at,created_at) "
            "VALUES ('attempt','work',1,1,'mock_cli',100,1)"
        )
        connection.execute(
            "INSERT INTO work_dispatch_bindings VALUES "
            "('attempt',1,'job','work','principal','grant',1,'contract',"
            '\'{"schema_version":1,"id":"contract"}\',?,\'snapshot\',1)',
            (digest,),
        )
        connection.execute(
            "INSERT INTO work_delivery_orders VALUES "
            "('attempt',1,'launch',1,1,?,'{\"message\":\"legacy plaintext\"}',?,?)",
            (digest, digest, digest),
        )
        before = connection.execute("SELECT * FROM work_delivery_orders").fetchall()

    repository = module.WorkRepository(path)
    repository.initialize()
    repository.verify_schema()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM work_delivery_orders").fetchall() == before
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='work_delivery_content_refs'"
        ).fetchone() == ("work_delivery_content_refs",)
        assert connection.execute("SELECT count(*) FROM work_delivery_content_refs").fetchone() == (
            0,
        )
        assert connection.execute(
            "SELECT version FROM work_migrations WHERE version=14"
        ).fetchone() == (14,)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE work_delivery_orders SET adapter_version=2")


def test_dispatch_binding_migration_is_additive_and_verified(work_store_paths):
    module = repository_module()
    with sqlite3.connect(work_store_paths.database) as connection:
        for version in range(1, 10):
            for statement in module._MIGRATIONS[version]:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO work_migrations VALUES (?,?,?,?)",
                (version, module._CHECKSUMS[version], float(version), "verified"),
            )
        before = connection.execute("SELECT * FROM work_migrations ORDER BY version").fetchall()
    store = module.WorkRepository(work_store_paths.database)
    store.initialize()
    with sqlite3.connect(work_store_paths.database) as connection:
        assert (
            connection.execute(
                "SELECT * FROM work_migrations WHERE version<=9 ORDER BY version"
            ).fetchall()
            == before
        )
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='work_dispatch_bindings'"
        ).fetchone() == ("work_dispatch_bindings",)
        connection.execute("DROP TRIGGER work_dispatch_bindings_immutable_update")
    with pytest.raises(module.SchemaMismatch):
        store.verify_schema()


def seed_v1(path):
    module = repository_module()
    with sqlite3.connect(path) as connection:
        for statement in module._SCHEMA:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO work_migrations VALUES (1,?,123,'verified')", (module.SCHEMA_CHECKSUM,)
        )
        connection.execute(
            "INSERT INTO work_jobs (id,project_id,principal_id,allowed_providers,grant_id,created_at) VALUES ('old','project','owner','[]','old-grant',100)"
        )


def test_knowledge_and_fairness_are_additive_verified_migrations(work_store_paths):
    module = repository_module()
    store = module.WorkRepository(work_store_paths.database)
    store.initialize()
    with sqlite3.connect(work_store_paths.database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='work_knowledge_revisions'"
        ).fetchone()
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(work_scheduler_requests)")
        }
        assert "enqueue_frontier" in columns
        assert connection.execute(
            "SELECT version FROM work_migrations WHERE version IN (7,8) ORDER BY version"
        ).fetchall() == [(7,), (8,)]
        connection.execute("DROP TRIGGER work_scheduler_requests_frozen_frontier")
    with pytest.raises(module.SchemaMismatch):
        store.initialize()


def test_worktree_evidence_participates_in_verified_history_and_gc(work_store_paths):
    module = repository_module()
    store = module.WorkRepository(work_store_paths.database)
    store.initialize()
    with sqlite3.connect(work_store_paths.database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='work_worktree_evidence'"
        ).fetchone()
        assert connection.execute(
            "SELECT version FROM work_migrations WHERE version=6"
        ).fetchone() == (6,)
    assert store.referenced_artifact_hashes() == set()


def test_scheduler_schema_is_verified_with_earlier_history_intact(work_store_paths):
    module = repository_module()
    seed_v1(work_store_paths.database)
    store = module.WorkRepository(work_store_paths.database)
    store.initialize()
    with sqlite3.connect(work_store_paths.database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='work_scheduler_requests'"
        ).fetchone()
        assert connection.execute(
            "SELECT checksum FROM work_migrations WHERE version=1"
        ).fetchone() == (module.SCHEMA_CHECKSUM,)
        assert connection.execute(
            "SELECT version FROM work_migrations WHERE version=5"
        ).fetchone() == (5,)


def test_authority_migration_preserves_v1_checksum_and_rows(work_store_paths):
    module = repository_module()
    seed_v1(work_store_paths.database)
    with sqlite3.connect(work_store_paths.database) as connection:
        before = connection.execute("SELECT * FROM work_jobs").fetchall()
        ledger = connection.execute("SELECT * FROM work_migrations").fetchall()
    store = module.WorkRepository(work_store_paths.database)
    store.initialize()
    store.verify_schema()
    with sqlite3.connect(work_store_paths.database) as connection:
        assert connection.execute("SELECT * FROM work_jobs").fetchall() == before
        assert (
            connection.execute("SELECT * FROM work_migrations WHERE version=1").fetchall() == ledger
        )
        assert connection.execute("SELECT count(*) FROM work_grants").fetchone() == (0,)


def test_failed_authority_migration_rolls_back_all_new_objects(work_store_paths, monkeypatch):
    module = repository_module()
    seed_v1(work_store_paths.database)
    with sqlite3.connect(work_store_paths.database) as connection:
        before = connection.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY name"
        ).fetchall()
    store = module.WorkRepository(work_store_paths.database)
    original = store.connection

    @contextmanager
    def failing_connection():
        with original() as connection:
            connection.set_authorizer(
                lambda action, name, *args: (
                    sqlite3.SQLITE_DENY
                    if action == sqlite3.SQLITE_CREATE_TABLE and name == "work_grants"
                    else sqlite3.SQLITE_OK
                )
            )
            yield connection

    monkeypatch.setattr(store, "connection", failing_connection)
    with pytest.raises(sqlite3.DatabaseError):
        store.initialize()
    with sqlite3.connect(work_store_paths.database) as connection:
        assert (
            connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall()
            == before
        )


def test_v1_corrupt_ledger_is_not_upgraded(work_store_paths):
    module = repository_module()
    seed_v1(work_store_paths.database)
    with sqlite3.connect(work_store_paths.database) as connection:
        connection.execute("UPDATE work_migrations SET checksum='bad'")
    with pytest.raises(module.SchemaMismatch):
        module.WorkRepository(work_store_paths.database).initialize()
    with sqlite3.connect(work_store_paths.database) as connection:
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE name='work_grants'").fetchone()
            is None
        )


def test_knowledge_access_migration_preserves_populated_v11_and_is_immutable(work_store_paths):
    module = repository_module()
    path = work_store_paths.database
    seed_v1(path)
    with sqlite3.connect(path) as connection:
        for version in range(2, 12):
            for statement in module._MIGRATIONS[version]:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO work_migrations VALUES (?,?,?,?)",
                (version, module._CHECKSUMS[version], float(version), "verified"),
            )
        before_jobs = connection.execute("SELECT * FROM work_jobs").fetchall()
        before_ledger = connection.execute(
            "SELECT * FROM work_migrations ORDER BY version"
        ).fetchall()
    store = module.WorkRepository(path)
    store.initialize()
    store.initialize()
    store.verify_schema()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM work_jobs").fetchall() == before_jobs
        assert (
            connection.execute(
                "SELECT * FROM work_migrations WHERE version<=11 ORDER BY version"
            ).fetchall()
            == before_ledger
        )
        connection.execute(
            "INSERT INTO work_knowledge_access_audit "
            "(actor_id,authority_hash,action,target_hash,outcome,occurred_at) VALUES (?,?,?,?,?,?)",
            ("operator", "a" * 64, "read", "b" * 64, "allowed", 1.0),
        )
        for operation in (
            "UPDATE work_knowledge_access_audit SET actor_id='other'",
            "DELETE FROM work_knowledge_access_audit",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(operation)
        connection.execute("DROP TRIGGER work_knowledge_access_audit_immutable_update")
    with pytest.raises(module.SchemaMismatch):
        store.verify_schema()


def test_v30_to_v31_preserves_identity_rows_and_adds_private_setup_intent(work_store_paths):
    from test.clients.test_work_bubblewrap_process_identity import _identity, _sent_attempt

    module = repository_module()
    store = verified_store_at_version(module, work_store_paths.database, 30)
    _sent_attempt(store)
    identity = _identity()
    identity_json = module._json(identity)
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO work_bubblewrap_process_identities "
            "(attempt_id,generation,protocol_version,identity_json,identity_sha256,created_at) "
            "VALUES ('a',1,1,?,?,?)",
            (identity_json, identity["identity_sha256"], 1.0),
        )
    with store.connection() as connection:
        old_objects = module._schema_objects(connection)
        old_identity = tuple(
            connection.execute("SELECT * FROM work_bubblewrap_process_identities").fetchone()
        )
        old_attempt = tuple(
            connection.execute("SELECT * FROM work_attempts WHERE id='a'").fetchone()
        )
    with pytest.raises(module.SchemaMismatch):
        store.verify_schema()

    store.initialize()
    store.initialize()
    store.verify_schema()
    with store.connection() as connection:
        objects = module._schema_objects(connection)
        assert set(old_objects.items()).issubset(set(objects.items()))
        assert (
            tuple(connection.execute("SELECT * FROM work_bubblewrap_process_identities").fetchone())
            == old_identity
        )
        assert (
            tuple(connection.execute("SELECT * FROM work_attempts WHERE id='a'").fetchone())
            == old_attempt
        )
        assert "work_bubblewrap_setup_intents" in objects
        assert (
            connection.execute("SELECT count(*) FROM work_bubblewrap_setup_intents").fetchone()[0]
            == 0
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert [
            row[0]
            for row in connection.execute("SELECT version FROM work_migrations ORDER BY version")
        ] == list(range(1, module.SCHEMA_VERSION + 1))
        with pytest.raises(module.SchemaMismatch):
            module.WorkRepository._verify(connection, version=30)


def test_v31_to_v32_preserves_setup_intent_and_adds_immutable_release_claim(work_store_paths):
    from test.clients.test_work_bubblewrap_process_identity import _identity, _sent_attempt

    module = repository_module()
    store = verified_store_at_version(module, work_store_paths.database, 31)
    _sent_attempt(store)
    identity = _identity()
    digest = "a" * 64
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO work_executable_contents VALUES (?,?,?,?)", (digest, digest, 4, 1.0)
        )
        connection.execute(
            "INSERT INTO work_bubblewrap_process_identities "
            "(attempt_id,generation,protocol_version,identity_json,identity_sha256,created_at) "
            "VALUES ('a',1,1,?,?,?)",
            (module._json(identity), identity["identity_sha256"], 1.0),
        )
        connection.execute(
            "INSERT INTO work_bubblewrap_setup_intents "
            "(attempt_id,generation,schema_version,contract_hash,command_token,"
            "executable_sha256,ack_json,ack_sha256,process_identity_sha256,release_intent,created_at) "
            "VALUES ('a',1,1,?,'/bin/alpha',?,'{}',?,?,'pending',1.0)",
            ("b" * 64, digest, "c" * 64, identity["identity_sha256"]),
        )
    with store.connection() as connection:
        old_objects = module._schema_objects(connection)
        old_setup = tuple(
            connection.execute("SELECT * FROM work_bubblewrap_setup_intents").fetchone()
        )

    store.initialize()
    store.initialize()
    store.verify_schema()
    with store.connection() as connection:
        objects = module._schema_objects(connection)
        assert module.SCHEMA_VERSION >= 32
        assert set(old_objects.items()).issubset(set(objects.items()))
        assert (
            tuple(connection.execute("SELECT * FROM work_bubblewrap_setup_intents").fetchone())
            == old_setup
        )
        assert (
            connection.execute("SELECT count(*) FROM work_bubblewrap_release_claims").fetchone()[0]
            == 0
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert [
            row[0]
            for row in connection.execute("SELECT version FROM work_migrations ORDER BY version")
        ] == list(range(1, module.SCHEMA_VERSION + 1))

        claim_insert = (
            "INSERT INTO work_bubblewrap_release_claims "
            "(attempt_id,generation,contract_hash,command_token,executable_sha256,"
            "ack_sha256,process_identity_sha256,claimed_at) VALUES (?,?,?,?,?,?,?,?)"
        )
        claim_values = (
            "a",
            1,
            "b" * 64,
            "/bin/alpha",
            digest,
            "c" * 64,
            identity["identity_sha256"],
            2.0,
        )
        connection.execute(claim_insert, claim_values)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(claim_insert, claim_values)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE work_bubblewrap_release_claims SET claimed_at=3.0")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM work_bubblewrap_release_claims")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(claim_insert, ("missing", *claim_values[1:]))


def test_v32_to_v33_adds_immutable_one_shot_proxy_issue_without_changing_attempt(work_store_paths):
    from test.clients.test_work_bubblewrap_process_identity import _sent_attempt

    module = repository_module()
    store = verified_store_at_version(module, work_store_paths.database, 32)
    _sent_attempt(store)
    with store.connection() as connection:
        old_objects = module._schema_objects(connection)
        old_attempt = tuple(connection.execute("SELECT * FROM work_attempts WHERE id='a'").fetchone())

    store.initialize()
    store.initialize()
    store.verify_schema()
    with store.connection() as connection:
        objects = module._schema_objects(connection)
        assert set(old_objects.items()).issubset(set(objects.items()))
        assert tuple(connection.execute("SELECT * FROM work_attempts WHERE id='a'").fetchone()) == old_attempt
        assert connection.execute("SELECT count(*) FROM work_mcp_proxy_issues").fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert [row[0] for row in connection.execute(
            "SELECT version FROM work_migrations ORDER BY version"
        )] == list(range(1, module.SCHEMA_VERSION + 1))
        with pytest.raises(module.SchemaMismatch):
            module.WorkRepository._verify(connection, version=32)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO work_mcp_proxy_issues "
                "(attempt_id,generation,attempt_revision,contract_hash,expires_at,issued_at) "
                "VALUES ('a',1,1,?,10.0,1.0)", ("a" * 64,)
            )


def test_v33_to_v34_adds_append_only_mcp_effect_journal(work_store_paths):
    from test.clients.test_work_bubblewrap_process_identity import _sent_attempt

    module = repository_module()
    store = verified_store_at_version(module, work_store_paths.database, 33)
    _sent_attempt(store)
    with store.connection() as connection:
        old_objects = module._schema_objects(connection)
        old_attempt = tuple(connection.execute("SELECT * FROM work_attempts WHERE id='a'").fetchone())

    store.initialize()
    store.initialize()
    with store.connection() as connection:
        objects = module._schema_objects(connection)
        assert set(old_objects.items()).issubset(set(objects.items()))
        assert tuple(connection.execute("SELECT * FROM work_attempts WHERE id='a'").fetchone()) == old_attempt
        assert connection.execute("SELECT count(*) FROM work_mcp_proxy_effects").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM work_mcp_proxy_effect_events").fetchone()[0] == 0
        assert module._EXPECTED_SCHEMAS[module.SCHEMA_VERSION] == objects
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(module.SchemaMismatch):
            module.WorkRepository._verify(connection, version=33)
