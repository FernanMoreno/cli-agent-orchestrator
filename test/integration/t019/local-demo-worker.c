#include <errno.h>
#include <unistd.h>

int main(void) {
    char input[4096];
    for (;;) {
        ssize_t count = read(STDIN_FILENO, input, sizeof(input));
        if (count > 0) continue;
        if (count == 0) break;
        if (errno == EINTR) continue;
        return 1;
    }

    static const char marker[] = "CAO_LOCAL_DOCKER_WORKER_RAN\n";
    size_t offset = 0;
    while (offset < sizeof(marker) - 1) {
        ssize_t count = write(STDOUT_FILENO, marker + offset, sizeof(marker) - 1 - offset);
        if (count > 0) {
            offset += (size_t)count;
            continue;
        }
        if (count < 0 && errno == EINTR) continue;
        return 1;
    }
    return 0;
}
