#include <stddef.h>
#include <unistd.h>

int main(void) {
    static const char request[] =
        "{\"jsonrpc\":\"2.0\",\"id\":\"docker-mcp\",\"method\":\"tools/call\","
        "\"params\":{\"name\":\"cao.work.child\",\"arguments\":{}}}\n";
    if (write(3, request, sizeof(request) - 1U) != (ssize_t)(sizeof(request) - 1U)) return 10;
    char response[8192];
    size_t used = 0U;
    while (used < sizeof(response)) {
        ssize_t count = read(3, response + used, sizeof(response) - used);
        if (count <= 0) return 11;
        for (ssize_t index = 0; index < count; ++index) {
            if (response[used + (size_t)index] == '\n') {
                used += (size_t)index + 1U;
                if (write(STDOUT_FILENO, response, used) != (ssize_t)used) return 12;
                return 0;
            }
        }
        used += (size_t)count;
    }
    return 13;
}
