/*
 * Narrow Landlock executable-policy probe used by
 * test/security/test_work_bubblewrap_adversarial.py.
 *
 * The payload mode only writes a marker in the test's Bubblewrap-mounted
 * scratch directory. It does not invoke commands or access host state.
 */
#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/landlock.h>
#include <linux/memfd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

#ifndef MFD_EXEC
#define MFD_EXEC 0x0010U
#endif

#define PROBE_DENIED 77
#define PROBE_UNAVAILABLE 78
#define PROBE_ERROR 79

extern char **environ;

static int write_marker(const char *path) {
    static const char marker[] = "landlock-adversarial-payload-ran\n";
    int fd = open(path, O_CREAT | O_TRUNC | O_WRONLY, 0600);

    if (fd < 0) {
        perror("payload open marker");
        return PROBE_ERROR;
    }
    if (write(fd, marker, sizeof(marker) - 1) != (ssize_t) (sizeof(marker) - 1)) {
        perror("payload write marker");
        close(fd);
        return PROBE_ERROR;
    }
    close(fd);
    return 0;
}

static int install_exec_only_landlock(const char *allowed_exec_path) {
    struct landlock_ruleset_attr ruleset_attr = {
        .handled_access_fs = LANDLOCK_ACCESS_FS_EXECUTE,
    };
    struct landlock_path_beneath_attr loader_rule;
    int ruleset_fd;
    int loader_fd;

    ruleset_fd = syscall(SYS_landlock_create_ruleset, &ruleset_attr, sizeof(ruleset_attr), 0);
    if (ruleset_fd < 0) {
        if (errno == ENOSYS || errno == EOPNOTSUPP) {
            fprintf(stderr, "landlock-unavailable: create_ruleset errno=%d\n", errno);
            return PROBE_UNAVAILABLE;
        }
        perror("landlock_create_ruleset");
        return PROBE_ERROR;
    }

    loader_fd = open(allowed_exec_path, O_PATH | O_CLOEXEC);
    if (loader_fd < 0) {
        perror("open executable allowlist path");
        close(ruleset_fd);
        return PROBE_ERROR;
    }
    loader_rule.allowed_access = LANDLOCK_ACCESS_FS_EXECUTE;
    loader_rule.parent_fd = loader_fd;
    if (syscall(SYS_landlock_add_rule, ruleset_fd, LANDLOCK_RULE_PATH_BENEATH,
                &loader_rule, 0) < 0) {
        perror("landlock_add_rule");
        close(loader_fd);
        close(ruleset_fd);
        return PROBE_ERROR;
    }
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0) {
        perror("PR_SET_NO_NEW_PRIVS");
        close(loader_fd);
        close(ruleset_fd);
        return PROBE_ERROR;
    }
    if (syscall(SYS_landlock_restrict_self, ruleset_fd, 0) < 0) {
        perror("landlock_restrict_self");
        close(loader_fd);
        close(ruleset_fd);
        return PROBE_ERROR;
    }
    close(loader_fd);
    close(ruleset_fd);
    return 0;
}

static int report_exec_failure(const char *operation, const char *target) {
    if (errno == EACCES || errno == EPERM) {
        fprintf(stderr, "denied: %s errno=%d target=%s\n", operation, errno, target);
        return PROBE_DENIED;
    }
    if (errno == ENOSYS || errno == EOPNOTSUPP || errno == EINVAL) {
        fprintf(stderr, "probe-unavailable: %s errno=%d target=%s\n", operation, errno, target);
        return PROBE_UNAVAILABLE;
    }
    fprintf(stderr, "probe-error: %s errno=%d target=%s\n", operation, errno, target);
    return PROBE_ERROR;
}

static int exec_path(const char *operation, const char *path, char *const args[]) {
    execve(path, args, environ);
    return report_exec_failure(operation, path);
}

static int copy_into_memfd(const char *payload_path) {
    char bytes[65536];
    int source_fd;
    int memfd;
    ssize_t bytes_read;

    source_fd = open(payload_path, O_RDONLY | O_CLOEXEC);
    if (source_fd < 0) {
        perror("open payload for memfd copy");
        return -1;
    }
    memfd = syscall(SYS_memfd_create, "cao-landlock-probe", MFD_ALLOW_SEALING | MFD_EXEC);
    if (memfd < 0 && errno == EINVAL) {
        memfd = syscall(SYS_memfd_create, "cao-landlock-probe", MFD_ALLOW_SEALING);
    }
    if (memfd < 0) {
        int saved_errno = errno;
        if (saved_errno == ENOSYS || saved_errno == EOPNOTSUPP || saved_errno == EPERM ||
            saved_errno == EINVAL) {
            fprintf(stderr, "probe-unavailable: memfd_create errno=%d\n", saved_errno);
        } else {
            perror("memfd_create");
        }
        close(source_fd);
        errno = saved_errno;
        return -1;
    }

    while ((bytes_read = read(source_fd, bytes, sizeof(bytes))) > 0) {
        ssize_t offset = 0;
        while (offset < bytes_read) {
            ssize_t bytes_written = write(memfd, bytes + offset, (size_t) (bytes_read - offset));
            if (bytes_written < 0) {
                perror("write memfd");
                close(source_fd);
                close(memfd);
                return -1;
            }
            offset += bytes_written;
        }
    }
    if (bytes_read < 0 || fchmod(memfd, 0700) < 0 || lseek(memfd, 0, SEEK_SET) < 0) {
        perror("prepare memfd payload");
        close(source_fd);
        close(memfd);
        return -1;
    }
    close(source_fd);
    return memfd;
}

int main(int argc, char **argv) {
    const char *mode;
    const char *payload_path;
    const char *marker_path;
    const char *loader_path;
    const char *allowed_exec_path;
    char *payload_args[4];
    int setup_result;

    if (argc >= 2 && strcmp(argv[1], "--payload") == 0) {
        if (argc != 3) {
            fprintf(stderr, "payload mode needs a marker path\n");
            return PROBE_ERROR;
        }
        return write_marker(argv[2]);
    }
    if (argc != 5) {
        fprintf(stderr, "usage: probe MODE PAYLOAD MARKER LOADER\n");
        return PROBE_ERROR;
    }

    mode = argv[1];
    payload_path = argv[2];
    marker_path = argv[3];
    loader_path = argv[4];
    allowed_exec_path = loader_path;
    if (strcmp(mode, "kernel-execve-payload-only") == 0 ||
        strcmp(mode, "direct-loader-payload-only") == 0) {
        allowed_exec_path = payload_path;
    }
    setup_result = install_exec_only_landlock(allowed_exec_path);
    if (setup_result != 0) {
        return setup_result;
    }

    payload_args[0] = (char *) payload_path;
    payload_args[1] = "--payload";
    payload_args[2] = (char *) marker_path;
    payload_args[3] = NULL;

    if (strcmp(mode, "kernel-execve") == 0) {
        return exec_path("execve", payload_path, payload_args);
    }
    if (strcmp(mode, "kernel-execve-payload-only") == 0) {
        return exec_path("PT_INTERP bootstrap with loader denied", payload_path, payload_args);
    }
    if (strcmp(mode, "direct-loader-payload-only") == 0) {
        char *loader_args[] = {
            (char *) loader_path, (char *) payload_path, "--payload", (char *) marker_path, NULL,
        };
        return exec_path("direct loader not in allowlist", loader_path, loader_args);
    }
    if (strcmp(mode, "allowlisted-loader") == 0) {
        char *loader_args[] = {
            (char *) loader_path, (char *) payload_path, "--payload", (char *) marker_path, NULL,
        };
        return exec_path("direct-loader", loader_path, loader_args);
    }
    if (strcmp(mode, "proc-self-fd-file") == 0) {
        int payload_fd = open(payload_path, O_RDONLY);
        char proc_fd_path[64];
        if (payload_fd < 0) {
            perror("open payload for /proc/self/fd");
            return PROBE_ERROR;
        }
        if (snprintf(proc_fd_path, sizeof(proc_fd_path), "/proc/self/fd/%d", payload_fd) < 0) {
            fprintf(stderr, "could not format /proc/self/fd path\n");
            return PROBE_ERROR;
        }
        return exec_path("write-path /proc/self/fd execve", proc_fd_path, payload_args);
    }
    if (strcmp(mode, "memfd-execve") == 0 ||
        strcmp(mode, "memfd-execveat") == 0 ||
        strcmp(mode, "allowlisted-loader-memfd") == 0) {
        int memfd = copy_into_memfd(payload_path);
        char proc_fd_path[64];

        if (memfd < 0) {
            return errno == ENOSYS || errno == EOPNOTSUPP || errno == EPERM || errno == EINVAL
                ? PROBE_UNAVAILABLE : PROBE_ERROR;
        }
        if (snprintf(proc_fd_path, sizeof(proc_fd_path), "/proc/self/fd/%d", memfd) < 0) {
            fprintf(stderr, "could not format /proc/self/fd path\n");
            return PROBE_ERROR;
        }
        if (strcmp(mode, "memfd-execve") == 0) {
            return exec_path("memfd execve through /proc/self/fd", proc_fd_path, payload_args);
        }
        if (strcmp(mode, "memfd-execveat") == 0) {
            syscall(SYS_execveat, memfd, "", payload_args, environ, AT_EMPTY_PATH);
            return report_exec_failure("memfd execveat(AT_EMPTY_PATH)", "memfd");
        }
        {
            char *loader_args[] = {
                (char *) loader_path, proc_fd_path, "--payload", (char *) marker_path, NULL,
            };
            return exec_path("dynamic loader on memfd via /proc/self/fd", loader_path, loader_args);
        }
    }

    fprintf(stderr, "unknown probe mode: %s\n", mode);
    return PROBE_ERROR;
}
