"""Pure sealed-context checks for explicit replacement admission."""

import hashlib
from dataclasses import replace

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_contract import (
    ContractPermissions,
    ContractResources,
    ContractSnapshot,
    EffectiveWorkContract,
)
from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope
from cli_agent_orchestrator.models.work_origin import ProvisionedLaunch, ProvisionRef
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_launch_runtime import (
    LaunchIntent,
    LaunchRuntime,
    ResolvedLaunch,
    ResolvedReplacement,
    _canonical,
)


def test_replacement_handoff_is_sealed_to_runtime_principal_and_generation(tmp_path):
    principal = auth._verified_principal(
        "https://issuer.test", "replacement-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    repository = WorkRepository(tmp_path / "replacement-runtime")
    runtime = LaunchRuntime(repository, backends={}, delivery_adapters={})
    contract = EffectiveWorkContract(
        id="replacement-contract",
        operation_kind="launch",
        provider="mock_cli",
        backend="mock_backend",
        permissions=ContractPermissions(tools=("tool.read",)),
        resources=ContractResources(checkout_root=str(tmp_path.resolve()), units=1),
        snapshot=ContractSnapshot(
            state="present",
            id="replacement-snapshot",
            delivered_hash="a" * 64,
        ),
    )
    provision = ProvisionedLaunch(
        ref=ProvisionRef(
            id="replacement-provision",
            principal_id=principal.id,
            selector="replacement",
            revision=1,
        ),
        job_id="replacement-job",
        grant_id="replacement-grant",
        grant_revision=1,
        contract=contract,
        contract_hash=contract.canonical_hash(),
        snapshot_id=contract.snapshot.id,
        snapshot_hash=contract.snapshot.delivered_hash,
        adapter_version=1,
        lease_seconds=300,
    )
    intent = LaunchIntent(
        agent_profile="developer",
        session_name="replacement-session",
        message="continue the review",
        allowed_tools=("tool.read",),
    )
    request_hash, base_key = runtime._operation_request(
        principal.id, provision.ref.selector, provision, intent
    )
    base_terminal = hashlib.sha256(
        ("launch-terminal-v1:" + request_hash + ":" + base_key).encode("utf-8")
    ).hexdigest()[:8]
    payload = {
        "terminal_id": base_terminal,
        **intent.canonical(),
    }
    base_launch = ResolvedLaunch(
        principal_id=principal.id,
        selection=provision.ref.selector,
        selection_auto_resolved=False,
        job_id=provision.job_id,
        grant_id=provision.grant_id,
        grant_revision=provision.grant_revision,
        contract=contract,
        request_hash=request_hash,
        idempotency_key=base_key,
        envelope=WorkDeliveryEnvelope(
            operation_kind="launch",
            adapter_version=1,
            payload_json=_canonical(payload),
        ),
        terminal_id=base_terminal,
        lease_seconds=provision.lease_seconds,
        provision=provision,
        provision_fingerprint=provision.fingerprint(),
        _seal="",
        _runtime=runtime._runtime_identity,
    )
    base_launch = replace(base_launch, _seal=runtime._seal_handoff(base_launch))
    retry_key, retry_hash = runtime._replacement_identity(
        principal_id=principal.id,
        work_item_id="replacement-work",
        generation=2,
        action_idempotency_key="receiver-action-2",
        launch=base_launch,
    )
    terminal_id = hashlib.sha256(
        ("launch-terminal-v1:" + request_hash + ":" + retry_key).encode("utf-8")
    ).hexdigest()[:8]
    payload["terminal_id"] = terminal_id
    launch = replace(
        base_launch,
        idempotency_key=retry_key,
        terminal_id=terminal_id,
        envelope=WorkDeliveryEnvelope(
            operation_kind="launch",
            adapter_version=1,
            payload_json=_canonical(payload),
        ),
        _seal="",
    )
    launch = replace(launch, _seal=runtime._seal_handoff(launch))
    resolved = ResolvedReplacement(
        principal_id=principal.id,
        work_item_id="replacement-work",
        generation=2,
        expected_work_revision=4,
        expected_attempt_id="replacement-attempt-2",
        planned_attempt_revision=1,
        action_idempotency_key="receiver-action-2",
        retry_idempotency_key=retry_key,
        retry_request_hash=retry_hash,
        launch=launch,
        _seal="",
        _runtime=runtime._runtime_identity,
    )
    resolved = replace(resolved, _seal=runtime._seal_replacement(resolved))

    foreign_runtime = LaunchRuntime(repository, backends={}, delivery_adapters={})
    other_principal = auth._verified_principal(
        "https://issuer.test", "other-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    assert runtime._replacement_handoff_is_current(principal, resolved)
    assert not foreign_runtime._replacement_handoff_is_current(principal, resolved)
    assert not runtime._replacement_handoff_is_current(other_principal, resolved)
    assert not runtime._replacement_handoff_is_current(principal, replace(resolved, generation=3))
