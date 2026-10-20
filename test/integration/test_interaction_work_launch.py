"""Work V1 launch binds the source capability before provider initialization.

This composes durable Work authority/admission and the real terminal creation
service. The backend records its guarded session creation, and Codex's provider
initialization is intercepted before any CLI process can run.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from test.postgres.interactions import postgres_interaction_store

import pytest

from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.models.work_contract import (
    ContractPermissions,
    ContractResources,
    ContractSnapshot,
    EffectiveWorkContract,
)
from cli_agent_orchestrator.providers.codex import CodexProvider
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    ResolvedSnapshot,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_launch import launch_adapter
from cli_agent_orchestrator.services.work_launch_runtime import LaunchIntent, LaunchRuntime
from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning

pytestmark = pytest.mark.postgres


class _RecordingWorkBackend(TmuxBackend):
    """Exercise Work's guarded creation boundary without tmux or a CLI process."""

    def __init__(self):
        self.created = []
        self.preflighted = []

    def preflight_work(self, contract):
        self.preflighted.append(contract)

    def create_session(
        self,
        session_name,
        window_name,
        terminal_id,
        working_directory=None,
        extra_env=None,
        *,
        work_safe=False,
    ):
        self.created.append(
            {
                "session_name": session_name,
                "window_name": window_name,
                "terminal_id": terminal_id,
                "working_directory": working_directory,
                "work_safe": work_safe,
            }
        )
        return window_name

    def session_exists(self, session_name):
        return False

    def supports_event_inbox(self):
        return True


def test_work_v1_launch_registers_exact_capability_before_provider_init(
    tmp_path, monkeypatch, postgres_interaction_store
):
    from cli_agent_orchestrator.models.terminal import TerminalStatus
    from cli_agent_orchestrator.providers import codex
    from cli_agent_orchestrator.services import status_monitor, terminal_service

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    write_path = checkout / "work"
    write_path.mkdir()
    repository = postgres_interaction_store.work_repository
    from cli_agent_orchestrator.services.work_scheduler import WorkScheduler

    WorkScheduler(repository).configure(
        capacity=1, max_queue=8, aging_seconds=10, expected_policy_revision=0
    )
    owner = auth._verified_principal(
        "https://interaction.work.integration",
        "interaction-work-v1-owner",
        [auth.SCOPE_READ, auth.SCOPE_WRITE, auth.SCOPE_ADMIN],
        "jwt",
    )
    job = repository.create_job(
        project_id="interaction-work-project",
        principal_id=owner.id,
        allowed_providers=["codex"],
        grant_id="interaction-work-root-grant",
        budget={"scheduler_units": 10},
    )
    grant = WorkAuthority(repository).issue_root(
        owner,
        job_id=job["id"],
        providers={"codex"},
        permissions=Permissions(
            tools={"@cao-mcp-server", "knowledge.read"},
            paths={str(checkout)},
        ),
        expires_at=time.time() + 900,
    )
    snapshot = DelegationSnapshots(
        repository,
        policy=KnowledgePolicy(repository, job["id"], grant.id, grant.revision),
    ).freeze(
        principal=owner,
        job_id=job["id"],
        contract_id="interaction-work-v1-contract",
        binding_key="interaction-work-v1-snapshot",
        request_hash=hashlib.sha256(b"interaction-work-v1-request").hexdigest(),
        scope="project",
        scope_id="interaction-work-project",
        resolver=lambda _connection, _principal: ResolvedSnapshot("Frozen Work launch context."),
    )
    contract = EffectiveWorkContract(
        id="interaction-work-v1-contract",
        operation_kind="launch",
        provider="codex",
        backend="test",
        permissions=ContractPermissions(
            tools=("@cao-mcp-server",),
            paths=(str(checkout),),
        ),
        resources=ContractResources(
            checkout_root=str(checkout),
            write_paths=(str(write_path),),
            units=1,
        ),
        snapshot=ContractSnapshot(
            state="present",
            id=snapshot.id,
            delivered_hash=snapshot.delivered_hash,
        ),
    )
    selection = "interaction-work-v1-selection"
    WorkProvisioning(repository).provision_launch(
        owner,
        subject=owner,
        selector=selection,
        expected_revision=0,
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=grant.revision,
        contract=contract,
        adapter_version=1,
        lease_seconds=300,
    )

    backend = _RecordingWorkBackend()
    runtime = LaunchRuntime(
        repository,
        backends={"test": backend},
        delivery_adapters={("launch", 1): launch_adapter()},
    )
    resolved = runtime.resolve_launch(
        owner,
        selection,
        LaunchIntent(
            agent_profile="interaction-work-v1-profile",
            session_name="interaction-work-v1-session",
            message="A test launch; no provider process is started.",
            allowed_tools=("@cao-mcp-server",),
        ),
    )
    receipt = runtime.admit_launch(owner, resolved)

    profile = AgentProfile(
        name="interaction-work-v1-profile",
        description="Work V1 source capability integration profile",
        provider="codex",
        mcpServers={
            "cao-mcp-server": {
                "command": "cao-mcp-server",
                "args": [],
                "env": {"CAO_TEST_MARKER": "preserved"},
            }
        },
    )
    monkeypatch.setattr(terminal_service, "load_agent_profile", lambda _name: profile)
    monkeypatch.setattr(codex, "load_agent_profile", lambda _name: profile)
    monkeypatch.setattr(
        terminal_service,
        "build_skill_catalog",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(
        status_monitor.status_monitor, "get_status", lambda _terminal: TerminalStatus.IDLE
    )
    monkeypatch.setattr("cli_agent_orchestrator.constants.CAO_HOME_DIR", tmp_path / "cao-home")

    interaction_service = postgres_interaction_store.service
    monkeypatch.setattr(terminal_service, "_interaction_service", lambda: interaction_service)

    init_observations = []

    def create_provider(
        provider_name,
        terminal_id,
        session_name,
        window_name,
        agent_profile,
        allowed_tools,
        **kwargs,
    ):
        assert provider_name == "codex"
        provider = CodexProvider(
            terminal_id,
            session_name,
            window_name,
            agent_profile=agent_profile,
            allowed_tools=allowed_tools,
            skill_prompt=kwargs.get("skill_prompt"),
            model=kwargs.get("model"),
        )
        provider._developer_instructions_file_path = lambda: tmp_path / "codex-instructions.toml"

        async def initialize_without_process():
            capability_file = Path(provider.interaction_capability_file)
            assert capability_file.is_file()
            assert capability_file.stat().st_mode & 0o777 == 0o600
            raw_capability = capability_file.read_text(encoding="ascii")
            agent = interaction_service.authenticate_agent(raw_capability)
            assert agent.terminal_id == terminal_id
            registered_servers = provider.interaction_mcp_servers(profile.mcpServers)
            config = registered_servers["cao-mcp-server"]
            assert config["env"]["CAO_TEST_MARKER"] == "preserved"
            assert config["env"]["CAO_INTERACTION_CAPABILITY_FILE"] == str(capability_file)
            # Exercise Codex's profile-to-command config writer without
            # executing its returned shell command or starting Codex.
            command = provider._build_codex_command()
            assert str(capability_file) in command
            assert raw_capability not in command
            init_observations.append((terminal_id, raw_capability, command))
            return True

        provider.initialize = initialize_without_process
        return provider

    monkeypatch.setattr(terminal_service.provider_manager, "create_provider", create_provider)
    sent_messages = []
    monkeypatch.setattr(
        terminal_service,
        "send_input",
        lambda terminal_id, message, **kwargs: sent_messages.append((terminal_id, message, kwargs)),
    )

    delivered = asyncio.run(runtime.dispatch_registered_next())
    assert delivered is not None
    assert delivered["id"] == receipt.work_item_id
    assert backend.preflighted and backend.created
    assert len(init_observations) == 1
    terminal_id, raw_capability, _command = init_observations[0]
    assert terminal_id == resolved.terminal_id
    assert sent_messages and sent_messages[0][0] == terminal_id
    assert sent_messages[0][2]["task_delivery"] is True

    with repository.connection() as connection:
        source = connection.execute(
            "SELECT * FROM interaction_sources WHERE terminal_id=%s", (terminal_id,)
        ).fetchone()
        attempt = connection.execute(
            "SELECT a.id,a.generation,a.work_item_id,b.contract_hash,b.grant_id,b.grant_revision,"
            "g.principal_id AS grant_principal_id "
            "FROM work_attempts a JOIN work_dispatch_bindings b "
            "ON b.attempt_id=a.id AND b.generation=a.generation "
            "JOIN work_grants g ON g.job_id=b.job_id AND g.id=b.grant_id "
            "AND g.revision=b.grant_revision WHERE a.id=%s",
            (receipt.attempt_id,),
        ).fetchone()
        assert source is not None and attempt is not None
        assert source["execution_mode"] == "work_v1"
        assert source["work_item_id"] == receipt.work_item_id == attempt["work_item_id"]
        assert source["attempt_id"] == receipt.attempt_id == attempt["id"]
        assert source["attempt_generation"] == attempt["generation"] == receipt.generation
        assert source["contract_hash"] == attempt["contract_hash"] == contract.canonical_hash()
        assert source["grant_id"] == attempt["grant_id"] == grant.id
        assert source["grant_revision"] == attempt["grant_revision"] == grant.revision
        assert source["owner_principal_id"] == attempt["grant_principal_id"]
        assert source["owner_source"] == "work_principal"
        assert source["capability_hash"] != raw_capability
        assert raw_capability not in json.dumps(dict(source))

    # The profile is copied for runtime overlay; its saved config is unchanged.
    assert profile.mcpServers["cao-mcp-server"]["env"] == {"CAO_TEST_MARKER": "preserved"}
