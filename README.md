# obe-build-environment
Building the OBE environment from KL repos. The build script clones tip for
all of the major repos required and builds OBE in a self-contained
'target-root' directory, along with avconf/ffmpeg and any other project
dependencies.

## Isolated Ubuntu build

From the repository root, build the toolchain image and run the build inside it:

```sh
docker build -t obe-rt-build:ubuntu24.04 -f docker/obe-builder/Dockerfile.ubuntu .
docker run --name obe-rt-build --cpus=6 --memory=8g \
    --mount "type=bind,source=$PWD,target=/work" \
    -e GIT_TERMINAL_PROMPT=0 obe-rt-build:ubuntu24.04
```

System dependencies stay in the container, and Python packages use its virtual
environment. Sources and build outputs persist in the repository directory;
the executable is `obecli`, also installed under `target-root/usr/local/bin`.
VapourSynth is pinned to R70 to retain the Autotools and `setup.py` build used
by this script; current upstream uses a different build system.
Restart an existing build container with `docker start -a obe-rt-build` to retry
after a failure; completed dependency builds are reused through `.skip` files.

Run the resulting CLI with the same isolated toolchain and the locally built
libraries and Python module:

```sh
docker run --rm -it --cpus=6 --memory=8g \
    --mount "type=bind,source=$PWD,target=/work" \
    -e LD_LIBRARY_PATH=/work/target-root/usr/local/lib:/work/NDI/sdk/lib/x86_64-linux-gnu \
    -e PYTHONPATH=/work/target-root/usr/local/lib/python3.12/site-packages \
    obe-rt-build:ubuntu24.04 ./obecli
```

The build includes the upstream FFmpeg fix for Binutils 2.41 and newer. CLI
startup, a short FFmpeg software encode, and a VapourSynth blank frame were
verified in this container. Capture devices and GPU encoding require their
drivers and explicit device access when launching a runtime container.

## Original CentOS instructions

Tested on CentOS 7.2.1511 only.

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

