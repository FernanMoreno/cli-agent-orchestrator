"""Ordinary terminal creation under a durable launch order, without a real CLI."""

import importlib
import json
from test.integration.test_work_dispatch import AdmissionOnlyBackend, admit, context  # noqa: F401
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.backends.registry import get_backend
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.mock_cli import MockCliProvider
from cli_agent_orchestrator.services import settings_service, terminal_service
from cli_agent_orchestrator.services.work_admission import WorkAdmission
from cli_agent_orchestrator.services.work_service import DeliveryUncertain


class LaunchBackend(AdmissionOnlyBackend):
    def __init__(self):
        super().__init__()
        self.sessions = {}

    def session_exists(self, name):
        return name in self.sessions

    def create_session(self, name, window, terminal_id, directory, extra_env=None, work_safe=False):
        assert work_safe
        self.sessions[name] = (window, terminal_id, directory)
        return window

    def supports_event_inbox(self):
        return True

    def kill_session(self, name):
        return self.sessions.pop(name, None) is not None


@pytest.fixture
def launch_context(context, isolated_memory_db, monkeypatch):
    from cli_agent_orchestrator import constants

    monkeypatch.setattr(constants, "LOCK_DIR", context.repo.path.parent / "locks")
    module = importlib.import_module("cli_agent_orchestrator.services.work_launch")
    providers = {}

    class UnscopedBackend:
        def __getattr__(self, name):
            raise AssertionError("managed launch escaped its protected backend scope")

    monkeypatch.setattr("cli_agent_orchestrator.backends.registry._backend", UnscopedBackend())
    backend = LaunchBackend()
    context.backend = backend
    context.before_ready = lambda: None

    class Provider(MockCliProvider):
        async def initialize(self):
            context.before_ready()
            get_backend().send_keys(self.session_name, self.window_name, "mock_cli startup")
            self._initialized = True
            return True

    def create_provider(provider, terminal_id, session, window, profile, allowed_tools, **kwargs):
        instance = Provider(terminal_id, session, window, allowed_tools)
        providers[terminal_id] = instance
        return instance

    monkeypatch.setattr(terminal_service.provider_manager, "create_provider", create_provider)
    monkeypatch.setattr(terminal_service.provider_manager, "get_provider", providers.get)
    monkeypatch.setattr(terminal_service, "load_agent_profile", lambda name: None)
    monkeypatch.setattr(terminal_service, "get_herdr_inbox_service", lambda: None)
    monkeypatch.setattr(terminal_service, "get_max_terminals", lambda: None)
    monkeypatch.setattr(
        terminal_service,
        "status_monitor",
        SimpleNamespace(
            get_status=lambda terminal_id: TerminalStatus.IDLE,
            notify_input_sent=lambda *args, **kwargs: None,
            clear_rolling_buffer=lambda *args, **kwargs: None,
            clear_terminal=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(terminal_service, "_memory_injected_terminals", set())
    context.service = WorkAdmission(
        context.repo,
        backends={"test": backend},
        delivery_adapters={("launch", 1): module.launch_adapter()},
    )
    return context


def launch_envelope(key="1234abcd"):
    from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope

    return WorkDeliveryEnvelope(
        operation_kind="launch",
        adapter_version=1,
        payload_json=json.dumps(
            {
                "terminal_id": key,
                "agent_profile": "developer",
                "session_name": "cao-durable-launch",
                "message": "task text",
                "allowed_tools": [],
            }
        ),
    )


@pytest.mark.asyncio
async def test_required_launch_mode_rejects_ordinary_session_before_backend_effect(
    launch_context, monkeypatch
):
    monkeypatch.setenv("CAO_WORK_LAUNCH_MODE", "required")

    with pytest.raises(PermissionError, match="managed Work launch"):
        await terminal_service.create_terminal(
            provider="mock_cli",
            agent_profile="developer",
            session_name="legacy-session",
            new_session=True,
        )

    assert launch_context.backend.sessions == {}
    assert launch_context.backend.effects == []


@pytest.mark.asyncio
async def test_required_launch_mode_keeps_registered_launch_on_managed_path(
    launch_context, monkeypatch
):
    monkeypatch.setenv("CAO_WORK_LAUNCH_MODE", "required")
    work = admit(launch_context, "required-mode", delivery=launch_envelope())

    result = await launch_context.service.dispatch_registered_next()

    assert result["id"] == work["id"]
    assert result["attempts"][0]["state"] == "sent"
    assert len(launch_context.backend.sessions) == 1


@pytest.mark.asyncio
async def test_registered_launch_uses_real_terminal_service_and_frozen_context(launch_context):
    context = launch_context
    work = admit(context, "launch-adapter", delivery=launch_envelope())
    result = await context.service.dispatch_registered_next()
    assert result["id"] == work["id"]
    assert result["attempts"][0]["terminal_id"] == "1234abcd"
    assert database.get_terminal_metadata("1234abcd")["provider"] == "mock_cli"
    assert context.backend.effects[0][2] == "mock_cli startup"
    assert "frozen context launch-adapter" in context.backend.effects[1][2]
    assert "task text" in context.backend.effects[1][2]
    # A successful paste is not a provider receipt.
    assert result["attempts"][0]["state"] == "sent"
    assert await context.service.dispatch_registered_next() is None
    assert len(context.backend.sessions) == 1


@pytest.mark.asyncio
async def test_registered_dispatch_waits_for_terminal_dispatch_lock_before_binding(
    launch_context, monkeypatch
):
    import asyncio
    import threading

    from cli_agent_orchestrator import constants
    from cli_agent_orchestrator.services import work_admission
    from cli_agent_orchestrator.services.work_terminal import terminal_dispatch_lock

    context = launch_context
    terminal_id = "1234abcd"
    monkeypatch.setattr(constants, "LOCK_DIR", context.repo.path.parent / "locks")
    work = admit(context, "dispatch-lock", delivery=launch_envelope(terminal_id))

    holder_ready = threading.Event()
    release_holder = threading.Event()
    dispatch_lock_attempted = threading.Event()
    original_lock = work_admission.terminal_dispatch_lock

    def observe_dispatch_lock(database_path, target_terminal_id):
        if target_terminal_id == terminal_id:
            dispatch_lock_attempted.set()
        return original_lock(database_path, target_terminal_id)

    monkeypatch.setattr(work_admission, "terminal_dispatch_lock", observe_dispatch_lock)

    def hold_terminal_lock():
        with terminal_dispatch_lock(context.repo.path, terminal_id):
            holder_ready.set()
            release_holder.wait(timeout=10)

    holder = threading.Thread(target=hold_terminal_lock, daemon=True)
    holder.start()
    dispatch = None
    try:
        assert await asyncio.to_thread(holder_ready.wait, 5)
        dispatch = asyncio.create_task(context.service.dispatch_registered_next())
        assert await asyncio.to_thread(dispatch_lock_attempted.wait, 5)

        attempt = context.repo.get_work(work["id"])["attempts"][0]
        assert attempt["terminal_id"] is None
        assert attempt["state"] == "planned"
    finally:
        release_holder.set()
        await asyncio.to_thread(holder.join, 5)

    assert dispatch is not None
    result = await dispatch
    assert result["attempts"][0]["terminal_id"] == terminal_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "snapshot_text", "max_content_bytes", "redacted", "truncated"),
    [
        ("launch-unicode", "contexto durable: π\nlínea dos", 65536, False, False),
        (
            "launch-redacted",
            "Authorization: Bearer a-very-long-token-that-must-never-be-delivered",
            65536,
            True,
            False,
        ),
        ("launch-truncated", "é" * 8, 7, False, True),
        ("launch-empty", "", 65536, False, False),
    ],
)
async def test_restarted_registered_launch_delivers_exact_persisted_snapshot_without_live_memory(
    launch_context,
    monkeypatch,
    key,
    snapshot_text,
    max_content_bytes,
    redacted,
    truncated,
):
    """Changing launch to use any live value instead of ``snapshot.content`` breaks this.

    The admitted order is persisted before the original service and delivery
    registry are discarded.  A rebuilt managed dispatcher must materialize only
    the SQLite snapshot, including its redaction/truncation/empty semantics.
    """
    context = launch_context
    work = admit(
        context,
        key,
        delivery=launch_envelope(),
        snapshot_content=snapshot_text,
        snapshot_max_content_bytes=max_content_bytes,
    )
    with context.repo.connection() as connection:
        persisted = connection.execute(
            "SELECT content,redacted,truncated FROM work_delegation_snapshots"
        ).fetchone()
    assert persisted["redacted"] == int(redacted)
    assert persisted["truncated"] == int(truncated)
    if redacted:
        assert b"[REDACTED:bearer_token]" in persisted["content"]
        assert b"a-very-long-token-that-must-never-be-delivered" not in persisted["content"]
    if truncated:
        assert persisted["content"] == "é".encode() * 3

    monkeypatch.setattr(settings_service, "is_memory_enabled", lambda: True)
    monkeypatch.setattr(
        terminal_service,
        "MemoryService",
        lambda: pytest.fail("managed launch must not resolve live memory after restart"),
    )
    module = importlib.import_module("cli_agent_orchestrator.services.work_launch")
    context.service = WorkAdmission(
        WorkRepository(context.repo.path),
        backends={"test": context.backend},
        delivery_adapters={("launch", 1): module.launch_adapter()},
    )

    result = await context.service.dispatch_registered_next()

    assert result["id"] == work["id"]
    expected = "task text"
    if persisted["content"]:
        expected = persisted["content"].decode("utf-8") + "\n\n" + expected
    assert [effect[2] for effect in context.backend.effects] == [
        "mock_cli startup",
        expected,
    ]
    assert await context.service.dispatch_registered_next() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["retire", "replace"])
async def test_launch_provision_change_after_window_creation_blocks_provider_readiness(
    launch_context, change
):
    from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning

    context = launch_context
    key = f"launch-provision-{change}"
    work = admit(context, key, delivery=launch_envelope())
    origin = context.launch_origins[(0, key)]
    provisioning = WorkProvisioning(context.repo)

    def change_active_provision():
        assert context.backend.sessions
        assert database.get_terminal_metadata("1234abcd") is not None
        provider = terminal_service.provider_manager.get_provider("1234abcd")
        assert provider is not None
        assert provider._initialized is False

        if change == "retire":
            revised = provisioning.retire_launch(
                context.actor,
                subject=context.actor,
                selector=origin.ref.selector,
                expected_revision=origin.ref.revision,
            )
        else:
            revised = provisioning.provision_launch(
                context.actor,
                subject=context.actor,
                selector=origin.ref.selector,
                expected_revision=origin.ref.revision,
                job_id=origin.job_id,
                grant_id=origin.grant_id,
                grant_revision=origin.grant_revision,
                contract=origin.contract,
                adapter_version=origin.adapter_version,
                lease_seconds=origin.lease_seconds,
            )
        assert revised.revision == origin.ref.revision + 1

    context.before_ready = change_active_provision
    with pytest.raises(DeliveryUncertain):
        await context.service.dispatch_registered_next()

    assert context.backend.effects == []
    assert context.backend.sessions
    assert database.get_terminal_metadata("1234abcd")["tmux_session"] == "cao-durable-launch"
    result = context.repo.get_work(work["id"])
    assert result["state"] == "reconcile"
    assert result["attempts"][0]["terminal_id"] == "1234abcd"


@pytest.mark.asyncio
async def test_revocation_after_window_creation_blocks_provider_start(launch_context):
    context = launch_context
    work = admit(context, "launch-revoke", delivery=launch_envelope())

    def revoke():
        context.control.revoke(
            context.actor,
            grant_id=context.jobs[0][1].id,
            expected_grant_revision=1,
            reason="revoked before initialization",
        )

    context.before_ready = revoke
    with pytest.raises(DeliveryUncertain):
        await context.service.dispatch_registered_next()
    assert context.backend.effects == []
    assert context.repo.get_work(work["id"])["state"] == "reconcile"
    assert context.backend.sessions
    assert database.get_terminal_metadata("1234abcd")["tmux_session"] == "cao-durable-launch"


@pytest.mark.asyncio
async def test_revocation_after_provider_start_blocks_task_receipt_and_send(
    launch_context, monkeypatch
):
    """A revoked managed launch leaves no new task receipt after provider startup.

    The CLI startup command is already an external effect.  When authority is
    revoked after that command but before the assigned task, the next task
    delivery must be denied before it creates a receipt or reaches the backend.
    """
    context = launch_context
    monkeypatch.setattr(MockCliProvider, "requires_turn_receipt", True)
    original_send = context.backend.send_keys

    def send_then_revoke(session_name, window_name, text, **kwargs):
        original_send(session_name, window_name, text, **kwargs)
        if text == "mock_cli startup":
            context.control.revoke(
                context.actor,
                grant_id=context.jobs[0][1].id,
                expected_grant_revision=1,
                reason="revoked after provider startup",
            )

    context.backend.send_keys = send_then_revoke
    work = admit(context, "launch-revoke-before-task", delivery=launch_envelope())

    with pytest.raises(DeliveryUncertain):
        await context.service.dispatch_registered_next()

    assert [effect[2] for effect in context.backend.effects] == ["mock_cli startup"]
    assert database.get_terminal_turn_receipt("1234abcd") is None
    assert context.repo.get_work(work["id"])["state"] == "reconcile"


def test_launch_tools_outside_contract_are_rejected_before_queueing(launch_context):
    from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope

    context = launch_context
    value = launch_envelope()
    raw = json.loads(value.payload_json)
    raw["allowed_tools"] = ["Bash"]
    value = WorkDeliveryEnvelope(
        operation_kind="launch", adapter_version=1, payload_json=json.dumps(raw)
    )
    with pytest.raises(ValueError):
        admit(context, "wide-tools", delivery=value)
    with context.repo.connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_items").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_disabled_memory_does_not_silently_drop_admitted_snapshot(
    launch_context, monkeypatch
):
    context = launch_context
    work = admit(context, "disabled-memory", delivery=launch_envelope())
    context.before_ready = lambda: monkeypatch.setattr(
        "cli_agent_orchestrator.services.settings_service.is_memory_enabled", lambda: False
    )
    with pytest.raises(DeliveryUncertain):
        await context.service.dispatch_registered_next()
    assert all("task text" not in effect[2] for effect in context.backend.effects)
    assert context.repo.get_work(work["id"])["state"] == "reconcile"


@pytest.mark.asyncio
async def test_launch_id_collision_preserves_existing_terminal(launch_context):
    context = launch_context
    database.create_terminal("1234abcd", "cao-existing", "existing-window", "mock_cli")
    admit(context, "collision", delivery=launch_envelope())
    with pytest.raises(DeliveryUncertain):
        await context.service.dispatch_registered_next()
    assert database.get_terminal_metadata("1234abcd")["tmux_session"] == "cao-existing"
    assert context.backend.sessions == {}


@pytest.mark.asyncio
async def test_launch_insert_race_preserves_winning_terminal(launch_context, monkeypatch):
    context = launch_context
    original = terminal_service.db_create_terminal
    kill_calls = []
    original_kill = context.backend.kill_session
    context.backend.kill_session = lambda name: (kill_calls.append(name), original_kill(name))[1]

    def competing_insert(*args, **kwargs):
        database.create_terminal("1234abcd", "cao-race-winner", "winner-window", "mock_cli")
        return original(*args, **kwargs)

    monkeypatch.setattr(terminal_service, "db_create_terminal", competing_insert)
    work = admit(context, "insert-race", delivery=launch_envelope())
    with pytest.raises(DeliveryUncertain):
        await context.service.dispatch_registered_next()
    assert database.get_terminal_metadata("1234abcd")["tmux_session"] == "cao-race-winner"
    # The competing row owns a different session. This backend exposes no
    # server-instance-fenced session identity, so Work must leave the created
    # resource untouched for reconciliation rather than kill by reusable name.
    assert context.backend.sessions == {
        "cao-durable-launch": (
            "developer-1234",
            "1234abcd",
            str(context.root),
        )
    }
    assert kill_calls == []
    state = context.repo.get_work(work["id"])
    assert state["state"] == "reconcile"
    assert state["attempts"][0]["cleanup_state"] == "not_requested"


@pytest.mark.asyncio
async def test_launch_insert_after_commit_preserves_durable_session_owner(
    launch_context, monkeypatch
):
    context = launch_context
    original = terminal_service.db_create_terminal

    def commit_then_lose_confirmation(*args, **kwargs):
        result = original(*args, **kwargs)
        raise RuntimeError("terminal insert confirmation was lost")

    monkeypatch.setattr(terminal_service, "db_create_terminal", commit_then_lose_confirmation)
    work = admit(context, "insert-confirmation-lost", delivery=launch_envelope())
    with pytest.raises(DeliveryUncertain):
        await context.service.dispatch_registered_next()

    assert database.get_terminal_metadata("1234abcd")["tmux_session"] == "cao-durable-launch"
    assert context.backend.sessions == {
        "cao-durable-launch": (
            "developer-1234",
            "1234abcd",
            str(context.root),
        )
    }
    state = context.repo.get_work(work["id"])
    assert state["state"] == "reconcile"
    assert state["attempts"][0]["cleanup_state"] == "not_requested"


@pytest.mark.asyncio
async def test_launch_insert_race_after_revocation_keeps_session_for_reconcile(
    launch_context, monkeypatch
):
    context = launch_context
    original = terminal_service.db_create_terminal

    def competing_insert_after_revocation(*args, **kwargs):
        database.create_terminal("1234abcd", "cao-race-winner", "winner-window", "mock_cli")
        context.control.revoke(
            context.actor,
            grant_id=context.jobs[0][1].id,
            expected_grant_revision=1,
            reason="revoked before failed insert compensation",
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(terminal_service, "db_create_terminal", competing_insert_after_revocation)
    work = admit(context, "insert-race-revoked", delivery=launch_envelope())
    with pytest.raises(DeliveryUncertain):
        await context.service.dispatch_registered_next()

    assert database.get_terminal_metadata("1234abcd")["tmux_session"] == "cao-race-winner"
    assert context.backend.sessions
    state = context.repo.get_work(work["id"])
    assert state["state"] == "reconcile"
    assert state["attempts"][0]["cleanup_state"] == "not_requested"


@pytest.mark.asyncio
async def test_cancelled_create_after_revocation_keeps_reconciliation_evidence(launch_context):
    import asyncio
    import threading

    context = launch_context
    entered, release = threading.Event(), threading.Event()
    original = context.backend.create_session

    def blocked_create(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(10)
        return result

    context.backend.create_session = blocked_create
    work = admit(context, "cancelled-create", delivery=launch_envelope())
    task = asyncio.create_task(context.service.dispatch_registered_next())
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        context.control.revoke(
            context.actor,
            grant_id=context.jobs[0][1].id,
            expected_grant_revision=1,
            reason="cancelled during creation",
        )
        task.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert context.backend.sessions
    assert database.get_terminal_metadata("1234abcd") is not None
    assert context.repo.get_work(work["id"])["state"] == "reconcile"
