"""Export a bounded, descriptive continuity package for one live work attempt."""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import NoReturn

from cli_agent_orchestrator.services.secret_gate import scan_for_secrets
from cli_agent_orchestrator.services.step_output_store import ArtifactRef, ImmutableResultStore
from cli_agent_orchestrator.services.work_authority import WorkAuthority
from cli_agent_orchestrator.services.work_contract import WorkContracts


class ContinuationRejected(ValueError):
    """The requested continuity package is not currently safe to use."""


class UnsupportedContinuationSchema(ContinuationRejected):
    """The package declares a schema version this preflight does not support."""


@dataclass(frozen=True)
class ContinuationPreflight:
    """A descriptive source preflight, not authority or a reservation for T052.

    T052 must revalidate the live source and authority immediately before any
    later effect.  This value contains neither executable state nor a destination.
    """

    package_hash: str
    source_attempt_id: str
    source_generation: int
    source_work_item_id: str
    source_job_id: str


_IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_REASONS = frozenset({"provider replacement"})
_MAX_ARTIFACTS = 128
_MAX_PACKAGE_BYTES = 32 * 1024
_PAYLOAD_FIELDS = frozenset(
    {
        "schema_version",
        "source_attempt_id",
        "source_generation",
        "contract",
        "snapshot",
        "artifacts",
        "completed",
        "pending",
        "uncertain",
        "reason",
    }
)


def _reject() -> NoReturn:
    raise ContinuationRejected("continuation export rejected")


def _positive(value) -> None:
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        _reject()


def _identifier(value) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) or scan_for_secrets(value):
        _reject()


def _digest(value) -> None:
    if not isinstance(value, str) or not _HASH.fullmatch(value) or scan_for_secrets(value):
        _reject()


def _canonical(value: dict) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _reject()


def _no_duplicate_keys(pairs):
    """Build JSON objects while rejecting duplicate keys at every depth."""
    value = {}
    for key, item in pairs:
        if key in value:
            _reject()
        value[key] = item
    return value


def _no_nonfinite_constant(_constant):
    _reject()


class WorkContinuations:
    """Describe one currently authorized work attempt without exporting authority."""

    def __init__(self, repository, artifact_store: ImmutableResultStore):
        self.repository = repository
        self.artifact_store = artifact_store

    def export_continuation(
        self,
        *,
        principal,
        source_attempt_id,
        generation,
        grant_id,
        expected_grant_revision,
        reason,
    ) -> bytes:
        """Return the canonical v1 continuity description for one live binding."""
        try:
            _identifier(source_attempt_id)
            _positive(generation)
            _identifier(grant_id)
            _positive(expected_grant_revision)
            if reason not in _REASONS or scan_for_secrets(reason):
                _reject()
            if not isinstance(self.artifact_store, ImmutableResultStore):
                _reject()

            with self.repository.read_snapshot() as connection:
                contracts = WorkContracts(self.repository)
                binding = contracts._revalidate_continuation(
                    connection, source_attempt_id, generation=generation
                )
                if (
                    binding.attempt_id != source_attempt_id
                    or binding.generation != generation
                    or binding.principal_id != getattr(principal, "id", None)
                    or binding.grant_id != grant_id
                    or binding.grant_revision != expected_grant_revision
                ):
                    _reject()
                WorkAuthority(self.repository)._authorize(
                    connection,
                    principal,
                    job_id=binding.job_id,
                    grant_id=grant_id,
                    expected_grant_revision=expected_grant_revision,
                    provider=binding.contract.provider,
                    requested_permissions=contracts._permissions(binding.contract),
                )
                source = self._source(connection, binding)
                artifacts = self._artifacts(connection, source_attempt_id)
                completed, pending, uncertain = self._classification(source)
                payload = self._payload(
                    binding,
                    artifacts=artifacts,
                    completed=completed,
                    pending=pending,
                    uncertain=uncertain,
                    reason=reason,
                )
                self._validate_payload(payload, max_artifact_bytes=self.artifact_store.max_bytes)
                unsigned = _canonical(payload)
                package = _canonical(
                    {
                        **payload,
                        "package_hash": hashlib.sha256(unsigned).hexdigest(),
                    }
                )
                if len(package) > _MAX_PACKAGE_BYTES:
                    _reject()
                return package
        except ContinuationRejected:
            raise
        except Exception:
            _reject()

    def import_continuation(
        self,
        package: bytes,
        *,
        principal,
        grant_id,
        expected_grant_revision,
    ) -> ContinuationPreflight:
        """Read-only v1 preflight of live source evidence in this configured store.

        This does not import state, create a destination, authorize a future
        effect, or cache a replay result.  Every call rechecks the live source.
        """
        try:
            payload = self._parse_import_package(package)
            _identifier(grant_id)
            _positive(expected_grant_revision)
            if not isinstance(self.artifact_store, ImmutableResultStore):
                _reject()

            with self.repository.read_snapshot() as connection:
                contracts = WorkContracts(self.repository)
                binding = contracts._revalidate_continuation(
                    connection,
                    payload["source_attempt_id"],
                    generation=payload["source_generation"],
                )
                if (
                    binding.attempt_id != payload["source_attempt_id"]
                    or binding.generation != payload["source_generation"]
                    or binding.principal_id != getattr(principal, "id", None)
                    or binding.grant_id != grant_id
                    or binding.grant_revision != expected_grant_revision
                ):
                    _reject()
                WorkAuthority(self.repository)._authorize(
                    connection,
                    principal,
                    job_id=binding.job_id,
                    grant_id=grant_id,
                    expected_grant_revision=expected_grant_revision,
                    provider=binding.contract.provider,
                    requested_permissions=contracts._permissions(binding.contract),
                )
                source = self._source(connection, binding)
                artifacts = self._artifacts(connection, binding.attempt_id)
                completed, pending, uncertain = self._classification(source)
                expected_payload = self._payload(
                    binding,
                    artifacts=artifacts,
                    completed=completed,
                    pending=pending,
                    uncertain=uncertain,
                    reason=payload["reason"],
                )
                if payload != expected_payload:
                    _reject()
                for artifact in artifacts:
                    self.artifact_store.read(
                        ArtifactRef(
                            artifact["content_hash"],
                            artifact["content_ref"],
                            artifact["byte_length"],
                        )
                    )
                return ContinuationPreflight(
                    package_hash=hashlib.sha256(package).hexdigest(),
                    source_attempt_id=binding.attempt_id,
                    source_generation=binding.generation,
                    source_work_item_id=binding.work_item_id,
                    source_job_id=binding.job_id,
                )
        except UnsupportedContinuationSchema:
            raise
        except ContinuationRejected:
            raise
        except Exception:
            _reject()

    def _parse_import_package(self, package: bytes) -> dict:
        if type(package) is not bytes or len(package) > _MAX_PACKAGE_BYTES:
            _reject()
        try:
            parsed = json.loads(
                package.decode("utf-8"),
                object_pairs_hook=_no_duplicate_keys,
                parse_constant=_no_nonfinite_constant,
            )
        except ContinuationRejected:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError):
            _reject()
        if type(parsed) is not dict:
            _reject()
        schema_version = parsed.get("schema_version")
        if type(schema_version) is not int:
            _reject()
        if schema_version != 1:
            raise UnsupportedContinuationSchema("unsupported continuation schema")
        if set(parsed) != _PAYLOAD_FIELDS | {"package_hash"}:
            _reject()
        if package != _canonical(parsed):
            _reject()
        package_hash = parsed["package_hash"]
        _digest(package_hash)
        payload = {field: parsed[field] for field in _PAYLOAD_FIELDS}
        self._validate_payload(payload, max_artifact_bytes=self.artifact_store.max_bytes)
        if package_hash != hashlib.sha256(_canonical(payload)).hexdigest():
            _reject()
        return payload

    @staticmethod
    def _source(connection, binding):
        source = connection.execute(
            "SELECT a.id,a.generation,a.state AS attempt_state,a.work_item_id,a.result_id,"
            "w.id AS work_id,w.state AS work_state,w.accepted_result_id "
            "FROM work_attempts a JOIN work_items w ON w.id=a.work_item_id "
            "WHERE a.id=? AND a.generation=? AND w.id=?",
            (binding.attempt_id, binding.generation, binding.work_item_id),
        ).fetchone()
        if source is None:
            _reject()
        completed = source["attempt_state"] == "finished" and source["work_state"] == "succeeded"
        if completed and (
            source["accepted_result_id"] is None
            or source["accepted_result_id"] != source["result_id"]
            or connection.execute(
                "SELECT id FROM work_results WHERE id=? AND attempt_id=? "
                "AND validation_state='verified' AND schema_version=1",
                (source["accepted_result_id"], binding.attempt_id),
            ).fetchone()
            is None
        ):
            # A work_results row is evidence, not a winner.  Only the immutable
            # accepted_result_id written by the finish CAS identifies completion.
            _reject()
        if not completed and source["accepted_result_id"] is not None:
            _reject()
        if (
            not completed
            and source["result_id"] is not None
            and connection.execute(
                "SELECT id FROM work_results WHERE id=? AND attempt_id=? "
                "AND validation_state='verified' AND schema_version=1",
                (source["result_id"], binding.attempt_id),
            ).fetchone()
            is None
        ):
            _reject()
        return source

    def _artifacts(self, connection, attempt_id) -> list[dict]:
        rows = connection.execute(
            "SELECT content_hash,immutable_location,byte_length FROM work_results "
            "WHERE attempt_id=? AND validation_state='verified' AND schema_version=1 "
            "ORDER BY content_hash,immutable_location,byte_length LIMIT ?",
            (attempt_id, _MAX_ARTIFACTS + 1),
        ).fetchall()
        if len(rows) > _MAX_ARTIFACTS:
            _reject()
        lengths: dict[str, int] = {}
        artifacts = []
        seen = set()
        for row in rows:
            reference = ArtifactRef(
                row["content_hash"], row["immutable_location"], row["byte_length"]
            )
            if reference.byte_length > self.artifact_store.max_bytes:
                _reject()
            previous = lengths.setdefault(reference.content_hash, reference.byte_length)
            if previous != reference.byte_length:
                _reject()
            triple = (
                reference.content_hash,
                reference.immutable_location,
                reference.byte_length,
            )
            if triple not in seen:
                seen.add(triple)
                artifacts.append(
                    {
                        "content_hash": reference.content_hash,
                        "content_ref": reference.immutable_location,
                        "byte_length": reference.byte_length,
                    }
                )
        return sorted(
            artifacts,
            key=lambda artifact: (
                artifact["content_hash"],
                artifact["content_ref"],
                artifact["byte_length"],
            ),
        )

    @staticmethod
    def _classification(source) -> tuple[list[str], list[str], list[str]]:
        work_id = source["work_id"]
        if source["attempt_state"] == "finished" and source["work_state"] == "succeeded":
            return [work_id], [], []
        if source["attempt_state"] == "planned" and source["work_state"] == "queued":
            return [], [work_id], []
        if source["attempt_state"] in {"sent", "acknowledged", "running"} and source[
            "work_state"
        ] in {"running", "waiting_children"}:
            return [], [], [work_id]
        _reject()

    @staticmethod
    def _payload(binding, *, artifacts, completed, pending, uncertain, reason) -> dict:
        return {
            "schema_version": 1,
            "source_attempt_id": binding.attempt_id,
            "source_generation": binding.generation,
            "contract": {"id": binding.contract.id, "hash": binding.contract_hash},
            "snapshot": {
                "id": binding.contract.snapshot.id,
                "delivered_hash": binding.contract.snapshot.delivered_hash,
            },
            "artifacts": artifacts,
            "completed": completed,
            "pending": pending,
            "uncertain": uncertain,
            "reason": reason,
        }

    @staticmethod
    def _validate_payload(payload: dict, *, max_artifact_bytes: int) -> None:
        if (
            type(payload) is not dict
            or set(payload) != _PAYLOAD_FIELDS
            or type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
        ):
            _reject()
        _identifier(payload["source_attempt_id"])
        _positive(payload["source_generation"])
        contract, snapshot = payload["contract"], payload["snapshot"]
        if (
            type(contract) is not dict
            or set(contract) != {"id", "hash"}
            or type(snapshot) is not dict
            or set(snapshot) != {"id", "delivered_hash"}
        ):
            _reject()
        _identifier(contract["id"])
        _digest(contract["hash"])
        _identifier(snapshot["id"])
        _digest(snapshot["delivered_hash"])
        if (
            type(payload["reason"]) is not str
            or payload["reason"] not in _REASONS
            or scan_for_secrets(payload["reason"])
        ):
            _reject()
        artifacts = payload["artifacts"]
        if type(artifacts) is not list or len(artifacts) > _MAX_ARTIFACTS:
            _reject()
        triples = []
        lengths: dict[str, int] = {}
        for artifact in artifacts:
            if type(artifact) is not dict or set(artifact) != {
                "content_hash",
                "content_ref",
                "byte_length",
            }:
                _reject()
            reference = ArtifactRef(
                artifact["content_hash"], artifact["content_ref"], artifact["byte_length"]
            )
            if reference.byte_length > max_artifact_bytes:
                _reject()
            previous = lengths.setdefault(reference.content_hash, reference.byte_length)
            if previous != reference.byte_length:
                _reject()
            triples.append(
                (reference.content_hash, reference.immutable_location, reference.byte_length)
            )
        if triples != sorted(set(triples)):
            _reject()
        states = [payload["completed"], payload["pending"], payload["uncertain"]]
        if any(type(state) is not list for state in states):
            _reject()
        work_ids = []
        for state in states:
            for work_id in state:
                _identifier(work_id)
                work_ids.append(work_id)
            if state != sorted(set(state)):
                _reject()
        if len(work_ids) != 1 or len(set(work_ids)) != 1:
            _reject()
