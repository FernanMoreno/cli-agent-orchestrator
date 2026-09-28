"""Stage identity-checked static ELF bytes in a sealed anonymous file."""

from __future__ import annotations

import errno
import fcntl
import os
import posixpath
import stat
import sys
from dataclasses import dataclass

from cli_agent_orchestrator.models.work_contract import ExecutableIdentity
from cli_agent_orchestrator.services.work_elf_identity import identify_static_executable

_EXECUTABLE_MODE = 0o500
_MAX_EXECUTABLE_BYTES = 8 * 1024 * 1024
_MFD_EXEC_UAPI_FLAG = 0x0010
_FCNTL_UAPI_CONSTANTS = {
    "F_ADD_SEALS": 1033,
    "F_GET_SEALS": 1034,
    "F_SEAL_SEAL": 0x0001,
    "F_SEAL_SHRINK": 0x0002,
    "F_SEAL_GROW": 0x0004,
    "F_SEAL_WRITE": 0x0008,
}


class WorkExecutableStagingUnavailable(OSError):
    """The kernel or runtime cannot provide a required immutable memfd feature."""


@dataclass(slots=True)
class StagedExecutable:
    """Own the sealed memfd and O_PATH descriptors for one staged snapshot.

    ``opath_fd`` is only an object reference; it does not grant execution
    authority or guarantee that Landlock accepts the anonymous memfd as a rule.
    The caller must gate execution on successful rule installation and constrain
    inherited executable descriptors and proc-FD routes to the admitted set.
    """

    memfd_fd: int
    opath_fd: int
    exec_path: str
    identity: ExecutableIdentity
    _closed: bool = False

    def __enter__(self) -> StagedExecutable:
        if self._closed:
            raise RuntimeError("staged executable is already closed")
        return self

    def __exit__(self, _exception_type, _exception, _traceback) -> None:
        self.close()

    def close(self) -> None:
        """Close owned descriptors once, attempting both even after an error."""

        if self._closed:
            return
        self._closed = True
        descriptors = (self.opath_fd, self.memfd_fd)
        self.opath_fd = -1
        self.memfd_fd = -1
        first_error: OSError | None = None
        for descriptor in descriptors:
            if descriptor < 0:
                continue
            try:
                os.close(descriptor)
            except OSError as exc:
                if exc.errno != errno.EBADF and first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error


def bubblewrap_bind_data_arguments(
    staged: StagedExecutable, destination: str = "/exec"
) -> tuple[str, ...]:
    """Build Bubblewrap arguments to copy a verified stage to a read-only path.

    The caller must pass the source FD only to the accepted Bubblewrap process,
    for example with ``pass_fds`` and ``close_fds=True``. This helper grants no
    Work authority and does not prove that Bubblewrap closes the source FD in
    its child.
    """

    if not isinstance(staged, StagedExecutable):
        raise TypeError("staged must be a StagedExecutable")
    if (
        type(staged._closed) is not bool
        or staged._closed
        or type(staged.memfd_fd) is not int
        or staged.memfd_fd < 0
        or type(staged.opath_fd) is not int
        or staged.opath_fd < 0
        or staged.exec_path != f"/proc/self/fd/{staged.memfd_fd}"
        or not isinstance(staged.identity, ExecutableIdentity)
    ):
        raise ValueError("staged executable is closed or malformed")

    if not isinstance(destination, str):
        raise TypeError("destination must be a string")
    if (
        not destination.startswith("/")
        or destination.startswith("//")
        or destination == "/"
        or "\0" in destination
        or posixpath.normpath(destination) != destination
    ):
        raise ValueError("destination must be a canonical absolute non-root path")

    try:
        memfd_info = os.fstat(staged.memfd_fd)
        opath_info = os.fstat(staged.opath_fd)
        opath_flags = fcntl.fcntl(staged.opath_fd, fcntl.F_GETFL)
    except OSError as exc:
        raise ValueError("staged executable is closed or malformed") from exc

    path_flag = _required_constant(os, "O_PATH")
    if (
        not stat.S_ISREG(memfd_info.st_mode)
        or stat.S_IMODE(memfd_info.st_mode) != _EXECUTABLE_MODE
        or not stat.S_ISREG(opath_info.st_mode)
        or (memfd_info.st_dev, memfd_info.st_ino) != (opath_info.st_dev, opath_info.st_ino)
        or opath_flags & path_flag != path_flag
    ):
        raise ValueError("staged executable metadata is malformed or has the wrong mode")

    required_seals = (
        _linux_fcntl_constant("F_SEAL_WRITE")
        | _linux_fcntl_constant("F_SEAL_GROW")
        | _linux_fcntl_constant("F_SEAL_SHRINK")
        | _linux_fcntl_constant("F_SEAL_SEAL")
    )
    try:
        installed_seals = fcntl.fcntl(staged.memfd_fd, _linux_fcntl_constant("F_GET_SEALS"))
    except OSError as exc:
        raise ValueError("staged executable is missing required seals") from exc
    if installed_seals & required_seals != required_seals:
        raise ValueError("staged executable is missing required seals")
    if not 1 <= memfd_info.st_size <= _MAX_EXECUTABLE_BYTES:
        raise ValueError("staged executable size exceeds the content bound")

    snapshot = _read_exact(staged.memfd_fd, memfd_info.st_size)
    try:
        observed_identity = identify_static_executable(staged.identity.command_token, snapshot)
    except ValueError as exc:
        raise ValueError("staged executable identity does not match sealed bytes") from exc
    if observed_identity != staged.identity:
        raise ValueError("staged executable identity does not match sealed bytes")

    try:
        os.lseek(staged.memfd_fd, 0, os.SEEK_SET)
    except OSError as exc:
        raise ValueError("staged executable source FD cannot be rewound") from exc

    return ("--perms", "0500", "--ro-bind-data", str(staged.memfd_fd), destination)


def stage_static_executable(
    identity: ExecutableIdentity, executable_bytes: bytes
) -> StagedExecutable:
    """Verify and seal static executable bytes in an anonymous kernel object.

    The supplied snapshot is checked before allocating a memfd, then copied to
    a new executable memfd. Required seals prevent later content or size
    changes. The returned ``opath_fd`` identifies that same memfd, and
    ``exec_path`` resolves through its live proc-FD link. Staging does not
    install execution policy or guarantee that Landlock accepts the memfd as a
    path rule (the tested kernel returns ``EBADFD``). Callers must fail closed
    unless the exact Landlock rule is installed, and must constrain inherited
    executable FDs and proc-FD execution routes to the admitted set. No source
    path is opened or retained.
    """

    if not isinstance(identity, ExecutableIdentity):
        raise TypeError("identity must be an ExecutableIdentity")
    if not isinstance(executable_bytes, (bytes, bytearray, memoryview)):
        raise TypeError("executable_bytes must be bytes-like")
    snapshot = bytes(executable_bytes)
    computed_identity = identify_static_executable(identity.command_token, snapshot)
    if computed_identity != identity:
        raise ValueError("identity does not match executable bytes")

    if not sys.platform.startswith("linux"):
        raise WorkExecutableStagingUnavailable(
            errno.ENOSYS, "static executable memfd staging requires Linux"
        )

    memfd_flags = _required_constant(os, "MFD_ALLOW_SEALING")
    # Python versions before exposing MFD_EXEC still pass Linux's stable
    # UAPI flag through memfd_create. The syscall below verifies kernel
    # support and fails closed with EINVAL when the flag is unavailable.
    memfd_flags |= _mfd_exec_flag()
    memfd_flags |= _required_constant(os, "MFD_CLOEXEC")
    add_seals = _linux_fcntl_constant("F_ADD_SEALS")
    get_seals = _linux_fcntl_constant("F_GET_SEALS")
    required_seals = (
        _linux_fcntl_constant("F_SEAL_WRITE")
        | _linux_fcntl_constant("F_SEAL_GROW")
        | _linux_fcntl_constant("F_SEAL_SHRINK")
        | _linux_fcntl_constant("F_SEAL_SEAL")
    )
    path_flag = _required_constant(os, "O_PATH")
    cloexec_flag = _required_constant(os, "O_CLOEXEC")
    if not callable(getattr(os, "memfd_create", None)):
        raise WorkExecutableStagingUnavailable(errno.ENOSYS, "os.memfd_create is unavailable")

    memfd_fd = -1
    opath_fd = -1
    try:
        try:
            memfd_fd = os.memfd_create("cao-work-executable", memfd_flags)
        except OSError as exc:
            if exc.errno in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP, errno.EPERM}:
                raise WorkExecutableStagingUnavailable(
                    exc.errno,
                    "kernel cannot create an executable, sealable memfd",
                ) from exc
            raise

        _write_all(memfd_fd, snapshot)
        os.fchmod(memfd_fd, _EXECUTABLE_MODE)

        try:
            fcntl.fcntl(memfd_fd, add_seals, required_seals)
            installed_seals = fcntl.fcntl(memfd_fd, get_seals)
        except OSError as exc:
            if exc.errno in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP, errno.EPERM}:
                raise WorkExecutableStagingUnavailable(
                    exc.errno, "kernel cannot apply required memfd seals"
                ) from exc
            raise
        if installed_seals & required_seals != required_seals:
            raise WorkExecutableStagingUnavailable(
                errno.EOPNOTSUPP, "kernel did not install every required memfd seal"
            )

        memfd_info = os.fstat(memfd_fd)
        if (
            not stat.S_ISREG(memfd_info.st_mode)
            or stat.S_IMODE(memfd_info.st_mode) != _EXECUTABLE_MODE
            or memfd_info.st_size != len(snapshot)
        ):
            raise OSError(errno.EIO, "staged executable metadata changed unexpectedly")

        readback = _read_exact(memfd_fd, memfd_info.st_size)
        readback_identity = identify_static_executable(identity.command_token, readback)
        if readback != snapshot or readback_identity != identity:
            raise OSError(errno.EIO, "sealed executable readback does not match its identity")

        exec_path = f"/proc/self/fd/{memfd_fd}"
        opath_fd = os.open(exec_path, path_flag | cloexec_flag)
        opath_info = os.fstat(opath_fd)
        opath_flags = fcntl.fcntl(opath_fd, fcntl.F_GETFL)
        if (
            opath_flags & path_flag != path_flag
            or not stat.S_ISREG(opath_info.st_mode)
            or (opath_info.st_dev, opath_info.st_ino) != (memfd_info.st_dev, memfd_info.st_ino)
        ):
            raise OSError(errno.EIO, "proc-FD path does not identify the sealed executable")

        staged = StagedExecutable(memfd_fd, opath_fd, exec_path, identity)
        memfd_fd = -1
        opath_fd = -1
        return staged
    except BaseException:
        _close_quietly(opath_fd)
        _close_quietly(memfd_fd)
        raise


def _required_constant(module: object, name: str) -> int:
    value = getattr(module, name, None)
    if type(value) is not int:
        raise WorkExecutableStagingUnavailable(
            errno.ENOSYS, f"required Linux constant {name} is unavailable"
        )
    return value


def _mfd_exec_flag() -> int:
    value = getattr(os, "MFD_EXEC", None)
    if type(value) is int:
        return value
    return _MFD_EXEC_UAPI_FLAG


def _linux_fcntl_constant(name: str) -> int:
    value = getattr(fcntl, name, None)
    if type(value) is int:
        return value
    try:
        return _FCNTL_UAPI_CONSTANTS[name]
    except KeyError as exc:
        raise WorkExecutableStagingUnavailable(
            errno.ENOSYS, f"required Linux fcntl constant {name} is unavailable"
        ) from exc


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        try:
            written = os.write(descriptor, view[offset:])
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError(errno.EIO, "short write while staging executable bytes")
        offset += written


def _read_exact(descriptor: int, size: int) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    while offset < size:
        try:
            chunk = os.pread(descriptor, size - offset, offset)
        except InterruptedError:
            continue
        if not chunk:
            raise OSError(errno.EIO, "short read while verifying staged executable")
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _close_quietly(descriptor: int) -> None:
    if descriptor >= 0:
        try:
            os.close(descriptor)
        except OSError:
            pass
