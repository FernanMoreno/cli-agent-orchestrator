"""Managed and legacy mock_cli lifecycle acceptance across runtime restarts."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib
import json
import shutil
import time
import uuid
from test.fixtures.cao_server import _pick_free_port, _start_cao_server
from test.integration.test_work_dispatch import admit, context  # noqa: F401
from test.services.test_work_agent_step import agent_step_context, agent_step_envelope
from test.services.test_work_inbox import _coordinator, _request, paired_context
from test.services.test_work_launch import launch_context, launch_envelope

import pytest
import requests

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
from cli_agent_orchestrator.models.work_contract import ContractPermissions, ContractResources
from cli_agent_orchestrator.models.work_origin import ManagedLineageIntent, WorkAttemptRef
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services import terminal_service
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    ResolvedSnapshot,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.work_admission import WorkAdmission
from cli_agent_orchestrator.services.work_agent_step import agent_step_adapter
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_contract import ContractConflict
from cli_agent_orchestrator.services.work_launch import launch_adapter
from cli_agent_orchestrator.services.work_origin import WorkOriginAuthority, WorkOrigins

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def _install_lifecycle_postgres_schema(postgres_targets):
    """Give the real subprocess the same fully provisioned target as test helpers."""
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

    bootstrap_scope(postgres_targets.main)


@pytest.fixture(scope="session", autouse=True)
def require_cao_server():
    """These e2e cases use the real Work composition with a local mock_cli."""
    yield


@pytest.fixture(scope="session", autouse=True)
def require_tmux():
    yield


@pytest.mark.asyncio
async def test_launch_restart_delivers_the_persisted_snapshot(launch_context, monkeypatch):
    context = launch_context
    work = admit(
        context,
        "lifecycle-launch",
        delivery=launch_envelope(),
        snapshot_content="launch frozen: π\nsecond line",
    )
    with context.repo.connection() as connection:
        persisted = connection.execute(
            "SELECT id,content,delivered_hash FROM work_delegation_snapshots"
        ).fetchone()

    monkeypatch.setattr(
        terminal_service,
        "MemoryService",
        lambda: pytest.fail("managed launch must not resolve live memory after restart"),
    )
    restarted = WorkRepository(context.repo.target, filesystem_home=context.repo.filesystem_home)
    context.service = WorkAdmission(
        restarted,
        backends={"test": context.backend},
        delivery_adapters={("launch", 1): launch_adapter()},
    )

    recovered = await context.service.dispatch_registered_next()

    assert recovered["id"] == work["id"]
    assert recovered["attempts"][0]["generation"] == work["attempts"][0]["generation"]
    effects = context.backend.effects
    assert len(effects) == 2
    assert effects[0][:2] == effects[1][:2]
    assert effects[0][0] == "cao-durable-launch"
    assert [effect[2] for effect in effects] == [
        "mock_cli startup",
        persisted["content"].decode("utf-8") + "\n\ntask text",
    ]
    assert hashlib.sha256(persisted["content"]).hexdigest() == persisted["delivered_hash"]
    assert await context.service.dispatch_registered_next() is None
    assert len(context.backend.effects) == 2


@pytest.mark.asyncio
async def test_inbox_restart_preserves_snapshot_and_does_not_replay(paired_context):
    from test.services.test_work_inbox import _managed_module

    context = paired_context
    module = _managed_module()
    coordinator = _coordinator(context)
    receipt = coordinator.admit(**_request(context, "lifecycle-inbox"))
    original_binding = context.service.contracts.revalidate_order(
        receipt.attempt_id, generation=receipt.generation
    )
    snapshot_id = original_binding.contract.snapshot.id
    with context.repo.connection() as connection:
        persisted_snapshot = connection.execute(
            "SELECT id,content,delivered_hash FROM work_delegation_snapshots WHERE id=%s",
            (snapshot_id,),
        ).fetchone()

    restarted = WorkRepository(context.repo.target, filesystem_home=context.repo.filesystem_home)
    service = WorkAdmission(
        restarted,
        backends={"test": context.backend},
        delivery_adapters=module.managed_inbox_delivery_adapters(restarted),
    )
    sent = await service.dispatch_registered_next()

    assert sent["id"] == receipt.work_item_id
    assert sent["attempts"][0]["id"] == receipt.attempt_id
    assert sent["attempts"][0]["generation"] == receipt.generation
    binding = service.contracts.revalidate_order(receipt.attempt_id, generation=receipt.generation)
    assert (binding.contract.snapshot.id, binding.contract.snapshot.delivered_hash) == (
        persisted_snapshot["id"],
        persisted_snapshot["delivered_hash"],
    )
    assert (
        hashlib.sha256(persisted_snapshot["content"]).hexdigest()
        == persisted_snapshot["delivered_hash"]
    )
    assert context.backend.effects == [
        ("managed-session", "managed-window", "managed lifecycle-inbox")
    ]
    assert database.get_inbox_messages("receiver", status=None)[0].status.value == "reconcile"
    assert await service.dispatch_registered_next() is None
    assert len(context.backend.effects) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change_target", ("move", "remove"))
async def test_inbox_rejects_target_change_after_preview(paired_context, change_target):
    context = paired_context
    coordinator = _coordinator(context)
    coordinator.admit(**_request(context, f"target-change-{change_target}"))

    def change_receiver_during_preflight():
        with database.SessionLocal() as session:
            receiver = session.get(database.TerminalModel, "receiver")
            if change_target == "remove":
                session.delete(receiver)
            else:
                receiver.tmux_session = "replacement-session"
            session.commit()

    context.backend.preflight_hook = change_receiver_during_preflight

    with pytest.raises(WorkConflict):
        await context.service.dispatch_registered_next()

    assert context.backend.effects == []


@pytest.mark.asyncio
async def test_legacy_v1_inbox_order_replays_fail_closed_after_restart(paired_context, monkeypatch):
    from test.services.test_work_inbox import _managed_module

    context = paired_context
    module = _managed_module()
    envelope_type = module.WorkDeliveryEnvelope

    def legacy_envelope(*, operation_kind, adapter_version, payload_json):
        payload = json.loads(payload_json)
        return envelope_type(
            operation_kind=operation_kind,
            adapter_version=1,
            payload_json=json.dumps({"inbox_id": payload["inbox_id"]}, separators=(",", ":")),
        )

    monkeypatch.setattr(module, "WorkDeliveryEnvelope", legacy_envelope)
    request = _request(context, "legacy-v1-reopen")
    coordinator = _coordinator(context)
    receipt = coordinator.admit(**request)
    with context.repo.connection() as connection:
        legacy_order = connection.execute(
            "SELECT adapter_version,payload_json FROM work_delivery_orders WHERE attempt_id=%s",
            (receipt.attempt_id,),
        ).fetchone()
        assert legacy_order["adapter_version"] == 1
        _, legacy_payload = context.service.deliveries._restore(
            connection,
            context.service.contracts.revalidate_order(
                receipt.attempt_id, generation=receipt.generation
            ),
        )
    assert legacy_payload.model_dump(mode="json") == {"inbox_id": receipt.inbox_id}

    restarted = WorkRepository(context.repo.target, filesystem_home=context.repo.filesystem_home)
    service = WorkAdmission(
        restarted,
        backends={"test": context.backend},
        delivery_adapters=module.managed_inbox_delivery_adapters(restarted),
    )
    replayed = module.WorkInboxCoordinator(service).admit(**request)

    assert replayed == receipt
    with restarted.connection() as connection:
        _, reopened_payload = service.deliveries._restore(
            connection,
            service.contracts.revalidate_order(receipt.attempt_id, generation=receipt.generation),
        )
        replayed_order = connection.execute(
            "SELECT adapter_version,payload_json FROM work_delivery_orders WHERE attempt_id=%s",
            (receipt.attempt_id,),
        ).fetchone()
    assert reopened_payload.model_dump(mode="json") == {"inbox_id": receipt.inbox_id}
    assert replayed_order["adapter_version"] == legacy_order["adapter_version"]
    assert replayed_order["payload_json"] == legacy_order["payload_json"]
    with database.SessionLocal() as session:
        receiver = session.get(database.TerminalModel, "receiver")
        receiver.tmux_session = "replacement-session"
        session.commit()

    with pytest.raises(ContractConflict, match="no pinned terminal target"):
        await service.dispatch_registered_next()
    with pytest.raises(ContractConflict, match="no pinned terminal target"):
        await service.dispatch_registered_next()
    assert context.backend.effects == []
    assert restarted.get_work(receipt.work_item_id)["state"] == "queued"
    assert restarted.get_work(receipt.work_item_id)["attempts"][0]["terminal_id"] is None
    with restarted.connection() as connection:
        reopened_order = connection.execute(
            "SELECT adapter_version,payload_json FROM work_delivery_orders WHERE attempt_id=%s",
            (receipt.attempt_id,),
        ).fetchone()
    assert reopened_order["adapter_version"] == 1
    assert reopened_order["payload_json"] == legacy_order["payload_json"]


def _wait_for_terminal_status(server, terminal_id, ready_states, *, timeout=25.0):
    deadline = time.monotonic() + timeout
    observed = "unknown"
    while time.monotonic() < deadline:
        response = requests.get(f"{server.url}/terminals/{terminal_id}", timeout=3.0)
        if response.status_code == 200:
            observed = response.json().get("status", "unknown")
            if observed in ready_states:
                return observed
            if observed == "error":
                break
        time.sleep(0.1)
    raise AssertionError(f"terminal {terminal_id} did not become ready; last status={observed}")


def _wait_for_mock_echo(server, terminal_id, message, *, timeout=25.0):
    deadline = time.monotonic() + timeout
    latest = ""
    while time.monotonic() < deadline:
        response = requests.get(
            f"{server.url}/terminals/{terminal_id}/output",
            # LAST captures tmux history directly. FULL is the streamed FIFO
            # buffer and can lag a completed pane until its reader drains.
            params={"mode": "last"},
            timeout=3.0,
        )
        if response.status_code == 200:
            latest = response.json().get("output", "")
            if latest.strip() == message:
                return latest
        else:
            latest = response.text
        time.sleep(0.1)
    raise AssertionError(f"mock_cli did not echo {message!r}; output tail={latest[-500:]!r}")


@pytest.mark.e2e
def test_real_mock_cli_session_and_inbox_survive_server_restart(tmp_path, postgres_targets):
    """Exercise legacy mock_cli session/inbox ingress across a real server restart."""
    if not shutil.which("tmux"):
        pytest.skip("real mock_cli server lifecycle requires tmux")

    home = tmp_path / "isolated-cao-home"
    extra_env = {"CAO_WORK_LAUNCH_MODE": "legacy"}
    first_server = _start_cao_server(
        home, _pick_free_port(), extra_env=extra_env, database_targets=postgres_targets
    )
    current_server = first_server
    session_names = []
    terminal_ids = []
    sender_id = receiver_id = None
    try:
        for role in ("sender", "receiver"):
            session_name = f"lifecycle-{role}-{uuid.uuid4().hex[:8]}"
            response = requests.post(
                f"{current_server.url}/sessions",
                params={
                    "provider": "mock_cli",
                    "agent_profile": "developer",
                    "session_name": session_name,
                },
                timeout=30.0,
            )
            assert response.status_code in (200, 201), response.text
            terminal_id = response.json()["id"]
            session_names.append(response.json()["session_name"])
            terminal_ids.append(terminal_id)
            _wait_for_terminal_status(current_server, terminal_id, {"idle", "completed"})
        sender_id, receiver_id = terminal_ids

        message = f"restart inbox {uuid.uuid4().hex[:8]}"
        sent = requests.post(
            f"{current_server.url}/terminals/{receiver_id}/inbox/messages",
            params={"sender_id": sender_id, "message": message},
            timeout=10.0,
        )
        assert sent.status_code == 200, sent.text
        output_before_restart = _wait_for_mock_echo(current_server, receiver_id, message)
        assert output_before_restart.strip() == message
        delivered = requests.get(
            f"{current_server.url}/terminals/{receiver_id}/inbox/messages",
            params={"status": "delivered"},
            timeout=5.0,
        )
        assert delivered.status_code == 200, delivered.text
        assert [row["message"] for row in delivered.json()] == [message]

        current_server.stop()
        current_server = _start_cao_server(
            home, _pick_free_port(), extra_env=extra_env, database_targets=postgres_targets
        )

        output_after_restart = _wait_for_mock_echo(current_server, receiver_id, message)
        assert output_after_restart.strip() == message
        full_after_restart = requests.get(
            f"{current_server.url}/terminals/{receiver_id}/output",
            params={"mode": "full"},
            timeout=5.0,
        )
        assert full_after_restart.status_code == 200, full_after_restart.text
        assert full_after_restart.json()["output"].count(f"> MOCK: {message}") == 1
        recovered = requests.get(
            f"{current_server.url}/terminals/{receiver_id}/inbox/messages",
            params={"status": "delivered"},
            timeout=5.0,
        )
        assert recovered.status_code == 200, recovered.text
        assert [row["message"] for row in recovered.json()] == [message]
    finally:
        for session_name in session_names:
            with contextlib.suppress(requests.RequestException):
                requests.delete(f"{current_server.url}/sessions/{session_name}", timeout=5.0)
        with contextlib.suppress(Exception):
            current_server.stop()


def _admit_managed_lineage(context, *, kind: str):
    from cli_agent_orchestrator.models.work_contract import ContractSnapshot, EffectiveWorkContract

    owner = context.actor
    job, root_grant = context.jobs[0]
    expected_context = f"{kind} frozen: π\nsecond line"
    snapshot = DelegationSnapshots(
        context.repo,
        policy=KnowledgePolicy(context.repo, job["id"], root_grant.id, root_grant.revision),
    ).freeze(
        principal=owner,
        job_id=job["id"],
        contract_id=f"{kind}-parent-contract",
        binding_key=f"{kind}-parent-snapshot",
        request_hash=hashlib.sha256(f"{kind}-parent".encode()).hexdigest(),
        scope="project",
        scope_id="project",
        resolver=lambda _connection, _principal: ResolvedSnapshot(expected_context),
    )
    parent_contract = EffectiveWorkContract(
        id=f"{kind}-parent-contract",
        operation_kind="inbox",
        provider="mock_cli",
        backend="test",
        permissions=ContractPermissions(paths=(str(context.root),)),
        resources=ContractResources(
            checkout_root=str(context.root),
            write_paths=(str(context.root / f"{kind}-parent"),),
            units=1,
        ),
        snapshot=ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
    )
    parent = context.service.admit(
        principal=owner,
        job_id=job["id"],
        idempotency_key=f"{kind}-parent",
        request_hash=hashlib.sha256(f"{kind}-parent-request".encode()).hexdigest(),
        grant_id=root_grant.id,
        expected_grant_revision=root_grant.revision,
        contract=parent_contract,
        lease_seconds=120,
    )

    child = auth._verified_principal(
        "https://issuer.test", f"{kind}-child", [auth.SCOPE_WRITE], "jwt"
    )
    receiver = auth._verified_principal(
        "https://issuer.test", f"{kind}-receiver", [auth.SCOPE_WRITE], "jwt"
    )
    permissions = Permissions(tools={"knowledge.read"}, paths={str(context.root)})
    child_grant = WorkAuthority(context.repo).delegate(
        owner,
        parent_grant_id=root_grant.id,
        expected_parent_revision=root_grant.revision,
        child_principal=child,
        providers={"mock_cli"},
        permissions=permissions,
        expires_at=time.time() + 300,
    )
    receiver_grant = WorkAuthority(context.repo).delegate(
        owner,
        parent_grant_id=root_grant.id,
        expected_parent_revision=root_grant.revision,
        child_principal=receiver,
        providers={"mock_cli"},
        permissions=permissions,
        expires_at=time.time() + 300,
    )
    origin_authority = WorkOriginAuthority(context.repo)
    child_subject = origin_authority.register_subject(
        owner,
        verified_subject=child,
        kind="child",
        issuer_id=owner.id,
        expected_revision=0,
    )
    child_authorization = origin_authority.authorize(
        owner,
        subject=child,
        origin_kind="child",
        grant_id=child_grant.id,
        grant_revision=child_grant.revision,
        actions={"admit_child", "admit_handoff", "execute"},
        expires_at=time.time() + 120,
        expected_revision=0,
    )
    receiver_subject = origin_authority.register_subject(
        owner,
        verified_subject=receiver,
        kind="receiver",
        issuer_id=owner.id,
        expected_revision=0,
    )
    receiver_authorization = origin_authority.authorize(
        owner,
        subject=receiver,
        origin_kind="receiver",
        grant_id=receiver_grant.id,
        grant_revision=receiver_grant.revision,
        actions={"task_received"},
        expires_at=time.time() + 120,
        expected_revision=0,
    )

    origins = WorkOrigins(context.repo)
    context.service = WorkAdmission(
        context.repo,
        backends={"test": context.backend},
        delivery_adapters={("agent_step", 1): agent_step_adapter()},
        origins=origins,
    )
    child_contract = parent_contract.model_copy(
        update={"id": f"{kind}-child-contract", "operation_kind": "agent_step"}
    )
    terminal_id = "c11d0001" if kind == "child" else "0ff10001"
    message = f"perform managed {kind} task"
    intent = ManagedLineageIntent(
        contract=child_contract,
        delivery=agent_step_envelope(
            terminal_id=terminal_id,
            message=message,
        ),
        lease_seconds=120,
    )
    parent_attempt = parent["attempts"][0]
    authenticated = origins.authenticated_context(owner, child, receiver)
    handoff = origins.admit(
        authenticated_context=authenticated,
        parent_attempt_ref=WorkAttemptRef(
            work_item_id=parent["id"],
            attempt_id=parent_attempt["id"],
            generation=parent_attempt["generation"],
        ),
        child_subject_ref=child_subject,
        child_authorization_ref=child_authorization.ref,
        receiver_subject_ref=receiver_subject,
        receiver_authorization_ref=receiver_authorization.ref,
        intent=intent,
        idempotency_key=f"{kind}-work",
        kind=kind,
    )
    work = context.service.admit_managed_lineage(handoff)
    return work, snapshot, expected_context, message, terminal_id


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ("child", "handoff"))
async def test_managed_lineage_restart_delivers_parent_snapshot(
    agent_step_context, monkeypatch, kind
):
    context = agent_step_context
    work, snapshot, expected_context, message, terminal_id = _admit_managed_lineage(
        context, kind=kind
    )
    attempt = work["attempts"][0]
    assert work["operation_kind"] == "agent_step"

    monkeypatch.setattr(
        terminal_service,
        "MemoryService",
        lambda: pytest.fail("managed lineage must not resolve live memory after restart"),
    )
    restarted = WorkRepository(context.repo.target, filesystem_home=context.repo.filesystem_home)
    context.service = WorkAdmission(
        restarted,
        backends={"test": context.backend},
        delivery_adapters={("agent_step", 1): agent_step_adapter()},
        origins=WorkOrigins(restarted),
    )

    recovered = await context.service.dispatch_registered_next()

    assert recovered["id"] == work["id"]
    assert recovered["attempts"][0]["id"] == attempt["id"]
    assert recovered["attempts"][0]["generation"] == attempt["generation"]
    binding = context.service.contracts.revalidate_order(
        attempt["id"], generation=attempt["generation"]
    )
    assert (binding.contract.snapshot.id, binding.contract.snapshot.delivered_hash) == (
        snapshot.id,
        snapshot.delivered_hash,
    )
    effects = context.backend.effects[-2:]
    assert len(effects) == 2
    assert effects[0][:2] == effects[1][:2]
    assert effects[0][0] == f"cao-{terminal_id}"
    assert [effect[2] for effect in effects] == [
        "mock_cli startup",
        expected_context + "\n\n" + message,
    ]
    assert await context.service.dispatch_registered_next() is None
    assert len(context.backend.effects) == 2


def _create_legacy_mock_parent(server, session_name):
    response = requests.post(
        f"{server.url}/sessions",
        params={
            "provider": "mock_cli",
            "agent_profile": "developer",
            "session_name": session_name,
        },
        timeout=30.0,
    )
    assert response.status_code in (200, 201), response.text
    terminal_id = response.json()["id"]
    _wait_for_terminal_status(server, terminal_id, {"idle", "completed"})
    return terminal_id


def _wait_for_native_child(
    server, parent_terminal_id, child_terminal_id, *, expected_states=None, timeout=25.0
):
    deadline = time.monotonic() + timeout
    latest = []
    latest_child = None
    while time.monotonic() < deadline:
        response = requests.get(
            f"{server.url}/terminals/{parent_terminal_id}/children", timeout=3.0
        )
        if response.status_code == 200:
            latest = response.json()
            for child in latest:
                if child.get("terminal_id") == child_terminal_id:
                    latest_child = child
                    if expected_states is None or child.get("state") in expected_states:
                        return child
        time.sleep(0.1)
    raise AssertionError(
        f"native child {child_terminal_id} was not recorded for {parent_terminal_id}; "
        f"expected_states={expected_states!r}, child={latest_child!r}, receipts={latest!r}"
    )


def _wait_for_mock_output_contains(server, terminal_id, *fragments, timeout=25.0):
    deadline = time.monotonic() + timeout
    latest = ""
    while time.monotonic() < deadline:
        response = requests.get(
            f"{server.url}/terminals/{terminal_id}/output",
            params={"mode": "full"},
            timeout=3.0,
        )
        if response.status_code == 200:
            latest = response.json().get("output", "")
            if all(fragment in latest for fragment in fragments):
                return latest
        else:
            latest = response.text
        time.sleep(0.1)
    raise AssertionError(
        f"mock_cli output for {terminal_id} did not contain {fragments!r}; "
        f"output tail={latest[-800:]!r}"
    )


@pytest.mark.e2e
def test_legacy_assign_child_receipt_survives_server_restart(
    tmp_path, monkeypatch, postgres_targets
):
    """Exercise legacy assign against real tmux/mock_cli and retain its child receipt."""
    if not shutil.which("tmux"):
        pytest.skip("real mock_cli child lifecycle requires tmux")

    home = tmp_path / "isolated-cao-home"
    extra_env = {"CAO_WORK_LAUNCH_MODE": "legacy"}
    current_server = _start_cao_server(
        home, _pick_free_port(), extra_env=extra_env, database_targets=postgres_targets
    )
    session_name = f"lifecycle-child-{uuid.uuid4().hex[:8]}"
    try:
        parent_id = _create_legacy_mock_parent(current_server, session_name)
        orchestration = importlib.import_module("cli_agent_orchestrator.utils.orchestration")
        monkeypatch.setattr(orchestration, "API_BASE_URL", current_server.url)
        monkeypatch.setenv("CAO_TERMINAL_ID", parent_id)

        message = f"restart child {uuid.uuid4().hex[:8]}"
        operation_key = session_name
        assigned = orchestration._assign_impl("developer", message, operation_key=operation_key)
        assert assigned["success"] is True, assigned
        assert assigned["operation_key"] == operation_key
        child_id = assigned["terminal_id"]
        assert child_id

        expected_fragments = [f"> MOCK: {message}"]
        if orchestration.ENABLE_SENDER_ID_INJECTION:
            expected_fragments.append(
                f"> MOCK: [Assigned by terminal {parent_id}. "
                f"When done, send results back to terminal {parent_id} using send_message]"
            )
        output = _wait_for_mock_output_contains(current_server, child_id, *expected_fragments)
        assert all(fragment in output for fragment in expected_fragments)
        before_restart = _wait_for_native_child(
            current_server,
            parent_id,
            child_id,
            expected_states={"sent", "running"},
        )
        assert before_restart["parent_terminal_id"] == parent_id
        assert before_restart["state"] in {"sent", "running"}

        current_server.stop()
        current_server = _start_cao_server(
            home, _pick_free_port(), extra_env=extra_env, database_targets=postgres_targets
        )
        monkeypatch.setattr(orchestration, "API_BASE_URL", current_server.url)

        output_after_restart = _wait_for_mock_output_contains(
            current_server, child_id, *expected_fragments
        )
        assert all(fragment in output_after_restart for fragment in expected_fragments)
        after_restart = _wait_for_native_child(current_server, parent_id, child_id)
        assert after_restart["id"] == before_restart["id"]
        assert after_restart["parent_terminal_id"] == parent_id
        assert after_restart["state"] == before_restart["state"]
    finally:
        with contextlib.suppress(requests.RequestException):
            requests.delete(f"{current_server.url}/sessions/{session_name}", timeout=5.0)
        with contextlib.suppress(Exception):
            current_server.stop()


@pytest.mark.e2e
def test_legacy_handoff_returns_output_and_settled_receipt_across_restart(
    tmp_path, monkeypatch, postgres_targets
):
    """Exercise legacy run-step handoff on real tmux/mock_cli and persist its result receipt."""
    if not shutil.which("tmux"):
        pytest.skip("real mock_cli handoff lifecycle requires tmux")

    home = tmp_path / "isolated-cao-home"
    extra_env = {"CAO_WORK_LAUNCH_MODE": "legacy"}
    current_server = _start_cao_server(
        home, _pick_free_port(), extra_env=extra_env, database_targets=postgres_targets
    )
    session_name = f"lifecycle-handoff-{uuid.uuid4().hex[:8]}"
    try:
        parent_id = _create_legacy_mock_parent(current_server, session_name)
        orchestration = importlib.import_module("cli_agent_orchestrator.utils.orchestration")
        monkeypatch.setattr(orchestration, "API_BASE_URL", current_server.url)
        monkeypatch.setenv("CAO_TERMINAL_ID", parent_id)

        message = f"restart handoff {uuid.uuid4().hex[:8]}"
        result = asyncio.run(orchestration._handoff_impl("developer", message, timeout=30))
        assert result.success is True, result
        assert result.output == message
        assert result.terminal_id

        before_restart = _wait_for_native_child(current_server, parent_id, result.terminal_id)
        assert before_restart["parent_terminal_id"] == parent_id
        assert before_restart["state"] == "succeeded"
        assert before_restart["cleanup_completed_at"] is not None

        current_server.stop()
        current_server = _start_cao_server(
            home, _pick_free_port(), extra_env=extra_env, database_targets=postgres_targets
        )
        monkeypatch.setattr(orchestration, "API_BASE_URL", current_server.url)

        response = requests.get(f"{current_server.url}/terminals/{parent_id}/children", timeout=5.0)
        assert response.status_code == 200, response.text
        after_restart = next(
            child for child in response.json() if child.get("terminal_id") == result.terminal_id
        )
        assert after_restart["id"] == before_restart["id"]
        assert after_restart["state"] == "succeeded"
        assert after_restart["cleanup_completed_at"] is not None
        assert after_restart["cleanup_completed_at"] is not None
    finally:
        with contextlib.suppress(requests.RequestException):
            requests.delete(f"{current_server.url}/sessions/{session_name}", timeout=5.0)
        with contextlib.suppress(Exception):
            current_server.stop()


@pytest.mark.e2e
@pytest.mark.parametrize("tier", ("yaml",))
def test_real_mock_workflow_result_and_attempt_survive_server_restart(
    tmp_path, tier, postgres_targets
):
    """Prove legacy workflow durability using real HTTP, tmux and mock_cli.

    Managed Work authority/result isolation is tested by the Docker T020 suite.
    This closes the credential-free workflow ingress case of the five-path
    lifecycle matrix without treating terminal output as an authenticated Work result.
    """
    if not shutil.which("tmux"):
        pytest.skip("real mock_cli workflow lifecycle requires tmux")

    home = tmp_path / "isolated-cao-home"
    extra_env = {"CAO_WORK_LAUNCH_MODE": "legacy"}
    current_server = _start_cao_server(
        home, _pick_free_port(), extra_env=extra_env, database_targets=postgres_targets
    )
    run_id = f"lifecycle-{tier}-{uuid.uuid4().hex[:8]}"
    marker = f"workflow-result-{uuid.uuid4().hex}"
    spec_dir = home / ".aws" / "cli-agent-orchestrator" / "workflows"
    spec_dir.mkdir(parents=True, exist_ok=True)
    if tier == "yaml":
        spec_file = spec_dir / f"{run_id}.yaml"
        spec_file.write_text(
            f"name: {run_id}\nsteps:\n"
            "  - id: s1\n    provider: mock_cli\n    agent: developer\n"
            f"    prompt: {marker}\n    retries: 0\n",
            encoding="utf-8",
        )
    else:
        spec_file = spec_dir / f"{run_id}.py"
        spec_file.write_text(
            "from cao_workflow import run_step, emit_output\n"
            f"result = run_step('mock_cli', 'developer', {marker!r}, "
            "step_id='s1', timeout=30, teardown=True)\n"
            "emit_output({'echo': result.output})\n",
            encoding="utf-8",
        )

    def read_attempt():
        with connect(postgres_targets.main) as connection:
            return connection.execute(
                "SELECT run_id,step_id,state,attempts,terminal_id,call_fingerprint,result_json "
                "FROM workflow_run_step WHERE run_id=%s AND step_id='s1'",
                (run_id,),
            ).fetchone()

    try:
        submitted = requests.post(
            f"{current_server.url}/workflows/runs:submit",
            json={"name_or_path": str(spec_file), "inputs": {}, "run_id": run_id},
            timeout=10,
        )
        assert submitted.status_code == 202, submitted.text
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            result = requests.get(f"{current_server.url}/workflows/runs/{run_id}/result", timeout=5)
            if result.status_code == 200 and result.json()["state"] in {
                "completed",
                "failed",
                "cancelled",
            }:
                break
            time.sleep(0.1)
        assert result.status_code == 200, result.text
        before = result.json()
        assert before["state"] == "completed", before
        attempt = read_attempt()
        assert attempt is not None and attempt[2] == "completed"
        assert attempt[5]
        assert json.loads(attempt[6])["last_message"] == marker
        # /result intentionally retains per-step data, not run-level script output.

        # Recovery must read the frozen journal, even after the source changes.
        spec_file.write_text("source changed after completion\n", encoding="utf-8")
        current_server.stop()
        current_server = _start_cao_server(
            home, _pick_free_port(), extra_env=extra_env, database_targets=postgres_targets
        )
        recovered = requests.get(f"{current_server.url}/workflows/runs/{run_id}/result", timeout=5)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json() == before
        assert read_attempt() == attempt
    finally:
        with contextlib.suppress(Exception):
            current_server.stop()


@pytest.mark.e2e
def test_prepared_script_workflow_result_and_attempt_survive_server_restart(
    tmp_path, postgres_targets
):
    """Run an approved script through the real Docker backend and recover its receipt."""
    import os
    import re
    import subprocess
    from pathlib import Path

    if os.environ.get("CAO_T020_ACCEPTANCE") != "1":
        pytest.skip("run test/integration/t020/run-docker-acceptance.sh for Docker acceptance")

    image_id = os.environ.get("CAO_T020_DOCKER_IMAGE_ID", "")
    worker_path = os.environ.get("CAO_T020_WORKER_BINARY", "")
    worker_sha256 = os.environ.get("CAO_T020_WORKER_SHA256", "")
    docker_host = os.environ.get("DOCKER_HOST", "")
    configured_docker = os.environ.get("CAO_T020_DOCKER_CLI", "")
    assert re.fullmatch(
        r"sha256:[0-9a-f]{64}", image_id
    ), "the T020 runner must provide an immutable local worker rootfs ID"
    assert os.environ.get("CAO_WORK_DOCKER_LOCAL") == "1"
    assert os.environ.get("CAO_WORK_DOCKER_IMAGE_ID") == image_id
    assert os.environ.get("CAO_T019_DOCKER_IMAGE_ID") == image_id
    assert docker_host.startswith("unix:///")
    socket_path = Path(docker_host.removeprefix("unix://"))
    assert socket_path.is_socket(), "the T020 Docker socket must be a local Unix socket"

    docker_cli = shutil.which(configured_docker)
    assert docker_cli is not None, "the configured T020 Docker CLI is unavailable"
    worker_binary = Path(worker_path)
    worker_bytes = worker_binary.read_bytes()
    assert re.fullmatch(r"[0-9a-f]{64}", worker_sha256)
    assert hashlib.sha256(worker_bytes).hexdigest() == worker_sha256

    home = tmp_path / "isolated-cao-home"
    app_home = home / ".aws" / "cli-agent-orchestrator"
    spec_dir = app_home / "workflows"
    spec_dir.mkdir(parents=True)
    private_bin = home / "private-bin"
    private_bin.mkdir(mode=0o700)
    docker_link = private_bin / "docker"
    docker_link.symlink_to(docker_cli)
    child_path = os.pathsep.join((str(private_bin), os.environ.get("PATH", "")))
    docker_config = home / ".docker"
    docker_config.mkdir(mode=0o700)
    docker_config_file = docker_config / "config.json"
    docker_config_file.write_text('{"auths":{}}\n', encoding="utf-8")
    docker_config_file.chmod(0o600)

    docker_environment = os.environ.copy()
    docker_environment["DOCKER_HOST"] = docker_host
    docker_environment["DOCKER_CONFIG"] = str(docker_config)
    for variable in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        docker_environment.pop(variable, None)
    rootfs_inspection = subprocess.run(
        [docker_cli, "--host", docker_host, "image", "inspect", "--format", "{{.Id}}", image_id],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=docker_environment,
    )
    assert rootfs_inspection.returncode == 0, "the pinned T020 rootfs image is unavailable"
    assert rootfs_inspection.stdout.strip() == image_id

    from test.fixtures.cao_server import (
        _JWKSServer,
        _pick_free_port,
        _session_rsa_keys,
        _start_cao_server,
    )
    from test.integration.t020.test_workflow_acceptance import (
        _CONTEXT_TEXT,
        _binding_proof,
        _make_authority_and_provision,
        _receiver_receipt_proof,
        _snapshot_proof,
    )

    from authlib.jose import JsonWebToken

    from cli_agent_orchestrator.services import workflow_journal
    from cli_agent_orchestrator.services.work_scheduler import WorkScheduler
    from cli_agent_orchestrator.services.work_workflow import WorkWorkflowOrigins
    from cli_agent_orchestrator.services.work_workflow_plans import WorkWorkflowPlans

    private_pem, public_jwk = _session_rsa_keys()
    jwks = _JWKSServer(public_jwk)
    jwks.start()
    jwt = JsonWebToken(["RS256"])
    now = int(time.time())

    def mint_token(subject: str, scopes: str) -> str:
        token = jwt.encode(
            {"alg": "RS256", "kid": "test-kid"},
            {
                "iss": "https://issuer.test",
                "aud": "cao://test",
                "iat": now - 1,
                "exp": now + 600,
                "scope": scopes,
                "sub": subject,
            },
            private_pem,
        )
        return token.decode("utf-8") if isinstance(token, bytes) else token

    workflow_token = mint_token("t020-script-workflow", "cao:read cao:write")
    approval_token = mint_token("t020-separate-approver", "cao:admin")
    headers_for = lambda token: {  # noqa: E731 — one local header constructor
        "Authorization": f"Bearer {token}",
        "Host": "localhost",
    }

    extra_env = {
        "AUTH0_DOMAIN": "",
        "CAO_AUTH_JWKS_URI": jwks.url,
        "CAO_AUTH_JWKS_CACHE_TTL": "0",
        "CAO_AUTH_ISSUER": "https://issuer.test",
        "CAO_AUTH_AUDIENCE": "cao://test",
        "CAO_WORKFLOW_REQUIRE_APPROVAL": "1",
        "CAO_ENABLE_PUBLIC_WORK_INGRESS": "true",
        "CAO_WORK_DOCKER_LOCAL": "1",
        "CAO_WORK_DOCKER_IMAGE_ID": image_id,
        "CAO_T020_ACCEPTANCE": "1",
        "DOCKER_HOST": docker_host,
        "DOCKER_CONTEXT": "",
        "DOCKER_TLS_VERIFY": "",
        "DOCKER_CERT_PATH": "",
        "DOCKER_CONFIG": str(docker_config),
        "PATH": child_path,
    }
    current_server = None
    try:
        current_server = _start_cao_server(
            home, _pick_free_port(), extra_env=extra_env, database_targets=postgres_targets
        )
        repository = WorkRepository(
            current_server.database_targets.main,
            filesystem_home=app_home,
        )
        repository.initialize()
        WorkScheduler(repository).configure(
            capacity=2, max_queue=20, aging_seconds=5, expected_policy_revision=0
        )

        run_id = f"t020-script-{uuid.uuid4().hex[:16]}"
        workflow_id = f"t020_script_{uuid.uuid4().hex[:8]}"
        step_id = "worker"
        prompt = "T122 deterministic input"
        source = (
            "SCOPE = {'version': 1, 'targets': "
            "{'repo': {'agents': ['developer'], 'memory': 'exact-snapshot'}}}\n"
            "import json\n"
            "import time\n"
            "from cao_workflow import ShimHTTPError, emit_output, run_step\n"
            "deadline = time.monotonic() + 180\n"
            "while True:\n"
            "    try:\n"
            "        result = run_step(\n"
            "            'scratch_worker', 'developer', 'T122 deterministic input',\n"
            "            step_id='worker', target_key='repo', timeout=60,\n"
            "        )\n"
            "        break\n"
            "    except ShimHTTPError as error:\n"
            "        try:\n"
            "            detail = json.loads(error.body)['detail']\n"
            "        except (ValueError, TypeError, KeyError):\n"
            "            raise\n"
            "        if (error.status != 409 or not isinstance(detail, dict)\n"
            "                or detail.get('kind') != 'work_pending'):\n"
            "            raise\n"
            "        if time.monotonic() >= deadline:\n"
            "            raise\n"
            "        time.sleep(0.25)\n"
            "emit_output(result.output)\n"
        )
        source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        spec_file = spec_dir / f"{workflow_id}.py"
        spec_file.write_text(source, encoding="utf-8")

        repo_root = tmp_path / "private-git-target"
        from test.e2e.prepared_script_helpers import _initialize_git_target

        _initialize_git_target(repo_root)
        case = _make_authority_and_provision(
            repository,
            repo_root,
            tier="script",
            run_id=run_id,
            workflow_id=workflow_id,
            step_id=step_id,
            spec_hash=source_hash,
            worker_bytes=worker_bytes,
            worker_sha256=worker_sha256,
            delivery_message=prompt,
        )
        assert case["principal"].issuer == "https://issuer.test"
        assert (
            case["principal"].id
            == auth._verified_principal(
                "https://issuer.test", "t020-script-workflow", [auth.SCOPE_WRITE], "jwt"
            ).id
        )

        workflow_headers = headers_for(workflow_token)
        approval_headers = headers_for(approval_token)
        unprepared_run_id = run_id + "-unprepared"
        unprepared = requests.post(
            f"{current_server.url}/workflows/runs:submit",
            headers=workflow_headers,
            json={"name_or_path": workflow_id, "inputs": {}, "run_id": unprepared_run_id},
            timeout=10,
        )
        assert unprepared.status_code == 422, unprepared.text
        assert unprepared.json()["detail"]["kind"] == "prepared_plan_required"
        assert workflow_journal.get_run(unprepared_run_id) is None

        prepared_response = requests.post(
            f"{current_server.url}/workflows/plans:prepare",
            headers=workflow_headers,
            json={
                "name_or_path": workflow_id,
                "inputs": {},
                "target_mappings": {"repo": str(repo_root)},
                "binding_selections": {"repo": {"developer": step_id}},
            },
            timeout=30,
        )
        assert prepared_response.status_code == 201, prepared_response.text
        prepared = prepared_response.json()
        prepared_id = prepared["prepared_id"]
        plan_id = prepared["plan_id"]

        review = requests.get(
            f"{current_server.url}/workflows/plans/{prepared_id}",
            headers=workflow_headers,
            timeout=15,
        )
        assert review.status_code == 200, review.text
        assert review.json()["plan_id"] == plan_id

        denied_approval = requests.post(
            f"{current_server.url}/workflows/plans/approve",
            headers=workflow_headers,
            json={"plan_id": plan_id},
            timeout=10,
        )
        assert denied_approval.status_code == 403, denied_approval.text
        approved = requests.post(
            f"{current_server.url}/workflows/plans/approve",
            headers=approval_headers,
            json={"plan_id": plan_id},
            timeout=15,
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["approved"] is True
        assert approved.json()["plan_id"] == plan_id

        submitted = requests.post(
            f"{current_server.url}/workflows/runs:submit",
            headers=workflow_headers,
            json={
                "name_or_path": workflow_id,
                "inputs": {},
                "run_id": run_id,
                "prepared_id": prepared_id,
                "expected_plan_id": plan_id,
            },
            timeout=15,
        )
        assert submitted.status_code == 202, submitted.text
        assert submitted.json()["plan_id"] == plan_id

        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            result_response = requests.get(
                f"{current_server.url}/workflows/runs/{run_id}/result",
                headers=workflow_headers,
                timeout=10,
            )
            assert result_response.status_code == 200, result_response.text
            result_before = result_response.json()
            if result_before["state"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.25)
        assert result_before["state"] == "completed", result_before
        assert len(result_before["steps"]) == 1
        assert result_before["steps"][0]["id"] == step_id
        assert result_before["steps"][0]["state"] == "completed"
        assert result_before["steps"][0]["attempts"] == 1
        assert result_before["steps"][0]["output"]["value"] == "t122-deterministic"

        run_before = workflow_journal.get_run(run_id)
        step_before = workflow_journal.get_step(run_id, step_id)
        assert run_before is not None and run_before.tier == "script"
        assert run_before.state == "completed" and run_before.generation == "1"
        assert run_before.manifest_json and plan_id in run_before.manifest_json
        assert run_before.spec_snapshot is not None
        frozen = json.loads(run_before.spec_snapshot)
        assert frozen["source"] == source
        assert frozen["content_hash"] == source_hash
        assert step_before is not None and step_before.state == "completed"
        assert step_before.attempts == 1

        plans = WorkWorkflowPlans(repository)
        plans.initialize()
        origins = WorkWorkflowOrigins(repository, plans=plans)
        plans.origins = origins
        binding_before = origins.read_step_binding("script", run_id, 1, step_id, 1)
        assert binding_before is not None and binding_before.spec_hash == source_hash
        proof_before = _binding_proof(binding_before)
        snapshot_before = _snapshot_proof(repository, binding_before.snapshot_id)
        assert snapshot_before[2] == hashlib.sha256(snapshot_before[3]).hexdigest()
        assert snapshot_before[3] == _CONTEXT_TEXT.encode("utf-8")
        assert binding_before.snapshot_hash == snapshot_before[2]
        receiver_receipt_before = _receiver_receipt_proof(
            repository, binding_before.work_attempt_id, binding_before.work_generation
        )
        assert receiver_receipt_before is not None
        work_before = repository.get_work(binding_before.work_item_id)
        assert work_before["state"] == "succeeded"
        assert work_before["accepted_result_id"]
        assert len(work_before["attempts"]) == 1
        assert work_before["attempts"][0]["id"] == binding_before.work_attempt_id
        assert work_before["attempts"][0]["generation"] == binding_before.work_generation
        assert work_before["attempts"][0]["cleanup_state"] == "not_requested"

        from cli_agent_orchestrator.backends.docker_backend import DockerWorkBackend

        attempt_id = binding_before.work_attempt_id
        generation = binding_before.work_generation
        container_name = DockerWorkBackend._container_name(attempt_id, generation)
        attempt_image_tag = DockerWorkBackend._attempt_image_tag(attempt_id, generation)

        def inspect_docker_object(*arguments):
            return subprocess.run(
                [docker_cli, "--host", docker_host, *arguments],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                env=docker_environment,
            )

        assert inspect_docker_object("container", "inspect", container_name).returncode != 0
        assert inspect_docker_object("image", "inspect", attempt_image_tag).returncode != 0
        retained_rootfs = inspect_docker_object("image", "inspect", "--format", "{{.Id}}", image_id)
        assert retained_rootfs.returncode == 0
        assert retained_rootfs.stdout.strip() == image_id

        spec_file.write_text(
            source.replace("T122 deterministic input", "changed after run"), encoding="utf-8"
        )
        current_server.stop()
        current_server = _start_cao_server(
            home, _pick_free_port(), extra_env=extra_env, database_targets=postgres_targets
        )
        restarted_repository = WorkRepository(
            current_server.database_targets.main,
            filesystem_home=app_home,
        )
        restarted_repository.initialize()
        restarted_plans = WorkWorkflowPlans(restarted_repository)
        restarted_plans.initialize()
        restarted_origins = WorkWorkflowOrigins(restarted_repository, plans=restarted_plans)
        restarted_plans.origins = restarted_origins
        recovered_response = requests.get(
            f"{current_server.url}/workflows/runs/{run_id}/result",
            headers=workflow_headers,
            timeout=10,
        )
        assert recovered_response.status_code == 200, recovered_response.text
        assert recovered_response.json() == result_before

        run_after = workflow_journal.get_run(run_id)
        step_after = workflow_journal.get_step(run_id, step_id)
        assert run_after is not None and run_after.spec_snapshot == run_before.spec_snapshot
        assert run_after.manifest_json == run_before.manifest_json
        assert run_after.state == "completed" and run_after.generation == "1"
        assert step_after is not None and step_after.state == "completed"
        assert step_after.attempts == 1
        recovered_binding = restarted_origins.read_step_binding("script", run_id, 1, step_id, 1)
        assert recovered_binding is not None
        assert _binding_proof(recovered_binding) == proof_before
        assert (
            _snapshot_proof(restarted_repository, recovered_binding.snapshot_id) == snapshot_before
        )
        assert (
            _receiver_receipt_proof(
                restarted_repository,
                recovered_binding.work_attempt_id,
                recovered_binding.work_generation,
            )
            == receiver_receipt_before
        )
        work_after = restarted_repository.get_work(binding_before.work_item_id)
        assert work_after["state"] == "succeeded"
        assert work_after["accepted_result_id"] == work_before["accepted_result_id"]
        assert len(work_after["attempts"]) == 1
        assert work_after["attempts"][0]["id"] == binding_before.work_attempt_id
        assert work_after["attempts"][0]["generation"] == binding_before.work_generation
        assert work_after["attempts"][0]["cleanup_state"] == "not_requested"
        assert spec_file.read_text(encoding="utf-8") != frozen["source"]
    finally:
        if current_server is not None:
            with contextlib.suppress(Exception):
                current_server.stop()
        jwks.stop()
