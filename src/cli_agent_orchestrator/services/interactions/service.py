"""Authority-checked service for durable agent/human interactions."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import re
import threading
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, Mapping, Sequence, cast
from uuid import UUID, uuid4

from pydantic import ValidationError

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients.interaction_repository import (
    InteractionRepository,
    InteractionSchemaMismatch,
)
from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget, Record
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security.auth import is_verified_principal
from cli_agent_orchestrator.services.interactions.capability import (
    REQUIRED_MCP_TOOL,
    answer_receipt,
    canonical_tool_fingerprint,
    capability_hash,
    cursor_decode,
    cursor_encode,
    digest_json,
    issue_capability,
    receipt_hash,
)
from cli_agent_orchestrator.services.interactions.models import (
    AgentActor,
    AnswerAck,
    AnswerReceipt,
    CreatedGroup,
    CreateGroupRequest,
    DelegationReceipt,
    DeliveryClaim,
    DeliveryReceipt,
    HistoryEvent,
    InteractionAnswerPayload,
    InteractionCapacityExceeded,
    InteractionConflict,
    InteractionError,
    InteractionForbidden,
    InteractionInvalid,
    InteractionNotFound,
    InteractionOption,
    InteractionPage,
    InteractionStale,
    IssuedCapability,
    NativeObservationPin,
    NativeWaitStatus,
    ObservationReceipt,
    ProviderCapability,
    QuestionInput,
    ReturnedAnswer,
    SourceStatus,
    WaitResult,
    WaitStatus,
    WorkSourceBinding,
)
from cli_agent_orchestrator.services.interactions.policy import InteractionPolicy

_AGENT_SEAL = object()
_MAX_ACTIVE = 100
_MAX_PER_SOURCE = 20
_DEFAULT_TTL = 1800
_HARD_TTL = 86400
_MAX_WAIT_SECONDS = 30
_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
InteractionRow = Record | Mapping[str, Any]
SourceStatusState = Literal["current", "stale_turn", "replaced", "inactive", "unregistered"]


SourceProjection = dict[str, object]
"""Safe source identity returned to server code without a capability token."""


_SECRET_PATTERNS = (
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{2,}\b"),
    re.compile(r"(?i)\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(?:bearer|password|api[_ -]?key|access[_ -]?token)\s*[:=]\s*\S+"),
    re.compile(
        r"(?i)\b(?:password|passphrase|contraseña|clave|token de acceso)(?:\s+(?:is|es)\s+|[:=]\s*)\S+"
    ),
)
_CREDENTIAL_PROMPT = re.compile(
    r"(?i)\b(?:password|passphrase|contrase(?:ñ|n)a|contrase(?:ñ|n)a de un solo uso|clave|secret|api[_ -]?key|access token|credential|pin|passcode|mfa|2fa|otp|one[- ]time (?:code|password)|verification[ -]?code|login[ -]?code|backup[ -]?code|security[ -]?code|recovery[ -]?code|authentication code|c[oó]digo de (?:un solo uso|verificaci[oó]n|seguridad|recuperaci[oó]n|autenticaci[oó]n|acceso|inicio de sesi[oó]n|respaldo))\b"
)


def _now() -> float:
    return time.time()


def _datetime(value: float | int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _required_datetime(value: float | int | None, field: str) -> datetime:
    result = _datetime(value)
    if result is None:
        raise InteractionSchemaMismatch(f"stored interaction {field} is unexpectedly empty")
    return result


def _is_secret(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value)
    return any(pattern.search(normalized) for pattern in _SECRET_PATTERNS)


def _is_credential_prompt(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _CREDENTIAL_PROMPT.search(normalized) is not None


def _allows_cao_mcp(tools: Sequence[str]) -> bool:
    """Use CAO's shared MCP selector semantics (exact, glob, or `*`)."""
    from cli_agent_orchestrator.utils.tool_mapping import granted_mcp_servers

    return bool(granted_mcp_servers(tools, [REQUIRED_MCP_TOOL.removeprefix("@")]))


def _mark_cycle_edges(edges: list[dict[str, object]]) -> None:
    """Mark only edges whose endpoints are in the same cyclic SCC."""
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        adjacency.setdefault(str(edge["from"]), []).append(str(edge["to"]))
        adjacency.setdefault(str(edge["to"]), [])
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    components: list[set[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in adjacency[node]:
            if target not in indexes:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[target])
        if lowlinks[node] == indexes[node]:
            component: set[str] = set()
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.add(member)
                if member == node:
                    break
            components.append(component)

    for node in adjacency:
        if node not in indexes:
            visit(node)
    component_by_node = {
        node: component_index
        for component_index, component in enumerate(components)
        for node in component
    }
    cyclic_components = {
        component_index
        for component_index, component in enumerate(components)
        if len(component) > 1
    }
    cyclic_components.update(
        component_by_node[str(edge["from"])] for edge in edges if edge["from"] == edge["to"]
    )
    for edge in edges:
        from_component = component_by_node[str(edge["from"])]
        edge["cycle"] = (
            from_component == component_by_node[str(edge["to"])]
            and from_component in cyclic_components
        )


def _identifier(value: object, label: str, *, minimum: int = 1, maximum: int = 128) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum or value != value.strip():
        raise InteractionInvalid(f"invalid {label}")
    return value


def _payload(value: object) -> InteractionAnswerPayload:
    try:
        return (
            value
            if isinstance(value, InteractionAnswerPayload)
            else InteractionAnswerPayload.model_validate(value)
        )
    except (ValidationError, TypeError, ValueError) as error:
        raise InteractionInvalid("invalid typed answer") from error


def _question(value: object) -> QuestionInput:
    try:
        return value if isinstance(value, QuestionInput) else QuestionInput.model_validate(value)
    except (ValidationError, TypeError, ValueError) as error:
        raise InteractionInvalid("invalid typed question") from error


def _work_binding(value: object | None) -> WorkSourceBinding | None:
    if value is None:
        return None
    if isinstance(value, WorkSourceBinding):
        result = value
    else:
        names = (
            "job_id",
            "work_item_id",
            "attempt_id",
            "attempt_generation",
            "contract_hash",
            "grant_id",
            "grant_revision",
            "principal_id",
        )
        try:
            data = {name: getattr(value, name) for name in names if hasattr(value, name)}
            if "attempt_generation" not in data and hasattr(value, "generation"):
                data["attempt_generation"] = value.generation
            result = WorkSourceBinding(**data)
        except (TypeError, ValueError) as error:
            raise InteractionInvalid("invalid server Work binding") from error
    if (
        not all(
            isinstance(item, str) and item
            for item in (
                result.job_id,
                result.work_item_id,
                result.attempt_id,
                result.contract_hash,
                result.grant_id,
            )
        )
        or type(result.attempt_generation) is not int
        or result.attempt_generation < 1
        or type(result.grant_revision) is not int
        or result.grant_revision < 1
        or re.fullmatch(r"[0-9a-f]{64}", result.contract_hash) is None
    ):
        raise InteractionInvalid("incomplete server Work binding")
    return result


class InteractionService:
    """The single service authority for source-bound questions and receipts."""

    def __init__(
        self,
        repository: InteractionRepository,
        work_repository: WorkRepository | None = None,
        policy: InteractionPolicy | None = None,
    ):
        self.repository = repository
        self.work_repository = work_repository or repository.work_repository
        if self.work_repository.target.identity != repository.target.identity:
            raise ValueError("interaction and Work repositories must share one PostgreSQL target")
        self.policy = policy or InteractionPolicy()
        self._schema_ready = False
        self._recovery_done = False
        self._lock = threading.RLock()

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._lock:
            if not self._schema_ready:
                self.repository.initialize()
                self._schema_ready = True

    @contextmanager
    def _transaction(self):
        self._ensure_schema()
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            yield connection

    @contextmanager
    def _read(self):
        self._ensure_schema()
        with self.repository.read_connection() as connection:
            self.repository._verify(connection)
            yield connection

    def _expire_before_read(self) -> None:
        """Apply bounded expiry/escalation transitions before protected reads."""
        with self._transaction() as connection:
            self._expire_due(connection, _now())

    def initialize(self) -> None:
        """Application startup hook; only this hook recovers in-flight claims."""
        self._ensure_schema()
        with self._lock:
            if self._recovery_done:
                return
            self.recover_inflight_claims()
            self._recovery_done = True

    @staticmethod
    def _source_projection(row: Record) -> SourceProjection:
        return {
            "terminal_id": row["terminal_id"],
            "incarnation_id": row["incarnation_id"],
            "provider": row["provider"],
            "provider_version": row["provider_version"],
            "execution_mode": row["execution_mode"],
        }

    @staticmethod
    def _terminal(connection: Connection, terminal_id: str) -> Record | None:
        return cast(
            Record | None,
            connection.execute("SELECT * FROM terminals WHERE id=%s", (terminal_id,)).fetchone(),
        )

    @staticmethod
    def _current_incarnation(connection: Connection, session_name: str) -> str | None:
        row = connection.execute(
            "SELECT incarnation_id FROM session_incarnations WHERE session_name=%s",
            (session_name,),
        ).fetchone()
        return cast(str | None, row["incarnation_id"]) if row is not None else None

    def _assert_terminal_binding(
        self,
        connection: Connection,
        *,
        terminal_id: str,
        incarnation_id: str,
        session_name: str,
        provider: str,
        require_tool: bool = True,
    ) -> tuple[Record, tuple[str, ...], str]:
        terminal = self._terminal(connection, terminal_id)
        if terminal is None:
            raise InteractionNotFound("terminal source does not exist")
        if (
            terminal["session_incarnation_id"] != incarnation_id
            or terminal["tmux_session"] != session_name
            or terminal["provider"] != provider
        ):
            raise InteractionStale("terminal incarnation or provider changed")
        current = self._current_incarnation(connection, session_name)
        if current is not None and current != incarnation_id:
            raise InteractionStale("session incarnation was replaced")
        fingerprint, tools = canonical_tool_fingerprint(terminal["allowed_tools"])
        if require_tool and not _allows_cao_mcp(tools):
            raise InteractionForbidden("current profile does not allow the CAO MCP server")
        return terminal, tools, fingerprint

    def _validate_source_row(
        self,
        connection: Connection,
        source: InteractionRow,
        *,
        require_capability: bool = False,
        require_tool: bool = True,
        terminal_row: InteractionRow | None = None,
        terminal_loaded: bool = False,
        current_incarnation: str | None = None,
        incarnation_loaded: bool = False,
        work_unmanaged: bool = False,
    ) -> None:
        if source["revoked_at"] is not None:
            raise InteractionForbidden("source capability has been revoked")
        if require_capability and not source["capability_available"]:
            raise InteractionForbidden("source capability was not installed for this terminal")
        terminal = (
            terminal_row if terminal_loaded else self._terminal(connection, source["terminal_id"])
        )
        if terminal is None:
            raise InteractionStale("source terminal no longer exists")
        if (
            terminal["session_incarnation_id"] != source["incarnation_id"]
            or terminal["tmux_session"] != source["session_name"]
            or terminal["provider"] != source["provider"]
        ):
            raise InteractionStale("source terminal incarnation was replaced")
        current = (
            current_incarnation
            if incarnation_loaded
            else self._current_incarnation(connection, source["session_name"])
        )
        if current is not None and current != source["incarnation_id"]:
            raise InteractionStale("source session incarnation was replaced")
        fingerprint, tools = canonical_tool_fingerprint(terminal["allowed_tools"])
        if require_tool and not _allows_cao_mcp(tools):
            raise InteractionForbidden("current profile no longer allows the CAO MCP server")
        if fingerprint != source["allowed_tool_fingerprint"]:
            raise InteractionForbidden("source profile/tool grant changed")
        if not work_unmanaged:
            self._validate_work(connection, source)

    def _validate_work(self, connection: Connection, source: InteractionRow) -> None:
        work_table = connection.execute(
            "SELECT to_regclass(%s) AS relation", ("work_attempts",)
        ).fetchone()
        if work_table is None or work_table["relation"] is None:
            if source["attempt_id"] is not None or source["execution_mode"] in {
                "work_managed",
                "work-managed",
                "managed_work",
            }:
                raise InteractionForbidden("Work-owned source has no verifiable Work schema")
            return
        # InteractionService only calls this with a connection yielded by its
        # _transaction()/_read() contexts. Those contexts delegate to the
        # WorkRepository's verified transaction/read snapshot, so repeating its
        # full schema fingerprint check here would query the PostgreSQL catalogs
        # once per source row. Keep the durable per-source Work checks below.
        attempts = connection.execute(
            "SELECT id,generation,state FROM work_attempts WHERE terminal_id=%s",
            (source["terminal_id"],),
        ).fetchall()
        active_attempts = [
            attempt
            for attempt in attempts
            if attempt["state"] in {"planned", "sent", "acknowledged", "running"}
        ]
        if source["attempt_id"] is None:
            if attempts or source["execution_mode"] in {
                "work_managed",
                "work-managed",
                "managed_work",
            }:
                raise InteractionForbidden(
                    "a Work-owned terminal requires an exact interaction binding"
                )
            return
        if (
            len(active_attempts) != 1
            or active_attempts[0]["id"] != source["attempt_id"]
            or (active_attempts[0]["generation"] != source["attempt_generation"])
        ):
            raise InteractionForbidden(
                "interaction source does not match the terminal's active Work attempt"
            )
        try:
            from cli_agent_orchestrator.models.work_contract import EffectiveWorkContractV2
            from cli_agent_orchestrator.security.auth import SCOPE_WRITE, _verified_principal
            from cli_agent_orchestrator.services.work_authority import WorkAuthority
            from cli_agent_orchestrator.services.work_contract import WorkContracts

            binding = WorkContracts(self.work_repository)._revalidate_order(
                connection, source["attempt_id"], generation=source["attempt_generation"]
            )
            identity = connection.execute(
                "SELECT issuer,subject,kind FROM work_principals WHERE id=%s",
                (binding.principal_id,),
            ).fetchone()
            if identity is None:
                raise ValueError("registered Work Principal is missing")
            principal = _verified_principal(
                identity["issuer"], identity["subject"], [SCOPE_WRITE], identity["kind"]
            )
            if principal.id != binding.principal_id:
                raise ValueError("registered Work Principal identity mismatch")
            WorkAuthority(self.work_repository)._authorize(
                connection,
                principal,
                job_id=binding.job_id,
                grant_id=binding.grant_id,
                expected_grant_revision=binding.grant_revision,
                provider=binding.contract.provider,
                requested_permissions=WorkContracts._permissions(binding.contract),
            )
        except Exception as error:
            raise InteractionForbidden(
                "current Work attempt or grant is no longer authorized"
            ) from error
        if isinstance(binding.contract, EffectiveWorkContractV2):
            raise InteractionForbidden("Work V2 process agents cannot use terminal interactions")
        if (
            binding.job_id != source["work_job_id"]
            or binding.work_item_id != source["work_item_id"]
            or binding.attempt_id != source["attempt_id"]
            or binding.generation != source["attempt_generation"]
            or binding.contract_hash != source["contract_hash"]
            or binding.grant_id != source["grant_id"]
            or binding.grant_revision != source["grant_revision"]
            or (
                source["owner_principal_id"]
                and binding.principal_id != source["owner_principal_id"]
            )
            or REQUIRED_MCP_TOOL not in binding.contract.permissions.tools
        ):
            raise InteractionForbidden(
                "Work source binding does not match the current exact contract"
            )

    def _current_source_by_capability(
        self, connection: Connection, token: object, *, for_update: bool = False
    ) -> tuple[Record, AgentActor]:
        try:
            digest = capability_hash(token)
        except ValueError as error:
            raise InteractionForbidden("invalid source capability") from error
        statement = "SELECT * FROM interaction_sources WHERE capability_hash=%s"
        if for_update:
            statement += " FOR UPDATE"
        source = connection.execute(statement, (digest,)).fetchone()
        if source is None:
            raise InteractionForbidden("unknown source capability")
        self._validate_source_row(connection, source, require_capability=True)
        actor = AgentActor(
            terminal_id=source["terminal_id"],
            incarnation_id=source["incarnation_id"],
            source_id=f"{source['terminal_id']}:{source['incarnation_id']}",
            turn_generation=source["turn_generation"],
            _seal=_AGENT_SEAL,
            _cap_hash=digest,
        )
        return source, actor

    def _lock_sources(
        self, connection: Connection, identities: Iterable[tuple[str, str]]
    ) -> dict[tuple[str, str], Record]:
        """Lock source rows in a stable order before changing source-bound state."""
        locked: dict[tuple[str, str], Record] = {}
        for terminal_id, incarnation_id in sorted(set(identities)):
            source = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s FOR UPDATE",
                (terminal_id, incarnation_id),
            ).fetchone()
            if source is None:
                raise InteractionNotFound("interaction source does not exist")
            self._validate_source_row(connection, source, require_capability=False)
            locked[(terminal_id, incarnation_id)] = source
        return locked

    def _lock_question_for_mutation(
        self,
        connection: Connection,
        question_id: str,
        version: int | None = None,
    ) -> tuple[Record, dict[str, Any], Record]:
        """Lock a question's source, then its mutable row, and reload its version."""
        initial: Record | None = connection.execute(
            "SELECT * FROM interaction_questions WHERE question_id=%s", (question_id,)
        ).fetchone()
        if initial is None:
            raise InteractionNotFound("question does not exist")
        source = self._lock_question_source(connection, initial)
        question, details = self._load_question(connection, question_id, version, for_update=True)
        return question, details, source

    def _live_actor(self, connection: Connection, actor: object) -> tuple[Record, AgentActor]:
        if type(actor) is not AgentActor or actor._seal is not _AGENT_SEAL:
            raise InteractionForbidden("a server-authenticated agent actor is required")
        source = connection.execute(
            "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s AND capability_hash=%s",
            (actor.terminal_id, actor.incarnation_id, actor._cap_hash),
        ).fetchone()
        if source is None:
            raise InteractionForbidden("agent actor is no longer current")
        self._validate_source_row(connection, source, require_capability=True)
        if actor.turn_generation != source["turn_generation"]:
            raise InteractionStale("agent actor belongs to an earlier source turn")
        return source, actor

    def authenticate_agent(self, capability: object) -> AgentActor:
        with self._read() as connection:
            _, actor = self._current_source_by_capability(connection, capability)
            return actor

    validate_agent = authenticate_agent

    def register_source(
        self,
        *,
        terminal_id: str,
        incarnation_id: str,
        session_name: str,
        provider: str,
        principal: object | None = None,
        provider_version: str | None = None,
        execution_mode: str = "local",
        profile_revision: str | None = None,
        allowed_tool_fingerprint: str | None = None,
        assignment_id: str | None = None,
        work_binding: WorkSourceBinding | object | None = None,
        capability_available: bool = True,
        _registration_purpose: Literal["capability", "observer"] = "capability",
    ) -> IssuedCapability:
        """Register a source and issue its token.

        Runtime installation can keep the token unusable until its private
        file and provider path have both been installed.
        """
        terminal_id = _identifier(terminal_id, "terminal id")
        incarnation_id = _identifier(incarnation_id, "incarnation id")
        session_name = _identifier(session_name, "session name")
        provider = _identifier(provider, "provider")
        execution_mode = _identifier(execution_mode, "execution mode")
        if type(capability_available) is not bool:
            raise InteractionInvalid("capability availability must be a boolean")
        if _registration_purpose not in {"capability", "observer"}:
            raise InteractionInvalid("unknown source registration purpose")
        if _registration_purpose == "observer" and (
            capability_available or execution_mode != "local" or work_binding is not None
        ):
            raise InteractionForbidden(
                "observer-only sources must be local and cannot carry a Work binding or capability"
            )
        if provider_version is not None:
            provider_version = _identifier(provider_version, "provider version", maximum=256)
        if principal is not None and not is_verified_principal(principal):
            raise InteractionForbidden("source owner must be a verified Principal")
        binding = _work_binding(work_binding)
        token_hash: str
        token: str
        token, token_hash = issue_capability()
        now = _now()
        with self._transaction() as connection:
            connection.execute(
                "SELECT id FROM terminals WHERE id=%s FOR UPDATE", (terminal_id,)
            ).fetchone()
            terminal, _, fingerprint = self._assert_terminal_binding(
                connection,
                terminal_id=terminal_id,
                incarnation_id=incarnation_id,
                session_name=session_name,
                provider=provider,
            )
            if allowed_tool_fingerprint is not None and allowed_tool_fingerprint != fingerprint:
                raise InteractionForbidden(
                    "supplied profile fingerprint does not match current terminal"
                )
            source_owner = principal.id if principal is not None else None
            owner_kind = "authenticated_creation" if source_owner else "unowned"
            parent_terminal_id = parent_incarnation_id = validated_assignment = None
            # Always inspect durable Work ownership, including when the caller
            # supplied no binding.  An omitted DTO field must not turn a
            # Work-owned (or historically Work-owned) terminal into a local
            # source.
            provisional = {
                "terminal_id": terminal_id,
                "execution_mode": execution_mode,
                "attempt_id": binding.attempt_id if binding else None,
                "attempt_generation": binding.attempt_generation if binding else None,
                "work_job_id": binding.job_id if binding else None,
                "work_item_id": binding.work_item_id if binding else None,
                "contract_hash": binding.contract_hash if binding else None,
                "grant_id": binding.grant_id if binding else None,
                "grant_revision": binding.grant_revision if binding else None,
                "owner_principal_id": binding.principal_id if binding else None,
            }
            self._validate_work(connection, provisional)
            if binding is not None:
                if principal is not None and binding.principal_id not in {None, principal.id}:
                    raise InteractionForbidden("Work Principal does not match creation Principal")
                if source_owner is None and binding.principal_id:
                    source_owner, owner_kind = binding.principal_id, "work_principal"
            if assignment_id is not None:
                assignment_id = _identifier(assignment_id, "assignment id")
                inherited = self._validated_assignment_owner(
                    connection, assignment_id=assignment_id, child_terminal_id=terminal_id
                )
                if inherited is not None:
                    parent_terminal_id = inherited["terminal_id"]
                    parent_incarnation_id = inherited["incarnation_id"]
                    validated_assignment = assignment_id
                    if source_owner is None:
                        source_owner = inherited["owner_principal_id"]
                        owner_kind = "validated_parent_inheritance"
            if source_owner is None:
                owner_kind = "unowned"
            profile_revision = profile_revision or terminal["agent_profile"] or "unknown"
            existing = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s FOR UPDATE",
                (terminal_id, incarnation_id),
            ).fetchone()
            if existing is not None:
                if existing["owner_principal_id"] is not None:
                    if source_owner is not None and existing["owner_principal_id"] != source_owner:
                        source_owner = existing["owner_principal_id"]
                        owner_kind = existing["owner_source"]
                    else:
                        source_owner = existing["owner_principal_id"]
                        owner_kind = existing["owner_source"]
                connection.execute(
                    "UPDATE interaction_sources SET provider=%s,provider_version=%s,execution_mode=%s,"
                    "profile_revision=%s,allowed_tool_fingerprint=%s,owner_principal_id=%s,owner_source=%s,"
                    "parent_terminal_id=%s,parent_incarnation_id=%s,assignment_id=%s,work_job_id=%s,work_item_id=%s,"
                    "attempt_id=%s,attempt_generation=%s,contract_hash=%s,grant_id=%s,grant_revision=%s,"
                    "capability_hash=%s,capability_available=%s,revoked_at=NULL WHERE terminal_id=%s AND incarnation_id=%s",
                    (
                        provider,
                        provider_version,
                        execution_mode,
                        profile_revision,
                        fingerprint,
                        source_owner,
                        owner_kind,
                        parent_terminal_id,
                        parent_incarnation_id,
                        validated_assignment,
                        binding.job_id if binding else None,
                        binding.work_item_id if binding else None,
                        binding.attempt_id if binding else None,
                        binding.attempt_generation if binding else None,
                        binding.contract_hash if binding else None,
                        binding.grant_id if binding else None,
                        binding.grant_revision if binding else None,
                        token_hash,
                        bool(capability_available),
                        terminal_id,
                        incarnation_id,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE interaction_sources SET revoked_at=%s WHERE terminal_id=%s AND incarnation_id<>%s AND revoked_at IS NULL",
                    (now, terminal_id, incarnation_id),
                )
                self._stale_replaced_sources(connection, terminal_id, incarnation_id, now)
                connection.execute(
                    "INSERT INTO interaction_sources(terminal_id,incarnation_id,session_name,provider,"
                    "provider_version,execution_mode,profile_revision,allowed_tool_fingerprint,owner_principal_id,"
                    "owner_source,parent_terminal_id,parent_incarnation_id,assignment_id,work_job_id,work_item_id,"
                    "attempt_id,attempt_generation,contract_hash,grant_id,grant_revision,turn_generation,"
                    "capability_hash,capability_available,created_at,revoked_at) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,NULL)",
                    (
                        terminal_id,
                        incarnation_id,
                        session_name,
                        provider,
                        provider_version,
                        execution_mode,
                        profile_revision,
                        fingerprint,
                        source_owner,
                        owner_kind,
                        parent_terminal_id,
                        parent_incarnation_id,
                        validated_assignment,
                        binding.job_id if binding else None,
                        binding.work_item_id if binding else None,
                        binding.attempt_id if binding else None,
                        binding.attempt_generation if binding else None,
                        binding.contract_hash if binding else None,
                        binding.grant_id if binding else None,
                        binding.grant_revision if binding else None,
                        token_hash,
                        bool(capability_available),
                        now,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
                (terminal_id, incarnation_id),
            ).fetchone()
            self.repository.append_event(
                connection,
                event_type="interaction.source_registered",
                source_terminal_id=terminal_id,
                source_incarnation_id=incarnation_id,
                actor_kind="system",
                actor_id=None,
                reason_code=(
                    "observer_source_registered"
                    if _registration_purpose == "observer"
                    else (
                        "source_capability_issued"
                        if capability_available
                        else "source_capability_pending_installation"
                    )
                ),
                metadata={"provider": provider, "execution_mode": execution_mode},
            )
            self._recompute_wait_cycles(connection)
            return IssuedCapability(token=token, source=self._source_projection(row))

    def register_observer_source(
        self,
        *,
        terminal_id: str,
        incarnation_id: str,
        session_name: str,
        provider: str,
        principal: object | None = None,
        provider_version: str | None = None,
        profile_revision: str | None = None,
        allowed_tool_fingerprint: str | None = None,
        assignment_id: str | None = None,
    ) -> SourceProjection:
        """Register a local native-observation source without issuing access.

        The underlying registration and source event share one transaction.
        The generated hash is never returned to runtime code, and this source
        cannot authenticate agent requests or expose the explicit pull channel.
        """
        issued = self.register_source(
            terminal_id=terminal_id,
            incarnation_id=incarnation_id,
            session_name=session_name,
            provider=provider,
            principal=principal,
            provider_version=provider_version,
            execution_mode="local",
            profile_revision=profile_revision,
            allowed_tool_fingerprint=allowed_tool_fingerprint,
            assignment_id=assignment_id,
            work_binding=None,
            capability_available=False,
            _registration_purpose="observer",
        )
        return issued.source

    def activate_capability(
        self,
        terminal_id: str,
        incarnation_id: str,
        expected_token: str,
    ) -> bool:
        """Enable a pending token after secure runtime installation.

        The exact token hash, current terminal/profile, and Work binding are
        revalidated in the same transaction as the activation CAS.
        """
        terminal_id = _identifier(terminal_id, "terminal id")
        incarnation_id = _identifier(incarnation_id, "incarnation id")
        try:
            expected_hash = capability_hash(expected_token)
        except ValueError as error:
            raise InteractionForbidden("invalid expected source capability") from error
        with self._transaction() as connection:
            source = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s FOR UPDATE",
                (terminal_id, incarnation_id),
            ).fetchone()
            if source is None:
                raise InteractionNotFound("interaction source does not exist")
            self._validate_source_row(connection, source, require_capability=False)
            if not hmac.compare_digest(str(source["capability_hash"]), expected_hash):
                raise InteractionStale(
                    "source capability changed before installation was activated"
                )
            if source["capability_available"]:
                return False
            updated = connection.execute(
                "UPDATE interaction_sources SET capability_available=TRUE WHERE terminal_id=%s AND incarnation_id=%s "
                "AND capability_hash=%s AND capability_available=FALSE AND revoked_at IS NULL",
                (terminal_id, incarnation_id, expected_hash),
            )
            if updated.rowcount != 1:
                raise InteractionStale(
                    "source capability changed before installation was activated"
                )
            self.repository.append_event(
                connection,
                event_type="interaction.source_capability_activated",
                source_terminal_id=terminal_id,
                source_incarnation_id=incarnation_id,
                actor_kind="system",
                actor_id=None,
                reason_code="capability_installation_succeeded",
                metadata={
                    "terminal_id": terminal_id,
                    "incarnation_id": incarnation_id,
                    "provider": source["provider"],
                    "execution_mode": source["execution_mode"],
                },
            )
            return True

    def mark_capability_unavailable(
        self,
        terminal_id: str,
        incarnation_id: str,
        expected_token: str,
    ) -> bool:
        """Disable only MCP capability use when secure token installation failed.

        The source remains current for human/native manual handling. The expected
        token is compared by its digest and is never persisted or emitted.
        """
        terminal_id = _identifier(terminal_id, "terminal id")
        incarnation_id = _identifier(incarnation_id, "incarnation id")
        try:
            expected_hash = capability_hash(expected_token)
        except ValueError as error:
            raise InteractionForbidden("invalid expected source capability") from error
        with self._transaction() as connection:
            source = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s FOR UPDATE",
                (terminal_id, incarnation_id),
            ).fetchone()
            if source is None:
                raise InteractionNotFound("interaction source does not exist")
            self._validate_source_row(connection, source, require_capability=False)
            if not hmac.compare_digest(str(source["capability_hash"]), expected_hash):
                raise InteractionStale(
                    "source capability changed before installation failure was recorded"
                )
            if not source["capability_available"]:
                return False
            updated = connection.execute(
                "UPDATE interaction_sources SET capability_available=FALSE WHERE terminal_id=%s AND incarnation_id=%s "
                "AND capability_hash=%s AND capability_available=TRUE",
                (terminal_id, incarnation_id, expected_hash),
            )
            if updated.rowcount != 1:
                raise InteractionStale(
                    "source capability changed before installation failure was recorded"
                )
            self.repository.append_event(
                connection,
                event_type="interaction.source_capability_unavailable",
                source_terminal_id=terminal_id,
                source_incarnation_id=incarnation_id,
                actor_kind="system",
                actor_id=None,
                reason_code="capability_installation_failed",
                metadata={
                    "terminal_id": terminal_id,
                    "incarnation_id": incarnation_id,
                    "provider": source["provider"],
                    "execution_mode": source["execution_mode"],
                },
            )
            return True

    def _validated_assignment_owner(
        self,
        connection: Connection,
        *,
        assignment_id: str,
        child_terminal_id: str,
    ) -> Record | None:
        relation = connection.execute(
            "SELECT to_regclass(%s) AS relation", ("assignment_intents",)
        ).fetchone()
        if relation is None or relation["relation"] is None:
            return None
        row: Record | None = connection.execute(
            "SELECT parent_terminal_id,parent_incarnation_id,state,result_json FROM assignment_intents WHERE assignment_id=%s",
            (assignment_id,),
        ).fetchone()
        if row is None or row["state"] != "submitted":
            return None
        try:
            result = json.loads(row["result_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if result.get("success") is not True or result.get("terminal_id") != child_terminal_id:
            return None
        if result.get("assignment_id") != assignment_id:
            return None
        parent: Record | None = connection.execute(
            "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s AND revoked_at IS NULL",
            (row["parent_terminal_id"], row["parent_incarnation_id"]),
        ).fetchone()
        if parent is None or parent["owner_principal_id"] is None:
            return None
        try:
            # Owner inheritance depends on a sealed, current source and exact
            # submitted assignment proof, not on that source having an MCP
            # token installed. Native/manual-only owners are valid principals.
            self._validate_source_row(connection, parent, require_capability=False)
        except (InteractionError, InteractionSchemaMismatch):
            return None
        return parent

    def _stale_replaced_sources(
        self, connection: Connection, terminal_id: str, new_incarnation_id: str, now: float
    ) -> None:
        rows = connection.execute(
            "SELECT incarnation_id FROM interaction_sources WHERE terminal_id=%s AND incarnation_id<>%s "
            "ORDER BY incarnation_id FOR UPDATE",
            (terminal_id, new_incarnation_id),
        ).fetchall()
        for source in rows:
            incarnation = source["incarnation_id"]
            connection.execute(
                "UPDATE interaction_questions SET lifecycle='stale' WHERE terminal_id=%s AND incarnation_id=%s AND lifecycle IN ('open','manual_only')",
                (terminal_id, incarnation),
            )
            connection.execute(
                "UPDATE interaction_groups SET lifecycle='stale',version=version+1 WHERE terminal_id=%s AND incarnation_id=%s AND lifecycle IN ('open','cycle_blocked')",
                (terminal_id, incarnation),
            )
            connection.execute(
                "UPDATE interaction_waits SET state='stale',released_at=%s WHERE terminal_id=%s AND incarnation_id=%s AND state IN ('waiting','cycle_blocked')",
                (now, terminal_id, incarnation),
            )

    def source_status(
        self,
        terminal_id: str,
        incarnation_id: str | None = None,
        turn_generation: int | None = None,
    ) -> SourceStatus:
        with self._read() as connection:
            source = None
            if incarnation_id is not None:
                source = connection.execute(
                    "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
                    (terminal_id, incarnation_id),
                ).fetchone()
            else:
                source = connection.execute(
                    "SELECT * FROM interaction_sources WHERE terminal_id=%s ORDER BY created_at DESC LIMIT 1",
                    (terminal_id,),
                ).fetchone()
            if source is None:
                exists = connection.execute(
                    "SELECT 1 FROM interaction_sources WHERE terminal_id=%s LIMIT 1", (terminal_id,)
                ).fetchone()
                state: SourceStatusState = "replaced" if exists else "unregistered"
                return SourceStatus(
                    state=state,
                    terminal_id=terminal_id,
                    incarnation_id=incarnation_id,
                    current_generation=None,
                    capability_available=False,
                )
            state = "current"
            if source["revoked_at"] is not None:
                state = "inactive"
            else:
                terminal = self._terminal(connection, terminal_id)
                if (
                    terminal is None
                    or terminal["session_incarnation_id"] != source["incarnation_id"]
                ):
                    state = "replaced"
                else:
                    current = self._current_incarnation(connection, source["session_name"])
                    if current is not None and current != source["incarnation_id"]:
                        state = "replaced"
                    elif (
                        turn_generation is not None and turn_generation != source["turn_generation"]
                    ):
                        state = "stale_turn"
                    else:
                        fingerprint, tools = canonical_tool_fingerprint(terminal["allowed_tools"])
                        if (
                            not _allows_cao_mcp(tools)
                            or fingerprint != source["allowed_tool_fingerprint"]
                        ):
                            state = "inactive"
            pending = connection.execute(
                "SELECT min(g.expires_at) AS deadline FROM interaction_groups g "
                "WHERE g.terminal_id=%s AND g.incarnation_id=%s AND g.turn_generation=%s "
                "AND g.lifecycle IN ('open','cycle_blocked') AND g.expires_at>%s AND EXISTS("
                "SELECT 1 FROM interaction_questions q WHERE q.group_id=g.group_id AND q.required IS TRUE AND ("
                "(q.lifecycle IN ('open','manual_only') AND q.expires_at>%s) OR "
                "(q.lifecycle='answered' AND NOT EXISTS(SELECT 1 FROM interaction_answers a "
                "JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
                "WHERE a.question_id=q.question_id AND a.question_version=q.version "
                "AND d.state IN ('returned_to_mcp','agent_acknowledged')))))",
                (
                    terminal_id,
                    source["incarnation_id"],
                    source["turn_generation"],
                    _now(),
                    _now(),
                ),
            ).fetchone()
            return SourceStatus(
                state=state,
                terminal_id=terminal_id,
                incarnation_id=source["incarnation_id"],
                current_generation=source["turn_generation"],
                capability_available=bool(source["capability_available"]),
                deadline=_datetime(pending["deadline"]),
            )

    @staticmethod
    def _has_websocket_advance_chain(
        connection: Connection,
        terminal_id: str,
        incarnation_id: str,
        from_generation: int,
        to_generation: int,
    ) -> bool:
        """Return whether every intervening turn was an authorized WS input."""
        if to_generation < from_generation:
            return False
        if to_generation == from_generation:
            return True
        advances = connection.execute(
            "SELECT from_generation,to_generation,reason FROM interaction_turn_advances "
            "WHERE terminal_id=%s AND incarnation_id=%s AND from_generation>=%s "
            "AND to_generation<=%s ORDER BY from_generation,to_generation",
            (terminal_id, incarnation_id, from_generation, to_generation),
        ).fetchall()
        expected = from_generation
        for advance in advances:
            if (
                advance["from_generation"] != expected
                or advance["to_generation"] != expected + 1
                or advance["reason"] != "websocket_input"
            ):
                return False
            expected += 1
        return expected == to_generation

    @staticmethod
    def _native_closed_event(connection: Connection, question_id: str) -> Record | None:
        return cast(
            Record | None,
            connection.execute(
                "SELECT event_id FROM interaction_events WHERE question_id=%s "
                "AND event_type='interaction.native_closed' ORDER BY sequence DESC LIMIT 1",
                (question_id,),
            ).fetchone(),
        )

    @staticmethod
    def _native_target_fingerprint(
        terminal_id: str,
        incarnation_id: str,
        session_name: str,
        window_name: str,
    ) -> str:
        """Bind a durable native notice to its exact target without exposing names."""
        return digest_json(
            {
                "domain": "cao.native-target.v1",
                "terminal_id": terminal_id,
                "incarnation_id": incarnation_id,
                "session_name": session_name,
                "window_name": window_name,
            }
        )

    def _latest_native_question(
        self,
        connection: Connection,
        terminal_id: str,
        incarnation_id: str,
    ) -> Record | None:
        return cast(
            Record | None,
            connection.execute(
                "SELECT q.* FROM interaction_questions q "
                "WHERE q.terminal_id=%s AND q.incarnation_id=%s AND q.source_kind='native' "
                "ORDER BY q.question_sequence DESC LIMIT 1",
                (terminal_id, incarnation_id),
            ).fetchone(),
        )

    def _native_question_has_newer_observation(
        self,
        connection: Connection,
        question: Record,
    ) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM interaction_questions WHERE terminal_id=%s AND incarnation_id=%s "
                "AND source_kind='native' AND question_sequence>%s LIMIT 1",
                (
                    question["terminal_id"],
                    question["incarnation_id"],
                    question["question_sequence"],
                ),
            ).fetchone()
            is not None
        )

    def _native_question_blocks_dispatch(
        self,
        connection: Connection,
        source: Record,
        question: Record | None = None,
    ) -> bool:
        """Keep exact native UI notices as a durable gate until physical closure."""
        question = question or self._latest_native_question(
            connection, source["terminal_id"], source["incarnation_id"]
        )
        if question is None or question["lifecycle"] == "reconciled_external":
            return False
        if self._native_closed_event(connection, question["question_id"]) is not None:
            return False
        lifecycle = question["lifecycle"]
        if lifecycle in {"expired", "cancelled"}:
            # Response deadline/cancellation is not evidence that the CLI
            # stopped displaying the dialog.
            return True
        if lifecycle == "manual_only":
            return True
        if lifecycle != "stale":
            return False
        observed_generation = int(question["turn_generation"])
        current_generation = int(source["turn_generation"])
        if observed_generation >= current_generation:
            return False
        source_advanced = connection.execute(
            "SELECT 1 FROM interaction_events WHERE question_id=%s "
            "AND event_type='interaction.stale' AND reason_code='source_turn_advanced' LIMIT 1",
            (question["question_id"],),
        ).fetchone()
        # Even a non-manual advance does not prove that the provider removed
        # the old native dialog. Keep the physical input fence until exact
        # closure; the waiter separately requires a websocket-only chain to
        # resume the old task.
        return bool(source_advanced)

    def has_unresolved_native_notice(self, terminal_id: str, incarnation_id: str) -> bool:
        """Whether an exact current source still has a native dialog on screen."""
        terminal_id = _identifier(terminal_id, "terminal id")
        incarnation_id = _identifier(incarnation_id, "incarnation id")
        with self._transaction() as connection:
            source = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s FOR UPDATE",
                (terminal_id, incarnation_id),
            ).fetchone()
            if source is None:
                known = connection.execute(
                    "SELECT 1 FROM interaction_sources WHERE terminal_id=%s LIMIT 1",
                    (terminal_id,),
                ).fetchone()
                if known is not None:
                    raise InteractionStale("terminal now belongs to a different incarnation")
                return False
            self._validate_source_row(connection, source, require_capability=False)
            terminal = self._terminal(connection, terminal_id)
            if terminal is None or terminal["session_incarnation_id"] != incarnation_id:
                raise InteractionStale("terminal incarnation was replaced")
            self._expire_due(connection, _now())
            question = self._latest_native_question(connection, terminal_id, incarnation_id)
            if question is None:
                return False
            return self._native_question_blocks_dispatch(connection, source, question)

    def latest_unresolved_native_observation(
        self, terminal_id: str, incarnation_id: str
    ) -> NativeObservationPin | None:
        """Return the exact latest native pin only when its target is revalidated.

        The screen and target names are never returned from durable event data.
        The event stores a digest of the target captured with the observation;
        the current source and terminal rows must reproduce that digest before
        the monitor can rebuild its process-local closure pin.
        """
        terminal_id = _identifier(terminal_id, "terminal id")
        incarnation_id = _identifier(incarnation_id, "incarnation id")
        with self._transaction() as connection:
            source = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s FOR UPDATE",
                (terminal_id, incarnation_id),
            ).fetchone()
            if source is None:
                known = connection.execute(
                    "SELECT 1 FROM interaction_sources WHERE terminal_id=%s LIMIT 1",
                    (terminal_id,),
                ).fetchone()
                if known is not None:
                    raise InteractionStale("terminal now belongs to a different incarnation")
                return None
            self._validate_source_row(connection, source, require_capability=False)
            terminal = self._terminal(connection, terminal_id)
            if terminal is None or terminal["session_incarnation_id"] != incarnation_id:
                raise InteractionStale("terminal incarnation was replaced")
            current_incarnation = self._current_incarnation(connection, source["session_name"])
            if current_incarnation is not None and current_incarnation != incarnation_id:
                raise InteractionStale("session incarnation was replaced")
            self._expire_due(connection, _now())
            question = self._latest_native_question(connection, terminal_id, incarnation_id)
            if question is None or not self._native_question_blocks_dispatch(
                connection, source, question
            ):
                return None

            session_name = source["session_name"]
            window_name = terminal["tmux_window"]
            if not isinstance(session_name, str) or not isinstance(window_name, str):
                return None
            target = (terminal_id, session_name, window_name)
            target_fingerprint = self._native_target_fingerprint(
                terminal_id, incarnation_id, session_name, window_name
            )
            event = connection.execute(
                "SELECT metadata_json FROM interaction_events WHERE question_id=%s "
                "AND event_type='interaction.native_observed' ORDER BY sequence DESC LIMIT 1",
                (question["question_id"],),
            ).fetchone()
            if event is None:
                # Legacy observations did not persist an exact target binding.
                # Keep their physical gate closed until an operator can inspect
                # the terminal; never infer a historical target from current state.
                return None
            try:
                metadata = json.loads(event["metadata_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
            if not isinstance(metadata, dict) or any(
                metadata.get(field) != expected
                for field, expected in (
                    ("question_id", question["question_id"]),
                    ("observation_id", question["observation_id"]),
                    ("turn_generation", question["turn_generation"]),
                    ("dialog_fingerprint", question["dialog_fingerprint"]),
                    ("provider_evidence_version", question["provider_evidence_version"]),
                    ("target_fingerprint", target_fingerprint),
                )
            ):
                return None
            try:
                original_generation = int(question["turn_generation"])
                current_generation = int(source["turn_generation"])
            except (TypeError, ValueError):
                return None
            return NativeObservationPin(
                terminal_id=terminal_id,
                incarnation_id=incarnation_id,
                question_id=str(question["question_id"]),
                observation_id=str(question["observation_id"]),
                turn_generation=original_generation,
                current_generation=current_generation,
                lifecycle=str(question["lifecycle"]),
                dialog_fingerprint=str(question["dialog_fingerprint"]),
                provider_evidence_version=str(question["provider_evidence_version"]),
                exact_target=target,
            )

    def advance_turn(
        self,
        terminal_id: str,
        incarnation_id: str,
        *,
        reason: str,
        idempotency_key: str,
    ) -> int | None:
        terminal_id = _identifier(terminal_id, "terminal id")
        incarnation_id = _identifier(incarnation_id, "incarnation id")
        idempotency_key = _identifier(idempotency_key, "turn idempotency key", minimum=16)
        reason = _identifier(reason, "turn reason", maximum=64)
        with self._transaction() as connection:
            source = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s FOR UPDATE",
                (terminal_id, incarnation_id),
            ).fetchone()
            if source is None:
                known = connection.execute(
                    "SELECT 1 FROM interaction_sources WHERE terminal_id=%s LIMIT 1", (terminal_id,)
                ).fetchone()
                if known:
                    raise InteractionStale("terminal now belongs to a different incarnation")
                return None
            terminal = self._terminal(connection, terminal_id)
            if terminal is None or terminal["session_incarnation_id"] != incarnation_id:
                raise InteractionStale("terminal incarnation was replaced")
            self._validate_source_row(connection, source, require_capability=False)
            current_inc = self._current_incarnation(connection, source["session_name"])
            if current_inc is not None and current_inc != incarnation_id:
                raise InteractionStale("session incarnation was replaced")
            now = _now()
            self._expire_due(connection, now)
            if reason != "websocket_input" and self._native_question_blocks_dispatch(
                connection, source
            ):
                raise InteractionConflict(
                    "provider-owned native interaction must be closed before advancing this source turn"
                )
            prior: Record | None = connection.execute(
                "SELECT reason,to_generation FROM interaction_turn_advances WHERE terminal_id=%s AND incarnation_id=%s AND idempotency_key=%s",
                (terminal_id, incarnation_id, idempotency_key),
            ).fetchone()
            if prior is not None:
                if prior["reason"] != reason:
                    raise InteractionConflict(
                        "turn idempotency key was reused with a different reason"
                    )
                to_generation = prior["to_generation"]
                if not isinstance(to_generation, int):
                    raise InteractionSchemaMismatch("stored source turn generation is invalid")
                return to_generation
            groups = connection.execute(
                "SELECT group_id FROM interaction_groups WHERE terminal_id=%s AND incarnation_id=%s "
                "AND turn_generation=%s AND lifecycle IN ('open','cycle_blocked') AND expires_at>%s",
                (terminal_id, incarnation_id, source["turn_generation"], now),
            ).fetchall()
            if any(self._required_pending_count(connection, group["group_id"]) for group in groups):
                raise InteractionConflict(
                    "required interaction must be resolved before advancing this source turn"
                )
            previous = int(source["turn_generation"])
            generation = previous + 1
            connection.execute(
                "INSERT INTO interaction_turn_advances VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (terminal_id, incarnation_id, idempotency_key, reason, previous, generation, now),
            )
            connection.execute(
                "UPDATE interaction_sources SET turn_generation=%s WHERE terminal_id=%s AND incarnation_id=%s AND turn_generation=%s",
                (generation, terminal_id, incarnation_id, previous),
            )
            stale_questions = connection.execute(
                "SELECT question_id FROM interaction_questions WHERE terminal_id=%s AND incarnation_id=%s "
                "AND turn_generation<%s AND lifecycle IN ('open','manual_only') "
                "ORDER BY question_sequence FOR UPDATE",
                (terminal_id, incarnation_id, generation),
            ).fetchall()
            connection.execute(
                "UPDATE interaction_questions SET lifecycle='stale' WHERE terminal_id=%s AND incarnation_id=%s "
                "AND turn_generation<%s AND lifecycle IN ('open','manual_only')",
                (terminal_id, incarnation_id, generation),
            )
            connection.execute(
                "UPDATE interaction_groups SET lifecycle='stale',version=version+1 WHERE terminal_id=%s AND incarnation_id=%s "
                "AND turn_generation<%s AND lifecycle IN ('open','cycle_blocked')",
                (terminal_id, incarnation_id, generation),
            )
            connection.execute(
                "UPDATE interaction_waits SET state='stale',released_at=%s WHERE terminal_id=%s AND incarnation_id=%s AND state IN ('waiting','cycle_blocked')",
                (now, terminal_id, incarnation_id),
            )
            for item in stale_questions:
                self.repository.append_event(
                    connection,
                    event_type="interaction.stale",
                    source_terminal_id=terminal_id,
                    source_incarnation_id=incarnation_id,
                    actor_kind="system",
                    actor_id=None,
                    reason_code="source_turn_advanced",
                    metadata={"question_id": item["question_id"], "turn_generation": generation},
                    question_id=item["question_id"],
                )
            self.repository.append_event(
                connection,
                event_type="interaction.turn_advanced",
                source_terminal_id=terminal_id,
                source_incarnation_id=incarnation_id,
                actor_kind="system",
                actor_id=None,
                reason_code="source_turn_advanced",
                metadata={"turn_generation": generation},
            )
            self._recompute_wait_cycles(connection)
            return generation

    @staticmethod
    def _required_pending_count(connection: Connection, group_id: str) -> int:
        """Count required questions whose answer has not reached the source.

        Human acceptance is durable, but does not unblock the originating
        task. The source receives that answer only when its MCP pull commits
        ``returned_to_mcp``; a later acknowledgement remains optional.
        """
        row: Record | None = connection.execute(
            "SELECT count(*) FROM interaction_questions q "
            "LEFT JOIN interaction_answers a ON a.question_id=q.question_id "
            "AND a.question_version=q.version "
            "LEFT JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
            "WHERE q.group_id=%s AND q.required IS TRUE AND ("
            "(q.lifecycle IN ('open','manual_only') AND q.expires_at>%s) OR "
            "(q.lifecycle='answered' AND coalesce(d.state,'accepted') "
            "NOT IN ('returned_to_mcp','agent_acknowledged'))) ",
            (group_id, _now()),
        ).fetchone()
        if row is None or not isinstance(row[0], int):
            raise InteractionSchemaMismatch("required interaction count is invalid")
        return row[0]

    @staticmethod
    def _expired_group_has_deliverable_answers(connection: Connection, group_id: str) -> bool:
        """Allow a current source to pull already-accepted answers after TTL.

        Expiry still ends the runtime dependency. It must not destroy a durable
        human decision that was accepted before expiry. Groups with any
        unanswered required member remain expired and return no answer bodies.
        """
        unanswered_required_row = connection.execute(
            "SELECT count(*) FROM interaction_questions q WHERE q.group_id=%s AND q.required IS TRUE AND NOT EXISTS("
            "SELECT 1 FROM interaction_answers a JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
            "WHERE a.question_id=q.question_id AND a.question_version=q.version "
            "AND d.state IN ('accepted','returned_to_mcp','agent_acknowledged'))",
            (group_id,),
        ).fetchone()
        if unanswered_required_row is None:
            raise InteractionSchemaMismatch("required interaction count query returned no row")
        unanswered_required = unanswered_required_row[0]
        if unanswered_required:
            return False
        return (
            connection.execute(
                "SELECT 1 FROM interaction_questions q JOIN interaction_answers a "
                "ON a.question_id=q.question_id AND a.question_version=q.version "
                "JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
                "WHERE q.group_id=%s AND d.state IN ('accepted','returned_to_mcp','agent_acknowledged') LIMIT 1",
                (group_id,),
            ).fetchone()
            is not None
        )

    def has_required_wait(
        self,
        terminal_id: str,
        incarnation_id: str | None = None,
        turn_generation: int | None = None,
    ) -> bool:
        with self._read() as connection:
            source = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s ORDER BY created_at DESC LIMIT 1",
                (terminal_id,),
            ).fetchone()
            if source is None or source["revoked_at"] is not None:
                return False
            if incarnation_id is not None and source["incarnation_id"] != incarnation_id:
                return False
            if turn_generation is not None and source["turn_generation"] != turn_generation:
                return False
            # A registered but unverifiable source is an error, not evidence
            # that it is safe to continue with generic terminal input.
            self._validate_source_row(connection, source, require_capability=False)
            row = connection.execute(
                "SELECT g.group_id FROM interaction_groups g WHERE g.terminal_id=%s AND g.incarnation_id=%s "
                "AND g.turn_generation=%s AND g.lifecycle IN ('open','cycle_blocked') AND g.expires_at>%s "
                "ORDER BY g.created_at",
                (
                    source["terminal_id"],
                    source["incarnation_id"],
                    source["turn_generation"],
                    _now(),
                ),
            ).fetchall()
            return any(self._required_pending_count(connection, group["group_id"]) for group in row)

    async def wait_for_required(
        self,
        terminal_id: str,
        incarnation_id: str,
        *,
        timeout_seconds: float,
        expected_generation: int | None = None,
    ) -> WaitStatus:
        if (
            not isinstance(timeout_seconds, (int, float))
            or timeout_seconds < 0
            or timeout_seconds > _MAX_WAIT_SECONDS
        ):
            raise InteractionInvalid("runtime interaction wait is bounded to 30 seconds")
        deadline = time.monotonic() + float(timeout_seconds)
        while True:
            with self._transaction() as connection:
                status = self._source_status_in_connection(connection, terminal_id, incarnation_id)
                if status.state in {"replaced", "inactive", "unregistered", "stale_turn"}:
                    return WaitStatus(state="stale")
                source = connection.execute(
                    "SELECT turn_generation FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s FOR UPDATE",
                    (terminal_id, incarnation_id),
                ).fetchone()
                if source is None:
                    return WaitStatus(state="stale")
                current_generation = source["turn_generation"]
                if expected_generation is None:
                    expected_generation = current_generation
                if current_generation != expected_generation:
                    return WaitStatus(state="stale")
                self._expire_due(connection, _now())
                groups = connection.execute(
                    "SELECT g.group_id,g.expires_at,g.lifecycle FROM interaction_groups g "
                    "WHERE g.terminal_id=%s AND g.incarnation_id=%s AND g.turn_generation=%s "
                    "AND g.lifecycle IN ('open','expired','cancelled','stale','cycle_blocked') "
                    "ORDER BY g.created_at",
                    (terminal_id, incarnation_id, current_generation),
                ).fetchall()
                waiting = []
                terminal_state = None
                earliest = None
                for group in groups:
                    unresolved = self._required_pending_count(connection, group["group_id"])
                    if group["lifecycle"] == "open" and unresolved:
                        waiting.append(group["group_id"])
                        earliest = (
                            group["expires_at"]
                            if earliest is None
                            else min(earliest, group["expires_at"])
                        )
                    elif group["lifecycle"] in {"expired", "cancelled", "stale", "cycle_blocked"}:
                        terminal_state = group["lifecycle"]
                if not waiting:
                    return WaitStatus(
                        state=terminal_state or "released",
                        group_ids=tuple(),
                        deadline=_datetime(earliest),
                    )
                state = WaitStatus(
                    state="waiting", group_ids=tuple(waiting), deadline=_datetime(earliest)
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return state
            await asyncio.sleep(min(0.25, remaining))

    async def wait_for_native_manual(
        self,
        terminal_id: str,
        incarnation_id: str,
        *,
        timeout_seconds: float,
        expected_generation: int,
        expected_question_id: str | None = None,
        expected_observation_id: str | None = None,
    ) -> NativeWaitStatus:
        """Wait briefly for one exact native manual notice to close or expire.

        This is an observation wait only. It never accepts or creates an answer.
        After a direct input advances the source, it continues to watch only the
        captured old observation for exact provider closure evidence.
        """
        terminal_id = _identifier(terminal_id, "terminal id")
        incarnation_id = _identifier(incarnation_id, "incarnation id")
        if type(expected_generation) is not int or expected_generation < 1:
            raise InteractionInvalid(
                "native interaction wait requires a positive source generation"
            )
        if (expected_question_id is None) != (expected_observation_id is None):
            raise InteractionInvalid(
                "native interaction wait identity requires both question and observation ids"
            )
        if expected_question_id is not None:
            expected_question_id = _identifier(expected_question_id, "native question id")
            assert expected_observation_id is not None
            expected_observation_id = _identifier(expected_observation_id, "native observation id")
        bound_question_id = expected_question_id
        bound_observation_id = expected_observation_id
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or timeout_seconds < 0
            or timeout_seconds > _MAX_WAIT_SECONDS
        ):
            raise InteractionInvalid("runtime native interaction wait is bounded to 30 seconds")
        deadline = time.monotonic() + float(timeout_seconds)
        while True:
            with self._transaction() as connection:
                source = connection.execute(
                    "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s FOR UPDATE",
                    (terminal_id, incarnation_id),
                ).fetchone()
                if source is None:
                    return NativeWaitStatus(state="unregistered")
                if source["revoked_at"] is not None:
                    return NativeWaitStatus(state="stale")
                try:
                    self._validate_source_row(connection, source, require_capability=False)
                except (InteractionStale, InteractionForbidden):
                    return NativeWaitStatus(state="stale")
                current_generation = int(source["turn_generation"])
                generation_advanced = current_generation != expected_generation
                direct_manual_advance = self._has_websocket_advance_chain(
                    connection,
                    terminal_id,
                    incarnation_id,
                    expected_generation,
                    current_generation,
                )
                self._expire_due(connection, _now())
                if bound_question_id is None:
                    question = connection.execute(
                        "SELECT question_id,observation_id,dialog_fingerprint,lifecycle,expires_at,"
                        "terminal_id,incarnation_id,question_sequence FROM interaction_questions "
                        "WHERE terminal_id=%s AND incarnation_id=%s AND turn_generation=%s "
                        "AND source_kind='native' ORDER BY question_sequence DESC LIMIT 1",
                        (terminal_id, incarnation_id, expected_generation),
                    ).fetchone()
                    if question is not None:
                        bound_question_id = question["question_id"]
                        bound_observation_id = question["observation_id"]
                else:
                    question = connection.execute(
                        "SELECT question_id,observation_id,dialog_fingerprint,lifecycle,expires_at,"
                        "terminal_id,incarnation_id,question_sequence FROM interaction_questions "
                        "WHERE terminal_id=%s AND incarnation_id=%s AND turn_generation=%s "
                        "AND source_kind='native' AND question_id=%s AND observation_id=%s",
                        (
                            terminal_id,
                            incarnation_id,
                            expected_generation,
                            bound_question_id,
                            bound_observation_id,
                        ),
                    ).fetchone()
                    if question is None:
                        return NativeWaitStatus(
                            state="stale",
                            question_id=bound_question_id,
                            observation_id=bound_observation_id,
                        )
                if question is not None:
                    newer_observation = self._native_question_has_newer_observation(
                        connection, question
                    )
                    if question["lifecycle"] not in {
                        "expired",
                        "cancelled",
                    } and self._native_closed_event(connection, question["question_id"]):
                        return NativeWaitStatus(
                            state="stale",
                            lifecycle=question["lifecycle"],
                            question_id=question["question_id"],
                            observation_id=question["observation_id"],
                            deadline=_datetime(question["expires_at"]),
                        )
                    if question["lifecycle"] == "reconciled_external":
                        if not direct_manual_advance or newer_observation:
                            return NativeWaitStatus(
                                state="stale",
                                lifecycle="reconciled_external",
                                question_id=question["question_id"],
                                observation_id=question["observation_id"],
                                deadline=_datetime(question["expires_at"]),
                            )
                        return NativeWaitStatus(
                            state="released",
                            lifecycle="reconciled_external",
                            question_id=question["question_id"],
                            observation_id=question["observation_id"],
                            current_generation=current_generation,
                            deadline=_datetime(question["expires_at"]),
                        )
                    if question["lifecycle"] in {"expired", "cancelled"}:
                        return NativeWaitStatus(
                            state=question["lifecycle"],
                            lifecycle=question["lifecycle"],
                            question_id=question["question_id"],
                            observation_id=question["observation_id"],
                            deadline=_datetime(question["expires_at"]),
                        )
                    if generation_advanced:
                        source_advanced = (
                            question["lifecycle"] == "stale"
                            and connection.execute(
                                "SELECT 1 FROM interaction_events WHERE question_id=%s "
                                "AND event_type='interaction.stale' AND reason_code='source_turn_advanced' LIMIT 1",
                                (question["question_id"],),
                            ).fetchone()
                        )
                        reopened = connection.execute(
                            "SELECT 1 FROM interaction_questions WHERE terminal_id=%s AND incarnation_id=%s "
                            "AND turn_generation=%s AND source_kind='native' AND dialog_fingerprint=%s "
                            "AND lifecycle='manual_only' LIMIT 1",
                            (
                                terminal_id,
                                incarnation_id,
                                source["turn_generation"],
                                question["dialog_fingerprint"],
                            ),
                        ).fetchone()
                        if (
                            not direct_manual_advance
                            or not source_advanced
                            or reopened
                            or newer_observation
                        ):
                            return NativeWaitStatus(
                                state="stale",
                                lifecycle=question["lifecycle"],
                                question_id=question["question_id"],
                                observation_id=question["observation_id"],
                                deadline=_datetime(question["expires_at"]),
                            )
                        status = NativeWaitStatus(
                            state="waiting",
                            lifecycle="stale",
                            question_id=question["question_id"],
                            observation_id=question["observation_id"],
                            deadline=_datetime(question["expires_at"]),
                        )
                    elif question["lifecycle"] == "manual_only":
                        if newer_observation:
                            return NativeWaitStatus(
                                state="stale",
                                lifecycle=question["lifecycle"],
                                question_id=question["question_id"],
                                observation_id=question["observation_id"],
                                deadline=_datetime(question["expires_at"]),
                            )
                        status = NativeWaitStatus(
                            state="waiting",
                            lifecycle="manual_only",
                            question_id=question["question_id"],
                            observation_id=question["observation_id"],
                            deadline=_datetime(question["expires_at"]),
                        )
                    elif question["lifecycle"] == "stale":
                        return NativeWaitStatus(
                            state="stale",
                            lifecycle=question["lifecycle"],
                            question_id=question["question_id"],
                            observation_id=question["observation_id"],
                            deadline=_datetime(question["expires_at"]),
                        )
                    else:
                        return NativeWaitStatus(
                            state="stale",
                            lifecycle=question["lifecycle"],
                            question_id=question["question_id"],
                            observation_id=question["observation_id"],
                            deadline=_datetime(question["expires_at"]),
                        )
                else:
                    if generation_advanced:
                        return NativeWaitStatus(state="stale")
                    # The monitor may not have persisted its first exact
                    # observation yet; bounded polling lets that observation
                    # arrive without pausing legacy/unregistered terminals.
                    status = NativeWaitStatus(state="waiting")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return status
            await asyncio.sleep(min(0.25, remaining))

    def _source_status_in_connection(
        self, connection: Connection, terminal_id: str, incarnation_id: str
    ) -> SourceStatus:
        source = connection.execute(
            "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
            (terminal_id, incarnation_id),
        ).fetchone()
        if source is None:
            return SourceStatus(
                state="replaced",
                terminal_id=terminal_id,
                incarnation_id=incarnation_id,
                current_generation=None,
                capability_available=False,
            )
        state: SourceStatusState = "current"
        if source["revoked_at"] is not None:
            state = "inactive"
        else:
            try:
                self._validate_source_row(connection, source, require_capability=False)
            except InteractionStale:
                state = "replaced"
            except InteractionForbidden:
                state = "inactive"
        pending = connection.execute(
            "SELECT min(g.expires_at) AS deadline FROM interaction_groups g "
            "WHERE g.terminal_id=%s AND g.incarnation_id=%s AND g.turn_generation=%s "
            "AND g.lifecycle IN ('open','cycle_blocked') AND g.expires_at>%s AND EXISTS("
            "SELECT 1 FROM interaction_questions q WHERE q.group_id=g.group_id AND q.required IS TRUE AND ("
            "(q.lifecycle IN ('open','manual_only') AND q.expires_at>%s) OR "
            "(q.lifecycle='answered' AND NOT EXISTS(SELECT 1 FROM interaction_answers a "
            "JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
            "WHERE a.question_id=q.question_id AND a.question_version=q.version "
            "AND d.state IN ('returned_to_mcp','agent_acknowledged')))))",
            (terminal_id, incarnation_id, source["turn_generation"], _now(), _now()),
        ).fetchone()
        if pending is None:
            raise InteractionSchemaMismatch("pending source deadline query returned no row")
        return SourceStatus(
            state=state,
            terminal_id=terminal_id,
            incarnation_id=incarnation_id,
            current_generation=source["turn_generation"],
            capability_available=bool(source["capability_available"]),
            deadline=_datetime(pending["deadline"]),
        )

    @staticmethod
    def _question_version(connection: Connection, question_id: str, version: int) -> dict[str, Any]:
        row: Record | None = connection.execute(
            "SELECT * FROM interaction_question_versions WHERE question_id=%s AND version=%s",
            (question_id, version),
        ).fetchone()
        if row is None:
            raise InteractionNotFound("question version does not exist")
        return dict(row)

    def _load_question(
        self,
        connection: Connection,
        question_id: str,
        version: int | None = None,
        *,
        for_update: bool = False,
    ) -> tuple[Record, dict[str, Any]]:
        statement = "SELECT * FROM interaction_questions WHERE question_id=%s"
        if for_update:
            statement += " FOR UPDATE"
        question: Record | None = connection.execute(statement, (question_id,)).fetchone()
        if question is None:
            raise InteractionNotFound("question does not exist")
        selected_version = question["version"] if version is None else version
        details = dict(question)
        details.update(self._question_version(connection, question_id, selected_version))
        details["current_version"] = question["version"]
        return question, details

    def _lock_question_source(self, connection: Connection, question: Record) -> Record:
        source: Record | None = connection.execute(
            "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s FOR UPDATE",
            (question["terminal_id"], question["incarnation_id"]),
        ).fetchone()
        if source is None:
            raise InteractionNotFound("interaction source does not exist")
        self._validate_source_row(connection, source, require_capability=False)
        return source

    @staticmethod
    def _current_version(question: Record, expected_version: int) -> None:
        if type(expected_version) is not int or expected_version < 1:
            raise InteractionInvalid("a positive expected question version is required")
        if question["version"] != expected_version:
            error = InteractionStale("question version changed")
            error.current_version = int(question["version"])
            raise error

    def _write_question_version(
        self,
        connection: Connection,
        question: Record,
        updates: Mapping[str, object],
    ) -> int:
        version = int(question["version"]) + 1
        values = {
            "dialog_fingerprint": question["dialog_fingerprint"],
            "provider_evidence_version": question["provider_evidence_version"],
            "observation_id": question["observation_id"],
            "kind": question["kind"],
            "human_required": question["human_required"],
            "prompt_content": question["prompt_content"],
            "options_json": question["options_json"],
            "allow_free_text": question["allow_free_text"],
            "required": question["required"],
            "recipient_kind": question["recipient_kind"],
            "recipient_id": question["recipient_id"],
            "route_reason": question["route_reason"],
            "response_capability": question["response_capability"],
            "expires_at": question["expires_at"],
        }
        values.update(updates)
        connection.execute(
            "INSERT INTO interaction_question_versions(question_id,version,dialog_fingerprint,"
            "provider_evidence_version,observation_id,kind,human_required,prompt_content,options_json,"
            "allow_free_text,required,recipient_kind,recipient_id,route_reason,response_capability,created_at,expires_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                question["question_id"],
                version,
                values["dialog_fingerprint"],
                values["provider_evidence_version"],
                values["observation_id"],
                values["kind"],
                values["human_required"],
                values["prompt_content"],
                values["options_json"],
                values["allow_free_text"],
                values["required"],
                values["recipient_kind"],
                values["recipient_id"],
                values["route_reason"],
                values["response_capability"],
                _now(),
                values["expires_at"],
            ),
        )
        mutable = (
            "dialog_fingerprint",
            "provider_evidence_version",
            "observation_id",
            "kind",
            "human_required",
            "prompt_content",
            "options_json",
            "allow_free_text",
            "required",
            "recipient_kind",
            "recipient_id",
            "route_reason",
            "response_capability",
            "expires_at",
        )
        assignments = ",".join(f"{column}=%s" for column in (*mutable, "version"))
        updated = connection.execute(
            f"UPDATE interaction_questions SET {assignments} WHERE question_id=%s AND version=%s",
            tuple(values[column] for column in mutable)
            + (version, question["question_id"], question["version"]),
        )
        if updated.rowcount != 1:
            raise InteractionStale("question version changed during update")
        if question["group_id"]:
            connection.execute(
                "UPDATE interaction_groups SET version=version+1 WHERE group_id=%s",
                (question["group_id"],),
            )
        return version

    def _expire_due(self, connection: Connection, now: float) -> None:
        rows = connection.execute(
            "SELECT question_id,group_id,terminal_id,incarnation_id FROM interaction_questions "
            "WHERE (lifecycle IN ('open','manual_only') OR "
            "(source_kind='native' AND lifecycle='stale')) AND expires_at<=%s "
            "ORDER BY question_sequence FOR UPDATE SKIP LOCKED",
            (now,),
        ).fetchall()
        for row in rows:
            updated = connection.execute(
                "UPDATE interaction_questions SET lifecycle='expired' WHERE question_id=%s AND "
                "(lifecycle IN ('open','manual_only') OR (source_kind='native' AND lifecycle='stale'))",
                (row["question_id"],),
            )
            if updated.rowcount != 1:
                continue
            self.repository.append_event(
                connection,
                event_type="interaction.expired",
                source_terminal_id=row["terminal_id"],
                source_incarnation_id=row["incarnation_id"],
                actor_kind="system",
                actor_id=None,
                reason_code="question_deadline_elapsed",
                metadata={"question_id": row["question_id"], "lifecycle": "expired"},
                group_id=row["group_id"],
                question_id=row["question_id"],
            )
        groups = connection.execute(
            "SELECT group_id,terminal_id,incarnation_id FROM interaction_groups "
            "WHERE lifecycle IN ('open','cycle_blocked') AND expires_at<=%s "
            "ORDER BY created_at,group_id FOR UPDATE SKIP LOCKED",
            (now,),
        ).fetchall()
        for group in groups:
            updated = connection.execute(
                "UPDATE interaction_groups SET lifecycle='expired',version=version+1 "
                "WHERE group_id=%s AND lifecycle IN ('open','cycle_blocked')",
                (group["group_id"],),
            )
            if updated.rowcount != 1:
                continue
            connection.execute(
                "UPDATE interaction_waits SET state='expired',released_at=%s WHERE group_id=%s AND state IN ('waiting','cycle_blocked')",
                (now, group["group_id"]),
            )
            self.repository.append_event(
                connection,
                event_type="interaction.group_expired",
                source_terminal_id=group["terminal_id"],
                source_incarnation_id=group["incarnation_id"],
                actor_kind="system",
                actor_id=None,
                reason_code="group_deadline_elapsed",
                metadata={"group_id": group["group_id"], "lifecycle": "expired"},
                group_id=group["group_id"],
            )
        expired_delegations = connection.execute(
            "SELECT q.*,d.delegation_id,d.assignment_id FROM interaction_delegations d JOIN interaction_questions q "
            "ON q.question_id=d.question_id AND q.version=d.question_version "
            "WHERE d.revoked_at IS NULL AND d.expires_at<=%s AND q.lifecycle='open' "
            "ORDER BY d.expires_at,d.delegation_id FOR UPDATE OF q SKIP LOCKED",
            (now,),
        ).fetchall()
        for delegation in expired_delegations:
            # Exact old-version CAS: a concurrent answer, revoke or new
            # routing version wins and this expiry becomes a no-op.
            source = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
                (delegation["terminal_id"], delegation["incarnation_id"]),
            ).fetchone()
            owner_id = source["owner_principal_id"] if source else None
            version = self._write_question_version(
                connection,
                delegation,
                {
                    "human_required": True,
                    "recipient_kind": "human" if owner_id else "unassigned",
                    "recipient_id": owner_id,
                    "route_reason": "delegation_deadline_expired",
                },
            )
            self.repository.append_event(
                connection,
                event_type="interaction.escalated",
                source_terminal_id=delegation["terminal_id"],
                source_incarnation_id=delegation["incarnation_id"],
                actor_kind="system",
                actor_id=None,
                reason_code="delegation_deadline_expired",
                metadata={
                    "delegation_id": delegation["delegation_id"],
                    "question_id": delegation["question_id"],
                    "question_version": version,
                    "assignment_id": delegation["assignment_id"],
                    "recipient_kind": "human" if owner_id else "unassigned",
                    "outcome": "expired",
                },
                group_id=delegation["group_id"],
                question_id=delegation["question_id"],
            )
        self._recompute_wait_cycles(connection)

    def _recompute_wait_cycles(self, connection: Connection) -> None:
        """Persist current server-derived delegated-wait SCCs without answers."""
        rows = connection.execute(
            "SELECT w.*,g.lifecycle AS group_lifecycle,g.turn_generation,g.expires_at AS group_expires_at,"
            "s.owner_principal_id,s.revoked_at,s.turn_generation AS source_generation "
            "FROM interaction_waits w JOIN interaction_groups g ON g.group_id=w.group_id "
            "JOIN interaction_sources s ON s.terminal_id=w.terminal_id AND s.incarnation_id=w.incarnation_id "
            "WHERE w.state IN ('waiting','cycle_blocked') AND g.lifecycle IN ('open','cycle_blocked') "
            "AND (w.state='cycle_blocked' OR g.lifecycle='cycle_blocked' OR EXISTS ("
            "SELECT 1 FROM interaction_questions candidate_question "
            "JOIN interaction_delegations candidate_delegation "
            "ON candidate_delegation.question_id=candidate_question.question_id "
            "WHERE candidate_question.group_id=w.group_id)) "
            "AND s.owner_principal_id IS NOT NULL AND s.revoked_at IS NULL "
            "AND s.turn_generation=g.turn_generation AND g.expires_at>%s ORDER BY w.paused_at,w.wait_id",
            (_now(),),
        ).fetchall()
        edges: list[dict[str, object]] = []
        group_rows: dict[str, Record] = {}
        for wait in rows:
            try:
                source = connection.execute(
                    "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
                    (wait["terminal_id"], wait["incarnation_id"]),
                ).fetchone()
                if source is None:
                    continue
                self._validate_source_row(connection, source, require_capability=False)
            except (InteractionError, InteractionSchemaMismatch):
                # The graph is informational. If authority cannot be proven,
                # omit the delegated edge rather than inferring a participant.
                continue
            group_id = wait["group_id"]
            task_id = f"task:{wait['dependent_id']}"
            group_node_id = f"question_group:{group_id}"
            group_rows[group_id] = wait
            edges.append({"from": task_id, "to": group_node_id, "required": True, "cycle": False})
            questions = connection.execute(
                "SELECT * FROM interaction_questions WHERE group_id=%s AND source_kind='explicit' "
                "AND required IS TRUE AND lifecycle='open' AND expires_at>%s ORDER BY question_id",
                (group_id, _now()),
            ).fetchall()
            seen_supervisors: set[tuple[str, str]] = set()
            for question in questions:
                try:
                    self._assert_question_source(connection, question)
                    delegation = self.policy.active_delegation(connection, question, _now())
                    if delegation is None:
                        continue
                    supervisor = connection.execute(
                        "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
                        (
                            delegation["supervisor_terminal_id"],
                            delegation["supervisor_incarnation_id"],
                        ),
                    ).fetchone()
                    if supervisor is None:
                        continue
                    self._validate_source_row(connection, supervisor, require_capability=False)
                except (InteractionError, InteractionSchemaMismatch):
                    continue
                supervisor_key = (
                    delegation["supervisor_terminal_id"],
                    delegation["supervisor_incarnation_id"],
                )
                if supervisor_key in seen_supervisors:
                    continue
                seen_supervisors.add(supervisor_key)
                edges.append(
                    {
                        "from": group_node_id,
                        "to": f"task:{delegation['supervisor_terminal_id']}",
                        "required": True,
                        "cycle": False,
                    }
                )

        _mark_cycle_edges(edges)
        cyclic_groups = {
            node.removeprefix("question_group:")
            for edge in edges
            if edge["cycle"]
            for node in (str(edge["from"]), str(edge["to"]))
            if node.startswith("question_group:")
        }
        now = _now()
        for group_id, wait in group_rows.items():
            should_block = group_id in cyclic_groups
            is_blocked = (
                wait["group_lifecycle"] == "cycle_blocked" or wait["state"] == "cycle_blocked"
            )
            if should_block == is_blocked:
                continue
            next_lifecycle = "cycle_blocked" if should_block else "open"
            next_state = "cycle_blocked" if should_block else "waiting"
            updated_group = connection.execute(
                "UPDATE interaction_groups SET lifecycle=%s,version=version+1 "
                "WHERE group_id=%s AND lifecycle=%s",
                (next_lifecycle, group_id, wait["group_lifecycle"]),
            )
            if updated_group.rowcount != 1:
                continue
            connection.execute(
                "UPDATE interaction_waits SET state=%s,released_at=%s WHERE wait_id=%s AND state IN ('waiting','cycle_blocked')",
                (next_state, None, wait["wait_id"]),
            )
            self.repository.append_event(
                connection,
                event_type=(
                    "interaction.wait_cycle_blocked"
                    if should_block
                    else "interaction.wait_cycle_cleared"
                ),
                source_terminal_id=wait["terminal_id"],
                source_incarnation_id=wait["incarnation_id"],
                actor_kind="system",
                actor_id=None,
                reason_code=(
                    "delegated_wait_cycle" if should_block else "delegated_wait_cycle_cleared"
                ),
                metadata={
                    "group_id": group_id,
                    "wait_id": wait["wait_id"],
                    "lifecycle": next_lifecycle,
                },
                group_id=group_id,
            )

    def _assert_question_source(
        self,
        connection: Connection,
        question: InteractionRow,
        *,
        current_turn: bool = True,
    ) -> Record:
        source: Record | None = connection.execute(
            "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
            (question["terminal_id"], question["incarnation_id"]),
        ).fetchone()
        if source is None:
            raise InteractionStale("question source is no longer registered")
        self._validate_source_row(connection, source, require_capability=False)
        if current_turn and source["turn_generation"] != question["turn_generation"]:
            raise InteractionStale("question belongs to an earlier source turn")
        return source

    def _assert_question_open(
        self,
        connection: Connection,
        question: Record,
        expected_version: int,
        *,
        require_current_turn: bool = True,
    ) -> dict[str, Any]:
        self._current_version(question, expected_version)
        self._expire_due(connection, _now())
        refreshed_question: Record | None = connection.execute(
            "SELECT * FROM interaction_questions WHERE question_id=%s", (question["question_id"],)
        ).fetchone()
        if refreshed_question is None:
            raise InteractionNotFound("question does not exist")
        question = refreshed_question
        if question["lifecycle"] != "open":
            if question["lifecycle"] == "expired":
                raise InteractionStale("question has expired")
            if question["lifecycle"] == "stale":
                raise InteractionStale("question belongs to an earlier source turn")
            raise InteractionConflict("question is not open for an answer")
        self._assert_question_source(connection, question, current_turn=require_current_turn)
        return self._question_version(connection, question["question_id"], expected_version)

    @staticmethod
    def _decode_options(question: InteractionRow) -> list[dict[str, object]]:
        try:
            options = json.loads(str(question["options_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            raise InteractionInvalid("stored question options are invalid") from None
        return options if isinstance(options, list) else []

    def _validate_answer_payload(
        self,
        question: InteractionRow,
        payload_value: object,
    ) -> tuple[InteractionAnswerPayload, str]:
        payload = _payload(payload_value)
        kind = question["kind"]
        allowed = {
            str(item["id"]): bool(item.get("available", True))
            for item in self._decode_options(question)
        }
        if kind in {"credential", "unknown"}:
            raise InteractionForbidden("this question cannot accept an automated answer")
        if kind == "text":
            if payload.kind not in {"text", "free_text"}:
                raise InteractionInvalid("text question requires a text response")
        elif kind == "single_choice":
            if payload.kind != "options" or len(payload.option_ids) != 1:
                raise InteractionInvalid("single choice requires exactly one declared option")
        elif kind in {"multi_choice", "approval"}:
            if payload.kind != "options":
                raise InteractionInvalid("choice question requires declared option ids")
        elif kind == "choice_or_text":
            if payload.kind == "free_text" and not question["allow_free_text"]:
                raise InteractionInvalid("free text is disabled for this question")
            if payload.kind == "text":
                raise InteractionInvalid(
                    "choice_or_text requires options or explicitly enabled free text"
                )
        else:
            raise InteractionInvalid("unsupported question type")
        if payload.kind == "options":
            if any(
                option_id not in allowed or not allowed[option_id]
                for option_id in payload.option_ids
            ):
                raise InteractionInvalid("answer references an unavailable option")
        if payload.text is not None and _is_secret(payload.text):
            raise InteractionInvalid("credential-shaped answers cannot be stored")
        if _is_credential_prompt(str(question["prompt_content"])):
            raise InteractionForbidden("credential requests require an out-of-band manual process")
        canonical = payload.model_dump(mode="json", exclude_none=True)
        return payload, digest_json(canonical)

    def _store_answer(
        self,
        connection: Connection,
        question: Record,
        version_details: Mapping[str, Any],
        payload_value: object,
        *,
        actor_kind: str,
        actor_id: str,
        idempotency_key: str,
        capability_digest: str,
    ) -> AnswerReceipt:
        idempotency_key = _identifier(idempotency_key, "answer idempotency key", minimum=16)
        payload, payload_digest = self._validate_answer_payload(version_details, payload_value)
        existing_key = connection.execute(
            "SELECT a.*,d.state AS delivery_state FROM interaction_answers a "
            "JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
            "WHERE a.question_id=%s AND a.idempotency_key=%s",
            (question["question_id"], idempotency_key),
        ).fetchone()
        if existing_key is not None:
            if (
                existing_key["canonical_payload_hash"] != payload_digest
                or existing_key["question_version"] != version_details["version"]
            ):
                raise InteractionConflict(
                    "answer idempotency key was reused with different content"
                )
            return AnswerReceipt(
                question_id=question["question_id"],
                version=existing_key["question_version"],
                answer_id=existing_key["answer_id"],
                lifecycle="answered",
                delivery_state=existing_key["delivery_state"],
                accepted_at=_required_datetime(existing_key["accepted_at"], "answer accepted_at"),
                replayed=True,
            )
        existing_answer = connection.execute(
            "SELECT a.*,d.state AS delivery_state FROM interaction_answers a "
            "JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
            "WHERE a.question_id=%s AND a.question_version=%s",
            (question["question_id"], version_details["version"]),
        ).fetchone()
        if existing_answer is not None:
            if existing_answer["canonical_payload_hash"] != payload_digest:
                raise InteractionConflict("a different answer already won this question version")
            return AnswerReceipt(
                question_id=question["question_id"],
                version=existing_answer["question_version"],
                answer_id=existing_answer["answer_id"],
                lifecycle="answered",
                delivery_state=existing_answer["delivery_state"],
                accepted_at=_required_datetime(
                    existing_answer["accepted_at"], "answer accepted_at"
                ),
                replayed=True,
            )
        version_details = self._assert_question_open(
            connection, question, int(version_details["version"])
        )
        if question["lifecycle"] == "manual_only":
            raise InteractionForbidden(
                "manual-only questions cannot be answered through the ledger"
            )
        if question["lifecycle"] != "open":
            raise InteractionConflict("question is not open for an answer")
        now = _now()
        answer_id = str(uuid4())
        payload_json = payload.model_dump_json(exclude_none=True)
        connection.execute(
            "INSERT INTO interaction_answers(answer_id,question_id,question_version,idempotency_key,"
            "canonical_payload_hash,payload_json,actor_kind,actor_id,accepted_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                answer_id,
                question["question_id"],
                version_details["version"],
                idempotency_key,
                payload_digest,
                payload_json,
                actor_kind,
                actor_id,
                now,
            ),
        )
        mode = "provider_native" if question["source_kind"] == "native" else "mcp_pull"
        hmac_receipt = answer_receipt(
            capability_digest, answer_id, question["question_id"], int(version_details["version"])
        )
        connection.execute(
            "INSERT INTO interaction_deliveries(delivery_id,answer_id,mode,state,receipt_hash,updated_at) "
            "VALUES(%s,%s,%s,'accepted',%s,%s)",
            (str(uuid4()), answer_id, mode, receipt_hash(hmac_receipt), now),
        )
        connection.execute(
            "UPDATE interaction_questions SET lifecycle='answered' WHERE question_id=%s AND version=%s AND lifecycle='open'",
            (question["question_id"], question["version"]),
        )
        self.repository.append_event(
            connection,
            event_type="interaction.answer_accepted",
            source_terminal_id=question["terminal_id"],
            source_incarnation_id=question["incarnation_id"],
            actor_kind=actor_kind,
            actor_id=actor_id,
            reason_code="typed_answer_accepted",
            metadata={
                "answer_id": answer_id,
                "question_id": question["question_id"],
                "question_version": int(version_details["version"]),
                "delivery_state": "accepted",
            },
            group_id=question["group_id"],
            question_id=question["question_id"],
        )
        if question["group_id"]:
            self._satisfy_group_if_ready(connection, question["group_id"])
        return AnswerReceipt(
            question_id=question["question_id"],
            version=int(version_details["version"]),
            answer_id=answer_id,
            lifecycle="answered",
            delivery_state="accepted",
            accepted_at=_required_datetime(now, "answer accepted_at"),
            replayed=False,
        )

    def _satisfy_group_if_ready(self, connection: Connection, group_id: str) -> None:
        group = connection.execute(
            "SELECT * FROM interaction_groups WHERE group_id=%s FOR UPDATE", (group_id,)
        ).fetchone()
        if group is None or group["lifecycle"] not in {"open", "cycle_blocked"}:
            return
        unresolved = self._required_pending_count(connection, group_id)
        if unresolved:
            return
        updated = connection.execute(
            "UPDATE interaction_groups SET lifecycle='satisfied',version=version+1 "
            "WHERE group_id=%s AND lifecycle IN ('open','cycle_blocked')",
            (group_id,),
        )
        if updated.rowcount != 1:
            return
        now = _now()
        connection.execute(
            "UPDATE interaction_waits SET state='released',released_at=%s WHERE group_id=%s AND state IN ('waiting','cycle_blocked')",
            (now, group_id),
        )
        self.repository.append_event(
            connection,
            event_type="interaction.group_satisfied",
            source_terminal_id=group["terminal_id"],
            source_incarnation_id=group["incarnation_id"],
            actor_kind="system",
            actor_id=None,
            reason_code="required_answers_complete",
            metadata={"group_id": group_id, "lifecycle": "satisfied"},
            group_id=group_id,
        )
        self._recompute_wait_cycles(connection)

    def _created_group(
        self, connection: Connection, group: Record, *, replayed: bool
    ) -> CreatedGroup:
        questions = connection.execute(
            "SELECT question_id,version,required,lifecycle,expires_at FROM interaction_questions "
            "WHERE group_id=%s ORDER BY question_sequence",
            (group["group_id"],),
        ).fetchall()
        return CreatedGroup(
            group_id=group["group_id"],
            lifecycle=group["lifecycle"],
            wait_required=bool(group["wait_required"]),
            questions=tuple(
                {
                    "question_id": row["question_id"],
                    "version": row["version"],
                    "required": bool(row["required"]),
                    "lifecycle": row["lifecycle"],
                    "expires_at": _datetime(row["expires_at"]),
                }
                for row in questions
            ),
            replayed=replayed,
        )

    def create_group(
        self,
        capability: object,
        request: CreateGroupRequest | Mapping[str, object],
        operation_key: str | None = None,
    ) -> CreatedGroup:
        try:
            request_model = (
                request
                if isinstance(request, CreateGroupRequest)
                else CreateGroupRequest.model_validate(request)
            )
        except (ValidationError, TypeError, ValueError) as error:
            raise InteractionInvalid("invalid interaction request") from error
        for item in request_model.questions:
            if _is_secret(item.prompt) or any(_is_secret(option.label) for option in item.options):
                raise InteractionInvalid(
                    "literal credential-shaped content cannot be stored in an interaction request"
                )
        raw_key: object = operation_key
        if raw_key is None and isinstance(request, Mapping):
            raw_key = request.get("operation_key")
        key = _identifier(raw_key, "operation key", minimum=16)
        canonical_request = request_model.model_dump(mode="json")
        request_hash = digest_json(canonical_request)
        now = _now()
        with self._transaction() as connection:
            # Capacity is a store-wide invariant. Serialize creators across
            # distinct sources while retaining the normal per-source lock.
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("cao.interactions.global_capacity",),
            )
            source, actor = self._current_source_by_capability(
                connection, capability, for_update=True
            )
            self._expire_due(connection, now)
            existing = connection.execute(
                "SELECT * FROM interaction_groups WHERE terminal_id=%s AND incarnation_id=%s "
                "AND turn_generation=%s AND operation_key=%s",
                (source["terminal_id"], source["incarnation_id"], source["turn_generation"], key),
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise InteractionConflict(
                        "operation key was reused with a different question group"
                    )
                return self._created_group(connection, existing, replayed=True)
            active_total = connection.execute(
                "SELECT count(*) FROM interaction_questions q "
                "LEFT JOIN interaction_answers a ON a.question_id=q.question_id AND a.question_version=q.version "
                "LEFT JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
                "WHERE q.expires_at>%s AND (q.lifecycle IN ('open','manual_only') OR "
                "(q.required IS TRUE AND q.lifecycle='answered' AND "
                "coalesce(d.state,'accepted') NOT IN ('returned_to_mcp','agent_acknowledged'))) ",
                (now,),
            ).fetchone()[0]
            active_source = connection.execute(
                "SELECT count(*) FROM interaction_questions q "
                "LEFT JOIN interaction_answers a ON a.question_id=q.question_id AND a.question_version=q.version "
                "LEFT JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
                "WHERE q.terminal_id=%s AND q.incarnation_id=%s AND q.expires_at>%s AND "
                "(q.lifecycle IN ('open','manual_only') OR "
                "(q.required IS TRUE AND q.lifecycle='answered' AND "
                "coalesce(d.state,'accepted') NOT IN ('returned_to_mcp','agent_acknowledged'))) ",
                (source["terminal_id"], source["incarnation_id"], now),
            ).fetchone()[0]
            if (
                active_total + len(request_model.questions) > _MAX_ACTIVE
                or active_source + len(request_model.questions) > _MAX_PER_SOURCE
            ):
                raise InteractionCapacityExceeded("interaction question capacity is full")
            group_id = str(uuid4())
            expires_at = now + min(request_model.expires_in_seconds, _HARD_TTL)
            required_count = sum(1 for question in request_model.questions if question.required)
            effective_wait_required = required_count > 0
            work_binding = {
                key: source[key]
                for key in (
                    "work_job_id",
                    "work_item_id",
                    "attempt_id",
                    "attempt_generation",
                    "contract_hash",
                    "grant_id",
                    "grant_revision",
                )
                if source[key] is not None
            }
            connection.execute(
                "INSERT INTO interaction_groups(group_id,terminal_id,incarnation_id,turn_generation,operation_key,"
                "request_hash,work_binding_json,required_question_ids_json,wait_required,lifecycle,version,created_at,expires_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'open',1,%s,%s)",
                (
                    group_id,
                    source["terminal_id"],
                    source["incarnation_id"],
                    source["turn_generation"],
                    key,
                    request_hash,
                    json.dumps(work_binding, sort_keys=True),
                    "[]",
                    bool(effective_wait_required),
                    now,
                    expires_at,
                ),
            )
            required_ids: list[str] = []
            question_rows: list[tuple[object, ...]] = []
            question_version_rows: list[tuple[object, ...]] = []
            question_events: list[tuple[str, dict[str, object]]] = []
            for question_input in request_model.questions:
                q = _question(question_input)
                question_id = str(uuid4())
                options = [option.model_dump(mode="json") for option in q.options]
                credential = (
                    _is_credential_prompt(q.prompt)
                    or _is_secret(q.prompt)
                    or any(
                        _is_credential_prompt(str(option.get("label", "")))
                        or _is_secret(str(option.get("label", "")))
                        for option in options
                    )
                )
                stored_prompt = (
                    "A credential or secret request requires manual action."
                    if credential
                    else q.prompt
                )
                if credential:
                    options = []
                stored_kind = "credential" if credential else q.kind
                lifecycle = "manual_only" if credential else "open"
                capability_kind = "manual_only" if credential else "pull_explicit"
                human_required = True
                owner_value = source["owner_principal_id"]
                owner_id = owner_value if isinstance(owner_value, str) else None
                recipient_kind = "human" if owner_id else "unassigned"
                route_reason = (
                    "credential_manual_process"
                    if credential
                    else ("content_owner" if owner_id else "no_verified_content_owner")
                )
                encoded_options = json.dumps(options, ensure_ascii=False, separators=(",", ":"))
                question_rows.append(
                    (
                        question_id,
                        group_id,
                        q.client_key,
                        source["terminal_id"],
                        source["incarnation_id"],
                        source["turn_generation"],
                        stored_kind,
                        human_required,
                        stored_prompt,
                        encoded_options,
                        bool(q.allow_free_text and not credential),
                        bool(q.required),
                        recipient_kind,
                        owner_id,
                        route_reason,
                        capability_kind,
                        lifecycle,
                        now,
                        expires_at,
                    )
                )
                question_version_rows.append(
                    (
                        question_id,
                        stored_kind,
                        human_required,
                        stored_prompt,
                        encoded_options,
                        bool(q.allow_free_text and not credential),
                        bool(q.required),
                        recipient_kind,
                        owner_id,
                        route_reason,
                        capability_kind,
                        now,
                        expires_at,
                    )
                )
                if q.required:
                    required_ids.append(question_id)
                question_events.append(
                    (
                        question_id,
                        {
                            "question_id": question_id,
                            "question_version": 1,
                            "required": bool(q.required),
                            "source_kind": "explicit",
                            "recipient_kind": recipient_kind,
                            "response_capability": capability_kind,
                        },
                    )
                )
            connection.executemany(
                "INSERT INTO interaction_questions(question_id,group_id,client_key,terminal_id,incarnation_id,"
                "turn_generation,source_kind,dialog_fingerprint,provider_evidence_version,observation_id,kind,"
                "human_required,prompt_content,options_json,allow_free_text,required,recipient_kind,recipient_id,"
                "route_reason,response_capability,lifecycle,version,created_at,expires_at) "
                "VALUES(%s,%s,%s,%s,%s,%s, 'explicit',NULL,NULL,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s)",
                question_rows,
            )
            connection.executemany(
                "INSERT INTO interaction_question_versions(question_id,version,dialog_fingerprint,"
                "provider_evidence_version,observation_id,kind,human_required,prompt_content,options_json,"
                "allow_free_text,required,recipient_kind,recipient_id,route_reason,response_capability,created_at,expires_at) "
                "VALUES(%s,1,NULL,NULL,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                question_version_rows,
            )
            self.repository.append_events(
                connection,
                [
                    {
                        "event_type": "interaction.question_created",
                        "source_terminal_id": source["terminal_id"],
                        "source_incarnation_id": source["incarnation_id"],
                        "actor_kind": "agent",
                        "actor_id": source["terminal_id"],
                        "reason_code": "explicit_question_created",
                        "metadata": event_metadata,
                        "group_id": group_id,
                        "question_id": question_id,
                    }
                    for question_id, event_metadata in question_events
                ],
            )
            connection.execute(
                "UPDATE interaction_groups SET required_question_ids_json=%s WHERE group_id=%s",
                (json.dumps(required_ids), group_id),
            )
            if effective_wait_required:
                wait_id = str(uuid4())
                connection.execute(
                    "INSERT INTO interaction_waits(wait_id,group_id,terminal_id,incarnation_id,dependent_kind,"
                    "dependent_id,state,active_elapsed_before_pause,response_deadline,paused_at,released_at) "
                    "VALUES(%s,%s,%s,%s,%s ,%s,'waiting',0,%s,%s,NULL)",
                    (
                        wait_id,
                        group_id,
                        source["terminal_id"],
                        source["incarnation_id"],
                        "terminal_task",
                        source["terminal_id"],
                        expires_at,
                        now,
                    ),
                )
            self.repository.append_event(
                connection,
                event_type="interaction.group_created",
                source_terminal_id=source["terminal_id"],
                source_incarnation_id=source["incarnation_id"],
                actor_kind="agent",
                actor_id=source["terminal_id"],
                reason_code="explicit_group_created",
                metadata={
                    "group_id": group_id,
                    "count": len(request_model.questions),
                    "wait_required": effective_wait_required,
                    "operation_key_hash": hashlib.sha256(key.encode()).hexdigest(),
                },
                group_id=group_id,
            )
            created = connection.execute(
                "SELECT * FROM interaction_groups WHERE group_id=%s", (group_id,)
            ).fetchone()
            return self._created_group(connection, created, replayed=False)

    def accept_answer(
        self,
        principal: object,
        question_id: str,
        expected_version: int,
        payload: object,
        idempotency_key: str,
    ) -> AnswerReceipt:
        principal = self.policy.require_principal(principal, write=True)
        question_id = _identifier(question_id, "question id")
        with self._transaction() as connection:
            question, details, source = self._lock_question_for_mutation(
                connection, question_id, expected_version
            )
            principal = self.policy.require_owner(principal, source)
            self._current_version(question, expected_version)
            self._assert_question_source(connection, question)
            return self._store_answer(
                connection,
                question,
                details,
                payload,
                actor_kind="principal",
                actor_id=principal.id,
                idempotency_key=idempotency_key,
                capability_digest=source["capability_hash"],
            )

    def agent_answer(
        self,
        capability: object,
        question_id: str,
        expected_version: int,
        payload: object,
        idempotency_key: str,
    ) -> AnswerReceipt:
        question_id = _identifier(question_id, "question id")
        with self._transaction() as connection:
            source, initial_actor = self._current_source_by_capability(connection, capability)
            initial_question: Record | None = connection.execute(
                "SELECT * FROM interaction_questions WHERE question_id=%s", (question_id,)
            ).fetchone()
            if initial_question is None:
                raise InteractionNotFound("question does not exist")
            self._lock_sources(
                connection,
                (
                    (source["terminal_id"], source["incarnation_id"]),
                    (initial_question["terminal_id"], initial_question["incarnation_id"]),
                ),
            )
            source, actor = self._current_source_by_capability(connection, capability)
            if actor.source_id != initial_actor.source_id:
                raise InteractionStale("agent capability source changed")
            question, details = self._load_question(
                connection, question_id, expected_version, for_update=True
            )
            question_source = self._assert_question_source(connection, question)
            self._current_version(question, expected_version)
            if not self.policy.agent_can_answer(connection, actor, details, question_source):
                raise InteractionForbidden("this agent is not authorized to answer this question")
            return self._store_answer(
                connection,
                question,
                details,
                payload,
                actor_kind="agent",
                actor_id=actor.terminal_id,
                idempotency_key=idempotency_key,
                capability_digest=source["capability_hash"],
            )

    async def wait_for_answers(
        self,
        capability: object,
        group_id: str,
        timeout_seconds: float = 0,
        *,
        after_event_sequence: int | None = None,
    ) -> WaitResult:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds < 0
            or timeout_seconds > _MAX_WAIT_SECONDS
        ):
            raise InteractionInvalid("answer wait must be bounded to 30 seconds")
        if after_event_sequence is not None and (
            type(after_event_sequence) is not int or after_event_sequence < 0
        ):
            raise InteractionInvalid("event cursor must be a non-negative integer")
        group_id = _identifier(group_id, "group id")
        deadline = time.monotonic() + float(timeout_seconds)
        while True:
            with self._transaction() as connection:
                source, actor = self._current_source_by_capability(
                    connection, capability, for_update=True
                )
                self._expire_due(connection, _now())
                group = connection.execute(
                    "SELECT * FROM interaction_groups WHERE group_id=%s FOR UPDATE", (group_id,)
                ).fetchone()
                if group is None:
                    raise InteractionNotFound("interaction group does not exist")
                if (group["terminal_id"], group["incarnation_id"]) != (
                    actor.terminal_id,
                    actor.incarnation_id,
                ):
                    raise InteractionForbidden("group belongs to another interaction source")
                if group["turn_generation"] != source["turn_generation"]:
                    return WaitResult(
                        group_id=group_id,
                        state="stale",
                        next_event_sequence=self._event_sequence(connection),
                    )
                required_open = connection.execute(
                    "SELECT count(*) FROM interaction_questions WHERE group_id=%s AND required IS TRUE AND lifecycle IN ('open','manual_only')",
                    (group_id,),
                ).fetchone()[0]
                blocking_state = group["lifecycle"]
                if blocking_state in {"expired", "cancelled", "stale", "cycle_blocked"} and not (
                    blocking_state == "expired"
                    and self._expired_group_has_deliverable_answers(connection, group_id)
                ):
                    return WaitResult(
                        group_id=group_id,
                        state=blocking_state,
                        next_event_sequence=self._event_sequence(connection),
                    )
                if required_open:
                    result = WaitResult(
                        group_id=group_id,
                        state="pending",
                        answers=(),
                        next_event_sequence=self._event_sequence(connection),
                        next_poll_after_ms=1000,
                    )
                else:
                    answered = connection.execute(
                        "SELECT q.question_id,q.version,a.answer_id,a.payload_json,a.accepted_at,d.delivery_id,d.state "
                        "FROM interaction_questions q JOIN interaction_answers a ON a.question_id=q.question_id "
                        "AND a.question_version=q.version JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
                        "WHERE q.group_id=%s ORDER BY q.question_sequence",
                        (group_id,),
                    ).fetchall()
                    returned: list[ReturnedAnswer] = []
                    for row in answered:
                        receipt = answer_receipt(
                            source["capability_hash"],
                            row["answer_id"],
                            row["question_id"],
                            row["version"],
                        )
                        if row["state"] == "accepted":
                            connection.execute(
                                "UPDATE interaction_deliveries SET state='returned_to_mcp',updated_at=%s WHERE delivery_id=%s AND state='accepted'",
                                (_now(), row["delivery_id"]),
                            )
                            self.repository.append_event(
                                connection,
                                event_type="interaction.answer_returned",
                                source_terminal_id=actor.terminal_id,
                                source_incarnation_id=actor.incarnation_id,
                                actor_kind="agent",
                                actor_id=actor.terminal_id,
                                reason_code="mcp_pull_result",
                                metadata={
                                    "answer_id": row["answer_id"],
                                    "question_id": row["question_id"],
                                    "question_version": row["version"],
                                    "delivery_state": "returned_to_mcp",
                                },
                                group_id=group_id,
                                question_id=row["question_id"],
                            )
                        try:
                            payload = InteractionAnswerPayload.model_validate_json(
                                row["payload_json"]
                            )
                        except ValidationError as error:
                            raise InteractionSchemaMismatch(
                                "stored answer payload is invalid"
                            ) from error
                        returned.append(
                            ReturnedAnswer(
                                question_id=row["question_id"],
                                version=row["version"],
                                receipt=receipt,
                                response=payload,
                            )
                        )
                    self._satisfy_group_if_ready(connection, group_id)
                    result = WaitResult(
                        group_id=group_id,
                        state="ready",
                        answers=tuple(returned),
                        next_event_sequence=self._event_sequence(connection),
                        next_poll_after_ms=0,
                    )
            if result.state != "pending":
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return result
            await asyncio.sleep(min(0.25, remaining))

    @staticmethod
    def _event_sequence(connection: Connection) -> int:
        row = connection.execute(
            "SELECT coalesce(max(sequence),0) FROM interaction_events"
        ).fetchone()
        if row is None:
            raise InteractionSchemaMismatch("interaction event sequence query returned no row")
        return int(row[0])

    def acknowledge_answers(
        self,
        capability: object,
        group_id: str,
        acknowledgements: Sequence[AnswerAck | Mapping[str, object]],
    ) -> tuple[DeliveryReceipt, ...]:
        group_id = _identifier(group_id, "group id")
        if (
            not isinstance(acknowledgements, Sequence)
            or not acknowledgements
            or len(acknowledgements) > 20
        ):
            raise InteractionInvalid("one to twenty answer receipts are required")
        try:
            acks = tuple(
                item if isinstance(item, AnswerAck) else AnswerAck.model_validate(item)
                for item in acknowledgements
            )
        except (ValidationError, TypeError, ValueError) as error:
            raise InteractionInvalid("answer receipt is malformed") from error
        if len({ack.question_id for ack in acks}) != len(acks):
            raise InteractionInvalid("duplicate answer receipts are not allowed")
        if any(not isinstance(ack.receipt, str) or not ack.receipt.isascii() for ack in acks):
            raise InteractionInvalid("answer receipt is malformed")
        receipts: list[DeliveryReceipt] = []
        with self._transaction() as connection:
            source, actor = self._current_source_by_capability(
                connection, capability, for_update=True
            )
            group = connection.execute(
                "SELECT * FROM interaction_groups WHERE group_id=%s FOR UPDATE", (group_id,)
            ).fetchone()
            if group is None:
                raise InteractionNotFound("interaction group does not exist")
            if (group["terminal_id"], group["incarnation_id"], group["turn_generation"]) != (
                actor.terminal_id,
                actor.incarnation_id,
                source["turn_generation"],
            ):
                raise InteractionStale("group no longer belongs to the current source turn")
            for ack in acks:
                question = connection.execute(
                    "SELECT * FROM interaction_questions WHERE question_id=%s AND group_id=%s FOR UPDATE",
                    (ack.question_id, group_id),
                ).fetchone()
                if question is None:
                    raise InteractionNotFound("answer question is not in this group")
                self._current_version(question, ack.version)
                self._assert_question_source(connection, question)
                answer = connection.execute(
                    "SELECT a.answer_id,a.accepted_at,d.delivery_id,d.state,d.receipt_hash FROM interaction_answers a "
                    "JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
                    "WHERE a.question_id=%s AND a.question_version=%s",
                    (ack.question_id, ack.version),
                ).fetchone()
                if answer is None:
                    raise InteractionConflict("there is no accepted answer for this receipt")
                expected_receipt = answer_receipt(
                    source["capability_hash"], answer["answer_id"], ack.question_id, ack.version
                )
                if (
                    not hmac.compare_digest(expected_receipt, ack.receipt)
                    or receipt_hash(ack.receipt) != answer["receipt_hash"]
                ):
                    raise InteractionForbidden(
                        "answer receipt is not valid for this source capability"
                    )
                if answer["state"] not in {"returned_to_mcp", "agent_acknowledged"}:
                    raise InteractionConflict(
                        "answer must be returned before the agent can acknowledge it"
                    )
                now = _now()
                if answer["state"] == "returned_to_mcp":
                    connection.execute(
                        "UPDATE interaction_deliveries SET state='agent_acknowledged',updated_at=%s "
                        "WHERE delivery_id=%s AND state='returned_to_mcp'",
                        (now, answer["delivery_id"]),
                    )
                    self.repository.append_event(
                        connection,
                        event_type="interaction.answer_acknowledged",
                        source_terminal_id=actor.terminal_id,
                        source_incarnation_id=actor.incarnation_id,
                        actor_kind="agent",
                        actor_id=actor.terminal_id,
                        reason_code="answer_receipt_acknowledged",
                        metadata={
                            "answer_id": answer["answer_id"],
                            "question_id": ack.question_id,
                            "question_version": ack.version,
                            "delivery_state": "agent_acknowledged",
                        },
                        group_id=group_id,
                        question_id=ack.question_id,
                    )
                receipts.append(
                    DeliveryReceipt(
                        delivery_id=answer["delivery_id"],
                        answer_id=answer["answer_id"],
                        state="agent_acknowledged",
                        question_id=ack.question_id,
                        version=ack.version,
                        updated_at=_required_datetime(now, "delivery updated_at"),
                    )
                )
        return tuple(receipts)

    def _metadata(
        self,
        connection: Connection,
        question: Mapping[str, Any],
        *,
        actor: AgentActor | None = None,
        source: InteractionRow | None = None,
        source_loaded: bool = False,
        delivery_state: str | None = None,
        delivery_loaded: bool = False,
        group_progress: Mapping[str, object] | None = None,
        group_progress_loaded: bool = False,
    ) -> dict[str, object]:
        if not source_loaded:
            source = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
                (question["terminal_id"], question["incarnation_id"]),
            ).fetchone()
            # Preserve the detail-reader's terminal lookup. Page readers pass
            # a source that was validated against this terminal in their
            # repeatable-read snapshot and do not need a duplicate query.
            self._terminal(connection, str(question["terminal_id"]))
        if not delivery_loaded:
            delivery = connection.execute(
                "SELECT d.state FROM interaction_answers a JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
                "WHERE a.question_id=%s AND a.question_version=%s",
                (question["question_id"], question["version"]),
            ).fetchone()
            delivery_state = delivery["state"] if delivery else None
        if not group_progress_loaded and question.get("group_id"):
            counts = connection.execute(
                "SELECT count(*) AS total,sum(CASE WHEN q.lifecycle='answered' THEN 1 ELSE 0 END) AS answered "
                "FROM interaction_questions q WHERE q.group_id=%s AND q.required IS TRUE",
                (question["group_id"],),
            ).fetchone()
            if counts is None:
                raise InteractionSchemaMismatch(
                    "required interaction progress query returned no row"
                )
            group_progress = {
                "required_total": counts["total"],
                "required_answered": counts["answered"] or 0,
            }
        elif not group_progress_loaded:
            group_progress = None
        recipient_kind = str(question["recipient_kind"])
        recipient_label = {
            "human": "Human",
            "agent": "Supervisor agent",
            "unassigned": "Unassigned",
        }.get(recipient_kind, "Manual review")
        can_answer = False
        if actor is not None and source is not None:
            can_answer = self.policy.agent_can_answer(connection, actor, question, source)
        elif source is not None:
            can_answer = bool(
                question["recipient_kind"] == "human"
                and question["human_required"]
                and question["lifecycle"] == "open"
            )
        return {
            "question_id": question["question_id"],
            "version": question["version"],
            "group_id": question["group_id"],
            "lifecycle": question["lifecycle"],
            "delivery_state": delivery_state,
            "source": {
                "terminal_id": question["terminal_id"],
                "incarnation_id": question["incarnation_id"],
                "provider": source["provider"] if source else None,
                "provider_version": source["provider_version"] if source else None,
                "execution_mode": source["execution_mode"] if source else None,
            },
            "task": (
                {
                    "work_item_id": source["work_item_id"] if source else None,
                    "attempt_id": source["attempt_id"] if source else None,
                    "session_name": source["session_name"] if source else None,
                }
                if source and source["work_item_id"]
                else None
            ),
            "kind": question["kind"],
            "required": bool(question["required"]),
            "recipient": {
                "kind": recipient_kind,
                "label": recipient_label,
                "reason": question["route_reason"],
            },
            "group_progress": group_progress,
            "origin": question["source_kind"],
            "can_answer": bool(can_answer),
            "manual_reason": (
                question["route_reason"]
                if question["response_capability"] in {"manual_only", "unsupported"}
                else None
            ),
            "created_at": _datetime(question["created_at"]),
            "expires_at": _datetime(question["expires_at"]),
        }

    def _detail(
        self,
        connection: Connection,
        question: Record,
        details: Mapping[str, Any],
        *,
        actor: AgentActor | None = None,
    ) -> dict[str, object]:
        result = self._metadata(connection, details, actor=actor)
        result.update(
            {
                "prompt": details["prompt_content"],
                "options": self._decode_options(details),
                "allow_free_text": bool(details["allow_free_text"]),
                "answer_receipt": self._answer_receipt_for(connection, question, details),
                "delegation_candidate": self._public_delegation_candidate(
                    self.policy.delegation_candidate(connection, question)
                ),
                "delegation": self._delegation_projection(
                    connection, question["question_id"], details["version"]
                ),
            }
        )
        return result

    @staticmethod
    def _public_delegation_candidate(candidate: Mapping[str, str] | None) -> dict[str, str] | None:
        if candidate is None:
            return None
        return {
            "terminal_id": candidate["terminal_id"],
            "incarnation_id": candidate["incarnation_id"],
            "assignment_id": candidate["assignment_id"],
        }

    def _answer_receipt_for(
        self,
        connection: Connection,
        question: Record,
        details: Mapping[str, Any],
    ) -> AnswerReceipt | None:
        answer = connection.execute(
            "SELECT a.answer_id,a.accepted_at,d.state FROM interaction_answers a "
            "JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
            "WHERE a.question_id=%s AND a.question_version=%s",
            (question["question_id"], details["version"]),
        ).fetchone()
        if answer is None:
            return None
        return AnswerReceipt(
            question_id=question["question_id"],
            version=int(details["version"]),
            answer_id=answer["answer_id"],
            lifecycle="answered",
            delivery_state=answer["state"],
            accepted_at=_required_datetime(answer["accepted_at"], "answer accepted_at"),
            replayed=False,
        )

    def _delegation_projection(
        self,
        connection: Connection,
        question_id: str,
        version: int,
    ) -> dict[str, object] | None:
        row = connection.execute(
            "SELECT * FROM interaction_delegations WHERE question_id=%s "
            "ORDER BY created_at DESC,delegation_id DESC LIMIT 1",
            (question_id,),
        ).fetchone()
        if row is None:
            return None
        if row["revoked_at"] is not None:
            state = "revoked"
        elif row["expires_at"] <= _now():
            state = "expired"
        elif int(row["question_version"]) != int(version):
            state = "revoked"
        else:
            state = "active"
        return {
            "terminal_id": row["supervisor_terminal_id"],
            "assignment_id": row["assignment_id"],
            "expires_at": _required_datetime(row["expires_at"], "delegation expires_at"),
            "state": state,
        }

    def get_detail(
        self,
        principal_or_actor: object,
        question_id: str,
        expected_version: int | None = None,
    ) -> dict[str, object]:
        question_id = _identifier(question_id, "question id")
        self._expire_before_read()
        with self._read() as connection:
            question, details = self._load_question(connection, question_id, expected_version)
            source = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
                (question["terminal_id"], question["incarnation_id"]),
            ).fetchone()
            if source is None:
                raise InteractionNotFound("question source no longer exists")
            self._validate_work(connection, source)
            if type(principal_or_actor) is AgentActor:
                actor_source, actor = self._live_actor(connection, principal_or_actor)
                if not self.policy.agent_can_read(connection, actor, details, source):
                    raise InteractionForbidden("agent does not have question detail authority")
                if details["turn_generation"] != actor_source["turn_generation"]:
                    raise InteractionStale("question belongs to an earlier source turn")
                return self._detail(connection, question, details, actor=actor)
            if isinstance(principal_or_actor, str):
                raise InteractionForbidden("authenticate the agent capability before detail access")
            self.policy.require_owner(principal_or_actor, source)
            return self._detail(connection, question, details)

    def agent_get(self, capability: object, question_id: str) -> dict[str, object]:
        actor = self.authenticate_agent(capability)
        return self.get_detail(actor, question_id)

    def list_metadata(
        self,
        principal: object,
        filters: Mapping[str, Any] | None = None,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> InteractionPage:
        principal = self.policy.require_principal(principal)
        filters = filters or {}
        if isinstance(filters, Mapping):
            requested_cursor = filters.get("cursor")
            if cursor is None and isinstance(requested_cursor, str):
                cursor = requested_cursor
            if "limit" in filters and limit == 50:
                requested_limit = filters["limit"]
                if isinstance(requested_limit, int) and not isinstance(requested_limit, bool):
                    limit = requested_limit
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InteractionInvalid("interaction page limit must be 1..100")
        try:
            after = cursor_decode(cursor) if cursor else None
        except ValueError as error:
            raise InteractionInvalid("interaction cursor or filter is invalid") from error
        self._expire_before_read()
        with self._read() as connection:
            return self._metadata_page_on_connection(
                connection, filters=filters, after=after, limit=limit
            )

    @staticmethod
    def validate_projection_cursor(cursor: str | None) -> None:
        """Validate a projection page cursor before an SSE response starts."""
        if cursor is None:
            return
        try:
            timestamp, question_id = cursor_decode(cursor)
            if not math.isfinite(timestamp) or str(UUID(question_id)) != question_id:
                raise ValueError("invalid interaction projection cursor")
        except ValueError as error:
            raise InteractionInvalid("interaction projection cursor is invalid") from error

    def projection_snapshot(
        self, *, cursor: str | None = None, limit: int = 100
    ) -> dict[str, object]:
        """Return bounded, content-free interaction metadata for an authorized observer stream.

        The transport must authenticate and revalidate its stream scope before
        each call. This reader is deliberately distinct from principal/agent
        detail methods: it never returns prompts, options, answer payloads,
        delegation data, capability values, or action authority. Metadata,
        delivery receipts, and the high-water mark are read in one PostgreSQL
        snapshot so their versions cannot disagree.
        """
        self.validate_projection_cursor(cursor)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InteractionInvalid("interaction projection page limit must be 1..100")
        try:
            after = cursor_decode(cursor) if cursor else None
        except ValueError as error:
            raise InteractionInvalid("interaction projection cursor is invalid") from error
        self._expire_before_read()
        with self._read() as connection:
            page = self._metadata_page_on_connection(
                connection, filters={}, after=after, limit=limit
            )
            items: list[dict[str, object]] = []
            for raw_metadata in page.items:
                metadata = dict(raw_metadata)
                question_id = metadata.get("question_id")
                if not isinstance(question_id, str):
                    raise InteractionInvalid("projection question ID is invalid")
                question, details = self._load_question(connection, question_id)
                answer_receipt = self._answer_receipt_for(connection, question, details)
                metadata["can_answer"] = False
                metadata["answer_receipt"] = (
                    answer_receipt.model_dump(mode="json") if answer_receipt is not None else None
                )
                items.append(metadata)
            high_water = connection.execute(
                "SELECT coalesce(max(sequence),0) FROM interaction_events"
            ).fetchone()[0]
            return {
                "items": items,
                "next_cursor": page.next_cursor,
                "event_sequence": int(high_water),
            }

    def _metadata_page_on_connection(
        self,
        connection: Connection,
        *,
        filters: Mapping[str, Any],
        after: tuple[float, str] | None,
        limit: int,
    ) -> InteractionPage:
        if isinstance(filters, Mapping):
            status_filter = filters.get("status")
        else:
            status_filter = None
        if status_filter is not None and not isinstance(status_filter, (list, tuple, set)):
            status_filter = [status_filter]
        # Metadata is the intentionally broad, content-free observation
        # surface (including for `cao:admin` self-claim). Detail and answer
        # access remain protected by the exact source-owner policy below.
        clauses: list[str] = []
        args: list[object] = []
        if status_filter:
            statuses = tuple(str(item) for item in status_filter)
            if any(
                item
                not in {
                    "open",
                    "answered",
                    "expired",
                    "cancelled",
                    "stale",
                    "manual_only",
                    "reconciled_external",
                }
                for item in statuses
            ):
                raise InteractionInvalid("unknown interaction lifecycle filter")
            clauses.append("q.lifecycle IN (" + ",".join("%s" for _ in statuses) + ")")
            args.extend(statuses)
        if filters.get("terminal_id"):
            clauses.append("q.terminal_id=%s")
            args.append(_identifier(filters["terminal_id"], "terminal filter"))
        if filters.get("work_item_id"):
            clauses.append("s.work_item_id=%s")
            args.append(_identifier(filters["work_item_id"], "Work item filter"))
        if filters.get("recipient"):
            clauses.append("q.recipient_kind=%s")
            args.append(_identifier(filters["recipient"], "recipient filter"))
        if after:
            clauses.append("(q.created_at<%s OR (q.created_at=%s AND q.question_id<%s))")
            args.extend((after[0], after[0], after[1]))
        sql = (
            "SELECT q.*,s.provider,s.provider_version,s.execution_mode,s.session_name,s.work_item_id,s.attempt_id "
            "FROM interaction_questions q LEFT JOIN interaction_sources s ON s.terminal_id=q.terminal_id "
            "AND s.incarnation_id=q.incarnation_id "
        )
        if clauses:
            sql += "WHERE " + " AND ".join(clauses) + " "
        sql += "ORDER BY q.created_at DESC,q.question_id DESC LIMIT %s"
        args.append(limit + 1)
        rows = connection.execute(sql, args).fetchall()
        question_ids = [str(row["question_id"]) for row in rows]
        delivery_states: dict[tuple[str, int], str] = {}
        group_progress: dict[str, dict[str, object]] = {}
        group_ids = {str(row["group_id"]) for row in rows if row["group_id"] is not None}
        if question_ids:
            for delivery in connection.execute(
                "SELECT a.question_id,a.question_version,d.state "
                "FROM interaction_answers a JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
                "WHERE a.question_id=ANY(%s)",
                (question_ids,),
            ).fetchall():
                delivery_states[
                    (str(delivery["question_id"]), int(delivery["question_version"]))
                ] = str(delivery["state"])
        for group_id in group_ids:
            group_progress[group_id] = {"required_total": 0, "required_answered": 0}
        if group_ids:
            for counts in connection.execute(
                "SELECT q.group_id,count(*) FILTER (WHERE q.required IS TRUE) AS required_total, "
                "count(*) FILTER (WHERE q.required IS TRUE AND q.lifecycle='answered') AS required_answered "
                "FROM interaction_questions q WHERE q.group_id=ANY(%s) "
                "GROUP BY q.group_id",
                (list(group_ids),),
            ).fetchall():
                group_progress[str(counts["group_id"])] = {
                    "required_total": counts["required_total"],
                    "required_answered": counts["required_answered"],
                }
        source_keys = list(
            dict.fromkeys((str(row["terminal_id"]), str(row["incarnation_id"])) for row in rows)
        )
        source_rows: dict[tuple[str, str], InteractionRow | None] = {
            source_key: None for source_key in source_keys
        }
        if source_keys:
            source_pairs = ",".join("(%s,%s)" for _ in source_keys)
            source_parameters = tuple(value for source_key in source_keys for value in source_key)
            for source in connection.execute(
                "SELECT * FROM interaction_sources WHERE (terminal_id,incarnation_id) IN ("
                + source_pairs
                + ")",
                source_parameters,
            ).fetchall():
                source_key = (str(source["terminal_id"]), str(source["incarnation_id"]))
                source_rows[source_key] = source
        present_sources = [source for source in source_rows.values() if source is not None]
        terminal_ids = list(dict.fromkeys(str(source["terminal_id"]) for source in present_sources))
        session_names = list(
            dict.fromkeys(str(source["session_name"]) for source in present_sources)
        )
        terminal_rows: dict[str, InteractionRow] = {}
        current_incarnations: dict[str, str] = {}
        attempts_by_terminal: dict[str, list[Record]] = {}
        work_table_present = False
        if terminal_ids:
            for terminal in connection.execute(
                "SELECT id,session_incarnation_id,tmux_session,provider,allowed_tools "
                "FROM terminals WHERE id=ANY(%s)",
                (terminal_ids,),
            ).fetchall():
                terminal_rows[str(terminal["id"])] = terminal
            for incarnation in connection.execute(
                "SELECT session_name,incarnation_id FROM session_incarnations WHERE session_name=ANY(%s)",
                (session_names,),
            ).fetchall():
                current_incarnations[str(incarnation["session_name"])] = str(
                    incarnation["incarnation_id"]
                )
            work_table = connection.execute(
                "SELECT to_regclass(%s) AS relation", ("work_attempts",)
            ).fetchone()
            work_table_present = work_table is not None and work_table["relation"] is not None
            if work_table_present:
                for attempt in connection.execute(
                    "SELECT terminal_id,id,generation,state FROM work_attempts WHERE terminal_id=ANY(%s)",
                    (terminal_ids,),
                ).fetchall():
                    attempts_by_terminal.setdefault(str(attempt["terminal_id"]), []).append(attempt)
        invalid_sources: set[tuple[str, str]] = set()
        for source_key, source_record_or_none in source_rows.items():
            if source_record_or_none is None:
                continue
            terminal_id, _ = source_key
            source_session = str(source_record_or_none["session_name"])
            source_has_work = (
                source_record_or_none["attempt_id"] is not None
                or source_record_or_none["execution_mode"]
                in {"work_managed", "work-managed", "managed_work"}
                or bool(attempts_by_terminal.get(terminal_id))
            )
            try:
                self._validate_source_row(
                    connection,
                    source_record_or_none,
                    require_capability=False,
                    terminal_row=terminal_rows.get(terminal_id),
                    terminal_loaded=True,
                    current_incarnation=current_incarnations.get(source_session),
                    incarnation_loaded=True,
                    work_unmanaged=work_table_present and not source_has_work,
                )
            except InteractionError:
                invalid_sources.add(source_key)
        visible: list[tuple[dict[str, Any], float, str]] = []
        for row in rows:
            source_key = (str(row["terminal_id"]), str(row["incarnation_id"]))
            metadata_source = source_rows[source_key]
            if metadata_source is None:
                continue
            if source_key in invalid_sources:
                continue
            question_id = str(row["question_id"])
            version = int(row["version"])
            metadata_group_id = str(row["group_id"]) if row["group_id"] is not None else None
            visible.append(
                (
                    self._metadata(
                        connection,
                        dict(row),
                        source=metadata_source,
                        source_loaded=True,
                        delivery_state=delivery_states.get((question_id, version)),
                        delivery_loaded=True,
                        group_progress=(
                            group_progress.get(metadata_group_id) if metadata_group_id else None
                        ),
                        group_progress_loaded=True,
                    ),
                    float(row["created_at"]),
                    question_id,
                )
            )
            if len(visible) > limit:
                break
        next_cursor = None
        if len(rows) > limit:
            # Cursor over the last visible authorized row, not an
            # unrelated source which happened to be in the SQL window.
            if len(visible) > limit:
                _, last_created, last_question_id = visible[limit - 1]
                next_cursor = cursor_encode(last_created, last_question_id)
            else:
                last = rows[-1]
                next_cursor = cursor_encode(last["created_at"], last["question_id"])
        if len(visible) > limit:
            visible = visible[:limit]
        return InteractionPage(
            items=tuple(metadata for metadata, _, _ in visible), next_cursor=next_cursor
        )

    def agent_list(
        self,
        capability: object,
        filters: Mapping[str, Any] | None = None,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> InteractionPage:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InteractionInvalid("interaction page limit must be 1..100")
        filters = filters or {}
        try:
            after = cursor_decode(cursor) if cursor else None
        except ValueError as error:
            raise InteractionInvalid("interaction cursor is invalid") from error
        self._expire_before_read()
        with self._read() as connection:
            source, actor = self._current_source_by_capability(connection, capability)
            rows = connection.execute(
                "SELECT q.* FROM interaction_questions q WHERE "
                "(q.terminal_id=%s AND q.incarnation_id=%s) OR q.question_id IN ("
                "SELECT d.question_id FROM interaction_delegations d WHERE d.supervisor_terminal_id=%s "
                "AND d.supervisor_incarnation_id=%s AND d.revoked_at IS NULL AND d.expires_at>%s) "
                "ORDER BY q.created_at DESC,q.question_id DESC",
                (
                    actor.terminal_id,
                    actor.incarnation_id,
                    actor.terminal_id,
                    actor.incarnation_id,
                    _now(),
                ),
            ).fetchall()
            visible: list[tuple[dict[str, Any], float, str]] = []
            for q in rows:
                if after and not (
                    q["created_at"] < after[0]
                    or (q["created_at"] == after[0] and q["question_id"] < after[1])
                ):
                    continue
                if filters.get("status") and q["lifecycle"] not in filters["status"]:
                    continue
                source_row = connection.execute(
                    "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
                    (q["terminal_id"], q["incarnation_id"]),
                ).fetchone()
                if source_row is None:
                    continue
                try:
                    self._validate_source_row(connection, source_row, require_capability=False)
                except InteractionError:
                    continue
                _, details = self._load_question(connection, q["question_id"])
                if not self.policy.agent_can_read(connection, actor, details, source_row):
                    continue
                visible.append(
                    (
                        self._metadata(connection, details, actor=actor),
                        float(q["created_at"]),
                        str(q["question_id"]),
                    )
                )
                if len(visible) > limit:
                    break
            next_cursor = None
            if len(visible) > limit:
                _, last_created, last_question_id = visible[limit - 1]
                next_cursor = cursor_encode(last_created, last_question_id)
                visible = visible[:limit]
            return InteractionPage(
                items=tuple(metadata for metadata, _, _ in visible), next_cursor=next_cursor
            )

    def bind_validated_assignment_owner(
        self,
        terminal_id: str,
        incarnation_id: str,
        assignment_id: str,
    ) -> dict[str, object]:
        """Finalize child lineage only after the durable assignment is submitted."""
        terminal_id = _identifier(terminal_id, "terminal id")
        incarnation_id = _identifier(incarnation_id, "incarnation id")
        assignment_id = _identifier(assignment_id, "assignment id")
        with self._transaction() as connection:
            source = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s FOR UPDATE",
                (terminal_id, incarnation_id),
            ).fetchone()
            if source is None:
                raise InteractionNotFound("child interaction source is not registered")
            # This system-side binding only transfers content-owner metadata
            # after the durable assignment is submitted. Native observer
            # sources intentionally have no explicit MCP capability, but still
            # require every source/incarnation/profile/Work check here.
            self._validate_source_row(connection, source, require_capability=False)
            parent = self._validated_assignment_owner(
                connection, assignment_id=assignment_id, child_terminal_id=terminal_id
            )
            if parent is None:
                raise InteractionStale(
                    "assignment does not prove the current child and parent relation"
                )
            inherited_owner = source["owner_principal_id"] or parent["owner_principal_id"]
            owner_kind = (
                source["owner_source"]
                if source["owner_principal_id"]
                else ("validated_parent_inheritance" if inherited_owner else "unowned")
            )
            connection.execute(
                "UPDATE interaction_sources SET parent_terminal_id=%s,parent_incarnation_id=%s,assignment_id=%s,"
                "owner_principal_id=%s,owner_source=%s WHERE terminal_id=%s AND incarnation_id=%s",
                (
                    parent["terminal_id"],
                    parent["incarnation_id"],
                    assignment_id,
                    inherited_owner,
                    owner_kind,
                    terminal_id,
                    incarnation_id,
                ),
            )
            if inherited_owner and not source["owner_principal_id"]:
                open_questions = connection.execute(
                    "SELECT * FROM interaction_questions WHERE terminal_id=%s AND incarnation_id=%s "
                    "AND lifecycle IN ('open','manual_only')",
                    (terminal_id, incarnation_id),
                ).fetchall()
                for question in open_questions:
                    next_version = self._write_question_version(
                        connection,
                        question,
                        {
                            "recipient_kind": "human",
                            "recipient_id": inherited_owner,
                            "route_reason": "validated_parent_content_owner",
                        },
                    )
                    self.repository.append_event(
                        connection,
                        event_type="interaction.routing_updated",
                        source_terminal_id=terminal_id,
                        source_incarnation_id=incarnation_id,
                        actor_kind="system",
                        actor_id=None,
                        reason_code="validated_assignment_owner_bound",
                        metadata={
                            "question_id": question["question_id"],
                            "question_version": next_version,
                            "recipient_kind": "human",
                        },
                        group_id=question["group_id"],
                        question_id=question["question_id"],
                    )
            self.repository.append_event(
                connection,
                event_type="interaction.assignment_owner_bound",
                source_terminal_id=terminal_id,
                source_incarnation_id=incarnation_id,
                actor_kind="system",
                actor_id=None,
                reason_code="validated_assignment_submitted",
                metadata={
                    "assignment_id": assignment_id,
                    "terminal_id": terminal_id,
                    "incarnation_id": incarnation_id,
                },
            )
            self._recompute_wait_cycles(connection)
            updated = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
                (terminal_id, incarnation_id),
            ).fetchone()
            return self._source_projection(updated)

    def delegate_question(
        self,
        principal: object,
        question_id: str,
        expected_version: int,
        *,
        expected_assignment_id: str,
        reason: str,
        expires_in_seconds: int = 60,
    ) -> DelegationReceipt:
        principal = self.policy.require_principal(principal, write=True)
        question_id = _identifier(question_id, "question id")
        expected_assignment_id = _identifier(expected_assignment_id, "assignment id")
        reason = _identifier(reason, "delegation reason", maximum=512)
        if _is_secret(reason):
            raise InteractionInvalid("delegation reason cannot contain credential-shaped content")
        if type(expires_in_seconds) is not int or not 1 <= expires_in_seconds <= 300:
            raise InteractionInvalid("delegation lifetime must be 1..300 seconds")
        with self._transaction() as connection:
            initial_question: Record | None = connection.execute(
                "SELECT * FROM interaction_questions WHERE question_id=%s", (question_id,)
            ).fetchone()
            if initial_question is None:
                raise InteractionNotFound("question does not exist")
            initial_source = self._assert_question_source(connection, initial_question)
            initial_candidate = self.policy.delegation_candidate(connection, initial_question)
            identities = [(initial_source["terminal_id"], initial_source["incarnation_id"])]
            if initial_candidate is not None:
                identities.append(
                    (initial_candidate["terminal_id"], initial_candidate["incarnation_id"])
                )
            locked_sources = self._lock_sources(connection, identities)
            source_key = (initial_source["terminal_id"], initial_source["incarnation_id"])
            source = locked_sources[source_key]
            question, _ = self._load_question(connection, question_id, for_update=True)
            self._expire_due(connection, _now())
            question, _ = self._load_question(connection, question_id, for_update=True)
            self._current_version(question, expected_version)
            details = self._assert_question_open(connection, question, expected_version)
            principal = self.policy.require_owner(principal, source)
            if (
                question["source_kind"] != "explicit"
                or question["kind"] in {"approval", "credential", "unknown"}
                or question["response_capability"] != "pull_explicit"
                or question["lifecycle"] != "open"
            ):
                raise InteractionForbidden("this question type cannot be delegated")
            candidate = self.policy.delegation_candidate(connection, question)
            if candidate is None or candidate["assignment_id"] != expected_assignment_id:
                raise InteractionForbidden(
                    "the current server-validated supervisor assignment does not match"
                )
            parent_key = (candidate["terminal_id"], candidate["incarnation_id"])
            parent = locked_sources.get(parent_key)
            if parent is None:
                raise InteractionStale("supervisor assignment changed during delegation")
            self._validate_source_row(connection, parent, require_capability=True)
            if parent["owner_principal_id"] != principal.id:
                raise InteractionForbidden(
                    "only the current source owner may delegate this question"
                )
            delegation_id = str(uuid4())
            expires_at = _now() + expires_in_seconds
            new_version = self._write_question_version(
                connection,
                question,
                {
                    "human_required": False,
                    "recipient_kind": "agent",
                    "recipient_id": candidate["terminal_id"],
                    "route_reason": "owner_reviewed_clarification",
                },
            )
            connection.execute(
                "INSERT INTO interaction_delegations(delegation_id,question_id,question_version,owner_principal_id,"
                "supervisor_terminal_id,supervisor_incarnation_id,assignment_id,reason,created_at,expires_at,revoked_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)",
                (
                    delegation_id,
                    question_id,
                    new_version,
                    principal.id,
                    candidate["terminal_id"],
                    candidate["incarnation_id"],
                    expected_assignment_id,
                    reason,
                    _now(),
                    expires_at,
                ),
            )
            self.repository.append_event(
                connection,
                event_type="interaction.delegated",
                source_terminal_id=question["terminal_id"],
                source_incarnation_id=question["incarnation_id"],
                actor_kind="principal",
                actor_id=principal.id,
                reason_code="owner_reviewed_exact_question",
                metadata={
                    "delegation_id": delegation_id,
                    "question_id": question_id,
                    "question_version": new_version,
                    "assignment_id": expected_assignment_id,
                    "expires_at": int(expires_at),
                    "recipient_kind": "agent",
                },
                group_id=question["group_id"],
                question_id=question_id,
            )
            self._recompute_wait_cycles(connection)
            return DelegationReceipt(
                delegation_id=delegation_id,
                question_id=question_id,
                question_version=new_version,
                supervisor_terminal_id=candidate["terminal_id"],
                supervisor_incarnation_id=candidate["incarnation_id"],
                assignment_id=expected_assignment_id,
                expires_at=_required_datetime(expires_at, "delegation expires_at"),
            )

    def revoke_delegation(
        self,
        principal: object,
        question_id: str,
        expected_version: int,
        *,
        reason: str,
    ) -> DelegationReceipt:
        principal = self.policy.require_principal(principal, write=True)
        question_id = _identifier(question_id, "question id")
        reason = _identifier(reason, "delegation revocation reason", maximum=512)
        if len(reason.encode("utf-8")) > 512 or _is_secret(reason):
            raise InteractionInvalid("delegation reason cannot contain credential-shaped content")
        with self._transaction() as connection:
            question, _, source = self._lock_question_for_mutation(connection, question_id)
            self._current_version(question, expected_version)
            principal = self.policy.require_owner(principal, source)
            delegation = connection.execute(
                "SELECT * FROM interaction_delegations WHERE question_id=%s AND question_version=%s "
                "ORDER BY created_at DESC,delegation_id DESC LIMIT 1",
                (question_id, expected_version),
            ).fetchone()
            if delegation is None or delegation["owner_principal_id"] != principal.id:
                raise InteractionNotFound("active delegation does not exist")
            if delegation["revoked_at"] is not None:
                raise InteractionConflict("delegation has already been revoked")
            accepted = connection.execute(
                "SELECT 1 FROM interaction_answers WHERE question_id=%s AND question_version=%s LIMIT 1",
                (question_id, expected_version),
            ).fetchone()
            if accepted is not None:
                # Acceptance commits the reviewed authority and answer version
                # together. Revoking afterward would orphan a durable answer
                # or advance the question beyond the version the source can pull.
                raise InteractionConflict(
                    "a delegation cannot be revoked after its answer is accepted"
                )
            now = _now()
            connection.execute(
                "UPDATE interaction_delegations SET revoked_at=%s WHERE delegation_id=%s AND revoked_at IS NULL",
                (now, delegation["delegation_id"]),
            )
            new_version = self._write_question_version(
                connection,
                question,
                {
                    "human_required": True,
                    "recipient_kind": "human",
                    "recipient_id": source["owner_principal_id"],
                    "route_reason": "owner_revoked_supervisor_delegation",
                },
            )
            event_sequence = self.repository.append_event(
                connection,
                event_type="interaction.delegation_revoked",
                source_terminal_id=question["terminal_id"],
                source_incarnation_id=question["incarnation_id"],
                actor_kind="principal",
                actor_id=principal.id,
                reason_code="owner_revoked_delegation",
                metadata={
                    "delegation_id": delegation["delegation_id"],
                    "question_id": question_id,
                    "question_version": new_version,
                    "assignment_id": delegation["assignment_id"],
                    "recipient_kind": "human",
                },
                group_id=question["group_id"],
                question_id=question_id,
            )
            self.repository.append_private_audit(
                connection,
                event_sequence=event_sequence,
                action="delegation_revoked",
                operator_principal_id=principal.id,
                target_question_id=question_id,
                reason=reason,
                created_at=now,
            )
            self._recompute_wait_cycles(connection)
            return DelegationReceipt(
                delegation_id=delegation["delegation_id"],
                question_id=question_id,
                question_version=new_version,
                supervisor_terminal_id=delegation["supervisor_terminal_id"],
                supervisor_incarnation_id=delegation["supervisor_incarnation_id"],
                assignment_id=delegation["assignment_id"],
                expires_at=_required_datetime(delegation["expires_at"], "delegation expires_at"),
                revoked_at=_required_datetime(now, "delegation revoked_at"),
            )

    def escalate(
        self,
        capability: object,
        question_id: str,
        reason_code: str,
        detail: str | None = None,
    ) -> dict[str, object]:
        question_id = _identifier(question_id, "question id")
        reason_code = _identifier(reason_code, "escalation reason", maximum=64)
        if detail is not None and (
            not isinstance(detail, str) or len(detail.encode("utf-8")) > 512 or _is_secret(detail)
        ):
            raise InteractionInvalid("escalation detail is invalid")
        with self._transaction() as connection:
            source, initial_actor = self._current_source_by_capability(connection, capability)
            initial_question: Record | None = connection.execute(
                "SELECT * FROM interaction_questions WHERE question_id=%s", (question_id,)
            ).fetchone()
            if initial_question is None:
                raise InteractionNotFound("question does not exist")
            self._lock_sources(
                connection,
                (
                    (source["terminal_id"], source["incarnation_id"]),
                    (initial_question["terminal_id"], initial_question["incarnation_id"]),
                ),
            )
            source, actor = self._current_source_by_capability(connection, capability)
            if actor.source_id != initial_actor.source_id:
                raise InteractionStale("agent capability source changed")
            question, _ = self._load_question(connection, question_id, for_update=True)
            question_source = self._assert_question_source(connection, question)
            if not self.policy.agent_can_read(connection, actor, question, question_source):
                raise InteractionForbidden("agent cannot escalate an unrelated question")
            if question["lifecycle"] not in {"open", "manual_only"}:
                raise InteractionConflict("question is no longer open for escalation")
            owner_id = question_source["owner_principal_id"]
            version = self._write_question_version(
                connection,
                question,
                {
                    "human_required": True,
                    "recipient_kind": "human" if owner_id else "unassigned",
                    "recipient_id": owner_id,
                    "route_reason": reason_code,
                },
            )
            self.repository.append_event(
                connection,
                event_type="interaction.escalated",
                source_terminal_id=question["terminal_id"],
                source_incarnation_id=question["incarnation_id"],
                actor_kind="agent",
                actor_id=actor.terminal_id,
                reason_code=reason_code,
                metadata={
                    "question_id": question_id,
                    "question_version": version,
                    "recipient_kind": "human" if owner_id else "unassigned",
                },
                group_id=question["group_id"],
                question_id=question_id,
            )
            self._recompute_wait_cycles(connection)
            updated, details = self._load_question(connection, question_id)
            return self._detail(connection, updated, details, actor=actor)

    def designate_content_owner(
        self,
        principal: object,
        terminal_id: str,
        expected_incarnation_id: str,
        reason: str,
    ) -> dict[str, object]:
        principal = self.policy.require_admin(principal)
        terminal_id = _identifier(terminal_id, "terminal id")
        expected_incarnation_id = _identifier(expected_incarnation_id, "incarnation id")
        reason = _identifier(reason, "owner designation reason", maximum=512)
        if len(reason.encode("utf-8")) > 512 or _is_secret(reason):
            raise InteractionInvalid("owner designation reason cannot contain credentials")
        with self._transaction() as connection:
            source = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s FOR UPDATE",
                (terminal_id, expected_incarnation_id),
            ).fetchone()
            if source is None:
                raise InteractionNotFound("interaction source does not exist")
            self._validate_source_row(connection, source, require_capability=True)
            if source["owner_principal_id"] not in {None, principal.id}:
                raise InteractionConflict("content owner is already designated")
            connection.execute(
                "UPDATE interaction_sources SET owner_principal_id=%s,owner_source='operator_designation' "
                "WHERE terminal_id=%s AND incarnation_id=%s",
                (principal.id, terminal_id, expected_incarnation_id),
            )
            questions = connection.execute(
                "SELECT * FROM interaction_questions WHERE terminal_id=%s AND incarnation_id=%s "
                "AND lifecycle IN ('open','manual_only')",
                (terminal_id, expected_incarnation_id),
            ).fetchall()
            for question in questions:
                version = self._write_question_version(
                    connection,
                    question,
                    {
                        "recipient_kind": "human",
                        "recipient_id": principal.id,
                        "route_reason": "operator_designated_owner",
                    },
                )
                self.repository.append_event(
                    connection,
                    event_type="interaction.routing_updated",
                    source_terminal_id=terminal_id,
                    source_incarnation_id=expected_incarnation_id,
                    actor_kind="principal",
                    actor_id=principal.id,
                    reason_code="operator_designated_owner",
                    metadata={
                        "question_id": question["question_id"],
                        "question_version": version,
                        "recipient_kind": "human",
                    },
                    group_id=question["group_id"],
                    question_id=question["question_id"],
                )
            event_sequence = self.repository.append_event(
                connection,
                event_type="interaction.owner_designated",
                source_terminal_id=terminal_id,
                source_incarnation_id=expected_incarnation_id,
                actor_kind="principal",
                actor_id=principal.id,
                reason_code="operator_owner_designation",
                metadata={"terminal_id": terminal_id, "incarnation_id": expected_incarnation_id},
            )
            self.repository.append_private_audit(
                connection,
                event_sequence=event_sequence,
                action="content_owner_designated",
                operator_principal_id=principal.id,
                target_terminal_id=terminal_id,
                target_incarnation_id=expected_incarnation_id,
                reason=reason,
            )
            self._recompute_wait_cycles(connection)
            updated = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
                (terminal_id, expected_incarnation_id),
            ).fetchone()
            return {"source": self._source_projection(updated)}

    def cancel(
        self,
        principal: object,
        question_id: str,
        expected_version: int,
        reason: str,
    ) -> dict[str, object]:
        principal = self.policy.require_principal(principal, write=True)
        question_id = _identifier(question_id, "question id")
        reason = _identifier(reason, "cancellation reason", maximum=512)
        if len(reason.encode("utf-8")) > 512 or _is_secret(reason):
            raise InteractionInvalid("cancellation reason cannot contain credentials")
        # Commit expiry transitions before checking cancellation eligibility.
        # Raising from the same transaction that expires a question would roll
        # that transition back and allow a late cancellation to win on retry.
        self._expire_before_read()
        with self._transaction() as connection:
            question, _, source = self._lock_question_for_mutation(connection, question_id)
            self._current_version(question, expected_version)
            principal = self.policy.require_owner(principal, source)
            if question["lifecycle"] == "expired":
                raise InteractionStale("question has expired")
            if question["lifecycle"] not in {"open", "manual_only"}:
                raise InteractionConflict("only an open interaction can be cancelled")
            now = _now()
            connection.execute(
                "UPDATE interaction_questions SET lifecycle='cancelled' WHERE question_id=%s AND version=%s",
                (question_id, expected_version),
            )
            if question["group_id"] and question["required"]:
                group = connection.execute(
                    "SELECT * FROM interaction_groups WHERE group_id=%s FOR UPDATE",
                    (question["group_id"],),
                ).fetchone()
                connection.execute(
                    "UPDATE interaction_questions SET lifecycle='cancelled' WHERE group_id=%s AND lifecycle IN ('open','manual_only')",
                    (question["group_id"],),
                )
                connection.execute(
                    "UPDATE interaction_groups SET lifecycle='cancelled',version=version+1 WHERE group_id=%s AND lifecycle IN ('open','cycle_blocked')",
                    (question["group_id"],),
                )
                connection.execute(
                    "UPDATE interaction_waits SET state='cancelled',released_at=%s WHERE group_id=%s AND state IN ('waiting','cycle_blocked')",
                    (now, question["group_id"]),
                )
                group_id = question["group_id"]
            else:
                group_id = question["group_id"]
            connection.execute(
                "UPDATE interaction_delegations SET revoked_at=%s WHERE question_id=%s AND revoked_at IS NULL",
                (now, question_id),
            )
            event_sequence = self.repository.append_event(
                connection,
                event_type="interaction.cancelled",
                source_terminal_id=question["terminal_id"],
                source_incarnation_id=question["incarnation_id"],
                actor_kind="principal",
                actor_id=principal.id,
                reason_code="owner_cancelled_question",
                metadata={
                    "question_id": question_id,
                    "question_version": expected_version,
                    "lifecycle": "cancelled",
                },
                group_id=group_id,
                question_id=question_id,
            )
            self.repository.append_private_audit(
                connection,
                event_sequence=event_sequence,
                action="question_cancelled",
                operator_principal_id=principal.id,
                target_question_id=question_id,
                reason=reason,
                created_at=now,
            )
            self._recompute_wait_cycles(connection)
            return {
                "question_id": question_id,
                "version": expected_version,
                "lifecycle": "cancelled",
                "delivery_state": self._current_delivery_state(
                    connection, question_id, expected_version
                ),
            }

    def reconcile(
        self,
        principal: object,
        question_id: str,
        expected_version: int,
        outcome: str,
        evidence_code: str,
        note: str | None = None,
    ) -> dict[str, object]:
        principal = self.policy.require_principal(principal, write=True)
        question_id = _identifier(question_id, "question id")
        outcome = _identifier(outcome, "reconciliation outcome", maximum=64)
        evidence_code = _identifier(evidence_code, "evidence code", maximum=128)
        if (
            _is_secret(evidence_code)
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}", evidence_code) is None
        ):
            raise InteractionInvalid(
                "evidence code must be a safe identifier without credential-shaped content"
            )
        if note is not None and (
            not isinstance(note, str) or len(note.encode("utf-8")) > 512 or _is_secret(note)
        ):
            raise InteractionInvalid("reconciliation note is invalid")
        if outcome not in {
            "answered_outside_cao",
            "no_effect_proven",
            "remains_uncertain",
            "dialog_still_open",
        }:
            raise InteractionInvalid("unsupported interaction reconciliation outcome")
        with self._transaction() as connection:
            question, _, source = self._lock_question_for_mutation(connection, question_id)
            self._current_version(question, expected_version)
            principal = self.policy.require_owner(principal, source)
            delivery = connection.execute(
                "SELECT d.* FROM interaction_answers a JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
                "WHERE a.question_id=%s AND a.question_version=%s",
                (question_id, expected_version),
            ).fetchone()
            now = _now()
            if outcome == "answered_outside_cao":
                if delivery is not None and delivery["state"] not in {
                    "uncertain",
                    "no_effect",
                    "reconciled",
                }:
                    raise InteractionConflict(
                        "only an uncertain external effect can be reconciled outside CAO"
                    )
                connection.execute(
                    "UPDATE interaction_questions SET lifecycle='reconciled_external' WHERE question_id=%s AND version=%s",
                    (question_id, expected_version),
                )
                if delivery is not None:
                    connection.execute(
                        "UPDATE interaction_deliveries SET state='reconciled',updated_at=%s WHERE delivery_id=%s",
                        (now, delivery["delivery_id"]),
                    )
                    self._append_delivery_attempt_state(
                        connection,
                        delivery,
                        "reconciled",
                        {"evidence_code": evidence_code, "outcome": outcome},
                        now,
                    )
            elif outcome == "no_effect_proven":
                if delivery is None or delivery["state"] not in {"claimed", "uncertain"}:
                    raise InteractionConflict(
                        "no unresolved provider effect can be marked no-effect"
                    )
                connection.execute(
                    "UPDATE interaction_deliveries SET state='no_effect',updated_at=%s WHERE delivery_id=%s",
                    (now, delivery["delivery_id"]),
                )
                self._append_delivery_attempt_state(
                    connection,
                    delivery,
                    "no_effect",
                    {"evidence_code": evidence_code, "outcome": outcome},
                    now,
                )
            elif outcome == "remains_uncertain":
                if delivery is None or delivery["state"] not in {
                    "claimed",
                    "uncertain",
                    "reconciled",
                }:
                    raise InteractionConflict("there is no uncertain provider effect to reconcile")
                connection.execute(
                    "UPDATE interaction_deliveries SET state='uncertain',updated_at=%s WHERE delivery_id=%s",
                    (now, delivery["delivery_id"]),
                )
                self._append_delivery_attempt_state(
                    connection,
                    delivery,
                    "uncertain",
                    {"evidence_code": evidence_code, "outcome": outcome},
                    now,
                )
            else:
                if (
                    question["source_kind"] != "native"
                    or question["response_capability"] != "manual_only"
                ):
                    raise InteractionConflict(
                        "open-dialog evidence applies only to a manual native observation"
                    )
                # The item remains blocking and answerable only through the explicit
                # human reconciliation route; this never performs a provider effect.
            self.repository.append_event(
                connection,
                event_type="interaction.reconciled",
                source_terminal_id=question["terminal_id"],
                source_incarnation_id=question["incarnation_id"],
                actor_kind="principal",
                actor_id=principal.id,
                reason_code=evidence_code,
                metadata={
                    "question_id": question_id,
                    "question_version": expected_version,
                    "outcome": outcome,
                    "delivery_state": self._current_delivery_state(
                        connection, question_id, expected_version
                    ),
                },
                group_id=question["group_id"],
                question_id=question_id,
            )
            if question["group_id"] and outcome == "answered_outside_cao":
                self._satisfy_group_if_ready(connection, question["group_id"])
            return {
                "question_id": question_id,
                "version": expected_version,
                "lifecycle": (
                    "reconciled_external"
                    if outcome == "answered_outside_cao"
                    else question["lifecycle"]
                ),
                "delivery_state": self._current_delivery_state(
                    connection, question_id, expected_version
                ),
            }

    @staticmethod
    def _current_delivery_state(
        connection: Connection, question_id: str, version: int
    ) -> str | None:
        row = connection.execute(
            "SELECT d.state FROM interaction_answers a JOIN interaction_deliveries d ON d.answer_id=a.answer_id "
            "WHERE a.question_id=%s AND a.question_version=%s",
            (question_id, version),
        ).fetchone()
        return row["state"] if row is not None else None

    def history(self, principal_or_actor: object, question_id: str) -> tuple[HistoryEvent, ...]:
        question_id = _identifier(question_id, "question id")
        self._expire_before_read()
        with self._read() as connection:
            question = connection.execute(
                "SELECT * FROM interaction_questions WHERE question_id=%s", (question_id,)
            ).fetchone()
            if question is None:
                raise InteractionNotFound("question does not exist")
            source = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
                (question["terminal_id"], question["incarnation_id"]),
            ).fetchone()
            if source is None:
                raise InteractionNotFound("question source no longer exists")
            self._validate_work(connection, source)
            if type(principal_or_actor) is AgentActor:
                actor_source, actor = self._live_actor(connection, principal_or_actor)
                _, details = self._load_question(connection, question_id)
                if not self.policy.agent_can_read(connection, actor, details, source):
                    raise InteractionForbidden("agent cannot read this interaction history")
                if actor_source["terminal_id"] not in {
                    question["terminal_id"],
                    (self.policy.delegation_candidate(connection, question) or {}).get(
                        "terminal_id"
                    ),
                }:
                    raise InteractionForbidden(
                        "history is outside the current interaction relationship"
                    )
            else:
                self.policy.require_owner(principal_or_actor, source)
            rows = connection.execute(
                "SELECT * FROM interaction_events WHERE question_id=%s ORDER BY sequence",
                (question_id,),
            ).fetchall()
            return tuple(
                HistoryEvent(
                    sequence=row["sequence"],
                    event_type=row["event_type"],
                    actor_kind=row["actor_kind"],
                    actor_label=(
                        "Agent"
                        if row["actor_kind"] == "agent"
                        else "Human" if row["actor_kind"] == "principal" else "System"
                    ),
                    occurred_at=_required_datetime(row["created_at"], "event created_at"),
                    safe_metadata=json.loads(row["metadata_json"]),
                )
                for row in rows
            )

    def wait_graph(self, principal: object) -> dict[str, object]:
        principal = self.policy.require_principal(principal)
        self._expire_before_read()
        with self._read() as connection:
            waits = connection.execute(
                "SELECT w.*,g.lifecycle,g.turn_generation,g.expires_at,s.owner_principal_id "
                "FROM interaction_waits w JOIN interaction_groups g ON g.group_id=w.group_id "
                "JOIN interaction_sources s ON s.terminal_id=w.terminal_id AND s.incarnation_id=w.incarnation_id "
                "WHERE w.state IN ('waiting','cycle_blocked') AND g.lifecycle IN ('open','cycle_blocked') "
                "AND s.owner_principal_id=%s AND s.revoked_at IS NULL AND s.turn_generation=g.turn_generation "
                "ORDER BY w.paused_at,w.wait_id",
                (principal.id,),
            ).fetchall()
            nodes: dict[str, dict[str, object]] = {}
            edges: list[dict[str, object]] = []
            for wait in waits:
                source = connection.execute(
                    "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
                    (wait["terminal_id"], wait["incarnation_id"]),
                ).fetchone()
                if source is None:
                    continue
                try:
                    self._validate_source_row(connection, source, require_capability=False)
                except (InteractionError, InteractionSchemaMismatch):
                    continue
                task_id = f"task:{wait['dependent_id']}"
                group_id = f"question_group:{wait['group_id']}"
                nodes[task_id] = {
                    "id": task_id,
                    "kind": "task",
                    "state": "waiting",
                    "label": "Terminal task",
                }
                nodes[group_id] = {
                    "id": group_id,
                    "kind": "question_group",
                    "state": wait["lifecycle"],
                    "label": "Required operator response",
                }
                edges.append({"from": task_id, "to": group_id, "required": True, "cycle": False})
                questions = connection.execute(
                    "SELECT * FROM interaction_questions WHERE group_id=%s AND source_kind='explicit' "
                    "AND required IS TRUE AND lifecycle='open' AND expires_at>%s ORDER BY question_id",
                    (wait["group_id"], _now()),
                ).fetchall()
                seen_supervisors: set[tuple[str, str]] = set()
                for question in questions:
                    try:
                        self._assert_question_source(connection, question)
                        delegation = self.policy.active_delegation(connection, question, _now())
                        if delegation is None:
                            continue
                        supervisor = connection.execute(
                            "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
                            (
                                delegation["supervisor_terminal_id"],
                                delegation["supervisor_incarnation_id"],
                            ),
                        ).fetchone()
                        if supervisor is None:
                            continue
                        self._validate_source_row(connection, supervisor, require_capability=False)
                    except (InteractionError, InteractionSchemaMismatch):
                        continue
                    supervisor_key = (
                        delegation["supervisor_terminal_id"],
                        delegation["supervisor_incarnation_id"],
                    )
                    if supervisor_key in seen_supervisors:
                        continue
                    seen_supervisors.add(supervisor_key)
                    supervisor_task_id = f"task:{delegation['supervisor_terminal_id']}"
                    nodes.setdefault(
                        supervisor_task_id,
                        {
                            "id": supervisor_task_id,
                            "kind": "task",
                            "state": "waiting",
                            "label": "Supervisor task",
                        },
                    )
                    edges.append(
                        {
                            "from": group_id,
                            "to": supervisor_task_id,
                            "required": True,
                            "cycle": False,
                        }
                    )
            _mark_cycle_edges(edges)
            return {"nodes": list(nodes.values()), "edges": edges}

    @staticmethod
    def _append_delivery_attempt_state(
        connection: Connection,
        delivery: Mapping[str, object],
        state: str,
        evidence: Mapping[str, object],
        now: float,
    ) -> None:
        """Append a bounded, content-free record for one native effect attempt."""
        if not connection.in_transaction:
            raise ValueError("delivery attempt history requires a caller-owned transaction")
        nonce = delivery["claim_nonce"]
        delivery_id = delivery["delivery_id"]
        if state not in {"claimed", "confirmed", "uncertain", "no_effect", "reconciled"}:
            raise InteractionInvalid("unsupported native delivery attempt state")
        if not isinstance(nonce, str) or not nonce:
            raise InteractionConflict("native delivery attempt has no claim nonce")
        allowed = {
            "evidence_code",
            "outcome",
            "observation_id",
            "source_fingerprint",
            "provider_evidence_version",
            "dialog_fingerprint",
        }
        if set(evidence) - allowed:
            raise InteractionInvalid("delivery evidence contains an unapproved field")
        clean: dict[str, str] = {}
        for key, value in evidence.items():
            if not isinstance(value, str) or not 1 <= len(value) <= 256 or _is_secret(value):
                raise InteractionInvalid("delivery evidence must be a bounded safe code")
            if (
                key in {"source_fingerprint", "dialog_fingerprint"}
                and re.fullmatch(r"[0-9a-f]{64}", value) is None
            ):
                raise InteractionInvalid("delivery fingerprint is invalid")
            if (
                key in {"evidence_code", "outcome"}
                and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) is None
            ):
                raise InteractionInvalid("delivery evidence code is invalid")
            clean[key] = value
        prior_row = connection.execute(
            "SELECT coalesce(max(sequence),0) FROM interaction_delivery_attempts WHERE attempt_id=%s",
            (nonce,),
        ).fetchone()
        if prior_row is None:
            raise InteractionSchemaMismatch("delivery attempt sequence query returned no row")
        prior = prior_row[0]
        connection.execute(
            "INSERT INTO interaction_delivery_attempts(attempt_id,sequence,delivery_id,claim_nonce,state,evidence_json,created_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (
                nonce,
                int(prior) + 1,
                delivery_id,
                nonce,
                state,
                json.dumps(clean, sort_keys=True),
                now,
            ),
        )

    def recover_inflight_claims(self) -> int:
        """Mark process-lost provider claims uncertain without replaying them."""
        recovered = 0
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT d.*,a.question_id,a.question_version,q.group_id,q.terminal_id,q.incarnation_id "
                "FROM interaction_deliveries d JOIN interaction_answers a ON a.answer_id=d.answer_id "
                "JOIN interaction_questions q ON q.question_id=a.question_id "
                "WHERE d.mode='provider_native' AND d.state='claimed' "
                "ORDER BY d.claimed_at,d.delivery_id FOR UPDATE OF d SKIP LOCKED"
            ).fetchall()
            for delivery in rows:
                now = _now()
                changed = connection.execute(
                    "UPDATE interaction_deliveries SET state='uncertain',updated_at=%s "
                    "WHERE delivery_id=%s AND state='claimed'",
                    (now, delivery["delivery_id"]),
                ).rowcount
                if not changed:
                    continue
                self._append_delivery_attempt_state(
                    connection,
                    delivery,
                    "uncertain",
                    {"evidence_code": "process_restart_recovery", "outcome": "uncertain"},
                    now,
                )
                self.repository.append_event(
                    connection,
                    event_type="interaction.delivery_uncertain",
                    source_terminal_id=delivery["terminal_id"],
                    source_incarnation_id=delivery["incarnation_id"],
                    actor_kind="system",
                    actor_id=None,
                    reason_code="process_restart_recovery",
                    metadata={
                        "answer_id": delivery["answer_id"],
                        "question_id": delivery["question_id"],
                        "question_version": delivery["question_version"],
                        "delivery_state": "uncertain",
                        "outcome": "uncertain",
                    },
                    group_id=delivery["group_id"],
                    question_id=delivery["question_id"],
                )
                recovered += 1
        return recovered

    def claim_native_delivery(
        self,
        capability: object,
        answer_id: str,
        *,
        observation_id: str,
        source_fingerprint: str,
        provider_evidence_version: str,
    ) -> DeliveryClaim:
        """Claim an evidenced native effect; current provider matrix is fail-closed."""
        _identifier(answer_id, "answer id")
        _identifier(observation_id, "observation id")
        _identifier(source_fingerprint, "source fingerprint", maximum=256)
        _identifier(provider_evidence_version, "provider evidence version", maximum=256)
        with self._transaction() as connection:
            source, _ = self._current_source_by_capability(connection, capability)
            delivery = connection.execute(
                "SELECT d.*,a.question_id,a.question_version,q.terminal_id,q.incarnation_id,q.turn_generation,"
                "q.source_kind,q.response_capability,q.dialog_fingerprint,q.observation_id,q.provider_evidence_version,q.kind "
                "FROM interaction_deliveries d JOIN interaction_answers a ON a.answer_id=d.answer_id "
                "JOIN interaction_questions q ON q.question_id=a.question_id WHERE d.answer_id=%s",
                (answer_id,),
            ).fetchone()
            if delivery is None:
                raise InteractionNotFound("interaction answer delivery does not exist")
            if (delivery["terminal_id"], delivery["incarnation_id"]) != (
                source["terminal_id"],
                source["incarnation_id"],
            ):
                raise InteractionForbidden("native delivery belongs to another source")
            self._assert_question_source(connection, delivery)
            # No provider/version in the initial support matrix has a proven
            # exact native response operation. Detection remains a manual-only
            # notice, so this route cannot create a sendable claim today.
            raise InteractionForbidden("native response is unsupported for this provider/version")

    def observe_native(
        self,
        terminal_id: str,
        incarnation_id: str,
        turn_generation: int,
        observation: object,
    ) -> ObservationReceipt:
        """Record only a current, exact-target native manual signal.

        The provider object may contain visible text/options for a future
        evidence-backed adapter. This ledger intentionally discards both and
        persists only opaque fingerprints plus a fixed safe notice.
        """
        terminal_id = _identifier(terminal_id, "terminal id")
        incarnation_id = _identifier(incarnation_id, "incarnation id")
        if type(turn_generation) is not int or turn_generation < 1:
            raise InteractionInvalid("native observation turn is invalid")

        def value(name: str, default: object = None) -> object:
            return (
                observation.get(name, default)
                if isinstance(observation, Mapping)
                else getattr(observation, name, default)
            )

        if value("observation") != "manual_signal":
            raise InteractionInvalid("only exact provider manual signals are recorded")
        source_fingerprint = value("source_fingerprint")
        dialog_fingerprint = value("dialog_fingerprint")
        dialog_version = value("dialog_version")
        exact_target = value("exact_target")
        observed_at = value("observed_at")
        dialog_kind = value("dialog_kind", "unknown")
        if not all(
            isinstance(item, str)
            for item in (source_fingerprint, dialog_fingerprint, dialog_version)
        ):
            raise InteractionInvalid("native observation fingerprints are required")
        assert isinstance(source_fingerprint, str)
        assert isinstance(dialog_fingerprint, str)
        assert isinstance(dialog_version, str)
        if (
            re.fullmatch(r"[0-9a-f]{64}", source_fingerprint) is None
            or re.fullmatch(r"[0-9a-f]{64}", dialog_fingerprint) is None
        ):
            raise InteractionInvalid("native observation fingerprints must be SHA-256 values")
        if not isinstance(exact_target, (tuple, list)) or len(exact_target) != 3:
            raise InteractionInvalid(
                "native observation target must identify terminal, session and window"
            )
        exact_target_values = tuple(exact_target)
        if any(not isinstance(item, str) or not item for item in exact_target_values):
            raise InteractionInvalid("native observation target identifiers are invalid")
        if not isinstance(dialog_kind, str) or dialog_kind not in {
            "text",
            "single_choice",
            "multi_choice",
            "choice_or_text",
            "approval",
            "credential",
            "unknown",
        }:
            dialog_kind = "unknown"
        try:
            observed = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
            if observed.tzinfo is None:
                raise ValueError("timezone required")
            observed_epoch = observed.timestamp()
        except (TypeError, ValueError, OverflowError) as error:
            raise InteractionInvalid("native observation timestamp is invalid") from error
        now = _now()
        if observed_epoch > now + 5 or now - observed_epoch > 30:
            raise InteractionStale("native provider observation is not current")
        with self._transaction() as connection:
            source = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s FOR UPDATE",
                (terminal_id, incarnation_id),
            ).fetchone()
            if source is None:
                raise InteractionNotFound("interaction source is not registered")
            # Provider-native dialogs remain manual terminal interactions even
            # when installing the explicit MCP capability failed.
            self._validate_source_row(connection, source, require_capability=False)
            if source["turn_generation"] != turn_generation:
                raise InteractionStale("native observation belongs to an earlier source turn")
            terminal = self._terminal(connection, terminal_id)
            if terminal is None:
                raise InteractionStale("native observation target terminal no longer exists")
            if exact_target_values != (
                terminal_id,
                source["session_name"],
                terminal["tmux_window"],
            ):
                raise InteractionStale(
                    "native observation target no longer matches the source terminal"
                )
            if source_fingerprint != dialog_fingerprint:
                raise InteractionInvalid(
                    "manual native signal must bind one exact viewport fingerprint"
                )
            self._expire_due(connection, now)
            observation_identity = (
                f"{terminal_id}\0{incarnation_id}\0{turn_generation}\0"
                f"{dialog_fingerprint}\0{dialog_version}"
            )
            observation_id = hashlib.sha256(
                f"{observation_identity}\0{observed_at}".encode("utf-8")
            ).hexdigest()
            existing = connection.execute(
                "SELECT * FROM interaction_questions WHERE terminal_id=%s AND incarnation_id=%s "
                "AND turn_generation=%s AND observation_id=%s",
                (terminal_id, incarnation_id, turn_generation, observation_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["dialog_fingerprint"] != dialog_fingerprint
                    or existing["provider_evidence_version"] != dialog_version
                ):
                    raise InteractionConflict(
                        "native observation identity was reused with different evidence"
                    )
                return ObservationReceipt(
                    observation_id=observation_id,
                    question_id=existing["question_id"],
                    lifecycle=existing["lifecycle"],
                    response_capability="manual_only",
                    manual_reason="Native provider dialogs require manual action in the exact terminal.",
                    turn_generation=existing["turn_generation"],
                    replayed=True,
                )
            open_identical = connection.execute(
                "SELECT * FROM interaction_questions WHERE terminal_id=%s AND incarnation_id=%s "
                "AND turn_generation=%s AND source_kind='native' AND dialog_fingerprint=%s "
                "AND provider_evidence_version=%s AND lifecycle IN ('manual_only','expired','cancelled') "
                "ORDER BY question_sequence DESC LIMIT 1",
                (
                    terminal_id,
                    incarnation_id,
                    turn_generation,
                    dialog_fingerprint,
                    dialog_version,
                ),
            ).fetchone()
            previous_native = self._latest_native_question(connection, terminal_id, incarnation_id)
            if (
                open_identical is not None
                and previous_native is not None
                and open_identical["question_id"] == previous_native["question_id"]
                and self._native_closed_event(connection, open_identical["question_id"]) is None
            ):
                # Distinct viewport samples of the latest still-open dialog
                # share one durable question. A terminal or superseded older
                # occurrence cannot absorb a reappearing dialog; it needs a new
                # row so exact closure can clear the latest physical gate.
                return ObservationReceipt(
                    observation_id=open_identical["observation_id"],
                    question_id=open_identical["question_id"],
                    lifecycle=open_identical["lifecycle"],
                    response_capability="manual_only",
                    manual_reason="Native provider dialogs require manual action in the exact terminal.",
                    turn_generation=open_identical["turn_generation"],
                    replayed=True,
                )
            if (
                previous_native is not None
                and previous_native["dialog_fingerprint"] == dialog_fingerprint
                and previous_native["provider_evidence_version"] == dialog_version
                and previous_native["lifecycle"] in {"manual_only", "stale", "expired", "cancelled"}
                and self._native_closed_event(connection, previous_native["question_id"]) is None
                and self._has_websocket_advance_chain(
                    connection,
                    terminal_id,
                    incarnation_id,
                    int(previous_native["turn_generation"]),
                    turn_generation,
                )
            ):
                # A new viewport sample of the same exact dialog during a
                # sequence of manual WebSocket key frames does not create a
                # replacement notice or reset the original wait's identity.
                return ObservationReceipt(
                    observation_id=previous_native["observation_id"],
                    question_id=previous_native["question_id"],
                    lifecycle=previous_native["lifecycle"],
                    response_capability="manual_only",
                    manual_reason="Native provider dialogs require manual action in the exact terminal.",
                    turn_generation=previous_native["turn_generation"],
                    replayed=True,
                )
            # A new exact active-dialog fingerprint replaces older pending
            # native notices for this turn, while preserving their audit row.
            replaced = connection.execute(
                "SELECT question_id FROM interaction_questions WHERE terminal_id=%s AND incarnation_id=%s "
                "AND turn_generation=%s AND source_kind='native' AND lifecycle='manual_only'",
                (terminal_id, incarnation_id, turn_generation),
            ).fetchall()
            for row in replaced:
                connection.execute(
                    "UPDATE interaction_questions SET lifecycle='stale' WHERE question_id=%s AND lifecycle='manual_only'",
                    (row["question_id"],),
                )
                self.repository.append_event(
                    connection,
                    event_type="interaction.stale",
                    source_terminal_id=terminal_id,
                    source_incarnation_id=incarnation_id,
                    actor_kind="system",
                    actor_id=None,
                    reason_code="native_dialog_replaced",
                    metadata={
                        "question_id": row["question_id"],
                        "turn_generation": turn_generation,
                    },
                    question_id=row["question_id"],
                )
            question_id = str(uuid4())
            expires_at = now + _DEFAULT_TTL
            kind = "credential" if dialog_kind == "credential" else dialog_kind
            reason_code = (
                "credential_manual_action"
                if kind == "credential"
                else "native_dialog_manual_action"
            )
            owner_id = source["owner_principal_id"]
            connection.execute(
                "INSERT INTO interaction_questions(question_id,group_id,client_key,terminal_id,incarnation_id,"
                "turn_generation,source_kind,dialog_fingerprint,provider_evidence_version,observation_id,kind,"
                "human_required,prompt_content,options_json,allow_free_text,required,recipient_kind,recipient_id,"
                "route_reason,response_capability,lifecycle,version,created_at,expires_at) "
                "VALUES(%s,NULL,NULL,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s,'[]',FALSE,TRUE,%s,%s,%s,%s, 'manual_only',1,%s,%s)",
                (
                    question_id,
                    terminal_id,
                    incarnation_id,
                    turn_generation,
                    "native",
                    dialog_fingerprint,
                    dialog_version,
                    observation_id,
                    kind,
                    "A native provider dialog requires manual action in its terminal.",
                    "human" if owner_id else "unassigned",
                    owner_id,
                    reason_code,
                    "manual_only",
                    now,
                    expires_at,
                ),
            )
            connection.execute(
                "INSERT INTO interaction_question_versions(question_id,version,dialog_fingerprint,provider_evidence_version,"
                "observation_id,kind,human_required,prompt_content,options_json,allow_free_text,required,recipient_kind,"
                "recipient_id,route_reason,response_capability,created_at,expires_at) "
                "VALUES(%s,1,%s,%s,%s,%s,TRUE,%s,'[]',FALSE,TRUE,%s,%s,%s,%s,%s,%s)",
                (
                    question_id,
                    dialog_fingerprint,
                    dialog_version,
                    observation_id,
                    kind,
                    "A native provider dialog requires manual action in its terminal.",
                    "human" if owner_id else "unassigned",
                    owner_id,
                    reason_code,
                    "manual_only",
                    now,
                    expires_at,
                ),
            )
            self.repository.append_event(
                connection,
                event_type="interaction.native_manual_notice",
                source_terminal_id=terminal_id,
                source_incarnation_id=incarnation_id,
                actor_kind="system",
                actor_id=None,
                reason_code=reason_code,
                metadata={
                    "question_id": question_id,
                    "question_version": 1,
                    "source_kind": "native",
                    "response_capability": "manual_only",
                    "recipient_kind": "human" if owner_id else "unassigned",
                },
                question_id=question_id,
            )
            self.repository.append_event(
                connection,
                event_type="interaction.native_observed",
                source_terminal_id=terminal_id,
                source_incarnation_id=incarnation_id,
                actor_kind="system",
                actor_id=None,
                reason_code="native_target_bound",
                metadata={
                    "question_id": question_id,
                    "observation_id": observation_id,
                    "turn_generation": turn_generation,
                    "dialog_fingerprint": dialog_fingerprint,
                    "provider_evidence_version": dialog_version,
                    "target_fingerprint": self._native_target_fingerprint(
                        terminal_id,
                        incarnation_id,
                        str(source["session_name"]),
                        str(terminal["tmux_window"]),
                    ),
                },
                question_id=question_id,
            )
            return ObservationReceipt(
                observation_id=observation_id,
                question_id=question_id,
                lifecycle="manual_only",
                response_capability="manual_only",
                manual_reason="Native provider dialogs require manual action in the exact terminal.",
                turn_generation=turn_generation,
            )

    def reconcile_native_observation(
        self,
        terminal_id: str,
        incarnation_id: str,
        turn_generation: int,
        *,
        observation_id: str,
        dialog_fingerprint: str,
        provider_evidence_version: str,
        evidence_code: str = "provider_dialog_closed",
        allow_resume: bool = True,
    ) -> ObservationReceipt:
        """Close only the exact current manual observation on provider evidence."""
        terminal_id = _identifier(terminal_id, "terminal id")
        incarnation_id = _identifier(incarnation_id, "incarnation id")
        observation_id = _identifier(observation_id, "observation id")
        evidence_code = _identifier(evidence_code, "native closure evidence", maximum=64)
        if evidence_code != "provider_dialog_closed":
            raise InteractionInvalid(
                "native observation closure requires provider dialog-closed evidence"
            )
        if type(allow_resume) is not bool:
            raise InteractionInvalid("native closure resume policy is invalid")
        with self._transaction() as connection:
            source = connection.execute(
                "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s FOR UPDATE",
                (terminal_id, incarnation_id),
            ).fetchone()
            if source is None:
                raise InteractionNotFound("interaction source is not registered")
            # Closing an exact native observation is also a manual terminal
            # operation and remains available after capability install failure.
            self._validate_source_row(connection, source, require_capability=False)
            if source["turn_generation"] < turn_generation:
                raise InteractionStale("native closure evidence is from a future source turn")
            question = connection.execute(
                "SELECT * FROM interaction_questions WHERE terminal_id=%s AND incarnation_id=%s "
                "AND turn_generation=%s AND observation_id=%s AND source_kind='native' FOR UPDATE",
                (terminal_id, incarnation_id, turn_generation, observation_id),
            ).fetchone()
            if question is None:
                raise InteractionNotFound("native observation does not exist")
            self._current_version(question, question["version"])
            if (
                question["dialog_fingerprint"] != dialog_fingerprint
                or question["provider_evidence_version"] != provider_evidence_version
            ):
                raise InteractionStale(
                    "native closure evidence does not match the exact observed dialog"
                )
            terminal = self._terminal(connection, terminal_id)
            if (
                terminal is None
                or terminal["session_incarnation_id"] != incarnation_id
                or not isinstance(source["session_name"], str)
                or not isinstance(terminal["tmux_window"], str)
            ):
                raise InteractionStale("native closure target is no longer current")
            target_fingerprint = self._native_target_fingerprint(
                terminal_id,
                incarnation_id,
                source["session_name"],
                terminal["tmux_window"],
            )
            observed_event = connection.execute(
                "SELECT metadata_json FROM interaction_events WHERE question_id=%s "
                "AND event_type='interaction.native_observed' ORDER BY sequence DESC LIMIT 1",
                (question["question_id"],),
            ).fetchone()
            if observed_event is None:
                raise InteractionStale("native observation has no durable exact-target binding")
            try:
                observed_metadata = json.loads(observed_event["metadata_json"])
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise InteractionStale("native observation target binding is invalid") from error
            if not isinstance(observed_metadata, dict) or any(
                observed_metadata.get(field) != expected
                for field, expected in (
                    ("question_id", question["question_id"]),
                    ("observation_id", question["observation_id"]),
                    ("turn_generation", question["turn_generation"]),
                    ("dialog_fingerprint", question["dialog_fingerprint"]),
                    ("provider_evidence_version", question["provider_evidence_version"]),
                    ("target_fingerprint", target_fingerprint),
                )
            ):
                raise InteractionStale("native observation target no longer matches the terminal")
            self._expire_due(connection, _now())
            question = connection.execute(
                "SELECT * FROM interaction_questions WHERE question_id=%s",
                (question["question_id"],),
            ).fetchone()
            if question is None:
                raise InteractionNotFound("native observation does not exist")
            if self._native_closed_event(connection, question["question_id"]) is not None:
                return ObservationReceipt(
                    observation_id=observation_id,
                    question_id=question["question_id"],
                    lifecycle=question["lifecycle"],
                    response_capability="manual_only",
                    manual_reason="Native dialog was closed in the provider terminal.",
                    turn_generation=turn_generation,
                    replayed=True,
                )
            if question["lifecycle"] == "reconciled_external":
                return ObservationReceipt(
                    observation_id=observation_id,
                    question_id=question["question_id"],
                    lifecycle="reconciled_external",
                    response_capability="manual_only",
                    manual_reason="Native dialog was closed in the provider terminal.",
                    turn_generation=turn_generation,
                    replayed=True,
                )
            if self._native_question_has_newer_observation(connection, question):
                raise InteractionStale(
                    "a newer native observation supersedes this closure evidence"
                )

            current_generation = int(source["turn_generation"])
            manual_chain = self._has_websocket_advance_chain(
                connection,
                terminal_id,
                incarnation_id,
                turn_generation,
                current_generation,
            )
            source_advanced = (
                connection.execute(
                    "SELECT 1 FROM interaction_events WHERE question_id=%s "
                    "AND event_type='interaction.stale' AND reason_code='source_turn_advanced' LIMIT 1",
                    (question["question_id"],),
                ).fetchone()
                is not None
            )
            lifecycle = str(question["lifecycle"])

            if lifecycle in {"expired", "cancelled"}:
                # Expiry/cancellation ends the response lifecycle, not the
                # physical prompt. Persist closure evidence separately so a
                # future task can proceed without reviving or releasing this
                # already-terminal wait.
                event_type = "interaction.native_closed"
                outcome = "closed_after_terminal_lifecycle"
                returned_lifecycle = lifecycle
            elif not allow_resume and lifecycle in {"manual_only", "stale"}:
                # A monitor recovering after process restart may confirm that
                # the old dialog disappeared, but there is no surviving
                # invocation to resume. Clear only the physical-input gate and
                # preserve the original question lifecycle for auditability.
                event_type = "interaction.native_closed"
                outcome = "closed_after_monitor_recovery"
                returned_lifecycle = lifecycle
            elif (lifecycle == "manual_only" and current_generation == turn_generation) or (
                lifecycle == "stale"
                and current_generation > turn_generation
                and source_advanced
                and manual_chain
            ):
                connection.execute(
                    "UPDATE interaction_questions SET lifecycle='reconciled_external' "
                    "WHERE question_id=%s AND version=%s",
                    (question["question_id"], question["version"]),
                )
                event_type = "interaction.reconciled"
                outcome = "closed_outside_cao"
                returned_lifecycle = "reconciled_external"
            elif lifecycle == "stale" or (
                lifecycle == "manual_only" and current_generation > turn_generation
            ):
                # Exact closure may clear a stale physical-input gate, but an
                # unrelated/task turn must not be resumed by this receipt.
                event_type = "interaction.native_closed"
                outcome = "closed_without_task_resume"
                returned_lifecycle = lifecycle
            else:
                raise InteractionConflict("native observation is no longer open")

            self.repository.append_event(
                connection,
                event_type=event_type,
                source_terminal_id=terminal_id,
                source_incarnation_id=incarnation_id,
                actor_kind="system",
                actor_id=None,
                reason_code=evidence_code,
                metadata={
                    "question_id": question["question_id"],
                    "question_version": question["version"],
                    "outcome": outcome,
                    "source_kind": "native",
                    "turn_generation": turn_generation,
                },
                question_id=question["question_id"],
            )
            return ObservationReceipt(
                observation_id=observation_id,
                question_id=question["question_id"],
                lifecycle=returned_lifecycle,
                response_capability="manual_only",
                manual_reason="Native dialog was closed in the provider terminal.",
                turn_generation=turn_generation,
            )

    def capabilities(
        self,
        *,
        actor: object,
        provider: str | None = None,
        provider_version: str | None = None,
        execution_mode: str | None = None,
    ) -> tuple[ProviderCapability, ...]:
        """Report explicit-channel and manual native support for authorized sources."""
        for name, value in (
            ("provider", provider),
            ("provider version", provider_version),
            ("execution mode", execution_mode),
        ):
            if value is not None:
                _identifier(value, name, maximum=256)
        with self._read() as connection:
            sources: list[Record] = []
            if type(actor) is AgentActor and actor._seal is _AGENT_SEAL:
                source, _ = self._live_actor(connection, actor)
                sources = [source]
            else:
                principal = self.policy.require_principal(actor)
                sources = connection.execute(
                    "SELECT * FROM interaction_sources WHERE owner_principal_id=%s AND revoked_at IS NULL "
                    "ORDER BY provider,provider_version,execution_mode",
                    (principal.id,),
                ).fetchall()
            result: list[ProviderCapability] = []
            seen: set[tuple[str, str | None, str]] = set()
            for source in sources:
                if provider is not None and source["provider"] != provider:
                    continue
                if provider_version is not None and source["provider_version"] != provider_version:
                    continue
                if execution_mode is not None and source["execution_mode"] != execution_mode:
                    continue
                try:
                    self._validate_source_row(connection, source, require_capability=False)
                except InteractionError:
                    continue
                key = (source["provider"], source["provider_version"], source["execution_mode"])
                if key in seen:
                    continue
                seen.add(key)
                terminal = self._terminal(connection, source["terminal_id"])
                _, tools = canonical_tool_fingerprint(
                    terminal["allowed_tools"] if terminal else None
                )
                explicit = bool(source["capability_available"]) and _allows_cao_mcp(tools)
                result.append(
                    ProviderCapability(
                        provider=source["provider"],
                        provider_version=source["provider_version"],
                        execution_mode=source["execution_mode"],
                        emits_explicit=explicit,
                        detects_native=True,
                        responds_native=(),
                        manual_reason="Native dialogs have no verified version-specific response route; inspect and answer them in the provider terminal.",
                        required_mcp_tools=(REQUIRED_MCP_TOOL,) if explicit else (),
                    )
                )
            return tuple(result)


_DEFAULT_SERVICE: InteractionService | None = None
_DEFAULT_SERVICE_LOCK = threading.Lock()


def get_default_interaction_service() -> InteractionService:
    """Return the process singleton bound to the configured CAO database."""
    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        with _DEFAULT_SERVICE_LOCK:
            if _DEFAULT_SERVICE is None:
                target = DatabaseTarget.from_settings(home=constants.CAO_HOME_DIR)
                _DEFAULT_SERVICE = InteractionService(InteractionRepository(target))
    return _DEFAULT_SERVICE
