"""Explicit server-owned registry for backends that enforce Work contracts."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

from cli_agent_orchestrator.backends.base import TerminalBackend

if TYPE_CHECKING:
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

# Work admission stays unavailable until the server explicitly registers a
# backend whose preflight and protected effect boundary enforce the contract.
WORK_BACKENDS: dict[str, TerminalBackend] = {}

_LOCAL_DOCKER_ENABLE = "CAO_WORK_DOCKER_LOCAL"
_LOCAL_DOCKER_IMAGE_ID = "CAO_WORK_DOCKER_IMAGE_ID"
_LOCAL_DOCKER_KEY = "docker-local"
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


def local_work_backends_for(
    repository: WorkRepository,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, TerminalBackend]:
    """Return this runtime's Work registry, optionally adding the local Docker backend.

    The process-wide registry remains empty by default. Docker is opt-in for one
    runtime and must be bound to the exact repository instance that runtime uses
    to construct ``WorkOrigins``. Its image must be selected by immutable image
    ID; Docker daemon locality and image contents are still verified by backend
    preflight before any effect.
    """
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    if not isinstance(repository, WorkRepository):
        raise ValueError("verified work repository required")
    environment = os.environ if environ is None else environ
    if not isinstance(environment, Mapping):
        raise ValueError("explicit local Work environment mapping required")

    backends = dict(WORK_BACKENDS)
    enabled = environment.get(_LOCAL_DOCKER_ENABLE, "")
    if enabled in ("", "0"):
        return backends
    if enabled != "1":
        raise ValueError(f"{_LOCAL_DOCKER_ENABLE} must be unset, '0', or exactly '1'")

    image_ref = environment.get(_LOCAL_DOCKER_IMAGE_ID, "")
    if not isinstance(image_ref, str) or not _IMAGE_ID.fullmatch(image_ref):
        raise ValueError(f"{_LOCAL_DOCKER_IMAGE_ID} must be an immutable image ID")
    if _LOCAL_DOCKER_KEY in backends:
        raise ValueError(f"Work backend key {_LOCAL_DOCKER_KEY!r} is already registered")

    from cli_agent_orchestrator.backends.docker_backend import DockerWorkBackend

    backends[_LOCAL_DOCKER_KEY] = DockerWorkBackend(
        image_ref=image_ref,
        repository=repository,
    )
    return backends
