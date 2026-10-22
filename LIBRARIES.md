# Open-source snapshot: 2026-10-21

Versions were checked against the projects' upstream repositories/releases.
The build selects stable releases where available, and fixed revisions for
projects distributed primarily through Git. `sources-manifest.txt` records
the exact checked-out commits; local compatibility patches are listed below.

| Component | Selected version |
| --- | --- |
| FFmpeg | n9.0.2 |
| libSRT | v1.5.7 (`899348d8318eb9a3c5a5b6ec43c4a1114288773a`), Haivision upstream, OpenSSL encryption |
| x264 | OBE `368ad15875cf45478ff59cfae2441b1049dbdc91` + official upstream `0480cb05fa188d37ae87e8f4fd8f1aea3711f7ee` |
| x265 | 4.2, official multicoreware Bitbucket repository |
| FDK-AAC | v3.0.0 |
| TwoLAME | 0.4.0, upstream njh/twolame |
| libyuv | `47ba8e2160b71a7cf95c0720cd8944ab771e533e`, Chromium upstream |
| ZVBI | v0.2.45, upstream zapping-vbi/zvbi |
| zimg | release-3.0.6 |
| VapourSynth | R81 |
| libklvanc | vid.obe.1.12.0 |
| libklscte35 | `9df34b200b71485fcd757f9da6b3aeb492e559b6` (2026-09-01) |
| libltntstools | `f6399630de4d29496c94256f1fec727efa10d1df` (2026-10-02) + segmentwriter patch |
| libmpegts-obe | `1ee8519e79280f9cfbcf65af90cc9dc26345527a`, HEVC development branch |
| libdrm | 2.4.134 |
| libva / libva-utils | 2.24.1 / 2.24.0 |
| intel-vaapi-driver | `fd727a4e9cb8b2878a1e93d4dddc8dd1c1a4e0ea`, archived i965 driver |
| OBE | `3fabd16c5b00daef9945a63c9bdfa1c8467ef6f6` + compatibility patch |

NDI v4 and DeckLink 10.8.5/10.11.2 are retained. Proprietary SDK upgrades,
hardware-driver upgrades, optional VapourSynth plugins, and deployment are
outside this snapshot. System development libraries come from Ubuntu 24.04;
the host's packages are not modified. Meson 1.12.1, meson-python 0.22.1 and
Cython 3.3.0 support the R81 build. VapourSynth pins its glslang and Vulkan
header subprojects in its upstream wrap files.
libSRT is built statically with its applications and OpenSSL EVP encryption;
FFmpeg enables `--enable-libsrt`. OpenSSL development files are provided by the
isolated `obe-rt-build:srt-2026-10-21` image.

## Compatibility changes

- `0007-x264-current-upstream.patch` integrates official x264 changes into
  the OBE fork, retaining speed control and the fork's custom encoding options.
  Conflicts in `configure`, `encoder/set.c` and `x264.c` retain both sets of features.
- `0008-obe-current-libraries.patch` migrates FFmpeg packets, channel layouts,
  codec capability queries and FIFOs. OBE's public channel-mask representation
  and timestamp units remain unchanged. It also integrates the R81 header and
  library layout, and supplies actual image strides to unmodified libyuv.
  Audio conversion resets discard buffered state rather than future samples;
  encoders consume the actual number of converted samples. A decoded-signal
  regression check detected silent startup with the previous reset logic.
- `0009-ffmpeg-obe-x264.patch` adapts FFmpeg's x264 wrapper to the OBE fork's
  I/P/B QP arrays and additional profile argument. This allows the same x264
  library to serve both OBE and FFmpeg without mixing incompatible ABIs.
- The existing VAAPI utility patches remain required by OBE.
- `0010-ltntstools-literal-file-target.patch` adds an empty-suffix convention
  for a literal single-file target. OBE uses it to preserve configured output
  filenames; segmented `.ts` recordings retain their timestamped names. It also
  returns the queued byte count from writes, as required by the API and OBE.
  The patch is rebased on the October upstream snapshot and captures byte
  counts before enqueueing in both write APIs, preventing a race with cleanup.
- `0011-obe-network-input.patch` enables decoded network URL input, preserves
  video signalling, and handles bounded network reads and queue growth. It
  also fixes the MPEG-TS statistics lifecycle for current libltntstools and
  keeps URL audio timing independent of hardware latency compensation.
- `0012-obe-url-reconnect.patch` retries transient URL input failures with
  cancellable delays of 1, 2, 4 and then 5 seconds. Reopened sessions keep the
  configured streams and resume the media timeline after sender timestamp
  resets; unsupported format changes still require a new probe.
- `0013-obe-ltn-safety-fixes.patch` backports selected LTN fixes for filter
  shutdown, long-running teletext timestamps, DeckLink VANC bounds and error
  cleanup, and audio/metadata allocation failures. It also fixes the metadata
  structure allocation size, with an AddressSanitizer regression test.
- `0014-ffmpeg-srt-secret-log.patch` redacts the passphrase value if FFmpeg
  fails while applying that option.
- `0015-obe-srt-input.patch` adds SRT caller/listener input to the existing
  reconnecting URL reader. Optional encryption takes a 10–79 byte passphrase
  only from `OBE_SRT_PASSPHRASE`, uses AES-256 and enforces encryption agreement.
  Crypto options in URLs, including percent-encoded option names, are rejected
  before CLI history or probing. Without the environment variable, both peers
  must use unencrypted SRT.
- The old ZVBI/libyuv/x265/FFmpeg patches remain as historical files but are
  not applied to this snapshot. x265 4.2 already contains the SEI allocation
  fixes; current FFmpeg includes the Binutils fix. PNG dependencies are
  resolved using ZVBI's pkg-config metadata.

Software checks cannot validate SDI/NDI capture, actual GPU encoding, driver
compatibility, or sustained broadcast operation. The previous working build
should remain available until those checks have been run on the target hardware.

## Validation

The pre-LTN snapshot (`f2fbadc`) passed the complete `./build.sh` and
`./tests/smoke-current.sh` in the isolated Ubuntu 24.04 image on 2026-10-21.
The final smoke test used a fresh
container and covered AAC-LC (mono/stereo/5.1), HE-AAC v1/v2, AC-3, E-AC-3,
MP2, PES timestamp increments, decoded signal levels, FIFO wrap/growth/reset,
NV12 strides and guards, x264 speed control, MPEG-2/H.264/HEVC software encoding,
file recording paths and contents, embedded VSScript, Python R81, and OBE CLI
startup/quit. The FIFO/libyuv/x264 harness also ran with AddressSanitizer.
All compatibility patches were checked against their pinned Git base trees.

URL reconnection was rebuilt and checked with `tests/input-url-reconnect.py`:
three sender restarts with reset timestamps, loss of only audio or video, and
shutdown during silence/backoff. UDP PAL input also passed its regression check.
Patches `0008`, `0011` and `0012` reproduce the tested URL reader on the pinned
OBE revision; Internet transport and sustained operation still need validation.

The LTN integration rebuilt and installed libklscte35 (209 API tests passed),
libltntstools and OBE, reusing the other unchanged libraries. The current smoke
suite passed, including metadata allocation checks with ASan/UBSan and both
writer APIs with ASan. The writer test disables leak detection for preexisting
upstream lifecycle leaks; it verifies the enqueue ownership race independently.
UDP PAL/1080i50 input, three sender restarts, single-track loss of audio/video
and bounded shutdown passed on the new binary. The complete encoder and writer
source trees match their pinned bases plus the build patches. See the
[LTN audit](docs/upstream-ltn-2026-10-21.md) for scope and remaining limitations.

The SRT build completed in `obe-rt-build:srt-2026-10-21`. On 2026-10-21,
`tests/input-srt.py` passed encrypted caller and listener input, sender restarts
with reset PTS, unchanged stream formats, continuous PTS/DTS and decoded A/V
checks. Shutdown without a sender took 0.03 seconds in both modes. The separate
wrong-passphrase run passed in 3.02 seconds after a successful correct-key probe
on the same endpoint (`tests/.build/srt-wrong-key.log`). Keys are ephemeral and
are not retained in test configuration or logs. See the [SRT guide](docs/input-srt.md)
for reproduction and artifact locations. These checks use container loopback;
no SRT WAN test has succeeded, and listener reachability through the NAT/gateway
remains unverified. They do not establish WAN loss/jitter tolerance or sustained
broadcast readiness.
