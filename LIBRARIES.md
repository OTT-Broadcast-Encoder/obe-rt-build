# Open-source snapshot: 2026-10-21

Versions were checked against the projects' upstream repositories/releases.
The build selects stable releases where available, and fixed revisions for
projects distributed primarily through Git. `sources-manifest.txt` records
the exact checked-out commits; local compatibility patches are listed below.

| Component | Selected version |
| --- | --- |
| FFmpeg | n9.0.2 |
| x264 | OBE `368ad15875cf45478ff59cfae2441b1049dbdc91` + official upstream `0480cb05fa188d37ae87e8f4fd8f1aea3711f7ee` |
| x265 | 4.2, official multicoreware Bitbucket repository |
| FDK-AAC | v3.0.0 |
| TwoLAME | 0.4.0, upstream njh/twolame |
| libyuv | `47ba8e2160b71a7cf95c0720cd8944ab771e533e`, Chromium upstream |
| ZVBI | v0.2.45, upstream zapping-vbi/zvbi |
| zimg | release-3.0.6 |
| VapourSynth | R81 |
| libklvanc | vid.obe.1.12.0 |
| libklscte35 | vid.obe.1.4.0 |
| libltntstools | v1.38.6 |
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
- `0011-obe-network-input.patch` enables decoded network URL input, preserves
  video signalling, and handles bounded network reads and queue growth. It
  also fixes the MPEG-TS statistics lifecycle for current libltntstools and
  keeps URL audio timing independent of hardware latency compensation.
- The old ZVBI/libyuv/x265/FFmpeg patches remain as historical files but are
  not applied to this snapshot. x265 4.2 already contains the SEI allocation
  fixes; current FFmpeg includes the Binutils fix. PNG dependencies are
  resolved using ZVBI's pkg-config metadata.

Software checks cannot validate SDI/NDI capture, actual GPU encoding, driver
compatibility, or sustained broadcast operation. The previous working build
should remain available until those checks have been run on the target hardware.

## Validation

The complete `./build.sh` and `./tests/smoke-current.sh` both passed in the
isolated Ubuntu 24.04 image on 2026-10-21. The final smoke test used a fresh
container and covered AAC-LC (mono/stereo/5.1), HE-AAC v1/v2, AC-3, E-AC-3,
MP2, PES timestamp increments, decoded signal levels, FIFO wrap/growth/reset,
NV12 strides and guards, x264 speed control, MPEG-2/H.264/HEVC software encoding,
file recording paths and contents, embedded VSScript, Python R81, and OBE CLI
startup/quit. The FIFO/libyuv/x264 harness also ran with AddressSanitizer.
All compatibility patches were checked against their pinned Git base trees.
