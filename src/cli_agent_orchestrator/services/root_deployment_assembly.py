"""Inert trusted composition: configuration never creates resource authority."""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from cli_agent_orchestrator.clients.postgres import DatabaseTarget
from cli_agent_orchestrator.models.root_control_protocol import ROOT_CONTROL_FD_ENV
from cli_agent_orchestrator.models.root_deployment_profile import (
    RootDeploymentFootprint,
    RootDeploymentProfile,
)
from cli_agent_orchestrator.models.sql_process_origin import RESOURCE_BINDING_FD_ENV
from cli_agent_orchestrator.models.sql_startup_permission import (
    SQL_BROKER_FD_ENV,
    SQL_ROOT_GENESIS_ENV,
    SQL_STARTUP_PERMISSION_ENV,
)
from cli_agent_orchestrator.security.private_root_configuration import (
    ExternalRootSigner,
    read_private_root_document,
)
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.admission_policy import ResourceAmounts
from cli_agent_orchestrator.services.delivery_admission import (
    DeliveryAdmission,
    StorageControlPermit,
)
from cli_agent_orchestrator.services.root_deployment_runtime import (
    RootDeploymentConfig,
    RootDeploymentRuntime,
)


class RootDeploymentAssemblyUnavailable(RuntimeError):
    """A fixed configuration error that never echoes a private source."""

    def __init__(self) -> None:
        super().__init__("Root deployment assembly is unavailable")


def load_root_deployment_profile(path: Path) -> RootDeploymentProfile:
    """Explicit private configuration I/O; constructing a profile is separate."""
    try:
        document = read_private_root_document(path, maximum_bytes=16 * 1024)
        return RootDeploymentProfile.model_validate(document)
    except Exception:
        raise RootDeploymentAssemblyUnavailable() from None


def _amounts(value: RootDeploymentFootprint) -> ResourceAmounts:
    return ResourceAmounts(value.memory_bytes, value.cpu_millicores, value.storage_slots)


def assemble_root_deployment(
    profile: RootDeploymentProfile,
    *,
    bootstrap: ResourceBootstrap,
    owner: DeliveryAdmission,
    control: StorageControlPermit,
    target: DatabaseTarget,
    env: Mapping[str, str],
    clock: Callable[[], float],
    python_executable: Path | None = None,
) -> RootDeploymentRuntime:
    """Build an uninstalled runtime using separately supplied funding.

    This does not attest, sample, partition or grant resources. An unknown or
    unfunded owner still cannot launch. The caller must provide a disjoint
    authority and must not borrow the API monitor's exclusive control permit.
    No credentials, files, threads, subprocesses or SQL are opened here.
    """
    try:
        if type(profile) is not RootDeploymentProfile:
            raise ValueError("invalid deployment profile")
        if python_executable is not None and not isinstance(python_executable, Path):
            raise ValueError("invalid trusted interpreter")
        executable = sys.executable if python_executable is None else str(python_executable)
        if not executable or "\0" in executable or not Path(executable).is_absolute():
            raise ValueError("invalid trusted interpreter")
        environment = dict(env)
        if (
            any(
                name in environment
                for name in (
                    RESOURCE_BINDING_FD_ENV,
                    ROOT_CONTROL_FD_ENV,
                    SQL_STARTUP_PERMISSION_ENV,
                    SQL_BROKER_FD_ENV,
                )
            )
            or environment.get(SQL_ROOT_GENESIS_ENV, "1") != "1"
        ):
            raise ValueError("inherited process capabilities are not deployment configuration")
        # Select the strict Root protocol. This flag grants no compute or SQL:
        # the child still requires its independently signed activation packet.
        environment[SQL_ROOT_GENESIS_ENV] = "1"
        signer = ExternalRootSigner(
            signing_key_file=Path(profile.signing_key_file),
            trust_file=Path(profile.trust_file),
        )
        config = RootDeploymentConfig(
            bootstrap=bootstrap,
            owner=owner,
            control=control,
            target=target,
            argv=(
                executable,
                "-m",
                "cli_agent_orchestrator.runtime_channel.root_control",
                "--trust-file",
                profile.trust_file,
                "--policy-file",
                profile.policy_file,
                "--scope",
                profile.scope,
                "--close-timeout",
                str(profile.close_timeout_s),
            ),
            env=environment,
            key_supplier=signer,
            credential_preflight=signer.preflight,
            worker_footprint=_amounts(profile.worker_footprint),
            child_footprint=_amounts(profile.child_footprint),
            command_footprint=_amounts(profile.command_footprint),
            startup_footprint=_amounts(profile.startup_footprint),
            refresh_footprint=_amounts(profile.refresh_footprint),
            clock=clock,
            timeout_s=profile.timeout_s,
        )
        return RootDeploymentRuntime(config)
    except Exception:
        raise RootDeploymentAssemblyUnavailable() from None
