#!/bin/bash -e

JOBS=8

BMDSDK_VER=10.8.5
FFMPEG_TAG=n4.2.1
LIBYUV_TAG=cbe5385055b9360cacd14877450631b87eea1fcd
LIBZVBI_TAG=e62d905e00cdd1d6d4333ead90fb5b44bfb49371
LIBKLVANC_TAG=vid.obe.1.10.0
LIBKLSCTE35_TAG=vid.obe.1.3.0
LIBMPEGTS_TAG=hevc-dev
X265_TAG=ef1c5205fc14d436b71b1459eba0c85fec0013b7
X264_BITDEPTH=8
LIBLTNTSTOOLS_TAG=4fbb32125bc1ea095cf26a0f7b1b279082fdd592
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
	                        libfdk-aac-dev \
	                        meson \
	                        pkg-config \
	                        libdrm-dev \
	                        libkmod-dev \
	                        libdw-dev \
	                        libpixman-1-dev \
	                        libcairo-dev \
	                        flex \
	                        bison

	pip3 install -U --break-system-packages cython setuptools wheel

	exit 0
else
	echo "Invalid argument"

	exit 1
fi

if [ ! -d blackmagic-sdk ]; then
    git clone https://github.com/OTT-Broadcast-Encoder/blackmagic-sdk.git
fi


if [ ! -d libltntstools ]; then
	git clone https://github.com/LTNGlobal-opensource/libltntstools.git

	pushd libltntstools
		git checkout $LIBLTNTSTOOLS_TAG
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
	git clone https://github.com/videolan/x265.git

	pushd x265
		git checkout $X265_TAG
		patch -p1 <../patches/0003-x265-sei-overflow.patch
		patch -p1 <../patches/0004-x265-sei-additional-elements-segfault.patch
		#patch -p1 <../patches/0005-x265-additional-recovery-points.patch
	popd
fi

if [ ! -d libzvbi ]; then
	git clone https://github.com/LTNGlobal-opensource/libzvbi.git

	pushd libzvbi
		git checkout $LIBZVBI_TAG
		patch -p1 <../patches/0000-libzvbi-remove-png-dep.patch
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
	git clone https://bitbucket.org/the-sekrit-twc/zimg.git --depth 1 --recurse-submodules --shallow-submodules
	pushd zimg && git config pull.rebase true && popd
else
	pushd zimg && git pull && popd
fi

if [ ! -d vapoursynth ]; then
	git clone https://github.com/vapoursynth/vapoursynth.git --depth 1 --recurse-submodules --shallow-submodules
	pushd vapoursynth && git config pull.rebase true && popd
else
	pushd vapoursynth && git pull && popd
fi


if [ ! -d obe-rt ]; then
	git clone https://github.com/OTT-Broadcast-Encoder/obe-rt.git
	git config pull.rebase true
else
	pushd obe-rt && git pull && popd
fi

if [ ! -d x264 ]; then
	git clone https://github.com/OTT-Broadcast-Encoder/x264.git
	git config pull.rebase true
else
	pushd x264 && git pull && popd
fi

if [ ! -d ffmpeg ]; then
	git clone https://git.ffmpeg.org/ffmpeg.git ffmpeg
	pushd ffmpeg && git checkout $FFMPEG_TAG && popd
fi

if [ ! -d libmpegts-obe ]; then
	git clone https://github.com/LTNGlobal-opensource/libmpegts-obe.git

	pushd libmpegts-obe
		git checkout $LIBMPEGTS_TAG
	popd
fi

if [ ! -d libyuv ]; then
	git clone https://github.com/LTNGlobal-opensource/libyuv.git

	pushd libyuv
		git checkout $LIBYUV_TAG
		patch -p1 <../patches/0002-libyuv-csc-height.patch
	popd
fi

if [ ! -d twolame ]; then
	git clone https://github.com/OTT-Broadcast-Encoder/twolame.git
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
		./configure --prefix=$PWD/../target-root/usr/local --enable-shared=no
		make -j$JOBS
		make install
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

pushd ffmpeg
if [ ! -f .skip ]; then
	export CFLAGS="-I$PWD/../target-root/usr/local/include -I$PWD/../blackmagic-sdk/10.11.2/Linux/include"
	export LDFLAGS="-L$PWD/../target-root/usr/local/lib -lpthread -ldl"
	export PKG_CONFIG_PATH=$PWD/../target-root/usr/local/lib/pkgconfig:/usr/local/lib/pkgconfig
	./configure --prefix=$PWD/../target-root/usr/local \
		--enable-gpl \
		--enable-nonfree \
		--enable-decklink \
		--disable-swscale-alpha \
		--pkg-config-flags="--static" \
		--enable-gpl \
		--enable-nonfree

# For NVENC and Cuda
#			--enable-cuda-nvcc --enable-cuvid --enable-nvenc \
#			--extra-cflags="-I/usr/local/cuda/include/" \
#			--extra-ldflags=-L/usr/local/cuda/lib64/

	make -j$JOBS
	make install
	unset CFLAGS
	unset LDFLAGS
	unset PKG_CONFIG_PATH
	touch .skip
fi
popd

pushd libyuv
	if [ ! -f .skip ]; then
		make -f linux.mk
		cp -r include/* $PWD/../target-root/usr/local/include
		cp libyuv.a $PWD/../target-root/usr/local/lib
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
		export CFLAGS="-I$PWD/../target-root/usr/local/include -DVS_USE_LATEST_API"
		export LDFLAGS="-L$PWD/../target-root/usr/local/lib"
		export PKG_CONFIG_PATH=$PWD/../target-root/usr/local/lib/pkgconfig:/usr/local/lib/pkgconfig
		./autogen.sh
		./configure --prefix=$PWD/../target-root/usr/local
		make clean
		make -j$JOBS
		make install
		python3 setup.py sdist -d sdist
        mkdir -p empty
		pushd empty
        	pip3 install --break-system-packages vapoursynth --no-index --find-links ../sdist
        popd
		unset CFLAGS
		unset LDFLAGS
		unset PKG_CONFIG_PATH
		unset PYTHON_VERSION
		touch .skip
	fi
popd

build_obe() {
    BMSDK_DIR=$PWD/blackmagic-sdk/$BMDSDK_VER/Linux

	echo $BMDSDK_VER

    echo "Building OBE for BlackMagic SDK version $BMDSDK_VER"

	pushd obe-rt
	export CFLAGS="-I$PWD/../target-root/usr/local/include -I$BMSDK_DIR -no-pie"
	export CFLAGS="$CFLAGS -I$NDI_SDK/include"
	export LDFLAGS="-L$PWD/../target-root/usr/local/lib -no-pie"
	export LDFLAGS="$LDFLAGS -L$NDI_SDK/lib/x86_64-linux-gnu"
	export PKG_CONFIG_PATH=$PWD/../target-root/usr/local/lib/pkgconfig

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