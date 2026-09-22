"""Registered provider descriptors for discovery and operation preflight.

The catalogue is deliberately **individual**: a descriptor says what one
adapter can do on its own.  It contains no provider-pair compatibility,
preference, or routing policy.  Any two providers admitted by a job may
collaborate; CAO coordinates write safety through task/resource leases rather
than by judging provider names.

Consumers such as the HTTP discovery endpoint and an external scheduler can
use these operational facts to preflight a requested action.  They must only
require a capability needed by that action (for example, a durable turn receipt
when that action needs one), never use this module to reject a parent/child
provider combination.
"""

from dataclasses import dataclass
from typing import Dict, Tuple, Type

from cli_agent_orchestrator.models.provider import ProviderType
from cli_agent_orchestrator.providers.antigravity_cli import AntigravityCliProvider
from cli_agent_orchestrator.providers.base import BaseProvider
from cli_agent_orchestrator.providers.claude_code import ClaudeCodeProvider
from cli_agent_orchestrator.providers.codex import CodexProvider
from cli_agent_orchestrator.providers.copilot_cli import CopilotCliProvider
from cli_agent_orchestrator.providers.cursor_cli import CursorCliProvider
from cli_agent_orchestrator.providers.gemini_cli import GeminiCliProvider
from cli_agent_orchestrator.providers.grok_cli import GrokCliProvider
from cli_agent_orchestrator.providers.hermes import HermesProvider
from cli_agent_orchestrator.providers.kimi_cli import KimiCliProvider
from cli_agent_orchestrator.providers.kiro_cli import KiroCliProvider
from cli_agent_orchestrator.providers.minimax_code import MiniMaxCodeProvider
from cli_agent_orchestrator.providers.omp import OmpProvider
from cli_agent_orchestrator.providers.opencode_cli import OpenCodeCliProvider


@dataclass(frozen=True)
class ProviderDescriptor:
    """A stable, operation-level description of one registered adapter.

    ``native_children`` and ``sibling_messages`` describe CAO control-plane
    operations that every production adapter can participate in.  They do not
    make any statement about a particular parent/child pair.  The remaining
    flags are read directly from the adapter class, so providers can expose
    their terminal-observation and receipt behaviour without an endpoint-side
    second source of truth.
    """

    name: str
    binary: str
    adapter_class: Type[BaseProvider]
    native_children: bool = True
    sibling_messages: bool = True

    def capabilities(self) -> Dict[str, bool]:
        """Return JSON-safe operational capabilities for this adapter only."""

        return {
            "native_children": self.native_children,
            "sibling_messages": self.sibling_messages,
            "terminal_status": True,
            "screen_status": bool(self.adapter_class.supports_screen_detection),
            "direct_status_probe": bool(self.adapter_class.supports_direct_status_probe),
            "midburst_processing_probe": bool(
                self.adapter_class.supports_midburst_processing_probe
            ),
            "durable_turn_receipts": bool(self.adapter_class.requires_turn_receipt),
        }


# Keep this registry in the same stable order that /agents/providers exposed
# before descriptors were added.  ``mock_cli`` is intentionally absent: it is
# credentials-free test infrastructure, not a production adapter an operator
# can preflight or select for a real job.
_REGISTERED_PROVIDER_DESCRIPTORS: Tuple[ProviderDescriptor, ...] = (
    ProviderDescriptor(ProviderType.KIRO_CLI.value, "kiro-cli", KiroCliProvider),
    ProviderDescriptor(ProviderType.CLAUDE_CODE.value, "claude", ClaudeCodeProvider),
    ProviderDescriptor(ProviderType.CODEX.value, "codex", CodexProvider),
    ProviderDescriptor(ProviderType.HERMES.value, "hermes", HermesProvider),
    ProviderDescriptor(ProviderType.KIMI_CLI.value, "kimi", KimiCliProvider),
    ProviderDescriptor(ProviderType.COPILOT_CLI.value, "copilot", CopilotCliProvider),
    ProviderDescriptor(ProviderType.OPENCODE_CLI.value, "opencode", OpenCodeCliProvider),
    ProviderDescriptor(ProviderType.CURSOR_CLI.value, "agent", CursorCliProvider),
    ProviderDescriptor(ProviderType.ANTIGRAVITY_CLI.value, "agy", AntigravityCliProvider),
    ProviderDescriptor(ProviderType.GEMINI_CLI.value, "gemini", GeminiCliProvider),
    ProviderDescriptor(ProviderType.OMP.value, "omp", OmpProvider),
    ProviderDescriptor(ProviderType.GROK_CLI.value, "grok", GrokCliProvider),
    ProviderDescriptor(ProviderType.MINIMAX_CODE.value, "mcode", MiniMaxCodeProvider),
)


def registered_provider_descriptors() -> Tuple[ProviderDescriptor, ...]:
    """Return every production provider registered in this CAO checkout.

    The immutable tuple prevents API or scheduler callers from accidentally
    mutating the registry.  A provider is selected by a job's own allowlist;
    this discovery function has no authorization effect.
    """

    return _REGISTERED_PROVIDER_DESCRIPTORS
