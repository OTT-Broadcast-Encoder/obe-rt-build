# obe-build-environment
Building the OBE environment from pinned upstream releases and the OBE forks.
The open-source library snapshot is dated 2026-10-21. Sources and artifacts
are kept in this checkout, with installed libraries under `target-root`.

The [feature roadmap](tasks/plan.md) and [implementation backlog](tasks/todo.md)
prioritize encoding quality, the PAL-to-1080i transition, and URL-based network
input for testing without SDI (in Italian).

The [LTN upstream audit](docs/upstream-ltn-2026-10-21.md) records the library
rebase, selected encoder fixes, validation and deferred upstream changes.

The [URL input guide](docs/input-url.md) describes the first UDP/RTP MPEG-TS
input implementation, a live synthetic source, CLI configuration and its
integration tests. SRT and the Internet contribution link remain follow-up work.

## Isolated Ubuntu build

From the repository root, build the toolchain image and run the build inside it:

```sh
docker build -t obe-rt-build:ubuntu24.04 -f docker/obe-builder/Dockerfile.ubuntu .
docker build -t obe-rt-build:current-2026-10-21 -f docker/obe-builder/Dockerfile.current .
docker run --name obe-rt-build-current --cpus=6 --memory=8g \
    --mount "type=bind,source=$PWD,target=/work" \
    -e GIT_TERMINAL_PROMPT=0 -e JOBS=6 obe-rt-build:current-2026-10-21
```

System dependencies stay in the container, and Python packages use its virtual
environment. Sources and build outputs persist in the repository directory;
the executable is `obecli`, also installed under `target-root/usr/local/bin`.
VapourSynth R81 uses Meson and installs its Python package, native libraries
and headers under `target-root/usr/local/lib/python3.12/site-packages`.
Restart an existing build container with `docker start -a obe-rt-build-current` to retry
after a failure; completed dependency builds are reused through `.skip` files.

Run the resulting CLI with the same isolated toolchain and the locally built
libraries and Python module. `run.sh` supplies the library and Python paths:

```sh
docker run --rm -it --cpus=6 --memory=8g \
    --mount "type=bind,source=$PWD,target=/work" \
    obe-rt-build:current-2026-10-21 ./run.sh
```

Use a fresh checkout for this snapshot: `.skip` files reuse completed builds
and do not upgrade existing source directories or detect version changes.
Keep an older working checkout separate for comparisons and rollback.
NDI v4 and DeckLink 10.8.5 (OBE) / 10.11.2 (FFmpeg) are unchanged.
The archived Intel i965 driver is retained for Haswell compatibility.
Capture and GPU encoding require hardware drivers and explicit device access.

Run the software integration checks in the same isolated environment:

```sh
docker run --rm --cpus=6 --memory=8g \
    --mount "type=bind,source=$PWD,target=/work" \
    obe-rt-build:current-2026-10-21 ./tests/smoke-current.sh
```

The checks exercise OBE's audio encoders and PES timestamps, decoded audio,
FIFO growth/reset, file output names and bytes, padded NV12 conversion, x264 speed control, FFmpeg software
encoders, embedded VSScript, the Python module, and CLI startup.
See [LIBRARIES.md](LIBRARIES.md) for source versions and compatibility patches.

## Original CentOS instructions

Historical instructions for the original dependency set; this snapshot is
validated with Ubuntu 24.04, GCC 13 and Python 3.12 in Docker.

Manually, make sure you yum install any packages described at the start of the build script,
see build.sh yum commands.

# Any sub-repo dependencies are automatically downloaded during the build process.
     $ git clone https://github.com/stoth68000/obe-build-environment build
     $ cd build
     $ ./build.sh a52
     $ ./target-root/usr/local/bin/obecli 

     Open Broadcast Encoder command line interface.
     Including Kernel Labs fixups.
     Version 1.5 (1.0-180-g1e5dea09)

     obecli> quit

