"""Internal negative lifecycle authority; a seal never proves physical closure."""

from dataclasses import dataclass
from uuid import NAMESPACE_OID, uuid5

from cli_agent_orchestrator.models.resource_envelope import ResourceBinding

_FACTORY = object()


@dataclass(frozen=True)
class GenerationSeal:
    seal_id: str
    instance_id: str
    process_generation: str


@dataclass(frozen=True, eq=False, init=False, repr=False, slots=True)
class TerminalGenerationAuthority:
    binding: ResourceBinding
    seal_id: str
    _factory: object

    def __copy__(self):
        raise TypeError("terminal generation authority is not transferable")

    def __deepcopy__(self, memo):
        raise TypeError("terminal generation authority is not transferable")

    def __reduce_ex__(self, protocol):
        raise TypeError("terminal generation authority is not serializable")


def _issue_terminal_generation_authority(binding: ResourceBinding) -> TerminalGenerationAuthority:
    if type(binding) is not ResourceBinding:
        raise ValueError("terminal generation requires a trusted binding")
    authority = object.__new__(TerminalGenerationAuthority)
    object.__setattr__(authority, "binding", binding)
    object.__setattr__(
        authority,
        "seal_id",
        str(
            uuid5(
                NAMESPACE_OID,
                ":".join(
                    (
                        "cao-terminal-generation-v1",
                        binding.store_id,
                        binding.target_identity_sha256,
                        binding.instance_id,
                        binding.process_generation,
                    )
                ),
            )
        ),
    )
    object.__setattr__(authority, "_factory", _FACTORY)
    return authority


def is_terminal_generation_authority(value: object) -> bool:
    return (
        type(value) is TerminalGenerationAuthority and getattr(value, "_factory", None) is _FACTORY
    )
