"""Independent C07 proofs through actual scoped Work, SQLite and owned Python."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from test.services.test_integration_008_coordinator import (
    accept_iteration,
    admit_iteration,
    configure_signed_owner,
    context,
    coordinator_context,
    isolated_app,
    plan_context,
    start_controller,
)
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services import script_runner, workflow_journal
from cli_agent_orchestrator.services.work_coordinator import WorkCoordinator
from cli_agent_orchestrator.services.workflow_continuation_driver import (
    DriverRefused,
    WorkflowContinuationDriver,
)


@contextmanager
def running_api(f, monkeypatch):
    import uvicorn

    app = isolated_app(f)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        limit = time.monotonic() + 5
        while not server.started and time.monotonic() < limit:
            time.sleep(0.01)
        assert server.started
        monkeypatch.setattr(script_runner, "API_BASE_URL", "http://127.0.0.1:" + str(port))
        yield app
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()


def fresh_driver(f, app):
    from cli_agent_orchestrator.api.main import _compose_managed_workflow_runtime
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.services.work_admission import WorkAdmission
    from cli_agent_orchestrator.services.work_origin import WorkOrigins

    repository = WorkRepository(f.context.repo.path)
    origins = WorkOrigins(repository)
    old = f.context.service
    admission = WorkAdmission(
        repository,
        backends=dict(old.backends),
        delivery_adapters=dict(old.deliveries.adapters),
        origins=origins,
    )
    runtime = SimpleNamespace(_admission=admission, repository=repository, origins=origins)
    projector = _compose_managed_workflow_runtime(
        app, repository, SimpleNamespace(_launch_runtime_provider=SimpleNamespace(_runtime=runtime))
    )
    plans = app.state.work_workflow_origins.plans
    driver = WorkflowContinuationDriver(plans, projector, instance_id="independent-new-reader")
    service = WorkCoordinator(plans, driver)
    app.state.workflow_coordinator = service
    app.state.workflow_continuation_driver = driver
    f.context.service = admission
    f.runtime = runtime
    f.projector = projector
    return driver, service


def observe_private_fd_environment(process, credential, *, timeout=1.0):
    """Wait only for a live child's transiently empty /proc environment."""
    deadline = time.monotonic() + timeout
    path = Path("/proc", str(process.pid), "environ")
    while True:
        assert process.returncode is None, "child exited before environment observation"
        try:
            environ = path.read_bytes()
        except FileNotFoundError:
            raise AssertionError("child disappeared before environment observation") from None
        if environ:
            # Explicit raises keep pytest's assertion introspection from printing
            # environment bytes or the credential when a security check fails.
            if credential.encode() in environ:
                raise AssertionError("raw credential present in child environment")
            if b"CAO_WORKFLOW_AUTH_FD=" not in environ:
                raise AssertionError("private credential FD marker missing")
            return
        assert time.monotonic() < deadline, "child environment remained unobservable"
        time.sleep(0.01)


@pytest.mark.parametrize("failure", ["empty", "missing_marker", "raw_credential", "exited"])
def test_private_fd_observation_refuses_unverifiable_child(failure, monkeypatch):
    credential = "synthetic-private-capability-for-test-only"
    env = {"CAO_WORKFLOW_AUTH_FD": "0"}
    expected = "environment remained unobservable"
    if failure == "missing_marker":
        env = {"OTHER": "value"}
        expected = "FD marker missing"
    elif failure == "raw_credential":
        env["UNSAFE_CREDENTIAL"] = credential
        expected = "raw credential present"
    elif failure == "exited":
        expected = "child exited"
    with subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE,
        env=env,
    ) as process:
        try:
            if failure == "empty":
                read_bytes = Path.read_bytes

                def empty_environment(path):
                    if path == Path("/proc", str(process.pid), "environ"):
                        return b""
                    return read_bytes(path)

                monkeypatch.setattr(Path, "read_bytes", empty_environment)
            elif failure == "exited":
                process.stdin.close()
                process.wait(timeout=3)
            with pytest.raises(AssertionError, match=expected):
                observe_private_fd_environment(process, credential, timeout=0.03)
            if failure != "exited":
                assert process.poll() is None
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=3)


@pytest.mark.parametrize("transient_empty_environment", [False, True])
def test_real_private_fd_barrier_records_process_before_any_work_effect(
    coordinator_context, monkeypatch, transient_empty_environment
):
    f = coordinator_context
    started = start_controller(f)
    entered, release = threading.Event(), threading.Event()
    original = f.driver.attach_process
    observed = []

    if transient_empty_environment:
        read_bytes = Path.read_bytes
        empty_reads = 2

        def delayed_environment(path):
            nonlocal empty_reads
            if path.parent.parent == Path("/proc") and path.name == "environ" and empty_reads:
                empty_reads -= 1
                return b""
            return read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", delayed_environment)

    def pause_attach(run, epoch, pid):
        from cli_agent_orchestrator.services import workflow_service

        process = workflow_service.run_registry[run].process
        observe_private_fd_environment(process, started["prepared"].run_credential)
        observed.append(pid)
        entered.set()
        assert release.wait(4)
        return original(run, epoch, pid)

    monkeypatch.setattr(f.driver, "attach_process", pause_attach)
    with running_api(f, monkeypatch):

        async def drive():
            task = asyncio.create_task(
                f.driver.drive(f.subject, "controller-run", prepared=started["prepared"])
            )
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                with f.context.repo.read_snapshot() as conn:
                    assert json.loads(
                        conn.execute(
                            "SELECT process_identity_json FROM workflow_driver"
                        ).fetchone()[0]
                    ) == {"allocation_pending": True}
                    assert (
                        conn.execute(
                            "SELECT count(*) FROM work_workflow_step_bindings WHERE run_id='controller-run'"
                        ).fetchone()[0]
                        == 0
                    )
            finally:
                release.set()
            return await task

        result = asyncio.run(drive())
    assert result.state.value == "running"
    assert workflow_journal.get_step("controller-run", "iteration-1").state == "work_pending"
    with f.context.repo.read_snapshot() as conn:
        assert (
            conn.execute("SELECT process_identity_json FROM workflow_driver").fetchone()[0] is None
        )
        assert (
            conn.execute("SELECT stop_evidence_ref FROM workflow_driver")
            .fetchone()[0]
            .startswith("owned-process-reaped:")
        )
    assert not __import__("pathlib").Path("/proc", str(observed[0])).exists()


def test_real_process_restart_signed_owner_duplicate_wake_never_admits_third_work(
    coordinator_context, monkeypatch
):
    f = coordinator_context
    started = start_controller(f)
    with running_api(f, monkeypatch) as app:
        assert (
            asyncio.run(
                f.driver.drive(f.subject, "controller-run", prepared=started["prepared"])
            ).state.value
            == "running"
        )
        accept_iteration(f, 41)
        configure_signed_owner(f, monkeypatch)
        driver, service = fresh_driver(f, app)
        assert driver.principals == {}
        assert driver.owner("controller-run").id == f.subject.id

        async def continue_once():
            await driver.tick()
            assert "controller-run" in driver.tasks
            result = await driver.tasks["controller-run"]
            assert result.state.value == "running"
            await driver.tick()
            await driver.tick()

        asyncio.run(continue_once())
        assert workflow_journal.get_run("controller-run").generation == "2"
        assert workflow_journal.get_step("controller-run", "iteration-2").state == "work_pending"
        with f.context.repo.read_snapshot() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM work_workflow_step_bindings WHERE run_id='controller-run'"
                ).fetchone()[0]
                == 2
            )
            assert (
                conn.execute("SELECT count(*) FROM workflow_continuation_outbox").fetchone()[0] == 1
            )
            assert (
                conn.execute("SELECT state FROM workflow_continuation_outbox").fetchone()[0]
                == "consumed"
            )
        # A duplicate actual projection is a no-op, not a new wake or paste.
        assert all(
            item.status == "result_pending"
            for item in f.projector.project_pending_for_run("controller-run")
        )
        asyncio.run(driver.tick())
        with f.context.repo.read_snapshot() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM work_workflow_step_bindings WHERE run_id='controller-run'"
                ).fetchone()[0]
                == 2
            )


def test_other_authenticated_observer_tick_cannot_revoke_current_active_driver_lease(
    coordinator_context, monkeypatch
):
    f = coordinator_context
    start_controller(f)
    epoch = f.driver.claim(f.subject, "controller-run")
    admit_iteration(f, 1)
    accept_iteration(f, 41)
    f.projector.project_pending_for_run("controller-run")
    configure_signed_owner(f, monkeypatch)
    other = WorkflowContinuationDriver(f.service.plans, f.projector, instance_id="observer")
    WorkCoordinator(f.service.plans, other)
    with f.context.repo.read_snapshot() as conn:
        before = dict(conn.execute("SELECT * FROM workflow_driver").fetchone())
    asyncio.run(other.tick())
    with f.context.repo.read_snapshot() as conn:
        after = dict(conn.execute("SELECT * FROM workflow_driver").fetchone())
    assert (after["state"], after["owner_instance"], after["epoch"], after["lease_expires_at"]) == (
        before["state"],
        before["owner_instance"],
        before["epoch"],
        before["lease_expires_at"],
    )
    f.driver.assert_current("controller-run", epoch)


def file_controller(f, monkeypatch):
    import hashlib

    from cli_agent_orchestrator.models.work_contract import ContractPermissions, ContractSnapshot
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services import approval_store
    from cli_agent_orchestrator.services.delegation_snapshot import (
        DelegationSnapshots,
        ResolvedSnapshot,
    )
    from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
    from cli_agent_orchestrator.services.work_authority import Permissions
    from cli_agent_orchestrator.services.work_origin import WorkOriginAuthority
    from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning

    c = f.context
    provision = WorkProvisioning(c.repo)
    template = f.service.template("mock_cli", "developer", "off", name="wf")
    seed = provision.resolve_workflow_step(
        f.subject, workflow_id="wf", step_id="controller-seed", spec_hash=template["source_hash"]
    )
    # A real administrator issues this test's separate bounded fs_read grant;
    # production coordinator never creates grants or expands profile authority.
    job = c.repo.create_job(
        project_id="project",
        principal_id=c.actor.id,
        allowed_providers=["mock_cli"],
        grant_id="review-file-root",
        budget={"scheduler_units": 100},
    )
    permission = Permissions(tools={"knowledge.read", "fs_read"}, paths={str(c.root)})
    root = c.control.issue_root(
        c.actor,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=permission,
        expires_at=time.time() + 600,
    )
    grant = c.control.delegate(
        c.actor,
        parent_grant_id=root.id,
        expected_parent_revision=1,
        child_principal=f.subject,
        providers={"mock_cli"},
        permissions=permission,
        expires_at=time.time() + 500,
    )
    authority = WorkOriginAuthority(c.repo)
    authorization = authority.authorize(
        c.actor,
        subject=f.subject,
        origin_kind="workflow",
        grant_id=grant.id,
        grant_revision=1,
        actions={"admit_step", "execute"},
        expires_at=time.time() + 400,
        expected_revision=1,
    )
    receiver = auth._verified_principal("issuer", "review-file-receiver", [auth.SCOPE_WRITE], "jwt")
    rgrant = c.control.delegate(
        c.actor,
        parent_grant_id=root.id,
        expected_parent_revision=1,
        child_principal=receiver,
        providers={"mock_cli"},
        permissions=permission,
        expires_at=time.time() + 500,
    )
    rref = authority.register_subject(
        c.actor,
        verified_subject=receiver,
        kind="receiver",
        issuer_id=c.actor.id,
        expected_revision=0,
    )
    rauth = authority.authorize(
        c.actor,
        subject=receiver,
        origin_kind="receiver",
        grant_id=rgrant.id,
        grant_revision=1,
        actions={"task_received", "task_result"},
        expires_at=time.time() + 400,
        expected_revision=0,
    )
    frozen = DelegationSnapshots(
        c.repo, policy=KnowledgePolicy(c.repo, job["id"], grant.id, 1)
    ).freeze(
        principal=f.subject,
        job_id=job["id"],
        contract_id="review-file-contract",
        binding_key="review-file-empty",
        request_hash=hashlib.sha256(b"review-file-empty").hexdigest(),
        scope="project",
        scope_id="project",
        resolver=lambda conn, p: ResolvedSnapshot(""),
    )
    contract = seed.contract.model_copy(
        update={
            "id": "review-file-contract",
            "permissions": ContractPermissions(tools=("fs_read",), paths=(str(c.root),)),
            "snapshot": ContractSnapshot(
                state="present", id=frozen.id, delivered_hash=frozen.delivered_hash
            ),
        }
    )
    provision.provision_workflow_step(
        c.actor,
        subject=f.subject,
        workflow_id="wf",
        step_id="file-controller",
        expected_revision=0,
        spec_hash=template["source_hash"],
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=1,
        subject_ref=seed.workflow_subject_ref,
        authorization_ref=authorization.ref,
        receiver_subject_ref=rref,
        receiver_authorization_ref=rauth.ref,
        contract=contract,
        delivery=seed.delivery_template,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.utils.agent_profiles._read_agent_profile_source",
        lambda name: "---\nname: developer\nallowedTools: [fs_read]\n---\nExplicitly approved read-only file check\n",
    )
    artifact = c.root / "answer.txt"
    artifact.write_text("verified answer 42")
    prepared = f.service.prepare(
        f.subject,
        "wf",
        {"description": "Return answer and preserve file"},
        [
            {
                "kind": "file_sha256",
                "path": "answer.txt",
                "value": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                "tool": "fs_read",
            }
        ],
        {"repo": str(c.root)},
        {"repo": {"developer": "file-controller"}},
        scan_dir=str(f.directory),
    )
    approval_store.grant(prepared["plan_id"], "fixture-admin")
    return (
        f.service.start(
            f.subject,
            prepared["prepared_id"],
            prepared["plan_id"],
            "controller-run",
            scan_dir=str(f.directory),
        ),
        artifact,
        grant,
    )


def test_actual_approved_file_criteria_revalidate_current_bytes_not_completion_metadata(
    coordinator_context, monkeypatch
):
    f = coordinator_context
    started, artifact, grant = file_controller(f, monkeypatch)
    epoch = f.driver.claim(f.subject, "controller-run")
    admit_iteration(f, 1)
    accept_iteration(f, 42)
    f.driver.release("controller-run", epoch)
    f.projector.project_pending_for_run("controller-run")
    with f.context.repo.read_snapshot() as conn:
        event = conn.execute("SELECT * FROM workflow_continuation_outbox").fetchone()
    assert f.service.checkpoint(f.subject, "controller-run", event) is False
    assert (
        f.service.complete(f.subject, started["coordinator_id"])["work_verified_completed"] is True
    )
    artifact.write_text("changed after completion")
    with pytest.raises(ValueError, match="criteria_unsatisfied"):
        f.service.complete(f.subject, started["coordinator_id"])


@pytest.mark.parametrize("kind", ["origin", "grant"])
def test_actual_native_accepted_history_survives_revoke_without_next_effect(
    coordinator_context, monkeypatch, kind
):
    from cli_agent_orchestrator.services.work_authority import WorkAuthority
    from cli_agent_orchestrator.services.work_origin import WorkOriginAuthority

    f = coordinator_context
    started = start_controller(f)
    with running_api(f, monkeypatch):
        assert (
            asyncio.run(
                f.driver.drive(f.subject, "controller-run", prepared=started["prepared"])
            ).state.value
            == "running"
        )
        accept_iteration(f, 41)
        binding = f.service.plans.origins.read_step_binding(
            "script", "controller-run", 1, "iteration-1", 1, historical=True
        )
        accepted = f.service.result_service.read_accepted_workflow_result(binding)
        if kind == "origin":
            WorkOriginAuthority(f.context.repo).revoke(
                owner=f.context.actor,
                subject=f.subject,
                origin_kind="workflow",
                expected_revision=1,
            )
        else:
            WorkAuthority(f.context.repo).revoke(
                f.context.actor,
                grant_id=binding.grant_id,
                expected_grant_revision=binding.grant_revision,
                reason="independent current grant refusal",
            )
        before = list(f.context.service.backends["test"].effects)
        asyncio.run(f.driver.tick())
        assert f.service.result_service.read_accepted_workflow_result(binding) == accepted
        assert f.context.service.backends["test"].effects == before
        assert workflow_journal.get_step("controller-run", "iteration-2") is None
        assert workflow_journal.get_run("controller-run").generation == "1"
        with f.context.repo.read_snapshot() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM work_workflow_step_bindings WHERE run_id='controller-run'"
                ).fetchone()[0]
                == 1
            )
            assert conn.execute("SELECT state FROM workflow_driver").fetchone()[0] == "paused"
            assert (
                conn.execute("SELECT count(*) FROM workflow_continuation_outbox").fetchone()[0] == 1
            )


def test_actual_live_unattached_process_never_reclaimed_by_expired_lease(
    coordinator_context, monkeypatch
):
    f = coordinator_context
    started = start_controller(f)
    entered, release = threading.Event(), threading.Event()
    original = f.driver.attach_process
    observed = []

    def pause_attach(*args):
        observed.append(args)
        entered.set()
        assert release.wait(4)
        return original(*args)

    monkeypatch.setattr(f.driver, "attach_process", pause_attach)
    with running_api(f, monkeypatch):

        async def race():
            task = asyncio.create_task(
                f.driver.drive(f.subject, "controller-run", prepared=started["prepared"])
            )
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                with f.context.repo.transaction() as conn:
                    expiry = conn.execute(
                        "SELECT lease_expires_at FROM workflow_driver"
                    ).fetchone()[0]
                    conn.execute("UPDATE workflow_driver SET lease_expires_at=0")
                other = WorkflowContinuationDriver(
                    f.service.plans, f.projector, instance_id="replacement"
                )
                with pytest.raises(DriverRefused, match="orphan_process"):
                    other.claim(f.subject, "controller-run")
                assert __import__("pathlib").Path("/proc", str(observed[0][2])).exists()
                with f.context.repo.transaction() as conn:
                    assert (
                        conn.execute("SELECT owner_instance FROM workflow_driver").fetchone()[0]
                        == f.driver.instance_id
                    )
                    assert (
                        conn.execute(
                            "SELECT count(*) FROM work_workflow_step_bindings WHERE run_id='controller-run'"
                        ).fetchone()[0]
                        == 0
                    )
                    conn.execute("UPDATE workflow_driver SET lease_expires_at=?", (expiry,))
            finally:
                release.set()
            return await task

        assert asyncio.run(race()).state.value == "running"


def test_signed_read_only_history_verifies_actual_bytes_after_origin_revoke(
    coordinator_context, monkeypatch
):
    import jwt

    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.work_origin import WorkOriginAuthority

    f = coordinator_context
    started = start_controller(f)
    epoch = f.driver.claim(f.subject, "controller-run")
    admit_iteration(f, 1)
    accept_iteration(f, 42)
    f.driver.release("controller-run", epoch)
    f.projector.project_pending_for_run("controller-run")
    with f.context.repo.read_snapshot() as conn:
        event = conn.execute("SELECT * FROM workflow_continuation_outbox").fetchone()
    f.service.checkpoint(f.subject, "controller-run", event)
    WorkOriginAuthority(f.context.repo).revoke(
        owner=f.context.actor, subject=f.subject, origin_kind="workflow", expected_revision=1
    )
    key, claims = configure_signed_owner(f, monkeypatch)
    claims["scope"] = auth.SCOPE_READ
    reader = auth.principal_from_token(jwt.encode(claims, key, algorithm="RS256"))
    assert reader.id == f.subject.id and reader.scopes == frozenset({auth.SCOPE_READ})
    assert f.service.status(reader, started["coordinator_id"])["work_verified_completed"] is True
    with pytest.raises(PermissionError):
        f.service.complete(reader, started["coordinator_id"])
    with f.context.repo.read_snapshot() as conn:
        location = conn.execute("SELECT immutable_location FROM work_results").fetchone()[0]
    (f.service.result_service.artifacts.root / location).write_bytes(b"corrupt accepted bytes")
    refused = f.service.status(reader, started["coordinator_id"])
    assert refused["work_verified_completed"] is False
    assert refused["completion_evidence_state"] == "validation_refused"


def test_actual_approved_file_criterion_refuses_fifo_with_bounded_owned_process(
    coordinator_context, monkeypatch
):
    import multiprocessing

    f = coordinator_context
    started, artifact, _ = file_controller(f, monkeypatch)
    epoch = f.driver.claim(f.subject, "controller-run")
    admit_iteration(f, 1)
    accept_iteration(f, 42)
    f.driver.release("controller-run", epoch)
    f.projector.project_pending_for_run("controller-run")
    with f.context.repo.read_snapshot() as conn:
        event = dict(conn.execute("SELECT * FROM workflow_continuation_outbox").fetchone())
    artifact.unlink()
    os.mkfifo(artifact, 0o600)
    fork = multiprocessing.get_context("fork")
    queue = fork.Queue()

    def check():
        try:
            f.service.checkpoint(f.subject, "controller-run", event)
        except ValueError as error:
            queue.put(str(error))
        else:
            queue.put("incorrect success")

    process = fork.Process(target=check)
    process.start()
    try:
        process.join(3)
        assert not process.is_alive(), "Actual authorized FIFO criterion blocked the owned reader"
        assert process.exitcode == 0
        assert "regular" in queue.get(timeout=1)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(3)
        queue.close()
    with f.context.repo.read_snapshot() as conn:
        assert conn.execute("SELECT state FROM workflow_coordinator").fetchone()[0] != "completed"


def test_actual_owned_python_exit_and_late_success_are_not_work_cleanup_proof(
    coordinator_context, monkeypatch
):
    f = coordinator_context
    started = start_controller(f)
    with running_api(f, monkeypatch):
        assert (
            asyncio.run(
                f.driver.drive(f.subject, "controller-run", prepared=started["prepared"])
            ).state.value
            == "running"
        )
        with f.context.repo.read_snapshot() as conn:
            assert (
                conn.execute("SELECT process_identity_json FROM workflow_driver").fetchone()[0]
                is None
            )
            work = conn.execute(
                "SELECT work_item_id,work_attempt_id FROM work_workflow_step_bindings WHERE run_id='controller-run'"
            ).fetchone()
        stopped = asyncio.run(f.service.stop(f.subject, started["coordinator_id"]))
        assert stopped["state"] == "stopping" and not stopped["work_verified_completed"]
        # Exact original accepted result remains legitimate historical evidence,
        # but neither a Python exit nor success asserts physical worker teardown.
        accept_iteration(f, 42)
        f.projector.project_pending_for_run("controller-run")
        status = f.service.reconcile_stop(f.subject, started["coordinator_id"])
        assert status["state"] == "stopping" and not status["work_verified_completed"]
        with f.context.repo.read_snapshot() as conn:
            assert (
                conn.execute("SELECT state FROM work_items WHERE id=?", (work[0],)).fetchone()[0]
                == "succeeded"
            )
            assert (
                conn.execute(
                    "SELECT cleanup_state FROM work_attempts WHERE id=?", (work[1],)
                ).fetchone()[0]
                != "complete"
            )
            assert (
                conn.execute("SELECT count(*) FROM workflow_continuation_outbox").fetchone()[0] == 0
            )
            assert (
                conn.execute(
                    "SELECT count(*) FROM work_workflow_step_bindings WHERE run_id='controller-run'"
                ).fetchone()[0]
                == 1
            )
        asyncio.run(f.driver.tick())
        assert workflow_journal.get_step("controller-run", "iteration-2") is None
