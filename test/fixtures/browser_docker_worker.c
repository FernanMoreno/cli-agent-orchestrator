/* Deterministic no-provider worker; logout happens while this real ELF sleeps. */
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
    sleep(8);
    static const char marker[] = "BROWSER_DOCKER_WORKER_COMPLETED\n";
    return write(STDOUT_FILENO, marker, sizeof(marker)-1U) == (ssize_t)(sizeof(marker)-1U) ? 0 : 2;
}
