"""Publish and stage only exact static executable bytes named by a V2 attempt."""

import time

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_contract import EffectiveWorkContractV2, ExecutableIdentity
from cli_agent_orchestrator.services.step_output_store import ArtifactRef, ImmutableResultStore
from cli_agent_orchestrator.services.work_contract import ContractConflict, WorkContracts
from cli_agent_orchestrator.services.work_elf_identity import identify_static_executable
from cli_agent_orchestrator.services.work_executable_staging import stage_static_executable

_MAX_EXECUTABLE_BYTES = 8 * 1024 * 1024


class WorkExecutableContent:
    """Server-owned content namespace; callers can resolve only a bound command."""

    def __init__(self, repository: WorkRepository) -> None:
        self.repository = repository
        self.content: ImmutableResultStore = ImmutableResultStore(
            repository.executable_content_root, max_bytes=_MAX_EXECUTABLE_BYTES
        )

    def publish(self, identity: ExecutableIdentity, executable_bytes: bytes) -> ArtifactRef:
        if not isinstance(identity, ExecutableIdentity):
            raise TypeError("identity must be an ExecutableIdentity")
        if not isinstance(executable_bytes, (bytes, bytearray, memoryview)):
            raise TypeError("executable bytes must be bytes-like")
        byte_length = memoryview(executable_bytes).nbytes
        if byte_length == 0 or byte_length > _MAX_EXECUTABLE_BYTES:
            raise ValueError("executable content exceeds the private store's size bound")
        snapshot = bytes(executable_bytes)
        if identify_static_executable(identity.command_token, snapshot) != identity:
            raise ValueError("identity does not match executable bytes")

        def accept(ref: ArtifactRef) -> ArtifactRef:
            with self.repository.transaction() as connection:
                self.repository._verify(connection)
                row = connection.execute(
                    "SELECT content_hash,immutable_location,byte_length "
                    "FROM work_executable_contents WHERE content_hash=?",
                    (ref.content_hash,),
                ).fetchone()
                expected = (ref.content_hash, ref.immutable_location, ref.byte_length)
                if row is None:
                    connection.execute(
                        "INSERT INTO work_executable_contents "
                        "(content_hash,immutable_location,byte_length,created_at) VALUES (?,?,?,?)",
                        (*expected, time.time()),
                    )
                elif tuple(row) != expected:
                    raise ContractConflict(
                        "executable content catalog conflicts with verified bytes"
                    )
            return ref

        return self.content.publish(snapshot, accept)

    def resolve_and_stage(self, attempt_id: str, generation: int, command_token: str):
        if not isinstance(attempt_id, str) or not attempt_id:
            raise ContractConflict("attempt identity is required")
        if type(generation) is not int or generation <= 0:
            raise ContractConflict("positive attempt generation is required")
        if not isinstance(command_token, str) or not command_token:
            raise ContractConflict("command token is required")

        with self.repository.read_snapshot() as connection:
            binding = WorkContracts(self.repository)._revalidate_order(
                connection, attempt_id, generation=generation
            )
            contract = binding.contract
            if not isinstance(contract, EffectiveWorkContractV2):
                raise ContractConflict("attempt has no V2 executable identity")
            identity = next(
                (
                    item
                    for item in contract.executable_identities
                    if item.command_token == command_token
                ),
                None,
            )
            if identity is None:
                raise ContractConflict("command is absent from the bound V2 contract")
            catalog = connection.execute(
                "SELECT content_hash,immutable_location,byte_length "
                "FROM work_executable_contents WHERE content_hash=?",
                (identity.sha256_digest,),
            ).fetchone()
            if catalog is None or (
                catalog["content_hash"] != identity.sha256_digest
                or catalog["immutable_location"] != identity.sha256_digest
                or type(catalog["byte_length"]) is not int
                or not 1 <= catalog["byte_length"] <= _MAX_EXECUTABLE_BYTES
            ):
                raise ContractConflict("bound executable content is absent or inconsistent")
            reference = ArtifactRef(
                identity.sha256_digest, identity.sha256_digest, catalog["byte_length"]
            )

        snapshot = self.content.read(reference)
        return stage_static_executable(identity, snapshot)
