"""Receipt canonicalization stays inside the existing render-to-CAS boundary."""

import contextlib
import hashlib
from unittest.mock import MagicMock

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.base import (
    BaseProvider,
    OutputExtractionError,
    TurnResultUnavailableError,
)
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


def test_verified_last_read_returns_persisted_text_without_canonicalizing(monkeypatch):
    persisted = "Previously verified logical result\nwith LF"
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


@pytest.mark.parametrize("restored", [False, True], ids=["fresh", "restored"])
@pytest.mark.parametrize(
    "native_body",
    ["Validado ñ, 中文.", "Validado\n ñ, 中文."],
    ids=["logical-one-line", "semantic-line-feed"],
)
def test_real_opencode_owned_store_reaches_existing_receipt_cas(
    tmp_path, monkeypatch, restored, native_body
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
