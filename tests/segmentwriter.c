#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <libltntstools/segmentwriter.h>

static void check_writer(const char *directory, int mode)
{
    char target[256], filename[512] = {0};
    snprintf(target, sizeof(target), "%s/%s", directory, mode ? "segments@" : "literal.ts");
    void *writer = NULL;
    assert(ltntstools_segmentwriter_alloc(&writer, target, mode ? ".ts" : "", mode) == 0);
    uint8_t data[188 * 7], result[sizeof(data)];
    for (size_t i = 0; i < sizeof(data); i++) data[i] = i % 188 ? i % 251 : 0x47;
    assert(ltntstools_segmentwriter_write(writer, data, sizeof(data)) == sizeof(data));
    for (int retry = 0; retry < 500 && ltntstools_segmentwriter_get_recording_size(writer) < (int64_t)sizeof(data); retry++)
        usleep(10000);
    assert(ltntstools_segmentwriter_get_recording_size(writer) == sizeof(data));
    assert(ltntstools_segmentwriter_get_current_filename(writer, filename, sizeof(filename)) == 0);
    if (mode) {
        assert(strncmp(filename, target, strlen(target)) == 0);
        assert(strcmp(filename + strlen(filename) - 3, ".ts") == 0);
        assert(strlen(filename) > strlen(target) + 3);
    } else {
        assert(strcmp(filename, target) == 0);
    }
    ltntstools_segmentwriter_free(writer);
    FILE *file = fopen(filename, "rb");
    assert(file);
    assert(fread(result, 1, sizeof(result), file) == sizeof(result));
    assert(fgetc(file) == EOF);
    assert(memcmp(result, data, sizeof(data)) == 0);
    fclose(file);
}

int main(void)
{
    char directory[] = "tests/.build/writer.XXXXXX";
    assert(mkdtemp(directory));
    check_writer(directory, SEGMENTEDWRITER_SINGLE_FILE);
    check_writer(directory, SEGMENTEDWRITER_SEGMENTED);
    puts("PASS file output: literal target, segmented filename and TS bytes");
    return 0;
}
