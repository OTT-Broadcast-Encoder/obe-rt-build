/* Exercise the actual OBE encoder entry points with synthetic capture frames.
 * Only queue transport and allocation are supplied by this harness. */
#include <assert.h>
#include <math.h>
#include "common/common.h"
#include "encoders/audio/audio.h"

static obe_encoder_t *active_encoder;
static FILE *encoded_output;
static int output_count;
static int64_t previous_pts, expected_step, expected_first_pts;

obe_coded_frame_t *new_coded_frame(int stream_id, int len)
{
    assert(stream_id == 2 && len > 0);
    obe_coded_frame_t *frame = calloc(1, sizeof(*frame));
    assert(frame);
    frame->data = malloc(len);
    assert(frame->data);
    frame->len = len;
    return frame;
}

int add_to_queue(obe_queue_t *queue, void *item)
{
    (void)queue;
    obe_coded_frame_t *frame = item;
    assert(frame->len > 0 && frame->random_access);
    if (output_count)
        assert(frame->pts - previous_pts == expected_step);
    else if (expected_first_pts)
        assert(frame->pts == expected_first_pts);
    previous_pts = frame->pts;
    output_count++;
    assert(fwrite(frame->data, 1, frame->len, encoded_output) == (size_t)frame->len);
    free(frame->data);
    free(frame);
    return 0;
}

int remove_from_queue(obe_queue_t *queue)
{
    assert(queue == &active_encoder->queue && queue->size > 0);
    --queue->size;
    memmove(queue->queue, queue->queue + 1, queue->size * sizeof(void *));
    if (!queue->size)
        active_encoder->cancel_thread = 1;
    return 0;
}

static void release_audio(void *ptr)
{
    obe_raw_frame_t *frame = ptr;
    free(frame->audio_frame.audio_data[0]);
}

static void encode_case(const char *name, int format, uint64_t layout, int frames_per_pes, int aac_profile, int url_input)
{
    const int chunk_sizes[] = {480, 960, 1024, 1920};
    const int url_chunk_sizes[] = {2048, 4096, 1024, 1152};
    const int num_chunks = 160;
    int channels = av_popcount64(layout);
    int samples_per_frame = format == AUDIO_MP2 ? 1152 : format == AUDIO_AAC ?
        (aac_profile == AAC_LC ? 1024 : 2048) : 1536;
    obe_t h = {0};
    obe_device_t device = {0};
    device.device_type = url_input ? INPUT_URL : INPUT_DEVICE_DECKLINK;
    h.devices[0] = &device;
    h.num_devices = 1;
    obe_encoder_t encoder = {0};
    obe_output_stream_t stream = {0};
    obe_aud_enc_params_t *params = aud_enc_params_alloc();
    assert(params);
    h.obe_system = OBE_SYSTEM_TYPE_GENERIC;
    encoder.output_stream_id = 2;
    pthread_mutex_init(&encoder.queue.mutex, NULL);
    pthread_cond_init(&encoder.queue.in_cv, NULL);
    encoder.queue.queue = calloc(num_chunks, sizeof(void *));
    encoder.queue.size = num_chunks;
    assert(encoder.queue.queue);
    stream.stream_format = format;
    stream.channel_layout = layout;
    stream.bitrate = channels > 2 ? 384 : channels == 2 ? 192 : 96;
    if (format == AUDIO_AAC && aac_profile != AAC_LC)
        stream.bitrate = aac_profile == AAC_HE_V2 ? 48 : 64;
    stream.mp2_mode = 1;
    stream.audio_offset_ms = 7;
    stream.aac_opts.aac_profile = aac_profile;
    params->h = &h;
    params->encoder = &encoder;
    params->stream = &stream;
    params->sample_rate = 48000;
    params->input_sample_format = AV_SAMPLE_FMT_S32;
    params->frames_per_pes = frames_per_pes;

    int64_t total_samples = 0;
    for (int n = 0; n < num_chunks; ++n) {
        int samples = url_input ? url_chunk_sizes[n % 4] : chunk_sizes[n % 4];
        obe_raw_frame_t *frame = calloc(1, sizeof(*frame));
        assert(frame);
        frame->release_data = release_audio;
        frame->release_frame = free;
        frame->audio_frame.channel_layout = layout;
        frame->audio_frame.num_samples = samples;
        frame->audio_frame.sample_fmt = AV_SAMPLE_FMT_S32;
        frame->audio_frame.linesize = samples * channels * sizeof(int32_t);
        int32_t *pcm = malloc(frame->audio_frame.linesize);
        assert(pcm);
        for (int s = 0; s < samples; ++s)
            for (int c = 0; c < channels; ++c)
                pcm[s * channels + c] = sin(2.0 * M_PI * (220 + 110 * c) * (total_samples + s) / 48000) * 536870912;
        frame->audio_frame.audio_data[0] = (uint8_t *)pcm;
        frame->avfm.audio_pts = OBE_CLOCK + total_samples * OBE_CLOCK / 48000;
        frame->avfm.audio_pts_corrected = frame->avfm.audio_pts;
        frame->pts = frame->avfm.audio_pts;
        encoder.queue.queue[n] = frame;
        total_samples += samples;
    }
    char path[256];
    snprintf(path, sizeof(path), "tests/.build/%s", name);
    encoded_output = fopen(path, "wb");
    assert(encoded_output);
    active_encoder = &encoder;
    output_count = 0;
    expected_first_pts = url_input ? OBE_CLOCK + 7 * 27000 : 0;
    expected_step = (int64_t)samples_per_frame * OBE_CLOCK * frames_per_pes / 48000;
    const obe_aud_enc_func_t *func = format == AUDIO_MP2 ? &twolame_encoder : &lavc_encoder;
    func->start_encoder(params);
    assert(encoder.queue.size == 0);
    assert(output_count >= total_samples / samples_per_frame / frames_per_pes - 3);
    fclose(encoded_output);
    free(encoder.queue.queue);
    pthread_mutex_destroy(&encoder.queue.mutex);
    pthread_cond_destroy(&encoder.queue.in_cv);
    printf("PASS %s: %d PES groups, PTS step %" PRIi64 "\n", name, output_count, expected_step);
}

int main(void)
{
    setvbuf(stdout, NULL, _IOLBF, 0);
    encode_case("mono.aac", AUDIO_AAC, AV_CH_LAYOUT_MONO, 1, AAC_LC, 0);
    encode_case("stereo.aac", AUDIO_AAC, AV_CH_LAYOUT_STEREO, 6, AAC_LC, 0);
    encode_case("surround.aac", AUDIO_AAC, AV_CH_LAYOUT_5POINT1_BACK, 6, AAC_LC, 0);
    encode_case("stereo-hev1.aac", AUDIO_AAC, AV_CH_LAYOUT_STEREO, 6, AAC_HE_V1, 0);
    encode_case("stereo-hev2.aac", AUDIO_AAC, AV_CH_LAYOUT_STEREO, 6, AAC_HE_V2, 0);
    encode_case("stereo.ac3", AUDIO_AC_3, AV_CH_LAYOUT_STEREO, 2, AAC_LC, 0);
    encode_case("surround.eac3", AUDIO_E_AC_3, AV_CH_LAYOUT_5POINT1_BACK, 2, AAC_LC, 0);
    encode_case("stereo.mp2", AUDIO_MP2, AV_CH_LAYOUT_STEREO, 2, AAC_LC, 0);
    encode_case("url-stereo.aac", AUDIO_AAC, AV_CH_LAYOUT_STEREO, 6, AAC_LC, 1);
    encode_case("url-stereo.ac3", AUDIO_AC_3, AV_CH_LAYOUT_STEREO, 2, AAC_LC, 1);
    return 0;
}
