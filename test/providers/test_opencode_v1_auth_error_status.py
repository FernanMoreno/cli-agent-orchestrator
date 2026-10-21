"""Current native-v1 auth failure, based on a sanitized real T054 viewport.

The fixture retains row geometry and error chrome; prompts/IDs/receipts/paths
were removed. These are detector contracts, not actual provider acceptance.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.opencode_cli import OpenCodeCliProvider

FIXTURE = Path(__file__).parent / "fixtures" / "opencode_v1_invalid_credential.txt"
ERROR = "Upstream request failed: Invalid credential"
FOOTER = "tab agents  ctrl+p commands    • OpenCode 1.18.35"
MODEL = "  ┃  V1-Probe · LongCat 2.5 Preview Free OpenCode Go"
COMPLETED = "▣  V1-Probe · LongCat 2.5 Preview Free · 7.2s"


def status(text: str, path: str, major: int = 1) -> TerminalStatus:
    provider = OpenCodeCliProvider("auth-test", "private-session", "window", "v1-probe")
    provider._cli_major = major
    with (
        patch.object(provider, "_resolve_native_status", return_value=None),
        patch.object(provider, "_resolve_buffer", side_effect=lambda value: value),
    ):
        if path == "raw":
            return provider.get_status(text)
        return provider.get_status_from_screen(text.splitlines())


@pytest.mark.parametrize("path", ["raw", "screen"])
def test_recorded_current_idle_credential_refusal_is_error(path):
    assert status(FIXTURE.read_text(), path) == TerminalStatus.ERROR


def test_raw_ansi_wrapped_current_error_is_error():
    text = FIXTURE.read_text().replace(ERROR, "\x1b[31m" + ERROR + "\x1b[0m")
    assert status(text, "raw") == TerminalStatus.ERROR


@pytest.mark.parametrize("path", ["raw", "screen"])
def test_old_completion_before_current_failure_does_not_hide_error(path):
    assert status(COMPLETED + "\n" + FIXTURE.read_text(), path) == TerminalStatus.ERROR


@pytest.mark.parametrize("path", ["raw", "screen"])
@pytest.mark.parametrize(
    "case",
    [
        "quoted",
        "body_only",
        "toast_only",
        "old_frame",
        "later_completion",
        "retrying",
        "active_footer",
        "other_provider",
        "no_model",
        "wrong_version",
        "no_version",
        "trailing_prose",
        "no_footer",
        "empty",
    ],
)
def test_prose_stale_completed_and_active_retry_do_not_become_auth_error(path, case):
    text = FIXTURE.read_text()
    if case == "quoted":
        text = f'┃  Discuss "{ERROR}"\n{MODEL}\n{FOOTER}'
    elif case == "body_only":
        text = "\n".join(
            row for row in text.splitlines() if not (ERROR in row and row.rstrip().endswith("┃"))
        )
    elif case == "toast_only":
        text = "\n".join(row for row in text.splitlines() if row.strip() != "┃  " + ERROR)
    elif case == "old_frame":
        text += f"\n{MODEL}\n{FOOTER}"
    elif case == "later_completion":
        text = text.replace("  [private cwd]", COMPLETED + "\n  [private cwd]")
    elif case == "retrying":
        text = text.replace(
            FOOTER, ERROR + " [retrying in 4s attempt #5] esc interrupt • OpenCode 1.18.35"
        )
    elif case == "active_footer":
        text = text.replace(FOOTER, "esc interrupt  " + FOOTER)
    elif case == "other_provider":
        text = text.replace("OpenCode Go", "Other provider")
    elif case == "no_model":
        text = text.replace(MODEL, "")
    elif case == "wrong_version":
        text = text.replace("OpenCode 1.18.35", "OpenCode 2.0.0")
    elif case == "no_version":
        text = text.replace("OpenCode 1.18.35", "OpenCode")
    elif case == "trailing_prose":
        text += "\nSome later text"
    elif case == "no_footer":
        text = text.replace(FOOTER, "")
    elif case == "empty":
        text = ""
    result = status(text, path)
    assert result != TerminalStatus.ERROR
    if case in {"retrying", "active_footer"}:
        assert result == TerminalStatus.PROCESSING
    if case == "later_completion":
        assert result == TerminalStatus.COMPLETED


@pytest.mark.parametrize("path", ["raw", "screen"])
def test_native_v1_error_shape_does_not_enable_v2_auth_detector(path):
    assert status(FIXTURE.read_text(), path, major=2) != TerminalStatus.ERROR


RETRY_FIXTURE = FIXTURE.with_name("opencode_v1_invalid_credential_retry.txt")


@pytest.mark.parametrize("path", ["raw", "screen"])
def test_recorded_current_permanent_credential_retry_is_error(path):
    assert status(RETRY_FIXTURE.read_text(), path) == TerminalStatus.ERROR


@pytest.mark.parametrize("path", ["raw", "screen"])
@pytest.mark.parametrize(
    "case",
    [
        "other_provider",
        "transient",
        "prose_prefix",
        "old_frame",
        "later_completion",
        "wrong_version",
    ],
)
def test_transient_or_noncurrent_retry_is_not_permanent_auth_error(path, case):
    text = RETRY_FIXTURE.read_text()
    if case == "other_provider":
        text = text.replace("OpenCode Go", "Other provider")
    elif case == "transient":
        text = text.replace("Invalid credential", "Service temporarily unavailable")
    elif case == "prose_prefix":
        text = text.replace("Upstream request failed:", "A user quoted Upstream request failed:")
    elif case == "old_frame":
        text += f"\n{MODEL}\n{FOOTER}"
    elif case == "later_completion":
        text += f"\n{COMPLETED}\n{MODEL}\n{FOOTER}"
    elif case == "wrong_version":
        text = text.replace("OpenCode 1.18.35", "OpenCode 2.0.0")
    assert status(text, path) != TerminalStatus.ERROR
    if case == "transient":
        assert status(text, path) == TerminalStatus.PROCESSING


@pytest.mark.parametrize("path", ["raw", "screen"])
def test_native_v1_permanent_retry_does_not_change_v2(path):
    assert status(RETRY_FIXTURE.read_text(), path, major=2) != TerminalStatus.ERROR


@pytest.mark.parametrize("path", ["raw", "screen"])
@pytest.mark.parametrize(
    "spinner", ["⬝" * 8, "■" * 8, "⬝" * 5 + "■" * 3, "■" * 3 + "⬝" * 5, "⬝■" * 4]
)
def test_permanent_refusal_is_error_across_native_block_animation(path, spinner):
    # Pinned upstream createFrames emits both inactive dots and active blocks.
    # A live T056-r2 intermediate viewport had the mixed five-dot/three-block frame.
    text = RETRY_FIXTURE.read_text().replace("⬝" * 8, spinner)
    assert status(text, path) == TerminalStatus.ERROR
