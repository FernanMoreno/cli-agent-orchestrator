"""Attempt-bound runtime evidence for the local Docker Work boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

_PROOF_SEAL = object()


class WorkDockerIsolationExpired(RuntimeError):
    """The Docker container no longer matches the authorized attempt."""


@dataclass(frozen=True, slots=True)
class WorkDockerRuntimeIsolationProof:
    attempt_id: str
    generation: int
    attempt_revision: int
    contract_hash: str
    image_id: str
    container_id: str
    started_at: str
    worker_socket_identity: tuple[int, int]
    _inspect_current: Callable[[], dict[str, str]] = field(repr=False, compare=False)
    _seal: object = field(repr=False, compare=False)

    def require_current(self, attempt_id: str, generation: int, contract_hash: str) -> None:
        if (
            self._seal is not _PROOF_SEAL
            or type(attempt_id) is not str
            or type(generation) is not int
            or type(contract_hash) is not str
            or (attempt_id, generation, contract_hash)
            != (self.attempt_id, self.generation, self.contract_hash)
        ):
            raise WorkDockerIsolationExpired("Docker isolation proof binding differs")
        try:
            current = self._inspect_current()
        except Exception as error:
            raise WorkDockerIsolationExpired("Docker container evidence is unavailable") from error
        if current != {
            "container_id": self.container_id,
            "image_id": self.image_id,
            "started_at": self.started_at,
        }:
            raise WorkDockerIsolationExpired("Docker container identity changed")

    def close(self) -> None:
        """Docker identity proofs own no host descriptors."""


def issue_docker_runtime_isolation_proof(
    *,
    attempt_id: str,
    generation: int,
    attempt_revision: int,
    contract_hash: str,
    image_id: str,
    container_id: str,
    started_at: str,
    worker_socket_identity: tuple[int, int],
    inspect_current: Callable[[], dict[str, str]],
) -> WorkDockerRuntimeIsolationProof:
    """Issue evidence from a verified running container and its setup gate."""
    if (
        type(attempt_id) is not str
        or not attempt_id
        or type(generation) is not int
        or generation <= 0
        or type(attempt_revision) is not int
        or attempt_revision <= 0
        or type(contract_hash) is not str
        or len(contract_hash) != 64
        or any(value not in "0123456789abcdef" for value in contract_hash)
        or type(image_id) is not str
        or not image_id.startswith("sha256:")
        or type(container_id) is not str
        or len(container_id) != 64
        or any(value not in "0123456789abcdef" for value in container_id)
        or type(started_at) is not str
        or not started_at
        or type(worker_socket_identity) is not tuple
        or len(worker_socket_identity) != 2
        or any(type(value) is not int or value <= 0 for value in worker_socket_identity)
        or not callable(inspect_current)
    ):
        raise WorkDockerIsolationExpired("Docker isolation proof inputs are invalid")
    return WorkDockerRuntimeIsolationProof(
        attempt_id=attempt_id,
        generation=generation,
        attempt_revision=attempt_revision,
        contract_hash=contract_hash,
        image_id=image_id,
        container_id=container_id,
        started_at=started_at,
        worker_socket_identity=worker_socket_identity,
        _inspect_current=inspect_current,
        _seal=_PROOF_SEAL,
    )
