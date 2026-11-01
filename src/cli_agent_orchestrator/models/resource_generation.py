"""Internal lifecycle evidence; no value alone proves resource closure."""

import os
from dataclasses import dataclass
from uuid import NAMESPACE_OID, UUID, uuid5

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


@dataclass(frozen=True, eq=False, init=False, repr=False, slots=True)
class ProcessExitObservation:
    """Only one owned child's exit; no descendant, SQL or credit authority."""

    binding: ResourceBinding
    boot_id: str
    start_time_ticks: int
    pid_namespace: tuple[int, int]
    exit_kind: str
    exit_status: int
    _issuer_pid: int
    _factory: object

    @property
    def returncode(self) -> int:
        return self.exit_status if self.exit_kind == "exited" else -self.exit_status

    def __copy__(self):
        raise TypeError("process exit observation is not transferable")

    def __deepcopy__(self, memo):
        raise TypeError("process exit observation is not transferable")

    def __reduce_ex__(self, protocol):
        raise TypeError("process exit observation is not serializable")


def _issue_process_exit_observation(
    binding: ResourceBinding,
    *,
    boot_id: str,
    start_time_ticks: int,
    pid_namespace: tuple[int, int],
    exit_kind: str,
    exit_status: int,
) -> ProcessExitObservation:
    if (
        type(binding) is not ResourceBinding
        or str(UUID(boot_id)) != boot_id
        or type(start_time_ticks) is not int
        or start_time_ticks < 1
        or type(pid_namespace) is not tuple
        or len(pid_namespace) != 2
        or any(type(value) is not int or value < 0 for value in pid_namespace)
        or exit_kind not in {"exited", "signaled", "core_dumped"}
        or type(exit_status) is not int
        or not 0 <= exit_status <= 255
        or (exit_kind != "exited" and exit_status == 0)
    ):
        raise ValueError("process exit observation requires validated kernel evidence")
    value = object.__new__(ProcessExitObservation)
    for name, field in (
        ("binding", binding),
        ("boot_id", boot_id),
        ("start_time_ticks", start_time_ticks),
        ("pid_namespace", pid_namespace),
        ("exit_kind", exit_kind),
        ("exit_status", exit_status),
        ("_issuer_pid", os.getpid()),
        ("_factory", _FACTORY),
    ):
        object.__setattr__(value, name, field)
    return value


def is_process_exit_observation(value: object) -> bool:
    return (
        type(value) is ProcessExitObservation
        and getattr(value, "_factory", None) is _FACTORY
        and getattr(value, "_issuer_pid", None) == os.getpid()
    )
