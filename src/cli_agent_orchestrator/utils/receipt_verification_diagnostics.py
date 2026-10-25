"""Opt-in, content-free receipt-verification stage observations.

The default sink is unset. The fixture-facing sink receives only a bounded
attempt ordinal and fixed stage/outcome enums; it cannot receive a result,
receipt, identifier, path, hash, or exception.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from threading import Lock
from typing import Callable, Iterator

DiagnosticSink = Callable[[int, str, str], object]

_ALLOWED_OUTCOMES = {
    "admission": frozenset({"stale_capture", "current_capture", "progress_unconfirmed"}),
    "attempt": frozenset({"started", "verified", "unresolved", "superseded"}),
    "result_extraction": frozenset({"started", "returned", "unavailable", "error"}),
    "receipt_witness": frozenset({"started", "passed", "rejected", "error", "not_applicable"}),
    "canonicalizer": frozenset({"started", "passed", "rejected", "error", "not_applicable"}),
    "active_receipt_identity": frozenset(
        {"started", "passed", "rejected", "error", "skip", "not_applicable"}
    ),
    "native_binding": frozenset({"started", "passed", "rejected", "error", "skip"}),
    "native_parts": frozenset({"started", "passed", "rejected", "error", "skip"}),
    "native_receipt_identity": frozenset({"started", "passed", "rejected", "error", "skip"}),
    "native_render_guard": frozenset({"started", "passed", "rejected", "error", "skip"}),
    "durable_cas": frozenset({"started", "settled", "conflict", "error"}),
}

_sink_lock = Lock()
_sink: DiagnosticSink | None = None
_attempt_ordinal: ContextVar[int] = ContextVar("receipt_verification_attempt", default=0)
_stage_outcomes: ContextVar[dict[str, str] | None] = ContextVar(
    "receipt_verification_stage_outcomes", default=None
)


def set_receipt_verification_diagnostic_sink(
    sink: DiagnosticSink | None,
) -> DiagnosticSink | None:
    """Install an opt-in sink and return the previously installed sink."""
    if sink is not None and not callable(sink):
        raise TypeError("diagnostic sink must be callable or None")
    global _sink
    with _sink_lock:
        previous = _sink
        _sink = sink
    return previous


def receipt_verification_diagnostics_enabled() -> bool:
    """Return whether an opt-in sink is installed without exposing it."""
    with _sink_lock:
        return _sink is not None


def current_receipt_verification_attempt() -> int:
    """Return the current scheduled verifier ordinal, or zero for direct reads."""
    return _attempt_ordinal.get()


@contextmanager
def receipt_verification_attempt(ordinal: int) -> Iterator[None]:
    """Bind one already-scheduled local retry attempt to nested verifier calls."""
    if type(ordinal) is not int or ordinal not in {1, 2, 3}:
        raise ValueError("receipt verification attempt ordinal is outside the fixed range")
    attempt_token: Token[int] = _attempt_ordinal.set(ordinal)
    outcomes_token: Token[dict[str, str] | None] = _stage_outcomes.set(
        {} if receipt_verification_diagnostics_enabled() else None
    )
    try:
        yield
    finally:
        _stage_outcomes.reset(outcomes_token)
        _attempt_ordinal.reset(attempt_token)


def receipt_verification_diagnostic_stage_outcome(stage: str) -> str | None:
    """Return the last fixed outcome recorded for a stage in this attempt."""
    outcomes = _stage_outcomes.get()
    if outcomes is None:
        return None
    return outcomes.get(stage)


def emit_receipt_verification_event(stage: str, outcome: str) -> None:
    """Emit a fixed event while suppressing every sink failure, including BaseException."""
    if type(stage) is not str or type(outcome) is not str:
        return
    allowed = _ALLOWED_OUTCOMES.get(stage)
    if allowed is None or outcome not in allowed:
        return
    ordinal = _attempt_ordinal.get()
    if type(ordinal) is not int or ordinal not in {0, 1, 2, 3}:
        return
    with _sink_lock:
        sink = _sink
    if sink is None:
        return
    outcomes = _stage_outcomes.get()
    if outcomes is not None:
        outcomes[stage] = outcome
    try:
        sink(ordinal, stage, outcome)
    except BaseException:
        # Diagnostic code must never change the verifier's result or exception.
        return


@contextmanager
def receipt_verification_diagnostic_stage(stage: str) -> Iterator[Callable[[str], None]]:
    """Record one stage start and its fixed completion while preserving failures."""
    if not receipt_verification_diagnostics_enabled():
        yield lambda _outcome: None
        return
    emit_receipt_verification_event(stage, "started")
    completed = False

    def finish(outcome: str) -> None:
        nonlocal completed
        if completed:
            return
        allowed = _ALLOWED_OUTCOMES.get(stage)
        if outcome == "started" or allowed is None or outcome not in allowed:
            return
        emit_receipt_verification_event(stage, outcome)
        completed = True

    try:
        yield finish
    except BaseException:
        if not completed:
            finish("error")
        raise
    else:
        if not completed:
            finish("passed")
