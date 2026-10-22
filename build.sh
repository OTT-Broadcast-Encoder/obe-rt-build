#!/bin/bash -e

JOBS=${JOBS:-6}

BMDSDK_VER=10.8.5
BMDSDK_TAG=ad4195d3433e2aeeaa42e942bfea378110c9a283
FFMPEG_TAG=n9.0.2
SRT_TAG=899348d8318eb9a3c5a5b6ec43c4a1114288773a # v1.5.7
LIBYUV_TAG=47ba8e2160b71a7cf95c0720cd8944ab771e533e
LIBZVBI_TAG=v0.2.45
LIBKLVANC_TAG=vid.obe.1.12.0
LIBKLSCTE35_TAG=9df34b200b71485fcd757f9da6b3aeb492e559b6
LIBMPEGTS_TAG=1ee8519e79280f9cfbcf65af90cc9dc26345527a
X265_TAG=4.2
X264_TAG=e40cb22ee121c5dcc35b55a619cf8515d45d3ff4
X264_BITDEPTH=8
LIBLTNTSTOOLS_TAG=f6399630de4d29496c94256f1fec727efa10d1df
VAPOURSYNTH_TAG=R81
ZIMG_TAG=release-3.0.6
TWOLAME_TAG=0.4.0
FDKAAC_TAG=v3.0.0
OBE_TAG=3effd5c94ba941c2c8a957dded65e63924a2bf48
NDI_SDK=$PWD/NDI/sdk


if [ "$1" == "" ]; then
    echo "Building..."
elif [ "$1" == "clean" ]; then
    pushd vaapi
        ./clean.sh
    popd

    rm -rf decklink-sdk
    rm -rf fdk-aac
    rm -rf libav-obe ffmpeg
    rm -rf srt
    rm -rf libmpegts-obe
    rm -rf libyuv
    rm -rf obe-rt
    rm -rf target-root
    rm -rf twolame
    rm -rf x264
    rm -rf obe-bitstream
    rm -rf libklvanc
    rm -rf libklscte35
    rm -rf blackmagic-sdk
    rm -rf obecli
    rm -rf libzvbi
    rm -rf x265
    rm -rf libwebsockets
    rm -rf libltntstools
    rm -rf zimg vapoursynth
    rm -f  tarball.tgz

    exit 0
elif [ "$1" == "installdeps" ]; then
    sudo apt-get install -y git \
                            unzip \
                            build-essential \
                            gobjc \
                            gobjc++ \
                            cmake \
                            yasm \
                            nasm \
                            autoconf \
                            libtool \
                            autotools-dev \
                            automake \
                            libreadline-dev \
                            libvdpau-dev \
                            libva-dev \
                            libx11-dev \
                            libzvbi0 \
                            libzvbi-dev \
                            libzvbi-common \
                            libasound2-dev \
                            libbz2-dev \
                            liblzma-dev \
                            libssl-dev \
                            libfdk-aac-dev \
                            meson \
                            pkg-config \
                            libdrm-dev \
                            libkmod-dev \
                            libdw-dev \
                            libpixman-1-dev \
                            libcairo2-dev \
                            flex \
                            bison

    pip3 install -U --break-system-packages cython setuptools wheel

    exit 0
else
    echo "Invalid argument"

    exit 1
fi

PREFIX=$PWD/target-root/usr/local
PYTHON_SITE=$PREFIX/lib/python3.12/site-packages
export PKG_CONFIG_PATH=$PREFIX/lib/pkgconfig
export LD_LIBRARY_PATH=$PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}

if [ ! -d blackmagic-sdk ]; then
    git clone https://github.com/OTT-Broadcast-Encoder/blackmagic-sdk.git
    git -C blackmagic-sdk checkout "$BMDSDK_TAG"
fi


if [ ! -d libltntstools ]; then
    git clone https://github.com/LTNGlobal-opensource/libltntstools.git

    pushd libltntstools
        git checkout $LIBLTNTSTOOLS_TAG
        git apply ../patches/0010-ltntstools-literal-file-target.patch
    popd
fi

if [ ! -d NDI ]; then
    mkdir -p NDI

    pushd NDI
        if [ ! -f InstallNDISDK_v4_Linux.tar.gz ]; then
            wget --no-check-certificate https://downloads.ndi.tv/SDK/NDI_SDK_Linux/InstallNDISDK_v4_Linux.tar.gz
        fi

        if [ ! -f InstallNDISDK_v4_Linux.sh ]; then
            tar -zxf InstallNDISDK_v4_Linux.tar.gz
        fi

        if [ ! -d sdk ]; then
            echo "Y" | ./InstallNDISDK_v4_Linux.sh >/dev/null
            mv 'NDI SDK for Linux' sdk
        fi
    popd
fi

if [ ! -d x265 ]; then
    git clone https://bitbucket.org/multicoreware/x265_git.git x265

    pushd x265
        git checkout $X265_TAG

        # The former SEI fixes are included upstream in 4.2.
    popd
fi

if [ ! -d libzvbi ]; then
    git clone https://github.com/zapping-vbi/zvbi.git libzvbi

    pushd libzvbi
        git checkout $LIBZVBI_TAG
    popd
fi

if [ ! -d libklvanc ]; then
    git clone https://github.com/LTNGlobal-opensource/libklvanc.git

    pushd libklvanc
        git checkout $LIBKLVANC_TAG
    popd
fi

if [ ! -d libklscte35 ]; then
    git clone https://github.com/LTNGlobal-opensource/libklscte35.git

    pushd libklscte35
        git checkout $LIBKLSCTE35_TAG
    popd
fi

if [ ! -d zimg ]; then
    git clone https://github.com/sekrit-twc/zimg.git --branch "$ZIMG_TAG" --depth 1 --recurse-submodules --shallow-submodules
fi

if [ ! -d vapoursynth ]; then
    git clone https://github.com/vapoursynth/vapoursynth.git --branch "$VAPOURSYNTH_TAG" --depth 1 --recurse-submodules --shallow-submodules
fi

if [ ! -d obe-rt ]; then
    git clone https://github.com/OTT-Broadcast-Encoder/obe-rt.git
    git -C obe-rt checkout "$OBE_TAG"
fi

if [ ! -d x264 ]; then
    git clone https://github.com/OTT-Broadcast-Encoder/x264.git
    git -C x264 checkout "$X264_TAG"
fi

if [ ! -d ffmpeg ]; then
    git clone https://git.ffmpeg.org/ffmpeg.git ffmpeg

    pushd ffmpeg
        git checkout $FFMPEG_TAG
        git apply ../patches/0009-ffmpeg-obe-x264.patch
        git apply ../patches/0014-ffmpeg-srt-secret-log.patch
        git apply ../patches/0016-ffmpeg-srt-statistics.patch
    popd
fi

if [ ! -d srt ]; then
    git clone https://github.com/Haivision/srt.git
    git -C srt checkout "$SRT_TAG"
fi

if [ ! -d libmpegts-obe ]; then
    git clone https://github.com/LTNGlobal-opensource/libmpegts-obe.git

    pushd libmpegts-obe
        git checkout $LIBMPEGTS_TAG
    popd
fi

if [ ! -d libyuv ]; then
    git clone https://chromium.googlesource.com/libyuv/libyuv

    pushd libyuv
        git checkout $LIBYUV_TAG
    popd
fi

if [ ! -d twolame ]; then
    git clone --branch "$TWOLAME_TAG" --depth 1 https://github.com/njh/twolame.git
fi

if [ ! -d fdk-aac ]; then
    git clone --branch "$FDKAAC_TAG" --depth 1 https://github.com/mstorsjo/fdk-aac.git
fi

pushd libltntstools
    if [ ! -f .skip ]; then
        ./autogen.sh --build
        ./configure --prefix=$PWD/../target-root/usr/local --enable-shared=no

        make -j$JOBS
        make install

        touch .skip
    fi
popd

pushd vaapi
    ./build.sh
    # Newer builds default to the iHD driver, use the haswell compatible driver.
    # LIBVA_DRIVER_NAME=i965 ./vainfo
popd

pushd x265/build/linux
    if [ ! -f .skip ]; then
        # Static only build
        #./make-Makefiles.bash
        cmake -DCMAKE_INSTALL_PREFIX=$PWD/../../../target-root/usr/local \
            -G "Unix Makefiles" \
            -DENABLE_SHARED:bool=off \
            -DENABLE_LIBNUMA:bool=off \
            ../../source

        make -j$JOBS
        make install

        touch .skip
    fi
popd

pushd libzvbi
    if [ ! -f .skip ]; then
        ./autogen.sh
        ./configure --enable-shared=no --prefix=$PWD/../target-root/usr/local

        make -j$JOBS
        make install

        touch .skip
    fi
popd

pushd libklvanc
    if [ ! -f .skip ]; then
        ./autogen.sh --build
        ./configure --enable-shared=no --prefix=$PWD/../target-root/usr/local --enable-dep-curses=no

        make -j$JOBS
        make install

        touch .skip
    fi
popd

pushd libklscte35
    if [ ! -f .skip ]; then
        ./autogen.sh --build

        export CFLAGS="-I$PWD/../target-root/usr/local/include"
        export LDFLAGS="-L$PWD/../target-root/usr/local/lib"

        ./configure --enable-shared=no --prefix=$PWD/../target-root/usr/local

        make -j$JOBS
        make install

        touch .skip
    fi
popd

pushd libmpegts-obe
    if [ ! -f .skip ]; then
        ./configure --prefix=$PWD/../target-root/usr/local

        make -j$JOBS
        make install

        touch .skip
    fi
popd

pushd twolame
    if [ ! -f .skip ]; then
        NOCONFIGURE=1 ./autogen.sh
        ./configure --prefix=$PWD/../target-root/usr/local --enable-shared=no --disable-maintainer-mode

        # Git tags omit generated manuals; OBE only needs the library and headers.
        make -C libtwolame -j$JOBS
        make -C libtwolame install
        make install-pkgconfigDATA

        touch .skip
    fi
popd

pushd fdk-aac
    if [ ! -f .skip ]; then
        cmake -S . -B build -DCMAKE_INSTALL_PREFIX="$PREFIX" \
            -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF
        cmake --build build -j"$JOBS"
        cmake --install build
        touch .skip
    fi
popd

pushd x264
    if [ ! -f .skip ]; then
        make clean

        ./configure --enable-static --disable-cli --prefix=$PWD/../target-root/usr/local \
            --disable-lavf --disable-swscale --disable-opencl --bit-depth=$X264_BITDEPTH
        make -j$JOBS
        make install

        touch .skip
    fi
popd

pushd srt
    if [ ! -f .skip ]; then
        cmake -S . -B build -DCMAKE_INSTALL_PREFIX="$PREFIX" \
            -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_BUILD_TYPE=Release \
            -DENABLE_SHARED=OFF -DENABLE_STATIC=ON -DENABLE_APPS=ON \
            -DENABLE_ENCRYPTION=ON -DUSE_ENCLIB=openssl-evp
        cmake --build build -j"$JOBS"
        cmake --install build
        touch .skip
    fi
popd

pushd ffmpeg
    if [ ! -f .skip ]; then
        export CFLAGS="-I$PWD/../target-root/usr/local/include -I$PWD/../blackmagic-sdk/10.11.2/Linux/include"
        export CXXFLAGS="$CFLAGS"
        export LDFLAGS="-L$PWD/../target-root/usr/local/lib -lpthread -ldl"
        export PKG_CONFIG_PATH=$PWD/../target-root/usr/local/lib/pkgconfig:/usr/local/lib/pkgconfig

        ./configure --prefix=$PWD/../target-root/usr/local \
            --enable-gpl \
            --enable-nonfree \
            --enable-decklink \
            --enable-libfdk-aac \
            --enable-libx264 \
            --enable-libx265 \
            --enable-libsrt \
            --pkg-config-flags="--static" \
            --enable-gpl \
            --enable-nonfree

    # For NVENC and Cuda
    #            --enable-cuda-nvcc --enable-cuvid --enable-nvenc \
    #            --extra-cflags="-I/usr/local/cuda/include/" \
    #            --extra-ldflags=-L/usr/local/cuda/lib64/

        make -j$JOBS
        make install

        unset CFLAGS
        unset CXXFLAGS
        unset LDFLAGS
        unset PKG_CONFIG_PATH

        touch .skip
    fi
popd

pushd libyuv
    if [ ! -f .skip ]; then
        cmake -S . -B build -DCMAKE_INSTALL_PREFIX="$PREFIX" \
            -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_BUILD_TYPE=Release -DLIBYUV_DISABLE_JPEG=ON
        cmake --build build -j"$JOBS"
        cmake --install build

        touch .skip
    fi
popd

pushd zimg
    if [ ! -f .skip ]; then
        ./autogen.sh
        ./configure --prefix=$PWD/../target-root/usr/local

        make clean
        make -j$JOBS
        make install

        touch .skip
    fi
popd

pushd vapoursynth
    if [ ! -f .skip ]; then
        PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig" \
            pip3 install --no-build-isolation --no-deps --target "$PYTHON_SITE" \
            --config-settings=build-dir=build \
            --config-settings=setup-args="-Db_lto_threads=$JOBS" \
            --config-settings=compile-args="-j$JOBS" .

        touch .skip
    fi
popd

build_obe() {
    BMSDK_DIR=$PWD/blackmagic-sdk/$BMDSDK_VER/Linux

    echo "Building OBE for BlackMagic SDK version $BMDSDK_VER"

    pushd obe-rt
        export CFLAGS="-I$PWD/../target-root/usr/local/include -I$BMSDK_DIR -no-pie -D__STDC_CONSTANT_MACROS"
        export CFLAGS="$CFLAGS -I$NDI_SDK/include"
        export LDFLAGS="-L$PWD/../target-root/usr/local/lib -no-pie"
        export LDFLAGS="$LDFLAGS -L$NDI_SDK/lib/x86_64-linux-gnu"
        export PKG_CONFIG_PATH=$PREFIX/lib/pkgconfig:$PYTHON_SITE/vapoursynth/pkgconfig

        if [ -f autogen.sh ]; then
            ./autogen.sh --build
        fi

        ./configure --prefix=$PWD/../target-root/usr/local

        make clean
        make -j$JOBS
        make install
    popd

    cp target-root/usr/local/bin/obecli obecli
}

build_obe
