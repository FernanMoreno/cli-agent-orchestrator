"""CAO credentials go only to its own MCP, preserving explicit overrides."""

from cli_agent_orchestrator.utils import mcp_resolution


def test_cao_environment_inherits_only_named_context_and_preserves_override(monkeypatch):
    monkeypatch.setenv("CAO_AUTH_LOCAL_TOKEN", "private-value")
    monkeypatch.setenv("CAO_API_PORT", "12345")
    monkeypatch.setenv("UNRELATED_SECRET", "do-not-forward")
    config = {"command": "cao-mcp-server", "env": {"CAO_API_PORT": "23456"}}
    result = mcp_resolution.cao_mcp_environment("cao-mcp-server", config)
    assert result["CAO_API_PORT"] == "23456"
    assert result["CAO_AUTH_LOCAL_TOKEN"] == "private-value"
    assert "UNRELATED_SECRET" not in result
    assert config["env"] == {"CAO_API_PORT": "23456"}


def test_external_mcp_environment_receives_no_cao_credentials(monkeypatch):
    monkeypatch.setenv("CAO_AUTH_LOCAL_TOKEN", "private-value")
    assert mcp_resolution.cao_mcp_environment(
        "external", {"command": "third-party", "env": {"OWN": "value"}}
    ) == {"OWN": "value"}


def test_auth0_audience_is_forwarded_only_to_cao(monkeypatch):
    monkeypatch.setenv("AUTH0_AUDIENCE", "cao://custom")
    assert (
        mcp_resolution.cao_mcp_environment("cao-mcp-server", {})["AUTH0_AUDIENCE"] == "cao://custom"
    )


def test_codex_explicit_bearer_override_uses_private_file_not_argv(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from cli_agent_orchestrator.providers import codex
    from cli_agent_orchestrator.utils import atomic_file

    profile = MagicMock(model=None, system_prompt="", codexProfile=None)
    profile.mcpServers = {
        "cao-mcp-server": {
            "command": "cao-mcp-server",
            "env": {"CAO_AUTH_LOCAL_TOKEN": "explicit-private-override"},
        }
    }
    monkeypatch.setattr(codex, "load_agent_profile", lambda _: profile)
    monkeypatch.setattr(codex, "CAO_HOME_DIR", tmp_path)
    monkeypatch.setattr(atomic_file, "LOCK_DIR", tmp_path / "locks")
    monkeypatch.setenv("CAO_AUTH_LOCAL_TOKEN_FILE", "/private/inherited.jwt")
    provider = codex.CodexProvider("proof-terminal", "cao-proof", "worker", "proof")
    command = provider._build_codex_command()
    assert '"CAO_AUTH_LOCAL_TOKEN_FILE" = ""' in command
    assert "explicit-private-override" not in command
    private = tmp_path / "tmp/proof-terminal.mcp-bearer"
    assert private.read_text() == "explicit-private-override"
    assert private.stat().st_mode & 0o777 == 0o600
    provider.cleanup()
    assert not private.exists()


def test_renewable_source_reaches_only_cao(monkeypatch):
    monkeypatch.setenv("CAO_AUTH_LOCAL_TOKEN_FILE", "/private/mcp-bearer.jwt")
    assert (
        mcp_resolution.cao_mcp_environment("cao-mcp-server", {})["CAO_AUTH_LOCAL_TOKEN_FILE"]
        == "/private/mcp-bearer.jwt"
    )
    assert "CAO_AUTH_LOCAL_TOKEN_FILE" not in mcp_resolution.cao_mcp_environment(
        "external", {"command": "other"}
    )


def test_explicit_static_token_disables_inherited_source(monkeypatch):
    monkeypatch.setenv("CAO_AUTH_LOCAL_TOKEN_FILE", "/private/inherited.jwt")
    config = {"env": {"CAO_AUTH_LOCAL_TOKEN": "chosen-token"}}
    result = mcp_resolution.cao_mcp_environment("cao-mcp-server", config)
    assert result["CAO_AUTH_LOCAL_TOKEN_FILE"] == ""
    assert result["CAO_AUTH_LOCAL_TOKEN"] == "chosen-token"
    assert config == {"env": {"CAO_AUTH_LOCAL_TOKEN": "chosen-token"}}


def test_explicit_file_is_preserved(monkeypatch):
    monkeypatch.setenv("CAO_AUTH_LOCAL_TOKEN_FILE", "/private/inherited.jwt")
    config = {"env": {"CAO_AUTH_LOCAL_TOKEN_FILE": "/private/chosen.jwt"}}
    assert (
        mcp_resolution.cao_mcp_environment("cao-mcp-server", config)["CAO_AUTH_LOCAL_TOKEN_FILE"]
        == "/private/chosen.jwt"
    )
