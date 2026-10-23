/* MPEG-TS SRT sink. Credentials arrive only via the environment, never argv.
 * stdin: uint16 network-order length followed by one TS datagram (<= 1316).
 * stdout: bounded JSON counters. No raw URLs, keys or library errors are logged.
 */
#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <errno.h>
#include <inttypes.h>
#include <netdb.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#ifdef __linux__
#include <sys/prctl.h>
#endif
#include <srt/srt.h>

static uint64_t sent_bytes, sent_packets;

static int option(SRTSOCKET fd, SRT_SOCKOPT name, int value) {
    return srt_setsockflag(fd, name, &value, sizeof(value));
}

static int option64(SRTSOCKET fd, SRT_SOCKOPT name, int64_t value) {
    return srt_setsockflag(fd, name, &value, sizeof(value));
}

static int parse_long(const char *text, long *result) {
    char *end = NULL;
    errno = 0;
    long value = strtol(text, &end, 10);
    if (errno || end == text || *end) return -1;
    *result = value;
    return 0;
}

static int read_all(void *buffer, size_t length) {
    char *cursor = buffer;
    while (length) {
        ssize_t got = read(STDIN_FILENO, cursor, length);
        if (got < 0 && errno == EINTR) continue;
        if (got <= 0) return -1;
        cursor += got;
        length -= (size_t)got;
    }
    return 0;
}

static int metrics(SRTSOCKET fd) {
    SRT_TRACEBSTATS stats;
    memset(&stats, 0, sizeof(stats));
    if (srt_bstats(fd, &stats, 0) < 0) return -1;
    printf("{\"status\":\"live\",\"sent_bytes\":%" PRIu64 ",\"sent_packets\":%" PRIu64
           ",\"rtt_ms\":%.3f,\"lost_packets\":%d,\"retransmitted_packets\":%d,\"srt_dropped_packets\":%d}\n",
           sent_bytes, sent_packets, stats.msRTT, stats.pktSndLossTotal,
           stats.pktRetransTotal, stats.pktSndDropTotal);
    return fflush(stdout);
}

int main(int argc, char **argv) {
#ifdef __linux__
    /* A broker crash must also release helpers blocked in srt_accept/connect. */
    pid_t parent = getppid();
    const char *expected_parent = getenv("OBE_OUTPUT_PARENT_PID");
    char parent_text[32];
    snprintf(parent_text, sizeof(parent_text), "%ld", (long)parent);
    if ((expected_parent && strcmp(expected_parent, parent_text))
            || prctl(PR_SET_PDEATHSIG, SIGTERM) < 0 || getppid() != parent) return 3;
    unsetenv("OBE_OUTPUT_PARENT_PID");
#endif
    /* Keep the original invocation usable during an atomic helper upgrade. */
    if (argc != 5 && argc != 10) return 2;
    long latency, key_length = OBE_SRT_KEY_LENGTH_DEFAULT;
    long bandwidth = OBE_SRT_MAX_BANDWIDTH_KBPS_DEFAULT;
    long connect_timeout = OBE_SRT_CONNECT_TIMEOUT_MS_DEFAULT;
    long send_timeout = OBE_SRT_SEND_TIMEOUT_MS_DEFAULT, drop_late = OBE_SRT_DROP_LATE_PACKETS_DEFAULT;
    if (parse_long(argv[4], &latency) < 0 || !OBE_LATENCY_MS_VALID(latency)) return 2;
    if (argc == 10 && (parse_long(argv[5], &key_length) < 0 || !OBE_SRT_KEY_LENGTH_VALID(key_length)
            || parse_long(argv[6], &bandwidth) < 0 || !OBE_SRT_MAX_BANDWIDTH_KBPS_VALID(bandwidth)
            || parse_long(argv[7], &connect_timeout) < 0 || !OBE_SRT_CONNECT_TIMEOUT_MS_VALID(connect_timeout)
            || parse_long(argv[8], &send_timeout) < 0 || !OBE_SRT_SEND_TIMEOUT_MS_VALID(send_timeout)
            || parse_long(argv[9], &drop_late) < 0 || !OBE_SRT_DROP_LATE_PACKETS_VALID(drop_late))) return 2;
    int listener = !strcmp(argv[3], "listener");
    if (!listener && strcmp(argv[3], "caller")) return 2;
    signal(SIGPIPE, SIG_IGN);
    if (srt_startup() < 0) return 3;
    srt_setloglevel(0);
    SRTSOCKET fd = srt_create_socket(), accepted = SRT_INVALID_SOCK;
    int result = 3;
    struct addrinfo hints, *address = NULL;
    memset(&hints, 0, sizeof(hints));
    hints.ai_socktype = SOCK_DGRAM;
    hints.ai_family = AF_UNSPEC;
    hints.ai_flags = AI_NUMERICSERV;
    if (fd == SRT_INVALID_SOCK || getaddrinfo(argv[1], argv[2], &hints, &address)) goto done;
    if (option(fd, SRTO_TRANSTYPE, SRTT_LIVE) < 0 || option(fd, SRTO_SENDER, 1) < 0
            || option(fd, SRTO_CONNTIMEO, (int)connect_timeout) < 0
            || option(fd, SRTO_SNDTIMEO, (int)send_timeout) < 0
            || option(fd, SRTO_TLPKTDROP, (int)drop_late) < 0
            || option(fd, SRTO_PEERLATENCY, (int)latency) < 0
            || option(fd, SRTO_SNDBUF, 1024 * 1024) < 0
            || (bandwidth && option64(fd, SRTO_MAXBW, (int64_t)bandwidth * 125) < 0))
        goto done;
    char *key = getenv("OBE_OUTPUT_SRT_PASSPHRASE");
    if (key && *key) {
        size_t length = strlen(key);
        if (length < 10 || length > 79 || option(fd, SRTO_PBKEYLEN, (int)key_length) < 0
                || srt_setsockflag(fd, SRTO_PASSPHRASE, key, (int)length) < 0) goto done;
        /* Remove the original environment bytes as soon as libsrt copied them. */
        memset(key, 0, length);
    }
    unsetenv("OBE_OUTPUT_SRT_PASSPHRASE");
    if (listener) {
        if (srt_bind(fd, address->ai_addr, (int)address->ai_addrlen) < 0 || srt_listen(fd, 1) < 0) goto done;
        accepted = srt_accept(fd, NULL, NULL);
        if (accepted == SRT_INVALID_SOCK) goto done;
        srt_close(fd);
        fd = accepted;
    } else if (srt_connect(fd, address->ai_addr, (int)address->ai_addrlen) < 0) goto done;
    if (metrics(fd) < 0) goto done;
    struct timespec previous;
    clock_gettime(CLOCK_MONOTONIC, &previous);
    for (;;) {
        struct pollfd input = { .fd = STDIN_FILENO, .events = POLLIN };
        int ready = poll(&input, 1, 200);
        if (ready < 0 && errno == EINTR) continue;
        if (ready < 0 || srt_getsockstate(fd) != SRTS_CONNECTED) goto done;
        if (ready) {
            uint16_t network_length;
            if (read_all(&network_length, sizeof(network_length)) < 0) { result = 0; break; }
            size_t length = ntohs(network_length);
            char data[1316];
            if (!length || length > sizeof(data) || length % 188 || read_all(data, length) < 0) goto done;
            for (size_t i = 0; i < length; i += 188) if ((unsigned char)data[i] != 0x47) goto done;
            if (srt_sendmsg(fd, data, (int)length, -1, 1) != (int)length) goto done;
            sent_bytes += length;
            ++sent_packets;
        }
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        if ((now.tv_sec - previous.tv_sec) * 1000000000LL + now.tv_nsec - previous.tv_nsec >= 200000000LL) {
            if (metrics(fd) < 0) goto done;
            previous = now;
        }
    }
done:
    if (address) freeaddrinfo(address);
    if (fd != SRT_INVALID_SOCK) srt_close(fd);
    srt_cleanup();
    return result;
}
