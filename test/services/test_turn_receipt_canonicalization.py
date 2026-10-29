"""Receipt canonicalization stays inside the existing render-to-CAS boundary."""

import contextlib
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.base import (
    BaseProvider,
    OutputExtractionError,
    TurnResultUnavailableError,
)
from cli_agent_orchestrator.providers.opencode_cli import OpenCodeCliProvider
from cli_agent_orchestrator.services import remote_terminal_service as remote
from cli_agent_orchestrator.services import terminal_service as terminal

pytestmark = pytest.mark.offline_database

TERMINAL_ID = "canonical-terminal"
GENERATION = "a" * 32
NONCE = "CAO-TURN-RECEIPT-" + "b" * 32
RECEIPT_SHA256 = hashlib.sha256(NONCE.encode("utf-8")).hexdigest()
TRANSCRIPT = "current rendered frame"
RENDERED_RESULT = "Visible split\nbody\n" + NONCE
CANONICAL_RESULT = "Native hard\nline\n" + NONCE


@pytest.fixture(autouse=True)
def no_database_dispatch_lock(monkeypatch):
    from cli_agent_orchestrator.services import work_terminal

    @contextlib.contextmanager
    def unlocked(*_args, **_kwargs):
        yield None

    monkeypatch.setattr(work_terminal, "terminal_dispatch_lock", unlocked)
    monkeypatch.setattr(
        work_terminal.DatabaseTarget,
        "from_settings",
        classmethod(lambda _cls, **_kwargs: None),
    )


class _ReceiptProvider(BaseProvider):
    requires_turn_receipt = True
    supports_canonical_turn_receipt_result = True

    def __init__(self, canonical_result=CANONICAL_RESULT, statuses=None):
        super().__init__(TERMINAL_ID, "session", "window")
        self._pending_turn_generation = GENERATION
        self._pending_turn_receipt_sha256 = RECEIPT_SHA256
        self._pending_turn_receipt = None
        self.canonical_result = canonical_result
        self.canonical_error = None
        self.canonical_side_effect = None
        self.canonical_calls = []
        self.witnessed = []
        self.statuses = list(statuses or [])
        self.marked_verified = 0

    async def initialize(self):
        return True

    def get_status(self, _buffer):
        return TerminalStatus.COMPLETED

    def extract_last_message_from_script(self, _output):
        return RENDERED_RESULT

    def exit_cli(self):
        return "/exit"

    def cleanup(self):
        return None

    def receipt_result_terminal_status(self, transcript, result):
        self.witnessed.append((transcript, result))
        if not BaseProvider._result_has_active_receipt(self, result):
            return None
        if self.statuses:
            return self.statuses.pop(0)
        return TerminalStatus.COMPLETED

    def extract_post_turn_completion_result(self, _transcript):
        return None

    def canonicalize_turn_receipt_result(self, result):
        self.canonical_calls.append(result)
        if self.canonical_side_effect is not None:
            self.canonical_side_effect(self)
        if self.canonical_error is not None:
            raise self.canonical_error
        return self.canonical_result

    def mark_turn_receipt_result_verified(self):
        self.marked_verified += 1
        super().mark_turn_receipt_result_verified()


class _IdentityReceiptProvider(BaseProvider):
    """Use BaseProvider's identity hook instead of opting into canonical text."""

    requires_turn_receipt = True

    def __init__(self, terminal_id, session_name, window_name):
        super().__init__(terminal_id, session_name, window_name)
        self._pending_turn_generation = GENERATION
        self._pending_turn_receipt_sha256 = RECEIPT_SHA256
        self._pending_turn_receipt = NONCE
        self.status_reads = 0

    async def initialize(self):
        return True

    def get_status(self, _buffer):
        self.status_reads += 1
        return TerminalStatus.COMPLETED

    def extract_last_message_from_script(self, _output):
        return "rendered"

    def exit_cli(self):
        return "/exit"

    def cleanup(self):
        return None


class _UnoptedIdentityReceiptProvider(_ReceiptProvider):
    """Model a versioned provider whose canonicalizer remains an identity."""

    supports_canonical_turn_receipt_result = False

    def canonicalize_turn_receipt_result(self, result):
        self.canonical_calls.append(result)
        return result


def _patch_settle(monkeypatch, return_value=True, events=None):
    calls = []

    def settle(*args, **kwargs):
        calls.append((args, kwargs))
        if events is not None:
            events.append("cas")
        return return_value

    monkeypatch.setattr(terminal, "settle_terminal_turn_receipt_result", settle)
    return calls


def _patch_status(monkeypatch, events=None):
    calls = []

    def publish(*args):
        calls.append(args)
        if events is not None:
            events.append("status")

    monkeypatch.setattr(terminal.status_monitor, "publish_observed_status", publish)
    return calls


def test_base_canonicalizer_defaults_to_identity():
    provider = _IdentityReceiptProvider("identity-terminal", "session", "window")

    assert provider.canonicalize_turn_receipt_result(RENDERED_RESULT) == RENDERED_RESULT


def test_base_identity_preserves_single_completed_witness(monkeypatch):
    provider = _IdentityReceiptProvider(TERMINAL_ID, "session", "window")
    settle_calls = _patch_settle(monkeypatch)
    status_calls = _patch_status(monkeypatch)

    result = terminal._verify_receipt_bearing_result(
        TERMINAL_ID, provider, TRANSCRIPT, RENDERED_RESULT
    )

    assert result == "Visible split\nbody"
    assert provider.status_reads == 1
    assert len(settle_calls) == 1
    assert status_calls == [(TERMINAL_ID, TerminalStatus.COMPLETED)]


def test_v1_style_identity_override_preserves_single_completed_witness(monkeypatch):
    provider = _UnoptedIdentityReceiptProvider(statuses=[TerminalStatus.COMPLETED])
    settle_calls = _patch_settle(monkeypatch)
    _patch_status(monkeypatch)

    result = terminal._verify_receipt_bearing_result(
        TERMINAL_ID, provider, TRANSCRIPT, RENDERED_RESULT
    )

    assert result == "Visible split\nbody"
    assert provider.canonical_calls == []
    assert provider.witnessed == [(TRANSCRIPT, RENDERED_RESULT)]
    args, kwargs = settle_calls[0]
    assert args[3] == hashlib.sha256(RENDERED_RESULT.encode("utf-8")).hexdigest()
    assert kwargs == {"result_text": "Visible split\nbody"}


def test_instance_proxy_canonicalizer_is_not_used_without_a_concrete_class_hook(monkeypatch):
    provider = MagicMock()
    provider.requires_turn_receipt = True
    provider.pending_turn_receipt_state.return_value = {
        "generation": GENERATION,
        "receipt_sha256": RECEIPT_SHA256,
    }
    provider.receipt_result_terminal_status.return_value = TerminalStatus.COMPLETED
    provider.canonicalize_turn_receipt_result.return_value = CANONICAL_RESULT
    settle_calls = _patch_settle(monkeypatch)
    _patch_status(monkeypatch)

    result = terminal._verify_receipt_bearing_result(
        TERMINAL_ID, provider, TRANSCRIPT, RENDERED_RESULT
    )

    assert result == "Visible split\nbody"
    provider.canonicalize_turn_receipt_result.assert_not_called()
    args, kwargs = settle_calls[0]
    assert args[3] == hashlib.sha256(RENDERED_RESULT.encode("utf-8")).hexdigest()
    assert kwargs == {"result_text": "Visible split\nbody"}


def test_completed_render_witness_cas_uses_canonical_raw_and_public_text(monkeypatch):
    provider = _ReceiptProvider(statuses=[TerminalStatus.COMPLETED] * 2)
    events = []
    provider.canonical_side_effect = lambda _provider: events.append("canonical")
    settle_calls = _patch_settle(monkeypatch, events=events)
    status_calls = _patch_status(monkeypatch, events=events)

    result = terminal._verify_receipt_bearing_result(
        TERMINAL_ID, provider, TRANSCRIPT, RENDERED_RESULT
    )

    expected_public = "Native hard\nline"
    assert result == expected_public
    assert provider.canonical_calls == [RENDERED_RESULT]
    assert provider.witnessed == [
        (TRANSCRIPT, RENDERED_RESULT),
        (TRANSCRIPT, CANONICAL_RESULT),
    ]
    args, kwargs = settle_calls[0]
    assert args == (
        TERMINAL_ID,
        GENERATION,
        RECEIPT_SHA256,
        hashlib.sha256(CANONICAL_RESULT.encode("utf-8")).hexdigest(),
    )
    assert kwargs == {"result_text": expected_public}
    assert events == ["canonical", "cas", "status"]
    assert provider.marked_verified == 1
    assert status_calls == [(TERMINAL_ID, TerminalStatus.COMPLETED)]


def test_canonicalizer_is_not_called_without_a_normal_completed_witness(monkeypatch):
    provider = _ReceiptProvider(statuses=[None])
    settle_calls = _patch_settle(monkeypatch)
    status_calls = _patch_status(monkeypatch)

    with pytest.raises(TurnResultUnavailableError):
        terminal._verify_receipt_bearing_result(TERMINAL_ID, provider, TRANSCRIPT, RENDERED_RESULT)

    assert provider.canonical_calls == []
    assert settle_calls == []
    assert status_calls == []
    assert provider.marked_verified == 0
    assert provider.pending_turn_receipt_state() == {
        "generation": GENERATION,
        "receipt_sha256": RECEIPT_SHA256,
    }


def test_post_turn_blocker_keeps_the_existing_rendered_result_path(monkeypatch):
    provider = _ReceiptProvider(statuses=[TerminalStatus.WAITING_USER_ANSWER])
    settle_calls = _patch_settle(monkeypatch)
    status_calls = _patch_status(monkeypatch)

    result = terminal._verify_receipt_bearing_result(
        TERMINAL_ID, provider, TRANSCRIPT, RENDERED_RESULT
    )

    assert result == "Visible split\nbody"
    assert provider.canonical_calls == []
    args, kwargs = settle_calls[0]
    assert args[3] == hashlib.sha256(RENDERED_RESULT.encode("utf-8")).hexdigest()
    assert kwargs == {"result_text": "Visible split\nbody"}
    assert status_calls == [(TERMINAL_ID, TerminalStatus.WAITING_USER_ANSWER)]


@pytest.mark.parametrize(
    "bad_result",
    ["", 7, "Native output without the active receipt"],
    ids=["empty", "non-string", "missing-active-receipt"],
)
def test_invalid_canonical_result_fails_without_cas_status_or_clear(monkeypatch, bad_result):
    provider = _ReceiptProvider(canonical_result=bad_result)
    settle_calls = _patch_settle(monkeypatch)
    status_calls = _patch_status(monkeypatch)

    with pytest.raises(TurnResultUnavailableError):
        terminal._verify_receipt_bearing_result(TERMINAL_ID, provider, TRANSCRIPT, RENDERED_RESULT)

    assert settle_calls == []
    assert status_calls == []
    assert provider.marked_verified == 0
    assert provider.pending_turn_receipt_state() == {
        "generation": GENERATION,
        "receipt_sha256": RECEIPT_SHA256,
    }


def test_reader_extraction_error_fails_without_fallback_or_cas(monkeypatch):
    provider = _ReceiptProvider()
    provider.canonical_error = OutputExtractionError("fixed reader unavailable")
    settle_calls = _patch_settle(monkeypatch)
    status_calls = _patch_status(monkeypatch)

    with pytest.raises(TurnResultUnavailableError):
        terminal._verify_receipt_bearing_result(TERMINAL_ID, provider, TRANSCRIPT, RENDERED_RESULT)

    assert provider.canonical_calls == [RENDERED_RESULT]
    assert settle_calls == []
    assert status_calls == []
    assert provider.marked_verified == 0
    assert provider.pending_turn_receipt_state() == {
        "generation": GENERATION,
        "receipt_sha256": RECEIPT_SHA256,
    }


def test_changed_pending_fingerprint_after_read_fails_before_cas(monkeypatch):
    provider = _ReceiptProvider()
    provider.canonical_side_effect = lambda current: setattr(
        current, "_pending_turn_generation", "c" * 32
    )
    settle_calls = _patch_settle(monkeypatch)
    status_calls = _patch_status(monkeypatch)

    with pytest.raises(TurnResultUnavailableError):
        terminal._verify_receipt_bearing_result(TERMINAL_ID, provider, TRANSCRIPT, RENDERED_RESULT)

    assert settle_calls == []
    assert status_calls == []
    assert provider.marked_verified == 0


def test_canonical_result_must_rewitness_the_same_completed_status(monkeypatch):
    provider = _ReceiptProvider(
        statuses=[TerminalStatus.COMPLETED, TerminalStatus.WAITING_USER_ANSWER]
    )
    settle_calls = _patch_settle(monkeypatch)
    status_calls = _patch_status(monkeypatch)

    with pytest.raises(TurnResultUnavailableError):
        terminal._verify_receipt_bearing_result(TERMINAL_ID, provider, TRANSCRIPT, RENDERED_RESULT)

    assert provider.witnessed == [
        (TRANSCRIPT, RENDERED_RESULT),
        (TRANSCRIPT, CANONICAL_RESULT),
    ]
    assert settle_calls == []
    assert status_calls == []
    assert provider.marked_verified == 0


def test_failed_receipt_cas_preserves_pending_provider_state(monkeypatch):
    provider = _ReceiptProvider(statuses=[TerminalStatus.COMPLETED] * 2)
    settle_calls = _patch_settle(monkeypatch, return_value=False)
    status_calls = _patch_status(monkeypatch)

    with pytest.raises(TurnResultUnavailableError):
        terminal._verify_receipt_bearing_result(TERMINAL_ID, provider, TRANSCRIPT, RENDERED_RESULT)

    assert len(settle_calls) == 1
    assert status_calls == []
    assert provider.marked_verified == 0
    assert provider.pending_turn_receipt_state() == {
        "generation": GENERATION,
        "receipt_sha256": RECEIPT_SHA256,
    }


def test_two_poll_probe_stays_rendered_until_settlement(monkeypatch):
    provider = _ReceiptProvider(statuses=[TerminalStatus.COMPLETED] * 4)
    monkeypatch.setattr(terminal.provider_manager, "get_provider", lambda _id: provider)
    monkeypatch.setattr(terminal.status_monitor, "get_buffer", lambda _id: TRANSCRIPT)
    _patch_settle(monkeypatch)
    _patch_status(monkeypatch)

    assert terminal.probe_post_turn_receipt_result(TERMINAL_ID) == RENDERED_RESULT
    assert provider.canonical_calls == []
    assert terminal.settle_post_turn_receipt_result(TERMINAL_ID, RENDERED_RESULT) == (
        "Native hard\nline"
    )
    assert provider.canonical_calls == [RENDERED_RESULT]


@pytest.mark.parametrize(
    "persisted",
    ["Previously verified logical result\nwith LF", ""],
    ids=["nonempty", "empty"],
)
def test_verified_last_read_returns_persisted_text_without_canonicalizing(monkeypatch, persisted):
    monkeypatch.setattr(remote, "placement", lambda _id: None)
    monkeypatch.setattr(
        terminal,
        "get_terminal_metadata",
        lambda _id: {"tmux_session": "session", "tmux_window": "window", "provider": "opencode"},
    )
    monkeypatch.setattr(terminal.status_monitor, "get_buffer", lambda _id: "current pane")
    monkeypatch.setattr(
        terminal,
        "get_terminal_turn_recovery",
        lambda _id: {"state": "verified", "generation": GENERATION, "result_text": persisted},
    )
    monkeypatch.setattr(
        terminal.provider_manager,
        "get_provider",
        lambda _id: pytest.fail("verified recovery must return before provider lookup"),
    )

    assert terminal.get_output(TERMINAL_ID, terminal.OutputMode.LAST) == persisted
    assert terminal.get_output(TERMINAL_ID, terminal.OutputMode.LAST) == persisted


@pytest.mark.parametrize(
    "result_text",
    [None, 7],
    ids=["absent", "non-string"],
)
def test_verified_last_read_without_string_result_fails_before_provider_lookup(
    monkeypatch, result_text
):
    recovery = {"state": "verified", "generation": GENERATION, "result_text": result_text}
    monkeypatch.setattr(remote, "placement", lambda _id: None)
    monkeypatch.setattr(
        terminal,
        "get_terminal_metadata",
        lambda _id: {"tmux_session": "session", "tmux_window": "window", "provider": "opencode"},
    )
    monkeypatch.setattr(terminal.status_monitor, "get_buffer", lambda _id: "current pane")
    monkeypatch.setattr(terminal, "get_terminal_turn_recovery", lambda _id: recovery.copy())
    provider_lookup = MagicMock(return_value=None)
    monkeypatch.setattr(terminal.provider_manager, "get_provider", provider_lookup)

    with pytest.raises(TurnResultUnavailableError):
        terminal.get_output(TERMINAL_ID, terminal.OutputMode.LAST)

    provider_lookup.assert_not_called()


def test_legacy_split_verification_without_retained_text_cannot_return_receipt_transcript(
    monkeypatch, request
):
    engine = create_engine("sqlite:///:memory:")
    request.addfinalizer(engine.dispose)
    tables = [
        database.TerminalModel.__table__,
        database.TerminalTurnReceiptModel.__table__,
        database.TerminalTurnRecoveryModel.__table__,
    ]
    database.Base.metadata.create_all(engine, tables=tables)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", session_factory)
    with session_factory() as session:
        session.add(
            database.TerminalModel(
                id=TERMINAL_ID,
                tmux_session="session",
                tmux_window="window",
                provider="opencode",
                insertion_sequence=1,
            )
        )
        session.add(
            database.TerminalTurnReceiptModel(
                terminal_id=TERMINAL_ID,
                provider="opencode",
                generation=GENERATION,
                receipt_sha256=RECEIPT_SHA256,
                phase="sent",
            )
        )
        session.commit()

    assert database.verify_terminal_turn_receipt_result(
        TERMINAL_ID, GENERATION, RECEIPT_SHA256, "c" * 64
    )
    legacy_recovery = database.get_terminal_turn_recovery(TERMINAL_ID)
    assert legacy_recovery is not None
    assert legacy_recovery["state"] == "verified"
    assert legacy_recovery["result_text"] is None

    transcript = (
        "› request\n\nACK\n" + NONCE + "\n\nagent · model · 1.0s\n┃\nagent · model · ctrl+p menu"
    )
    provider = OpenCodeCliProvider(TERMINAL_ID, "session", "window")
    provider._cli_major = 2
    assert provider.pending_turn_receipt_state() is None
    assert provider.extract_last_message_from_script(transcript) == "ACK\n" + NONCE

    monkeypatch.setattr(remote, "placement", lambda _id: None)
    monkeypatch.setattr(
        terminal,
        "get_terminal_metadata",
        lambda _id: {"tmux_session": "session", "tmux_window": "window", "provider": "opencode"},
    )
    monkeypatch.setattr(terminal.status_monitor, "get_buffer", lambda _id: "current pane")
    monkeypatch.setattr(
        terminal,
        "get_terminal_turn_recovery",
        lambda _id: database.get_terminal_turn_recovery(TERMINAL_ID),
    )
    provider_lookup = MagicMock(return_value=provider)
    monkeypatch.setattr(terminal.provider_manager, "get_provider", provider_lookup)
    backend = MagicMock()
    backend.get_history.return_value = transcript
    monkeypatch.setattr(terminal, "get_backend", lambda: backend)

    with pytest.raises(TurnResultUnavailableError):
        terminal.get_output(TERMINAL_ID, terminal.OutputMode.LAST)

    provider_lookup.assert_not_called()
    assert database.get_terminal_turn_recovery(TERMINAL_ID) == legacy_recovery


def test_full_output_remains_the_transcript_when_verified_text_is_missing(monkeypatch):
    transcript = "full transcript\n" + NONCE
    monkeypatch.setattr(remote, "placement", lambda _id: None)
    monkeypatch.setattr(
        terminal,
        "get_terminal_metadata",
        lambda _id: {"tmux_session": "session", "tmux_window": "window"},
    )
    monkeypatch.setattr(terminal.status_monitor, "get_buffer", lambda _id: transcript)

    assert terminal.get_output(TERMINAL_ID, terminal.OutputMode.FULL) == transcript


@pytest.mark.parametrize("phase_index", [0, 1], ids=["before-restart", "after-restart"])
@pytest.mark.parametrize("restored", [False, True], ids=["fresh", "restored"])
@pytest.mark.parametrize(
    "native_body",
    ["Validado ñ, 中文.", "Validado\n ñ, 中文."],
    ids=["logical-one-line", "semantic-line-feed"],
)
def test_real_opencode_owned_store_reaches_existing_receipt_cas(
    tmp_path, monkeypatch, restored, native_body, phase_index
):
    # Reuse the actual owned SQLite fixture, then compose both production
    # components. The parser and native reader remain real; only the external
    # durable CAS/status sinks are intercepted by this offline contract test.
    from test.providers.test_opencode_v2_canonical_result import (
        RENDERED,
        TOKEN,
        _provider_and_store,
    )

    provider, _owner, _root, database = _provider_and_store(
        tmp_path, parts=[native_body + "\n" + TOKEN], restored=restored
    )
    # Materialize the actual acceptance input and production receipt envelope,
    # rather than giving the native reader a generic nonce-only test request.
    source = (
        Path(__file__).resolve().parents[2]
        / "specs/025-provider-mcp-profiles/fixtures/run_native_v2_acceptance.py"
    )
    spec = importlib.util.spec_from_file_location("t124_source_phase", source)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    # Dataclasses in the source runner resolve their defining module here.
    import sys

    monkeypatch.setitem(sys.modules, spec.name, runner)
    spec.loader.exec_module(runner)
    phase_prompt = runner._phase_prompt(phase_index, "1234abcd", runner.PHASES[phase_index][1])
    prepared = provider._format_turn_receipt_input(phase_prompt, TOKEN)
    assert prepared.startswith(phase_prompt + "\n\n")
    assert prepared.index(runner.LAST_RECEIPT) < prepared.index(TOKEN)
    with contextlib.closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "UPDATE session_message SET data=? WHERE id='user-1'",
            (json.dumps({"text": prepared}, ensure_ascii=False),),
        )
    transcript = "› current request\n\n" + RENDERED + "\n\n┃\nctrl+p menu"
    rendered = provider.extract_last_message_from_script(transcript)
    assert "Validado\n" in rendered
    assert provider.receipt_result_terminal_status(transcript, rendered) is TerminalStatus.COMPLETED
    before = database.read_bytes()
    state = provider.pending_turn_receipt_state()
    settle_calls = _patch_settle(monkeypatch)
    status_calls = _patch_status(monkeypatch)

    public = terminal._verify_receipt_bearing_result(
        provider.terminal_id, provider, transcript, rendered
    )

    assert public == native_body
    args, kwargs = settle_calls[0]
    assert args == (
        provider.terminal_id,
        state["generation"],
        state["receipt_sha256"],
        hashlib.sha256((native_body + "\n" + TOKEN).encode("utf-8")).hexdigest(),
    )
    assert kwargs == {"result_text": native_body}
    assert provider.pending_turn_receipt_state() is None
    assert status_calls == [(provider.terminal_id, TerminalStatus.COMPLETED)]
    assert database.read_bytes() == before


class _ViewportReceiptProvider(_IdentityReceiptProvider):
    supports_screen_detection = True

    def extract_last_message_from_script(self, output):
        raise ValueError("native redraw removed assistant head")

    def extract_viewport_completion_result(self, viewport):
        return RENDERED_RESULT if viewport == TRANSCRIPT else None

    def receipt_result_viewport_terminal_status(self, viewport, result):
        if viewport == TRANSCRIPT and self._result_has_active_receipt(result):
            return TerminalStatus.COMPLETED
        return None


@pytest.mark.parametrize("historical_answer", [False, True])
def test_last_uses_current_viewport_candidate_after_missing_or_stale_head(
    monkeypatch, historical_answer
):
    provider = _ViewportReceiptProvider(TERMINAL_ID, "session", "window")
    if historical_answer:
        provider.extract_last_message_from_script = lambda _output: "old answer"
    backend = MagicMock()
    backend.get_history.side_effect = lambda *_a, **kw: (
        TRANSCRIPT if kw.get("visible_only") else "history without current answer"
    )
    monkeypatch.setattr(remote, "placement", lambda _id: None)
    monkeypatch.setattr(terminal, "ensure_terminal_is_not_work_owned", lambda _id: None)
    monkeypatch.setattr(
        terminal,
        "get_terminal_metadata",
        lambda _id: {"tmux_session": "session", "tmux_window": "window", "provider": "claude_code"},
    )
    monkeypatch.setattr(terminal, "get_terminal_turn_recovery", lambda _id: None)
    monkeypatch.setattr(terminal, "get_backend", lambda: backend)
    monkeypatch.setattr(terminal.provider_manager, "get_provider", lambda _id: provider)
    monkeypatch.setattr(terminal.status_monitor, "get_buffer", lambda _id: "raw redraw")
    settled = _patch_settle(monkeypatch)
    _patch_status(monkeypatch)
    assert (
        terminal.get_output(TERMINAL_ID, terminal.OutputMode.LAST, expected_generation=GENERATION)
        == "Visible split\nbody"
    )
    assert len(settled) == 1
    assert provider.pending_turn_receipt_state() is None


@pytest.mark.parametrize("change", ["missing", "result", "generation", "provider"])
def test_current_candidate_requires_two_consistent_observations(monkeypatch, change):
    p = _ViewportReceiptProvider(TERMINAL_ID, "session", "window")
    monkeypatch.setattr(terminal.provider_manager, "get_provider", lambda _id: p)
    first = (p, TRANSCRIPT, RENDERED_RESULT, True)
    second = first
    if change == "missing":
        second = None
    elif change == "result":
        second = (p, TRANSCRIPT, "changed\n" + NONCE, True)
    calls = 0

    def capture(_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == "generation":
                p._pending_turn_generation = "f" * 32
            elif change == "provider":
                monkeypatch.setattr(terminal.provider_manager, "get_provider", lambda _id: object())
            return second
        return first

    monkeypatch.setattr(terminal, "_post_turn_receipt_candidate", capture)
    settled = _patch_settle(monkeypatch)
    assert terminal._verify_current_receipt_candidate(TERMINAL_ID, p, GENERATION, False) is None
    assert settled == []


def test_native_hook_never_runs_on_raw_or_history_capture(monkeypatch):
    p = _ViewportReceiptProvider(TERMINAL_ID, "session", "window")
    called = []
    monkeypatch.setattr(
        type(p), "extract_viewport_completion_result", lambda *_a: called.append(True)
    )
    monkeypatch.setattr(terminal.provider_manager, "get_provider", lambda _id: p)
    monkeypatch.setattr(terminal.status_monitor, "get_buffer", lambda _id: TRANSCRIPT)
    backend = MagicMock()
    backend.get_history.side_effect = RuntimeError("viewport unavailable")
    monkeypatch.setattr(terminal, "get_backend", lambda: backend)
    assert terminal._post_turn_receipt_candidate(TERMINAL_ID) is None
    assert called == []


def test_native_hook_exception_is_only_missing_evidence(monkeypatch):
    p = _ViewportReceiptProvider(TERMINAL_ID, "session", "window")

    def broken(*_args):
        raise OSError("optional channel unavailable")

    monkeypatch.setattr(type(p), "extract_viewport_completion_result", broken)
    monkeypatch.setattr(terminal.provider_manager, "get_provider", lambda _id: p)
    monkeypatch.setattr(terminal.status_monitor, "get_buffer", lambda _id: "raw")
    backend = MagicMock()
    backend.get_history.return_value = TRANSCRIPT
    monkeypatch.setattr(terminal, "get_backend", lambda: backend)
    assert terminal._post_turn_receipt_candidate(TERMINAL_ID) is None


def test_last_does_not_retry_a_conflicted_durable_receipt(monkeypatch):
    p = _ReceiptProvider()
    monkeypatch.setattr(remote, "placement", lambda _id: None)
    monkeypatch.setattr(terminal, "ensure_terminal_is_not_work_owned", lambda _id: None)
    monkeypatch.setattr(
        terminal,
        "get_terminal_metadata",
        lambda _id: {"tmux_session": "session", "tmux_window": "window", "provider": "claude_code"},
    )
    monkeypatch.setattr(terminal, "get_terminal_turn_recovery", lambda _id: None)
    monkeypatch.setattr(terminal.provider_manager, "get_provider", lambda _id: p)
    monkeypatch.setattr(terminal.status_monitor, "get_buffer", lambda _id: TRANSCRIPT)
    backend = MagicMock()
    backend.get_history.return_value = TRANSCRIPT
    monkeypatch.setattr(terminal, "get_backend", lambda: backend)
    monkeypatch.setattr(
        terminal,
        "_verify_current_receipt_candidate",
        lambda *_a: pytest.fail(
            "a durable CAS conflict must propagate without trying a new candidate"
        ),
    )
    settled = _patch_settle(monkeypatch, return_value=False)
    with pytest.raises(TurnResultUnavailableError, match="receipt changed"):
        terminal.get_output(TERMINAL_ID, terminal.OutputMode.LAST)
    assert len(settled) == 1
    assert p.pending_turn_receipt_state() is not None
