/* A failed string allocation must not disclose an SRT passphrase. */
#include <assert.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <libavutil/error.h>
#include <libavutil/mem.h>
#include <libavutil/opt.h>

typedef struct { const AVClass *class; char *passphrase; } Context;
static char captured[1024];

static void capture(void *context, int level, const char *format, va_list args)
{
    (void)context; (void)level;
    size_t used = strlen(captured);
    vsnprintf(captured + used, sizeof(captured) - used, format, args);
}

int main(void)
{
    static const AVOption options[] = {
        {"passphrase", NULL, offsetof(Context, passphrase), AV_OPT_TYPE_STRING}, {NULL}
    };
    static const AVClass class = {.class_name="secret-log-test", .item_name=av_default_item_name,
                                  .option=options, .version=LIBAVUTIL_VERSION_INT};
    Context context = { .class=&class };
    AVDictionary *dictionary = NULL;
    const char *sentinel = "ephemeral-test-sentinel";
    assert(av_dict_set(&dictionary, "passphrase", sentinel, 0) == 0);
    av_log_set_callback(capture);
    av_max_alloc(1);
    assert(av_opt_set_dict2(&context, &dictionary, 0) == AVERROR(ENOMEM));
    av_max_alloc(SIZE_MAX);
    assert(strstr(captured, "[redacted]") && !strstr(captured, sentinel));
    av_dict_free(&dictionary);
    av_opt_free(&context);
    puts("PASS FFmpeg: allocation failure redacts the SRT passphrase");
    return 0;
}
