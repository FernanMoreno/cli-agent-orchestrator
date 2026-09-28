from __future__ import annotations

import ctypes
import errno
import os
import platform
import shutil
import socket
import struct
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from cli_agent_orchestrator.services import work_process_landlock as landlock


def _open_path(path: Path) -> int:
    return os.open(path, os.O_PATH | os.O_CLOEXEC)


def _require_landlock_abi_one() -> int:
    try:
        abi_version = landlock._query_abi_version()
    except OSError as exc:
        if exc.errno in {errno.ENOSYS, errno.EOPNOTSUPP}:
            pytest.skip(f"kernel does not support Landlock ABI 1: errno={exc.errno}")
        raise
    assert abi_version >= 1
    return abi_version


def _resolve_unix_access() -> landlock.PathPolicyAccess:
    return landlock.PathPolicyAccess.RESOLVE_UNIX


def _write_static_exit_binary(path: Path) -> None:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        elf_machine = 62
        code = b"\xb8\x3c\x00\x00\x00\x31\xff\x0f\x05"
    elif machine in {"aarch64", "arm64"}:
        elf_machine = 183
        code = b"\xa8\x0b\x80\xd2\x00\x00\x80\xd2\x01\x00\x00\xd4"
    else:
        raise OSError(errno.ENOSYS, "test executable is unavailable for this architecture")

    base_address = 0x400000
    ehdr_size = 64
    phdr_size = 56
    entry = base_address + ehdr_size + phdr_size
    ident = b"\x7fELF" + bytes((2, 1, 1, 0)) + bytes(8)
    ehdr = ident + struct.pack(
        "<HHIQQQIHHHHHH",
        2,
        elf_machine,
        1,
        entry,
        ehdr_size,
        0,
        0,
        ehdr_size,
        phdr_size,
        1,
        0,
        0,
        0,
    )
    binary_size = ehdr_size + phdr_size + len(code)
    phdr = struct.pack(
        "<IIQQQQQQ",
        1,
        5,
        0,
        base_address,
        0,
        binary_size,
        binary_size,
        0x1000,
    )
    path.write_bytes(ehdr + phdr + code)
    path.chmod(0o755)


def test_linux_path_policy_confines_recursive_read_and_exact_write(
    tmp_path: Path,
) -> None:
    _require_landlock_abi_one()

    readable_dir = tmp_path / "readable"
    nested_dir = readable_dir / "nested"
    nested_dir.mkdir(parents=True)
    readable_file = nested_dir / "allowed.txt"
    readable_file.write_text("read me", encoding="utf-8")
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("secret", encoding="utf-8")

    writable_file = tmp_path / "writable.txt"
    writable_file.write_text("old", encoding="utf-8")
    sibling_file = tmp_path / "sibling.txt"
    sibling_file.write_text("keep", encoding="utf-8")
    writable_dir = tmp_path / "writable-dir"
    writable_nested = writable_dir / "nested"
    writable_nested.mkdir(parents=True)
    recursive_write_file = writable_nested / "allowed.txt"
    recursive_write_file.write_text("old", encoding="utf-8")
    outside_write_file = tmp_path / "outside-write.txt"
    outside_write_file.write_text("keep", encoding="utf-8")

    readable_fd = _open_path(readable_dir)
    writable_fd = _open_path(writable_file)
    writable_dir_fd = _open_path(writable_dir)
    try:
        child_code = r"""
import errno
import os
import sys

from cli_agent_orchestrator.services.work_process_landlock import (
    PathPolicyAccess,
    PathPolicyRule,
    install_path_policy,
)

install_path_policy([
    PathPolicyRule(int(sys.argv[1]), PathPolicyAccess.READ),
    PathPolicyRule(int(sys.argv[2]), PathPolicyAccess.WRITE),
    PathPolicyRule(int(sys.argv[3]), PathPolicyAccess.WRITE),
])

with open(sys.argv[4], encoding="utf-8") as stream:
    if stream.read() != "read me":
        raise SystemExit(20)

try:
    open(sys.argv[5], encoding="utf-8")
except OSError as exc:
    if exc.errno != errno.EACCES:
        raise
else:
    raise SystemExit(21)

fd = os.open(sys.argv[6], os.O_WRONLY | os.O_CLOEXEC)
try:
    if os.write(fd, b"new") != 3:
        raise SystemExit(22)
finally:
    os.close(fd)

try:
    os.open(sys.argv[7], os.O_WRONLY | os.O_CLOEXEC)
except OSError as exc:
    if exc.errno != errno.EACCES:
        raise
else:
    raise SystemExit(23)

fd = os.open(sys.argv[8], os.O_WRONLY | os.O_CLOEXEC)
try:
    if os.write(fd, b"new") != 3:
        raise SystemExit(24)
finally:
    os.close(fd)

try:
    os.open(sys.argv[9], os.O_WRONLY | os.O_CLOEXEC)
except OSError as exc:
    if exc.errno != errno.EACCES:
        raise
else:
    raise SystemExit(25)
"""
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                child_code,
                str(readable_fd),
                str(writable_fd),
                str(writable_dir_fd),
                str(readable_file),
                str(outside_file),
                str(writable_file),
                str(sibling_file),
                str(recursive_write_file),
                str(outside_write_file),
            ],
            pass_fds=(readable_fd, writable_fd, writable_dir_fd),
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        os.close(writable_fd)
        os.close(writable_dir_fd)
        os.close(readable_fd)

    assert completed.returncode == 0, (
        f"isolated process exited {completed.returncode}; "
        f"stdout={completed.stdout!r}, stderr={completed.stderr!r}"
    )


def test_path_policy_rule_requires_typed_nonempty_rights() -> None:
    with pytest.raises(TypeError, match="integer file descriptor"):
        landlock.PathPolicyRule(True, landlock.PathPolicyAccess.READ)

    with pytest.raises(TypeError, match="PathPolicyAccess"):
        landlock.PathPolicyRule(3, 1)

    with pytest.raises(ValueError, match="at least one access right"):
        landlock.PathPolicyRule(3, landlock.PathPolicyAccess(0))

    with pytest.raises(ValueError, match="unsupported path-policy access"):
        landlock.PathPolicyRule(3, landlock.PathPolicyAccess(16))


def test_path_policy_rejects_duplicate_and_unsupported_descriptor_inputs(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.write_text("target", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)

    fd = _open_path(target)
    duplicate_fd = os.dup(fd)
    ordinary_fd = os.open(target, os.O_RDONLY | os.O_CLOEXEC)
    symlink_fd = os.open(link, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
    fifo_fd = _open_path(fifo)
    rule = landlock.PathPolicyRule(fd, landlock.PathPolicyAccess.READ)
    try:
        with patch.object(landlock, "_landlock_syscall") as syscall:
            with pytest.raises(ValueError, match="duplicate"):
                landlock.install_path_policy([rule, rule])
            with pytest.raises(ValueError, match="duplicate"):
                landlock.install_path_policy(
                    [rule, landlock.PathPolicyRule(duplicate_fd, landlock.PathPolicyAccess.READ)]
                )
            with pytest.raises(ValueError, match="O_PATH"):
                landlock.install_path_policy(
                    [landlock.PathPolicyRule(ordinary_fd, landlock.PathPolicyAccess.READ)]
                )
            with pytest.raises(ValueError, match="regular file or directory"):
                landlock.install_path_policy(
                    [landlock.PathPolicyRule(symlink_fd, landlock.PathPolicyAccess.READ)]
                )
            with pytest.raises(ValueError, match="regular file or directory"):
                landlock.install_path_policy(
                    [landlock.PathPolicyRule(fifo_fd, landlock.PathPolicyAccess.READ)]
                )
            with pytest.raises(TypeError, match="PathPolicyRule"):
                landlock.install_path_policy([object()])
        syscall.assert_not_called()
    finally:
        os.close(fifo_fd)
        os.close(symlink_fd)
        os.close(ordinary_fd)
        os.close(duplicate_fd)
        os.close(fd)


@pytest.mark.parametrize("abi_version", [1, 2, 3, 5, 7, 9])
def test_install_path_policy_handles_and_grants_only_abi_supported_rights(
    tmp_path: Path,
    abi_version: int,
) -> None:
    readable_dir = tmp_path / "readable"
    readable_dir.mkdir()
    writable_dir = tmp_path / "writable"
    writable_dir.mkdir()
    writable_file = tmp_path / "writable.txt"
    writable_file.write_text("data", encoding="utf-8")
    executable_file = tmp_path / "executable"
    executable_file.write_text("data", encoding="utf-8")

    readable_fd = _open_path(readable_dir)
    writable_dir_fd = _open_path(writable_dir)
    writable_file_fd = _open_path(writable_file)
    executable_fd = _open_path(executable_file)
    rules = [
        landlock.PathPolicyRule(readable_fd, landlock.PathPolicyAccess.READ),
        landlock.PathPolicyRule(writable_dir_fd, landlock.PathPolicyAccess.WRITE),
        landlock.PathPolicyRule(writable_file_fd, landlock.PathPolicyAccess.WRITE),
        landlock.PathPolicyRule(executable_fd, landlock.PathPolicyAccess.EXECUTE),
    ]
    syscall_numbers = landlock._landlock_syscalls()
    calls: list[tuple[int, tuple[object, ...]]] = []
    timeline: list[str] = []
    created_access: list[int] = []
    added_rules: list[tuple[int, int]] = []

    def fake_syscall(number: int, *args: object) -> int:
        calls.append((number, args))
        if number == syscall_numbers.create_ruleset:
            if args[0] is None:
                timeline.append("abi")
                return abi_version
            timeline.append("create")
            attr = ctypes.cast(args[0], ctypes.POINTER(landlock._RulesetAttr)).contents
            created_access.append(attr.handled_access_fs)
            return 59
        if number == syscall_numbers.add_rule:
            timeline.append("add")
            rule = ctypes.cast(args[2], ctypes.POINTER(landlock._PathBeneathAttr)).contents
            added_rules.append((rule.parent_fd, rule.allowed_access))
        if number == syscall_numbers.restrict_self:
            timeline.append("restrict")
        return 0

    try:
        with (
            patch.object(landlock, "_landlock_syscall", side_effect=fake_syscall),
            patch.object(landlock, "_set_no_new_privs", side_effect=lambda: timeline.append("nnp")),
            patch.object(landlock.os, "set_inheritable") as set_inheritable,
            patch.object(landlock.os, "close") as close_fd,
        ):
            landlock.install_path_policy(rules)

        execute = 1 << 0
        write_file = 1 << 1
        read_file = 1 << 2
        read_dir = 1 << 3
        directory_mutations = sum(1 << bit for bit in range(4, 13))
        refer = 1 << 13
        truncate = 1 << 14
        ioctl_dev = 1 << 15
        resolve_unix = 1 << 16
        handled = (1 << 13) - 1
        if abi_version >= 2:
            handled |= refer
        if abi_version >= 3:
            handled |= truncate
        if abi_version >= 5:
            handled |= ioctl_dev
        if abi_version >= 9:
            handled |= resolve_unix

        assert created_access == [handled]
        assert added_rules == [
            (readable_fd, read_file | read_dir),
            (
                writable_dir_fd,
                write_file
                | directory_mutations
                | (refer if abi_version >= 2 else 0)
                | (truncate if abi_version >= 3 else 0),
            ),
            (writable_file_fd, write_file | (truncate if abi_version >= 3 else 0)),
            (executable_fd, execute),
        ]
        assert timeline == ["abi", "create", "add", "add", "add", "add", "nnp", "restrict"]
        set_inheritable.assert_called_once_with(59, False)
        close_fd.assert_called_once_with(59)
        assert [number for number, _args in calls][-1] == syscall_numbers.restrict_self
    finally:
        os.close(executable_fd)
        os.close(writable_file_fd)
        os.close(writable_dir_fd)
        os.close(readable_fd)


def test_install_path_policy_grants_resolve_unix_to_exact_socket_inode(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "approved.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    fd = _open_path(socket_path)
    rule = landlock.PathPolicyRule(fd, _resolve_unix_access())
    syscall_numbers = landlock._landlock_syscalls()
    created_access: list[int] = []
    added_rules: list[tuple[int, int]] = []

    def fake_syscall(number: int, *args: object) -> int:
        if number == syscall_numbers.create_ruleset:
            attr = ctypes.cast(args[0], ctypes.POINTER(landlock._RulesetAttr)).contents
            created_access.append(attr.handled_access_fs)
            return 71
        if number == syscall_numbers.add_rule:
            path_rule = ctypes.cast(args[2], ctypes.POINTER(landlock._PathBeneathAttr)).contents
            added_rules.append((path_rule.parent_fd, path_rule.allowed_access))
        return 0

    try:
        with (
            patch.object(landlock, "_query_abi_version", return_value=9),
            patch.object(landlock, "_landlock_syscall", side_effect=fake_syscall),
            patch.object(landlock, "_set_no_new_privs"),
            patch.object(landlock.os, "set_inheritable"),
            patch.object(landlock.os, "close"),
        ):
            landlock.install_path_policy([rule])

        assert created_access == [0x1FFFF]
        assert added_rules == [(fd, 1 << 16)]
    finally:
        os.close(fd)
        listener.close()


@pytest.mark.parametrize("target_kind", ["directory", "regular_file"])
def test_resolve_unix_rejects_non_socket_path_targets(
    tmp_path: Path,
    target_kind: str,
) -> None:
    target = tmp_path / target_kind
    if target_kind == "directory":
        target.mkdir()
    else:
        target.write_text("file", encoding="utf-8")
    fd = _open_path(target)
    try:
        rule = landlock.PathPolicyRule(fd, _resolve_unix_access())
        with pytest.raises(ValueError, match="AF_UNIX socket"):
            landlock.install_path_policy([rule])
    finally:
        os.close(fd)


@pytest.mark.parametrize("family", [socket.AF_UNIX, socket.AF_INET])
def test_resolve_unix_rejects_unnamed_socket_descriptors(
    family: socket.AddressFamily,
) -> None:
    sock = socket.socket(family, socket.SOCK_STREAM)
    fd = os.open(f"/proc/self/fd/{sock.fileno()}", os.O_PATH | os.O_CLOEXEC)
    try:
        with pytest.raises(ValueError, match="AF_UNIX socket"):
            landlock._validate_path_rules([landlock.PathPolicyRule(fd, _resolve_unix_access())])
    finally:
        os.close(fd)
        sock.close()


@pytest.mark.parametrize(
    "other_access",
    [
        landlock.PathPolicyAccess.READ,
        landlock.PathPolicyAccess.WRITE,
        landlock.PathPolicyAccess.EXECUTE,
    ],
)
def test_resolve_unix_cannot_be_combined_with_file_access(
    tmp_path: Path,
    other_access: landlock.PathPolicyAccess,
) -> None:
    socket_path = tmp_path / "mixed.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    fd = _open_path(socket_path)
    try:
        rule = landlock.PathPolicyRule(fd, _resolve_unix_access() | other_access)
        with pytest.raises(ValueError, match="cannot be combined"):
            landlock.install_path_policy([rule])
    finally:
        os.close(fd)
        listener.close()


def test_resolve_unix_requires_landlock_abi_nine(tmp_path: Path) -> None:
    socket_path = tmp_path / "unsupported.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    fd = _open_path(socket_path)
    try:
        rule = landlock.PathPolicyRule(fd, _resolve_unix_access())
        with (
            patch.object(landlock, "_query_abi_version", return_value=8),
            patch.object(landlock, "_landlock_syscall") as syscall,
        ):
            with pytest.raises(OSError) as error:
                landlock.install_path_policy([rule])

        assert error.value.errno == errno.EOPNOTSUPP
        syscall.assert_not_called()
    finally:
        os.close(fd)
        listener.close()


def test_linux_resolve_unix_allows_listed_and_denies_unlisted_pathname_sockets(
    tmp_path: Path,
) -> None:
    abi_version = landlock._query_abi_version()
    if abi_version < 9:
        pytest.skip(
            f"pathname Unix socket rules require Landlock ABI 9; detected ABI {abi_version}"
        )

    approved_path = tmp_path / "approved.sock"
    unlisted_path = tmp_path / "unlisted.sock"
    approved_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    unlisted_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    approved_server.bind(str(approved_path))
    approved_server.listen()
    unlisted_server.bind(str(unlisted_path))
    unlisted_server.listen()
    approved_fd = _open_path(approved_path)
    try:
        child_code = r"""
import errno
import socket
import sys

from cli_agent_orchestrator.services.work_process_landlock import (
    PathPolicyAccess,
    PathPolicyRule,
    install_path_policy,
)

install_path_policy([PathPolicyRule(int(sys.argv[1]), PathPolicyAccess.RESOLVE_UNIX)])
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.connect(sys.argv[2])
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    try:
        client.connect(sys.argv[3])
    except OSError as exc:
        if exc.errno != errno.EACCES:
            raise
    else:
        raise SystemExit(50)
"""
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                child_code,
                str(approved_fd),
                str(approved_path),
                str(unlisted_path),
            ],
            pass_fds=(approved_fd,),
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        os.close(approved_fd)
        approved_server.close()
        unlisted_server.close()

    assert completed.returncode == 0, (
        f"isolated process exited {completed.returncode}; "
        f"stdout={completed.stdout!r}, stderr={completed.stderr!r}"
    )


def test_linux_path_policy_allows_exact_execute_and_denies_unlisted_execute(
    tmp_path: Path,
) -> None:
    _require_landlock_abi_one()
    executable = tmp_path / "allowed"
    denied = tmp_path / "denied"
    executable_dir = tmp_path / "tree"
    nested_dir = executable_dir / "nested"
    nested_dir.mkdir(parents=True)
    recursive_executable = nested_dir / "allowed"
    try:
        _write_static_exit_binary(executable)
        _write_static_exit_binary(denied)
        _write_static_exit_binary(recursive_executable)
    except OSError as exc:
        if exc.errno == errno.ENOSYS:
            pytest.skip(f"test executable unavailable: errno={exc.errno}")
        raise

    executable_fd = _open_path(executable)
    executable_dir_fd = _open_path(executable_dir)
    try:
        child_code = r"""
import errno
import subprocess
import sys

from cli_agent_orchestrator.services.work_process_landlock import (
    PathPolicyAccess,
    PathPolicyRule,
    install_path_policy,
)

install_path_policy([
    PathPolicyRule(
        int(sys.argv[1]),
        PathPolicyAccess.READ | PathPolicyAccess.EXECUTE,
    ),
    PathPolicyRule(
        int(sys.argv[2]),
        PathPolicyAccess.READ | PathPolicyAccess.EXECUTE,
    ),
])
if subprocess.run([sys.argv[3]], check=False).returncode != 0:
    raise SystemExit(30)
if subprocess.run([sys.argv[4]], check=False).returncode != 0:
    raise SystemExit(32)
try:
    subprocess.run([sys.argv[5]], check=False)
except OSError as exc:
    if exc.errno != errno.EACCES:
        raise
else:
    raise SystemExit(31)
"""
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                child_code,
                str(executable_fd),
                str(executable_dir_fd),
                str(executable),
                str(recursive_executable),
                str(denied),
            ],
            pass_fds=(executable_fd, executable_dir_fd),
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        os.close(executable_dir_fd)
        os.close(executable_fd)

    assert completed.returncode == 0, (
        f"isolated process exited {completed.returncode}; "
        f"stdout={completed.stdout!r}, stderr={completed.stderr!r}"
    )


def test_install_path_policy_closes_ruleset_when_adding_rule_fails(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.write_text("target", encoding="utf-8")
    fd = _open_path(target)
    rule = landlock.PathPolicyRule(fd, landlock.PathPolicyAccess.READ)
    syscall_numbers = landlock._landlock_syscalls()
    timeline: list[str] = []
    calls: list[int] = []

    def fail_add_rule(number: int, *args: object) -> int:
        calls.append(number)
        if number == syscall_numbers.create_ruleset:
            if args[0] is None:
                timeline.append("abi")
                return 1
            timeline.append("create")
            return 61
        if number == syscall_numbers.add_rule:
            timeline.append("add")
            raise OSError(errno.EINVAL, "mocked add rule failure")
        timeline.append("restrict")
        return 0

    try:
        with (
            patch.object(landlock, "_landlock_syscall", side_effect=fail_add_rule),
            patch.object(landlock, "_set_no_new_privs") as set_no_new_privs,
            patch.object(landlock.os, "set_inheritable"),
            patch.object(
                landlock.os,
                "close",
                side_effect=lambda fd: timeline.append("close"),
            ) as close_fd,
            patch.object(landlock.os, "execve") as execve,
            patch.object(landlock.os, "execvpe") as execvpe,
        ):
            with pytest.raises(OSError, match="mocked add rule failure"):
                landlock.install_path_policy([rule])

        assert timeline == ["abi", "create", "add", "close"]
        assert calls == [
            syscall_numbers.create_ruleset,
            syscall_numbers.create_ruleset,
            syscall_numbers.add_rule,
        ]
        close_fd.assert_called_once_with(61)
        set_no_new_privs.assert_not_called()
        execve.assert_not_called()
        execvpe.assert_not_called()
    finally:
        os.close(fd)


def test_install_path_policy_closes_ruleset_when_restrict_fails_without_exec(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.write_text("target", encoding="utf-8")
    fd = _open_path(target)
    rule = landlock.PathPolicyRule(fd, landlock.PathPolicyAccess.READ)
    syscall_numbers = landlock._landlock_syscalls()
    timeline: list[str] = []
    calls: list[int] = []

    def fail_restrict(number: int, *args: object) -> int:
        calls.append(number)
        if number == syscall_numbers.create_ruleset:
            if args[0] is None:
                timeline.append("abi")
                return 1
            timeline.append("create")
            return 67
        if number == syscall_numbers.add_rule:
            timeline.append("add")
            return 0
        timeline.append("restrict")
        raise OSError(errno.EPERM, "mocked restrict failure")

    try:
        with (
            patch.object(landlock, "_landlock_syscall", side_effect=fail_restrict),
            patch.object(
                landlock,
                "_set_no_new_privs",
                side_effect=lambda: timeline.append("no_new_privs"),
            ) as set_no_new_privs,
            patch.object(landlock.os, "set_inheritable"),
            patch.object(
                landlock.os,
                "close",
                side_effect=lambda fd: timeline.append("close"),
            ) as close_fd,
            patch.object(landlock.os, "execve") as execve,
            patch.object(landlock.os, "execvpe") as execvpe,
        ):
            with pytest.raises(OSError, match="mocked restrict failure"):
                landlock.install_path_policy([rule])

        assert timeline == ["abi", "create", "add", "no_new_privs", "restrict", "close"]
        assert calls == [
            syscall_numbers.create_ruleset,
            syscall_numbers.create_ruleset,
            syscall_numbers.add_rule,
            syscall_numbers.restrict_self,
        ]
        close_fd.assert_called_once_with(67)
        set_no_new_privs.assert_called_once_with()
        execve.assert_not_called()
        execvpe.assert_not_called()
    finally:
        os.close(fd)


def test_linux_empty_path_policy_denies_supported_read_write_and_execute(
    tmp_path: Path,
) -> None:
    _require_landlock_abi_one()
    target = tmp_path / "unlisted.txt"
    target.write_text("secret", encoding="utf-8")
    executable = tmp_path / "unlisted-executable"
    try:
        _write_static_exit_binary(executable)
    except OSError as exc:
        if exc.errno == errno.ENOSYS:
            pytest.skip(f"test executable unavailable: errno={exc.errno}")
        raise

    child_code = r"""
import errno
import os
import subprocess
import sys

from cli_agent_orchestrator.services.work_process_landlock import install_path_policy

install_path_policy([])
try:
    open(sys.argv[1], encoding="utf-8")
except OSError as exc:
    if exc.errno != errno.EACCES:
        raise
else:
    raise SystemExit(40)

try:
    os.open(sys.argv[1], os.O_WRONLY | os.O_CLOEXEC)
except OSError as exc:
    if exc.errno != errno.EACCES:
        raise
else:
    raise SystemExit(42)

try:
    subprocess.run([sys.argv[2]], check=False)
except OSError as exc:
    if exc.errno != errno.EACCES:
        raise
else:
    raise SystemExit(41)
"""
    completed = subprocess.run(
        [sys.executable, "-c", child_code, str(target), str(executable)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, (
        f"isolated process exited {completed.returncode}; "
        f"stdout={completed.stdout!r}, stderr={completed.stderr!r}"
    )


def test_install_requires_nonempty_sequence_of_file_descriptors() -> None:
    with pytest.raises(ValueError, match="non-empty sequence"):
        landlock.install_exec_policy([])

    with pytest.raises(ValueError, match="sequence"):
        landlock.install_exec_policy("3")

    with pytest.raises(TypeError, match="integer file descriptors"):
        landlock.install_exec_policy([True])


def test_install_requires_opath_regular_files(tmp_path: Path) -> None:
    regular = tmp_path / "regular"
    regular.write_text("target", encoding="utf-8")
    directory_fd = _open_path(tmp_path)
    ordinary_fd = os.open(regular, os.O_RDONLY | os.O_CLOEXEC)
    try:
        with patch.object(landlock, "_landlock_syscall") as syscall:
            with pytest.raises(ValueError, match="O_PATH"):
                landlock.install_exec_policy([ordinary_fd])
            with pytest.raises(ValueError, match="regular file"):
                landlock.install_exec_policy([directory_fd])
        syscall.assert_not_called()
    finally:
        os.close(directory_fd)
        os.close(ordinary_fd)


def test_install_adds_one_execute_rule_per_unique_file_and_restricts_last(
    tmp_path: Path,
) -> None:
    target = tmp_path / "allowed"
    target.write_text("target", encoding="utf-8")
    first_fd = _open_path(target)
    duplicate_fd = os.dup(first_fd)
    try:
        calls: list[tuple[int, tuple[object, ...]]] = []
        timeline: list[str] = []

        def fake_syscall(number: int, *args: object) -> int:
            calls.append((number, args))
            if number == landlock._landlock_syscalls().create_ruleset:
                if args[0] is None:
                    timeline.append("abi")
                    return 2
                timeline.append("create")
                return 3
            if number == landlock._landlock_syscalls().add_rule:
                timeline.append("add")
            if number == landlock._landlock_syscalls().restrict_self:
                timeline.append("restrict")
            return 0

        def fake_no_new_privs() -> None:
            timeline.append("no_new_privs")

        with (
            patch.object(landlock, "_landlock_syscall", side_effect=fake_syscall),
            patch.object(
                landlock,
                "_set_no_new_privs",
                side_effect=fake_no_new_privs,
            ) as set_no_new_privs,
            patch.object(landlock.os, "set_inheritable") as set_inheritable,
            patch.object(landlock.os, "close") as close_fd,
        ):
            landlock.install_exec_policy([first_fd, duplicate_fd, first_fd])

        numbers = [number for number, _args in calls]
        syscall_numbers = landlock._landlock_syscalls()
        assert numbers == [
            syscall_numbers.create_ruleset,
            syscall_numbers.create_ruleset,
            syscall_numbers.add_rule,
            syscall_numbers.restrict_self,
        ]
        assert timeline == ["abi", "create", "add", "no_new_privs", "restrict"]
        _number, create_args = calls[1]
        ruleset_attr = ctypes.cast(create_args[0], ctypes.POINTER(landlock._RulesetAttr)).contents
        assert ctypes.sizeof(landlock._RulesetAttr) == 24
        assert ctypes.sizeof(landlock._PathBeneathAttr) == 12
        assert ruleset_attr.handled_access_fs == 1
        assert ruleset_attr.handled_access_net == 0
        assert ruleset_attr.scoped == 0

        _number, add_args = calls[2]
        assert add_args[0] == 3
        assert add_args[1] == 1
        rule = ctypes.cast(add_args[2], ctypes.POINTER(landlock._PathBeneathAttr)).contents
        assert rule.allowed_access == 1
        assert rule.parent_fd == first_fd

        set_no_new_privs.assert_called_once_with()
        set_inheritable.assert_called_once_with(3, False)
        close_fd.assert_called_once_with(3)
    finally:
        os.close(duplicate_fd)
        os.close(first_fd)


def test_install_fails_closed_for_unknown_architecture(tmp_path: Path) -> None:
    target = tmp_path / "allowed"
    target.write_text("target", encoding="utf-8")
    fd = _open_path(target)
    try:
        with (
            patch.object(landlock.platform, "machine", return_value="mystery64"),
            patch.object(landlock, "_landlock_syscall") as syscall,
        ):
            with pytest.raises(OSError, match="unsupported architecture"):
                landlock.install_exec_policy([fd])
        syscall.assert_not_called()
    finally:
        os.close(fd)


def test_install_requires_landlock_abi_one_or_newer(tmp_path: Path) -> None:
    target = tmp_path / "allowed"
    target.write_text("target", encoding="utf-8")
    fd = _open_path(target)
    syscall_numbers = landlock._landlock_syscalls()
    try:
        with patch.object(landlock, "_landlock_syscall", return_value=0) as syscall:
            with pytest.raises(OSError, match="FS_EXECUTE"):
                landlock.install_exec_policy([fd])
        syscall.assert_called_once_with(
            syscall_numbers.create_ruleset,
            None,
            0,
            1,
        )
    finally:
        os.close(fd)


def test_install_closes_ruleset_fd_when_adding_rule_fails(tmp_path: Path) -> None:
    target = tmp_path / "allowed"
    target.write_text("target", encoding="utf-8")
    fd = _open_path(target)
    syscall_numbers = landlock._landlock_syscalls()

    def fail_add_rule(number: int, *args: object) -> int:
        if number == syscall_numbers.create_ruleset:
            return 1 if args[0] is None else 47
        raise OSError(errno.EINVAL, "mocked add rule failure")

    try:
        with (
            patch.object(landlock, "_landlock_syscall", side_effect=fail_add_rule),
            patch.object(landlock.os, "set_inheritable"),
            patch.object(landlock.os, "close") as close_fd,
            patch.object(landlock, "_set_no_new_privs") as set_no_new_privs,
        ):
            with pytest.raises(OSError, match="mocked add rule failure"):
                landlock.install_exec_policy([fd])
        close_fd.assert_called_once_with(47)
        set_no_new_privs.assert_not_called()
    finally:
        os.close(fd)


def test_install_closes_ruleset_fd_when_no_new_privs_fails(tmp_path: Path) -> None:
    target = tmp_path / "allowed"
    target.write_text("target", encoding="utf-8")
    fd = _open_path(target)
    syscall_numbers = landlock._landlock_syscalls()

    def create_ruleset(number: int, *args: object) -> int:
        if number == syscall_numbers.create_ruleset:
            return 1 if args[0] is None else 53
        return 0

    try:
        with (
            patch.object(landlock, "_landlock_syscall", side_effect=create_ruleset),
            patch.object(landlock.os, "set_inheritable"),
            patch.object(landlock.os, "close") as close_fd,
            patch.object(
                landlock,
                "_set_no_new_privs",
                side_effect=OSError(errno.EPERM, "mocked no_new_privs failure"),
            ),
        ):
            with pytest.raises(OSError, match="mocked no_new_privs failure"):
                landlock.install_exec_policy([fd])
        close_fd.assert_called_once_with(53)
    finally:
        os.close(fd)


def test_linux_subprocess_allows_target_path_denies_unrelated_exec_and_inherits_policy(
    tmp_path: Path,
) -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("Landlock is a Linux kernel feature")

    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("C compiler is unavailable")

    source = tmp_path / "target.c"
    executable = tmp_path / "target-static"
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    compiled = subprocess.run(
        [compiler, "-static", "-O2", "-o", str(executable), str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    if compiled.returncode != 0:
        pytest.skip("static C toolchain is unavailable: " + compiled.stderr.strip())

    try:
        abi = landlock._query_abi_version()
    except OSError as exc:
        if exc.errno in {errno.ENOSYS, errno.EOPNOTSUPP}:
            pytest.skip("kernel does not provide the Landlock ABI")
        raise
    if abi < 1:
        pytest.skip("kernel Landlock ABI lacks FS_EXECUTE")

    target_fd = _open_path(executable)
    try:
        child_code = r"""
import errno
import os
import subprocess
import sys

from cli_agent_orchestrator.services.work_process_landlock import install_exec_policy

install_exec_policy([int(sys.argv[1])])
allowed = subprocess.run([sys.argv[2]], check=False)
if allowed.returncode != 0:
    raise SystemExit(10)

try:
    subprocess.run(["/bin/true"], check=False)
except OSError as exc:
    if exc.errno != errno.EACCES:
        raise SystemExit(11)
else:
    raise SystemExit(12)

read_fd, write_fd = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(read_fd)
    try:
        os.execv("/bin/true", ["/bin/true"])
    except OSError as exc:
        os.write(write_fd, str(exc.errno).encode("ascii"))
        os._exit(0)
    os._exit(13)
os.close(write_fd)
descendant_errno = os.read(read_fd, 32)
_, status = os.waitpid(pid, 0)
os.close(read_fd)
if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
    raise SystemExit(14)
if descendant_errno != str(errno.EACCES).encode("ascii"):
    raise SystemExit(15)
"""
        completed = subprocess.run(
            [sys.executable, "-c", child_code, str(target_fd), str(executable)],
            pass_fds=(target_fd,),
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        os.close(target_fd)

    assert completed.returncode == 0, (
        f"isolated process exited {completed.returncode}; "
        f"stdout={completed.stdout!r}, stderr={completed.stderr!r}"
    )
