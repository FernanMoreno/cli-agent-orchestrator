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
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, Type

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

if TYPE_CHECKING:
    from cli_agent_orchestrator.models.agent_profile import AgentProfile


# Adapter input selection only: these are inspected command/config builders,
# not a list of models accepted by a remote account or a CLI release. A native
# CLI's own configuration can add another resolution layer beyond this adapter.
_MODEL_TRANSPORT = {
    KiroCliProvider: ("_get_profile_model", "--model", ("model", "profile.model")),
    CodexProvider: ("_build_codex_command", "--model", ("model", "profile.model")),
    HermesProvider: ("_build_hermes_command", "--model", ("model", "profile.model")),
    KimiCliProvider: ("initialize", "--model", ("model", "profile.model")),
    OpenCodeCliProvider: ("_build_launch_command", "--model", ("model",)),
    CursorCliProvider: ("_build_cursor_command", "--model", ("profile.model", "model")),
    AntigravityCliProvider: ("_build_agy_command", "--model", ("profile.model", "model")),
    GeminiCliProvider: ("_build_gemini_command", "--model", ("model", "profile.model")),
    OmpProvider: ("_build_omp_command", "--model", ("model", "profile.model")),
    GrokCliProvider: ("_build_grok_command", "--model", ("model", "profile.model")),
    MiniMaxCodeProvider: (
        "_prepare_runtime",
        "config.yaml:defaultModel",
        ("model", "profile.model"),
    ),
}


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

    def operation_capabilities(
        self, operation: str, *, profile: Optional["AgentProfile"] = None
    ) -> Dict[str, Any]:
        """Describe launch configuration without changing legacy bool discovery.

        ``profile`` is an already resolved context, never loaded or serialized
        here. Only branch identity is inspected; model strings, prompts, tokens,
        and MCP config never enter the descriptor. Nonlaunch operations cannot
        inherit launch-time model/effort configuration claims.
        """
        result = self.adapter_class.operation_capabilities(operation)
        if operation != "launch":
            return result
        transport = _MODEL_TRANSPORT.get(self.adapter_class)
        if self.adapter_class is ClaudeCodeProvider and profile is not None:
            if profile.native_agent:
                result.update(
                    model_selection="profile_owned",
                    model_source="native_agent",
                    model_override_ignored=True,
                    effort_selection="profile_owned",
                    effort_source="native_agent",
                )
                return result
            transport = ("_build_claude_command", "--model", ("model", "profile.model"))
            result.update(
                effort_selection="passthrough",
                effort_source="profile.claudeConfig.effort",
                effort_transport="--effort",
            )
        if transport is not None:
            source, flag, precedence = transport
            override_ignored = False
            if precedence[0] == "profile.model":
                override_ignored = bool(profile.model) if profile is not None else None
            result.update(
                model_selection="passthrough",
                model_source=f"{self.adapter_class.__module__}.{self.adapter_class.__name__}.{source}",
                model_transport=flag,
                model_precedence=list(precedence),
                model_override_ignored=override_ignored,
            )
        if self.adapter_class is CodexProvider:
            result.update(
                effort_selection="passthrough",
                effort_source="profile.codexConfig.model_reasoning_effort",
                effort_transport="-c model_reasoning_effort=...",
            )
        return result

    def preflight(
        self,
        operation: str,
        *,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        profile: Optional["AgentProfile"] = None,
    ) -> Dict[str, Any]:
        """Reject only selections the descriptor explicitly marks inapplicable.

        ``unverified`` remains an honest description of a capability that CAO
        does not establish here.  It is not evidence to reject an existing
        launch path, and this method never probes a binary or a provider.
        """

        result = self.operation_capabilities(operation, profile=profile)
        for selection, requested in (("model", model), ("effort", effort)):
            if requested is not None and result.get(f"{selection}_selection") == "not_applicable":
                raise ValueError(
                    f"Provider {self.name!r} declares {selection} not applicable "
                    f"for operation {operation!r}"
                )
        return result

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


def registered_provider_descriptor(name: str) -> Optional[ProviderDescriptor]:
    """Return a production descriptor without changing legacy provider lookup."""

    return next(
        (descriptor for descriptor in _REGISTERED_PROVIDER_DESCRIPTORS if descriptor.name == name),
        None,
    )
