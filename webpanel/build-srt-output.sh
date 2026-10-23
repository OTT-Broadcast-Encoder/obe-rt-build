#!/bin/sh
# Uses the existing build environment and libsrt; never installs dependencies.
set -eu
panel_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
panel_prefix="$panel_root/target-root/usr/local"
panel_output=${1:-"$panel_root/webpanel/bin/obe-srt-output"}
mkdir -p -- "$(dirname -- "$panel_output")"
panel_object=$(mktemp)
panel_header=$(mktemp)
trap 'rm -f "$panel_object" "$panel_header"' EXIT HUP INT TERM
python3 -B "$panel_root/webpanel/srt_options.py" > "$panel_header"
cc -std=c11 -O2 -Wall -Wextra -Werror -include "$panel_header" -I"$panel_prefix/include" -c "$panel_root/webpanel/srt_output.c" -o "$panel_object"
c++ "$panel_object" "$panel_prefix/lib/libsrt.a" -lssl -lcrypto -lpthread -lm -o "$panel_output"
