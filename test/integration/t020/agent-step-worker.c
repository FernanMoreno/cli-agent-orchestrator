#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define INPUT_LIMIT 32768U
#define MCP_LINE_LIMIT 65536U

static int write_all(int descriptor, const char *buffer, size_t size) {
    size_t offset = 0U;
    while (offset < size) {
        ssize_t written = write(descriptor, buffer + offset, size - offset);
        if (written < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        if (written == 0) return -1;
        offset += (size_t)written;
    }
    return 0;
}

static int read_mcp_line(int descriptor, char *buffer, size_t capacity) {
    size_t used = 0U;
    while (used + 1U < capacity) {
        char value;
        ssize_t count = read(descriptor, &value, 1U);
        if (count < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        if (count == 0) return -1;
        if (value == '\n') {
            buffer[used] = '\0';
            return 0;
        }
        buffer[used++] = value;
    }
    return -1;
}

static int mcp_call(int descriptor, const char *request, const char *request_id) {
    char response[MCP_LINE_LIMIT + 1U];
    size_t request_size = strlen(request);
    if (write_all(descriptor, request, request_size) != 0 ||
        read_mcp_line(descriptor, response, sizeof(response)) != 0) {
        return -1;
    }
    response[sizeof(response) - 1U] = '\0';

    char expected_id[64];
    int length = snprintf(expected_id, sizeof(expected_id), "\"id\":\"%s\"", request_id);
    if (length <= 0 || (size_t)length >= sizeof(expected_id) ||
        strstr(response, "\"jsonrpc\":\"2.0\"") == NULL ||
        strstr(response, expected_id) == NULL || strstr(response, "\"result\"") == NULL ||
        strstr(response, "\"error\"") != NULL) {
        return -1;
    }
    return 0;
}

static int contains_no_result_marker(const unsigned char *input, size_t input_size) {
    static const char marker[] = "T122_OMIT_RESULT";
    const size_t marker_size = sizeof(marker) - 1U;
    if (input_size < marker_size) return 0;
    for (size_t offset = 0U; offset <= input_size - marker_size; ++offset) {
        if (memcmp(input + offset, marker, marker_size) == 0) return 1;
    }
    return 0;
}

static int contains_failure_marker(const unsigned char *input, size_t input_size) {
    static const char marker[] = "T122_FAIL";
    const size_t marker_size = sizeof(marker) - 1U;
    if (input_size < marker_size) return 0;
    for (size_t offset = 0U; offset <= input_size - marker_size; ++offset) {
        if (memcmp(input + offset, marker, marker_size) == 0) return 1;
    }
    return 0;
}

static int work_mcp_fd(void) {
    const char *value = getenv("CAO_WORK_MCP_FD");
    if (value == NULL || value[0] < '0' || value[0] > '9') return -1;
    char *end = NULL;
    errno = 0;
    long descriptor = strtol(value, &end, 10);
    if (errno != 0 || end == value || *end != '\0' || descriptor < 3 || descriptor > INT_MAX) {
        return -1;
    }
    return (int)descriptor;
}

int main(void) {
    unsigned char input[INPUT_LIMIT];
    size_t input_size = 0U;
    for (;;) {
        ssize_t count = read(STDIN_FILENO, input + input_size, sizeof(input) - input_size);
        if (count == 0) break;
        if (count < 0) {
            if (errno == EINTR) continue;
            return 1;
        }
        input_size += (size_t)count;
        if (input_size == sizeof(input)) {
            unsigned char extra;
            ssize_t overflow = read(STDIN_FILENO, &extra, 1U);
            if (overflow != 0) return 1;
            break;
        }
    }

    int descriptor = work_mcp_fd();
    if (descriptor < 0) return 1;
    static const char receipt_request[] =
        "{\"jsonrpc\":\"2.0\",\"id\":\"t122-receipt\",\"method\":\"tools/call\","
        "\"params\":{\"name\":\"cao.work.task_received\",\"arguments\":{}}}\n";
    if (mcp_call(descriptor, receipt_request, "t122-receipt") != 0) return 1;

    if (contains_no_result_marker(input, input_size)) {
        static const char marker[] = "T122_AGENT_STEP_EXITED_WITHOUT_RESULT\n";
        return write_all(STDOUT_FILENO, marker, sizeof(marker) - 1U) == 0 ? 0 : 1;
    }

    if (contains_failure_marker(input, input_size)) {
        static const char marker[] = "T122_AGENT_STEP_FAILED\n";
        if (write_all(STDOUT_FILENO, marker, sizeof(marker) - 1U) != 0) return 1;
        return 23;
    }

    static const char result_request[] =
        "{\"jsonrpc\":\"2.0\",\"id\":\"t122-result\",\"method\":\"tools/call\","
        "\"params\":{\"name\":\"cao.work.submit_result\",\"arguments\":{"
        "\"schema_version\":1,\"status\":\"completed\","
        "\"output\":{\"value\":\"t122-deterministic\"}}}}\n";
    if (mcp_call(descriptor, result_request, "t122-result") != 0) return 1;
    static const char marker[] = "T122_AGENT_STEP_COMPLETED\n";
    return write_all(STDOUT_FILENO, marker, sizeof(marker) - 1U) == 0 ? 0 : 1;
}
