"""Install a narrow Linux x86_64 seccomp layer for isolated Work processes.

This filter blocks selected kernel interfaces that can create namespaces,
change mounts, inspect other processes, or create alternate execution paths.
The bootstrap sets dumpability to 0 before worker release, and the filter denies
prctl calls that would raise it or assign a nonzero ``PR_SET_PTRACER`` value.
Linux resets dumpability to 1 on ``execve``; therefore the isolation proof also
requires Yama ``ptrace_scope=1`` before releasing a worker and before each
effect. The filter deliberately allows ``execve`` and does not confine
executable paths or loader behavior; a separate command policy must do that.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import sys
from typing import Final


class WorkProcessSeccompUnavailable(RuntimeError):
    """The host could not install the requested seccomp filter."""


# prctl operations and Linux seccomp constants.
_PR_GET_SECCOMP: Final = 21
_PR_SET_DUMPABLE: Final = 4
_PR_SET_PTRACER: Final = 0x59616D61
_PR_SET_NO_NEW_PRIVS: Final = 38
_SECCOMP_MODE_FILTER: Final = 2
_SYS_SECCOMP_X86_64: Final = 317
_SECCOMP_SET_MODE_FILTER: Final = 1
_SECCOMP_FILTER_FLAG_TSYNC: Final = 1

_AUDIT_ARCH_X86_64: Final = 0xC000003E
_X32_SYSCALL_BIT: Final = 0x40000000
_SYS_PRCTL_X86_64: Final = 157

_SECCOMP_RET_KILL_PROCESS: Final = 0x80000000
_SECCOMP_RET_ERRNO: Final = 0x00050000
_SECCOMP_RET_ALLOW: Final = 0x7FFF0000

# Classic BPF opcodes used by the small generated filter.
_BPF_LD_W_ABS: Final = 0x20
_BPF_JMP_JEQ_K: Final = 0x15
_BPF_JMP_JSET_K: Final = 0x45
_BPF_RET_K: Final = 0x06

# seccomp_data offsets: nr at byte 0, arch at byte 4, and args[0] at byte 16.
_SECCOMP_DATA_NR: Final = 0
_SECCOMP_DATA_ARCH: Final = 4
_SECCOMP_DATA_ARG0: Final = 16
_SECCOMP_DATA_ARG1: Final = 24

# Linux x86_64 syscall numbers. The filter is architecture-gated before these
# numbers are examined. Unknown syscalls remain allowed so ordinary user-space
# runtimes continue to work; x32 calls are rejected before reaching this list.
_SYSCALL_ERRORS: Final = (
    (42, errno.EPERM),  # connect: workers use only the inherited broker socketpair
    (101, errno.EPERM),  # ptrace
    (155, errno.EPERM),  # pivot_root
    (165, errno.EPERM),  # mount
    (166, errno.EPERM),  # umount2
    (272, errno.EPERM),  # unshare
    (308, errno.EPERM),  # setns
    (310, errno.EPERM),  # process_vm_readv
    (311, errno.EPERM),  # process_vm_writev
    (319, errno.EPERM),  # memfd_create
    (321, errno.EPERM),  # bpf
    (322, errno.EPERM),  # execveat
    (425, errno.EPERM),  # io_uring_setup
    (428, errno.EPERM),  # open_tree
    (429, errno.EPERM),  # move_mount
    (430, errno.EPERM),  # fsopen
    (431, errno.EPERM),  # fsconfig
    (432, errno.EPERM),  # fsmount
    (433, errno.EPERM),  # fspick
    # Return ENOSYS so libc can fall back to legacy clone where needed. The
    # clone fallback remains usable only without any CLONE_NEW* flags.
    (435, errno.ENOSYS),  # clone3
    (438, errno.EPERM),  # pidfd_getfd
    (442, errno.EPERM),  # mount_setattr
)

# clone(2) is still needed for ordinary forks and threads. Refuse only namespace
# creation flags, including CLONE_NEWTIME, which older kernels may not define.
_CLONE_NEWNS: Final = 0x00020000
_CLONE_NEWCGROUP: Final = 0x02000000
_CLONE_NEWUTS: Final = 0x04000000
_CLONE_NEWIPC: Final = 0x08000000
_CLONE_NEWUSER: Final = 0x10000000
_CLONE_NEWPID: Final = 0x20000000
_CLONE_NEWNET: Final = 0x40000000
_CLONE_NEWTIME: Final = 0x00000080
_CLONE_NAMESPACE_FLAGS: Final = (
    _CLONE_NEWNS
    | _CLONE_NEWCGROUP
    | _CLONE_NEWUTS
    | _CLONE_NEWIPC
    | _CLONE_NEWUSER
    | _CLONE_NEWPID
    | _CLONE_NEWNET
    | _CLONE_NEWTIME
)


class _SockFilter(ctypes.Structure):
    _fields_ = (
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint),
    )


class _SockFprog(ctypes.Structure):
    _fields_ = (
        ("len", ctypes.c_ushort),
        ("filter", ctypes.POINTER(_SockFilter)),
    )


def _build_filter_instructions() -> tuple[_SockFilter, ...]:
    """Build a classic BPF program that fails closed across ABI boundaries."""

    instructions: list[_SockFilter] = [
        _SockFilter(_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_ARCH),
        # On a match, skip the following kill. A different audit architecture
        # reaches that kill before any syscall number is interpreted.
        _SockFilter(_BPF_JMP_JEQ_K, 1, 0, _AUDIT_ARCH_X86_64),
        _SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_KILL_PROCESS),
        _SockFilter(_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_NR),
        # x32 shares AUDIT_ARCH_X86_64 but marks syscall numbers with this bit.
        _SockFilter(_BPF_JMP_JSET_K, 0, 1, _X32_SYSCALL_BIT),
        _SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_KILL_PROCESS),
    ]

    denied = _SECCOMP_RET_ERRNO | errno.EPERM
    for syscall_number, error_number in _SYSCALL_ERRORS:
        instructions.extend(
            (
                _SockFilter(_BPF_JMP_JEQ_K, 0, 1, syscall_number),
                _SockFilter(
                    _BPF_RET_K,
                    0,
                    0,
                    _SECCOMP_RET_ERRNO | error_number,
                ),
            )
        )

    # Keep the bootstrap's initial PR_SET_DUMPABLE(0), but prevent later code
    # from enabling core dumps. PR_SET_PTRACER(0) only clears an exception;
    # every nonzero PID, including PR_SET_PTRACER_ANY, is denied. Other prctl
    # options continue through the existing allow path.
    instructions.extend(
        (
            # Non-prctl syscalls jump directly to the clone checks below while
            # the accumulator still contains the syscall number.
            _SockFilter(_BPF_JMP_JEQ_K, 0, 12, _SYS_PRCTL_X86_64),
            _SockFilter(_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_ARG0),
            # A dumpable=0 request is allowed; every nonzero value is denied.
            _SockFilter(_BPF_JMP_JEQ_K, 0, 4, _PR_SET_DUMPABLE),
            _SockFilter(_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_ARG1),
            _SockFilter(_BPF_JMP_JEQ_K, 1, 0, 0),
            _SockFilter(_BPF_RET_K, 0, 0, denied),
            _SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_ALLOW),
            _SockFilter(_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_ARG0),
            # A ptracer value of zero only clears the exception; nonzero widens it.
            _SockFilter(_BPF_JMP_JEQ_K, 0, 3, _PR_SET_PTRACER),
            _SockFilter(_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_ARG1),
            _SockFilter(_BPF_JMP_JEQ_K, 1, 0, 0),
            _SockFilter(_BPF_RET_K, 0, 0, denied),
            # PR_SET_PTRACER(0) and unrelated prctl options end here. Their
            # accumulator is an argument, not a syscall number for clone tests.
            _SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_ALLOW),
        )
    )

    # clone3 takes a pointer to a structure that classic BPF cannot inspect, so
    # it is unavailable above (with ENOSYS to support libc fallback). For
    # clone(2), the namespace flags are scalar arg0 and can be checked without
    # breaking plain fork/thread creation.
    instructions.extend(
        (
            # If this is not clone, skip the three checks and return ALLOW.
            _SockFilter(_BPF_JMP_JEQ_K, 0, 3, 56),  # clone
            _SockFilter(_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_ARG0),
            _SockFilter(_BPF_JMP_JSET_K, 0, 1, _CLONE_NAMESPACE_FLAGS),
            _SockFilter(_BPF_RET_K, 0, 0, denied),
            _SockFilter(_BPF_RET_K, 0, 0, _SECCOMP_RET_ALLOW),
        )
    )
    return tuple(instructions)


def _prctl(
    option: int,
    arg2: int = 0,
    arg3: int = 0,
    arg4: int = 0,
    arg5: int = 0,
) -> int:
    """Call libc prctl with machine-width unsigned-long arguments."""

    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.restype = ctypes.c_int
    result = prctl(
        ctypes.c_int(option),
        ctypes.c_ulong(arg2),
        ctypes.c_ulong(arg3),
        ctypes.c_ulong(arg4),
        ctypes.c_ulong(arg5),
    )
    if result == -1:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    return int(result)


def _seccomp_tsync(program_address: int) -> None:
    """Install the filter atomically across the process thread group."""

    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    result = syscall(
        ctypes.c_long(_SYS_SECCOMP_X86_64),
        ctypes.c_ulong(_SECCOMP_SET_MODE_FILTER),
        ctypes.c_ulong(_SECCOMP_FILTER_FLAG_TSYNC),
        ctypes.c_void_p(program_address),
    )
    if result == -1:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    if result != 0:
        # seccomp(2) returns the thread ID that prevented synchronization when
        # TSYNC cannot update the entire thread group.
        raise OSError(
            errno.EBUSY,
            f"SECCOMP_FILTER_FLAG_TSYNC failed for thread {result}",
        )


def _require_supported_runtime() -> None:
    if (
        sys.platform != "linux"
        or platform.machine().lower() not in {"x86_64", "amd64"}
        or ctypes.sizeof(ctypes.c_void_p) != 8
    ):
        raise WorkProcessSeccompUnavailable("the Work process seccomp filter requires Linux x86_64")


def install_work_process_seccomp_filter() -> None:
    """Install the deny-list filter across this thread group and descendants.

    The call synchronizes the filter across existing threads and descendants.
    Blocked syscalls return EPERM except clone3, which returns ENOSYS so user
    space can fall back to legacy clone with namespace flags checked here.
    Call it in a dedicated bootstrap process. Any unsupported platform, kernel,
    or failed kernel operation raises :class:`WorkProcessSeccompUnavailable`.
    """

    _require_supported_runtime()

    try:
        mode = _prctl(_PR_GET_SECCOMP)
    except OSError as exc:
        raise WorkProcessSeccompUnavailable(
            f"seccomp is unavailable on this Linux kernel: {exc}"
        ) from exc
    if mode == 1:
        raise WorkProcessSeccompUnavailable(
            "seccomp strict mode is active; a filter cannot be installed"
        )
    if mode not in (0, _SECCOMP_MODE_FILTER):
        raise WorkProcessSeccompUnavailable(f"seccomp reported unsupported mode {mode}")

    try:
        _prctl(_PR_SET_DUMPABLE, 0)
    except OSError as exc:
        raise WorkProcessSeccompUnavailable(
            f"could not disable process dumpability before seccomp: {exc}"
        ) from exc

    instructions = _build_filter_instructions()
    filter_array_type = _SockFilter * len(instructions)
    filter_array = filter_array_type(*instructions)
    program = _SockFprog(len(instructions), ctypes.cast(filter_array, ctypes.POINTER(_SockFilter)))
    program_address = ctypes.cast(ctypes.pointer(program), ctypes.c_void_p).value
    if program_address is None:  # pragma: no cover - ctypes always yields an address
        raise WorkProcessSeccompUnavailable("could not prepare the seccomp filter")

    try:
        _prctl(_PR_SET_NO_NEW_PRIVS, 1)
    except OSError as exc:
        raise WorkProcessSeccompUnavailable(
            f"could not set PR_SET_NO_NEW_PRIVS before seccomp: {exc}"
        ) from exc

    try:
        _seccomp_tsync(program_address)
    except OSError as exc:
        raise WorkProcessSeccompUnavailable(
            "could not install the seccomp filter across all threads with "
            f"SECCOMP_FILTER_FLAG_TSYNC: {exc}"
        ) from exc
