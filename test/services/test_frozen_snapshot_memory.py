"""Security contract for explicit frozen-snapshot memory references."""

import time

import pytest

from cli_agent_orchestrator.clients.work_dispatch_schema import DISPATCH_SCHEMA
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.clients.work_snapshot_schema import SNAPSHOT_SCHEMA
from cli_agent_orchestrator.models.work_contract import (
    ContractPermissions,
    ContractResources,
    ContractSnapshot,
    EffectiveWorkContract,
)
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services import frozen_run_memory, settings_service, terminal_service
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    ResolvedSnapshot,
    SnapshotUnavailable,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgeAccessDenied, KnowledgePolicy
from cli_agent_orchestrator.services.terminal_service import inject_memory_context
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_contract import WorkContracts


class ExactReader:
    """Strict test port: it accepts only the selected opaque reference."""

    def __init__(self, snapshot_id, content):
        self.snapshot_id = snapshot_id
        self.content = content
        self.calls = 0

    def read_authorized(self, snapshot_id):
        assert snapshot_id == self.snapshot_id
        self.calls += 1
        return self.content


class NeverReader:
    """A strict port used to prove invalid references are rejected before reads."""

    def __init__(self):
        self.calls = 0

    def read_authorized(self, snapshot_id):
        self.calls += 1
        pytest.fail(f"invalid reference reached the reader: {snapshot_id}")


class ResultReader:
    """Strict reader with one configured return value for type-contract tests."""

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def read_authorized(self, _snapshot_id):
        self.calls += 1
        return self.result


def _resolve(legacy_memory, *, snapshot_id, snapshot_reader):
    """Exercise the future adapter while making its absence fail semantically."""
    adapter = getattr(frozen_run_memory, "resolve_frozen_memory", None)
    if adapter is None:
        return legacy_memory
    return adapter(
        legacy_memory,
        snapshot_id=snapshot_id,
        snapshot_reader=snapshot_reader,
    )


def test_explicit_authorized_snapshot_replaces_legacy_content_exactly():
    """Removing the explicit-reference arm must expose legacy bytes, not pass silently."""
    reader = ExactReader("snapshot-001", "<cao-memory>FROZEN</cao-memory>")

    resolved = _resolve(
        "<cao-memory>LEGACY</cao-memory>",
        snapshot_id="snapshot-001",
        snapshot_reader=reader,
    )

    assert resolved == "<cao-memory>FROZEN</cao-memory>"
    assert reader.calls == 1


@pytest.mark.parametrize("legacy_memory", [None, "", "<cao-memory>LEGACY</cao-memory>"])
def test_absent_snapshot_reference_preserves_legacy_memory_without_reading(legacy_memory):
    reader = NeverReader()

    assert _resolve(legacy_memory, snapshot_id=None, snapshot_reader=reader) == legacy_memory
    assert reader.calls == 0


def test_empty_snapshot_reference_fails_before_reader_or_legacy_fallback():
    reader = NeverReader()

    with pytest.raises(frozen_run_memory.FrozenSnapshotMemoryUnavailable):
        _resolve("<cao-memory>LEGACY</cao-memory>", snapshot_id="", snapshot_reader=reader)

    assert reader.calls == 0


def test_legacy_frozen_memory_api_does_not_read_a_port_without_a_reference():
    reader = NeverReader()

    assert (
        frozen_run_memory.frozen_memory_for(
            None,
            "terminal-legacy",
            "task",
            snapshot_id=None,
            snapshot_reader=reader,
        )
        is None
    )
    assert reader.calls == 0


def test_authorized_empty_snapshot_reaches_terminal_without_live_fallback(tmp_path, monkeypatch):
    _, _, _, _, _, snapshot, _, contracts, binding = _bound_snapshot(tmp_path, content="")
    reader_type = getattr(frozen_run_memory, "WorkOrderSnapshotReader", None)
    assert (
        reader_type is not None
    ), "explicit references need a reader bound to a durable work order"
    reader = reader_type(contracts, binding)
    monkeypatch.setattr(settings_service, "is_memory_enabled", lambda: True)
    monkeypatch.setattr(
        terminal_service,
        "MemoryService",
        lambda: pytest.fail("authorized empty content must not query live memory"),
    )
    with terminal_service._memory_injected_lock:
        terminal_service._memory_injected_terminals.clear()
    try:
        frozen_memory = frozen_run_memory.frozen_memory_for(
            None,
            "terminal-empty",
            "task",
            snapshot_id=snapshot.id,
            snapshot_reader=reader,
        )
        assert frozen_memory == ""
        assert (
            inject_memory_context("task", "terminal-empty", frozen_memory=frozen_memory) == "task"
        )
    finally:
        with terminal_service._memory_injected_lock:
            terminal_service._memory_injected_terminals.clear()


@pytest.mark.parametrize("reader", [None, object(), ResultReader(None), ResultReader(1)])
def test_explicit_reference_rejects_missing_or_invalid_reader_result_without_legacy_fallback(
    reader,
):
    with pytest.raises(frozen_run_memory.FrozenSnapshotMemoryUnavailable):
        frozen_run_memory.frozen_memory_for(
            None,
            "terminal-reader",
            "task",
            snapshot_id="snapshot-001",
            snapshot_reader=reader,
        )


def test_terminal_treats_a_snapshot_shaped_string_as_content_not_a_reference(monkeypatch):
    monkeypatch.setattr(settings_service, "is_memory_enabled", lambda: True)
    monkeypatch.setattr(
        terminal_service,
        "MemoryService",
        lambda: pytest.fail("supplied content must not query live memory"),
    )
    with terminal_service._memory_injected_lock:
        terminal_service._memory_injected_terminals.clear()
    try:
        assert inject_memory_context("task", "terminal-literal", frozen_memory="snapshot-001") == (
            "snapshot-001\n\ntask"
        )
    finally:
        with terminal_service._memory_injected_lock:
            terminal_service._memory_injected_terminals.clear()


def _bound_snapshot(tmp_path, *, content="<cao-memory>FROZEN</cao-memory>"):
    """Create a real policy, durable order and snapshot binding in temporary SQLite."""
    repository = WorkRepository(tmp_path / "snapshot-adapter.sqlite3")
    repository.initialize()
    principal = auth._verified_principal(
        "https://issuer.test", "owner-a", [auth.SCOPE_ADMIN], "jwt"
    )
    job = repository.create_job(
        project_id="project-a",
        principal_id=principal.id,
        allowed_providers=["mock_cli"],
        grant_id="root-a",
    )
    authority = WorkAuthority(repository)
    grant = authority.issue_root(
        principal,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read", "tool.read"}, paths={str(tmp_path)}),
        expires_at=time.time() + 600,
    )
    snapshots = DelegationSnapshots(
        repository, policy=KnowledgePolicy(repository, job["id"], grant.id, grant.revision)
    )
    snapshot = snapshots.freeze(
        principal=principal,
        job_id=job["id"],
        contract_id="contract-a",
        binding_key="destination-a",
        request_hash="a" * 64,
        scope="project",
        scope_id="project-a",
        resolver=lambda _connection, _principal: ResolvedSnapshot(content),
    )
    destination = tmp_path / "destination-a"
    destination.mkdir()
    item = repository.admit_work(
        job_id=job["id"],
        operation_kind="launch",
        idempotency_key="work-a",
        request_hash="b" * 64,
        contract_id="contract-a",
        snapshot_id=snapshot.id,
        provider="mock_cli",
        actor_id=principal.id,
    )
    contract = EffectiveWorkContract(
        id="contract-a",
        operation_kind="launch",
        provider="mock_cli",
        backend="test-backend",
        permissions=ContractPermissions(tools=("tool.read",), paths=(str(tmp_path),)),
        resources=ContractResources(
            checkout_root=str(tmp_path), write_paths=(str(destination),), units=1
        ),
        snapshot=ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
    )
    contracts = WorkContracts(repository)
    binding = contracts.bind(
        principal=principal,
        attempt_id=item["attempts"][0]["id"],
        generation=1,
        expected_attempt_revision=1,
        grant_id=grant.id,
        expected_grant_revision=grant.revision,
        contract=contract,
    )
    return repository, principal, job, grant, snapshots, snapshot, authority, contracts, binding


def _trigger(schema, name):
    return next(
        statement for statement in schema if statement.startswith(f"CREATE TRIGGER {name} ")
    )


def test_real_order_bound_reader_delivers_only_its_authorized_snapshot(tmp_path):
    _, _, _, _, _, snapshot, _, contracts, binding = _bound_snapshot(tmp_path)
    reader_type = getattr(frozen_run_memory, "WorkOrderSnapshotReader", None)

    assert (
        reader_type is not None
    ), "explicit references need a reader bound to a durable work order"
    reader = reader_type(contracts, binding)

    assert _resolve(
        "<cao-memory>LEGACY</cao-memory>", snapshot_id=snapshot.id, snapshot_reader=reader
    ) == ("<cao-memory>FROZEN</cao-memory>")


def test_bound_reader_refuses_foreign_job_contract_or_destination_snapshot(tmp_path):
    repository, principal, job, grant, snapshots, _, _, contracts, binding = _bound_snapshot(
        tmp_path
    )
    reader_type = getattr(frozen_run_memory, "WorkOrderSnapshotReader", None)
    assert (
        reader_type is not None
    ), "explicit references need a reader bound to a durable work order"
    reader = reader_type(contracts, binding)

    foreign_principal = auth._verified_principal(
        "https://issuer.test", "owner-b", [auth.SCOPE_ADMIN], "jwt"
    )
    foreign_job = repository.create_job(
        project_id="project-b",
        principal_id=foreign_principal.id,
        allowed_providers=["mock_cli"],
        grant_id="root-b",
    )
    foreign_grant = WorkAuthority(repository).issue_root(
        foreign_principal,
        job_id=foreign_job["id"],
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read"}),
        expires_at=time.time() + 600,
    )
    foreign = DelegationSnapshots(
        repository,
        policy=KnowledgePolicy(
            repository, foreign_job["id"], foreign_grant.id, foreign_grant.revision
        ),
    ).freeze(
        principal=foreign_principal,
        job_id=foreign_job["id"],
        contract_id="contract-b",
        binding_key="destination-b",
        request_hash="c" * 64,
        scope="project",
        scope_id="project-b",
        resolver=lambda _connection, _principal: ResolvedSnapshot("FOREIGN JOB SECRET"),
    )
    other_destination = snapshots.freeze(
        principal=principal,
        job_id=job["id"],
        contract_id="contract-a",
        binding_key="destination-other",
        request_hash="d" * 64,
        scope="project",
        scope_id="project-a",
        resolver=lambda _connection, _principal: ResolvedSnapshot("OTHER DESTINATION SECRET"),
    )

    with pytest.raises(KnowledgeAccessDenied):
        DelegationSnapshots(
            repository, policy=KnowledgePolicy(repository, job["id"], grant.id, grant.revision)
        ).read(principal, foreign.id)

    for foreign_id, secret in (
        (foreign.id, "FOREIGN JOB SECRET"),
        (other_destination.id, "OTHER DESTINATION SECRET"),
    ):
        with pytest.raises(frozen_run_memory.FrozenSnapshotMemoryUnavailable) as raised:
            _resolve(
                "<cao-memory>LEGACY</cao-memory>", snapshot_id=foreign_id, snapshot_reader=reader
            )
        assert secret not in str(raised.value)


def test_missing_corrupt_or_revoked_snapshot_fails_closed_without_legacy_fallback(tmp_path):
    repository, principal, job, grant, _, snapshot, authority, contracts, binding = _bound_snapshot(
        tmp_path
    )
    reader_type = getattr(frozen_run_memory, "WorkOrderSnapshotReader", None)
    assert (
        reader_type is not None
    ), "explicit references need a reader bound to a durable work order"
    reader = reader_type(contracts, binding)

    with repository.transaction() as connection:
        connection.execute("DROP TRIGGER work_delegation_snapshots_immutable_update")
        connection.execute(
            "UPDATE work_delegation_snapshots SET content=? WHERE id=?",
            (b"CORRUPT SECRET", snapshot.id),
        )
        connection.execute(
            "CREATE TRIGGER work_delegation_snapshots_immutable_update BEFORE UPDATE ON "
            "work_delegation_snapshots BEGIN SELECT RAISE(ABORT,'delegation snapshot history is immutable'); END"
        )
    with pytest.raises(frozen_run_memory.FrozenSnapshotMemoryUnavailable) as raised:
        _resolve("<cao-memory>LEGACY</cao-memory>", snapshot_id=snapshot.id, snapshot_reader=reader)
    assert "CORRUPT SECRET" not in str(raised.value)

    missing_path = tmp_path / "missing"
    missing_path.mkdir()
    repository, principal, _, _, snapshots, snapshot, _, contracts, binding = _bound_snapshot(
        missing_path
    )
    reader = reader_type(contracts, binding)
    with repository.transaction() as connection:
        for name in (
            "work_snapshot_sources_immutable_delete",
            "work_dispatch_bindings_immutable_delete",
            "work_delegation_snapshots_immutable_delete",
        ):
            connection.execute(f"DROP TRIGGER {name}")
        connection.execute("DELETE FROM work_snapshot_sources WHERE snapshot_id=?", (snapshot.id,))
        connection.execute("DELETE FROM work_dispatch_bindings WHERE snapshot_id=?", (snapshot.id,))
        connection.execute("DELETE FROM work_delegation_snapshots WHERE id=?", (snapshot.id,))
        connection.execute(_trigger(SNAPSHOT_SCHEMA, "work_snapshot_sources_immutable_delete"))
        connection.execute(_trigger(DISPATCH_SCHEMA, "work_dispatch_bindings_immutable_delete"))
        connection.execute(_trigger(SNAPSHOT_SCHEMA, "work_delegation_snapshots_immutable_delete"))
    with pytest.raises(SnapshotUnavailable):
        snapshots.read(principal, snapshot.id)
    with pytest.raises(frozen_run_memory.FrozenSnapshotMemoryUnavailable):
        _resolve("<cao-memory>LEGACY</cao-memory>", snapshot_id=snapshot.id, snapshot_reader=reader)

    revoked_path = tmp_path / "revoked"
    revoked_path.mkdir()
    _, principal, _, grant, _, snapshot, authority, contracts, binding = _bound_snapshot(
        revoked_path
    )
    reader = reader_type(contracts, binding)
    authority.revoke(
        principal, grant_id=grant.id, expected_grant_revision=grant.revision, reason="test"
    )
    with pytest.raises(frozen_run_memory.FrozenSnapshotMemoryUnavailable):
        _resolve("<cao-memory>LEGACY</cao-memory>", snapshot_id=snapshot.id, snapshot_reader=reader)
