"""Fail-closed per-attempt Docker runtime for static managed Work executables."""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import threading
import time
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from cli_agent_orchestrator.services.work_origin import WorkOrigins

from cli_agent_orchestrator.backends.base import (
    ProcessRestrictionContract,
    TerminalBackendError,
    UnsupportedWorkEnforcement,
)
from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
from cli_agent_orchestrator.clients.tmux import TmuxClient
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_contract import EffectiveWorkContractV2
from cli_agent_orchestrator.services.work_attempt_credential import (
    read_attempt_credential_descriptor,
    validate_attempt_credential_descriptor,
)
from cli_agent_orchestrator.services.work_contract import WorkContracts
from cli_agent_orchestrator.services.work_docker_isolation_proof import (
    issue_docker_runtime_isolation_proof,
)
from cli_agent_orchestrator.services.work_executable_content import WorkExecutableContent
from cli_agent_orchestrator.services.work_mcp_proxy import WorkMcpProxy, WorkMcpProxyError

_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_BASE_TAG = re.compile(r"^cao-work-rootfs:[0-9a-f]{64}$")
_VERSION = re.compile(r"^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})$")
_OUTPUT_LIMIT = 1024 * 1024
_INPUT_LIMIT = 32768
_REQUEST_LIMIT = 16384
_FRAME_LIMIT = 65536
_ALLOWED_MANAGED_TOOLS = frozenset(
    {
        "cao.work.child",
        "cao.work.handoff",
        "cao.work.task_received",
        "cao.work.submit_result",
    }
)
_RECEIVER_AUTHENTICATED_TOOLS = frozenset({"cao.work.task_received", "cao.work.submit_result"})
_SUPERVISOR_SOURCE = Path(__file__).with_name("docker_work_supervisor.c")
_SUPERVISOR_SOURCE_SHA256 = hashlib.sha256(_SUPERVISOR_SOURCE.read_bytes()).hexdigest()


def _unsupported(reason: str) -> UnsupportedWorkEnforcement:
    return UnsupportedWorkEnforcement("DockerWorkBackend", reason)


def _receiver_credential_for_request(request: dict, descriptor: int | None) -> bytes | None:
    """Read receiver authority only for its two separately authenticated actions."""
    try:
        name = request["params"]["name"]
    except (KeyError, TypeError):
        raise _unsupported("Docker Work MCP request is invalid")
    if name not in _RECEIVER_AUTHENTICATED_TOOLS:
        return None
    if descriptor is None:
        raise _unsupported("receiver action requires its separate credential")
    try:
        return read_attempt_credential_descriptor(descriptor)
    except Exception as error:
        raise _unsupported("server-owned receiver credential descriptor is unavailable") from error


class DockerWorkBackendUnavailable(TerminalBackendError):
    """Docker could not safely prepare or observe the local attempt container."""


class DockerWorkAttemptRecoveryRequired(DockerWorkBackendUnavailable):
    """A previous container identity remains and must be reconciled first."""


class DockerWorkExecutionUncertain(DockerWorkBackendUnavailable):
    """Worker start or cleanup could not be resolved from the Docker daemon."""


@dataclass(frozen=True, slots=True)
class DockerWorkExecution:
    """Bounded output from one exact container-owned process attempt."""

    returncode: int
    stdout: bytes
    stderr: bytes
    process_stopped: bool
    container_removed: bool
    image_removed: bool


class DockerWorkBackend(TmuxBackend):
    """Run one static ELF and bridge only managed Work MCP calls over attach stdio."""

    def __init__(
        self,
        client: TmuxClient | None = None,
        *,
        image_ref: str,
        docker_command: str | os.PathLike[str] = "docker",
        repository: WorkRepository | None = None,
        timeout_seconds: int = 120,
        mcp_proxy_factory: Callable[[str, int], WorkMcpProxy] | None = None,
    ) -> None:
        super().__init__(client)
        if not isinstance(image_ref, str) or not _IMAGE_ID.fullmatch(image_ref):
            raise ValueError("Docker Work requires an immutable image ID")
        if (
            not isinstance(docker_command, (str, os.PathLike))
            or not str(docker_command)
            or "\x00" in str(docker_command)
        ):
            raise ValueError("Docker CLI executable is required")
        if repository is not None and not isinstance(repository, WorkRepository):
            raise ValueError("Docker Work requires the server-owned Work repository")
        if mcp_proxy_factory is not None and not callable(mcp_proxy_factory):
            raise ValueError("Docker Work MCP proxy factory must be callable")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
            raise ValueError("Docker Work timeout must be between 1 and 3600 seconds")
        self.image_ref = image_ref
        self._base_image_tag = f"cao-work-rootfs:{image_ref.removeprefix('sha256:')}"
        self.docker_command = str(docker_command)
        self.repository = repository
        self.timeout_seconds = timeout_seconds
        self._validated_image = False
        self._preflight_lock = threading.Lock()
        self._base_layer_count = 0
        self._base_layer_digests: tuple[str, ...] = ()
        self._mcp_proxy_factory = mcp_proxy_factory
        self._work_origins: WorkOrigins | None = None
        self._mcp_proxy_instances: weakref.WeakSet[WorkMcpProxy] = weakref.WeakSet()
        self._mcp_proxy_factory_lock = threading.Lock()
        self._local_docker_host: str | None = None
        self._docker_environment: dict[str, str] | None = None

    def bind_work_origins(self, origins) -> None:
        """Bind the one WorkOrigins owner paired with this runtime's repository."""
        from cli_agent_orchestrator.services.work_origin import WorkOrigins

        if (
            not isinstance(origins, WorkOrigins)
            or self.repository is None
            or origins.repository is not self.repository
        ):
            raise ValueError("Docker Work origins must share the server-owned repository")
        self._work_origins = origins

    @staticmethod
    def _supported_contract(restriction: ProcessRestrictionContract) -> None:
        if not isinstance(restriction, ProcessRestrictionContract):
            raise _unsupported("an explicit ProcessRestrictionContract is required")
        if restriction.paths or restriction.network:
            raise _unsupported(
                "this local Docker profile cannot enforce workspace writes or network access"
            )
        if any(tool not in _ALLOWED_MANAGED_TOOLS for tool in restriction.tools):
            raise _unsupported("Docker Work supports only the managed cao.work.* MCP tools")
        identities = restriction.executable_identities
        if (
            len(restriction.commands) != 1
            or len(identities) != 1
            or restriction.commands != (identities[0].command_token,)
            or identities[0].static is not True
        ):
            raise _unsupported("exactly one immutable static ELF executable is required")

    def _run(self, arguments, *, timeout=15, check=True, input_data: bytes | None = None):
        command = self._docker_arguments(arguments)
        if input_data is not None and type(input_data) is not bytes:
            raise TypeError("Docker stdin payload must be immutable bytes")
        options = {
            "capture_output": True,
            "timeout": timeout,
            "check": False,
            "env": self._docker_environment,
        }
        if input_data is None:
            options.update(stdin=subprocess.DEVNULL, text=True)
        else:
            options["input"] = input_data
            options["text"] = False
        try:
            result = subprocess.run(
                command,
                **options,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise DockerWorkBackendUnavailable("Docker CLI could not be queried") from error
        if check and result.returncode != 0:
            raise DockerWorkBackendUnavailable("Docker daemon rejected a required operation")
        return result

    def _docker_executable(self) -> str:
        return shutil.which(self.docker_command) or self.docker_command

    def _configure_local_docker_endpoint(self) -> None:
        """Pin every Docker operation to the verified local Unix socket."""
        if self._local_docker_host is not None:
            return
        environment = os.environ.copy()
        docker_host = environment.get("DOCKER_HOST")
        docker_context = environment.get("DOCKER_CONTEXT")
        executable = self._docker_executable()
        if docker_host:
            host = docker_host
        else:
            if not docker_context:
                try:
                    context = subprocess.run(
                        [executable, "context", "show"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                        env=environment,
                    )
                except (OSError, subprocess.SubprocessError) as error:
                    raise DockerWorkBackendUnavailable(
                        "Docker local context could not be queried"
                    ) from error
                if context.returncode != 0:
                    raise DockerWorkBackendUnavailable("Docker local context could not be queried")
                docker_context = context.stdout.strip()
            if not docker_context or "\x00" in docker_context:
                raise _unsupported("a local Docker context must be selected")
            try:
                inspected = subprocess.run(
                    [
                        executable,
                        "context",
                        "inspect",
                        docker_context,
                        "--format",
                        "{{.Endpoints.docker.Host}}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                    env=environment,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise DockerWorkBackendUnavailable(
                    "Docker local context could not be inspected"
                ) from error
            if inspected.returncode != 0:
                raise DockerWorkBackendUnavailable("Docker local context could not be inspected")
            host = inspected.stdout.strip()
        if not host.startswith("unix://"):
            raise _unsupported("Docker Work accepts only a local Docker daemon")
        socket_path = host.removeprefix("unix://")
        if not socket_path.startswith("/") or "\x00" in socket_path:
            raise _unsupported("Docker Work accepts only a local Docker daemon")
        try:
            socket_stat = os.stat(socket_path)
        except OSError as error:
            raise DockerWorkBackendUnavailable(
                "local Docker socket could not be verified"
            ) from error
        if not stat.S_ISSOCK(socket_stat.st_mode):
            raise _unsupported("Docker Work accepts only a local Docker daemon")
        for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
            environment.pop(key, None)
        self._local_docker_host = host
        self._docker_environment = environment

    def _docker_arguments(self, arguments) -> list[str]:
        self._configure_local_docker_endpoint()
        return [self._docker_executable(), "--host", self._local_docker_host, *arguments]

    def _validate_engine_and_image(self) -> None:
        with self._preflight_lock:
            if self._validated_image:
                return
            if platform.system() != "Linux":
                raise _unsupported("Docker Work requires a Linux Docker engine client host")
            self._configure_local_docker_endpoint()
            version_result = self._run(
                ["version", "--format", "{{.Server.Version}}|{{.Server.Os}}|{{.Server.Arch}}"]
            )
            try:
                version, engine_os, architecture = version_result.stdout.strip().split("|")
                match = _VERSION.fullmatch(version)
                version_tuple = tuple(int(part) for part in match.groups()) if match else ()
            except (ValueError, TypeError):
                version_tuple = ()
                engine_os = architecture = ""
            if not version_tuple or version_tuple < (25, 0, 0):
                raise _unsupported("Docker Engine version 25.0.0 or newer must be verified")
            if engine_os != "linux" or architecture not in {"amd64", "x86_64"}:
                raise _unsupported("Docker Engine must report Linux amd64")
            info = self._run(["info", "--format", "{{json .SecurityOptions}}"])
            try:
                security_options = json.loads(info.stdout)
            except (TypeError, ValueError):
                security_options = []
            if not isinstance(security_options, list) or not any(
                isinstance(value, str) and value.startswith("name=seccomp")
                for value in security_options
            ):
                raise _unsupported("Docker Engine seccomp enforcement is unavailable")
            image = self._run(["image", "inspect", self.image_ref])
            try:
                inspected = json.loads(image.stdout)
                metadata = inspected[0]
                config = metadata["Config"]
                rootfs = metadata["RootFS"]
                rootfs_layers = rootfs.get("Layers", [])
            except (IndexError, KeyError, TypeError, ValueError):
                raise _unsupported("pinned Docker image metadata is invalid") from None
            if (
                type(inspected) is not list
                or metadata.get("Id") != self.image_ref
                or (metadata.get("Os"), metadata.get("Architecture")) != ("linux", "amd64")
                or rootfs.get("Type") != "layers"
                or len(rootfs_layers) != 1
                or not isinstance(config, dict)
                or config.get("User", "") != ""
                or config.get("Env")
                not in (
                    None,
                    [],
                    ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"],
                )
                or config.get("Entrypoint") not in (None, [])
                or config.get("Cmd") not in (None, [])
                or config.get("Volumes") not in (None, {})
                or config.get("WorkingDir", "") not in ("", "/")
                or config.get("Healthcheck") not in (None, {})
                or not isinstance(config.get("Labels"), dict)
                or config["Labels"].get("org.cao.work.supervisor.protocol") != "1"
                or config["Labels"].get("org.cao.work.supervisor.source_sha256")
                != _SUPERVISOR_SOURCE_SHA256
            ):
                raise _unsupported(
                    "Docker image must be the pinned Linux amd64 Work supervisor rootfs"
                )
            self._base_layer_count = len(rootfs_layers)
            self._base_layer_digests = tuple(rootfs_layers)
            self._validated_image = True

    def preflight_work(self, contract: ProcessRestrictionContract) -> None:
        self._supported_contract(contract)
        if contract.tools and self._mcp_proxy_factory is None and self._work_origins is None:
            raise _unsupported("managed Work tools require a bound WorkOrigins proxy")
        self._validate_engine_and_image()

    def _staged_executable(self, attempt_id: str, generation: int, command_token: str) -> bytes:
        if self.repository is None:
            raise _unsupported("server Work repository is required")
        with WorkExecutableContent(self.repository).resolve_and_stage(
            attempt_id, generation, command_token
        ) as staged:
            executable = bytearray()
            offset = 0
            while offset < 8 * 1024 * 1024:
                block = os.pread(
                    staged.memfd_fd, min(1024 * 1024, 8 * 1024 * 1024 - offset), offset
                )
                if not block:
                    break
                executable.extend(block)
                offset += len(block)
            if hashlib.sha256(executable).hexdigest() != staged.identity.sha256_digest:
                raise DockerWorkBackendUnavailable("staged executable identity changed")
            return bytes(executable)

    @staticmethod
    def _worker_archive(executable: bytes) -> bytes:
        """Create the fixed, readonly worker member used in the image context."""
        if type(executable) is not bytes or not executable or len(executable) > 8 * 1024 * 1024:
            raise DockerWorkBackendUnavailable("Docker worker content is outside its byte bound")
        archive = io.BytesIO()
        metadata = tarfile.TarInfo("worker")
        metadata.type = tarfile.REGTYPE
        metadata.size = len(executable)
        metadata.mode = 0o555
        metadata.uid = metadata.gid = 0
        metadata.mtime = 0
        with tarfile.open(fileobj=archive, mode="w", format=tarfile.USTAR_FORMAT) as stream:
            stream.addfile(metadata, io.BytesIO(executable))
        return archive.getvalue()

    @classmethod
    def _attempt_image_archive(
        cls, base_image_id: str, attempt_id: str, generation: int, executable: bytes
    ) -> bytes:
        if (
            not _BASE_TAG.fullmatch(base_image_id)
            or type(attempt_id) is not str
            or not attempt_id
            or type(generation) is not int
            or generation <= 0
        ):
            raise DockerWorkBackendUnavailable("Docker attempt image binding is invalid")
        cls._worker_archive(executable)
        worker_digest = hashlib.sha256(executable).hexdigest()
        attempt_digest = hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()
        dockerfile = (
            f"FROM {base_image_id}\n"
            "COPY --chown=0:0 --chmod=0555 cao-work-worker /cao-work-worker\n"
            'LABEL org.cao.work.backend="docker"\n'
            f'LABEL org.cao.work.attempt_sha256="{attempt_digest}"\n'
            f'LABEL org.cao.work.generation="{generation}"\n'
            f'LABEL org.cao.work.worker_sha256="{worker_digest}"\n'
            f'LABEL org.cao.work.supervisor.source_sha256="{_SUPERVISOR_SOURCE_SHA256}"\n'
        ).encode("ascii")
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w", format=tarfile.USTAR_FORMAT) as stream:
            for name, data, mode in (
                ("Dockerfile", dockerfile, 0o444),
                ("cao-work-worker", executable, 0o555),
            ):
                metadata = tarfile.TarInfo(name)
                metadata.type = tarfile.REGTYPE
                metadata.size = len(data)
                metadata.mode = mode
                metadata.uid = metadata.gid = metadata.mtime = 0
                stream.addfile(metadata, io.BytesIO(data))
        return archive.getvalue()

    @staticmethod
    def _container_name(attempt_id: str, generation: int) -> str:
        token = hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()[:24]
        return f"cao-work-{token}-g{generation}"

    @staticmethod
    def _attempt_image_tag(attempt_id: str, generation: int) -> str:
        token = hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()[:24]
        return f"cao-work-attempt-{token}:g{generation}"

    def _validate_attempt_image(
        self, image_id: str, *, attempt_id: str, generation: int, worker_sha256: str
    ) -> None:
        image = self._run(["image", "inspect", image_id])
        try:
            metadata = json.loads(image.stdout)[0]
            config = metadata["Config"]
            rootfs = metadata["RootFS"]
            labels = config.get("Labels")
        except (IndexError, KeyError, TypeError, ValueError):
            raise DockerWorkBackendUnavailable("Docker worker image metadata is invalid") from None
        if (
            metadata.get("Id") != image_id
            or (metadata.get("Os"), metadata.get("Architecture")) != ("linux", "amd64")
            or rootfs.get("Type") != "layers"
            or tuple(rootfs.get("Layers", ()))[: self._base_layer_count] != self._base_layer_digests
            or len(rootfs.get("Layers", ())) != self._base_layer_count + 1
            or not isinstance(config, dict)
            or config.get("User", "") != ""
            or config.get("Entrypoint") not in (None, [])
            or config.get("Cmd") not in (None, [])
            or config.get("Volumes") not in (None, {})
            or config.get("WorkingDir", "") not in ("", "/")
            or config.get("Healthcheck") not in (None, {})
            or not isinstance(labels, dict)
            or labels.get("org.cao.work.backend") != "docker"
            or labels.get("org.cao.work.attempt_sha256")
            != hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()
            or labels.get("org.cao.work.generation") != str(generation)
            or labels.get("org.cao.work.worker_sha256") != worker_sha256
            or labels.get("org.cao.work.supervisor.source_sha256") != _SUPERVISOR_SOURCE_SHA256
        ):
            raise DockerWorkBackendUnavailable(
                "Docker worker image differs from its one-file rootfs"
            )

    def _cleanup_container(self, container_id: str, *, kill: bool) -> bool:
        if kill:
            self._run(["container", "kill", container_id], check=False)
            self._run(["container", "wait", container_id], timeout=30, check=False)
        self._run(["container", "rm", "--force", container_id], check=False)
        inspect = self._run(["container", "inspect", container_id], check=False)
        return not self._inspect_reports_present(inspect)

    def _cleanup_attempt_image(self, image_tag: str) -> bool:
        self._run(["image", "rm", "--force", image_tag], check=False)
        inspect = self._run(["image", "inspect", image_tag], check=False)
        return not self._inspect_reports_present(inspect)

    def _cleanup_failed_execution(
        self, attempt_id: str, generation: int, container_id: str | None, image_tag: str
    ) -> tuple[bool, bool]:
        if container_id is None:
            cleanup = self.reconcile_attempt(attempt_id, generation)
            return (
                cleanup.get("container_removed") is True,
                cleanup.get("image_removed") is True,
            )
        return (
            self._cleanup_container(container_id, kill=True),
            self._cleanup_attempt_image(image_tag),
        )

    @staticmethod
    def _inspect_reports_present(result) -> bool:
        if result.returncode == 0:
            return True
        diagnostic = "\n".join(
            value
            for value in (getattr(result, "stdout", ""), getattr(result, "stderr", ""))
            if isinstance(value, str)
        ).lower()
        if re.search(r"no such (?:object|container|image)", diagnostic):
            return False
        raise DockerWorkExecutionUncertain("Docker artifact state could not be verified")

    @staticmethod
    def _frame(prefix: bytes, payload: bytes) -> bytes:
        return prefix + str(len(payload)).encode("ascii") + b"\n" + payload + b"\n"

    @staticmethod
    def _read_exact(pipe, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            block = pipe.read(size - len(result))
            if not block:
                raise DockerWorkExecutionUncertain("Docker supervisor closed a partial frame")
            result.extend(block)
        return bytes(result)

    @classmethod
    def _read_frame(cls, pipe, prefix: bytes, limit: int) -> bytes:
        header = pipe.readline(96)
        if not header.startswith(prefix) or not header.endswith(b"\n"):
            raise DockerWorkExecutionUncertain("Docker supervisor frame header is invalid")
        raw_size = header[len(prefix) : -1]
        if not raw_size or not raw_size.isdigit() or len(raw_size) > 9:
            raise DockerWorkExecutionUncertain("Docker supervisor frame size is invalid")
        size = int(raw_size)
        if size > limit:
            raise DockerWorkExecutionUncertain("Docker supervisor frame exceeds its byte bound")
        payload = cls._read_exact(pipe, size)
        if cls._read_exact(pipe, 1) != b"\n":
            raise DockerWorkExecutionUncertain("Docker supervisor frame terminator is invalid")
        return payload

    def _build_attempt_image(
        self, *, attempt_id: str, generation: int, executable: bytes
    ) -> tuple[str, str]:
        tag = self._attempt_image_tag(attempt_id, generation)
        worker_sha256 = hashlib.sha256(executable).hexdigest()
        base = self._run(
            ["image", "inspect", "--format", "{{.Id}}", self._base_image_tag], check=False
        )
        if base.returncode != 0:
            self._run(["image", "tag", self.image_ref, self._base_image_tag])
            base = self._run(["image", "inspect", "--format", "{{.Id}}", self._base_image_tag])
        if base.stdout.strip() != self.image_ref:
            raise DockerWorkAttemptRecoveryRequired(
                "the local immutable Work rootfs alias resolves to another image"
            )
        self._run(
            [
                "image",
                "build",
                "--platform=linux/amd64",
                "--network=none",
                "--pull=false",
                "--tag",
                tag,
                "-",
            ],
            timeout=60,
            input_data=self._attempt_image_archive(
                self._base_image_tag, attempt_id, generation, executable
            ),
        )
        inspected = self._run(["image", "inspect", "--format", "{{.Id}}", tag])
        image_id = inspected.stdout.strip()
        if not _IMAGE_ID.fullmatch(image_id):
            raise DockerWorkBackendUnavailable("Docker returned an invalid attempt image identity")
        self._validate_attempt_image(
            image_id,
            attempt_id=attempt_id,
            generation=generation,
            worker_sha256=worker_sha256,
        )
        return tag, image_id

    def _inspect_container(self, container_id: str) -> dict:
        result = self._run(["container", "inspect", "--format", "{{json .}}", container_id])
        try:
            value = json.loads(result.stdout)
        except (TypeError, ValueError) as error:
            raise DockerWorkExecutionUncertain("Docker container inspection is invalid") from error
        if type(value) is not dict:
            raise DockerWorkExecutionUncertain("Docker container inspection is invalid")
        return value

    def _validate_running_container(
        self,
        container_id: str,
        *,
        attempt_id: str,
        generation: int,
        image_id: str,
        command: list[str],
        mcp_enabled: bool,
        expected_running: bool = True,
    ) -> dict[str, str]:
        metadata = self._inspect_container(container_id)
        state = metadata.get("State")
        config = metadata.get("Config")
        host = metadata.get("HostConfig")
        labels = config.get("Labels") if isinstance(config, dict) else None
        expected_attempt_hash = hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()
        if (
            not isinstance(state, dict)
            or not isinstance(config, dict)
            or not isinstance(host, dict)
        ):
            raise DockerWorkExecutionUncertain("Docker container runtime metadata is incomplete")
        if (
            metadata.get("Id") != container_id
            or metadata.get("Image") != image_id
            or state.get("Running") is not expected_running
            or not state.get("StartedAt")
            or state.get("StartedAt", "").startswith("0001-01-01T00:00:00")
            or config.get("User") not in {"0:0", "root"}
            or config.get("Entrypoint") != ["/cao-work-supervisor"]
            or config.get("Cmd") != command
            or config.get("AttachStdin") is not True
            or config.get("OpenStdin") is not True
            or config.get("Env")
            not in (
                ["LC_ALL=C"],
                ["LC_ALL=C", "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"],
            )
            or not isinstance(labels, dict)
            or labels.get("cao.work.backend") != "docker"
            or labels.get("cao.work.attempt_sha256") != expected_attempt_hash
            or labels.get("cao.work.generation") != str(generation)
            or labels.get("cao.work.image_sha256") != image_id.removeprefix("sha256:")
            or labels.get("cao.work.mcp_enabled") != ("true" if mcp_enabled else "false")
            or host.get("ReadonlyRootfs") is not True
            or host.get("NetworkMode") != "none"
            or host.get("IpcMode") != "private"
            or host.get("PidMode") not in {"", "private"}
            or set(host.get("CapDrop") or ()) != {"ALL"}
            or set(host.get("CapAdd") or ()) != {"CAP_SETGID", "CAP_SETUID"}
            or not set(host.get("SecurityOpt") or ()).intersection(
                {"no-new-privileges", "no-new-privileges:true"}
            )
            or host.get("Privileged") is not False
            or host.get("PidsLimit") != 2
            or host.get("Memory") != 256 * 1024 * 1024
            or host.get("MemorySwap") != 256 * 1024 * 1024
            or host.get("NanoCpus") != 1_000_000_000
            or host.get("Binds") not in (None, [])
            or host.get("Tmpfs") not in (None, {})
            or host.get("VolumesFrom") not in (None, [])
            or host.get("Devices") not in (None, [])
            or host.get("PortBindings") not in (None, {})
            or metadata.get("Mounts") not in (None, [])
        ):
            raise DockerWorkExecutionUncertain(
                "Docker container differs from its isolation contract"
            )
        return {
            "container_id": container_id,
            "image_id": image_id,
            "started_at": state["StartedAt"],
        }

    def reconcile_attempt(self, attempt_id: str, generation: int) -> dict[str, bool]:
        """Remove only exact Docker artifacts for this attempt; never starts work."""
        if (
            type(attempt_id) is not str
            or not attempt_id
            or len(attempt_id) > 256
            or "\x00" in attempt_id
            or type(generation) is not int
            or generation <= 0
        ):
            raise ValueError("invalid Docker Work reconciliation owner")
        attempt_hash = hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()
        container_name = self._container_name(attempt_id, generation)
        image_tag = self._attempt_image_tag(attempt_id, generation)
        container_result = self._run(
            ["container", "inspect", "--format", "{{json .}}", container_name], check=False
        )
        container_exists = self._inspect_reports_present(container_result)
        container_removed = not container_exists
        if container_exists:
            try:
                container = json.loads(container_result.stdout)
                labels = container["Config"]["Labels"]
            except (KeyError, TypeError, ValueError):
                raise DockerWorkAttemptRecoveryRequired(
                    "existing Docker container identity cannot be reconciled safely"
                ) from None
            if (
                labels.get("cao.work.backend") != "docker"
                or labels.get("cao.work.attempt_sha256") != attempt_hash
                or labels.get("cao.work.generation") != str(generation)
            ):
                raise DockerWorkAttemptRecoveryRequired(
                    "existing Docker container belongs to another Work identity"
                )
            container_removed = self._cleanup_container(container["Id"], kill=True)

        image_result = self._run(
            ["image", "inspect", "--format", "{{json .}}", image_tag], check=False
        )
        image_exists = self._inspect_reports_present(image_result)
        image_removed = not image_exists
        if image_exists:
            try:
                image = json.loads(image_result.stdout)
                labels = image["Config"]["Labels"]
            except (KeyError, TypeError, ValueError):
                raise DockerWorkAttemptRecoveryRequired(
                    "existing Docker image identity cannot be reconciled safely"
                ) from None
            if (
                labels.get("org.cao.work.backend") != "docker"
                or labels.get("org.cao.work.attempt_sha256") != attempt_hash
                or labels.get("org.cao.work.generation") != str(generation)
            ):
                raise DockerWorkAttemptRecoveryRequired(
                    "existing Docker image belongs to another Work identity"
                )
            image_removed = self._cleanup_attempt_image(image_tag)
        return {"container_removed": container_removed, "image_removed": image_removed}

    def execute_bound_process(
        self,
        restriction: ProcessRestrictionContract,
        *,
        binding,
        command_token: str,
        worker_input: bytes,
        expected_attempt_revision: int,
        attempt_credential_fd: int,
        receiver_credential_fd: int | None = None,
        before_effect: Callable[[], None],
        authorize_setup: Callable,
        authorize_go: Callable,
    ) -> DockerWorkExecution:
        self._supported_contract(restriction)
        if self.repository is None or not isinstance(binding.contract, EffectiveWorkContractV2):
            raise _unsupported("bound V2 contract and server Work repository are required")
        matches = tuple(
            item
            for item in binding.contract.executable_identities
            if item.command_token == command_token
        )
        if (
            len(matches) != 1
            or len(binding.contract.executable_identities) != 1
            or restriction.executable_identities != binding.contract.executable_identities
            or restriction.commands != (command_token,)
            or restriction.tools != binding.contract.permissions.tools
            or binding.contract.permissions.commands != (command_token,)
            or binding.contract.permissions.network
            or any(
                tool not in _ALLOWED_MANAGED_TOOLS for tool in binding.contract.permissions.tools
            )
            or binding.contract.resources.write_paths
            or binding.contract.permissions.network
            or not isinstance(worker_input, bytes)
            or len(worker_input) > _INPUT_LIMIT
            or type(expected_attempt_revision) is not int
            or expected_attempt_revision <= 0
            or type(attempt_credential_fd) is not int
            or not callable(before_effect)
            or not callable(authorize_setup)
            or not callable(authorize_go)
        ):
            raise _unsupported("process effect exceeds the narrow Docker contract")
        self._validate_engine_and_image()
        try:
            validate_attempt_credential_descriptor(attempt_credential_fd)
        except Exception as error:
            raise _unsupported(
                "server-owned attempt credential descriptor is unavailable"
            ) from error
        if receiver_credential_fd is not None:
            try:
                validate_attempt_credential_descriptor(receiver_credential_fd)
            except Exception as error:
                raise _unsupported(
                    "server-owned receiver credential descriptor is unavailable"
                ) from error
        tools = binding.contract.permissions.tools
        if (
            any(tool in _RECEIVER_AUTHENTICATED_TOOLS for tool in tools)
            and receiver_credential_fd is None
        ):
            raise _unsupported("receiver action requires its separate credential")
        mcp_proxy = None
        endpoint = None
        proxy_thread = None
        proxy_errors: list[BaseException] = []
        if tools:
            if self._mcp_proxy_factory is None and self._work_origins is None:
                raise _unsupported("managed Work tools require a bound WorkOrigins proxy")
            try:
                if self._mcp_proxy_factory is not None:
                    mcp_proxy = self._mcp_proxy_factory(binding.attempt_id, binding.generation)
                else:

                    def upstream(request, secret):
                        receiver_secret = _receiver_credential_for_request(
                            request, receiver_credential_fd
                        )
                        return self._work_origins.handle_mcp_request(
                            request, secret, receiver_credential=receiver_secret
                        )

                    mcp_proxy = WorkMcpProxy(
                        self.repository,
                        server_secret_factory=lambda: read_attempt_credential_descriptor(
                            attempt_credential_fd
                        ),
                        upstream=upstream,
                    )
            except Exception as error:
                raise _unsupported("attempt-bound MCP proxy setup failed before launch") from error
            if not isinstance(mcp_proxy, WorkMcpProxy):
                raise _unsupported("MCP proxy factory must return a WorkMcpProxy")
            with self._mcp_proxy_factory_lock:
                if mcp_proxy in self._mcp_proxy_instances:
                    raise _unsupported("MCP proxy factory must return a fresh instance per attempt")
                self._mcp_proxy_instances.add(mcp_proxy)
        before_effect()
        attempt_id = binding.attempt_id
        generation = binding.generation
        with self.repository.read_snapshot() as connection:
            self.repository._verify(connection)
            current = WorkContracts(self.repository)._revalidate_order(
                connection, attempt_id, generation=generation
            )
            attempt = connection.execute(
                "SELECT state,revision,lease_expires_at FROM work_attempts "
                "WHERE id=? AND generation=?",
                (attempt_id, generation),
            ).fetchone()
            if (
                current != binding
                or attempt is None
                or (attempt["state"], attempt["revision"]) != ("sent", expected_attempt_revision)
            ):
                raise DockerWorkBackendUnavailable("Docker attempt binding is stale")
            lease_expires_at = attempt["lease_expires_at"]
        executable_content = self._staged_executable(attempt_id, generation, command_token)
        container_name = self._container_name(attempt_id, generation)
        attempt_image_tag = self._attempt_image_tag(attempt_id, generation)
        prior = self._run(
            ["container", "inspect", "--format", "{{.Id}}", container_name], check=False
        )
        if self._inspect_reports_present(prior):
            raise DockerWorkAttemptRecoveryRequired(
                "a container already exists for this Work attempt; reconcile before retry"
            )
        prior_image = self._run(["image", "inspect", attempt_image_tag], check=False)
        if self._inspect_reports_present(prior_image):
            raise DockerWorkAttemptRecoveryRequired(
                "a worker image already exists for this Work attempt; reconcile before retry"
            )

        container_id = None
        attempt_image_id = None
        process: subprocess.Popen[bytes] | None = None
        stdout = bytearray()
        stderr = bytearray()
        ready_payload: list[dict] = []
        exit_codes: list[int] = []
        protocol_errors: list[BaseException] = []
        ready_event = threading.Event()
        exited_event = threading.Event()
        protocol_lock = threading.Lock()
        stdin_lock = threading.Lock()
        readers: tuple[threading.Thread, ...] = ()
        docker_proof = None
        runtime_identity = None
        attempt_image_tag = self._attempt_image_tag(attempt_id, generation)
        attempt_hash = hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()
        mcp_enabled = bool(tools)
        worker_uid = 65532
        worker_gid = 65532
        supervisor_args = [
            "--protocol=1",
            "--worker",
            "/cao-work-worker",
            "--argv0",
            command_token,
            "--uid",
            str(worker_uid),
            "--gid",
            str(worker_gid),
        ]
        if mcp_enabled:
            supervisor_args.append("--mcp")
        supervisor_command = list(supervisor_args)

        def authorize(callback):
            with self.repository.transaction() as connection:
                self.repository._verify(connection)
                callback(connection)

        def fail(error: BaseException) -> None:
            with protocol_lock:
                if not protocol_errors:
                    protocol_errors.append(error)
            ready_event.set()
            exited_event.set()

        def payload_after_header(pipe, header: bytes, prefix: bytes, limit: int) -> bytes:
            if not header.startswith(prefix) or not header.endswith(b"\n"):
                raise DockerWorkExecutionUncertain("Docker supervisor frame header is invalid")
            raw_size = header[len(prefix) : -1]
            if not raw_size or not raw_size.isdigit() or len(raw_size) > 9:
                raise DockerWorkExecutionUncertain("Docker supervisor frame size is invalid")
            size = int(raw_size)
            if size > limit:
                raise DockerWorkExecutionUncertain("Docker supervisor frame exceeds its byte bound")
            payload = self._read_exact(pipe, size)
            if self._read_exact(pipe, 1) != b"\n":
                raise DockerWorkExecutionUncertain("Docker supervisor frame terminator is invalid")
            return payload

        def read_stdout() -> None:
            total = 0
            try:
                assert process is not None and process.stdout is not None
                while True:
                    header = process.stdout.readline(128)
                    if not header:
                        if not exit_codes:
                            raise DockerWorkExecutionUncertain(
                                "Docker supervisor ended without an exit frame"
                            )
                        return
                    if header.startswith(b"CAO-READY/1 "):
                        payload = payload_after_header(
                            process.stdout, header, b"CAO-READY/1 ", 4096
                        )
                        value = json.loads(payload.decode("utf-8"))
                        if type(value) is not dict or ready_payload:
                            raise DockerWorkExecutionUncertain("Docker supervisor READY is invalid")
                        ready_payload.append(value)
                        ready_event.set()
                        continue
                    if header.startswith(b"CAO-OUT/1 "):
                        parts = header.decode("ascii", "strict").strip().split(" ")
                        if len(parts) != 3 or parts[1] not in {"stdout", "stderr"}:
                            raise DockerWorkExecutionUncertain(
                                "Docker worker output frame is invalid"
                            )
                        raw_size = parts[2]
                        if not raw_size.isdigit() or len(raw_size) > 9:
                            raise DockerWorkExecutionUncertain(
                                "Docker worker output size is invalid"
                            )
                        size = int(raw_size)
                        if size > _OUTPUT_LIMIT or total + size > _OUTPUT_LIMIT:
                            raise DockerWorkExecutionUncertain(
                                "Docker worker output exceeded its byte bound"
                            )
                        payload = self._read_exact(process.stdout, size)
                        if self._read_exact(process.stdout, 1) != b"\n":
                            raise DockerWorkExecutionUncertain(
                                "Docker worker output frame is truncated"
                            )
                        total += size
                        (stdout if parts[1] == "stdout" else stderr).extend(payload)
                        continue
                    if header.startswith(b"CAO-EXIT/1 "):
                        raw_code = header[len(b"CAO-EXIT/1 ") : -1]
                        if not raw_code.isdigit() or len(raw_code) > 3:
                            raise DockerWorkExecutionUncertain(
                                "Docker supervisor exit status is invalid"
                            )
                        exit_codes.append(int(raw_code))
                        exited_event.set()
                        return
                    raise DockerWorkExecutionUncertain(
                        "Docker supervisor output protocol is invalid"
                    )
            except BaseException as error:
                fail(error)

        def read_mcp() -> None:
            try:
                assert process is not None and process.stderr is not None
                while True:
                    header = process.stderr.readline(96)
                    if not header:
                        return
                    request = payload_after_header(
                        process.stderr, header, b"CAO-MCP/1 ", _REQUEST_LIMIT
                    )
                    if not mcp_enabled or mcp_proxy is None or endpoint is None:
                        raise DockerWorkExecutionUncertain(
                            "Docker supervisor emitted MCP without an authorized endpoint"
                        )
                    response = mcp_proxy.forward_from_docker(endpoint, request)
                    frame = self._frame(b"CAO-MCP/1 ", response)
                    with stdin_lock:
                        if process.stdin is None:
                            raise DockerWorkExecutionUncertain("Docker attach input is unavailable")
                        process.stdin.write(frame)
                        process.stdin.flush()
            except BaseException as error:
                fail(error)
                if process is not None and process.poll() is None:
                    process.kill()

        def write_stdin(payload: bytes) -> None:
            if process is None or process.stdin is None:
                raise DockerWorkExecutionUncertain("Docker attach input is unavailable")
            with stdin_lock:
                process.stdin.write(payload)
                process.stdin.flush()

        mcp_owner_stopped = False
        try:
            if mcp_proxy is not None:
                endpoint = mcp_proxy.create_bound_attempt(
                    attempt_id=attempt_id,
                    generation=generation,
                    expected_attempt_revision=expected_attempt_revision,
                    contract_hash=binding.contract_hash,
                    expires_at=lease_expires_at,
                )
            attempt_image_tag, attempt_image_id = self._build_attempt_image(
                attempt_id=attempt_id,
                generation=generation,
                executable=executable_content,
            )
            create = self._run(
                [
                    "container",
                    "create",
                    "--interactive",
                    "--name",
                    container_name,
                    "--label",
                    "cao.work.backend=docker",
                    "--label",
                    f"cao.work.attempt_sha256={attempt_hash}",
                    "--label",
                    f"cao.work.generation={generation}",
                    "--label",
                    f"cao.work.image_sha256={attempt_image_id.removeprefix('sha256:')}",
                    "--label",
                    f"cao.work.mcp_enabled={'true' if mcp_enabled else 'false'}",
                    "--platform=linux/amd64",
                    "--read-only",
                    "--network=none",
                    "--ipc=private",
                    "--cap-drop=ALL",
                    "--cap-add=SETUID",
                    "--cap-add=SETGID",
                    "--security-opt=no-new-privileges",
                    "--pids-limit=2",
                    "--memory=256m",
                    "--memory-swap=256m",
                    "--cpus=1",
                    "--user=0:0",
                    "--env=LC_ALL=C",
                    "--entrypoint=/cao-work-supervisor",
                    attempt_image_id,
                    *supervisor_args,
                ]
            )
            container_id = create.stdout.strip()
            if not re.fullmatch(r"[0-9a-f]{64}", container_id):
                raise DockerWorkBackendUnavailable("Docker returned an invalid container identity")
            try:
                process = subprocess.Popen(
                    self._docker_arguments(
                        ["container", "start", "--attach", "--interactive", container_id]
                    ),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    close_fds=True,
                    env=self._docker_environment,
                )
            except OSError as error:
                raise DockerWorkBackendUnavailable(
                    "Docker container start could not be observed"
                ) from error
            readers = (
                threading.Thread(target=read_stdout, daemon=True),
                threading.Thread(target=read_mcp, daemon=True),
            )
            for reader in readers:
                reader.start()
            timeout = min(self.timeout_seconds, max(0.1, lease_expires_at - time.time()))
            deadline = time.monotonic() + timeout
            write_stdin(self._frame(b"CAO-INPUT/1 ", worker_input))
            while not ready_event.wait(0.02):
                if protocol_errors:
                    raise protocol_errors[0]
                if process.poll() is not None:
                    raise DockerWorkExecutionUncertain(
                        "Docker supervisor exited before its setup acknowledgement"
                    )
                if time.monotonic() >= deadline:
                    raise TimeoutError("Docker supervisor exceeded its setup limit")
            if protocol_errors or not ready_payload:
                raise (
                    protocol_errors[0]
                    if protocol_errors
                    else DockerWorkExecutionUncertain(
                        "Docker supervisor setup acknowledgement is missing"
                    )
                )
            ready = ready_payload[0]
            if (
                ready.get("protocol") != 1
                or ready.get("mcp_enabled") is not mcp_enabled
                or ready.get("worker_uid") != worker_uid
                or ready.get("worker_gid") != worker_gid
                or ready.get("supervisor_source_sha256") != _SUPERVISOR_SOURCE_SHA256
                or ready.get("worker_socket_fd") != (3 if mcp_enabled else -1)
            ):
                raise DockerWorkExecutionUncertain("Docker supervisor setup evidence differs")

            def inspect_current() -> dict[str, str]:
                if container_id is None or attempt_image_id is None:
                    raise DockerWorkExecutionUncertain("Docker runtime identity is unavailable")
                return self._validate_running_container(
                    container_id,
                    attempt_id=attempt_id,
                    generation=generation,
                    image_id=attempt_image_id,
                    command=supervisor_command,
                    mcp_enabled=mcp_enabled,
                )

            runtime_identity = inspect_current()
            authorize(authorize_setup)
            if endpoint is not None:
                assert mcp_proxy is not None  # Only a bound proxy creates this endpoint.
                docker_proof = issue_docker_runtime_isolation_proof(
                    attempt_id=attempt_id,
                    generation=generation,
                    attempt_revision=expected_attempt_revision,
                    contract_hash=binding.contract_hash,
                    image_id=attempt_image_id,
                    container_id=container_id,
                    started_at=runtime_identity["started_at"],
                    worker_socket_identity=endpoint.worker_socket_identity,
                    inspect_current=inspect_current,
                )
                mcp_proxy.activate_with_isolation_proof(endpoint, docker_proof)

                def serve_proxy() -> None:
                    assert mcp_proxy is not None
                    try:
                        mcp_proxy.serve(attempt_id, generation)
                    except WorkMcpProxyError as error:
                        proxy_errors.append(error)

                proxy_thread = threading.Thread(target=serve_proxy, daemon=True)
                proxy_thread.start()
            authorize(authorize_go)
            write_stdin(b"CAO-GO/1\n")
            while not exited_event.wait(0.02):
                if protocol_errors:
                    raise protocol_errors[0]
                # Process exit can precede the reader consuming a buffered EXIT
                # frame. The reader owns protocol completion and reports EOF
                # without EXIT; the deadline still bounds a stalled reader.
                if time.monotonic() >= deadline:
                    raise TimeoutError("Docker worker exceeded its lease or runtime limit")
            if protocol_errors or not exit_codes:
                raise (
                    protocol_errors[0]
                    if protocol_errors
                    else DockerWorkExecutionUncertain("Docker supervisor exit status is missing")
                )
            if mcp_proxy is not None:
                mcp_proxy.revoke(attempt_id, generation)
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=5)
            for reader in readers:
                reader.join(timeout=5)
                if reader.is_alive():
                    raise DockerWorkExecutionUncertain("Docker protocol reader did not stop")
            final_identity = self._validate_running_container(
                container_id,
                attempt_id=attempt_id,
                generation=generation,
                image_id=attempt_image_id,
                command=supervisor_command,
                mcp_enabled=mcp_enabled,
                expected_running=False,
            )
            final_state = self._inspect_container(container_id)["State"]
            code = final_state.get("ExitCode")
            if (
                not isinstance(code, int)
                or code != exit_codes[0]
                or final_identity != runtime_identity
                or process.returncode not in (code, 0)
            ):
                raise DockerWorkExecutionUncertain(
                    "Docker worker exit status is not bound to its runtime"
                )
            if any(isinstance(error, WorkMcpProxyError) for error in proxy_errors):
                raise DockerWorkExecutionUncertain(
                    "managed Work proxy failed during Docker execution"
                )
            if not self._cleanup_container(container_id, kill=False):
                raise DockerWorkExecutionUncertain(
                    "Docker worker exited but container cleanup is uncertain"
                )
            container_id = None
            if not self._cleanup_attempt_image(attempt_image_tag):
                raise DockerWorkExecutionUncertain(
                    "Docker worker exited but image cleanup is uncertain"
                )
            attempt_image_id = None
            mcp_owner_stopped = True
            return DockerWorkExecution(
                code,
                bytes(stdout),
                bytes(stderr),
                process_stopped=True,
                container_removed=True,
                image_removed=True,
            )
        except TimeoutError:
            container_removed, image_removed = self._cleanup_failed_execution(
                attempt_id, generation, container_id, attempt_image_tag
            )
            if not container_removed or not image_removed:
                raise DockerWorkExecutionUncertain(
                    "Docker worker exceeded its limit and cleanup is uncertain"
                )
            mcp_owner_stopped = True
            raise
        except BaseException:
            container_removed, image_removed = self._cleanup_failed_execution(
                attempt_id, generation, container_id, attempt_image_tag
            )
            if not container_removed or not image_removed:
                raise DockerWorkExecutionUncertain(
                    "Docker worker or image cleanup is uncertain; reconcile before retry"
                )
            mcp_owner_stopped = True
            raise
        finally:
            if mcp_proxy is not None:
                mcp_proxy.close()
            if process is not None and process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            for reader in readers:
                reader.join(timeout=1)
            if proxy_thread is not None:
                proxy_thread.join(timeout=5)
            if mcp_owner_stopped and mcp_proxy is not None:
                if proxy_thread is not None and proxy_thread.is_alive():
                    raise DockerWorkExecutionUncertain(
                        "managed Work proxy did not stop after Docker cleanup"
                    )
                try:
                    with self.repository.cleanup_owner_lock(attempt_id, generation):
                        mcp_proxy.recover_incomplete_effects(attempt_id, generation)
                        mcp_proxy.recover_incomplete_issues(attempt_id, generation)
                except Exception as error:
                    raise DockerWorkExecutionUncertain(
                        "managed Work proxy closure could not be recorded"
                    ) from error

    def create_session(self, *args, **kwargs):
        raise _unsupported("Docker Work does not provide terminal sessions")

    def create_window(self, *args, **kwargs):
        raise _unsupported("Docker Work does not provide terminal windows")

    def send_keys(self, *args, **kwargs):
        raise _unsupported("Docker Work does not provide terminal input")

    def send_special_key(self, *args, **kwargs):
        raise _unsupported("Docker Work does not provide terminal input")

    def kill_session(self, *args, **kwargs):
        raise _unsupported("Docker Work does not provide terminal session lifecycle")

    def kill_window(self, *args, **kwargs):
        raise _unsupported("Docker Work does not provide terminal window lifecycle")

    def pipe_pane(self, *args, **kwargs):
        raise _unsupported("Docker Work does not provide terminal logging")

    def stop_pipe_pane(self, *args, **kwargs):
        raise _unsupported("Docker Work does not provide terminal logging")
