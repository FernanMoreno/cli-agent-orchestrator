"""Unregistered Bubblewrap backend for one bound static Work executable.

Admission stays fail-closed until the canonical Bubblewrap binary has the
approved version and digest. When that host gate is met, the process policy
allows only the single immutable ELF mapping enforced by the composed launcher.
The backend remains unregistered until T097/C08 acceptance.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import stat
import subprocess
import threading
import weakref
from pathlib import Path
from typing import Callable, Optional

from cli_agent_orchestrator.backends.base import (
    ProcessRestrictionContract,
    UnsupportedWorkEnforcement,
)
from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
from cli_agent_orchestrator.clients.tmux import TmuxClient
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_contract import EffectiveWorkContractV2
from cli_agent_orchestrator.services.work_process_supervisor import WorkProcessSupervisor
from cli_agent_orchestrator.services.work_mcp_proxy import WorkMcpProxy
from cli_agent_orchestrator.services.work_process_landlock import _query_abi_version
from cli_agent_orchestrator.work_bubblewrap_policy import BUBBLEWRAP_VERSION

_EXECUTABLE_TOKEN_RE = re.compile(r"^/[A-Za-z0-9._/+@-]+$")
_BWRAP_VERSION_RE = re.compile(
    r"^bubblewrap (0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})$"
)
# Approved Bubblewrap 0.13.0 builds from the checksum-pinned upstream source
# archive, recorded with their build profiles in
# docs/auditoria-t097/soluciones/S08-limites-del-entorno.md. Hosted builds:
# ubuntu-24.04 image 20260920.314.1 (GCC 13.3.0, Meson 1.3.2), and pinned
# Ubuntu 26.10 QEMU guest image 20260919 (GCC 15.3.0-4ubuntu1, Meson 1.10.1).
# The guest digest was reviewed from run 36457607699; its retry must pass T097
# acceptance before registry activation.
_BWRAP_SHA256_ALLOWLIST: frozenset[str] = frozenset(
    {
        "f41ba3f7be0280df0afe201f0e2eeb16a17e969782491e830e6753c67f78d70d",
        "a5882b87c0b8105a5d9e81db5f64a4f8d373affc36f531e5df7409db5b1af8f6",
        "15eae8145dc0053ce790a954f2abe9914a17f49b4ccb20778b88ecc9b9522250",
    }
)
_BWRAP_MAX_BYTES = 64 * 1024 * 1024
_CANONICAL_BWRAP_EXECUTABLE = Path("/usr/bin/bwrap")
_TRUSTED_BWRAP_PARENT_DIRECTORIES = (Path("/"), Path("/usr"), Path("/usr/bin"))


def _unsupported(reason: str) -> UnsupportedWorkEnforcement:
    return UnsupportedWorkEnforcement("BubblewrapWorkBackend", reason)


def _require_work_broker_identity(
    account_name: str | None,
    *,
    effective_uid: int | None = None,
    passwd_lookup: Callable[[str], object] | None = None,
) -> None:
    """Require the configured, non-root, non-login host identity before probes."""
    if (
        not isinstance(account_name, str)
        or not account_name
        or account_name != account_name.strip()
        or not re.fullmatch(r"[a-z_][a-z0-9_-]*[$]?", account_name)
    ):
        raise _unsupported("CAO_WORK_BROKER_ACCOUNT must name the dedicated host account")

    uid = os.geteuid() if effective_uid is None else effective_uid
    if uid == 0:
        raise _unsupported("the Work broker process must not run as root")

    if passwd_lookup is None:
        try:
            import pwd
        except ImportError as exc:
            raise _unsupported("the Work broker account requires a Linux passwd database") from exc
        passwd_lookup = pwd.getpwnam

    try:
        account = passwd_lookup(account_name)
    except (KeyError, ValueError) as exc:
        raise _unsupported("configured Work broker account does not exist on this host") from exc
    except OSError as exc:
        raise _unsupported(f"configured Work broker account lookup failed: {exc}") from exc

    account_uid = getattr(account, "pw_uid", None)
    if account_uid == 0:
        raise _unsupported("the configured Work broker account must not be root")
    if account_uid != uid:
        raise _unsupported("effective UID does not match CAO_WORK_BROKER_ACCOUNT")

    shell = getattr(account, "pw_shell", "")
    if not isinstance(shell, str) or not Path(shell).is_absolute():
        raise _unsupported("the configured Work broker account must have a non-login shell")
    # Resolve symlinks so a passwd entry named nologin cannot point at a login shell.
    shell_name = Path(os.path.realpath(shell)).name
    if shell_name not in {"nologin", "false"}:
        raise _unsupported("the configured Work broker account must have a non-login shell")


def _validate_command_token_syntax(commands: tuple[str, ...]) -> None:
    """Reject shell fragments and relative names before host verification."""
    for command in commands:
        if (
            not _EXECUTABLE_TOKEN_RE.fullmatch(command)
            or command.startswith("//")
            or os.path.normpath(command) != command
            or command == "/"
        ):
            raise _unsupported("effective commands must be canonical absolute executable tokens")


def _verified_bwrap_descriptor(executable: Path) -> int:
    """Open only the root-controlled system Bubblewrap and pin its identity."""
    if executable != _CANONICAL_BWRAP_EXECUTABLE:
        raise _unsupported(
            f"Bubblewrap must use the canonical trusted path {_CANONICAL_BWRAP_EXECUTABLE}"
        )

    for directory in _TRUSTED_BWRAP_PARENT_DIRECTORIES:
        try:
            parent_identity = os.stat(directory, follow_symlinks=False)
        except (OSError, ValueError) as exc:
            raise _unsupported(f"Bubblewrap parent directory cannot be verified: {exc}") from exc
        if (
            not stat.S_ISDIR(parent_identity.st_mode)
            or parent_identity.st_uid != 0
            or stat.S_IMODE(parent_identity.st_mode) & 0o022
        ):
            raise _unsupported(
                "canonical Bubblewrap parent directory ownership or permissions are unsafe"
            )

    try:
        path_identity = os.stat(executable, follow_symlinks=False)
        descriptor = os.open(
            executable,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
    except FileNotFoundError as exc:
        raise _unsupported("configured Bubblewrap binary is missing") from exc
    except (OSError, ValueError) as exc:
        raise _unsupported(f"configured Bubblewrap binary cannot be opened safely: {exc}") from exc

    try:
        identity = os.fstat(descriptor)
        if (
            not stat.S_ISREG(path_identity.st_mode)
            or not stat.S_ISREG(identity.st_mode)
            or (path_identity.st_dev, path_identity.st_ino) != (identity.st_dev, identity.st_ino)
        ):
            raise _unsupported("configured Bubblewrap file identity changed or is not regular")

        mode = stat.S_IMODE(identity.st_mode)
        if (
            identity.st_uid != 0
            or mode & 0o022
            or identity.st_mode & (stat.S_ISUID | stat.S_ISGID)
            or not mode & 0o111
            or not os.access(f"/proc/self/fd/{descriptor}", os.X_OK, effective_ids=True)
        ):
            raise _unsupported(
                "canonical Bubblewrap executable ownership or permissions are unsafe"
            )
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _parse_bwrap_version(stdout: str, returncode: int) -> tuple[int, int, int]:
    match = _BWRAP_VERSION_RE.fullmatch(stdout.strip())
    if returncode != 0 or match is None:
        raise _unsupported("Bubblewrap version output is malformed or unknown")
    version = tuple(int(part) for part in match.groups())
    if version != BUBBLEWRAP_VERSION:
        version_text = ".".join(str(part) for part in version)
        required = ".".join(str(part) for part in BUBBLEWRAP_VERSION)
        raise _unsupported(
            f"Bubblewrap {version_text} does not match the required version {required}"
        )
    return version


def _bubblewrap_sha256(descriptor: int) -> str:
    """Hash a bounded pinned executable without changing its file offset."""
    try:
        identity = os.fstat(descriptor)
        if not stat.S_ISREG(identity.st_mode) or identity.st_size <= 0:
            raise _unsupported("configured Bubblewrap binary is not a non-empty regular file")
        if identity.st_size > _BWRAP_MAX_BYTES:
            raise _unsupported("configured Bubblewrap exceeds the bounded digest size")
        digest = hashlib.sha256()
        offset = 0
        while offset < identity.st_size:
            chunk = os.pread(descriptor, min(1024 * 1024, identity.st_size - offset), offset)
            if not chunk:
                raise _unsupported("configured Bubblewrap changed during digest verification")
            digest.update(chunk)
            offset += len(chunk)
        after = os.fstat(descriptor)
        if (
            identity.st_dev,
            identity.st_ino,
            identity.st_size,
            identity.st_mtime_ns,
            identity.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise _unsupported("configured Bubblewrap changed during digest verification")
        return digest.hexdigest()
    except OSError as exc:
        raise _unsupported(f"configured Bubblewrap digest could not be verified: {exc}") from exc


def _probe_bwrap_version(executable: Path) -> tuple[int, int, int]:
    descriptor = _verified_bwrap_descriptor(executable)
    try:
        digest = _bubblewrap_sha256(descriptor)
        if digest not in _BWRAP_SHA256_ALLOWLIST:
            raise _unsupported("Bubblewrap SHA-256 digest is not allowlisted")
        identity = os.fstat(descriptor)
        try:
            probe = subprocess.run(
                [f"/proc/self/fd/{descriptor}", "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                env={"LC_ALL": "C"},
                pass_fds=(descriptor,),
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            raise _unsupported(f"Bubblewrap version probe failed: {exc}") from exc

        after_probe = os.fstat(descriptor)
        if (identity.st_dev, identity.st_ino, identity.st_uid, identity.st_mode) != (
            after_probe.st_dev,
            after_probe.st_ino,
            after_probe.st_uid,
            after_probe.st_mode,
        ) or (identity.st_size, identity.st_mtime_ns, identity.st_ctime_ns) != (
            after_probe.st_size,
            after_probe.st_mtime_ns,
            after_probe.st_ctime_ns,
        ):
            raise _unsupported("configured Bubblewrap metadata changed during version verification")

        return _parse_bwrap_version(probe.stdout, probe.returncode)
    finally:
        os.close(descriptor)


class BubblewrapWorkBackend(TmuxBackend):
    """Fail-closed prototype; it never launches an unconfined Work process."""

    def __init__(
        self,
        client: Optional[TmuxClient] = None,
        *,
        bwrap_executable: str | Path = "/usr/bin/bwrap",
        repository: WorkRepository | None = None,
        bwrap_sha256_digest: str | None = None,
        supervisor_factory=WorkProcessSupervisor,
        mcp_proxy_factory: Callable[[str, int], WorkMcpProxy] | None = None,
        broker_account: str | None = None,
    ) -> None:
        super().__init__(client)
        # Retain the option for constructor compatibility; preflight never runs
        # a configurable candidate before its identity can be trusted.
        self._bwrap_executable = Path(bwrap_executable)
        if repository is not None and not isinstance(repository, WorkRepository):
            raise ValueError("Bubblewrap Work requires the server-owned Work repository")
        if not callable(supervisor_factory):
            raise ValueError("Bubblewrap Work requires a process supervisor factory")
        if mcp_proxy_factory is not None and not callable(mcp_proxy_factory):
            raise ValueError("Bubblewrap Work MCP proxy factory must be callable")
        self._work_repository = repository
        self._bwrap_sha256_digest = bwrap_sha256_digest
        self._supervisor_factory = supervisor_factory
        self._mcp_proxy_factory = mcp_proxy_factory
        self._broker_account = (
            os.environ.get("CAO_WORK_BROKER_ACCOUNT") if broker_account is None else broker_account
        )
        self._mcp_proxy_instances = weakref.WeakSet()
        self._mcp_proxy_factory_lock = threading.Lock()

    def preflight_work(self, contract: ProcessRestrictionContract) -> None:
        if platform.system() != "Linux":
            raise _unsupported("Bubblewrap Work is Linux-only")
        _require_work_broker_identity(self._broker_account)
        if not isinstance(contract, ProcessRestrictionContract):
            raise _unsupported("an explicit ProcessRestrictionContract is required")
        if contract.tools and self._mcp_proxy_factory is None:
            raise _unsupported("contract tools require an attempt-bound MCP proxy factory")
        _validate_command_token_syntax(contract.commands)
        if contract.network:
            raise _unsupported("non-empty network contracts have no supported grammar")
        if not contract.commands:
            raise _unsupported(
                "no command contract is currently supportable: no effective command is present"
            )
        if len(contract.commands) != 1:
            raise _unsupported("exactly one executable mapping is currently supportable")
        if (
            len(contract.executable_identities) != 1
            or contract.executable_identities[0].command_token != contract.commands[0]
            or contract.executable_identities[0].static is not True
        ):
            raise _unsupported("exactly one immutable static ELF executable mapping is required")
        try:
            landlock_abi = _query_abi_version()
        except OSError as exc:
            raise _unsupported(f"Landlock ABI version probe failed: {exc}") from exc
        if landlock_abi < 9:
            raise _unsupported(
                f"Landlock ABI 9 or later is required; host reports ABI {landlock_abi}"
            )
        _probe_bwrap_version(self._bwrap_executable)

    def execute_bound_process(
        self,
        restriction: ProcessRestrictionContract,
        *,
        binding,
        command_token: str,
        worker_input: bytes,
        expected_attempt_revision: int,
        before_effect,
        authorize_setup,
        authorize_go,
    ):
        """Execute one admitted static ELF under Work guards and the supervisor."""
        if self._work_repository is None or self._bwrap_sha256_digest is None:
            raise _unsupported("server Work repository and accepted Bubblewrap digest are required")
        if not isinstance(binding.contract, EffectiveWorkContractV2):
            raise _unsupported("a V2 process contract is required")
        if not isinstance(restriction, ProcessRestrictionContract):
            raise _unsupported("an explicit process restriction contract is required")
        selected = tuple(
            identity
            for identity in binding.contract.executable_identities
            if identity.command_token == command_token
        )
        if (
            len(binding.contract.executable_identities) != 1
            or len(selected) != 1
            or selected[0].static is not True
            or restriction.executable_identities != binding.contract.executable_identities
            or restriction.commands != (command_token,)
            or restriction.network
            or binding.contract.permissions.commands != (command_token,)
            or restriction.tools != binding.contract.permissions.tools
            or binding.contract.permissions.network
            or not isinstance(worker_input, bytes)
            or len(worker_input) > 32768
            or type(expected_attempt_revision) is not int
            or expected_attempt_revision <= 0
            or not callable(before_effect)
            or not callable(authorize_setup)
            or not callable(authorize_go)
        ):
            raise _unsupported("process effect differs from its immutable Work contract")
        mcp_proxy = None
        if binding.contract.permissions.tools:
            if self._mcp_proxy_factory is None:
                raise _unsupported("contract tools require an attempt-bound MCP proxy factory")
            try:
                mcp_proxy = self._mcp_proxy_factory(binding.attempt_id, binding.generation)
            except Exception as exc:
                raise _unsupported("attempt-bound MCP proxy factory failed before launch") from exc
            if not isinstance(mcp_proxy, WorkMcpProxy):
                raise _unsupported("MCP proxy factory must return a WorkMcpProxy")
            with self._mcp_proxy_factory_lock:
                if mcp_proxy in self._mcp_proxy_instances:
                    raise _unsupported("MCP proxy factory must return a fresh instance per attempt")
                self._mcp_proxy_instances.add(mcp_proxy)
        from cli_agent_orchestrator.services.work_bubblewrap_composition import (
            launch_recorded_bound_work_static_elf,
        )

        return launch_recorded_bound_work_static_elf(
            self._work_repository,
            binding.attempt_id,
            binding.generation,
            expected_attempt_revision=expected_attempt_revision,
            command_token=command_token,
            bwrap_path=self._bwrap_executable,
            bwrap_sha256_digest=self._bwrap_sha256_digest,
            worker_input=worker_input,
            process_supervisor=self._supervisor_factory(),
            mcp_proxy=mcp_proxy,
            before_start=before_effect,
            authorize_setup=authorize_setup,
            authorize_go=authorize_go,
        )

    def create_session(self, *args, **kwargs):
        raise _unsupported("direct unguarded session creation is disabled")

    def create_window(self, *args, **kwargs):
        raise _unsupported("direct unguarded window creation is disabled")

    def send_keys(self, *args, **kwargs):
        raise _unsupported("direct unguarded input is disabled")

    def send_special_key(self, *args, **kwargs):
        raise _unsupported("direct unguarded special-key input is disabled")

    def kill_session(self, *args, **kwargs):
        raise _unsupported("direct unguarded session termination is disabled")

    def kill_window(self, *args, **kwargs):
        raise _unsupported("direct unguarded window termination is disabled")

    def pipe_pane(self, *args, **kwargs):
        raise _unsupported("direct unguarded pane piping is disabled")

    def stop_pipe_pane(self, *args, **kwargs):
        raise _unsupported("direct unguarded pane piping changes are disabled")
