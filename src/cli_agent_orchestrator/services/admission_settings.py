"""Atomic operator policy reload; configuration never attests resource authority."""

import json
import os
import stat
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal

from cli_agent_orchestrator.clients.postgres.config import ConfigurationError
from cli_agent_orchestrator.clients.postgres.connection import DatabaseError
from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch
from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy, validate_clock
from cli_agent_orchestrator.services.admission_postgres import PostgresPressurePolicy

if TYPE_CHECKING:
    from cli_agent_orchestrator.clients.admission_policy_repository import AdmissionPolicyRepository
    from cli_agent_orchestrator.clients.funded_control_session import FundedControlSession


@dataclass(frozen=True)
class AdmissionConfiguration:
    policy: AdmissionPolicy = field(default_factory=AdmissionPolicy)
    postgres: PostgresPressurePolicy = field(default_factory=PostgresPressurePolicy)
    sample_interval_s: float = 1.0
    healthy_samples_before_growth: int = 3

    def __post_init__(self) -> None:
        validate_clock(self.sample_interval_s, positive=True)
        if self.sample_interval_s >= self.policy.max_sample_age_s:
            raise ValueError("sampling interval must be shorter than observation freshness")
        count = self.healthy_samples_before_growth
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise ValueError("growth recovery requires positive integer samples")


_DEFAULTS: dict[str, object] = {
    **asdict(AdmissionPolicy()),
    "postgres_reserve_percent": 20,
    "postgres_statement_timeout_ms": 500,
    "sample_interval_s": 1.0,
    "healthy_samples_before_growth": 3,
}


def _integer(values: Mapping[str, object], key: str) -> int:
    value = values[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("operator policy requires integer units")
    return value


def _duration(values: Mapping[str, object], key: str) -> float:
    value = values[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("operator policy requires finite seconds")
    validate_clock(value, positive=True)
    return float(value)


def parse_operator_policy(document: Mapping[str, object]) -> AdmissionConfiguration:
    """Parse all settings as one candidate; unknown fields are never ignored."""
    if not isinstance(document, Mapping) or set(document) != {"version", "policy"}:
        raise ValueError("invalid operator policy document")
    version = document["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise ValueError("unsupported operator policy version")
    supplied = document["policy"]
    if not isinstance(supplied, Mapping) or any(key not in _DEFAULTS for key in supplied):
        raise ValueError("invalid operator policy fields")
    values = dict(_DEFAULTS)
    values.update(supplied)
    return AdmissionConfiguration(
        policy=AdmissionPolicy(
            memory_per_delivery_bytes=_integer(values, "memory_per_delivery_bytes"),
            cpu_per_delivery_millicores=_integer(values, "cpu_per_delivery_millicores"),
            memory_reserve_percent=_integer(values, "memory_reserve_percent"),
            cpu_reserve_percent=_integer(values, "cpu_reserve_percent"),
            max_sample_age_s=_duration(values, "max_sample_age_s"),
        ),
        postgres=PostgresPressurePolicy(
            reserve_percent=_integer(values, "postgres_reserve_percent"),
            statement_timeout_ms=_integer(values, "postgres_statement_timeout_ms"),
        ),
        sample_interval_s=_duration(values, "sample_interval_s"),
        healthy_samples_before_growth=_integer(values, "healthy_samples_before_growth"),
    )


def _private_regular(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise PermissionError("operator policy must be a private owner-controlled regular file")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("operator policy contains duplicate object fields")
        result[key] = value
    return result


def read_private_operator_policy(path: Path) -> Mapping[str, object]:
    """Read a bounded, stable private descriptor; never write or follow aliases."""
    if not path.is_absolute():
        raise ValueError("operator policy path must be absolute")
    maximum_bytes = 64 * 1024
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        _private_regular(before)
        if before.st_size > maximum_bytes:
            raise ValueError("operator policy exceeds its document size limit")
        payload = bytearray()
        while len(payload) <= maximum_bytes:
            chunk = os.read(descriptor, maximum_bytes + 1 - len(payload))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        _private_regular(after)
        if len(payload) > maximum_bytes or (
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("operator policy changed while reading")
    finally:
        os.close(descriptor)
    try:
        decoded: object = json.loads(payload, object_pairs_hook=_unique_json_object)
    except RecursionError:
        raise ValueError("operator policy exceeds JSON nesting limits") from None
    if not isinstance(decoded, dict) or any(not isinstance(key, str) for key in decoded):
        raise ValueError("operator policy must be an object")
    return decoded


@dataclass(frozen=True)
class PolicyLoadResult:
    configuration: AdmissionConfiguration | None
    status: Literal["current", "retained", "unavailable"]
    reason: Literal["policy_valid", "policy_invalid", "policy_unreadable"]
    revision: int


class AdmissionPolicyLoader:
    """Retain a validated whole candidate only within one explicit source scope.

    The source is an operator-owned policy reader, never generic settings.json,
    a profile, a request, or a hierarchy/identity attestation. Each lifecycle must
    use a scope bound to its owner and source; a new scope starts without fallback.
    """

    def __init__(
        self,
        *,
        source: Callable[[], Mapping[str, object]] | None = None,
        repository: "AdmissionPolicyRepository | None" = None,
    ):
        if source is None and repository is None:
            raise ValueError("operator policy requires an explicit source")
        self._source = source
        self.repository = repository
        self._bootstrap_loader = (
            AdmissionPolicyLoader(source=source)
            if repository is not None and source is not None
            else None
        )
        self._guard = threading.Lock()
        self._scope: str | None = None
        self._last: AdmissionConfiguration | None = None
        self._revision = 0

    @property
    def persistent(self) -> bool:
        return self.repository is not None

    def load_bootstrap(self, *, scope_id: str) -> PolicyLoadResult:
        """Pre-SQL startup settings; never read or credit a database revision."""
        if not self.persistent:
            return self.load(scope_id=scope_id)
        if self._bootstrap_loader is None:
            return PolicyLoadResult(None, "unavailable", "policy_unreadable", 0)
        return self._bootstrap_loader.load(scope_id=scope_id)

    def load(
        self,
        *,
        scope_id: str,
        control_session: "FundedControlSession | None" = None,
    ) -> PolicyLoadResult:
        if not isinstance(scope_id, str) or not scope_id.strip():
            raise ValueError("operator policy requires a source scope identity")
        if self.repository is not None:
            # The caller owns the control operation and any original SQL session.
            # Failed reads never inherit file configuration or an older DB row.
            try:
                record = self.repository.read_latest(
                    self.repository.resolve_target(), control_session=control_session
                )
                if record is None or not record.active:
                    return PolicyLoadResult(None, "unavailable", "policy_unreadable", 0)
                candidate = parse_operator_policy(
                    {"version": 1, "policy": record.policy.model_dump()}
                )
                return PolicyLoadResult(candidate, "current", "policy_valid", record.revision)
            except StorageAdmissionUnavailable:
                if control_session is not None:
                    raise  # A refused original-session operation is an authority failure.
                return PolicyLoadResult(None, "unavailable", "policy_unreadable", 0)
            except (
                OSError,
                ValueError,
                DatabaseError,
                ConfigurationError,
                SchemaMismatch,
            ):
                return PolicyLoadResult(None, "unavailable", "policy_unreadable", 0)
        with self._guard:
            if self._scope != scope_id:
                self._scope = scope_id
                self._last = None
                self._revision = 0
            failure: Literal["policy_invalid", "policy_unreadable"] | None = None
            try:
                assert self._source is not None
                candidate = parse_operator_policy(self._source())
            except OSError:
                failure = "policy_unreadable"
            except ValueError:
                failure = "policy_invalid"
            if failure is not None:
                return PolicyLoadResult(
                    self._last,
                    "retained" if self._last is not None else "unavailable",
                    failure,
                    self._revision,
                )
            if candidate != self._last:
                self._last = candidate
                self._revision += 1
            return PolicyLoadResult(self._last, "current", "policy_valid", self._revision)


def configured_policy_loader(path: Path | None) -> AdmissionPolicyLoader:
    """Trusted deployment selector; it never supplies authority or repairs a store."""
    selected = os.environ.get("CAO_ADMISSION_POLICY_STORE", "file")
    if selected not in {"file", "postgres"}:
        raise ValueError("invalid admission policy store")
    if path is not None and not path.is_absolute():
        raise ValueError("admission policy requires an absolute private source path")
    source = None if path is None else lambda: read_private_operator_policy(path)
    if selected == "file":
        if source is None:
            raise ValueError("file admission policy requires a private source")
        return AdmissionPolicyLoader(source=source)
    from cli_agent_orchestrator.clients.admission_policy_repository import AdmissionPolicyRepository
    from cli_agent_orchestrator.services.local_peer_identity import get_persistent_instance_id

    return AdmissionPolicyLoader(
        source=source,
        repository=AdmissionPolicyRepository(profile_id=get_persistent_instance_id()),
    )
