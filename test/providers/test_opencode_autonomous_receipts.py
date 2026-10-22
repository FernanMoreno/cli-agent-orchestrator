"""Native v2 responses must not be cut at an earlier transport failure."""

import hashlib

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.opencode_cli import OpenCodeCliProvider

RECEIPT = "CAO-TURN-RECEIPT-" + "a" * 32


def provider():
    p = OpenCodeCliProvider("offline", "offline", "offline")
    p._cli_major = 2
    p._resolve_native_status = lambda output: None
    p.restore_turn_receipt_state(
        {
            "generation": "0" * 32,
            "receipt_sha256": hashlib.sha256(RECEIPT.encode()).hexdigest(),
            "phase": "sent",
        }
    )
    return p


def frame(receipt=RECEIPT):
    return (
        "› Implementa la mejora; devuelve un recibo auténtico.\n\n"
        "Intento anterior\nTransport: The operation timed out.\n"
        "auto-web · Kimi K3 · 5m 57s\n\n"
        "Mejora implementada y comprobada.\n" + receipt + "\n\n┃\n"
        "auto-web · Kimi K3 · 75.8K (7%) · OpenCode Go · $0.03 · ctrl+p menu"
    )


@pytest.mark.parametrize("answer", ["Mejora implementada y comprobada.", "Resumen.\n\nDetalle."])
def test_current_receipt_response_is_not_cut_at_stale_duration_marker(answer):
    p = provider()
    text = frame().replace("Mejora implementada y comprobada.", answer)
    result = p.extract_last_message_from_script(text)
    assert result == answer + "\n" + RECEIPT
    assert p.receipt_result_terminal_status(text, result) == TerminalStatus.COMPLETED


@pytest.mark.parametrize("screen", [False, True])
def test_receipt_with_current_idle_footer_does_not_need_a_duration(screen):
    p = provider()
    text = frame().replace("auto-web · Kimi K3 · 5m 57s\n", "")
    status = p.get_status_from_screen(text.splitlines()) if screen else p.get_status(text)
    assert status == TerminalStatus.COMPLETED
    assert p.extract_last_message_from_script(text).endswith(RECEIPT)


@pytest.mark.parametrize(
    "bad", ["CAO-TURN-RECEIPT-" + "b" * 32, "> " + RECEIPT, "`" + RECEIPT + "`"]
)
def test_wrong_generation_or_quoted_receipt_cannot_verify(bad):
    p = provider()
    result = p.extract_last_message_from_script(frame(bad))
    assert p.receipt_result_terminal_status(frame(bad), result) is None


def test_processing_after_receipt_cannot_be_completed():
    p = provider()
    text = frame().replace("ctrl+p menu", "esc interrupt")
    assert p.get_status(text) == TerminalStatus.PROCESSING
    with pytest.raises(ValueError):
        p.extract_last_message_from_script(text)


@pytest.mark.parametrize("screen", [False, True])
@pytest.mark.parametrize(
    "notice", ["Go usage limit exceeded", "Error from provider: Go usage limit exceeded"]
)
def test_native_go_usage_limit_exceeded_is_quota(screen, notice):
    p = provider()
    text = "› sigue\n" + notice + "\n┃\nauto-web · Kimi K3 · OpenCode Go · ctrl+p menu"
    status = p.get_status_from_screen(text.splitlines()) if screen else p.get_status(text)
    assert status == TerminalStatus.WAITING_QUOTA
    assert p.pending_turn_receipt_state() is not None


def test_wrapped_receipt_in_user_delivery_contract_is_not_an_answer():
    import re

    p = OpenCodeCliProvider("offline", "offline", "offline")
    p._cli_major = 2
    p._resolve_native_status = lambda output: None
    prepared = p.prepare_input("Implementa la tarea\n\nConserva los datos.")
    receipt = re.search(r"CAO-TURN-RECEIPT-[0-9a-f]{32}", prepared).group(0)
    echoed = prepared.replace(
        "exactly this receipt: " + receipt, "exactly this receipt:\n" + receipt
    )
    text = "› " + echoed + "\n\n┃\nauto-web · Kimi K3 · OpenCode Go · ctrl+p menu"
    assert p.get_status(text) != TerminalStatus.COMPLETED
    with pytest.raises(ValueError):
        p.extract_last_message_from_script(text)


@pytest.mark.parametrize("screen", [False, True])
def test_current_receipt_detected_when_query_rolled_out_of_viewport(screen):
    p = provider()
    text = (
        "Mejora implementada y comprobada.\n"
        + RECEIPT
        + "\n\n┃\nauto-web · Kimi K3 · 75.8K (7%) · OpenCode Go · ctrl+p menu"
    )
    status = p.get_status_from_screen(text.splitlines()) if screen else p.get_status(text)
    assert status == TerminalStatus.COMPLETED
    # Result extraction still requires the complete answer boundary, so the
    # service widens history rather than returning a partial answer as full.
    with pytest.raises(ValueError):
        p.extract_last_message_from_script(text)


def test_historical_thinking_capture_cannot_prove_public_final_channel():
    from pathlib import Path

    text = (
        Path(__file__).parent / "fixtures" / "opencode-v2-stale-duration-receipt.txt"
    ).read_text()
    p = provider()
    # Keep the authentic historical frame intact. Its flat Thinking preamble
    # does not prove where public output starts, even with a matching nonce.
    with pytest.raises(ValueError, match="final answer channel is ambiguous"):
        p.extract_last_message_from_script(text)


@pytest.mark.parametrize("wrap_contract", [False, True])
def test_duration_response_excludes_complete_multiline_delivery_contract(wrap_contract):
    import textwrap

    p = provider()
    prepared = p._format_turn_receipt_input(
        "RONDA2: Review this project.\n\nDo not edit files.", RECEIPT
    )
    if wrap_contract:
        prepared = "\n".join(textwrap.fill(line, width=73) for line in prepared.splitlines())
    answer = "RONDA2_DO\nNE — Findings sent via CAO send_message."
    text = (
        "› "
        + prepared
        + "\n\n"
        + answer
        + "\n"
        + RECEIPT
        + "\n\ndemo-frontend · GPT-6 Luna · 33.7s\n┃\ndemo-frontend · GPT-6 Luna · ctrl+p menu"
    )
    result = p.extract_last_message_from_script(text)
    assert result == answer + "\n" + RECEIPT
    assert "CAO completion receipt requirement" not in result
    assert p.receipt_result_terminal_status(text, result) == TerminalStatus.COMPLETED


def test_task_quoting_contract_end_cannot_turn_echoed_receipt_into_answer():
    import re

    p = OpenCodeCliProvider("offline", "offline", "offline")
    p._cli_major = 2
    p._resolve_native_status = lambda output: None
    sentence = "Do not quote or emit that receipt before the task is complete, and do not perform further tool calls after it."
    prepared = p.prepare_input(
        "Explain this quoted sentence: " + sentence + "\n\nKeep the explanation concise."
    )
    receipt = re.search(r"CAO-TURN-RECEIPT-[0-9a-f]{32}", prepared).group(0)
    echoed = prepared.replace(
        "exactly this receipt: " + receipt, "exactly this receipt:\n" + receipt
    )
    text = "› " + echoed + "\n\n┃\ndemo-frontend · GPT-6 Luna · ctrl+p menu"
    assert p.get_status(text) != TerminalStatus.COMPLETED
    with pytest.raises(ValueError):
        p.extract_last_message_from_script(text)


def test_native_demo_capture_excludes_multiline_delivery_contract():
    from pathlib import Path

    text = (
        Path(__file__).parent / "fixtures" / "opencode-v2-multiline-contract-real.txt"
    ).read_text()
    p = provider()
    result = p.extract_last_message_from_script(text)
    assert p.receipt_result_terminal_status(text, result) == TerminalStatus.COMPLETED
    assert result.rstrip().endswith(RECEIPT)
    assert "CAO completion receipt requirement" not in result
    assert "Do not quote or emit that receipt" not in result


@pytest.mark.parametrize("fragment", ["truncated", "quoted_full_contract"])
def test_incomplete_or_quoted_contract_cannot_verify_user_echo(fragment):
    import re

    p = OpenCodeCliProvider("offline", "offline", "offline")
    p._cli_major = 2
    p._resolve_native_status = lambda output: None
    sentence = "Do not quote or emit that receipt before the task is complete, and do not perform further tool calls after it."
    task = "Review the demo.\n\nKeep changes out of scope."
    if fragment == "quoted_full_contract":
        task += "\n\nQuoted text:\nCAO completion receipt requirement: quoted policy. " + sentence
    prepared = p.prepare_input(task)
    receipt = re.search(r"CAO-TURN-RECEIPT-[0-9a-f]{32}", prepared).group(0)
    echoed = prepared.replace(
        "exactly this receipt: " + receipt, "exactly this receipt:\n" + receipt
    )
    if fragment == "truncated":
        echoed = echoed[: echoed.rindex(sentence)]
    text = "› " + echoed + "\n\n┃\ndemo-frontend · GPT-6 Luna · ctrl+p menu"
    assert p.get_status(text) != TerminalStatus.COMPLETED
    with pytest.raises(ValueError):
        p.extract_last_message_from_script(text)


@pytest.mark.parametrize("quoted_nonce", [False, True])
@pytest.mark.parametrize("wrap_nonce", [False, True])
def test_assistant_contract_heading_does_not_erase_current_answer(quoted_nonce, wrap_nonce):
    p = provider()
    sentence = (
        "Do not quote or emit that receipt before the task is complete, "
        "and do not perform further tool calls after it."
    )
    quote = "CAO completion receipt requirement:"
    if quoted_nonce:
        quote += " receipt " + RECEIPT
    text = (
        "› Receive the message.\n\nCAO completion receipt requirement: receipt "
        + (RECEIPT[:25] + "\n" + RECEIPT[25:] if wrap_nonce else RECEIPT)
        + ". "
        + sentence
        + "\n\nThe phrase "
        + quote
        + " describes the protocol.\n\nACK\n"
        + RECEIPT
        + "\n\nagent · model · 1.0s\n┃\nagent · model · ctrl+p menu"
    )
    result = p.extract_last_message_from_script(text)
    assert result.endswith("ACK\n" + RECEIPT)
    assert quote in result
    assert p.receipt_result_terminal_status(text, result) == TerminalStatus.COMPLETED


def test_older_complete_contract_cannot_replace_incomplete_current_contract():
    p = provider()
    sentence = (
        "Do not quote or emit that receipt before the task is complete, "
        "and do not perform further tool calls after it."
    )
    older = "CAO-TURN-RECEIPT-" + "b" * 32
    text = (
        "› Explain this old quotation.\nCAO completion receipt requirement: "
        + older
        + ". "
        + sentence
        + "\n\nCAO completion receipt requirement: current receipt\n"
        + RECEIPT
        + "\n\n┃\nagent · model · ctrl+p menu"
    )
    assert p.get_status(text) != TerminalStatus.COMPLETED
    with pytest.raises(ValueError):
        p.extract_last_message_from_script(text)


def test_contract_matcher_does_not_promote_inline_result_receipt():
    p = provider()
    prepared = p._format_turn_receipt_input("Receive message.", RECEIPT)
    text = (
        "› " + prepared + "\n\nCAO completion receipt requirement: quoted."
        "\n\nACK with inline receipt "
        + RECEIPT
        + "\n\nagent · model · 1.0s\n┃\nagent · model · ctrl+p menu"
    )
    result = p.extract_last_message_from_script(text)
    assert p.receipt_result_terminal_status(text, result) is None


@pytest.mark.parametrize("receipt_count", [1, 2])
@pytest.mark.parametrize("duration", [False, True])
def test_flattened_thinking_receipt_cannot_be_promoted_to_final_answer(receipt_count, duration):
    p = provider()
    prepared = p._format_turn_receipt_input("Receive message.", RECEIPT)
    response = "Thinking: Consider the output protocol.\n\nMore reasoning.\n\n"
    response += ("ACK\n" + RECEIPT + "\n\n") * receipt_count
    text = "› " + prepared + "\n\n" + response
    if duration:
        text += "agent · model · 1.0s\n"
    text += "┃\nagent · model · ctrl+p menu"
    with pytest.raises(ValueError):
        p.extract_last_message_from_script(text)


def test_full_thinking_transcript_remains_visible_without_settling_receipt():
    from unittest.mock import patch

    from cli_agent_orchestrator.services import terminal_service as ts

    p = provider()
    prepared = p._format_turn_receipt_input("Receive message.", RECEIPT)
    text = "› " + prepared + "\n\nThinking: Example output.\n\nACK\n" + RECEIPT
    text += "\n\nagent · model · 1.0s\n┃\nagent · model · ctrl+p menu"
    with (
        patch(
            "cli_agent_orchestrator.services.remote_terminal_service.placement", return_value=None
        ),
        patch.object(
            ts,
            "get_terminal_metadata",
            return_value={"provider": "opencode", "tmux_session": "owned", "tmux_window": "owned"},
        ),
        patch.object(ts.status_monitor, "get_buffer", return_value=text),
        patch.object(ts.provider_manager, "get_provider", return_value=p),
        patch.object(ts, "get_backend") as backend,
        patch.object(ts, "settle_terminal_turn_receipt_result") as settle,
    ):
        backend.return_value.get_history.return_value = text
        assert ts.get_output("abcd1234", ts.OutputMode.FULL) == text
        settle.assert_not_called()
