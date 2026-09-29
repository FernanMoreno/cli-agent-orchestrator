#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#define INPUT_LIMIT 32768U
#define REQUEST_LIMIT 16384U

static int read_input(char *buffer, size_t capacity, size_t *used) {
    *used = 0U;
    while (*used < capacity) {
        ssize_t count = read(STDIN_FILENO, buffer + *used, capacity - *used);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) return -1;
        if (count == 0) return 0;
        *used += (size_t)count;
    }
    return -1;
}

static int request_child(char *arguments) {
    char request[REQUEST_LIMIT + 1U];
    int request_length = snprintf(
        request,
        sizeof(request),
        "{\"jsonrpc\":\"2.0\",\"id\":\"docker-child\","
        "\"method\":\"tools/call\",\"params\":{\"name\":\"cao.work.child\","
        "\"arguments\":%s}}\n",
        arguments
    );
    if (request_length <= 0 || (size_t)request_length >= sizeof(request)) return 20;
    if (write(3, request, (size_t)request_length) != request_length) return 21;

    char response[8192];
    size_t used = 0U;
    while (used < sizeof(response)) {
        ssize_t count = read(3, response + used, sizeof(response) - used);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return 22;
        for (ssize_t index = 0; index < count; ++index) {
            if (response[used + (size_t)index] == '\n') {
                used += (size_t)index + 1U;
                return write(STDOUT_FILENO, response, used) == (ssize_t)used ? 0 : 23;
            }
        }
        used += (size_t)count;
    }
    return 24;
}

int main(void) {
    char input[INPUT_LIMIT + 1U];
    size_t input_size = 0U;
    if (read_input(input, INPUT_LIMIT, &input_size) != 0) return 10;
    input[input_size] = '\0';
    char *arguments = strstr(input, "\n\n");
    if (arguments == NULL) return 11;
    arguments += 2;
    if (strncmp(arguments, "MCPARGS:", 8U) != 0) return 12;
    arguments += 8;
    if (*arguments != '{' || strlen(arguments) == 0U) return 13;
    return request_child(arguments);
}
