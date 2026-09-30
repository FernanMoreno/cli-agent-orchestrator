"""Provider manager as module singleton with direct terminal_id → provider mapping."""

import logging
from typing import Dict, List, Optional

from cli_agent_orchestrator.clients.database import (
    get_terminal_metadata,
    get_terminal_turn_receipt,
    settle_terminal_turn_receipt_result,
)
from cli_agent_orchestrator.models.kiro_engine import KiroEngine, resolve_kiro_engine
from cli_agent_orchestrator.models.provider import ProviderType
from cli_agent_orchestrator.providers.antigravity_cli import AntigravityCliProvider
from cli_agent_orchestrator.providers.base import BaseProvider
from cli_agent_orchestrator.providers.catalog import registered_provider_descriptor
from cli_agent_orchestrator.providers.claude_code import ClaudeCodeProvider
from cli_agent_orchestrator.providers.codex import CodexProvider
from cli_agent_orchestrator.providers.copilot_cli import CopilotCliProvider
from cli_agent_orchestrator.providers.cursor_cli import CursorCliProvider
from cli_agent_orchestrator.providers.gemini_cli import GeminiCliProvider
from cli_agent_orchestrator.providers.grok_cli import GrokCliProvider
from cli_agent_orchestrator.providers.hermes import HermesProvider
from cli_agent_orchestrator.providers.kimi_cli import KimiCliProvider
from cli_agent_orchestrator.providers.kiro_capabilities import KiroPhase0KASError
from cli_agent_orchestrator.providers.kiro_cli import KiroCliProvider
from cli_agent_orchestrator.providers.minimax_code import MiniMaxCodeProvider
from cli_agent_orchestrator.providers.mock_cli import MockCliProvider
from cli_agent_orchestrator.providers.omp import OmpProvider
from cli_agent_orchestrator.providers.opencode_cli import OpenCodeCliProvider

logger = logging.getLogger(__name__)


class ProviderManager:
    """Simplified provider manager with direct mapping."""

    def __init__(self) -> None:
        self._providers: Dict[str, BaseProvider] = {}

    def create_provider(
        self,
        provider_type: str,
        terminal_id: str,
        tmux_session: str,
        tmux_window: str,
        agent_profile: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
        skill_prompt: Optional[str] = None,
        model: Optional[str] = None,
        engine: Optional[KiroEngine] = None,
        resume_session_id: Optional[str] = None,
    ) -> BaseProvider:
        """Create and store provider instance."""
        try:
            provider: BaseProvider
            descriptor = registered_provider_descriptor(provider_type)
            if descriptor is not None:
                descriptor.preflight("launch", model=model)
            if resume_session_id and provider_type != ProviderType.CLAUDE_CODE.value:
                raise ValueError(
                    "resume_session_id is only supported by the claude_code provider "
                    f"(got provider '{provider_type}')"
                )
            if provider_type == ProviderType.KIRO_CLI.value:
                if not agent_profile:
                    raise ValueError("Kiro CLI provider requires agent_profile parameter")
                resolved_engine = resolve_kiro_engine(persisted=engine)
                if resolved_engine == KiroEngine.KAS:
                    raise KiroPhase0KASError(profile_has_v2_policy=False)
                provider = KiroCliProvider(
                    terminal_id,
                    tmux_session,
                    tmux_window,
                    agent_profile,
                    allowed_tools,
                    engine=resolved_engine,
                    model=model,
                )
            elif provider_type == ProviderType.CLAUDE_CODE.value:
                provider = ClaudeCodeProvider(
                    terminal_id,
                    tmux_session,
                    tmux_window,
                    agent_profile,
                    allowed_tools,
                    skill_prompt=skill_prompt,
                    model=model,
                    resume_session_id=resume_session_id,
                )
            elif provider_type == ProviderType.CODEX.value:
                provider = CodexProvider(
                    terminal_id,
                    tmux_session,
                    tmux_window,
                    agent_profile,
                    allowed_tools,
                    skill_prompt=skill_prompt,
                    model=model,
                )
            elif provider_type == ProviderType.COPILOT_CLI.value:
                provider = CopilotCliProvider(
                    terminal_id,
                    tmux_session,
                    tmux_window,
                    agent_profile,
                    allowed_tools,
                    model=model,
                )
            elif provider_type == ProviderType.KIMI_CLI.value:
                provider = KimiCliProvider(
                    terminal_id,
                    tmux_session,
                    tmux_window,
                    agent_profile,
                    allowed_tools,
                    skill_prompt=skill_prompt,
                    model=model,
                )
            elif provider_type == ProviderType.OPENCODE_CLI.value:
                provider = OpenCodeCliProvider(
                    terminal_id,
                    tmux_session,
                    tmux_window,
                    agent_profile,
                    allowed_tools,
                    model=model,
                )
            elif provider_type == ProviderType.OMP.value:
                provider = OmpProvider(
                    terminal_id,
                    tmux_session,
                    tmux_window,
                    agent_profile,
                    allowed_tools,
                    skill_prompt=skill_prompt,
                    model=model,
                )
            elif provider_type == ProviderType.HERMES.value:
                provider = HermesProvider(
                    terminal_id,
                    tmux_session,
                    tmux_window,
                    agent_profile,
                    allowed_tools,
                    skill_prompt=skill_prompt,
                    model=model,
                )
            elif provider_type == ProviderType.CURSOR_CLI.value:
                provider = CursorCliProvider(
                    terminal_id,
                    tmux_session,
                    tmux_window,
                    agent_profile,
                    allowed_tools,
                    model=model,
                    skill_prompt=skill_prompt,
                )
            elif provider_type == ProviderType.ANTIGRAVITY_CLI.value:
                provider = AntigravityCliProvider(
                    terminal_id,
                    tmux_session,
                    tmux_window,
                    agent_profile,
                    allowed_tools,
                    model=model,
                    skill_prompt=skill_prompt,
                )
            elif provider_type == ProviderType.GEMINI_CLI.value:
                provider = GeminiCliProvider(
                    terminal_id,
                    tmux_session,
                    tmux_window,
                    agent_profile,
                    allowed_tools,
                    skill_prompt=skill_prompt,
                    model=model,
                )
            elif provider_type == ProviderType.GROK_CLI.value:
                provider = GrokCliProvider(
                    terminal_id,
                    tmux_session,
                    tmux_window,
                    agent_profile,
                    allowed_tools,
                    skill_prompt=skill_prompt,
                    model=model,
                )
            elif provider_type == ProviderType.MINIMAX_CODE.value:
                provider = MiniMaxCodeProvider(
                    terminal_id,
                    tmux_session,
                    tmux_window,
                    agent_profile,
                    allowed_tools,
                    skill_prompt=skill_prompt,
                    model=model,
                )
            # --- Credentials-free mock provider (test/CI infrastructure) ---
            elif provider_type == ProviderType.MOCK_CLI.value:
                provider = MockCliProvider(
                    terminal_id,
                    tmux_session,
                    tmux_window,
                    allowed_tools,
                )
            else:
                raise ValueError(f"Unknown provider type: {provider_type}")

            # Store in direct mapping
            self._providers[terminal_id] = provider
            logger.info(f"Created {provider_type} provider for terminal: {terminal_id}")
            return provider

        except Exception as e:
            logger.error(
                f"Failed to create provider {provider_type} for terminal {terminal_id}: {e}"
            )
            raise

    def get_provider(self, terminal_id: str) -> Optional[BaseProvider]:
        """Get provider instance, creating on-demand if not found.

        Args:
            terminal_id: Terminal ID to get provider for

        Returns:
            Provider instance

        Raises:
            ValueError: If terminal not found in database or provider creation fails
        """
        # Check if already exists
        provider = self._providers.get(terminal_id)
        if provider:
            if (
                isinstance(provider, KiroCliProvider)
                and getattr(provider, "_engine", None) == KiroEngine.KAS
            ):
                raise KiroPhase0KASError(profile_has_v2_policy=False)
            return provider

        # Try to create on-demand from database metadata
        metadata = get_terminal_metadata(terminal_id)
        if not metadata:
            raise ValueError(f"Terminal {terminal_id} not found in database")

        persisted_engine = (
            resolve_kiro_engine(persisted=metadata.get("engine"))
            if metadata["provider"] == ProviderType.KIRO_CLI.value
            else None
        )
        if persisted_engine == KiroEngine.KAS:
            raise KiroPhase0KASError(profile_has_v2_policy=False)

        # Create provider on-demand
        provider = self.create_provider(
            metadata["provider"],
            terminal_id,
            metadata["tmux_session"],
            metadata["tmux_window"],
            metadata["agent_profile"],
            allowed_tools=metadata.get("allowed_tools"),
            engine=persisted_engine,
        )
        if getattr(provider, "requires_turn_receipt", False) is True:
            # Receipt state is private database state, intentionally separate
            # from the agent-editable terminal metadata.  A restart has no
            # plaintext nonce or task body to redeliver, so an active record
            # restores as reconcile/block until CAO verifies its result.
            turn_receipt = get_terminal_turn_receipt(terminal_id)
            if turn_receipt is not None and turn_receipt.get("phase") in {"prepared", "sent"}:
                provider.restore_turn_receipt_state(turn_receipt)
            elif (
                turn_receipt is not None
                and turn_receipt.get("phase") == "result_verified"
                and isinstance(turn_receipt.get("result_sha256"), str)
            ):
                # A server can crash after an older split receipt-verification
                # commit but before projecting its native child success. New
                # result settlement is atomic, yet repair that historic
                # durable state on provider reconstruction too. The helper
                # refuses to revive cancelled/failed children or a removed
                # terminal, so this is proof-based recovery rather than an
                # inferred completion.
                settled = settle_terminal_turn_receipt_result(
                    terminal_id,
                    turn_receipt["generation"],
                    turn_receipt["receipt_sha256"],
                    turn_receipt["result_sha256"],
                )
                if not settled:
                    logger.warning(
                        "Verified receipt for terminal %s could not safely project completion",
                        terminal_id,
                    )
        # Restore shell_command baseline from DB so get_status() can detect kiro exit.
        # The terminal already exists in the DB, so its CLI has long since
        # launched — mark the provider as initialized so KiroCliProvider's
        # post-launch checks (Check 3) trust the restored baseline. Without
        # this, a restored terminal that has returned to the shell would be
        # misreported as PROCESSING indefinitely.
        if metadata.get("shell_command"):
            provider.shell_baseline = metadata["shell_command"]
            if hasattr(provider, "_initialized"):
                provider._initialized = True
        logger.info(f"Created provider on-demand for terminal {terminal_id}")
        return provider

    def cleanup_provider(self, terminal_id: str) -> bool:
        """Cleanup a provider, retaining any retryable private state on failure.

        A provider returning ``False`` deliberately keeps its map entry and
        lets the service retain DB metadata.  That metadata is the only
        durable route to retry deterministic provider-private cleanup after a
        terminal or server restart.
        """
        try:
            provider = self._providers.get(terminal_id)
            if provider:
                cleanup_result = provider.cleanup()
                if cleanup_result is False:
                    logger.warning("Cleanup deferred for terminal: %s", terminal_id)
                    return False
                self._providers.pop(terminal_id, None)
                logger.info(f"Cleaned up provider for terminal: {terminal_id}")
                return True

            # Provider instances are in-memory only.  After cao-server
            # restarts terminal deletion still has database metadata, but no
            # provider map entry.  Grok has a deterministic, private on-disk
            # home containing generated MCP config, so instantiate the small
            # cleanup-only adapter rather than leaking that directory.
            metadata = get_terminal_metadata(terminal_id)
            if metadata and metadata.get("provider") == ProviderType.GROK_CLI.value:
                restored_grok_provider = GrokCliProvider(
                    terminal_id,
                    metadata["tmux_session"],
                    metadata["tmux_window"],
                    metadata.get("agent_profile"),
                )
                if restored_grok_provider.cleanup() is False:
                    logger.warning("Cleanup deferred for restored Grok provider: %s", terminal_id)
                    return False
                logger.info("Cleaned up restored Grok provider for terminal: %s", terminal_id)
            elif metadata and metadata.get("provider") == ProviderType.MINIMAX_CODE.value:
                restored_minimax_provider = MiniMaxCodeProvider(
                    terminal_id,
                    metadata["tmux_session"],
                    metadata["tmux_window"],
                    metadata.get("agent_profile"),
                )
                if restored_minimax_provider.cleanup() is False:
                    logger.warning(
                        "Cleanup deferred for restored MiniMax Code provider: %s", terminal_id
                    )
                    return False
                logger.info(
                    "Cleaned up restored MiniMax Code provider for terminal: %s", terminal_id
                )
            elif metadata and metadata.get("provider") == ProviderType.GEMINI_CLI.value:
                # Gemini keeps only a deterministic, terminal-private settings
                # overlay.  Restore the minimal adapter so a server restart
                # cannot leak that overlay during terminal deletion.
                restored_gemini_provider = GeminiCliProvider(
                    terminal_id,
                    metadata["tmux_session"],
                    metadata["tmux_window"],
                    metadata.get("agent_profile"),
                )
                if restored_gemini_provider.cleanup() is False:
                    logger.warning(
                        "Cleanup deferred for restored Gemini CLI provider: %s", terminal_id
                    )
                    return False
                logger.info("Cleaned up restored Gemini CLI provider for terminal: %s", terminal_id)
            return True
        except Exception as e:
            logger.error(f"Failed to cleanup provider for terminal {terminal_id}: {e}")
            return False

    def list_providers(self) -> Dict[str, str]:
        """List all active providers (for debugging)."""
        return {
            terminal_id: provider.__class__.__name__
            for terminal_id, provider in self._providers.items()
        }


# Module-level singleton
provider_manager = ProviderManager()
