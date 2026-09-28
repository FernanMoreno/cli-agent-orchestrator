"""Install irreversible, path-based filesystem rules for one child.

Call :func:`install_exec_policy` or :func:`install_path_policy` in the
dedicated child immediately before its launcher ``exec``. Both helpers use
``PATH_BENEATH`` rules anchored by ``O_PATH`` descriptors. The path policy
grants read, write, and execute rights to regular files or recursively beneath
directories. On ABI 9 and later it can also grant Unix socket resolution to
one exact socket inode. Filesystem rights supported by the kernel and
recognized here are otherwise denied by default.

These helpers enforce path-based access only. They do not confine already-open
file descriptions, inherited descriptors used through ``/proc/self/fd``, or
other pre-opened objects, and do not establish executable identity or isolate
network/proxy access. Callers must enforce a separate file-descriptor and
seccomp boundary. The Landlock domain is inherited by forked and exec'd
descendants.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntFlag

try:
    import fcntl
except ImportError:  # pragma: no cover - available on Linux
    fcntl = None  # type: ignore[assignment]


_FS_EXECUTE = 1 << 0
_FS_WRITE_FILE = 1 << 1
_FS_READ_FILE = 1 << 2
_FS_READ_DIR = 1 << 3
_FS_REMOVE_DIR = 1 << 4
_FS_REMOVE_FILE = 1 << 5
_FS_MAKE_CHAR = 1 << 6
_FS_MAKE_DIR = 1 << 7
_FS_MAKE_REG = 1 << 8
_FS_MAKE_SOCK = 1 << 9
_FS_MAKE_FIFO = 1 << 10
_FS_MAKE_BLOCK = 1 << 11
_FS_MAKE_SYM = 1 << 12
_FS_REFER = 1 << 13
_FS_TRUNCATE = 1 << 14
_FS_IOCTL_DEV = 1 << 15
_FS_RESOLVE_UNIX = 1 << 16
_FS_ABI_1_RIGHTS = (1 << 13) - 1
_CREATE_RULESET_VERSION = 1 << 0
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38


class PathPolicyAccess(IntFlag):
    """Semantic path-policy rights accepted by :class:`PathPolicyRule`."""

    READ = 1 << 0
    WRITE = 1 << 1
    EXECUTE = 1 << 2
    RESOLVE_UNIX = 1 << 3


_PATH_POLICY_ACCESS_MASK = (
    PathPolicyAccess.READ
    | PathPolicyAccess.WRITE
    | PathPolicyAccess.EXECUTE
    | PathPolicyAccess.RESOLVE_UNIX
)


@dataclass(frozen=True)
class _LandlockSyscalls:
    create_ruleset: int
    add_rule: int
    restrict_self: int


@dataclass(frozen=True)
class PathPolicyRule:
    """Grant typed path-based access through one ``O_PATH`` descriptor.

    A regular-file descriptor anchors an exact file rule. A directory
    descriptor anchors a recursive rule for that directory and its contents.
    ``RESOLVE_UNIX`` instead requires an exact AF_UNIX socket inode and cannot
    be combined with file or directory access.
    """

    fd: int
    access: PathPolicyAccess

    def __post_init__(self) -> None:
        if type(self.fd) is not int:
            raise TypeError("fd must be an integer file descriptor")
        if self.fd < 0:
            raise ValueError("fd must be a non-negative file descriptor")
        if type(self.access) is not PathPolicyAccess:
            raise TypeError("access must be a PathPolicyAccess value")
        if not self.access:
            raise ValueError("a path rule must grant at least one access right")
        if int(self.access) & ~int(_PATH_POLICY_ACCESS_MASK):
            raise ValueError("path rule contains unsupported path-policy access")


class _RulesetAttr(ctypes.Structure):
    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    ]


class _PathBeneathAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def _landlock_syscalls() -> _LandlockSyscalls:
    """Return syscall numbers verified against Linux UAPI headers."""

    machine = platform.machine().lower()
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise OSError(errno.ENOSYS, "unsupported architecture for Landlock syscall ABI")
    if machine in {"x86_64", "amd64"}:
        return _LandlockSyscalls(444, 445, 446)
    if machine in {"aarch64", "arm64"}:
        return _LandlockSyscalls(444, 445, 446)
    raise OSError(
        errno.ENOSYS,
        f"unsupported architecture for Landlock syscall ABI: {machine or 'unknown'}",
    )


def _landlock_syscall(number: int, *args: object) -> int:
    """Call a Landlock syscall and convert errno into ``OSError``."""

    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    result = syscall(number, *args)
    if result < 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    return int(result)


def _query_abi_version() -> int:
    """Return the kernel's Landlock ABI version, or raise the syscall error."""

    numbers = _landlock_syscalls()
    return _landlock_syscall(
        numbers.create_ruleset,
        None,
        0,
        _CREATE_RULESET_VERSION,
    )


def _set_no_new_privs() -> None:
    """Set ``PR_SET_NO_NEW_PRIVS`` on the calling task."""

    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    prctl.restype = ctypes.c_int
    if prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _validate_targets(target_fds: Sequence[int]) -> tuple[int, ...]:
    if isinstance(target_fds, (str, bytes, bytearray)) or not isinstance(target_fds, Sequence):
        raise ValueError("target_fds must be a non-empty sequence of integer file descriptors")
    if not target_fds:
        raise ValueError("target_fds must be a non-empty sequence of integer file descriptors")
    if fcntl is None or not hasattr(os, "O_PATH"):
        raise OSError(errno.ENOSYS, "O_PATH file descriptors are unavailable on this platform")

    unique_fds: list[int] = []
    seen_identities: set[tuple[int, int]] = set()
    for fd in target_fds:
        if type(fd) is not int:
            raise TypeError("target_fds must contain integer file descriptors")
        descriptor_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        if descriptor_flags & os.O_PATH != os.O_PATH:
            raise ValueError(f"file descriptor {fd} is not opened with O_PATH")
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"file descriptor {fd} does not identify a regular file")
        identity = (metadata.st_dev, metadata.st_ino)
        if identity not in seen_identities:
            seen_identities.add(identity)
            unique_fds.append(fd)
    return tuple(unique_fds)


def _is_anonymous_socket_inode(fd: int) -> bool:
    """Return whether an O_PATH socket descriptor targets socketfs itself.

    ``fstat`` reports ``S_IFSOCK`` for both filesystem Unix socket nodes and
    O_PATH handles opened through ``/proc/self/fd`` for ordinary socket FDs.
    Only the former can name a pathname socket for Landlock's RESOLVE_UNIX
    right, so use procfs' descriptor link to distinguish them. If procfs is
    unavailable, callers fail closed rather than infer a socket family.
    """

    target = os.readlink(f"/proc/self/fd/{fd}")
    return target.startswith("socket:[") and target.endswith("]") and target[8:-1].isdigit()


def _validate_path_rules(
    rules: Sequence[PathPolicyRule],
) -> tuple[tuple[PathPolicyRule, bool], ...]:
    if isinstance(rules, (str, bytes, bytearray)) or not isinstance(rules, Sequence):
        raise ValueError("rules must be a sequence of PathPolicyRule values")
    if fcntl is None or not hasattr(os, "O_PATH"):
        raise OSError(errno.ENOSYS, "O_PATH file descriptors are unavailable on this platform")

    validated: list[tuple[PathPolicyRule, bool]] = []
    seen_fds: set[int] = set()
    seen_identities: set[tuple[int, int]] = set()
    for rule in rules:
        if not isinstance(rule, PathPolicyRule):
            raise TypeError("rules must contain PathPolicyRule values")
        if rule.fd in seen_fds:
            raise ValueError(f"duplicate path-policy descriptor {rule.fd}")
        seen_fds.add(rule.fd)

        descriptor_flags = fcntl.fcntl(rule.fd, fcntl.F_GETFL)
        if descriptor_flags & os.O_PATH != os.O_PATH:
            raise ValueError(f"file descriptor {rule.fd} is not opened with O_PATH")
        metadata = os.fstat(rule.fd)
        if rule.access & PathPolicyAccess.RESOLVE_UNIX:
            if rule.access != PathPolicyAccess.RESOLVE_UNIX:
                raise ValueError("RESOLVE_UNIX cannot be combined with READ, WRITE, or EXECUTE")
            if not stat.S_ISSOCK(metadata.st_mode):
                raise ValueError(
                    f"file descriptor {rule.fd} does not identify an AF_UNIX socket inode"
                )
            if _is_anonymous_socket_inode(rule.fd):
                raise ValueError(
                    f"file descriptor {rule.fd} does not identify an AF_UNIX socket inode"
                )
            is_directory = False
        else:
            is_directory = stat.S_ISDIR(metadata.st_mode)
            if not is_directory and not stat.S_ISREG(metadata.st_mode):
                raise ValueError(
                    f"file descriptor {rule.fd} does not identify a regular file or directory"
                )
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in seen_identities:
            raise ValueError(f"duplicate path-policy target for descriptor {rule.fd}")
        seen_identities.add(identity)
        validated.append((rule, is_directory))
    return tuple(validated)


def _handled_fs_rights(abi_version: int) -> int:
    """Return known filesystem rights supported by the queried Landlock ABI."""

    handled = _FS_ABI_1_RIGHTS
    if abi_version >= 2:
        handled |= _FS_REFER
    if abi_version >= 3:
        handled |= _FS_TRUNCATE
    if abi_version >= 5:
        handled |= _FS_IOCTL_DEV
    if abi_version >= 9:
        handled |= _FS_RESOLVE_UNIX
    return handled


def _path_rule_rights(
    access: PathPolicyAccess,
    *,
    is_directory: bool,
    abi_version: int,
) -> int:
    """Translate semantic access into rights supported by ``abi_version``."""

    if access & PathPolicyAccess.RESOLVE_UNIX:
        if access != PathPolicyAccess.RESOLVE_UNIX:
            raise ValueError("RESOLVE_UNIX cannot be combined with READ, WRITE, or EXECUTE")
        if abi_version < 9:
            raise OSError(errno.EOPNOTSUPP, "RESOLVE_UNIX requires Landlock ABI 9")
        return _FS_RESOLVE_UNIX

    allowed = 0
    if access & PathPolicyAccess.READ:
        allowed |= _FS_READ_FILE
        if is_directory:
            allowed |= _FS_READ_DIR
    if access & PathPolicyAccess.WRITE:
        allowed |= _FS_WRITE_FILE
        if is_directory:
            allowed |= (
                _FS_REMOVE_DIR
                | _FS_REMOVE_FILE
                | _FS_MAKE_CHAR
                | _FS_MAKE_DIR
                | _FS_MAKE_REG
                | _FS_MAKE_SOCK
                | _FS_MAKE_FIFO
                | _FS_MAKE_BLOCK
                | _FS_MAKE_SYM
            )
            if abi_version >= 2:
                allowed |= _FS_REFER
        if abi_version >= 3:
            allowed |= _FS_TRUNCATE
    if access & PathPolicyAccess.EXECUTE:
        allowed |= _FS_EXECUTE
    return allowed


def install_exec_policy(target_fds: Sequence[int]) -> None:
    """Add path-based ``FS_EXECUTE`` rules for targets identified by ``target_fds``.

    Every target must be an ``O_PATH`` descriptor to a regular file. This
    operation is irreversible and intended only for the dedicated child just
    before its launcher ``exec``. ``PATH_BENEATH`` governs path-based execution;
    these rules do not establish executable identity or block pre-opened objects
    reached through file descriptors such as ``/proc/self/fd``. The caller must
    enforce a separate FD inheritance and seccomp boundary, and keep staged
    files and their path policy immutable for the process tree's lifetime.
    """

    unique_fds = _validate_targets(target_fds)
    numbers = _landlock_syscalls()
    abi_version = _query_abi_version()
    if abi_version < 1:
        raise OSError(errno.EOPNOTSUPP, "Landlock ABI 1 with FS_EXECUTE is required")

    ruleset_attr = _RulesetAttr(handled_access_fs=_FS_EXECUTE)
    ruleset_fd = _landlock_syscall(
        numbers.create_ruleset,
        ctypes.byref(ruleset_attr),
        ctypes.sizeof(ruleset_attr),
        0,
    )
    try:
        # Do not let an intermediate ruleset descriptor leak through launcher exec.
        os.set_inheritable(ruleset_fd, False)
        for target_fd in unique_fds:
            rule = _PathBeneathAttr(allowed_access=_FS_EXECUTE, parent_fd=target_fd)
            _landlock_syscall(
                numbers.add_rule,
                ruleset_fd,
                _RULE_PATH_BENEATH,
                ctypes.byref(rule),
                0,
            )

        # Keep NNP adjacent to restrict_self: no other setup runs after this point.
        _set_no_new_privs()
        _landlock_syscall(numbers.restrict_self, ruleset_fd, 0)
    finally:
        os.close(ruleset_fd)


def install_path_policy(rules: Sequence[PathPolicyRule]) -> None:
    """Install a path-only read/write/execute policy for one child.

    Each rule must identify an ``O_PATH`` descriptor to a regular file or
    directory, except that ``RESOLVE_UNIX`` identifies an exact AF_UNIX socket
    inode. A regular file rule is exact; a directory rule is recursive.
    ``READ`` grants file reads and, for directories, directory listing.
    ``WRITE`` grants file writes and truncation where supported; for directory
    rules it also grants child creation, removal, and cross-directory moves
    when the ABI supports ``REFER``. ``EXECUTE`` grants path-based execution;
    grant ``READ`` as well where the kernel checks reads while loading a file.

    All recognized filesystem rights supported by the queried ABI are handled
    by the ruleset. Matching rules add their grants, and rights not granted by
    any matching rule are denied. An empty sequence installs a deny-all policy
    for those rights. ``WRITE`` grants ``TRUNCATE`` from ABI 3 when supported;
    directory ``WRITE`` also grants ``REFER`` from ABI 2. ``IOCTL_DEV`` from
    ABI 5 is handled when supported but never granted by this API.
    ``RESOLVE_UNIX`` grants only the ABI 9 Unix-socket resolution right and
    cannot be combined with ``READ``, ``WRITE``, or ``EXECUTE``.

    This is path policy only. It cannot revoke access through file descriptions
    or other objects opened before restriction, and does not confine inherited
    descriptors or access through ``/proc/self/fd``. Call it in a dedicated
    child immediately before its launcher ``exec`` and enforce a separate file
    descriptor and seccomp boundary.
    """

    validated_rules = _validate_path_rules(rules)
    numbers = _landlock_syscalls()
    abi_version = _query_abi_version()
    if abi_version < 1:
        raise OSError(errno.EOPNOTSUPP, "Landlock ABI 1 is required for path policy")
    if abi_version < 9 and any(
        rule.access == PathPolicyAccess.RESOLVE_UNIX for rule, _is_directory in validated_rules
    ):
        raise OSError(errno.EOPNOTSUPP, "RESOLVE_UNIX requires Landlock ABI 9")

    ruleset_attr = _RulesetAttr(handled_access_fs=_handled_fs_rights(abi_version))
    ruleset_fd = _landlock_syscall(
        numbers.create_ruleset,
        ctypes.byref(ruleset_attr),
        ctypes.sizeof(ruleset_attr),
        0,
    )
    try:
        os.set_inheritable(ruleset_fd, False)
        for rule, is_directory in validated_rules:
            path_rule = _PathBeneathAttr(
                allowed_access=_path_rule_rights(
                    rule.access,
                    is_directory=is_directory,
                    abi_version=abi_version,
                ),
                parent_fd=rule.fd,
            )
            _landlock_syscall(
                numbers.add_rule,
                ruleset_fd,
                _RULE_PATH_BENEATH,
                ctypes.byref(path_rule),
                0,
            )

        _set_no_new_privs()
        _landlock_syscall(numbers.restrict_self, ruleset_fd, 0)
    finally:
        os.close(ruleset_fd)
