#include <errno.h>
#include <unistd.h>

int main(void) {
    static const char request[] =
        "{\"jsonrpc\":\"2.0\",\"id\":\"docker-ack\",\"method\":\"tools/call\","
        "\"params\":{\"name\":\"cao.work.task_received\",\"arguments\":{}}}\n";
    if (write(3, request, sizeof(request) - 1U) != (ssize_t)(sizeof(request) - 1U)) return 10;
    char response[8192];
    size_t used = 0U;
    while (used < sizeof(response)) {
        ssize_t count = read(3, response + used, sizeof(response) - used);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return 11;
        for (ssize_t index = 0; index < count; ++index) {
            if (response[used + (size_t)index] == '\n') {
                used += (size_t)index + 1U;
                return write(STDOUT_FILENO, response, used) == (ssize_t)used ? 0 : 12;
            }
        }
        used += (size_t)count;
    }
    return 13;
}
