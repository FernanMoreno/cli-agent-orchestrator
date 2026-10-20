"""Runtime capability installation failure keeps source support truthful."""

import uuid
from test.postgres.interactions import postgres_interaction_store

import pytest

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.base import BaseProvider
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.interactions.service import InteractionForbidden
from cli_agent_orchestrator.services.terminal_service import (
    _install_interaction_source_capability,
    _interaction_capability_path,
)

pytestmark = pytest.mark.postgres


class _ProviderWithFailedInstall(BaseProvider):
    supports_interaction_mcp_overlay = True

    async def initialize(self):
        return True

    def get_status(self, buffer):
        return TerminalStatus.UNKNOWN

    def extract_last_message_from_script(self, script_output):
        return script_output

    def exit_cli(self):
        return "/exit"

    def cleanup(self):
        return None

    def set_interaction_capability_file(self, path):
        if path is not None:
            super().set_interaction_capability_file(path)
            raise OSError("injected provider configuration failure")
        super().set_interaction_capability_file(None)


class _ProviderForInstall(BaseProvider):
    supports_interaction_mcp_overlay = True

    async def initialize(self):
        return True

    def get_status(self, buffer):
        return TerminalStatus.UNKNOWN

    def extract_last_message_from_script(self, script_output):
        return script_output

    def exit_cli(self):
        return "/exit"

    def cleanup(self):
        return None


def test_private_file_install_failure_disables_only_explicit_capability(
    monkeypatch, tmp_path, postgres_interaction_store
):
    suffix = uuid.uuid4().hex[:10]
    terminal_id = "cap-install-" + suffix
    incarnation_id = "cap-inc-" + suffix
    session_name = "cap-session-" + suffix
    principal = auth._verified_principal(
        "https://test.issuer", "cap-owner-" + suffix, [auth.SCOPE_ADMIN], "jwt"
    )
    database.create_terminal(
        terminal_id,
        session_name,
        "worker",
        "codex",
        allowed_tools=["@cao-mcp-server"],
        session_incarnation_id=incarnation_id,
        new_session_incarnation=True,
    )
    service = postgres_interaction_store.service
    provider = _ProviderWithFailedInstall(terminal_id, session_name, "worker")
    token = []
    register_source = service.register_source

    def capture_token(**kwargs):
        issued = register_source(**kwargs)
        token.append(issued.token)
        return issued

    monkeypatch.setattr(service, "register_source", capture_token)
    monkeypatch.setattr(constants, "CAO_HOME_DIR", tmp_path / "cao-home")

    installed = _install_interaction_source_capability(
        provider,
        service,
        terminal_id=terminal_id,
        incarnation_id=incarnation_id,
        session_name=session_name,
        provider="codex",
        principal=principal,
    )

    assert installed is False
    assert provider.interaction_capability_file is None
    assert provider.interaction_capabilities["explicit_channel"] == "unsupported"
    overlaid = provider.interaction_mcp_servers(
        {
            "cao-mcp-server": {
                "command": "cao-mcp-server",
                "env": {"CAO_INTERACTION_CAPABILITY_FILE": "/profile/forged"},
            }
        }
    )
    assert "CAO_INTERACTION_CAPABILITY_FILE" not in overlaid["cao-mcp-server"]["env"]
    assert not _interaction_capability_path(terminal_id, incarnation_id).exists()

    status = service.source_status(terminal_id, incarnation_id)
    assert status.state == "current"
    assert status.capability_available is False
    assert service.capabilities(actor=principal)[0].emits_explicit is False
    with pytest.raises(InteractionForbidden):
        service.authenticate_agent(token[0])


def _source_install_fixture(monkeypatch, tmp_path, suffix, store):
    terminal_id = "cap-activation-" + suffix
    incarnation_id = "cap-activation-inc-" + suffix
    session_name = "cap-activation-session-" + suffix
    principal = auth._verified_principal(
        "https://test.issuer", "cap-activation-owner-" + suffix, [auth.SCOPE_ADMIN], "jwt"
    )
    database.create_terminal(
        terminal_id,
        session_name,
        "worker",
        "codex",
        allowed_tools=["@cao-mcp-server"],
        session_incarnation_id=incarnation_id,
        new_session_incarnation=True,
    )
    service = store.service
    provider = _ProviderForInstall(terminal_id, session_name, "worker")
    tokens = []
    register_source = service.register_source

    def capture_token(**kwargs):
        issued = register_source(**kwargs)
        tokens.append(issued.token)
        return issued

    monkeypatch.setattr(service, "register_source", capture_token)
    monkeypatch.setattr(constants, "CAO_HOME_DIR", tmp_path / "cao-home")
    return terminal_id, incarnation_id, session_name, principal, service, provider, tokens


def test_capability_activates_only_after_private_file_and_provider_path(
    monkeypatch, tmp_path, postgres_interaction_store
):
    suffix = uuid.uuid4().hex[:10]
    terminal_id, incarnation_id, session_name, principal, service, provider, tokens = (
        _source_install_fixture(monkeypatch, tmp_path, suffix, postgres_interaction_store)
    )
    activate = service.activate_capability

    def assert_installed_before_activation(source_terminal_id, source_incarnation_id, token):
        path = _interaction_capability_path(source_terminal_id, source_incarnation_id)
        assert source_terminal_id == terminal_id
        assert source_incarnation_id == incarnation_id
        assert path.read_text(encoding="ascii") == token
        assert path.stat().st_mode & 0o777 == 0o600
        assert provider.interaction_capability_file == str(path)
        return activate(source_terminal_id, source_incarnation_id, token)

    monkeypatch.setattr(service, "activate_capability", assert_installed_before_activation)

    installed = _install_interaction_source_capability(
        provider,
        service,
        terminal_id=terminal_id,
        incarnation_id=incarnation_id,
        session_name=session_name,
        provider="codex",
        principal=principal,
    )

    assert installed is True
    assert len(tokens) == 1
    status = service.source_status(terminal_id, incarnation_id)
    assert status.state == "current"
    assert status.capability_available is True
    assert service.capabilities(actor=principal)[0].emits_explicit is True
    assert service.authenticate_agent(tokens[0]).terminal_id == terminal_id


def test_activation_failure_remains_unavailable_if_unavailable_mark_fails(
    monkeypatch, tmp_path, postgres_interaction_store
):
    suffix = uuid.uuid4().hex[:10]
    terminal_id, incarnation_id, session_name, principal, service, provider, tokens = (
        _source_install_fixture(monkeypatch, tmp_path, suffix, postgres_interaction_store)
    )

    def fail_activation(*_args, **_kwargs):
        raise RuntimeError("injected activation failure")

    def fail_unavailable_mark(*_args, **_kwargs):
        raise RuntimeError("injected compensating update failure")

    monkeypatch.setattr(service, "activate_capability", fail_activation)
    monkeypatch.setattr(service, "mark_capability_unavailable", fail_unavailable_mark)

    installed = _install_interaction_source_capability(
        provider,
        service,
        terminal_id=terminal_id,
        incarnation_id=incarnation_id,
        session_name=session_name,
        provider="codex",
        principal=principal,
    )

    assert installed is False
    assert provider.interaction_capability_file is None
    assert not _interaction_capability_path(terminal_id, incarnation_id).exists()
    assert provider.interaction_capabilities["explicit_channel"] == "unsupported"
    assert service.capabilities(actor=principal)[0].emits_explicit is False
    assert len(tokens) == 1
    with pytest.raises(InteractionForbidden):
        service.authenticate_agent(tokens[0])
