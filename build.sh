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
JSONC_TAG=6c55f65d07a972dbd2d1668aab2e0056ccdd52fc
BUILD_X265=1
BUILD_VAAPI=1
BUILD_LIBLTNTSTOOLS=1
LIBLTNTSTOOLS_TAG=4fbb32125bc1ea095cf26a0f7b1b279082fdd592
# Absolute path to the SDK. Fixme.
BUILD_NDI=1
NDI_SDK=$PWD/NDI/sdk
# Absolute path to the SDK. Fixme.
DEKTEC_DRV=$PWD/dektecsdk/2019.11.0/Drivers/DtPcie/Source/Linux
DEKTEC_SDK=$PWD/dektecsdk/2019.11.0/DTAPI
DEKTEC_SDK_INC=$PWD/dektecsdk/2019.11.0/DTAPI/Include


if [ "$1" == "" ]; then
	echo "Building..."
elif [ "$1" == "clean" ]; then
	pushd vaapi
		./clean.sh
	popd

	rm -rf 	decklink-sdk
	rm -rf 	fdk-aac
	rm -rf 	libav-obe ffmpeg
	rm -rf 	libmpegts-obe
	rm -rf 	libyuv
	rm -rf 	obe-rt
	rm -rf 	target-root
	rm -rf 	twolame
	rm -rf 	x264-obe
	rm -rf 	obe-bitstream
	rm -rf 	libklvanc
	rm -rf 	libklscte35
	rm -rf 	blackmagic-sdk
	rm -rf  obecli
	rm -rf  libzvbi
	rm -rf  x265
	rm -rf  libwebsockets
	rm -rf  json-c
	rm -rf  libltntstools
	rm -f   tarball.tgz
elif [ "$1" == "installdeps" ]; then
	sudo apt-get install git -y
	sudo apt-get install unzip -y
	sudo apt-get install build-essential -y
	sudo apt-get install gobjc -y
	sudo apt-get install gobjc++ -y
	sudo apt-get install cmake -y
	sudo apt-get install yasm -y
	sudo apt-get install autoconf -y
	sudo apt-get install libtool -y
	sudo apt-get install autotools-dev -y
	sudo apt-get install automake -y
	sudo apt-get install libreadline-dev -y
	sudo apt-get install libvdpau-dev -y
	sudo apt-get install libva-dev -y
	sudo apt-get install libx11-dev -y
	sudo apt-get install libzvbi0 -y
	sudo apt-get install libzvbi-dev -y
	sudo apt-get install libzvbi-common -y
	sudo apt-get install libasound2-dev -y
	sudo apt-get install libbz2-dev -y
	sudo apt-get install liblzma-dev -y
	sudo apt-get install libfdk-aac-dev -y
	sudo apt-get install meson -y
	sudo apt-get install pkg-config -y
	sudo apt-get install libdrm-dev -y
	sudo apt-get install libkmod-dev -y
	sudo apt-get install libprocps-dev -y
	sudo apt-get install libdw-dev -y
	sudo apt-get install libpixman-1-dev -y
	sudo apt-get install libcairo-dev -y
	sudo apt-get install flex -y
	sudo apt-get install bison -y

	exit 0
else
	echo "Invalid argument"
	exit 1
fi

if [ ! -d blackmagic-sdk ]; then
    git clone https://github.com/OTT-Broadcast-Encoder/blackmagic-sdk.git
fi


if [ $BUILD_LIBLTNTSTOOLS -eq 1 ]; then
	if [ ! -d libltntstools ]; then
		git clone https://github.com/LTNGlobal-opensource/libltntstools.git
		cd libltntstools && git checkout $LIBLTNTSTOOLS_TAG && cd ..
	fi
fi

if [ $BUILD_NDI -eq 1 ]; then
	mkdir -p NDI
	cd NDI
	if [ ! -f InstallNDISDK_v4_Linux.tar.gz ]; then
		wget --no-check-certificate https://downloads.ndi.tv/SDK/NDI_SDK_Linux/InstallNDISDK_v4_Linux.tar.gz
	fi
	if [ ! -f InstallNDISDK_v4_Linux.sh ]; then
		tar zxf InstallNDISDK_v4_Linux.tar.gz
	fi
	if [ ! -d sdk ]; then
		echo "Y" | ./InstallNDISDK_v4_Linux.sh >/dev/null
		mv 'NDI SDK for Linux' sdk
	fi
	cd ..
fi

if [ $BUILD_X265 -eq 1 ]; then
	if [ ! -d x265 ]; then
		git clone https://github.com/videolan/x265.git
		cd x265
		git checkout $X265_TAG
		patch -p1 <../patches/0003-x265-sei-overflow.patch
		patch -p1 <../patches/0004-x265-sei-additional-elements-segfault.patch
		#patch -p1 <../patches/0005-x265-additional-recovery-points.patch
		cd ..
	fi
fi

if [ ! -d libzvbi ]; then
	git clone https://github.com/LTNGlobal-opensource/libzvbi.git
	if [ "$LIBZVBI_TAG" != "" ]; then
		cd libzvbi
		git checkout $LIBZVBI_TAG
		patch -p1 <../patches/0000-libzvbi-remove-png-dep.patch
		cd ..
	fi
fi

if [ ! -d libklvanc ]; then
	git clone https://github.com/LTNGlobal-opensource/libklvanc.git
	if [ "$LIBKLVANC_TAG" != "" ]; then
		cd libklvanc && git checkout $LIBKLVANC_TAG && cd ..
	fi
fi

if [ ! -d libklscte35 ]; then
	git clone https://github.com/LTNGlobal-opensource/libklscte35.git
	if [ "$LIBKLSCTE35_TAG" != "" ]; then
		cd libklscte35 && git checkout $LIBKLSCTE35_TAG && cd ..
	fi
fi

if [ ! -d obe-rt ]; then
	git clone https://github.com/OTT-Broadcast-Encoder/obe-rt.git
fi

if [ ! -d x264-obe ]; then
	git clone https://github.com/LTNGlobal-opensource/x264-obe.git
fi

if [ ! -d ffmpeg ]; then
	git clone https://git.ffmpeg.org/ffmpeg.git ffmpeg
	cd ffmpeg && git checkout $FFMPEG_TAG && cd ..
fi

if [ ! -d libmpegts-obe ]; then
	git clone https://github.com/LTNGlobal-opensource/libmpegts-obe.git
	if [ "$LIBMPEGTS_TAG" != "" ]; then
		cd libmpegts-obe && git checkout $LIBMPEGTS_TAG && cd ..
	fi
fi

if [ ! -d libyuv ]; then
	git clone https://github.com/LTNGlobal-opensource/libyuv.git
	pushd libyuv
		# Make sure we use a known-good version
		git checkout $LIBYUV_TAG
		patch -p1 <../patches/0002-libyuv-csc-height.patch
	popd
fi

if [ ! -d twolame ]; then
	git clone https://github.com/OTT-Broadcast-Encoder/twolame.git
fi

if [ $BUILD_LIBLTNTSTOOLS -eq 1 ]; then
	pushd libltntstools
		if [ ! -f .skip ]; then
			./autogen.sh --build
			./configure --prefix=$PWD/../target-root/usr/local --enable-shared=no
			make -j$JOBS
			make install
			touch .skip
		fi
	popd
fi

if [ $BUILD_VAAPI -eq 1 ]; then
	pushd vaapi
		./build.sh
		# Newer builds default to the iHD driver, use the haswell compatible driver.
		# LIBVA_DRIVER_NAME=i965 ./vainfo
	popd
fi


if [ $BUILD_JSONC -eq 1 ]; then
	pushd json-c
		if [ ! -f .skip ]; then
			./autogen.sh
			./configure --prefix=$PWD/../target-root/usr/local --enable-shared=no
			make -j$JOBS
			make install
			touch .skip
		fi
	popd
fi

if [ $BUILD_X265 -eq 1 ]; then
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
			#touch .skip
		fi
	popd
fi

pushd libzvbi
	if [ ! -f .skip ]; then
		./configure --enable-shared=no --prefix=$PWD/../target-root/usr/local
		make && make install
		make install
		touch .skip
	fi
popd

pushd libklvanc
	if [ ! -f .skip ]; then
		./autogen.sh --build
		./configure --enable-shared=no --prefix=$PWD/../target-root/usr/local --enable-dep-curses=no
		make && make install
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
		make && make install
		touch .skip
	fi
popd

pushd libmpegts-obe
	if [ ! -f .skip ]; then
		./configure --prefix=$PWD/../target-root/usr/local
		make && make install
		make install
		touch .skip
	fi
popd

pushd twolame
	if [ ! -f .skip ]; then
		./configure --prefix=$PWD/../target-root/usr/local --enable-shared=no
		make && make install
		touch .skip
	fi
popd

pushd x264-obe
	if [ ! -f .skip ]; then
		make clean
		./configure --enable-static --disable-cli --prefix=$PWD/../target-root/usr/local --disable-lavf --disable-swscale --disable-opencl --bit-depth=$X264_BITDEPTH
		make -j$JOBS && make install
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

	make -j$JOBS && make install
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

build_obe() {
    BMSDK_DIR=$PWD/blackmagic-sdk/$BMDSDK_VER/Linux

	echo $BMDSDK_VER

    echo "Building OBE for BlackMagic SDK version $BMDSDK_VER"

	pushd obe-rt
	export CFLAGS="-I$PWD/../target-root/usr/local/include -I$BMSDK_DIR -no-pie"
	export LDFLAGS="-L$PWD/../target-root/usr/local/lib -no-pie"
	export PKG_CONFIG_PATH=$PWD/../target-root/usr/local/lib/pkgconfig
	if [ $BUILD_NDI -eq 1 ]; then
		export CFLAGS="$CFLAGS -I$NDI_SDK/include"
		export LDFLAGS="$LDFLAGS -L$NDI_SDK/lib/x86_64-linux-gnu"
	fi

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