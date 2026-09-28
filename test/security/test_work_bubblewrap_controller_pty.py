"""Characterize a direct T097 Bubblewrap worker launched on a controller PTY.

This is evidence about the pinned Bwrap launch surface only. A passing probe
does not authorize or register the Bubblewrap Work backend.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import pty
import re
import select
import shutil
import signal
import subprocess
import tempfile
import termios
import time
from pathlib import Path

import pytest

from cli_agent_orchestrator.services.work_elf_identity import identify_static_executable
from cli_agent_orchestrator.services.work_executable_staging import (
    WorkExecutableStagingUnavailable,
    bubblewrap_bind_data_arguments,
    stage_static_executable,
)

_T097_ROOT = Path("/tmp/caos-exec/T097")
_SCRATCH_BWRAP = _T097_ROOT / "bubblewrap-build" / "bwrap"
_VERSION_RE = re.compile(r"^bubblewrap\s+(\d+)\.(\d+)\.(\d+)(?:\s.*)?$")

_STATIC_WORKER_SOURCE = r"""
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

static int parse_long(const char *text, long *value) {
    char *end = NULL;
    errno = 0;
    long parsed = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0') {
        return -1;
    }
    *value = parsed;
    return 0;
}

static int run_trusted_bootstrap(
    const char *worker_path,
    const char *source_fd_text,
    const char *expected_dev_text,
    const char *expected_ino_text) {
    long source_fd;
    long expected_dev;
    long expected_ino;
    if (parse_long(source_fd_text, &source_fd) != 0 ||
        parse_long(expected_dev_text, &expected_dev) != 0 ||
        parse_long(expected_ino_text, &expected_ino) != 0 || source_fd < 0) {
        fputs("invalid bootstrap arguments\n", stderr);
        return 13;
    }

    errno = 0;
    long before_foreground_group = tcgetpgrp(STDIN_FILENO);
    int before_tcgetpgrp_errno = before_foreground_group < 0 ? errno : 0;
    long before_process_group = (long)getpgrp();
    long before_session_id = (long)getsid(0);
    int before_tty_fd = open("/dev/tty", O_RDONLY | O_CLOEXEC);
    int before_dev_tty_open = before_tty_fd >= 0;
    if (before_tty_fd >= 0) {
        close(before_tty_fd);
    }

    errno = 0;
    pid_t session_result = setsid();
    int session_errno = session_result < 0 ? errno : 0;

    errno = 0;
    int ctty_result = ioctl(STDIN_FILENO, TIOCSCTTY, 0);
    int ctty_errno = ctty_result < 0 ? errno : 0;

    errno = 0;
    long foreground_group = tcgetpgrp(STDIN_FILENO);
    int tcgetpgrp_errno = foreground_group < 0 ? errno : 0;
    long process_group = (long)getpgrp();
    long session_id = (long)getsid(0);
    int tty_fd = open("/dev/tty", O_RDONLY | O_CLOEXEC);
    int dev_tty_open = tty_fd >= 0;
    if (tty_fd >= 0) {
        close(tty_fd);
    }
    int stdin_isatty = isatty(STDIN_FILENO);

    int job_control_ready =
        ctty_result == 0 && stdin_isatty && dev_tty_open &&
        process_group > 0 && session_id > 0 &&
        foreground_group == process_group && tcgetpgrp_errno == 0;
    printf("{\"stage\":\"bootstrap\",\"before_process_group\":%ld,"
           "\"before_session_id\":%ld,\"before_tcgetpgrp_stdin\":%ld,"
           "\"before_tcgetpgrp_stdin_errno\":%d,"
           "\"before_dev_tty_open\":%s,\"setsid_result\":%ld,"
           "\"setsid_errno\":%d,\"tiocsctty_result\":%d,"
           "\"tiocsctty_errno\":%d,\"stdin_isatty\":%s,"
           "\"dev_tty_open\":%s,\"process_group\":%ld,"
           "\"session_id\":%ld,\"tcgetpgrp_stdin\":%ld,"
           "\"tcgetpgrp_stdin_errno\":%d,\"job_control_ready\":%s}\n",
           before_process_group, before_session_id, before_foreground_group,
           before_tcgetpgrp_errno,
           before_dev_tty_open ? "true" : "false",
           (long)session_result, session_errno, ctty_result, ctty_errno,
           stdin_isatty ? "true" : "false",
           dev_tty_open ? "true" : "false",
           process_group, session_id, foreground_group, tcgetpgrp_errno,
           job_control_ready ? "true" : "false");
    fflush(stdout);

    char *worker_args[] = {
        (char *)worker_path,
        "--worker",
        (char *)source_fd_text,
        (char *)expected_dev_text,
        (char *)expected_ino_text,
        NULL,
    };
    execv(worker_path, worker_args);
    perror("exec trusted static worker");
    return 21;
}

int main(int argc, char **argv) {
    DIR *directory;
    struct dirent *entry;
    struct stat source_info;
    long source_fd;
    long expected_dev;
    long expected_ino;
    long input_fg;
    long output_fg;
    int tty_fd;
    int directory_fd;
    int first = 1;
    int fd_count = 0;
    int fds[256];
    const char *report_stage = NULL;

    if (argc == 6 && strcmp(argv[1], "--bootstrap") == 0) {
        return run_trusted_bootstrap(argv[2], argv[3], argv[4], argv[5]);
    }
    if (argc == 5 && strcmp(argv[1], "--worker") == 0) {
        report_stage = "worker";
        argv += 1;
        argc -= 1;
    }
    if (argc != 4 || parse_long(argv[1], &source_fd) != 0 ||
        parse_long(argv[2], &expected_dev) != 0 ||
        parse_long(argv[3], &expected_ino) != 0 || source_fd < 0) {
        fputs("invalid probe arguments\n", stderr);
        return 10;
    }

    errno = 0;
    input_fg = tcgetpgrp(STDIN_FILENO);
    if (input_fg < 0 && errno == 0) {
        errno = EIO;
    }
    int input_tc_errno = input_fg < 0 ? errno : 0;

    errno = 0;
    output_fg = tcgetpgrp(STDOUT_FILENO);
    if (output_fg < 0 && errno == 0) {
        errno = EIO;
    }
    int output_tc_errno = output_fg < 0 ? errno : 0;

    tty_fd = open("/dev/tty", O_RDONLY | O_CLOEXEC);
    int dev_tty_open = tty_fd >= 0;
    if (tty_fd >= 0) {
        close(tty_fd);
    }

    directory = opendir("/proc/self/fd");
    if (directory == NULL) {
        perror("opendir /proc/self/fd");
        return 11;
    }
    directory_fd = dirfd(directory);
    while ((entry = readdir(directory)) != NULL) {
        char *end = NULL;
        long candidate = strtol(entry->d_name, &end, 10);
        if (end == entry->d_name || *end != '\0' || candidate == directory_fd) {
            continue;
        }
        if (fd_count == (int)(sizeof(fds) / sizeof(fds[0]))) {
            closedir(directory);
            fputs("too many descriptors to report\n", stderr);
            return 12;
        }
        fds[fd_count++] = (int) candidate;
    }
    closedir(directory);

    int source_memfd_inherited =
        fstat((int) source_fd, &source_info) == 0 &&
        (long) source_info.st_dev == expected_dev &&
        (long) source_info.st_ino == expected_ino;

    putchar('{');
    if (report_stage != NULL) {
        printf("\"stage\":\"%s\",", report_stage);
    }
    printf("\"stdin_isatty\":%s,\"stdout_isatty\":%s,"
           "\"tcgetpgrp_stdin\":%ld,\"tcgetpgrp_stdin_errno\":%d,"
           "\"tcgetpgrp_stdout\":%ld,\"tcgetpgrp_stdout_errno\":%d,"
           "\"process_group\":%ld,\"session_id\":%ld,"
           "\"dev_tty_open\":%s,\"source_memfd_inherited\":%s,"
           "\"open_fds\":[",
           isatty(STDIN_FILENO) ? "true" : "false",
           isatty(STDOUT_FILENO) ? "true" : "false",
           input_fg, input_tc_errno, output_fg, output_tc_errno,
           (long)getpgrp(), (long)getsid(0),
           dev_tty_open ? "true" : "false",
           source_memfd_inherited ? "true" : "false");
    for (int index = 0; index < fd_count; index++) {
        if (!first) {
            putchar(',');
        }
        printf("%d", fds[index]);
        first = 0;
    }
    puts("]}");
    return 0;
}
"""


@pytest.fixture(scope="session")
def scratch_bubblewrap() -> Path:
    """Validate and use only the designated Bubblewrap build."""
    if not _SCRATCH_BWRAP.is_file():
        pytest.skip(f"T097 scratch Bubblewrap is absent at {_SCRATCH_BWRAP}")
    try:
        executable = _SCRATCH_BWRAP.resolve(strict=True)
        scratch_root = _T097_ROOT.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"cannot resolve the T097 Bubblewrap build: {exc}")
    if not executable.is_relative_to(scratch_root):
        pytest.skip("T097 Bubblewrap resolves outside its designated scratch tree")
    if executable == Path("/usr/bin/bwrap").resolve():
        pytest.skip("refusing the installed /usr/bin/bwrap")
    if not os.access(executable, os.X_OK):
        pytest.skip(f"T097 Bubblewrap is not executable: {executable}")

    try:
        version = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            env={"LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"could not verify T097 Bubblewrap: {exc}")
    match = _VERSION_RE.fullmatch(version.stdout.strip())
    if version.returncode != 0 or match is None or tuple(map(int, match.groups())) != (0, 13, 0):
        pytest.skip(
            "this probe requires the designated Bubblewrap 0.13.0 build; "
            f"observed {version.stdout.strip()!r}"
        )
    return executable


@pytest.fixture(scope="session")
def staged_worker(scratch_bubblewrap: Path):
    """Build a static reporting ELF and stage it through the sealed path."""
    del scratch_bubblewrap
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("C compiler 'cc' is unavailable for the static PTY probe")
    with tempfile.TemporaryDirectory(prefix="cao-t097-controller-pty-") as work_dir:
        work = Path(work_dir)
        source = work / "controller-pty-probe.c"
        binary = work / "controller-pty-probe"
        source.write_text(_STATIC_WORKER_SOURCE, encoding="utf-8")
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
            pytest.skip(f"could not build the static PTY probe: {exc}")
        if build.returncode != 0:
            pytest.skip(f"compiler cannot produce the required static ELF: {build.stderr.strip()}")

        executable_bytes = binary.read_bytes()
        try:
            identity = identify_static_executable("/worker", executable_bytes)
            staged = stage_static_executable(identity, executable_bytes)
        except WorkExecutableStagingUnavailable as exc:
            pytest.skip(f"sealed static ELF staging is unavailable: {exc}")
        try:
            yield staged
        finally:
            staged.close()


def _acquire_controller_pty() -> None:
    """Run after setsid and stdio duplication, before execing Bubblewrap."""
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _run_on_controller_pty(
    argv: list[str],
    source_fd: int,
    cwd: Path,
    *,
    acquire_controller_tty: bool = True,
) -> tuple[int, str]:
    master_fd, slave_fd = pty.openpty()
    process: subprocess.Popen[bytes] | None = None
    output = bytearray()
    deadline = time.monotonic() + 15
    try:
        process = subprocess.Popen(
            argv,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            env={"LC_ALL": "C"},
            close_fds=True,
            pass_fds=(source_fd,),
            start_new_session=True,
            preexec_fn=_acquire_controller_pty if acquire_controller_tty else None,
        )
        os.close(slave_fd)
        slave_fd = -1

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                pytest.fail("direct Bubblewrap PTY probe timed out; process group killed")
            readable, _, _ = select.select([master_fd], [], [], min(remaining, 0.1))
            if readable:
                try:
                    chunk = os.read(master_fd, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:  # PTY master reports EIO after the slave closes.
                        break
                    raise
                if not chunk:
                    break
                output.extend(chunk)
                continue
            if process.poll() is not None:
                break
        return process.wait(), output.decode("utf-8", errors="replace")
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        if slave_fd >= 0:
            os.close(slave_fd)
        os.close(master_fd)


def test_direct_bubblewrap_worker_reports_controller_pty_and_open_fds(
    scratch_bubblewrap: Path, staged_worker, tmp_path: Path
) -> None:
    """Record TTY and FD facts for an accepted, direct Bwrap worker launch."""
    source_fd = staged_worker.memfd_fd
    source_info = os.fstat(source_fd)
    bind_arguments = bubblewrap_bind_data_arguments(staged_worker, "/worker")
    argv = [
        str(scratch_bubblewrap),
        "--unshare-all",
        "--die-with-parent",
        "--tmpfs",
        "/",
        "--dir",
        "/dev",
        "--dir",
        "/proc",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        *bind_arguments,
        "--",
        "/worker",
        str(source_fd),
        str(source_info.st_dev),
        str(source_info.st_ino),
    ]

    print(f"T097_BWRAP_ARGV={json.dumps(argv)}")
    returncode, terminal_output = _run_on_controller_pty(argv, source_fd, tmp_path)
    report_line = next(
        (line.strip() for line in terminal_output.splitlines() if line.startswith("{")),
        None,
    )
    assert returncode == 0, (
        f"direct Bwrap launch failed with status {returncode}; "
        f"controller PTY output={terminal_output!r}"
    )
    assert report_line is not None, f"worker emitted no JSON report: {terminal_output!r}"
    report = json.loads(report_line)
    assert set(report) == {
        "stdin_isatty",
        "stdout_isatty",
        "tcgetpgrp_stdin",
        "tcgetpgrp_stdin_errno",
        "tcgetpgrp_stdout",
        "tcgetpgrp_stdout_errno",
        "process_group",
        "session_id",
        "dev_tty_open",
        "source_memfd_inherited",
        "open_fds",
    }
    assert report["open_fds"] == sorted(set(report["open_fds"]))
    assert all(type(fd) is int and fd >= 0 for fd in report["open_fds"])
    assert report["stdin_isatty"] is True
    assert report["stdout_isatty"] is True
    assert report["dev_tty_open"] is True
    # /dev/tty opens, but tcgetpgrp reports zero (rather than a usable process
    # group ID). Preserve this exact result; it does not prove foreground job
    # control for the worker.
    assert report["tcgetpgrp_stdin"] == 0
    assert report["tcgetpgrp_stdin_errno"] == 0
    assert report["tcgetpgrp_stdout"] == 0
    assert report["tcgetpgrp_stdout_errno"] == 0
    assert report["process_group"] == 0
    assert report["session_id"] == 0
    assert report["open_fds"] == [0, 1, 2]
    assert report["source_memfd_inherited"] is False
    print(f"T097_DIRECT_PTY_REPORT={json.dumps(report, sort_keys=True)}")


@pytest.mark.parametrize(
    "new_session", [False, True], ids=["inherited-session", "bwrap-new-session"]
)
def test_trusted_bootstrap_attaches_pty_before_static_worker_exec(
    scratch_bubblewrap: Path, staged_worker, tmp_path: Path, new_session: bool
) -> None:
    """Check whether a trusted post-setup bootstrap can establish job control."""
    source_fd = staged_worker.memfd_fd
    source_info = os.fstat(source_fd)
    bind_arguments = bubblewrap_bind_data_arguments(staged_worker, "/worker")
    argv = [
        str(scratch_bubblewrap),
        "--unshare-all",
        "--die-with-parent",
        *(["--new-session"] if new_session else []),
        "--tmpfs",
        "/",
        "--dir",
        "/dev",
        "--dir",
        "/proc",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        *bind_arguments,
        "--symlink",
        "/worker",
        "/bootstrap",
        "--",
        "/bootstrap",
        "--bootstrap",
        "/worker",
        str(source_fd),
        str(source_info.st_dev),
        str(source_info.st_ino),
    ]

    returncode, terminal_output = _run_on_controller_pty(
        argv, source_fd, tmp_path, acquire_controller_tty=False
    )
    reports = [
        json.loads(line.strip()) for line in terminal_output.splitlines() if line.startswith("{")
    ]
    print(
        f"T097_BOOTSTRAP_PTY_REPORT={json.dumps({'new_session': new_session, 'argv': argv, 'reports': reports}, sort_keys=True)}"
    )
    assert returncode == 0, (
        f"trusted bootstrap launch failed with status {returncode}; "
        f"new_session={new_session}; PTY output={terminal_output!r}"
    )
    assert len(reports) == 2, f"expected bootstrap and worker reports: {terminal_output!r}"
    bootstrap, worker = reports
    assert bootstrap["stage"] == "bootstrap"
    assert worker["stage"] == "worker"
    assert bootstrap["before_dev_tty_open"] is False
    assert bootstrap["before_tcgetpgrp_stdin"] == -1
    assert bootstrap["before_tcgetpgrp_stdin_errno"] == errno.ENOTTY
    if new_session:
        assert bootstrap["before_process_group"] == 1
        assert bootstrap["before_session_id"] == 1
    else:
        assert bootstrap["before_process_group"] == 0
        assert bootstrap["before_session_id"] == 0
    assert bootstrap["setsid_result"] > 0
    assert bootstrap["setsid_errno"] == 0
    assert bootstrap["tiocsctty_result"] == 0
    assert bootstrap["stdin_isatty"] is True
    assert bootstrap["dev_tty_open"] is True
    assert bootstrap["job_control_ready"] is True
    assert bootstrap["process_group"] > 0
    assert bootstrap["session_id"] > 0
    assert bootstrap["tcgetpgrp_stdin"] == bootstrap["process_group"]
    assert bootstrap["tcgetpgrp_stdin_errno"] == 0
    assert worker["stdin_isatty"] is True
    assert worker["stdout_isatty"] is True
    assert worker["process_group"] == bootstrap["process_group"]
    assert worker["session_id"] == bootstrap["session_id"]
    assert worker["tcgetpgrp_stdin"] == worker["process_group"]
    assert worker["tcgetpgrp_stdout"] == worker["process_group"]
    assert worker["tcgetpgrp_stdin_errno"] == 0
    assert worker["dev_tty_open"] is True
    assert worker["open_fds"] == [0, 1, 2]
    assert worker["source_memfd_inherited"] is False
