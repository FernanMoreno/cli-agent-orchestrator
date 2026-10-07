"""Provider deny-all regressions (no CLI processes launched)."""

import logging
import shlex
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestSoftProvidersKeepAZeroToolInstruction:
    """The SOFT_ENFORCEMENT_PROVIDERS carry their policy as prompt text only.

    With ``[]`` the guard fell through, so the emitted prompt lost both the security
    preamble and the tool sentence while the launch stays unrestricted.
    """

    NO_TOOLS = "You may not use any tools."

    def test_codex_emits_it(self, tmp_path):
        from cli_agent_orchestrator.providers.codex import CodexProvider

        profile = MagicMock(
            model=None, system_prompt="Original.", mcpServers=None, codexProfile=None
        )
        provider = CodexProvider("tid", "sess", "win", "agent", allowed_tools=[])
        with (
            patch(
                "cli_agent_orchestrator.providers.codex.load_agent_profile", return_value=profile
            ),
            patch("cli_agent_orchestrator.providers.codex.CAO_HOME_DIR", tmp_path),
        ):
            command = provider._build_codex_command()
        path = Path(command.split("$(cat ")[1].split(")")[0])
        assert self.NO_TOOLS in path.read_text(encoding="utf-8")

    def test_omp_emits_it(self, tmp_path, monkeypatch):
        from cli_agent_orchestrator.models.agent_profile import AgentProfile
        from cli_agent_orchestrator.providers.omp import OmpProvider

        monkeypatch.setattr("cli_agent_orchestrator.providers.omp.CAO_HOME_DIR", tmp_path)
        profile = AgentProfile(name="analyst", description="t", system_prompt="Role")
        provider = OmpProvider(
            terminal_id="t",
            session_name="s",
            window_name="w",
            agent_profile="analyst",
            allowed_tools=[],
        )
        with (
            patch("cli_agent_orchestrator.providers.omp.shutil.which", return_value="/usr/bin/omp"),
            patch("cli_agent_orchestrator.providers.omp.load_agent_profile", return_value=profile),
        ):
            parts = shlex.split(provider._build_omp_command())
        context = Path(parts[parts.index("--append-system-prompt") + 1])
        assert self.NO_TOOLS in context.read_text(encoding="utf-8")

    def test_minimax_emits_it(self, tmp_path):
        from cli_agent_orchestrator.models.agent_profile import AgentProfile
        from cli_agent_orchestrator.providers.minimax_code import MiniMaxCodeProvider

        profile = AgentProfile(name="reviewer", description="t", system_prompt="Review.")
        provider = MiniMaxCodeProvider(
            terminal_id="deadbeef",
            session_name="s",
            window_name="w",
            agent_profile="reviewer",
            allowed_tools=[],
        )
        with (
            patch("cli_agent_orchestrator.providers.minimax_code.CAO_HOME_DIR", tmp_path),
            patch(
                "cli_agent_orchestrator.providers.minimax_code.load_agent_profile",
                return_value=profile,
            ),
            patch(
                "cli_agent_orchestrator.providers.minimax_code.shutil.which",
                return_value="/usr/local/bin/mcode",
            ),
        ):
            command = provider._build_command()
        assert self.NO_TOOLS in shlex.split(command)[4]

    def test_kimi_emits_it(self):
        from cli_agent_orchestrator.providers.kimi_cli import KimiCliProvider

        profile = MagicMock(model=None, system_prompt="Custom.", mcpServers=None)
        provider = KimiCliProvider("t", "s", "w", agent_profile="dev", allowed_tools=[])
        try:
            with patch(
                "cli_agent_orchestrator.providers.kimi_cli.load_agent_profile",
                return_value=profile,
            ):
                provider._build_kimi_command()
            system_md = Path(provider._temp_dir) / "system.md"
            assert self.NO_TOOLS in system_md.read_text(encoding="utf-8")
        finally:
            provider.cleanup()


class TestHermesWarnsOnADenyAll:
    """Hermes has no restriction mechanism, so the warning is the whole story.

    It is not in ``SOFT_ENFORCEMENT_PROVIDERS`` and was not in the review, but it
    carried the same guard, so a deny-all passed without telling the operator that
    nothing enforces it.
    """

    WARNING = "no CAO-native tool restriction flag"

    @staticmethod
    def _warnings(allowed, caplog):
        from cli_agent_orchestrator.providers.hermes import HermesProvider

        provider = HermesProvider("t", "s", "w", allowed_tools=allowed)
        with caplog.at_level(logging.WARNING):
            provider._build_hermes_command()
        return [r for r in caplog.records if TestHermesWarnsOnADenyAll.WARNING in r.getMessage()]

    def test_a_deny_all_warns(self, caplog):
        assert self._warnings([], caplog)

    def test_a_restricted_policy_still_warns(self, caplog):
        assert self._warnings(["fs_read"], caplog)

    def test_an_unrestricted_policy_does_not_warn(self, caplog):
        assert not self._warnings(["*"], caplog)

    def test_an_unresolved_policy_does_not_warn(self, caplog):
        assert not self._warnings(None, caplog)


def test_antigravity_denies_empty_tools_in_emitted_prompt():
    from cli_agent_orchestrator.models.agent_profile import AgentProfile
    from cli_agent_orchestrator.providers.antigravity_cli import AntigravityCliProvider

    profile = AgentProfile(name="restricted", description="test", system_prompt="Original.")
    provider = AntigravityCliProvider("t", "s", "w", "restricted", allowed_tools=[])
    with (
        patch(
            "cli_agent_orchestrator.providers.antigravity_cli.load_agent_profile",
            return_value=profile,
        ),
        patch(
            "cli_agent_orchestrator.providers.antigravity_cli.shutil.which", return_value="/bin/agy"
        ),
    ):
        parts = shlex.split(provider._build_agy_command())
    prompt = parts[parts.index("-i") + 1]
    assert "You may not use any tools." in prompt
    assert "Original." in prompt


@pytest.mark.parametrize("name", ["codex", "kimi_cli", "antigravity_cli"])
@pytest.mark.parametrize("allowed", [[], ["fs_read"], None, ["*"]])
def test_no_profile_still_emits_explicit_policy_and_keeps_unrestricted_control(
    name, allowed, tmp_path
):
    if name == "codex":
        from cli_agent_orchestrator.providers.codex import CodexProvider

        provider = CodexProvider("tid", "sess", "win", allowed_tools=allowed)
        with patch("cli_agent_orchestrator.providers.codex.CAO_HOME_DIR", tmp_path):
            provider._build_codex_command()
            prompt_path = provider._developer_instructions_file_path()
            prompt = prompt_path.read_text() if prompt_path.exists() else ""
    elif name == "kimi_cli":
        from cli_agent_orchestrator.providers.kimi_cli import KimiCliProvider

        provider = KimiCliProvider("t", "s", "w", allowed_tools=allowed)
        try:
            provider._build_kimi_command()
            prompt_path = Path(provider._temp_dir) / "system.md"
            prompt = prompt_path.read_text() if prompt_path.exists() else ""
        finally:
            provider.cleanup()
    else:
        from cli_agent_orchestrator.providers.antigravity_cli import AntigravityCliProvider

        provider = AntigravityCliProvider("t", "s", "w", allowed_tools=allowed)
        with patch(
            "cli_agent_orchestrator.providers.antigravity_cli.shutil.which", return_value="/bin/agy"
        ):
            parts = shlex.split(provider._build_agy_command())
        prompt = parts[parts.index("-i") + 1] if "-i" in parts else ""
    if allowed == []:
        assert "You may not use any tools." in prompt
    elif allowed == ["fs_read"]:
        assert "CAO allowed tool permissions: fs_read" in prompt
    else:
        assert "You may not use any tools." not in prompt
        assert "CAO allowed tool permissions:" not in prompt
