"""The app lifespan composes durable launch admission from a server registry."""

import asyncio
import hashlib
import sqlite3
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.api import main
from cli_agent_orchestrator.backends.herdr_backend import HerdrBackend
from cli_agent_orchestrator.backends import work_registry
from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
from cli_agent_orchestrator.clients.work_repository import SchemaMismatch, WorkRepository
from cli_agent_orchestrator.models.work_contract import (
    ContractPermissions,
    ContractResources,
    ContractSnapshot,
    EffectiveWorkContractV2,
    ExecutableIdentity,
    ObservedValue,
)
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    ResolvedSnapshot,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services import (
    agui_enablement,
    herdr_inbox_registry,
    work_launch_gateway,
)
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning
from cli_agent_orchestrator.services.work_scheduler import WorkScheduler


_UNAVAILABLE = {
    "detail": {
        "code": "launch_runtime_unavailable",
        "message": "Trusted launch runtime is unavailable.",
        "retryable": False,
        "required_action": "inspect_server_configuration",
    }
}


class CapableBackend(TmuxBackend):
    """Admission can preflight this port; any terminal effect is a test failure."""

    def __init__(self):
        self.preflights = []
        self.effects = []

    def preflight_work(self, restriction):
        self.preflights.append(restriction)

    def execute_bound_process(self, *args, **kwargs):
        self.effects.append((args, kwargs))
        raise AssertionError("admission must not execute the worker")

    def create_session(self, *args, **kwargs):
        self.effects.append((args, kwargs))
        raise AssertionError("admission must not create a terminal")


def _durable_counts(repository):
    with repository.connection() as connection:
        return tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "work_items",
                "work_attempts",
                "work_dispatch_bindings",
                "work_delivery_orders",
                "work_scheduler_requests",
            )
        )


def _provisioned_launch(repository, root, *, backend):
    """Create only the private authority evidence needed to exercise queued admission."""
    WorkScheduler(repository).configure(
        capacity=2, max_queue=100, aging_seconds=10, expected_policy_revision=0
    )
    principal = auth._verified_principal(
        "https://issuer.test", "composition-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    job = repository.create_job(
        project_id="composition-project",
        principal_id=principal.id,
        allowed_providers=["mock_cli"],
        grant_id="composition-root",
        budget={"scheduler_units": 100},
    )
    authority = WorkAuthority(repository)
    grant = authority.issue_root(
        principal,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"knowledge.read", "tool.read"},
            paths={str(root)},
            commands={"/bin/alpha"},
        ),
        expires_at=time.time() + 600,
    )
    snapshot = DelegationSnapshots(
        repository, policy=KnowledgePolicy(repository, job["id"], grant.id, 1)
    ).freeze(
        principal=principal,
        job_id=job["id"],
        contract_id="composition-contract",
        binding_key="composition-context",
        request_hash=hashlib.sha256(b"composition context").hexdigest(),
        scope="project",
        scope_id=job["project_id"],
        resolver=lambda connection, actor: ResolvedSnapshot("composition snapshot"),
    )
    contract = EffectiveWorkContractV2(
        id="composition-contract",
        operation_kind="launch",
        provider="mock_cli",
        backend=backend,
        permissions=ContractPermissions(
            tools=("tool.read",), paths=(str(root),), commands=("/bin/alpha",)
        ),
        resources=ContractResources(
            checkout_root=str(root), write_paths=(str(root / "work"),), units=1
        ),
        snapshot=ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
        model=ObservedValue(status="known", value="mock-model", provenance="launch_config"),
        executable_identities=(
            ExecutableIdentity(
                command_token="/bin/alpha",
                content_reference="sha256:" + "a" * 64,
                sha256_digest="a" * 64,
                elf_machine="x86_64",
                elf_class="ELF64",
                endianness="little",
            ),
        ),
    )
    WorkProvisioning(repository).provision_launch(
        principal,
        subject=principal,
        selector="opaque",
        expected_revision=0,
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=grant.revision,
        contract=contract,
        adapter_version=2,
        lease_seconds=300,
    )
    return principal


def _install_lifespan_sandbox(monkeypatch):
    """Keep composition tests inside one temporary SQLite store and no providers."""
    calls = []

    async def never_returns(*args):
        await asyncio.Future()

    async def no_op(*args):
        return None

    monkeypatch.setattr(main, "setup_logging", lambda: None)
    monkeypatch.setattr(main, "install_access_log_redaction", lambda: None)
    monkeypatch.setattr(main, "init_telemetry", lambda *_: None)
    monkeypatch.setattr(main, "shutdown_telemetry", lambda: None)
    monkeypatch.setattr(main, "_seed_default_skills_at_startup", lambda: None)
    monkeypatch.setattr(main, "_reconcile_memory_at_startup", lambda: None)
    monkeypatch.setattr(main, "cleanup_old_data", lambda: None)
    monkeypatch.setattr(main, "cleanup_expired_memories", no_op)
    monkeypatch.setattr(main, "_sweep_workflow_runs_at_startup", lambda: None)
    monkeypatch.setattr(main, "flow_daemon", never_returns)
    monkeypatch.setattr(main, "opencode_inbox_delivery_daemon", never_returns)
    monkeypatch.setattr(main, "inbox_reconciliation_daemon", never_returns)
    monkeypatch.setattr(main.status_monitor, "run", never_returns)
    monkeypatch.setattr(main.log_writer, "run", never_returns)
    monkeypatch.setattr(main.inbox_service, "run", never_returns)
    monkeypatch.setattr(main.PluginRegistry, "load", no_op)
    monkeypatch.setattr(main.PluginRegistry, "teardown", no_op)
    monkeypatch.setattr(main.fifo_manager, "stop_watchdog", lambda: None)
    monkeypatch.setattr(main.bus, "set_loop", lambda _: None)
    monkeypatch.setattr(main, "get_backend", lambda: calls.append("get_backend") or TmuxBackend())
    return calls


class _StartupTaskTracker:
    """Record lifespan task sources and gather groups using real asyncio tasks."""

    def __init__(self):
        self.tasks = []
        self.created = []
        self.awaited_groups = []

    def __getattr__(self, name):
        return getattr(asyncio, name)

    def create_task(self, coroutine, *args, **kwargs):
        task = asyncio.create_task(coroutine, *args, **kwargs)
        self.created.append((coroutine, task))
        self.tasks.append(task)
        return task

    def gather(self, *awaitables, **kwargs):
        task_group = tuple(awaitables)
        if any(task in self.tasks for task in task_group):
            self.awaited_groups.append(task_group)
        return asyncio.gather(*awaitables, **kwargs)

    def to_thread(self, function, *args, **kwargs):
        return asyncio.to_thread(function, *args, **kwargs)


@pytest.fixture
def composed_server(tmp_path, monkeypatch):
    """A server whose lifespan sees one already-verified temporary Work store."""
    repository = WorkRepository(tmp_path / "composition.sqlite3")
    repository.initialize()
    calls = _install_lifespan_sandbox(monkeypatch)
    monkeypatch.setattr(constants, "DATABASE_FILE", repository.path)
    monkeypatch.setattr(main, "init_db", lambda: calls.append("init_db"))
    previous_gateway = getattr(main.app.state, "durable_launch_gateway", None)
    if hasattr(main.app.state, "durable_launch_gateway"):
        del main.app.state.durable_launch_gateway
    yield SimpleNamespace(repository=repository, root=tmp_path, calls=calls)
    if hasattr(main.app.state, "durable_launch_gateway"):
        del main.app.state.durable_launch_gateway
    if previous_gateway is not None:
        main.app.state.durable_launch_gateway = previous_gateway


@contextmanager
def _client_for(principal):
    main.app.dependency_overrides[main.get_work_launch_principal] = lambda: principal
    try:
        with TestClient(main.app, base_url="http://localhost") as client:
            yield client
    finally:
        main.app.dependency_overrides.pop(main.get_work_launch_principal, None)


def _launch_body():
    return {
        "selection": "opaque",
        "agent_profile": "developer",
        "session_name": "composition-session",
        "message": "admit only",
        "allowed_tools": ["tool.read"],
    }


def test_server_owned_work_registry_defaults_to_empty():
    """Breaks if the server enables a work backend implicitly by default."""
    assert work_registry.WORK_BACKENDS == {}


@pytest.mark.parametrize("registry", [{}, {"other": CapableBackend()}])
def test_lifespan_preserves_fixed_503_without_a_registered_exact_backend(
    composed_server, monkeypatch, registry
):
    """Breaks if an unregistered contract backend falls back to tmux or another key."""
    principal = _provisioned_launch(composed_server.repository, composed_server.root, backend="exact")
    monkeypatch.setattr(work_registry, "WORK_BACKENDS", registry)

    with _client_for(principal) as client:
        response = client.post("/work-launches", json=_launch_body())

    assert composed_server.calls == ["init_db", "get_backend"]
    assert response.status_code == 503
    assert response.json() == _UNAVAILABLE
    assert _durable_counts(composed_server.repository) == (0, 0, 0, 0, 0)
    assert all(backend.effects == [] for backend in registry.values())


def test_lifespan_rejects_an_incapable_exact_backend_without_effects(composed_server, monkeypatch):
    """Breaks if registration alone bypasses the effective contract's preflight."""
    principal = _provisioned_launch(composed_server.repository, composed_server.root, backend="exact")
    monkeypatch.setattr(work_registry, "WORK_BACKENDS", {"exact": TmuxBackend()})

    with _client_for(principal) as client:
        response = client.post("/work-launches", json=_launch_body())

    assert response.status_code == 503
    assert response.json() == _UNAVAILABLE
    assert _durable_counts(composed_server.repository) == (0, 0, 0, 0, 0)


def test_lifespan_admits_queued_work_only_through_the_exact_registered_backend(
    composed_server, monkeypatch
):
    """Breaks if the lifespan omits gateway composition or admission dispatches a terminal."""
    backend = CapableBackend()
    principal = _provisioned_launch(composed_server.repository, composed_server.root, backend="exact")
    monkeypatch.setattr(work_registry, "WORK_BACKENDS", {"exact": backend})

    with _client_for(principal) as client:
        response = client.post("/work-launches", json=_launch_body())

    assert composed_server.calls == ["init_db", "get_backend"]
    assert response.status_code == 202, response.json()
    assert response.json()["state"] == "queued"
    assert _durable_counts(composed_server.repository) == (1, 1, 1, 1, 1)
    assert len(backend.preflights) == 1
    assert backend.effects == []


def test_lifespan_composes_omitted_selection_through_durable_work_only(
    composed_server, monkeypatch
):
    """An omitted selector uses the composed queue path without legacy sessions."""
    backend = CapableBackend()
    principal = _provisioned_launch(composed_server.repository, composed_server.root, backend="exact")
    monkeypatch.setattr(work_registry, "WORK_BACKENDS", {"exact": backend})
    legacy_session_calls = []

    async def record_legacy_session_call(**kwargs):
        legacy_session_calls.append(kwargs)
        raise AssertionError("queue-only work launch must not create a legacy session")

    monkeypatch.setattr(main.session_service, "create_session", record_legacy_session_call)
    body = _launch_body()
    body.pop("selection")

    with _client_for(principal) as client:
        response = client.post("/work-launches", json=body)

    assert composed_server.calls == ["init_db", "get_backend"]
    assert response.status_code == 202, response.json()
    assert response.json()["state"] == "queued"
    assert _durable_counts(composed_server.repository) == (1, 1, 1, 1, 1)
    assert len(backend.preflights) == 1
    assert backend.effects == []
    assert legacy_session_calls == []


def test_lifespan_refuses_to_yield_when_the_work_schema_is_corrupt(tmp_path, monkeypatch):
    """Breaks if a malformed Work store can serve before startup validation."""
    database_path = tmp_path / "corrupt.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE work_items (id INTEGER PRIMARY KEY)")
    calls = _install_lifespan_sandbox(monkeypatch)
    monkeypatch.setattr(constants, "DATABASE_FILE", database_path)
    monkeypatch.setattr(work_registry, "WORK_BACKENDS", {})

    def initialize_corrupt_work_store():
        calls.append("init_db")
        WorkRepository(database_path).initialize()

    monkeypatch.setattr(main, "init_db", initialize_corrupt_work_store)

    with pytest.raises(SchemaMismatch, match="work migration ledger is missing or modified"):
        with TestClient(main.app, base_url="http://localhost"):
            pass

    assert calls == ["init_db"]


def test_lifespan_gateway_factory_rejects_schema_corruption_after_init_db(
    composed_server, monkeypatch
):
    """Breaks if gateway composition skips its own repository schema verification."""
    real_builder = work_launch_gateway.build_durable_launch_gateway

    def corrupt_schema_then_build(repository, *, backends):
        composed_server.calls.append("build_gateway")
        with sqlite3.connect(repository.path) as connection:
            connection.execute("DROP TABLE work_migrations")
        return real_builder(repository, backends=backends)

    monkeypatch.setattr(
        work_launch_gateway, "build_durable_launch_gateway", corrupt_schema_then_build
    )

    with pytest.raises(SchemaMismatch, match="work schema is incomplete, incompatible or modified"):
        with TestClient(main.app, base_url="http://localhost"):
            pytest.fail("lifespan must not yield after gateway schema verification fails")

    assert composed_server.calls == ["init_db", "get_backend", "build_gateway"]


@pytest.mark.asyncio
async def test_lifespan_cleans_up_when_plugin_registry_load_is_cancelled(
    composed_server, monkeypatch
):
    """A cancelled plugin load still releases the resources initialized before it."""
    hooks = []
    load_entered = asyncio.Event()

    async def wait_for_cancellation(*args):
        load_entered.set()
        await asyncio.Future()

    async def record_registry_teardown(*args):
        hooks.append("registry_teardown")

    monkeypatch.setattr(main.PluginRegistry, "load", wait_for_cancellation)
    monkeypatch.setattr(main.PluginRegistry, "teardown", record_registry_teardown)
    monkeypatch.setattr(
        main.fifo_manager, "stop_watchdog", lambda: hooks.append("watchdog_stop")
    )
    monkeypatch.setattr(
        main, "shutdown_telemetry", lambda: hooks.append("telemetry_shutdown")
    )

    previous_service = herdr_inbox_registry.get_herdr_inbox_service()
    unrelated_service = object()
    herdr_inbox_registry.set_herdr_inbox_service(unrelated_service)
    lifespan_manager = main.lifespan(main.app)
    enter_task = asyncio.create_task(lifespan_manager.__aenter__())
    try:
        await asyncio.wait_for(load_entered.wait(), timeout=5)
        enter_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await enter_task

        assert composed_server.calls == ["init_db"]
        assert hooks == ["watchdog_stop", "registry_teardown", "telemetry_shutdown"]
        assert herdr_inbox_registry.get_herdr_inbox_service() is unrelated_service
    finally:
        if not enter_task.done():
            enter_task.cancel()
            await asyncio.gather(enter_task, return_exceptions=True)
        herdr_inbox_registry.set_herdr_inbox_service(previous_service)


@pytest.mark.asyncio
async def test_lifespan_failure_after_herdr_registration_clears_service_and_awaits_task(
    composed_server, monkeypatch
):
    """Post-registration startup failure cancels the Herdr task and clears its singleton."""
    class FakeHerdrBackend(HerdrBackend):
        def __init__(self):
            self._herdr_session = "temporary-test-session"

    class FakeHerdrInboxService:
        def __init__(self, *, herdr_session, delivery_callback):
            self.herdr_session = herdr_session
            self.delivery_callback = delivery_callback
            self.start_coroutine = None

        def start(self):
            async def wait_forever():
                await asyncio.Future()

            self.start_coroutine = wait_forever()
            return self.start_coroutine

    previous_service = herdr_inbox_registry.get_herdr_inbox_service()
    if previous_service is not None:
        herdr_inbox_registry.set_herdr_inbox_service(None)
    startup_error = RuntimeError("post-registration startup failure")
    task_tracker = _StartupTaskTracker()
    monkeypatch.setattr(main, "asyncio", task_tracker)
    backend = FakeHerdrBackend()
    service_instances = []

    def make_service(**kwargs):
        service = FakeHerdrInboxService(**kwargs)
        service_instances.append(service)
        return service

    original_info = main.logger.info

    def fail_after_herdr_registration(message, *args, **kwargs):
        if message == "Herdr inbox service started":
            composed_server.calls.append("herdr_registered")
            raise startup_error
        return original_info(message, *args, **kwargs)

    monkeypatch.setattr(
        main,
        "get_backend",
        lambda: composed_server.calls.append("get_backend") or backend,
    )
    monkeypatch.setattr(main, "HerdrInboxService", make_service)
    monkeypatch.setattr(agui_enablement, "agui_surface_enabled", lambda: False)
    monkeypatch.setattr(main.logger, "info", fail_after_herdr_registration)

    try:
        with pytest.raises(RuntimeError, match="post-registration startup failure") as raised:
            async with main.lifespan(main.app):
                pytest.fail("lifespan must not yield after the post-registration failure")

        assert raised.value is startup_error
        assert composed_server.calls == ["init_db", "get_backend", "herdr_registered"]
        assert len(service_instances) == 1
        herdr_coroutine = service_instances[0].start_coroutine
        herdr_task = next(
            task for source, task in task_tracker.created if source is herdr_coroutine
        )
        assert herdr_task.done() and herdr_task.cancelled()
        assert any(
            herdr_task in awaited_group for awaited_group in task_tracker.awaited_groups
        )
        assert all(task.done() and task.cancelled() for task in task_tracker.tasks)
        assert herdr_inbox_registry.get_herdr_inbox_service() is None
    finally:
        pending_tasks = [task for task in task_tracker.tasks if not task.done()]
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        herdr_inbox_registry.set_herdr_inbox_service(None)
        if previous_service is not None:
            herdr_inbox_registry.set_herdr_inbox_service(previous_service)


@pytest.mark.asyncio
async def test_lifespan_cleans_started_resources_when_gateway_composition_fails(
    composed_server, monkeypatch
):
    """Breaks if post-spawn startup failure skips cancellation and teardown."""
    cleanup_hooks = []
    started_tasks = []
    startup_error = RuntimeError("gateway composition failed")

    def wait_until_cancelled(name):
        async def wait(*args):
            started_tasks.append(name)
            await asyncio.Future()

        return wait

    class TaskTracker:
        def __init__(self):
            self.tasks = []
            self.awaited_groups = []

        def __getattr__(self, name):
            return getattr(asyncio, name)

        def create_task(self, coroutine, *args, **kwargs):
            task = asyncio.create_task(coroutine, *args, **kwargs)
            self.tasks.append(task)
            return task

        def gather(self, *awaitables, **kwargs):
            task_group = tuple(awaitables)
            if any(task in self.tasks for task in task_group):
                self.awaited_groups.append(task_group)
            return asyncio.gather(*awaitables, **kwargs)

        def to_thread(self, function, *args, **kwargs):
            return asyncio.to_thread(function, *args, **kwargs)

    task_tracker = TaskTracker()
    monkeypatch.setattr(main, "asyncio", task_tracker)

    async def record_registry_teardown(*args):
        cleanup_hooks.append("registry_teardown")

    def fail_gateway_builder(repository, *, backends):
        assert repository.path == composed_server.repository.path
        assert backends is work_registry.WORK_BACKENDS
        composed_server.calls.append("build_gateway")
        raise startup_error

    monkeypatch.setattr(main.PluginRegistry, "teardown", record_registry_teardown)
    monkeypatch.setattr(
        main, "cleanup_expired_memories", wait_until_cancelled("memory_cleanup")
    )
    monkeypatch.setattr(main, "flow_daemon", wait_until_cancelled("flow_daemon"))
    monkeypatch.setattr(
        main, "opencode_inbox_delivery_daemon", wait_until_cancelled("opencode_inbox")
    )
    monkeypatch.setattr(
        main, "inbox_reconciliation_daemon", wait_until_cancelled("inbox_reconcile")
    )
    monkeypatch.setattr(
        main.status_monitor, "run", wait_until_cancelled("status_monitor")
    )
    monkeypatch.setattr(main.log_writer, "run", wait_until_cancelled("log_writer"))
    monkeypatch.setattr(
        main.inbox_service, "run", wait_until_cancelled("inbox_service")
    )
    monkeypatch.setattr(agui_enablement, "agui_surface_enabled", lambda: False)
    monkeypatch.setattr(
        main.fifo_manager,
        "stop_watchdog",
        lambda: cleanup_hooks.append("watchdog_stop"),
    )
    monkeypatch.setattr(
        main,
        "shutdown_telemetry",
        lambda: cleanup_hooks.append("telemetry_shutdown"),
    )
    monkeypatch.setattr(
        work_launch_gateway,
        "build_durable_launch_gateway",
        fail_gateway_builder,
    )

    try:
        with pytest.raises(RuntimeError, match="gateway composition failed") as raised:
            async with main.lifespan(main.app):
                pytest.fail("lifespan must not yield after gateway composition fails")

        assert raised.value is startup_error
        assert composed_server.calls == ["init_db", "get_backend", "build_gateway"]
        assert started_tasks == []
        assert cleanup_hooks == ["watchdog_stop", "registry_teardown", "telemetry_shutdown"]
        assert task_tracker.tasks
        assert all(task.done() and task.cancelled() for task in task_tracker.tasks)
        assert any(
            set(task_group) == set(task_tracker.tasks)
            for task_group in task_tracker.awaited_groups
        )
    finally:
        pending_tasks = [task for task in task_tracker.tasks if not task.done()]
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
