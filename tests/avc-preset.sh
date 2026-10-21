#!/bin/bash
set -euo pipefail
ulimit -c 0
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
PREFIX=$PWD/target-root/usr/local
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig"
export LD_LIBRARY_PATH="$PREFIX/lib"
mkdir -p tests/.build
# Keep only the parameter builder and its dependencies from the real obe.c.
cc -O1 -g -ffunction-sections -fdata-sections -Wl,--gc-sections \
    -Iobe-rt -Iobe-rt/obe -I"$PREFIX/include" \
    -D_DEFAULT_SOURCE -DKL_USERSPACE -DVERSION_MAJOR=4 -DVERSION_MINOR=1 -DVERSION_PATCH=4 \
    '-DGIT_VERSION="preset-test"' '-DVERSION="preset-test"' \
    obe-rt/tests/avc-preset.c obe-rt/obe/obe.c \
    -o tests/.build/avc-preset $(pkg-config --libs --static x264)
tests/.build/avc-preset
