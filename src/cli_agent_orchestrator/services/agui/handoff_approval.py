"""Human-in-the-loop approval stack for agent handoff and permission prompts.

Components:
- ``classify_reason``: total, deterministic classifier that maps provider + raw
  prompt text to a structured ``namespace:local_name`` reason string.
- ``ApprovalDecision``: enum of possible user decisions.
- ``Interrupt``: frozen record of a pending (or resolved) approval request.
- ``AgentHandoffWithApproval``: L2 construct managing the full interrupt lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol

from cli_agent_orchestrator.services.agui.base import AguiConstruct, RecordingUiEmitter, UiEmitter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider pattern imports (re-use existing detection patterns)
# ---------------------------------------------------------------------------

from cli_agent_orchestrator.providers.claude_code import (
    TRUST_PROMPT_PATTERN as CLAUDE_TRUST_PATTERN,
)
from cli_agent_orchestrator.providers.claude_code import (
    WAITING_USER_ANSWER_PATTERN as CLAUDE_WAITING_PATTERN,
)
from cli_agent_orchestrator.providers.codex import TRUST_PROMPT_PATTERN as CODEX_TRUST_PATTERN
from cli_agent_orchestrator.providers.codex import WAITING_PROMPT_PATTERN as CODEX_WAITING_PATTERN
from cli_agent_orchestrator.providers.kiro_cli import TUI_PERMISSION_PATTERN as KIRO_TUI_PATTERN

# Legacy kiro permission pattern (instantiated on the provider instance normally,
# but we duplicate the static regex here for the classifier).
KIRO_LEGACY_PERMISSION_PATTERN = r"Allow this action\?.*?\[.*?y.*?/.*?n.*?/.*?t.*?\]:"


# ---------------------------------------------------------------------------
# classify_reason: total, deterministic, NEVER raises
# ---------------------------------------------------------------------------

# Namespace map: provider name -> namespace segment.
_NAMESPACE_MAP: Dict[str, str] = {
    "kiro_cli": "kiro",
    "claude_code": "claude-code",
    "codex": "codex",
}


def _to_kebab(name: str) -> str:
    """Convert a provider name to kebab-case namespace (lowercase, hyphens)."""
    # Replace underscores with hyphens, strip non-alphanumeric/hyphen chars.
    result = re.sub(r"[^a-z0-9-]", "-", name.lower().replace("_", "-"))
    # Collapse multiple hyphens.
    result = re.sub(r"-+", "-", result).strip("-")
    return result or "unknown"


def classify_reason(provider: str, raw_prompt: str) -> str:
    """Classify a provider waiting prompt into a structured reason string.

    Returns ``namespace:local_name`` where:
    - namespace matches ``^[a-z0-9-]+$``
    - local_name matches ``^[a-z0-9_]+$``
    - NEVER returns ``core:*`` (reserved by ag-ui)

    This function is total and deterministic: it never raises for any input.
    """
    try:
        # Determine namespace
        namespace = _NAMESPACE_MAP.get(provider, _to_kebab(provider))
        # Safety: never produce "core" namespace
        if namespace == "core":
            namespace = "provider-core"

        # Per-provider classification
        local_name = _classify_local(provider, raw_prompt)
        return f"{namespace}:{local_name}"
    except Exception:  # pragma: no cover - total-function safety net
        # Total: absorb any unexpected error
        namespace = _NAMESPACE_MAP.get(provider, _to_kebab(provider)) if provider else "unknown"
        if namespace == "core":
            namespace = "provider-core"
        return f"{namespace}:unknown_prompt"


def _classify_local(provider: str, raw_prompt: str) -> str:
    """Determine the local_name for a given provider and prompt text."""
    if provider == "claude_code":
        # Trust prompt takes priority (it also matches WAITING pattern sometimes)
        if re.search(CLAUDE_TRUST_PATTERN, raw_prompt):
            return "trust_prompt"
        if re.search(CLAUDE_WAITING_PATTERN, raw_prompt):
            return "permission_request"
        return "unknown_prompt"

    elif provider == "kiro_cli":
        # TUI permission pattern (check specific patterns before the generic "trust" word)
        if re.search(KIRO_TUI_PATTERN, raw_prompt):
            return "permission_request"
        # Legacy permission pattern
        if re.search(KIRO_LEGACY_PERMISSION_PATTERN, raw_prompt):
            return "permission_request"
        # Trust-related wording (generic, checked last)
        if re.search(r"trust", raw_prompt, re.IGNORECASE):
            return "trust_prompt"
        return "unknown_prompt"

    elif provider == "codex":
        # Trust prompt
        if re.search(CODEX_TRUST_PATTERN, raw_prompt):
            return "trust_prompt"
        # Approval request
        if re.search(CODEX_WAITING_PATTERN, raw_prompt, re.MULTILINE):
            return "approval_request"
        return "unknown_prompt"

    else:
        return "unknown_prompt"


# ---------------------------------------------------------------------------
# ApprovalDecision enum
# ---------------------------------------------------------------------------


class ApprovalDecision(str, Enum):
    """Possible decisions a user can make on an approval interrupt."""

    APPROVE = "approve"
    DENY = "deny"
    EDIT = "edit"


class DeliveryError(RuntimeError):
    """Raised when delivering a resolved decision to the terminal fails.

    The interrupt is left UNRESOLVED (retryable): the caller should surface a
    non-success result so a later resume can re-attempt delivery rather than
    stranding the terminal on a silent failure.
    """


class DurableApprovalConflict(ValueError):
    """A bound approval no longer names its immutable durable target."""


class DeliveryUncertain(DeliveryError):
    """A claimed bound effect cannot safely be delivered a second time."""


# Per-terminal delivery lock with reference counting. Incremented before any
# await (loop-atomic with get/create) so queued waiters keep the entry alive;
# popped only at zero refs (no holder AND no waiters); decrement→pop has no
# await between them so nothing can interleave.
class _RefCountedLock:
    __slots__ = ("lock", "refs")

    def __init__(self) -> None:
        self.lock: asyncio.Lock = asyncio.Lock()
        self.refs: int = 0


@dataclass(frozen=True)
class _DurableApprovalBinding:
    """Private server-registered target for one process-local interrupt.

    This record is never derived from terminal metadata, UI props, edited text,
    or a resume payload.  Registration is an internal server integration seam,
    not a transport endpoint.
    """

    interrupt_id: str
    terminal_id: str
    provider: str
    work_item_id: str
    attempt_id: str
    generation: int
    job_id: str
    grant_id: str
    grant_revision: int
    contract_hash: str
    evidence_refs: tuple[str, ...]
    idempotency_key: str
    effect: str


# Deliveries run to completion (no hard timeout): asyncio.wait_for would cancel
# only the awaiter, leaving the to_thread worker alive to paste LATE — after a
# retry has already delivered a different decision (a record/side-effect skew).
# We instead measure duration and warn if a delivery is pathologically slow,
# keeping the diagnostic signal without orphaning the worker.
_DELIVERY_SLOW_WARN_SECONDS = 15.0


# ---------------------------------------------------------------------------
# Interrupt dataclass
# ---------------------------------------------------------------------------


@dataclass
class Interrupt:
    """Record of a pending or resolved approval request."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    reason: str = ""
    message: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    options: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: Optional[str] = None
    resolved: bool = False
    outcome: Optional[str] = None


# ---------------------------------------------------------------------------
# AnswerDelivery protocol (for dependency injection in tests)
# ---------------------------------------------------------------------------


class AnswerDelivery(Protocol):
    """Protocol for delivering an answer to a terminal."""

    def send_input(
        self, terminal_id: str, text: str, **kwargs: Any
    ) -> bool | None: ...  # pragma: no cover

    def send_special_key(self, terminal_id: str, key: str) -> bool: ...  # pragma: no cover


class TerminalServiceAnswerDelivery:
    """Production ``AnswerDelivery`` backed by ``terminal_service``.

    Delivers a resolved approval decision to the waiting CLI by driving the same
    tmux input path the rest of CAO uses. ``terminal_service`` is imported lazily
    (mirroring ``approval_bridge``) so the metadata-only ``services/agui`` package
    keeps its import graph free of the heavy terminal layer at module load.

    Methods are blocking and are invoked from a **worker thread** (via
    ``asyncio.to_thread`` inside the shielded ``_deliver_and_commit`` task).
    They must stay thread-safe and must not assume an event loop is running in
    the current thread. The per-terminal lock that serializes deliveries is held
    by the async construct above — these methods just perform the I/O.
    """

    def send_input(self, terminal_id: str, text: str, **kwargs: Any) -> None:
        from cli_agent_orchestrator.services import terminal_service

        # Clear any partially-entered input before pasting so a retry REPLACES
        # rather than APPENDS. Idempotent on an empty prompt line; corrective if
        # a prior attempt pasted text but failed before Enter. Best-effort: a
        # failed clear must not fail the delivery itself.
        try:
            terminal_service.send_special_key(terminal_id, "C-u")
        except Exception:
            logger.debug("line-clear before paste failed for terminal %s", terminal_id)
        terminal_service.send_input(terminal_id, text)

    def send_special_key(self, terminal_id: str, key: str) -> bool:
        from cli_agent_orchestrator.services import terminal_service

        return terminal_service.send_special_key(terminal_id, key)


# ---------------------------------------------------------------------------
# Per-provider answer translation
# ---------------------------------------------------------------------------

# ANSI/VT escape sequences: CSI (ESC[ ... final), OSC (ESC] ... BEL/ST), and
# lone two-char escapes (ESC + Fe). C0/C1 control bytes (incl. NUL) are stripped
# separately, preserving only tab/newline/carriage-return.
_ANSI_ESCAPE_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"  # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL or ST
    r"|\x1b[@-Z\\-_]"  # two-char escapes
)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def _sanitize_edited_text(text: str) -> str:
    """Strip ANSI/VT escape sequences and control bytes (incl. NUL) from
    operator-supplied edit text before it is written to a terminal via
    ``send_input``, and collapse it to a SINGLE line.

    An edited answer is a single line. Now that delivery is wired to a live PTY,
    a bare CR/LF in the edit text would submit (Enter) mid-answer and could
    inject a follow-on command line (e.g. ``"y\\rrm -rf ~"``). So carriage
    returns are removed and the text is truncated at the first newline, in
    addition to stripping ANSI/VT sequences and C0/C1 control bytes. Horizontal
    tab is preserved. Any bare ESC left after escape-sequence removal is caught
    by the control-byte pass (0x1b is within \\x0e-\\x1f). (P1-4 + WS-2.)
    """
    without_ansi = _ANSI_ESCAPE_RE.sub("", text)
    stripped = _CONTROL_CHARS_RE.sub("", without_ansi)
    # Single-line only: drop CR, then keep everything before the first LF so an
    # edited answer can neither submit itself nor smuggle a second line.
    return stripped.replace("\r", "").split("\n", 1)[0]


def _translate_decision(
    provider: str,
    decision: ApprovalDecision,
    edited_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Translate a user decision into provider-specific terminal input.

    Returns a dict with one of:
    - {"type": "text", "value": str} for text input
    - {"type": "key", "value": str} for one special key
    - {"type": "keys", "value": list[str]} for an ordered key sequence
    Existing providers retain their single-action behavior.
    """
    if decision == ApprovalDecision.EDIT:
        # Edit always sends the edited text, sanitized against terminal escape
        # injection (ANSI/VT sequences + control bytes) before send_input.
        sanitized = _sanitize_edited_text(edited_text or "")
        if not sanitized.strip():
            raise ValueError(
                "Edit text is empty after sanitization (control characters / "
                "leading newlines stripped). Provide visible text content."
            )
        return {"type": "text", "value": sanitized}

    if provider == "claude_code":
        if decision == ApprovalDecision.APPROVE:
            return {"type": "key", "value": "Enter"}
        else:  # deny
            return {"type": "key", "value": "Escape"}

    elif provider == "omp":
        # OMP 17.2.10's selector starts with Approve selected. Enter submits
        # approve; Down then Enter selects and submits Deny.
        if decision == ApprovalDecision.APPROVE:
            return {"type": "key", "value": "Enter"}
        else:  # deny
            return {"type": "keys", "value": ["Down", "Enter"]}

    elif provider == "kiro_cli":
        if decision == ApprovalDecision.APPROVE:
            return {"type": "text", "value": "y"}
        else:  # deny
            return {"type": "text", "value": "n"}

    elif provider == "codex":
        if decision == ApprovalDecision.APPROVE:
            return {"type": "text", "value": "y"}
        else:  # deny
            return {"type": "text", "value": "n"}

    else:
        # Generic fallback
        if decision == ApprovalDecision.APPROVE:
            return {"type": "text", "value": "y"}
        else:
            return {"type": "text", "value": "n"}


# Default options per reason category
_DEFAULT_OPTIONS: Dict[str, List[str]] = {
    "permission_request": ["approve", "deny", "edit"],
    "trust_prompt": ["approve", "deny"],
    "approval_request": ["approve", "deny", "edit"],
    "unknown_prompt": ["approve", "deny"],
}


def _options_for_reason(reason: str) -> List[str]:
    """Determine available options based on the classified reason."""
    # Extract local_name from "namespace:local_name"
    parts = reason.split(":", 1)
    local_name = parts[1] if len(parts) == 2 else reason
    return _DEFAULT_OPTIONS.get(local_name, ["approve", "deny"])


# ---------------------------------------------------------------------------
# Registry bounds
# ---------------------------------------------------------------------------

_REGISTRY_CAP = 1000
_RESOLVED_TTL_SECONDS = 300.0


# ---------------------------------------------------------------------------
# AgentHandoffWithApproval construct
# ---------------------------------------------------------------------------


class AgentHandoffWithApproval(AguiConstruct):
    """L2 construct managing the full human-in-the-loop approval lifecycle.

    Features:
    - Creates Interrupt records when a provider enters WAITING_USER_ANSWER.
    - Resolves interrupts exactly once (lock-guarded).
    - Translates decisions to per-provider terminal input.
    - Expires interrupts with zero keystrokes on status transitions.
    - Bounded registry with TTL eviction for resolved entries.

    Concurrency invariant (single event loop):
        ``resume()`` holds an ``asyncio.Lock`` only for the check-then-register
        step (idempotency check, decision validation, and creating the single
        authoritative per-interrupt delivery task). Actual delivery + state
        commit run in ``_deliver_and_commit`` as a SHIELDED task OUTSIDE the
        lock, so (a) a stuck backend delivery cannot block approvals for other
        interrupts (P2), and (b) a cancelled awaiter cannot abort the
        delivery+commit and let a retry deliver a contrary decision (P1) —
        concurrent resumes of the same interrupt join the one in-flight task.
        The sync methods ``on_provider_waiting()`` and ``expire()`` mutate the
        same shared registries WITHOUT the lock; this is safe ONLY when every
        caller runs on the one asyncio event loop (the CAO server model),
        because control yields only at ``await`` points. Do NOT call these
        methods from a separate OS thread or a second event loop.
    """

    def __init__(
        self,
        emitter: UiEmitter,
        answer_delivery: Optional[AnswerDelivery] = None,
        decisions: Optional[Any] = None,
    ) -> None:
        super().__init__(emitter)
        self._answer_delivery = answer_delivery
        self._decisions = decisions
        self._interrupts: Dict[str, Interrupt] = {}
        # Map terminal_id -> interrupt_id for quick lookup of open interrupts
        self._terminal_to_interrupt: Dict[str, str] = {}
        self._lock = asyncio.Lock()
        # Track resolution timestamps for TTL eviction
        self._resolved_at: Dict[str, float] = {}
        # Authoritative per-interrupt delivery+commit task. A concurrent resume
        # JOINS the in-flight task instead of starting a second delivery, and
        # the task is SHIELDED from awaiter cancellation, so an aborted
        # /agui/v1/run stream cannot let a retry deliver a contrary decision (P1).
        self._inflight: Dict[str, asyncio.Task] = {}
        # Number of keys in a multi-key action that have been delivered. A
        # retry after a partial delivery resumes at the first unsent key rather
        # than replaying a selector movement against the live terminal state.
        self._delivery_progress: Dict[str, int] = {}
        # Original decision for a partially delivered multi-key action. A
        # contrary retry must continue the original terminal selection rather
        # than applying a new decision to the already-moved selector.
        self._delivery_decisions: Dict[str, ApprovalDecision] = {}
        # Per-terminal delivery locks. A delivery worker OUTLIVES its interrupt's
        # registry state (a status flap can expire interrupt A and open a new
        # interrupt B on the SAME terminal while A's worker is still pasting), and
        # _inflight is keyed by interrupt id — so per-interrupt join alone does
        # NOT serialize two workers that target the same terminal. This lock
        # (acquired INSIDE the shielded delivery task, so awaiter cancellation
        # cannot skip it) serializes physical delivery per terminal without the
        # construct-wide blast radius. Bounded by the count of distinct terminals
        # that ever received an approval delivery.
        self._delivery_locks: Dict[str, _RefCountedLock] = {}
        self._durable_bindings: Dict[str, _DurableApprovalBinding] = {}
        # Validate this before legacy resolved/in-flight shortcuts, otherwise a
        # contrary bound request could silently join the first request.
        self._durable_requests: Dict[str, tuple[str, str]] = {}
        self._durable_uncertain: set[str] = set()

    def handle_frame(
        self, agui_type: str, data: Dict[str, Any], event_id: Optional[str] = None
    ) -> None:
        """Not used for event-driven processing; this construct is API-driven."""
        pass

    def projection(self) -> Dict[str, Any]:
        """Return current state as JSON-serializable dict."""
        return {
            "pending": [_interrupt_to_dict(i) for i in self._interrupts.values() if not i.resolved],
            "total": len(self._interrupts),
        }

    def on_provider_waiting(
        self,
        terminal_id: str,
        provider: str,
        raw_prompt: str,
        session_name: Optional[str] = None,
    ) -> Interrupt:
        """Create an Interrupt when a provider enters WAITING_USER_ANSWER.

        Classifies the reason, builds the interrupt record, emits an
        approval_card UI intent, and returns the interrupt.
        """
        reason = classify_reason(provider, raw_prompt)
        # Metadata-only contract (docs/agui.md, "privacy tests"): the raw prompt
        # is terminal-stdout BODY and must never reach the AG-UI wire. The
        # classified category is carried by `reason`; the human-readable body is
        # dropped here so it is absent from the approval_card props, the
        # Interrupt record, and any run-plane snapshot. (WS-2 privacy.)
        message = ""

        options = _options_for_reason(reason)

        interrupt = Interrupt(
            reason=reason,
            message=message,
            metadata={
                "provider": provider,
                "terminal_id": terminal_id,
                "session_name": session_name,
                "source_event_id": None,
            },
            options=options,
        )

        self._interrupts[interrupt.id] = interrupt
        self._terminal_to_interrupt[terminal_id] = interrupt.id

        # Evict if over cap
        self._evict_if_needed()

        # Emit approval_card UI intent
        try:
            self.emit(
                "approval_card",
                {
                    "interrupt_id": interrupt.id,
                    "reason": reason,
                    "message": message,
                    "options": options,
                    "provider": provider,
                    "terminal_id": terminal_id,
                },
                terminal_id=terminal_id,
                session_name=session_name,
            )
        except (ValueError, RuntimeError):
            # Emit failure should not block interrupt creation
            logger.debug("Failed to emit approval_card for interrupt %s", interrupt.id)

        return interrupt

    @staticmethod
    def _binding_text(value: Any, label: str, maximum: int = 512) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > maximum
        ):
            raise DurableApprovalConflict(f"{label} must be a bounded nonempty string")
        return value

    @classmethod
    def _binding_tokens(cls, value: Any) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)) or not value or len(value) > 128:
            raise DurableApprovalConflict("evidence_refs must be a bounded nonempty sequence")
        tokens = tuple(cls._binding_text(item, "evidence_refs", 4096) for item in value)
        if len(set(tokens)) != len(tokens):
            raise DurableApprovalConflict("evidence_refs cannot contain duplicates")
        return tokens

    def register_durable_binding(
        self,
        *,
        interrupt_id: str,
        work_item_id: str,
        attempt_id: str,
        generation: int,
        job_id: str,
        grant_id: str,
        grant_revision: int,
        contract_hash: str,
        evidence_refs: tuple[str, ...] | list[str],
        idempotency_key: str,
        effect: str,
    ) -> None:
        """Pin a trusted upstream work order to a pending local interrupt.

        This has no HTTP/MCP entry point.  The upstream caller is responsible for
        selecting the key/effect; this method proves all remaining work
        coordinates against WorkContracts before retaining the frozen mapping.
        """
        if self._decisions is None:
            raise DurableApprovalConflict("durable approval integration is unavailable")
        interrupt = self._interrupts.get(interrupt_id)
        if interrupt is None or interrupt.resolved:
            raise DurableApprovalConflict("only a pending server interrupt can be bound")
        if interrupt_id in self._durable_bindings:
            raise DurableApprovalConflict("an interrupt cannot be rebound")
        if type(generation) is not int or not 0 < generation <= 2**63 - 1:
            raise DurableApprovalConflict("generation must be a positive bounded integer")
        if type(grant_revision) is not int or not 0 < grant_revision <= 2**63 - 1:
            raise DurableApprovalConflict("grant_revision must be a positive bounded integer")

        candidate = _DurableApprovalBinding(
            interrupt_id=self._binding_text(interrupt_id, "interrupt_id"),
            terminal_id=self._binding_text(interrupt.metadata.get("terminal_id"), "terminal_id"),
            provider=self._binding_text(interrupt.metadata.get("provider"), "provider"),
            work_item_id=self._binding_text(work_item_id, "work_item_id"),
            attempt_id=self._binding_text(attempt_id, "attempt_id"),
            generation=generation,
            job_id=self._binding_text(job_id, "job_id"),
            grant_id=self._binding_text(grant_id, "grant_id"),
            grant_revision=grant_revision,
            contract_hash=self._binding_text(contract_hash, "contract_hash", 4096),
            evidence_refs=self._binding_tokens(evidence_refs),
            idempotency_key=self._binding_text(idempotency_key, "idempotency_key"),
            effect=self._binding_text(effect, "effect", 4096),
        )
        try:
            from cli_agent_orchestrator.services.work_contract import WorkContracts

            current = WorkContracts(self._decisions.repository).revalidate_order(
                candidate.attempt_id, generation=candidate.generation
            )
        except Exception as error:
            raise DurableApprovalConflict("durable work binding cannot be proven") from error
        if (
            current.work_item_id,
            current.job_id,
            current.grant_id,
            current.grant_revision,
            current.contract_hash,
        ) != (
            candidate.work_item_id,
            candidate.job_id,
            candidate.grant_id,
            candidate.grant_revision,
            candidate.contract_hash,
        ):
            raise DurableApprovalConflict("durable work binding does not match registered target")
        self._durable_bindings[interrupt_id] = candidate

    def has_durable_binding(self, interrupt_id: str) -> bool:
        """Whether an API resume must resolve a verified principal first."""
        return interrupt_id in self._durable_bindings

    def _bound_interrupt(self, interrupt: Interrupt, binding: _DurableApprovalBinding) -> None:
        # A matching request is permitted to read the retained process-local
        # result after success/expiry.  _resume_bound compares its principal and
        # decision fingerprint immediately afterwards, before returning it.
        if interrupt.resolved:
            return
        if (
            interrupt.id != binding.interrupt_id
            or interrupt.metadata.get("terminal_id") != binding.terminal_id
            or interrupt.metadata.get("provider") != binding.provider
            or self._terminal_to_interrupt.get(binding.terminal_id) != interrupt.id
        ):
            raise DurableApprovalConflict("bound approval target changed before delivery")

    async def resume(
        self,
        interrupt_id: str,
        decision: ApprovalDecision,
        edited_text: Optional[str] = None,
        principal: Any = None,
    ) -> Interrupt:
        """Resolve an interrupt with the user's decision.

        Lock-guarded for exactly-once resolution. Returns the interrupt
        (with outcome set). If already resolved, returns the recorded outcome
        with no side effects (idempotent).

        Delivery ordering (P1): the decision is delivered to the terminal
        BEFORE the interrupt is committed as resolved. Delivery runs off the
        event loop via ``asyncio.to_thread`` (P2) so blocking backend I/O never
        stalls the loop. On delivery failure the interrupt is left UNRESOLVED
        and ``DeliveryError`` is raised, so the caller reports a non-success
        result and a later resume can re-attempt (retryable policy).

        Raises:
            KeyError: if interrupt_id is unknown
            ValueError: if decision is invalid for this interrupt
            DeliveryError: if delivering the decision to the terminal failed
        """
        binding = self._durable_bindings.get(interrupt_id)
        if binding is not None:
            return await self._resume_bound(interrupt_id, decision, edited_text, principal, binding)
        async with self._lock:
            interrupt = self._interrupts.get(interrupt_id)
            if interrupt is None:
                raise KeyError(f"Unknown interrupt: {interrupt_id}")

            # Idempotent: already resolved -> return recorded outcome.
            if interrupt.resolved:
                return interrupt

            # If an authoritative delivery+commit is already in flight for THIS
            # interrupt, JOIN it rather than starting a second one (P1): a retry
            # (possibly with a contrary decision) must never overtake a delivery
            # already under way. The first decision to reach here wins.
            task = self._inflight.get(interrupt_id)
            if task is None:
                # Once a multi-key action has physically delivered a prefix,
                # retries must remain pinned to that original decision. The
                # presence of a pinned decision proves that at least one key
                # was delivered; with zero delivered keys, callers may retry
                # with a different valid decision.
                pinned_decision = self._delivery_decisions.get(interrupt_id)
                if pinned_decision is not None:
                    decision = pinned_decision

                # Validate the effective decision under the lock so an invalid
                # decision is rejected without starting any delivery.
                if decision.value not in interrupt.options:
                    raise ValueError(
                        f"Decision '{decision.value}' not supported for this interrupt. "
                        f"Allowed: {interrupt.options}"
                    )
                if decision == ApprovalDecision.EDIT:
                    if not edited_text or not edited_text.strip():
                        raise ValueError("Edit decision requires non-empty edited_text")
                    if len(edited_text) > 4000:
                        raise ValueError(
                            f"edited_text too long ({len(edited_text)} chars, max 4000)"
                        )

                terminal_id = interrupt.metadata.get("terminal_id")
                provider = interrupt.metadata.get("provider", "")
                action = _translate_decision(provider, decision, edited_text)

                task = asyncio.ensure_future(
                    self._deliver_and_commit(interrupt, decision, terminal_id, provider, action)
                )
                self._inflight[interrupt_id] = task

                def _cleanup(_t: "asyncio.Future[Interrupt]", _iid: str = interrupt_id) -> None:
                    # Remove from the in-flight registry only AFTER delivery +
                    # state reconciliation finish (success OR DeliveryError), so a
                    # retry after a genuine failure can re-attempt.
                    self._inflight.pop(_iid, None)
                    # Retrieve any exception so that, if every awaiter was
                    # cancelled and no retry ever joins, asyncio does not log a
                    # spurious "Task exception was never retrieved".
                    if not _t.cancelled():
                        _t.exception()

                task.add_done_callback(_cleanup)

        # Await the authoritative task OUTSIDE the construct lock, SHIELDED from
        # our own cancellation. Holding the lock only for check+register (never
        # across the unbounded backend delivery) bounds a stuck delivery's blast
        # radius to its own interrupt (P2). Shielding means a cancelled awaiter
        # (e.g. an aborted /agui/v1/run stream) cannot abort the delivery+commit
        # and let a retry deliver a contrary decision (P1); the task runs to
        # completion regardless of the awaiter's fate.
        return await asyncio.shield(task)

    async def _resume_bound(
        self,
        interrupt_id: str,
        decision: ApprovalDecision,
        edited_text: Optional[str],
        principal: Any,
        binding: _DurableApprovalBinding,
    ) -> Interrupt:
        """Validate a durable request before every legacy fast path."""
        if principal is None or not isinstance(getattr(principal, "id", None), str):
            raise DurableApprovalConflict("verified principal required for bound approval")
        if decision == ApprovalDecision.EDIT:
            # T053 has no frozen target-payload contract for arbitrary edit text.
            raise DurableApprovalConflict("bound edited approvals are unsupported")
        fingerprint = (principal.id, decision.value)
        async with self._lock:
            interrupt = self._interrupts.get(interrupt_id)
            if interrupt is None:
                raise KeyError(f"Unknown interrupt: {interrupt_id}")
            self._bound_interrupt(interrupt, binding)
            if decision.value not in interrupt.options:
                raise ValueError(
                    f"Decision '{decision.value}' not supported for this interrupt. "
                    f"Allowed: {interrupt.options}"
                )
            prior = self._durable_requests.get(interrupt_id)
            if prior is not None and prior != fingerprint:
                raise DurableApprovalConflict("bound approval request conflicts with first request")
            self._durable_requests.setdefault(interrupt_id, fingerprint)
            if interrupt_id in self._durable_uncertain:
                raise DeliveryUncertain("bound effect was claimed; terminal delivery is uncertain")
            if interrupt.resolved:
                return interrupt
            task = self._inflight.get(interrupt_id)
            if task is None:
                action = _translate_decision(binding.provider, decision)
                task = asyncio.ensure_future(
                    self._deliver_bound_and_commit(interrupt, binding, decision, principal, action)
                )
                self._inflight[interrupt_id] = task

                def _cleanup(_task: "asyncio.Future[Interrupt]", _iid: str = interrupt_id) -> None:
                    self._inflight.pop(_iid, None)
                    if not _task.cancelled():
                        _task.exception()

                task.add_done_callback(_cleanup)
        return await asyncio.shield(task)

    async def _bound_delivery(
        self, binding: _DurableApprovalBinding, action: Dict[str, Any]
    ) -> None:
        """Run terminal I/O only after decision/claim transactions have committed."""
        if self._answer_delivery is None:
            raise DeliveryUncertain("bound terminal delivery target is unavailable")
        if action["type"] == "text":
            result = await asyncio.to_thread(
                self._answer_delivery.send_input, binding.terminal_id, action["value"]
            )
            if result is False:
                raise DeliveryUncertain("terminal rejected bound text delivery")
            return
        keys = [action["value"]] if action["type"] == "key" else list(action["value"])
        for key in keys:
            result = await asyncio.to_thread(
                self._answer_delivery.send_special_key, binding.terminal_id, key
            )
            if result is False:
                raise DeliveryUncertain("terminal rejected bound key delivery")

    async def _deliver_bound_and_commit(
        self,
        interrupt: Interrupt,
        binding: _DurableApprovalBinding,
        decision: ApprovalDecision,
        principal: Any,
        action: Dict[str, Any],
    ) -> Interrupt:
        """Claim is the linearization point; post-claim delivery is never retried."""
        assert self._decisions is not None  # Durable registration requires this port.
        entry = self._delivery_locks.get(binding.terminal_id)
        if entry is None:
            entry = self._delivery_locks[binding.terminal_id] = _RefCountedLock()
        entry.refs += 1
        try:
            async with entry.lock:
                self._bound_interrupt(interrupt, binding)
                if interrupt.resolved:
                    return interrupt
                try:
                    durable_decision = self._decisions.decide_work(
                        principal=principal,
                        work_item_id=binding.work_item_id,
                        attempt_id=binding.attempt_id,
                        generation=binding.generation,
                        idempotency_key=binding.idempotency_key,
                        evidence_refs=binding.evidence_refs,
                        action=decision.value,
                        reason=f"agui approval {binding.interrupt_id}: {decision.value}",
                        authorized_effects=(binding.effect,),
                    )
                    claimed = self._decisions.consume_effect(
                        principal=principal,
                        decision_id=durable_decision.id,
                        attempt_id=binding.attempt_id,
                        generation=binding.generation,
                        effect=binding.effect,
                    )
                except Exception as error:
                    # WorkDecisions owns both SQLite transactions and rechecks the
                    # current binding/grant during consume_effect.  No transaction
                    # can survive to the terminal call below.
                    raise DurableApprovalConflict(
                        "bound durable effect could not be claimed"
                    ) from error
                if not claimed:
                    self._durable_uncertain.add(interrupt.id)
                    raise DeliveryUncertain("bound effect was already claimed; delivery is unknown")
                try:
                    await self._bound_delivery(binding, action)
                except asyncio.CancelledError:
                    # Preserve cancellation semantics.  The consumed claim still
                    # fences a later replay, which will report uncertainty.
                    self._durable_uncertain.add(interrupt.id)
                    raise
                except DeliveryUncertain:
                    self._durable_uncertain.add(interrupt.id)
                    raise
                except Exception as error:
                    # A false acknowledgement, partial multi-key delivery, or a
                    # backend exception after claim is non-retryable: an external
                    # terminal may already have acted even though this caller did
                    # not receive confirmation.
                    self._durable_uncertain.add(interrupt.id)
                    raise DeliveryUncertain("bound terminal delivery is uncertain") from error

                interrupt.resolved = True
                interrupt.outcome = decision.value
                self._resolved_at[interrupt.id] = time.monotonic()
                if self._terminal_to_interrupt.get(binding.terminal_id) == interrupt.id:
                    del self._terminal_to_interrupt[binding.terminal_id]
                try:
                    self.emit(
                        "approval_card",
                        {
                            "interrupt_id": interrupt.id,
                            "resolved": True,
                            "outcome": decision.value,
                            "provider": binding.provider,
                            "terminal_id": binding.terminal_id,
                        },
                        terminal_id=binding.terminal_id,
                        session_name=interrupt.metadata.get("session_name"),
                    )
                except (ValueError, RuntimeError):
                    logger.debug("Failed to emit durable resolution for interrupt %s", interrupt.id)
                return interrupt
        finally:
            entry.refs -= 1
            if entry.refs == 0:
                self._delivery_locks.pop(binding.terminal_id, None)

    async def _deliver_and_commit(
        self,
        interrupt: Interrupt,
        decision: ApprovalDecision,
        terminal_id: Optional[str],
        provider: str,
        action: Dict[str, Any],
    ) -> Interrupt:
        """Authoritative delivery + state commit for a single interrupt.

        Runs as a shielded task (see ``resume``). Delivers the decision to the
        terminal off the event loop via ``asyncio.to_thread`` (blocking tmux /
        Herdr I/O must never stall the loop), then commits resolution. On
        delivery failure the interrupt is left UNRESOLVED and ``DeliveryError``
        is raised (retryable) — unless a concurrent ``expire()`` already
        resolved it mid-flight, in which case expiry wins (nothing was
        delivered) and the failure is not advertised as retryable.
        """
        interrupt_id = interrupt.id
        if self._answer_delivery and terminal_id:
            # Serialize physical delivery PER TERMINAL: two workers must never
            # interleave keystrokes on the same terminal, even across different
            # interrupts (a status flap can expire this interrupt and open a new
            # one on the same terminal while this worker is still pasting). The
            # lock lives inside this shielded task, so awaiter cancellation cannot
            # skip it; per-terminal (not construct-wide) keeps P2 blast radius
            # bounded.
            #
            # Ref-counted: refs increments before any await (loop-atomic with
            # get/create) so a queued waiter always keeps the entry alive; pop
            # happens only at zero (no holder AND no waiters); decrement→pop has
            # no await between them so nothing can interleave; finally covers
            # success, DeliveryError, and both expiry returns.
            _entry = self._delivery_locks.get(terminal_id)
            if _entry is None:
                _entry = self._delivery_locks[terminal_id] = _RefCountedLock()
            _entry.refs += 1
            try:
                async with _entry.lock:
                    # Expiry-before-send: this interrupt may have expired while its
                    # delivery was QUEUED behind a stuck sibling delivery on the same
                    # terminal. Nothing has been sent for it yet, so expiry wins with
                    # ZERO keystrokes (delivery-beats-expire below applies only once
                    # keystrokes are actually in flight).
                    if interrupt.resolved:
                        return interrupt
                    _delivery_start = time.monotonic()
                    # In-flight watchdog: emit a WARNING while the worker is still
                    # running if it exceeds the threshold. Never cancels the worker.
                    _loop = asyncio.get_running_loop()
                    _watchdog_handle = _loop.call_later(
                        _DELIVERY_SLOW_WARN_SECONDS,
                        lambda: logger.warning(
                            "Approval delivery in-flight for interrupt %s on terminal %s "
                            "has exceeded %.1fs (still running)",
                            interrupt_id,
                            terminal_id,
                            _DELIVERY_SLOW_WARN_SECONDS,
                        ),
                    )
                    try:
                        if action["type"] == "text":
                            await asyncio.to_thread(
                                self._answer_delivery.send_input,
                                terminal_id,
                                action["value"],
                            )
                        elif action["type"] == "key":
                            await asyncio.to_thread(
                                self._answer_delivery.send_special_key,
                                terminal_id,
                                action["value"],
                            )
                        elif action["type"] == "keys":
                            keys = action["value"]
                            start_index = self._delivery_progress.get(interrupt_id, 0)
                            for index in range(start_index, len(keys)):
                                await asyncio.to_thread(
                                    self._answer_delivery.send_special_key,
                                    terminal_id,
                                    keys[index],
                                )
                                # Advance only after the backend call returns:
                                # a failed key remains retryable, while a
                                # successfully delivered prefix is not replayed.
                                self._delivery_progress[interrupt_id] = index + 1
                                self._delivery_decisions.setdefault(interrupt_id, decision)
                    except Exception as e:
                        # Reconcile a concurrent expire() (unlocked sync path) that
                        # resolved this interrupt while the FAILED delivery was in
                        # flight: nothing was delivered, so expiry wins and the
                        # failure is NOT advertised as retryable.
                        if interrupt.resolved:
                            self._delivery_progress.pop(interrupt_id, None)
                            self._delivery_decisions.pop(interrupt_id, None)
                            logger.info(
                                "delivery failed but interrupt %s expired mid-flight;"
                                " honoring expiry",
                                interrupt_id,
                            )
                            return interrupt
                        # Retryable: leave the interrupt UNRESOLVED so a later resume
                        # can re-attempt; surface the failure to the caller.
                        logger.warning(
                            "Failed to deliver answer for interrupt %s: %s",
                            interrupt_id,
                            e,
                        )
                        raise DeliveryError(str(e)) from e
                    finally:
                        _watchdog_handle.cancel()
                    _elapsed = time.monotonic() - _delivery_start
                    if _elapsed > _DELIVERY_SLOW_WARN_SECONDS:
                        logger.warning(
                            "Slow answer delivery for interrupt %s: %.1fs"
                            " (backend may be degraded)",
                            interrupt_id,
                            _elapsed,
                        )
            finally:
                _entry.refs -= 1
                if _entry.refs == 0:
                    self._delivery_locks.pop(terminal_id, None)

        # Delivery-beats-expire: reaching here means delivery succeeded (or was
        # not applicable), so the terminal already received the input. Commit the
        # decision even if a concurrent expire() raced in during the await — the
        # record must reflect ground truth (delivered), not a mid-flight expiry.
        # (Committing overwrites any "expired" outcome expire() set; the
        # terminal-map guard tolerates its removal.)
        interrupt.resolved = True
        interrupt.outcome = decision.value
        self._resolved_at[interrupt_id] = time.monotonic()
        self._delivery_progress.pop(interrupt_id, None)
        self._delivery_decisions.pop(interrupt_id, None)
        if terminal_id and self._terminal_to_interrupt.get(terminal_id) == interrupt_id:
            del self._terminal_to_interrupt[terminal_id]

        # Emit resolution intent
        try:
            self.emit(
                "approval_card",
                {
                    "interrupt_id": interrupt_id,
                    "resolved": True,
                    "outcome": decision.value,
                    "provider": provider,
                    "terminal_id": terminal_id,
                },
                terminal_id=terminal_id,
                session_name=interrupt.metadata.get("session_name"),
            )
        except (ValueError, RuntimeError):
            logger.debug("Failed to emit resolution for interrupt %s", interrupt_id)

        return interrupt

    def expire(self, terminal_id: str) -> Optional[Interrupt]:
        """Expire the open interrupt for a terminal (zero keystrokes).

        Returns the expired interrupt, or None if no open interrupt exists.
        """
        interrupt_id = self._terminal_to_interrupt.get(terminal_id)
        if interrupt_id is None:
            return None

        interrupt = self._interrupts.get(interrupt_id)
        if interrupt is None or interrupt.resolved:
            # Clean up stale mapping
            self._terminal_to_interrupt.pop(terminal_id, None)
            return None

        # Resolve as expired with ZERO keystrokes
        interrupt.resolved = True
        interrupt.outcome = "expired"
        self._resolved_at[interrupt_id] = time.monotonic()
        self._delivery_progress.pop(interrupt_id, None)
        self._delivery_decisions.pop(interrupt_id, None)

        # Remove from terminal map
        del self._terminal_to_interrupt[terminal_id]

        # Emit expiration intent
        try:
            self.emit(
                "approval_card",
                {
                    "interrupt_id": interrupt_id,
                    "resolved": True,
                    "outcome": "expired",
                    "provider": interrupt.metadata.get("provider", ""),
                    "terminal_id": terminal_id,
                },
                terminal_id=terminal_id,
                session_name=interrupt.metadata.get("session_name"),
            )
        except (ValueError, RuntimeError):
            logger.debug("Failed to emit expiration for interrupt %s", interrupt_id)

        return interrupt

    def pending(self) -> List[Interrupt]:
        """Return all unresolved interrupts."""
        return [i for i in self._interrupts.values() if not i.resolved]

    def get_interrupt(self, interrupt_id: str) -> Optional[Interrupt]:
        """Return an interrupt by ID, or None if not found."""
        return self._interrupts.get(interrupt_id)

    def _evict_durable_state(self, interrupt_id: str) -> None:
        """Remove process-local authority retained for an evicted terminal interrupt."""
        self._durable_bindings.pop(interrupt_id, None)
        self._durable_requests.pop(interrupt_id, None)
        self._durable_uncertain.discard(interrupt_id)

    def _evict_if_needed(self) -> None:
        """Evict resolved entries beyond the TTL, then oldest resolved if over cap."""
        now = time.monotonic()

        # First pass: evict resolved entries past TTL
        expired_ids = [
            iid
            for iid, resolved_time in self._resolved_at.items()
            if now - resolved_time >= _RESOLVED_TTL_SECONDS
        ]
        for iid in expired_ids:
            self._interrupts.pop(iid, None)
            self._resolved_at.pop(iid, None)
            self._evict_durable_state(iid)

        # Second pass: if still over cap, evict oldest resolved first
        while len(self._interrupts) > _REGISTRY_CAP:
            # Find oldest resolved
            oldest_id = None
            oldest_time = float("inf")
            for iid, resolved_time in self._resolved_at.items():
                if resolved_time < oldest_time:
                    oldest_time = resolved_time
                    oldest_id = iid
            if oldest_id:
                self._interrupts.pop(oldest_id, None)
                self._resolved_at.pop(oldest_id, None)
                self._evict_durable_state(oldest_id)
            else:
                break  # No resolved entries to evict


def _interrupt_to_dict(interrupt: Interrupt) -> Dict[str, Any]:
    """Convert an Interrupt to a JSON-serializable dict."""
    return {
        "id": interrupt.id,
        "reason": interrupt.reason,
        "message": interrupt.message,
        "metadata": interrupt.metadata,
        "options": interrupt.options,
        "created_at": interrupt.created_at,
        "expires_at": interrupt.expires_at,
        "resolved": interrupt.resolved,
        "outcome": interrupt.outcome,
    }


__all__ = [
    "AgentHandoffWithApproval",
    "AnswerDelivery",
    "ApprovalDecision",
    "Interrupt",
    "classify_reason",
]
