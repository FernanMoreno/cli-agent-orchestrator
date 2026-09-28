"""Record immutable Bubblewrap setup evidence before a separate release decision."""

import errno
import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass, is_dataclass
from typing import Callable, Mapping

from cli_agent_orchestrator.clients.work_repository import (
    SchemaMismatch,
    WorkConflict,
    WorkRepository,
    _identity,
    _read_stored_bubblewrap_identity,
    _validate_bubblewrap_identity,
)
from cli_agent_orchestrator.models.work_contract import EffectiveWorkContractV2
from cli_agent_orchestrator.services.work_contract import WorkContracts

_ACK_LIMIT = 8192
_MAX_INT = 2**63 - 1
_ACK_FIELDS = frozenset(
    {
        "destination",
        "sha256_digest",
        "mode",
        "size",
        "destination_device",
        "destination_inode",
        "open_fds",
        "landlock_abi",
        "seccomp_mode",
        "unlisted_path_denied",
        "unmounted_host_path_absent",
        "memfd_create_denied_errno",
        "connect_denied_errno",
        "proxy_socket_device",
        "proxy_socket_inode",
        "monitor_pid",
    }
)


@dataclass(frozen=True, slots=True)
class WorkBubblewrapSetupEvidence:
    """Historical facts only; possession does not authorize GO or another effect."""

    attempt_id: str
    generation: int
    contract_hash: str
    command_token: str
    executable_sha256: str
    ack_sha256: str
    process_identity_sha256: str
    release_intent: str
    created_at: float


class WorkBubblewrapReleaseUncertain(RuntimeError):
    """GO may have reached the worker; the pending intent requires reconciliation."""


def _canonical_ack(
    value: Mapping[str, object],
    digest: str,
    size: int,
    monitor_pid: int,
    expected_proxy_fd: int | None,
    expected_proxy_socket_identity: tuple[int, int] | None,
) -> str:
    if not isinstance(value, Mapping) or set(value) != _ACK_FIELDS:
        raise ValueError("Bubblewrap setup ACK has an incomplete or unknown field set")
    if (
        type(value["destination"]) is not str
        or type(value["sha256_digest"]) is not str
        or value["destination"] != "/exec/worker"
        or value["sha256_digest"] != digest
        or value["open_fds"]
        not in (
            ((0, 1, 2) if expected_proxy_fd is None else (0, 1, 2, expected_proxy_fd)),
            ([0, 1, 2] if expected_proxy_fd is None else [0, 1, 2, expected_proxy_fd]),
        )
        or value["unlisted_path_denied"] is not True
        or value["unmounted_host_path_absent"] is not True
    ):
        raise ValueError("Bubblewrap setup ACK does not match the selected executable or policy")
    if (expected_proxy_fd is None) != (expected_proxy_socket_identity is None):
        raise ValueError("proxy FD and socket identity must be supplied together")
    if expected_proxy_socket_identity is None:
        if value["proxy_socket_device"] is not None or value["proxy_socket_inode"] is not None:
            raise ValueError("setup ACK has an unexpected proxy socket")
    elif (
        len(expected_proxy_socket_identity) != 2
        or any(type(part) is not int or part < 0 for part in expected_proxy_socket_identity)
        or type(value["proxy_socket_device"]) is not int
        or type(value["proxy_socket_inode"]) is not int
        or (value["proxy_socket_device"], value["proxy_socket_inode"])
        != expected_proxy_socket_identity
    ):
        raise ValueError("setup ACK proxy socket differs from the server-owned endpoint")
    expected = {
        "mode": 0o500,
        "size": size,
        "seccomp_mode": 2,
        "memfd_create_denied_errno": errno.EPERM,
        "connect_denied_errno": errno.EPERM,
        "monitor_pid": monitor_pid,
    }
    for key, match in expected.items():
        if type(value[key]) is not int or value[key] != match:
            raise ValueError(f"Bubblewrap setup ACK has invalid {key}")
    for key, minimum in (
        ("destination_device", 0),
        ("destination_inode", 1),
        ("landlock_abi", 1),
    ):
        if type(value[key]) is not int or not minimum <= value[key] <= _MAX_INT:
            raise ValueError(f"Bubblewrap setup ACK has invalid {key}")
    if not 0 < size <= 8388608 or not 0 < monitor_pid <= _MAX_INT:
        raise ValueError("Bubblewrap setup ACK has an unbounded size or PID")
    if any(type(fd) is not int for fd in value["open_fds"]):
        raise ValueError("Bubblewrap setup ACK has invalid descriptors")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > _ACK_LIMIT:
        raise ValueError("Bubblewrap setup ACK exceeds its byte limit")
    return encoded


def _evidence(row: sqlite3.Row) -> WorkBubblewrapSetupEvidence:
    return WorkBubblewrapSetupEvidence(
        attempt_id=row["attempt_id"],
        generation=row["generation"],
        contract_hash=row["contract_hash"],
        command_token=row["command_token"],
        executable_sha256=row["executable_sha256"],
        ack_sha256=row["ack_sha256"],
        process_identity_sha256=row["process_identity_sha256"],
        release_intent=row["release_intent"],
        created_at=row["created_at"],
    )


class WorkBubblewrapSetupIntent:
    def __init__(self, repository: WorkRepository):
        self.repository = repository
        self.contracts = WorkContracts(repository)

    def record_pre_go(
        self,
        attempt_id: str,
        generation: int,
        expected_attempt_revision: int,
        command_token: str,
        acknowledgement: Mapping[str, object] | object,
        process_identity: Mapping[str, object],
        process_identity_sha256: str,
        *,
        expected_proxy_fd: int | None = None,
        expected_proxy_socket_identity: tuple[int, int] | None = None,
        authorize: Callable[[sqlite3.Connection], None] | None = None,
    ) -> WorkBubblewrapSetupEvidence:
        """Commit setup facts in one writer transaction, without granting release."""
        _identity(attempt_id, "attempt_id")
        if type(generation) is not int or not 0 < generation <= _MAX_INT:
            raise ValueError("invalid attempt generation")
        if (
            type(expected_attempt_revision) is not int
            or not 0 < expected_attempt_revision <= _MAX_INT
        ):
            raise ValueError("invalid expected attempt revision")
        if not isinstance(command_token, str) or not command_token:
            raise ValueError("invalid command token")
        if is_dataclass(acknowledgement) and not isinstance(acknowledgement, type):
            ack_fields = asdict(acknowledgement)
        elif isinstance(acknowledgement, Mapping):
            ack_fields = dict(acknowledgement)
        else:
            raise ValueError("Bubblewrap setup ACK must be a closed mapping or dataclass")
        identity, identity_digest = _validate_bubblewrap_identity(process_identity)
        if process_identity_sha256 != identity_digest:
            raise ValueError("Bubblewrap process identity digest differs")
        if authorize is not None and not callable(authorize):
            raise TypeError("setup authorization callback must be callable")

        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            binding = self.contracts._revalidate_order(
                connection, attempt_id, generation=generation
            )
            attempt = connection.execute(
                "SELECT state,revision FROM work_attempts WHERE id=? AND generation=?",
                (attempt_id, generation),
            ).fetchone()
            if (
                attempt is None
                or attempt["state"] != "sent"
                or attempt["revision"] != expected_attempt_revision
            ):
                raise WorkConflict("Bubblewrap setup requires the current sent attempt revision")
            contract = binding.contract
            if not isinstance(contract, EffectiveWorkContractV2):
                raise WorkConflict("Bubblewrap setup requires a v2 executable contract")
            selected = [
                item
                for item in contract.executable_identities
                if item.command_token == command_token
            ]
            if len(selected) != 1:
                raise WorkConflict("Bubblewrap command token is not unique in the contract")
            executable_digest = selected[0].sha256_digest
            if (
                sum(
                    item.sha256_digest == executable_digest
                    for item in contract.executable_identities
                )
                != 1
            ):
                raise WorkConflict("Bubblewrap executable digest is not unique in the contract")
            catalog = connection.execute(
                "SELECT byte_length,immutable_location FROM work_executable_contents WHERE content_hash=?",
                (executable_digest,),
            ).fetchone()
            if catalog is None or catalog["immutable_location"] != executable_digest:
                raise WorkConflict("Bubblewrap executable is absent from the immutable catalog")
            if expected_proxy_fd is not None and (
                type(expected_proxy_fd) is not int or not 3 <= expected_proxy_fd <= 64
            ):
                raise ValueError("proxy descriptor is outside the bounded setup range")
            ack_json = _canonical_ack(
                ack_fields,
                executable_digest,
                catalog["byte_length"],
                identity["monitor_pid"],
                expected_proxy_fd,
                expected_proxy_socket_identity,
            )
            ack_sha256 = hashlib.sha256(ack_json.encode("utf-8")).hexdigest()
            if authorize is not None:
                authorize(connection)
            self.repository._persist_bubblewrap_process_identity(
                connection, attempt_id, generation, identity, identity_digest
            )
            existing = connection.execute(
                "SELECT * FROM work_bubblewrap_setup_intents WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            values = (
                generation,
                binding.contract_hash,
                command_token,
                executable_digest,
                ack_json,
                ack_sha256,
                identity_digest,
            )
            if existing is not None:
                if (
                    tuple(
                        existing[key]
                        for key in (
                            "generation",
                            "contract_hash",
                            "command_token",
                            "executable_sha256",
                            "ack_json",
                            "ack_sha256",
                            "process_identity_sha256",
                        )
                    )
                    != values
                    or existing["schema_version"] != 1
                    or existing["release_intent"] != "pending"
                ):
                    raise WorkConflict("Bubblewrap setup intent is already recorded differently")
                return _evidence(existing)
            connection.execute(
                "INSERT INTO work_bubblewrap_setup_intents "
                "(attempt_id,generation,schema_version,contract_hash,command_token,"
                "executable_sha256,ack_json,ack_sha256,process_identity_sha256,release_intent,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,'pending',?)",
                (
                    attempt_id,
                    generation,
                    1,
                    binding.contract_hash,
                    command_token,
                    executable_digest,
                    ack_json,
                    ack_sha256,
                    identity_digest,
                    time.time(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM work_bubblewrap_setup_intents WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            return _evidence(row)

    def release_if_current(
        self,
        evidence: WorkBubblewrapSetupEvidence,
        *,
        expected_attempt_revision: int,
        release: Callable[[], None],
        authorize: Callable[[sqlite3.Connection], None] | None = None,
    ) -> None:
        """Hold the writer fence from fresh authority validation through GO."""
        if not isinstance(evidence, WorkBubblewrapSetupEvidence):
            raise TypeError("verified Bubblewrap setup evidence is required")
        if type(expected_attempt_revision) is not int or expected_attempt_revision <= 0:
            raise ValueError("positive attempt revision is required")
        if not callable(release):
            raise TypeError("release must be callable")
        if authorize is not None and not callable(authorize):
            raise TypeError("release authorization callback must be callable")

        claim_values = (
            evidence.attempt_id,
            evidence.generation,
            evidence.contract_hash,
            evidence.command_token,
            evidence.executable_sha256,
            evidence.ack_sha256,
            evidence.process_identity_sha256,
        )
        with self.repository.transaction() as connection:
            self._require_current_release(connection, evidence, expected_attempt_revision)
            if authorize is not None:
                authorize(connection)
            if connection.execute(
                "SELECT 1 FROM work_bubblewrap_release_claims WHERE attempt_id=?",
                (evidence.attempt_id,),
            ).fetchone():
                raise WorkConflict("Bubblewrap GO has already been claimed for this attempt")
            connection.execute(
                "INSERT INTO work_bubblewrap_release_claims "
                "(attempt_id,generation,contract_hash,command_token,executable_sha256,"
                "ack_sha256,process_identity_sha256,claimed_at) VALUES (?,?,?,?,?,?,?,?)",
                (*claim_values, time.time()),
            )

        release_attempted = False
        try:
            with self.repository.transaction() as connection:
                self._require_current_release(connection, evidence, expected_attempt_revision)
                if authorize is not None:
                    authorize(connection)
                claim = connection.execute(
                    "SELECT attempt_id,generation,contract_hash,command_token,executable_sha256,"
                    "ack_sha256,process_identity_sha256,schema_version "
                    "FROM work_bubblewrap_release_claims WHERE attempt_id=?",
                    (evidence.attempt_id,),
                ).fetchone()
                if (
                    claim is None
                    or tuple(claim[:7]) != claim_values
                    or claim["schema_version"] != 1
                ):
                    raise WorkConflict("Bubblewrap release claim differs from the pending setup")
                lease = connection.execute(
                    "SELECT lease_expires_at FROM work_attempts WHERE id=? AND generation=?",
                    (evidence.attempt_id, evidence.generation),
                ).fetchone()
                if lease is None or lease["lease_expires_at"] <= time.time():
                    raise WorkConflict("Bubblewrap lease expired before GO")
                release_attempted = True
                release()
        except BaseException as exc:
            if release_attempted:
                raise WorkBubblewrapReleaseUncertain(
                    "Bubblewrap GO was attempted; reconcile the pending setup before retry"
                ) from exc
            raise

    def _require_current_release(
        self,
        connection: sqlite3.Connection,
        evidence: WorkBubblewrapSetupEvidence,
        expected_attempt_revision: int,
    ) -> None:
        self.repository._verify(connection)
        binding = self.contracts._revalidate_order(
            connection, evidence.attempt_id, generation=evidence.generation
        )
        attempt = connection.execute(
            "SELECT state,revision,lease_expires_at FROM work_attempts WHERE id=? AND generation=?",
            (evidence.attempt_id, evidence.generation),
        ).fetchone()
        stored = connection.execute(
            "SELECT * FROM work_bubblewrap_setup_intents WHERE attempt_id=?",
            (evidence.attempt_id,),
        ).fetchone()
        if (
            attempt is None
            or attempt["state"] != "sent"
            or attempt["revision"] != expected_attempt_revision
            or binding.contract_hash != evidence.contract_hash
            or not isinstance(binding.contract, EffectiveWorkContractV2)
            or stored is None
            or stored["schema_version"] != 1
            or stored["release_intent"] != "pending"
            or _evidence(stored) != evidence
            or attempt["lease_expires_at"] <= time.time()
        ):
            raise WorkConflict("Bubblewrap release requires the current pending setup and lease")

    def read_historical(
        self, attempt_id: str, generation: int
    ) -> WorkBubblewrapSetupEvidence | None:
        """Read verified pending evidence for cleanup; current authority is irrelevant here."""
        _identity(attempt_id, "attempt_id")
        if type(generation) is not int or generation <= 0:
            raise ValueError("invalid attempt generation")
        with self.repository.read_snapshot() as connection:
            row = connection.execute(
                "SELECT * FROM work_bubblewrap_setup_intents WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if row is None:
                return None
            if (
                row["generation"] != generation
                or row["schema_version"] != 1
                or row["release_intent"] != "pending"
            ):
                raise SchemaMismatch(
                    "stored Bubblewrap setup intent has invalid version or generation"
                )
            identity_row = connection.execute(
                "SELECT * FROM work_bubblewrap_process_identities WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if identity_row is None or identity_row["generation"] != generation:
                raise SchemaMismatch(
                    "stored Bubblewrap setup intent has no matching process identity"
                )
            identity, digest = _read_stored_bubblewrap_identity(identity_row)
            if digest != row["process_identity_sha256"]:
                raise SchemaMismatch(
                    "stored Bubblewrap setup intent has a different process identity"
                )
            binding = connection.execute(
                "SELECT * FROM work_dispatch_bindings WHERE attempt_id=? AND generation=?",
                (attempt_id, generation),
            ).fetchone()
            if binding is None:
                raise SchemaMismatch("stored Bubblewrap setup intent has no contract binding")
            try:
                contract = self.contracts._stored_contract(connection, binding)
            except ValueError as exc:
                raise SchemaMismatch("stored Bubblewrap setup intent contract is corrupt") from exc
            if (
                not isinstance(contract, EffectiveWorkContractV2)
                or contract.canonical_hash() != row["contract_hash"]
            ):
                raise SchemaMismatch("stored Bubblewrap setup intent contract differs")
            selected = [
                item
                for item in contract.executable_identities
                if item.command_token == row["command_token"]
            ]
            if len(selected) != 1 or selected[0].sha256_digest != row["executable_sha256"]:
                raise SchemaMismatch("stored Bubblewrap setup intent executable differs")
            if (
                sum(
                    item.sha256_digest == row["executable_sha256"]
                    for item in contract.executable_identities
                )
                != 1
            ):
                raise SchemaMismatch("stored Bubblewrap setup intent digest is ambiguous")
            catalog = connection.execute(
                "SELECT byte_length,immutable_location FROM work_executable_contents WHERE content_hash=?",
                (row["executable_sha256"],),
            ).fetchone()
            if catalog is None or catalog["immutable_location"] != row["executable_sha256"]:
                raise SchemaMismatch("stored Bubblewrap executable catalog entry is missing")
            try:
                ack = json.loads(row["ack_json"])
                if not isinstance(ack, dict):
                    raise ValueError("stored ACK is not an object")
                stored_fds = ack.get("open_fds")
                if stored_fds in ([0, 1, 2], (0, 1, 2)):
                    stored_proxy_fd = None
                    stored_socket_identity = None
                elif stored_fds in ([0, 1, 2, 3], (0, 1, 2, 3)):
                    stored_proxy_fd = 3
                    stored_socket_identity = (
                        ack.get("proxy_socket_device"),
                        ack.get("proxy_socket_inode"),
                    )
                else:
                    raise ValueError("stored ACK has an invalid FD inventory")
                canonical = _canonical_ack(
                    ack,
                    row["executable_sha256"],
                    catalog["byte_length"],
                    identity["monitor_pid"],
                    stored_proxy_fd,
                    stored_socket_identity,
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise SchemaMismatch("stored Bubblewrap setup ACK is invalid") from exc
            if (
                canonical != row["ack_json"]
                or hashlib.sha256(canonical.encode("utf-8")).hexdigest() != row["ack_sha256"]
            ):
                raise SchemaMismatch(
                    "stored Bubblewrap setup ACK is not canonical or has a bad digest"
                )
            return _evidence(row)
