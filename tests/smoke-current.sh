#!/bin/bash
set -euo pipefail
ulimit -c 0
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
PREFIX=$PWD/target-root/usr/local
PYTHON_SITE=$PREFIX/lib/python3.12/site-packages
export PATH="$PREFIX/bin:$PYTHON_SITE/bin:$PATH"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PYTHON_SITE/vapoursynth/pkgconfig"
export PYTHONPATH="$PYTHON_SITE"
export LD_LIBRARY_PATH="$PREFIX/lib:$PYTHON_SITE/vapoursynth:$PWD/NDI/sdk/lib/x86_64-linux-gnu"
mkdir -p tests/.build

cc -g -fsanitize=address,undefined obe-rt/tests/metadata-allocation.c \
    -o tests/.build/metadata-allocation
tests/.build/metadata-allocation

cc -g -fsanitize=address,undefined -Iobe-rt $(pkg-config --cflags libavutil) \
    obe-rt/tests/srt-options.c -o tests/.build/srt-options $(pkg-config --libs --static libavutil)
tests/.build/srt-options
cc -g $(pkg-config --cflags libavutil) tests/ffmpeg-secret-log.c \
    -o tests/.build/ffmpeg-secret-log $(pkg-config --libs --static libavutil)
tests/.build/ffmpeg-secret-log

cc -O2 -g -Iobe-rt -Iobe-rt/obe $(pkg-config --cflags libavcodec) \
    tests/audio-encoders.c obe-rt/encoders/audio/lavc/lavc.c \
    obe-rt/encoders/audio/mp2/twolame.c obe-rt/encoders/audio/audio_enc_params.c \
    -o tests/.build/audio-encoders $(pkg-config --libs --static libavcodec libswresample twolame) -lpthread
tests/.build/audio-encoders

for name in mono.aac stereo.aac surround.aac stereo-hev1.aac stereo-hev2.aac stereo.ac3 surround.eac3 stereo.mp2 url-stereo.aac url-stereo.ac3; do
    ffmpeg -v error -y -i "tests/.build/$name" -f f32le "tests/.build/$name.f32"
done
python3 - <<'PY'
from array import array
from pathlib import Path
import math
for name in ('mono.aac', 'stereo.aac', 'surround.aac', 'stereo-hev1.aac', 'stereo-hev2.aac', 'stereo.ac3', 'surround.eac3', 'stereo.mp2', 'url-stereo.aac', 'url-stereo.ac3'):
    samples = array('f')
    samples.frombytes(Path(f'tests/.build/{name}.f32').read_bytes())
    assert len(samples) > 10000, name
    assert all(math.isfinite(x) for x in samples), name
    # The generated signal has amplitude 0.25. Check the beginning too: a
    # resampler reset must not discard the first second or encode stale data.
    channels = 1 if name.startswith('mono') else 6 if name.startswith('surround') else 2
    initial = samples[:8192 * channels]
    rms = math.sqrt(sum(x*x for x in initial) / len(initial))
    assert 0.05 < rms < 0.3, (name, rms)
    print(f'PASS decoded {name}: {len(samples)} samples, initial RMS {rms:.3f}')
PY

cc -O2 -g -fsanitize=address -I"$PREFIX/include" tests/libraries.c \
    -o tests/.build/libraries -L"$PREFIX/lib" -lyuv $(pkg-config --libs --static libavutil x264) -lstdc++
tests/.build/libraries

cc -O2 $(pkg-config --cflags libltntstools) tests/segmentwriter.c \
    -o tests/.build/segmentwriter $(pkg-config --libs --static libltntstools)
tests/.build/segmentwriter

make -C libltntstools/test -B test_segmentwriter_enqueue \
    CFLAGS="-Wall -O1 -g -fsanitize=address -fno-omit-frame-pointer"
# Upstream writer lifecycle leaks are separate from the enqueue ownership test.
ASAN_OPTIONS=detect_leaks=0 libltntstools/test/test_segmentwriter_enqueue copy
ASAN_OPTIONS=detect_leaks=0 libltntstools/test/test_segmentwriter_enqueue object

cc -O2 $(pkg-config --cflags vapoursynth) tests/vsscript.c \
    -o tests/.build/vsscript -L"$PYTHON_SITE/vapoursynth" -lvsscript
tests/.build/vsscript
python3 - <<'PY'
import vapoursynth as vs
clip = vs.core.std.BlankClip(width=64, height=32, length=1)
frame = clip.get_frame(0)
assert (frame.width, frame.height) == (64, 32)
print('PASS Python VapourSynth:', vs.__version__)
PY

for encoder in mpeg2video libx264 libx265; do
    encoder_opts=()
    if [ "$encoder" = libx264 ]; then
        encoder_opts=(-profile:v high -qmin 12 -qmax 40)
    elif [ "$encoder" = libx265 ]; then
        encoder_opts=(-x265-params pools=1:frame-threads=1:log-level=error)
    fi
    ffmpeg -v error -f lavfi -i testsrc2=size=128x72:rate=25 \
        -frames:v 4 -threads 1 -c:v "$encoder" "${encoder_opts[@]}" -f null -
done
printf 'quit\n' | ./run.sh
echo 'PASS current-library smoke checks'
