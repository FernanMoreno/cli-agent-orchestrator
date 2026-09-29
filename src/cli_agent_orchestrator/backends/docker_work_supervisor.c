#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

#ifndef CAO_SUPERVISOR_SOURCE_SHA256
#define CAO_SUPERVISOR_SOURCE_SHA256 "untracked"
#endif

#define CAO_REQUEST_LIMIT 16384U
#define CAO_RESPONSE_LIMIT 65536U
#define CAO_INPUT_LIMIT 32768U
#define CAO_OUTPUT_LIMIT 1048576U

static int read_exact(int fd, void *buffer, size_t size) {
    unsigned char *cursor = buffer;
    size_t remaining = size;
    while (remaining > 0U) {
        ssize_t count = read(fd, cursor, remaining);
        if (count == 0) return -1;
        if (count < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        cursor += (size_t)count;
        remaining -= (size_t)count;
    }
    return 0;
}

static int write_exact(int fd, const void *buffer, size_t size) {
    const unsigned char *cursor = buffer;
    size_t remaining = size;
    while (remaining > 0U) {
        ssize_t count = write(fd, cursor, remaining);
        if (count < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        if (count == 0) return -1;
        cursor += (size_t)count;
        remaining -= (size_t)count;
    }
    return 0;
}

static int read_line(int fd, char *buffer, size_t capacity, size_t *length) {
    size_t used = 0U;
    while (used + 1U < capacity) {
        char value;
        ssize_t count = read(fd, &value, 1U);
        if (count == 0) return -1;
        if (count < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        buffer[used++] = value;
        if (value == '\n') {
            buffer[used] = '\0';
            *length = used;
            return 0;
        }
    }
    return -1;
}

static int read_frame_header(int fd, const char *prefix, size_t limit, size_t *size) {
    char header[96];
    size_t length = 0U;
    if (read_line(fd, header, sizeof(header), &length) != 0) return -1;
    size_t prefix_length = strlen(prefix);
    if (length <= prefix_length + 1U || strncmp(header, prefix, prefix_length) != 0) return -1;
    char *end = NULL;
    errno = 0;
    unsigned long parsed = strtoul(header + prefix_length, &end, 10);
    if (errno != 0 || end == header + prefix_length || *end != '\n' || parsed > limit) return -1;
    *size = (size_t)parsed;
    return 0;
}

static int read_frame(int fd, const char *prefix, size_t limit, unsigned char *buffer, size_t *size) {
    if (read_frame_header(fd, prefix, limit, size) != 0) return -1;
    if (read_exact(fd, buffer, *size) != 0) return -1;
    char newline = '\0';
    if (read_exact(fd, &newline, 1U) != 0 || newline != '\n') return -1;
    return 0;
}

static int emit_output(const char *channel, const unsigned char *buffer, size_t size) {
    char header[96];
    int length = snprintf(header, sizeof(header), "CAO-OUT/1 %s %zu\n", channel, size);
    if (length <= 0 || (size_t)length >= sizeof(header)) return -1;
    return write_exact(STDOUT_FILENO, header, (size_t)length) == 0 &&
                   write_exact(STDOUT_FILENO, buffer, size) == 0 &&
                   write_exact(STDOUT_FILENO, "\n", 1U) == 0
               ? 0
               : -1;
}

static int forward_output(int *descriptor, const char *channel, size_t *total) {
    unsigned char buffer[16384];
    ssize_t count = read(*descriptor, buffer, sizeof(buffer));
    if (count == 0) {
        close(*descriptor);
        *descriptor = -1;
        return 0;
    }
    if (count < 0) {
        if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) return 0;
        return -1;
    }
    if ((size_t)count > CAO_OUTPUT_LIMIT - *total) return -1;
    *total += (size_t)count;
    return emit_output(channel, buffer, (size_t)count);
}

static int parse_unsigned(const char *value, unsigned long maximum, unsigned long *result) {
    char *end = NULL;
    errno = 0;
    unsigned long parsed = strtoul(value, &end, 10);
    if (errno != 0 || end == value || *end != '\0' || parsed > maximum) return -1;
    *result = parsed;
    return 0;
}

static int set_worker_identity(uid_t uid, gid_t gid) {
    if (geteuid() == 0) {
        if (setgroups(0U, NULL) != 0 || setresgid(gid, gid, gid) != 0 ||
            setresuid(uid, uid, uid) != 0) {
            return -1;
        }
    } else if (geteuid() != uid || getegid() != gid) {
        errno = EPERM;
        return -1;
    }
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) return -1;
    return 0;
}

static void close_extra_descriptors(void) {
#ifdef SYS_close_range
    if (syscall(SYS_close_range, 4U, UINT_MAX, 0U) == 0) return;
#endif
    long maximum = sysconf(_SC_OPEN_MAX);
    if (maximum < 0 || maximum > 65536) maximum = 65536;
    for (int descriptor = 4; descriptor < maximum; ++descriptor) close(descriptor);
}

static int worker_child(
    const char *worker_path,
    const char *argv0,
    uid_t worker_uid,
    gid_t worker_gid,
    int worker_mcp_fd,
    int input_fd,
    int output_fd,
    int error_fd,
    int mcp_enabled
) {
    (void)setpgid(0, 0);
    if (dup2(input_fd, STDIN_FILENO) < 0 || dup2(output_fd, STDOUT_FILENO) < 0 ||
        dup2(error_fd, STDERR_FILENO) < 0) _exit(126);
    if (mcp_enabled && dup2(worker_mcp_fd, 3) < 0) _exit(126);
    if (!mcp_enabled) close(3);
    close_extra_descriptors();
    if (set_worker_identity(worker_uid, worker_gid) != 0) _exit(126);
    if (clearenv() != 0 || setenv("LC_ALL", "C", 1) != 0) _exit(126);
    if (mcp_enabled && setenv("CAO_WORK_MCP_FD", "3", 1) != 0) _exit(126);
    char *arguments[] = {(char *)argv0, NULL};
    execve(worker_path, arguments, environ);
    _exit(127);
}

static int start_worker(
    const char *worker_path,
    const char *argv0,
    uid_t worker_uid,
    gid_t worker_gid,
    int mcp_enabled,
    int worker_input,
    size_t worker_input_size,
    int *child_pid,
    int *worker_mcp,
    int *worker_stdout,
    int *worker_stderr
) {
    int input_pipe[2] = {-1, -1};
    int output_pipe[2] = {-1, -1};
    int error_pipe[2] = {-1, -1};
    int mcp_pair[2] = {-1, -1};
    if (pipe2(input_pipe, O_CLOEXEC) != 0 || pipe2(output_pipe, O_CLOEXEC) != 0 ||
        pipe2(error_pipe, O_CLOEXEC) != 0 ||
        (mcp_enabled && socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, mcp_pair) != 0)) {
        goto failure;
    }
    pid_t pid = fork();
    if (pid < 0) goto failure;
    if (pid == 0) {
        close(input_pipe[1]);
        close(output_pipe[0]);
        close(error_pipe[0]);
        if (mcp_enabled) close(mcp_pair[0]);
        int result = worker_child(
            worker_path,
            argv0,
            worker_uid,
            worker_gid,
            mcp_enabled ? mcp_pair[1] : -1,
            input_pipe[0],
            output_pipe[1],
            error_pipe[1],
            mcp_enabled
        );
        _exit(result);
    }
    close(input_pipe[0]);
    close(output_pipe[1]);
    close(error_pipe[1]);
    if (mcp_enabled) close(mcp_pair[1]);
    *child_pid = (int)pid;
    *worker_mcp = mcp_enabled ? mcp_pair[0] : -1;
    *worker_stdout = output_pipe[0];
    *worker_stderr = error_pipe[0];
    if (worker_input_size > 0U) {
        /* worker_input is passed as an open descriptor containing the staged bytes. */
        unsigned char buffer[4096];
        size_t copied = 0U;
        while (copied < worker_input_size) {
            size_t wanted = worker_input_size - copied;
            if (wanted > sizeof(buffer)) wanted = sizeof(buffer);
            ssize_t count = read(worker_input, buffer, wanted);
            if (count <= 0 || write_exact(input_pipe[1], buffer, (size_t)count) != 0) {
                close(input_pipe[1]);
                kill(pid, SIGKILL);
                waitpid(pid, NULL, 0);
                return -1;
            }
            copied += (size_t)count;
        }
    }
    close(input_pipe[1]);
    return 0;

failure:
    for (size_t index = 0U; index < 2U; ++index) {
        if (input_pipe[index] >= 0) close(input_pipe[index]);
        if (output_pipe[index] >= 0) close(output_pipe[index]);
        if (error_pipe[index] >= 0) close(error_pipe[index]);
        if (mcp_pair[index] >= 0) close(mcp_pair[index]);
    }
    return -1;
}

static int mcp_round_trip(int worker_mcp) {
    unsigned char request[CAO_REQUEST_LIMIT + 1U];
    size_t used = 0U;
    while (used < sizeof(request)) {
        ssize_t count = read(worker_mcp, request + used, sizeof(request) - used);
        if (count < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        if (count == 0) return 1;
        if (memchr(request + used, '\n', (size_t)count) != NULL) {
            used += (size_t)count;
            break;
        }
        used += (size_t)count;
    }
    if (used == 0U || used > CAO_REQUEST_LIMIT || request[used - 1U] != '\n' ||
        memchr(request, '\n', used - 1U) != NULL) return -1;
    char header[96];
    int header_size = snprintf(header, sizeof(header), "CAO-MCP/1 %zu\n", used);
    if (header_size <= 0 || (size_t)header_size >= sizeof(header) ||
        write_exact(STDERR_FILENO, header, (size_t)header_size) != 0 ||
        write_exact(STDERR_FILENO, request, used) != 0 ||
        write_exact(STDERR_FILENO, "\n", 1U) != 0) return -1;

    unsigned char response[CAO_RESPONSE_LIMIT];
    size_t response_size = 0U;
    if (read_frame(STDIN_FILENO, "CAO-MCP/1 ", CAO_RESPONSE_LIMIT, response, &response_size) != 0 ||
        response_size == 0U || response[response_size - 1U] != '\n' ||
        write_exact(worker_mcp, response, response_size) != 0) return -1;
    return 0;
}

static int run_worker(
    const char *worker_path,
    const char *argv0,
    uid_t worker_uid,
    gid_t worker_gid,
    int mcp_enabled,
    int worker_input,
    size_t worker_input_size
) {
    int child_pid = -1;
    int worker_mcp = -1;
    int worker_stdout = -1;
    int worker_stderr = -1;
    if (start_worker(
            worker_path,
            argv0,
            worker_uid,
            worker_gid,
            mcp_enabled,
            worker_input,
            worker_input_size,
            &child_pid,
            &worker_mcp,
            &worker_stdout,
            &worker_stderr
        ) != 0) return 126;

    struct pollfd descriptors[3];
    size_t output_total = 0U;
    int status = 0;
    int reaped = 0;
    for (;;) {
        if (!reaped) {
            pid_t result = waitpid((pid_t)child_pid, &status, WNOHANG);
            if (result == (pid_t)child_pid) reaped = 1;
            else if (result < 0 && errno != EINTR) return 126;
        }
        if (reaped && worker_stdout < 0 && worker_stderr < 0 && worker_mcp < 0) break;
        nfds_t count = 0U;
        if (worker_stdout >= 0) {
            descriptors[count++] = (struct pollfd){.fd = worker_stdout, .events = POLLIN | POLLHUP};
        }
        if (worker_stderr >= 0) {
            descriptors[count++] = (struct pollfd){.fd = worker_stderr, .events = POLLIN | POLLHUP};
        }
        if (worker_mcp >= 0) {
            descriptors[count++] = (struct pollfd){.fd = worker_mcp, .events = POLLIN | POLLHUP};
        }
        int ready = poll(descriptors, count, 50);
        if (ready < 0 && errno != EINTR) return 126;
        for (nfds_t index = 0U; index < count && ready > 0; ++index) {
            if (!(descriptors[index].revents & (POLLIN | POLLHUP | POLLERR))) continue;
            if (descriptors[index].fd == worker_stdout) {
                if (forward_output(&worker_stdout, "stdout", &output_total) != 0) goto failed;
            } else if (descriptors[index].fd == worker_stderr) {
                if (forward_output(&worker_stderr, "stderr", &output_total) != 0) goto failed;
            } else if (descriptors[index].fd == worker_mcp) {
                int result = mcp_round_trip(worker_mcp);
                if (result == 1) {
                    close(worker_mcp);
                    worker_mcp = -1;
                } else if (result != 0) {
                    goto failed;
                }
            }
            --ready;
        }
    }
    if (worker_mcp >= 0) close(worker_mcp);
    int exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : (WIFSIGNALED(status) ? 128 + WTERMSIG(status) : 126);
    char exit_line[64];
    int length = snprintf(exit_line, sizeof(exit_line), "CAO-EXIT/1 %d\n", exit_code);
    if (length <= 0 || (size_t)length >= sizeof(exit_line) ||
        write_exact(STDOUT_FILENO, exit_line, (size_t)length) != 0) return 126;
    return exit_code;

failed:
    kill((pid_t)child_pid, SIGKILL);
    (void)waitpid((pid_t)child_pid, NULL, 0);
    if (worker_mcp >= 0) close(worker_mcp);
    if (worker_stdout >= 0) close(worker_stdout);
    if (worker_stderr >= 0) close(worker_stderr);
    return 126;
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--version") == 0) {
        return write_exact(STDOUT_FILENO, "cao-work-supervisor/1\n", 22U) == 0 ? 0 : 1;
    }
    const char *worker_path = NULL;
    const char *argv0 = NULL;
    unsigned long worker_uid_value = ULONG_MAX;
    unsigned long worker_gid_value = ULONG_MAX;
    int mcp_enabled = 0;
    for (int index = 1; index < argc; ++index) {
        if (strcmp(argv[index], "--protocol=1") == 0) continue;
        if (strcmp(argv[index], "--mcp") == 0) {
            if (mcp_enabled) return 64;
            mcp_enabled = 1;
        } else if (strcmp(argv[index], "--worker") == 0 && index + 1 < argc) {
            worker_path = argv[++index];
        } else if (strcmp(argv[index], "--argv0") == 0 && index + 1 < argc) {
            argv0 = argv[++index];
        } else if (strcmp(argv[index], "--uid") == 0 && index + 1 < argc) {
            if (parse_unsigned(argv[++index], (unsigned long)(uid_t)-1, &worker_uid_value) != 0) return 64;
        } else if (strcmp(argv[index], "--gid") == 0 && index + 1 < argc) {
            if (parse_unsigned(argv[++index], (unsigned long)(gid_t)-1, &worker_gid_value) != 0) return 64;
        } else {
            return 64;
        }
    }
    if (worker_path == NULL || argv0 == NULL || !worker_path[0] || !argv0[0] ||
        worker_uid_value == ULONG_MAX || worker_gid_value == ULONG_MAX) return 64;

    unsigned char worker_input[CAO_INPUT_LIMIT];
    size_t worker_input_size = 0U;
    if (read_frame(STDIN_FILENO, "CAO-INPUT/1 ", CAO_INPUT_LIMIT, worker_input, &worker_input_size) != 0) return 65;
    int worker_input_fd = -1;
    if (worker_input_size > 0U) {
        worker_input_fd = memfd_create("cao-work-input", MFD_CLOEXEC | MFD_ALLOW_SEALING);
        if (worker_input_fd < 0 || write_exact(worker_input_fd, worker_input, worker_input_size) != 0 ||
            lseek(worker_input_fd, 0, SEEK_SET) < 0) return 65;
    }

    struct stat namespace_stat;
    long namespace_device = -1;
    long namespace_inode = -1;
    if (stat("/proc/self/ns/pid", &namespace_stat) == 0) {
        namespace_device = (long)namespace_stat.st_dev;
        namespace_inode = (long)namespace_stat.st_ino;
    }
    char ready[512];
    int ready_length = snprintf(
        ready,
        sizeof(ready),
        "{\"protocol\":1,\"mcp_enabled\":%s,\"worker_uid\":%lu,\"worker_gid\":%lu,"
        "\"supervisor_source_sha256\":\"%s\",\"pid_namespace\":[%ld,%ld],"
        "\"worker_socket_fd\":%d}\n",
        mcp_enabled ? "true" : "false",
        worker_uid_value,
        worker_gid_value,
        CAO_SUPERVISOR_SOURCE_SHA256,
        namespace_device,
        namespace_inode,
        mcp_enabled ? 3 : -1
    );
    char header[96];
    int header_length = snprintf(header, sizeof(header), "CAO-READY/1 %d\n", ready_length);
    if (ready_length <= 0 || (size_t)ready_length >= sizeof(ready) || header_length <= 0 ||
        (size_t)header_length >= sizeof(header) || write_exact(STDOUT_FILENO, header, (size_t)header_length) != 0 ||
        write_exact(STDOUT_FILENO, ready, (size_t)ready_length) != 0 ||
        write_exact(STDOUT_FILENO, "\n", 1U) != 0) return 65;

    char go_line[32];
    size_t go_length = 0U;
    if (read_line(STDIN_FILENO, go_line, sizeof(go_line), &go_length) != 0 || strcmp(go_line, "CAO-GO/1\n") != 0) return 65;
    int exit_code = run_worker(
        worker_path,
        argv0,
        (uid_t)worker_uid_value,
        (gid_t)worker_gid_value,
        mcp_enabled,
        worker_input_fd,
        worker_input_size
    );
    if (worker_input_fd >= 0) close(worker_input_fd);
    return exit_code;
}
