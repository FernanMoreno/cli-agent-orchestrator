"""Tests for assign MCP tool."""

import os
from unittest.mock import MagicMock, patch

import pytest
import requests

from cli_agent_orchestrator.constants import API_BASE_URL
from cli_agent_orchestrator.mcp_server.server import _build_assign_description
from cli_agent_orchestrator.utils.orchestration import _mcp_timeout


class TestCreateTerminalProviderResolution:
    """Tests for provider resolution used by dispatched worker terminals."""

    @patch(
        "cli_agent_orchestrator.utils.orchestration._resolve_child_allowed_tools",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.utils.orchestration.resolve_provider", return_value="claude_code"
    )
    @patch("cli_agent_orchestrator.utils.orchestration.requests")
    def test_existing_session_respects_child_profile_provider(
        self, mock_requests, mock_resolve_provider, mock_allowed_tools
    ):
        """Worker profile provider should override the supervisor provider."""
        from cli_agent_orchestrator.utils.orchestration import _create_terminal

        metadata_response = MagicMock()
        metadata_response.json.return_value = {
            "provider": "kiro_cli",
            "session_name": "cao-session",
            "allowed_tools": None,
        }
        metadata_response.raise_for_status.return_value = None

        post_response = MagicMock()
        post_response.json.return_value = {"id": "worker-1", "provider": "claude_code"}
        post_response.raise_for_status.return_value = None

        mock_requests.get.return_value = metadata_response
        mock_requests.post.return_value = post_response

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "a1b2c3d4"}):
            terminal_id, provider = _create_terminal("reviewer", "/repo")

        assert terminal_id == "worker-1"
        assert provider == "claude_code"
        mock_resolve_provider.assert_called_once_with("reviewer", fallback_provider="kiro_cli")
        mock_requests.post.assert_called_once_with(
            f"{API_BASE_URL}/sessions/cao-session/terminals",
            params={
                "provider": "claude_code",
                "agent_profile": "reviewer",
                "caller_id": "a1b2c3d4",
                "working_directory": "/repo",
            },
            json=None,
            headers=None,
            allow_redirects=False,
            timeout=_mcp_timeout(),
        )

    @patch(
        "cli_agent_orchestrator.utils.orchestration._resolve_child_allowed_tools",
        return_value=None,
    )
    @patch("cli_agent_orchestrator.utils.orchestration.resolve_provider", return_value="kiro_cli")
    @patch("cli_agent_orchestrator.utils.orchestration.requests")
    def test_existing_session_falls_back_to_supervisor_provider(
        self, mock_requests, mock_resolve_provider, mock_allowed_tools
    ):
        """Worker without a provider should inherit the supervisor provider."""
        from cli_agent_orchestrator.utils.orchestration import _create_terminal

        metadata_response = MagicMock()
        metadata_response.json.return_value = {
            "provider": "kiro_cli",
            "session_name": "cao-session",
            "allowed_tools": None,
        }
        metadata_response.raise_for_status.return_value = None

        post_response = MagicMock()
        post_response.json.return_value = {"id": "worker-2", "provider": "kiro_cli"}
        post_response.raise_for_status.return_value = None

        mock_requests.get.return_value = metadata_response
        mock_requests.post.return_value = post_response

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "a1b2c3d4"}):
            terminal_id, provider = _create_terminal("reviewer", "/repo")

        assert terminal_id == "worker-2"
        assert provider == "kiro_cli"
        mock_resolve_provider.assert_called_once_with("reviewer", fallback_provider="kiro_cli")
        mock_requests.post.assert_called_once_with(
            f"{API_BASE_URL}/sessions/cao-session/terminals",
            params={
                "provider": "kiro_cli",
                "agent_profile": "reviewer",
                "caller_id": "a1b2c3d4",
                "working_directory": "/repo",
            },
            json=None,
            headers=None,
            allow_redirects=False,
            timeout=_mcp_timeout(),
        )

    @patch(
        "cli_agent_orchestrator.utils.orchestration._resolve_child_allowed_tools",
        return_value=None,
    )
    @patch("cli_agent_orchestrator.utils.orchestration.resolve_provider", return_value="mcode")
    @patch("cli_agent_orchestrator.utils.orchestration.requests")
    def test_mcode_worker_omits_kiro_engine_and_forwards_model(
        self, mock_requests, mock_resolve_provider, mock_allowed_tools
    ):
        """MCode workers omit Kiro-only engine but receive a terminal-local model."""
        from cli_agent_orchestrator.models.inbox import OrchestrationType
        from cli_agent_orchestrator.utils.orchestration import _create_terminal

        metadata_response = MagicMock()
        metadata_response.json.return_value = {
            "provider": "mcode",
            "engine": None,
            "session_name": "cao-session",
            "allowed_tools": None,
        }
        metadata_response.raise_for_status.return_value = None
        post_response = MagicMock()
        post_response.json.return_value = {"id": "worker-1", "provider": "mcode"}
        post_response.raise_for_status.return_value = None
        mock_requests.get.return_value = metadata_response
        mock_requests.post.return_value = post_response

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "a1b2c3d4"}):
            _create_terminal(
                "reviewer",
                working_directory=None,
                engine="v2",
                defer_init=True,
                initial_message="Analyze the sensitive logs at /secret/path",
                initial_message_orchestration_type=OrchestrationType.ASSIGN,
                model="MiniMax-M2.1",
            )

        _, kwargs = mock_requests.post.call_args
        assert kwargs["params"].get("defer_init") == "true"
        assert "engine" not in kwargs["params"]
        assert kwargs["params"]["model"] == "MiniMax-M2.1"
        assert "initial_message" not in kwargs["params"]
        assert kwargs["json"]["initial_message"] == "Analyze the sensitive logs at /secret/path"
        assert kwargs["json"]["initial_message_orchestration_type"] == "assign"

    @patch(
        "cli_agent_orchestrator.utils.orchestration._resolve_child_allowed_tools",
        return_value=None,
    )
    @patch("cli_agent_orchestrator.utils.orchestration.resolve_provider", return_value="kiro_cli")
    @patch("cli_agent_orchestrator.utils.orchestration.requests")
    def test_child_engine_is_explicit_not_inherited(
        self, mock_requests, _mock_resolve_provider, _mock_allowed_tools
    ):
        """A parent KAS value does not become an implicit child engine."""
        from cli_agent_orchestrator.utils.orchestration import _create_terminal

        metadata_response = MagicMock()
        metadata_response.json.return_value = {
            "provider": "kiro_cli",
            "engine": "kas",
            "session_name": "cao-session",
            "allowed_tools": None,
        }
        metadata_response.raise_for_status.return_value = None
        post_response = MagicMock()
        post_response.json.return_value = {"id": "worker-3", "provider": "kiro_cli"}
        post_response.raise_for_status.return_value = None
        mock_requests.get.return_value = metadata_response
        mock_requests.post.return_value = post_response

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "a1b2c3d4"}):
            _create_terminal("reviewer", "/repo")

        assert "engine" not in mock_requests.post.call_args.kwargs["params"]

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "a1b2c3d4"}):
            _create_terminal("reviewer", "/repo", engine="v2")

        assert mock_requests.post.call_args.kwargs["params"]["engine"] == "v2"

    @patch(
        "cli_agent_orchestrator.utils.orchestration.generate_session_name",
        return_value="cao-new-session",
    )
    @patch(
        "cli_agent_orchestrator.utils.orchestration.resolve_provider",
        return_value="codex",
    )
    @patch("cli_agent_orchestrator.utils.orchestration.requests")
    def test_new_session_forwards_model_and_initial_message(
        self, mock_requests, mock_resolve_provider, mock_generate_session_name
    ):
        """The no-current-terminal branch no longer drops either launch field."""
        from cli_agent_orchestrator.models.inbox import OrchestrationType
        from cli_agent_orchestrator.utils.orchestration import _create_terminal

        post_response = MagicMock()
        post_response.json.return_value = {"id": "worker-1", "provider": "codex"}
        post_response.raise_for_status.return_value = None
        mock_requests.post.return_value = post_response

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": ""}):
            terminal_id, provider = _create_terminal(
                "reviewer",
                defer_init=True,
                initial_message="Review the current change",
                initial_message_orchestration_type=OrchestrationType.ASSIGN,
                model="gpt-5.1-codex",
            )

        assert terminal_id == "worker-1"
        assert provider == "codex"
        mock_requests.post.assert_called_once_with(
            f"{API_BASE_URL}/sessions",
            params={
                "provider": "codex",
                "agent_profile": "reviewer",
                "session_name": "cao-new-session",
                "model": "gpt-5.1-codex",
            },
            json={
                "initial_message": "Review the current change",
                "initial_message_orchestration_type": "assign",
            },
            headers=None,
            allow_redirects=False,
            timeout=_mcp_timeout(),
        )

    @patch(
        "cli_agent_orchestrator.utils.orchestration.generate_session_name",
        return_value="cao-new-session",
    )
    @patch(
        "cli_agent_orchestrator.utils.orchestration.resolve_provider",
        return_value="codex",
    )
    @patch("cli_agent_orchestrator.utils.orchestration.requests")
    def test_new_session_initial_message_is_forwarded_without_defer_flag(
        self, mock_requests, mock_resolve_provider, mock_generate_session_name
    ):
        """An initial message cannot be dropped when defer_init keeps its default."""
        from cli_agent_orchestrator.utils.orchestration import _create_terminal

        post_response = MagicMock()
        post_response.json.return_value = {"id": "worker-1", "provider": "codex"}
        post_response.raise_for_status.return_value = None
        mock_requests.post.return_value = post_response

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": ""}):
            _create_terminal(
                "reviewer",
                initial_message="Review the current change",
            )

        mock_requests.post.assert_called_once_with(
            f"{API_BASE_URL}/sessions",
            params={
                "provider": "codex",
                "agent_profile": "reviewer",
                "session_name": "cao-new-session",
            },
            json={"initial_message": "Review the current change"},
            headers=None,
            allow_redirects=False,
            timeout=_mcp_timeout(),
        )

    @patch("cli_agent_orchestrator.utils.orchestration.requests")
    def test_defer_init_without_message_on_new_session_raises(self, mock_requests):
        """A bare defer flag still fails rather than changing semantics silently."""
        from cli_agent_orchestrator.utils.orchestration import _create_terminal

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": ""}):
            with pytest.raises(ValueError, match="defer_init requires initial_message"):
                _create_terminal("reviewer", defer_init=True)

        mock_requests.post.assert_not_called()

    @patch(
        "cli_agent_orchestrator.utils.orchestration._resolve_child_allowed_tools",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.utils.orchestration.resolve_provider", return_value="claude_code"
    )
    @patch("cli_agent_orchestrator.utils.orchestration.get_local_bearer", return_value="tok")
    @patch("cli_agent_orchestrator.utils.orchestration.requests")
    def test_attaches_bearer_when_auth_enabled(
        self, mock_requests, _bearer, mock_resolve_provider, mock_allowed_tools
    ):
        """Review on PR #634: both the metadata GET and the create POST carry
        the local bearer when configured -- covers the assign/handoff path."""
        from cli_agent_orchestrator.utils.orchestration import _create_terminal

        metadata_response = MagicMock()
        metadata_response.json.return_value = {
            "provider": "claude_code",
            "session_name": "cao-session",
            "allowed_tools": None,
        }
        metadata_response.raise_for_status.return_value = None
        post_response = MagicMock()
        post_response.json.return_value = {"id": "worker-1", "provider": "claude_code"}
        post_response.raise_for_status.return_value = None
        mock_requests.get.return_value = metadata_response
        mock_requests.post.return_value = post_response

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "a1b2c3d4"}):
            _create_terminal("reviewer", "/repo")

        assert mock_requests.get.call_args.kwargs["headers"] == {"Authorization": "Bearer tok"}
        assert mock_requests.post.call_args.kwargs["headers"] == {"Authorization": "Bearer tok"}


class TestCreateTerminalModelOverride:
    """_create_terminal's own `model` parameter -- an explicit per-call model
    override for the new terminal, forwarded to the existing-session POST as
    a params entry (see terminal_service.create_terminal's own docstring for
    how it wins over the profile's own static model field)."""

    @patch(
        "cli_agent_orchestrator.utils.orchestration._resolve_child_allowed_tools",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.utils.orchestration.resolve_provider", return_value="claude_code"
    )
    @patch("cli_agent_orchestrator.utils.orchestration.requests")
    def test_model_is_forwarded_as_a_param(
        self, mock_requests, mock_resolve_provider, mock_allowed_tools
    ):
        from cli_agent_orchestrator.utils.orchestration import _create_terminal

        metadata_response = MagicMock()
        metadata_response.json.return_value = {
            "provider": "kiro_cli",
            "session_name": "cao-session",
            "allowed_tools": None,
        }
        metadata_response.raise_for_status.return_value = None
        post_response = MagicMock()
        post_response.json.return_value = {"id": "worker-1", "provider": "claude_code"}
        post_response.raise_for_status.return_value = None
        mock_requests.get.return_value = metadata_response
        mock_requests.post.return_value = post_response

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "a1b2c3d4"}):
            _create_terminal("reviewer", "/repo", model="fable-5")

        _, kwargs = mock_requests.post.call_args
        assert kwargs["params"]["model"] == "fable-5"

    @patch(
        "cli_agent_orchestrator.utils.orchestration._resolve_child_allowed_tools",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.utils.orchestration.resolve_provider", return_value="claude_code"
    )
    @patch("cli_agent_orchestrator.utils.orchestration.requests")
    def test_omitted_model_leaves_params_unchanged(
        self, mock_requests, mock_resolve_provider, mock_allowed_tools
    ):
        """No model given -> params dict is byte-for-byte the pre-fix shape
        (no 'model' key at all) -- existing callers see zero behavior change."""
        from cli_agent_orchestrator.utils.orchestration import _create_terminal

        metadata_response = MagicMock()
        metadata_response.json.return_value = {
            "provider": "kiro_cli",
            "session_name": "cao-session",
            "allowed_tools": None,
        }
        metadata_response.raise_for_status.return_value = None
        post_response = MagicMock()
        post_response.json.return_value = {"id": "worker-1", "provider": "claude_code"}
        post_response.raise_for_status.return_value = None
        mock_requests.get.return_value = metadata_response
        mock_requests.post.return_value = post_response

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "a1b2c3d4"}):
            _create_terminal("reviewer", "/repo")

        _, kwargs = mock_requests.post.call_args
        assert "model" not in kwargs["params"]


class TestCreateTerminalUseWorktree:
    """issue #100 Phase 1: use_worktree is a routing flag, same shape as
    defer_init -- stays in query params (not the JSON body), and is only
    included when True (matching defer_init's own conditional-inclusion, not
    unconditional like run-step's JSON field -- a plain query string has no
    natural way to distinguish 'absent' from 'false' anyway)."""

    @patch(
        "cli_agent_orchestrator.utils.orchestration._resolve_child_allowed_tools",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.utils.orchestration.resolve_provider", return_value="claude_code"
    )
    @patch("cli_agent_orchestrator.utils.orchestration.requests")
    def test_use_worktree_true_is_included_in_params(
        self, mock_requests, mock_resolve_provider, mock_allowed_tools
    ):
        from cli_agent_orchestrator.utils.orchestration import _create_terminal

        metadata_response = MagicMock()
        metadata_response.json.return_value = {
            "provider": "kiro_cli",
            "session_name": "cao-session",
            "allowed_tools": None,
        }
        metadata_response.raise_for_status.return_value = None
        post_response = MagicMock()
        post_response.json.return_value = {"id": "worker-1", "provider": "claude_code"}
        post_response.raise_for_status.return_value = None
        mock_requests.get.return_value = metadata_response
        mock_requests.post.return_value = post_response

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "a1b2c3d4"}):
            _create_terminal("reviewer", "/repo", use_worktree=True)

        _, kwargs = mock_requests.post.call_args
        assert kwargs["params"]["use_worktree"] == "true"

    @patch(
        "cli_agent_orchestrator.utils.orchestration._resolve_child_allowed_tools",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.utils.orchestration.resolve_provider", return_value="claude_code"
    )
    @patch("cli_agent_orchestrator.utils.orchestration.requests")
    def test_use_worktree_false_is_omitted_from_params(
        self, mock_requests, mock_resolve_provider, mock_allowed_tools
    ):
        """Default False = today's exact behavior unchanged -- no new query
        param reaches the server for a caller that never mentions it."""
        from cli_agent_orchestrator.utils.orchestration import _create_terminal

        metadata_response = MagicMock()
        metadata_response.json.return_value = {
            "provider": "kiro_cli",
            "session_name": "cao-session",
            "allowed_tools": None,
        }
        metadata_response.raise_for_status.return_value = None
        post_response = MagicMock()
        post_response.json.return_value = {"id": "worker-1", "provider": "claude_code"}
        post_response.raise_for_status.return_value = None
        mock_requests.get.return_value = metadata_response
        mock_requests.post.return_value = post_response

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "a1b2c3d4"}):
            _create_terminal("reviewer", "/repo")

        _, kwargs = mock_requests.post.call_args
        assert "use_worktree" not in kwargs["params"]

    @patch(
        "cli_agent_orchestrator.utils.orchestration.generate_session_name",
        return_value="cao-new-session",
    )
    @patch(
        "cli_agent_orchestrator.utils.orchestration.resolve_provider",
        return_value="claude_code",
    )
    @patch("cli_agent_orchestrator.utils.orchestration.requests")
    def test_use_worktree_true_reaches_new_session_params_too(
        self, mock_requests, mock_resolve_provider, mock_generate_session_name
    ):
        """Regression (review on PR #634): a fresh-session caller (no
        CAO_TERMINAL_ID -- e.g. `cao agent handoff --use-worktree` run outside
        a CAO terminal) used to have use_worktree silently dropped, because
        only the existing-session branch above forwarded it. POST /sessions
        itself needed the same parameter its /sessions/{name}/terminals
        sibling already had."""
        from cli_agent_orchestrator.utils.orchestration import _create_terminal

        post_response = MagicMock()
        post_response.json.return_value = {"id": "worker-1", "provider": "claude_code"}
        post_response.raise_for_status.return_value = None
        mock_requests.post.return_value = post_response

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": ""}):
            _create_terminal("reviewer", use_worktree=True)

        _, kwargs = mock_requests.post.call_args
        assert kwargs["params"]["use_worktree"] == "true"

    @patch(
        "cli_agent_orchestrator.utils.orchestration.generate_session_name",
        return_value="cao-new-session",
    )
    @patch(
        "cli_agent_orchestrator.utils.orchestration.resolve_provider",
        return_value="claude_code",
    )
    @patch("cli_agent_orchestrator.utils.orchestration.requests")
    def test_use_worktree_false_omitted_from_new_session_params(
        self, mock_requests, mock_resolve_provider, mock_generate_session_name
    ):
        """Default False = today's exact behavior unchanged for the
        new-session branch too."""
        from cli_agent_orchestrator.utils.orchestration import _create_terminal

        post_response = MagicMock()
        post_response.json.return_value = {"id": "worker-1", "provider": "claude_code"}
        post_response.raise_for_status.return_value = None
        mock_requests.post.return_value = post_response

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": ""}):
            _create_terminal("reviewer")

        _, kwargs = mock_requests.post.call_args
        assert "use_worktree" not in kwargs["params"]

    @patch(
        "cli_agent_orchestrator.utils.orchestration._resolve_child_allowed_tools",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.utils.orchestration.resolve_provider", return_value="claude_code"
    )
    @patch("cli_agent_orchestrator.utils.orchestration.requests")
    def test_idempotency_key_reaches_existing_session_params(
        self, mock_requests, mock_resolve_provider, mock_allowed_tools
    ):
        """Review on PR #634, issue #616."""
        from cli_agent_orchestrator.utils.orchestration import _create_terminal

        metadata_response = MagicMock()
        metadata_response.json.return_value = {
            "provider": "claude_code",
            "session_name": "cao-session",
            "allowed_tools": None,
        }
        metadata_response.raise_for_status.return_value = None
        post_response = MagicMock()
        post_response.json.return_value = {"id": "worker-1", "provider": "claude_code"}
        post_response.raise_for_status.return_value = None
        mock_requests.get.return_value = metadata_response
        mock_requests.post.return_value = post_response

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "a1b2c3d4"}):
            _create_terminal("reviewer", "/repo", idempotency_key="retry-1")

        _, kwargs = mock_requests.post.call_args
        assert kwargs["params"]["idempotency_key"] == "retry-1"

    @patch(
        "cli_agent_orchestrator.utils.orchestration.generate_session_name",
        return_value="cao-new-session",
    )
    @patch(
        "cli_agent_orchestrator.utils.orchestration.resolve_provider",
        return_value="claude_code",
    )
    @patch("cli_agent_orchestrator.utils.orchestration.requests")
    def test_idempotency_key_reaches_new_session_params(
        self, mock_requests, mock_resolve_provider, mock_generate_session_name
    ):
        """Review on PR #634, issue #616."""
        from cli_agent_orchestrator.utils.orchestration import _create_terminal

        post_response = MagicMock()
        post_response.json.return_value = {"id": "worker-1", "provider": "claude_code"}
        post_response.raise_for_status.return_value = None
        mock_requests.post.return_value = post_response

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": ""}):
            _create_terminal("reviewer", idempotency_key="retry-1")

        _, kwargs = mock_requests.post.call_args
        assert kwargs["params"]["idempotency_key"] == "retry-1"

    @patch(
        "cli_agent_orchestrator.utils.orchestration.resolve_provider",
        return_value="claude_code",
    )
    @patch("cli_agent_orchestrator.utils.orchestration.requests")
    def test_two_keyed_fresh_session_retries_send_the_same_session_name(
        self, mock_requests, mock_resolve_provider
    ):
        """A keyed retry outside a CAO terminal must reattach, not 409.

        Review on PR #634. The test above pins ``generate_session_name`` to one
        value and calls once, so it cannot see this: the server fingerprints
        ``session_name``, and this branch used to mint a fresh uuid4 per
        invocation, so two retries of the same command produced DIFFERENT
        fingerprints and the retry conflicted with its own first attempt.

        Deliberately does NOT patch the name generator -- pinning it is exactly
        what hid the bug. Two real calls, and the session name they send must
        match.
        """
        from cli_agent_orchestrator.utils.orchestration import _create_terminal

        post_response = MagicMock()
        post_response.json.return_value = {"id": "worker-1", "provider": "claude_code"}
        post_response.raise_for_status.return_value = None
        mock_requests.post.return_value = post_response

        sent = []
        with patch.dict(os.environ, {"CAO_TERMINAL_ID": ""}):
            for _ in range(2):
                _create_terminal("reviewer", idempotency_key="retry-1")
                sent.append(mock_requests.post.call_args.kwargs["params"]["session_name"])

        assert sent[0] == sent[1], "a keyed retry must re-send the same session_name"

        # A DIFFERENT key must still get its own session, or one key would
        # capture every fresh-session request on the node.
        with patch.dict(os.environ, {"CAO_TERMINAL_ID": ""}):
            _create_terminal("reviewer", idempotency_key="retry-2")
        assert mock_requests.post.call_args.kwargs["params"]["session_name"] != sent[0]

    @patch(
        "cli_agent_orchestrator.utils.orchestration.resolve_provider",
        return_value="claude_code",
    )
    @patch("cli_agent_orchestrator.utils.orchestration.requests")
    def test_unkeyed_fresh_sessions_still_get_unique_names(
        self, mock_requests, mock_resolve_provider
    ):
        """No key means the old uuid4 behaviour, unchanged.

        Pinned because deriving the name from the key must not leak into the
        default path: two unkeyed handoffs are unrelated requests and sharing a
        session name would collide them.
        """
        from cli_agent_orchestrator.utils.orchestration import _create_terminal

        post_response = MagicMock()
        post_response.json.return_value = {"id": "worker-1", "provider": "claude_code"}
        post_response.raise_for_status.return_value = None
        mock_requests.post.return_value = post_response

        sent = []
        with patch.dict(os.environ, {"CAO_TERMINAL_ID": ""}):
            for _ in range(2):
                _create_terminal("reviewer")
                sent.append(mock_requests.post.call_args.kwargs["params"]["session_name"])

        assert sent[0] != sent[1]

    @patch("cli_agent_orchestrator.mcp_server.server._assign_impl")
    def test_assign_tool_forwards_use_worktree_to_impl(self, mock_impl):
        """The public `assign` MCP tool itself threads use_worktree through to
        _assign_impl -- both the workdir-enabled and disabled variants.

        _assign_impl's non-message/working_directory args (engine, model,
        use_worktree) are forwarded as keywords (see server.py's assign()),
        not positionally -- assert via kwargs rather than a positional index.

        Patches ``mcp_server.server._assign_impl`` (NOT
        ``utils.orchestration._assign_impl``): the `assign` tool wrapper still
        lives in server.py and resolves the name from ITS OWN module globals
        (populated by server.py's own `from utils.orchestration import
        _assign_impl`), so that is the reference that must be replaced for
        this call to observe the mock.
        """
        from cli_agent_orchestrator.mcp_server import server as server_module

        mock_impl.return_value = {"success": True, "terminal_id": "w1", "message": "ok"}

        import asyncio

        asyncio.run(
            server_module.assign(agent_profile="reviewer", message="do it", use_worktree=True)
        )

        _, kwargs = mock_impl.call_args
        assert kwargs["use_worktree"] is True


class TestAssignSenderIdInjection:
    """Callback composition passes through the durable assignment API."""

    @pytest.fixture(autouse=True)
    def durable_transport(self):
        with patch("cli_agent_orchestrator.utils.orchestration.requests") as transport:
            transport.HTTPError = requests.HTTPError
            transport.get.return_value.json.return_value = {"generation": 7}
            transport.post.return_value.json.return_value = {
                "assignment_id": "assignment-1",
                "terminal_id": "worker-1",
                "success": True,
                "state": "admitted",
                "message": "Worker initializing; message delivery pending.",
            }
            self.transport = transport
            yield

    def assign(self, message="Analyze the logs", **kwargs):
        from cli_agent_orchestrator.utils.orchestration import _assign_impl

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "a1b2c3d4"}):
            return _assign_impl("developer", message, **kwargs)

    @patch("cli_agent_orchestrator.utils.orchestration.ENABLE_SENDER_ID_INJECTION", True)
    def test_assign_appends_sender_id_when_injection_enabled(self):
        result = self.assign()
        assert result["success"] is True
        sent = self.transport.post.call_args.kwargs["json"]
        assert sent["message"].startswith("Analyze the logs")
        assert "[Assigned by terminal a1b2c3d4" in sent["message"]
        assert "send results back to terminal a1b2c3d4 using send_message]" in sent["message"]
        assert sent["generation"] == 7
        assert sent["operation_key"] == result["operation_key"]
        assert self.transport.post.call_args.args[0].endswith("/terminals/a1b2c3d4/assignments")

    @patch("cli_agent_orchestrator.utils.orchestration.ENABLE_SENDER_ID_INJECTION", False)
    def test_assign_no_suffix_when_injection_disabled(self):
        assert self.assign()["success"] is True
        assert self.transport.post.call_args.kwargs["json"]["message"] == "Analyze the logs"

    @pytest.mark.parametrize("injection", [True, False])
    def test_assign_missing_terminal_id_errors_before_creating_terminal(self, injection):
        from cli_agent_orchestrator.utils.orchestration import _assign_impl

        with patch(
            "cli_agent_orchestrator.utils.orchestration.ENABLE_SENDER_ID_INJECTION", injection
        ):
            with patch.dict(os.environ, {}, clear=True):
                result = _assign_impl("developer", "Build feature X")
        assert result["success"] is False
        assert result["terminal_id"] is None
        assert "CAO_TERMINAL_ID not set" in result["message"]
        self.transport.get.assert_not_called()
        self.transport.post.assert_not_called()

    def test_assign_surfaces_terminal_id_when_create_fails(self):
        self.transport.post.side_effect = requests.ConnectionError("connection refused")
        result = self.assign()
        assert result["success"] is False
        assert result["terminal_id"] is None
        assert result["state"] == "reconcile"
        assert result["operation_key"]
        assert self.transport.post.call_count == 1

    @patch("cli_agent_orchestrator.utils.orchestration.ENABLE_SENDER_ID_INJECTION", True)
    def test_assign_suffix_is_appended_not_prepended(self):
        original = "Do the task described in /path/to/task.md"
        self.assign(original)
        sent = self.transport.post.call_args.kwargs["json"]["message"]
        assert sent.startswith(original)
        assert sent.index("[Assigned by terminal") > len(original)

    def test_assign_returns_fast_success_message(self):
        result = self.assign("Do work")
        assert result["success"] is True
        assert result["terminal_id"] == "worker-1"
        assert "initializing" in result["message"].lower()

    def test_assign_forwards_model_to_create_terminal(self):
        assert self.assign("Do work", model="fable-5")["success"] is True
        assert self.transport.post.call_args.kwargs["json"]["model"] == "fable-5"

    def test_assign_omitted_model_passes_none(self):
        self.assign("Do work")
        assert self.transport.post.call_args.kwargs["json"]["model"] is None

    def test_response_loss_reuses_same_authoritative_identity(self):
        self.transport.post.side_effect = requests.ConnectionError("response lost")
        first = self.assign()
        second = self.assign()
        assert first["operation_key"] == second["operation_key"]
        assert first["state"] == second["state"] == "reconcile"

    def test_missing_generation_requires_explicit_stable_key(self):
        self.transport.get.return_value.json.return_value = {"generation": None}
        result = self.assign()
        assert result["error_kind"] == "assignment_operation_key_required"
        self.transport.post.assert_not_called()
        assert self.assign(operation_key="caller-stable-key")["success"] is True
        assert self.transport.post.call_args.kwargs["json"]["operation_key"] == "caller-stable-key"


class TestBuildAssignDescription:
    """Tests for the _build_assign_description helper.

    Covers all four combinations of (enable_sender_id, enable_workdir) flags.
    """

    # ------------------------------------------------------------------
    # Shared content assertions
    # ------------------------------------------------------------------

    def test_always_starts_with_action_sentence(self):
        """All combinations begin with the same one-liner action summary."""
        for sender_id in (True, False):
            for workdir in (True, False):
                desc = _build_assign_description(sender_id, workdir)
                assert desc.startswith("Assigns a task to another agent without blocking.")

    def test_always_contains_args_section(self):
        """All combinations include an Args section with agent_profile and message."""
        for sender_id in (True, False):
            for workdir in (True, False):
                desc = _build_assign_description(sender_id, workdir)
                assert "Args:" in desc
                assert "agent_profile:" in desc
                assert "message:" in desc

    def test_always_contains_returns_section(self):
        """All combinations include a Returns section."""
        for sender_id in (True, False):
            for workdir in (True, False):
                desc = _build_assign_description(sender_id, workdir)
                assert "Returns:" in desc
                assert "Dict with success status" in desc

    # ------------------------------------------------------------------
    # Sender ID injection flag
    # ------------------------------------------------------------------

    def test_sender_id_enabled_uses_auto_injection_overview(self):
        """When sender ID injection is on, overview says ID is automatically appended."""
        desc = _build_assign_description(enable_sender_id=True, enable_workdir=False)
        assert "automatically be appended" in desc

    def test_sender_id_enabled_omits_manual_callback_instructions(self):
        """When injection is on, no manual CAO_TERMINAL_ID instructions are included."""
        desc = _build_assign_description(enable_sender_id=True, enable_workdir=False)
        assert "CAO_TERMINAL_ID" not in desc
        assert "send results back" not in desc

    def test_sender_id_disabled_includes_manual_callback_instructions(self):
        """When injection is off, the description instructs the caller to include callback info."""
        desc = _build_assign_description(enable_sender_id=False, enable_workdir=False)
        assert "CAO_TERMINAL_ID" in desc
        assert "send results back" in desc
        assert "Example message:" in desc

    def test_sender_id_disabled_omits_auto_injection_mention(self):
        """When injection is off, no mention of automatic appending."""
        desc = _build_assign_description(enable_sender_id=False, enable_workdir=False)
        assert "automatically be appended" not in desc

    # ------------------------------------------------------------------
    # Working directory flag
    # ------------------------------------------------------------------

    def test_workdir_enabled_includes_working_directory_section(self):
        """When workdir is enabled, a '## Working Directory' section is present."""
        desc = _build_assign_description(enable_sender_id=False, enable_workdir=True)
        assert "## Working Directory" in desc
        assert "supervisor's current working directory" in desc

    def test_workdir_enabled_includes_working_directory_arg(self):
        """When workdir is on, working_directory appears in the Args section."""
        desc = _build_assign_description(enable_sender_id=False, enable_workdir=True)
        assert "working_directory:" in desc

    def test_workdir_disabled_omits_working_directory_section(self):
        """When workdir is off, no Working Directory section."""
        desc = _build_assign_description(enable_sender_id=False, enable_workdir=False)
        assert "## Working Directory" not in desc

    def test_workdir_disabled_omits_working_directory_arg(self):
        """When workdir is off, working_directory does not appear in Args."""
        desc = _build_assign_description(enable_sender_id=False, enable_workdir=False)
        assert "working_directory:" not in desc

    # ------------------------------------------------------------------
    # Model section (unconditional -- not gated on any flag)
    # ------------------------------------------------------------------

    def test_model_section_and_arg_always_present(self):
        """Unlike working_directory, the Model section/arg isn't feature-
        flagged -- present in all four combinations."""
        for sender_id in (True, False):
            for workdir in (True, False):
                desc = _build_assign_description(sender_id, workdir)
                assert "## Model" in desc
                assert "model:" in desc

    # ------------------------------------------------------------------
    # All four flag combinations
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "enable_sender_id, enable_workdir",
        [
            (False, False),
            (False, True),
            (True, False),
            (True, True),
        ],
    )
    def test_returns_non_empty_string(self, enable_sender_id, enable_workdir):
        """All combinations produce a non-empty string."""
        desc = _build_assign_description(enable_sender_id, enable_workdir)
        assert isinstance(desc, str)
        assert len(desc) > 0

    def test_sender_id_true_workdir_true(self):
        """Both flags on: auto-injection overview + Working Directory section present."""
        desc = _build_assign_description(enable_sender_id=True, enable_workdir=True)
        assert "automatically be appended" in desc
        assert "## Working Directory" in desc
        assert "working_directory:" in desc
        assert "CAO_TERMINAL_ID" not in desc

    def test_sender_id_true_workdir_false(self):
        """Injection on, workdir off: no Working Directory section."""
        desc = _build_assign_description(enable_sender_id=True, enable_workdir=False)
        assert "automatically be appended" in desc
        assert "## Working Directory" not in desc
        assert "working_directory:" not in desc

    def test_sender_id_false_workdir_true(self):
        """Injection off, workdir on: manual callback instructions + Working Directory."""
        desc = _build_assign_description(enable_sender_id=False, enable_workdir=True)
        assert "CAO_TERMINAL_ID" in desc
        assert "## Working Directory" in desc
        assert "working_directory:" in desc

    def test_sender_id_false_workdir_false(self):
        """Both flags off: manual callback instructions, no Working Directory section."""
        desc = _build_assign_description(enable_sender_id=False, enable_workdir=False)
        assert "CAO_TERMINAL_ID" in desc
        assert "## Working Directory" not in desc
        assert "working_directory:" not in desc

    # ------------------------------------------------------------------
    # Structural ordering
    # ------------------------------------------------------------------

    def test_args_section_appears_after_overview(self):
        """The Args section should come after the overview text."""
        desc = _build_assign_description(enable_sender_id=False, enable_workdir=False)
        overview_pos = desc.index("Assigns a task")
        args_pos = desc.index("Args:")
        assert overview_pos < args_pos

    def test_working_directory_section_appears_before_args(self):
        """The Working Directory section should come before the Args section."""
        desc = _build_assign_description(enable_sender_id=False, enable_workdir=True)
        workdir_pos = desc.index("## Working Directory")
        args_pos = desc.index("Args:")
        assert workdir_pos < args_pos

    def test_returns_section_appears_after_args(self):
        """The Returns section should come after the Args section."""
        desc = _build_assign_description(enable_sender_id=False, enable_workdir=False)
        args_pos = desc.index("Args:")
        returns_pos = desc.index("Returns:")
        assert args_pos < returns_pos
