/*
 * Inert, self-contained T097 probes for Bwrap descriptor and loader behavior.
 * This program only reads a fixed memfd marker or writes a marker in the
 * caller's private test directory; it never accesses credentials or services.
 */
#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <linux/landlock.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define PROBE_UNAVAILABLE 78
#define PROBE_ERROR 79

extern char **environ;

static const char fd_marker[] = "cao-t097-inert-fd-probe\n";
static const char payload_marker[] = "t097-writable-shm-elf-ran\n";

static int write_marker(const char *path) {
    int fd = open(path, O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC, 0600);

    if (fd < 0) {
        perror("open marker");
        return PROBE_ERROR;
    }
    if (write(fd, payload_marker, sizeof(payload_marker) - 1) !=
        (ssize_t) (sizeof(payload_marker) - 1)) {
        perror("write marker");
        close(fd);
        return PROBE_ERROR;
    }
    close(fd);
    return 0;
}

static int check_inherited_memfd(const char *fd_text) {
    char *end = NULL;
    long fd_number = strtol(fd_text, &end, 10);
    char contents[sizeof(fd_marker)];
    struct stat info;
    ssize_t count;

    if (end == fd_text || *end != '\0' || fd_number < 3 || fd_number > 65535) {
        fprintf(stderr, "invalid inherited fd number\n");
        return PROBE_ERROR;
    }
    if (fstat((int) fd_number, &info) < 0 || info.st_nlink != 0) {
        puts("memfd-inherited=absent");
        return 1;
    }
    count = pread((int) fd_number, contents, sizeof(fd_marker) - 1, 0);
    if (count == (ssize_t) (sizeof(fd_marker) - 1) &&
        memcmp(contents, fd_marker, sizeof(fd_marker) - 1) == 0) {
        puts("memfd-inherited=present");
        return 0;
    }
    puts("memfd-inherited=absent");
    return 1;
}

static int copy_file(const char *source_path, const char *destination_path) {
    char buffer[16384];
    int source_fd = open(source_path, O_RDONLY | O_CLOEXEC);
    int destination_fd;
    ssize_t count;

    if (source_fd < 0) {
        perror("open probe source");
        return PROBE_ERROR;
    }
    destination_fd = open(destination_path,
                          O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC, 0700);
    if (destination_fd < 0) {
        perror("create /dev/shm payload");
        close(source_fd);
        return PROBE_ERROR;
    }
    while ((count = read(source_fd, buffer, sizeof(buffer))) > 0) {
        ssize_t offset = 0;
        while (offset < count) {
            ssize_t written = write(destination_fd, buffer + offset,
                                    (size_t) (count - offset));
            if (written < 0) {
                perror("write /dev/shm payload");
                close(source_fd);
                close(destination_fd);
                return PROBE_ERROR;
            }
            offset += written;
        }
    }
    if (count < 0 || fchmod(destination_fd, 0700) < 0) {
        perror("prepare /dev/shm payload");
        close(source_fd);
        close(destination_fd);
        return PROBE_ERROR;
    }
    close(source_fd);
    close(destination_fd);
    return 0;
}

static int install_loader_only_landlock(const char *loader_path) {
    struct landlock_ruleset_attr ruleset_attr = {
        .handled_access_fs = LANDLOCK_ACCESS_FS_EXECUTE,
    };
    struct landlock_path_beneath_attr loader_rule;
    int ruleset_fd = syscall(SYS_landlock_create_ruleset, &ruleset_attr,
                             sizeof(ruleset_attr), 0);
    int loader_fd;

    if (ruleset_fd < 0) {
        if (errno == ENOSYS || errno == EOPNOTSUPP || errno == EINVAL || errno == EPERM) {
            fprintf(stderr, "landlock-unavailable: create_ruleset errno=%d\n", errno);
            return PROBE_UNAVAILABLE;
        }
        perror("landlock_create_ruleset");
        return PROBE_ERROR;
    }
    loader_fd = open(loader_path, O_PATH | O_CLOEXEC);
    if (loader_fd < 0) {
        perror("open allowed loader");
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
        if (errno == ENOSYS || errno == EOPNOTSUPP || errno == EINVAL || errno == EPERM) {
            fprintf(stderr, "landlock-unavailable: restrict_self errno=%d\n", errno);
            close(loader_fd);
            close(ruleset_fd);
            return PROBE_UNAVAILABLE;
        }
        perror("landlock_restrict_self");
        close(loader_fd);
        close(ruleset_fd);
        return PROBE_ERROR;
    }
    close(loader_fd);
    close(ruleset_fd);
    return 0;
}

static int probe_writable_shm_loader(int argc, char **argv) {
    const char *source_path;
    const char *payload_path;
    const char *marker_path;
    const char *loader_path;
    char *payload_args[5];
    int result;

    if (argc != 6) {
        fprintf(stderr,
                "usage: --shm-loader SOURCE PAYLOAD MARKER LOADER\n");
        return PROBE_ERROR;
    }
    source_path = argv[2];
    payload_path = argv[3];
    marker_path = argv[4];
    loader_path = argv[5];
    result = copy_file(source_path, payload_path);
    if (result != 0) {
        return result;
    }
    result = install_loader_only_landlock(loader_path);
    if (result != 0) {
        return result;
    }

    payload_args[0] = (char *) payload_path;
    payload_args[1] = "--write-marker";
    payload_args[2] = (char *) marker_path;
    payload_args[3] = NULL;
    execve(payload_path, payload_args, environ);
    if (errno != EACCES && errno != EPERM) {
        perror("direct exec of writable /dev/shm ELF");
        return PROBE_ERROR;
    }
    puts("direct-exec-denied=1");
    fflush(stdout);

    payload_args[0] = (char *) loader_path;
    payload_args[1] = (char *) payload_path;
    payload_args[2] = "--write-marker";
    payload_args[3] = (char *) marker_path;
    payload_args[4] = NULL;
    execve(loader_path, payload_args, environ);
    perror("exec allowed loader");
    return PROBE_ERROR;
}

static int wait_for_child_bounded(pid_t child, int *status) {
    struct timespec deadline;
    struct timespec now;
    const struct timespec pause = {.tv_sec = 0, .tv_nsec = 10 * 1000 * 1000};

    if (clock_gettime(CLOCK_MONOTONIC, &deadline) < 0) {
        perror("clock_gettime");
        return PROBE_ERROR;
    }
    deadline.tv_sec += 10;
    for (;;) {
        pid_t waited = waitpid(child, status, WNOHANG);
        if (waited == child) {
            return 0;
        }
        if (waited < 0 && errno != EINTR) {
            perror("waitpid detached descendant");
            return PROBE_ERROR;
        }
        if (clock_gettime(CLOCK_MONOTONIC, &now) < 0) {
            perror("clock_gettime");
            (void) kill(-child, SIGKILL);
            (void) kill(child, SIGKILL);
            while (waitpid(child, status, 0) < 0 && errno == EINTR) {
            }
            return PROBE_ERROR;
        }
        if (now.tv_sec > deadline.tv_sec ||
            (now.tv_sec == deadline.tv_sec && now.tv_nsec >= deadline.tv_nsec)) {
            (void) kill(-child, SIGKILL);
            (void) kill(child, SIGKILL);
            while (waitpid(child, status, 0) < 0 && errno == EINTR) {
            }
            fprintf(stderr, "detached descendant probe timed out\n");
            return PROBE_ERROR;
        }
        (void) nanosleep(&pause, NULL);
    }
}

static int run_writable_shm_loader_as_detached_descendant(
    const char *payload_path, const char *marker_path, const char *loader_path) {
    pid_t child = fork();
    int status;

    if (child < 0) {
        perror("fork detached descendant");
        return PROBE_ERROR;
    }
    if (child == 0) {
        pid_t grandchild;
        if (setsid() < 0) {
            perror("setsid detached descendant");
            _exit(PROBE_ERROR);
        }
        grandchild = fork();
        if (grandchild < 0) {
            perror("fork grandchild");
            _exit(PROBE_ERROR);
        }
        if (grandchild > 0) {
            do {
                child = waitpid(grandchild, &status, 0);
            } while (child < 0 && errno == EINTR);
            if (child < 0) {
                perror("waitpid grandchild");
                _exit(PROBE_ERROR);
            }
            if (WIFEXITED(status)) {
                _exit(WEXITSTATUS(status));
            }
            _exit(PROBE_ERROR);
        }

        char *direct_args[] = {
            (char *) payload_path,
            "--write-marker",
            (char *) marker_path,
            NULL,
        };
        char *loader_args[] = {
            (char *) loader_path,
            (char *) payload_path,
            "--write-marker",
            (char *) marker_path,
            NULL,
        };
        execve(payload_path, direct_args, environ);
        if (errno != EACCES && errno != EPERM) {
            perror("direct exec of writable descendant ELF");
            _exit(PROBE_ERROR);
        }
        puts("direct-exec-denied=1");
        fflush(stdout);

        execve(loader_path, loader_args, environ);
        perror("exec allowed loader from detached descendant");
        _exit(PROBE_ERROR);
    }

    if (wait_for_child_bounded(child, &status) != 0) {
        return PROBE_ERROR;
    }
    if (WIFEXITED(status)) {
        return WEXITSTATUS(status);
    }
    return PROBE_ERROR;
}

static int probe_writable_shm_loader_descendant(int argc, char **argv) {
    const char *source_path;
    const char *payload_path;
    const char *marker_path;
    const char *loader_path;
    int result;

    if (argc != 6) {
        fprintf(stderr,
                "usage: --shm-loader-descendant SOURCE PAYLOAD MARKER LOADER\n");
        return PROBE_ERROR;
    }
    source_path = argv[2];
    payload_path = argv[3];
    marker_path = argv[4];
    loader_path = argv[5];
    result = copy_file(source_path, payload_path);
    if (result != 0) {
        return result;
    }
    result = install_loader_only_landlock(loader_path);
    if (result != 0) {
        return result;
    }
    puts("descendant=setsid-double-fork");
    fflush(stdout);
    return run_writable_shm_loader_as_detached_descendant(
        payload_path, marker_path, loader_path);
}

int main(int argc, char **argv) {
    if (argc >= 2 && strcmp(argv[1], "--fd-check") == 0) {
        if (argc != 3) {
            fprintf(stderr, "--fd-check needs an fd number\n");
            return PROBE_ERROR;
        }
        return check_inherited_memfd(argv[2]);
    }
    if (argc >= 2 && strcmp(argv[1], "--write-marker") == 0) {
        if (argc != 3) {
            fprintf(stderr, "--write-marker needs a marker path\n");
            return PROBE_ERROR;
        }
        return write_marker(argv[2]);
    }
    if (argc >= 2 && strcmp(argv[1], "--shm-loader") == 0) {
        return probe_writable_shm_loader(argc, argv);
    }
    if (argc >= 2 && strcmp(argv[1], "--shm-loader-descendant") == 0) {
        return probe_writable_shm_loader_descendant(argc, argv);
    }
    fprintf(stderr,
            "expected --fd-check, --write-marker, --shm-loader, or "
            "--shm-loader-descendant\n");
    return PROBE_ERROR;
}
