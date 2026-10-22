#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#include <libavutil/fifo.h>
#include <libyuv.h>
#include <x264.h>

static void test_fifo(void)
{
    const size_t count = 3 * 1024 * 1024;
    uint8_t *data = malloc(count), *out = malloc(count);
    assert(data && out);
    for (size_t i = 0; i < count; i++)
        data[i] = i % 251;
    AVFifo *fifo = av_fifo_alloc2(188, 1, AV_FIFO_FLAG_AUTO_GROW);
    assert(fifo);
    av_fifo_auto_grow_limit(fifo, INT_MAX);
    assert(av_fifo_write(fifo, data, 187) == 0);
    assert(av_fifo_read(fifo, out, 100) == 0);
    assert(memcmp(data, out, 100) == 0);
    assert(av_fifo_write(fifo, data + 187, count - 187) == 0);
    assert(av_fifo_can_read(fifo) == count - 100);
    assert(av_fifo_read(fifo, out, count - 100) == 0);
    assert(memcmp(data + 100, out, count - 100) == 0);
    assert(av_fifo_write(fifo, data, count) == 0);
    av_fifo_reset2(fifo);
    assert(av_fifo_can_read(fifo) == 0);
    assert(av_fifo_write(fifo, data + 17, 188) == 0);
    assert(av_fifo_read(fifo, out, 188) == 0);
    assert(memcmp(out, data + 17, 188) == 0);
    av_fifo_freep2(&fifo);
    free(out);
    free(data);
    puts("PASS FIFO: wrap, growth beyond 1 MiB, reset and byte order");
}

static void test_nv12(void)
{
    enum { W = 18, H = 10, YS = 32, CS = 16, DS = 32 };
    uint8_t y[YS * H], u[CS * H / 2], v[CS * H / 2];
    uint8_t dst_y[DS * H + 32], dst_uv[DS * H / 2 + 32];
    memset(y, 0, sizeof(y));
    memset(u, 0, sizeof(u));
    memset(v, 0, sizeof(v));
    memset(dst_y, 0xcd, sizeof(dst_y));
    memset(dst_uv, 0xcd, sizeof(dst_uv));
    for (int r = 0; r < H; r++)
        for (int c = 0; c < W; c++) y[r * YS + c] = r + c;
    for (int r = 0; r < H / 2; r++)
        for (int c = 0; c < W / 2; c++) {
            u[r * CS + c] = 40 + r + c;
            v[r * CS + c] = 150 + r + c;
        }
    assert(I420ToNV12(y, YS, u, CS, v, CS, dst_y + 16, DS,
                      dst_uv + 16, DS, W, H) == 0);
    for (int r = 0; r < H; r++)
        for (int c = 0; c < DS; c++)
            assert(dst_y[16 + r * DS + c] == (c < W ? y[r * YS + c] : 0xcd));
    for (int r = 0; r < H / 2; r++)
        for (int c = 0; c < DS; c++) {
            int expected = c >= W ? 0xcd : c % 2 ? v[r * CS + c / 2] : u[r * CS + c / 2];
            assert(dst_uv[16 + r * DS + c] == expected);
        }
    for (int i = 0; i < 16; i++) {
        assert(dst_y[i] == 0xcd && dst_y[DS * H + 16 + i] == 0xcd);
        assert(dst_uv[i] == 0xcd && dst_uv[DS * H / 2 + 16 + i] == 0xcd);
    }
    puts("PASS libyuv: NV12 chroma, padded strides and buffer guards");
}

static void test_x264(void)
{
    x264_param_t param;
    assert(x264_param_default_preset(&param, "ultrafast", "zerolatency") == 0);
    param.i_width = 64;
    param.i_height = 32;
    param.i_csp = X264_CSP_I420;
    param.i_fps_num = 25;
    param.i_fps_den = 1;
    param.i_threads = 1;
    param.sc.i_buffer_size = 12;
    param.sc.f_speed = 1;
    x264_t *encoder = x264_encoder_open(&param);
    assert(encoder);
    x264_picture_t in, out;
    assert(x264_picture_alloc(&in, X264_CSP_I420, 64, 32) == 0);
    memset(in.img.plane[0], 64, 64 * 32);
    memset(in.img.plane[1], 100, 32 * 16);
    memset(in.img.plane[2], 150, 32 * 16);
    int bytes = 0;
    for (int i = 0; i < 4; i++) {
        x264_nal_t *nals;
        int count;
        in.i_pts = i;
        x264_speedcontrol_sync(encoder, 0.75, 12, 0);
        int result = x264_encoder_encode(encoder, &nals, &count, &in, &out);
        assert(result > 0 && count > 0);
        bytes += result;
    }
    assert(bytes > 0);
    x264_picture_clean(&in);
    x264_encoder_close(encoder);
    x264_param_cleanup(&param);
    puts("PASS x264: software encode with OBE speed control");
}

int main(void)
{
    test_fifo();
    test_nv12();
    test_x264();
    return 0;
}
