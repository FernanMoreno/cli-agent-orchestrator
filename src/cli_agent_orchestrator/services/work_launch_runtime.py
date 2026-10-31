"""Resolve a preprovisioned launch selection into one durable work admission.

This is intentionally an internal application service.  It resolves only
durable provisioning evidence; it neither creates jobs or grants nor accepts
either from the caller.  HTTP, MCP and CLI wiring belong to later work.
"""

import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from cli_agent_orchestrator.clients.postgres import DatabaseError
from cli_agent_orchestrator.clients.work_repository import (
    SchemaMismatch,
    WorkConflict,
    WorkRepository,
)
from cli_agent_orchestrator.models.work_contract import (
    EffectiveWorkContract,
    EffectiveWorkContractV2,
)
from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope
from cli_agent_orchestrator.models.work_origin import ProvisionedLaunch
from cli_agent_orchestrator.security.auth import Principal
from cli_agent_orchestrator.services.delegation_snapshot import (
    SnapshotConflict,
    SnapshotUnavailable,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgeAccessDenied
from cli_agent_orchestrator.services.work_admission import WorkAdmission
from cli_agent_orchestrator.services.work_authority import (
    AuthorityDenied,
    GrantConflict,
    WorkAuthority,
)
from cli_agent_orchestrator.services.work_contract import ContractConflict
from cli_agent_orchestrator.services.work_origin import WorkOrigins
from cli_agent_orchestrator.services.work_provisioning import (
    ProvisionDenied,
    ProvisionSelectionUnavailable,
    ProvisionUnavailable,
    WorkProvisioning,
)

_IDENTITY = re.compile(r"^[A-Za-z0-9._:-]+$")
_TERMINAL_ID = re.compile(r"^[0-9a-f]{8}$")


def _identity(value: str, label: str, *, maximum: int = 128) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or not _IDENTITY.fullmatch(value)
    ):
        raise ValueError(f"invalid {label}")
    return value


def _launch_tool(value: str) -> str:
    """Accept explicit MCP names; admission still checks exact contract permission."""
    if isinstance(value, str) and value.startswith("@"):
        _identity(value[1:], "launch tool", maximum=127)
        return value
    return _identity(value, "launch tool")


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


class LaunchRuntimeError(Exception):
    """Sanitized internal error that a future transport can map without leaking setup."""

    def __init__(self, code: str, message: str, *, retryable: bool, required_action: str):
        self.code = code
        self.message = message
        self.retryable = retryable
        self.required_action = required_action
        super().__init__(message)

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "required_action": self.required_action,
        }


@dataclass(frozen=True)
class LaunchIntent:
    """Unprivileged launch content; all effective execution fields stay in context."""

    agent_profile: str
    session_name: str
    message: str
    allowed_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identity(self.agent_profile, "agent profile")
        _identity(self.session_name, "session name")
        if not isinstance(self.message, str) or len(self.message) > 32768:
            raise ValueError("invalid launch message")
        if type(self.allowed_tools) is not tuple:
            raise ValueError("launch tools must be an explicit tuple")
        tools = tuple(sorted({_launch_tool(tool) for tool in self.allowed_tools}))
        object.__setattr__(self, "allowed_tools", tools)

    def canonical(self) -> dict[str, object]:
        return {
            "agent_profile": self.agent_profile,
            "session_name": self.session_name,
            "message": self.message,
            "allowed_tools": list(self.allowed_tools),
        }


@dataclass(frozen=True)
class ResolvedLaunch:
    """Private server handoff between resolve and admission, bound to one runtime."""

    principal_id: str
    selection: str
    selection_auto_resolved: bool
    job_id: str
    grant_id: str
    grant_revision: int
    contract: EffectiveWorkContract | EffectiveWorkContractV2
    request_hash: str
    idempotency_key: str
    envelope: WorkDeliveryEnvelope
    terminal_id: str
    lease_seconds: int
    provision: ProvisionedLaunch
    provision_fingerprint: str
    _seal: str = field(repr=False, compare=False)
    _runtime: object = field(repr=False, compare=False)

    @property
    def provision_ref(self):
        return self.provision.ref


@dataclass(frozen=True)
class ResolvedReplacement:
    """Sealed, single-runtime context for one explicit planned-generation admission."""

    principal_id: str
    work_item_id: str
    generation: int
    expected_work_revision: int
    expected_attempt_id: str
    planned_attempt_revision: int
    action_idempotency_key: str
    retry_idempotency_key: str
    retry_request_hash: str
    launch: ResolvedLaunch
    _seal: str = field(repr=False, compare=False)
    _runtime: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class LaunchReceipt:
    """The durable admission identity for this operation only, never a terminal view."""

    work_item_id: str
    attempt_id: str
    generation: int
    state: str


class LaunchRuntime:
    """Compose trusted launch setup with ``WorkAdmission`` without dispatching it."""

    def __init__(
        self,
        repository: WorkRepository,
        *,
        backends: Mapping,
        delivery_adapters: Mapping,
        resource_admission=None,
        resource_clock=time.monotonic,
    ):
        if not isinstance(repository, WorkRepository):
            raise ValueError("verified work repository required")
        self.repository = repository
        self._provisioning = WorkProvisioning(repository)
        # One origin owner is paired with the admission and receipt owner for
        # this repository runtime. Resolvers created elsewhere cannot be
        # substituted into this dispatcher.
        self.origins = WorkOrigins(repository)
        # WorkAdmission owns the only admission/write path.  Its constructor also
        # validates the explicit server backend and adapter registries.
        self._admission = WorkAdmission(
            repository,
            backends=backends,
            delivery_adapters=delivery_adapters,
            origins=self.origins,
            resource_admission=resource_admission,
            resource_clock=resource_clock,
        )
        for backend in self._admission.backends.values():
            bind_origins = getattr(backend, "bind_work_origins", None)
            if callable(bind_origins):
                bind_origins(self.origins)
        self._runtime_identity = object()
        self._handoff_secret = secrets.token_bytes(32)

    @staticmethod
    def _handoff_payload(resolved: ResolvedLaunch) -> str:
        """Canonicalize every effective handoff field before it enters admission."""
        return _canonical(
            {
                "contract": resolved.contract.canonical_json(),
                "envelope": {
                    "adapter_version": resolved.envelope.adapter_version,
                    "operation_kind": resolved.envelope.operation_kind,
                    "payload_json": resolved.envelope.payload_json,
                },
                "grant_id": resolved.grant_id,
                "grant_revision": resolved.grant_revision,
                "idempotency_key": resolved.idempotency_key,
                "job_id": resolved.job_id,
                "lease_seconds": resolved.lease_seconds,
                "principal_id": resolved.principal_id,
                "provision_fingerprint": resolved.provision_fingerprint,
                "provision_ref": resolved.provision.ref.model_dump(mode="json"),
                "request_hash": resolved.request_hash,
                "selection_auto_resolved": resolved.selection_auto_resolved,
                "selection": resolved.selection,
                "terminal_id": resolved.terminal_id,
            }
        )

    def _seal_handoff(self, resolved: ResolvedLaunch) -> str:
        return hmac.new(
            self._handoff_secret,
            self._handoff_payload(resolved).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _replacement_identity(
        *,
        principal_id: str,
        work_item_id: str,
        generation: int,
        action_idempotency_key: str,
        launch: ResolvedLaunch,
    ) -> tuple[str, str]:
        payload = _canonical(
            {
                "version": 1,
                "principal_id": principal_id,
                "work_item_id": work_item_id,
                "generation": generation,
                "action_idempotency_key": action_idempotency_key,
                "launch_request_hash": launch.request_hash,
                "provision_fingerprint": launch.provision_fingerprint,
                "provision_ref": launch.provision.ref.model_dump(mode="json"),
            }
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"replacement-v1-{digest}", digest

    @staticmethod
    def _replacement_payload(resolved: ResolvedReplacement) -> str:
        return _canonical(
            {
                "principal_id": resolved.principal_id,
                "work_item_id": resolved.work_item_id,
                "generation": resolved.generation,
                "expected_work_revision": resolved.expected_work_revision,
                "expected_attempt_id": resolved.expected_attempt_id,
                "planned_attempt_revision": resolved.planned_attempt_revision,
                "action_idempotency_key": resolved.action_idempotency_key,
                "retry_idempotency_key": resolved.retry_idempotency_key,
                "retry_request_hash": resolved.retry_request_hash,
                "launch_handoff": LaunchRuntime._handoff_payload(resolved.launch),
            }
        )

    def _seal_replacement(self, resolved: ResolvedReplacement) -> str:
        return hmac.new(
            self._handoff_secret,
            self._replacement_payload(resolved).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _replacement_handoff_is_current(self, principal: Principal, resolved) -> bool:
        try:
            if (
                not isinstance(resolved, ResolvedReplacement)
                or resolved._runtime is not self._runtime_identity
                or resolved.principal_id != principal.id
                or resolved.launch._runtime is not self._runtime_identity
                or not self._handoff_is_current(resolved.launch)
                or not hmac.compare_digest(resolved._seal, self._seal_replacement(resolved))
                or type(resolved.generation) is not int
                or resolved.generation <= 1
                or type(resolved.expected_work_revision) is not int
                or resolved.expected_work_revision <= 0
                or type(resolved.planned_attempt_revision) is not int
                or resolved.planned_attempt_revision <= 0
                or resolved.launch.idempotency_key != resolved.retry_idempotency_key
                or resolved.launch.principal_id != principal.id
            ):
                return False
            retry_key, retry_hash = self._replacement_identity(
                principal_id=resolved.principal_id,
                work_item_id=resolved.work_item_id,
                generation=resolved.generation,
                action_idempotency_key=resolved.action_idempotency_key,
                launch=resolved.launch,
            )
            return (
                retry_key == resolved.retry_idempotency_key
                and retry_hash == resolved.retry_request_hash
            )
        except (AttributeError, TypeError, ValueError):
            return False

    def _handoff_is_current(self, resolved: ResolvedLaunch) -> bool:
        """Reject replace/forgery before it can reach admission or preflight."""
        try:
            if not hmac.compare_digest(resolved._seal, self._seal_handoff(resolved)):
                return False
            if type(resolved.selection_auto_resolved) is not bool:
                return False
            provision = resolved.provision
            if (
                not isinstance(provision, ProvisionedLaunch)
                or resolved.provision_fingerprint != provision.fingerprint()
                or (
                    resolved.principal_id,
                    resolved.selection,
                    resolved.job_id,
                    resolved.grant_id,
                    resolved.grant_revision,
                    resolved.lease_seconds,
                )
                != (
                    provision.ref.principal_id,
                    provision.ref.selector,
                    provision.job_id,
                    provision.grant_id,
                    provision.grant_revision,
                    provision.lease_seconds,
                )
                or resolved.contract.canonical_json() != provision.contract.canonical_json()
                or resolved.contract.canonical_hash() != provision.contract_hash
                or resolved.envelope.operation_kind != provision.contract.operation_kind
                or resolved.envelope.adapter_version != provision.adapter_version
            ):
                return False
            payload = json.loads(resolved.envelope.payload_json)
            expected_payload_fields = {
                "agent_profile",
                "allowed_tools",
                "message",
                "session_name",
                "terminal_id",
            }
            if provision.adapter_version == 2:
                expected_payload_fields.add("command_token")
            if not isinstance(payload, dict) or set(payload) != expected_payload_fields:
                return False
            launch_intent = LaunchIntent(
                agent_profile=payload["agent_profile"],
                session_name=payload["session_name"],
                message=payload["message"],
                allowed_tools=tuple(payload["allowed_tools"]),
            )
            request_hash, _ = self._operation_request(
                resolved.principal_id, resolved.selection, provision, launch_intent
            )
            terminal_id = hashlib.sha256(
                ("launch-terminal-v1:" + request_hash + ":" + resolved.idempotency_key).encode(
                    "utf-8"
                )
            ).hexdigest()[:8]
            canonical_fields = {
                "terminal_id": terminal_id,
                "agent_profile": launch_intent.agent_profile,
                "session_name": launch_intent.session_name,
                "message": launch_intent.message,
                "allowed_tools": list(launch_intent.allowed_tools),
            }
            if provision.adapter_version == 2:
                if (
                    not isinstance(resolved.contract, EffectiveWorkContractV2)
                    or len(resolved.contract.executable_identities) != 1
                    or payload["command_token"]
                    != resolved.contract.executable_identities[0].command_token
                ):
                    return False
                canonical_fields["command_token"] = payload["command_token"]
            canonical_payload = _canonical(canonical_fields)
            return (
                resolved.request_hash == request_hash
                and resolved.terminal_id == terminal_id
                and payload["terminal_id"] == terminal_id
                and resolved.envelope.payload_json == canonical_payload
            )
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            return False

    @staticmethod
    def _error(
        code: str, message: str, *, retryable: bool, required_action: str
    ) -> LaunchRuntimeError:
        return LaunchRuntimeError(
            code, message, retryable=retryable, required_action=required_action
        )

    @staticmethod
    def _require_principal(principal: Principal) -> None:
        try:
            WorkAuthority._principal(principal)
        except AuthorityDenied as error:
            raise LaunchRuntime._error(
                "launch_authority_denied",
                "Verified launch authority is required.",
                retryable=False,
                required_action="authenticate",
            ) from error

    @staticmethod
    def _selection(value: str) -> str:
        try:
            return _identity(value, "launch selection")
        except ValueError as error:
            raise LaunchRuntime._error(
                "launch_context_unavailable",
                "Trusted launch context is unavailable.",
                retryable=False,
                required_action="provision_launch_context",
            ) from error

    @staticmethod
    def _idempotency_key(value: str) -> str:
        try:
            return _identity(value, "idempotency key")
        except ValueError as error:
            raise LaunchRuntime._error(
                "launch_intent_invalid",
                "Launch intent is invalid.",
                retryable=False,
                required_action="correct_launch_intent",
            ) from error

    @staticmethod
    def _intent(value: LaunchIntent) -> LaunchIntent:
        if not isinstance(value, LaunchIntent):
            raise LaunchRuntime._error(
                "launch_intent_invalid",
                "Launch intent is invalid.",
                retryable=False,
                required_action="correct_launch_intent",
            )
        return value

    @staticmethod
    def _operation_request(
        principal_id: str, selection: str, provision: ProvisionedLaunch, intent: LaunchIntent
    ) -> tuple[str, str]:
        """Bind a server-owned operation identity to exact durable launch evidence."""
        canonical_request = _canonical(
            {
                "version": 2,
                "principal_id": principal_id,
                "selection": selection,
                "provision": {
                    "fingerprint": provision.fingerprint(),
                    "ref": provision.ref.model_dump(mode="json"),
                },
                "intent": intent.canonical(),
            }
        )
        request_hash = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
        return request_hash, f"launch-v1-{request_hash}"

    def _resolve(
        self,
        principal: Principal,
        selection: str | None,
        intent: LaunchIntent,
        idempotency_key: str | None = None,
    ) -> ResolvedLaunch:
        self._require_principal(principal)
        if selection is None:
            selection_auto_resolved = True
            selected_selection = None
        else:
            selection_auto_resolved = False
            selected_selection = self._selection(selection)
        intent = self._intent(intent)
        if selected_selection is None:
            provision = self._provisioning.resolve_unique_active_launch(principal)
            selected_selection = provision.ref.selector
        else:
            provision = self._provisioning.resolve_launch(principal, selected_selection)
        selection = selected_selection
        contract = provision.contract
        if not set(intent.allowed_tools).issubset(contract.permissions.tools):
            raise self._error(
                "launch_intent_invalid",
                "Launch intent is invalid.",
                retryable=False,
                required_action="correct_launch_intent",
            )
        if (
            contract.operation_kind,
            provision.adapter_version,
        ) not in self._admission.deliveries.adapters:
            raise self._error(
                "launch_runtime_unavailable",
                "Trusted launch runtime is unavailable.",
                retryable=False,
                required_action="inspect_server_configuration",
            )
        if provision.adapter_version == 2:
            if (
                not isinstance(contract, EffectiveWorkContractV2)
                or len(contract.executable_identities) != 1
                or contract.permissions.network
            ):
                raise self._error(
                    "launch_context_invalid",
                    "Trusted launch context is no longer valid.",
                    retryable=False,
                    required_action="refresh_launch_context",
                )
            backend = self._admission.backends.get(contract.backend)
            if not callable(getattr(backend, "execute_bound_process", None)):
                raise self._error(
                    "launch_runtime_unavailable",
                    "Trusted launch runtime is unavailable.",
                    retryable=False,
                    required_action="inspect_server_configuration",
                )
        request_hash, server_operation_key = self._operation_request(
            principal.id, selection, provision, intent
        )
        idempotency_key = (
            server_operation_key
            if selection_auto_resolved or idempotency_key is None
            else self._idempotency_key(idempotency_key)
        )
        terminal_id = hashlib.sha256(
            ("launch-terminal-v1:" + request_hash + ":" + idempotency_key).encode("utf-8")
        ).hexdigest()[:8]
        assert _TERMINAL_ID.fullmatch(terminal_id)
        launch_payload = {
            "terminal_id": terminal_id,
            "agent_profile": intent.agent_profile,
            "session_name": intent.session_name,
            "message": intent.message,
            "allowed_tools": list(intent.allowed_tools),
        }
        if provision.adapter_version == 2:
            launch_payload["command_token"] = contract.executable_identities[0].command_token
        envelope = WorkDeliveryEnvelope(
            operation_kind=contract.operation_kind,
            adapter_version=provision.adapter_version,
            payload_json=_canonical(launch_payload),
        )
        resolved = ResolvedLaunch(
            principal_id=principal.id,
            selection=selection,
            selection_auto_resolved=selection_auto_resolved,
            job_id=provision.job_id,
            grant_id=provision.grant_id,
            grant_revision=provision.grant_revision,
            contract=contract,
            request_hash=request_hash,
            idempotency_key=idempotency_key,
            envelope=envelope,
            terminal_id=terminal_id,
            lease_seconds=provision.lease_seconds,
            provision=provision,
            provision_fingerprint=provision.fingerprint(),
            _seal="",
            _runtime=self._runtime_identity,
        )
        return replace(resolved, _seal=self._seal_handoff(resolved))

    def resolve_launch(
        self,
        principal: Principal,
        selection: str | None,
        intent: LaunchIntent,
        idempotency_key: str | None = None,
    ) -> ResolvedLaunch:
        """Resolve trusted setup; public ingress omits the legacy internal key override."""
        try:
            return self._resolve(principal, selection, intent, idempotency_key)
        except LaunchRuntimeError:
            raise
        except ProvisionSelectionUnavailable as error:
            raise self._error(
                "launch_context_unavailable",
                "Trusted launch context is unavailable.",
                retryable=False,
                required_action="provision_launch_context",
            ) from error
        except AuthorityDenied as error:
            raise self._error(
                "launch_authority_denied",
                "Verified launch authority is required.",
                retryable=False,
                required_action="reauthorize",
            ) from error
        except ProvisionDenied as error:
            raise self._error(
                "launch_context_unavailable",
                "Trusted launch context is unavailable.",
                retryable=False,
                required_action="provision_launch_context",
            ) from error
        except ProvisionUnavailable as error:
            if str(error) == "durable launch provision is unavailable":
                raise self._error(
                    "launch_context_unavailable",
                    "Trusted launch context is unavailable.",
                    retryable=False,
                    required_action="provision_launch_context",
                ) from error
            raise self._error(
                "launch_context_invalid",
                "Trusted launch context is no longer valid.",
                retryable=False,
                required_action="refresh_launch_context",
            ) from error
        except (
            GrantConflict,
            ContractConflict,
            SnapshotConflict,
            SnapshotUnavailable,
            KnowledgeAccessDenied,
        ) as error:
            raise self._error(
                "launch_context_invalid",
                "Trusted launch context is no longer valid.",
                retryable=False,
                required_action="refresh_launch_context",
            ) from error
        except (SchemaMismatch, DatabaseError) as error:
            raise self._error(
                "launch_store_unavailable",
                "Verified work store is unavailable.",
                retryable=True,
                required_action="retry_same_intent",
            ) from error
        except (TypeError, ValueError) as error:
            raise self._error(
                "launch_context_invalid",
                "Trusted launch context is no longer valid.",
                retryable=False,
                required_action="refresh_launch_context",
            ) from error

    def resolve_replacement(
        self,
        principal: Principal,
        selection: str | None,
        intent: LaunchIntent,
        *,
        work_item_id: str,
        generation: int,
        idempotency_key: str,
    ) -> ResolvedReplacement:
        """Resolve current launch evidence for an explicit planned replacement generation."""
        self._require_principal(principal)
        try:
            work_item_id = _identity(work_item_id, "replacement work item")
            if type(generation) is not int or generation <= 1:
                raise ValueError("replacement generation must follow the initial generation")
            if selection is None:
                raise ValueError("replacement admission requires an explicit launch selector")
            action_key = self._idempotency_key(idempotency_key)
            base = self.resolve_launch(principal, selection, intent)
            retry_key, retry_hash = self._replacement_identity(
                principal_id=principal.id,
                work_item_id=work_item_id,
                generation=generation,
                action_idempotency_key=action_key,
                launch=base,
            )
            payload = json.loads(base.envelope.payload_json)
            terminal_id = hashlib.sha256(
                ("launch-terminal-v1:" + base.request_hash + ":" + retry_key).encode("utf-8")
            ).hexdigest()[:8]
            payload["terminal_id"] = terminal_id
            launch = replace(
                base,
                idempotency_key=retry_key,
                terminal_id=terminal_id,
                envelope=WorkDeliveryEnvelope(
                    operation_kind=base.envelope.operation_kind,
                    adapter_version=base.envelope.adapter_version,
                    payload_json=_canonical(payload),
                ),
                _seal="",
            )
            launch = replace(launch, _seal=self._seal_handoff(launch))

            with self.repository.read_snapshot() as connection:
                work = self.repository._work(connection, work_item_id)
                if (
                    work["job_id"] != launch.job_id
                    or work["operation_kind"] != "launch"
                    or work["origin_protocol"] != "launch-v1"
                    or work["lineage_protocol"] != "legacy"
                    or not work["attempts"]
                ):
                    raise WorkConflict("replacement work is not an original managed launch")
                current = work["attempts"][-1]
                if current["generation"] != generation:
                    raise WorkConflict("replacement generation is no longer current")
                existing = connection.execute(
                    "SELECT retry_admission_key,retry_request_hash,replanned_work_revision,"
                    "planned_attempt_revision FROM work_launch_retry_bindings "
                    "WHERE attempt_id=%s AND generation=%s",
                    (current["id"], generation),
                ).fetchone()
                if existing is None:
                    if work["state"] != "queued" or current["state"] != "planned":
                        raise WorkConflict("replacement is not awaiting explicit admission")
                    work_revision = work["revision"]
                    attempt_revision = current["revision"]
                else:
                    if (
                        existing["retry_admission_key"],
                        existing["retry_request_hash"],
                    ) != (retry_key, retry_hash):
                        raise WorkConflict(
                            "replacement admission identity conflicts with its order"
                        )
                    work_revision = existing["replanned_work_revision"]
                    attempt_revision = existing["planned_attempt_revision"]
                    if current["revision"] < attempt_revision or work["revision"] < work_revision:
                        raise WorkConflict(
                            "replacement durable revision moved behind its admission"
                        )

            resolved = ResolvedReplacement(
                principal_id=principal.id,
                work_item_id=work_item_id,
                generation=generation,
                expected_work_revision=work_revision,
                expected_attempt_id=current["id"],
                planned_attempt_revision=attempt_revision,
                action_idempotency_key=action_key,
                retry_idempotency_key=retry_key,
                retry_request_hash=retry_hash,
                launch=launch,
                _seal="",
                _runtime=self._runtime_identity,
            )
            return replace(resolved, _seal=self._seal_replacement(resolved))
        except LaunchRuntimeError:
            raise
        except (WorkConflict, KeyError, ValueError) as error:
            raise self._error(
                "launch_context_invalid",
                "Trusted replacement context is no longer valid.",
                retryable=False,
                required_action="resolve_replacement_again",
            ) from error
        except (SchemaMismatch, DatabaseError) as error:
            raise self._error(
                "launch_store_unavailable",
                "Verified work store is unavailable.",
                retryable=True,
                required_action="retry_same_replacement",
            ) from error

    def admit_replacement(
        self, principal: Principal, resolved: ResolvedReplacement
    ) -> LaunchReceipt:
        """Explicitly bind and queue one already-planned generation; never dispatch it."""
        self._require_principal(principal)
        if not self._replacement_handoff_is_current(principal, resolved):
            raise self._error(
                "launch_context_invalid",
                "Trusted replacement context is no longer valid.",
                retryable=False,
                required_action="resolve_replacement_again",
            )
        try:
            work = self._admission.replacements.admit(principal, resolved)
            attempt = next(
                attempt
                for attempt in work["attempts"]
                if attempt["generation"] == resolved.generation
            )
            return LaunchReceipt(
                work_item_id=work["id"],
                attempt_id=attempt["id"],
                generation=attempt["generation"],
                state=work["state"],
            )
        except AuthorityDenied as error:
            raise self._error(
                "launch_authority_denied",
                "Verified launch authority is required.",
                retryable=False,
                required_action="reauthorize",
            ) from error
        except ProvisionDenied as error:
            raise self._error(
                "launch_context_invalid",
                "Trusted launch context is no longer valid.",
                retryable=False,
                required_action="refresh_launch_context",
            ) from error
        except WorkConflict as error:
            raise self._error(
                "launch_context_invalid",
                "Trusted replacement context is no longer valid.",
                retryable=False,
                required_action="resolve_replacement_again",
            ) from error
        except (
            GrantConflict,
            ContractConflict,
            SnapshotConflict,
            SnapshotUnavailable,
            KnowledgeAccessDenied,
        ) as error:
            raise self._error(
                "launch_context_invalid",
                "Trusted replacement context is no longer valid.",
                retryable=False,
                required_action="resolve_replacement_again",
            ) from error
        except ProvisionUnavailable as error:
            raise self._error(
                "launch_context_invalid",
                "Trusted launch context is no longer valid.",
                retryable=False,
                required_action="resolve_replacement_again",
            ) from error
        except (SchemaMismatch, DatabaseError) as error:
            raise self._error(
                "launch_store_unavailable",
                "Verified work store is unavailable.",
                retryable=True,
                required_action="retry_same_replacement",
            ) from error
        except (TypeError, ValueError) as error:
            raise self._error(
                "launch_context_invalid",
                "Trusted replacement context is no longer valid.",
                retryable=False,
                required_action="resolve_replacement_again",
            ) from error

    def admit_launch(self, principal: Principal, resolved: ResolvedLaunch) -> LaunchReceipt:
        """Admit one resolved launch through the sole durable owner, without dispatching."""
        self._require_principal(principal)
        if (
            not isinstance(resolved, ResolvedLaunch)
            or resolved._runtime is not self._runtime_identity
            or resolved.principal_id != principal.id
            or not self._handoff_is_current(resolved)
        ):
            raise self._error(
                "launch_context_invalid",
                "Trusted launch context is no longer valid.",
                retryable=False,
                required_action="resolve_launch_again",
            )
        try:
            work = self._admission.admit(
                principal=principal,
                job_id=resolved.job_id,
                idempotency_key=resolved.idempotency_key,
                request_hash=resolved.request_hash,
                grant_id=resolved.grant_id,
                expected_grant_revision=resolved.grant_revision,
                contract=resolved.contract,
                lease_seconds=resolved.lease_seconds,
                delivery=resolved.envelope,
                launch_origin=resolved.provision,
                launch_fingerprint=resolved.provision_fingerprint,
                require_unique_selection=resolved.selection_auto_resolved,
            )
            attempt = work["attempts"][0]
            return LaunchReceipt(
                work_item_id=work["id"],
                attempt_id=attempt["id"],
                generation=attempt["generation"],
                state=work["state"],
            )
        except WorkConflict as error:
            raise self._error(
                "launch_idempotency_conflict",
                "Launch operation conflicts with an existing server-owned identity.",
                retryable=False,
                required_action="inspect_existing_launch",
            ) from error
        except AuthorityDenied as error:
            raise self._error(
                "launch_authority_denied",
                "Verified launch authority is required.",
                retryable=False,
                required_action="reauthorize",
            ) from error
        except ProvisionSelectionUnavailable as error:
            raise self._error(
                "launch_context_unavailable",
                "Trusted launch context is unavailable.",
                retryable=False,
                required_action="provision_launch_context",
            ) from error
        except ProvisionUnavailable as error:
            raise self._error(
                "launch_context_invalid",
                "Trusted launch context is no longer valid.",
                retryable=False,
                required_action="resolve_launch_again",
            ) from error
        except (
            GrantConflict,
            ContractConflict,
            SnapshotConflict,
            SnapshotUnavailable,
            KnowledgeAccessDenied,
        ) as error:
            raise self._error(
                "launch_context_invalid",
                "Trusted launch context is no longer valid.",
                retryable=False,
                required_action="resolve_launch_again",
            ) from error
        except (SchemaMismatch, DatabaseError) as error:
            raise self._error(
                "launch_store_unavailable",
                "Verified work store is unavailable.",
                retryable=True,
                required_action="retry_same_intent",
            ) from error
        except ValueError as error:
            raise self._error(
                "launch_runtime_unavailable",
                "Trusted launch runtime is unavailable.",
                retryable=False,
                required_action="inspect_server_configuration",
            ) from error

    async def dispatch_registered_next(self) -> dict | None:
        """Dispatch one durable order through WorkAdmission's registered backend only.

        This is an internal server lifecycle operation, not part of launch HTTP
        ingress. Queue selection and WorkService uncertainty handling stay with
        WorkAdmission; admission continues to return only its queued receipt.
        """
        return await self._admission.dispatch_registered_next()

    async def prepare_registered_dispatch(self):
        """Admit one registered order without waiting for its external delivery."""
        return await self._admission.prepare_registered_dispatch()

    async def deliver_registered_dispatch(self, prepared) -> dict:
        """Complete the exact single-use handoff returned by preparation."""
        return await self._admission.deliver_registered_dispatch(prepared)

    async def abandon_registered_dispatch(self, prepared) -> None:
        """Reconcile a committed handoff that could not acquire a delivery task."""
        await self._admission.abandon_registered_dispatch(prepared)

    async def wait_for_registered_dispatch_cleanup(self) -> None:
        """Join canceled preparation cleanup before shared runtime shutdown."""
        await self._admission.wait_for_dispatch_cleanup()
