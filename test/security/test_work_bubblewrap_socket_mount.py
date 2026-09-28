"""Characterize pathname-mounted AF_UNIX proxy transport for T097."""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest


_T097_ROOT = Path("/tmp/caos-exec/T097")
_SCRATCH_BWRAP = _T097_ROOT / "bubblewrap-build" / "bwrap"
_VERSION_RE = re.compile(r"^bubblewrap\s+(\d+)\.(\d+)\.(\d+)(?:\s.*)?$")
_REPORT_PREFIX = "T097_SOCKET_MOUNT_REPORT="

_WORKER_SOURCE = r"""
#define _GNU_SOURCE
#include <errno.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

int main(int argc, char **argv) {
    struct stat mounted_info;
    struct stat host_info;
    struct sockaddr_un address;
    char response[4];
    const char *mounted_path;
    const char *host_path;
    unsigned long long expected_dev;
    unsigned long long expected_ino;
    int parent_socket_fd;
    int fd;

    if (argc != 6) {
        fputs("invalid-arguments\n", stderr);
        return 10;
    }
    mounted_path = argv[1];
    host_path = argv[2];
    parent_socket_fd = atoi(argv[3]);
    expected_dev = strtoull(argv[4], NULL, 10);
    expected_ino = strtoull(argv[5], NULL, 10);
    if (fstat(parent_socket_fd, &host_info) == 0 &&
        (unsigned long long)host_info.st_dev == expected_dev &&
        (unsigned long long)host_info.st_ino == expected_ino) {
        fputs("parent-socket-fd-inherited\n", stderr);
        return 20;
    }
    if (lstat(mounted_path, &mounted_info) != 0) {
        perror("stat-mounted-endpoint");
        return 11;
    }
    if (!S_ISSOCK(mounted_info.st_mode)) {
        fputs("mounted-endpoint-is-not-socket\n", stderr);
        return 12;
    }
    if (lstat(host_path, &host_info) == 0 || errno != ENOENT) {
        fputs("host-endpoint-path-visible\n", stderr);
        return 13;
    }

    fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        perror("create-client-socket");
        return 14;
    }
    memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    if (strlen(mounted_path) >= sizeof(address.sun_path)) {
        fputs("mounted-path-too-long\n", stderr);
        close(fd);
        return 15;
    }
    strcpy(address.sun_path, mounted_path);
    if (connect(fd, (struct sockaddr *)&address, sizeof(address)) != 0) {
        perror("connect-mounted-endpoint");
        close(fd);
        return 16;
    }
    if (write(fd, "PING", 4) != 4) {
        perror("write-request");
        close(fd);
        return 17;
    }
    if (read(fd, response, sizeof(response)) != (ssize_t)sizeof(response)) {
        perror("read-response");
        close(fd);
        return 18;
    }
    close(fd);
    if (memcmp(response, "PONG", 4) != 0) {
        fputs("unexpected-response\n", stderr);
        return 19;
    }
    puts("T097_SOCKET_MOUNT_REPORT=connected;host-path-hidden;parent-fd-absent");
    return 0;
}
"""

_LATE_SOCKET_SOURCE = r"""
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

int main(void) {
    char go;
    struct sockaddr_un address = {0};
    address.sun_family = AF_UNIX;
    strcpy(address.sun_path, "/runtime/late.sock");
    puts("READY");
    fflush(stdout);
    if (read(0, &go, 1) != 1 || go != 'G') return 11;
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return 12;
    if (connect(fd, (struct sockaddr *)&address, sizeof(address)) == 0) {
        puts("CONNECTED");
        close(fd);
        return 0;
    }
    printf("DENIED:%d\n", errno);
    close(fd);
    return 0;
}
"""


@pytest.fixture(scope="session")
def scratch_bubblewrap() -> Path:
    """Use only the accepted T097 scratch Bubblewrap build."""
    if not sys.platform.startswith("linux"):
        pytest.skip("AF_UNIX mount characterization requires Linux")
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
            "T097 scratch Bubblewrap did not report a parseable version: "
            f"stdout={version_probe.stdout!r} stderr={version_probe.stderr!r}"
        )
    version = tuple(int(part) for part in match.groups())
    if version != (0, 13, 0):
        pytest.skip(
            "this characterization requires accepted T097 scratch Bubblewrap 0.13.0; "
            f"observed {version_probe.stdout.strip()}"
        )

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
    if namespace_probe.returncode != 0:
        pytest.skip(
            "T097 scratch Bubblewrap cannot create required namespaces: "
            f"{namespace_probe.stderr.strip() or namespace_probe.returncode}"
        )
    return executable


@pytest.fixture(scope="session")
def static_socket_worker(tmp_path_factory) -> Path:
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("C compiler 'cc' is unavailable for the static worker")
    work = tmp_path_factory.mktemp("t097-socket-worker")
    source = work / "socket-worker.c"
    binary = work / "socket-worker"
    source.write_text(_WORKER_SOURCE, encoding="utf-8")
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
        pytest.skip(f"compiler could not build the static socket worker: {exc}")
    if build.returncode != 0:
        pytest.skip(f"compiler cannot produce a static worker: {build.stderr.strip()}")
    return binary


def test_late_socket_in_read_only_runtime_tree_remains_connectable_without_snapshot(
    scratch_bubblewrap: Path, tmp_path: Path,
) -> None:
    """Positive characterization: a mutable ro-bind does not fence AF_UNIX."""
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("static C compiler unavailable")
    source = tmp_path / "late.c"
    worker = tmp_path / "late-worker"
    source.write_text(_LATE_SOCKET_SOURCE)
    build = subprocess.run(
        [compiler, "-static", "-O2", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(worker)],
        capture_output=True, text=True, timeout=30,
    )
    if build.returncode != 0:
        pytest.skip(f"static worker build unavailable: {build.stderr}")
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    process = subprocess.Popen(
        [str(scratch_bubblewrap), "--unshare-all", "--die-with-parent",
         "--dir", "/runtime", "--ro-bind", str(runtime), "/runtime",
         "--ro-bind", str(worker), "/worker", "--", "/worker"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env={"LC_ALL": "C"}, close_fds=True,
    )
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        assert process.stdout.readline().strip() == "READY"
        listener.bind(str(runtime / "late.sock"))
        listener.listen(1)
        stdout, stderr = process.communicate(input="G", timeout=10)
        assert process.returncode == 0, stderr
        assert stdout.strip() == "CONNECTED", stdout
    finally:
        listener.close()
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_parent_and_static_worker_exchange_over_mounted_socket_path(
    scratch_bubblewrap: Path,
    static_socket_worker: Path,
    tmp_path: Path,
) -> None:
    attempt_dir = tmp_path / "attempt"
    attempt_dir.mkdir(mode=0o700)
    host_endpoint = attempt_dir / "proxy.sock"
    mounted_endpoint = "/run/work-proxy.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(host_endpoint))
    listener.listen(1)
    listener.settimeout(8)
    listener.set_inheritable(True)
    endpoint_info = os.stat(host_endpoint)

    server_errors: list[str] = []
    exchanges: list[tuple[bytes, bytes]] = []

    def serve_one_request() -> None:
        try:
            connection, _ = listener.accept()
            with connection:
                request = connection.recv(4)
                exchanges.append((request, b"PONG"))
                if request == b"PING":
                    connection.sendall(b"PONG")
                else:
                    server_errors.append(f"unexpected request: {request!r}")
        except (OSError, TimeoutError) as exc:
            server_errors.append(f"parent accept/response failed: {exc}")

    server_thread = threading.Thread(target=serve_one_request, daemon=True)
    server_thread.start()
    completed: subprocess.CompletedProcess[str] | None = None
    cleanup_ok = False
    try:
        completed = subprocess.run(
            [
                str(scratch_bubblewrap),
                "--unshare-all",
                "--die-with-parent",
                "--dir",
                "/run",
                "--ro-bind",
                str(static_socket_worker),
                "/worker",
                "--ro-bind",
                str(host_endpoint),
                mounted_endpoint,
                "--",
                "/worker",
                mounted_endpoint,
                str(host_endpoint),
                str(listener.fileno()),
                str(endpoint_info.st_dev),
                str(endpoint_info.st_ino),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=12,
            env={"LC_ALL": "C"},
            close_fds=True,
        )
    finally:
        server_thread.join(timeout=9)
        listener.close()
        try:
            host_endpoint.unlink(missing_ok=True)
            attempt_dir.rmdir()
        finally:
            cleanup_ok = not host_endpoint.exists() and not attempt_dir.exists()

    assert completed is not None
    assert server_thread.is_alive() is False, "parent socket server did not stop"
    assert cleanup_ok, "attempt-scoped socket path or directory survived cleanup"
    assert not server_errors, "; ".join(server_errors)
    assert exchanges == [(b"PING", b"PONG")], f"unexpected parent exchange: {exchanges!r}"
    assert completed.returncode == 0, (
        "expected confined static ELF to connect through the mounted AF_UNIX path; "
        f"status={completed.returncode}; stdout={completed.stdout!r}; "
        f"stderr={completed.stderr!r}"
    )
    assert _REPORT_PREFIX + "connected;host-path-hidden;parent-fd-absent" in completed.stdout
