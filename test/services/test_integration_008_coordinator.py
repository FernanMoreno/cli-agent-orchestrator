import pytest


def test_fixed_template_has_finite_reviewed_scope_and_barrier():
    from cli_agent_orchestrator.services.execution_scope import parse_scope_declaration
    from cli_agent_orchestrator.services.work_coordinator import WorkCoordinator
    from cli_agent_orchestrator.services.workflow_spec_service import _extract_inputs

    template = WorkCoordinator.template("mock_cli", "developer", memory="off")
    assert template["source_hash"]
    assert _extract_inputs(template["content"])["task_json"].type == "string"
    declaration = parse_scope_declaration(template["content"])
    assert declaration.targets[0].allowed_agent_profiles[0].value == "developer"
    assert "_read_run_capability()" in template["content"]


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(max_iterations=0),
        dict(min_iterations=3, max_iterations=2),
        dict(deadline_seconds=0),
        dict(stall_limit=0),
        dict(correction_budget=-1),
    ],
)
def test_policy_refuses_invalid_bounds(kwargs):
    from cli_agent_orchestrator.services.work_coordinator import validate_policy

    with pytest.raises(ValueError):
        validate_policy(
            task={"description": "task"},
            criteria=[{"kind": "output_equals", "path": ["answer"], "value": 42}],
            **kwargs,
        )


def test_completion_requires_real_nonempty_criteria():
    from cli_agent_orchestrator.services.work_coordinator import validate_policy

    with pytest.raises(ValueError):
        validate_policy(task={"description": "task"}, criteria=[])


from test.services.test_integration_008_prepared_plans import context, plan_context  # noqa:F401


@pytest.fixture
def coordinator_context(plan_context):
    import hashlib
    from types import SimpleNamespace

    from cli_agent_orchestrator.api.main import _compose_managed_workflow_runtime
    from cli_agent_orchestrator.services.work_coordinator import WorkCoordinator
    from cli_agent_orchestrator.services.work_origin import WorkOrigins
    from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning
    from cli_agent_orchestrator.services.workflow_continuation_driver import (
        WorkflowContinuationDriver,
    )

    old, subject, directory, c = plan_context
    c.service.workflow_origins = None
    app = SimpleNamespace(state=SimpleNamespace())
    runtime = SimpleNamespace(_admission=c.service, repository=c.repo, origins=WorkOrigins(c.repo))
    projector = _compose_managed_workflow_runtime(
        app, c.repo, SimpleNamespace(_launch_runtime_provider=SimpleNamespace(_runtime=runtime))
    )
    plans = app.state.work_workflow_origins.plans
    driver = WorkflowContinuationDriver(plans, projector, instance_id="coordinator-test")
    service = WorkCoordinator(plans, driver)
    template = service.template("mock_cli", "developer", "off", name="wf")
    (directory / "wf.py").write_text(template["content"])
    seed = WorkProvisioning(c.repo).resolve_workflow_step(
        subject,
        workflow_id="wf",
        step_id="seed",
        spec_hash=hashlib.sha256(
            "INPUTS = {}\nSCOPE = {'version': 1, 'targets': {'repo': {'agents': ['developer'], 'memory': 'off'}}}\n".encode()
        ).hexdigest(),
    )
    WorkProvisioning(c.repo).provision_workflow_step(
        c.actor,
        subject=subject,
        workflow_id="wf",
        step_id="controller-seed",
        expected_revision=0,
        spec_hash=template["source_hash"],
        job_id=seed.job_id,
        grant_id=seed.grant_id,
        grant_revision=seed.grant_revision,
        subject_ref=seed.workflow_subject_ref,
        authorization_ref=seed.workflow_authorization_ref,
        receiver_subject_ref=seed.receiver_subject_ref,
        receiver_authorization_ref=seed.receiver_authorization_ref,
        contract=seed.contract,
        delivery=seed.delivery_template,
    )
    return SimpleNamespace(
        service=service,
        driver=driver,
        subject=subject,
        context=c,
        directory=directory,
        projector=projector,
        app=app,
        runtime=runtime,
    )


def start_controller(f, max_iterations=3):
    from cli_agent_orchestrator.services import approval_store

    prepared = f.service.prepare(
        f.subject,
        "wf",
        {"description": "Return answer 42"},
        [{"kind": "output_equals", "path": ["answer"], "value": 42}],
        {"repo": str(f.context.root)},
        {"repo": {"developer": "controller-seed"}},
        scan_dir=str(f.directory),
        max_iterations=max_iterations,
    )
    approval_store.grant(prepared["plan_id"], "fixture-admin")
    return f.service.start(
        f.subject,
        prepared["prepared_id"],
        prepared["plan_id"],
        "controller-run",
        scan_dir=str(f.directory),
    )


def admit_iteration(f, iteration, generation=1):
    import asyncio

    from cli_agent_orchestrator.services import workflow_journal

    step = "iteration-" + str(iteration)
    request = dict(
        provider="mock_cli",
        agent="developer",
        prompt="Return answer 42; iteration " + str(iteration),
    )
    callback = f.service.plans.authorize_step(f.subject, "controller-run", step, "repo", request)
    now = "2026-10-02T00:00:00Z"
    workflow_journal.insert_steps("controller-run", [(step, "pending")], now)
    workflow_journal.mark_work_pending(
        run_id="controller-run",
        step_id=step,
        generation=str(generation),
        step_attempt=1,
        tier="script",
        updated_at=now,
    )
    work = asyncio.run(
        callback(
            tier="script",
            run_id="controller-run",
            run_generation=generation,
            step_id=step,
            step_attempt=1,
            prompt=request["prompt"],
        )
    )
    return work


def accept_iteration(f, answer):
    import os

    from cli_agent_orchestrator.models.work_origin import WorkflowStepResultV1

    prepared = f.context.service._prepare_dispatch(registered_only=False)
    assert prepared
    _, port, _ = prepared
    try:
        attempt = os.pread(port._attempt_credential_fd, 32, 0)
        receiver = os.pread(port._receiver_credential_fd, 32, 0)
    finally:
        port.close()
    f.runtime.origins.accept_task_received_with_credentials(
        attempt_credential=attempt, receiver_credential=receiver
    )
    f.projector.work_service.submit_workflow_step_result(
        attempt_credential=attempt,
        receiver_credential=receiver,
        result=WorkflowStepResultV1.from_payload(
            {"schema_version": 1, "status": "completed", "output": {"answer": answer}}
        ),
    )
    return attempt


def test_actual_scoped_checkpoint_is_accepted_artifact_not_completion_claim(coordinator_context):
    f = coordinator_context
    start = start_controller(f)
    epoch = f.driver.claim(f.subject, "controller-run")
    with pytest.raises(ValueError, match="accepted_artifact_required"):
        f.service.complete(f.subject, start["coordinator_id"])
    admit_iteration(f, 1)
    accept_iteration(f, 42)
    f.driver.release("controller-run", epoch)
    assert f.projector.project_pending_for_run("controller-run")[0].status == "projected"
    with f.context.repo.read_snapshot() as conn:
        event = conn.execute("SELECT * FROM workflow_continuation_outbox").fetchone()
    assert f.service.checkpoint(f.subject, "controller-run", event) is False
    status = f.service.complete(f.subject, start["coordinator_id"])
    assert status["work_verified_completed"] is True and status["iteration"] == 1
    assert any(e["accepted_result_id"] for e in status["events"] if e["kind"] == "complete")
    assert f.service.checkpoint(f.subject, "controller-run", event) is False
    assert f.service.status(f.subject, start["coordinator_id"])["iteration"] == 1


def test_same_tx_attachment_failure_leaves_no_run_or_driver(coordinator_context):
    f = coordinator_context
    from cli_agent_orchestrator.services import approval_store

    p = f.service.prepare(
        f.subject,
        "wf",
        {"description": "task"},
        [{"kind": "output_equals", "path": ["answer"], "value": 42}],
        {"repo": str(f.context.root)},
        {"repo": {"developer": "controller-seed"}},
        scan_dir=str(f.directory),
    )
    approval_store.grant(p["plan_id"], "fixture-admin")

    def refuse(*args):
        raise ValueError("binding_CAS_lost")

    with pytest.raises(ValueError, match="binding_CAS_lost"):
        f.service.start(
            f.subject,
            p["prepared_id"],
            p["plan_id"],
            "rolled-back",
            scan_dir=str(f.directory),
            attach_callback=refuse,
        )
    with f.context.repo.read_snapshot() as conn:
        for table in ("workflow_run", "workflow_driver", "workflow_coordinator"):
            assert (
                conn.execute(
                    "SELECT 1 FROM " + table + " WHERE run_id=?", ("rolled-back",)
                ).fetchone()
                is None
            )


def test_feedback_replay_conflict_and_immutable_next_iteration_context(coordinator_context):
    f = coordinator_context
    start = start_controller(f)
    f.driver.claim(f.subject, "controller-run")
    f.service.feedback(f.subject, start["coordinator_id"], "feedback-1", "Use explicit criteria")
    assert f.service.context(f.subject, "controller-run", 1)["feedback"] == [
        "Use explicit criteria"
    ]
    f.service.feedback(f.subject, start["coordinator_id"], "feedback-1", "Use explicit criteria")
    with pytest.raises(ValueError, match="request_conflict"):
        f.service.feedback(
            f.subject, start["coordinator_id"], "feedback-1", "Changed uncertain correction"
        )
    f.service.feedback(f.subject, start["coordinator_id"], "feedback-2", "Later bounded feedback")
    assert f.service.context(f.subject, "controller-run", 1)["feedback"] == [
        "Use explicit criteria"
    ]


def test_stop_does_not_claim_cessation_of_queued_work(coordinator_context):
    import asyncio

    f = coordinator_context
    start = start_controller(f)
    f.driver.claim(f.subject, "controller-run")
    admit_iteration(f, 1)
    result = asyncio.run(f.service.stop(f.subject, start["coordinator_id"]))
    assert result["state"] == "stopping"
    assert result["work_verified_completed"] is False
    with pytest.raises(ValueError, match="driver_stopping"):
        admit_iteration(f, 2)


def isolated_app(f):
    from fastapi import FastAPI

    from cli_agent_orchestrator.api.main import run_step
    from cli_agent_orchestrator.api.work_coordinator_routes import router

    app = FastAPI()
    from fastapi import HTTPException
    from fastapi.exception_handlers import http_exception_handler

    app.state.refusals = []

    @app.exception_handler(HTTPException)
    async def record_refusal(request, exc):
        app.state.refusals.append((exc.status_code, exc.detail))
        return await http_exception_handler(request, exc)

    for name, value in vars(f.app.state).items():
        setattr(app.state, name, value)
    app.state.workflow_coordinator = f.service
    app.state.workflow_continuation_driver = f.driver
    app.include_router(router)
    app.add_api_route("/terminals/run-step", run_step, methods=["POST"])
    return app


def test_configured_auth_accepts_sealed_run_capability_without_bearer(
    coordinator_context, monkeypatch
):
    from fastapi.testclient import TestClient

    from cli_agent_orchestrator.security import auth

    f = coordinator_context
    start = start_controller(f)
    f.driver.claim(f.subject, "controller-run")
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    body = {
        "provider": "mock_cli",
        "agent": "developer",
        "prompt": "Exact authorized template task",
        "step_id": "iteration-1",
        "recovery": "reconcile",
        "target_key": "repo",
        "env_vars": {
            "CAO_WORKFLOW_RUN_ID": "controller-run",
            "CAO_WORKFLOW_GENERATION": "1",
            "CAO_WORKFLOW_STEP_ID": "iteration-1",
        },
    }
    with TestClient(isolated_app(f)) as client:
        response = client.post(
            "/terminals/run-step",
            json=body,
            headers={"X-CAO-Workflow-Run-Credential": start["prepared"].run_credential},
        )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["kind"] == "work_pending"


@pytest.mark.parametrize("restart", [False, True])
def test_real_trusted_process_acceptance_automatically_continues_exact_next_step(
    coordinator_context, monkeypatch, restart
):
    import asyncio
    import socket
    import threading
    import time

    import uvicorn

    from cli_agent_orchestrator.services import script_runner, workflow_journal

    f = coordinator_context
    start = start_controller(f)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    app = isolated_app(f)
    begin = workflow_journal.begin_managed_work_step

    def trace_begin(*args, **kwargs):
        try:
            return begin(*args, **kwargs)
        except Exception as exc:
            app.state.refusals.append(("begin", str(exc), args[:4]))
            raise

    monkeypatch.setattr(workflow_journal, "begin_managed_work_step", trace_begin)
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        limit = time.monotonic() + 5
        while not server.started and time.monotonic() < limit:
            time.sleep(0.01)
        assert server.started
        monkeypatch.setattr(script_runner, "API_BASE_URL", "http://127.0.0.1:" + str(port))
        first = asyncio.run(f.driver.drive(f.subject, "controller-run", prepared=start["prepared"]))
        assert first.state.value == "running", (app.state.refusals, first.model_dump())
        assert workflow_journal.get_step("controller-run", "iteration-1").state == "work_pending"
        accept_iteration(f, 41)
        if restart:
            from types import SimpleNamespace

            from cli_agent_orchestrator.api.main import _compose_managed_workflow_runtime
            from cli_agent_orchestrator.services.work_admission import WorkAdmission
            from cli_agent_orchestrator.services.work_coordinator import WorkCoordinator
            from cli_agent_orchestrator.services.work_origin import WorkOrigins
            from cli_agent_orchestrator.services.workflow_continuation_driver import (
                WorkflowContinuationDriver,
            )

            configure_signed_owner(f, monkeypatch)
            fresh_origins = WorkOrigins(f.context.repo)
            old = f.context.service
            fresh_admission = WorkAdmission(
                f.context.repo,
                backends=dict(old.backends),
                delivery_adapters=dict(old.deliveries.adapters),
                origins=fresh_origins,
            )
            f.context.service = fresh_admission
            f.runtime = SimpleNamespace(
                _admission=fresh_admission, repository=f.context.repo, origins=fresh_origins
            )
            f.projector = _compose_managed_workflow_runtime(
                app,
                f.context.repo,
                SimpleNamespace(_launch_runtime_provider=SimpleNamespace(_runtime=f.runtime)),
            )
            plans = app.state.work_workflow_origins.plans
            f.driver = WorkflowContinuationDriver(
                plans, f.projector, instance_id="actual-restarted"
            )
            f.service = WorkCoordinator(plans, f.driver)
            app.state.workflow_coordinator = f.service
            app.state.workflow_continuation_driver = f.driver

        async def resume():
            await f.driver.tick()
            assert "controller-run" in f.driver.tasks
            resumed = await f.driver.tasks["controller-run"]
            assert resumed is not None and resumed.state.value == "running", str(app.state.refusals)

        asyncio.run(resume())
        next_step = workflow_journal.get_step("controller-run", "iteration-2")
        assert next_step is not None, (
            f.service.status(f.subject, start["coordinator_id"])["pause_reason"],
            f.service.status(f.subject, start["coordinator_id"])["escalation_reason"],
            workflow_journal.get_run("controller-run").state,
            workflow_journal.get_run("controller-run").generation,
        )
        assert next_step.state == "work_pending"
        with f.context.repo.read_snapshot() as conn:
            assert conn.execute("SELECT count(*) FROM work_items").fetchone()[0] == 2
            assert (
                conn.execute("SELECT state FROM workflow_continuation_outbox").fetchone()[0]
                == "consumed"
            )
        # Repeated wake has no new result/projection and cannot allocate a third.
        asyncio.run(f.driver.tick())
        with f.context.repo.read_snapshot() as conn:
            assert conn.execute("SELECT count(*) FROM work_items").fetchone()[0] == 2
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()


def test_read_only_owner_status_checks_actual_accepted_completion(coordinator_context):
    from cli_agent_orchestrator.security import auth

    f = coordinator_context
    start = start_controller(f)
    epoch = f.driver.claim(f.subject, "controller-run")
    admit_iteration(f, 1)
    accept_iteration(f, 42)
    f.driver.release("controller-run", epoch)
    f.projector.project_pending_for_run("controller-run")
    with f.context.repo.read_snapshot() as conn:
        event = conn.execute("SELECT * FROM workflow_continuation_outbox").fetchone()
    f.service.checkpoint(f.subject, "controller-run", event)
    reader = auth._verified_principal(
        f.subject.issuer, f.subject.subject, [auth.SCOPE_READ], f.subject.kind
    )
    assert f.service.status(reader, start["coordinator_id"])["work_verified_completed"] is True
    with pytest.raises(PermissionError):
        f.service.complete(reader, start["coordinator_id"])


def test_private_policy_cannot_be_extended_by_mutable_controller_columns(coordinator_context):
    f = coordinator_context
    start = start_controller(f)
    with f.context.repo.transaction() as conn:
        conn.execute(
            "UPDATE workflow_coordinator SET max_iterations=max_iterations+1 WHERE id=?",
            (start["coordinator_id"],),
        )
    with pytest.raises(ValueError, match="private_budget_integrity"):
        f.service.status(f.subject, start["coordinator_id"])


def test_manual_resume_pending_work_preserves_generation(coordinator_context):
    import asyncio

    from cli_agent_orchestrator.services import workflow_journal

    f = coordinator_context
    start = start_controller(f)
    epoch = f.driver.claim(f.subject, "controller-run")
    admit_iteration(f, 1)
    f.driver.release("controller-run", epoch)
    with pytest.raises(ValueError, match="coordinator_work_pending"):
        asyncio.run(f.service.resume(f.subject, start["coordinator_id"]))
    assert workflow_journal.get_run("controller-run").generation == "1"


def test_restart_authenticates_current_signed_configured_owner(coordinator_context, monkeypatch):
    import time
    from types import SimpleNamespace

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.workflow_continuation_driver import (
        DriverRefused,
        WorkflowContinuationDriver,
    )

    f = coordinator_context
    start_controller(f)
    key, claims = configure_signed_owner(f, monkeypatch)
    restarted = WorkflowContinuationDriver(f.service.plans, f.projector, instance_id="restarted")
    assert restarted.principals == {}
    owner = restarted.owner("controller-run")
    assert auth.is_verified_principal(owner) and owner.id == f.subject.id
    claims["exp"] = int(time.time()) - 1
    token = jwt.encode(claims, key, algorithm="RS256")
    monkeypatch.setattr(auth, "get_local_bearer", lambda: token)
    with pytest.raises(DriverRefused, match="owner_authentication_required"):
        restarted.owner("controller-run")


def test_unknown_allocation_blocks_new_driver_even_after_lease_expiry(coordinator_context):
    from cli_agent_orchestrator.services.workflow_continuation_driver import (
        DriverRefused,
        WorkflowContinuationDriver,
    )

    f = coordinator_context
    start_controller(f)
    epoch = f.driver.claim(f.subject, "controller-run")
    f.driver.begin_process("controller-run", epoch)
    with f.context.repo.transaction() as conn:
        conn.execute(
            "UPDATE workflow_driver SET lease_expires_at=0 WHERE run_id=?", ("controller-run",)
        )
    other = WorkflowContinuationDriver(f.service.plans, f.projector, instance_id="other")
    with pytest.raises(DriverRefused, match="orphan_process"):
        other.claim(f.subject, "controller-run")


def configure_signed_owner(f, monkeypatch):
    import time
    from types import SimpleNamespace

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    from cli_agent_orchestrator.security import auth

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = {
        "iss": f.subject.issuer,
        "sub": f.subject.subject,
        "aud": "cao-test",
        "scope": auth.SCOPE_WRITE,
        "exp": int(time.time()) + 60,
    }
    token = jwt.encode(claims, key, algorithm="RS256")
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(auth, "get_jwks_uri", lambda: "https://idp.invalid/jwks")
    monkeypatch.setattr(auth, "get_authorization_servers", lambda: [f.subject.issuer])
    monkeypatch.setattr(auth, "get_expected_audience", lambda: "cao-test")
    monkeypatch.setattr(
        auth._jwks_cache,
        "get_client",
        lambda uri: SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key())
        ),
    )
    monkeypatch.setattr(auth, "get_local_bearer", lambda: token)
    return key, claims


def test_deadline_fences_pending_run_without_fabricating_stop(coordinator_context):
    import asyncio
    import time

    f = coordinator_context
    start = start_controller(f)
    epoch = f.driver.claim(f.subject, "controller-run")
    admit_iteration(f, 1)
    f.driver.release("controller-run", epoch)
    with f.context.repo.transaction() as conn:
        conn.execute(
            "UPDATE workflow_coordinator SET deadline=? WHERE id=?",
            (time.time() - 1, start["coordinator_id"]),
        )
    asyncio.run(f.driver.tick())
    value = f.service.status(f.subject, start["coordinator_id"])
    assert value["state"] == "stopping" and value["escalation_reason"] == "deadline"
    assert value["work_verified_completed"] is False


def test_revoked_origin_after_acceptance_cannot_allocate_next_iteration(coordinator_context):
    import asyncio

    from cli_agent_orchestrator.services.work_origin import WorkOriginAuthority

    f = coordinator_context
    start = start_controller(f)
    epoch = f.driver.claim(f.subject, "controller-run")
    admit_iteration(f, 1)
    accept_iteration(f, 41)
    f.driver.release("controller-run", epoch)
    WorkOriginAuthority(f.context.repo).revoke(
        owner=f.context.actor, subject=f.subject, origin_kind="workflow", expected_revision=1
    )
    asyncio.run(f.driver.tick())
    with f.context.repo.read_snapshot() as conn:
        assert conn.execute("SELECT count(*) FROM work_items").fetchone()[0] == 1
        assert conn.execute("SELECT state FROM workflow_driver").fetchone()[0] == "paused"
    assert f.service.status(f.subject, start["coordinator_id"])["work_verified_completed"] is False


def test_schema_drift_refuses_before_driver_claim(coordinator_context):
    f = coordinator_context
    start_controller(f)
    with f.context.repo.transaction() as conn:
        conn.execute("DROP TRIGGER coordinator_events_no_update")
    with pytest.raises(ValueError, match="continuation_schema_integrity"):
        f.driver.claim(f.subject, "controller-run")


def test_file_criterion_does_not_broaden_existing_tool_grant(coordinator_context):
    f = coordinator_context
    start_controller(f)
    from types import SimpleNamespace

    policy = {
        "criteria": [
            {"kind": "file_contains", "path": "artifact.txt", "value": "done", "tool": "fs_read"}
        ]
    }
    (f.context.root / "artifact.txt").write_text("done")
    with pytest.raises(PermissionError, match="file_read_not_approved"):
        f.service._criteria(
            f.subject, "controller-run", policy, SimpleNamespace(result=SimpleNamespace(output={}))
        )


@pytest.mark.parametrize(
    "minimum,answers,expected", [(2, [42, 42], "completed"), (1, [41, 41], "escalated")]
)
def test_finite_minimum_and_maximum_use_accepted_artifacts(
    coordinator_context, minimum, answers, expected
):
    f = coordinator_context
    from cli_agent_orchestrator.services import approval_store

    p = f.service.prepare(
        f.subject,
        "wf",
        {"description": "task"},
        [{"kind": "output_equals", "path": ["answer"], "value": 42}],
        {"repo": str(f.context.root)},
        {"repo": {"developer": "controller-seed"}},
        scan_dir=str(f.directory),
        min_iterations=minimum,
        max_iterations=2,
    )
    approval_store.grant(p["plan_id"], "fixture-admin")
    value = f.service.start(
        f.subject, p["prepared_id"], p["plan_id"], "controller-run", scan_dir=str(f.directory)
    )
    for iteration, answer in enumerate(answers, 1):
        epoch = f.driver.claim(f.subject, "controller-run")
        admit_iteration(f, iteration)
        accept_iteration(f, answer)
        f.driver.release("controller-run", epoch)
        f.projector.project_pending_for_run("controller-run")
        with f.context.repo.read_snapshot() as conn:
            event = conn.execute(
                "SELECT * FROM workflow_continuation_outbox WHERE step_id=?",
                ("iteration-" + str(iteration),),
            ).fetchone()
        continued = f.service.checkpoint(f.subject, "controller-run", event)
        assert continued is (iteration < len(answers))
        if continued:
            # AdmissionOnlyBackend never allocates a process. Its stopped proof
            # releases the real scheduler/path reservation between iterations.
            from cli_agent_orchestrator.services.work_scheduler import StoppedWriter, WorkScheduler

            with f.context.repo.read_snapshot() as conn:
                attempt_id = conn.execute(
                    "SELECT work_attempt_id FROM work_workflow_step_bindings WHERE step_id=?",
                    ("iteration-" + str(iteration),),
                ).fetchone()[0]
            f.projector.work_service.cleanup_attempt(
                attempt_id, lambda: True, actor_id=f.context.actor.id
            )
            scheduler = WorkScheduler(
                f.context.repo,
                stop_verifier=lambda owner: StoppedWriter(
                    owner.attempt_id,
                    owner.generation,
                    owner.revision,
                    "admission-only backend: no process allocated",
                ),
            )
            reservation = scheduler.get_by_attempt(attempt_id)
            attempt = f.context.repo.get_attempt(attempt_id)
            scheduler.release(
                reservation.id,
                generation=reservation.generation,
                expected_revision=reservation.revision,
                expected_attempt_revision=attempt["revision"],
                actor_id=f.context.actor.id,
            )
            from cli_agent_orchestrator.services.work_reservations import WorkReservations

            with f.context.repo.read_snapshot() as conn:
                path_id = conn.execute(
                    "SELECT id FROM work_reservation_sets WHERE attempt_id=?", (attempt_id,)
                ).fetchone()[0]
            paths = WorkReservations(
                f.context.repo,
                stop_verifier=lambda owner: StoppedWriter(
                    owner.attempt_id,
                    owner.generation,
                    owner.revision,
                    "admission-only backend: no process allocated",
                ),
            )
            path_set = paths.get(path_id)
            paths.release(
                path_id,
                generation=path_set.generation,
                expected_revision=path_set.revision,
                expected_attempt_revision=attempt["revision"],
                actor_id=f.context.actor.id,
            )
    status = f.service.status(f.subject, value["coordinator_id"])
    assert status["state"] == expected
    assert status["work_verified_completed"] is (expected == "completed")


def test_approved_file_criterion_refuses_fifo_without_blocking(coordinator_context, monkeypatch):
    import os
    from test.services.test_integration_008_continuation_review import file_controller
    from types import SimpleNamespace

    f = coordinator_context
    _, artifact, _ = file_controller(f, monkeypatch)
    artifact.unlink()
    os.mkfifo(artifact)
    policy = {
        "criteria": [
            {"kind": "file_sha256", "path": artifact.name, "value": "0" * 64, "tool": "fs_read"}
        ]
    }
    with pytest.raises(ValueError, match="artifact_not_regular"):
        f.service._criteria(
            f.subject, "controller-run", policy, SimpleNamespace(result=SimpleNamespace(output={}))
        )


def test_shutdown_fences_owned_admission_before_process_teardown(coordinator_context):
    import asyncio

    from cli_agent_orchestrator.services.workflow_continuation_driver import DriverRefused

    f = coordinator_context
    start_controller(f)
    epoch = f.driver.claim(f.subject, "controller-run")
    asyncio.run(f.driver.shutdown())
    with pytest.raises(DriverRefused, match="driver_fence_changed"):
        f.driver.assert_current("controller-run", epoch)


@pytest.mark.parametrize("bad_generation,bad_token", [("01", False), ("0", False), ("1", True)])
def test_sealed_run_scope_refuses_bad_identity_without_bearer_fallback(
    coordinator_context, monkeypatch, bad_generation, bad_token
):
    from fastapi.testclient import TestClient

    from cli_agent_orchestrator.security import auth

    f = coordinator_context
    start = start_controller(f)
    f.driver.claim(f.subject, "controller-run")
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)

    async def forbidden_fallback(*args, **kwargs):
        raise AssertionError("sealed header must not fall back to bearer")

    monkeypatch.setattr(auth, "get_current_scopes", forbidden_fallback)
    token = "invalid-capability" if bad_token else start["prepared"].run_credential
    body = {
        "provider": "mock_cli",
        "agent": "developer",
        "prompt": "authorized task",
        "step_id": "iteration-1",
        "recovery": "reconcile",
        "target_key": "repo",
        "env_vars": {
            "CAO_WORKFLOW_RUN_ID": "controller-run",
            "CAO_WORKFLOW_GENERATION": bad_generation,
            "CAO_WORKFLOW_STEP_ID": "iteration-1",
        },
    }
    with TestClient(isolated_app(f)) as client:
        response = client.post(
            "/terminals/run-step",
            json=body,
            headers={"X-CAO-Workflow-Run-Credential": token, "Authorization": "Bearer ignored"},
        )
    assert response.status_code == 401, response.text
    with f.context.repo.read_snapshot() as conn:
        assert conn.execute("SELECT count(*) FROM work_items").fetchone()[0] == 0
