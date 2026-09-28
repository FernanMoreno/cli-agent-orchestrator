#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/memfd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
static const char *loader_path = T098_LOADER_PATH;

static int report(const char *message) {
    size_t remaining = strlen(message);
    const char *cursor = message;

    while (remaining > 0) {
        ssize_t written = write(STDOUT_FILENO, cursor, remaining);
        if (written < 0 && errno == EINTR) {
            continue;
        }
        if (written <= 0) {
            return 1;
        }
        cursor += written;
        remaining -= (size_t) written;
    }
    return 0;
}

static int expect_exec_denied(const char *path, char *const arguments[], const char *label) {
    pid_t child = fork();
    int status;

    if (child < 0) {
        return report("fork-failed\n");
    }
    if (child == 0) {
        execve(path, arguments, environ);
        _exit(errno == EACCES || errno == EPERM ? 77 : 78);
    }
    if (waitpid(child, &status, 0) != child || !WIFEXITED(status)) {
        return report("exec-probe-not-reaped\n");
    }
    if (WEXITSTATUS(status) != 77) {
        return report("unmapped-exec-escaped\n");
    }
    return report(label);
}

static int expect_mapped_exec(void) {
    pid_t child = fork();
    int status;

    if (child < 0) {
        return report("fork-failed\n");
    }
    if (child == 0) {
        char *arguments[] = {"/worker", "--mapped-descendant", NULL};
        execve("/exec/worker", arguments, environ);
        _exit(79);
    }
    if (waitpid(child, &status, 0) != child || !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        return report("mapped-exec-blocked\n");
    }
    return 0;
}

static int probe_descendant_policy(void) {
    char *execveat_arguments[] = {"fd-probe", NULL};
    long memfd;

    errno = 0;
    memfd = syscall(SYS_memfd_create, "t098-descendant-probe", MFD_CLOEXEC | MFD_EXEC);
    if (memfd >= 0) {
        close((int) memfd);
        return report("memfd-create-escaped\n");
    }
    if (errno != EPERM || report("memfd-create-denied\n") != 0) {
        return report("memfd-create-wrong-error\n");
    }

    errno = 0;
    syscall(SYS_execveat, STDOUT_FILENO, "", execveat_arguments, environ, AT_EMPTY_PATH);
    if (errno != EPERM || report("execveat-denied\n") != 0) {
        return report("execveat-wrong-error\n");
    }

    if (expect_mapped_exec() != 0) {
        return 1;
    }

    char *false_arguments[] = {"/usr/bin/false", NULL};
    if (expect_exec_denied(
            "/usr/bin/false", false_arguments, "unmapped-exec-denied\n") != 0) {
        return 1;
    }

    char *loader_arguments[] = {(char *) loader_path, "/dev/null", NULL};
    if (expect_exec_denied(
            loader_path, loader_arguments, "direct-loader-denied\n") != 0) {
        return 1;
    }
    return 0;
}

int main(int argc, char **argv) {
    pid_t intermediate;
    int intermediate_status;
    int child_status;
    int failed = 0;

    if (argc > 1 && strcmp(argv[1], "--mapped-descendant") == 0) {
        return report("mapped-descendant-exec-allowed\n");
    }

    intermediate = fork();
    if (intermediate < 0) {
        return report("first-fork-failed\n");
    }
    if (intermediate == 0) {
        pid_t grandchild = fork();
        if (grandchild < 0) {
            _exit(80);
        }
        if (grandchild > 0) {
            _exit(0);
        }
        _exit(probe_descendant_policy() == 0 ? 0 : 81);
    }

    if (waitpid(intermediate, &intermediate_status, 0) != intermediate ||
        !WIFEXITED(intermediate_status) || WEXITSTATUS(intermediate_status) != 0) {
        failed = 1;
    }
    for (;;) {
        pid_t adopted = waitpid(-1, &child_status, 0);
        if (adopted < 0) {
            if (errno == ECHILD) {
                break;
            }
            if (errno == EINTR) {
                continue;
            }
            failed = 1;
            break;
        }
        if (!WIFEXITED(child_status) || WEXITSTATUS(child_status) != 0) {
            failed = 1;
        }
    }
    if (report(failed ? "double-fork-descendant-failed\n" : "double-fork-descendant-reaped\n") != 0) {
        return 1;
    }
    return failed;
}
