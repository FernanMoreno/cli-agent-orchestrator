"""Characterize sealed ELF exposure through scratch Bubblewrap ro-bind-data."""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


_T097_ROOT = Path("/tmp/caos-exec/T097")
_SCRATCH_BWRAP = _T097_ROOT / "bubblewrap-build" / "bwrap"
_VERSION_RE = re.compile(r"^bubblewrap\s+(\d+)\.(\d+)\.(\d+)(?:\s.*)?$")

_ELF_PROBE_SOURCE = r"""
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

extern char **environ;

static int parse_u64(const char *text, uint64_t *value) {
    char *end = NULL;
    unsigned long long parsed = strtoull(text, &end, 10);
    if (end == text || *end != '\0') {
        return -1;
    }
    *value = (uint64_t) parsed;
    return 0;
}

int main(int argc, char **argv) {
    struct stat target_info;
    struct stat inherited_info;
    const char *target;
    uint64_t inherited_fd;
    uint64_t expected_dev;
    uint64_t expected_ino;
    int write_fd;

    if (argc == 6 && strcmp(argv[1], "--runner") == 0) {
        target = argv[2];
        if (parse_u64(argv[3], &inherited_fd) != 0 ||
            parse_u64(argv[4], &expected_dev) != 0 ||
            parse_u64(argv[5], &expected_ino) != 0) {
            fputs("invalid-runner-arguments\n", stderr);
            return 10;
        }
    } else if (argc == 4) {
        target = argv[0];
        if (parse_u64(argv[1], &inherited_fd) != 0 ||
            parse_u64(argv[2], &expected_dev) != 0 ||
            parse_u64(argv[3], &expected_ino) != 0) {
            fputs("invalid-target-arguments\n", stderr);
            return 10;
        }
    } else {
        fputs("invalid-probe-arguments\n", stderr);
        return 10;
    }
    if (inherited_fd > 1048576) {
        fputs("invalid-source-fd\n", stderr);
        return 10;
    }

    if (fstat((int) inherited_fd, &inherited_info) == 0 &&
        (uint64_t) inherited_info.st_dev == expected_dev &&
        (uint64_t) inherited_info.st_ino == expected_ino) {
        fputs("source-fd-inherited\n", stderr);
        return 11;
    }
    if (stat(target, &target_info) != 0) {
        perror("stat-target");
        return 12;
    }
    if ((target_info.st_mode & 0777) != 0500) {
        fprintf(stderr, "unexpected-mode=%04o\n", target_info.st_mode & 07777);
        return 13;
    }

    if (argc == 6) {
        write_fd = open(target, O_WRONLY | O_APPEND);
        if (write_fd >= 0) {
            close(write_fd);
            fputs("target-opened-writable\n", stderr);
            return 14;
        }
        if (errno != EROFS) {
            fprintf(stderr, "write-errno=%d\n", errno);
            return 15;
        }

        if (chmod(target, 0700) == 0) {
            fputs("target-chmod-succeeded\n", stderr);
            return 16;
        }
        if (errno != EROFS) {
            fprintf(stderr, "chmod-errno=%d\n", errno);
            return 17;
        }

        char *target_args[] = {
            (char *) target,
            argv[3],
            argv[4],
            argv[5],
            NULL,
        };
        execve(target, target_args, environ);
        perror("exec-target");
        return 18;
    }

    return 42;
}
"""


@pytest.fixture(scope="session")
def scratch_bubblewrap() -> Path:
    """Resolve and validate only the accepted T097 scratch build."""
    if not sys.platform.startswith("linux"):
        pytest.skip("ro-bind-data characterization requires Linux")
    if not _SCRATCH_BWRAP.is_file():
        pytest.skip(
            f"T097 scratch Bubblewrap is absent at {_SCRATCH_BWRAP}; "
            "installed /usr/bin/bwrap is intentionally never used"
        )
    try:
        executable = _SCRATCH_BWRAP.resolve(strict=True)
        scratch_root = _T097_ROOT.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"could not resolve T097 scratch Bubblewrap: {exc}")
    if not executable.is_relative_to(scratch_root) or executable == Path(
        "/usr/bin/bwrap"
    ).resolve():
        pytest.skip("T097 Bubblewrap resolves outside its scratch tree; refusing fallback")
    if not os.access(executable, os.X_OK):
        pytest.skip(f"T097 scratch Bubblewrap is not executable: {executable}")

    try:
        version_probe = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            env={"LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"could not probe T097 scratch Bubblewrap: {exc}")
    match = _VERSION_RE.fullmatch(version_probe.stdout.strip())
    if version_probe.returncode != 0 or match is None:
        pytest.skip(
            "T097 scratch Bubblewrap did not report a parseable version >= 0.12.0: "
            f"stdout={version_probe.stdout!r} stderr={version_probe.stderr!r}"
        )
    version = tuple(int(part) for part in match.groups())
    if version < (0, 12, 0):
        pytest.skip(
            f"T097 scratch Bubblewrap is {version_probe.stdout.strip()}, below 0.12.0; "
            "installed /usr/bin/bwrap is never substituted"
        )

    try:
        namespace_probe = subprocess.run(
            [
                str(executable),
                "--unshare-all",
                "--die-with-parent",
                "--ro-bind",
                "/",
                "/",
                "--",
                "/bin/true",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env={"LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"T097 scratch Bubblewrap namespace probe failed: {exc}")
    if namespace_probe.returncode != 0:
        pytest.skip(
            "T097 scratch Bubblewrap cannot create the namespaces required for this "
            f"characterization: {namespace_probe.stderr.strip() or namespace_probe.returncode}"
        )
    return executable


@pytest.fixture(scope="session")
def static_elf_probe(tmp_path_factory) -> Path:
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("C compiler 'cc' is unavailable for the static ELF probe")

    work = tmp_path_factory.mktemp("work-ro-bind-data")
    source = work / "ro-bind-data-probe.c"
    binary = work / "ro-bind-data-probe"
    source.write_text(_ELF_PROBE_SOURCE, encoding="utf-8")
    try:
        build = subprocess.run(
            [
                compiler,
                "-std=c11",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-static",
                str(source),
                "-o",
                str(binary),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"compiler could not build the static ELF probe: {exc}")
    if build.returncode != 0:
        pytest.skip(f"compiler cannot produce the required static ELF probe: {build.stderr.strip()}")
    return binary


@pytest.fixture(scope="session")
def executable_mountpoint() -> Path:
    """Choose an existing ELF path for the pre-exec read-only check runner."""
    installed_bwrap = Path("/usr/bin/bwrap").resolve()
    for directory in (Path("/usr/bin"), Path("/bin"), Path("/usr/local/bin")):
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.iterdir()):
            try:
                info = candidate.lstat()
                if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
                    continue
                if candidate.resolve() == installed_bwrap or candidate.name in {
                    "bwrap",
                    "bubblewrap",
                }:
                    continue
                with candidate.open("rb") as stream:
                    if stream.read(4) == b"\x7fELF":
                        return candidate
            except OSError:
                continue
    pytest.skip("no regular ELF mountpoint is available for the namespace runner")


def test_sealed_static_elf_is_executable_readonly_and_source_fd_is_closed(
    scratch_bubblewrap: Path,
    static_elf_probe: Path,
    executable_mountpoint: Path,
) -> None:
    try:
        source_fd = os.memfd_create(
            "work-ro-bind-data-elf", flags=getattr(os, "MFD_ALLOW_SEALING", 2)
        )
    except (AttributeError, OSError) as exc:
        pytest.skip(f"sealed source memfd is unavailable: {exc}")

    try:
        with static_elf_probe.open("rb") as stream:
            with os.fdopen(os.dup(source_fd), "wb") as destination:
                shutil.copyfileobj(stream, destination)
        seals = (
            getattr(fcntl, "F_SEAL_WRITE", 0x0008)
            | getattr(fcntl, "F_SEAL_GROW", 0x0004)
            | getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
            | getattr(fcntl, "F_SEAL_SEAL", 0x0001)
        )
        try:
            fcntl.fcntl(source_fd, getattr(fcntl, "F_ADD_SEALS", 1033), seals)
        except (AttributeError, OSError) as exc:
            pytest.skip(f"kernel cannot seal the ELF source memfd: {exc}")
        applied_seals = fcntl.fcntl(source_fd, getattr(fcntl, "F_GET_SEALS", 1034))
        if applied_seals & seals != seals:
            pytest.skip(f"kernel applied incomplete source memfd seals: {applied_seals:#x}")

        os.lseek(source_fd, 0, os.SEEK_SET)
        source_info = os.fstat(source_fd)
        target = "/tmp/t097-ro-bind-data-elf"
        completed = subprocess.run(
            [
                str(scratch_bubblewrap),
                "--unshare-all",
                "--die-with-parent",
                "--uid",
                "0",
                "--gid",
                "0",
                "--cap-add",
                "CAP_DAC_OVERRIDE",
                "--cap-add",
                "CAP_FOWNER",
                "--ro-bind",
                "/",
                "/",
                "--tmpfs",
                "/tmp",
                "--ro-bind",
                str(static_elf_probe),
                str(executable_mountpoint),
                "--perms",
                "0500",
                "--ro-bind-data",
                str(source_fd),
                target,
                "--",
                str(executable_mountpoint),
                "--runner",
                target,
                str(source_fd),
                str(source_info.st_dev),
                str(source_info.st_ino),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env={"LC_ALL": "C"},
            pass_fds=(source_fd,),
        )
    finally:
        os.close(source_fd)

    assert completed.returncode == 42, (
        f"expected sealed static ELF to execute with read-only semantics; "
        f"status={completed.returncode}; stdout={completed.stdout!r}; "
        f"stderr={completed.stderr!r}"
    )
