#!/usr/bin/env python3
"""Default-closed source-bound OpenCode v2 full-collaboration message candidate.

This candidate is closed unless the operator supplies ``--go``, the expected
source HEAD, a private manifest and its exact SHA256, and the independent review
digest bound into that manifest. Execution uses a private byte-for-byte derived
copy whose sole source change enables the copy; the repository candidate stays
closed. It installs two production CAO profiles,
creates two owned native terminals against the already-running pinned
PostgreSQL service, sends one generic ``send_message`` before and one after a
single API restart, and checks PostgreSQL/API/AG-UI/native-observer records.
The sender receives the full unfiltered collaboration profile. Its task still
requires exactly one sibling lookup and two generic messages. The receiver has
an empty tool filter and replies with the exact public acknowledgement.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import datetime as dt
import gc
import hashlib
import importlib.util
import inspect
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPOSITORY_ROOT_ENV = "CAO_T104_REPOSITORY_ROOT"
_DEFAULT_REPO = Path(__file__).resolve().parents[3]
REPO = Path(os.environ.get(REPOSITORY_ROOT_ENV, str(_DEFAULT_REPO))).resolve()
PREP = Path(__file__).resolve().parent
EXPECTED_HEAD: str | None = None
PINNED_PYTHON: Path | None = None
PINNED_PYTHON_RESOLVED: Path | None = None
PINNED_OPENCODE: Path | None = None
PINNED_OPENCODE_SHA256: str | None = None
PINNED_POSTGRES_ID: str | None = None
PINNED_POSTGRES_IMAGE: str | None = None
PINNED_POSTGRES_FIXTURE_SHA256: str | None = None
PINNED_POSTGRES_GUARD_DERIVATION_SHA256: str | None = None
MODEL = "opencode-go/glm-5.3-flash"
RUN_ENABLED = False
RUN_MANIFEST_SCHEMA = "spec025.t104.native-v2-collaboration-manifest.v2"
RUN_MANIFEST: dict[str, Any] | None = None
RUN_MANIFEST_SHA256: str | None = None
REVIEW_DIGEST: str | None = None
SOURCE_FINGERPRINT_SHA256: str | None = None
RUNNER_SHA256_AT_ADMISSION: str | None = None
MEMORY_FLOOR_MIB = 6144
MODEL_PHASE_SECONDS = 75.0
TASK_QUERY_SECONDS = 180.0
OBSERVER_GRACE_SECONDS = 10.0
OUTPUT_RANGE_MAX_BYTES = 1024 * 1024
OUTPUT_RANGE_CHUNK_BYTES = 64 * 1024
LABEL = "t104-native-v2-full-collaboration"
RECEIPT_SCHEMA = "spec025.t104.opencode-v2-collaboration-messages.private.v2"
RECEIPT = PREP / f"{LABEL}-receipt.json"
EVIDENCE = PREP / f"{LABEL}-evidence"

# A deferred guarded context must outlive _run's local frame. The guard in the
# source-derived fixture helper remains false at interpreter shutdown, so a
# generator finalizer cannot tear down PostgreSQL state while an owned service
# is unconfirmed.
_DEFERRED_POSTGRES_CONTEXTS: list[Any] = []
_ACTIVE_PRIVATE_RUN_ROOT: Path | None = None
_SIGTERM_CLEANUP_ACTIVE = False
_SIGTERM_PENDING = False

SENDER_TOOLS = (
    "cao_acknowledge_user_answers",
    "cao_ask_user",
    "cao_escalate_interaction",
    "cao_get_interaction",
    "cao_list_interactions",
    "cao_wait_for_user_answers",
    "list_siblings",
    "send_message",
)
SENDER_CALLS = ("list_siblings", "send_message", "send_message")
MCP_HTTP_OFFER_TOOL_NAMES = tuple("cao-mcp-server_" + tool for tool in SENDER_TOOLS)
OPTIONAL_OPENCODE_BUILTIN_TOOL_NAMES = ("skill", "todowrite")
DIAGNOSTIC_ONLY_OPENCODE_TOOL_NAMES = (
    "apply_patch",
    "bash",
    "batch",
    "codesearch",
    "edit",
    "execute",
    "glob",
    "grep",
    "lsp",
    "question",
    "read",
    "task",
    "webfetch",
    "websearch",
    "write",
)
HTTP_OFFER_TOOL_NAMES = (
    MCP_HTTP_OFFER_TOOL_NAMES
    + OPTIONAL_OPENCODE_BUILTIN_TOOL_NAMES
    + DIAGNOSTIC_ONLY_OPENCODE_TOOL_NAMES
)
CAPABILITY_BINDING_DIAGNOSTIC_FIELDS = (
    "trusted_api_context",
    "source_current",
    "owner_exact",
    "kind_exact",
    "capability_hash_matches",
    "tool_fingerprint_matches",
)
PRE_RESTART_MESSAGE = "Antes del reinicio: café, español y 日本語."
POST_RESTART_MESSAGE = "Después del reinicio: café, español y 日本語."
LAST_RECEIPT = "Validado ñ, 中文."
PHASES = (
    ("before_restart", PRE_RESTART_MESSAGE),
    ("after_restart", POST_RESTART_MESSAGE),
)


@dataclass(frozen=True)
class NativeV2AcceptanceCase:
    case_id: str
    label: str
    manifest_schema: str
    receipt_schema: str
    profile_fixture_path: str
    profile_fixture_schema: str
    profile_prefix: str
    group_prefix: str
    session_prefix: str
    sender_allowed_tools: tuple[str, ...]
    sender_enabled_mcp_tools: tuple[str, ...] | None
    sender_catalog: tuple[str, ...]
    sender_calls: tuple[str, ...]
    sender_prompt: str
    receiver_prompt: str
    requires_human_capability: bool
    native_surface: str


DEFAULT_CASE_ID = "collaboration"
GENERIC_MESSAGE_CASE_ID = "generic_message"
COORDINATION_CASE_ID = "coordination"
TASK_QUERY_CASE_ID = "task_query"
COORDINATION_SENDER_TOOLS = tuple(
    sorted(
        SENDER_TOOLS
        + (
            "assign_local_cao_task",
            "cancel_local_cao_task",
            "get_local_cao_task",
            "list_local_cao_peers",
        )
    )
)
_COORDINATION_SENDER_PROMPT = (
    "This profile is for a read-only local coordination and machine-to-machine transport diagnostic. "
    "Use exactly one cao-mcp-server_list_local_cao_peers call, then exactly one "
    "cao-mcp-server_send_message call for each requested delivery phase. "
    "Do not assign tasks, cancel tasks, pair instances, or use human-interaction, "
    "supervision, memory, or other tools."
)
_COLLABORATION_SENDER_PROMPT = (
    "This profile is for a machine-to-machine transport diagnostic. "
    "Use exactly one cao-mcp-server_list_siblings call, then exactly one "
    "cao-mcp-server_send_message call for each requested delivery phase. "
    "Do not use any human-interaction, delegation, supervision, or memory tool."
)
_GENERIC_MESSAGE_SENDER_PROMPT = (
    "This profile is for a machine-to-machine transport diagnostic. Use exactly one "
    "cao-mcp-server_list_siblings discovery call, then exactly one "
    "cao-mcp-server_send_message call for each requested delivery phase. Do not use any "
    "human-interaction, delegation, supervision, or memory tool."
)
_RECEIVER_PROMPT = (
    "You are a passive receiver for an automated machine-to-machine delivery check. "
    "A received message is not a human request. When one arrives, the public acknowledgement "
    "body must be exactly ACK. Do not add summaries, explanations, quotes, or message echoes "
    "to that body. Also append the active machine turn receipt required by the delivery "
    "contract as a separate final line. The receipt is protocol metadata, separate from the "
    "acknowledgement body. Do not use tools, ask questions, or send messages."
)
_GENERIC_RECEIVER_PROMPT = _RECEIVER_PROMPT
CASE_DEFINITIONS = (
    NativeV2AcceptanceCase(
        case_id=DEFAULT_CASE_ID,
        label="t104-native-v2-full-collaboration",
        manifest_schema="spec025.t104.native-v2-collaboration-manifest.v2",
        receipt_schema="spec025.t104.opencode-v2-collaboration-messages.private.v2",
        profile_fixture_path=(
            "specs/025-provider-mcp-profiles/fixtures/"
            "native-v2-collaboration-acceptance-profiles.json"
        ),
        profile_fixture_schema="caos.native-v2-collaboration-acceptance-profiles.v1",
        profile_prefix="t104-v2",
        group_prefix="spec025-t104-v2",
        session_prefix="t104v2",
        sender_allowed_tools=("@cao-mcp-server",),
        sender_enabled_mcp_tools=None,
        sender_catalog=SENDER_TOOLS,
        sender_calls=SENDER_CALLS,
        sender_prompt=_COLLABORATION_SENDER_PROMPT,
        receiver_prompt=_RECEIVER_PROMPT,
        requires_human_capability=True,
        native_surface=(
            "full unfiltered collaboration profile; human capability issued and source-bound"
        ),
    ),
    NativeV2AcceptanceCase(
        case_id=GENERIC_MESSAGE_CASE_ID,
        label="t116-native-v2-generic-message",
        manifest_schema="spec025.t116.native-v2-generic-message-manifest.v1",
        receipt_schema="spec025.t116.opencode-v2-generic-message.private.v1",
        profile_fixture_path=(
            "specs/025-provider-mcp-profiles/fixtures/"
            "native-v2-generic-message-acceptance-profiles.json"
        ),
        profile_fixture_schema="caos.native-v2-generic-message-acceptance-profiles.v1",
        profile_prefix="t116-v2-generic-message",
        group_prefix="spec025-t116-generic-v2",
        session_prefix="t116gm",
        sender_allowed_tools=("@cao-mcp-server", "discovery"),
        sender_enabled_mcp_tools=("list_siblings", "send_message"),
        sender_catalog=("list_siblings", "send_message"),
        sender_calls=("list_siblings", "send_message", "send_message"),
        sender_prompt=_GENERIC_MESSAGE_SENDER_PROMPT,
        receiver_prompt=_RECEIVER_PROMPT,
        requires_human_capability=False,
        native_surface=(
            "exact two-tool generic-message profile; no human capability claimed or issued"
        ),
    ),
    NativeV2AcceptanceCase(
        case_id=COORDINATION_CASE_ID,
        label="t166-native-v2-coordination-read",
        manifest_schema="spec025.t166.native-v2-coordination-manifest.v1",
        receipt_schema="spec025.t166.opencode-v2-coordination.private.v1",
        profile_fixture_path="specs/025-provider-mcp-profiles/fixtures/native-v2-coordination-acceptance-profiles.json",
        profile_fixture_schema="caos.native-v2-coordination-acceptance-profiles.v1",
        profile_prefix="t166-v2-coordination",
        group_prefix="spec025-t166-coordination-v2",
        session_prefix="t166coord",
        sender_allowed_tools=("@cao-mcp-server",),
        sender_enabled_mcp_tools=None,
        sender_catalog=COORDINATION_SENDER_TOOLS,
        sender_calls=("list_local_cao_peers", "send_message", "send_message"),
        sender_prompt=_COORDINATION_SENDER_PROMPT,
        receiver_prompt=_RECEIVER_PROMPT,
        requires_human_capability=True,
        native_surface="unfiltered coordination12; one authorized read and two exact message phases",
    ),
    NativeV2AcceptanceCase(
        case_id=TASK_QUERY_CASE_ID,
        label="t214-native-v2-task-query",
        manifest_schema="spec025.t214.native-v2-task-query-manifest.v1",
        receipt_schema="spec025.t214.opencode-v2-task-query.private.v1",
        profile_fixture_path="specs/025-provider-mcp-profiles/fixtures/native-v2-task-query-acceptance-profiles.json",
        profile_fixture_schema="caos.native-v2-task-query-acceptance-profiles.v1",
        profile_prefix="t214-v2-task-query",
        group_prefix="spec025-t214-task-query-v2",
        session_prefix="t214query",
        sender_allowed_tools=("@cao-mcp-server",),
        sender_enabled_mcp_tools=None,
        sender_catalog=COORDINATION_SENDER_TOOLS,
        sender_calls=("get_local_cao_task",),
        sender_prompt=(
            "Query the supplied completed diagnostic task with exactly one "
            "cao-mcp-server_get_local_cao_task call. Report its terminal state briefly. "
            "Do not use any other tool or change files."
        ),
        receiver_prompt=_RECEIVER_PROMPT,
        requires_human_capability=True,
        native_surface="unfiltered coordination12; one owned synthetic completed-task query; passive receiver",
    ),
)

SOURCE_PATHS = (
    "src",
    "test/live_provider",
    "test/fixtures/cao_server.py",
    "test/postgres/fixtures.py",
    "specs/025-provider-mcp-profiles/fixtures/run_native_v2_acceptance.py",
    "specs/025-provider-mcp-profiles/fixtures/native-v2-collaboration-acceptance-profiles.json",
    "specs/025-provider-mcp-profiles/fixtures/native-v2-generic-message-acceptance-profiles.json",
    "specs/025-provider-mcp-profiles/fixtures/native-v2-coordination-acceptance-profiles.json",
    "specs/025-provider-mcp-profiles/fixtures/native-v2-task-query-acceptance-profiles.json",
    "specs/025-provider-mcp-profiles/fixtures/test_native_task_query_case.py",
    "specs/025-provider-mcp-profiles/fixtures/test_native_v2_acceptance.py",
    "specs/025-provider-mcp-profiles/fixtures/native_v2_text_probe.py",
    "specs/025-provider-mcp-profiles/fixtures/test_native_v2_text_probe.py",
    "specs/025-provider-mcp-profiles/fixtures/native_v2_store.py",
    "specs/025-provider-mcp-profiles/fixtures/test_native_v2_store.py",
    "test/providers/test_opencode_v2_store.py",
    "test/services/test_turn_receipt_canonicalization.py",
    "test/providers/test_opencode_v2_result_binding.py",
    "test/providers/test_opencode_v2_canonical_result.py",
    "specs/025-provider-mcp-profiles/fixtures/native_phase_readers.py",
    "specs/025-provider-mcp-profiles/fixtures/native_v1_probe.py",
)
CLOSED_CANDIDATE_RELATIVE_PATH = (
    "specs/025-provider-mcp-profiles/fixtures/run_native_v2_acceptance.py"
)
_CLOSED_FLAG_BYTES = b"RUN_ENABLED = " + b"False"
_EXECUTED_FLAG_BYTES = b"RUN_ENABLED = " + b"True"
_MAX_PRIVATE_MANIFEST_BYTES = 256 * 1024
SOURCE_ENTRIES = (
    str(REPO / "src"),
    str(REPO),
    str(REPO / "test/live_provider"),
)
RUN_SOURCE_MODULES = (
    "test.fixtures.cao_server",
    "test.live_provider.message_api_restart_live",
    "message_api_restart",
    "test.live_provider.opencode_interaction_journey",
    "test.postgres.fixtures",
    "cli_agent_orchestrator.clients.postgres.connection",
    "cli_agent_orchestrator.models.opencode_agent",
    "cli_agent_orchestrator.utils.opencode_permissions",
    "cli_agent_orchestrator.utils.opencode_policy",
    "cli_agent_orchestrator.security.auth",
    "cli_agent_orchestrator.api.main",
    "cli_agent_orchestrator.api.local_coordination_routes",
    "cli_agent_orchestrator.services.local_peer_service",
    "cli_agent_orchestrator.api.interactions",
    "cli_agent_orchestrator.services.terminal_service",
    "cli_agent_orchestrator.services.provider_lifecycle",
    "cli_agent_orchestrator.services.interactions.service",
    "cli_agent_orchestrator.providers.opencode_cli",
    "cli_agent_orchestrator.providers.mcp_interactions",
    "cli_agent_orchestrator.utils.mcp_tool_policy",
    "test.live_provider.opencode_http_tool_offer_observer",
    "test.live_provider.opencode_http_prompt_observer",
    "test.live_provider.opencode_http_response_argument_observer",
    "test.live_provider.receipt_verification_observer",
    "cli_agent_orchestrator.utils.receipt_verification_diagnostics",
    "cli_agent_orchestrator.utils.opencode_config",
    "cli_agent_orchestrator.utils.opencode_ownership",
    "cli_agent_orchestrator.utils.opencode_v2",
    "cli_agent_orchestrator.utils.opencode_v2_store",
    "cli_agent_orchestrator.utils.opencode_v2_result_binding",
)
RUN_SOURCE_RELATIVE_PATHS = {
    "test.live_provider.receipt_verification_observer": (
        "test/live_provider/receipt_verification_observer.py"
    ),
    "cli_agent_orchestrator.utils.receipt_verification_diagnostics": (
        "src/cli_agent_orchestrator/utils/receipt_verification_diagnostics.py"
    ),
    "test.fixtures.cao_server": "test/fixtures/cao_server.py",
    "test.live_provider.message_api_restart_live": (
        "test/live_provider/message_api_restart_live.py"
    ),
    "message_api_restart": "test/live_provider/message_api_restart.py",
    "test.live_provider.opencode_interaction_journey": (
        "test/live_provider/opencode_interaction_journey.py"
    ),
    "test.postgres.fixtures": "test/postgres/fixtures.py",
    "cli_agent_orchestrator.clients.postgres.connection": (
        "src/cli_agent_orchestrator/clients/postgres/connection.py"
    ),
    "cli_agent_orchestrator.models.opencode_agent": (
        "src/cli_agent_orchestrator/models/opencode_agent.py"
    ),
    "cli_agent_orchestrator.utils.opencode_permissions": (
        "src/cli_agent_orchestrator/utils/opencode_permissions.py"
    ),
    "cli_agent_orchestrator.utils.opencode_policy": (
        "src/cli_agent_orchestrator/utils/opencode_policy.py"
    ),
    "cli_agent_orchestrator.security.auth": "src/cli_agent_orchestrator/security/auth.py",
    "cli_agent_orchestrator.api.main": "src/cli_agent_orchestrator/api/main.py",
    "cli_agent_orchestrator.api.local_coordination_routes": "src/cli_agent_orchestrator/api/local_coordination_routes.py",
    "cli_agent_orchestrator.services.local_peer_service": "src/cli_agent_orchestrator/services/local_peer_service.py",
    "cli_agent_orchestrator.api.interactions": "src/cli_agent_orchestrator/api/interactions.py",
    "cli_agent_orchestrator.services.terminal_service": (
        "src/cli_agent_orchestrator/services/terminal_service.py"
    ),
    "cli_agent_orchestrator.services.provider_lifecycle": (
        "src/cli_agent_orchestrator/services/provider_lifecycle.py"
    ),
    "cli_agent_orchestrator.services.interactions.service": (
        "src/cli_agent_orchestrator/services/interactions/service.py"
    ),
    "cli_agent_orchestrator.providers.opencode_cli": (
        "src/cli_agent_orchestrator/providers/opencode_cli.py"
    ),
    "cli_agent_orchestrator.providers.mcp_interactions": (
        "src/cli_agent_orchestrator/providers/mcp_interactions.py"
    ),
    "cli_agent_orchestrator.utils.mcp_tool_policy": (
        "src/cli_agent_orchestrator/utils/mcp_tool_policy.py"
    ),
    "test.live_provider.opencode_http_tool_offer_observer": (
        "test/live_provider/opencode_http_tool_offer_observer.py"
    ),
    "test.live_provider.opencode_http_prompt_observer": (
        "test/live_provider/opencode_http_prompt_observer.py"
    ),
    "test.live_provider.opencode_http_response_argument_observer": (
        "test/live_provider/opencode_http_response_argument_observer.py"
    ),
    "cli_agent_orchestrator.utils.opencode_config": (
        "src/cli_agent_orchestrator/utils/opencode_config.py"
    ),
    "cli_agent_orchestrator.utils.opencode_ownership": (
        "src/cli_agent_orchestrator/utils/opencode_ownership.py"
    ),
    "cli_agent_orchestrator.utils.opencode_v2": ("src/cli_agent_orchestrator/utils/opencode_v2.py"),
    "cli_agent_orchestrator.utils.opencode_v2_store": (
        "src/cli_agent_orchestrator/utils/opencode_v2_store.py"
    ),
    "cli_agent_orchestrator.utils.opencode_v2_result_binding": (
        "src/cli_agent_orchestrator/utils/opencode_v2_result_binding.py"
    ),
}


class GateError(RuntimeError):
    """Fixed, content-free failure code suitable for the private receipt."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _case_definition(case_id: Any = DEFAULT_CASE_ID) -> NativeV2AcceptanceCase:
    """Resolve only one of the immutable source-defined native v2 cases."""
    if type(case_id) is str:
        for case in CASE_DEFINITIONS:
            if case.case_id == case_id:
                return case
    raise GateError("acceptance_case_invalid")


class _ControlledSigterm(BaseException):
    """Route a safe-point SIGTERM observation through owned-resource cleanup."""


def _cancellation_checkpoint() -> None:
    """Raise only at explicit safe points, never from the signal handler."""
    if _SIGTERM_PENDING and not _SIGTERM_CLEANUP_ACTIVE:
        raise _ControlledSigterm("SIGTERM received")


@contextlib.contextmanager
def _scoped_sigterm_handler():
    global _SIGTERM_CLEANUP_ACTIVE, _SIGTERM_PENDING
    if threading.current_thread() is not threading.main_thread():
        raise GateError("execution_requires_main_thread")
    previous = signal.getsignal(signal.SIGTERM)
    _SIGTERM_CLEANUP_ACTIVE = False
    _SIGTERM_PENDING = False

    def handle_sigterm(signum, frame):
        global _SIGTERM_PENDING
        _SIGTERM_PENDING = True

    signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        yield
    finally:
        _SIGTERM_CLEANUP_ACTIVE = False
        signal.signal(signal.SIGTERM, previous)


def _message_live_failure_stage(error: Any) -> str | None:
    message_live = sys.modules.get("test.live_provider.message_api_restart_live")
    if message_live is None:
        return None
    failure_type = getattr(message_live, "AcceptanceFailure", None)
    stages = getattr(message_live, "_RUN_STAGES", None)
    if (
        not isinstance(failure_type, type)
        or type(stages) is not frozenset
        or any(type(stage) is not str for stage in stages)
        or not isinstance(error, failure_type)
    ):
        return None
    try:
        stage = object.__getattribute__(error, "stage")
    except (AttributeError, TypeError):
        return None
    return stage if type(stage) is str and stage in stages else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _execution_source_bytes(closed_source: bytes) -> bytes:
    """Return the sole permitted private execution derivation of this runner."""
    if not isinstance(closed_source, bytes) or closed_source.count(_CLOSED_FLAG_BYTES) != 1:
        raise GateError("closed_candidate_flag_source_mismatch")
    if _EXECUTED_FLAG_BYTES in closed_source:
        raise GateError("closed_candidate_already_enabled")
    return closed_source.replace(_CLOSED_FLAG_BYTES, _EXECUTED_FLAG_BYTES, 1)


def _execution_source_matches(closed_source: bytes, executed_source: bytes) -> bool:
    try:
        return executed_source == _execution_source_bytes(closed_source)
    except GateError:
        return False


def _write_private_execution_copy(destination: Path) -> dict[str, str]:
    """Create a private copy that differs from the repository candidate only at RUN_ENABLED."""
    source_path = REPO / CLOSED_CANDIDATE_RELATIVE_PATH
    try:
        source_before = source_path.lstat()
        if (
            source_path.is_symlink()
            or not stat.S_ISREG(source_before.st_mode)
            or source_before.st_uid != os.getuid()
        ):
            raise GateError("closed_candidate_source_invalid")
        closed_source = source_path.read_bytes()
        source_after = source_path.lstat()
    except OSError:
        raise GateError("closed_candidate_source_unreadable") from None
    if (
        source_before.st_dev,
        source_before.st_ino,
        source_before.st_size,
        source_before.st_mtime_ns,
    ) != (source_after.st_dev, source_after.st_ino, source_after.st_size, source_after.st_mtime_ns):
        raise GateError("closed_candidate_source_changed")
    executed_source = _execution_source_bytes(closed_source)
    if not destination.is_absolute() or destination.is_relative_to(REPO):
        raise GateError("private_execution_copy_path_invalid")
    try:
        parent = destination.parent.resolve(strict=True)
        parent_info = parent.lstat()
    except OSError:
        raise GateError("private_execution_copy_parent_invalid") from None
    if (
        parent != destination.parent
        or parent.is_relative_to(REPO.resolve(strict=True))
        or not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.getuid()
        or stat.S_IMODE(parent_info.st_mode) != 0o700
    ):
        raise GateError("private_execution_copy_parent_invalid")
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError:
        raise GateError("private_execution_copy_destination_invalid") from None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(executed_source)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o600)
    except BaseException:
        with contextlib.suppress(OSError):
            destination.unlink()
        raise
    written = _read_owned_private_file(destination, maximum=len(executed_source))
    if not _execution_source_matches(closed_source, written):
        with contextlib.suppress(OSError):
            destination.unlink()
        raise GateError("private_execution_copy_verification_failed")
    return {
        "candidate_sha256": hashlib.sha256(closed_source).hexdigest(),
        "execution_candidate_sha256": hashlib.sha256(executed_source).hexdigest(),
    }


def _read_owned_private_file(path: Path, *, maximum: int) -> bytes:
    """Read one owned, mode-0600, single-link regular file without following links."""
    if not path.is_absolute():
        raise GateError("private_file_path_invalid")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(descriptor)
        path_before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size > maximum
            or (before.st_dev, before.st_ino) != (path_before.st_dev, path_before.st_ino)
            or path.is_symlink()
        ):
            raise GateError("private_file_identity_invalid")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            _cancellation_checkpoint()
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        path_after = path.lstat()
        identity = lambda item: (
            item.st_dev,
            item.st_ino,
            item.st_uid,
            item.st_nlink,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
            stat.S_IMODE(item.st_mode),
        )
        if (
            len(raw) > maximum
            or len(raw) != before.st_size
            or identity(before) != identity(after)
            or identity(after) != identity(path_after)
        ):
            raise GateError("private_file_changed_during_read")
        return raw
    except OSError:
        raise GateError("private_file_unreadable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _private_execution_copy_contract() -> bool:
    """Bind the executing file to the exact one-flag derivation of the repository source."""
    execution_path = Path(__file__).resolve(strict=True)
    if execution_path.is_relative_to(REPO.resolve(strict=True)):
        return False
    parent = execution_path.parent
    try:
        parent_info = parent.lstat()
        closed_source = (REPO / CLOSED_CANDIDATE_RELATIVE_PATH).read_bytes()
        executed_source = _read_owned_private_file(execution_path, maximum=len(closed_source))
    except (OSError, GateError, RuntimeError):
        return False
    return (
        stat.S_ISDIR(parent_info.st_mode)
        and parent_info.st_uid == os.getuid()
        and stat.S_IMODE(parent_info.st_mode) == 0o700
        and _execution_source_matches(closed_source, executed_source)
    )


def _source_files(*, expected_head: str | None = None) -> list[str]:
    head = expected_head if expected_head is not None else EXPECTED_HEAD
    if head is None:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    if type(head) is not str or re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise GateError("source_head_invalid")
    tracked = subprocess.check_output(
        ["git", "ls-tree", "-r", "-z", "--name-only", head, "--", *SOURCE_PATHS],
        cwd=REPO,
    )
    files = []
    for index, item in enumerate(tracked.split(b"\0")):
        if item:
            if index % 64 == 0:
                _cancellation_checkpoint()
            files.append(item.decode("utf-8"))
    if not files:
        raise GateError("source_file_inventory_empty")
    return sorted(files)


def _source_fingerprints(*, expected_head: str | None = None) -> dict[str, str]:
    files = _source_files(expected_head=expected_head)
    if not files:
        raise GateError("source_file_inventory_empty")
    fingerprints = {}
    for index, name in enumerate(files):
        if index % 16 == 0:
            _cancellation_checkpoint()
        fingerprints[name] = _sha256(REPO / name)
    return fingerprints


def _digest_shape(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _canonical_private_run_root(value: Any) -> bool:
    if type(value) is not str or not Path(value).is_absolute():
        return False
    path = Path(value)
    try:
        resolved = path.resolve(strict=True)
        repository = REPO.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return False
    return path == resolved and not resolved.is_relative_to(repository)


def _manifest_document_valid(
    document: Any,
    *,
    expected_head: str,
    review_digest: str,
    runner_sha256: str,
    expected_case_id: str = DEFAULT_CASE_ID,
) -> bool:
    if not isinstance(document, dict):
        return False
    try:
        case = _case_definition(expected_case_id)
    except GateError:
        return False
    source_hashes = document.get("source_sha256s")
    if (
        document.get("schema") != case.manifest_schema
        or document.get("case_id") != case.case_id
        or document.get("case_profile_fixture_path") != case.profile_fixture_path
        or document.get("case_profile_fixture_schema") != case.profile_fixture_schema
        or document.get("receipt_schema") != case.receipt_schema
        or document.get("artifact_label") != case.label
        or document.get("run_enabled") is not True
        or document.get("expected_source_head") != expected_head
        or document.get("review_digest") != review_digest
        or document.get("execution_candidate_sha256") != runner_sha256
        or document.get("repository_root") != str(REPO.resolve(strict=True))
        or document.get("model") != MODEL
        or document.get("source_paths") != list(SOURCE_PATHS)
        or not isinstance(source_hashes, dict)
        or any(
            not isinstance(path, str) or not _digest_shape(digest)
            for path, digest in source_hashes.items()
        )
        or type(document.get("source_file_count")) is not int
        or document.get("source_file_count") != len(source_hashes)
        or not _digest_shape(document.get("source_fingerprint_sha256"))
        or type(document.get("postgres_autostart")) is not int
        or document.get("postgres_autostart") != 0
        or type(document.get("owner_uid")) is not int
        or document.get("owner_uid") != os.getuid()
    ):
        return False
    fingerprint = hashlib.sha256(
        json.dumps(source_hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if document.get("source_fingerprint_sha256") != fingerprint:
        return False
    if set(source_hashes) != set(_source_files(expected_head=expected_head)):
        return False
    if not _digest_shape(document.get("operator_auth_snapshot_sha256")):
        return False
    if any(
        field in document
        for field in (
            "allowed_tools",
            "sender_allowed_tools",
            "sender_catalog",
            "sender_tools",
            "receiver_allowed_tools",
            "receiver_tools",
            "enabled_mcp_tools",
            "mcp_tool_filter",
            "tool_names",
        )
    ):
        return False
    for field in (
        "candidate_sha256",
        "execution_candidate_sha256",
        "profile_fixture_sha256",
        "python_sha256",
        "binary_sha256",
        "postgres_fixture_sha256",
        "postgres_guard_derivation_sha256",
    ):
        if not _digest_shape(document.get(field)):
            return False
    if (
        document.get("profile_fixture_sha256") != source_hashes.get(case.profile_fixture_path)
        or not _case_fixture_contract(case.case_id)
        or document.get("postgres_fixture_sha256") != source_hashes.get("test/postgres/fixtures.py")
        or document.get("candidate_sha256") != source_hashes.get(CLOSED_CANDIDATE_RELATIVE_PATH)
    ):
        return False
    try:
        closed_source = (REPO / CLOSED_CANDIDATE_RELATIVE_PATH).read_bytes()
        expected_execution_source = _execution_source_bytes(closed_source)
    except (OSError, GateError):
        return False
    if hashlib.sha256(closed_source).hexdigest() != document.get(
        "candidate_sha256"
    ) or hashlib.sha256(expected_execution_source).hexdigest() != document.get(
        "execution_candidate_sha256"
    ):
        return False
    python_path = document.get("python_path")
    binary_path = document.get("binary_path")
    run_root = document.get("run_root")
    postgres_id = document.get("postgres_container_id")
    postgres_image = document.get("postgres_image_id")
    return (
        type(python_path) is str
        and Path(python_path).is_absolute()
        and _python_manifest_identity_matches(document)
        and type(binary_path) is str
        and Path(binary_path).is_absolute()
        and _canonical_private_run_root(run_root)
        and type(document.get("repository_root")) is str
        and Path(document["repository_root"]).is_absolute()
        and type(postgres_id) is str
        and re.fullmatch(r"[0-9a-f]{64}", postgres_id) is not None
        and type(postgres_image) is str
        and re.fullmatch(r"sha256:[0-9a-f]{64}", postgres_image) is not None
    )


def _python_manifest_identity_matches(document: dict[str, Any]) -> bool:
    """Bind the venv launch alias and its canonical interpreter as separate pins."""
    try:
        launch_path = Path(sys.executable)
        resolved_path = launch_path.resolve(strict=True)
        manifest_launch = document.get("python_path")
        manifest_resolved = document.get("python_resolved_path")
        return (
            launch_path.is_absolute()
            and type(manifest_launch) is str
            and manifest_launch == str(launch_path)
            and type(manifest_resolved) is str
            and Path(manifest_resolved).is_absolute()
            and Path(manifest_resolved) == resolved_path
            and not resolved_path.is_symlink()
            and resolved_path.is_file()
        )
    except (OSError, RuntimeError, ValueError):
        return False


def _load_run_manifest(
    path: Path,
    *,
    manifest_sha256: str,
    expected_head: str,
    review_digest: str,
    case_id: str = DEFAULT_CASE_ID,
) -> dict[str, Any]:
    try:
        case = _case_definition(case_id)
    except GateError:
        raise GateError("acceptance_case_invalid") from None
    try:
        resolved_manifest_path = path.resolve(strict=True)
    except OSError:
        raise GateError("private_run_manifest_invalid") from None
    if not path.is_absolute() or path.is_relative_to(REPO) or resolved_manifest_path != path:
        raise GateError("private_run_manifest_invalid")
    try:
        manifest_bytes = _read_owned_private_file(path, maximum=_MAX_PRIVATE_MANIFEST_BYTES)
    except GateError:
        raise GateError("private_run_manifest_invalid") from None
    if (
        not _digest_shape(manifest_sha256)
        or hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256
    ):
        raise GateError("private_run_manifest_digest_mismatch")
    if not _digest_shape(review_digest):
        raise GateError("review_digest_invalid")
    if re.fullmatch(r"[0-9a-f]{40}", expected_head) is None:
        raise GateError("expected_source_head_invalid")
    try:
        document = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise GateError("private_run_manifest_invalid") from None
    runner_sha256 = _sha256(Path(__file__))
    if not _manifest_document_valid(
        document,
        expected_head=expected_head,
        review_digest=review_digest,
        runner_sha256=runner_sha256,
        expected_case_id=case.case_id,
    ):
        raise GateError("private_run_manifest_contract_mismatch")
    try:
        if not _source_head_tree_clean(expected_head):
            raise GateError("source_head_or_runtime_tree_mismatch")
        fingerprint = _source_fingerprints(expected_head=expected_head)
    except (OSError, subprocess.CalledProcessError):
        raise GateError("source_fingerprint_unavailable") from None
    if fingerprint != document["source_sha256s"]:
        raise GateError("source_file_hash_mismatch")
    if (
        hashlib.sha256(
            json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        != document["source_fingerprint_sha256"]
    ):
        raise GateError("source_fingerprint_mismatch")
    run_root = Path(document["run_root"])
    try:
        run_root_info = run_root.lstat()
        run_root_empty = not any(run_root.iterdir())
    except OSError:
        raise GateError("private_run_root_invalid") from None
    if (
        run_root.is_symlink()
        or not stat.S_ISDIR(run_root_info.st_mode)
        or run_root_info.st_uid != os.getuid()
        or stat.S_IMODE(run_root_info.st_mode) != 0o700
        or not _canonical_private_run_root(str(run_root))
        or not run_root_empty
    ):
        raise GateError("private_run_root_invalid")
    try:
        resolved_python_path = Path(document["python_resolved_path"])
        if (
            resolved_python_path.is_symlink()
            or not resolved_python_path.is_file()
            or resolved_python_path.resolve(strict=True) != resolved_python_path
            or _sha256(resolved_python_path) != document["python_sha256"]
        ):
            raise GateError("python_interpreter_identity_mismatch")
    except (OSError, RuntimeError, ValueError, KeyError):
        raise GateError("python_interpreter_identity_mismatch") from None
    for path_key, digest_key in (("binary_path", "binary_sha256"),):
        executable = Path(document[path_key])
        if (
            executable.is_symlink()
            or not executable.is_file()
            or _sha256(executable) != document[digest_key]
        ):
            raise GateError(f"{path_key.removesuffix('_path')}_identity_mismatch")
    global EXPECTED_HEAD, PINNED_PYTHON, PINNED_PYTHON_RESOLVED, PINNED_OPENCODE
    global PINNED_OPENCODE_SHA256, PINNED_POSTGRES_ID, PINNED_POSTGRES_IMAGE
    global PINNED_POSTGRES_FIXTURE_SHA256, PINNED_POSTGRES_GUARD_DERIVATION_SHA256
    global PREP, RECEIPT, EVIDENCE, RUN_MANIFEST, RUN_MANIFEST_SHA256
    global REVIEW_DIGEST, SOURCE_FINGERPRINT_SHA256, RUNNER_SHA256_AT_ADMISSION
    EXPECTED_HEAD = expected_head
    PINNED_PYTHON = Path(document["python_path"])
    PINNED_PYTHON_RESOLVED = Path(document["python_resolved_path"])
    PINNED_OPENCODE = Path(document["binary_path"])
    PINNED_OPENCODE_SHA256 = document["binary_sha256"]
    PINNED_POSTGRES_ID = document["postgres_container_id"]
    PINNED_POSTGRES_IMAGE = document["postgres_image_id"]
    PINNED_POSTGRES_FIXTURE_SHA256 = document["postgres_fixture_sha256"]
    PINNED_POSTGRES_GUARD_DERIVATION_SHA256 = document["postgres_guard_derivation_sha256"]
    PREP = run_root
    RECEIPT = PREP / f"{case.label}-receipt.json"
    EVIDENCE = PREP / f"{case.label}-evidence"
    RUN_MANIFEST = document
    RUN_MANIFEST_SHA256 = manifest_sha256
    REVIEW_DIGEST = review_digest
    SOURCE_FINGERPRINT_SHA256 = document["source_fingerprint_sha256"]
    RUNNER_SHA256_AT_ADMISSION = runner_sha256
    return document


def _runtime_path_valid() -> bool:
    if PINNED_PYTHON is None or PINNED_PYTHON_RESOLVED is None:
        return False
    try:
        launch_path = Path(sys.executable)
        resolved_path = launch_path.resolve(strict=True)
        return (
            launch_path.is_absolute()
            and launch_path == PINNED_PYTHON
            and resolved_path == PINNED_PYTHON_RESOLVED
            and not resolved_path.is_symlink()
        )
    except (OSError, RuntimeError, ValueError):
        return False


def _prepend_current_source() -> None:
    os.environ["PYTHONPATH"] = os.pathsep.join(SOURCE_ENTRIES)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    for entry in SOURCE_ENTRIES:
        while entry in sys.path:
            sys.path.remove(entry)
    sys.path[:0] = list(SOURCE_ENTRIES)


def _source_head_tree_clean(expected_head: str) -> bool:
    if type(expected_head) is not str or re.fullmatch(r"[0-9a-f]{40}", expected_head) is None:
        return False
    try:
        current_head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip()
        if current_head != expected_head:
            return False
        status = subprocess.check_output(
            [
                "git",
                "status",
                "--porcelain",
                "--untracked-files=all",
                "-z",
                "--",
                *SOURCE_PATHS,
            ],
            cwd=REPO,
        )
        return status == b""
    except (OSError, subprocess.CalledProcessError):
        return False


def _source_tree_clean() -> bool:
    expected_head = EXPECTED_HEAD
    if expected_head is None:
        try:
            expected_head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return False
    try:
        return _source_head_tree_clean(expected_head) and _source_fingerprints(
            expected_head=expected_head
        ) == (RUN_MANIFEST or {}).get("source_sha256s")
    except (OSError, subprocess.CalledProcessError):
        return False


def _available_memory_mib() -> int:
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    raise GateError("memory_observation_unavailable")


def _admit(receipt: dict[str, Any], phase: str) -> None:
    _cancellation_checkpoint()
    available = _available_memory_mib()
    receipt.setdefault("memory_admissions_mib", {})[phase] = available
    if available < MEMORY_FLOOR_MIB:
        raise GateError("memory_below_unchanged_6144_mib_floor")


def _private_directory(path: Path, *, parent: Path | None = None) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise GateError("private_directory_invalid")
    if parent is not None and path.parent.resolve(strict=True) != parent.resolve(strict=True):
        raise GateError("private_directory_parent_invalid")
    return path


def _write_private(path: Path, value: str | bytes) -> None:
    encoded = value.encode("utf-8") if isinstance(value, str) else value
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def _derive_private_mcp_observer_source(
    source: bytes,
    *,
    sender_catalog: tuple[str, ...],
    expected_message_bodies: tuple[str, ...],
    expected_safe_result_tool: str | None = None,
) -> str:
    """Enable the fixed-body argument probe only in the source-derived private copy."""
    if (
        type(source) is not bytes
        or type(sender_catalog) is not tuple
        or any(type(name) is not str for name in sender_catalog)
        or type(expected_message_bodies) is not tuple
        or len(expected_message_bodies) != len(PHASES)
        or any(type(body) is not str for body in expected_message_bodies)
        or (
            expected_safe_result_tool is not None
            and (
                type(expected_safe_result_tool) is not str
                or expected_safe_result_tool not in {"list_local_cao_peers", "get_local_cao_task"}
            )
        )
    ):
        raise GateError("observer_source_binding_shape_changed")
    try:
        original = source.decode("utf-8", errors="strict")
    except UnicodeError:
        raise GateError("observer_source_binding_shape_changed") from None
    start_token = "INTERACTION_TOOLS = frozenset("
    observer_end_token = "_NONCE_RE ="
    try:
        functions = [
            node
            for node in ast.parse(original).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "install_protocol_observer_from_env"
        ]
        returns = (
            [node for node in functions[0].body if isinstance(node, ast.Return)]
            if len(functions) == 1
            else []
        )
        if (
            len(returns) != 1
            or not isinstance(returns[0].value, ast.Call)
            or not isinstance(returns[0].value.func, ast.Name)
            or returns[0].value.func.id != "install_protocol_observer"
            or len(returns[0].value.args) != 3
            or [ast.unparse(arg) for arg in returns[0].value.args]
            != ["low_level_server", "Path(raw_path)", "nonce"]
        ):
            raise ValueError("observer_return_shape_invalid")
        old_return = ast.get_source_segment(original, returns[0])
        call = returns[0].value
        call.args[1] = ast.parse(
            "Path(raw_path).with_name(Path(raw_path).name + '-' + terminal)", mode="eval"
        ).body
        # Preserve the production installer's task path/requester bindings.
        # The derived run selects its own capture mode and fixed message probe.
        call.keywords = [
            keyword
            for keyword in call.keywords
            if keyword.arg not in {"expected_safe_result_tool", "expected_message_bodies"}
        ]
        call.keywords.extend(
            [
                ast.keyword(
                    arg="expected_safe_result_tool", value=ast.Constant(expected_safe_result_tool)
                ),
                ast.keyword(
                    arg="expected_message_bodies",
                    value=ast.Name(id="_MESSAGE_ARGUMENT_EXPECTATIONS", ctx=ast.Load()),
                ),
            ]
        )
        adapted_return = ast.unparse(returns[0])
    except (SyntaxError, ValueError):
        raise GateError("observer_source_binding_shape_changed") from None
    if (
        original.count(start_token) != 1
        or original.count(observer_end_token) != 1
        or type(old_return) is not str
        or original.count(old_return) != 1
    ):
        raise GateError("observer_source_binding_shape_changed")
    observer_start = original.index(start_token)
    observer_end = original.index(observer_end_token, observer_start)
    adapted = (
        original[:observer_start]
        + start_token
        + repr(sorted(sender_catalog))
        + ")\n"
        + "_MESSAGE_ARGUMENT_EXPECTATIONS = "
        + repr(expected_message_bodies)
        + "\n"
        + original[observer_end:]
    )
    new_return = (
        "terminal = selected_environment.get('CAO_TERMINAL_ID', '')\n"
        "    if not re.fullmatch(r'[0-9a-f]{8}', terminal):\n"
        "        raise RuntimeError('mcp_observer_terminal_binding_invalid')\n"
        "    " + adapted_return
    )
    return adapted.replace(old_return, new_return, 1)


def _safe_terminal_identity(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{8}", value) is not None


def _validated_returned_session_name(
    requested: Any, returned: Any, case_id: Any = DEFAULT_CASE_ID
) -> str:
    """Bind the API's normalized receiver name and return that exact value."""
    case = _case_definition(case_id)
    request_pattern = rf"{re.escape(case.session_prefix)}-[0-9a-f]{{16}}"
    if not isinstance(requested, str) or re.fullmatch(request_pattern, requested) is None:
        raise GateError("requested_session_name_invalid")
    if not isinstance(returned, str) or re.fullmatch(r"cao-" + request_pattern, returned) is None:
        raise GateError("returned_session_name_invalid")
    if returned != f"cao-{requested}":
        raise GateError("returned_session_name_mismatch")
    return returned


def _owned_session_cleanup_name(requested: Any, case_id: Any = DEFAULT_CASE_ID) -> str:
    """Return only this run's validated request token for best-effort cleanup.

    The pinned API normalizes the create token with ``cao-`` on response and
    accepts the original token on DELETE. This fallback never trusts response
    data and is established before the creation request is sent.
    """
    case = _case_definition(case_id)
    request_pattern = rf"{re.escape(case.session_prefix)}-[0-9a-f]{{16}}"
    if not isinstance(requested, str) or re.fullmatch(request_pattern, requested) is None:
        raise GateError("owned_cleanup_session_name_invalid")
    return requested


def _receiver_cleanup_target(
    requested: Any,
    *,
    creation_attempted: Any,
    status_code: Any,
    json_parsed: Any,
    returned: Any,
    case_id: Any = DEFAULT_CASE_ID,
) -> str | None:
    """Use a valid returned name, otherwise retain only the own request token."""
    if creation_attempted is not True:
        return None
    own_target = _owned_session_cleanup_name(requested, case_id)
    if type(status_code) is not int or status_code != 201 or json_parsed is not True:
        return own_target
    try:
        return _validated_returned_session_name(requested, returned, case_id)
    except GateError:
        return own_target


def _session_cleanup_due(
    creation_attempted: Any, cleanup_session_name: Any, deletion_attempted: Any
) -> bool:
    return (
        creation_attempted is True
        and isinstance(cleanup_session_name, str)
        and bool(cleanup_session_name)
        and deletion_attempted is False
    )


def _case_fixture_contract(case_id: Any = DEFAULT_CASE_ID) -> bool:
    """Require the private profile fixture to match its fixed source definition."""
    try:
        case = _case_definition(case_id)
        document = json.loads((REPO / case.profile_fixture_path).read_text(encoding="utf-8"))
    except (GateError, OSError, ValueError, UnicodeError):
        return False
    profiles = document.get("profiles") if isinstance(document, dict) else None
    sender = profiles.get("sender") if isinstance(profiles, dict) else None
    receiver = profiles.get("receiver") if isinstance(profiles, dict) else None
    checks = document.get("required_checks") if isinstance(document, dict) else None
    request_observation = (
        document.get("native_model_request_observation") if isinstance(document, dict) else None
    )
    expected_sender_filter = (
        None if case.sender_enabled_mcp_tools is None else list(case.sender_enabled_mcp_tools)
    )
    sender_capability = "Required" if case.requires_human_capability else "Absent"
    if (
        not isinstance(document, dict)
        or document.get("schema") != case.profile_fixture_schema
        or ("case_id" in document and document.get("case_id") != case.case_id)
        or not isinstance(sender, dict)
        or sender.get("allowed_tools") != list(case.sender_allowed_tools)
        or sender.get("native_tool_profile") != _case_native_profile("sender", case.case_id)
        or sender.get("enabled_mcp_tools") != expected_sender_filter
        or not isinstance(sender.get("runtime_capability"), str)
        or (
            not sender["runtime_capability"].startswith(sender_capability)
            if case.requires_human_capability
            else sender["runtime_capability"] != "Absent"
        )
        or sender.get("prompt") != case.sender_prompt
        or not isinstance(receiver, dict)
        or receiver.get("allowed_tools") != []
        or receiver.get("native_tool_profile") != _case_native_profile("receiver", case.case_id)
        or receiver.get("enabled_mcp_tools") != []
        or receiver.get("runtime_capability") != "Absent"
        or receiver.get("prompt") != case.receiver_prompt
        or document.get("catalog") != list(case.sender_catalog)
        or not isinstance(checks, dict)
        or not isinstance(request_observation, dict)
        or type(request_observation.get("minimum_target_primary_requests_per_role")) is not int
        or request_observation.get("minimum_target_primary_requests_per_role") != len(PHASES)
    ):
        return False
    expected_calls = list(case.sender_calls)
    if case.case_id == TASK_QUERY_CASE_ID:
        return (
            document.get("label") == case.label
            and document.get("receipt_schema") == case.receipt_schema
            and document.get("sender_calls") == expected_calls
            and document.get("receiver_catalog") == []
            and checks
            == {
                "sender_calls_exact": expected_calls,
                "task_fixture": "owned_synthetic_completed",
                "api_restarts": 0,
                "messages": 0,
            }
        )
    if case.case_id in {DEFAULT_CASE_ID, COORDINATION_CASE_ID}:
        if checks.get("sender_calls_exact") != expected_calls:
            return False
    elif document.get("case_id") != case.case_id or (
        document.get("label") != case.label
        or document.get("receipt_schema") != case.receipt_schema
        or document.get("sender_calls") != expected_calls
        or document.get("receiver_catalog") != []
        or document.get("capability_inventory")
        != {
            "sender": 0,
            "receiver": 0,
            "before_first_input": 0,
            "before_delete": 0,
            "after_delete": 0,
        }
    ):
        return False
    if case.case_id == GENERIC_MESSAGE_CASE_ID:
        observation = document.get("native_model_request_observation")
        expected_shared_acceptance = {
            "message_phases": 2,
            "api_restart_between_phases": True,
            "exact_pg_api_and_agui_rows": True,
            "exact_receiver_ack": "ACK",
            "literal_sender_unicode": LAST_RECEIPT,
            "model_phase_seconds": int(MODEL_PHASE_SECONDS),
            "memory_floor_mib": MEMORY_FLOOR_MIB,
            "owned_cleanup": True,
        }
        expected_capability_inventory = {
            "sender": 0,
            "receiver": 0,
            "before_first_input": 0,
            "before_delete": 0,
            "after_delete": 0,
        }
        expected_observation = {
            "sender_cao_mcp_wire_names": [
                "cao-mcp-server_list_siblings",
                "cao-mcp-server_send_message",
            ],
            "receiver_cao_mcp_wire_names": [],
            "source_approved_optional_native_builtins": list(OPTIONAL_OPENCODE_BUILTIN_TOOL_NAMES),
            "diagnostic_only_native_names_must_be_zero": True,
            "minimum_target_primary_requests_per_role": len(PHASES),
            "all_selected_model_primary_requests_must_match_role_profile": True,
            "builtin_execution_observation": "not_observed",
        }
        expected_checks = {
            "case_id_and_artifacts_bound_before_resources": True,
            "sender_exact_two_tool_catalog": list(case.sender_catalog),
            "sender_calls_exact": list(case.sender_calls),
            "sender_calls_from_canonical_cao_mcp_server": True,
            "receiver_catalog_empty": True,
            "receiver_calls_empty": True,
            "sender_has_no_runtime_human_capability": True,
            "receiver_has_no_runtime_human_capability": True,
            "capability_inventory_zero_before_first_input": True,
            "capability_inventory_zero_before_delete": True,
            "capability_inventory_zero_after_delete": True,
            "diagnostic_and_unexpected_model_tools_rejected": True,
            "shared_two_message_ack_unicode_restart_and_owned_cleanup_gates": True,
            "no_full_eight_human_capability_claim": True,
            "receipt_redacted_only_private_raw_evidence_separate": True,
        }
        expected_keys = {
            "schema",
            "case_id",
            "label",
            "receipt_schema",
            "scope",
            "profiles",
            "catalog",
            "sender_calls",
            "receiver_catalog",
            "capability_inventory",
            "native_model_request_observation",
            "shared_acceptance",
            "required_checks",
        }
        return (
            set(document) == expected_keys
            and set(sender)
            == {
                "allowed_tools",
                "native_tool_profile",
                "enabled_mcp_tools",
                "runtime_capability",
                "prompt",
            }
            and set(receiver)
            == {
                "allowed_tools",
                "native_tool_profile",
                "enabled_mcp_tools",
                "runtime_capability",
                "prompt",
            }
            and document.get("label") == case.label
            and document.get("receipt_schema") == case.receipt_schema
            and document.get("scope")
            == (
                "Source-defined native OpenCode v2 generic-message transport case with an exact "
                "two-tool sender and a zero-tool receiver. It does not exercise or claim human "
                "interaction capability."
            )
            and document.get("sender_calls") == list(case.sender_calls)
            and document.get("receiver_catalog") == []
            and document.get("capability_inventory") == expected_capability_inventory
            and document.get("shared_acceptance") == expected_shared_acceptance
            and isinstance(observation, dict)
            and observation == expected_observation
            and checks == expected_checks
            and sender.get("runtime_capability") == "Absent"
            and "capability_owner" not in sender
            and "legacy_three_tool_marker" not in sender
            and document.get("catalog") == list(case.sender_catalog)
            and len(case.sender_catalog) == 2
            and case.sender_enabled_mcp_tools == case.sender_catalog
            and case.sender_calls == ("list_siblings", "send_message", "send_message")
            and case.requires_human_capability is False
        )
    return (
        _coordination_fixture_contract(document, case)
        if case.case_id == COORDINATION_CASE_ID
        else True
    )


def _case_native_profile(role: str, case_id: Any = DEFAULT_CASE_ID) -> str:
    case = _case_definition(case_id)
    if role not in {"sender", "receiver"}:
        raise ValueError("invalid_role")
    return (
        "coordination"
        if role == "sender" and case.case_id in {COORDINATION_CASE_ID, TASK_QUERY_CASE_ID}
        else "collaboration"
    )


def _case_observer_options(case_id: Any = DEFAULT_CASE_ID) -> dict[str, Any]:
    case = _case_definition(case_id)
    if case.case_id == TASK_QUERY_CASE_ID:
        return {"expected_safe_result_tool": "get_local_cao_task"}
    return (
        {"expected_safe_result_tool": "list_local_cao_peers"}
        if case.case_id == COORDINATION_CASE_ID
        else {}
    )


def _case_mcp_wire_names(case_id: Any = DEFAULT_CASE_ID) -> tuple[str, ...]:
    case = _case_definition(case_id)
    return (
        tuple("cao-mcp-server_" + tool for tool in case.sender_catalog)
        if case.case_id in {COORDINATION_CASE_ID, TASK_QUERY_CASE_ID}
        else MCP_HTTP_OFFER_TOOL_NAMES
    )


def _case_http_offer_names(case_id: Any = DEFAULT_CASE_ID) -> tuple[str, ...]:
    return (
        _case_mcp_wire_names(case_id)
        + OPTIONAL_OPENCODE_BUILTIN_TOOL_NAMES
        + DIAGNOSTIC_ONLY_OPENCODE_TOOL_NAMES
    )


def _case_receipt_key(field: str, case_id: Any = DEFAULT_CASE_ID) -> str:
    case = _case_definition(case_id)
    if case.case_id not in {COORDINATION_CASE_ID, TASK_QUERY_CASE_ID}:
        return field
    return {
        "sender_native_v3_full_collaboration_unfiltered": "sender_native_v3_full_coordination_unfiltered",
        "sender_model_request_full_eight_cao_mcp_tools": "sender_model_request_full_twelve_cao_mcp_tools",
    }.get(field, field)


def _case_scope(case_id: Any = DEFAULT_CASE_ID) -> str:
    case = _case_definition(case_id)
    if case.case_id == TASK_QUERY_CASE_ID:
        return "one native coordination12 task query of an owned synthetic completed receipt; passive receiver, no messages or API restart"
    if case.case_id == GENERIC_MESSAGE_CASE_ID:
        return "one exact two-tool generic-message sender, one zero-tool receiver, two generic send_message phases around API restart"
    if case.case_id == COORDINATION_CASE_ID:
        return "one exact twelve-tool coordination sender, one zero-tool receiver, one authorized empty peer read and two send_message phases around API restart"
    return "one full-collaboration sender, one empty-tool receiver, two generic send_message phases around API restart"


def _coordination_context_contract(
    metadata: Any, *, sender_id: str, working_directory: str, peer_status: Any, peer_body: Any
) -> bool:
    return (
        isinstance(metadata, dict)
        and _safe_terminal_identity(sender_id)
        and type(working_directory) is str
        and bool(working_directory)
        and Path(working_directory).is_absolute()
        and metadata.get("id") == sender_id
        and metadata.get("working_directory") == working_directory
        and type(peer_status) is int
        and peer_status == 200
        and type(peer_body) is list
        and len(peer_body) == 0
    )


def _coordination_result_contract(sender: Any) -> bool:
    calls = sender.get("mcp_tool_calls") if isinstance(sender, dict) else None
    if type(calls) is not list or any(type(call) is not dict for call in calls):
        return False
    reads = [call for call in calls if call.get("tool") == "list_local_cao_peers"]
    return len(reads) == 1 and (
        reads[0].get("server") == "cao-mcp-server"
        and reads[0].get("status") == "completed"
        and reads[0].get("error_present") is False
        and reads[0].get("result_present") is True
        and reads[0].get("safe_result_success") is True
        and type(reads[0].get("safe_result_peer_count")) is int
        and reads[0]["safe_result_peer_count"] == 0
    )


def _task_query_prompt(task_id: Any) -> str:
    if (
        type(task_id) is not str
        or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", task_id
        )
        is None
    ):
        raise GateError("task_query_identity_invalid")
    return (
        "Call cao-mcp-server_get_local_cao_task exactly once with task_id "
        + task_id
        + ". This is an owned synthetic completed receipt. Report its state briefly. "
        "Do not use other tools or change files."
    )


def _task_query_result_contract(sender: Any) -> bool:
    calls = sender.get("mcp_tool_calls") if type(sender) is dict else None
    if type(calls) is not list or len(calls) != 1 or type(calls[0]) is not dict:
        return False
    call = calls[0]
    return (
        call.get("server") == "cao-mcp-server"
        and call.get("tool") == "get_local_cao_task"
        and call.get("status") == "completed"
        and call.get("error_present") is False
        and all(
            call.get(key) is True
            for key in (
                "result_present",
                "safe_result_success",
                "safe_result_task_binding_observed",
                "safe_result_task_argument_matches",
                "safe_result_task_id_matches",
                "safe_result_task_requester_matches",
                "safe_result_task_state_matches",
                "safe_result_task_result_state_matches",
            )
        )
        and call.get("safe_result_task_state") == "succeeded"
        and call.get("safe_result_task_result_state") == "succeeded"
    )


def _coordination_fixture_contract(document: dict[str, Any], case: NativeV2AcceptanceCase) -> bool:
    return (
        document.get("case_id") == case.case_id
        and document.get("label") == case.label
        and document.get("receipt_schema") == case.receipt_schema
        and document.get("sender_calls") == list(case.sender_calls)
        and document.get("receiver_catalog") == []
        and document.get("capability_inventory")
        == {
            "sender": 1,
            "receiver": 0,
            "before_first_input": 1,
            "before_delete": 1,
            "after_delete": 0,
        }
        and document.get("coordination_operation")
        == {
            "tool": "list_local_cao_peers",
            "calls": 1,
            "success": True,
            "peer_count": 0,
            "preflight_http_status": 200,
            "preflight_peer_count": 0,
            "mutating_calls": 0,
        }
        and document.get("native_model_request_observation")
        == {
            "sender_cao_mcp_wire_names": list(_case_mcp_wire_names(case.case_id)),
            "receiver_cao_mcp_wire_names": [],
            "source_approved_optional_native_builtins": list(OPTIONAL_OPENCODE_BUILTIN_TOOL_NAMES),
            "diagnostic_only_native_names_must_be_zero": True,
            "minimum_target_primary_requests_per_role": len(PHASES),
            "all_selected_model_primary_requests_must_match_role_profile": True,
            "builtin_execution_observation": "not_observed",
        }
        and document.get("required_checks")
        == {
            "sender_calls_exact": list(case.sender_calls),
            "sender_exact_twelve_tool_catalog": list(case.sender_catalog),
            "receiver_catalog_empty": True,
            "receiver_calls_empty": True,
            "coordination_context_preflight_passed": True,
            "coordination_read_success_observed": True,
            "human_capability_current_source_bound": True,
            "two_message_ack_unicode_restart_and_owned_cleanup": True,
            "model_phase_seconds": int(MODEL_PHASE_SECONDS),
            "memory_floor_mib": MEMORY_FLOOR_MIB,
            "no_assign_cancel_or_pair_calls": True,
            "receipt_has_no_peer_entries_or_raw_tool_result": True,
        }
    )


def _profile_contract(
    role: str, case_id: Any = DEFAULT_CASE_ID
) -> tuple[list[str], list[str] | None, str]:
    """Return fixed case grants, native filter and profile prompt for one role."""
    case = _case_definition(case_id)
    if role == "sender":
        allowed = list(case.sender_allowed_tools)
        enabled = (
            None if case.sender_enabled_mcp_tools is None else list(case.sender_enabled_mcp_tools)
        )
        return allowed, enabled, case.sender_prompt
    if role == "receiver":
        return [], [], case.receiver_prompt
    raise ValueError("invalid_role")


def _native_permission_matches(metadata: Any, allowed_tools: list[str], translator: Any) -> bool:
    """Match the OpenCode-native permission map emitted by the current installer."""
    if not isinstance(metadata, dict):
        return False
    permission = metadata.get("permission")
    return type(permission) is dict and permission == translator(allowed_tools)


def _phase_prompt(
    index: int, receiver_id: str, message: str, case_id: Any = DEFAULT_CASE_ID
) -> str:
    if index not in (0, 1) or not _safe_terminal_identity(receiver_id):
        raise ValueError("invalid_phase_binding")
    case = _case_definition(case_id)
    discover = (
        "First call cao-mcp-server_list_siblings exactly once and confirm the target ID is in the same group. "
        if index == 0
        else ""
    )
    if case.case_id == COORDINATION_CASE_ID:
        discover = (
            "First call cao-mcp-server_list_local_cao_peers exactly once; its authorized result must be success true with an empty peer list. "
            if index == 0
            else ""
        )
    restrictions = (
        "Do not assign tasks, cancel tasks, pair instances, or use human-interaction, supervision, or memory tools. "
        if case.case_id == COORDINATION_CASE_ID
        else "Do not use human-interaction, delegation, supervision, or memory tools, and do not use any operation beyond the requested message transport. "
    )
    return (
        "This is one automated machine-to-machine delivery check, not a human request. "
        + restrictions
        + discover
        + "Call cao-mcp-server_send_message exactly once with receiver_id "
        + receiver_id
        + " and message "
        + json.dumps(message, ensure_ascii=False)
        + ". Pass message as the exact JSON-decoded string, preserving every Unicode "
        + "code point, whitespace and punctuation verbatim. Do not translate, normalize, "
        + "or substitute characters or punctuation width. Do not retry or redeliver. "
        + "After a successful call, the public result body "
        + "must be exactly this line: "
        + LAST_RECEIPT
        + "\nThen comply with this turn's CAO completion receipt contract: append its "
        + "active receipt on a separate final line. That receipt is protocol metadata, "
        + "separate from the public result body. Do not add other result text."
    )


def _phase_contract(
    *,
    expected_message: str,
    sender_id: str,
    receiver_id: str,
    pg_rows: list[dict[str, Any]],
    api_rows: list[dict[str, Any]],
    phase_index: int,
    last_text: str,
    terminal: dict[str, Any],
    turn_sequence: Any,
    agui_matches: list[bool],
) -> bool:
    """Pure acceptance predicate for exactly one completed delivery phase."""
    from test.live_provider.message_api_restart import expected_persisted_message

    if (
        phase_index not in (0, 1)
        or expected_message != PHASES[phase_index][1]
        or not _safe_terminal_identity(sender_id)
        or not _safe_terminal_identity(receiver_id)
        or not isinstance(terminal, dict)
        or type(turn_sequence) is not int
        or turn_sequence < 1
        or terminal.get("turn_sequence") != turn_sequence
        or terminal.get("turn_completed") != turn_sequence
        or terminal.get("status") != "completed"
        or not isinstance(last_text, str)
        or LAST_RECEIPT not in last_text
        or len(agui_matches) != 1
        or agui_matches != [True]
        or len(pg_rows) != phase_index + 1
        or len(api_rows) != phase_index + 1
    ):
        return False
    for prior_index in range(phase_index + 1):
        expected = expected_persisted_message(PHASES[prior_index][1], sender_id)
        pg_matches = [row for row in pg_rows if row.get("message") == expected]
        if len(pg_matches) != 1:
            return False
        api_matches = [row for row in api_rows if row.get("id") == pg_matches[0].get("id")]
        if len(api_matches) != 1 or not _persisted_row_matches(
            pg_matches[0],
            api_matches[0],
            sender_id=sender_id,
            receiver_id=receiver_id,
            expected_message=expected,
        ):
            return False
    return True


def _receiver_ack_exact(text: Any) -> bool:
    """Verify the receiver's prompt-specified acknowledgement without retaining it."""
    return isinstance(text, str) and text.strip() == "ACK"


def _timestamps_equal(database_value: Any, api_value: Any) -> bool:
    try:
        database_time = (
            database_value
            if isinstance(database_value, dt.datetime)
            else dt.datetime.fromisoformat(str(database_value))
        )
        api_time = (
            api_value
            if isinstance(api_value, dt.datetime)
            else dt.datetime.fromisoformat(str(api_value))
        )
        if database_time.tzinfo is None or api_time.tzinfo is None:
            return database_time == api_time
        return database_time.astimezone(dt.timezone.utc) == api_time.astimezone(dt.timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return False


def _persisted_row_matches(
    pg_row: dict[str, Any],
    api_row: dict[str, Any],
    *,
    sender_id: str,
    receiver_id: str,
    expected_message: str,
) -> bool:
    fields = {"id", "sender_id", "receiver_id", "message", "status", "created_at"}
    return (
        set(pg_row) == fields
        and set(api_row) == fields
        and type(pg_row.get("id")) is int
        and pg_row["id"] > 0
        and type(api_row.get("id")) is int
        and api_row["id"] > 0
        and pg_row.get("status") == "delivered"
        and api_row.get("status") == "delivered"
        and all(
            isinstance(row.get(key), str) and row[key]
            for row in (pg_row, api_row)
            for key in ("sender_id", "receiver_id", "message")
        )
        # _pg_rows preserves the PostgreSQL driver's datetime object, while
        # _api_rows decodes the API's JSON datetime as an ISO string.
        and isinstance(pg_row.get("created_at"), dt.datetime)
        and isinstance(api_row.get("created_at"), str)
        and pg_row.get("sender_id") == sender_id
        and api_row.get("sender_id") == sender_id
        and pg_row.get("receiver_id") == receiver_id
        and api_row.get("receiver_id") == receiver_id
        and pg_row.get("message") == expected_message
        and api_row.get("message") == expected_message
        and api_row.get("id") == pg_row.get("id")
        and _timestamps_equal(pg_row.get("created_at"), api_row.get("created_at"))
    )


def _persisted_pair_contract(
    pg_rows: list[dict[str, Any]],
    api_rows: list[dict[str, Any]],
    *,
    sender_id: str,
    receiver_id: str,
) -> bool:
    from test.live_provider.message_api_restart import expected_persisted_message

    if (
        not _safe_terminal_identity(sender_id)
        or not _safe_terminal_identity(receiver_id)
        or sender_id == receiver_id
        or len(pg_rows) != len(PHASES)
        or len(api_rows) != len(PHASES)
    ):
        return False
    pg_ids = [row.get("id") for row in pg_rows]
    if any(type(value) is not int or value <= 0 for value in pg_ids):
        return False
    if len(set(pg_ids)) != len(PHASES):
        return False
    for _, body in PHASES:
        expected = expected_persisted_message(body, sender_id)
        pg_matches = [row for row in pg_rows if row.get("message") == expected]
        if len(pg_matches) != 1:
            return False
        api_matches = [row for row in api_rows if row.get("id") == pg_matches[0]["id"]]
        if len(api_matches) != 1 or not _persisted_row_matches(
            pg_matches[0],
            api_matches[0],
            sender_id=sender_id,
            receiver_id=receiver_id,
            expected_message=expected,
        ):
            return False
    return True


def _observer_contract(observations: dict[str, Any], case_id: Any = DEFAULT_CASE_ID) -> bool:
    try:
        case = _case_definition(case_id)
    except GateError:
        return False
    sender = observations.get("sender", {})
    receiver = observations.get("receiver", {})
    if not isinstance(sender, dict) or not isinstance(receiver, dict):
        return False
    calls = sender.get("mcp_tool_calls", [])
    catalog = sender.get("mcp_catalog_tool_names", [])
    sender_catalog_count = sender.get("mcp_catalog_observation_count")
    receiver_catalog_count = receiver.get("mcp_catalog_observation_count")
    receiver_catalog_status = receiver.get("mcp_catalog_observation_status")
    return (
        (case.case_id != COORDINATION_CASE_ID or _coordination_result_contract(sender))
        and (case.case_id != TASK_QUERY_CASE_ID or _task_query_result_contract(sender))
        and isinstance(calls, list)
        and all(isinstance(call, dict) for call in calls)
        and sender.get("observer_status") == "observed"
        and receiver.get("observer_status") == "observed"
        and sender.get("mcp_observer_shutdown_observed") is True
        and receiver.get("mcp_observer_shutdown_observed") is True
        and sender.get("mcp_catalog_observation_status") == "observed"
        and type(sender_catalog_count) is int
        and sender_catalog_count >= 1
        and isinstance(catalog, list)
        and len(catalog) == len(case.sender_catalog)
        and catalog == list(case.sender_catalog)
        and sender.get("mcp_catalog_unexpected_tool_count") == 0
        and sender.get("mcp_tool_observation_status") == "observed"
        and receiver.get("mcp_catalog_tool_names") == []
        and receiver.get("mcp_catalog_unexpected_tool_count") == 0
        and (
            (
                receiver_catalog_status == "unobserved"
                and type(receiver_catalog_count) is int
                and receiver_catalog_count == 0
            )
            or (
                receiver_catalog_status == "observed"
                and type(receiver_catalog_count) is int
                and receiver_catalog_count >= 1
            )
        )
        and [call.get("tool") for call in calls] == list(case.sender_calls)
        and all(
            call.get("server") == "cao-mcp-server"
            and call.get("status") == "completed"
            and call.get("error_present") is False
            and call.get("result_present") is True
            for call in calls
        )
        and sender.get("mcp_tool_unexpected_call_count") == 0
        and receiver.get("mcp_tool_calls") == []
        and receiver.get("mcp_tool_unexpected_call_count") == 0
        and receiver.get("mcp_tool_observation_status") == "unobserved"
    )


_COLLABORATION_ONLY_RECEIPT_FIELDS = frozenset(
    {
        "sender_native_v3_full_collaboration_unfiltered",
        "sender_runtime_capability_current_source_bound",
        "sender_capability_authenticated_local_operator_owner_bound",
        "sender_capability_auth_off_loopback_api_process_binding_verified",
        "sender_capability_file_private_owned",
        "sender_model_request_full_eight_cao_mcp_tools",
        "expected_capability_set_before_delete",
    }
)
_GENERIC_MESSAGE_FORBIDDEN_RECEIPT_FIELDS = frozenset(
    {
        "sender_native_v3_full_collaboration_unfiltered",
        "sender_runtime_capability_current_source_bound",
        "sender_capability_source_owner_kind",
        "sender_capability_authenticated_local_operator_owner_bound",
        "sender_capability_auth_off_loopback_api_process_binding_verified",
        "sender_capability_binding_diagnostics",
        "sender_capability_file_private_owned",
        "sender_capability_path_sha256",
        "sender_model_request_full_eight_cao_mcp_tools",
        "expected_capability_set_before_delete",
    }
)


def _runtime_source_modules_match(source_module_paths: Any) -> bool:
    return (
        isinstance(source_module_paths, dict)
        and set(source_module_paths) == set(RUN_SOURCE_MODULES)
        and source_module_paths == RUN_SOURCE_RELATIVE_PATHS
    )


def _lifespan_context_contains(context: Any, expected: Any) -> bool:
    pending = [context]
    seen: set[int] = set()
    while pending and len(seen) < 256:
        current = pending.pop()
        if current is expected:
            return True
        if not inspect.isfunction(current) or id(current) in seen:
            continue
        seen.add(id(current))
        try:
            closure_values = inspect.getclosurevars(current).nonlocals.values()
        except (TypeError, ValueError):
            continue
        pending.extend(value for value in closure_values if inspect.isfunction(value))
    return False


def _production_api_capability_issuance_contract(
    api_main: Any,
    security_auth_module: Any,
    interactions_api_module: Any,
    terminal_service: Any,
    interaction_service_module: Any,
    opencode_cli: Any,
    start_cao_server: Any,
) -> dict[str, bool]:
    """Bind the API worker, production lifespan and current issuance/launch paths."""
    try:
        lifespan_source = inspect.getsource(api_main.lifespan)
        terminal_creation_source = inspect.getsource(terminal_service.create_terminal)
        capability_install_source = inspect.getsource(
            terminal_service._install_interaction_source_capability
        )
        model_launch_source = inspect.getsource(
            opencode_cli.OpenCodeCliProvider._build_launch_command
        )
        api_worker_source = inspect.getsource(start_cao_server)
        session_route_source = inspect.getsource(api_main.create_session)
        auth_enabled_source = inspect.getsource(security_auth_module.is_auth_enabled)
        local_principal_source = inspect.getsource(security_auth_module.local_operator_principal)
        current_principal_source = inspect.getsource(security_auth_module.get_current_principal)
        optional_creator_source = inspect.getsource(
            interactions_api_module.optional_creation_principal
        )
        register_source = inspect.getsource(
            interaction_service_module.InteractionService.register_source
        )
    except (AttributeError, OSError, TypeError):
        return {
            "api_lifespan_and_interaction_service": False,
            "capability_issuance_path": False,
            "model_launch_source_binding": False,
            "production_api_worker": False,
            "auth_off_loopback_creator_source": False,
            "authenticated_creation_owner_persistence": False,
        }
    return {
        "api_lifespan_and_interaction_service": (
            _lifespan_context_contains(
                getattr(api_main.app.router, "lifespan_context", None), api_main.lifespan
            )
            and "get_default_interaction_service" in lifespan_source
            and "interaction_service.initialize" in lifespan_source
            and callable(
                getattr(interaction_service_module, "get_default_interaction_service", None)
            )
        ),
        "capability_issuance_path": (
            all(
                marker in terminal_creation_source
                for marker in (
                    "source_registration_eligible",
                    "interaction_service = _interaction_service()",
                    "_install_interaction_source_capability(",
                    "model=_provider_launch_model(provider, model, profile)",
                )
            )
            and all(
                marker in capability_install_source
                for marker in (
                    "interaction_service.register_source(",
                    "_write_interaction_capability_file(",
                    "provider_instance.set_interaction_capability_file(written_path)",
                    "interaction_service.activate_capability(",
                )
            )
        ),
        "model_launch_source_binding": (
            'cmd_parts += ["--model", self._model]' in model_launch_source
        ),
        "production_api_worker": (
            '"cli_agent_orchestrator.api.main"' in api_worker_source
            and '"-m"' in api_worker_source
            and "--lifespan" not in api_worker_source
        ),
        "auth_off_loopback_creator_source": (
            "return bool(_auth0_domain()) or bool(_generic_jwks_uri())" in auth_enabled_source
            and '"urn:cao:local-operator"' in local_principal_source
            and "str(os.geteuid())" in local_principal_source
            and "if is_auth_enabled():" in local_principal_source
            and 'for key in ("server", "client")' in current_principal_source
            and "ipaddress.ip_address(address[0]).is_loopback" in current_principal_source
            and "return local_operator_principal()" in current_principal_source
            and 'get_current_principal(request, request.headers.get("Authorization"))'
            in optional_creator_source
            and "is_verified_principal(principal)" in optional_creator_source
            and "return principal" in optional_creator_source
        ),
        "authenticated_creation_owner_persistence": (
            "creation_principal = await optional_creation_principal(request)"
            in session_route_source
            and "creation_principal=creation_principal" in session_route_source
            and "verified_creator = (" in terminal_creation_source
            and "is_verified_principal(creation_principal)" in terminal_creation_source
            and "principal=source_principal" in terminal_creation_source
            and "source_owner = principal.id if principal is not None else None" in register_source
            and 'owner_kind = "authenticated_creation" if source_owner else "unowned"'
            in register_source
            and "owner_principal_id=%s,owner_source=%s" in register_source
        ),
    }


def _working_directory_matches(actual: Any, expected: Path) -> bool:
    if type(actual) is not str or not actual or not Path(actual).is_absolute():
        return False
    try:
        return Path(actual).resolve(strict=True) == expected.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return False


_REQUIRED_TRUE_RECEIPT_FIELDS = (
    "postgres_identity_verified",
    "api_healthy",
    "receiver_production_install_passed",
    "sender_production_install_passed",
    "receiver_installed_native_v3_verified",
    "sender_installed_native_v3_verified",
    "receiver_installed_prompt_matches_fixture",
    "sender_installed_prompt_matches_fixture",
    "receiver_operation_permissions_exact",
    "sender_operation_permissions_exact",
    "receiver_allowed_tools_exact",
    "sender_allowed_tools_exact",
    "sender_native_v3_full_collaboration_unfiltered",
    "sender_runtime_capability_current_source_bound",
    "sender_capability_authenticated_local_operator_owner_bound",
    "sender_capability_auth_off_loopback_api_process_binding_verified",
    "sender_capability_file_private_owned",
    "sender_legacy_three_tool_marker_absent",
    "receiver_native_v3_empty_tool_filter",
    "receiver_no_runtime_capability",
    "production_api_lifespan_and_interaction_service_verified",
    "production_capability_issuance_path_verified",
    "production_auth_off_loopback_creator_source_verified",
    "production_authenticated_creation_owner_binding_verified",
    "production_model_launch_source_binding_verified",
    "production_api_worker_invocation_verified",
    "receiver_current_working_directory_bound",
    "sender_current_working_directory_bound",
    "receiver_model_ready",
    "sender_model_ready",
    "receiver_model_phase_within_75_seconds",
    "sender_model_phase_within_75_seconds",
    "two_native_v2_provider_roots",
    "two_native_provider_roots_before_delete",
    "expected_capability_set_before_delete",
    "two_exact_pg_api_messages",
    "both_message_phases_passed",
    "postgres_target_identity_stable",
    "terminal_metadata_stable_on_api_restart",
    "terminal_lifecycle_stable_on_api_restart",
    "terminal_verified_receipts_stable_on_api_restart",
    "native_process_identity_stable_on_api_restart",
    "provider_root_identity_stable_on_api_restart",
    "first_pg_api_row_stable_on_restart",
    "api_restart_process_changed",
    "native_catalog_and_calls_exact",
    "native_model_request_cao_mcp_surfaces_exact",
    "sender_model_request_full_eight_cao_mcp_tools",
    "receiver_model_request_zero_cao_mcp_tools",
    "native_builtin_offer_names_within_source_approved_optional_contract",
    "http_tool_offer_observer_installed",
    "project_config_disable_flag_absent",
    "graceful_observer_seals_observed",
    "observer_seal_within_10_seconds",
    "api_processes_confirmed_dead_before_postgres_teardown",
    "api_launches_tracked_through_confirmed_death",
    "agui_capture_threads_confirmed_dead_before_postgres_teardown",
    "tmux_processes_confirmed_dead_before_postgres_teardown",
    "api_a_cleanup",
    "api_b_cleanup",
    "api_cleanup",
    "tmux_cleanup",
    "postgres_cleanup",
    "postgres_cleanup_guard_installed",
    "ambient_environment_sanitized_by_allowlist",
    "operator_auth_unchanged",
    "source_unchanged",
    "binary_unchanged",
    "runner_unchanged",
    "python_interpreter_unchanged",
    "private_state_removed",
    "owned_process_snapshot_refresh_complete",
    "output_range_controls_passed",
)
_GENERIC_MESSAGE_TRUE_RECEIPT_FIELDS = tuple(
    field
    for field in _REQUIRED_TRUE_RECEIPT_FIELDS
    if field not in _COLLABORATION_ONLY_RECEIPT_FIELDS
) + (
    "sender_native_v3_exact_two_tool_filter",
    "sender_no_runtime_human_capability",
    "receiver_no_runtime_human_capability",
    "capability_inventory_zero_before_first_input",
    "capability_inventory_zero_before_delete",
    "capability_inventory_zero_after_delete",
    "sender_model_request_exact_two_cao_mcp_tools",
    "no_full_eight_human_capability_claim",
)

_SUCCESS_MEMORY_ADMISSIONS = (
    "initial",
    "production_profile_install",
    "install_receiver",
    "install_sender",
    "api_start",
    "terminal_receiver",
    "terminal_sender",
    "model_phase_1_sender",
    "model_phase_1_receiver",
    "model_phase_1_start",
    "receiver_phase_1_started",
    "api_restart",
    "model_phase_2_sender",
    "model_phase_2_receiver",
    "model_phase_2_start",
    "receiver_phase_2_started",
)


def _guard_contextmanager_finalbody(
    function_source: str,
    *,
    function_name: str,
    guarded_name: str,
    guard_name: str,
    namespace: dict[str, Any],
    filename: str,
) -> tuple[Any, str]:
    """Derive a context-manager function whose finalizer obeys one guard."""
    try:
        parsed = ast.parse(function_source, filename=filename)
    except SyntaxError as error:
        raise GateError("postgres_cleanup_helper_source_invalid") from error
    functions = [
        node
        for node in parsed.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
    ]
    if len(functions) != 1 or not isinstance(functions[0], ast.FunctionDef):
        raise GateError("postgres_cleanup_helper_function_shape_changed")
    function = functions[0]
    finalizers = [
        node for node in ast.walk(function) if isinstance(node, ast.Try) and node.finalbody
    ]
    if len(finalizers) != 1:
        raise GateError("postgres_cleanup_helper_finalizer_shape_changed")
    finalizer = finalizers[0]
    if any(
        isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == guard_name
        for node in finalizer.finalbody
    ):
        raise GateError("postgres_cleanup_helper_already_guarded")
    finalizer.finalbody = [
        ast.If(
            test=ast.Name(id=guard_name, ctx=ast.Load()),
            body=finalizer.finalbody,
            orelse=[],
        )
    ]
    function.name = guarded_name
    derived = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(derived)
    derived_source = ast.unparse(derived)
    derived_sha256 = hashlib.sha256(derived_source.encode("utf-8")).hexdigest()
    try:
        exec(compile(derived, filename, "exec"), namespace)
        guarded_function = namespace.pop(guarded_name)
    except Exception as error:
        raise GateError("postgres_cleanup_helper_derivation_failed") from error
    if not callable(guarded_function):
        raise GateError("postgres_cleanup_helper_derivation_failed")
    return guarded_function, derived_sha256


def _install_guarded_postgres_cleanup(fixtures_module: Any) -> tuple[str, str]:
    expected_path = (REPO / "test/postgres/fixtures.py").resolve(strict=True)
    fixture_path = Path(getattr(fixtures_module, "__file__", "")).resolve(strict=True)
    if fixture_path != expected_path:
        raise GateError("postgres_fixture_source_binding_invalid")
    fixture_sha256 = _sha256(fixture_path)
    if fixture_sha256 != PINNED_POSTGRES_FIXTURE_SHA256:
        raise GateError("postgres_fixture_source_pin_mismatch")
    source_function = getattr(fixtures_module, "_isolated_live_targets", None)
    if source_function is None:
        raise GateError("postgres_fixture_cleanup_helper_missing")
    try:
        source = inspect.getsource(source_function)
    except (OSError, TypeError) as error:
        raise GateError("postgres_fixture_cleanup_helper_unavailable") from error
    namespace = fixtures_module.__dict__
    namespace["_cao_private_pg_teardown_permitted"] = False
    guarded, derived_sha256 = _guard_contextmanager_finalbody(
        source,
        function_name="_isolated_live_targets",
        guarded_name="_isolated_live_targets_private_guarded",
        guard_name="_cao_private_pg_teardown_permitted",
        namespace=namespace,
        filename=str(fixture_path),
    )
    if derived_sha256 != PINNED_POSTGRES_GUARD_DERIVATION_SHA256:
        raise GateError("postgres_fixture_guard_derivation_pin_mismatch")
    if getattr(guarded, "__wrapped__", None).__globals__ is not namespace:
        raise GateError("postgres_fixture_guard_namespace_mismatch")
    fixtures_module._isolated_live_targets = guarded
    return fixture_sha256, derived_sha256


def _enter_guarded_postgres_context(context: Any, fixtures_module: Any) -> Any:
    """Permit fixture rollback during __enter__, before any owned API/tmux exists."""
    fixtures_module._cao_private_pg_teardown_permitted = True
    try:
        return context.__enter__()
    finally:
        fixtures_module._cao_private_pg_teardown_permitted = False


def _exit_guarded_postgres_context(context: Any, fixtures_module: Any) -> Any:
    """Permit teardown only after all owned service identities are confirmed dead."""
    fixtures_module._cao_private_pg_teardown_permitted = True
    try:
        return context.__exit__(None, None, None)
    finally:
        fixtures_module._cao_private_pg_teardown_permitted = False


def _api_launches_confirmed_dead(
    records: list[dict[str, Any]],
    *,
    attempted_roles: set[str],
    journey: Any,
) -> bool:
    roles = [record.get("role") for record in records]
    if set(roles) != attempted_roles or len(roles) != len(set(roles)):
        return False
    for record in records:
        identity = record.get("identity")
        if identity is None:
            return False
        try:
            if journey._process_identity_alive(identity) is not False:
                return False
        except Exception:
            return False
    return True


def _record_early_failure_cleanup_observation(
    receipt: dict[str, Any], cao_home: Path, private_run_root: Path
) -> bool:
    """Allow pre-runtime cleanup only after proving no writers and zero CAO roots."""
    no_resources_started = all(
        type(receipt.get(field)) is bool and receipt.get(field) is False
        for field in (
            "api_a_launch_attempted",
            "api_b_launch_attempted",
            "tmux_launch_attempted",
            "postgres_context_entered",
            "postgres_context_present",
            "private_v2_credential_snapshot_created",
            "session_creation_attempted",
            "session_deletion_attempted",
        )
    ) and all(
        type(receipt.get(field)) is int and receipt.get(field) == 0
        for field in (
            "api_server_record_count",
            "capture_started_count",
            "owned_process_identity_count",
        )
    )
    receipt["early_failure_resource_scan_complete"] = False
    if not no_resources_started:
        receipt["early_failure_cleanup_case"] = False
        return False
    credential_status = _credential_snapshot_status(private_run_root, snapshot_created=False)
    receipt["credential_snapshot_status_at_early_failure"] = credential_status
    if credential_status != "not_created":
        receipt["early_failure_cleanup_case"] = False
        return False
    try:
        provider_roots = _provider_roots(cao_home)
        capability_files = _capability_files(cao_home)
    except Exception:
        receipt["early_failure_cleanup_case"] = True
        return False
    receipt["early_failure_cleanup_case"] = True
    receipt["native_provider_roots_at_early_failure"] = len(provider_roots)
    receipt["capability_files_at_early_failure"] = len(capability_files)
    receipt["early_failure_resource_scan_complete"] = True
    return not provider_roots and not capability_files


def _within_budget(value: Any, budget: float) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and 0 <= value <= budget
    except (OverflowError, TypeError):
        return False


def _observer_seal_observed_within_deadline(
    started_at: Any, observed_at: Any, deadline: Any
) -> bool:
    """Accept only a finite in-loop observation made by the absolute deadline."""
    if any(type(value) not in (int, float) for value in (started_at, observed_at, deadline)):
        return False
    try:
        elapsed = observed_at - started_at
        allowed = deadline - started_at
        return (
            math.isfinite(started_at)
            and math.isfinite(observed_at)
            and math.isfinite(deadline)
            and math.isfinite(elapsed)
            and math.isfinite(allowed)
            and started_at <= observed_at <= deadline
            and 0 <= elapsed <= allowed
        )
    except (OverflowError, TypeError):
        return False


def _parse_postgres_inspect(stdout: Any) -> tuple[str, str, bool] | None:
    """Parse the exact one-line Docker inspect output used by the runner."""
    if type(stdout) is not str:
        return None
    match = re.fullmatch(
        r"([0-9a-f]{64}) (sha256:[0-9a-f]{64}) (true|false)(?:\n|\r\n)?",
        stdout,
    )
    if match is None:
        return None
    return match.group(1), match.group(2), match.group(3) == "true"


def _postgres_service_identity_matches(
    metadata: Any, inspect_returncode: Any, inspect_stdout: Any
) -> bool:
    """Check fixture metadata and live inspect output without invented keys."""
    if (
        not isinstance(metadata, dict)
        or metadata.get("container_id") != PINNED_POSTGRES_ID
        or type(inspect_returncode) is not int
        or inspect_returncode != 0
    ):
        return False
    return _parse_postgres_inspect(inspect_stdout) == (
        PINNED_POSTGRES_ID,
        PINNED_POSTGRES_IMAGE,
        True,
    )


def _capture_threads_confirmed_dead(captures: list[Any]) -> bool:
    """Return thread liveness only; stream health is a separate acceptance check."""
    for capture in captures:
        try:
            if capture._thread.is_alive() is not False:
                return False
        except Exception:
            return False
    return True


def _capture_stopped_cleanly(capture: Any) -> bool:
    """Require both a healthy observer stream and a stopped thread for phase acceptance."""
    try:
        return capture._failed is False and capture._thread.is_alive() is False
    except Exception:
        return False


def _postgres_teardown_allowed(
    *,
    api_processes_dead: Any,
    capture_threads_dead: Any,
    tmux_processes_dead: Any,
) -> bool:
    """Gate destructive fixture teardown only on owned-service liveness."""
    return (
        api_processes_dead is True and capture_threads_dead is True and tmux_processes_dead is True
    )


def _restart_snapshots_unchanged(receipt: dict[str, Any]) -> bool:
    roles = {"sender", "receiver"}
    before_pids = receipt.get("native_process_pids_before_restart")
    after_pids = receipt.get("native_process_pids_after_restart")
    before_metadata = receipt.get("terminal_metadata_before_restart")
    after_metadata = receipt.get("terminal_metadata_after_restart")
    if (
        not isinstance(before_pids, dict)
        or not isinstance(after_pids, dict)
        or set(before_pids) != roles
        or set(after_pids) != roles
        or not isinstance(before_metadata, dict)
        or not isinstance(after_metadata, dict)
        or set(before_metadata) != roles
        or set(after_metadata) != roles
        or before_metadata != after_metadata
    ):
        return False
    for role in roles:
        before = before_pids[role]
        after = after_pids[role]
        if (
            not isinstance(before, list)
            or not before
            or any(type(pid) is not int or pid <= 0 for pid in before)
            or before != sorted(set(before))
            or after != before
            or not isinstance(before_metadata[role], dict)
        ):
            return False
    return True


def _api_process_identities_valid(receipt: dict[str, Any]) -> bool:
    api_a_pid = receipt.get("api_a_pid")
    api_b_pid = receipt.get("api_b_pid")
    api_a_start = receipt.get("api_a_process_start")
    api_b_start = receipt.get("api_b_process_start")
    return (
        type(api_a_pid) is int
        and api_a_pid > 0
        and type(api_b_pid) is int
        and api_b_pid > 0
        and api_a_pid != api_b_pid
        and type(api_a_start) is int
        and api_a_start > 0
        and type(api_b_start) is int
        and api_b_start > 0
        and api_a_start != api_b_start
        and type(receipt.get("api_worker_restarts")) is int
        and receipt.get("api_worker_restarts") == 1
    )


def _private_root_cleanup_safe(receipt: Any) -> bool:
    """Permit private-root removal when every attempted owner is confirmed dead.

    Per-role API cleanup flags remain truthful for acceptance. An API role that
    was never attempted does not own a process and must not block safe cleanup.
    """
    if not isinstance(receipt, dict):
        return False
    if receipt.get("credentials_cleanup_deferred") is not False:
        return False
    if any(
        type(receipt.get(f"{role}_launch_attempted")) is not bool
        or type(receipt.get(f"{role}_cleanup")) is not bool
        for role in ("api_a", "api_b")
    ):
        return False
    if any(
        receipt[f"{role}_launch_attempted"] is False and receipt[f"{role}_cleanup"] is not False
        for role in ("api_a", "api_b")
    ):
        return False
    required_true = (
        "api_cleanup",
        "api_processes_confirmed_dead_before_postgres_teardown",
        "api_launches_tracked_through_confirmed_death",
        "agui_capture_threads_confirmed_dead_before_postgres_teardown",
        "tmux_processes_confirmed_dead_before_postgres_teardown",
        "owned_process_snapshot_refresh_complete",
        "tmux_cleanup",
        "postgres_cleanup",
        "operator_auth_unchanged",
        "source_unchanged",
        "binary_unchanged",
        "runner_unchanged",
        "python_interpreter_unchanged",
    )
    post_delete_counts_safe = (
        type(receipt.get("native_provider_roots_immediately_after_delete")) is int
        and receipt.get("native_provider_roots_immediately_after_delete") == 0
        and type(receipt.get("capability_files_immediately_after_delete")) is int
        and receipt.get("capability_files_immediately_after_delete") == 0
    )
    early_failure_counts_safe = (
        receipt.get("early_failure_cleanup_case") is True
        and receipt.get("early_failure_resource_scan_complete") is True
        and receipt.get("credential_snapshot_status_at_early_failure") == "not_created"
        and all(
            type(receipt.get(field)) is bool and receipt.get(field) is False
            for field in (
                "api_a_launch_attempted",
                "api_b_launch_attempted",
                "tmux_launch_attempted",
                "postgres_context_entered",
                "postgres_context_present",
                "private_v2_credential_snapshot_created",
                "session_creation_attempted",
                "session_deletion_attempted",
            )
        )
        and all(
            type(receipt.get(field)) is int and receipt.get(field) == 0
            for field in (
                "api_server_record_count",
                "capture_started_count",
                "owned_process_identity_count",
                "native_provider_roots_at_early_failure",
                "capability_files_at_early_failure",
            )
        )
    )
    return (
        all(receipt.get(key) is True for key in required_true)
        and receipt.get("postgres_cleanup_deferred") is False
        and (post_delete_counts_safe or early_failure_counts_safe)
    )


def _task_query_passive_offer_contract(observation: Any, *, expected_nonce: str) -> bool:
    return (
        type(observation) is dict
        and observation.get("observer_status") == "observed"
        and observation.get("tool_offer_status") == "unobserved"
        and observation.get("nonce") == expected_nonce
        and observation.get("hook_disposed") is True
        and observation.get("trace_complete") is True
        and observation.get("overflow") is False
        and observation.get("requests") == []
        and all(
            type(observation.get(key)) is int and observation[key] == 0
            for key in (
                "request_count",
                "durable_record_count",
                "target_primary_request_count",
                "valid_target_primary_request_count",
                "other_tool_name_count",
            )
        )
        and type(observation.get("tool_name_counts")) is dict
        and set(observation["tool_name_counts"]) == set(_case_http_offer_names(TASK_QUERY_CASE_ID))
        and all(
            type(value) is int and value == 0 for value in observation["tool_name_counts"].values()
        )
    )


def _task_query_acceptance_contract(receipt: dict[str, Any]) -> bool:
    """Task-only scope with the same source, authority and retirement fences."""
    case = _case_definition(TASK_QUERY_CASE_ID)
    manifest = RUN_MANIFEST if type(RUN_MANIFEST) is dict else {}
    excluded = {
        "receiver_model_phase_within_75_seconds",
        "sender_model_phase_within_75_seconds",
        "two_exact_pg_api_messages",
        "both_message_phases_passed",
        "terminal_metadata_stable_on_api_restart",
        "terminal_lifecycle_stable_on_api_restart",
        "terminal_verified_receipts_stable_on_api_restart",
        "native_process_identity_stable_on_api_restart",
        "provider_root_identity_stable_on_api_restart",
        "first_pg_api_row_stable_on_restart",
        "api_restart_process_changed",
        "api_b_cleanup",
    }
    required = tuple(
        _case_receipt_key(key, TASK_QUERY_CASE_ID)
        for key in _REQUIRED_TRUE_RECEIPT_FIELDS
        if key not in excluded
    )
    exact = {
        "schema": case.receipt_schema,
        "case_id": case.case_id,
        "artifact_label": case.label,
        "profile_fixture_path": case.profile_fixture_path,
        "profile_fixture_schema": case.profile_fixture_schema,
        "scope": _case_scope(case.case_id),
        "native_surface": case.native_surface,
        "model": MODEL,
        "auth_lane": MODEL,
        "cao_auth_mode": "off",
        "source_head_expected": EXPECTED_HEAD,
        "source_head": EXPECTED_HEAD,
        "run_manifest_sha256": RUN_MANIFEST_SHA256,
        "review_digest": REVIEW_DIGEST,
        "source_fingerprint_sha256_expected": SOURCE_FINGERPRINT_SHA256,
        "source_fingerprint_sha256": SOURCE_FINGERPRINT_SHA256,
        "runner_sha256_expected": RUNNER_SHA256_AT_ADMISSION,
        "runner_sha256": RUNNER_SHA256_AT_ADMISSION,
        "profile_fixture_sha256_expected": manifest.get("profile_fixture_sha256"),
        "operator_auth_snapshot_sha256": manifest.get("operator_auth_snapshot_sha256"),
        "binary_sha256_expected": PINNED_OPENCODE_SHA256,
        "postgres_container_id_expected": PINNED_POSTGRES_ID,
        "postgres_container_id": PINNED_POSTGRES_ID,
        "postgres_image_id_expected": PINNED_POSTGRES_IMAGE,
        "postgres_image_id": PINNED_POSTGRES_IMAGE,
        "postgres_fixture_cleanup_guard_source_sha256": PINNED_POSTGRES_FIXTURE_SHA256,
        "postgres_fixture_cleanup_guard_derived_sha256": PINNED_POSTGRES_GUARD_DERIVATION_SHA256,
        "sender_capability_source_owner_kind": "authenticated_creation",
        "native_model_offer_scope": "selected_model_primary_requests_only",
        "receiver_native_builtin_execution_observation": "not_observed",
    }
    manifest_exact = {
        "schema": case.manifest_schema,
        "case_id": case.case_id,
        "artifact_label": case.label,
        "case_profile_fixture_path": case.profile_fixture_path,
        "case_profile_fixture_schema": case.profile_fixture_schema,
        "receipt_schema": case.receipt_schema,
    }
    admissions = receipt.get("memory_admissions_mib")
    memory_keys = set(_SUCCESS_MEMORY_ADMISSIONS[:7]) | {"task_query"}
    return (
        "error_code" not in receipt
        and all(
            type(value) is str and bool(value) and receipt.get(key) == value
            for key, value in exact.items()
        )
        and all(manifest.get(key) == value for key, value in manifest_exact.items())
        and all(
            receipt.get(key) is True
            for key in required
            + (
                "task_fixture_owned_preflight_passed",
                "task_query_turn_verified",
                "task_query_receiver_unprompted",
                "task_query_no_messages",
            )
        )
        and _private_root_cleanup_safe(receipt)
        and _capability_binding_diagnostics_pass(receipt)
        and _observer_contract(receipt.get("native_observations", {}), case.case_id)
        and _within_budget(receipt.get("task_query_seconds"), TASK_QUERY_SECONDS)
        and _within_budget(receipt.get("observer_seal_seconds"), OBSERVER_GRACE_SECONDS)
        and receipt.get("phases") == []
        and receipt.get("api_worker_restarts", 0) == 0
        and receipt.get("api_b_launch_attempted") is False
        and receipt.get("api_b_cleanup") is False
        and receipt.get("api_a_launch_attempted") is True
        and type(receipt.get("api_server_record_count")) is int
        and receipt["api_server_record_count"] == 1
        and all(
            type(receipt.get(key)) is int and receipt[key] > 0
            for key in ("api_a_pid", "api_a_process_start")
        )
        and type(receipt.get("session_delete_http_status")) is int
        and receipt["session_delete_http_status"] == 200
        and receipt.get("cleanup_failures") == []
        and all(
            receipt.get(key) is False
            for key in (
                "actual_cancel_observed",
                "cross_model_or_oauth_claimed",
                "claude_used",
                "credentials_cleanup_deferred",
                "postgres_cleanup_deferred",
            )
        )
        and receipt.get("receipt_redacted_only") is True
        and receipt.get("private_raw_evidence_present") is True
        and receipt.get("retained_private_state")
        == {"status": "removed", "credential_snapshot": "removed"}
        and type(admissions) is dict
        and set(admissions) == memory_keys
        and all(
            type(admissions[key]) is int and admissions[key] >= MEMORY_FLOOR_MIB
            for key in memory_keys
        )
    )


def _acceptance_contract(receipt: Any, case_id: Any = DEFAULT_CASE_ID) -> bool:
    """Pure final receipt predicate using the exact emitted receipt shape."""
    if not isinstance(receipt, dict):
        return False
    try:
        selected_case = _case_definition(case_id)
    except GateError:
        return False
    if selected_case.case_id == TASK_QUERY_CASE_ID:
        return _task_query_acceptance_contract(receipt)
    is_generic = selected_case.case_id == GENERIC_MESSAGE_CASE_ID
    manifest = RUN_MANIFEST if isinstance(RUN_MANIFEST, dict) else {}
    required_true_fields = (
        _GENERIC_MESSAGE_TRUE_RECEIPT_FIELDS if is_generic else _REQUIRED_TRUE_RECEIPT_FIELDS
    )
    if selected_case.case_id == COORDINATION_CASE_ID:
        required_true_fields = tuple(
            _case_receipt_key(field, selected_case.case_id) for field in required_true_fields
        ) + (
            "coordination_context_preflight_passed",
            "coordination_read_success_observed",
        )
    expected_scope = _case_scope(selected_case.case_id)
    case_identity_exact = (
        receipt.get("case_id") == selected_case.case_id
        and receipt.get("artifact_label") == selected_case.label
        and receipt.get("profile_fixture_path") == selected_case.profile_fixture_path
        and receipt.get("profile_fixture_schema") == selected_case.profile_fixture_schema
        and receipt.get("schema") == selected_case.receipt_schema
        and receipt.get("scope") == expected_scope
        and manifest.get("schema") == selected_case.manifest_schema
        and manifest.get("case_id") == selected_case.case_id
        and manifest.get("artifact_label") == selected_case.label
        and manifest.get("case_profile_fixture_path") == selected_case.profile_fixture_path
        and manifest.get("case_profile_fixture_schema") == selected_case.profile_fixture_schema
        and manifest.get("receipt_schema") == selected_case.receipt_schema
    )
    capability_inventory_exact = not is_generic or (
        all(
            type(receipt.get(field)) is int and receipt.get(field) == 0
            for field in (
                "capability_files_at_sender_initialization",
                "capability_files_at_receiver_initialization",
                "capability_files_before_first_input",
                "capability_files_before_delete",
                "capability_files_immediately_after_delete",
            )
        )
        and all(field not in receipt for field in _GENERIC_MESSAGE_FORBIDDEN_RECEIPT_FIELDS)
        and not any(field.startswith("sender_capability_") for field in receipt)
    )
    phases = receipt.get("phases")
    admissions = receipt.get("memory_admissions_mib")
    if (
        "error_code" in receipt
        or receipt.get("schema") != selected_case.receipt_schema
        or not case_identity_exact
        or (
            selected_case.case_id == COORDINATION_CASE_ID
            and any(
                field in receipt
                for field in (
                    "sender_native_v3_full_collaboration_unfiltered",
                    "sender_model_request_full_eight_cao_mcp_tools",
                )
            )
        )
        or not capability_inventory_exact
        or receipt.get("model") != MODEL
        or receipt.get("auth_lane") != MODEL
        or receipt.get("cao_auth_mode") != "off"
        or receipt.get("source_head_expected") != EXPECTED_HEAD
        or receipt.get("source_head") != EXPECTED_HEAD
        or receipt.get("run_manifest_sha256") != RUN_MANIFEST_SHA256
        or receipt.get("review_digest") != REVIEW_DIGEST
        or receipt.get("source_fingerprint_sha256_expected") != SOURCE_FINGERPRINT_SHA256
        or receipt.get("runner_sha256_expected") != RUNNER_SHA256_AT_ADMISSION
        or receipt.get("runner_sha256") != RUNNER_SHA256_AT_ADMISSION
        or receipt.get("source_fingerprint_sha256") != SOURCE_FINGERPRINT_SHA256
        or receipt.get("profile_fixture_sha256_expected")
        != (RUN_MANIFEST or {}).get("profile_fixture_sha256")
        or receipt.get("operator_auth_snapshot_sha256")
        != (RUN_MANIFEST or {}).get("operator_auth_snapshot_sha256")
        or (
            not is_generic
            and (
                receipt.get("sender_capability_source_owner_kind") != "authenticated_creation"
                or receipt.get("sender_capability_authenticated_local_operator_owner_bound")
                is not True
                or receipt.get("sender_capability_auth_off_loopback_api_process_binding_verified")
                is not True
                or not _capability_binding_diagnostics_pass(receipt)
            )
        )
        or receipt.get("native_surface") != selected_case.native_surface
        or receipt.get("cross_model_or_oauth_claimed") is not False
        or receipt.get("receiver_native_builtin_execution_observation") != "not_observed"
        or receipt.get("native_model_offer_scope") != "selected_model_primary_requests_only"
        or receipt.get("binary_sha256_expected") != PINNED_OPENCODE_SHA256
        or receipt.get("postgres_container_id_expected") != PINNED_POSTGRES_ID
        or receipt.get("postgres_image_id_expected") != PINNED_POSTGRES_IMAGE
        or receipt.get("postgres_container_id") != PINNED_POSTGRES_ID
        or receipt.get("postgres_image_id") != PINNED_POSTGRES_IMAGE
        or receipt.get("postgres_fixture_cleanup_guard_source_sha256")
        != PINNED_POSTGRES_FIXTURE_SHA256
        or receipt.get("postgres_fixture_cleanup_guard_derived_sha256")
        != PINNED_POSTGRES_GUARD_DERIVATION_SHA256
        or receipt.get("postgres_cleanup_deferred") is not False
        or receipt.get("claude_used") is not False
        or receipt.get("receipt_redacted_only") is not True
        or receipt.get("private_raw_evidence_present") is not True
        or receipt.get("actual_cancel_observed") is not False
        or receipt.get("credentials_cleanup_deferred") is not False
        or receipt.get("retained_private_state")
        != {"status": "removed", "credential_snapshot": "removed"}
        or type(receipt.get("session_delete_http_status")) is not int
        or receipt.get("session_delete_http_status") != 200
        or type(receipt.get("native_provider_roots_immediately_after_delete")) is not int
        or receipt.get("native_provider_roots_immediately_after_delete") != 0
        or type(receipt.get("capability_files_immediately_after_delete")) is not int
        or receipt.get("capability_files_immediately_after_delete") != 0
        or any(receipt.get(key) is not True for key in required_true_fields)
        or receipt.get("cleanup_failures") != []
        or not _within_budget(receipt.get("observer_seal_seconds"), OBSERVER_GRACE_SECONDS)
        or not _api_process_identities_valid(receipt)
        or not _restart_snapshots_unchanged(receipt)
        or not isinstance(admissions, dict)
        or set(admissions) != set(_SUCCESS_MEMORY_ADMISSIONS)
        or any(
            type(admissions.get(key)) is not int or admissions[key] < MEMORY_FLOOR_MIB
            for key in _SUCCESS_MEMORY_ADMISSIONS
        )
        or not isinstance(phases, list)
        or len(phases) != len(PHASES)
        or not all(
            _within_budget(receipt.get(f"{role}_model_phase_seconds"), MODEL_PHASE_SECONDS)
            for role in ("receiver", "sender")
        )
    ):
        return False
    return all(
        isinstance(phase, dict)
        and phase.get("name") == PHASES[index][0]
        and phase.get("one_exact_pg_api_message") is True
        and phase.get("agui_redacted_one_notification") is True
        and phase.get("receiver_turn_completed_once") is True
        and phase.get("receiver_ack_exact") is True
        and phase.get("durable_turn_completed") is True
        and phase.get("last_receipt_unicode_verified") is True
        and phase.get("agui_capture_stopped_cleanly") is True
        and phase.get("sender_model_within_75_seconds") is True
        and phase.get("receiver_model_within_75_seconds") is True
        and _within_budget(phase.get("sender_model_seconds"), MODEL_PHASE_SECONDS)
        and _within_budget(phase.get("receiver_model_seconds"), MODEL_PHASE_SECONDS)
        for index, phase in enumerate(phases)
    )


def _self_check(case_id: Any = DEFAULT_CASE_ID) -> dict[str, Any]:
    """Offline contract check. It never opens a service or reads credentials."""
    try:
        case = _case_definition(case_id)
    except GateError:
        return {
            "schema": "spec025.native-v2-self-check.v1",
            "success": False,
            "checks": {"case_id_source_defined": False},
        }
    self_check_schema = (
        "spec025.t116.native-v2-generic-message-self-check.v1"
        if case.case_id == GENERIC_MESSAGE_CASE_ID
        else "spec025.t104.native-v2-self-check.v1"
    )
    if case.case_id == COORDINATION_CASE_ID:
        self_check_schema = "spec025.t166.native-v2-coordination-self-check.v1"
    sender_allowed, sender_filter, _ = _profile_contract("sender", case.case_id)
    receiver_allowed, receiver_filter, _ = _profile_contract("receiver", case.case_id)
    try:
        output_range_controls_passed = _output_range_controls() == 8
    except (AssertionError, GateError, TypeError, ValueError):
        output_range_controls_passed = False
    checks = {
        "default_closed": RUN_ENABLED is False,
        "receiver_has_no_cao_mcp_tools": receiver_allowed == [] and receiver_filter == [],
        "selected_case_profile_exact": (
            sender_allowed == list(case.sender_allowed_tools)
            and sender_filter
            == (
                None
                if case.sender_enabled_mcp_tools is None
                else list(case.sender_enabled_mcp_tools)
            )
            and receiver_allowed == []
            and receiver_filter == []
        ),
        "full_eight_tool_catalog": len(SENDER_TOOLS) == 8
        and set(SENDER_TOOLS)
        == {
            "cao_ask_user",
            "cao_wait_for_user_answers",
            "cao_acknowledge_user_answers",
            "send_message",
            "list_siblings",
            "cao_list_interactions",
            "cao_get_interaction",
            "cao_escalate_interaction",
        },
        "only_two_message_operations": (
            case.sender_calls == ("get_local_cao_task",)
            if case.case_id == TASK_QUERY_CASE_ID
            else case.sender_calls
            == (
                (
                    "list_local_cao_peers"
                    if case.case_id == COORDINATION_CASE_ID
                    else "list_siblings"
                ),
                "send_message",
                "send_message",
            )
        ),
        "no_execute_in_full_tool_offer": (
            "cao-mcp-server_execute" not in MCP_HTTP_OFFER_TOOL_NAMES
        ),
        "http_offer_observer_case_bound": (
            MCP_HTTP_OFFER_TOOL_NAMES == tuple("cao-mcp-server_" + tool for tool in SENDER_TOOLS)
            and OPTIONAL_OPENCODE_BUILTIN_TOOL_NAMES == ("skill", "todowrite")
            and DIAGNOSTIC_ONLY_OPENCODE_TOOL_NAMES
            == (
                "apply_patch",
                "bash",
                "batch",
                "codesearch",
                "edit",
                "execute",
                "glob",
                "grep",
                "lsp",
                "question",
                "read",
                "task",
                "webfetch",
                "websearch",
                "write",
            )
            and not set(DIAGNOSTIC_ONLY_OPENCODE_TOOL_NAMES)
            & (set(MCP_HTTP_OFFER_TOOL_NAMES) | set(OPTIONAL_OPENCODE_BUILTIN_TOOL_NAMES))
            and HTTP_OFFER_TOOL_NAMES
            == (
                MCP_HTTP_OFFER_TOOL_NAMES
                + OPTIONAL_OPENCODE_BUILTIN_TOOL_NAMES
                + DIAGNOSTIC_ONLY_OPENCODE_TOOL_NAMES
            )
        ),
        "case_profile_fixture_exact": _case_fixture_contract(case.case_id),
        "generic_case_exact_two_tool_catalog_and_no_human_capability": (
            case.case_id != GENERIC_MESSAGE_CASE_ID
            or (
                case.sender_catalog == ("list_siblings", "send_message")
                and case.sender_enabled_mcp_tools == ("list_siblings", "send_message")
                and case.sender_calls == ("list_siblings", "send_message", "send_message")
                and case.requires_human_capability is False
            )
        ),
        "private_output_range_controls": output_range_controls_passed,
    }
    checks["default_collaboration_case_has_full_grant"] = (
        _profile_contract("sender", DEFAULT_CASE_ID)[0] == ["@cao-mcp-server"]
        and _profile_contract("sender", DEFAULT_CASE_ID)[1] is None
    )
    if case.case_id == DEFAULT_CASE_ID:
        checks["sender_has_full_collaboration_grant"] = checks[
            "default_collaboration_case_has_full_grant"
        ]
    if case.case_id == COORDINATION_CASE_ID:
        checks["coordination_exact_twelve_tool_catalog"] = (
            len(case.sender_catalog) == 12 and case.sender_catalog == COORDINATION_SENDER_TOOLS
        )
    return {
        "schema": self_check_schema,
        "success": all(checks.values()),
        "checks": checks,
    }


def _operator_state_paths(original_home: Path, original_env: dict[str, str]) -> list[Path]:
    from test.live_provider import opencode_interaction_journey as journey

    db = journey._source_credential_db_path(original_home=original_home, environment=original_env)
    auth = journey._source_auth_path(original_home=original_home, environment=original_env)
    return [db, Path(str(db) + "-wal"), Path(str(db) + "-shm"), auth]


def _operator_fingerprints(paths: list[Path]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for path in paths:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            result[str(path)] = None
            continue
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
        ):
            raise GateError("operator_auth_source_invalid")
        result[str(path)] = _sha256(path)
    return result


def _credential_snapshot_status(root: Path, *, snapshot_created: bool) -> str:
    """Observe only private DB/sidecar presence; never unlink a possibly active DB."""
    if type(snapshot_created) is not bool:
        return "unknown"
    database_directory = root / "opencode-data" / "opencode"
    for directory in (database_directory.parent, database_directory):
        try:
            metadata = directory.lstat()
        except FileNotFoundError:
            return "removed" if snapshot_created else "not_created"
        except OSError:
            return "unknown"
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            return "unknown"

    observed_file = False
    unreadable = False
    for name in ("opencode.db", "opencode.db-wal", "opencode.db-shm"):
        try:
            (database_directory / name).lstat()
        except FileNotFoundError:
            continue
        except OSError:
            unreadable = True
        else:
            observed_file = True
    if observed_file:
        return "retained"
    if unreadable:
        return "unknown"
    return "removed" if snapshot_created else "not_created"


def _private_opencode_root() -> Path:
    global _ACTIVE_PRIVATE_RUN_ROOT
    parent = Path("/tmp")
    parent_info = parent.lstat()
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or parent.is_symlink()
        or parent_info.st_uid != 0
        or not parent_info.st_mode & stat.S_ISVTX
    ):
        raise GateError("unsafe_private_temp_parent")
    root = Path(tempfile.mkdtemp(prefix="co-t104v2-", dir=parent))
    root.chmod(0o700)
    info = root.lstat()
    socket_path = root / "tmux" / f"tmux-{os.getuid()}" / "default"
    if (
        not stat.S_ISDIR(info.st_mode)
        or root.is_symlink()
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
        or len(os.fsencode(socket_path.absolute())) >= 108
    ):
        with contextlib.suppress(OSError):
            shutil.rmtree(root)
        raise GateError("private_run_root_invalid")
    _ACTIVE_PRIVATE_RUN_ROOT = root
    return root


def _profile_text(
    frontmatter: Any,
    role: str,
    child_env: dict[str, str],
    case_id: Any = DEFAULT_CASE_ID,
) -> str:
    case = _case_definition(case_id)
    allowed, enabled, prompt = _profile_contract(role, case.case_id)
    profile_name = f"{case.profile_prefix}-{role}"
    server = {
        "command": sys.executable,
        "args": ["-m", "cli_agent_orchestrator.mcp_server.server"],
        "env": child_env,
        "cao_tool_profile": _case_native_profile(role, case.case_id),
        **({"enabled_tools": enabled} if enabled is not None else {}),
        "timeout": 60_000,
    }
    post = frontmatter.Post(
        prompt,
        name=profile_name,
        description=f"Private native OpenCode v2 {case.case_id} acceptance profile",
        provider="opencode_cli",
        role="developer",
        allowedTools=allowed,
        mcpServers={"cao-mcp-server": server},
    )
    return frontmatter.dumps(post, sort_keys=False)


def _safe_metadata(
    data: dict[str, Any],
    *,
    role: str,
    expected_group: list[str],
    case_id: Any = DEFAULT_CASE_ID,
) -> dict[str, Any]:
    case = _case_definition(case_id)
    fields = (
        "id",
        "name",
        "session_name",
        "provider",
        "agent_profile",
        "caller_id",
        "allowed_tools",
        "group",
        "metadata",
        "session_incarnation_id",
    )
    if any(field not in data for field in fields):
        raise GateError("terminal_metadata_shape_invalid")
    expected_allowed = _profile_contract(role, case.case_id)[0]
    if (
        not _safe_terminal_identity(data.get("id"))
        or not isinstance(data.get("name"), str)
        or not data["name"]
        or not isinstance(data.get("session_name"), str)
        or not data["session_name"]
        or (
            data.get("caller_id") is not None and not _safe_terminal_identity(data.get("caller_id"))
        )
        or data.get("provider") != "opencode_cli"
        or data.get("agent_profile") != f"{case.profile_prefix}-{role}"
        or data.get("allowed_tools") != expected_allowed
        or data.get("group") != expected_group
        or (data.get("metadata") is not None and not isinstance(data.get("metadata"), dict))
        or not isinstance(data.get("session_incarnation_id"), str)
        or not data.get("session_incarnation_id")
    ):
        raise GateError("terminal_metadata_contract_mismatch")
    safe = {field: data[field] for field in fields}
    encoded = json.dumps(safe, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    if len(encoded) > 16_384:
        raise GateError("terminal_metadata_oversized")
    return safe


def _provider_roots(cao_home: Path) -> list[Path]:
    parent = cao_home / "opencode-v2"
    if parent.is_symlink():
        raise GateError("provider_root_parent_invalid")
    if not parent.exists():
        return []
    if not parent.is_dir():
        raise GateError("provider_root_parent_invalid")
    return sorted(path for path in parent.glob("terminal-*") if path.exists() or path.is_symlink())


def _capability_files(cao_home: Path) -> list[Path]:
    directory = cao_home / "tmp" / "interaction-capabilities"
    if directory.is_symlink():
        raise GateError("capability_directory_invalid")
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise GateError("capability_directory_invalid")
    return sorted(path for path in directory.glob("*.cap") if path.exists() or path.is_symlink())


def _expected_capability_path(cao_home: Path, terminal: dict[str, Any]) -> Path:
    terminal_id = terminal.get("id")
    incarnation_id = terminal.get("session_incarnation_id")
    if not _safe_terminal_identity(terminal_id) or not isinstance(incarnation_id, str):
        raise GateError("interaction_source_identity_invalid")
    if re.fullmatch(r"[0-9a-f]{32}", incarnation_id) is None:
        raise GateError("interaction_source_identity_invalid")
    terminal_key = hashlib.sha256(terminal_id.encode("utf-8")).hexdigest()
    incarnation_key = hashlib.sha256(incarnation_id.encode("utf-8")).hexdigest()
    return cao_home / "tmp" / "interaction-capabilities" / f"{terminal_key}.{incarnation_key}.cap"


def _safe_capability_binding_diagnostics(candidate: Any) -> dict[str, bool]:
    """Project only known boolean gate facts; never serialize source data or identities."""
    if not isinstance(candidate, dict):
        return {}
    return {
        name: candidate[name]
        for name in CAPABILITY_BINDING_DIAGNOSTIC_FIELDS
        if type(candidate.get(name)) is bool
    }


def _record_capability_binding_diagnostics(receipt: dict[str, Any], candidate: Any) -> None:
    safe = _safe_capability_binding_diagnostics(candidate)
    if safe:
        receipt["sender_capability_binding_diagnostics"] = safe


def _capability_binding_diagnostics_pass(receipt: dict[str, Any]) -> bool:
    diagnostics = receipt.get("sender_capability_binding_diagnostics")
    return (
        isinstance(diagnostics, dict)
        and set(diagnostics) == set(CAPABILITY_BINDING_DIAGNOSTIC_FIELDS)
        and all(
            type(diagnostics.get(name)) is bool and diagnostics[name] is True
            for name in CAPABILITY_BINDING_DIAGNOSTIC_FIELDS
        )
    )


def _runtime_capability_contract(
    cao_home: Path,
    target: Any,
    terminal: dict[str, Any],
    *,
    api_server: Any,
    api_process: Any,
    api_identity: Any,
    python_path: Path,
    diagnostics: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Prove the token binds to this current source and sealed loopback creator."""
    if diagnostics is not None:
        diagnostics.clear()
    capability_path = _expected_capability_path(cao_home, terminal)
    files = _capability_files(cao_home)
    if files != [capability_path]:
        raise GateError("sender_runtime_capability_ownership_mismatch")
    capability_directory = capability_path.parent
    directory_metadata = capability_directory.lstat()
    if (
        capability_directory.is_symlink()
        or not stat.S_ISDIR(directory_metadata.st_mode)
        or directory_metadata.st_uid != os.getuid()
        or stat.S_IMODE(directory_metadata.st_mode) != 0o700
    ):
        raise GateError("sender_capability_directory_not_private_owned")
    metadata = capability_path.lstat()
    if (
        capability_path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size < 32
        or metadata.st_size > 256
    ):
        raise GateError("sender_capability_file_not_private_owned")
    try:
        token = capability_path.read_text(encoding="ascii")
    except (OSError, UnicodeError):
        raise GateError("sender_capability_file_invalid") from None
    if not token or "\n" in token or "\r" in token:
        raise GateError("sender_capability_file_invalid")
    from cli_agent_orchestrator.clients.interaction_repository import InteractionRepository
    from cli_agent_orchestrator.services.interactions.capability import (
        canonical_tool_fingerprint,
        capability_hash,
    )
    from cli_agent_orchestrator.services.interactions.service import InteractionService

    terminal_id = terminal["id"]
    incarnation_id = terminal["session_incarnation_id"]
    expected_principal = _auth_off_loopback_creation_principal(
        api_server,
        api_process,
        api_identity,
        python_path,
        diagnostics=diagnostics,
    )
    service = InteractionService(InteractionRepository(target))
    actor = service.authenticate_agent(token)
    status = service.source_status(terminal_id, incarnation_id, actor.turn_generation)
    with service._read() as connection:
        source = connection.execute(
            "SELECT * FROM interaction_sources WHERE terminal_id=%s AND incarnation_id=%s",
            (terminal_id, incarnation_id),
        ).fetchone()
    if not _capability_source_binding_matches(
        actor,
        status,
        source,
        terminal,
        token,
        capability_hash,
        canonical_tool_fingerprint,
        expected_principal,
        diagnostics=diagnostics,
    ):
        raise GateError("sender_runtime_capability_source_binding_mismatch")
    return capability_path, {
        "current_source_bound": True,
        "capability_available": True,
        "capability_file_private_owned": True,
        "capability_hash_matches_source": True,
        "source_current": True,
        "source_owner_kind": "authenticated_creation",
        "source_owner_matches_expected_local_operator": True,
        "auth_off_loopback_api_process_binding_verified": True,
        "source_allowed_tool_fingerprint_matches": True,
        "capability_file_path_sha256": hashlib.sha256(
            str(capability_path).encode("utf-8")
        ).hexdigest(),
    }


def _capability_source_binding_matches(
    actor: Any,
    status: Any,
    source: Any,
    terminal: dict[str, Any],
    token: str,
    capability_hash_fn: Any,
    canonical_fingerprint_fn: Any,
    expected_principal: Any,
    *,
    diagnostics: dict[str, Any] | None = None,
) -> bool:
    from cli_agent_orchestrator.security.auth import is_verified_principal

    terminal_id = terminal.get("id")
    incarnation_id = terminal.get("session_incarnation_id")
    fingerprint, allowed_tools = canonical_fingerprint_fn(terminal.get("allowed_tools"))
    expected_local_operator = (
        is_verified_principal(expected_principal)
        and expected_principal.kind == "local_operator"
        and expected_principal.issuer == "urn:cao:local-operator"
        and expected_principal.subject == str(os.geteuid())
    )
    source_current = (
        actor.terminal_id == terminal_id
        and actor.incarnation_id == incarnation_id
        and status.state == "current"
        and status.capability_available is True
        and source is not None
        and source["terminal_id"] == terminal_id
        and source["incarnation_id"] == incarnation_id
        and source["revoked_at"] is None
        and source["capability_available"] is True
    )
    capability_hash_matches = source is not None and source[
        "capability_hash"
    ] == capability_hash_fn(token)
    tool_fingerprint_matches = (
        source is not None and source["allowed_tool_fingerprint"] == fingerprint
    )
    owner_exact = (
        source is not None
        and expected_local_operator
        and source["owner_principal_id"] == expected_principal.id
    )
    kind_exact = source is not None and source["owner_source"] == "authenticated_creation"
    other_source_bindings_match = (
        source is not None
        and source["provider"] == "opencode_cli"
        and source["execution_mode"] == "local"
        and source["session_name"] == terminal.get("session_name")
        and source["profile_revision"] == terminal.get("agent_profile")
    )
    if diagnostics is not None:
        diagnostics["source_current"] = bool(source_current)
        if source is not None:
            diagnostics.update(
                {
                    "owner_exact": bool(owner_exact),
                    "kind_exact": bool(kind_exact),
                    "capability_hash_matches": bool(capability_hash_matches),
                    "tool_fingerprint_matches": bool(tool_fingerprint_matches),
                }
            )
    return (
        bool(source_current)
        and bool(capability_hash_matches)
        and bool(tool_fingerprint_matches)
        and bool(other_source_bindings_match)
        and bool(expected_local_operator)
        and bool(owner_exact)
        and bool(kind_exact)
        and allowed_tools == ("@cao-mcp-server",)
    )


def _provider_config_contract(
    cao_home: Path,
    terminal: dict[str, Any],
    *,
    role: str,
    capability_path: Path | None,
    case_id: Any = DEFAULT_CASE_ID,
) -> bool:
    from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV
    from cli_agent_orchestrator.providers.mcp_interactions import (
        is_bundled_cao_mcp_entry,
    )
    from cli_agent_orchestrator.utils.mcp_tool_policy import (
        MCP_TOOL_FILTER_ENV,
        MCP_TOOL_PROFILE_ENV,
    )
    from cli_agent_orchestrator.utils.opencode_v2 import (
        validate_v2_mcp_configuration,
    )

    terminal_id = terminal.get("id")
    agent_profile = terminal.get("agent_profile")
    try:
        case = _case_definition(case_id)
    except GateError:
        return False
    if not _safe_terminal_identity(terminal_id) or agent_profile != f"{case.profile_prefix}-{role}":
        return False
    matches: list[tuple[Path, dict[str, Any]]] = []
    for root in _provider_roots(cao_home):
        marker = root / ".cao-ownership.json"
        try:
            marker_info = marker.lstat()
            if (
                marker.is_symlink()
                or not stat.S_ISREG(marker_info.st_mode)
                or marker_info.st_uid != os.getuid()
                or stat.S_IMODE(marker_info.st_mode) != 0o600
            ):
                continue
            owner = json.loads(marker.read_text(encoding="utf-8"))
            if owner.get("terminal_id") != terminal_id:
                continue
            claim = owner.get("claim")
            root_info = root.lstat()
            if (
                not stat.S_ISDIR(root_info.st_mode)
                or root_info.st_uid != os.getuid()
                or stat.S_IMODE(root_info.st_mode) != 0o700
                or type(claim) is not dict
                or claim.get("major") != 2
                or claim.get("name") != root.name
                or claim.get("device") != root_info.st_dev
                or claim.get("inode") != root_info.st_ino
            ):
                return False
            config_path = root / "opencode.json"
            config_info = config_path.lstat()
            if (
                config_path.is_symlink()
                or not stat.S_ISREG(config_info.st_mode)
                or config_info.st_uid != os.getuid()
                or stat.S_IMODE(config_info.st_mode) != 0o600
            ):
                return False
            config = json.loads(config_path.read_text(encoding="utf-8"))
            matches.append((root, config))
        except (OSError, ValueError, AttributeError):
            return False
    if len(matches) != 1:
        return False
    root, config = matches[0]
    mcp = config.get("mcp")
    servers = mcp.get("servers") if isinstance(mcp, dict) else None
    server = servers.get("cao-mcp-server") if isinstance(servers, dict) else None
    environment = server.get("environment") if isinstance(server, dict) else None
    agents = config.get("agents")
    agent = agents.get(agent_profile) if isinstance(agents, dict) else None
    permissions = agent.get("permissions") if isinstance(agent, dict) else None
    if not isinstance(server, dict) or not is_bundled_cao_mcp_entry("cao-mcp-server", server):
        return False
    if not _native_mcp_profile_contract(
        role,
        server,
        environment,
        permissions,
        capability_path=capability_path,
        case_id=case.case_id,
        filter_env=MCP_TOOL_FILTER_ENV,
        profile_env=MCP_TOOL_PROFILE_ENV,
        legacy_marker=_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV,
    ):
        return False
    try:
        validate_v2_mcp_configuration(
            root,
            agent_name=agent_profile,
            server_name="cao-mcp-server",
            require_interaction=case.requires_human_capability and role == "sender",
        )
    except (OSError, ValueError):
        return False
    return True


def _native_mcp_profile_contract(
    role: str,
    server: Any,
    environment: Any,
    permissions: Any,
    *,
    capability_path: Path | None,
    case_id: Any = DEFAULT_CASE_ID,
    filter_env: str,
    profile_env: str,
    legacy_marker: str,
) -> bool:
    from cli_agent_orchestrator.utils.opencode_v2 import _effective_permission

    if role not in ("sender", "receiver"):
        return False

    try:
        case = _case_definition(case_id)
    except GateError:
        return False

    if (
        not isinstance(server, dict)
        or not isinstance(environment, dict)
        or not isinstance(permissions, list)
        or server.get("codemode") is not False
        or server.get("enabled_tools") is not None
        or environment.get(profile_env) != _case_native_profile(role, case.case_id)
        or legacy_marker in environment
    ):
        return False
    if role == "sender":
        if case.requires_human_capability:
            profile_binding = (
                filter_env not in environment
                and capability_path is not None
                and environment.get("CAO_INTERACTION_CAPABILITY_FILE") == str(capability_path)
            )
        else:
            expected_filter = json.dumps(
                list(case.sender_enabled_mcp_tools or ()), separators=(",", ":")
            )
            profile_binding = (
                case.sender_enabled_mcp_tools is not None
                and environment.get(filter_env) == expected_filter
                and capability_path is None
                and "CAO_INTERACTION_CAPABILITY_FILE" not in environment
            )
        return (
            profile_binding
            and all(
                _effective_permission(permissions, "cao-mcp-server_" + tool) == "allow"
                for tool in case.sender_catalog
            )
            and (
                not case.requires_human_capability
                or not any(
                    rule.get("action") == "cao-mcp-server_execute" and rule.get("effect") == "allow"
                    for rule in permissions
                    if isinstance(rule, dict)
                )
            )
        )
    if role == "receiver":
        return (
            environment.get(filter_env) == "[]"
            and "CAO_INTERACTION_CAPABILITY_FILE" not in environment
        )
    return False


def _http_tool_offer_plugin_bytes(
    source: bytes,
    *,
    nonce: str,
    tool_names: tuple[str, ...],
) -> bytes:
    """Derive a private observer for this case's model and role wire catalog."""
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError:
        raise GateError("http_tool_offer_plugin_source_invalid") from None
    old_model = 'const MODEL_ID = "longcat-2.5-preview-free"'
    new_model = 'const MODEL_ID = "glm-5.3-flash"'
    if text.count(old_model) != 1:
        raise GateError("http_tool_offer_plugin_model_source_mismatch")
    text = text.replace(old_model, new_model, 1)
    old_names = """const TOOL_NAMES = Object.freeze([
  "cao-mcp-server_cao_ask_user",
  "cao-mcp-server_cao_wait_for_user_answers",
  "cao-mcp-server_cao_acknowledge_user_answers",
])"""
    new_names = (
        "const TOOL_NAMES = Object.freeze([\n"
        + ",\n".join(f'  "{tool}"' for tool in tool_names)
        + "\n])"
    )
    if text.count(old_names) != 1:
        raise GateError("http_tool_offer_plugin_catalog_source_mismatch")
    text = text.replace(old_names, new_names, 1)
    old_nonce = "nonce = process.env.CAO_OPENCODE_HTTP_TOOL_OFFER_NONCE"
    if not re.fullmatch(r"[0-9a-f]{32}", nonce) or text.count(old_nonce) != 1:
        raise GateError("http_tool_offer_plugin_nonce_source_mismatch")
    return text.replace(old_nonce, f'nonce = "{nonce}"', 1).encode("utf-8")


def _http_prompt_plugin_bytes(source: bytes, *, nonce: str) -> bytes:
    if type(nonce) is not str or re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise GateError("http_prompt_nonce_invalid")
    old = b"nonce = process.env.CAO_OPENCODE_HTTP_PROMPT_NONCE"
    if source.count(old) != 1:
        raise GateError("http_prompt_plugin_binding_shape_invalid")
    return source.replace(old, ('nonce = "' + nonce + '"').encode(), 1)


def _http_prompt_expectations(*, sender_id, nonce, phase_prompts):
    from types import SimpleNamespace

    from cli_agent_orchestrator.providers.base import BaseProvider

    if (
        not _safe_terminal_identity(sender_id)
        or type(nonce) is not str
        or re.fullmatch(r"[0-9a-f]{32}", nonce) is None
        or type(phase_prompts) is not tuple
        or len(phase_prompts) != len(PHASES)
        or any(type(value) is not str for value in phase_prompts)
    ):
        raise GateError("http_prompt_expectation_context_invalid")
    placeholder = "CAO-TURN-RECEIPT-" + "0" * 32
    formatted = BaseProvider._format_turn_receipt_input(
        SimpleNamespace(terminal_id=sender_id), "", placeholder
    )
    if formatted.count(placeholder) != 1:
        raise GateError("http_prompt_receipt_formatter_shape_invalid")
    prefix, suffix = formatted.split(placeholder)
    return {
        "schema_version": 1,
        "nonce": nonce,
        "phase_prompts": list(phase_prompts),
        "receipt_prefix": prefix,
        "receipt_suffix": suffix,
        "last_instruction": "the public result body must be exactly this line: " + LAST_RECEIPT,
    }


def _http_response_expectations(*, nonce):
    if type(nonce) is not str or re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise GateError("http_response_expectation_context_invalid")
    if type(PHASES) is not tuple or len(PHASES) != 2:
        raise GateError("http_response_expectation_context_invalid")
    messages = [message for _phase, message in PHASES]
    try:
        valid_messages = all(
            type(message) is str and 0 < len(message.encode("utf-8", errors="strict")) <= 8192
            for message in messages
        )
    except UnicodeEncodeError:
        valid_messages = False
    if not valid_messages:
        raise GateError("http_response_expectation_context_invalid")
    return {"schema_version": 1, "nonce": nonce, "phase_messages": messages}


def _read_source_bound_prompt_file(relative):
    if relative not in {
        "test/live_provider/opencode_http_prompt_observer.py",
        "test/live_provider/opencode_http_prompt_observer.js",
    }:
        raise GateError("http_prompt_source_binding_invalid")
    source = REPO / relative
    expected = (RUN_MANIFEST or {}).get("source_sha256s", {}).get(relative)
    if source.is_symlink() or not _digest_shape(expected):
        raise GateError("http_prompt_source_binding_invalid")
    before = source.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > 128 * 1024:
        raise GateError("http_prompt_source_binding_invalid")
    data = source.read_bytes()
    after = source.lstat()
    if (
        source.is_symlink()
        or (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size)
        != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size)
        or hashlib.sha256(data).hexdigest() != expected
        or _sha256(source) != expected
    ):
        raise GateError("http_prompt_source_binding_invalid")
    return data


_HTTP_RESPONSE_SOURCE_LIMITS = {
    "test/live_provider/opencode_http_response_argument_observer.js": 128 * 1024,
    "test/live_provider/opencode_http_response_protocol.js": 128 * 1024,
    "test/live_provider/opencode_http_response_argument_observer.py": 128 * 1024,
}


def _read_source_bound_response_file(relative):
    maximum = _HTTP_RESPONSE_SOURCE_LIMITS.get(relative)
    expected = (RUN_MANIFEST or {}).get("source_sha256s", {}).get(relative)
    if maximum is None or not _digest_shape(expected):
        raise GateError("http_response_source_binding_invalid")
    source = REPO / relative
    try:
        before = source.lstat()
        if (
            source.is_symlink()
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > maximum
            or source.resolve(strict=True) != source.absolute()
        ):
            raise GateError("http_response_source_binding_invalid")
        data = source.read_bytes()
        after = source.lstat()
    except GateError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise GateError("http_response_source_binding_invalid") from None
    identity = lambda info: (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )
    if (
        len(data) > maximum
        or identity(before) != identity(after)
        or hashlib.sha256(data).hexdigest() != expected
        or _sha256(source) != expected
    ):
        raise GateError("http_response_source_binding_invalid")
    return data


def _capture_http_prompt_diagnostic(*, context, root, raw_dir, writers_dead):
    unavailable = {
        "schema": "spec025.t125.http-prompt-diagnostic.v1",
        "status": "unavailable",
        "sealed_raw_copy_verified": False,
        "active_receipt_identity_attested": False,
        "acceptance_input_changed": False,
    }
    if writers_dead is not True or type(context) is not dict:
        return unavailable
    if (
        not isinstance(root, Path)
        or root != _ACTIVE_PRIVATE_RUN_ROOT
        or not isinstance(raw_dir, Path)
        or raw_dir != EVIDENCE / "raw"
        or context.get("working_directory") != root / "sender-workdir"
        or context.get("directory") != root / "sender-workdir/.opencode/http-prompt-observation"
        or type(context.get("expectation_bytes")) is not bytes
        or type(context.get("plugin_bytes")) is not bytes
        or type(context.get("nonce")) is not str
        or re.fullmatch(r"[0-9a-f]{32}", context["nonce"]) is None
    ):
        return unavailable
    work = context["working_directory"]
    ledger = context["directory"]
    directories = (
        root,
        work,
        work / ".opencode",
        work / ".opencode/plugins",
        ledger,
        EVIDENCE,
        raw_dir,
    )
    directory_identities = {}
    for directory in directories:
        info = directory.lstat()
        directory_identities[directory] = (info.st_dev, info.st_ino)
        if (
            directory.is_symlink()
            or directory.resolve(strict=True) != directory
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            return unavailable
    source = _read_source_bound_prompt_file("test/live_provider/opencode_http_prompt_observer.js")
    if _http_prompt_plugin_bytes(source, nonce=context["nonce"]) != context["plugin_bytes"]:
        return unavailable
    if (
        _read_owned_private_file(
            work / ".opencode/plugins/cao-http-prompt-observer.js", maximum=128 * 1024
        )
        != context["plugin_bytes"]
    ):
        return unavailable
    if (
        _read_owned_private_file(
            work / ".opencode/http-prompt-expectations.json", maximum=64 * 1024
        )
        != context["expectation_bytes"]
    ):
        return unavailable
    reader_relative = "test/live_provider/opencode_http_prompt_observer.py"
    reader_bytes = _read_source_bound_prompt_file(reader_relative)
    module = types.ModuleType("spec025_t125_http_prompt_reader")
    module.__file__ = str(REPO / reader_relative)
    exec(compile(reader_bytes, module.__file__, "exec"), module.__dict__)
    if _read_source_bound_prompt_file(reader_relative) != reader_bytes:
        return unavailable
    # Snapshot only the closed vocabulary; never adopt another run's directory.
    names = []
    with os.scandir(ledger) as entries:
        for entry in entries:
            if len(names) >= 17:
                return unavailable
            names.append(entry.name)
    names.sort()
    if (
        "seal.json" not in names
        or len(names) > 17
        or any(
            name != "seal.json" and re.fullmatch(r"request-[0-9]{4}\.json", name) is None
            for name in names
        )
    ):
        return unavailable
    snapshot = {}
    for name in names:
        snapshot[name] = _read_owned_private_file(
            ledger / name, maximum=2048 if name == "seal.json" else 4096
        )
    if sum(map(len, snapshot.values())) > 72 * 1024:
        return unavailable
    current_names = []
    with os.scandir(ledger) as entries:
        for entry in entries:
            if len(current_names) >= 17:
                return unavailable
            current_names.append(entry.name)
    if sorted(current_names) != names:
        return unavailable
    destination = raw_dir / "sender-http-prompt-observer-copy"
    destination.mkdir(mode=0o700)  # O_EXCL directory semantics: existing evidence is never adopted.
    for name, data in snapshot.items():
        _write_private(destination / name, data)
    for name, data in snapshot.items():
        if (
            _read_owned_private_file(
                destination / name, maximum=2048 if name == "seal.json" else 4096
            )
            != data
            or _read_owned_private_file(
                ledger / name, maximum=2048 if name == "seal.json" else 4096
            )
            != data
        ):
            return unavailable
    for directory, identity in directory_identities.items():
        info = directory.lstat()
        if (
            directory.is_symlink()
            or not stat.S_ISDIR(info.st_mode)
            or (info.st_dev, info.st_ino) != identity
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            return unavailable
    summary = module.read_observation(destination, expected_nonce=context["nonce"])
    if summary.get("observer_status") != "observed" or summary.get("trace_complete") is not True:
        return unavailable
    return {
        "schema": "spec025.t125.http-prompt-diagnostic.v1",
        "status": "sealed_observation_available",
        "observation": summary,
        "sealed_raw_copy_verified": True,
        "active_receipt_identity_attested": False,
        "acceptance_input_changed": False,
    }


def _capture_http_prompt_diagnostic_safely(**kwargs):
    try:
        return _capture_http_prompt_diagnostic(**kwargs)
    except BaseException:
        return {
            "schema": "spec025.t125.http-prompt-diagnostic.v1",
            "status": "unavailable",
            "sealed_raw_copy_verified": False,
            "active_receipt_identity_attested": False,
            "acceptance_input_changed": False,
        }


_HTTP_RESPONSE_PHASE_STATUSES = frozenset(
    {
        "exact",
        "known_punctuation_variant",
        "body_mismatch",
        "target_call_missing",
        "ambiguous_target_calls",
        "unavailable",
        "incomplete",
        "not_observed",
    }
)
_HTTP_RESPONSE_OBSERVER_STATUSES = frozenset({"observed", "incomplete", "unavailable"})
_HTTP_RESPONSE_LEDGER_NAME_PATTERN = re.compile(r"response-[0-9]{4}\.json\Z")


RECEIPT_STAGE_SOURCE_FILES = (
    "src/cli_agent_orchestrator/providers/opencode_cli.py",
    "src/cli_agent_orchestrator/services/status_monitor.py",
    "src/cli_agent_orchestrator/services/terminal_service.py",
    "src/cli_agent_orchestrator/utils/receipt_verification_diagnostics.py",
)


def _prepare_receipt_stage_api_pythonpath(
    *, observer, bootstrap_root, raw_dir, source_hashes, role, capture_render_inputs=False
):
    if role not in {"api_a", "api_b"} or type(source_hashes) is not dict:
        raise GateError("receipt_stage_bootstrap_context_invalid")
    hashes = {relative: source_hashes.get(relative) for relative in RECEIPT_STAGE_SOURCE_FILES}
    if not all(_digest_shape(value) for value in hashes.values()):
        raise GateError("receipt_stage_source_hash_missing")
    bootstrap = Path(bootstrap_root) / role
    options = {"capture_render_inputs": True} if capture_render_inputs is True else {}
    value = observer.prepare_api_observer(bootstrap, raw_dir, REPO, hashes, role, **options)
    expected = os.pathsep.join(map(str, (bootstrap, REPO / "src", REPO)))
    if value != expected:
        raise GateError("receipt_stage_api_pythonpath_mismatch")
    return value


def _receipt_stage_api_ready(raw_dir, role):
    """Reject a healthy API that did not initialize its requested bootstrap."""
    try:
        if type(role) is not str or role not in {"api_a", "api_b"}:
            return False
        directory = (Path(raw_dir) / role).lstat()
        event = (Path(raw_dir) / role / "events.jsonl").lstat()
        return (
            stat.S_ISDIR(directory.st_mode)
            and directory.st_uid == os.getuid()
            and stat.S_IMODE(directory.st_mode) == 0o700
            and stat.S_ISREG(event.st_mode)
            and event.st_uid == os.getuid()
            and event.st_nlink == 1
            and stat.S_IMODE(event.st_mode) == 0o600
        )
    except OSError:
        return False


def _receipt_stage_epoch_summary(value, role):
    from cli_agent_orchestrator.utils.receipt_verification_diagnostics import _ALLOWED_OUTCOMES

    unavailable = {
        "observer_status": "unavailable",
        "role": role,
        "event_count": None,
        "thread_count": None,
        "failed_event_count": None,
        "stage_outcome_counts": None,
    }
    if type(value) is not dict or set(value) != set(unavailable) or value["role"] != role:
        return unavailable
    if value["observer_status"] != "complete":
        return unavailable
    for key in ("event_count", "thread_count", "failed_event_count"):
        if type(value[key]) is not int or not 0 <= value[key] <= 4096:
            return unavailable
    if value["failed_event_count"] != 0 or value["thread_count"] > value["event_count"]:
        return unavailable
    counts = value["stage_outcome_counts"]
    if type(counts) is not dict or set(counts) != set(_ALLOWED_OUTCOMES):
        return unavailable
    safe = {}
    total = 0
    for stage, outcomes in _ALLOWED_OUTCOMES.items():
        row = counts[stage]
        if type(row) is not dict or set(row) != set(outcomes):
            return unavailable
        if any(type(count) is not int or not 0 <= count <= 4096 for count in row.values()):
            return unavailable
        safe[stage] = {outcome: row[outcome] for outcome in sorted(outcomes)}
        total += sum(row.values())
    if total != value["event_count"]:
        return unavailable
    return {**value, "stage_outcome_counts": safe}


def _capture_receipt_stage_diagnostic_safely(*, observer, raw_dir, writers_dead):
    result = {
        "schema": "spec025.receipt-verification-stages.v1",
        "status": "unavailable",
        "acceptance_input_changed": False,
    }
    if writers_dead is not True:
        return {**result, "reason": "writers_not_confirmed_dead"}
    try:
        if Path(raw_dir).resolve() != (EVIDENCE / "raw/receipt-verification").resolve():
            return {**result, "reason": "context_invalid"}
        epochs = {
            role: _receipt_stage_epoch_summary(
                observer.read_api_observation(raw_dir, role=role, writers_dead=True), role
            )
            for role in ("api_a", "api_b")
        }
        complete = sum(row["observer_status"] == "complete" for row in epochs.values())
        return {
            **result,
            "status": "complete" if complete == 2 else "partial" if complete else "unavailable",
            "epochs": epochs,
        }
    except BaseException:
        return {**result, "reason": "observer_unavailable"}


def _http_response_unavailable(reason):
    return {
        "schema": "spec025.t128.http-response-argument-diagnostic.v1",
        "status": "unavailable",
        "reason": reason,
        "observation": {
            "observer_status": "unavailable",
            "trace_complete": False,
            "overflow": None,
            "request_count": None,
            "response_count": None,
            "durable_record_count": None,
            "phase_results": [
                {
                    "phase_ordinal": phase,
                    "status": "unavailable",
                    "target_tool_call_count": None,
                    "argument_exact": None,
                    "known_punctuation_variant_exact": None,
                    "expected_utf8_bytes": None,
                    "observed_utf8_bytes": None,
                }
                for phase in (1, 2)
            ],
        },
        "sealed_raw_copy_verified": False,
        "active_receipt_identity_attested": False,
        "acceptance_input_changed": False,
    }


def _project_http_response_observation(summary):
    if type(summary) is not dict:
        return None
    status = summary.get("observer_status")
    trace_complete = summary.get("trace_complete")
    overflow = summary.get("overflow")
    counters = tuple(
        summary.get(key) for key in ("request_count", "response_count", "durable_record_count")
    )
    if (
        status not in _HTTP_RESPONSE_OBSERVER_STATUSES
        or type(trace_complete) is not bool
        or (overflow is not None and type(overflow) is not bool)
        or any(
            value is not None and (type(value) is not int or not 0 <= value <= 16)
            for value in counters
        )
    ):
        return None
    raw_phases = summary.get("phase_results")
    if type(raw_phases) is not list or len(raw_phases) != 2:
        return None
    phases = []
    for expected_phase, raw in enumerate(raw_phases, start=1):
        if (
            type(raw) is not dict
            or type(raw.get("phase_ordinal")) is not int
            or raw["phase_ordinal"] != expected_phase
        ):
            return None
        phase_status = raw.get("status")
        call_count = raw.get("target_tool_call_count")
        exact = raw.get("argument_exact")
        variant = raw.get("known_punctuation_variant_exact")
        expected_bytes = raw.get("expected_utf8_bytes")
        observed_bytes = raw.get("observed_utf8_bytes")
        if (
            phase_status not in _HTTP_RESPONSE_PHASE_STATUSES
            or (
                call_count is not None
                and (type(call_count) is not int or not 0 <= call_count <= 32)
            )
            or (exact is not None and type(exact) is not bool)
            or (variant is not None and type(variant) is not bool)
            or (
                expected_bytes is not None
                and (type(expected_bytes) is not int or not 0 <= expected_bytes <= 8192)
            )
            or (
                observed_bytes is not None
                and (type(observed_bytes) is not int or not 0 <= observed_bytes <= 65536)
            )
        ):
            return None
        phases.append(
            {
                "phase_ordinal": expected_phase,
                "status": phase_status,
                "target_tool_call_count": call_count,
                "argument_exact": exact,
                "known_punctuation_variant_exact": variant,
                "expected_utf8_bytes": expected_bytes,
                "observed_utf8_bytes": observed_bytes,
            }
        )
    return {
        "observer_status": status,
        "trace_complete": trace_complete,
        "overflow": overflow,
        "request_count": counters[0],
        "response_count": counters[1],
        "durable_record_count": counters[2],
        "phase_results": phases,
    }


def _capture_http_response_argument_diagnostic(*, context, root, raw_dir, writers_dead):
    if writers_dead is not True:
        return _http_response_unavailable("writers_not_confirmed_dead")
    if (
        type(context) is not dict
        or not isinstance(root, Path)
        or root != _ACTIVE_PRIVATE_RUN_ROOT
        or not isinstance(raw_dir, Path)
        or raw_dir != EVIDENCE / "raw"
        or context.get("working_directory") != root / "sender-workdir"
        or context.get("directory")
        != root / "sender-workdir/.opencode/http-response-argument-observation"
        or type(context.get("nonce")) is not str
        or re.fullmatch(r"[0-9a-f]{32}", context["nonce"]) is None
        or any(
            type(context.get(key)) is not bytes
            for key in (
                "plugin_bytes",
                "protocol_bytes",
                "prompt_plugin_bytes",
                "prompt_expectation_bytes",
                "expectation_bytes",
            )
        )
    ):
        return _http_response_unavailable("context_invalid")

    work = context["working_directory"]
    ledger = context["directory"]
    opencode = work / ".opencode"
    plugins = opencode / "plugins"
    support = opencode / "cao-http-response-support"
    directories = (root, work, opencode, plugins, support, ledger, EVIDENCE, raw_dir)
    directory_identities = {}
    try:
        for directory in directories:
            info = directory.lstat()
            directory_identities[directory] = (info.st_dev, info.st_ino)
            if (
                directory.is_symlink()
                or directory.resolve(strict=True) != directory
                or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                return _http_response_unavailable("private_directory_invalid")

        prompt_source = _read_source_bound_prompt_file(
            "test/live_provider/opencode_http_prompt_observer.js"
        )
        expected_prompt_plugin = _http_prompt_plugin_bytes(prompt_source, nonce=context["nonce"])
        response_source = _read_source_bound_response_file(
            "test/live_provider/opencode_http_response_argument_observer.js"
        )
        protocol_source = _read_source_bound_response_file(
            "test/live_provider/opencode_http_response_protocol.js"
        )
        reader_relative = "test/live_provider/opencode_http_response_argument_observer.py"
        reader_source = _read_source_bound_response_file(reader_relative)
        reader_module = types.ModuleType("spec025_t128_http_response_reader")
        reader_module.__file__ = str(REPO / reader_relative)
        exec(compile(reader_source, reader_module.__file__, "exec"), reader_module.__dict__)
        expected_response_plugin = reader_module.specialize_plugin_bytes(
            response_source, context["nonce"]
        )
        if (
            expected_prompt_plugin != context["prompt_plugin_bytes"]
            or expected_response_plugin != context["plugin_bytes"]
            or protocol_source != context["protocol_bytes"]
        ):
            return _http_response_unavailable("source_binding_mismatch")

        prompt_plugin_path = plugins / "cao-http-prompt-observer.js"
        response_plugin_path = plugins / "cao-http-response-argument-observer.js"
        protocol_path = support / "opencode_http_response_protocol.js"
        prompt_expectation_path = opencode / "http-prompt-expectations.json"
        response_expectation_path = opencode / "http-response-argument-expectations.json"
        installed_files = (
            (prompt_plugin_path, context["prompt_plugin_bytes"], 128 * 1024),
            (response_plugin_path, context["plugin_bytes"], 128 * 1024),
            (protocol_path, context["protocol_bytes"], 128 * 1024),
            (prompt_expectation_path, context["prompt_expectation_bytes"], 64 * 1024),
            (response_expectation_path, context["expectation_bytes"], 64 * 1024),
        )
        for path, expected, maximum in installed_files:
            if _read_owned_private_file(path, maximum=maximum) != expected:
                return _http_response_unavailable("installed_source_mismatch")

        if (
            _read_source_bound_prompt_file("test/live_provider/opencode_http_prompt_observer.js")
            != prompt_source
        ):
            return _http_response_unavailable("source_changed")
        if (
            _read_source_bound_response_file(
                "test/live_provider/opencode_http_response_argument_observer.js"
            )
            != response_source
            or _read_source_bound_response_file(
                "test/live_provider/opencode_http_response_protocol.js"
            )
            != protocol_source
            or _read_source_bound_response_file(reader_relative) != reader_source
        ):
            return _http_response_unavailable("source_changed")

        names = []
        with os.scandir(ledger) as entries:
            for entry in entries:
                if len(names) >= 17:
                    return _http_response_unavailable("ledger_limit_exceeded")
                names.append(entry.name)
        names.sort()
        if (
            "seal.json" not in names
            or len(names) > 17
            or any(
                name != "seal.json" and _HTTP_RESPONSE_LEDGER_NAME_PATTERN.fullmatch(name) is None
                for name in names
            )
        ):
            return _http_response_unavailable("ledger_shape_invalid")
        ledger_snapshot = {}
        for name in names:
            ledger_snapshot[name] = _read_owned_private_file(
                ledger / name, maximum=2048 if name == "seal.json" else 4096
            )
        ledger_bytes = sum(map(len, ledger_snapshot.values()))
        total_bound = 128 * 1024 + 128 * 1024 + 128 * 1024 + 64 * 1024 + 64 * 1024 + 72 * 1024
        snapshot_bytes = sum(len(item[1]) for item in installed_files) + ledger_bytes
        if ledger_bytes > 72 * 1024 or snapshot_bytes > total_bound:
            return _http_response_unavailable("ledger_limit_exceeded")

        current_names = []
        with os.scandir(ledger) as entries:
            for entry in entries:
                if len(current_names) >= 17:
                    return _http_response_unavailable("ledger_limit_exceeded")
                current_names.append(entry.name)
        if sorted(current_names) != names:
            return _http_response_unavailable("ledger_changed")

        snapshot_root = raw_dir / "sender-http-response-argument-observer-copy"
        snapshot_root.mkdir(mode=0o700)
        _private_directory(snapshot_root, parent=raw_dir)
        snapshot_opencode = _private_directory(snapshot_root / ".opencode", parent=snapshot_root)
        snapshot_plugins = _private_directory(
            snapshot_opencode / "plugins", parent=snapshot_opencode
        )
        snapshot_support = _private_directory(
            snapshot_opencode / "cao-http-response-support", parent=snapshot_opencode
        )
        snapshot_ledger = _private_directory(
            snapshot_opencode / "http-response-argument-observation", parent=snapshot_opencode
        )
        snapshot_files = (
            (
                snapshot_plugins / prompt_plugin_path.name,
                context["prompt_plugin_bytes"],
                128 * 1024,
            ),
            (snapshot_plugins / response_plugin_path.name, context["plugin_bytes"], 128 * 1024),
            (snapshot_support / protocol_path.name, context["protocol_bytes"], 128 * 1024),
            (
                snapshot_opencode / prompt_expectation_path.name,
                context["prompt_expectation_bytes"],
                64 * 1024,
            ),
            (
                snapshot_opencode / response_expectation_path.name,
                context["expectation_bytes"],
                64 * 1024,
            ),
        )
        for path, content, maximum in snapshot_files:
            _write_private(path, content)
            if _read_owned_private_file(path, maximum=maximum) != content:
                return _http_response_unavailable("snapshot_copy_mismatch")
        for name, content in ledger_snapshot.items():
            destination = snapshot_ledger / name
            _write_private(destination, content)
            maximum = 2048 if name == "seal.json" else 4096
            if (
                _read_owned_private_file(destination, maximum=maximum) != content
                or _read_owned_private_file(ledger / name, maximum=maximum) != content
            ):
                return _http_response_unavailable("snapshot_copy_mismatch")

        for directory, identity in directory_identities.items():
            info = directory.lstat()
            if (
                directory.is_symlink()
                or not stat.S_ISDIR(info.st_mode)
                or (info.st_dev, info.st_ino) != identity
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                return _http_response_unavailable("private_directory_changed")

        if (
            _read_source_bound_response_file(reader_relative) != reader_source
            or _read_source_bound_prompt_file("test/live_provider/opencode_http_prompt_observer.js")
            != prompt_source
        ):
            return _http_response_unavailable("source_changed")
        summary = reader_module.read_observation(snapshot_ledger, expected_nonce=context["nonce"])
        observation = _project_http_response_observation(summary)
        if observation is None:
            return _http_response_unavailable("reader_projection_invalid")
        return {
            "schema": "spec025.t128.http-response-argument-diagnostic.v1",
            "status": (
                "sealed_observation_available"
                if observation["observer_status"] in {"observed", "incomplete"}
                else "unavailable"
            ),
            "reason": (
                "none"
                if observation["observer_status"] in {"observed", "incomplete"}
                else "observer_unavailable"
            ),
            "observation": observation,
            "sealed_raw_copy_verified": True,
            "active_receipt_identity_attested": False,
            "acceptance_input_changed": False,
        }
    except BaseException:
        return _http_response_unavailable("collector_error")


def _capture_http_response_argument_diagnostic_safely(**kwargs):
    try:
        return _capture_http_response_argument_diagnostic(**kwargs)
    except BaseException:
        return _http_response_unavailable("collector_error")


def _http_tool_offer_contract(
    observation: Any,
    *,
    expected_nonce: str,
    expected_mcp_tool_names: tuple[str, ...],
    tracked_tool_names: tuple[str, ...] = HTTP_OFFER_TOOL_NAMES,
    case_id: Any = DEFAULT_CASE_ID,
) -> bool:
    try:
        case = _case_definition(case_id)
    except GateError:
        return False
    expected_sender_wire_names = tuple("cao-mcp-server_" + tool for tool in case.sender_catalog)
    if (
        not isinstance(observation, dict)
        or expected_mcp_tool_names not in (expected_sender_wire_names, ())
        or tracked_tool_names != _case_http_offer_names(case.case_id)
    ):
        return False
    records = observation.get("requests")
    if (
        observation.get("observer_status") != "observed"
        or observation.get("tool_offer_status") != "observed"
        or observation.get("nonce") != expected_nonce
        or observation.get("hook_disposed") is not True
        or observation.get("trace_complete") is not True
        or observation.get("overflow") is not False
        or not isinstance(records, list)
        or type(observation.get("request_count")) is not int
        or type(observation.get("durable_record_count")) is not int
        or observation.get("request_count") != observation.get("durable_record_count")
        or observation.get("request_count") != len(records)
        or observation.get("durable_record_count") != len(records)
        or type(observation.get("target_primary_request_count")) is not int
        or type(observation.get("valid_target_primary_request_count")) is not int
        or type(observation.get("other_tool_name_count")) is not int
        or observation.get("valid_target_primary_request_count")
        != observation.get("target_primary_request_count")
    ):
        return False
    target_records = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("model_match") is True
        and record.get("kind") == "primary"
    ]
    if len(target_records) != observation["target_primary_request_count"]:
        return False
    if not set(expected_mcp_tool_names).issubset(tracked_tool_names):
        return False
    for record in target_records:
        if record.get("status") not in {"observed", "valid_empty"}:
            return False
        names = record.get("name_counts")
        tool_count = record.get("tool_count")
        other_count = record.get("other_name_count")
        if (
            not isinstance(names, dict)
            or set(names) != set(tracked_tool_names)
            or type(tool_count) is not int
            or type(other_count) is not int
            or other_count != 0
            or any(type(count) is not int or count not in (0, 1) for count in names.values())
        ):
            return False
        if (
            tool_count != sum(names.values())
            or record.get("status") != ("observed" if tool_count else "valid_empty")
            or any(names[name] != 1 for name in expected_mcp_tool_names)
            or any(
                names[name] != 0
                for name in _case_mcp_wire_names(case.case_id)
                if name not in expected_mcp_tool_names
            )
            or any(names[name] != 0 for name in DIAGNOSTIC_ONLY_OPENCODE_TOOL_NAMES)
        ):
            return False
    aggregate_counts = observation.get("tool_name_counts")
    return (
        observation["target_primary_request_count"] >= len(PHASES)
        and observation.get("other_tool_name_count") == 0
        and isinstance(aggregate_counts, dict)
        and set(aggregate_counts) == set(tracked_tool_names)
        and all(
            type(aggregate_counts[name]) is int
            and aggregate_counts[name]
            == sum(record["name_counts"][name] for record in target_records)
            for name in tracked_tool_names
        )
    )


def _read_http_tool_offer_observation(
    observer: Any,
    ledger: Path,
    *,
    expected_nonce: str,
    tracked_tool_names: tuple[str, ...] = HTTP_OFFER_TOOL_NAMES,
) -> dict[str, Any]:
    """Use the existing strict private ledger reader with case-specific names."""
    original_names = observer.TOOL_NAMES
    try:
        observer.TOOL_NAMES = tracked_tool_names
        return observer.read_observation(ledger, expected_nonce=expected_nonce)
    finally:
        observer.TOOL_NAMES = original_names


def _api_json(
    requests: Any,
    method: str,
    url: str,
    *,
    expected: int,
    deadline: float | None = None,
    **kwargs: Any,
) -> Any:
    _cancellation_checkpoint()
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if not math.isfinite(remaining) or remaining <= 0:
            raise GateError("api_request_deadline_expired")
        kwargs["timeout"] = (remaining, remaining)
    response = requests.request(method, url, **kwargs)
    _cancellation_checkpoint()
    if deadline is not None and time.monotonic() > deadline:
        raise GateError("api_response_after_deadline")
    if response.status_code != expected:
        raise GateError("api_http_status_mismatch")
    try:
        return response.json()
    except (ValueError, requests.JSONDecodeError):
        raise GateError("api_json_invalid") from None


def _capture_api_process(server: Any, journey: Any) -> tuple[Any, Any]:
    from test.live_provider import message_api_restart_live as message_live

    process = message_live._api_process(server)
    info = journey._process_info(process.pid)
    if info is None or info[0].uid != os.getuid() or info[2] == "Z":
        raise GateError("api_process_identity_unavailable")
    return process, info[0]


def _api_worker_invocation_matches(process: Any, server: Any, python_path: Path) -> bool:
    args = getattr(process, "args", None)
    port = getattr(server, "port", None)
    if (
        not isinstance(args, list)
        or any(type(argument) is not str for argument in args)
        or type(port) is not int
        or len(args) != 7
        or args[1:]
        != [
            "-m",
            "cli_agent_orchestrator.api.main",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]
    ):
        return False
    try:
        executable = Path(args[0])
        return (
            executable.is_absolute()
            and executable == python_path
            and executable.resolve(strict=True).is_absolute()
        )
    except (OSError, RuntimeError, ValueError):
        return False


def _auth_off_loopback_creation_principal(
    server: Any,
    process: Any,
    identity: Any,
    python_path: Path,
    *,
    diagnostics: dict[str, Any] | None = None,
) -> Any:
    """Derive the expected sealed local creator only for this proven loopback API."""
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics["trusted_api_context"] = False
    from cli_agent_orchestrator.security.auth import (
        is_auth_enabled,
        is_verified_principal,
        local_operator_principal,
    )

    port = getattr(server, "port", None)
    process_pid = getattr(process, "pid", None)
    identity_pid = getattr(identity, "pid", None)
    identity_uid = getattr(identity, "uid", None)
    identity_start = getattr(identity, "start_time", None)
    if (
        os.environ.get("CAO_AUTH_MODE") != "off"
        or os.geteuid() != os.getuid()
        or is_auth_enabled() is not False
        or type(port) is not int
        or not 1 <= port <= 65535
        or getattr(server, "url", None) != f"http://127.0.0.1:{port}"
        or type(process_pid) is not int
        or process_pid <= 0
        or identity_pid != process_pid
        or identity_uid != os.getuid()
        or type(identity_start) is not int
        or identity_start <= 0
        or getattr(process, "poll", lambda: 1)() is not None
        or not _api_worker_invocation_matches(process, server, python_path)
    ):
        raise GateError("auth_off_loopback_creator_binding_mismatch")
    try:
        principal = local_operator_principal()
    except Exception:
        raise GateError("auth_off_loopback_creator_principal_unavailable") from None
    if (
        not is_verified_principal(principal)
        or principal.kind != "local_operator"
        or principal.issuer != "urn:cao:local-operator"
        or principal.subject != str(os.geteuid())
    ):
        raise GateError("auth_off_loopback_creator_principal_mismatch")
    if diagnostics is not None:
        diagnostics["trusted_api_context"] = True
    return principal


def _record_unreturned_api_servers(
    api_servers: list[dict[str, Any]],
    *,
    role: str,
    before: set[Any],
    after: set[Any],
    capture_identity: Any,
) -> int:
    """Track fixture-owned API handles retained after a failed start call."""
    newly_registered = [server for server in after if server not in before]
    for server in newly_registered:
        record = {
            "role": role,
            "server": server,
            "identity": None,
            "stop_confirmed": False,
            "start_returned": False,
        }
        api_servers.append(record)
        try:
            _process, identity = capture_identity(server)
        except Exception:
            # Keep the owner handle for best-effort stop; missing identity stays
            # fail-closed at the PostgreSQL teardown gate.
            identity = None
        record["identity"] = identity
    return len(newly_registered)


def _bounded_phase_get(
    requests: Any,
    url: str,
    *,
    deadline: float,
    params: Any = None,
    monotonic: Any = time.monotonic,
) -> tuple[Any | None, bool]:
    """Bound a model-phase GET by remaining wall time and verify response arrival."""
    _cancellation_checkpoint()
    started = monotonic()
    remaining = deadline - started
    if not math.isfinite(remaining) or remaining <= 0:
        return None, False
    response = requests.get(
        url,
        params=params,
        timeout=min(10.0, remaining),
    )
    _cancellation_checkpoint()
    observed = monotonic()
    return response, math.isfinite(observed) and observed <= deadline


def _read_terminal(requests: Any, base_url: str, terminal_id: str) -> dict[str, Any]:
    response = requests.get(f"{base_url}/terminals/{terminal_id}", timeout=10)
    if response.status_code != 200:
        raise GateError("terminal_read_failed")
    body = response.json()
    if not isinstance(body, dict):
        raise GateError("terminal_read_shape_invalid")
    return body


def _read_terminal_before_deadline(
    requests: Any,
    base_url: str,
    terminal_id: str,
    *,
    deadline: float,
) -> tuple[dict[str, Any] | None, bool]:
    response, observed_in_time = _bounded_phase_get(
        requests,
        f"{base_url}/terminals/{terminal_id}",
        deadline=deadline,
    )
    if response is None or not observed_in_time:
        return None, False
    if response.status_code != 200:
        raise GateError("terminal_read_failed")
    body = response.json()
    if not isinstance(body, dict):
        raise GateError("terminal_read_shape_invalid")
    return body, True


_OUTPUT_MARKER_PATTERNS = {
    "date_stamp_seen": re.compile(
        r"\b20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?" r"(?:Z|[+-]\d{2}:?\d{2})?\b"
    ),
    # R9e: also recognize the literal provider phrase "Endpoint is unavailable".
    "endpoint_unavailable_seen": re.compile(r"endpoint(?:[\s_-]+is)?[\s_-]*unavailable", re.I),
    "retry_seen": re.compile(r"\bretry(?:ing)?\b", re.I),
}
_RANGE_STATUS_VALUES = frozenset(
    {
        "unknown",
        "idle",
        "processing",
        "completed",
        "waiting_user_answer",
        "waiting_quota",
        "reconcile",
        "error",
    }
)
# R9e: every text marker is derived from terminal text that may echo the phase
# prompt. The prompt itself contains "retry" and LAST_RECEIPT, so these flags
# are never evidence of a model retry, a receipt, or provider attribution.
_RANGE_MARKER_PROVENANCE = {
    "marker_provenance": "terminal_text_may_include_prompt_echo",
    "may_include_prompt_echo": True,
    "date_stamp_seen_is_current_turn_evidence": False,
    "endpoint_unavailable_seen_is_provider_attribution": False,
    "retry_seen_is_model_retry_evidence": False,
    "footer_seen_is_receipt_evidence": False,
}


def _label_range_sample_provenance(sample: dict[str, Any]) -> dict[str, Any]:
    """Add explicit prompt-echo labels; compatibility fields stay unchanged."""
    sample.update(_RANGE_MARKER_PROVENANCE)
    return sample


def _new_output_range_observer() -> dict[str, Any]:
    return {
        "next_offset": 0,
        "marker_carry": "",
        "cursor_advance_blocked": False,
        "date_stamp_seen": False,
        "endpoint_unavailable_seen": False,
        "retry_seen": False,
        "footer_seen": False,
    }


def _reset_output_range_markers(observer: dict[str, Any]) -> None:
    """Start phase-local marker flags while retaining the append-only byte cursor."""
    observer["marker_carry"] = ""
    observer["date_stamp_seen"] = False
    observer["endpoint_unavailable_seen"] = False
    observer["retry_seen"] = False
    observer["footer_seen"] = False


def _bounded_output_range_sample(
    requests: Any,
    base_url: str,
    terminal_id: str,
    observer: dict[str, Any],
    *,
    deadline: float,
    phase_started: float,
    request_length: int = OUTPUT_RANGE_CHUNK_BYTES,
    monotonic: Any = time.monotonic,
) -> tuple[dict[str, Any], bool]:
    """Observe a bounded range without persisting terminal output or gating on it.

    The endpoint echoes the requested length; it does not report bytes read.
    ``decoded_utf8_bytes`` therefore names the decoded/re-encoded text length
    and is diagnostic only. A range ending in U+FFFD is reread from the start
    of that possibly partial/literal code point. Internal replacement ambiguity
    freezes cursor advancement rather than guessing a byte offset.
    """
    started = monotonic()
    offset = observer.get("next_offset")
    if type(offset) is not int or offset < 0:
        offset = 0
        observer["next_offset"] = offset
    requested_length = (
        request_length
        if type(request_length) is int and 1 <= request_length <= OUTPUT_RANGE_MAX_BYTES
        else OUTPUT_RANGE_CHUNK_BYTES
    )
    sample: dict[str, Any] = {
        "observed_by_elapsed_seconds": round(started - phase_started, 6),
        "request_offset": offset,
        "request_length": requested_length,
        "response_offset": None,
        "response_length_echo": None,
        "decoded_utf8_bytes": None,
        "offset_advanced_bytes": 0,
        "next_offset": observer["next_offset"],
        "http_status": None,
        "request_completed": False,
        "observed_within_deadline": False,
        "response_shape_valid": False,
        "request_skipped_due_to_offset_ambiguity": False,
        "utf8_replacement_seen": False,
        "utf8_offset_ambiguous": False,
        "cursor_advance_blocked": bool(observer.get("cursor_advance_blocked")),
        "may_include_prior_output": True,
        "date_stamp_seen": bool(observer.get("date_stamp_seen")),
        "endpoint_unavailable_seen": bool(observer.get("endpoint_unavailable_seen")),
        "retry_seen": bool(observer.get("retry_seen")),
        "footer_seen": bool(observer.get("footer_seen")),
        "turn_sequence": None,
        "turn_completed": None,
        "turn_status": None,
        "turn_closed": None,
    }
    if observer.get("cursor_advance_blocked"):
        sample["cursor_advance_blocked"] = True
        sample["request_skipped_due_to_offset_ambiguity"] = True
        within = math.isfinite(started) and started <= deadline
        sample["observed_within_deadline"] = within
        return sample, within
    remaining = deadline - started
    if not math.isfinite(remaining) or remaining <= 0:
        return sample, False
    try:
        response = requests.get(
            f"{base_url}/terminals/{terminal_id}/output/range",
            params={"offset": offset, "length": requested_length},
            timeout=min(10.0, remaining),
        )
    except Exception:
        observed = monotonic()
        within = math.isfinite(observed) and observed <= deadline
        sample["observed_by_elapsed_seconds"] = round(observed - phase_started, 6)
        sample["observed_within_deadline"] = within
        return sample, within

    observed = monotonic()
    within = math.isfinite(observed) and observed <= deadline
    sample["request_completed"] = True
    sample["observed_by_elapsed_seconds"] = round(observed - phase_started, 6)
    sample["observed_within_deadline"] = within
    status = getattr(response, "status_code", None)
    if type(status) is int and 100 <= status <= 599:
        sample["http_status"] = status
    if not within or status != 200:
        return sample, within

    try:
        body = response.json()
    except Exception:
        return sample, within
    required_keys = {"terminal_id", "offset", "length", "data"}
    if (
        not isinstance(body, dict)
        or set(body) != required_keys
        or body.get("terminal_id") != terminal_id
        or type(body.get("offset")) is not int
        or body.get("offset") != offset
        or type(body.get("length")) is not int
        or body.get("length") != requested_length
        or not isinstance(body.get("data"), str)
    ):
        return sample, within
    data = body["data"]
    if len(data) > OUTPUT_RANGE_MAX_BYTES:
        return sample, within
    try:
        decoded_utf8_bytes = len(data.encode("utf-8"))
    except UnicodeEncodeError:
        return sample, within
    if decoded_utf8_bytes > 3 * OUTPUT_RANGE_MAX_BYTES:
        return sample, within
    sample["response_shape_valid"] = True
    sample["response_offset"] = offset
    sample["response_length_echo"] = requested_length
    sample["decoded_utf8_bytes"] = decoded_utf8_bytes
    replacement_count = data.count("\ufffd")
    sample["utf8_replacement_seen"] = replacement_count > 0

    if observer.get("cursor_advance_blocked"):
        sample["cursor_advance_blocked"] = True
        return sample, within
    if replacement_count == 1 and data.endswith("\ufffd"):
        # The final replacement may be a truncated code point or a literal
        # U+FFFD. Re-read from its first byte on the next poll.
        safe_text = data[:-1]
        advanced = len(safe_text.encode("utf-8"))
        sample["utf8_offset_ambiguous"] = True
    elif replacement_count:
        observer["cursor_advance_blocked"] = True
        sample["utf8_offset_ambiguous"] = True
        sample["cursor_advance_blocked"] = True
        return sample, within
    else:
        safe_text = data
        advanced = decoded_utf8_bytes

    observer["next_offset"] = offset + advanced
    sample["offset_advanced_bytes"] = advanced
    sample["next_offset"] = observer["next_offset"]
    window = str(observer.get("marker_carry", "")) + safe_text
    observer["date_stamp_seen"] = bool(
        observer.get("date_stamp_seen") or _OUTPUT_MARKER_PATTERNS["date_stamp_seen"].search(window)
    )
    observer["endpoint_unavailable_seen"] = bool(
        observer.get("endpoint_unavailable_seen")
        or _OUTPUT_MARKER_PATTERNS["endpoint_unavailable_seen"].search(window)
    )
    observer["retry_seen"] = bool(
        observer.get("retry_seen") or _OUTPUT_MARKER_PATTERNS["retry_seen"].search(window)
    )
    observer["footer_seen"] = bool(observer.get("footer_seen") or LAST_RECEIPT in window)
    observer["marker_carry"] = window[-256:]
    for marker in (
        "date_stamp_seen",
        "endpoint_unavailable_seen",
        "retry_seen",
        "footer_seen",
    ):
        sample[marker] = bool(observer[marker])
    sample["cursor_advance_blocked"] = False
    return sample, within


def _attach_range_turn_fields(
    sample: dict[str, Any], body: dict[str, Any] | None, *, role: str, expected_sequence: int
) -> None:
    """Attach allowlisted turn state observed later in the same poll."""
    if not isinstance(body, dict):
        return
    sequence = body.get("turn_sequence")
    completed = body.get("turn_completed")
    status = body.get("status")
    if type(sequence) is int:
        sample["turn_sequence"] = sequence
    if type(completed) is int:
        sample["turn_completed"] = completed
    if isinstance(status, str) and status in _RANGE_STATUS_VALUES:
        sample["turn_status"] = status
    if role == "sender":
        sample["turn_closed"] = bool(
            sample["turn_status"] == "completed"
            and type(sequence) is int
            and type(completed) is int
            and sequence == expected_sequence
            and completed == expected_sequence
        )
    else:
        sample["turn_closed"] = bool(
            sample["turn_status"] in {"idle", "completed"}
            and type(sequence) is int
            and type(completed) is int
            and sequence == expected_sequence
            and completed == expected_sequence
        )


def _output_range_controls() -> int:
    """Pure response-shape, UTF-8 boundary, marker, and deadline controls."""

    class FakeClock:
        def __init__(self, value: float = 0.0):
            self.value = value

        def monotonic(self) -> float:
            return self.value

    class FakeResponse:
        def __init__(self, status: int, body: Any = None, *, fail_json: bool = False):
            self.status_code = status
            self._body = body
            self._fail_json = fail_json

        def json(self) -> Any:
            if self._fail_json:
                raise AssertionError("unexpected_body_read")
            return self._body

    class ByteRangeRequests:
        def __init__(self, raw: bytes, clock: FakeClock, latency: float = 0.01):
            self.raw = raw
            self.clock = clock
            self.latency = latency
            self.calls: list[dict[str, Any]] = []

        def get(self, url: str, *, params: dict[str, Any], timeout: float) -> FakeResponse:
            if not url.endswith("/terminals/terminal-a/output/range"):
                raise AssertionError("range_url_mismatch")
            self.clock.value += self.latency
            offset = params["offset"]
            length = params["length"]
            self.calls.append(
                {"method": "GET", "offset": offset, "length": length, "timeout": timeout}
            )
            chunk = self.raw[offset : offset + length]
            return FakeResponse(
                200,
                {
                    "terminal_id": "terminal-a",
                    "offset": offset,
                    "length": length,
                    "data": chunk.decode("utf-8", errors="replace"),
                },
            )

    class QueuedRequests:
        def __init__(self, responses: list[FakeResponse], clock: FakeClock, latency: float):
            self.responses = responses
            self.clock = clock
            self.latency = latency
            self.calls: list[dict[str, Any]] = []

        def get(self, url: str, *, params: dict[str, Any], timeout: float) -> FakeResponse:
            if not url.endswith("/terminals/terminal-a/output/range"):
                raise AssertionError("range_url_mismatch")
            self.calls.append(
                {
                    "method": "GET",
                    "offset": params["offset"],
                    "length": params["length"],
                    "timeout": timeout,
                }
            )
            self.clock.value += self.latency
            return self.responses.pop(0)

    controls = 0
    raw = (
        "Validado ñ, 中文. LAST_RECEIPT EndpointUnavailable retry " "2026-10-21T12:34:56Z"
    ).encode("utf-8")
    clock = FakeClock()
    requests = ByteRangeRequests(raw, clock)
    observer = _new_output_range_observer()
    samples: list[dict[str, Any]] = []
    for _ in range(len(raw) + 2):
        sample, within = _bounded_output_range_sample(
            requests,
            "http://private.invalid",
            "terminal-a",
            observer,
            deadline=75.0,
            phase_started=0.0,
            request_length=10,
            monotonic=clock.monotonic,
        )
        samples.append(sample)
        if not within or sample["decoded_utf8_bytes"] == 0:
            break
    split_marker_control = (
        requests.calls
        and requests.calls[0]["offset"] == 0
        and requests.calls[0]["length"] == 10
        and requests.calls[1]["offset"] == 9
        and samples[1]["utf8_replacement_seen"] is False
        and any(sample["utf8_offset_ambiguous"] for sample in samples)
        and observer["next_offset"] == len(raw)
        and observer["footer_seen"] is True
        and observer["endpoint_unavailable_seen"] is True
        and observer["retry_seen"] is True
        and observer["date_stamp_seen"] is True
        and all("data" not in sample for sample in samples)
        and all(sample["may_include_prior_output"] is True for sample in samples)
        and all(call["method"] == "GET" and call["timeout"] > 0 for call in requests.calls)
    )
    controls += 1

    literal_clock = FakeClock()
    literal_requests = ByteRangeRequests("prefix\ufffd".encode("utf-8"), literal_clock)
    literal_observer = _new_output_range_observer()
    literal_first, literal_first_in_time = _bounded_output_range_sample(
        literal_requests,
        "http://private.invalid",
        "terminal-a",
        literal_observer,
        deadline=75.0,
        phase_started=0.0,
        request_length=9,
        monotonic=literal_clock.monotonic,
    )
    literal_second, literal_second_in_time = _bounded_output_range_sample(
        literal_requests,
        "http://private.invalid",
        "terminal-a",
        literal_observer,
        deadline=75.0,
        phase_started=0.0,
        request_length=9,
        monotonic=literal_clock.monotonic,
    )
    partial_clock = FakeClock()
    partial_requests = ByteRangeRequests("prefix ñsuffix".encode("utf-8"), partial_clock)
    partial_observer = _new_output_range_observer()
    partial_first, partial_first_in_time = _bounded_output_range_sample(
        partial_requests,
        "http://private.invalid",
        "terminal-a",
        partial_observer,
        deadline=75.0,
        phase_started=0.0,
        request_length=8,
        monotonic=partial_clock.monotonic,
    )
    partial_second, partial_second_in_time = _bounded_output_range_sample(
        partial_requests,
        "http://private.invalid",
        "terminal-a",
        partial_observer,
        deadline=75.0,
        phase_started=0.0,
        request_length=8,
        monotonic=partial_clock.monotonic,
    )
    trailing_ambiguity_control = (
        literal_first_in_time
        and literal_second_in_time
        and literal_first["utf8_offset_ambiguous"] is True
        and literal_observer["next_offset"] == 6
        and literal_requests.calls[1]["offset"] == 6
        and literal_second["utf8_offset_ambiguous"] is True
        and literal_second["offset_advanced_bytes"] == 0
        and partial_first_in_time
        and partial_second_in_time
        and partial_first["utf8_offset_ambiguous"] is True
        and partial_observer["next_offset"] == 15
        and partial_requests.calls[1]["offset"] == 7
        and partial_second["utf8_replacement_seen"] is False
    )
    controls += 1

    internal_clock = FakeClock()
    internal_requests = QueuedRequests(
        [
            FakeResponse(
                200, {"terminal_id": "terminal-a", "offset": 0, "length": 8, "data": "a\ufffdb"}
            )
        ],
        internal_clock,
        0.01,
    )
    internal_observer = _new_output_range_observer()
    internal_sample, internal_in_time = _bounded_output_range_sample(
        internal_requests,
        "http://private.invalid",
        "terminal-a",
        internal_observer,
        deadline=75.0,
        phase_started=0.0,
        request_length=8,
        monotonic=internal_clock.monotonic,
    )
    internal_ambiguity_control = (
        internal_in_time
        and internal_sample["utf8_offset_ambiguous"] is True
        and internal_sample["cursor_advance_blocked"] is True
        and internal_observer["next_offset"] == 0
    )
    internal_retry_sample, internal_retry_in_time = _bounded_output_range_sample(
        internal_requests,
        "http://private.invalid",
        "terminal-a",
        internal_observer,
        deadline=75.0,
        phase_started=0.0,
        request_length=8,
        monotonic=internal_clock.monotonic,
    )
    internal_ambiguity_control = bool(
        internal_ambiguity_control
        and internal_retry_in_time
        and internal_retry_sample["request_skipped_due_to_offset_ambiguity"] is True
        and len(internal_requests.calls) == 1
    )
    controls += 1

    http_clock = FakeClock()
    http_requests = QueuedRequests([FakeResponse(503, fail_json=True)], http_clock, 0.01)
    http_observer = _new_output_range_observer()
    http_sample, http_in_time = _bounded_output_range_sample(
        http_requests,
        "http://private.invalid",
        "terminal-a",
        http_observer,
        deadline=75.0,
        phase_started=0.0,
        request_length=8,
        monotonic=http_clock.monotonic,
    )
    http_error_control = (
        http_in_time
        and http_sample["http_status"] == 503
        and http_sample["response_shape_valid"] is False
        and "data" not in http_sample
    )
    controls += 1

    malformed_clock = FakeClock()
    malformed_requests = QueuedRequests(
        [FakeResponse(200, {"terminal_id": "terminal-a", "offset": 99, "length": 8, "data": "x"})],
        malformed_clock,
        0.01,
    )
    malformed_observer = _new_output_range_observer()
    malformed_sample, malformed_in_time = _bounded_output_range_sample(
        malformed_requests,
        "http://private.invalid",
        "terminal-a",
        malformed_observer,
        deadline=75.0,
        phase_started=0.0,
        request_length=8,
        monotonic=malformed_clock.monotonic,
    )
    malformed_shape_control = (
        malformed_in_time
        and malformed_sample["response_shape_valid"] is False
        and malformed_observer["next_offset"] == 0
    )
    controls += 1

    exact_clock = FakeClock(74.9)
    exact_requests = QueuedRequests([FakeResponse(503)], exact_clock, 0.1)
    exact_sample, exact_within = _bounded_output_range_sample(
        exact_requests,
        "http://private.invalid",
        "terminal-a",
        _new_output_range_observer(),
        deadline=75.0,
        phase_started=0.0,
        request_length=8,
        monotonic=exact_clock.monotonic,
    )
    exact_deadline_control = (
        exact_within is True
        and exact_sample["observed_within_deadline"] is True
        and exact_sample["observed_by_elapsed_seconds"] == 75.0
        and abs(exact_requests.calls[0]["timeout"] - 0.1) < 1e-9
    )
    controls += 1

    late_clock = FakeClock(74.9)
    late_requests = QueuedRequests([FakeResponse(200, fail_json=True)], late_clock, 0.1004)
    late_sample, late_within = _bounded_output_range_sample(
        late_requests,
        "http://private.invalid",
        "terminal-a",
        _new_output_range_observer(),
        deadline=75.0,
        phase_started=0.0,
        request_length=8,
        monotonic=late_clock.monotonic,
    )
    late_deadline_control = (
        late_within is False
        and late_sample["observed_within_deadline"] is False
        and late_sample["observed_by_elapsed_seconds"] == 75.0004
        and late_sample["response_shape_valid"] is False
    )
    controls += 1

    turn_sample: dict[str, Any] = {}
    _attach_range_turn_fields(
        turn_sample,
        {"turn_sequence": 4, "turn_completed": 4, "status": "completed"},
        role="receiver",
        expected_sequence=4,
    )
    turn_fields_control = turn_sample == {
        "turn_sequence": 4,
        "turn_completed": 4,
        "turn_status": "completed",
        "turn_closed": True,
    }
    controls += 1

    results = [
        split_marker_control,
        trailing_ambiguity_control,
        internal_ambiguity_control,
        http_error_control,
        malformed_shape_control,
        exact_deadline_control,
        late_deadline_control,
        turn_fields_control,
    ]
    if not all(results):
        raise AssertionError(
            "private_output_range_controls_failed:"
            + ",".join(str(index) for index, passed in enumerate(results) if not passed)
        )
    return controls


def _pg_rows(connect: Any, target: Any, receiver_id: str) -> list[dict[str, Any]]:
    with connect(target, readonly=True) as connection:
        rows = connection.execute(
            "SELECT id,sender_id,receiver_id,message,status,created_at "
            "FROM inbox WHERE receiver_id=%s ORDER BY created_at,id",
            (receiver_id,),
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def _api_rows(requests: Any, base_url: str, receiver_id: str) -> list[dict[str, Any]]:
    response = requests.get(
        f"{base_url}/terminals/{receiver_id}/inbox/messages",
        params={"limit": 10},
        timeout=10,
    )
    if response.status_code != 200:
        raise GateError("inbox_api_read_failed")
    rows = response.json()
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise GateError("inbox_api_shape_invalid")
    return rows


def _api_metadata_snapshot(
    requests: Any,
    base_url: str,
    terminals: dict[str, dict[str, Any]],
    group: list[str],
    case_id: Any = DEFAULT_CASE_ID,
) -> dict[str, dict[str, Any]]:
    return {
        role: _safe_metadata(
            _read_terminal(requests, base_url, data["id"]),
            role=role,
            expected_group=group,
            case_id=case_id,
        )
        for role, data in terminals.items()
    }


def _project_lifecycle_diagnostic(terminal):
    status = terminal.get("status")
    statuses = {
        "idle",
        "completed",
        "processing",
        "unknown",
        "error",
        "reconcile",
        "waiting_user_answer",
        "waiting_quota",
    }
    counters = {}
    for key in ("turn_sequence", "turn_completed"):
        value = terminal.get(key)
        valid = type(value) is int and 0 <= value <= 2147483647
        counters[key] = value if valid else None
        counters[key + "_valid"] = valid
    return {
        "status": status if type(status) is str and status in statuses else "other",
        **counters,
        "turn": _project_turn_diagnostic(200, terminal.get("turn")),
    }


def _verified_receipt_identity(row):
    if type(row) is not dict or row.get("phase") != "result_verified":
        raise GateError("restart_receipt_not_verified")
    values = []
    for name, size in (("generation", 32), ("receipt_sha256", 64), ("result_sha256", 64)):
        value = row.get(name)
        if type(value) is not str or re.fullmatch("[0-9a-f]{" + str(size) + "}", value) is None:
            raise GateError("restart_receipt_identity_invalid")
        values.append(value)
    return (*values, "result_verified")


def _durable_receipt_snapshot(connect, target, terminals):
    if set(terminals) != {"sender", "receiver"}:
        raise GateError("restart_receipt_roles_invalid")
    result = {}
    with connect(target, readonly=True) as connection:
        for role in ("sender", "receiver"):
            row = connection.execute(
                "SELECT generation,receipt_sha256,result_sha256,phase "
                "FROM terminal_turn_receipts WHERE terminal_id=%s",
                (terminals[role]["id"],),
            ).fetchone()
            result[role] = _verified_receipt_identity(
                {key: row[key] for key in row.keys()} if row is not None else None
            )
    return result


def _wait_restart_readiness(
    requests, base_url, terminals, diagnostic, *, monotonic=time.monotonic, sleep=time.sleep
):
    deadline = monotonic() + MODEL_PHASE_SECONDS
    for role in ("receiver", "sender"):
        consecutive = 0
        previous_counters = None
        while monotonic() < deadline:
            _cancellation_checkpoint()
            response, on_time = _bounded_phase_get(
                requests,
                f"{base_url}/terminals/{terminals[role]['id']}",
                deadline=deadline,
                monotonic=monotonic,
            )
            if not on_time:
                raise GateError("terminal_restart_readiness_timeout")
            if response.status_code != 200:
                raise GateError("terminal_restart_readiness_read_failed")
            try:
                body = response.json()
            except ValueError:
                raise GateError("terminal_restart_readiness_shape_invalid") from None
            if monotonic() >= deadline:
                raise GateError("terminal_restart_readiness_timeout")
            if type(body) is not dict:
                raise GateError("terminal_restart_readiness_shape_invalid")
            item = _project_lifecycle_diagnostic(body)
            diagnostic[role] = item
            if (
                item["status"] in {"error", "reconcile", "other"}
                or not all(item[key + "_valid"] for key in ("turn_sequence", "turn_completed"))
                or item["turn"]["projection_status"] != "valid"
                or item["turn"]["state"] in {"reconcile", "cancelling", "cancelled"}
            ):
                raise GateError("terminal_restart_readiness_invalid")
            counters = (item["turn_sequence"], item["turn_completed"])
            settled = (
                item["status"] in {"idle", "completed"}
                and counters[0] == counters[1]
                and item["turn"]["state"] == "verified"
                and item["turn"]["attempts"] == 0
                and item["turn"]["generation_present"] is True
            )
            consecutive = (
                (consecutive + 1 if previous_counters == counters else 1) if settled else 0
            )
            previous_counters = counters if settled else None
            item["ready_samples"] = consecutive
            if consecutive == 3:
                break
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise GateError("terminal_restart_readiness_timeout")
            sleep(min(0.4, remaining))
        else:
            raise GateError("terminal_restart_readiness_timeout")
    return True


def _api_lifecycle_snapshot(
    requests: Any,
    base_url: str,
    terminals: dict[str, dict[str, Any]],
    *,
    diagnostic: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    result = {}
    for role, data in terminals.items():
        terminal = _read_terminal(requests, base_url, data["id"])
        try:
            if type(diagnostic) is dict and role in {"sender", "receiver"}:
                diagnostic[role] = _project_lifecycle_diagnostic(terminal)
        except Exception:
            # Diagnostics cannot replace or bypass the original predicate.
            pass
        sequence = terminal.get("turn_sequence")
        completed = terminal.get("turn_completed")
        if (
            terminal.get("status") not in {"idle", "completed"}
            or type(sequence) is not int
            or type(completed) is not int
            or sequence < 0
            or completed < 0
            or completed != sequence
        ):
            raise GateError("terminal_lifecycle_not_durable")
        result[role] = {
            "turn_sequence": sequence,
            "turn_completed": completed,
        }
    return result


def _native_config_roots(roots: list[Path]) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    for path in roots:
        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or path.is_symlink()
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise GateError("provider_root_identity_invalid")
        result[path.name] = (info.st_dev, info.st_ino)
    return result


def _model_catalog_is_eligible(result: Any) -> bool:
    return isinstance(result, dict) and result.get("eligible") is True


def _model_catalog_preflight(model: str) -> dict[str, Any]:
    from test.live_provider import opencode_model_catalog

    try:
        result = opencode_model_catalog.check_model(model)
    except Exception:
        raise GateError("model_catalog_unavailable") from None
    if not _model_catalog_is_eligible(result):
        raise GateError("model_catalog_rejected")
    return result


_T104_STATES = frozenset(
    {"none", "pending", "verifying", "reconcile", "verified", "cancelling", "cancelled"}
)
_T104_REASONS = frozenset({"receipt_result_unverifiable"})
_T104_LOG_LIMIT = 1024 * 1024
_T104_PANE_LIMIT = 262144


def _project_turn_diagnostic(status, payload):
    code = status if type(status) is int and 100 <= status <= 599 else None
    result = {
        "http_status": code,
        "projection_status": "http_error" if code != 200 else "invalid",
        "state": "other",
        "reason": "none",
        "attempts": None,
        "generation_present": False,
    }
    if code != 200 or type(payload) is not dict:
        return result
    state = payload.get("state")
    reason = payload.get("reason")
    attempts = payload.get("attempts")
    generation = payload.get("generation")
    result["state"] = state if type(state) is str and state in _T104_STATES else "other"
    result["reason"] = (
        "none"
        if reason is None
        else reason if type(reason) is str and reason in _T104_REASONS else "other"
    )
    result["attempts"] = attempts if type(attempts) is int and 0 <= attempts <= 2147483647 else None
    result["generation_present"] = generation is not None
    valid = (
        type(state) is str
        and state in _T104_STATES
        and (reason is None or (type(reason) is str and reason in _T104_REASONS))
        and type(attempts) is int
        and 0 <= attempts <= 2147483647
        and (
            generation is None
            or (type(generation) is str and re.fullmatch(r"[0-9a-f]{32}", generation) is not None)
        )
    )
    result["projection_status"] = "valid" if valid else "invalid"
    return result


def _receiver_log_relative_path(receiver_id):
    return (
        Path("logs") / "terminal" / f"{receiver_id}.log"
        if _safe_terminal_identity(receiver_id)
        else None
    )


def _receiver_pane_id_from_listing(listing, expected_session, expected_window):
    if (
        type(listing) is not bytes
        or len(listing) > 65536
        or type(expected_session) is not str
        or type(expected_window) is not str
    ):
        return None
    try:
        lines = listing.decode("utf-8", "strict").splitlines()
    except UnicodeError:
        return None
    matches = []
    for line in lines:
        fields = line.split("\t")
        if len(fields) != 5 or fields[1] != expected_session or fields[2] != expected_window:
            continue
        pane, _, _, w, h = fields
        if (
            re.fullmatch(r"%[0-9]+", pane) is None
            or not w.isdigit()
            or not h.isdigit()
            or not 1 <= int(w) <= 500
            or not 1 <= int(h) <= 200
        ):
            return None
        matches.append(pane)
    return matches[0] if len(matches) == 1 else None


def _copy_receiver_log(cao_home, receiver_id, destination):
    result = {
        "status": "unavailable",
        "file_size_bytes": None,
        "copied_bytes": 0,
        "prefix_truncated": False,
        "artifact": "receiver-terminal-log.bin",
    }
    relative = _receiver_log_relative_path(receiver_id)
    if relative is None:
        result["status"] = "invalid_identity"
        return result
    base = Path(cao_home)
    target = base / relative
    try:
        for directory in (base, base / "logs", base / "logs" / "terminal"):
            info = directory.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or directory.is_symlink()
                or info.st_uid != os.getuid()
            ):
                result["status"] = "unsafe_directory"
                return result
        fd = os.open(
            target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                result["status"] = "unsafe_file"
                return result
            size = info.st_size
            start = max(0, size - _T104_LOG_LIMIT)
            left = min(size, _T104_LOG_LIMIT)
            offset = start
            chunks = []
            while left:
                chunk = os.pread(fd, min(left, 65536), offset)
                if not chunk:
                    result["status"] = "short_read"
                    return result
                chunks.append(chunk)
                offset += len(chunk)
                left -= len(chunk)
        finally:
            os.close(fd)
        data = b"".join(chunks)
        _write_private(Path(destination), data)
        result.update(
            status="captured",
            file_size_bytes=size,
            copied_bytes=len(data),
            prefix_truncated=start > 0,
        )
    except FileNotFoundError:
        result["status"] = "not_found"
    except (OSError, GateError):
        result["status"] = "io_error"
    return result


def _capture_receiver_panes(
    journey, socket_path, socket_id, sid, session, window, raw_dir, deadline
):
    result = {
        "status": "unavailable",
        "history_bytes": 0,
        "viewport_bytes": 0,
        "history_artifact": "receiver-tmux-history.private",
        "viewport_artifact": "receiver-tmux-viewport.private",
    }
    try:
        if (
            journey._capture_private_tmux_socket_identity(socket_path) != socket_id
            or journey._find_tmux_server_identity(socket_path) != sid
        ):
            result["status"] = "socket_identity_mismatch"
            return result
        base = ["tmux", "-S", str(socket_path)]
        env = dict(os.environ)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            result["status"] = "deadline"
            return result
        listing = subprocess.run(
            base
            + [
                "list-panes",
                "-a",
                "-F",
                chr(9).join(
                    [
                        "#{pane_id}",
                        "#{session_name}",
                        "#{window_name}",
                        "#{pane_width}",
                        "#{pane_height}",
                    ]
                ),
            ],
            capture_output=True,
            timeout=min(2, remaining),
            check=False,
            env=env,
        )
        pane = _receiver_pane_id_from_listing(listing.stdout, session, window)
        if listing.returncode or pane is None:
            result["status"] = "receiver_pane_unresolved"
            return result
        for start, artifact, key in (
            ("-128", "receiver-tmux-history.private", "history_bytes"),
            ("0", "receiver-tmux-viewport.private", "viewport_bytes"),
        ):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result["status"] = "deadline"
                return result
            captured = subprocess.run(
                base + ["capture-pane", "-p", "-e", "-S", start, "-t", pane],
                capture_output=True,
                timeout=min(2, remaining),
                check=False,
                env=env,
            )
            if captured.returncode or len(captured.stdout) > _T104_PANE_LIMIT:
                result["status"] = "capture_failed_or_oversized"
                return result
            _write_private(Path(raw_dir) / artifact, captured.stdout)
            result[key] = len(captured.stdout)
        result["status"] = "captured"
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
    except (OSError, ValueError, TypeError, AttributeError):
        result["status"] = "capture_error"
    return result


def _read_private_turn_receipt(target, receiver_id, destination, deadline):
    result = {
        "status": "unavailable",
        "row_present": False,
        "generation_present": False,
        "receipt_sha256_present": False,
        "generation_matches_turn": None,
    }
    if not _safe_terminal_identity(receiver_id) or time.monotonic() >= deadline:
        result["status"] = "deadline_or_invalid_identity"
        return result
    try:
        import psycopg
        from psycopg import sql

        schema = getattr(target, "schema", None)
        if type(schema) is not str or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema) is None:
            result["status"] = "schema_invalid"
            return result
        params = target.connection_parameters()
        params["connect_timeout"] = 1
        params["options"] = (
            "-c statement_timeout=500 -c lock_timeout=500 -c default_transaction_read_only=on"
        )
        params["application_name"] = "t104-v2-readonly-diagnostic"
        if time.monotonic() >= deadline:
            result["status"] = "deadline"
            return result
        with psycopg.connect(**params, autocommit=True) as connection:
            with connection.cursor() as cursor:
                query = sql.SQL(
                    "SELECT generation, receipt_sha256, phase FROM {}.{} WHERE terminal_id = %s"
                ).format(sql.Identifier(schema), sql.Identifier("terminal_turn_receipts"))
                cursor.execute(query, (receiver_id,))
                row = cursor.fetchone()
        if row is None:
            result["status"] = "no_row"
            return result
        if not isinstance(row, (tuple, list)) or len(row) != 3:
            result["status"] = "malformed_row"
            return result
        generation, digest, phase = row
        if (
            type(generation) is not str
            or re.fullmatch(r"[0-9a-f]{32}", generation) is None
            or type(digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or type(phase) is not str
            or phase not in {"prepared", "sent", "result_verified"}
        ):
            result["status"] = "malformed_row"
            return result
        _write_private(
            destination,
            json.dumps(
                {"generation": generation, "receipt_sha256": digest, "phase": phase}, sort_keys=True
            )
            + "\n",
        )
        result.update(
            status="captured",
            row_present=True,
            generation_present=True,
            receipt_sha256_present=True,
            phase=phase,
        )
    except BaseException:
        result["status"] = "query_failed"
    return result


def _sender_last_observation(
    http_status, body, terminal, sequence, elapsed_seconds, *, body_parse_failed=False
):
    """Project existing LAST/state reads without changing their acceptance input."""
    text = body.get("output") if http_status == 200 and type(body) is dict else None
    try:
        size = len(text.encode("utf-8")) if type(text) is str else None
    except UnicodeError:
        size = None
    valid_text = type(size) is int and size <= OUTPUT_RANGE_MAX_BYTES
    state = terminal if type(terminal) is dict else {}
    status = state.get("status")
    status = (
        status
        if type(status) is str and status in {"idle", "completed", "processing", "error", "unknown"}
        else "other"
    )
    turn_sequence = state.get("turn_sequence")
    turn_completed = state.get("turn_completed")
    closed = (
        status == "completed"
        and type(sequence) is int
        and 0 < sequence <= 2**63 - 1
        and type(turn_sequence) is int
        and type(turn_completed) is int
        and turn_sequence == turn_completed == sequence
    )
    return {
        "http_status": (
            http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        ),
        "output_body_state": (
            "invalid_json"
            if body_parse_failed is True
            else (
                "mapping"
                if type(body) is dict and http_status == 200
                else "non_mapping" if http_status == 200 else "not_read"
            )
        ),
        "output_string_observed": valid_text,
        "output_utf8_bytes": size if valid_text else None,
        "output_lines": len(text.splitlines()) if valid_text else None,
        "literal_unicode_present": valid_text and LAST_RECEIPT in text,
        "whitespace_joined_unicode_present": valid_text and LAST_RECEIPT in " ".join(text.split()),
        "terminal_status": status,
        "terminal_closed_for_input": closed,
        "observed_by_elapsed_seconds": round(max(0.0, elapsed_seconds), 3),
        "acceptance_input_changed": False,
    }


def _record_sender_last_observation(phase, observation):
    if len(phase["sender_last_observations"]) < 256:
        phase["sender_last_observations"].append(observation)
    else:
        phase["sender_last_observations_truncated"] = True


def _sender_last_body(response, phase, sequence, elapsed_seconds):
    if response.status_code != 200:
        return None
    try:
        return response.json()
    except Exception:
        _record_sender_last_observation(
            phase,
            _sender_last_observation(
                response.status_code,
                None,
                None,
                sequence,
                elapsed_seconds,
                body_parse_failed=True,
            ),
        )
        raise


def _model_phase_failure_code(phase):
    if (
        phase.get("sender_model_within_75_seconds") is False
        and phase.get("sender_terminal_closed_within_75_seconds") is True
        and phase.get("last_receipt_unicode_verified") is False
    ):
        return "sender_last_unicode_not_verified"
    return "model_phase_exceeded_75_seconds"


def _receiver_phase_failure_code(phase):
    if phase.get("receiver_turn_completed_once") is not True:
        return "receiver_turn_not_completed_once"
    if phase.get("receiver_ack_exact") is True:
        return None
    observation = phase.get("receiver_last_observation")
    if (
        phase.get("receiver_ack_exact") is False
        and type(observation) is dict
        and observation.get("http_status") == 200
        and observation.get("body_state") == "mapping"
        and observation.get("output_string_observed") is True
        and observation.get("observed_within_deadline") is True
    ):
        return "receiver_ack_not_exact"
    return "receiver_ack_not_observed"


def _receiver_output_failure_snapshot_unavailable(reason):
    return {"status": "unavailable", "reason": reason}


def _capture_receiver_output_failure_snapshot(*, raw_dir, phase_ordinal, failure_code, body):
    if type(failure_code) is not str or failure_code not in {
        "receiver_turn_not_completed_once",
        "receiver_ack_not_exact",
        "receiver_ack_not_observed",
    }:
        return _receiver_output_failure_snapshot_unavailable("failure_code_invalid")
    if type(phase_ordinal) is not int or phase_ordinal not in {1, 2}:
        return _receiver_output_failure_snapshot_unavailable("phase_invalid")
    if type(body) is not dict or type(body.get("output")) is not str:
        return _receiver_output_failure_snapshot_unavailable("output_unavailable")

    output = body["output"]
    try:
        output_bytes = output.encode("utf-8", errors="strict")
    except UnicodeError:
        return _receiver_output_failure_snapshot_unavailable("output_not_utf8")
    if len(output_bytes) > OUTPUT_RANGE_MAX_BYTES:
        return _receiver_output_failure_snapshot_unavailable("output_too_large")
    try:
        serialized = (
            json.dumps(
                {"output": output},
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError):
        return _receiver_output_failure_snapshot_unavailable("output_not_serializable")
    if len(serialized) > OUTPUT_RANGE_MAX_BYTES + 32:
        return _receiver_output_failure_snapshot_unavailable("serialized_output_too_large")

    expected_raw_dir = EVIDENCE / "raw"
    if (
        not isinstance(raw_dir, Path)
        or not raw_dir.is_absolute()
        or raw_dir != expected_raw_dir
        or not EVIDENCE.is_absolute()
    ):
        return _receiver_output_failure_snapshot_unavailable("snapshot_context_invalid")
    try:
        for directory in (EVIDENCE, raw_dir):
            metadata = directory.lstat()
            if (
                directory.is_symlink()
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
                or directory.resolve(strict=True) != directory
            ):
                return _receiver_output_failure_snapshot_unavailable("snapshot_context_invalid")
    except (OSError, RuntimeError, ValueError):
        return _receiver_output_failure_snapshot_unavailable("snapshot_context_invalid")

    destination = raw_dir / f"phase-{phase_ordinal}-receiver-output-failure.json"
    try:
        _write_private(destination, serialized)
    except FileExistsError:
        return _receiver_output_failure_snapshot_unavailable("snapshot_target_exists")
    except Exception:
        return _receiver_output_failure_snapshot_unavailable("snapshot_write_failed")
    copied = _read_owned_private_file(destination, maximum=OUTPUT_RANGE_MAX_BYTES + 32)
    if copied != serialized:
        return _receiver_output_failure_snapshot_unavailable("snapshot_verification_failed")
    return {"status": "captured", "reason": "none"}


def _capture_receiver_output_failure_snapshot_safely(**kwargs):
    try:
        return _capture_receiver_output_failure_snapshot(**kwargs)
    except BaseException:
        return _receiver_output_failure_snapshot_unavailable("snapshot_error")


def _raise_receiver_phase_failure_if_needed(*, phase, failure_code, raw_dir, phase_ordinal, body):
    if failure_code is None:
        return
    phase["receiver_output_failure_snapshot"] = _capture_receiver_output_failure_snapshot_safely(
        raw_dir=raw_dir,
        phase_ordinal=phase_ordinal,
        failure_code=failure_code,
        body=body,
    )
    raise GateError(failure_code)


def _load_failure_diagnostic_fixture(filename):
    if filename not in {"native_v2_text_probe.py", "native_phase_readers.py"}:
        raise GateError("failure_diagnostic_source_binding_invalid")
    relative = "specs/025-provider-mcp-profiles/fixtures/" + filename
    path = REPO / relative
    expected = (RUN_MANIFEST or {}).get("source_sha256s", {}).get(relative)
    if not _digest_shape(expected) or path.is_symlink() or _sha256(path) != expected:
        raise GateError("failure_diagnostic_source_binding_invalid")
    module_name = "spec025_t111_" + path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise GateError("failure_diagnostic_source_binding_invalid")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    if _sha256(path) != expected:
        raise GateError("failure_diagnostic_source_binding_invalid")
    return module


def _capture_sender_message_argument_diagnostic(
    *,
    observer_source_path,
    observation_path,
    raw_dir,
    expected_sender_id,
    expected_nonce,
    case_id,
    writers_dead,
):
    unavailable = {
        "schema": "spec025.t122.send-message-argument-diagnostic.v1",
        "status": "unavailable",
        "reason": "context_unavailable",
        "expected_phase_count": len(PHASES),
        "observed_count": 0,
        "observations": [],
        "two_phase_arguments_exact": False,
        "acceptance_input_changed": False,
    }
    if writers_dead is not True:
        unavailable["reason"] = "writers_not_confirmed_dead"
        return unavailable
    if (
        not isinstance(observer_source_path, Path)
        or not isinstance(observation_path, Path)
        or not isinstance(raw_dir, Path)
        or type(expected_nonce) is not str
        or re.fullmatch(r"[0-9a-f]{32}", expected_nonce) is None
    ):
        return unavailable
    try:
        case = _case_definition(case_id)
    except (GateError, TypeError, ValueError):
        return unavailable
    expected_raw_dir = EVIDENCE / "raw"
    expected_observer_dir = expected_raw_dir / "observer"
    expected_observation_path = (
        expected_observer_dir / f"native-events-{expected_sender_id}"
        if _safe_terminal_identity(expected_sender_id)
        else None
    )
    if (
        not isinstance(raw_dir, Path)
        or not isinstance(observation_path, Path)
        or not expected_raw_dir.is_absolute()
        or raw_dir != expected_raw_dir
        or expected_observation_path is None
        or observation_path != expected_observation_path
    ):
        unavailable["reason"] = "observation_path_binding_invalid"
        return unavailable
    try:
        for directory in (EVIDENCE, expected_raw_dir, expected_observer_dir):
            metadata = directory.lstat()
            if (
                directory.is_symlink()
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
                or directory.resolve(strict=True) != directory
            ):
                unavailable["reason"] = "observation_path_binding_invalid"
                return unavailable
    except (OSError, RuntimeError):
        unavailable["reason"] = "observation_path_binding_invalid"
        return unavailable
    expected_bodies = tuple(message for _phase_name, message in PHASES)
    relative = "test/live_provider/opencode_mcp_observer.py"
    expected_source_sha256 = (RUN_MANIFEST or {}).get("source_sha256s", {}).get(relative)
    try:
        source_info = observer_source_path.lstat()
        raw_info = raw_dir.lstat()
        if (
            observer_source_path.is_symlink()
            or not stat.S_ISREG(source_info.st_mode)
            or source_info.st_size > 256 * 1024
            or not _digest_shape(expected_source_sha256)
            or _sha256(observer_source_path) != expected_source_sha256
            or raw_dir.is_symlink()
            or not stat.S_ISDIR(raw_info.st_mode)
            or raw_info.st_uid != os.getuid()
            or stat.S_IMODE(raw_info.st_mode) != 0o700
        ):
            unavailable["reason"] = "source_or_raw_binding_invalid"
            return unavailable
    except OSError:
        unavailable["reason"] = "source_or_raw_binding_invalid"
        return unavailable
    spec = importlib.util.spec_from_file_location("spec025_t122_mcp_observer", observer_source_path)
    if spec is None or spec.loader is None:
        unavailable["reason"] = "observer_reader_unavailable"
        return unavailable
    observer_module = importlib.util.module_from_spec(spec)
    try:
        source_bytes = observer_source_path.read_bytes()
        if hashlib.sha256(source_bytes).hexdigest() != expected_source_sha256:
            unavailable["reason"] = "source_or_raw_binding_invalid"
            return unavailable
        exec(compile(source_bytes, str(observer_source_path), "exec"), observer_module.__dict__)
        source_after = observer_source_path.lstat()
        if (
            observer_source_path.is_symlink()
            or (source_info.st_dev, source_info.st_ino)
            != (source_after.st_dev, source_after.st_ino)
            or _sha256(observer_source_path) != expected_source_sha256
        ):
            unavailable["reason"] = "source_or_raw_binding_invalid"
            return unavailable
        observer_module.INTERACTION_TOOLS = frozenset(case.sender_catalog)
        trace = _read_owned_private_file(
            observation_path,
            maximum=observer_module._MAX_BYTES,
        )
        marker_path = observation_path.with_name(observation_path.name + ".complete")
        marker = _read_owned_private_file(marker_path, maximum=128)
        if marker != observer_module._COMPLETE_CONTENT:
            unavailable["reason"] = "observer_trace_unsealed_or_invalid"
            return unavailable
        copied_trace_path = raw_dir / "sender-message-argument-observer.copy.jsonl"
        _write_private(copied_trace_path, trace)
        copied_marker_path = copied_trace_path.with_name(copied_trace_path.name + ".complete")
        _write_private(copied_marker_path, marker)
        if (
            _read_owned_private_file(copied_trace_path, maximum=observer_module._MAX_BYTES) != trace
            or _read_owned_private_file(copied_marker_path, maximum=128) != marker
        ):
            unavailable["reason"] = "observer_trace_copy_mismatch"
            return unavailable
        summary = observer_module.read_protocol_observations(
            copied_trace_path,
            expected_nonce=expected_nonce,
            expected_message_bodies=expected_bodies,
            **_case_observer_options(case.case_id),
        )
    except Exception:
        unavailable["reason"] = "observer_trace_copy_or_read_failed"
        return unavailable
    if summary.get("observer_status") != "observed" or summary.get(
        "send_message_argument_observation_status"
    ) not in {
        "observed_prefix",
        "unobserved",
    }:
        unavailable["reason"] = "observer_trace_unsealed_or_invalid"
        return unavailable
    observations = summary.get("send_message_argument_observations")
    if type(observations) is not list or len(observations) > 256:
        unavailable["reason"] = "observer_trace_unsealed_or_invalid"
        return unavailable
    projected = []
    for item in observations:
        if (
            type(item) is not dict
            or type(item.get("ordinal")) is not int
            or type(item.get("status")) is not str
            or type(item.get("exact")) is not bool
            or (item.get("utf8_length") is not None and type(item.get("utf8_length")) is not int)
        ):
            unavailable["reason"] = "observer_trace_unsealed_or_invalid"
            return unavailable
        projected.append(
            {
                "ordinal": item["ordinal"],
                "status": item["status"],
                "exact": item["exact"],
                "utf8_length": item["utf8_length"],
            }
        )
    return {
        "schema": "spec025.t122.send-message-argument-diagnostic.v1",
        "status": (
            "unobserved"
            if not projected
            else (
                "observed_prefix"
                if len(projected) < len(PHASES)
                else "observed_two_phase_arguments"
            )
        ),
        "reason": "none",
        "expected_phase_count": len(PHASES),
        "observed_count": len(projected),
        "observations": projected,
        "two_phase_arguments_exact": summary.get("send_message_argument_two_phase_exact") is True,
        "sealed_raw_copy_verified": True,
        "acceptance_input_changed": False,
    }


def _capture_sender_message_argument_diagnostic_safely(**kwargs):
    try:
        return _capture_sender_message_argument_diagnostic(**kwargs)
    except BaseException:
        return {
            "schema": "spec025.t122.send-message-argument-diagnostic.v1",
            "status": "diagnostic_failed_closed",
            "reason": "diagnostic_capture_failed",
            "expected_phase_count": len(PHASES),
            "observed_count": 0,
            "observations": [],
            "two_phase_arguments_exact": False,
            "acceptance_input_changed": False,
        }


def _capture_sender_native_failure_diagnostic(context, deadline):
    phase = context.get("phase")
    if (
        type(phase) is not dict
        or phase.get("sender_terminal_closed_within_75_seconds") is not True
        or phase.get("last_receipt_unicode_verified") is not False
    ):
        return {"status": "not_applicable", "acceptance_input_changed": False}
    if time.monotonic() >= deadline:
        return {"status": "deadline", "acceptance_input_changed": False}
    return _capture_role_native_text(
        context,
        role="sender",
        viewport=context.get("last_viewport", ""),
        rendered=context.get("last_text", ""),
        expected_marker=LAST_RECEIPT,
        deadline=deadline,
    )


def _capture_role_native_text(context, *, role, viewport, rendered, expected_marker, deadline):
    if time.monotonic() >= deadline:
        return {"status": "deadline", "acceptance_input_changed": False}
    if role not in {"sender", "receiver"}:
        return {"status": "context_unavailable", "acceptance_input_changed": False}
    terminal = context.get("terminals", {}).get(role, {})
    server = context.get("server")
    if (
        not _safe_terminal_identity(terminal.get("id"))
        or server is None
        or PINNED_PYTHON is None
        or context.get("root") is None
        or context.get("cao_home") is None
    ):
        return {"status": "context_unavailable", "acceptance_input_changed": False}
    probe = _load_failure_diagnostic_fixture("native_v2_text_probe.py")
    readers = _load_failure_diagnostic_fixture("native_phase_readers.py")
    return probe.observe_bounded(
        python=str(PINNED_PYTHON),
        source_root=str(REPO),
        run_root=context["root"],
        cao_home=context["cao_home"],
        terminal_id=terminal["id"],
        database_config_file=server.database_config_path,
        viewport=viewport,
        rendered=rendered,
        expected_agent=(
            f"{_case_definition(context.get('case_id', DEFAULT_CASE_ID)).profile_prefix}-{role}"
        ),
        expected_marker=expected_marker,
        deadline=deadline,
        capture=readers.capture_bytes,
    )


def _capture_receiver_native_failure_diagnostic(context, pane_observation, deadline):
    phase = context.get("phase")
    if (
        type(phase) is not dict
        or phase.get("receiver_turn_completed_once") is not True
        or phase.get("receiver_ack_exact") is True
    ):
        return {"status": "not_applicable", "acceptance_input_changed": False}
    if time.monotonic() >= deadline:
        return {"status": "deadline", "acceptance_input_changed": False}
    if type(pane_observation) is not dict or pane_observation.get("status") != "captured":
        return {"status": "viewport_unavailable", "acceptance_input_changed": False}
    raw_dir = context.get("raw_dir")
    if raw_dir is None:
        return {"status": "viewport_unavailable", "acceptance_input_changed": False}
    try:
        viewport = _read_owned_private_file(
            Path(raw_dir) / "receiver-tmux-viewport.private", maximum=65536
        ).decode("utf-8", errors="strict")
    except (OSError, ValueError, UnicodeError, GateError):
        return {"status": "viewport_unavailable", "acceptance_input_changed": False}
    return _capture_role_native_text(
        context,
        role="receiver",
        viewport=viewport,
        rendered=context.get("receiver_output_text", ""),
        expected_marker="ACK",
        deadline=deadline,
    )


def _receiver_last_observation(http_status, body, in_time, *, parse_failed=False):
    text = body.get("output") if type(body) is dict and http_status == 200 else None
    try:
        size = len(text.encode("utf-8")) if type(text) is str else None
    except UnicodeError:
        size = None
    valid_text = type(size) is int and size <= OUTPUT_RANGE_MAX_BYTES
    return {
        "http_status": (
            http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        ),
        "body_state": (
            "invalid_json"
            if parse_failed is True
            else (
                "mapping"
                if type(body) is dict and http_status == 200
                else "non_mapping" if http_status == 200 else "not_read"
            )
        ),
        "output_utf8_bytes": size if valid_text else None,
        "output_lines": len(text.splitlines()) if valid_text else None,
        "output_string_observed": valid_text,
        "public_ack_exact": in_time is True and valid_text and _receiver_ack_exact(text),
        "observed_within_deadline": in_time is True,
        "acceptance_input_changed": False,
    }


def _receiver_current_receipt_verified(result):
    row = result.get("receipt_row")
    turn = result.get("turn")
    return (
        type(row) is dict
        and row.get("status") == "captured"
        and row.get("phase") == "result_verified"
        and row.get("generation_matches_turn") is True
        and type(turn) is dict
        and turn.get("projection_status") == "valid"
        and turn.get("state") == "verified"
    )


def _receiver_native_current_turn_bound(result):
    native = result.get("native_text")
    return (
        _receiver_current_receipt_verified(result)
        and type(native) is dict
        and native.get("status") == "observed"
        and native.get("target_kind") == "ack"
    )


def _capture_receiver_failure_diagnostic(
    receiver,
    server,
    requests_module,
    journey,
    socket_path,
    socket_id,
    sid,
    session_name,
    cao_home,
    raw_dir,
    database_target,
    shared_deadline=None,
    native_text_context=None,
):
    started = time.monotonic()
    deadline = started + 9.0
    if shared_deadline is not None:
        deadline = min(deadline, shared_deadline)
    result = {
        "schema": "spec025.t104.receiver-failure-diagnostic.v1",
        "status": "partial",
        "elapsed_ms": 0,
        "within_10_seconds": False,
        "terminal_log": {"status": "not_attempted"},
        "tmux": {"status": "not_attempted"},
        "turn": _project_turn_diagnostic(None, None),
        "receipt_row": {"status": "not_attempted", "generation_matches_turn": None},
        "native_text_current_turn_bound": False,
    }
    if started >= deadline:
        result["status"] = "deadline_exceeded"
        result["within_shared_deadline"] = False
        result["within_10_seconds"] = True
        return result
    receiver_id = receiver.get("id") if type(receiver) is dict else None
    window = receiver.get("name") if type(receiver) is dict else None
    session = receiver.get("session_name") if type(receiver) is dict else session_name
    if not _safe_terminal_identity(receiver_id):
        result["status"] = "receiver_identity_unavailable"
    else:
        if time.monotonic() < deadline:
            result["terminal_log"] = _copy_receiver_log(
                cao_home, receiver_id, Path(raw_dir) / "receiver-terminal-log.bin"
            )
        if time.monotonic() < deadline:
            result["tmux"] = _capture_receiver_panes(
                journey, socket_path, socket_id, sid, session, window, raw_dir, deadline
            )
        if time.monotonic() < deadline and server is not None and requests_module is not None:
            try:
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    response = requests_module.get(
                        f"{server.url}/terminals/{receiver_id}/turn",
                        timeout=(min(0.4, remaining / 3), min(0.6, remaining / 3)),
                    )
                    payload = response.json() if response.status_code == 200 else None
                    result["turn"] = _project_turn_diagnostic(response.status_code, payload)
            except BaseException:
                result["turn"] = _project_turn_diagnostic(None, None)
        if deadline - time.monotonic() >= 2.0 and database_target is not None:
            row = _read_private_turn_receipt(
                database_target,
                receiver_id,
                Path(raw_dir) / "receiver-turn-receipt.private.json",
                deadline,
            )
            result["receipt_row"] = row
            if (
                row.get("status") == "captured"
                and result["turn"].get("projection_status") == "valid"
            ):
                try:
                    private_identity = json.loads(
                        (Path(raw_dir) / "receiver-turn-receipt.private.json").read_text()
                    )
                    row["generation_matches_turn"] = private_identity["generation"] == payload.get(
                        "generation"
                    )
                except (OSError, ValueError, KeyError):
                    row["generation_matches_turn"] = None
        if native_text_context is not None:
            try:
                expected_receiver = native_text_context.get("terminals", {}).get("receiver", {})
                if expected_receiver.get("id") != receiver_id:
                    result["native_text"] = {
                        "status": "context_unavailable",
                        "acceptance_input_changed": False,
                    }
                elif not _receiver_current_receipt_verified(result):
                    result["native_text"] = {
                        "status": "current_turn_unverified",
                        "acceptance_input_changed": False,
                    }
                else:
                    result["native_text"] = _capture_receiver_native_failure_diagnostic(
                        native_text_context, result["tmux"], deadline
                    )
            except BaseException:
                result["native_text"] = {
                    "status": "diagnostic_failed_closed",
                    "acceptance_input_changed": False,
                }
    elapsed = time.monotonic() - started
    result["native_text_current_turn_bound"] = _receiver_native_current_turn_bound(result)
    result["elapsed_ms"] = int(elapsed * 1000)
    result["within_10_seconds"] = elapsed <= 10.0
    result["within_shared_deadline"] = time.monotonic() <= deadline
    if result["status"] == "partial":
        result["status"] = "captured" if elapsed <= 10.0 else "deadline_exceeded"
    return result


_PRE_RESTART_SNAPSHOT_STAGES = (
    "durable_receipt_snapshot",
    "provider_process_snapshot",
    "complete",
)
_PRE_RESTART_PRIVATE_PROCESS_ERRORS = frozenset(
    {
        "private_process_environment_unreadable",
        "private_process_identity_changed",
        "private_process_identity_unsupported",
        "private_process_table_unavailable",
        "private_process_table_unreadable",
    }
)


def _capture_pre_restart_snapshots(
    *, receipt, connect, target, terminals, journey, socket_path, sid
):
    """Run the existing pre-restart reads once and retain their fixed checkpoint."""
    checkpoint = {"stage": "durable_receipt_snapshot"}
    receipt["pre_restart_snapshot_checkpoint"] = checkpoint
    durable_receipts = _durable_receipt_snapshot(connect, target, terminals)
    checkpoint["stage"] = "provider_process_snapshot"
    provider_processes = {
        role: journey._terminal_tagged_processes(data["id"], socket_path, server_identity=sid)
        for role, data in terminals.items()
    }
    checkpoint["stage"] = "complete"
    return durable_receipts, provider_processes


def _record_pre_restart_snapshot_exception(receipt, error):
    """Add only a fixed process error code to an incomplete snapshot checkpoint."""
    checkpoint = receipt.get("pre_restart_snapshot_checkpoint")
    if (
        type(checkpoint) is not dict
        or checkpoint.get("stage") not in _PRE_RESTART_SNAPSHOT_STAGES[:2]
        or not isinstance(error, RuntimeError)
    ):
        return
    code = None
    if type(error) is RuntimeError:
        args = error.args
        if type(args) is tuple and len(args) == 1:
            if type(args[0]) is str and args[0] in _PRE_RESTART_PRIVATE_PROCESS_ERRORS:
                code = args[0]
    checkpoint["private_error_code"] = code or "unknown_private_runtime_error"


def _run_task_query(
    *,
    receipt,
    requests,
    server,
    terminals,
    connect,
    target,
    working_directory,
    binding_path,
    nonce,
    principal,
):
    """One native query; completed fixture is synthetic, not executed work."""
    from uuid import uuid4

    from cli_agent_orchestrator.services.local_peer_service import project_binding

    sender_id = terminals["sender"]["id"]
    receiver_id = terminals["receiver"]["id"]
    context_deadline = time.monotonic() + OBSERVER_GRACE_SECONDS
    identity = _api_json(
        requests,
        "GET",
        server.url + "/local-coordination/identity",
        expected=200,
        deadline=context_deadline,
    )
    binding = project_binding(working_directory)
    verified = _api_json(
        requests,
        "GET",
        server.url + "/local-coordination/projects/verify",
        expected=200,
        deadline=context_deadline,
        params={
            "project_path": str(working_directory),
            "expected_project_id": binding["project_id"],
        },
    )
    source_id = identity.get("instance_id") if type(identity) is dict else None
    if type(source_id) is not str or verified.get("matches") is not True or not principal.id:
        raise GateError("task_query_context_invalid")
    before = {
        role: _api_json(
            requests,
            "GET",
            server.url + "/terminals/" + data["id"],
            expected=200,
            deadline=context_deadline,
        )
        for role, data in terminals.items()
    }
    task_id, target_id = str(uuid4()), str(uuid4())
    if target_id == source_id:
        raise GateError("task_query_synthetic_target_collision")
    result = {"state": "succeeded", "output": "owned synthetic diagnostic fixture"}
    with connect(target) as connection:
        old = connection.execute(
            "SELECT generation FROM terminal_turn_receipts WHERE terminal_id=%s", (sender_id,)
        ).fetchone()
        old_generation = old["generation"] if old else None
        connection.execute(
            "INSERT INTO local_peer_tasks (task_id,source_instance_id,target_instance_id,requester_terminal_id,requester_principal_id,project_id,operation_key,request_hash,use_worktree,state,result_json,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,false,'succeeded',%s,%s,%s)",
            (
                task_id,
                source_id,
                target_id,
                sender_id,
                principal.id,
                binding["project_id"],
                "t214-" + nonce,
                hashlib.sha256(nonce.encode()).hexdigest(),
                json.dumps(result),
                dt.datetime.now(dt.timezone.utc),
                dt.datetime.now(dt.timezone.utc),
            ),
        )
    prepared = _api_json(
        requests,
        "GET",
        server.url + "/local-coordination/agent/tasks/" + task_id,
        expected=200,
        deadline=context_deadline,
        params={"requester_terminal_id": sender_id},
    )
    if not (
        prepared.get("task_id") == task_id
        and prepared.get("requester_terminal_id") == sender_id
        and prepared.get("source_instance_id") == source_id
        and prepared.get("project_id") == binding["project_id"]
        and prepared.get("state") == "succeeded"
        and prepared.get("result") == result
    ):
        raise GateError("task_query_fixture_preflight_failed")
    _write_private(
        binding_path,
        json.dumps(
            {
                "nonce": nonce,
                "task_id": task_id,
                "requester_terminal_id": sender_id,
                "state": "succeeded",
            }
        ),
    )
    receipt["task_fixture_owned_preflight_passed"] = True
    _admit(receipt, "task_query")
    start = time.monotonic()
    deadline = start + TASK_QUERY_SECONDS
    submitted = _api_json(
        requests,
        "POST",
        server.url + "/terminals/" + sender_id + "/input",
        expected=200,
        deadline=deadline,
        params={"message": _task_query_prompt(task_id)},
    )
    sequence = submitted.get("turn_sequence")
    if (
        submitted.get("success") is not True
        or type(sequence) is not int
        or sequence != before["sender"].get("turn_sequence") + 1
    ):
        raise GateError("task_query_turn_sequence_invalid")
    while time.monotonic() < deadline:
        state = _api_json(
            requests, "GET", server.url + "/terminals/" + sender_id, expected=200, deadline=deadline
        )
        with connect(target, readonly=True) as connection:
            proof = connection.execute(
                "SELECT generation,phase FROM terminal_turn_receipts WHERE terminal_id=%s",
                (sender_id,),
            ).fetchone()
        if (
            state.get("status") == "completed"
            and state.get("turn_sequence") == sequence
            and state.get("turn_completed") == sequence
            and proof
            and proof["generation"] != old_generation
            and proof["phase"] == "result_verified"
        ):
            break
        time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
    else:
        raise GateError("task_query_model_deadline_expired")
    receipt["task_query_seconds"] = time.monotonic() - start
    receipt["task_query_turn_verified"] = True
    after_receiver = _api_json(
        requests, "GET", server.url + "/terminals/" + receiver_id, expected=200, deadline=deadline
    )
    receipt["task_query_receiver_unprompted"] = all(
        before["receiver"].get(key) == after_receiver.get(key)
        for key in ("turn_sequence", "turn_completed")
    )
    receipt["task_query_no_messages"] = not _pg_rows(
        connect, target, receiver_id
    ) and not _api_rows(requests, server.url, receiver_id)
    if not receipt["task_query_receiver_unprompted"] or not receipt["task_query_no_messages"]:
        raise GateError("task_query_unrelated_activity_observed")


def _run_enabled(
    model: str,
    *,
    go: bool,
    expected_head: str,
    manifest_path: Path,
    manifest_sha256: str,
    review_digest: str,
    case_id: Any = DEFAULT_CASE_ID,
) -> dict[str, Any]:
    global _ACTIVE_PRIVATE_RUN_ROOT, _SIGTERM_CLEANUP_ACTIVE
    case = _case_definition(case_id)
    if RUN_ENABLED is not True:
        raise GateError("private_execution_copy_required")
    if not _private_execution_copy_contract():
        raise GateError("private_execution_copy_derivation_mismatch")
    if go is not True:
        raise GateError("explicit_go_required")
    if not __debug__:
        raise GateError("optimized_python_not_supported")
    runner_manifest = _load_run_manifest(
        manifest_path,
        manifest_sha256=manifest_sha256,
        expected_head=expected_head,
        review_digest=review_digest,
        case_id=case.case_id,
    )
    _prepend_current_source()
    receipt: dict[str, Any] = {
        "schema": case.receipt_schema,
        "case_id": case.case_id,
        "artifact_label": case.label,
        "profile_fixture_path": case.profile_fixture_path,
        "profile_fixture_schema": case.profile_fixture_schema,
        "utc_started": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_head_expected": EXPECTED_HEAD,
        "binary_sha256_expected": PINNED_OPENCODE_SHA256,
        "postgres_container_id_expected": PINNED_POSTGRES_ID,
        "postgres_image_id_expected": PINNED_POSTGRES_IMAGE,
        "model": model,
        "scope": _case_scope(case.case_id),
        "run_manifest_sha256": RUN_MANIFEST_SHA256,
        "review_digest": REVIEW_DIGEST,
        "source_fingerprint_sha256_expected": SOURCE_FINGERPRINT_SHA256,
        "runner_sha256_expected": RUNNER_SHA256_AT_ADMISSION,
        "profile_fixture_sha256_expected": runner_manifest["profile_fixture_sha256"],
        "operator_auth_snapshot_sha256_expected": runner_manifest["operator_auth_snapshot_sha256"],
        "native_surface": case.native_surface,
        "auth_lane": MODEL,
        "cao_auth_mode": "off",
        "cross_model_or_oauth_claimed": False,
        "claude_used": False,
        "receipt_redacted_only": True,
        "private_raw_evidence_present": False,
        "actual_cancel_observed": False,
        "credentials_cleanup_deferred": False,
        "retained_private_state": {
            "status": "not_created",
            "credential_snapshot": "not_created",
        },
        "output_range_controls_passed": _output_range_controls() == 8,
        "postgres_cleanup_deferred": False,
        "phases": [],
    }
    if model != MODEL:
        raise GateError("explicit_pinned_model_required")
    if receipt["output_range_controls_passed"] is not True:
        raise GateError("output_range_controls_failed")
    if not _runtime_path_valid():
        raise GateError("private_venv_or_current_pythonpath_invalid")
    if (
        not PREP.is_dir()
        or PREP.is_symlink()
        or stat.S_IMODE(PREP.stat().st_mode) != 0o700
        or PREP.stat().st_uid != os.getuid()
    ):
        raise GateError("private_prep_directory_invalid")
    if not REPO.is_dir() or REPO.is_symlink():
        raise GateError("source_root_invalid")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    receipt["source_head"] = head
    if head != EXPECTED_HEAD or not _source_tree_clean():
        raise GateError("source_head_or_runtime_tree_mismatch")
    source_before = _source_fingerprints(expected_head=EXPECTED_HEAD)
    if source_before != runner_manifest.get("source_sha256s"):
        raise GateError("source_file_hash_mismatch")
    receipt["source_file_count"] = len(source_before)
    receipt["source_fingerprint_sha256"] = hashlib.sha256(
        json.dumps(source_before, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if not PINNED_OPENCODE.is_file() or PINNED_OPENCODE.is_symlink():
        raise GateError("pinned_opencode_binary_missing")
    binary_before = _sha256(PINNED_OPENCODE)
    if binary_before != PINNED_OPENCODE_SHA256:
        raise GateError("pinned_opencode_binary_changed")
    if RECEIPT.exists() or EVIDENCE.exists():
        raise GateError("immutable_receipt_or_evidence_already_exists")
    receipt["model_catalog"] = _model_catalog_preflight(model)
    _admit(receipt, "initial")
    if PINNED_PYTHON_RESOLVED is None:
        raise GateError("python_interpreter_identity_mismatch")
    python_real = PINNED_PYTHON_RESOLVED
    python_before = _sha256(python_real)
    receipt["python_interpreter_sha256"] = python_before

    try:
        from test.fixtures.cao_server import _pick_free_port, _start_cao_server
        from test.live_provider import message_api_restart_live as message_live
        from test.live_provider import opencode_http_prompt_observer as prompt_observer
        from test.live_provider import opencode_http_response_argument_observer as response_observer
        from test.live_provider import opencode_http_tool_offer_observer as offer_observer
        from test.live_provider import opencode_interaction_journey as journey
        from test.live_provider import receipt_verification_observer as receipt_stage_observer
        from test.postgres import fixtures as postgres_fixtures
        from test.postgres.fixtures import IMAGE_ID, _discover_postgres_service

        from cli_agent_orchestrator.api import interactions as interactions_api
        from cli_agent_orchestrator.api import main as api_main
        from cli_agent_orchestrator.clients.postgres import connection as postgres_connection
        from cli_agent_orchestrator.models import opencode_agent
        from cli_agent_orchestrator.providers import mcp_interactions, opencode_cli
        from cli_agent_orchestrator.security import auth as security_auth
        from cli_agent_orchestrator.services import terminal_service
        from cli_agent_orchestrator.services.interactions import (
            service as interaction_service_module,
        )
        from cli_agent_orchestrator.utils import (
            mcp_tool_policy,
            opencode_permissions,
            opencode_policy,
            opencode_v2,
            opencode_v2_result_binding,
            opencode_v2_store,
            receipt_verification_diagnostics,
        )
    except ModuleNotFoundError:
        raise GateError("current_source_import_missing") from None

    expected_source_modules = RUN_SOURCE_MODULES
    source_module_paths = {}
    for module_name in expected_source_modules:
        _cancellation_checkpoint()
        module = sys.modules.get(module_name)
        if module is None:
            raise GateError("current_source_import_mismatch")
        module_path = Path(getattr(module, "__file__", "")).resolve(strict=True)
        if not module_path.is_relative_to(REPO):
            raise GateError("current_source_import_mismatch")
        source_module_paths[module_name] = str(module_path.relative_to(REPO))
    if not _runtime_source_modules_match(source_module_paths):
        raise GateError("current_source_import_mismatch")
    receipt["current_source_modules"] = source_module_paths
    production_source_contract = _production_api_capability_issuance_contract(
        api_main,
        security_auth,
        interactions_api,
        terminal_service,
        interaction_service_module,
        opencode_cli,
        _start_cao_server,
    )
    if not all(production_source_contract.values()):
        raise GateError("production_api_capability_source_contract_mismatch")
    receipt["production_api_lifespan_and_interaction_service_verified"] = True
    receipt["production_capability_issuance_path_verified"] = True
    receipt["production_auth_off_loopback_creator_source_verified"] = production_source_contract[
        "auth_off_loopback_creator_source"
    ]
    receipt["production_authenticated_creation_owner_binding_verified"] = (
        production_source_contract["authenticated_creation_owner_persistence"]
    )
    receipt["production_model_launch_source_binding_verified"] = True
    fixture_guard_source_sha256, fixture_guard_derived_sha256 = _install_guarded_postgres_cleanup(
        postgres_fixtures
    )
    receipt["postgres_fixture_cleanup_guard_source_sha256"] = fixture_guard_source_sha256
    receipt["postgres_fixture_cleanup_guard_derived_sha256"] = fixture_guard_derived_sha256
    receipt["postgres_cleanup_guard_installed"] = True
    isolated_live_targets = postgres_fixtures.isolated_live_targets

    if IMAGE_ID != PINNED_POSTGRES_IMAGE:
        raise GateError("postgres_image_source_pin_mismatch")
    service = _discover_postgres_service()
    service_metadata = getattr(service, "metadata", None)
    if (
        not isinstance(service_metadata, dict)
        or service_metadata.get("container_id") != PINNED_POSTGRES_ID
    ):
        raise GateError("postgres_service_identity_mismatch")
    try:
        live_inspection = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.Id}} {{.Image}} {{.State.Running}}",
                PINNED_POSTGRES_ID,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GateError("postgres_live_inspect_failed") from error
    inspection_identity = _parse_postgres_inspect(live_inspection.stdout)
    if (
        not _postgres_service_identity_matches(
            service_metadata,
            live_inspection.returncode,
            live_inspection.stdout,
        )
        or inspection_identity is None
    ):
        raise GateError("postgres_service_identity_mismatch")
    receipt["postgres_container_id"] = inspection_identity[0]
    receipt["postgres_image_id"] = inspection_identity[1]
    receipt["postgres_identity_verified"] = True

    original_environment = dict(os.environ)
    original_home = Path.home()
    auth_paths = _operator_state_paths(original_home, original_environment)
    operator_before = _operator_fingerprints(auth_paths)
    if operator_before.get(str(auth_paths[0])) is None:
        raise GateError("existing_v2_credential_database_required")
    receipt["operator_v2_database_identity"] = "operator-v2-credential-database"
    receipt["operator_auth_snapshot_sha256"] = hashlib.sha256(
        json.dumps(operator_before, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if receipt["operator_auth_snapshot_sha256"] != runner_manifest["operator_auth_snapshot_sha256"]:
        raise GateError("operator_auth_snapshot_manifest_mismatch")
    runner_before = _sha256(Path(__file__))
    receipt["runner_sha256"] = runner_before
    root = _private_opencode_root()
    try:
        _private_directory(EVIDENCE, parent=PREP)
    except BaseException:
        with contextlib.suppress(OSError):
            shutil.rmtree(root)
        _ACTIVE_PRIVATE_RUN_ROOT = None
        raise

    server = None
    pg_context = None
    pg_entered = False
    tmux = None
    tmux_launch_attempted = False
    sid = None
    socket_id = None
    socket_path = None
    tmux_tmpdir = root / "tmux"
    owned_processes: set[Any] = set()
    credential_snapshot_created = False
    owned_process_snapshot_refresh_complete = False
    terminals: dict[str, dict[str, Any]] = {}
    captures: list[Any] = []
    capture_history: list[Any] = []
    api_servers: list[dict[str, Any]] = []
    targets = None
    session_name: str | None = None
    cleanup_session_name: str | None = None
    session_creation_attempted = False
    deletion_attempted = False
    post_delete_roots: int | None = None
    post_delete_caps: int | None = None
    api_cleanup = False
    api_a_started = False
    api_a_cleanup = False
    api_a_launch_attempted = False
    api_b_started = False
    api_b_cleanup = False
    api_b_launch_attempted = False
    api_identity_a = None
    api_identity_b = None
    api_processes_confirmed_dead = False
    agui_capture_threads_confirmed_dead = False
    tmux_processes_confirmed_dead = False
    tmux_cleanup = False
    postgres_cleanup = False
    source_after: dict[str, str] | None = None
    operator_after: dict[str, str | None] | None = None
    binary_after: str | None = None
    runner_after: str | None = None
    native_observations: dict[str, Any] = {}
    role_workdirs: dict[str, Path] = {}
    offer_nonces: dict[str, str] = {}
    offer_directories: dict[str, Path] = {}
    prompt_context: dict[str, Any] | None = None
    response_context: dict[str, Any] | None = None
    receipt_stage_context: dict[str, Path] | None = None
    failures: set[str] = set()
    start = time.monotonic()
    original_run_environment = dict(os.environ)
    sender_capability_path: Path | None = None
    try:
        _admit(receipt, "production_profile_install")
        import frontmatter
        import requests

        from cli_agent_orchestrator.clients.postgres.connection import connect
        from cli_agent_orchestrator.utils.opencode_policy import (
            parse_native_agent,
            validate_native_binding,
        )

        home = _private_directory(root / "home")
        role_workdirs = {
            role: _private_directory(root / f"{role}-workdir") for role in ("receiver", "sender")
        }
        aws_home = _private_directory(home / ".aws")
        cao_home = _private_directory(aws_home / "cli-agent-orchestrator")
        opencode_config_dir = _private_directory(aws_home / "opencode")
        opencode_agents_dir = _private_directory(opencode_config_dir / "agents")
        xdg_config = _private_directory(root / "xdg-config")
        opencode_data_home = _private_directory(root / "opencode-data")
        opencode_data = _private_directory(opencode_data_home / "opencode")
        private_cache = _private_directory(root / "xdg-cache")
        private_state = _private_directory(root / "xdg-state")
        tmp_dir = _private_directory(root / "tmp")
        private_mcp_home = _private_directory(root / "mcp-home")
        mcp_data_home = _private_directory(root / "mcp-xdg-data")
        observer_dir = _private_directory(root / "observer")
        raw_dir = _private_directory(EVIDENCE / "raw")
        raw_observer_dir = _private_directory(raw_dir / "observer")
        receipt_stage_context = {
            "bootstrap_root": _private_directory(root / "receipt-stage-bootstrap"),
            "raw_dir": _private_directory(raw_dir / "receipt-verification"),
        }
        receipt["private_raw_evidence_present"] = True

        offer_plugin_source = REPO / "test/live_provider/opencode_http_tool_offer_observer.js"
        offer_plugin_relative = "test/live_provider/opencode_http_tool_offer_observer.js"
        expected_offer_plugin_sha256 = runner_manifest["source_sha256s"].get(offer_plugin_relative)
        try:
            offer_plugin_bytes = offer_observer._read_pinned_regular_file(
                offer_plugin_source,
                maximum=offer_observer.MAX_PLUGIN_BYTES,
            )
        except (OSError, RuntimeError):
            raise GateError("http_tool_offer_plugin_source_unreadable") from None
        if (
            not _digest_shape(expected_offer_plugin_sha256)
            or hashlib.sha256(offer_plugin_bytes).hexdigest() != expected_offer_plugin_sha256
            or _sha256(offer_plugin_source) != expected_offer_plugin_sha256
        ):
            raise GateError("http_tool_offer_plugin_source_hash_mismatch")
        derived_plugin_hashes: dict[str, str] = {}
        for role, workdir in role_workdirs.items():
            offer_nonces[role] = os.urandom(16).hex()
            derived_offer_plugin = _http_tool_offer_plugin_bytes(
                offer_plugin_bytes,
                nonce=offer_nonces[role],
                tool_names=_case_http_offer_names(case.case_id),
            )
            offer_plugin_path = observer_dir / f"opencode-http-tool-offer-{role}.js"
            _write_private(offer_plugin_path, derived_offer_plugin)
            journey._initialize_private_git_repository(workdir)
            offer_directories[role] = offer_observer.install_project_plugin(
                working_directory=workdir,
                source_path=offer_plugin_path,
                nonce=offer_nonces[role],
            )
            derived_plugin_hashes[role] = hashlib.sha256(derived_offer_plugin).hexdigest()
        receipt["http_tool_offer_observer_source_sha256"] = expected_offer_plugin_sha256
        receipt["http_tool_offer_plugin_sha256_by_role"] = derived_plugin_hashes
        receipt["http_tool_offer_observer_installed"] = True
        prompt_relative = "test/live_provider/opencode_http_prompt_observer.js"
        prompt_source = REPO / prompt_relative
        prompt_source_bytes = _read_source_bound_prompt_file(prompt_relative)
        prompt_nonce = os.urandom(16).hex()
        derived_prompt = _http_prompt_plugin_bytes(prompt_source_bytes, nonce=prompt_nonce)
        private_prompt_source = observer_dir / "opencode-http-prompt-sender.js"
        _write_private(private_prompt_source, derived_prompt)
        prompt_directory = prompt_observer.install_project_plugin(
            working_directory=role_workdirs["sender"],
            source_path=private_prompt_source,
            nonce=prompt_nonce,
        )
        prompt_context = {
            "working_directory": role_workdirs["sender"],
            "directory": prompt_directory,
            "nonce": prompt_nonce,
            "plugin_bytes": derived_prompt,
            "expectation_bytes": None,
        }
        receipt["http_prompt_observer_installed"] = True
        response_context = {
            "working_directory": role_workdirs["sender"],
            "directory": role_workdirs["sender"] / ".opencode/http-response-argument-observation",
            "nonce": prompt_nonce,
            "plugin_bytes": None,
            "protocol_bytes": None,
            "prompt_plugin_bytes": derived_prompt,
            "prompt_expectation_bytes": None,
            "expectation_bytes": None,
        }

        response_plugin_relative = "test/live_provider/opencode_http_response_argument_observer.js"
        response_protocol_relative = "test/live_provider/opencode_http_response_protocol.js"
        response_plugin_source = _read_source_bound_response_file(response_plugin_relative)
        response_protocol_source = _read_source_bound_response_file(response_protocol_relative)
        private_response_plugin_source = observer_dir / "opencode-http-response-sender.js"
        private_response_protocol_source = observer_dir / "opencode-http-response-protocol.js"
        _write_private(private_response_plugin_source, response_plugin_source)
        _write_private(private_response_protocol_source, response_protocol_source)
        if (
            _read_owned_private_file(
                private_response_plugin_source,
                maximum=response_observer.MAX_PLUGIN_BYTES,
            )
            != response_plugin_source
            or _read_owned_private_file(
                private_response_protocol_source,
                maximum=response_observer.MAX_PROTOCOL_BYTES,
            )
            != response_protocol_source
        ):
            raise GateError("http_response_source_copy_mismatch")
        derived_response_plugin = response_observer.specialize_plugin_bytes(
            response_plugin_source, prompt_nonce
        )
        response_context["plugin_bytes"] = derived_response_plugin
        response_context["protocol_bytes"] = response_protocol_source
        response_ledger = response_observer.install_project_plugin(
            working_directory=role_workdirs["sender"],
            source_path=private_response_plugin_source,
            protocol_source_path=private_response_protocol_source,
            nonce=prompt_nonce,
        )
        response_plugin_installed = _read_owned_private_file(
            role_workdirs["sender"] / ".opencode/plugins/cao-http-response-argument-observer.js",
            maximum=response_observer.MAX_PLUGIN_BYTES,
        )
        response_protocol_installed = _read_owned_private_file(
            role_workdirs["sender"]
            / ".opencode/cao-http-response-support/opencode_http_response_protocol.js",
            maximum=response_observer.MAX_PROTOCOL_BYTES,
        )
        prompt_plugin_installed = _read_owned_private_file(
            role_workdirs["sender"] / ".opencode/plugins/cao-http-prompt-observer.js",
            maximum=prompt_observer.MAX_PLUGIN_BYTES,
        )
        if (
            response_plugin_installed != derived_response_plugin
            or response_protocol_installed != response_protocol_source
            or prompt_plugin_installed != derived_prompt
        ):
            raise GateError("http_response_plugin_install_mismatch")
        response_context["directory"] = response_ledger
        receipt["http_response_argument_observer_installed"] = True
        receipt["private_run_root_path_sha256"] = hashlib.sha256(
            os.fsencode(str(root.resolve(strict=True)))
        ).hexdigest()
        receipt["private_role_workspace_path_sha256_by_role"] = {
            role: hashlib.sha256(os.fsencode(str(workdir.resolve(strict=True)))).hexdigest()
            for role, workdir in role_workdirs.items()
        }

        mcp_observer_relative = "test/live_provider/opencode_mcp_observer.py"
        mcp_observer_source_path = REPO / mcp_observer_relative
        mcp_observer_source_sha256 = runner_manifest["source_sha256s"].get(mcp_observer_relative)
        try:
            mcp_observer_source = offer_observer._read_pinned_regular_file(
                mcp_observer_source_path,
                maximum=offer_observer.MAX_PLUGIN_BYTES,
            )
        except (OSError, RuntimeError):
            raise GateError("mcp_observer_source_unreadable") from None
        if (
            not _digest_shape(mcp_observer_source_sha256)
            or hashlib.sha256(mcp_observer_source).hexdigest() != mcp_observer_source_sha256
        ):
            raise GateError("mcp_observer_source_hash_mismatch")
        adapted_observer = _derive_private_mcp_observer_source(
            mcp_observer_source,
            sender_catalog=case.sender_catalog,
            expected_message_bodies=tuple(message for _phase_name, message in PHASES),
            **_case_observer_options(case.case_id),
        )
        observer_path = observer_dir / "opencode_mcp_observer.py"
        _write_private(observer_path, adapted_observer)
        if (
            hashlib.sha256(
                _read_owned_private_file(observer_path, maximum=offer_observer.MAX_PLUGIN_BYTES)
            ).hexdigest()
            != hashlib.sha256(adapted_observer.encode("utf-8")).hexdigest()
        ):
            raise GateError("private_mcp_observer_copy_mismatch")
        sitecustomize_path = observer_dir / "sitecustomize.py"
        sitecustomize_relative = "test/live_provider/sitecustomize.py"
        sitecustomize_source_sha256 = runner_manifest["source_sha256s"].get(sitecustomize_relative)
        try:
            sitecustomize_source = offer_observer._read_pinned_regular_file(
                REPO / sitecustomize_relative,
                maximum=offer_observer.MAX_PLUGIN_BYTES,
            )
        except (OSError, RuntimeError):
            raise GateError("sitecustomize_source_unreadable") from None
        if (
            not _digest_shape(sitecustomize_source_sha256)
            or hashlib.sha256(sitecustomize_source).hexdigest() != sitecustomize_source_sha256
        ):
            raise GateError("sitecustomize_source_hash_mismatch")
        _write_private(sitecustomize_path, sitecustomize_source)
        installed_sitecustomize = _read_owned_private_file(
            sitecustomize_path, maximum=offer_observer.MAX_PLUGIN_BYTES
        )
        if hashlib.sha256(installed_sitecustomize).hexdigest() != sitecustomize_source_sha256:
            raise GateError("private_sitecustomize_copy_mismatch")
        receipt["private_mcp_observer_source_sha256"] = mcp_observer_source_sha256
        receipt["private_sitecustomize_source_sha256"] = sitecustomize_source_sha256
        receipt["private_observer_sha256"] = _sha256(observer_path)
        receipt["private_sitecustomize_sha256"] = _sha256(sitecustomize_path)
        nonce = os.urandom(16).hex()
        base_observation = raw_observer_dir / "native-events"
        child_pythonpath = os.pathsep.join(
            (
                str(observer_dir),
                str(REPO / "test/live_provider"),
                str(REPO / "src"),
                str(REPO),
            )
        )
        api_port = _pick_free_port()
        child_env = {
            "HOME": str(private_mcp_home),
            "XDG_DATA_HOME": str(mcp_data_home),
            "TMPDIR": str(tmp_dir),
            "PYTHONPATH": child_pythonpath,
            "PYTHONDONTWRITEBYTECODE": "1",
            "CAO_HOME_DIR": str(cao_home),
            "CAO_DATABASE_CONFIG_FILE": str(cao_home / "database-config.json"),
            "CAO_API_HOST": "127.0.0.1",
            "CAO_API_PORT": str(api_port),
            "CAO_AUTH_MODE": "off",
            "CAO_MCP_APPS_ENABLED": "0",
            "CAO_OPENCODE_MCP_OBSERVER": "1",
            "CAO_OPENCODE_MCP_OBSERVER_PATH": str(base_observation),
            "CAO_OPENCODE_MCP_OBSERVER_NONCE": nonce,
            "CAO_OPENCODE_MCP_SOURCE_ROOT": str(REPO),
            "CAO_ENABLE_SENDER_ID_INJECTION": "true",
        }
        task_binding_path = raw_observer_dir / "task-query-binding.json"
        if case.case_id == TASK_QUERY_CASE_ID:
            child_env.update(
                {
                    "CAO_OPENCODE_MCP_SAFE_RESULT_TOOL": "get_local_cao_task",
                    "CAO_OPENCODE_MCP_SAFE_TASK_BINDING_PATH": str(task_binding_path),
                }
            )
        path_value = os.pathsep.join((str(PINNED_OPENCODE.parent), "/usr/bin", "/bin"))
        install_env = {
            "HOME": str(home),
            "CAO_HOME_DIR": str(cao_home),
            "CAO_MCP_APPS_ENABLED": "0",
            "PATH": path_value,
            "LANG": "C.UTF-8",
            "PYTHONPATH": os.pathsep.join((str(REPO / "src"), str(REPO))),
            "PYTHONDONTWRITEBYTECODE": "1",
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_DATA_HOME": str(opencode_data_home),
            "XDG_CACHE_HOME": str(private_cache),
            "XDG_STATE_HOME": str(private_state),
            "OPENCODE_CONFIG": str(opencode_config_dir / "opencode.json"),
            "OPENCODE_CONFIG_DIR": str(opencode_config_dir),
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
        }
        for role in ("receiver", "sender"):
            _admit(receipt, f"install_{role}")
            source = root / f"{role}.md"
            profile = _profile_text(frontmatter, role, child_env, case.case_id)
            _write_private(source, profile)
            result = subprocess.run(
                [
                    str(PINNED_PYTHON),
                    "-m",
                    "cli_agent_orchestrator.cli.main",
                    "install",
                    str(source),
                    "--provider",
                    "opencode_cli",
                ],
                cwd=REPO,
                env=install_env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            _write_private(
                raw_dir / f"install-{role}.private",
                (result.stdout + result.stderr).encode("utf-8", errors="replace"),
            )
            receipt[f"{role}_production_install_passed"] = result.returncode == 0
            if result.returncode != 0:
                raise GateError("production_profile_install_failed")
            native_path = opencode_agents_dir / f"{case.profile_prefix}-{role}.md"
            if (
                not native_path.is_file()
                or native_path.is_symlink()
                or stat.S_IMODE(native_path.stat().st_mode) != 0o600
            ):
                raise GateError("installed_native_profile_missing_or_not_private")
            native_text = native_path.read_text(encoding="utf-8")
            profile_name = f"{case.profile_prefix}-{role}"
            native_agent, native_policy = parse_native_agent(native_text, profile_name)
            expected_prompt = _profile_contract(role, case.case_id)[2]
            if native_agent.content.rstrip("\n") != expected_prompt.rstrip("\n"):
                raise GateError("installed_native_prompt_differs_from_fixture")
            receipt[f"{role}_installed_prompt_matches_fixture"] = True
            source_config = json.loads(
                (opencode_config_dir / "opencode.json").read_text(encoding="utf-8")
            )
            allowed, enabled, _ = _profile_contract(role, case.case_id)
            if (
                native_policy is None
                or native_policy.state != "ready"
                or native_policy.format_version != 3
                or dict(native_policy.profiles)
                != {"cao-mcp-server": _case_native_profile(role, case.case_id)}
                or dict(native_policy.filters)
                != ({} if enabled is None else {"cao-mcp-server": frozenset(enabled)})
                or not _native_permission_matches(
                    native_agent.metadata,
                    allowed,
                    opencode_permissions.cao_tools_to_opencode_permission,
                )
            ):
                raise GateError("installed_native_profile_contract_mismatch")
            validate_native_binding(native_agent, native_policy, source_config)
            receipt[f"{role}_installed_native_v3_verified"] = True
            receipt[f"{role}_allowed_tools_exact"] = True
            if role == "sender":
                if case.requires_human_capability:
                    receipt[
                        _case_receipt_key(
                            "sender_native_v3_full_collaboration_unfiltered", case.case_id
                        )
                    ] = True
                else:
                    receipt["sender_native_v3_exact_two_tool_filter"] = True
            else:
                receipt["receiver_native_v3_empty_tool_filter"] = True

        source_db = _operator_state_paths(original_home, original_environment)[0]
        from cli_agent_orchestrator.providers.opencode_cli import _copy_private_state_bundle

        _copy_private_state_bundle(source_db, opencode_data)
        private_db = opencode_data / "opencode.db"
        if not private_db.is_file() or private_db.is_symlink():
            raise GateError("private_v2_credential_snapshot_missing")
        credential_snapshot_created = True
        receipt["private_v2_credential_snapshot_created"] = True

        version_env = {
            "HOME": str(home),
            "PATH": path_value,
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_DATA_HOME": str(opencode_data_home),
            "XDG_CACHE_HOME": str(private_cache),
            "XDG_STATE_HOME": str(private_state),
            "OPENCODE_DB": str(private_db),
            "OPENCODE_CONFIG": str(opencode_config_dir / "opencode.json"),
            "OPENCODE_CONFIG_DIR": str(opencode_config_dir),
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
        }
        version_result = subprocess.run(
            [str(PINNED_OPENCODE), "--version"],
            cwd=home,
            env=version_env,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        version = (version_result.stdout or version_result.stderr).strip()
        if (
            version_result.returncode != 0
            or re.fullmatch(r"opencode v2\.[0-9]+(?:\.[0-9]+)?(?:[-+][A-Za-z0-9._-]+)?", version)
            is None
        ):
            raise GateError("pinned_opencode_v2_version_invalid")
        receipt["opencode_version"] = version

        tmux_tmpdir = _private_directory(root / "tmux")
        # Tmux itself snapshots its server environment. Rebuild that environment
        # from task-owned paths and explicit runtime settings so no ambient
        # provider keys, AWS identity/profile selectors, proxies, or auth tokens
        # can reach native provider subprocesses. Values are never inspected.
        os.environ.clear()
        os.environ.update(
            {
                "HOME": str(home),
                "PATH": path_value,
                "LANG": "C.UTF-8",
                "TERM": "xterm-256color",
                "TMPDIR": str(tmp_dir),
                "TMUX_TMPDIR": str(tmux_tmpdir),
                "CAO_HOME_DIR": str(cao_home),
                "CAO_DATABASE_CONFIG_FILE": str(cao_home / "database-config.json"),
                "CAO_MCP_APPS_ENABLED": "0",
                "CAO_TERMINAL_BACKEND": "tmux",
                "CAO_AUTH_MODE": "off",
                "CAO_ENABLE_SENDER_ID_INJECTION": "true",
                "XDG_CONFIG_HOME": str(xdg_config),
                "XDG_DATA_HOME": str(opencode_data_home),
                "XDG_CACHE_HOME": str(private_cache),
                "XDG_STATE_HOME": str(private_state),
                "OPENCODE_DB": str(private_db),
                "OPENCODE_CONFIG": str(opencode_config_dir / "opencode.json"),
                "OPENCODE_CONFIG_DIR": str(opencode_config_dir),
                "OPENCODE_DISABLE_AUTOUPDATE": "1",
                "PYTHONPATH": os.pathsep.join((str(REPO / "src"), str(REPO))),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        if "OPENCODE_DISABLE_PROJECT_CONFIG" in os.environ:
            raise GateError("project_plugin_loading_disabled_by_environment")
        receipt["ambient_environment_sanitized_by_allowlist"] = (
            all(not key.startswith("AWS_") for key in os.environ)
            and "TMUX" not in os.environ
            and "CAO_TERMINAL_ID" not in os.environ
        )
        if not receipt["ambient_environment_sanitized_by_allowlist"]:
            raise GateError("ambient_environment_allowlist_failed")

        pg_context = isolated_live_targets(root / "pg", service=service)
        targets = _enter_guarded_postgres_context(pg_context, postgres_fixtures)
        pg_entered = True
        target_fingerprint = message_live._target_fingerprint(
            targets, database_target_type=type(targets.main)
        )
        receipt["postgres_target_fingerprint_sha256"] = target_fingerprint

        api_extra = {
            "CAO_HOME_DIR": str(cao_home),
            "CAO_AUTH_MODE": "off",
            "CAO_AGUI_ENABLED": "true",
            "CAO_ENABLE_SENDER_ID_INJECTION": "true",
            "CAO_TERMINAL_BACKEND": "tmux",
            "PATH": path_value,
            "PYTHONPATH": os.pathsep.join((str(REPO / "src"), str(REPO))),
            "PYTHONDONTWRITEBYTECODE": "1",
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_DATA_HOME": str(opencode_data_home),
            "XDG_CACHE_HOME": str(private_cache),
            "XDG_STATE_HOME": str(private_state),
            "OPENCODE_DB": str(private_db),
            "OPENCODE_CONFIG": str(opencode_config_dir / "opencode.json"),
            "OPENCODE_CONFIG_DIR": str(opencode_config_dir),
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "TMUX_TMPDIR": str(tmux_tmpdir),
            "TERM": "xterm-256color",
        }
        if "OPENCODE_DISABLE_PROJECT_CONFIG" in api_extra:
            raise GateError("project_plugin_loading_disabled_by_api_environment")
        receipt["project_config_disable_flag_absent"] = True
        api_allowlist = frozenset(
            {
                "OPENCODE_DISABLE_AUTOUPDATE",
                "OPENCODE_CONFIG",
                "OPENCODE_CONFIG_DIR",
                "OPENCODE_DB",
            }
        )

        def start_api() -> Any:
            role = "api_b" if api_b_launch_attempted else "api_a"
            fixture_server_module = sys.modules.get("test.fixtures.cao_server")
            registry = getattr(fixture_server_module, "_LIVE_SERVERS", None)
            before = set(registry) if isinstance(registry, set) else None
            try:
                api_environment = dict(api_extra)
                api_environment["PYTHONPATH"] = _prepare_receipt_stage_api_pythonpath(
                    observer=receipt_stage_observer,
                    bootstrap_root=receipt_stage_context["bootstrap_root"],
                    raw_dir=receipt_stage_context["raw_dir"],
                    source_hashes=runner_manifest["source_sha256s"],
                    role=role,
                    capture_render_inputs=case.case_id == TASK_QUERY_CASE_ID,
                )
                started_server = _start_cao_server(
                    home,
                    api_port,
                    deadline=60,
                    database_targets=targets,
                    extra_env=api_environment,
                    allowed_external_credentials=api_allowlist,
                )
                if not _receipt_stage_api_ready(receipt_stage_context["raw_dir"], role):
                    raise GateError("receipt_stage_api_bootstrap_not_ready")
                return started_server
            except BaseException:
                registry_after = getattr(fixture_server_module, "_LIVE_SERVERS", None)
                if before is None or not isinstance(registry_after, set):
                    failures.add("api_start_registry_unavailable")
                else:
                    _record_unreturned_api_servers(
                        api_servers,
                        role=role,
                        before=before,
                        after=set(registry_after),
                        capture_identity=lambda handle: _capture_api_process(handle, journey),
                    )
                raise

        tmux = __import__(
            "cli_agent_orchestrator.clients.tmux", fromlist=["TmuxClient"]
        ).TmuxClient()
        socket_path = journey._pin_tmux_client_to_private_server(tmux, tmux_tmpdir)
        tmux_launch_attempted = True
        sid, socket_id = journey._start_and_pin_private_tmux_server(tmux, socket_path)
        receipt["private_tmux_server_pinned"] = True

        _admit(receipt, "api_start")
        api_a_launch_attempted = True
        server = start_api()
        api_servers.append(
            {
                "role": "api_a",
                "server": server,
                "identity": None,
                "stop_confirmed": False,
                "start_returned": True,
            }
        )
        api_a_started = True
        api_process, api_identity_a = _capture_api_process(server, journey)
        if PINNED_PYTHON is None or not _api_worker_invocation_matches(
            api_process, server, PINNED_PYTHON
        ):
            raise GateError("production_api_worker_invocation_mismatch")
        receipt["production_api_worker_invocation_verified"] = True
        api_servers[-1]["identity"] = api_identity_a
        receipt["api_a_url"] = server.url
        receipt["api_a_pid"] = api_identity_a.pid
        receipt["api_a_process_start"] = api_identity_a.start_time
        receipt["api_healthy"] = True
        group = [case.group_prefix, os.urandom(6).hex()]
        requested_session_name = case.session_prefix + "-" + os.urandom(8).hex()
        for role in ("receiver", "sender"):
            _admit(receipt, f"terminal_{role}")
            terminal_started = time.monotonic()
            terminal_deadline = terminal_started + MODEL_PHASE_SECONDS
            params: dict[str, Any] = {
                "provider": "opencode_cli",
                "agent_profile": f"{case.profile_prefix}-{role}",
                "working_directory": str(role_workdirs[role]),
                "model": model,
            }
            route = "/sessions"
            if role == "receiver":
                params["session_name"] = requested_session_name
                # Bind a unique, validated fallback before POST so malformed or
                # missing create-response data cannot orphan this own session.
                cleanup_session_name = _owned_session_cleanup_name(
                    requested_session_name, case.case_id
                )
                session_creation_attempted = True
            else:
                if not isinstance(session_name, str):
                    raise GateError("receiver_session_name_not_bound")
                route = f"/sessions/{session_name}/terminals"
                params["caller_id"] = terminals["receiver"]["id"]
            response = requests.post(
                server.url + route,
                params=params,
                json={"group": group},
                timeout=MODEL_PHASE_SECONDS,
            )
            _write_private(raw_dir / f"create-{role}.private", response.text)
            receipt[f"{role}_create_http_status"] = response.status_code
            if role == "receiver":
                cleanup_session_name = _receiver_cleanup_target(
                    requested_session_name,
                    creation_attempted=session_creation_attempted,
                    status_code=response.status_code,
                    json_parsed=False,
                    returned=None,
                    case_id=case.case_id,
                )
            if response.status_code != 201:
                raise GateError("production_terminal_creation_failed")
            data = response.json()
            if role == "receiver":
                cleanup_session_name = _receiver_cleanup_target(
                    requested_session_name,
                    creation_attempted=session_creation_attempted,
                    status_code=response.status_code,
                    json_parsed=True,
                    returned=data.get("session_name"),
                    case_id=case.case_id,
                )
            returned_session_name = _validated_returned_session_name(
                requested_session_name, data.get("session_name"), case.case_id
            )
            if role == "receiver":
                session_name = returned_session_name
                cleanup_session_name = returned_session_name
            elif session_name is None or returned_session_name != session_name:
                raise GateError("sender_session_name_binding_mismatch")
            terminals[role] = data
            terminal_id = data.get("id")
            if not _safe_terminal_identity(terminal_id):
                raise GateError("terminal_identity_invalid")
            owned_processes |= journey._snapshot_and_recheck_private_tmux_server(
                sid, socket_id, socket_path, terminal_id=terminal_id
            )
            while time.monotonic() < terminal_deadline:
                data, terminal_observed_in_time = _read_terminal_before_deadline(
                    requests,
                    server.url,
                    terminal_id,
                    deadline=terminal_deadline,
                )
                if not terminal_observed_in_time or data is None:
                    data = None
                    break
                if data.get("status") in {"idle", "completed"}:
                    break
                time.sleep(0.5)
            else:
                raise GateError("native_terminal_readiness_timeout")
            if data is None:
                raise GateError("native_terminal_readiness_timeout")
            if (
                session_name is None
                or _validated_returned_session_name(
                    requested_session_name, data.get("session_name"), case.case_id
                )
                != session_name
            ):
                raise GateError("terminal_session_name_changed")
            terminals[role] = data
            _safe_metadata(data, role=role, expected_group=group, case_id=case.case_id)
            expected_allowed = _profile_contract(role, case.case_id)[0]
            if data.get("allowed_tools") != expected_allowed:
                raise GateError("operation_permissions_mismatch")
            receipt[f"{role}_operation_permissions_exact"] = True
            working_directory_response = _api_json(
                requests,
                "GET",
                f"{server.url}/terminals/{terminal_id}/working-directory",
                expected=200,
                deadline=terminal_deadline,
            )
            if not _working_directory_matches(
                working_directory_response.get("working_directory"),
                role_workdirs[role],
            ):
                raise GateError("terminal_current_working_directory_mismatch")
            receipt[f"{role}_current_working_directory_bound"] = True
            receipt[f"{role}_current_working_directory_sha256"] = hashlib.sha256(
                os.fsencode(str(role_workdirs[role].resolve(strict=True)))
            ).hexdigest()
            viewport = tmux.get_history(
                session_name, data["name"], strip_escapes=True, visible_only=True
            )
            if (
                "GLM-5.3-Flash" not in viewport
                or "OpenCode Go" not in viewport
                or "Big Pickle" in viewport
            ):
                raise GateError("native_model_readiness_mismatch")
            receipt[f"{role}_model_ready"] = True
            raw_model_phase_seconds = time.monotonic() - terminal_started
            receipt[f"{role}_model_phase_within_75_seconds"] = _within_budget(
                raw_model_phase_seconds, MODEL_PHASE_SECONDS
            )
            receipt[f"{role}_model_phase_seconds"] = round(raw_model_phase_seconds, 3)
            if receipt[f"{role}_model_phase_within_75_seconds"] is not True:
                raise GateError("model_phase_exceeded_75_seconds")
            if role == "receiver":
                receiver_capabilities = _capability_files(cao_home)
                if receiver_capabilities:
                    raise GateError("receiver_runtime_capability_unexpected")
                if not case.requires_human_capability:
                    receipt["capability_files_at_receiver_initialization"] = len(
                        receiver_capabilities
                    )
                if not _provider_config_contract(
                    cao_home,
                    terminals[role],
                    role="receiver",
                    capability_path=None,
                    case_id=case.case_id,
                ):
                    raise GateError("receiver_v2_empty_tool_profile_contract_mismatch")
                receipt["receiver_no_runtime_capability"] = True
            else:
                capability_projection: dict[str, Any] | None = None
                if case.requires_human_capability:
                    sender_capability_diagnostics: dict[str, Any] = {}
                    try:
                        sender_capability_path, capability_projection = (
                            _runtime_capability_contract(
                                cao_home,
                                targets.main,
                                terminals[role],
                                api_server=server,
                                api_process=api_process,
                                api_identity=api_identity_a,
                                python_path=PINNED_PYTHON,
                                diagnostics=sender_capability_diagnostics,
                            )
                        )
                    finally:
                        _record_capability_binding_diagnostics(
                            receipt, sender_capability_diagnostics
                        )
                else:
                    sender_capabilities = _capability_files(cao_home)
                    if sender_capabilities:
                        raise GateError("sender_runtime_capability_unexpected")
                    receipt["capability_files_at_sender_initialization"] = len(sender_capabilities)
                    receipt["sender_no_runtime_human_capability"] = True
                if not _provider_config_contract(
                    cao_home,
                    terminals[role],
                    role="sender",
                    capability_path=sender_capability_path,
                    case_id=case.case_id,
                ):
                    raise GateError(
                        "sender_v2_profile_contract_mismatch"
                        if not case.requires_human_capability
                        else "sender_v2_full_profile_contract_mismatch"
                    )
                receipt["sender_legacy_three_tool_marker_absent"] = True
                if capability_projection is not None:
                    receipt.update(
                        {
                            "sender_runtime_capability_current_source_bound": capability_projection[
                                "current_source_bound"
                            ],
                            "sender_capability_file_private_owned": capability_projection[
                                "capability_file_private_owned"
                            ],
                            "sender_capability_source_owner_kind": capability_projection[
                                "source_owner_kind"
                            ],
                            "sender_capability_authenticated_local_operator_owner_bound": (
                                capability_projection[
                                    "source_owner_matches_expected_local_operator"
                                ]
                            ),
                            "sender_capability_auth_off_loopback_api_process_binding_verified": (
                                capability_projection[
                                    "auth_off_loopback_api_process_binding_verified"
                                ]
                            ),
                            "sender_capability_path_sha256": capability_projection[
                                "capability_file_path_sha256"
                            ],
                        }
                    )

        sender_id = terminals["sender"]["id"]
        receiver_id = terminals["receiver"]["id"]
        if (
            sender_id == receiver_id
            or terminals["sender"]["session_name"] != session_name
            or terminals["receiver"]["session_name"] != session_name
            or terminals["receiver"].get("caller_id") is not None
            or terminals["sender"].get("caller_id") != receiver_id
        ):
            raise GateError("two_terminal_topology_invalid")
        roots_before = _provider_roots(cao_home)
        if len(roots_before) != 2:
            raise GateError("two_native_v2_provider_roots_required")
        root_identity_before = _native_config_roots(roots_before)
        receipt["two_native_v2_provider_roots"] = True
        if (
            (
                case.requires_human_capability
                and (
                    sender_capability_path is None
                    or _capability_files(cao_home) != [sender_capability_path]
                )
            )
            or (not case.requires_human_capability and bool(_capability_files(cao_home)))
            or not _provider_config_contract(
                cao_home,
                terminals["sender"],
                role="sender",
                capability_path=sender_capability_path,
                case_id=case.case_id,
            )
            or not _provider_config_contract(
                cao_home,
                terminals["receiver"],
                role="receiver",
                capability_path=None,
                case_id=case.case_id,
            )
        ):
            raise GateError("pre_delete_capability_or_profile_binding_mismatch")
        if case.requires_human_capability:
            if sender_capability_path is None or _capability_files(cao_home) != [
                sender_capability_path
            ]:
                raise GateError("pre_delete_capability_or_profile_binding_mismatch")
            receipt["expected_capability_set_before_delete"] = True
        else:
            capability_count = len(_capability_files(cao_home))
            receipt["capability_files_before_first_input"] = capability_count
            if capability_count != 0:
                raise GateError("generic_message_capability_inventory_nonzero")
            receipt["capability_inventory_zero_before_first_input"] = True
            receipt["receiver_no_runtime_human_capability"] = True
            receipt["no_full_eight_human_capability_claim"] = True
        if _pg_rows(connect, targets.main, receiver_id) or _api_rows(
            requests, server.url, receiver_id
        ):
            raise GateError("initial_inbox_not_empty")

        if case.case_id == COORDINATION_CASE_ID:
            context_deadline = time.monotonic() + OBSERVER_GRACE_SECONDS
            cwd_response = _api_json(
                requests,
                "GET",
                f"{server.url}/terminals/{sender_id}/working-directory",
                expected=200,
                deadline=context_deadline,
            )
            expected_cwd = str(role_workdirs["sender"].resolve(strict=True))
            if (
                type(cwd_response) is not dict
                or cwd_response.get("working_directory") != expected_cwd
            ):
                raise GateError("coordination_sender_context_mismatch")
            peers = _api_json(
                requests,
                "GET",
                f"{server.url}/local-coordination/agent/peers",
                expected=200,
                deadline=context_deadline,
                params={"requester_terminal_id": sender_id, "project_path": expected_cwd},
            )
            context = {
                "id": terminals["sender"].get("id"),
                "working_directory": cwd_response["working_directory"],
            }
            if not _coordination_context_contract(
                context,
                sender_id=sender_id,
                working_directory=expected_cwd,
                peer_status=200,
                peer_body=peers,
            ):
                raise GateError("coordination_project_preflight_failed")
            receipt["coordination_context_preflight_passed"] = True

        if case.case_id == TASK_QUERY_CASE_ID:
            _run_task_query(
                receipt=receipt,
                requests=requests,
                server=server,
                terminals=terminals,
                connect=connect,
                target=targets.main,
                working_directory=role_workdirs["sender"],
                binding_path=task_binding_path,
                nonce=nonce,
                principal=_auth_off_loopback_creation_principal(
                    server, api_process, api_identity_a, PINNED_PYTHON
                ),
            )
        else:
            phase_prompts = tuple(
                _phase_prompt(index, receiver_id, message, case.case_id)
                for index, (_phase_name, message) in enumerate(PHASES)
            )
            expected_prompt_document = _http_prompt_expectations(
                sender_id=sender_id,
                nonce=prompt_nonce,
                phase_prompts=phase_prompts,
            )
            expectation_path = prompt_observer.write_expectations(
                working_directory=role_workdirs["sender"], document=expected_prompt_document
            )
            expected_bytes = _read_owned_private_file(expectation_path, maximum=64 * 1024)
            if json.loads(expected_bytes) != expected_prompt_document:
                raise GateError("http_prompt_expectation_copy_mismatch")
            prompt_context["expectation_bytes"] = expected_bytes

            if response_context is None:
                raise GateError("http_response_observer_context_unavailable")
            expected_response_document = _http_response_expectations(nonce=prompt_nonce)
            response_expectation_path = response_observer.write_expectations(
                working_directory=role_workdirs["sender"],
                document=expected_response_document,
            )
            response_expectation_bytes = _read_owned_private_file(
                response_expectation_path,
                maximum=response_observer.MAX_EXPECTATION_BYTES,
            )
            if json.loads(response_expectation_bytes) != expected_response_document:
                raise GateError("http_response_expectation_copy_mismatch")
            response_context["prompt_expectation_bytes"] = expected_bytes
            response_context["expectation_bytes"] = response_expectation_bytes

            first_row_identity = None
            first_api_identity = None
            provider_processes_before_restart = None
            metadata_before_restart = None
            target_fingerprint_before_restart = target_fingerprint
            server_a = server
            output_range_observers = {
                "sender": _new_output_range_observer(),
                "receiver": _new_output_range_observer(),
            }

            for index, (phase_name, message) in enumerate(PHASES):
                _cancellation_checkpoint()
                _admit(receipt, f"model_phase_{index + 1}_sender")
                _admit(receipt, f"model_phase_{index + 1}_receiver")
                lifecycle_before = _api_lifecycle_snapshot(
                    requests,
                    server.url,
                    terminals,
                    diagnostic=receipt.setdefault("lifecycle_observations", {}).setdefault(
                        f"phase_{index + 1}_before", {}
                    ),
                )
                sender_sequence_before = lifecycle_before["sender"]["turn_sequence"]
                receiver_sequence_before = lifecycle_before["receiver"]["turn_sequence"]
                capture = message_live.AguiCapture(server.url, sender_id, receiver_id)
                captures.append(capture)
                capture_history.append(capture)
                capture.start(timeout=20, failure_stage="phase_a" if index == 0 else "phase_b")
                _admit(receipt, f"model_phase_{index + 1}_start")
                _reset_output_range_markers(output_range_observers["sender"])
                _reset_output_range_markers(output_range_observers["receiver"])
                phase: dict[str, Any] = {
                    "name": phase_name,
                    "receiver_ack_exact": None,
                    "output_range_observations": {"sender": [], "receiver": []},
                    "sender_last_observations": [],
                    "sender_last_observations_truncated": False,
                }
                receipt["phases"].append(phase)
                model_start = time.monotonic()
                sender_deadline = model_start + MODEL_PHASE_SECONDS
                response = requests.post(
                    f"{server.url}/terminals/{sender_id}/input",
                    params={"message": phase_prompts[index]},
                    timeout=30,
                )
                _cancellation_checkpoint()
                if response.status_code != 200:
                    raise GateError("model_input_http_status_mismatch")
                input_result = response.json()
                if not isinstance(input_result, dict):
                    raise GateError("model_input_sequence_invalid")
                sequence = input_result.get("turn_sequence")
                if (
                    input_result.get("success") is not True
                    or type(sequence) is not int
                    or sequence != sender_sequence_before + 1
                ):
                    raise GateError("model_input_sequence_invalid")
                final_terminal: dict[str, Any] = {}
                final_receiver: dict[str, Any] = {}
                last_text = ""
                last_rows: list[dict[str, Any]] = []
                last_viewport = ""
                sender_completed_at: float | None = None
                sender_terminal_closed_at: float | None = None
                receiver_started_at: float | None = None
                receiver_completed_at: float | None = None
                receiver_output_text = ""
                previous_poll_started: float | None = None
                while True:
                    _cancellation_checkpoint()
                    current_time = time.monotonic()
                    if sender_completed_at is None and current_time >= sender_deadline:
                        break
                    if receiver_started_at is None and current_time >= sender_deadline:
                        break
                    if (
                        receiver_started_at is not None
                        and receiver_completed_at is None
                        and current_time >= receiver_started_at + MODEL_PHASE_SECONDS
                    ):
                        break
                    if sender_completed_at is not None and receiver_completed_at is not None:
                        break
                    poll_started = time.monotonic()
                    last_viewport = tmux.get_history(
                        session_name,
                        terminals["sender"]["name"],
                        strip_escapes=True,
                        visible_only=True,
                    )
                    last_rows = _pg_rows(connect, targets.main, receiver_id)
                    if sender_completed_at is None:
                        sender_range_sample, sender_range_in_time = _bounded_output_range_sample(
                            requests,
                            server.url,
                            sender_id,
                            output_range_observers["sender"],
                            deadline=sender_deadline,
                            phase_started=model_start,
                        )
                        phase["output_range_observations"]["sender"].append(
                            _label_range_sample_provenance(sender_range_sample)
                        )
                        if not sender_range_in_time:
                            break
                        output_response, output_observed_in_time = _bounded_phase_get(
                            requests,
                            f"{server.url}/terminals/{sender_id}/output",
                            deadline=sender_deadline,
                            params={"mode": "last"},
                        )
                        if not output_observed_in_time or output_response is None:
                            break
                        output_body = _sender_last_body(
                            output_response, phase, sequence, time.monotonic() - model_start
                        )
                        if output_response.status_code == 200:
                            last_text = (
                                output_body.get("output", "")
                                if isinstance(output_body, dict)
                                else ""
                            )
                        sender_terminal, sender_terminal_in_time = _read_terminal_before_deadline(
                            requests,
                            server.url,
                            sender_id,
                            deadline=sender_deadline,
                        )
                        if not sender_terminal_in_time or sender_terminal is None:
                            break
                        final_terminal = sender_terminal
                        sender_observation = _sender_last_observation(
                            output_response.status_code,
                            output_body,
                            final_terminal,
                            sequence,
                            time.monotonic() - model_start,
                        )
                        _record_sender_last_observation(phase, sender_observation)
                        if (
                            sender_terminal_closed_at is None
                            and sender_observation["terminal_closed_for_input"] is True
                        ):
                            sender_terminal_closed_at = time.monotonic()
                        _attach_range_turn_fields(
                            sender_range_sample,
                            sender_terminal,
                            role="sender",
                            expected_sequence=sequence,
                        )
                        if (
                            LAST_RECEIPT in last_text
                            and final_terminal.get("status") == "completed"
                            and final_terminal.get("turn_sequence") == sequence
                            and final_terminal.get("turn_completed") == sequence
                        ):
                            sender_completed_at = time.monotonic()
                    receiver_deadline = (
                        receiver_started_at + MODEL_PHASE_SECONDS
                        if receiver_started_at is not None
                        else sender_deadline
                    )
                    receiver_range_sample, receiver_range_in_time = _bounded_output_range_sample(
                        requests,
                        server.url,
                        receiver_id,
                        output_range_observers["receiver"],
                        deadline=receiver_deadline,
                        phase_started=(
                            receiver_started_at if receiver_started_at is not None else model_start
                        ),
                    )
                    phase["output_range_observations"]["receiver"].append(
                        _label_range_sample_provenance(receiver_range_sample)
                    )
                    if not receiver_range_in_time:
                        break
                    receiver_terminal, receiver_terminal_in_time = _read_terminal_before_deadline(
                        requests,
                        server.url,
                        receiver_id,
                        deadline=receiver_deadline,
                    )
                    if not receiver_terminal_in_time or receiver_terminal is None:
                        break
                    final_receiver = receiver_terminal
                    _attach_range_turn_fields(
                        receiver_range_sample,
                        receiver_terminal,
                        role="receiver",
                        expected_sequence=receiver_sequence_before + 1,
                    )
                    receiver_sequence = final_receiver.get("turn_sequence")
                    receiver_completed = final_receiver.get("turn_completed")
                    if (
                        type(receiver_sequence) is int
                        and receiver_sequence == receiver_sequence_before + 1
                        and receiver_started_at is None
                    ):
                        receiver_started_at = previous_poll_started or model_start
                        _admit(receipt, f"receiver_phase_{index + 1}_started")
                    if (
                        type(receiver_sequence) is int
                        and receiver_sequence > receiver_sequence_before + 1
                    ):
                        raise GateError("receiver_turn_count_exceeded_one_delivery")
                    if (
                        receiver_started_at is not None
                        and final_receiver.get("status") in {"idle", "completed"}
                        and receiver_sequence == receiver_sequence_before + 1
                        and type(receiver_completed) is int
                        and receiver_completed == receiver_sequence
                        and receiver_completed_at is None
                    ):
                        receiver_completed_at = time.monotonic()
                    if sender_completed_at is not None and receiver_completed_at is not None:
                        break
                    previous_poll_started = poll_started
                    time.sleep(0.5)
                _cancellation_checkpoint()
                phase["elapsed_seconds"] = round(time.monotonic() - model_start, 3)
                raw_sender_model_seconds = (
                    sender_completed_at - model_start if sender_completed_at is not None else None
                )
                raw_receiver_model_seconds = (
                    receiver_completed_at - receiver_started_at
                    if receiver_completed_at is not None and receiver_started_at is not None
                    else None
                )
                phase["sender_model_within_75_seconds"] = _within_budget(
                    raw_sender_model_seconds, MODEL_PHASE_SECONDS
                )
                phase["receiver_model_within_75_seconds"] = _within_budget(
                    raw_receiver_model_seconds, MODEL_PHASE_SECONDS
                )
                phase["sender_model_seconds"] = (
                    round(raw_sender_model_seconds, 3)
                    if raw_sender_model_seconds is not None
                    else None
                )
                phase["receiver_model_seconds"] = (
                    round(raw_receiver_model_seconds, 3)
                    if raw_receiver_model_seconds is not None
                    else None
                )
                measured_model_phases = [
                    value
                    for value in (
                        phase["sender_model_seconds"],
                        phase["receiver_model_seconds"],
                    )
                    if value is not None
                ]
                phase["model_seconds"] = max(measured_model_phases, default=None)
                phase["last_receipt_unicode_verified"] = LAST_RECEIPT in last_text
                raw_sender_closed_seconds = (
                    sender_terminal_closed_at - model_start
                    if sender_terminal_closed_at is not None
                    else None
                )
                phase["sender_terminal_closed_seconds"] = (
                    round(raw_sender_closed_seconds, 3)
                    if raw_sender_closed_seconds is not None
                    else None
                )
                phase["sender_terminal_closed_within_75_seconds"] = _within_budget(
                    raw_sender_closed_seconds, MODEL_PHASE_SECONDS
                )
                phase["durable_turn_completed"] = sender_completed_at is not None
                phase["receiver_turn_completed_once"] = (
                    receiver_completed_at is not None
                    and final_receiver.get("status") in {"idle", "completed"}
                    and type(final_receiver.get("turn_sequence")) is int
                    and type(final_receiver.get("turn_completed")) is int
                    and final_receiver.get("turn_sequence") == receiver_sequence_before + 1
                    and final_receiver.get("turn_completed") == receiver_sequence_before + 1
                )
                receiver_output_body = None
                receiver_output_http_status = None
                receiver_output_in_time = False
                receiver_output_parse_failed = False
                if receiver_completed_at is not None and receiver_started_at is not None:
                    try:
                        receiver_output, receiver_output_in_time = _bounded_phase_get(
                            requests,
                            f"{server.url}/terminals/{receiver_id}/output",
                            deadline=receiver_started_at + MODEL_PHASE_SECONDS,
                            params={"mode": "last"},
                        )
                        receiver_output_http_status = (
                            receiver_output.status_code if receiver_output is not None else None
                        )
                        if (
                            receiver_output_in_time
                            and receiver_output is not None
                            and receiver_output.status_code == 200
                        ):
                            try:
                                receiver_output_body = receiver_output.json()
                            except Exception:
                                receiver_output_parse_failed = True
                                raise
                            receiver_output_text = (
                                receiver_output_body.get("output", "")
                                if isinstance(receiver_output_body, dict)
                                else ""
                            )
                            phase["receiver_ack_exact"] = _receiver_ack_exact(receiver_output_text)
                    except Exception:
                        # Keep failures private and report only the fixed ACK gate.
                        pass
                phase["receiver_last_observation"] = _receiver_last_observation(
                    receiver_output_http_status,
                    receiver_output_body,
                    receiver_output_in_time,
                    parse_failed=receiver_output_parse_failed,
                )
                receiver_failure_code = _receiver_phase_failure_code(phase)
                _raise_receiver_phase_failure_if_needed(
                    phase=phase,
                    failure_code=receiver_failure_code,
                    raw_dir=raw_dir,
                    phase_ordinal=index + 1,
                    body=receiver_output_body,
                )
                _write_private(raw_dir / f"phase-{index + 1}-screen.private", last_viewport)
                _write_private(
                    raw_dir / f"phase-{index + 1}-pg.private",
                    json.dumps(last_rows, default=str, ensure_ascii=False),
                )
                if (
                    phase["sender_model_within_75_seconds"] is not True
                    or phase["receiver_model_within_75_seconds"] is not True
                ):
                    raise GateError(_model_phase_failure_code(phase))
                api_rows_now = _api_rows(requests, server.url, receiver_id)
                agui_matches = capture.take_until(time.monotonic() + 1.0)
                capture.stop()
                capture_thread_dead = _capture_threads_confirmed_dead([capture])
                phase["agui_capture_stopped_cleanly"] = _capture_stopped_cleanly(capture)
                phase["agui_redacted_one_notification"] = agui_matches == [True]
                if capture_thread_dead:
                    captures.remove(capture)
                if not capture_thread_dead:
                    raise GateError("agui_capture_thread_cleanup_unconfirmed")
                if not phase["agui_capture_stopped_cleanly"]:
                    raise GateError("agui_capture_stream_health_failed")
                phase["one_exact_pg_api_message"] = _phase_contract(
                    expected_message=message,
                    sender_id=sender_id,
                    receiver_id=receiver_id,
                    pg_rows=last_rows,
                    api_rows=api_rows_now,
                    phase_index=index,
                    last_text=last_text,
                    terminal=final_terminal,
                    turn_sequence=sequence,
                    agui_matches=agui_matches,
                )
                if not phase["one_exact_pg_api_message"]:
                    raise GateError("message_phase_acceptance_mismatch")
                if not phase["receiver_turn_completed_once"]:
                    raise GateError("receiver_turn_not_completed_once")

                if index == 0:
                    pg_row = last_rows[0]
                    api_row = api_rows_now[0]
                    first_row_identity = {
                        key: pg_row[key]
                        for key in ("id", "sender_id", "receiver_id", "message", "created_at")
                    }
                    first_api_identity = {
                        key: api_row[key]
                        for key in ("id", "sender_id", "receiver_id", "message", "created_at")
                    }
                    metadata_before_restart = _api_metadata_snapshot(
                        requests, server.url, terminals, group, case.case_id
                    )
                    lifecycle_before_restart = _api_lifecycle_snapshot(
                        requests,
                        server.url,
                        terminals,
                        diagnostic=receipt.setdefault("lifecycle_observations", {}).setdefault(
                            "before_restart", {}
                        ),
                    )
                    (
                        durable_receipts_before_restart,
                        provider_processes_before_restart,
                    ) = _capture_pre_restart_snapshots(
                        receipt=receipt,
                        connect=connect,
                        target=targets.main,
                        terminals=terminals,
                        journey=journey,
                        socket_path=socket_path,
                        sid=sid,
                    )
                    if not all(provider_processes_before_restart.values()):
                        raise GateError("native_process_identity_missing_before_restart")
                    receipt["native_process_pids_before_restart"] = {
                        role: sorted(item.pid for item in identities)
                        for role, identities in provider_processes_before_restart.items()
                    }
                    receipt["terminal_metadata_before_restart"] = metadata_before_restart
                    receipt["provider_roots_before_restart"] = root_identity_before
                    _admit(receipt, "api_restart")
                    old_api_process, old_api_identity = _capture_api_process(server, journey)
                    if old_api_identity != api_identity_a:
                        raise GateError("api_a_identity_changed_before_restart")
                    server_a = server
                    api_a_record = api_servers[0]
                    try:
                        server_a.stop()
                    except Exception:
                        api_a_record["stop_error"] = True
                        failures.add("api_cleanup_failed")
                        raise GateError("api_a_stop_failed") from None
                    if journey._process_identity_alive(old_api_identity):
                        raise GateError("api_a_process_survived_stop")
                    api_a_record["stop_confirmed"] = True
                    receipt["api_a_cleanup"] = True
                    api_a_cleanup = True
                    server = None
                    api_b_launch_attempted = True
                    server = start_api()
                    api_servers.append(
                        {
                            "role": "api_b",
                            "server": server,
                            "identity": None,
                            "stop_confirmed": False,
                            "start_returned": True,
                        }
                    )
                    api_b_started = True
                    api_process, api_identity_after = _capture_api_process(server, journey)
                    api_identity_b = api_identity_after
                    if PINNED_PYTHON is None or not _api_worker_invocation_matches(
                        api_process, server, PINNED_PYTHON
                    ):
                        raise GateError("production_api_worker_invocation_mismatch")
                    receipt["production_api_worker_invocation_verified"] = True
                    api_servers[-1]["identity"] = api_identity_after
                    receipt["api_b_url"] = server.url
                    if api_identity_after.pid == old_api_identity.pid:
                        raise GateError("api_restart_process_identity_not_changed")
                    receipt["api_b_pid"] = api_identity_after.pid
                    receipt["api_b_process_start"] = api_identity_after.start_time
                    receipt["api_restart_process_changed"] = True
                    after_fingerprint = message_live._target_fingerprint(
                        server.database_targets, database_target_type=type(targets.main)
                    )
                    receipt["postgres_target_identity_stable"] = (
                        after_fingerprint == target_fingerprint_before_restart
                    )
                    if not receipt["postgres_target_identity_stable"]:
                        raise GateError("postgres_target_changed_on_api_restart")
                    api_rows_after_restart = _api_rows(requests, server.url, receiver_id)
                    pg_rows_after_restart = _pg_rows(connect, targets.main, receiver_id)
                    if (
                        len(pg_rows_after_restart) != 1
                        or len(api_rows_after_restart) != 1
                        or any(
                            pg_rows_after_restart[0].get(key) != value
                            for key, value in first_row_identity.items()
                        )
                        or any(
                            api_rows_after_restart[0].get(key) != value
                            for key, value in first_api_identity.items()
                        )
                    ):
                        raise GateError("persisted_first_message_identity_changed")
                    metadata_after_restart = _api_metadata_snapshot(
                        requests, server.url, terminals, group, case.case_id
                    )
                    if metadata_after_restart != metadata_before_restart:
                        raise GateError("terminal_metadata_changed_on_api_restart")
                    _wait_restart_readiness(
                        requests, server.url, terminals, receipt.setdefault("restart_readiness", {})
                    )
                    lifecycle_after_restart = _api_lifecycle_snapshot(
                        requests,
                        server.url,
                        terminals,
                        diagnostic=receipt.setdefault("lifecycle_observations", {}).setdefault(
                            "after_restart", {}
                        ),
                    )
                    durable_receipts_after_restart = _durable_receipt_snapshot(
                        connect, targets.main, terminals
                    )
                    if durable_receipts_after_restart != durable_receipts_before_restart:
                        raise GateError("terminal_verified_receipts_changed_on_api_restart")
                    receipt["terminal_verified_receipts_stable_on_api_restart"] = True
                    provider_processes_after_restart = {
                        role: journey._terminal_tagged_processes(
                            data["id"], socket_path, server_identity=sid
                        )
                        for role, data in terminals.items()
                    }
                    if provider_processes_after_restart != provider_processes_before_restart:
                        raise GateError("native_process_identity_changed_on_api_restart")
                    if any(
                        not journey._process_identity_alive(item)
                        for items in provider_processes_after_restart.values()
                        for item in items
                    ):
                        raise GateError("native_process_not_alive_after_api_restart")
                    roots_after_restart = _native_config_roots(_provider_roots(cao_home))
                    if roots_after_restart != root_identity_before:
                        raise GateError("provider_root_identity_changed_on_api_restart")
                    receipt["terminal_metadata_stable_on_api_restart"] = True
                    receipt["terminal_lifecycle_stable_on_api_restart"] = True
                    receipt["native_process_identity_stable_on_api_restart"] = True
                    receipt["provider_root_identity_stable_on_api_restart"] = True
                    receipt["first_pg_api_row_stable_on_restart"] = True
                    receipt["native_process_pids_after_restart"] = {
                        role: sorted(item.pid for item in identities)
                        for role, identities in provider_processes_after_restart.items()
                    }
                    receipt["terminal_metadata_after_restart"] = metadata_after_restart
                    receipt["api_worker_restarts"] = 1

            final_pg_rows = _pg_rows(connect, targets.main, receiver_id)
            final_api_rows = _api_rows(requests, server.url, receiver_id)
            receipt["two_exact_pg_api_messages"] = _persisted_pair_contract(
                final_pg_rows,
                final_api_rows,
                sender_id=sender_id,
                receiver_id=receiver_id,
            )
            if not receipt["two_exact_pg_api_messages"]:
                raise GateError("final_two_message_pair_mismatch")
            receipt["both_message_phases_passed"] = len(receipt["phases"]) == 2
            if not receipt["both_message_phases_passed"]:
                raise GateError("both_message_phases_required")

        for role, data in terminals.items():
            owned_processes |= journey._owned_process_snapshot_for_cleanup(
                sid, data["id"], socket_path
            )
            full = requests.get(
                f"{server.url}/terminals/{data['id']}/output",
                params={"mode": "full"},
                timeout=10,
            )
            if full.status_code == 200:
                _write_private(raw_dir / f"full-{role}.private", full.text)

        for role, data in terminals.items():
            exit_response = requests.post(f"{server.url}/terminals/{data['id']}/exit", timeout=5)
            receipt[f"{role}_graceful_exit_http_status"] = exit_response.status_code
            if exit_response.status_code != 200:
                raise GateError("graceful_terminal_exit_failed")
        graceful_started = time.monotonic()
        observation_paths = {
            role: base_observation.with_name(base_observation.name + "-" + data["id"])
            for role, data in terminals.items()
        }
        seal_deadline = graceful_started + OBSERVER_GRACE_SECONDS
        seal_observed_at = None
        while True:
            _cancellation_checkpoint()
            if time.monotonic() > seal_deadline:
                break
            if all(
                path.with_name(path.name + ".complete").is_file()
                for path in observation_paths.values()
            ):
                seal_observed_at = time.monotonic()
                break
            remaining = seal_deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.1, remaining))
        seal_finished_at = seal_observed_at if seal_observed_at is not None else time.monotonic()
        receipt["observer_seal_seconds"] = round(seal_finished_at - graceful_started, 3)
        receipt["observer_seal_within_10_seconds"] = (
            seal_observed_at is not None
            and _observer_seal_observed_within_deadline(
                graceful_started, seal_observed_at, seal_deadline
            )
        )
        if receipt["observer_seal_within_10_seconds"] is not True:
            raise GateError("graceful_observer_seal_not_observed_within_10_seconds")
        receipt["graceful_observer_seals_observed"] = True
        spec = importlib.util.spec_from_file_location("t104_v2_private_observer", observer_path)
        if spec is None or spec.loader is None:
            raise GateError("observer_reader_unavailable")
        observer_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(observer_module)
        native_observations = {
            role: observer_module.read_protocol_observations(
                observation_paths[role],
                expected_nonce=nonce,
                expected_message_bodies=tuple(message for _phase_name, message in PHASES),
                **_case_observer_options(case.case_id),
            )
            for role in terminals
        }
        receipt["native_observations"] = native_observations
        if case.case_id == COORDINATION_CASE_ID:
            receipt["coordination_read_success_observed"] = _coordination_result_contract(
                native_observations.get("sender")
            )
        receipt["native_catalog_and_calls_exact"] = _observer_contract(
            native_observations, case.case_id
        )
        if not receipt["native_catalog_and_calls_exact"]:
            raise GateError("native_catalog_or_call_contract_mismatch")

        role_expected_mcp_tools = {
            "sender": tuple("cao-mcp-server_" + tool for tool in case.sender_catalog),
            "receiver": (),
        }
        offer_observations = {
            role: _read_http_tool_offer_observation(
                offer_observer,
                offer_directories[role],
                expected_nonce=offer_nonces[role],
                tracked_tool_names=_case_http_offer_names(case.case_id),
            )
            for role in ("sender", "receiver")
        }
        receipt["native_model_offer_scope"] = "selected_model_primary_requests_only"
        receipt["opencode_http_tool_offer_observations"] = offer_observations
        receipt["opencode_native_builtin_offer_counts_by_role"] = {
            role: {
                name: offer_observations[role]["tool_name_counts"][name]
                for name in OPTIONAL_OPENCODE_BUILTIN_TOOL_NAMES
            }
            for role in ("sender", "receiver")
        }
        for role in ("sender", "receiver"):
            contract_key = (
                (
                    _case_receipt_key("sender_model_request_full_eight_cao_mcp_tools", case.case_id)
                    if case.requires_human_capability
                    else "sender_model_request_exact_two_cao_mcp_tools"
                )
                if role == "sender"
                else "receiver_model_request_zero_cao_mcp_tools"
            )
            receipt[contract_key] = (
                _task_query_passive_offer_contract(
                    offer_observations[role], expected_nonce=offer_nonces[role]
                )
                if case.case_id == TASK_QUERY_CASE_ID and role == "receiver"
                else _http_tool_offer_contract(
                    offer_observations[role],
                    expected_nonce=offer_nonces[role],
                    expected_mcp_tool_names=role_expected_mcp_tools[role],
                    tracked_tool_names=_case_http_offer_names(case.case_id),
                    case_id=case.case_id,
                )
            )
        receipt["native_model_request_cao_mcp_surfaces_exact"] = (
            receipt[
                (
                    _case_receipt_key("sender_model_request_full_eight_cao_mcp_tools", case.case_id)
                    if case.requires_human_capability
                    else "sender_model_request_exact_two_cao_mcp_tools"
                )
            ]
            and receipt["receiver_model_request_zero_cao_mcp_tools"]
        )
        if not receipt["native_model_request_cao_mcp_surfaces_exact"]:
            raise GateError("native_model_request_tool_offer_mismatch")
        receipt["native_builtin_offer_names_within_source_approved_optional_contract"] = True
        receipt["receiver_native_builtin_execution_observation"] = "not_observed"

        roots_before_delete = _native_config_roots(_provider_roots(cao_home))
        caps_before_delete = _capability_files(cao_home)
        if (
            roots_before_delete != root_identity_before
            or (
                case.requires_human_capability
                and (
                    sender_capability_path is None or caps_before_delete != [sender_capability_path]
                )
            )
            or (not case.requires_human_capability and bool(caps_before_delete))
            or not _provider_config_contract(
                cao_home,
                terminals["sender"],
                role="sender",
                capability_path=sender_capability_path,
                case_id=case.case_id,
            )
            or not _provider_config_contract(
                cao_home,
                terminals["receiver"],
                role="receiver",
                capability_path=None,
                case_id=case.case_id,
            )
        ):
            raise GateError("native_roots_or_capabilities_changed_before_delete")
        receipt["two_native_provider_roots_before_delete"] = True
        if case.requires_human_capability:
            receipt["expected_capability_set_before_delete"] = True
        else:
            receipt["capability_files_before_delete"] = len(caps_before_delete)
            if receipt["capability_files_before_delete"] != 0:
                raise GateError("generic_message_capability_inventory_nonzero")
            receipt["capability_inventory_zero_before_delete"] = True

        deletion_attempted = True
        delete_response = requests.delete(f"{server.url}/sessions/{session_name}", timeout=60)
        _cancellation_checkpoint()
        receipt["session_delete_http_status"] = delete_response.status_code
        if delete_response.status_code != 200:
            raise GateError("session_delete_http_200_required")
        # Measure native roots and capability files at the exact HTTP 200
        # boundary, before stopping the API, PostgreSQL scope, or tmux.
        post_delete_roots = len(_provider_roots(cao_home))
        post_delete_caps = len(_capability_files(cao_home))
        receipt["native_provider_roots_immediately_after_delete"] = post_delete_roots
        receipt["capability_files_immediately_after_delete"] = post_delete_caps
        if post_delete_roots != 0 or post_delete_caps != 0:
            raise GateError("native_roots_or_capabilities_remain_after_delete")
        if not case.requires_human_capability:
            receipt["capability_inventory_zero_after_delete"] = True
    except BaseException as error:
        _SIGTERM_CLEANUP_ACTIVE = True
        _record_pre_restart_snapshot_exception(receipt, error)
        if isinstance(error, _ControlledSigterm):
            receipt["sender_failure_diagnostic"] = {
                "status": "skipped_for_sigterm_cleanup",
                "acceptance_input_changed": False,
            }
            receipt["receiver_failure_diagnostic"] = {
                "schema": "spec025.t104.receiver-failure-diagnostic.v1",
                "status": "skipped_for_sigterm_cleanup",
                "within_10_seconds": False,
            }
        else:
            diagnostic_locals = locals()
            diagnostic_deadline = time.monotonic() + 9.0
            try:
                receipt["sender_failure_diagnostic"] = _capture_sender_native_failure_diagnostic(
                    diagnostic_locals, diagnostic_deadline
                )
            except BaseException:
                receipt["sender_failure_diagnostic"] = {
                    "status": "diagnostic_failed_closed",
                    "acceptance_input_changed": False,
                }
            try:
                receipt["receiver_failure_diagnostic"] = _capture_receiver_failure_diagnostic(
                    receiver=diagnostic_locals.get("terminals", {}).get("receiver"),
                    server=diagnostic_locals.get("server"),
                    requests_module=diagnostic_locals.get("requests"),
                    journey=diagnostic_locals.get("journey"),
                    socket_path=diagnostic_locals.get("socket_path"),
                    socket_id=diagnostic_locals.get("socket_id"),
                    sid=diagnostic_locals.get("sid"),
                    session_name=diagnostic_locals.get("session_name"),
                    cao_home=diagnostic_locals.get("cao_home"),
                    raw_dir=diagnostic_locals.get("raw_dir"),
                    database_target=getattr(diagnostic_locals.get("targets"), "main", None),
                    shared_deadline=diagnostic_deadline,
                    native_text_context=diagnostic_locals,
                )
            except BaseException:
                receipt["receiver_failure_diagnostic"] = {
                    "schema": "spec025.t104.receiver-failure-diagnostic.v1",
                    "status": "diagnostic_failed_closed",
                    "within_10_seconds": False,
                }
            receipt["failure_diagnostics_within_shared_budget"] = (
                time.monotonic() <= diagnostic_deadline
            )
        acceptance_failure_stage = _message_live_failure_stage(error)
        if acceptance_failure_stage is not None:
            receipt["message_live_acceptance_failure_stage"] = acceptance_failure_stage
        receipt["error_type"] = type(error).__name__
        receipt["error_code"] = (
            error.code
            if isinstance(error, GateError)
            else (
                "sigterm_requested"
                if isinstance(error, _ControlledSigterm)
                else "private_runtime_diagnostic_error"
            )
        )
    finally:
        _SIGTERM_CLEANUP_ACTIVE = True
        for capture in list(captures):
            try:
                capture.stop()
            except Exception:
                failures.add("agui_capture_cleanup_failed")
            try:
                capture_thread_dead = capture._thread.is_alive() is False
            except Exception:
                capture_thread_dead = False
            if capture_thread_dead:
                captures.remove(capture)
            else:
                failures.add("agui_capture_cleanup_failed")
        agui_capture_threads_confirmed_dead = _capture_threads_confirmed_dead(capture_history)
        if not agui_capture_threads_confirmed_dead:
            failures.add("agui_capture_cleanup_failed")
        receipt["agui_capture_threads_confirmed_dead_before_postgres_teardown"] = (
            agui_capture_threads_confirmed_dead
        )

        cleanup_server = server
        if cleanup_server is None:
            for record in reversed(api_servers):
                identity = record.get("identity")
                try:
                    if identity is None or journey._process_identity_alive(identity) is not False:
                        cleanup_server = record["server"]
                        break
                except Exception:
                    cleanup_server = record["server"]
                    break
        if _session_cleanup_due(
            session_creation_attempted, cleanup_session_name, deletion_attempted
        ):
            deletion_attempted = True
            if cleanup_server is None:
                failures.add("session_delete_failed")
            else:
                try:
                    delete_response = __import__("requests").delete(
                        f"{cleanup_server.url}/sessions/{cleanup_session_name}", timeout=60
                    )
                    receipt["cleanup_session_delete_http_status"] = delete_response.status_code
                    if delete_response.status_code == 200:
                        cleanup_cao_home = root / "home/.aws/cli-agent-orchestrator"
                        post_delete_roots = len(_provider_roots(cleanup_cao_home))
                        post_delete_caps = len(_capability_files(cleanup_cao_home))
                        receipt["native_provider_roots_immediately_after_delete"] = (
                            post_delete_roots
                        )
                        receipt["capability_files_immediately_after_delete"] = post_delete_caps
                    else:
                        failures.add("session_delete_failed")
                except Exception:
                    failures.add("session_delete_failed")

        attempted_api_roles = set()
        if api_a_launch_attempted:
            attempted_api_roles.add("api_a")
        if api_b_launch_attempted:
            attempted_api_roles.add("api_b")
        for record in api_servers:
            identity = record.get("identity")
            alive = True
            try:
                if identity is not None:
                    alive = journey._process_identity_alive(identity) is not False
            except Exception:
                alive = True
            if alive:
                try:
                    record["server"].stop()
                except Exception:
                    failures.add("api_cleanup_failed")
                try:
                    alive = (
                        identity is None or journey._process_identity_alive(identity) is not False
                    )
                except Exception:
                    alive = True
            record["stop_confirmed"] = identity is not None and alive is False
            if not record["stop_confirmed"]:
                failures.add("api_cleanup_failed")
        api_processes_confirmed_dead = _api_launches_confirmed_dead(
            api_servers,
            attempted_roles=attempted_api_roles,
            journey=journey,
        )
        api_record_by_role = {record["role"]: record for record in api_servers}
        api_a_cleanup = bool(api_record_by_role.get("api_a", {}).get("stop_confirmed") is True)
        api_b_cleanup = bool(api_record_by_role.get("api_b", {}).get("stop_confirmed") is True)
        receipt["api_a_launch_attempted"] = api_a_launch_attempted
        receipt["api_b_launch_attempted"] = api_b_launch_attempted
        # This aggregate describes cleanup of processes that were actually
        # attempted. Keep the per-role flags false for an unattempted role; the
        # success predicate still requires both roles and both message phases.
        api_cleanup = api_processes_confirmed_dead
        receipt["api_processes_confirmed_dead_before_postgres_teardown"] = (
            api_processes_confirmed_dead
        )
        receipt["api_launches_tracked_through_confirmed_death"] = api_processes_confirmed_dead
        receipt["api_b_cleanup"] = api_b_cleanup
        receipt["api_a_cleanup"] = api_a_cleanup
        receipt["api_cleanup"] = api_cleanup

        if tmux_launch_attempted:
            owned_process_snapshot_refresh_complete = bool(
                journey is not None
                and sid is not None
                and socket_path is not None
                and bool(terminals)
            )
            if owned_process_snapshot_refresh_complete:
                for data in terminals.values():
                    terminal_id = data.get("id") if isinstance(data, dict) else None
                    if not _safe_terminal_identity(terminal_id):
                        owned_process_snapshot_refresh_complete = False
                        failures.add("owned_process_snapshot_refresh_failed")
                        continue
                    try:
                        fresh_processes = journey._owned_process_snapshot_for_cleanup(
                            sid, terminal_id, socket_path
                        )
                        if not isinstance(fresh_processes, set):
                            raise RuntimeError("owned_process_snapshot_shape_invalid")
                        owned_processes.update(fresh_processes)
                    except BaseException:
                        owned_process_snapshot_refresh_complete = False
                        failures.add("owned_process_snapshot_refresh_failed")
            else:
                failures.add("owned_process_snapshot_refresh_failed")
        else:
            owned_process_snapshot_refresh_complete = not owned_processes
            if not owned_process_snapshot_refresh_complete:
                failures.add("owned_process_snapshot_refresh_failed")
        receipt["owned_process_snapshot_refresh_complete"] = owned_process_snapshot_refresh_complete

        if (
            tmux_launch_attempted
            and tmux is not None
            and sid is not None
            and socket_id is not None
            and socket_path is not None
        ):
            try:
                tmux_cleanup = journey._cleanup_private_tmux_server(
                    tmux,
                    tmux_tmpdir,
                    failures,
                    terminal_id=next(
                        (data.get("id", "") for data in reversed(list(terminals.values()))), ""
                    ),
                    server_identity=sid,
                    owned_processes=owned_processes,
                    socket_identity=socket_id,
                )
            except Exception:
                failures.add("tmux_cleanup_failed")
        elif not tmux_launch_attempted and not owned_processes:
            tmux_cleanup = True
        tmux_processes_confirmed_dead = (
            tmux_cleanup and owned_process_snapshot_refresh_complete
            if tmux_launch_attempted
            else not owned_processes and owned_process_snapshot_refresh_complete
        )
        receipt["tmux_processes_confirmed_dead_before_postgres_teardown"] = (
            tmux_processes_confirmed_dead
        )

        cleanup_locals = locals()
        if (
            receipt.get("error_code") is not None
            and type(cleanup_locals.get("phase")) is dict
            and not _SIGTERM_PENDING
        ):
            terminal_map = cleanup_locals.get("terminals")
            sender_data = terminal_map.get("sender") if type(terminal_map) is dict else None
            sender_id = sender_data.get("id") if type(sender_data) is dict else None
            base_observation_path = cleanup_locals.get("base_observation")
            if isinstance(base_observation_path, Path) and _safe_terminal_identity(sender_id):
                sender_observation_path = base_observation_path.with_name(
                    base_observation_path.name + "-" + sender_id
                )
            else:
                sender_observation_path = None
            receipt["sender_message_argument_diagnostic"] = (
                _capture_sender_message_argument_diagnostic_safely(
                    observer_source_path=REPO / "test/live_provider/opencode_mcp_observer.py",
                    observation_path=sender_observation_path,
                    raw_dir=cleanup_locals.get("raw_dir"),
                    expected_sender_id=sender_id,
                    expected_nonce=cleanup_locals.get("nonce"),
                    case_id=getattr(cleanup_locals.get("case"), "case_id", None),
                    writers_dead=(
                        api_processes_confirmed_dead is True
                        and tmux_processes_confirmed_dead is True
                    ),
                )
            )

        if prompt_context is not None:
            receipt["sender_http_prompt_diagnostic"] = _capture_http_prompt_diagnostic_safely(
                context=prompt_context,
                root=root,
                raw_dir=cleanup_locals.get("raw_dir"),
                writers_dead=(
                    api_processes_confirmed_dead is True and tmux_processes_confirmed_dead is True
                ),
            )

        if response_context is not None:
            receipt["sender_http_response_argument_diagnostic"] = (
                _capture_http_response_argument_diagnostic_safely(
                    context=response_context,
                    root=root,
                    raw_dir=cleanup_locals.get("raw_dir"),
                    writers_dead=(
                        api_processes_confirmed_dead is True
                        and tmux_processes_confirmed_dead is True
                    ),
                )
            )

        if receipt_stage_context is not None:
            receipt["receipt_verification_diagnostic"] = _capture_receipt_stage_diagnostic_safely(
                observer=receipt_stage_observer,
                raw_dir=receipt_stage_context["raw_dir"],
                writers_dead=api_processes_confirmed_dead is True,
            )

        if pg_context is not None:
            if _postgres_teardown_allowed(
                api_processes_dead=api_processes_confirmed_dead,
                capture_threads_dead=agui_capture_threads_confirmed_dead,
                tmux_processes_dead=tmux_processes_confirmed_dead,
            ):
                try:
                    _exit_guarded_postgres_context(pg_context, postgres_fixtures)
                    postgres_cleanup = True
                    receipt["postgres_cleanup_deferred"] = False
                except Exception:
                    failures.add("postgres_cleanup_failed")
            else:
                receipt["postgres_cleanup_deferred"] = True
                _DEFERRED_POSTGRES_CONTEXTS.append(pg_context)
                failures.add("postgres_cleanup_deferred_service_unconfirmed")
        else:
            # No fixture context was constructed, so this run created no PG state.
            postgres_cleanup = True
            receipt["postgres_cleanup_deferred"] = False
        receipt["tmux_cleanup"] = tmux_cleanup
        receipt["postgres_cleanup"] = postgres_cleanup
        receipt["cleanup_failures"] = sorted(failures)
        with contextlib.suppress(Exception):
            os.environ.clear()
            os.environ.update(original_run_environment)
        try:
            operator_after = _operator_fingerprints(auth_paths)
            receipt["operator_auth_unchanged"] = operator_after == operator_before
        except Exception:
            receipt["operator_auth_unchanged"] = False
        try:
            source_after = _source_fingerprints()
            receipt["source_unchanged"] = source_after == source_before and _source_tree_clean()
        except Exception:
            receipt["source_unchanged"] = False
        try:
            binary_after = _sha256(PINNED_OPENCODE)
            receipt["binary_unchanged"] = binary_after == binary_before
        except Exception:
            receipt["binary_unchanged"] = False
        try:
            runner_after = _sha256(Path(__file__))
            receipt["runner_unchanged"] = runner_after == runner_before
        except Exception:
            receipt["runner_unchanged"] = False
        try:
            receipt["python_interpreter_unchanged"] = _sha256(python_real) == python_before
        except Exception:
            receipt["python_interpreter_unchanged"] = False
        receipt["elapsed_seconds"] = round(time.monotonic() - start, 3)
        receipt["tmux_launch_attempted"] = tmux_launch_attempted
        receipt["postgres_context_entered"] = pg_entered
        receipt["postgres_context_present"] = pg_context is not None
        receipt["private_v2_credential_snapshot_created"] = credential_snapshot_created
        receipt["session_creation_attempted"] = session_creation_attempted
        receipt["session_deletion_attempted"] = deletion_attempted
        receipt["api_server_record_count"] = len(api_servers)
        receipt["capture_started_count"] = len(capture_history)
        receipt["owned_process_identity_count"] = len(owned_processes)
        receipt["early_failure_cleanup_observation_passed"] = (
            _record_early_failure_cleanup_observation(
                receipt,
                root / "home/.aws/cli-agent-orchestrator",
                root,
            )
        )
        safe_to_remove = _private_root_cleanup_safe(receipt)
        try:
            if safe_to_remove:
                removed = journey.common._remove_private_run_root(
                    root, process_stopped=api_cleanup and tmux_cleanup, failures=failures
                )
            else:
                failures.add("private_files_preserved")
                removed = False
            receipt["private_state_removed"] = bool(removed and safe_to_remove)
        except Exception:
            receipt["private_state_removed"] = False
        if receipt["private_state_removed"]:
            receipt["retained_private_state"] = {
                "status": "removed",
                "credential_snapshot": "removed",
            }
            receipt["credentials_cleanup_deferred"] = False
            _ACTIVE_PRIVATE_RUN_ROOT = None
        else:
            credential_snapshot_status = _credential_snapshot_status(
                root, snapshot_created=credential_snapshot_created
            )
            receipt["credentials_cleanup_deferred"] = credential_snapshot_status in {
                "retained",
                "unknown",
            }
            receipt["retained_private_state"] = {
                "status": "retained",
                "credential_snapshot": credential_snapshot_status,
            }
            if receipt["credentials_cleanup_deferred"]:
                failures.add("credential_snapshot_cleanup_deferred")
        if _SIGTERM_PENDING:
            receipt["actual_cancel_observed"] = True
            failures.add("sigterm_requested")
        receipt["cleanup_failures"] = sorted(failures)

    receipt["actual_cancel_observed"] = _SIGTERM_PENDING is True
    if receipt["actual_cancel_observed"]:
        failures.add("sigterm_requested")
        receipt["cleanup_failures"] = sorted(failures)
    receipt["success"] = _acceptance_contract(receipt, case.case_id)
    receipt["utc_finished"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return receipt


def _run(
    model: str,
    *,
    go: bool,
    expected_head: str,
    manifest_path: Path,
    manifest_sha256: str,
    review_digest: str,
    case_id: Any = DEFAULT_CASE_ID,
) -> dict[str, Any]:
    case = _case_definition(case_id)
    if RUN_ENABLED is not True:
        raise GateError("private_execution_copy_required")
    if not _private_execution_copy_contract():
        raise GateError("private_execution_copy_derivation_mismatch")
    try:
        with _scoped_sigterm_handler():
            receipt = _run_enabled(
                model,
                go=go,
                expected_head=expected_head,
                manifest_path=manifest_path,
                manifest_sha256=manifest_sha256,
                review_digest=review_digest,
                case_id=case.case_id,
            )
    except _ControlledSigterm:
        global _ACTIVE_PRIVATE_RUN_ROOT
        root = _ACTIVE_PRIVATE_RUN_ROOT
        if root is not None:
            with contextlib.suppress(OSError):
                root.rmdir()
                _ACTIVE_PRIVATE_RUN_ROOT = None
        raise GateError("sigterm_before_owned_cleanup_scope") from None
    if _SIGTERM_PENDING:
        receipt["actual_cancel_observed"] = True
        receipt["error_type"] = "SIGTERM"
        receipt["error_code"] = "sigterm_requested"
        failures = set(receipt.get("cleanup_failures", []))
        failures.add("sigterm_requested")
        receipt["cleanup_failures"] = sorted(failures)
    receipt["success"] = _acceptance_contract(receipt, case.case_id)
    _write_private(RECEIPT, json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "success": receipt["success"],
                "receipt": str(RECEIPT),
                "error_code": receipt.get("error_code"),
                "cleanup_failures": receipt["cleanup_failures"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None)
    parser.add_argument("--go", action="store_true")
    parser.add_argument(
        "--derive-execution-copy",
        type=Path,
        default=None,
        help="write a private copy differing only by RUN_ENABLED=False to True; does not run it",
    )
    parser.add_argument("--expected-head", default=None)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--manifest-sha256", default=None)
    parser.add_argument("--review-digest", default=None)
    parser.add_argument(
        "--case",
        choices=tuple(case.case_id for case in CASE_DEFINITIONS),
        default=DEFAULT_CASE_ID,
        help="source-defined case; defaults to the existing full8 collaboration case",
    )
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        if (
            args.go
            or args.model is not None
            or args.derive_execution_copy is not None
            or any(
                value is not None
                for value in (
                    args.expected_head,
                    args.manifest,
                    args.manifest_sha256,
                    args.review_digest,
                )
            )
        ):
            parser.error("--self-check cannot be combined with execution gates")
        check = _self_check(args.case)
        print(json.dumps(check, sort_keys=True))
        return 0 if check["success"] else 1
    if args.derive_execution_copy is not None:
        if (
            args.case != DEFAULT_CASE_ID
            or args.go
            or args.model is not None
            or any(
                value is not None
                for value in (
                    args.expected_head,
                    args.manifest,
                    args.manifest_sha256,
                    args.review_digest,
                )
            )
        ):
            parser.error("--derive-execution-copy cannot be combined with execution gates")
        try:
            result = _write_private_execution_copy(args.derive_execution_copy)
        except GateError as error:
            print(json.dumps({"success": False, "error_code": error.code}, sort_keys=True))
            return 2
        print(json.dumps({"success": True, **result}, sort_keys=True))
        return 0
    if args.go is not True:
        print(json.dumps({"success": False, "error_code": "explicit_go_required"}, sort_keys=True))
        return 2
    if (
        args.model != MODEL
        or args.expected_head is None
        or args.manifest is None
        or args.manifest_sha256 is None
        or args.review_digest is None
    ):
        print(
            json.dumps(
                {"success": False, "error_code": "exact_model_source_manifest_and_review_required"},
                sort_keys=True,
            )
        )
        return 2
    if RUN_ENABLED is not True:
        print(
            json.dumps(
                {"success": False, "error_code": "private_execution_copy_required"},
                sort_keys=True,
            )
        )
        return 2
    try:
        receipt = _run(
            args.model,
            go=args.go,
            expected_head=args.expected_head,
            manifest_path=args.manifest,
            manifest_sha256=args.manifest_sha256,
            review_digest=args.review_digest,
            case_id=args.case,
        )
    except GateError as error:
        print(json.dumps({"success": False, "error_code": error.code}, sort_keys=True))
        return 1
    return 0 if receipt.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
