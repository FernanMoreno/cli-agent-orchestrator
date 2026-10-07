"""Recovery guarantees for independently running local CAO profiles."""

from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services import (
    local_peer_identity,
    local_peer_registry,
    local_peer_service,
)
from cli_agent_orchestrator.services.terminal_service import (
    delete_terminal as _real_delete_terminal,
)


def test_startup_releases_a_write_lease_without_a_task_receipt(monkeypatch, tmp_path):
    """A crash between lease acquisition and receipt commit cannot fence a project forever."""
    monkeypatch.setattr(local_peer_registry, "LOCAL_PEER_DIR", tmp_path / "peers")
    monkeypatch.setattr(
        local_peer_registry, "LOCAL_PEER_REGISTRY_FILE", tmp_path / "peers" / "registry.sqlite3"
    )
    monkeypatch.setattr(
        local_peer_service,
        "current_process_identity",
        lambda: SimpleNamespace(instance_id="profile-local"),
    )

    local_peer_registry.acquire_project_write_lease(
        project_id="project-1", task_id="task-1", owner_instance_id="profile-local"
    )
    assert local_peer_registry.project_write_lease("project-1")["state"] == "held"

    local_peer_service.reconcile_local_peer_tasks_at_startup()

    lease = local_peer_registry.project_write_lease("project-1")
    assert lease is not None
    assert lease["state"] == "released"


def test_status_recovers_terminal_created_before_peer_receipt_was_linked(monkeypatch):
    task_id = "f" * 36
    assignment_id = "assignment-local-peer-recovery"
    terminal_id = "a1b2c3d4"
    with local_peer_service.database.SessionLocal() as session:
        session.add(
            local_peer_service.database.LocalPeerTaskModel(
                task_id=task_id,
                source_instance_id="source-profile",
                target_instance_id="target-profile",
                requester_terminal_id="deadbeef",
                project_id="b" * 64,
                operation_key="recovery-key-1",
                request_hash="c" * 64,
                assignment_id=assignment_id,
                use_worktree=True,
                state="accepted",
            )
        )
        session.add(
            local_peer_service.database.IdempotencyKeyModel(
                key=assignment_id,
                terminal_id=terminal_id,
                request_fingerprint="d" * 64,
            )
        )
        session.commit()

    from cli_agent_orchestrator.services import terminal_service

    monkeypatch.setattr(
        terminal_service, "get_terminal", lambda _terminal_id: {"status": "processing"}
    )

    receipt = local_peer_service.refresh_local_task(task_id)

    assert receipt["terminal_id"] == terminal_id
    assert receipt["state"] == "running"


@pytest.mark.parametrize("server_host", ["::1", "127.0.0.2"])
def test_discovery_is_disabled_for_hosts_not_accepted_by_default_host_policy(
    monkeypatch, server_host
):
    """Peer discovery must not publish an endpoint TrustedHostMiddleware will reject."""
    monkeypatch.setattr(local_peer_identity, "SERVER_HOST", server_host)
    monkeypatch.setattr(
        local_peer_identity, "ALLOWED_HOSTS", ["localhost", "127.0.0.1"], raising=False
    )

    with pytest.raises(local_peer_identity.LocalPeerUnavailableError):
        local_peer_identity._loopback_host_for_server()


def test_inter_peer_http_requests_ignore_configured_proxy_environment(monkeypatch):
    observed = {}

    class Session:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def request(self, method, url, **kwargs):
            observed.update(method=method, url=url, trust_env=self.trust_env, **kwargs)
            return "response"

    monkeypatch.setattr(local_peer_service.requests, "Session", Session)

    result = local_peer_service._loopback_request("GET", "http://127.0.0.1:9889/identity")

    assert result == "response"
    assert observed["trust_env"] is False


def test_peer_cli_uses_the_registered_server_process_identity(monkeypatch):
    """A CLI process must identify the CAO server, not its own short-lived PID."""
    caller_identity = SimpleNamespace(
        instance_id="profile-a",
        process_generation="cli-generation",
        pid=202,
        process_started_at=2.0,
        display_name="profile a",
        loopback_host="127.0.0.1",
        loopback_port=9889,
        public_key="P" * 43,
    )
    server_record = SimpleNamespace(
        instance_id="profile-a",
        process_generation="server-generation",
        pid=101,
        process_started_at=1.0,
        display_name="profile a",
        loopback_host="127.0.0.1",
        loopback_port=9889,
    )
    live_identity = {
        "instance_id": "profile-a",
        "process_generation": "server-generation",
        "loopback_port": 9889,
        "display_name": "profile a",
        "public_key": "P" * 43,
    }
    monkeypatch.setattr(local_peer_service, "current_process_identity", lambda: caller_identity)
    monkeypatch.setattr(local_peer_service, "get_instance", lambda _instance_id: server_record)
    monkeypatch.setattr(local_peer_service, "_verify_live_peer", lambda _record: live_identity)

    identity = local_peer_service.current_server_identity()

    assert identity.process_generation == "server-generation"
    assert identity.pid == 101


def test_peer_signatures_use_the_registered_server_process_identity(monkeypatch):
    server_identity = SimpleNamespace(
        instance_id="profile-a",
        process_generation="server-generation",
    )
    observed = {}
    monkeypatch.setattr(local_peer_service, "current_server_identity", lambda: server_identity)
    monkeypatch.setattr(
        local_peer_service,
        "signed_peer_headers",
        lambda **kwargs: observed.update(kwargs) or {},
    )

    local_peer_service._signed_headers(
        method="GET", path="/local-coordination/tasks/example", project_id="p" * 64
    )

    assert observed["identity"] is server_identity


def test_pairing_invitation_compares_origin_key_from_live_identity(monkeypatch):
    candidate_identity = SimpleNamespace(
        instance_id="candidate-profile",
        process_generation="candidate-generation",
        display_name="candidate",
        public_key="C" * 43,
    )
    origin_record = SimpleNamespace(
        instance_id="origin-profile",
        process_generation="origin-generation",
        display_name="origin",
        loopback_port=9889,
    )
    origin_live_identity = {
        "instance_id": "origin-profile",
        "process_generation": "origin-generation",
        "display_name": "origin",
        "public_key": "O" * 43,
    }
    binding = {
        "project_id": "p" * 64,
        "canonical_root": "/project",
        "git_common_dir": None,
    }
    payload = {
        "candidate_instance_id": "candidate-profile",
        "candidate_process_generation": "candidate-generation",
        "candidate_public_key": "C" * 43,
        "initiator_instance_id": "origin-profile",
        "initiator_process_generation": "origin-generation",
        "initiator_loopback_port": 9889,
        "initiator_display_name": "origin",
        "initiator_public_key": "O" * 43,
        "canonical_root": "/project",
        "project_id": "p" * 64,
        "git_common_dir": None,
    }
    received = {}
    monkeypatch.setattr(local_peer_service, "current_server_identity", lambda: candidate_identity)
    monkeypatch.setattr(
        local_peer_service,
        "_verify_registry_identity",
        lambda *_args: (origin_record, origin_live_identity),
    )
    monkeypatch.setattr(local_peer_service, "project_binding", lambda _path: binding)
    monkeypatch.setattr(local_peer_service, "_store_project_binding", lambda _binding: None)
    monkeypatch.setattr(
        local_peer_service.local_peer_auth,
        "receive_candidate_challenge",
        lambda sanitized: received.update(sanitized) or {"challenge_id": "challenge"},
    )

    receipt = local_peer_service.receive_pairing_invitation(payload)

    assert receipt["challenge_id"] == "challenge"
    assert received["current_instance_id"] == "candidate-profile"


@pytest.fixture
def cancellable_peer_task(monkeypatch, tmp_path):
    from cli_agent_orchestrator.services import terminal_service

    monkeypatch.setattr(local_peer_registry, "LOCAL_PEER_DIR", tmp_path / "peers")
    monkeypatch.setattr(
        local_peer_registry, "LOCAL_PEER_REGISTRY_FILE", tmp_path / "peers/registry.sqlite3"
    )
    monkeypatch.setattr(
        local_peer_service,
        "current_process_identity",
        lambda: SimpleNamespace(instance_id="target-profile"),
    )
    task, _ = local_peer_service._claim_local_task(
        task_id="cancel-task",
        source_instance_id="source-profile",
        target_instance_id="target-profile",
        requester_terminal_id="deadbeef",
        project_id="b" * 64,
        operation_key="cancel-key",
        request_hash="c" * 64,
        use_worktree=False,
    )
    local_peer_service._update_task(task["task_id"], terminal_id="a1b2c3d4", state="running")
    local_peer_registry.acquire_project_write_lease(
        project_id=task["project_id"], task_id=task["task_id"], owner_instance_id="target-profile"
    )
    monkeypatch.setattr(terminal_service, "get_terminal", lambda _: {"status": "processing"})
    monkeypatch.setattr(terminal_service, "delete_terminal", lambda *args, **kwargs: True)
    return task


def cancel_peer_task(task):
    return local_peer_service.cancel_incoming_task(
        task["task_id"],
        peer_instance_id=task["source_instance_id"],
        project_id=task["project_id"],
        requester_terminal_id=task["requester_terminal_id"],
        registry=None,
    )


def test_cancel_persists_matching_terminal_receipt(cancellable_peer_task):
    receipt = cancel_peer_task(cancellable_peer_task)
    assert receipt["state"] == receipt["result"]["state"] == "cancelled"
    assert receipt["result"]["terminal_id"] == "a1b2c3d4"
    assert local_peer_service.refresh_local_task(receipt["task_id"]) == receipt
    assert local_peer_registry.project_write_lease(receipt["project_id"])["state"] == "released"


def test_stale_reconciliation_cannot_replace_cancelled_receipt(cancellable_peer_task):
    receipt = cancel_peer_task(cancellable_peer_task)
    stale = local_peer_service._update_task(
        receipt["task_id"], state="running", result_json='{"state":"running"}'
    )
    assert stale == receipt


def test_stale_lease_update_cannot_reactivate_released_task(cancellable_peer_task):
    task = cancellable_peer_task
    cancel_peer_task(task)
    assert not local_peer_registry.update_project_write_lease(
        project_id=task["project_id"],
        task_id=task["task_id"],
        owner_instance_id="target-profile",
        state="held",
    )
    assert local_peer_registry.project_write_lease(task["project_id"])["state"] == "released"


def test_terminal_receipt_survives_unavailable_peer(cancellable_peer_task, monkeypatch):
    task = cancellable_peer_task
    receipt = cancel_peer_task(task)
    monkeypatch.setattr(
        local_peer_service,
        "current_server_identity",
        lambda: SimpleNamespace(instance_id="source-profile"),
    )

    def unavailable(*args):
        raise local_peer_service.LocalPeerUnavailable("peer stopped")

    monkeypatch.setattr(local_peer_service, "_outbound_peer", unavailable)
    assert (
        local_peer_service.task_from_peer(task["task_id"], requester_terminal_id="deadbeef")
        == receipt
    )


def test_terminal_receipt_retries_release_after_cross_database_failure(
    cancellable_peer_task, monkeypatch
):
    task = cancellable_peer_task
    actual = local_peer_service.update_project_write_lease
    monkeypatch.setattr(local_peer_service, "update_project_write_lease", lambda **kwargs: False)
    receipt = cancel_peer_task(task)
    assert local_peer_registry.project_write_lease(task["project_id"])["state"] == "held"
    monkeypatch.setattr(local_peer_service, "update_project_write_lease", actual)
    assert local_peer_service.refresh_local_task(task["task_id"]) == receipt
    assert local_peer_registry.project_write_lease(task["project_id"])["state"] == "released"


def test_principal_binding_migration_is_additive_and_idempotent(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text

    db = local_peer_service.database
    engine = create_engine(f'sqlite:///{tmp_path / "legacy.sqlite3"}')
    monkeypatch.setattr(db, "engine", engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE local_peer_tasks (task_id TEXT PRIMARY KEY, state TEXT NOT NULL, result_json TEXT)"
            )
        )
        connection.execute(
            text("INSERT INTO local_peer_tasks VALUES (:id, :state, :result)"),
            {"id": "old", "state": "cancelled", "result": '{"state":"cancelled"}'},
        )
    db._migrate_local_peer_principal()
    db._migrate_local_peer_principal()
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT task_id, state, result_json, requester_principal_id FROM local_peer_tasks")
        ).one() == ("old", "cancelled", '{"state":"cancelled"}', None)
        assert connection.execute(
            text("SELECT task_id, state, result_json FROM local_peer_tasks")
        ).one() == ("old", "cancelled", '{"state":"cancelled"}')
    engine.dispose()


def test_outgoing_idempotency_key_retains_authenticated_principal(cancellable_peer_task):
    task = cancellable_peer_task
    with local_peer_service.database.SessionLocal() as session:
        row = session.get(local_peer_service.database.LocalPeerTaskModel, task["task_id"])
        row.requester_principal_id = "alice"
        session.commit()
    with pytest.raises(
        local_peer_service.LocalPeerConflict, match="another authenticated principal"
    ):
        local_peer_service._claim_local_task(
            task_id=task["task_id"],
            source_instance_id=task["source_instance_id"],
            requester_terminal_id=task["requester_terminal_id"],
            target_instance_id=task["target_instance_id"],
            project_id=task["project_id"],
            operation_key=task["operation_key"],
            request_hash="c" * 64,
            use_worktree=False,
            requester_principal_id="bob",
        )


@pytest.mark.parametrize(
    "state,result",
    [
        ("cancelled", None),
        ("cancelled", '{"state":"running","output":"retained"}'),
        ("succeeded", '{"state":"running","output":"retained"}'),
        ("failed", '{"state":"succeeded","output":"retained"}'),
    ],
)
def test_legacy_terminal_receipt_repairs_state_without_releasing_unproven_writer(
    cancellable_peer_task, state, result
):
    task = cancellable_peer_task
    with local_peer_service.database.SessionLocal() as session:
        row = session.get(local_peer_service.database.LocalPeerTaskModel, task["task_id"])
        row.state, row.result_json = state, result
        session.commit()
    receipt = local_peer_service.refresh_local_task(task["task_id"])
    assert receipt["state"] == receipt["result"]["state"] == state
    if result:
        assert receipt["result"]["output"] == "retained"
    assert local_peer_registry.project_write_lease(task["project_id"])["state"] == "held"
    assert local_peer_service.refresh_local_task(task["task_id"]) == receipt


def test_operator_can_release_legacy_terminal_reservation_without_replacing_winner(
    cancellable_peer_task, monkeypatch
):
    from cli_agent_orchestrator.services import terminal_service

    task = cancellable_peer_task
    with local_peer_service.database.SessionLocal() as session:
        row = session.get(local_peer_service.database.LocalPeerTaskModel, task["task_id"])
        row.state, row.result_json = "cancelled", '{"state":"running","output":"retained"}'
        session.commit()

    def stopped(_):
        raise ValueError("worker absent after review")

    monkeypatch.setattr(terminal_service, "get_terminal", stopped)
    receipt = local_peer_service.reconcile_local_task_after_operator_review(task["task_id"])
    assert receipt["state"] == receipt["result"]["state"] == "cancelled"
    assert receipt["result"]["output"] == "retained"
    assert receipt["result"]["operator_confirmed_stopped"] is True
    assert local_peer_registry.project_write_lease(task["project_id"])["state"] == "released"


def test_operator_cannot_release_legacy_terminal_reservation_while_worker_active(
    cancellable_peer_task,
):
    task = cancellable_peer_task
    local_peer_service._update_task(
        task["task_id"], state="cancelled", result_json='{"state":"cancelled"}'
    )
    with pytest.raises(local_peer_service.LocalPeerConflict, match="still exists"):
        local_peer_service.reconcile_local_task_after_operator_review(task["task_id"])
    assert local_peer_registry.project_write_lease(task["project_id"])["state"] == "held"


def test_peer_disappearing_during_status_keeps_last_receipt_and_reports_unavailable(
    cancellable_peer_task, monkeypatch
):
    import requests

    task = cancellable_peer_task
    before = local_peer_service._find_task(task["task_id"])[0]
    monkeypatch.setattr(
        local_peer_service,
        "current_server_identity",
        lambda: SimpleNamespace(instance_id="source-profile"),
    )
    monkeypatch.setattr(
        local_peer_service,
        "_outbound_peer",
        lambda *args: (SimpleNamespace(base_url="http://127.0.0.1:9"), None),
    )
    monkeypatch.setattr(local_peer_service, "_signed_headers", lambda **kwargs: {})

    def offline(*args, **kwargs):
        raise requests.ConnectionError("peer exited mid-read")

    monkeypatch.setattr(local_peer_service, "_loopback_request", offline)
    result = local_peer_service.task_from_peer(task["task_id"], requester_terminal_id="deadbeef")
    assert result["error"] == "peer_unavailable"
    assert result["peer_state"] == "unavailable"
    assert result["state"] == before["state"]
    assert local_peer_service._find_task(task["task_id"])[0] == before


@pytest.mark.parametrize("stop_outcome", [False, "exception"])
def test_unacknowledged_backend_stop_retains_task_lease_and_runtime(
    cancellable_peer_task, monkeypatch, stop_outcome
):
    from unittest.mock import MagicMock

    from cli_agent_orchestrator.services import remote_terminal_service, terminal_service

    task = cancellable_peer_task
    # Undo the fixture's successful-delete stub; exercise the real teardown.
    monkeypatch.setattr(terminal_service, "delete_terminal", _real_delete_terminal)
    backend = MagicMock()
    if stop_outcome == "exception":
        backend.kill_window.side_effect = RuntimeError("backend stop unavailable")
    else:
        backend.kill_window.return_value = stop_outcome
    monkeypatch.setattr(terminal_service, "get_backend", lambda: backend)
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _: None)
    monkeypatch.setattr(terminal_service, "ensure_terminal_is_not_work_owned", lambda _: None)
    metadata = {
        "tmux_session": "cao-peer",
        "tmux_window": "worker",
        "live_working_directory": "/project/.cao/worktrees/a1b2c3d4",
    }
    monkeypatch.setattr(terminal_service, "capture_terminal_snapshot", lambda _: metadata)
    deleted = MagicMock(return_value=True)
    monkeypatch.setattr(terminal_service, "delete_terminal_row", deleted)
    removed = MagicMock()
    monkeypatch.setattr(terminal_service.worktree_service, "remove_worktree", removed)
    monkeypatch.setattr(
        terminal_service.worktree_service, "parse_worktree_path", lambda _: ("/project", "a1b2c3d4")
    )
    cleanup = MagicMock(return_value=True)
    monkeypatch.setattr(terminal_service.provider_manager, "cleanup_provider", cleanup)
    stop_reader = MagicMock(return_value=True)
    monkeypatch.setattr(terminal_service.fifo_manager, "stop_reader", stop_reader)
    unregister = MagicMock()
    monkeypatch.setattr(
        terminal_service,
        "get_herdr_inbox_service",
        lambda: SimpleNamespace(unregister_terminal=unregister),
    )
    receipt = cancel_peer_task(task)
    assert receipt["state"] != "cancelled"
    assert local_peer_registry.project_write_lease(task["project_id"])["state"] != "released"
    deleted.assert_not_called()
    removed.assert_not_called()
    cleanup.assert_not_called()
    stop_reader.assert_not_called()
    unregister.assert_not_called()
