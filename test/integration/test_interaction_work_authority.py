"""Work ownership cannot be hidden by declaring a terminal interaction local.

Admission, exact target binding, grants and PostgreSQL persistence are production
services. The transport backend records a send; it runs no provider/process.
"""

import time
from test.integration.test_work_delivery import target_envelope, targeted_registry
from test.integration.test_work_dispatch import AdmissionOnlyBackend, admit
from test.postgres.interactions import postgres_interaction_store
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.interactions.models import (
    CreateGroupRequest,
    InteractionAnswerPayload,
    InteractionError,
    InteractionForbidden,
    QuestionInput,
    WorkSourceBinding,
)
from cli_agent_orchestrator.services.work_admission import WorkAdmission
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_scheduler import WorkScheduler

pytestmark = pytest.mark.postgres


@pytest.fixture
def interaction_work_context(tmp_path, postgres_interaction_store):
    repository = postgres_interaction_store.work_repository
    WorkScheduler(repository).configure(
        capacity=2, max_queue=100, aging_seconds=10, expected_policy_revision=0
    )
    actor = auth._verified_principal("issuer", "interaction-work-owner", [auth.SCOPE_ADMIN], "jwt")
    control = WorkAuthority(repository)
    jobs = []
    for name in ("first", "second"):
        job = repository.create_job(
            project_id="project",
            principal_id=actor.id,
            allowed_providers=["mock_cli"],
            grant_id=name,
            budget={"scheduler_units": 100},
        )
        grant = control.issue_root(
            actor,
            job_id=job["id"],
            providers={"mock_cli"},
            permissions=Permissions(tools={"knowledge.read"}, paths={str(tmp_path)}),
            expires_at=time.time() + 600,
        )
        jobs.append((job, grant))

    backend = AdmissionOnlyBackend()
    context = SimpleNamespace(
        root=tmp_path,
        repo=repository,
        actor=actor,
        control=control,
        jobs=jobs,
        backend=backend,
        service=WorkAdmission(repository, backends={"test": backend}),
        contracts={},
        launch_origins={},
        interactions=postgres_interaction_store.service,
    )
    terminal_id = "ac030001"
    session = "interaction-work-owned"
    incarnation = "interaction-work-incarnation"
    database.create_terminal(
        terminal_id,
        session,
        "worker",
        "mock_cli",
        allowed_tools=["@cao-mcp-server"],
        session_incarnation_id=incarnation,
    )
    context.terminal_id = terminal_id
    context.incarnation_id = incarnation
    context.session_name = session
    return context


async def _bind_work(context):
    adapters = targeted_registry(context, [])
    work = admit(
        context,
        "interaction-work-bound-task",
        delivery=target_envelope(
            "launch",
            "managed task",
            terminal_id=context.terminal_id,
            session_name=context.session_name,
            window_name="worker",
        ),
    )
    restarted = WorkAdmission(
        context.repo,
        backends={"test": context.backend},
        delivery_adapters=adapters,
    )
    delivered = await restarted.dispatch_registered_next()
    assert delivered["id"] == work["id"]
    return context.repo.get_work(work["id"])


def _register(context, **kwargs):
    return context.interactions.register_source(
        terminal_id=context.terminal_id,
        incarnation_id=context.incarnation_id,
        session_name=context.session_name,
        provider="mock_cli",
        principal=context.actor,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_new_work_ownership_revokes_a_preexisting_unbound_source(interaction_work_context):
    context = interaction_work_context
    issued = _register(context)
    group = context.interactions.create_group(
        issued.token,
        CreateGroupRequest(
            questions=[
                QuestionInput(kind="text", prompt="Preexisting ordinary question", required=True)
            ]
        ),
        "work-preexisting-question-operation",
    )
    question = group.questions[0]
    await _bind_work(context)
    with pytest.raises(InteractionForbidden):
        context.interactions.authenticate_agent(issued.token)
    with pytest.raises(InteractionForbidden):
        context.interactions.accept_answer(
            context.actor,
            question["question_id"],
            question["version"],
            InteractionAnswerPayload(kind="text", text="Cannot bypass Work"),
            "work-preexisting-answer-operation",
        )
    assert len(context.backend.effects) == 1


@pytest.mark.asyncio
async def test_work_owned_source_cannot_omit_exact_binding(interaction_work_context):
    context = interaction_work_context
    await _bind_work(context)
    with pytest.raises(InteractionForbidden):
        _register(context, execution_mode="local")
    with context.repo.connection() as connection:
        assert connection.execute("SELECT count(*) FROM interaction_sources").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_historical_work_binding_does_not_become_ordinary_terminal(interaction_work_context):
    context = interaction_work_context
    work = await _bind_work(context)
    # Fault injection models a terminal whose managed attempt already ended.
    # The durable ownership identity survives completion, as in the ordinary
    # terminal input guard. No new unbound capability may reinterpret it.
    with context.repo.transaction() as connection:
        connection.execute(
            "UPDATE work_attempts SET state='finished' WHERE id=%s", (work["attempts"][0]["id"],)
        )
    with pytest.raises(InteractionForbidden):
        _register(context, execution_mode="local")


def test_incomplete_work_store_is_not_treated_as_absent_work(interaction_work_context):
    context = interaction_work_context
    with connect(context.repo.target.migration_target()) as connection, connection.transaction():
        # The PostgreSQL baseline has multiple FK-boundary tables referencing
        # attempts. Drop dependent constraints along with the target so this
        # fault injection leaves the store observably incomplete.
        connection.execute("DROP TABLE work_attempts CASCADE")
    with pytest.raises((InteractionError, RuntimeError)):
        _register(context, execution_mode="local")


@pytest.mark.asyncio
async def test_terminal_tool_permission_cannot_expand_the_work_contract(interaction_work_context):
    context = interaction_work_context
    work = await _bind_work(context)
    attempt = work["attempts"][0]
    job, grant = context.jobs[0]
    with context.repo.connection() as connection:
        order = connection.execute(
            "SELECT contract_hash FROM work_dispatch_bindings WHERE attempt_id=%s",
            (attempt["id"],),
        ).fetchone()
    binding = WorkSourceBinding(
        job_id=job["id"],
        work_item_id=work["id"],
        attempt_id=attempt["id"],
        attempt_generation=attempt["generation"],
        contract_hash=order["contract_hash"],
        grant_id=grant.id,
        grant_revision=grant.revision,
        principal_id=context.actor.id,
    )
    # The real Work contract grants no CAO MCP tool even though terminal
    # metadata advertises it. The narrower effective contract must win.
    with pytest.raises(InteractionForbidden):
        _register(context, execution_mode="work_v1", work_binding=binding)
    with context.repo.connection() as connection:
        assert connection.execute("SELECT count(*) FROM interaction_sources").fetchone()[0] == 0


async def _bind_authorized_interaction_work(context):
    import hashlib
    import time

    from cli_agent_orchestrator.models.work_contract import (
        ContractPermissions,
        ContractResources,
        ContractSnapshot,
        EffectiveWorkContract,
    )
    from cli_agent_orchestrator.services.delegation_snapshot import (
        DelegationSnapshots,
        ResolvedSnapshot,
    )
    from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
    from cli_agent_orchestrator.services.work_authority import Permissions

    job = context.repo.create_job(
        project_id="project",
        principal_id=context.actor.id,
        allowed_providers=["mock_cli"],
        grant_id="interaction-authorized-root",
        budget={"scheduler_units": 100},
    )
    grant = context.control.issue_root(
        context.actor,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"knowledge.read", "@cao-mcp-server"}, paths={str(context.root)}
        ),
        expires_at=time.time() + 600,
    )
    context.jobs[0] = (job, grant)
    adapters = targeted_registry(context, [], operations=("inbox",))
    snapshot = DelegationSnapshots(
        context.repo,
        policy=KnowledgePolicy(context.repo, job["id"], grant.id, grant.revision),
    ).freeze(
        principal=context.actor,
        job_id=job["id"],
        contract_id="interaction-authorized-contract",
        binding_key="interaction-authorized-snapshot",
        request_hash=hashlib.sha256(b"interaction").hexdigest(),
        scope="project",
        scope_id="project",
        resolver=lambda connection, principal: ResolvedSnapshot("Frozen project context"),
    )
    contract = EffectiveWorkContract(
        id="interaction-authorized-contract",
        operation_kind="inbox",
        provider="mock_cli",
        backend="test",
        permissions=ContractPermissions(tools=("@cao-mcp-server",), paths=(str(context.root),)),
        resources=ContractResources(
            checkout_root=str(context.root),
            write_paths=(str(context.root / "allowed-work"),),
            units=1,
        ),
        snapshot=ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
    )
    work = context.service.admit(
        principal=context.actor,
        job_id=job["id"],
        idempotency_key="interaction-work-authorized-operation",
        request_hash=hashlib.sha256(b"authorized-request").hexdigest(),
        grant_id=grant.id,
        expected_grant_revision=grant.revision,
        contract=contract,
        lease_seconds=300,
        delivery=target_envelope(
            "inbox",
            "Safe managed task",
            terminal_id=context.terminal_id,
            session_name=context.session_name,
            window_name="worker",
        ),
    )
    restarted = WorkAdmission(
        context.repo, backends={"test": context.backend}, delivery_adapters=adapters
    )
    await restarted.dispatch_registered_next()
    work = context.repo.get_work(work["id"])
    attempt = work["attempts"][0]
    with context.repo.connection() as connection:
        dispatch = connection.execute(
            "SELECT contract_hash FROM work_dispatch_bindings WHERE attempt_id=%s", (attempt["id"],)
        ).fetchone()
    binding = WorkSourceBinding(
        job_id=job["id"],
        work_item_id=work["id"],
        attempt_id=attempt["id"],
        attempt_generation=attempt["generation"],
        contract_hash=dispatch["contract_hash"],
        grant_id=grant.id,
        grant_revision=grant.revision,
        principal_id=context.actor.id,
    )
    return _register(context, execution_mode="work_v1", work_binding=binding), grant


@pytest.mark.asyncio
async def test_authorized_work_v1_pull_revalidates_revocation_in_shared_transaction(
    interaction_work_context, monkeypatch
):
    context = interaction_work_context
    source, grant = await _bind_authorized_interaction_work(context)
    seen_connections = []
    from cli_agent_orchestrator.services.work_contract import WorkContracts

    real_revalidate = WorkContracts._revalidate_order

    def observe_revalidation(self, connection, *args, **kwargs):
        assert connection.in_transaction
        assert self.repository.target.identity == context.interactions.repository.target.identity
        seen_connections.append(connection)
        return real_revalidate(self, connection, *args, **kwargs)

    monkeypatch.setattr(WorkContracts, "_revalidate_order", observe_revalidation)
    group = context.interactions.create_group(
        source.token,
        CreateGroupRequest(
            questions=[QuestionInput(kind="text", prompt="Which format?", required=True)]
        ),
        "authorized-work-question-operation",
    )
    question = group.questions[0]
    accepted = context.interactions.accept_answer(
        context.actor,
        question["question_id"],
        1,
        InteractionAnswerPayload(kind="text", text="JSON"),
        "authorized-work-answer-operation",
    )
    assert accepted.delivery_state == "accepted"
    assert seen_connections
    context.control.revoke(
        context.actor,
        grant_id=grant.id,
        expected_grant_revision=grant.revision,
        reason="Owner revoked the work before answer retrieval",
    )
    with pytest.raises(InteractionForbidden):
        await context.interactions.wait_for_answers(source.token, group.group_id, 0)
    with pytest.raises(InteractionForbidden):
        context.interactions.accept_answer(
            context.actor,
            question["question_id"],
            1,
            InteractionAnswerPayload(kind="text", text="JSON"),
            "authorized-work-answer-retry-operation",
        )
    with context.repo.connection() as connection:
        assert (
            connection.execute(
                "SELECT state FROM interaction_deliveries WHERE answer_id=%s", (accepted.answer_id,)
            ).fetchone()[0]
            == "accepted"
        )
    assert len(context.backend.effects) == 1
