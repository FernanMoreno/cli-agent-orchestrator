#include <errno.h>
#include <stddef.h>
#include <unistd.h>

static int write_all(int fd, const char *buffer, size_t size) {
    while (size > 0) {
        ssize_t written = write(fd, buffer, size);
        if (written < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        buffer += written;
        size -= (size_t)written;
    }
    return 0;
}

int main(void) {
    static const char request[] =
        "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\","
        "\"params\":{\"name\":\"test.echo\",\"arguments\":{}}}\n";
    char response[65536];
    size_t used = 0;

    if (write_all(STDERR_FILENO, "worker-started\n", sizeof("worker-started\n") - 1) < 0) {
        return 7;
    }
    if (write_all(3, request, sizeof(request) - 1) < 0) return 2;
    if (write_all(STDERR_FILENO, "proxy-request-sent\n", sizeof("proxy-request-sent\n") - 1) < 0) {
        return 8;
    }
    while (used < sizeof(response)) {
        ssize_t amount = read(3, response + used, sizeof(response) - used);
        if (amount < 0) {
            if (errno == EINTR) continue;
            return 3;
        }
        if (amount == 0) return 4;
        used += (size_t)amount;
        for (size_t index = 0; index < used; ++index) {
            if (response[index] == '\n') {
                return write_all(STDOUT_FILENO, response, index + 1) < 0 ? 5 : 0;
            }
        }
    }
    return 6;
}
