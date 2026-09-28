#define _GNU_SOURCE

#include <arpa/inet.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <mqueue.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ipc.h>
#include <sys/shm.h>
#include <sys/socket.h>
#include <unistd.h>

#ifndef T097_SHM_KEY
#error "T097_SHM_KEY must be provided by the acceptance harness"
#endif

static int report(const char *message) {
    size_t length = strlen(message);
    const char *cursor = message;
    while (length > 0) {
        ssize_t written = write(STDOUT_FILENO, cursor, length);
        if (written < 0 && errno == EINTR) continue;
        if (written <= 0) return 1;
        cursor += written;
        length -= (size_t)written;
    }
    return 0;
}

int main(void) {
    if (access("/etc/passwd", F_OK) == 0) return report("host-etc-visible\n");
    if (errno != ENOENT) return report("host-etc-probe-failed\n");
    if (report("host-etc-hidden\n") != 0) return 1;

    errno = 0;
    int host_binary = open("/usr/bin/true", O_WRONLY | O_CLOEXEC);
    if (host_binary >= 0) {
        close(host_binary);
        return report("readonly-usr-writable\n");
    }
    if (errno != EROFS && errno != EACCES) return report("readonly-usr-wrong-error\n");
    if (report("readonly-usr-denied\n") != 0) return 1;

    int private_tmp = open("/tmp/t097-private-tmp-effect", O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (private_tmp >= 0) {
        close(private_tmp);
        return report("tmpfs-write-allowed-without-contract\n");
    }
    if (errno != EACCES && errno != EPERM) return report("tmpfs-write-wrong-error\n");
    if (report("tmpfs-write-denied\n") != 0) return 1;

    int network_fd = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (network_fd < 0) return report("network-socket-unavailable\n");
    struct sockaddr_in remote = {.sin_family = AF_INET, .sin_port = htons(9)};
    remote.sin_addr.s_addr = htonl(0xcb007109U); /* TEST-NET-3 */
    errno = 0;
    int connected = connect(network_fd, (struct sockaddr *)&remote, sizeof(remote));
    int network_errno = errno;
    close(network_fd);
    if (connected == 0) return report("network-connect-escaped\n");
    if (network_errno != EPERM) return report("network-connect-wrong-error\n");
    if (report("network-connect-denied\n") != 0) return 1;

    int segment = shmget((key_t)T097_SHM_KEY, 4096, IPC_CREAT | IPC_EXCL | 0600);
    if (segment < 0) {
        char message[64];
        int length = snprintf(message, sizeof(message), "private-sysv-shm-error-%d\n", errno);
        if (length <= 0 || (size_t)length >= sizeof(message)) return 1;
        return report(message);
    }
    if (report("private-sysv-shm-created\n") != 0) return 1;

    if (getenv("T097_SECRET") != NULL) return report("secret-environment-leaked\n");
    if (report("secret-environment-hidden\n") != 0) return 1;

    errno = 0;
    DIR *fd_directory = opendir("/proc/self/fd");
    if (fd_directory != NULL) {
        closedir(fd_directory);
        return report("proc-fd-visible-without-contract\n");
    }
    if (errno != EACCES && errno != EPERM) return report("proc-fd-wrong-error\n");
    if (report("proc-fd-path-denied\n") != 0) return 1;

    if (access("/dev/fd/0", F_OK) != 0) return report("dev-fd-stdin-alias-unavailable\n");
    return report("dev-fd-stdin-alias-available\n");
}
