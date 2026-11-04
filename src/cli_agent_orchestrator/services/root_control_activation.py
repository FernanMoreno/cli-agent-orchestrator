"""Owner-funded issuance and pre-SQL consumption of root control compute."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget
from cli_agent_orchestrator.clients.sql_intent_repository import SqlIntentRepository
from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.models.resource_envelope import ResourceEnvelopeRecord
from cli_agent_orchestrator.models.root_control_activation import (
    RootControlActivation,
    RootControlFootprint,
    VerifiedRootControlActivation,
    _issue_verified_compute_startup_sample,
    _issue_verified_root_control_activation,
    _sign_root_control_activation,
    verify_root_control_activation_signature,
)
from cli_agent_orchestrator.models.sql_root_genesis import (
    RootSqlOpeningIntent,
    SignedRootSqlOpening,
)
from cli_agent_orchestrator.models.sql_startup_permission import sql_startup_destination_sha256
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.admission_policy import (
    CapacityObservation,
    ComputeObservation,
    ResourceAmounts,
    compute_capacity,
    validate_clock,
)
from cli_agent_orchestrator.services.admission_resources import (
    LinuxResourceSampler,
    ResourceSample,
    StorageObservation,
)
from cli_agent_orchestrator.services.admission_settings import AdmissionPolicyLoader
from cli_agent_orchestrator.services.delivery_admission import (
    DeliveryAdmission,
    StorageControlPermit,
)
from cli_agent_orchestrator.services.original_control_owner import (
    OriginalControlConfig,
    OriginalControlOwner,
    OriginalControlUnavailable,
)
from cli_agent_orchestrator.services.resource_process_supervisor import _identity, _matches
from cli_agent_orchestrator.services.root_sql_control import RootSqlSubject
from cli_agent_orchestrator.services.sql_root_genesis import sign_root_opening, verify_root_opening
from cli_agent_orchestrator.utils.process_sql_origin import (
    _check_root_sql_startup_pending,
    _claim_root_control_activation,
    current_sql_application_name,
)


class RootControlActivationUnavailable(StorageAdmissionUnavailable):
    """Fixed refusal for invalid, replayed, or unmeasurable root activation."""

    def __init__(self) -> None:
        super().__init__()


class RootControlActivationUnavailableAfterCommit(RootControlActivationUnavailable):
    """The root SQL journal committed, but no activation packet was issued."""

    def __init__(self, record: object):
        super().__init__()
        self.record = record
        self.journal_id = getattr(record, "journal_id", None)
        self.intent_id = getattr(record, "intent_id", None)


def _target_digest(target: DatabaseTarget) -> str:
    return hashlib.sha256(json.dumps(target.identity, separators=(",", ":")).encode()).hexdigest()


def _validate_subject(subject: RootSqlSubject, target: DatabaseTarget) -> None:
    if type(subject) is not RootSqlSubject:
        raise RootControlActivationUnavailable()
    binding = subject.binding
    if (
        binding.process_pid == os.getpid()
        or binding.store_id != target.store_id
        or binding.target_identity_sha256 != _target_digest(target)
    ):
        raise RootControlActivationUnavailable()
    live = _identity(binding.process_pid)
    if not _matches(binding, live) or (
        subject.boot_id,
        subject.start_time_ticks,
        subject.pid_namespace,
    ) != (live.boot_id, live.ticks, live.pid_ns):
        raise RootControlActivationUnavailable()


def _as_footprint(value: object) -> RootControlFootprint:
    if type(value) is ResourceAmounts:
        if value.memory_bytes <= 0 or value.cpu_millicores <= 0 or value.storage_slots != 0:
            raise RootControlActivationUnavailable()
        return RootControlFootprint(value.memory_bytes, value.cpu_millicores, 0)
    if type(value) is RootControlFootprint:
        return value
    raise RootControlActivationUnavailable()


def _validate_activation_local_binding(
    activation: RootControlActivation,
    root_record: RootSqlOpeningIntent,
    bootstrap: ResourceBootstrap,
) -> None:
    """Recheck signed destination and live local identity without opening SQL."""
    if (
        type(activation) is not RootControlActivation
        or type(root_record) is not RootSqlOpeningIntent
    ):
        raise RootControlActivationUnavailable()
    target = bootstrap._repository.resolve_target()
    if (
        not isinstance(target, DatabaseTarget)
        or target.scope != "main"
        or not bootstrap.matches_primary_target(target)
    ):
        raise RootControlActivationUnavailable()
    subject = root_record.subject
    live = _identity(os.getpid())
    binding = bootstrap._capture(target)
    if (
        subject.process_pid != os.getpid()
        or binding != activation.envelope.binding
        or not _matches(subject, live)
        or (root_record.boot_id, root_record.start_time_ticks, root_record.pid_namespace)
        != (live.boot_id, live.ticks, live.pid_ns)
        or root_record.subject_sql_application_name != current_sql_application_name()
        or subject.store_id != target.store_id
        or subject.target_identity_sha256 != _target_digest(target)
        or root_record.destination_sha256 != sql_startup_destination_sha256(target)
        or root_record.destination_scope != "main"
    ):
        raise RootControlActivationUnavailable()


def issue_root_control_activation(
    bootstrap: ResourceBootstrap,
    channel: Connection,
    *,
    principal: object,
    journal_id: str,
    intent_id: str,
    session_id: str,
    subject: RootSqlSubject,
    key_supplier: Callable[[], tuple[str, Ed25519PrivateKey]],
    startup_footprint: ResourceAmounts | RootControlFootprint,
    refresh_footprint: ResourceAmounts | RootControlFootprint,
) -> bytes:
    """Issue only on the existing owner connection after confirmed journal commit."""
    # This identity check is intentionally the first operation with application
    # authority. A rejected principal cannot trigger target, key or SQL access.
    if type(bootstrap) is not ResourceBootstrap:
        raise RootControlActivationUnavailable()
    actor = bootstrap._administrator(principal)
    if (
        type(channel) is not Connection
        or not callable(key_supplier)
        or type(startup_footprint) not in (ResourceAmounts, RootControlFootprint)
        or type(refresh_footprint) not in (ResourceAmounts, RootControlFootprint)
    ):
        raise RootControlActivationUnavailable()
    startup = _as_footprint(startup_footprint)
    refresh = _as_footprint(refresh_footprint)
    target = bootstrap._repository.resolve_target()
    if (
        not isinstance(target, DatabaseTarget)
        or target.scope != "main"
        or not bootstrap.matches_primary_target(target)
        or channel.target.identity != target.identity
        or channel.target.store_id != target.store_id
    ):
        raise RootControlActivationUnavailable()
    _validate_subject(subject, target)
    channel._check_funded_worker()

    try:
        envelope_before = bootstrap._repository.read_latest_owner_funded(
            channel, target, subject.binding.instance_id
        )
    except Exception:
        raise RootControlActivationUnavailable() from None
    if (
        type(envelope_before) is not ResourceEnvelopeRecord
        or not envelope_before.active
        or not envelope_before.claims.complete_hierarchy
        or envelope_before.binding != subject.binding
    ):
        raise RootControlActivationUnavailable()

    repository = SqlIntentRepository(target, binding_supplier=lambda: bootstrap._capture(target))
    try:
        committed = repository.record_root_opening(
            channel,
            journal_id=journal_id,
            intent_id=intent_id,
            session_id=session_id,
            subject=subject.binding,
            subject_sql_application_name=subject.sql_application_name,
            boot_id=subject.boot_id,
            start_time_ticks=subject.start_time_ticks,
            pid_namespace=subject.pid_namespace,
            destination=target,
            principal_id=actor,
        )
    except BaseException:
        # In particular, FundedSqlCommitUncertain/SqlIntentCommitUncertain is
        # never converted into a signed or reusable activation capability.
        raise
    record = committed.record
    try:
        latest = bootstrap._repository.read_latest_owner_funded(
            channel, target, subject.binding.instance_id
        )
        if (
            type(latest) is not ResourceEnvelopeRecord
            or not latest.active
            or not latest.claims.complete_hierarchy
            or latest.binding != subject.binding
            or latest != envelope_before
        ):
            raise RootControlActivationUnavailable()
        key_result = key_supplier()
        if (
            type(key_result) is not tuple
            or len(key_result) != 2
            or type(key_result[0]) is not str
            or not isinstance(key_result[1], Ed25519PrivateKey)
        ):
            raise RootControlActivationUnavailable()
        key_id, private_key = key_result
        certificate = sign_root_opening(committed, key_id=key_id, private_key=private_key)
        activation = _sign_root_control_activation(
            committed=committed,
            certificate=certificate,
            envelope=latest,
            startup_footprint=startup,
            refresh_footprint=refresh,
            key_id=key_id,
            private_key=private_key,
        )
        return activation.to_bytes()
    except RootControlActivationUnavailableAfterCommit:
        raise
    except Exception:
        raise RootControlActivationUnavailableAfterCommit(record) from None


def _verify_activation(
    packet: bytes,
    *,
    trusted_keys: Mapping[str, Ed25519PublicKey],
    bootstrap: ResourceBootstrap,
) -> tuple[RootControlActivation, VerifiedRootControlActivation]:
    try:
        _check_root_sql_startup_pending()
        activation = RootControlActivation.from_bytes(packet)
        signature_proof = verify_root_control_activation_signature(
            activation, trusted_keys=trusted_keys
        )
        root_record = verify_root_opening(
            activation.certificate,
            trusted_keys=trusted_keys,
        )
        if root_record != activation.certificate.record:
            raise RootControlActivationUnavailable()
        _validate_activation_local_binding(activation, root_record, bootstrap)
        proof = _issue_verified_root_control_activation(
            activation,
            signature_proof=signature_proof,
        )
        return activation, proof
    except RootControlActivationUnavailable:
        raise
    except Exception:
        raise RootControlActivationUnavailable() from None


def _validated_compute_sample(
    sample: ResourceSample,
    *,
    allow_cpu_warming: bool,
) -> ComputeObservation | None:
    if type(sample) is not ResourceSample or type(sample.observation) is not CapacityObservation:
        raise RootControlActivationUnavailable()
    observation = sample.observation
    if (
        observation.storage_available_slots is not None
        or not isinstance(observation.memory_domains, tuple)
        or not observation.memory_domains
        or not isinstance(observation.cpu_domains, tuple)
    ):
        raise RootControlActivationUnavailable()
    issues = set(sample.issues)
    permitted = {"storage_unknown"}
    if allow_cpu_warming:
        permitted.add("cpu_warming")
    if "storage_unknown" not in issues or not issues <= permitted:
        raise RootControlActivationUnavailable()
    if allow_cpu_warming:
        if (
            "cpu_warming" not in issues
            or observation.cpu_available_millicores is not None
            or observation.memory_available_bytes is None
            or observation.memory_limit_bytes is None
            or observation.cpu_limit_millicores is None
            or not observation.memory_domains
        ):
            raise RootControlActivationUnavailable()
        return None
    if (
        "cpu_warming" in issues
        or observation.memory_available_bytes is None
        or observation.memory_limit_bytes is None
        or observation.cpu_available_millicores is None
        or observation.cpu_limit_millicores is None
        or not observation.cpu_domains
    ):
        raise RootControlActivationUnavailable()
    try:
        return ComputeObservation(
            observed_at=observation.observed_at,
            memory_available_bytes=observation.memory_available_bytes,
            memory_limit_bytes=observation.memory_limit_bytes,
            cpu_available_millicores=observation.cpu_available_millicores,
            cpu_limit_millicores=observation.cpu_limit_millicores,
            memory_domains=observation.memory_domains,
            cpu_domains=observation.cpu_domains,
        )
    except (TypeError, ValueError):
        raise RootControlActivationUnavailable() from None


async def prepare_original_control_owner(
    *,
    packet: bytes,
    trusted_keys: Mapping[str, Ed25519PublicKey],
    bootstrap: ResourceBootstrap,
    policy_loader: AdmissionPolicyLoader,
    scope_id: str,
    clock: Callable[[], float] = time.monotonic,
) -> OriginalControlOwner:
    """Verify, claim once, measure real Linux compute, and reserve actor startup."""
    if (
        type(bootstrap) is not ResourceBootstrap
        or type(policy_loader) is not AdmissionPolicyLoader
        or not isinstance(scope_id, str)
        or not scope_id.strip()
        or not callable(clock)
        or not isinstance(trusted_keys, Mapping)
    ):
        raise RootControlActivationUnavailable()
    activation, proof = _verify_activation(
        packet,
        trusted_keys=trusted_keys,
        bootstrap=bootstrap,
    )
    try:
        _claim_root_control_activation()
        if not proof._claim_startup():
            raise RootControlActivationUnavailable()
        loaded = (
            policy_loader.load_bootstrap(scope_id=scope_id)
            if policy_loader.persistent
            else policy_loader.load(scope_id=scope_id)
        )
        if loaded.status != "current" or loaded.configuration is None:
            raise RootControlActivationUnavailable()
        configuration = loaded.configuration
        policy = configuration.policy
        owner, control = DeliveryAdmission.with_storage_control(policy)
        claims = activation.envelope.claims
        owner._budget = ResourceAmounts(
            claims.memory_budget_bytes,
            claims.cpu_budget_millicores,
            0,
        )
        sampler = LinuxResourceSampler(
            clock=clock,
            complete_hierarchy=claims.complete_hierarchy,
            root_is_host=claims.root_is_host,
        )
        first_at = clock()
        validate_clock(first_at)
        first = sampler.sample(storage=StorageObservation(first_at, None))
        _validated_compute_sample(first, allow_cpu_warming=True)
        await asyncio.sleep(configuration.sample_interval_s)
        second_at = clock()
        validate_clock(second_at)
        second = sampler.sample(storage=StorageObservation(second_at, None))
        observation = _validated_compute_sample(second, allow_cpu_warming=False)
        if observation is None:
            raise RootControlActivationUnavailable()
        _validate_activation_local_binding(
            activation,
            activation.certificate.record,
            bootstrap,
        )
        compute_sample = _issue_verified_compute_startup_sample(
            proof,
            observation=observation,
        )
        startup_attempt = owner._reserve_root_control_startup(
            proof,
            compute_sample=compute_sample,
            now=clock(),
        )
        startup = startup_attempt.reservation
        if startup is None:
            raise RootControlActivationUnavailable()
        refresh = activation.refresh_footprint
        config = OriginalControlConfig(
            bootstrap=bootstrap,
            owner=owner,
            control=control,
            certificate=activation.certificate,
            trusted_keys=trusted_keys,
            policy_loader=policy_loader,
            scope_id=scope_id,
            command_footprint=ResourceAmounts(
                refresh.memory_bytes,
                refresh.cpu_millicores,
                refresh.storage_slots,
            ),
            clock=clock,
            expected_envelope=activation.envelope,
        )
        try:
            return OriginalControlOwner(config, startup=startup)
        except BaseException:
            startup.cancel_before_start()
            owner.stop_accepting()
            raise
    except RootControlActivationUnavailable:
        raise
    except OriginalControlUnavailable:
        raise RootControlActivationUnavailable() from None
    except asyncio.CancelledError:
        raise
    except Exception:
        raise RootControlActivationUnavailable() from None
